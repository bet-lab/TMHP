"""Thin adapters that hide per-family differences of the cycle-resolved models.

Each :class:`Family` knows how to build a model, how to call
``analyze_steady`` with "source" and "demand" temperatures and a requested
duty, and which result keys hold the condenser / evaporator heat, the
compressor power and the compressor efficiencies.

Temperature convention used by the invariant checks
---------------------------------------------------
``T_evap_side_in``
    Temperature of the stream that enters the evaporator (outdoor air,
    source fluid, ground loop fluid, or room air in cooling).
``T_cond_side_in``
    Temperature of the stream that enters the condenser (tank water, room
    air in heating, outdoor air or ground-loop fluid in cooling).
"""

from __future__ import annotations

import copy
import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import Any

import tmhp

Result = dict[str, Any]


def num(result: Result, key: str, default: float = math.nan) -> float:
    """Return ``result[key]`` as float, or ``default`` when absent/non-numeric."""
    value = result.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def first_num(result: Result, *keys: str) -> float:
    """First numeric value found among ``keys`` (NaN when none is present)."""
    for key in keys:
        if key in result:
            return num(result, key)
    return math.nan


@dataclass(frozen=True)
class SteadyCase:
    """One operating point in family-neutral terms."""

    T_source: float
    """Heat-source temperature [°C] (outdoor air, source fluid, borehole wall, room in cooling)."""
    T_demand: float
    """Heat-sink temperature [°C] (tank water, room in heating, outdoor/borehole in cooling)."""
    load_W: float
    """Requested duty magnitude [W] (condenser heat in heating, evaporator heat in cooling)."""


@dataclass(frozen=True)
class Family:
    key: str
    cls: type
    mode: str  # "heating" or "cooling"
    call: Callable[[Any, SteadyCase], Result]
    q_cond_key: str
    q_evap_key: str
    t_evap_side_in: Callable[[Result, SteadyCase], float]
    t_cond_side_in: Callable[[Result, SteadyCase], float]

    @property
    def q_demand_key(self) -> str:
        """Key of the duty the user asked for."""
        return self.q_cond_key if self.mode == "heating" else self.q_evap_key

    def build(self, ref: str, **kwargs: Any) -> Any:
        """Fresh model instance (deep copy of a per-process prototype)."""
        return copy.deepcopy(_prototype(self.cls, ref, tuple(sorted(kwargs.items()))))

    def run(self, ref: str, case: SteadyCase, **kwargs: Any) -> tuple[Any, Result]:
        model = self.build(ref, **kwargs)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = self.call(model, case)
        assert isinstance(result, dict)
        return model, result


def _prototype(cls: type, ref: str, kwargs: tuple[tuple[str, Any], ...]) -> Any:
    built = _build_or_error(cls, ref, kwargs)
    if isinstance(built, Exception):
        # Re-raise the cached constructor failure so it is paid once per process.
        raise built
    return built


@cache
def _build_or_error(cls: type, ref: str, kwargs: tuple[tuple[str, Any], ...]) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return cls(ref=ref, **dict(kwargs))
        except Exception as exc:  # noqa: BLE001 - surfaced to the test by _prototype
            return exc


# ---------------------------------------------------------------------------
# result accessors shared by all families
# ---------------------------------------------------------------------------


def q_cond(family: Family, r: Result) -> float:
    return num(r, family.q_cond_key)


def q_evap(family: Family, r: Result) -> float:
    return num(r, family.q_evap_key)


def e_cmp(r: Result) -> float:
    return num(r, "E_cmp [W]")


def eta_is(r: Result) -> float:
    return first_num(r, "eta_cmp_isen [-]")


def eta_v(r: Result) -> float:
    return first_num(r, "eta_cmp_vol [-]")


def eta_em(r: Result) -> float:
    return first_num(r, "eta_cmp [-]")


def t_evap_sat(r: Result) -> float:
    return num(r, "T_ref_evap_sat [°C]")


def t_cond_sat(r: Result) -> float:
    """Lower of the two condensing saturation temperatures (dew/bubble)."""
    return min(num(r, "T_ref_cond_sat_v [°C]"), num(r, "T_ref_cond_sat_l [°C]"))


