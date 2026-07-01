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

import math
from typing import Any, cast
from xml.etree.ElementTree import Element, SubElement

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

_REAL_UNITS = {
    "time": "s",
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


def _finite(value: float | None) -> float:
    """Sanitize a value before it crosses the FMI 3.0 boundary."""
    if value is None:
        return 0.0
    out = float(value)
    return out if math.isfinite(out) else 0.0


def _is_finite(value: Any) -> bool:
    """Return whether *value* is a finite FMI scalar."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(out)


def _failure_reason(value: object) -> str:
    """Normalize diagnostic reasons at the FMI string boundary."""
    if value is None:
        return "none"
    return str(value)


def _ensure_unit_definitions(root: Element) -> None:
    """Add FMI 3.0 unit definitions for importer-side unit checks."""
    if root.find("UnitDefinitions") is not None:
        return

    unit_definitions = Element("UnitDefinitions")
    unit_specs: tuple[tuple[str, dict[str, str]], ...] = (
        ("W", {"kg": "1", "m": "2", "s": "-3"}),
        ("s", {"s": "1"}),
        ("degC", {"K": "1", "offset": "273.15"}),
        ("m3/s", {"m": "3", "s": "-1"}),
        ("1", {}),
    )
    for name, base_attrs in unit_specs:
        unit = SubElement(unit_definitions, "Unit", attrib={"name": name})
        SubElement(unit, "BaseUnit", attrib=base_attrs)

    insertion_index = 0
    for index, child in enumerate(list(root)):
        if child.tag in {"CoSimulation", "ModelExchange", "ScheduledExecution"}:
            insertion_index = index + 1
    root.insert(insertion_index, unit_definitions)


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
        self.register_variable(
            String(
                "ref",
                causality=Fmi3Causality.parameter,
                variability=Fmi3Variability.fixed,
            )
        )
        for name in ("hp_capacity", "T_tank_w_init", "T_sur"):
            self.register_variable(
                Float64(
                    name,
                    causality=Fmi3Causality.parameter,
                    variability=Fmi3Variability.fixed,
                    unit=_REAL_UNITS[name],
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
                )
            )
        for name in ("hp_is_on", "converged"):
            self.register_variable(
                Boolean(
                    name,
                    causality=Fmi3Causality.output,
                    variability=Fmi3Variability.discrete,
                )
            )
        self.register_variable(
            String(
                "failure_reason",
                causality=Fmi3Causality.output,
                variability=Fmi3Variability.discrete,
            )
        )

        self._hp: AirSourceHeatPumpBoiler | None = None
        self._state: DynamicState | None = None
        self._n = 0

    def to_xml(self, model_options: dict[str, str] | None = None) -> Element:
        """Build a static FMI 3.0 model description for PythonFMU3."""
        root = cast(Element, super().to_xml({} if model_options is None else model_options))
        _ensure_unit_definitions(root)
        return root

    def exit_initialization_mode(self) -> None:
        """Finalize parameters and initialize the carried ASHPB state."""
        self._hp = AirSourceHeatPumpBoiler(ref=self.ref, hp_capacity=self.hp_capacity)
        self._state = self._hp.make_initial_state(self.T_tank_w_init)
        self._n = 0
        self.T_tank_w = self.T_tank_w_init

    def do_step(self, current_time: float, step_size: float) -> Fmi3StepResult:
        """Advance the FMU by one FMI 3.0 communication step."""
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
            return Fmi3StepResult(status=Fmi3Status.discard, earlyReturn=True)

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
