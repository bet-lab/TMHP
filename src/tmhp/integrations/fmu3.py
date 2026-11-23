"""FMI 3.0 co-simulation FMU wrapping the TMHP ASHPB ``step()`` kernel.

This adapter mirrors :mod:`tmhp.integrations.fmu` at the TMHP boundary but uses
``pythonfmu3`` and the FMI 3.0 ``Fmi3StepResult`` return contract. It targets
FMI 3.0 Co-Simulation only: the FMU owns the ASHPB dynamic state and advances it
once for each importer communication step.

Build::

    pythonfmu3 build -f src/tmhp/integrations/fmu3.py .

The adapter intentionally does not expose FMI 3.0 clocks, Scheduled Execution,
or array variables. Those features are useful for embedded controls and
multi-rate models; TMHP's current ASHPB FMU boundary is scalar Co-Simulation.
"""

from __future__ import annotations

from typing import Any, cast
from xml.etree.ElementTree import Element

from pythonfmu3 import (
    Boolean,
    Float64,
    Fmi3Causality,
    Fmi3Slave,
    Fmi3Status,
    Fmi3StepResult,
    Fmi3Variability,
    String,
)

from tmhp import AirSourceHeatPumpBoiler
from tmhp.dynamic_context import DynamicState
from tmhp.integrations import _fmi_common

_finite = _fmi_common.finite
_failure_reason = _fmi_common.failure_reason
_DESCRIPTIONS = _fmi_common.VARIABLE_DESCRIPTIONS

_REAL_UNITS = {
    "time": "s",
    "hp_capacity": "W",
    "T_tank_w_init": "degC",
    "T_sur": "degC",
    "T0": "degC",
    "RH_out": "1",
    "dhw_draw": "m3/s",
    "T_sup_w": "degC",
    "E_cmp": "W",
    "E_tot": "W",
    "Q_ref_tank": "W",
    "cop_sys": "1",
    "T_tank_w": "degC",
    "V_cmp_disp_cc": "cm3",
    "dV_fan_a_rated": "m3/s",
}
_UNIT_SPECS: tuple[tuple[str, dict[str, str]], ...] = (
    ("W", {"kg": "1", "m": "2", "s": "-3"}),
    ("s", {"s": "1"}),
    ("degC", {"K": "1", "offset": "273.15"}),
    ("m3/s", {"m": "3", "s": "-1"}),
    ("cm3", {"m": "3", "factor": "1e-6"}),
    ("1", {}),
)