def failure_reason(r: Result) -> str:
    return str(r.get("failure_reason", "none"))


def is_reported_running(r: Result) -> bool:
    """The model claims a normal running solution."""
    return bool(r.get("hp_is_on", False)) and bool(r.get("converged", False)) and failure_reason(r) == "none"


# ---------------------------------------------------------------------------
# family registry
# ---------------------------------------------------------------------------


def _ashpb_call(m: Any, c: SteadyCase) -> Result:
    return m.analyze_steady(T_tank_w=c.T_demand, T0=c.T_source, Q_ref_tank=c.load_W)


def _boiler_call(m: Any, c: SteadyCase) -> Result:
    return m.analyze_steady(T_tank_w=c.T_demand, T_source=c.T_source, Q_ref_tank=c.load_W, T0=10.0)


def _ashp_heat(m: Any, c: SteadyCase) -> Result:
    return m.analyze_steady(Q_r_iu=-c.load_W, T0=c.T_source, T_a_room=c.T_demand, verbose=False)


def _ashp_cool(m: Any, c: SteadyCase) -> Result:
    return m.analyze_steady(Q_r_iu=c.load_W, T0=c.T_demand, T_a_room=c.T_source, verbose=False)


def _gshp_heat(m: Any, c: SteadyCase) -> Result:
    return m.analyze_steady(Q_r_iu=-c.load_W, T0=0.0, T_a_room=c.T_demand, T_bhe_wall=c.T_source)


def _gshp_cool(m: Any, c: SteadyCase) -> Result:
    return m.analyze_steady(Q_r_iu=c.load_W, T0=30.0, T_a_room=c.T_source, T_bhe_wall=c.T_demand)


def _given_source(r: Result, c: SteadyCase) -> float:
    return c.T_source


def _given_demand(r: Result, c: SteadyCase) -> float:
    return c.T_demand


def _ground_fluid_to_hp(r: Result, c: SteadyCase) -> float:
    # Ground-loop fluid leaving the borehole enters the heat pump.
    return num(r, "T_bhe_f_out [°C]")


FAMILIES: dict[str, Family] = {
    f.key: f
    for f in (
        Family(
            "ASHPB",
            tmhp.AirSourceHeatPumpBoiler,
            "heating",
            _ashpb_call,
            "Q_ref_tank [W]",
            "Q_ref_ou [W]",
            _given_source,
            _given_demand,
        ),
        Family(
            "GSHPB",
            tmhp.GroundSourceHeatPumpBoiler,
            "heating",
            _boiler_call,
            "Q_ref_tank [W]",
            "Q_ref_ground [W]",
            _given_source,
            _given_demand,
        ),
        Family(
            "WSHPB",
            tmhp.WaterSourceHeatPumpBoiler,
            "heating",
            _boiler_call,
            "Q_ref_tank [W]",
            "Q_ref_water [W]",
            _given_source,
            _given_demand,
        ),
        Family(
            "ASHP-heat",
            tmhp.AirSourceHeatPump,
            "heating",
            _ashp_heat,
            "Q_ref_iu [W]",
            "Q_ref_ou [W]",
            _given_source,
            _given_demand,
        ),
        Family(
            "ASHP-cool",
            tmhp.AirSourceHeatPump,
            "cooling",
            _ashp_cool,
            "Q_ref_ou [W]",
            "Q_ref_iu [W]",
            _given_source,
            _given_demand,
        ),
        Family(
            "GSHP-heat",
            tmhp.GroundSourceHeatPump,
            "heating",
            _gshp_heat,
            "Q_ref_iu [W]",
            "Q_ref_ground [W]",
            _ground_fluid_to_hp,
            _given_demand,
        ),
        Family(
            "GSHP-cool",
            tmhp.GroundSourceHeatPump,
            "cooling",
            _gshp_cool,
            "Q_ref_ground [W]",
            "Q_ref_iu [W]",
            _given_source,
            _ground_fluid_to_hp,
        ),
    )
}
