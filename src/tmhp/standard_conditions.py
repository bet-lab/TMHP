"""Standard rating conditions, and whether a model runs at them: ``operating_range()``.

Every system model has a method ``operating_range()`` that solves a short list
of standard test conditions with ``analyze_steady`` and returns one table row
per condition: whether the compressor runs there, whether it delivers the
requested duty or a reduced one, whether it cycles on and off, the
``failure_reason`` of a point it cannot run, its ``cop_sys [-]``, and the
limit in words. It answers questions like "does the default R32 ASHPB run at
A-7/W55?" before a simulation is set up::

    import tmhp

    hp = tmhp.AirSourceHeatPumpBoiler(ref="R32")
    table = hp.operating_range()
    table[["condition", "T_tank_w [°C]", "runs", "meets_request", "failure_reason", "cop_sys [-]"]]

The check is a method and not part of construction because it solves the
cycle at every condition: an air-source model is built in a few milliseconds,
and solving its standard conditions takes a few tenths of a second, 30 to 140
times as long (the measured times are in the documentation, *failure_reason
semantics*). The constructor's own
set-point check (a boiler's upper tank set point above what its compressor
envelope reaches) looks at the settings; this method solves operating points.

Conditions
----------
A condition is a :class:`StandardCondition`. The default lists below follow the
condition names and temperatures of EN 14511-2 (test conditions for rating)
and EN 14825 (part-load conditions for seasonal performance); the brine-to-air
cooling point follows ISO 13256-1 (ground-loop heat pumps). The name gives the
source side first and the load side second: ``A`` outdoor air (dry bulb),
``B`` brine and ``W`` water entering the source heat exchanger, then ``W``
water leaving the heat pump (boilers) or the indoor air (space heat pumps).
The default request is the nameplate duty ``hp_capacity``; any condition can
be asked at part load (:meth:`StandardCondition.at_part_load`), and a caller can
pass a list of their own.

Which model input a standard temperature is
-------------------------------------------
- **Outdoor air** is ``T0`` (ASHP, ASHPB). The standard's wet bulb, where it
  gives one, becomes ``RH_out`` (the frost correction of a heating point).
- **Brine or source water entering the heat pump** is ``T_source`` (GSHPB,
  WSHPB). GSHP has no such argument: its source is the fluid leaving the
  borefield, the model state ``T_bhe_f_out``. ``operating_range()`` solves
  GSHP on a shallow copy of the model whose ``T_bhe_f_out`` is the condition's
  brine temperature, so the model itself is not changed.
- **Leaving water** (the ``W35`` of ``A7/W35``) is not an input of the boiler
  models: their sink is the tank temperature ``T_tank_w``. The table solves
  ``T_tank_w = leaving water - sink_offset_K`` with ``sink_offset_K = 2.5 K``
  (:data:`LEAVING_WATER_TO_TANK_K`), the offset every air-to-water catalogue
  of the validation set states for where the manufacturer measures
  (``sink_offset_K`` in ``validation/catalogs``). ``sink_offset_K=0`` solves
  the tank at the named temperature instead -- a stricter point, since the
  water around the condenser is then as hot as the standard's leaving water.
  The table prints both temperatures.
- **Indoor air** is ``T_a_room`` (ASHP, GSHP).
- ``T0`` of GSHP, GSHPB and WSHPB is the dead state of the exergy columns
  only; the table gives it the source-side temperature.

Silence and state
-----------------
The solves make no :class:`~tmhp.OperatingLimitWarning`: what a warning would
say is the ``limit`` column, worded by the same generator
(:func:`tmhp.operating_limits.steady_message`). They do not count as the
"reported once per model and limit" of a later ``analyze_steady`` call, and
they do not touch the model's dynamic state.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from . import operating_limits

__all__ = [
    "AIR_TO_AIR",
    "AIR_TO_WATER",
    "BRINE_TO_AIR",
    "BRINE_TO_WATER",
    "COLUMNS",
    "LEAVING_WATER_TO_TANK_K",
    "StandardCondition",
    "WATER_TO_WATER",
    "operating_range",
]

LEAVING_WATER_TO_TANK_K: float = 2.5
"""How far a boiler's tank temperature sits below the leaving water of a standard name [K].