class TmhpAshpbFmi3Slave(Fmi3Slave):
    """ASHPB single-timestep co-simulation kernel (FMI 3.0)."""

    author = "BET Lab"
    description = "TMHP ASHPB one-dt co-simulation kernel (FMI 3.0)"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.time = 0.0
        self.register_variable(
            Float64(
                "time",
                causality=Fmi3Causality.independent,
                variability=Fmi3Variability.continuous,
                unit=_REAL_UNITS["time"],
            )
        )

        self.ref = "R32"
        self.hp_capacity = 15000.0
        self.T_tank_w_init = 55.0
        self.T_sur = 20.0
        self.preset = ""
        self.V_cmp_disp_cc = 0.0
        self.dV_fan_a_rated = 0.0
        self.register_variable(
            String(
                "ref",
                causality=Fmi3Causality.parameter,
                variability=Fmi3Variability.fixed,
                description=_DESCRIPTIONS["ref"],
            )
        )

        for name in ("hp_capacity", "T_tank_w_init", "T_sur"):
            self.register_variable(
                Float64(
                    name,
                    causality=Fmi3Causality.parameter,
                    variability=Fmi3Variability.fixed,
                    unit=_REAL_UNITS[name],
                    description=_DESCRIPTIONS[name],
                )
            )

        self.T0 = 7.0
        self.dhw_draw = 0.0
        self.T_sup_w = 15.0
        for name in ("T0", "dhw_draw", "T_sup_w"):
            self.register_variable(
                Float64(
                    name,
                    causality=Fmi3Causality.input,
                    variability=Fmi3Variability.continuous,
                    unit=_REAL_UNITS[name],
                    description=_DESCRIPTIONS[name],
                )
            )

        self.E_cmp = 0.0
        self.E_tot = 0.0
        self.Q_ref_tank = 0.0
        self.cop_sys = 0.0
        self.T_tank_w = self.T_tank_w_init
        self.hp_is_on = False
        self.converged = True
        self.failure_reason = "none"
        for name in ("E_cmp", "E_tot", "Q_ref_tank", "cop_sys", "T_tank_w"):
            self.register_variable(
                Float64(
                    name,
                    causality=Fmi3Causality.output,
                    variability=Fmi3Variability.continuous,
                    unit=_REAL_UNITS[name],
                    description=_DESCRIPTIONS[name],
                )
            )
        for name in ("hp_is_on", "converged"):
            self.register_variable(
                Boolean(
                    name,
                    causality=Fmi3Causality.output,
                    variability=Fmi3Variability.discrete,
                    description=_DESCRIPTIONS[name],
                )
            )
        self.register_variable(
            String(
                "failure_reason",
                causality=Fmi3Causality.output,
                variability=Fmi3Variability.discrete,
                description=_DESCRIPTIONS["failure_reason"],
            )
        )

        # Appended parameters preserve all existing FMI 3.0 value references.
        self.register_variable(
            String(
                "preset",
                causality=Fmi3Causality.parameter,
                variability=Fmi3Variability.fixed,
                description=_DESCRIPTIONS["preset"],
            )
        )
        for name in ("V_cmp_disp_cc", "dV_fan_a_rated"):
            self.register_variable(
                Float64(
                    name,
                    causality=Fmi3Causality.parameter,
                    variability=Fmi3Variability.fixed,
                    unit=_REAL_UNITS[name],
                    description=_DESCRIPTIONS[name],
                )
            )

        # Append the optional input to preserve every existing value reference.
        self.RH_out = _fmi_common.RH_OUT_USE_MODEL_DEFAULT
        self.register_variable(
            Float64(
                "RH_out",
                causality=Fmi3Causality.input,
                variability=Fmi3Variability.continuous,
                unit=_REAL_UNITS["RH_out"],
                description=_DESCRIPTIONS["RH_out"],
            )
        )

        self._hp: AirSourceHeatPumpBoiler | None = None
        self._state: DynamicState | None = None
        self._n = 0
        # Set by an fmi3Error: FMI 3.0.2 sec. 2.3.1 puts the instance in
        # Terminated, where a further doStep is not allowed until a reset.
        # PythonFMU3's fmi3Reset builds a new slave, which clears it here.
        self._terminated = False

    def to_xml(self, model_options: dict[str, str] | None = None) -> Element:
        """Build a static FMI 3.0 model description for PythonFMU3."""
        root = cast(Element, super().to_xml({} if model_options is None else model_options))
        _fmi_common.ensure_unit_definitions(
            root,
            _UNIT_SPECS,
            insertion_after={"CoSimulation", "ModelExchange", "ScheduledExecution"},
        )
        return root

    def exit_initialization_mode(self) -> None:
        """Finalize parameters and initialize the carried ASHPB state."""
        preset_kwargs = _fmi_common.preset_kwargs(
            self.preset,
            ref=self.ref,
            hp_capacity=self.hp_capacity,
            V_cmp_disp_cc=self.V_cmp_disp_cc,
            dV_fan_a_rated=self.dV_fan_a_rated,
        )
        self._hp = AirSourceHeatPumpBoiler(ref=self.ref, hp_capacity=self.hp_capacity, **preset_kwargs)
        self._state = self._hp.make_initial_state(self.T_tank_w_init)
        self._n = 0
        self.T_tank_w = self.T_tank_w_init

    def do_step(self, current_time: float, step_size: float) -> Fmi3StepResult:
        """Advance the FMU by one FMI 3.0 communication step."""
        if self._hp is None or self._state is None:
            raise RuntimeError("FMU slave used before exit_initialization_mode()")
        # A refused step changes nothing an importer can observe. FMI 3.0.2
        # sec. 2.2.4 answers illegal arguments with fmi3Error; an unusable input
        # value is discarded, which promises "the same state as before the call"
        # so the importer may repeat the step with a corrected value
        # (sec. 2.2.7.3, 4.2.1; earlyReturn must not signal an error). The
        # Modelica Association Reference FMUs log an error and discard the same way.
        if self._terminated:
            return self._reject_step(
                current_time, "the instance is terminated after an fmi3Error; reset it first", Fmi3Status.error
            )
        problem = _fmi_common.invalid_step_arguments(current_time, step_size)
        if problem is not None:
            return self._reject_step(current_time, problem, Fmi3Status.error)
        problem = _fmi_common.invalid_step_inputs(
            T0=self.T0, RH_out=self.RH_out, dhw_draw=self.dhw_draw, T_sup_w=self.T_sup_w, T_sur=self.T_sur
        )
        if problem is not None:
            return self._reject_step(current_time, problem, Fmi3Status.discard)

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
        rh_out = float(self.RH_out)
        if rh_out != _fmi_common.RH_OUT_USE_MODEL_DEFAULT:
            inputs["RH_out"] = rh_out
        self._state, res = self._hp.step(self._state, inputs, float(step_size))
        self.time = float(current_time) + float(step_size)

        self.E_cmp = _finite(res["E_cmp [W]"])
        self.E_tot = _finite(res["E_tot [W]"])
        self.Q_ref_tank = _finite(res["Q_ref_tank [W]"])
        self.cop_sys = _finite(res.get("cop_sys [-]", float("nan")))
        self.T_tank_w = _finite(res["T_tank_w [°C]"])
        self.hp_is_on = bool(res.get("hp_is_on", self.E_cmp > 0.0))
        self.converged = bool(res.get("converged", True))
        self.failure_reason = _failure_reason(res.get("failure_reason", "none"))

        self._n += 1
        return Fmi3StepResult(status=Fmi3Status.ok)

    def _reject_step(self, current_time: float, problem: str, status: Fmi3Status) -> Fmi3StepResult:
        """Log why the step was refused and return ``status`` without touching any variable."""
        # Logged with the returned status (FMI 3.0.2 sec. 2.3.1) as a debug
        # message: PythonFMU3 forwards those only while debug logging is on,
        # and its plain Log() calls the logMessage callback without a NULL check.
        self.log(_fmi_common.rejected_step_message(current_time, problem), status=status, debug=True)
        if status == Fmi3Status.error:
            self._terminated = True
        return Fmi3StepResult(status=status)
