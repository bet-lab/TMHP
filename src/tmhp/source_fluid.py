"""Source-loop fluid of the liquid-source models: water or an antifreeze mixture.

The ground- and water-source models (GSHP, GSHPB, WSHPB) circulate a liquid
between the source (a borefield, a coil lying in surface water) and the heat
pump's source-side heat exchanger. This module says what that liquid is, where
it freezes and how cold it may run:

- :class:`SourceFluid` -- freezing point, operating lower limit and the
  properties the source-side balances use;
- :func:`resolve_source_fluid` -- the constructor argument ``source_fluid``
  (``"water"``, ``"MEG-25%"``, ``"MPG-30%"``, ``"ethanol-20%"``, ... or a
  :class:`SourceFluid`; left out: 25 % ethylene glycol for the ground-source
  models, :data:`GROUND_LOOP_FLUID_DEFAULT`, and water for the water-source
  boiler);
- :func:`limit_duty_to_source_freeze` and :func:`source_freeze_limited` -- the
  rule applied to every solved point.

Why a limit
-----------
Water freezes at 0 °C. Without the limit a model fed with a cold source
returned a running cycle whose loop "water" left the evaporator at -3 °C or
-12 °C. A real water loop does not do that: ice forms on the evaporator
plates, which are colder than the liquid, the flow stops and the plate heat
exchanger bursts. Loops that run near or below 0 °C are filled with an
antifreeze mixture (propylene or ethylene glycol, ethanol), and the heat
pump's controller stops the compressor before the mixture can freeze.

Operating lower limit
---------------------
``T_min = T_freeze + 3 K`` (:data:`FREEZE_MARGIN_K`), applied to the coldest
liquid in the loop: in heating the liquid leaving the heat pump
(``T_bhe_f_in [°C]``). Two things set the margin.

- The evaporator surface is colder than the liquid leaving it. With the
  default source heat exchanger (``UA = 0.18 * hp_capacity``) and the default
  flow (3 K loop temperature difference at the rated source heat) the
  refrigerant evaporates 2.9 K below the leaving liquid at the rated duty
  (effectiveness 0.51: ``3 K / 0.51 - 3 K``). A 3 K margin therefore keeps the
  refrigerant, and with it the plate surface, at or above the freezing point
  at that duty.
- Manufacturers of water-to-water and water-to-air units ask for antifreeze
  whenever the loop may enter the unit below 40-45 °F (4.4-7.2 °C), and for a
  freezing point 10-15 °F (5.6-8.3 K) below the lowest entering temperature
  (ClimateMaster and Carrier installation manuals: 15 °F; Enertech technical
  bulletin TB19.001: 10-15 °F; Trane GETB/WPHF: antifreeze below 45 °F). With
  the 3 K loop difference the leaving liquid is 3 K colder than the entering
  one, so those rules put the freezing point 2.6-5.3 K below the *leaving*
  liquid. 3 K is inside that range; for water it means: full duty down to
  about 6 °C entering, none below 3 °C.

A :class:`SourceFluid` built by the caller can carry any other limit.

What happens at the limit
-------------------------
A variable-speed unit does not trip the moment the limit is reached: its
controller lowers the compressor speed, the evaporator takes less heat from
the liquid and the liquid leaves warmer. The models do the same
(:func:`limit_duty_to_source_freeze`): the result is the largest duty whose
loop liquid stays at or above ``T_min``, with ``capacity_clamped == "max"``
and ``capacity_binding == "source_fluid_freezing"``. When no duty helps --
the liquid already reaches the heat pump below the limit, or it leaves below
the limit even at the compressor's lowest speed -- the compressor does not
run: the off state with ``failure_reason == "source_fluid_freezing"``.

Properties
----------
Water keeps the constants of :mod:`tmhp.constants` (1000 kg/m³, 4186 J/(kg K),
1.0 mPa s, 0.606 W/(m K)), so every result with the default fluid is unchanged
by the property path. Mixtures are read from CoolProp's incompressible
mixtures (``INCOMP::MPG[0.25]`` and the like; concentrations by mass) at
:data:`PROPERTY_REFERENCE_C` = 0 °C, the liquid inlet temperature of the
ISO 13256-1 ground-loop and EN 14511 brine rating points and the cold end an
antifreeze loop is designed for. They are constants in a run, like water's.

Viscosity of the source-side Reynolds number
--------------------------------------------
The viscosity changes far more with temperature than the other properties:
water has 1.79 mPa s at 0 °C, 1.31 at 10 °C and 1.00 at 20 °C (the constant
``mu_w``), 25 % ethylene glycol 3.70 mPa s at 0 °C and 1.90 at 20 °C.
:meth:`SourceFluid.viscosity` gives it at a temperature (water:
:func:`tmhp.constants.water_viscosity`; mixtures: the same CoolProp mixture),
and the source-side Reynolds number of the heat pump hosts -- the laminar-flow
warning and the pipe convection inside the borehole or coil resistance
``R_b`` -- takes it at the loop's design liquid temperature: the source
inlet temperature of the family's heating rating point,
:data:`GROUND_LOOP_DESIGN_INLET_C` (0 °C, EN 14511 B0 and ISO 13256-1 ground
loop) for GSHP and GSHPB and :data:`WATER_LOOP_DESIGN_INLET_C` (10 °C,
EN 14511 W10 and ISO 13256-1 ground water) for WSHPB. Those quantities are
evaluated once, at the reference flow, like ``R_b`` itself; the default glycol
is therefore evaluated at the temperature its properties are read at (0 °C,
unchanged), and water no longer at 20 °C (in a ground loop 1.79 mPa s at
0 °C, the coldest it is liquid; in the water-source boiler 1.31 mPa s). The ground-loop helpers that run during a step (hydraulic pump loss,
flow-dependent ``R_b``) keep the properties of water, see
:func:`warn_if_loop_modelled_as_water`.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import math
import re
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeVar, cast

from .compressor_envelope import CONTROL_KEY, QUALITY_KEY, SUPERHEAT_KEY
from .constants import c_w, k_w, mu_w, rho_w, water_viscosity

__all__ = [
    "FREEZE_MARGIN_K",
    "GROUND_LOOP_DESIGN_INLET_C",
    "GROUND_LOOP_FLUID_DEFAULT",
    "PROPERTY_REFERENCE_C",
    "SOURCE_FLUID_FREEZING",
    "SOURCE_FLUID_PRESETS",
    "WATER_LOOP_FLUID_DEFAULT",
    "WATER_LOOP_DESIGN_INLET_C",
    "SourceFluid",
    "freeze_violation",
    "limit_duty_to_source_freeze",
    "resolve_source_fluid",
    "source_fluid_table",
    "source_freeze_limited",
    "warn_if_loop_modelled_as_water",
]

#: ``failure_reason`` of a point whose loop liquid would run below its
#: operating limit, and ``capacity_binding`` of a duty reduced to stay on it.
SOURCE_FLUID_FREEZING = "source_fluid_freezing"
#: ``failure_detail``: the liquid reaches the heat pump below the limit already.
DETAIL_INLET = "source_inlet_below_limit"
#: ``failure_detail``: the liquid would leave the heat pump below the limit.
DETAIL_OUTLET = "source_outlet_below_limit"
#: Operating lower limit above the freezing point [K]; see the module docstring.
FREEZE_MARGIN_K = 3.0
#: Pressure the liquid properties are read at [Pa] (a pressurised loop).
_PROPERTY_PRESSURE_PA = 2.0e5
#: Temperature at which the properties of an antifreeze mixture are read [°C].
PROPERTY_REFERENCE_C = 0.0
#: Design liquid temperature of a ground loop [°C]: the source inlet of the
#: heating rating points (EN 14511 B0, ISO 13256-1 ground loop 0 °C). The
#: source-side Reynolds number of GSHP and GSHPB takes the viscosity there.
GROUND_LOOP_DESIGN_INLET_C = 0.0
#: Design liquid temperature of a water-source loop [°C]: the source inlet of
#: the heating rating points (EN 14511 W10, ISO 13256-1 ground water 10 °C),
#: for the source-side Reynolds number of WSHPB.
WATER_LOOP_DESIGN_INLET_C = 10.0
#: Tolerance of the comparison with the limit [K]. Results are solved by root
#: finding and differ between platforms at the 1e-9 level; 0.01 K is far below
#: any meaning the limit has.
FREEZE_TOL_K = 0.01
#: A reduced duty is accepted once the coldest liquid is within this of the limit [K]
#: (1 % of the 3 K loop temperature difference, i.e. about 1 % of the duty).
_BAND_K = 0.03
#: Smallest share of the request tried (the lowest rung of the envelope's reduction).
_MIN_FRACTION = 0.1
#: A reduction also stops once the duty is bracketed this closely, relative to the request.
_REL_TOL = 0.01
_MAX_TRIALS = 12
#: Result keys: the liquid leaving the heat pump (entering the ground) and entering it.
LEAVING_KEY = "T_bhe_f_in [°C]"
ENTERING_KEY = "T_bhe_f_out [°C]"
#: Request keys the boilers report, replaced by the caller's request on a reduced point.
_REQUEST_KEYS = ("Q_ref_tank_request [W]", "Q_tank_load [W]")
#: Keys :func:`tmhp.compressor_envelope.steady_envelope` puts on every steady
#: result, with the values an off state has; a refused point keeps them.
_OFF_ANNOTATIONS: dict[str, Any] = {
    "efficiency_extrapolated": False,
    SUPERHEAT_KEY: math.nan,
    QUALITY_KEY: math.nan,
    CONTROL_KEY: False,
}

#: Antifreeze bases: name accepted in ``source_fluid`` -> (CoolProp incompressible
#: mixture, description). Concentrations are mass fractions of the base in water.
_MIXTURE_BASES: dict[str, tuple[str, str]] = {
    "mpg": ("MPG", "propylene glycol"),
    "meg": ("MEG", "ethylene glycol"),
    "ethanol": ("MEA", "ethanol"),
}
#: Concentration range accepted for a mixture [% by mass].
_MIXTURE_RANGE_PCT = (5.0, 60.0)
_MIXTURE_PATTERN = re.compile(r"^\s*([a-z]+)[\s_-]*([0-9]+(?:\.[0-9]+)?)\s*%?\s*$")

#: The fluids listed in the documentation (any concentration in
#: ``_MIXTURE_RANGE_PCT`` is accepted, these are the common fills).
SOURCE_FLUID_PRESETS: tuple[str, ...] = (
    "water",
    "MEG-20%",
    "MEG-25%",
    "MEG-30%",
    "MPG-20%",
    "MPG-25%",
    "MPG-30%",
    "ethanol-20%",
    "ethanol-25%",
)


@dataclass(frozen=True)
class SourceFluid:
    """Liquid of the source loop: freezing point, operating limit and properties.

    Parameters
    ----------
    name : str
        Label used in messages and results.
    T_freeze_C : float
        Freezing point [°C].
    rho, cp, mu, k : float
        Density [kg/m³], specific heat [J/(kg K)], dynamic viscosity [Pa s]
        and thermal conductivity [W/(m K)], constant during a run.
    T_min_C : float | None
        Coldest the liquid may run [°C]. ``None`` (default):
        ``T_freeze_C + FREEZE_MARGIN_K``. It may not lie below the freezing
        point.
    coolprop : str | None
        Where :meth:`viscosity` reads the viscosity at another temperature:
        ``"Water"`` (:func:`tmhp.constants.water_viscosity`) or a CoolProp
        incompressible fluid (``"INCOMP::MEG[0.25]"``). ``None`` (default, a
        fluid built by the caller): ``mu`` at every temperature -- water built
        this way keeps the 20 °C viscosity everywhere, as before.
    """

    name: str
    T_freeze_C: float
    rho: float
    cp: float
    mu: float
    k: float
    T_min_C: float | None = None
    coolprop: str | None = None

    def __post_init__(self) -> None:
        if not (isinstance(self.name, str) and self.name):
            raise ValueError("SourceFluid name must be a non-empty string")
        if not math.isfinite(self.T_freeze_C):
            raise ValueError(f"SourceFluid T_freeze_C must be finite, got {self.T_freeze_C!r}")
        for label, value in (("rho", self.rho), ("cp", self.cp), ("mu", self.mu), ("k", self.k)):
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"SourceFluid {label} must be finite and positive, got {value!r}")
        if self.T_min_C is None:
            object.__setattr__(self, "T_min_C", self.T_freeze_C + FREEZE_MARGIN_K)
        limit = cast(float, self.T_min_C)
        if not math.isfinite(limit) or limit < self.T_freeze_C:
            raise ValueError(
                f"SourceFluid T_min_C must be finite and not below the freezing point "
                f"({self.T_freeze_C:g} °C), got {limit!r}"
            )

    @property
    def limit_C(self) -> float:
        """The operating lower limit [°C] as a float (``T_min_C`` after defaulting)."""
        return cast(float, self.T_min_C)

    def viscosity(self, T_C: float) -> float:
        """Dynamic viscosity [Pa s] at ``T_C`` [°C].

        Read from :attr:`coolprop` at ``T_C`` held between the freezing point
        and the upper end of the CoolProp mixture's range; ``mu`` when the
        fluid names no source (``coolprop=None``).
        """
        if self.coolprop is None:
            return float(self.mu)
        T = max(float(T_C), self.T_freeze_C)
        if self.coolprop == "Water":
            return float(water_viscosity(T))
        import CoolProp.CoolProp as CP

        T_max_C = float(CP.PropsSI("Tmax", "", 0, "", 0, self.coolprop)) - 273.15
        return float(CP.PropsSI("V", "T", min(T, T_max_C) + 273.15, "P", _PROPERTY_PRESSURE_PA, self.coolprop))

    @property
    def is_water(self) -> bool:
        """True for the default fluid, whose properties the ground-loop helpers assume."""
        return (self.rho, self.cp, self.mu, self.k) == (rho_w, c_w, mu_w, k_w)

    def describe(self) -> str:
        """``"water (freezes at 0.0 °C, operating limit 3.0 °C)"``."""
        return f"{self.name} (freezes at {self.T_freeze_C:.1f} °C, operating limit {self.limit_C:.1f} °C)"


#: Plain water with the constants every model has always used; its viscosity
#: at another temperature follows :func:`tmhp.constants.water_viscosity`.
WATER = SourceFluid("water", 0.0, float(rho_w), float(c_w), float(mu_w), float(k_w), coolprop="Water")


def _mixture(base: str, percent: float, label: str) -> SourceFluid:
    import CoolProp.CoolProp as CP

    coolprop, described = _MIXTURE_BASES[base]
    low, high = _MIXTURE_RANGE_PCT
    if not low <= percent <= high:
        raise ValueError(
            f"source_fluid {label!r}: the {described} concentration must be {low:g}-{high:g} % by mass, got {percent:g} %"
        )
    fluid = f"INCOMP::{coolprop}[{percent / 100.0:.6f}]"
    T_ref_K, pressure = PROPERTY_REFERENCE_C + 273.15, _PROPERTY_PRESSURE_PA
    T_freeze_C = float(CP.PropsSI("T_freeze", "T", T_ref_K, "P", pressure, fluid)) - 273.15
    rho, cp, mu, k = (float(CP.PropsSI(prop, "T", T_ref_K, "P", pressure, fluid)) for prop in ("D", "C", "V", "L"))
    name = f"{coolprop if base != 'ethanol' else 'ethanol'}-{percent:g}%"
    return SourceFluid(name, T_freeze_C, rho, cp, mu, k, coolprop=fluid)


_CACHE: dict[str, SourceFluid] = {}


#: Default loop liquid of the ground-source models (GSHP, GSHPB): 25 % ethylene
#: glycol by mass, freezing at -11.0 °C. Ground loops are rated and run with
#: brine (EN 14511 B0, ISO 13256-1 liquid entering at 0 °C), and of the common
#: fills this one stays turbulent in the default U-tube at the default flow
#: (3.7 mPa s at 0 °C against 5.5 for propylene glycol of the same strength).
GROUND_LOOP_FLUID_DEFAULT = "MEG-25%"
#: Default loop liquid of the water-source boiler (WSHPB): plain water.
WATER_LOOP_FLUID_DEFAULT = "water"


def resolve_source_fluid(source_fluid: str | SourceFluid | None, default: str = "water") -> SourceFluid:
    """The :class:`SourceFluid` a constructor's ``source_fluid`` argument names.

    ``None`` is the family's ``default`` (:data:`GROUND_LOOP_FLUID_DEFAULT`
    for GSHP and GSHPB, water for WSHPB). ``"water"`` is plain water. ``"<base>-<percent>%"`` is an
    antifreeze mixture by mass with ``base`` one of ``MPG`` (propylene
    glycol), ``MEG`` (ethylene glycol) or ``ethanol``, 5-60 %: its freezing
    point and properties come from CoolProp (``INCOMP::MPG``, ``MEG``,
    ``MEA``) at 0 °C. A :class:`SourceFluid` is returned as given. Anything
    else raises ``ValueError``.
    """
    if source_fluid is None:
        source_fluid = default
    if isinstance(source_fluid, SourceFluid):
        return source_fluid
    if not isinstance(source_fluid, str):
        raise ValueError(f"source_fluid must be a name or a SourceFluid, got {source_fluid!r}")
    key = source_fluid.strip().lower()
    if key == "water":
        return WATER
    if key not in _CACHE:
        match = _MIXTURE_PATTERN.match(key)
        if match is None or match.group(1) not in _MIXTURE_BASES:
            raise ValueError(
                f"source_fluid must be 'water', '<base>-<percent>%' with base MPG, MEG or ethanol "
                f"(e.g. 'MPG-25%'), or a SourceFluid, got {source_fluid!r}"
            )
        _CACHE[key] = _mixture(match.group(1), float(match.group(2)), source_fluid)
    return _CACHE[key]


def source_fluid_table(names: tuple[str, ...] = SOURCE_FLUID_PRESETS) -> list[dict[str, Any]]:
    """Freezing point, operating limit and properties of the listed fluids (for documentation)."""
    rows = []
    for name in names:
        fluid = resolve_source_fluid(name)
        rows.append(
            {
                "source_fluid": name,
                "T_freeze [°C]": fluid.T_freeze_C,
                "T_min [°C]": fluid.limit_C,
                "rho [kg/m3]": fluid.rho,
                "cp [J/(kg·K)]": fluid.cp,
                "mu [mPa·s]": fluid.mu * 1000.0,
                "k [W/(m·K)]": fluid.k,
            }
        )
    return rows


def warn_if_loop_modelled_as_water(
    fluid: SourceFluid, settings: Mapping[str, Any], what: str, *, chosen: bool = True
) -> None:
    """``UserWarning`` for the ground-loop options that still evaluate the loop as water.

    Only for a fluid the caller ``chosen``: a model left on its default
    liquid does not warn about it (the limitation is documented instead).

    The pipe pressure loss of the hydraulic pump model, the flow-dependent
    borehole resistance and the fluid-temperature closure of the coupled
    ground-flow solver live in :mod:`tmhp.ground_loop` and
    :mod:`tmhp.ground_flow_control`, which use the properties of water. With
    an antifreeze mixture the freezing limit and the heat pump's own
    source-side balance use the mixture; those three do not.
    """
    if fluid.is_water or not chosen:
        return
    parts = []
    if settings.get("hydraulic_pump"):
        parts.append(
            f"the hydraulic pump model computes the pipe loss with the viscosity of water "
            f"({mu_w * 1000:g} mPa s, against {fluid.mu * 1000:.2f} mPa s), so the pump power is understated"
        )
    if settings.get("variable_Rb"):
        parts.append("the flow-dependent borehole resistance uses the heat capacity of water")
    if settings.get("active"):
        parts.append("the coupled ground-flow solver closes the loop temperature with the heat capacity of water")
    if parts:
        warnings.warn(
            f"{what}: source_fluid={fluid.name!r} is applied to the freezing limit and to the heat pump's "
            "source-side heat balance, but " + "; ".join(parts) + ".",
            UserWarning,
            stacklevel=4,
        )


# ---------------------------------------------------------------------------
# The limit on a solved point
# ---------------------------------------------------------------------------


def _number(result: Mapping[str, Any], key: str) -> float:
    try:
        return float(result.get(key, math.nan))
    except (TypeError, ValueError):
        return math.nan


def _running(result: Mapping[str, Any]) -> bool:
    return bool(result.get("hp_is_on", False)) and result.get("failure_reason", "none") == "none"


def _coldest(result: Mapping[str, Any]) -> float:
    """Coldest liquid of the loop in a running result [°C] (NaN when it reports none)."""
    values = [v for v in (_number(result, LEAVING_KEY), _number(result, ENTERING_KEY)) if math.isfinite(v)]
    return min(values) if values else math.nan


def freeze_violation(result: Mapping[str, Any], fluid: SourceFluid) -> str | None:
    """``failure_detail`` when a running result's loop liquid is below the limit, else ``None``.

    :data:`DETAIL_INLET` when the liquid already reaches the heat pump below
    ``fluid.T_min_C``, :data:`DETAIL_OUTLET` when only the liquid leaving it
    is. A result that is not a running point is never a violation.
    """
    if not _running(result):
        return None
    limit = fluid.limit_C - FREEZE_TOL_K
    if _number(result, ENTERING_KEY) < limit:
        return DETAIL_INLET
    if _number(result, LEAVING_KEY) < limit:
        return DETAIL_OUTLET
    return None


def _message(fluid: SourceFluid, coldest: float, what: str) -> str:
    return (
        f"{what}: the source-loop liquid would run at {coldest:.1f} °C, below the operating limit of "
        f"{fluid.describe()}. "
    )


def _warn(model: Any, text: str, once: bool) -> None:
    if once:
        if getattr(model, "_source_freeze_warned", False):
            return
        model._source_freeze_warned = True
    warnings.warn(text, RuntimeWarning, stacklevel=5)


def limit_duty_to_source_freeze(
    model: Any,
    result: dict,
    solve: Callable[[float], Any],
    request: float,
    off: Callable[[str], dict],
    *,
    delivered_key: str = "Q_ref_tank [W]",
    once: bool = False,
    what: str = "analyze_steady",
) -> dict:
    """Keep a solved point's loop liquid at or above the fluid's operating limit.

    ``result`` is the solve at the duty ``request`` (signed as the model takes
    it); ``solve(q)`` solves the same point at a duty of magnitude ``q``;
    ``off(detail)`` is the model's off-state row for a point that does not run
    (``failure_reason = "source_fluid_freezing"``).

    A running ``result`` whose coldest liquid is at or above
    ``model.source_fluid.T_min_C`` is returned as it is, and so is a result
    that does not run. Otherwise the duty is lowered until the liquid stays on
    the limit: the coldest liquid falls almost linearly with the duty, so a
    false-position search between the refused duty and zero closes in two or
    three solves (where the liquid arrives below the limit and a smaller duty
    warms it, the first two refused duties are extrapolated; a duty another
    limit refuses bounds the search from below). The largest duty found is
    returned with
    ``capacity_clamped = "max"``, ``capacity_binding = "source_fluid_freezing"``,
    ``Q_max [W]`` and ``Q_request [W]``. When no duty runs -- the liquid enters
    below the limit while it gives heat up (:data:`DETAIL_INLET`), it leaves
    below the limit at the compressor's lowest speed, or another limit refuses
    every smaller duty -- ``off(detail)`` is returned and a ``RuntimeWarning``
    names the temperatures (``once``: only the first time in a dynamic run,
    where a reduced duty is reported the same way).
    """
    fluid: SourceFluid = model.source_fluid
    detail = freeze_violation(result, fluid)
    if detail is None:
        return result
    limit = fluid.limit_C
    magnitude = abs(float(request))
    leaving, entering = _number(result, LEAVING_KEY), _number(result, ENTERING_KEY)
    refused_at = _coldest(result)
    # A compressor on its speed floor (also one meeting a smaller request by
    # switching on and off) has no smaller running state.
    on_speed_floor = result.get("capacity_clamped") == "min"

    def refuse(reason_detail: str) -> dict:
        remedy = (
            "an antifreeze mixture, e.g. source_fluid='MPG-25%'"
            if fluid.is_water
            else "a mixture with a lower freezing point"
        )
        _warn(
            model,
            _message(fluid, refused_at, what) + "No smaller duty keeps it above the limit: the compressor does not "
            f"run (failure_reason={SOURCE_FLUID_FREEZING!r}). A loop this cold needs {remedy}."
            + (" Reported once per run." if once else ""),
            once,
        )
        row = off(reason_detail)
        row.setdefault("failure_detail", reason_detail)
        # The steady-state keys every result carries (compressor envelope), in their off values.
        for key, value in _OFF_ANNOTATIONS.items():
            if key in result:
                row.setdefault(key, value)
        return row

    # Heat rejected to the loop (cooling) warms the liquid through the heat
    # pump: its coldest point is where it arrives, and no duty changes that
    # for the better.
    if not (magnitude > 0 and math.isfinite(magnitude)) or (math.isfinite(leaving) and leaving >= entering):
        return refuse(DETAIL_INLET)
    if on_speed_floor:
        return refuse(detail)

    def quiet(q: float) -> dict:
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            out = solve(q)
        return out if isinstance(out, dict) else {}

    def delivered(row: Mapping[str, Any], fallback: float) -> float:
        value = abs(_number(row, delivered_key))
        return value if math.isfinite(value) and value > 0 else fallback

    target = limit + 0.5 * _BAND_K
    floor = _MIN_FRACTION * magnitude
    step = 0.5 * _REL_TOL * magnitude
    # The duty is bracketed between the refused point (q_hi: the liquid leaves
    # at T_hi, having entered at in_hi) and either the best running point
    # (q_lo, T_lo) or, before one is found, q_dead: the largest duty another
    # limit refuses (the speed floor, the discharge temperature at low speed),
    # below which nothing runs.
    q_hi, T_hi, in_hi = delivered(result, magnitude), leaving, entering
    q_lo = T_lo = math.nan
    q_dead = 0.0
    earlier: tuple[float, float] | None = None  # the refused point before (q_hi, T_hi)
    best: dict | None = None
    for _ in range(_MAX_TRIALS):
        if best is None and q_hi - q_dead <= _REL_TOL * magnitude:
            break  # refused for freezing just above a duty another limit refuses
        if best is not None:
            # False position between a running and a refused point.
            q = q_lo + (T_lo - target) * (q_hi - q_lo) / (T_lo - T_hi)
        elif earlier is not None and T_hi > earlier[1]:
            # Two refused duties: the liquid leaves warmer at the smaller one
            # (also where it arrives warmer there, with a borehole wall or a
            # coil behind the loop). Extrapolate to the limit.
            q = q_hi - (target - T_hi) * (earlier[0] - q_hi) / (T_hi - earlier[1])
        elif in_hi > target:
            # Without extraction the liquid leaves as it arrives: the entering
            # temperature is the leaving one at zero duty.
            q = q_hi * (in_hi - target) / (in_hi - T_hi)
        else:
            q = q_dead + 0.6 * (q_hi - q_dead)
        if best is None and q <= q_dead + step:
            q = 0.5 * (q_dead + q_hi)  # the estimate lies where nothing runs: bisect
        q = max(q, floor)
        if not q < q_hi - 0.5 * step:
            break
        row = quiet(q)
        if not _running(row):
            if best is not None:
                break
            q_dead = q
            continue
        q_row, T_row, in_row = delivered(row, q), _number(row, LEAVING_KEY), _number(row, ENTERING_KEY)
        if freeze_violation(row, fluid) is None:
            best, q_lo, T_lo = row, q_row, T_row
            if T_row <= limit + _BAND_K or q_hi - q_row <= _REL_TOL * magnitude:
                break
        else:
            # No smaller state exists (speed floor), or the liquid arrives
            # below the limit whatever the duty: nothing to search for.
            stuck = row.get("capacity_clamped") == "min" or not q_row < q_hi
            hopeless = in_row < limit - FREEZE_TOL_K and not in_row > in_hi + 1e-9
            earlier = (q_hi, T_hi)
            q_hi, T_hi, in_hi = q_row, T_row, in_row
            if stuck or hopeless:
                break
    if best is None:
        return refuse(DETAIL_INLET if in_hi < limit - FREEZE_TOL_K else DETAIL_OUTLET)

    best.update(
        {
            "capacity_clamped": "max",
            "capacity_binding": SOURCE_FLUID_FREEZING,
            "Q_max [W]": delivered(best, q_lo),
            "Q_request [W]": request,
        }
    )
    for key in _REQUEST_KEYS:
        if key in best:
            best[key] = magnitude
    if "Q_unmet [W]" in best:
        best["Q_unmet [W]"] = max(magnitude - delivered(best, q_lo), 0.0)
    if "PLR_request [-]" in best and getattr(model, "hp_capacity", 0) > 0:
        best["PLR_request [-]"] = magnitude / model.hp_capacity
    if once:
        _warn(
            model,
            _message(fluid, refused_at, what) + f"The duty is reduced to keep it on the limit "
            f"(capacity_binding={SOURCE_FLUID_FREEZING!r}; steps where no duty does are off with that "
            "failure_reason). Reported once per run.",
            once,
        )
    return best


_F = TypeVar("_F", bound=Callable[..., Any])


def source_freeze_limited(duty_arg: str, off: str, delivered_key: str = "Q_ref_tank [W]") -> Callable[[_F], _F]:
    """Decorate a single-duty solver method with the source-fluid freezing limit.

    ``duty_arg`` names the method's duty parameter (signed as the model takes
    it); ``off`` names the model method ``off(arguments, detail) -> dict``
    that builds the off-state row from the call's bound arguments. The
    decorated method returns :func:`limit_duty_to_source_freeze` of its
    result. Put it outside a compressor-envelope decorator, so the duties it
    tries are themselves inside the envelope. A ``return_dict=False`` call
    gets its one-row DataFrame back. A model that sets
    ``_source_freeze_context`` to ``"analyze_dynamic"`` around the calls of a
    dynamic run gets the limit reported once per run instead of on every call.
    """

    def decorate(function: _F) -> _F:
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            want_dict = bool(bound.arguments.get("return_dict", True))
            if "return_dict" in bound.arguments:
                bound.arguments["return_dict"] = True
            result = function(*bound.args, **bound.kwargs)
            if isinstance(result, dict):
                request = float(bound.arguments[duty_arg])

                def solve(q: float) -> Any:
                    arguments = dict(bound.arguments)
                    arguments[duty_arg] = math.copysign(q, request)
                    trial = inspect.BoundArguments(signature, arguments)  # type: ignore[arg-type]
                    return function(*trial.args, **trial.kwargs)

                context = getattr(self, "_source_freeze_context", None)
                result = limit_duty_to_source_freeze(
                    self,
                    result,
                    solve,
                    request,
                    lambda detail: getattr(self, off)(dict(bound.arguments), detail),
                    delivered_key=delivered_key,
                    once=context is not None,
                    what=context or "analyze_steady",
                )
            if want_dict or not isinstance(result, dict):
                return result
            import pandas as pd

            return pd.DataFrame([result])

        # ``__wrapped__`` stays the undecorated solve, as under the envelope decorator alone.
        wrapped.__wrapped__ = getattr(function, "__wrapped__", function)  # type: ignore[attr-defined]
        return cast(_F, wrapped)

    return decorate