The value every air-to-water catalogue of the validation set gives as
``sink_offset_K`` (the validation harness solves ``T_tank_w`` at the
catalogue's leaving water less this offset).
"""

#: Pressure at which a wet-bulb temperature is turned into a relative humidity [Pa].
_P_ATM = 101325.0


@dataclass(frozen=True)
class StandardCondition:
    """One test condition: temperatures, mode and requested part load.

    Parameters
    ----------
    name : str
        Condition name in the standards' notation, source side first:
        ``"A-7/W55"``, ``"B0/W35"``, ``"A7/20"``.
    mode : {"heating", "cooling"}
        Heating for every boiler condition; the space heat pumps have both.
    source_side_C : float
        Outdoor-air dry bulb (``A``), or brine or water entering the source
        heat exchanger (``B``, ``W``) [°C]. In cooling it is the side the
        heat is rejected to.
    load_side_C : float
        Water leaving the heat pump (boilers; the tank is solved
        ``sink_offset_K`` below it) or the indoor air (space heat pumps) [°C].
    outdoor_wet_bulb_C : float | None
        Outdoor wet bulb [°C] of an air-source heating condition, passed to
        the model as ``RH_out``. ``None``: the standard gives none, or the
        model does not use it; the model's default humidity applies.
    part_load : float
        Requested duty as a share of the nameplate ``hp_capacity`` (> 0).
    basis : str
        Where the condition comes from.
    """

    name: str
    mode: str
    source_side_C: float
    load_side_C: float
    outdoor_wet_bulb_C: float | None = None
    part_load: float = 1.0
    basis: str = ""

    def __post_init__(self) -> None:
        if self.mode not in ("heating", "cooling"):
            raise ValueError(f"StandardCondition {self.name!r}: mode must be 'heating' or 'cooling', got {self.mode!r}")
        for label in ("source_side_C", "load_side_C", "part_load"):
            value = getattr(self, label)
            if not (isinstance(value, (int, float)) and math.isfinite(value)):
                raise ValueError(f"StandardCondition {self.name!r}: {label} must be a finite number, got {value!r}")
        if self.part_load <= 0.0:
            raise ValueError(f"StandardCondition {self.name!r}: part_load must be above 0, got {self.part_load!r}")
        wet_bulb = self.outdoor_wet_bulb_C
        if wet_bulb is not None and not (math.isfinite(wet_bulb) and wet_bulb <= self.source_side_C):
            raise ValueError(
                f"StandardCondition {self.name!r}: outdoor_wet_bulb_C must be finite and not above the dry bulb "
                f"{self.source_side_C!r}, got {wet_bulb!r}"
            )

    def at_part_load(self, part_load: float) -> StandardCondition:
        """The same condition with the request at ``part_load`` of the nameplate duty."""
        return replace(self, part_load=part_load)

    @property
    def outdoor_humidity(self) -> float | None:
        """Outdoor relative humidity (0-1) of the dry and wet bulb at 101325 Pa; ``None`` without a wet bulb."""
        if self.outdoor_wet_bulb_C is None:
            return None
        from CoolProp.HumidAirProp import HAPropsSI

        return float(
            HAPropsSI("R", "T", self.source_side_C + 273.15, "B", self.outdoor_wet_bulb_C + 273.15, "P", _P_ATM)
        )


_EN14511 = "EN 14511-2 standard rating condition"
_EN14511_APP = "EN 14511-2 application rating condition"

AIR_TO_WATER: tuple[StandardCondition, ...] = (
    StandardCondition("A7/W35", "heating", 7.0, 35.0, 6.0, basis=f"{_EN14511}, low-temperature application (30/35 °C)"),
    StandardCondition("A2/W35", "heating", 2.0, 35.0, 1.0, basis=_EN14511_APP),
    StandardCondition("A-7/W35", "heating", -7.0, 35.0, -8.0, basis=f"{_EN14511_APP}; EN 14825 condition A (-7 °C)"),
    StandardCondition(
        "A7/W55", "heating", 7.0, 55.0, 6.0, basis=f"{_EN14511}, medium-temperature application (47/55 °C)"
    ),
    StandardCondition("A2/W55", "heating", 2.0, 55.0, 1.0, basis=_EN14511_APP),
    StandardCondition(
        "A-7/W55",
        "heating",
        -7.0,
        55.0,
        -8.0,
        basis=f"{_EN14511_APP}; EN 14825 condition A (-7 °C), fixed outlet",
    ),
    StandardCondition(
        "A-10/W55",
        "heating",
        -10.0,
        55.0,
        -11.0,
        basis="EN 14825 operation limit TOL of the average climate (-10 °C), fixed outlet",
    ),
    StandardCondition("A-15/W55", "heating", -15.0, 55.0, None, basis=f"{_EN14511_APP} (no wet bulb given)"),
)
"""Air-to-water heating conditions (ASHPB): outdoor dry bulb (wet bulb) / leaving water."""

AIR_TO_AIR: tuple[StandardCondition, ...] = (
    StandardCondition("A7/20", "heating", 7.0, 20.0, 6.0, basis=f"{_EN14511}, heating (indoor 20 °C)"),
    StandardCondition("A2/20", "heating", 2.0, 20.0, 1.0, basis=f"{_EN14511_APP}; EN 14825 condition B (+2 °C)"),
    StandardCondition("A-7/20", "heating", -7.0, 20.0, -8.0, basis=f"{_EN14511_APP}; EN 14825 condition A (-7 °C)"),
    StandardCondition("A-15/20", "heating", -15.0, 20.0, None, basis=f"{_EN14511_APP} (no wet bulb given)"),
    StandardCondition(
        "A35/27",
        "cooling",
        35.0,
        27.0,
        None,
        basis=f"{_EN14511}, cooling: outdoor 35 °C (24 °C wet bulb), indoor 27 °C (19 °C wet bulb); "
        "the model is sensible-only, so the wet bulbs are not inputs",
    ),
)
"""Air-to-air conditions (ASHP): outdoor dry bulb / indoor dry bulb."""

BRINE_TO_WATER: tuple[StandardCondition, ...] = (
    StandardCondition("B0/W35", "heating", 0.0, 35.0, basis=f"{_EN14511}, low-temperature application (brine 0/-3 °C)"),
    StandardCondition("B0/W55", "heating", 0.0, 55.0, basis=f"{_EN14511}, medium-temperature application (47/55 °C)"),
    StandardCondition("B-5/W45", "heating", -5.0, 45.0, basis=_EN14511_APP),
)
"""Brine-to-water heating conditions (GSHPB): brine entering / leaving water."""

WATER_TO_WATER: tuple[StandardCondition, ...] = (
    StandardCondition("W10/W35", "heating", 10.0, 35.0, basis=f"{_EN14511}, low-temperature application (10/7 °C)"),
    StandardCondition("W10/W55", "heating", 10.0, 55.0, basis=f"{_EN14511}, medium-temperature application (47/55 °C)"),
)
"""Water-to-water heating conditions (WSHPB): source water entering / leaving water."""

BRINE_TO_AIR: tuple[StandardCondition, ...] = (
    StandardCondition(
        "B0/20", "heating", 0.0, 20.0, basis=f"{_EN14511}, brine-to-air heating; ISO 13256-1 ground loop"
    ),
    StandardCondition(
        "B25/27",
        "cooling",
        25.0,
        27.0,
        basis="ISO 13256-1 ground-loop cooling (liquid entering 25 °C, indoor 27 °C)",
    ),
)
"""Brine-to-air conditions (GSHP): brine leaving the borefield / indoor dry bulb."""

COLUMNS: tuple[str, ...] = (
    "condition",
    "mode",
    "basis",
    "part_load [-]",
    "T_source_side [°C]",
    "T_load_side [°C]",
    "T_tank_w [°C]",
    "RH_out [-]",
    "inputs",
    "runs",
    "meets_request",
    "Q_request [W]",
    "Q_delivered [W]",
    "Q_max [W]",
    "Q_min [W]",
    "capacity_clamped",
    "capacity_binding",
    "runtime_fraction [-]",
    "cop_sys [-]",
    "failure_reason",
    "failure_detail",
    "limit",
)
"""Columns of an ``operating_range()`` table, the same for every family."""

_TEXT_COLUMNS = ("capacity_clamped", "capacity_binding", "failure_detail", "limit")


@dataclass(frozen=True)
class _Family:
    """How one family is asked a condition: its default list and its ``analyze_steady`` arguments."""

    conditions: tuple[StandardCondition, ...]
    delivered_key: str
    boiler: bool
    arguments: Callable[[StandardCondition, float, float | None, float], dict[str, Any]]
    on_copy: bool = False


def _air_to_water(c: StandardCondition, duty: float, humidity: float | None, offset: float) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"T_tank_w": c.load_side_C - offset, "T0": c.source_side_C, "Q_ref_tank": duty}
    if humidity is not None:
        kwargs["RH_out"] = humidity
    return kwargs


def _source_to_water(c: StandardCondition, duty: float, humidity: float | None, offset: float) -> dict[str, Any]:
    return {"T_tank_w": c.load_side_C - offset, "T_source": c.source_side_C, "Q_ref_tank": duty, "T0": c.source_side_C}


def _space_load(c: StandardCondition, duty: float) -> float:
    return -duty if c.mode == "heating" else duty  # the space models' sign: heating below zero


def _air_to_air(c: StandardCondition, duty: float, humidity: float | None, offset: float) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"Q_r_iu": _space_load(c, duty), "T0": c.source_side_C, "T_a_room": c.load_side_C}
    if humidity is not None and c.mode == "heating":
        kwargs["RH_out"] = humidity
    return kwargs


def _brine_to_air(c: StandardCondition, duty: float, humidity: float | None, offset: float) -> dict[str, Any]:
    return {"Q_r_iu": _space_load(c, duty), "T0": c.source_side_C, "T_a_room": c.load_side_C}


_FAMILIES: dict[str, _Family] = {
    "air_to_water": _Family(AIR_TO_WATER, "Q_ref_tank [W]", True, _air_to_water),
    "brine_to_water": _Family(BRINE_TO_WATER, "Q_ref_tank [W]", True, _source_to_water),
    "water_to_water": _Family(WATER_TO_WATER, "Q_ref_tank [W]", True, _source_to_water),
    "air_to_air": _Family(AIR_TO_AIR, "Q_ref_iu [W]", False, _air_to_air),
    "brine_to_air": _Family(BRINE_TO_AIR, "Q_ref_iu [W]", False, _brine_to_air, on_copy=True),
}


def _with_borefield_outlet(model: Any, T_C: float) -> Any:
    """A shallow copy of a GSHP whose borefield-outlet fluid (the source it is given) is at ``T_C``."""
    target = copy.copy(model)
    target.T_bhe_f_out = T_C
    target.T_bhe_f_out_K = T_C + 273.15
    return target


def _number(row: Mapping[str, Any], key: str) -> float:
    try:
        value = float(row.get(key, math.nan))
    except (TypeError, ValueError):
        return math.nan
    return value


def _label(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return str(value)


def _solve(model: Any, family: _Family, condition: StandardCondition, sink_offset_K: float) -> dict[str, Any]:
    """One table row: the condition, what was solved, and what came out."""
    duty = condition.part_load * float(model.hp_capacity)
    humidity = condition.outdoor_humidity
    kwargs = family.arguments(condition, duty, humidity, sink_offset_K)
    target = _with_borefield_outlet(model, condition.source_side_C) if family.on_copy else model
    # A scope of our own: the analyze_steady call issues nothing and leaves
    # what its refusals knew (the refused discharge temperature, ...) for the
    # limit text below, worded by the generator of the warnings.
    scope = operating_limits._Scope()
    token = operating_limits._SCOPE.set(scope)
    try:
        result = target.analyze_steady(**kwargs, return_dict=True)
    finally:
        operating_limits._SCOPE.reset(token)
    row: Mapping[str, Any] = result

    reason = _label(row.get("failure_reason")) or "none"
    runs = bool(row.get("hp_is_on", False)) and reason == "none"
    request = _number(row, "Q_request [W]")
    if not math.isfinite(request):
        request = kwargs.get("Q_ref_tank", kwargs.get("Q_r_iu", math.nan))
    delivered = _number(row, family.delivered_key)
    delivered = delivered if math.isfinite(delivered) else 0.0
    meets = runs and abs(delivered) >= abs(request) * (1.0 - operating_limits.SHORTFALL_REL_TOL)
    message = operating_limits.steady_message(
        target,
        row,
        operating_limits._echo(target, "analyze_steady", kwargs),
        family.delivered_key,
        scope.notes,
    )
    return {
        "condition": condition.name,
        "mode": condition.mode,
        "basis": condition.basis,
        "part_load [-]": condition.part_load,
        "T_source_side [°C]": condition.source_side_C,
        "T_load_side [°C]": condition.load_side_C,
        "T_tank_w [°C]": kwargs["T_tank_w"] if family.boiler else math.nan,
        "RH_out [-]": kwargs.get("RH_out", math.nan),
        "inputs": kwargs,
        "runs": runs,
        "meets_request": meets,
        "Q_request [W]": request,
        "Q_delivered [W]": delivered,
        "Q_max [W]": _number(row, "Q_max [W]"),
        "Q_min [W]": _number(row, "Q_min [W]"),
        "capacity_clamped": _label(row.get("capacity_clamped")),
        "capacity_binding": _label(row.get("capacity_binding")),
        "runtime_fraction [-]": _number(row, "runtime_fraction [-]"),
        "cop_sys [-]": _number(row, "cop_sys [-]"),
        "failure_reason": reason,
        "failure_detail": _label(row.get("failure_detail")),
        "limit": None if message is None else message[0],
    }


def operating_range(
    model: Any,
    family: str,
    conditions: Iterable[StandardCondition] | None = None,
    *,
    sink_offset_K: float = LEAVING_WATER_TO_TANK_K,
) -> pd.DataFrame:
    """Solve rating conditions with ``model.analyze_steady`` and tabulate whether the machine runs at each.

    The implementation behind every model's ``operating_range()`` method;
    call the method.

    Parameters
    ----------
    model : object
        A system model (ASHP, GSHP, ASHPB, GSHPB, WSHPB).
    family : {"air_to_water", "brine_to_water", "water_to_water", "air_to_air", "brine_to_air"}
        The model's kind, which selects the default conditions and how a
        condition becomes ``analyze_steady`` arguments.
    conditions : iterable of StandardCondition, optional
        Conditions to solve; the family's default list
        (:data:`AIR_TO_WATER`, ...) when omitted.
    sink_offset_K : float
        Boilers: the tank is solved this far below the condition's leaving
        water [K] (:data:`LEAVING_WATER_TO_TANK_K`; 0 solves the tank at the
        named temperature). Not used by the space heat pumps.

    Returns
    -------
    pd.DataFrame
        One row per condition, in order, with the columns :data:`COLUMNS`:

        - ``condition``, ``mode``, ``basis``, ``part_load [-]``: the condition.
        - ``T_source_side [°C]``, ``T_load_side [°C]``: its temperatures as
          named (leaving water for a boiler, indoor air for a space heat pump).
        - ``T_tank_w [°C]``: the tank temperature a boiler was solved at
          (``T_load_side - sink_offset_K``; NaN for a space heat pump).
        - ``RH_out [-]``: the outdoor humidity passed (NaN: the model default).
        - ``inputs``: the keyword arguments given to ``analyze_steady``
          (GSHP: on a copy whose ``T_bhe_f_out`` is ``T_source_side``).
        - ``runs``: the compressor runs (``hp_is_on`` and ``failure_reason ==
          "none"``).
        - ``meets_request``: it runs and delivers the request (within 0.1 %).
        - ``Q_request [W]``, ``Q_delivered [W]``: requested and delivered duty,
          signed as the model signs them (a space heat pump's heating is
          below zero); ``Q_delivered`` is ``Q_ref_tank [W]`` or
          ``Q_ref_iu [W]`` of the row.
        - ``Q_max [W]``, ``Q_min [W]``, ``capacity_clamped``,
          ``capacity_binding``, ``runtime_fraction [-]``, ``cop_sys [-]``,
          ``failure_reason``, ``failure_detail``: the result keys of the row
          (see *Result keys*). ``capacity_clamped == "max"`` is a reduced
          duty, ``"min"`` an on/off mean (``runtime_fraction [-]`` below 1).
        - ``limit``: what an :class:`~tmhp.OperatingLimitWarning` would say
          about the point -- the call, the limit in words with its value, and
          what removes it -- or ``None`` when the point meets its request or
          is an on/off mean.
    """
    try:
        spec = _FAMILIES[family]
    except KeyError:
        raise ValueError(f"unknown family {family!r}; one of {sorted(_FAMILIES)}") from None
    if not (isinstance(sink_offset_K, (int, float)) and math.isfinite(sink_offset_K) and sink_offset_K >= 0.0):
        raise ValueError(f"sink_offset_K must be a finite number >= 0 [K], got {sink_offset_K!r}")
    chosen = spec.conditions if conditions is None else tuple(conditions)
    for condition in chosen:
        if not isinstance(condition, StandardCondition):
            raise TypeError(f"conditions must be StandardCondition instances, got {type(condition).__name__}")
        if spec.boiler and condition.mode != "heating":
            raise ValueError(f"{type(model).__name__} heats water only; condition {condition.name!r} is cooling")
    rows = [_solve(model, spec, condition, float(sink_offset_K)) for condition in chosen]
    table = pd.DataFrame(rows, columns=list(COLUMNS))
    for column in _TEXT_COLUMNS:  # a missing label is None in every row, not NaN in some
        table[column] = pd.Series([row[column] for row in rows], index=table.index, dtype=object)
    return table
