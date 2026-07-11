"""FMI 2.0 co-simulation FMU wrapping the tmhp ASHPB ``step()`` kernel (#165 P1).

The slave advances :class:`~tmhp.AirSourceHeatPumpBoiler` one communication
step at a time through the public ``step()`` seam, so any FMI master (fmpy,
OMSimulator, Dymola, …) can drive the refrigerant-cycle-resolved heat-pump
model as a co-simulation component.

Build (PythonFMU CLI or API)::

    pythonfmu build -f src/tmhp/integrations/fmu.py <project_folder>
    # or: FmuBuilder.build_FMU(".../fmu.py", dest="out/")

Run::

    import fmpy
    fmpy.simulate_fmu("TmhpAshpbSlave.fmu", ...)

Lead track is FMI 2.0 (co-simulation only): ``tmhp`` exposes no continuous
state derivatives, and 2.0 co-sim is the most broadly importable flavour.
Boundary outputs are sanitized to avoid non-finite numeric values, while
``converged`` and ``failure_reason`` preserve step-level diagnostics for the
importing master.

.. note::
   Native-wheel caveat (CoolProp/numpy/scipy): the FMU is a *tool-coupling*
   artifact, not a hermetic binary — the importing environment must provide
   ``tmhp`` and its native dependencies for the chosen (OS, arch, Python-ABI).
   No save-state/rollback at this scope (single-pass co-sim only).
"""

from __future__ import annotations

from typing import Any, cast
from xml.etree.ElementTree import Element, SubElement

from pythonfmu import (
    Boolean,
    Fmi2Causality,
    Fmi2Slave,
    Fmi2Variability,
    Real,
    String,
)

from tmhp import AirSourceHeatPumpBoiler
from tmhp.dynamic_context import DynamicState
from tmhp.integrations import _fmi_common

_finite = _fmi_common.finite
_is_finite = _fmi_common.is_finite
_failure_reason = _fmi_common.failure_reason
_DESCRIPTIONS = _fmi_common.VARIABLE_DESCRIPTIONS

_REAL_UNITS = {
    "hp_capacity": "W",
    "T_tank_w_init": "degC",
    "T_sur": "degC",
    "T0": "degC",
    "dhw_draw": "m3/s",
    "T_sup_w": "degC",
    "E_cmp": "W",
    "E_tot": "W",
    "Q_ref_tank": "W",
    "cop_sys": "1",
    "T_tank_w": "degC",
}
_UNIT_SPECS: tuple[tuple[str, dict[str, str]], ...] = (
    ("W", {"kg": "1", "m": "2", "s": "-3"}),
    ("degC", {"K": "1", "offset": "273.15"}),
    ("m3/s", {"m": "3", "s": "-1"}),
    ("1", {}),
)


def _ensure_initial_unknowns(root: Element) -> None:
    """Add FMI 2.0 InitialUnknowns for PythonFMU-generated outputs.

    PythonFMU 0.7 writes ``ModelStructure/Outputs`` but omits
    ``ModelStructure/InitialUnknowns``. FMPy validation expects the output set
    to be listed there, so mirror the output indexes to keep the generated FMU
    statically interoperable.
    """
    model_structure = root.find("ModelStructure")
    if model_structure is None or model_structure.find("InitialUnknowns") is not None:
        return

    outputs = model_structure.find("Outputs")
    if outputs is None:
        return

    indexes = [unknown.attrib["index"] for unknown in outputs.findall("Unknown") if "index" in unknown.attrib]
    if not indexes:
        return

    initial_unknowns = SubElement(model_structure, "InitialUnknowns")
    for index in indexes:
        SubElement(initial_unknowns, "Unknown", attrib={"index": index})


class TmhpAshpbSlave(Fmi2Slave):
    """ASHPB single-timestep co-simulation kernel (FMI 2.0)."""

    author = "BET Lab"
    description = "tmhp ASHPB one-dt co-simulation kernel (FMI 2.0)"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # --- Parameters (fixed at initialization) ---
        self.ref = "R32"
        self.hp_capacity = 15000.0
        self.T_tank_w_init = 55.0
        self.T_sur = 20.0  # surrounding (tank-loss) temperature [°C]
        self.register_variable(
            String(
                "ref",
                causality=Fmi2Causality.parameter,
                variability=Fmi2Variability.fixed,
                description=_DESCRIPTIONS["ref"],
            )
        )
        for nm in ("hp_capacity", "T_tank_w_init", "T_sur"):
            self.register_variable(
                Real(
                    nm,
                    causality=Fmi2Causality.parameter,
                    variability=Fmi2Variability.fixed,
                    description=_DESCRIPTIONS[nm],
                )
            )

        # --- Inputs (master sets before each do_step) ---
        self.T0 = 7.0  # outdoor air temperature [°C]
        self.dhw_draw = 0.0  # service-water draw-off [m³/s] (-> dV_mix_w_out)
        self.T_sup_w = 15.0  # mains make-up water temperature [°C]
        for nm in ("T0", "dhw_draw", "T_sup_w"):
            self.register_variable(
                Real(
                    nm,
                    causality=Fmi2Causality.input,
                    variability=Fmi2Variability.continuous,
                    description=_DESCRIPTIONS[nm],
                )
            )

        # --- Outputs (master reads after each do_step) ---
        self.E_cmp = 0.0
        self.E_tot = 0.0
        self.Q_ref_tank = 0.0
        self.cop_sys = 0.0
        self.T_tank_w = self.T_tank_w_init
        self.hp_is_on = False
        self.converged = True
        self.failure_reason = "none"
        for nm in ("E_cmp", "E_tot", "Q_ref_tank", "cop_sys", "T_tank_w"):
            self.register_variable(Real(nm, causality=Fmi2Causality.output, description=_DESCRIPTIONS[nm]))
        # FMI forbids variability="continuous" on Boolean variables.
        for nm in ("hp_is_on", "converged"):
            self.register_variable(
                Boolean(
                    nm,
                    causality=Fmi2Causality.output,
                    variability=Fmi2Variability.discrete,
                    description=_DESCRIPTIONS[nm],
                )
            )
        self.register_variable(
            String(
                "failure_reason",
                causality=Fmi2Causality.output,
                variability=Fmi2Variability.discrete,
                description=_DESCRIPTIONS["failure_reason"],
            )
        )

        self._hp: AirSourceHeatPumpBoiler | None = None
        self._state: DynamicState | None = None
        self._n = 0

    def to_xml(self, model_options: dict[str, str] | None = None) -> Element:
        """Build a static FMI 2.0 model description for PythonFMU."""
        root = cast(Element, super().to_xml({} if model_options is None else model_options))
        _fmi_common.ensure_unit_definitions(root, _UNIT_SPECS, insertion_after={"CoSimulation", "ModelExchange"})
        _fmi_common.apply_fmi2_real_units(root, _REAL_UNITS)
        _ensure_initial_unknowns(root)
        return root

    def exit_initialization_mode(self) -> None:
        # Parameters are final here — build the model and seed the state.
        self._hp = AirSourceHeatPumpBoiler(ref=self.ref, hp_capacity=self.hp_capacity)
        self._state = self._hp.make_initial_state(self.T_tank_w_init)
        self._n = 0
        self.T_tank_w = self.T_tank_w_init

    def do_step(self, current_time: float, step_size: float) -> bool:
        if self._hp is None or self._state is None:
            raise RuntimeError("FMU slave used before exit_initialization_mode()")
        if not (
            _is_finite(current_time)
            and _is_finite(step_size)
            and float(step_size) > 0.0
            and _is_finite(self.T0)
            and _is_finite(self.dhw_draw)
            and float(self.dhw_draw) >= 0.0
            and _is_finite(self.T_sup_w)
            and _is_finite(self.T_sur)
        ):
            self.hp_is_on = False
            self.converged = False
            self.failure_reason = "invalid_input"
            return False

        inputs = {
            "n": self._n,
            "current_time_s": float(current_time),
            "T0": float(self.T0),
            "dV_mix_w_out": float(self.dhw_draw),
            "T_sup_w": float(self.T_sup_w),
            "T_sur": float(self.T_sur),
            "I_DN": 0.0,
            "I_dH": 0.0,
        }
        self._state, res = self._hp.step(self._state, inputs, float(step_size))

        self.E_cmp = _finite(res["E_cmp [W]"])
        self.E_tot = _finite(res["E_tot [W]"])
        self.Q_ref_tank = _finite(res["Q_ref_tank [W]"])
        self.cop_sys = _finite(res.get("cop_sys [-]", float("nan")))
        self.T_tank_w = _finite(res["T_tank_w [°C]"])
        self.hp_is_on = bool(res.get("hp_is_on", self.E_cmp > 0.0))
        self.converged = bool(res.get("converged", True))
        self.failure_reason = _failure_reason(res.get("failure_reason", "none"))

        self._n += 1
        return True
