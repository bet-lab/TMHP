"""Compressor operating envelope shared by every cycle-resolved heat-pump model.

A real compressor runs inside an envelope that its manufacturer publishes and
its controller enforces. Five limits matter for the single-stage vapour
compression cycle TMHP solves, and this module holds the rule for each so the
five families (ASHP, GSHP, ASHPB, GSHPB, WSHPB) apply them identically.

Pressure ratio (``PR_cycle_min`` / ``PR_cycle_max``)
    The physically primary lift limit is the **pressure ratio**
    ``PR = P_cond / P_evap``, not a fixed temperature lift: the lift needed to
    reach a given ``PR`` maps non-linearly through the saturation curve and
    differs by refrigerant and level, so a fixed minimum lift (e.g. 20 K) over-
    or under-states the true limit; a ``PR`` floor is transferable. Scroll axial
    loading / built-in volume ratio, the oil-feed pressure difference and the
    collapse of isentropic efficiency place the floor near ``PR ~ 1.5``
    (1.3--2.0); below it the cycle is **clamped** onto the floor. The ceiling is
    the single-stage scroll's hardware limit, about ``11:1`` (self-unloading of
    AC scrolls; Cuevas & Lebrun 2009; Bertsch & Groll 2008; Gayeski et al.
    2011); above it the point is **rejected** (:data:`PR_ABOVE_MAX`). Every
    family defaults to :data:`PR_CYCLE_MAX_SINGLE_STAGE`. Higher ratios belong
    to two-stage or vapour-injection machines, which the single-stage cycle
    does not describe. Since the discharge-temperature limit below binds first
    almost everywhere, the ratio ceiling acts as a hardware sanity bound.

Discharge temperature (``T_dis_max``)
    Lubricant breakdown and valve / motor temperature limit the gas leaving the
    compressor. Compressor application guides give the protection setting:
    Danfoss SH scrolls (R410A) "discharge gas temperature must not exceed
    135 degC"; Danfoss PSH (R410A/R454B) start wet injection at 121 degC with a
    135 degC maximum safety value; the external discharge-gas protection of
    Danfoss DSF scrolls (R32, which "leads to higher discharge temperatures
    than R410A") opens at 150 degC at most. Unit controllers act earlier on
    the discharge *pipe* temperature (Daikin room units reduce frequency from
    about 100 degC and stop at 110--120 degC). A point whose computed
    discharge temperature exceeds the limit is **not an operating point**
    (:data:`DISCHARGE_TEMP_ABOVE_MAX`). The defaults
    (:data:`DISCHARGE_TEMP_MAX_C`, :func:`default_discharge_temp_max`) are the
    *protection* values -- R32 150 degC, every other fluid 135 degC -- not the
    controllers' earlier set points: the cycle's discharge state is the gas
    leaving the compression (the default models send the share of the loss
    work to the gas that measured discharge temperatures give, 0.58 for the
    rotary types and 0.77 for the scroll,
    :mod:`tmhp.compressor_efficiency`), with no shell or pipe heat loss
    after it, which is the temperature the protection values refer to; a
    controller's pipe sensor reads lower. With a caller's efficiencies that
    put every loss into the gas (``eta_cmp`` near 1) it is an upper estimate
    of the machine's.

    Discharge-temperature control comes first (:func:`discharge_controlled`,
    ``x_suction_min``). A unit whose discharge runs hot does not stop: its
    controller opens the expansion valve, so the suction superheat falls, and
    then lets the suction go wet or injects liquid. The cycle at a candidate
    operating point is therefore re-solved, compressor speed included, first
    with the suction superheat reduced from its value towards 0 K until the
    discharge temperature equals ``T_dis_max``, then -- if 0 K is not enough --
    with a two-phase suction of quality ``x`` lowered towards
    ``x_suction_min`` (default :data:`X_SUCTION_MIN_DEFAULT` = 0.9). Wet
    suction is modelled as a homogeneous two-phase state at the evaporating
    pressure (see :func:`tmhp.refrigerant.calc_ref_state`); the evaporator
    outlet is then two-phase, the swept mass flow follows the homogeneous
    density, and the isentropic work is that of the two-phase state, so the
    cycle energy balance stays closed. The efficiency maps were fitted to dry
    suction, so applying them to wet suction is an approximation. Only a point
    that is still above the limit at ``x_suction_min`` is outside the envelope.
    ``x_suction_min=1`` keeps the superheat stage only; ``None`` switches the
    control off.

    How a family answers a point the control cannot bring inside the limit: the space-conditioning models
    (ASHP, GSHP) reject the candidate inside the operating-point search, so the
    search moves to a smaller lift if one carries the duty, and otherwise the
    existing capacity search returns the largest duty the envelope allows with
    ``capacity_clamped='max'`` and ``capacity_binding='discharge_temperature'``.
    The boilers (fixed requested duty) check the solved operating point;
    ``analyze_steady`` then tries smaller duties (:func:`steady_envelope`) and
    returns the largest one inside the limit the same way, or the off state
    with ``failure_reason='discharge_temp_above_max'`` when even a small duty
    exceeds it. A dynamic boiler step does the same with its one cycle solve
    per step (:func:`reduce_duty_within_envelope`): it runs at the reduced
    duty, or is an off step with that reason when no duty fits. The boilers
    answer every limit a smaller duty cures this way -- the pressure-ratio
    ceiling and the source heat exchanger's range as well
    (:func:`duty_reduction_binding`); ``capacity_binding`` names the limit.
    The envelope also has a lower side: at a large lift the isentropic
    efficiency falls towards low speed, so the discharge runs hotter there
    (past the limit even with the control used up) -- while larger duties
    run. A boiler's
    ``analyze_steady`` answers a request refused there with the smallest duty
    that runs (:func:`raise_duty_within_envelope`, ``capacity_clamped='min'``),
    which is then reported as the mean of on/off cycling like a request below
    the minimum speed; :class:`~tmhp.AirSourceHeatPump` does the same in its
    own search. ``failure_reason='discharge_temp_above_max'`` is left for a
    condition at which no duty runs.

Condensing pressure (``T_cond_max``)
    The top of every published envelope is a maximum condensing temperature,
    which is a maximum high-side pressure: the shell, the scroll set and the
    motor are built for it, and the unit's high-pressure switch stops the
    compressor just above it. ``T_cond_max`` is that limit as the saturation
    temperature of the condensing pressure [degC]; the check compares
    pressures, ``P_cond > P_dew(T_cond_max)`` (for a zeotropic blend the
    envelope temperature is the dew point; the cycle's own condensing
    temperature is the bubble point, lower by the glide -- R407C reaches the
    65 degC limit at 61.2 degC). The defaults (:data:`CONDENSING_TEMP_MAX_C`,
    :func:`default_condensing_temp_max`) are the upper end of the high-side
    working range of compressors sold for each fluid:

    ===========  ======  =====================================================
    fluid        degC    basis
    ===========  ======  =====================================================
    R410A        65      Copeland ZP*K3E/K5E application envelope tops at
                         about 65.5 degC (replacement guidelines C050202,
                         fig. 2); Danfoss PSH019-039 "65 degC saturated
                         condensing temperature"; Danfoss DSH working range to
                         41.7 bar(g) = 64.9 degC.
    R32          65      Danfoss DSF090 working range high side to 43 bar(g)
                         = 65.2 degC; Copeland YP (R32) is stated to cover the
                         field of application of the R410A scrolls.
    R407C        65      Danfoss HHP heat-pump scrolls: working range high
                         side to 27.4 bar(g) = 65.0 degC dew (Copeland ZR
                         envelope: about 67 degC).
    R290         75      Copeland ZH04KCU-ZH16KCU heat-pump scrolls: high-side
                         PS 28 bar(g) = 76.0 degC (Danfoss VZN: working range
                         to 31.5 bar(g) = 82 degC).
    R134a        80      Danfoss VZN with R513A (the R134a pressure class):
                         working range high side to 25.9 bar(g), 81.0 degC as
                         R134a.
    R1234yf      75      no envelope published for this fluid was found; the
                         R134a pressure class (25.9 bar(g)) would allow
                         83.3 degC. 75 degC is about 20 K below the critical
                         point (94.7 degC), as for R290 (96.7 degC).
    R1234ze(E)   90      Danfoss PSG605-800: working range to 25.3 bar(g)
                         = 93.0 degC.
    R600a        100     Danfoss PSG605-800: working range to 19.6 bar(g)
                         = 102.0 degC.
    R717         70      the 40 bar class of ammonia heat-pump compressors
                         (GEA Grasso V HP, 39 bar, hot water about 70 degC);
                         50 and 63 bar machines reach 82-95 degC.
    ===========  ======  =====================================================

    No default exceeds the saturation temperature at
    :data:`CONDENSING_PRESSURE_MAX_REDUCED` (0.9) of the critical pressure,
    and that alone is the default of a fluid not in the table: no published
    envelope reaches it, and next to the critical point the condenser has
    almost no latent heat left (R744 gets 26.4 degC: its heat pumps are
    transcritical, which this subcritical cycle does not describe). A machine
    with a different high-pressure rating is given its own ``T_cond_max``;
    ``None`` switches the check off.

    A point above the limit is **not an operating point**
    (:data:`CONDENSING_TEMP_ABOVE_MAX`), and it is answered like the discharge
    limit below, because the condensing temperature is the demand-side
    temperature plus the condenser approach and the approach shrinks with the
    duty: the space-conditioning models reject the candidate inside the
    operating-point search, the boilers reduce the duty
    (:func:`reduce_duty_within_envelope`) and report
    ``capacity_clamped='max'`` with ``capacity_binding='condensing_temperature'``.
    The boundary duty is found by a secant on the condensing temperature
    (:func:`_duty_on_condensing_limit`: three cycle solves in all, instead of
    the ladder and bisection of the other limits), so a dynamic step that runs
    against the limit stays cheap. When the demand side is itself at the limit
    (a tank at ``T_cond_max``) no duty runs: the off state with
    ``failure_reason='condensing_temp_above_max'``.

    A boiler's thermostat is told where the envelope ends
    (:func:`limit_tank_setpoints_to_envelope`): an upper set point above the
    tank temperature at which the nameplate duty condenses on the limit
    (:func:`condensing_tank_cutout`) is capped there at construction, with an
    :class:`~tmhp.OperatingLimitWarning`. A dynamic step solves one cycle and applies its heat over
    the step, so without the cap a long step overran the limit and the
    temperature at which the heat pump stopped depended on the step.

    That is the boiler constructors' ``condensing_limit_control="stop"``, the
    default: a fixed-speed unit, or one whose controller ends the charge on
    its high-pressure protection. ``"reduce_duty"`` is the inverter unit,
    which slows the compressor on the limit and keeps charging: a dynamic step
    then runs past the nameplate cut-out at the largest duty inside the limit,
    ``K_tank_hx (T_reach - T)`` (:func:`condensing_tank_reach`), which is what
    ``analyze_steady`` reports there. The tank approaches ``T_reach`` as a
    first-order lag with the time constant ``C_tank / K_tank_hx`` (about five
    minutes for the default 151 L tank and 15 kW unit), which the in-step
    thermostat follows in closed form
    (:func:`tmhp.dynamic_context.resolve_step_operation`): the run time and
    the heat of a step on this limit do not depend on the step (the tank
    temperature a step ends at is still one implicit solve with the loss at
    its end, as with ``"stop"``, so a tank that loses much heat needs short
    steps). The charge ends where the limit leaves less than
    the smallest duty the compressor runs at continuously
    (:func:`smallest_continuous_duty`: the speed floor, or the envelope's
    lower side). That duty depends on the source, so each step applies the
    stop of its own conditions (:func:`smallest_duty_on_condensing_limit`);
    the thermostat's cap is the stop at one stated reference source
    (:func:`limit_tank_setpoints_to_charge_limit`).

    That treatment covers the source states in which the condensing limit is
    the one that governs the stretch from the nameplate cut-out to that stop.
    Each source state is checked once, with three cycles
    (:func:`condensing_stretch`). Where another limit governs somewhere on
    the stretch -- the source fluid's freezing limit, a discharge-temperature
    ceiling, the compressor's speed ceiling at a cold source -- reduced-duty
    charging is withheld for the step: it is solved exactly as ``"stop"``
    solves it, and the row names what withheld it
    (``reduced_duty_withheld``).

Speed (``rps_min`` / ``rps_max``)
    ``rps_min`` is the inverter's floor (15 rev/s). ``rps_max`` defaults to
    :func:`default_rps_max` of the rated speed the model solves at its rating
    point: :data:`RPS_MAX_ABSOLUTE` (120 rev/s), or 1.5 times the rated speed
    where that is higher. The rated
    speed itself is solved over ``[rps_min, RATED_SPEED_SEARCH_MAX]``.

Efficiency domain
    The default efficiency models are followed as they are outside their
    (PR, N) data (:mod:`tmhp.compressor_efficiency`); the envelope sets no
    limit on an efficiency. Steady results carry
    ``efficiency_extrapolated`` so a caller can see a point evaluated outside
    the data hull of a default model.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import math
import warnings
from collections.abc import Callable, Iterator, Mapping
from functools import lru_cache, wraps
from typing import Any, Literal, TypeVar, cast

from scipy.optimize import brentq

from . import calc_util as cu
from .compressor_efficiency import efficiency_extrapolated
from .compressor_speed import CAPACITY_CLAMPED_MAX
from .dynamic_context import T_WATER_BOIL_C
from .operating_limits import OperatingLimitWarning
from .refrigerant import _props

__all__ = [
    "check_pr_envelope",
    "PR_BELOW_MIN",
    "PR_ABOVE_MAX",
    "PR_CYCLE_MAX_SINGLE_STAGE",
    "DISCHARGE_TEMP_ABOVE_MAX",
    "DISCHARGE_TEMP_MAX_C",
    "DISCHARGE_TEMP_MAX_DEFAULT_C",
    "DISCHARGE_TOL_K",
    "CONDENSING_TEMP_ABOVE_MAX",
    "CONDENSING_TEMP_MAX_C",
    "CONDENSING_PRESSURE_MAX_REDUCED",
    "CONDENSING_TOL_K",
    "ENVELOPE_REJECTIONS",
    "default_condensing_temp_max",
    "resolve_condensing_temp_max",
    "condensing_pressure_max",
    "condensing_temp_exceeded",
    "condensing_violation",
    "check_rated_condensing",
    "condensing_tank_reach",
    "condensing_tank_cutout",
    "limit_tank_setpoints_to_envelope",
    "CONDENSING_LIMIT_CONTROLS",
    "CONTROL_STOP",
    "CONTROL_REDUCE_DUTY",
    "resolve_condensing_limit_control",
    "smallest_continuous_duty",
    "smallest_duty_on_condensing_limit",
    "steady_smallest_duty",
    "limit_tank_setpoints_to_charge_limit",
    "on_condensing_limit",
    "condensing_stretch",
    "DUTY_MATCH_RTOL",
    "WITHHELD_NO_SMALLER_DUTY",
    "BINDING_DISCHARGE",
    "BINDING_CONDENSING",
    "BINDING_PRESSURE_RATIO",
    "BINDING_HEAT_EXCHANGER",
    "BINDING_CYCLE",
    "BINDING_COMPRESSOR",
    "CYCLE_KEY",
    "duty_reduction_binding",
    "default_discharge_temp_max",
    "resolve_discharge_temp_max",
    "discharge_temp_exceeded",
    "discharge_violation",
    "envelope_violation",
    "envelope_excess",
    "RPS_MAX_ABSOLUTE",
    "RPS_MAX_MIN_PER_RATED",
    "RATED_SPEED_SEARCH_MAX",
    "default_rps_max",
    "efficiency_extrapolated_at",
    "steady_envelope",
    "reduce_duty_within_envelope",
    "raise_duty_within_envelope",
    "remembering_smallest_duties",
    "remember_smallest_duty",
    "X_SUCTION_MIN_DEFAULT",
    "resolve_x_suction_min",
    "discharge_controlled",
]

#: Reason code: pressure ratio is below ``PR_cycle_min`` (floor -> clamp).
PR_BELOW_MIN = "pr_below_min"
#: Reason code: pressure ratio is above ``PR_cycle_max`` (ceiling -> reject).
PR_ABOVE_MAX = "pr_above_max"
#: Default ``PR_cycle_max`` of every family: the single-stage scroll hardware limit.
PR_CYCLE_MAX_SINGLE_STAGE = 11.0

#: ``failure_reason``: every duty tried, smaller and larger, exceeds ``T_dis_max``.
DISCHARGE_TEMP_ABOVE_MAX = "discharge_temp_above_max"
#: ``failure_reason``: even the smallest duty tried condenses above ``T_cond_max``.
CONDENSING_TEMP_ABOVE_MAX = "condensing_temp_above_max"
#: Reasons with which the envelope rejects a candidate of the space-conditioning
#: searches (``_last_pr_event[0]``); they are reported as the ``failure_reason``.
ENVELOPE_REJECTIONS = frozenset({DISCHARGE_TEMP_ABOVE_MAX, CONDENSING_TEMP_ABOVE_MAX})
#: ``capacity_binding``: the duty was reduced to stay within ``T_dis_max``.
BINDING_DISCHARGE = "discharge_temperature"
#: ``capacity_binding``: the duty was reduced to stay within ``T_cond_max``.
BINDING_CONDENSING = "condensing_temperature"
#: ``capacity_binding``: the duty was reduced to stay within ``PR_cycle_max``.
BINDING_PRESSURE_RATIO = "pressure_ratio"
#: ``capacity_binding``: the duty was reduced to what the source heat exchanger
#: carries within its flow range (ASHPB reports ``"outdoor_coil"``).
BINDING_HEAT_EXCHANGER = "source_heat_exchanger"
#: ``capacity_binding``: the next larger duty has no valid cycle, for a reason none of the above names.
BINDING_CYCLE = "cycle"
#: ``capacity_binding``: the compressor runs at its speed ceiling (``rps_max``).
BINDING_COMPRESSOR = "compressor"

#: Default discharge-temperature limit by refrigerant [degC]; see the module docstring.
DISCHARGE_TEMP_MAX_C: Mapping[str, float] = {"R32": 150.0}
#: Default for every refrigerant not in :data:`DISCHARGE_TEMP_MAX_C` [degC].
DISCHARGE_TEMP_MAX_DEFAULT_C = 135.0

#: Default maximum condensing temperature by refrigerant [degC] (saturation
#: temperature of the highest condensing pressure; dew point for a blend).
#: Sources in the module docstring.
CONDENSING_TEMP_MAX_C: Mapping[str, float] = {
    "R410A": 65.0,
    "R32": 65.0,
    "R407C": 65.0,
    "R290": 75.0,
    "R134a": 80.0,
    "R1234yf": 75.0,
    "R1234ze(E)": 90.0,
    "R600a": 100.0,
    "IsoButane": 100.0,
    "R717": 70.0,
}
#: No default condensing pressure exceeds this fraction of the critical
#: pressure; it alone is the default of a refrigerant not in
#: :data:`CONDENSING_TEMP_MAX_C`.
CONDENSING_PRESSURE_MAX_REDUCED = 0.9
#: Tolerance of every condensing-temperature comparison [K]. The duty
#: reduction solves ``T_cond = T_cond_max`` for the duty, so the check that
#: follows has to accept that root's residual (round-off for a tank whose
#: condensing approach is ``Q / UA``); 0.01 K is far below any meaning the
#: limit has, and it keeps the verdict the same on every platform.
CONDENSING_TOL_K = 0.01

#: Default lowest suction vapour quality the discharge-temperature control may use.
#:
#: Two-phase suction (wet compression) is one of the methods experimentally used
#: to hold the discharge temperature of R32 scroll compressors (Wang et al.,
#: International Compressor Engineering Conference 2014, paper 1595; Yang et
#: al., Int. J. Refrig. 2015), alongside liquid and two-phase injection; lower
#: suction quality lowers the discharge temperature but raises the risk of
#: over-wet compression and liquid slugging. No published lower bound on the
#: suction quality was found: 0.9 is a conservative default without a
#: quantitative basis (at most 10 % liquid by mass, of the order of the
#: liquid-injection ratios used for discharge-temperature control). Override it
#: with the constructor argument ``x_suction_min``.
X_SUCTION_MIN_DEFAULT = 0.9

#: ``rps_max`` default [rev/s]: 7200 rpm, the upper end of the speed ranges inverter rotary compressors
#: of heat-pump size are specified for (see :func:`default_rps_max`) ...
RPS_MAX_ABSOLUTE = 120.0
#: ... and never less than this multiple of the rated speed (catalogue maximum / rated capacity <= 1.52).
RPS_MAX_MIN_PER_RATED = 1.5
#: Upper speed bound for *solving* the rated speed when ``rps_max`` is not given [rev/s].
RATED_SPEED_SEARCH_MAX = 150.0

#: Tolerance of every discharge-temperature comparison [K]. The control solves
#: T_dis = T_dis_max by root finding, so the check that follows has to accept
#: the root's own residual (1e-5 K for the predicted closure, ~1e-3 K for the
#: bracketing fallback); 0.01 K is far below any meaning the limit has.
DISCHARGE_TOL_K = 0.01
_DISCHARGE_TOL_K = DISCHARGE_TOL_K


def check_pr_envelope(pr: float, pr_min: float, pr_max: float) -> str | None:
    """Classify a compressor pressure ratio against the operating envelope.

    Returns ``None`` when ``pr_min <= pr <= pr_max`` (feasible); otherwise
    :data:`PR_BELOW_MIN` or :data:`PR_ABOVE_MAX`.
    """
    if pr < pr_min:
        return PR_BELOW_MIN
    if pr > pr_max:
        return PR_ABOVE_MAX
    return None


def _fluid_name(ref: str) -> str:
    # "HEOS::R32" and "R32" name the same fluid; mixtures keep their spelling.
    return ref.split("::", 1)[1] if ref.upper().startswith("HEOS::") and "[" not in ref else ref


def default_discharge_temp_max(ref: str) -> float:
    """Default discharge-temperature limit for refrigerant ``ref`` [degC]."""
    return float(DISCHARGE_TEMP_MAX_C.get(_fluid_name(ref), DISCHARGE_TEMP_MAX_DEFAULT_C))


def resolve_discharge_temp_max(value: float | Literal["auto"] | None, ref: str) -> float | None:
    """Constructor argument -> limit: ``"auto"`` the refrigerant default, ``None`` no check."""
    if value is None:
        return None
    if isinstance(value, str):
        if value != "auto":
            raise ValueError(f"T_dis_max must be a temperature in degC, 'auto' or None, got {value!r}")
        return default_discharge_temp_max(ref)
    limit = float(value)
    if not math.isfinite(limit):
        raise ValueError("T_dis_max must be finite (use None to switch the check off)")
    return limit


def resolve_x_suction_min(value: float | None) -> float | None:
    """Constructor argument -> lowest suction quality of the control (``None``: no control)."""
    if value is None:
        return None
    x = float(value)
    if not (math.isfinite(x) and 0.0 < x <= 1.0):
        raise ValueError(f"x_suction_min must be a vapour quality in (0, 1] or None, got {value!r}")
    return x


@lru_cache(maxsize=256)
def _critical_point(ref: str) -> tuple[float, float]:
    """Critical temperature [K] and pressure [Pa] of ``ref``."""
    from CoolProp.CoolProp import PropsSI

    return float(PropsSI("Tcrit", ref)), float(PropsSI("Pcrit", ref))


def _dew_temperature_C(pressure: object, ref: str) -> float:
    """Dew-point temperature [degC] of a condensing pressure [Pa]; NaN where there is none."""
    try:
        return float(cu.K2C(_props("T", "P", float(cast(float, pressure)), "Q", 1, ref)[0]))
    except (TypeError, ValueError):
        return math.nan


@lru_cache(maxsize=256)
def default_condensing_temp_max(ref: str) -> float:
    """Default maximum condensing temperature for refrigerant ``ref`` [degC].

    The value of :data:`CONDENSING_TEMP_MAX_C`, never above the dew point at
    :data:`CONDENSING_PRESSURE_MAX_REDUCED` of the critical pressure; that
    dew point alone for a refrigerant not in the table.
    """
    _, P_crit = _critical_point(ref)
    near_critical = _dew_temperature_C(CONDENSING_PRESSURE_MAX_REDUCED * P_crit, ref)
    listed = CONDENSING_TEMP_MAX_C.get(_fluid_name(ref))
    if listed is None:
        return near_critical
    return min(float(listed), near_critical) if math.isfinite(near_critical) else float(listed)


def resolve_condensing_temp_max(value: float | Literal["auto"] | None, ref: str) -> float | None:
    """Constructor argument -> limit: ``"auto"`` the refrigerant default, ``None`` no check.

    A number is the caller's statement about the machine and is used as given;
    it has to lie below the critical temperature (above it the fluid has no
    condensing pressure to compare with).
    """
    if value is None:
        return None
    if isinstance(value, str):
        if value != "auto":
            raise ValueError(f"T_cond_max must be a temperature in degC, 'auto' or None, got {value!r}")
        limit = default_condensing_temp_max(ref)
        if not math.isfinite(limit):
            raise ValueError(f"no default T_cond_max for {ref!r}; give a temperature in degC or None")
        return limit
    limit = float(value)
    if not math.isfinite(limit):
        raise ValueError("T_cond_max must be finite (use None to switch the check off)")
    T_crit_C = cu.K2C(_critical_point(ref)[0])
    if limit >= T_crit_C:
        raise ValueError(
            f"T_cond_max={limit:g} °C is at or above the critical temperature of {ref} ({T_crit_C:.1f} °C); "
            "the limit is a condensing pressure, so give a lower temperature or None to switch the check off"
        )
    return limit


@lru_cache(maxsize=1024)
def _condensing_pressure_limits(ref: str, T_cond_max: float) -> tuple[float, float]:
    """Dew pressure [Pa] at ``T_cond_max`` and at ``T_cond_max + CONDENSING_TOL_K``."""
    T_crit_K, P_crit = _critical_point(ref)

    def dew_pressure(T_C: float) -> float:
        T_K = cu.C2K(T_C)
        if T_crit_K <= T_K:
            return P_crit
        try:
            return float(_props("P", "T", T_K, "Q", 1, ref)[0])
        except ValueError:
            return P_crit

    return dew_pressure(T_cond_max), dew_pressure(T_cond_max + CONDENSING_TOL_K)


def condensing_pressure_max(ref: str, T_cond_max: float | None) -> float | None:
    """Highest condensing pressure of the envelope [Pa]: the dew pressure at ``T_cond_max``."""
    return None if T_cond_max is None else _condensing_pressure_limits(ref, float(T_cond_max))[0]


def condensing_temp_exceeded(P_cond: object, T_cond_max: float | None, ref: str) -> bool:
    """Whether a condensing pressure [Pa] is above the dew pressure at ``T_cond_max``.

    Compared with :data:`CONDENSING_TOL_K` of margin. ``T_cond_max=None``
    switches the check off; a pressure that is not a finite number is not
    judged here (a state without one is refused elsewhere).
    """
    if T_cond_max is None:
        return False
    try:
        P = float(cast(float, P_cond))
    except (TypeError, ValueError):
        return False
    return math.isfinite(P) and _condensing_pressure_limits(ref, float(T_cond_max))[1] < P


#: Result key the check reads: the pressure the compressor discharges at.
_P_COND_KEY = "P_ref_cmp_out [Pa]"


def _condensing_excess_K(result: Mapping[str, Any], model: Any) -> float:
    """Kelvin by which a solved state condenses above the model's ``T_cond_max`` (0 inside)."""
    limit = getattr(model, "T_cond_max", None)
    ref = getattr(model, "ref", None)
    if limit is None or ref is None or not condensing_temp_exceeded(result.get(_P_COND_KEY), limit, ref):
        return 0.0
    T_dew = _dew_temperature_C(result[_P_COND_KEY], ref)
    # At or above the critical pressure there is no dew point: very far outside.
    return T_dew - float(limit) if math.isfinite(T_dew) else 1.0e3


def condensing_violation(result: Mapping[str, Any], model: Any) -> str | None:
    """:data:`CONDENSING_TEMP_ABOVE_MAX` if a solved state condenses above ``model.T_cond_max``, else ``None``.

    The refused state's condensing temperature (dew point of its pressure) is
    left on the model as ``_condensing_refused_C``, where the duty reduction
    reads it (:func:`_duty_on_condensing_limit`).
    """
    excess = _condensing_excess_K(result, model)
    if excess <= 0.0:
        return None
    with contextlib.suppress(AttributeError):
        model._condensing_refused_C = float(model.T_cond_max) + excess
    return CONDENSING_TEMP_ABOVE_MAX


def check_rated_condensing(model: Any, T_cond_sat_C: float, standard: str) -> str | None:
    """Why a rating point is outside the condensing limit, or ``None``.

    ``T_cond_sat_C`` is the condensing temperature the reference state solved
    (the cycle's bubble point). The message names the limit and what to change;
    :mod:`tmhp.reference_state` raises it at construction.
    """
    limit = getattr(model, "T_cond_max", None)
    ref = getattr(model, "ref", None)
    if limit is None or ref is None:
        return None
    try:
        (P_cond,) = _props("P", "T", cu.C2K(T_cond_sat_C), "Q", 0, ref)
    except ValueError:
        return None
    if not condensing_temp_exceeded(P_cond, limit, ref):
        return None
    return (
        f"the rating point {standard} condenses at {_dew_temperature_C(P_cond, ref):.1f} °C "
        f"({P_cond / 1e5:.1f} bar), above T_cond_max={float(limit):g} °C of {ref} "
        f"({_condensing_pressure_limits(ref, float(limit))[0] / 1e5:.1f} bar): the machine cannot be rated "
        "outside its own envelope. Give a larger condenser (UA), a rated_condition it reaches, the "
        "T_cond_max of the actual compressor, or T_cond_max=None"
    )


def condensing_tank_reach(model: Any) -> float | None:
    """Tank temperature at which the condensing limit leaves a boiler no duty [degC].

    The hottest the cycle may condense: the bubble point of the limit
    pressure (``T_cond_max`` itself for a pure fluid, lower by the glide for a
    blend). A duty ``Q`` needs the tank ``Q / K_tank_hx`` colder than this
    (:func:`condensing_tank_cutout` for the nameplate duty). ``None`` without
    a limit.
    """
    limit, ref = getattr(model, "T_cond_max", None), getattr(model, "ref", None)
    if limit is None or ref is None:
        return None
    try:
        (T_reach_K,) = _props("T", "P", _condensing_pressure_limits(ref, float(limit))[0], "Q", 0, ref)
    except ValueError:
        return None
    return float(cu.K2C(T_reach_K))


def condensing_tank_cutout(model: Any) -> float | None:
    """Highest tank temperature a boiler heats at its nameplate duty inside the condensing limit [degC].

    The cycle condenses at the tank temperature plus the tank heat exchanger's
    approach, ``hp_capacity / K_tank_hx`` at the nameplate duty (``K_tank_hx``:
    the heat the exchanger carries per kelvin above the tank water, the
    water entering a plate condenser), and the hottest it may condense is the
    bubble point of the limit pressure. ``None`` without a limit.
    """
    T_reach = condensing_tank_reach(model)
    if T_reach is None:
        return None
    return T_reach - float(model.hp_capacity) / float(model.K_tank_hx)


#: ``condensing_limit_control``: the charge ends where the nameplate duty meets the condensing limit.
CONTROL_STOP = "stop"
#: ``condensing_limit_control``: the compressor reduces its duty on the limit and keeps charging.
CONTROL_REDUCE_DUTY = "reduce_duty"
#: Values of the boilers' ``condensing_limit_control``.
CONDENSING_LIMIT_CONTROLS: tuple[str, ...] = (CONTROL_STOP, CONTROL_REDUCE_DUTY)


def resolve_condensing_limit_control(value: object) -> str:
    """Constructor argument -> one of :data:`CONDENSING_LIMIT_CONTROLS`."""
    if value not in CONDENSING_LIMIT_CONTROLS:
        raise ValueError(f"condensing_limit_control must be one of {CONDENSING_LIMIT_CONTROLS}, got {value!r}")
    return str(value)


def _cap_tank_setpoints(model: Any, limit: float) -> str:
    """Move the thermostat band below ``limit`` [degC]; the clause of the warning that says what moved."""
    lower, upper = model.T_tank_w_setpoints_requested
    new_lower = lower if lower < limit else limit - (upper - lower)
    model.T_tank_w_upper_bound, model.T_tank_w_lower_bound = limit, new_lower
    return "" if new_lower == lower else f", and the lower bound is moved from {lower:g} to {new_lower:.1f} °C"


def limit_tank_setpoints_to_envelope(model: Any, what: str) -> None:
    """Cap a boiler's thermostat band at the tank temperature its compressor envelope allows.

    A heat pump whose set point lies above what its compressor can reach does
    not charge the tank to it: the controller stops at the unit's maximum.
    The thermostat has to know that temperature -- a dynamic step solves one
    cycle and applies its heat over the step, so a cut-out the thermostat does
    not see is overrun inside a long step, by an amount that depends on the
    step. So when ``T_tank_w_upper_bound`` is above
    :func:`condensing_tank_cutout`, the model's upper bound becomes that
    cut-out; a lower bound that is not below it is moved down to keep the
    width of the band. The values asked for stay in
    ``T_tank_w_setpoints_requested`` (lower, upper), and an
    :class:`~tmhp.OperatingLimitWarning` (a ``UserWarning``) says what was
    changed. Up to the cut-out the machine runs at its nameplate
    duty; the duty reduction of :func:`reduce_duty_within_envelope` then
    serves ``analyze_steady`` and the steps a tank model or a minimum run
    time takes past it.

    This is ``condensing_limit_control="stop"``. With ``"reduce_duty"`` the
    band is only recorded here; the cap is the charging limit, set once the
    model can solve a cycle (:func:`limit_tank_setpoints_to_charge_limit`).
    """
    lower, upper = float(model.T_tank_w_lower_bound), float(model.T_tank_w_upper_bound)
    model.T_tank_w_setpoints_requested = (lower, upper)
    if getattr(model, "condensing_limit_control", CONTROL_STOP) == CONTROL_REDUCE_DUTY:
        return
    cutout = condensing_tank_cutout(model)
    if cutout is None or upper <= cutout:
        return
    moved = _cap_tank_setpoints(model, cutout)
    approach = float(model.hp_capacity) / float(model.K_tank_hx)
    warnings.warn(
        f"{what}: T_tank_w_upper_bound={upper:g} °C is above the tank temperature the compressor envelope "
        f"of {model.ref} allows at the nameplate duty, {cutout:.1f} °C (T_cond_max={float(model.T_cond_max):g} °C "
        f"less the {approach:.1f} K tank heat-exchanger approach). The thermostat stops the heat pump at "
        f"{cutout:.1f} °C{moved}. Lower the set points, use a refrigerant with a higher limit (e.g. R290, "
        "R134a), or give the T_cond_max of the actual compressor (None switches the limit off)",
        OperatingLimitWarning,
        stacklevel=3,
    )


#: Fixed-point steps :func:`smallest_duty_on_condensing_limit` takes at most.
_FLOOR_ITERATIONS = 4
#: ... and the tank temperature within which it is closed [K]: 0.005 K of
#: condenser approach, half the tolerance of the limit itself
#: (:data:`CONDENSING_TOL_K`).
_FLOOR_CLOSE_K = 0.005


def smallest_continuous_duty(
    model: Any, solve: Callable[[float], Any], delivered_key: str = "Q_ref_tank [W]"
) -> float | None:
    """Smallest duty a boiler's compressor runs at continuously at one operating point [W].

    ``solve(q)`` solves the point at duty ``q``. The probe is the smallest
    duty the duty reduction itself tries, the lowest rung of its ladder at the
    nameplate request (``0.1 hp_capacity``), so the answer is the floor
    ``analyze_steady`` has for a nameplate request at this point:

    - the probe runs with the compressor on its speed floor: what the machine
      delivers there (more than the probe);
    - the probe runs as asked: the probe itself (the reduction looks no
      lower);
    - the envelope refuses it from below (the discharge temperature at low
      speed): the smallest duty that runs above it
      (:func:`raise_duty_within_envelope`).

    ``None`` when no such duty runs (the point is beyond the condensing limit
    at every duty, or refused for another reason). The solves run silently.
    """
    probe = _REDUCTION_LADDER[-1] * float(model.hp_capacity)
    if not (probe > 0.0 and math.isfinite(probe)):
        return None

    def quiet(q: float) -> dict:
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            out = solve(q)
        return out if isinstance(out, dict) else {}

    result = quiet(probe)
    if not _running(result) and result.get("failure_reason") in _RAISED_OVER:
        result = raise_duty_within_envelope(model, result, quiet, probe, delivered_key)
    if not _running(result):
        return None
    delivered = _delivered(result, delivered_key)
    return delivered if math.isfinite(delivered) and delivered > 0.0 else None


def smallest_duty_on_condensing_limit(model: Any, floor_at: Callable[[float], float | None]) -> float | None:
    """Smallest continuous duty of a boiler where that duty condenses on the limit [W].

    ``floor_at(T)`` is the smallest continuous duty at tank temperature ``T``
    [degC] (:func:`smallest_continuous_duty` at the source of interest). The
    tank temperature at which it condenses on the limit is
    ``T_reach - floor / K_tank_hx`` (:func:`condensing_tank_reach`); the floor
    depends on that temperature only through the condensing pressure, weakly,
    so a fixed-point iteration from the nameplate cut-out closes in two or
    three evaluations (to :data:`_FLOOR_CLOSE_K`). A trial the limit refuses
    is retried half-way back.

    The search stays where a tank can be: no trial is above the boiling point
    of the stored water (:data:`tmhp.dynamic_context.T_WATER_BOIL_C`, the
    upper end of the tank temperatures the models accept). A refrigerant
    whose limit lies high enough (R600a with ``T_cond_max`` above about
    101 °C) meets its smallest duty above 100 °C; the duty returned is then
    the one at 100 °C, the tank temperature it gives
    (``T_reach - duty / K_tank_hx``) is above the boiling point, and the
    boiling cut-out, not the condensing limit, ends the charge.

    Above the tank temperature the returned duty gives,
    ``T_reach - duty / K_tank_hx``, no duty runs: the charging limit of
    ``condensing_limit_control="reduce_duty"``. ``None`` without a condensing
    limit, or when no duty below the nameplate runs at the nameplate cut-out
    (there is then no reduced stretch: the cut-out is the limit).
    """
    T_reach = condensing_tank_reach(model)
    if T_reach is None:
        return None
    K, capacity = float(model.K_tank_hx), float(model.hp_capacity)
    T = min(T_reach - capacity / K, T_WATER_BOIL_C)
    floor = floor_at(T)
    if floor is None or not floor < capacity:
        return None
    for _ in range(_FLOOR_ITERATIONS):
        T_next = min(T_reach - floor / K, T_WATER_BOIL_C)
        if abs(T_next - T) <= _FLOOR_CLOSE_K:
            break
        floor_next = floor_at(T_next)
        if floor_next is None:
            T_next = 0.5 * (T + T_next)
            floor_next = floor_at(T_next)
            if floor_next is None:
                break
        T, floor = T_next, min(floor_next, capacity)
    return floor


def steady_smallest_duty(model: Any, **source: Any) -> Callable[[float], float | None]:
    """``floor_at`` of :func:`smallest_duty_on_condensing_limit` from a boiler's ``analyze_steady``.

    ``source`` are the steady call's source arguments (``T0=...`` or
    ``T_source=...``). The probe request of :func:`smallest_continuous_duty`
    is solved by ``analyze_steady`` itself; a row on the speed floor carries
    the continuous duty in ``Q_min [W]`` (the row is the on/off mean).
    """
    from .operating_limits import silenced

    probe = _REDUCTION_LADDER[-1] * float(model.hp_capacity)

    def floor_at(T_tank_w: float) -> float | None:
        with silenced(), warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            row = model.analyze_steady(T_tank_w=T_tank_w, Q_ref_tank=probe, **source)
        if not _running(row):
            return None
        duty = _delivered(row, "Q_min [W]") if row.get("capacity_clamped") == "min" else math.nan
        if not math.isfinite(duty):
            duty = _delivered(row, "Q_ref_tank [W]")
        return duty if math.isfinite(duty) and duty > 0.0 else None

    return floor_at


def limit_tank_setpoints_to_charge_limit(
    model: Any, what: str, floor_at: Callable[[float], float | None], reference: str
) -> None:
    """Cap a reduced-duty boiler's thermostat band at its charging limit.

    The counterpart of :func:`limit_tank_setpoints_to_envelope` for
    ``condensing_limit_control="reduce_duty"``, called at the end of
    construction (it solves cycles). Past the nameplate cut-out the compressor
    charges on at the duty the condensing limit leaves, down to its smallest
    continuous duty; the charging limit is the tank temperature at which that
    duty condenses on the limit,
    ``T_reach - Q_min / K_tank_hx`` (:func:`smallest_duty_on_condensing_limit`
    with ``floor_at``). An upper set point above it becomes it, the lower one
    keeps the band's width, the values asked for stay in
    ``T_tank_w_setpoints_requested`` and an
    :class:`~tmhp.OperatingLimitWarning` says what was changed.

    The smallest continuous duty depends on the source (a warmer source gives
    the compressor denser suction gas, so more duty at its lowest speed, and
    the limit is met at a colder tank). The cap is evaluated once, at the
    source ``floor_at`` solves at -- ``reference`` names it in the warning --
    and each dynamic step applies the stop of its own source: below the cap
    the compressor then stops at that stop
    (``hp_stop_reason == "condensing_limit"``) and, while the call stands,
    holds the tank there or, with a minimum rest time, rests and restarts
    (``duty_limit`` of
    :func:`tmhp.dynamic_context.simulate_thermostat_step`). A set point at or
    below the nameplate cut-out needs no cycle solve and is left alone. For a
    blend the warning names the bubble point the tank is held below as well as
    ``T_cond_max``, the dew point of the same pressure.
    """
    lower, upper = model.T_tank_w_setpoints_requested
    cutout = condensing_tank_cutout(model)
    T_reach = condensing_tank_reach(model)
    if cutout is None or T_reach is None or upper <= cutout:
        return
    floor = smallest_duty_on_condensing_limit(model, floor_at)
    limit = cutout if floor is None else max(cutout, T_reach - floor / float(model.K_tank_hx))
    if upper <= limit:
        return
    moved = _cap_tank_setpoints(model, limit)
    duty = float(model.hp_capacity) if floor is None else floor
    # A blend condenses over a glide: the limit is the dew point of T_cond_max,
    # and the tank is held below the bubble point of that pressure.
    glide = float(model.T_cond_max) - T_reach
    bubble = f", whose pressure has its bubble point at {T_reach:.1f} °C," if glide > 0.05 else ""
    warnings.warn(
        f"{what}: T_tank_w_upper_bound={upper:g} °C is above the tank temperature the compressor envelope "
        f"of {model.ref} allows at the smallest continuous duty, {limit:.1f} °C (T_cond_max="
        f"{float(model.T_cond_max):g} °C{bubble} less the {duty / float(model.K_tank_hx):.1f} K tank heat-exchanger "
        f"approach of {duty:.0f} W, {reference}). The compressor reduces its duty from {cutout:.1f} °C and "
        f"the thermostat stops it at {limit:.1f} °C{moved}. Lower the set points, use a refrigerant with a "
        "higher limit (e.g. R290, R134a), or give the T_cond_max of the actual compressor (None switches "
        "the limit off)",
        OperatingLimitWarning,
        stacklevel=3,
    )


DUTY_MATCH_RTOL: float = 1e-3
"""Fraction by which a cycle may miss the duty the condensing limit leaves and
still count as running on that limit (:func:`condensing_stretch`).

A cycle asked for the limit's duty at a tank temperature on the limit delivers
it to the solver's last digits when the condensing limit is the one that
governs (measured ratio 1.000000 on the documented charges of the three boiler
families). One that delivers less by more than this was reduced further, by
another limit.
"""

#: :func:`condensing_stretch`: no continuous duty below the nameplate runs on
#: the condensing limit at this source, so there is no stretch to charge on.
WITHHELD_NO_SMALLER_DUTY = "no_smaller_duty"


def condensing_stretch(
    solve: Callable[[float, float], Any],
    T_zero_C: float,
    K: float,
    Q_knee: float,
    min_duty: Callable[[], float | None],
    delivered_key: str = "Q_ref_tank [W]",
) -> tuple[float | None, str | None]:
    """The stretch a reduced-duty step charges on at one source state: ``(Q_min, None)``, or ``(None, what withholds it)``.

    A step of ``condensing_limit_control="reduce_duty"`` integrates the tank
    along the condensing limit's duty, ``K (T_zero_C - T)`` [W]
    (:class:`tmhp.dynamic_context.DutyLimit`), from the knee -- the tank
    temperature at which that duty is ``Q_knee``, the nameplate duty -- to the
    stop, where it is ``Q_min``, the smallest continuous duty (``min_duty()``:
    :func:`smallest_duty_on_condensing_limit` at this source; neither point
    above the boiling point of the stored water). That is the tank's path only
    if the condensing limit is the limit that governs the whole stretch. This
    function checks it for one source state, with three cycles: ``solve(T,
    q)`` -- the step's cycle at tank temperature ``T`` [degC] asked for duty
    ``q`` [W], solved with the source as the step finds it -- at the knee, at
    the middle of the stretch and at the stop, each asked for the limit's
    duty there. The stretch qualifies if each of them runs, delivers that
    duty to :data:`DUTY_MATCH_RTOL`, and names no limit but the condensing
    one; ``Q_min`` is then returned.

    Otherwise reduced-duty charging is withheld at this source state, and the
    first check that fails says by what (later ones are not made; the knee
    comes first, before the search for ``Q_min``, which is the costly part):

    - a cycle that does not run: its ``failure_reason``
      (``"source_fluid_freezing"``, ``"discharge_temp_above_max"``, ...);
    - a cycle held at another duty, or naming another limit: its
      ``capacity_binding`` (``"discharge_temperature"``,
      ``"source_fluid_freezing"``, ...), and :data:`BINDING_COMPRESSOR` for one
      that names none -- the compressor at its speed ceiling, which a cold
      source leaves short of the nameplate duty at the knee;
    - no ``Q_min`` below ``Q_knee`` (``None``, or not smaller):
      :data:`WITHHELD_NO_SMALLER_DUTY`.

    The answer is a property of the machine and the source state: it does not
    depend on the step, nor on where the tank is. The boilers keep it for as
    long as the source state stays the same. Three points are a sample: a
    limit that acts only strictly between two of them is not seen. The check
    assumes that a limit which leaves the knee, the middle and the stop alone
    leaves the stretch between them alone. The solves run silently.
    """

    def other_limit(T: float) -> str | None:
        """What but the condensing limit governs the cycle asked for the limit's duty at ``T`` (``None``: nothing)."""
        duty = K * (T_zero_C - T)
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            out = solve(T, duty)
        result: Mapping[str, Any] = out if isinstance(out, Mapping) else {}
        if not _running(result):
            reason = result.get("failure_reason")
            return str(reason) if reason and reason != "none" else BINDING_CYCLE
        binding = result.get("capacity_binding")
        if not abs(_delivered(result, delivered_key) - duty) <= DUTY_MATCH_RTOL * duty:
            return str(binding) if binding else BINDING_COMPRESSOR
        return str(binding) if binding and binding != BINDING_CONDENSING else None

    T_knee = min(T_zero_C - Q_knee / K, T_WATER_BOIL_C)
    withheld = other_limit(T_knee)
    if withheld is not None:
        return None, withheld
    Q_min = min_duty()
    if Q_min is None or not (math.isfinite(Q_min) and 0.0 <= Q_min < Q_knee):
        return None, WITHHELD_NO_SMALLER_DUTY
    T_stop = max(T_knee, min(T_zero_C - Q_min / K, T_WATER_BOIL_C))
    for T in (0.5 * (T_knee + T_stop), T_stop):
        withheld = other_limit(T)
        if withheld is not None:
            return None, withheld
    return Q_min, None


def on_condensing_limit(model: Any, result: dict, request: float, delivered_key: str = "Q_ref_tank [W]") -> dict:
    """Capacity keys of a cycle a dynamic step ran on the condensing limit (in place).

    A step of ``condensing_limit_control="reduce_duty"`` solves the stretch it
    spends on the limit directly at the limit's duty, so the duty reduction
    (:func:`reduce_duty_within_envelope`) has nothing to reduce and writes no
    keys. The row is the one that reduction lands on for the nameplate
    ``request``, and gets its keys: ``capacity_clamped = "max"``,
    ``Q_max [W]``, ``Q_request [W]`` and ``capacity_binding`` -- the condensing
    limit, unless the solve was itself reduced further onto another limit,
    which then names it. An off row is returned unchanged.
    """
    if not _running(result):
        return result
    if result.get("capacity_clamped") != CAPACITY_CLAMPED_MAX or not result.get("capacity_binding"):
        result["capacity_binding"] = BINDING_CONDENSING
    result["capacity_clamped"] = CAPACITY_CLAMPED_MAX
    result["Q_max [W]"] = _delivered(result, delivered_key)
    result["Q_request [W]"] = float(request)
    if "PLR_request [-]" in result and getattr(model, "hp_capacity", 0) > 0:
        result["PLR_request [-]"] = request / model.hp_capacity
    return result


def discharge_temp_exceeded(T_dis_C: object, T_dis_max: float | None) -> bool:
    """Whether a discharge temperature [degC] violates ``T_dis_max``.

    A non-finite discharge temperature (no discharge state: the enthalpy left
    the fluid's equation of state) counts as exceeded. ``T_dis_max=None``
    switches the check off.
    """
    if T_dis_max is None:
        return False
    try:
        T = float(cast(float, T_dis_C))
    except (TypeError, ValueError):
        return True
    return not math.isfinite(T) or T_dis_max + _DISCHARGE_TOL_K < T


def discharge_violation(result: Mapping[str, Any], T_dis_max: float | None) -> str | None:
    """:data:`DISCHARGE_TEMP_ABOVE_MAX` if a solved state exceeds ``T_dis_max``, else ``None``."""
    if discharge_temp_exceeded(result.get("T_ref_cmp_out [°C]"), T_dis_max):
        return DISCHARGE_TEMP_ABOVE_MAX
    return None


def envelope_violation(result: Mapping[str, Any], model: Any) -> str | None:
    """Why a solved running state is outside the compressor envelope, or ``None``.

    :data:`CONDENSING_TEMP_ABOVE_MAX` or :data:`DISCHARGE_TEMP_ABOVE_MAX`
    (after the discharge-temperature control). The condensing pressure is
    judged first: it does not depend on the efficiency model or on the
    control, and a smaller duty always lowers it.
    """
    reason = condensing_violation(result, model)
    if reason is not None:
        return reason
    return discharge_violation(result, getattr(model, "T_dis_max", None))


def envelope_excess(result: Mapping[str, Any], model: Any) -> float:
    """How far a solved running state is outside the envelope, as a ranking weight [W-equivalent].

    0 inside. 1000 per kelvin of discharge above ``T_dis_max`` (beyond
    :data:`DISCHARGE_TOL_K`; an undefined discharge state counts as very far)
    and per kelvin of condensing above ``T_cond_max`` (beyond
    :data:`CONDENSING_TOL_K`). Used by searches to rank candidates outside the
    envelope among themselves.
    """
    excess = 1000.0 * _condensing_excess_K(result, model)
    T_max = getattr(model, "T_dis_max", None)
    if T_max is not None:
        T = _discharge(result)
        if not math.isfinite(T):
            excess += 1.0e9
        elif T_max + DISCHARGE_TOL_K < T:
            excess += 1000.0 * (T - T_max)
    return excess


def default_rps_max(rps_rated: float) -> float:
    """Default upper speed bound from the rated speed [rev/s].

    ``max(120, 1.5 * rps_rated)``: :data:`RPS_MAX_ABSOLUTE` for every machine
    rated at 80 rev/s or below.

    The ceiling is a property of the compressor, not of the nameplate.
    Manufacturer documents specify inverter rotary compressors of heat-pump
    size for 12--120 rev/s (8.9--21 cm3/rev), 12--110 (57.9 cm3/rev), 15--100
    (44 cm3/rev class) and 10--130 (small twins); an air-to-air outdoor unit's
    service manual gives 110 rev/s in heating. 120 rev/s (7200 rpm) is the
    upper end of that range for the 40--65 cm3/rev machines of the validation
    catalogues, whose own documents print no maximum speed (they limit the
    frequency by running current). The former bound ``min(2 * rps_rated,
    120)`` tied the ceiling to the nameplate, so two units that share one
    compressor had different ceilings (91 and 120 rev/s for the 9 and 12 kW
    units on a 42 cm3/rev compressor). A machine whose compressor is known
    should be given its documented ``rps_max``.

    A machine whose *rated* speed is already high -- a low-volumetric-capacity
    fluid with a default displacement that does not follow the refrigerant
    (#86) -- keeps at least :data:`RPS_MAX_MIN_PER_RATED` times its rated
    speed, so it can still reach the catalogue maximum-to-rated ratio; such a
    machine should be given its real ``V_cmp_ref``.
    """
    return max(RPS_MAX_ABSOLUTE, RPS_MAX_MIN_PER_RATED * float(rps_rated))


def efficiency_extrapolated_at(models: tuple[object, ...], result: Mapping[str, Any]) -> bool:
    """Whether any default efficiency model is evaluated outside its data at this state.

    Pressure ratio and speed are read from the result row
    (``P_ref_cmp_out / P_ref_cmp_in`` and ``cmp_rpm / 60``). False for a state
    that does not run, and for scalar or user-supplied efficiencies.
    """
    try:
        p_in = float(result["P_ref_cmp_in [Pa]"])
        p_out = float(result["P_ref_cmp_out [Pa]"])
        rps = float(result["cmp_rpm [rpm]"]) / 60.0
    except (KeyError, TypeError, ValueError):
        return False
    if not (math.isfinite(p_in) and math.isfinite(p_out) and math.isfinite(rps) and p_in > 0 and rps > 0):
        return False
    pr = p_out / p_in
    return any(_outside_domain(m, pr, rps) for m in models)


#: Relative margin inside which a point on the edge of a model's (PR, N) data
#: hull counts as inside. The pressure ratio and speed are read back from the
#: result row (``P_out / P_in``, ``cmp_rpm / 60``), so a point solved exactly
#: on an edge -- a speed bound can coincide with a hull vertex (30 or
#: 120 rev/s for the rotary types) -- comes back a few ulp either side, and
#: the flag would differ between platforms. 1e-6 is the relative tolerance of
#: the step-kernel goldens and far below any physical meaning of the hull.
_DOMAIN_RTOL = 1e-6


def _outside_domain(model: object, pressure_ratio: float, rps: float) -> bool:
    domain = getattr(model, "validity_domain", None)
    if domain is None or not efficiency_extrapolated(model, pressure_ratio, rps):  # type: ignore[arg-type]
        return False
    return not domain.contains(pressure_ratio, rps, rtol=_DOMAIN_RTOL)


_F = TypeVar("_F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Discharge-temperature control (wraps a family's cycle evaluation)
# ---------------------------------------------------------------------------

#: Result keys the control adds to every running cycle state (all five families).
SUPERHEAT_KEY = "dT_ref_superheat [K]"
QUALITY_KEY = "x_ref_cmp_in [-]"
CONTROL_KEY = "discharge_control"
_T_DIS_KEY = "T_ref_cmp_out [°C]"
_SUPERHEAT_XTOL_K = 1e-3
_QUALITY_XTOL = 1e-5


#: Key under which a candidate whose heat exchanger did not close keeps its solved cycle.
CYCLE_KEY = "_cycle"


def _state_running(result: object) -> bool:
    return isinstance(result, dict) and bool(result.get("hp_is_on")) and _T_DIS_KEY in result


def _solved_cycle(result: object) -> Mapping[str, Any] | None:
    """The refrigerant cycle a candidate state solved, if it solved one.

    The state itself when it runs. A candidate whose heat exchanger does not
    carry that cycle is not a running state, but its cycle exists and has a
    discharge temperature: a family may hand it over under :data:`CYCLE_KEY`,
    and the control then acts on it too -- which side of the heat exchanger's
    range the candidate falls on is a question about the *controlled* cycle.
    """
    if _state_running(result):
        return cast("Mapping[str, Any]", result)
    if isinstance(result, dict):
        cycle = result.get(CYCLE_KEY)
        if isinstance(cycle, Mapping) and _T_DIS_KEY in cycle:
            return cycle
    return None


def _discharge_of(state: object) -> float:
    cycle = _solved_cycle(state)
    return math.nan if cycle is None else _discharge(cycle)


def _discharge(result: Mapping[str, Any]) -> float:
    try:
        value = float(result[_T_DIS_KEY])
    except (KeyError, TypeError, ValueError):
        return math.nan
    return value if math.isfinite(value) else math.inf


def _superheat(result: Mapping[str, Any]) -> float:
    try:
        return max(0.0, float(result["T_ref_cmp_in [°C]"]) - float(result["T_ref_evap_sat [°C]"]))
    except (KeyError, TypeError, ValueError):
        return math.nan


def discharge_controlled(reject: bool) -> Callable[[_F], _F]:
    """Wrap a family's ``_calc_state`` with the discharge-temperature control.

    The wrapped method evaluates one candidate operating point; it reads the
    suction superheat from ``self.dT_superheat`` (capped by the evaporator
    approach where the family caps it) and the suction quality from
    ``self._x_suction``. When the running state discharges above
    ``self.T_dis_max``, the same candidate is re-evaluated with the superheat
    reduced (root of ``T_dis(superheat) = T_dis_max`` on ``[0, superheat]``)
    and, if 0 K is not enough, with the quality reduced (root on
    ``[x_suction_min, 1]``). The root is predicted from the fluid properties
    and corrected with two or three evaluations (:func:`_control_predicted`);
    root finding on the evaluations themselves is the fallback
    (:func:`_control_bracketed`). Every running state gets
    :data:`SUPERHEAT_KEY`, :data:`QUALITY_KEY` and :data:`CONTROL_KEY`.

    A state still above the limit is returned as is when ``reject`` is false
    (the boilers check the solved operating point and reduce the duty), and is
    rejected -- ``None``, with ``self._last_pr_event`` naming the discharge
    limit -- when ``reject`` is true (ASHP, GSHP: a candidate inside the search).

    With ``reject`` true the condensing limit (``self.T_cond_max``) is applied
    here too, before the control: a running state above it is rejected the
    same way (``_last_pr_event`` names :data:`CONDENSING_TEMP_ABOVE_MAX`). The
    boilers judge it on the solved operating point (:func:`envelope_violation`),
    after the control, so that the state they judge has a discharge state.
    """

    def decorate(function: _F) -> _F:
        @wraps(function)
        def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = function(self, *args, **kwargs)
            if reject and _state_running(result):
                # The suction state the control moves does not change the
                # condensing pressure: a candidate above the condensing limit
                # is outside the envelope whatever the control does.
                above = _condensing_excess_K(result, self)
                if above > 0.0:
                    limit = float(self.T_cond_max)
                    self._last_pr_event = (CONDENSING_TEMP_ABOVE_MAX, limit + above, limit)
                    self._condensing_rejected = True  # read by `_condensing_limit_binds`
                    return None
            T_max = self.T_dis_max
            cycle = _solved_cycle(result)
            if T_max is None or cycle is None:
                # No limit: the cycle state is returned untouched (the steady
                # wrapper still reports superheat and quality).
                return result
            superheat0 = _superheat(cycle)
            if _state_running(result):
                result.update({SUPERHEAT_KEY: superheat0, QUALITY_KEY: float(self._x_suction), CONTROL_KEY: False})
            if _discharge(cycle) <= T_max + _DISCHARGE_TOL_K:
                return result

            # A caller's diagnostics dict (ASHPB ``hx_diag``) must describe the
            # state returned: each re-evaluation fills a fresh one, copied back
            # only for the state the control keeps.
            diag_out = kwargs.get("hx_diag") if isinstance(kwargs.get("hx_diag"), dict) else None
            diags: dict[int, dict] = {}

            def at(superheat: float, quality: float) -> dict | None:
                saved = (self.dT_superheat, self._x_suction)
                self.dT_superheat, self._x_suction = superheat, quality
                call_kwargs = dict(kwargs)
                fresh: dict = {}
                if diag_out is not None:
                    call_kwargs["hx_diag"] = fresh
                try:
                    state = function(self, *args, **call_kwargs)
                except ValueError:
                    return None
                finally:
                    self.dT_superheat, self._x_suction = saved
                solved = _solved_cycle(state)
                if not isinstance(state, dict) or solved is None or not math.isfinite(_discharge(solved)):
                    return None
                if _state_running(state):
                    state.update({SUPERHEAT_KEY: _superheat(state), QUALITY_KEY: quality, CONTROL_KEY: True})
                diags[id(state)] = fresh
                return state

            controlled, held = _control(at, superheat0, T_max, self.x_suction_min, getattr(self, "ref", None), cycle)
            if reject and not held:
                self._last_pr_event = (DISCHARGE_TEMP_ABOVE_MAX, _discharge(cycle), float(T_max))
                return None
            if controlled is None:
                return result
            if diag_out is not None:
                diag_out.clear()
                diag_out.update(diags.get(id(controlled), {}))
            return controlled

        return cast(_F, wrapped)

    return decorate


#: The control is closed once the discharge is within this of ``T_dis_max`` [K].
#: Far below :data:`DISCHARGE_TOL_K`, so that the controlled state is a smooth
#: function of the operating point the searches minimise over (a looser
#: closure leaves root-finding noise in the objective, and the minimiser
#: spends its iterations on it).
_CONTROL_TOL_K = 1e-5
#: Full cycle evaluations the predicted closure may use before the bracketing one takes over.
_CONTROL_MAX_EVALUATIONS = 8
_FALLBACK = object()


class _DischargeEstimate:
    """Discharge enthalpy over the suction state, from one solved cycle state.

    The suction pressure, the condensing pressure and the isentropic
    efficiency of ``state`` are held; only the suction state moves. The
    coordinate ``u`` is the suction superheat [K] where positive and the
    vapour quality minus one where negative (``u = 0``: saturated vapour).
    The efficiency follows the compressor speed, which the suction state
    moves a little (a wetter suction is denser, so the same duty needs fewer
    revolutions): ``slope`` is its change per unit suction enthalpy -- a
    coordinate continuous across saturation -- taken from two solved states
    (:func:`_control_predicted`).
    """

    def __init__(self, ref: str, state: Mapping[str, Any], u: float, T_max: float) -> None:
        self.ref = ref
        self.u = u
        self.p_in = float(state["P_ref_cmp_in [Pa]"])
        self.p_out = float(state["P_ref_cmp_out [Pa]"])
        self.T_evap_K = cu.C2K(float(state["T_ref_evap_sat [°C]"]))
        self.h_in = h_in = float(state["h_ref_cmp_in [J/kg]"])
        h_out = float(state["h_ref_cmp_out [J/kg]"])
        (h_isen,) = _props("H", "P", self.p_out, "S", float(state["s_ref_cmp_in [J/(kg·K)]"]), ref)
        self.eta = (h_isen - h_in) / (h_out - h_in)
        (self.h_target,) = _props("H", "T", cu.C2K(T_max), "P", self.p_out, ref)
        if not (math.isfinite(self.eta) and self.eta > 0.0 and self.h_target > float(state["h_ref_cond_sat_v [J/kg]"])):
            raise ValueError("no discharge estimate for this state")
        self.slope = 0.0

    def excess(self, u: float) -> float:
        """Estimated discharge enthalpy above that of ``T_dis_max`` at suction state ``u`` [J/kg]."""
        if u > 1e-6:  # below it the cycle state takes saturated vapour (calc_ref_state)
            h_in, s_in = _props("HS", "T", self.T_evap_K + u, "P", self.p_in, self.ref)
        else:
            h_in, s_in = _props("HS", "P", self.p_in, "Q", 1.0 + min(u, 0.0), self.ref)
        (h_isen,) = _props("H", "P", self.p_out, "S", s_in, self.ref)
        eta = max(self.eta + self.slope * (h_in - self.h_in), 0.5 * self.eta)
        return h_in + (h_isen - h_in) / eta - self.h_target

    def root(self, u_min: float, u_max: float) -> float:
        """Suction state in ``[u_min, u_max]`` at the limit (an end when the limit is outside)."""
        if self.excess(u_max) <= 0.0:
            return u_max
        if u_max > 0.0 >= u_min:
            # Superheat and quality are different scales: solve on one side of saturation.
            if self.excess(0.0) > 0.0:
                u_max = 0.0
            else:
                u_min = 0.0
        if u_min >= u_max or self.excess(u_min) >= 0.0:
            return u_min
        return float(brentq(self.excess, u_min, u_max, xtol=1e-12, rtol=1e-14))


def _control_predicted(
    at: Callable[[float, float], dict | None],
    base: Mapping[str, Any],
    superheat0: float,
    T_max: float,
    x_min: float,
    ref: str,
) -> Any:
    """The controlled state by thermodynamic prediction and a few full evaluations.

    Brackets the limit between the uncontrolled state (above it) and the
    driest suction state found inside it, and proposes the next suction state
    from :class:`_DischargeEstimate` of the latest solved state -- a pure
    property calculation -- with the efficiency's dependence on the suction
    state taken from the last two solved states. Each proposal costs one full
    cycle evaluation; two or three close the control to
    :data:`_CONTROL_TOL_K`. Returns ``(state, held)`` as :func:`_control`
    does, or :data:`_FALLBACK` when a proposal has no cycle or the closure
    does not settle (the bracketing closure then decides).
    """
    u_min = min(x_min - 1.0, 0.0)
    if superheat0 <= u_min:
        return None, False  # saturated suction and no wet stage: nothing to control with
    try:
        estimate = _DischargeEstimate(ref, base, superheat0, T_max)
    except (KeyError, TypeError, ValueError):
        return _FALLBACK
    low: float | None = None
    low_state: dict | None = None
    high = superheat0
    for _ in range(_CONTROL_MAX_EVALUATIONS):
        try:
            u = estimate.root(u_min if low is None else low, high)
        except (ValueError, RuntimeError):
            return _FALLBACK
        if (low is not None and u <= low) or u >= high:
            if low is None:
                return _FALLBACK
            u = 0.5 * (low + high)
            if not (low < u < high):
                return low_state, True
        state = at(max(u, 0.0), 1.0 + min(u, 0.0))
        if state is None:
            return _FALLBACK
        above = _discharge_of(state) - T_max
        if abs(above) <= _CONTROL_TOL_K:
            return state, True
        if above > 0.0:
            if u <= u_min:
                return state, False  # the control's last stage, still above the limit
            high = u
        else:
            low, low_state = u, state
        try:
            latest = _DischargeEstimate(ref, _solved_cycle(state) or {}, u, T_max)
        except (KeyError, TypeError, ValueError):
            return _FALLBACK
        if latest.h_in != estimate.h_in:
            latest.slope = (latest.eta - estimate.eta) / (latest.h_in - estimate.h_in)
        estimate = latest
    return _FALLBACK


def _control(
    at: Callable[[float, float], dict | None],
    superheat0: float,
    T_max: float,
    x_min: float | None,
    ref: str | None,
    base: Mapping[str, Any],
) -> tuple[dict | None, bool]:
    """``(state, held)``: the controlled state, and whether it holds the discharge limit.

    ``held`` false means the control cannot bring the discharge inside the
    limit; ``state`` is then its last stage (the suction at ``x_min``, or
    saturated vapour when there is no wet stage) when that was solved, the
    state closest to the envelope this candidate has, or ``None``.
    """
    if x_min is None or not math.isfinite(superheat0):
        return None, False
    if ref is not None:
        controlled = _control_predicted(at, base, superheat0, T_max, x_min, ref)
        if controlled is not _FALLBACK:
            return cast("tuple[dict | None, bool]", controlled)
    return _control_bracketed(at, superheat0, T_max, x_min)


def _control_bracketed(
    at: Callable[[float, float], dict | None], superheat0: float, T_max: float, x_min: float
) -> tuple[dict | None, bool]:
    """:func:`_control` by root finding on full cycle evaluations (about seven of them)."""

    def excess(superheat: float, quality: float) -> float:
        return _discharge_of(at(superheat, quality)) - T_max

    def inside(state: dict | None) -> bool:
        return state is not None and _discharge_of(state) <= T_max + DISCHARGE_TOL_K

    dry_state = at(0.0, 1.0)
    dry = _discharge_of(dry_state) - T_max
    if not math.isfinite(dry):
        return None, False
    if dry <= 0.0:
        if superheat0 <= _SUPERHEAT_XTOL_K:
            return dry_state, True
        try:
            root = brentq(lambda sh: excess(sh, 1.0), 0.0, superheat0, xtol=_SUPERHEAT_XTOL_K)
        except (ValueError, RuntimeError):
            return dry_state, True
        # Take the bracket side inside the limit.
        state = at(root, 1.0)
        if not inside(state):
            state = at(max(0.0, root - _SUPERHEAT_XTOL_K), 1.0)
        return state, inside(state)
    if x_min >= 1.0:
        return dry_state, False
    wet_state = at(0.0, x_min)
    wet = _discharge_of(wet_state) - T_max
    if not (math.isfinite(wet) and wet <= 0.0):
        return wet_state, False
    try:
        root = brentq(lambda x: excess(0.0, x), x_min, 1.0, xtol=_QUALITY_XTOL)
    except (ValueError, RuntimeError):
        return wet_state, True
    state = at(0.0, root)
    if not inside(state):
        state = at(0.0, max(x_min, root - _QUALITY_XTOL))
    return state, inside(state)


# ---------------------------------------------------------------------------
# Steady-state wrapper
# ---------------------------------------------------------------------------

#: Fractions of the request tried, from the top, when the request has no operating point.
_REDUCTION_LADDER = (0.8, 0.6, 0.4, 0.25, 0.1)
#: Bisection stops once the bracket is this narrow relative to the request.
_REDUCTION_REL_TOL = 0.01

#: Failure reasons of a fixed-duty request that a smaller duty can cure, and
#: the ``capacity_binding`` each gives the reduced point.
#:
#: An inverter unit asked for more than an operating limit allows does not
#: stop; its controller lowers the compressor frequency until the limit holds
#: (discharge-temperature, compression-ratio and low-pressure protection all
#: act this way before any trip). In the model a smaller duty moves every one
#: of these limits the same way: the condenser needs a smaller approach, so
#: the condensing pressure and with it the pressure ratio and the discharge
#: temperature fall, and the source heat exchanger has less heat to absorb, so
#: it needs a smaller approach or less flow.
_REDUCIBLE_BINDING: Mapping[str, str] = {
    CONDENSING_TEMP_ABOVE_MAX: BINDING_CONDENSING,
    DISCHARGE_TEMP_ABOVE_MAX: BINDING_DISCHARGE,
    PR_ABOVE_MAX: BINDING_PRESSURE_RATIO,
    "hx_not_converged": BINDING_HEAT_EXCHANGER,
}
#: ``failure_detail`` of a failure a smaller duty makes worse, whatever its
#: reason: the outdoor coil already absorbs more than the cycle takes at the
#: smallest airflow.
_NOT_REDUCIBLE_DETAIL = frozenset({"outdoor_coil_excess_at_min_airflow"})
#: ``failure_detail`` of the ``cycle_invalid`` results a smaller duty can cure.
#:
#: Only a discharge enthalpy beyond the fluid's equation of state: that is a
#: discharge far above any limit, cured like :data:`DISCHARGE_TEMP_ABOVE_MAX`.
#: Every other ``cycle_invalid`` stays a failure. In particular a request with
#: no cycle at all is not reduced: with the boilers that is a condensing
#: temperature (tank temperature plus the condenser approach the duty needs)
#: at or above the critical point. With ``T_cond_max`` switched off the
#: largest smaller duty would sit on the critical point itself -- a state no
#: machine runs at -- so the request is reported as it is: not an operating
#: point. (With the limit on, such a request is refused by the limit where a
#: cycle exists, and reduced onto it.)
_REDUCIBLE_CYCLE_DETAIL: Mapping[str, str] = {"discharge_state_undefined": BINDING_DISCHARGE}
#: Failure reasons a *larger* duty can cure. At low speed the efficiency falls:
#: the discharge runs hotter, and on the speed floor the cycle delivers more
#: than the nameplate (``cycle_invalid``).
#: A duty refused for one of these, below a duty refused for another reason,
#: brackets the duties that run (:func:`_between_reasons`).
_CURED_BY_MORE_DUTY = frozenset({DISCHARGE_TEMP_ABOVE_MAX, "cycle_invalid"})

#: Duties tried, as fractions of the nameplate and from the bottom, to find one
#: that runs above a request the envelope refuses from below
#: (:func:`raise_duty_within_envelope`; the ladder of
#: :class:`~tmhp.AirSourceHeatPump`'s own search).
_RAISE_LADDER = (0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.6, 0.8, 1.0, 1.5, 2.0)
#: Relative width at which the bisection for the smallest continuous duty stops.
_RAISE_REL_TOL = 5e-3
#: Refusals of a request that :func:`raise_duty_within_envelope` answers with
#: the smallest duty that runs: the limit the compressor meets towards *low*
#: speed at a large lift. The isentropic efficiency falls there, so the
#: discharge runs hotter (:data:`DISCHARGE_TEMP_ABOVE_MAX`, once the
#: discharge-temperature control is used up). A larger duty leaves it behind.
#: The discharge limit also refuses duties from above (the lift grows with the
#: duty); that side is :func:`reduce_duty_within_envelope`'s.
_RAISED_OVER = frozenset({DISCHARGE_TEMP_ABOVE_MAX})
#: Model attribute holding the smallest continuous rows found while
#: :func:`remembering_smallest_duties` is active (``None`` outside it).
_SMALLEST_DUTY_MEMO = "_smallest_duty_memo"


def duty_reduction_binding(result: Mapping[str, Any], bindings: Mapping[str, str] | None = None) -> str | None:
    """The limit a failed fixed-duty result ran into, if a smaller duty may run; else ``None``.

    The value is the ``capacity_binding`` a reduced point reports.
    ``bindings`` lets a family rename one (the ASHPB's heat exchanger is its
    outdoor coil).
    """
    detail = result.get("failure_detail")
    if detail in _NOT_REDUCIBLE_DETAIL:
        return None
    reason = result.get("failure_reason")
    if reason == "cycle_invalid":
        return _REDUCIBLE_CYCLE_DETAIL.get(cast(str, detail))
    binding = _REDUCIBLE_BINDING.get(cast(str, reason))
    if binding is not None and bindings is not None:
        binding = bindings.get(cast(str, reason), binding)
    return binding


def _running(result: Mapping[str, Any]) -> bool:
    return bool(result.get("hp_is_on", False)) and result.get("failure_reason", "none") == "none"


def _delivered(result: Mapping[str, Any], key: str) -> float:
    try:
        return abs(float(result.get(key, math.nan)))
    except (TypeError, ValueError):
        return math.nan


def _annotate(model: Any, result: dict) -> None:
    models = tuple(getattr(model, name, None) for name in ("eta_cmp_isen", "eta_cmp_vol", "eta_cmp"))
    result["efficiency_extrapolated"] = bool(_running(result) and efficiency_extrapolated_at(models, result))
    # Suction state and control flag on every steady result of every family.
    running = _running(result) and _T_DIS_KEY in result
    result.setdefault(SUPERHEAT_KEY, _superheat(result) if running else math.nan)
    result.setdefault(QUALITY_KEY, 1.0 if running else math.nan)
    result.setdefault(CONTROL_KEY, False)
    if _running(result) and result.get("capacity_clamped") == "max" and "capacity_binding" not in result:
        # The boilers reach their ceiling only through the speed bound.
        result["capacity_binding"] = BINDING_COMPRESSOR


def _reduce_to_envelope(
    solve: Callable[[float], dict],
    request: float,
    delivered_key: str,
    nothing_below: Callable[[], bool] | None = None,
    refused: Mapping[str, Any] | None = None,
    on_condensing_limit: Callable[[dict], dict | None] | None = None,
    solved: dict[float, tuple[dict, bool]] | None = None,
) -> tuple[dict | None, dict | None]:
    """Largest duty below ``request`` whose solved point is a running point.

    A ladder of smaller duties finds the highest one that runs; a bisection
    between it and the rung above closes on the boundary. Returns that result
    and the failed trial just above it (``None`` when every trial above it ran),
    or ``(None, None)`` when no duty runs. The limits are not monotone in the
    duty (at low speed the efficiency falls and the discharge runs hotter), so
    this is the boundary nearest the request, not a global search.

    The duties that run can lie between two rungs: above them one limit
    refuses the duty (the pressure ratio, the source heat exchanger), below
    them another (the discharge temperature at low speed). So when no rung
    runs, each pair of neighbouring trials refused for *different* reasons is
    bisected by reason (:func:`_between_reasons`), ``refused`` being the
    result at the request itself.

    Two short cuts decide "no duty runs" without the whole ladder. A request
    refused by a limit that only tightens with the duty
    (:func:`_tightens_with_duty`) has its lowest rung solved first: refused
    the same way there, it is refused at every duty in between.
    ``nothing_below()``, asked after a failed rung, says that every smaller
    duty is the same machine state (the compressor on its speed floor at
    every candidate); the ladder then stops.

    A request refused by the condensing limit whose lowest rung runs is closed
    without the ladder: ``on_condensing_limit(lowest rung's result)`` returns
    the duty on the limit (:func:`_duty_on_condensing_limit`), or ``None`` to
    leave it to the ladder. ``solved`` carries trials already solved (duty ->
    result and its ``nothing_below()``).
    """
    tolerance = _REDUCTION_REL_TOL * request
    solved = {} if solved is None else solved

    def rung(fraction: float) -> tuple[float, dict, bool]:
        q = fraction * request
        if q not in solved:
            r = solve(q)
            solved[q] = (r, nothing_below is not None and nothing_below())
        return (q, *solved[q])

    if refused is not None and _tightens_with_duty(refused):
        _, lowest, _ = rung(_REDUCTION_LADDER[-1])
        if not _running(lowest) and _tightens_with_duty(lowest):
            return None, None
        if (
            on_condensing_limit is not None
            and refused.get("failure_reason") == CONDENSING_TEMP_ABOVE_MAX
            and _running(lowest)
        ):
            on_limit = on_condensing_limit(lowest)
            if on_limit is not None:
                return on_limit, dict(refused)
    failed: list[tuple[float, Mapping[str, Any]]] = [] if refused is None else [(request, refused)]
    hi_q, hi_r = request, None
    lo_q, lo_r = 0.0, None
    for fraction in _REDUCTION_LADDER:
        q, r, floor_bound = rung(fraction)
        if _running(r):
            lo_q, lo_r = q, r
            break
        hi_q, hi_r = q, r
        failed.append((q, r))
        if floor_bound:
            break
    if lo_r is None:
        for (q_above, r_above), (q_below, r_below) in zip(failed, failed[1:], strict=False):
            found = _between_reasons(solve, q_above, r_above, q_below, r_below, tolerance)
            if found is not None:
                lo_q, lo_r, hi_q, hi_r = found
                break
    if lo_r is None:
        return None, None
    while hi_q - lo_q > tolerance:
        mid = 0.5 * (lo_q + hi_q)
        r = solve(mid)
        if _running(r):
            lo_q, lo_r = mid, r
        else:
            hi_q, hi_r = mid, r
    delivered = _delivered(lo_r, delivered_key)
    if not (math.isfinite(delivered) and delivered < request):
        return None, None
    return lo_r, hi_r


def _reduced_binding(
    model: Any, reduced: Mapping[str, Any], above: Mapping[str, Any], bindings: Mapping[str, str] | None
) -> str:
    """``capacity_binding`` of a reduced point: the limit active *at that point*.

    ``reduced`` is the running point returned, ``above`` the failed trial just
    above it (or the refused request). The name is what bounds the delivered
    duty of ``reduced``, not where the duty search stopped, so every request
    that lands on the same operating point reports the same limit. When more
    than one limit is active at the point, the first of these names it:

    1. ``"pressure_ratio"`` -- the point sits on ``PR_cycle_max`` (the source
       heat exchanger may ask for a larger approach than the ceiling leaves,
       so the next larger duty is refused for the exchanger);
    2. ``"compressor"`` -- the point's own solve already ran at the speed
       ceiling (``capacity_clamped='max'``, ``rps_max``): every larger duty
       asked for delivers this same point, so a refusal further up the duty
       search is a property of the request, not of the point (the family's
       own binding is kept when its solve set one);
    3. the limit the next larger duty ran into
       (:func:`duty_reduction_binding`, ``"cycle"`` when none names it) --
       the duty search closed on that limit's boundary.
    """
    if _on_pressure_ratio_ceiling(model, reduced):
        return BINDING_PRESSURE_RATIO
    if reduced.get("capacity_clamped") == CAPACITY_CLAMPED_MAX:
        return cast(str, reduced.get("capacity_binding") or BINDING_COMPRESSOR)
    return duty_reduction_binding(above, bindings) or BINDING_CYCLE


def _on_pressure_ratio_ceiling(model: Any, result: Mapping[str, Any]) -> bool:
    """A running state whose pressure ratio is ``PR_cycle_max`` (to 0.1 %)."""
    ceiling = getattr(model, "PR_cycle_max", None)
    try:
        ratio = float(result["P_ref_cmp_out [Pa]"]) / float(result["P_ref_cmp_in [Pa]"])
        return ceiling is not None and ratio >= float(ceiling) * (1.0 - 1e-3)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


def _tightens_with_duty(result: Mapping[str, Any]) -> bool:
    """A refusal by a limit that a larger duty can only make worse.

    The pressure ratio: a larger duty needs a larger condenser approach and a
    larger source approach, so it rises with the duty at every candidate. The
    condensing temperature, for the first of those two reasons. And
    an outdoor coil that absorbs too little at its largest airflow at every
    approach the cycle reaches (``failure_detail``): a larger duty asks more
    of it. The discharge temperature is not such a limit (it also rises
    towards low speed), nor is a heat exchanger refusal without that detail.
    """
    reason = result.get("failure_reason")
    if reason in (PR_ABOVE_MAX, CONDENSING_TEMP_ABOVE_MAX):
        return True
    return reason == "hx_not_converged" and result.get("failure_detail") == "outdoor_coil_short_at_max_airflow"


def _between_reasons(
    solve: Callable[[float], dict],
    q_above: float,
    r_above: Mapping[str, Any],
    q_below: float,
    r_below: Mapping[str, Any],
    tolerance: float,
) -> tuple[float, dict, float, dict] | None:
    """A running duty between two refused ones, if their reasons differ.

    The upper duty is refused by a limit a smaller duty cures and the lower
    one by a limit a larger duty cures; duties that run, if any, lie where
    the two reasons meet. Bisect by reason: a trial refused like the upper
    duty moves the upper end down, one refused like the lower duty moves the
    lower end up.
    Returns ``(duty, result, refused duty above, its result)`` for the first
    trial that runs, or ``None`` when the reasons meet without one (or
    another third reason appears, which this cannot order).
    """
    reason_above, reason_below = r_above.get("failure_reason"), r_below.get("failure_reason")
    low_side = reason_below in _CURED_BY_MORE_DUTY or r_below.get("failure_detail") in _NOT_REDUCIBLE_DETAIL
    if reason_above == reason_below or not low_side:
        return None
    upper: dict = dict(r_above)
    while q_above - q_below > tolerance:
        mid = 0.5 * (q_above + q_below)
        r = solve(mid)
        if _running(r):
            return mid, r, q_above, upper
        reason = r.get("failure_reason")
        if reason == reason_above:
            q_above, upper = mid, r
        elif reason == reason_below:
            q_below = mid
        else:
            return None
    return None


#: Secant steps :func:`_duty_on_condensing_limit` may take before the ladder takes over.
_CONDENSING_SECANT_STEPS = 4
#: A duty is on the condensing limit once it condenses within this of ``T_cond_max`` [K]
#: (0.05 K of condenser approach is about 1 % of a rated duty).
_CONDENSING_CLOSE_K = 0.05


def _no_cycle_under_condensing_limit(model: Any, result: Mapping[str, Any]) -> bool:
    """A request for which no refrigerant cycle exists at all, on a model with a condensing limit.

    ``cycle_invalid`` without a ``failure_detail``: with the boilers that is a
    condensing temperature at or above the critical point, which is beyond
    every condensing limit.
    """
    return (
        getattr(model, "T_cond_max", None) is not None
        and result.get("failure_reason") == "cycle_invalid"
        and result.get("failure_detail") is None
    )


def _duty_on_condensing_limit(
    model: Any,
    solve: Callable[[float], dict],
    request: float,
    T_refused: float,
    lowest: Mapping[str, Any],
    delivered_key: str,
) -> dict | None:
    """The duty that condenses at ``T_cond_max``, by a secant on the condensing temperature.

    The request condenses at ``T_refused`` [degC], above the limit, and
    ``lowest`` is a smaller duty that runs inside it. The condensing
    temperature is the demand-side temperature plus the condenser approach,
    and the approach is close to proportional to the duty (exactly ``Q / UA``
    for a mixed tank): the duty at the limit follows from the two points, and
    one cycle solve confirms it. Where the relation bends (a stratified tank
    whose coil-side water warms with the duty) the secant is repeated on the
    new point. Returns the running result on the limit, or ``None`` when it
    does not close in :data:`_CONDENSING_SECANT_STEPS` solves or a trial fails
    for another reason (the ladder of :func:`_reduce_to_envelope` then
    decides).
    """
    limit, ref = getattr(model, "T_cond_max", None), getattr(model, "ref", None)
    if limit is None or ref is None:
        return None
    limit = float(limit)
    tolerance = _REDUCTION_REL_TOL * request
    q_hi, T_hi = request, T_refused
    q_lo, T_lo = _delivered(lowest, delivered_key), _dew_temperature_C(lowest.get(_P_COND_KEY), ref)
    for _ in range(_CONDENSING_SECANT_STEPS):
        if not (math.isfinite(T_hi) and math.isfinite(T_lo) and T_lo <= limit < T_hi and 0.0 < q_lo < q_hi):
            return None
        q = q_lo + (limit - T_lo) * (q_hi - q_lo) / (T_hi - T_lo)
        if not q_lo <= q < q_hi:
            return None
        model._condensing_refused_C = math.nan
        trial = solve(q)
        if _running(trial):
            delivered = _delivered(trial, delivered_key)
            T = _dew_temperature_C(trial.get(_P_COND_KEY), ref)
            if not (math.isfinite(delivered) and math.isfinite(T)):
                return None
            if limit - T <= _CONDENSING_CLOSE_K or q_hi - delivered <= tolerance:
                return dict(trial)
            if not delivered > q_lo:
                return None
            q_lo, T_lo = delivered, T
        elif trial.get("failure_reason") == CONDENSING_TEMP_ABOVE_MAX:
            q_hi, T_hi = q, float(getattr(model, "_condensing_refused_C", math.nan))
        else:
            return None
    return None


def reduce_duty_within_envelope(
    model: Any,
    result: dict,
    solve: Callable[[float], Any],
    request: float,
    delivered_key: str = "Q_ref_tank [W]",
    bindings: Mapping[str, str] | None = None,
) -> dict:
    """The largest duty with an operating point when the request has none.

    ``result`` is the solve at ``request``; ``solve(q)`` solves the same
    point at duty ``q``. When ``result`` failed on a limit a smaller duty can
    cure (:func:`duty_reduction_binding`: the condensing limit, the
    discharge-temperature limit even under its control, the pressure-ratio
    ceiling, the source heat exchanger's range), smaller duties are tried
    (:func:`_reduce_to_envelope`) and the largest that runs is returned with
    ``capacity_clamped='max'``, ``Q_max [W]``, ``Q_request [W]`` and
    ``capacity_binding`` naming the limit active at that point
    (:func:`_reduced_binding`: the pressure-ratio ceiling, then the speed
    ceiling, then the limit the next larger duty tried ran into);
    otherwise ``result`` is returned. The boilers use it for
    ``analyze_steady`` (:func:`steady_envelope`) and for each dynamic step's
    cycle, so a step runs at the same reduced duty as the steady solve -- its
    run time and step-average heat follow from that duty. The trial solves run
    silently.

    The condensing limit is closed directly (:func:`_duty_on_condensing_limit`):
    the request, the lowest duty of the ladder and the duty on the limit are
    the three solves of a step that runs against it, and two decide that no
    duty runs. On a model with a condensing limit a request for which no cycle
    exists at all (``cycle_invalid`` without a detail: its condensing
    temperature is at or above the critical point) is treated as beyond that
    limit: reduced onto it when a smaller duty runs, and otherwise reported
    with ``failure_reason='condensing_temp_above_max'`` when the lowest duty
    tried is refused by the limit.
    """
    reducible = duty_reduction_binding(result, bindings) is not None
    # A request with no cycle at all may lie beyond the condensing limit (its
    # condensing temperature at or above the critical point); see below.
    no_cycle = not reducible and _no_cycle_under_condensing_limit(model, result)
    if not (request > 0 and math.isfinite(request) and (reducible or no_cycle)):
        return result
    # Condensing temperature of the refused request, left by the check that refused it.
    T_refused = float(getattr(model, "_condensing_refused_C", math.nan))

    def quiet(q: float) -> dict:
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            out = solve(q)
        return out if isinstance(out, dict) else {}

    def nothing_below() -> bool:
        # Set by a family whose solve knows it (ASHPB: `_optimize_operation`).
        return bool(getattr(model, "_solve_bound_at_speed_floor", False))

    def on_condensing_limit(lowest: dict) -> dict | None:
        return _duty_on_condensing_limit(model, quiet, request, T_refused, lowest, delivered_key)

    solved: dict[float, tuple[dict, bool]] = {}
    if no_cycle:
        # Where the lowest duty tried is above the condensing limit, so is the
        # request (the condensing temperature rises with the duty): that is
        # the reason it does not run. Where the lowest duty runs, the request
        # is reduced -- but only onto the condensing limit: any other boundary
        # leaves the request the failure it was.
        q_lowest = _REDUCTION_LADDER[-1] * request
        lowest = quiet(q_lowest)
        solved[q_lowest] = (lowest, nothing_below())
        if lowest.get("failure_reason") == CONDENSING_TEMP_ABOVE_MAX:
            return {**result, "failure_reason": CONDENSING_TEMP_ABOVE_MAX}
        if not _running(lowest):
            return result
        reduced, above = _reduce_to_envelope(quiet, request, delivered_key, nothing_below, solved=solved)
        if reduced is None or above is None or above.get("failure_reason") != CONDENSING_TEMP_ABOVE_MAX:
            return result
    else:
        reduced, above = _reduce_to_envelope(
            quiet, request, delivered_key, nothing_below, result, on_condensing_limit, solved
        )
    if reduced is None:
        lowest = solved.get(_REDUCTION_LADDER[-1] * request)
        if (
            lowest is not None
            and lowest[0].get("failure_reason") == CONDENSING_TEMP_ABOVE_MAX
            and result.get("failure_reason") != CONDENSING_TEMP_ABOVE_MAX
        ):
            # No duty runs, and the smallest one tried condenses above the
            # limit: so does every larger one, the request included, whatever
            # limit its own solve met first. That is why nothing runs.
            refused = {key: value for key, value in result.items() if key != "failure_detail"}
            refused["failure_reason"] = CONDENSING_TEMP_ABOVE_MAX
            return refused
        return result
    binding = _reduced_binding(model, reduced, above if above is not None else result, bindings)
    reduced.update(
        {
            "capacity_clamped": "max",
            "capacity_binding": binding,
            "Q_max [W]": _delivered(reduced, delivered_key),
            "Q_request [W]": request,
        }
    )
    if "PLR_request [-]" in reduced and getattr(model, "hp_capacity", 0) > 0:
        reduced["PLR_request [-]"] = request / model.hp_capacity
    return reduced


@contextlib.contextmanager
def remembering_smallest_duties(model: Any) -> Iterator[None]:
    """Reuse the rows of :func:`raise_duty_within_envelope` while the block runs.

    The smallest continuous duty belongs to the condition, not to the request.
    One ``analyze_steady`` call can solve the same condition at several duties
    (the frost balance of :func:`tmhp.defrost.steady_defrost`); inside the
    block the search runs once per condition. The memory ends with the block,
    because the model may be changed between calls.
    """
    outer = getattr(model, _SMALLEST_DUTY_MEMO, None)
    if outer is None:
        setattr(model, _SMALLEST_DUTY_MEMO, {})
    try:
        yield
    finally:
        setattr(model, _SMALLEST_DUTY_MEMO, outer)


def remember_smallest_duty(function: _F) -> _F:
    """Decorate an ``analyze_steady`` so that its trial solves share :func:`remembering_smallest_duties`.

    Outermost: above the frost correction, whose solves at other duties are
    the ones that share the search.
    """

    @wraps(function)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        with remembering_smallest_duties(self):
            return function(self, *args, **kwargs)

    # ``analyze_steady.__wrapped__`` stays the undecorated solve.
    wrapped.__wrapped__ = getattr(function, "__wrapped__", function)  # type: ignore[attr-defined]
    return cast(_F, wrapped)


def _as_smallest_duty(model: Any, running: Mapping[str, Any], request: float) -> dict:
    """``running`` as the row of a ``request`` below it: ``capacity_clamped='min'`` and the caller's request."""
    floor = dict(running)
    floor["capacity_clamped"] = "min"
    floor["Q_request [W]"] = request
    if "PLR_request [-]" in floor and getattr(model, "hp_capacity", 0) > 0:
        floor["PLR_request [-]"] = request / model.hp_capacity
    return floor


def _condition_key(arguments: Mapping[str, Any], duty_arg: str) -> object:
    """Everything an ``analyze_steady`` call was given but the duty, as a dictionary key (``None`` if unhashable)."""
    items = tuple((name, value) for name, value in arguments.items() if name not in ("self", duty_arg, "return_dict"))
    try:
        hash(items)
    except TypeError:
        return None
    return items


def _remembered_smallest_duty(
    model: Any, result: Mapping[str, Any], request: float, delivered_key: str, condition: object
) -> dict | None:
    """The smallest continuous row already found at this condition, for a request refused from below it."""
    memo = getattr(model, _SMALLEST_DUTY_MEMO, None)
    if memo is None or condition is None or condition not in memo:
        return None
    if result.get("failure_reason") not in _RAISED_OVER or not request < _delivered(memo[condition], delivered_key):
        return None
    return _as_smallest_duty(model, memo[condition], request)


def raise_duty_within_envelope(
    model: Any,
    result: dict,
    solve: Callable[[float], Any],
    request: float,
    delivered_key: str = "Q_ref_tank [W]",
    condition: object = None,
) -> dict:
    """The smallest duty with an operating point when the envelope refuses the request from below.

    The counterpart of :func:`reduce_duty_within_envelope`. The compressor's
    speed floor is not the only lower end of continuous operation. At a large
    lift the isentropic efficiency falls towards low speed, and before
    ``rps_min`` is reached the envelope refuses the point for the discharge
    temperature, which the falling efficiency drives above ``T_dis_max`` even
    with the discharge-temperature control used up. Every duty below the one
    at which that limit lets go is refused, while larger duties run.
    A unit does not stay off there: it runs at the smallest duty the envelope
    admits and switches off when the tank has had enough, exactly as it does
    below its minimum speed. (Variable-speed compressors are sold with that
    restriction: the published envelope of the lowest speeds does not reach
    the pressure ratios of the higher ones, and the controller holds the
    frequency above it.)

    Why the smallest duty and not the most efficient one above it: the request
    just above that duty is met in continuous operation at it, so cycling at
    any other duty would make the result jump at the boundary -- and a
    controller that cycles runs as long as it can, at the lowest frequency its
    protections allow.

    ``result`` is the solve at ``request``, refused for the discharge limit
    (:data:`_RAISED_OVER`) after no smaller duty ran; ``solve(q)`` solves the
    same point at duty ``q``. A ladder of duties above the request
    (:data:`_RAISE_LADDER`, fractions of ``model.hp_capacity``) is climbed
    past the rungs refused the same way to the first that is not; a bisection
    then closes on the smallest duty that runs (to :data:`_RAISE_REL_TOL`).
    The rung that ends the climb may itself be refused -- by a limit of the
    other side (the pressure ratio, the source heat exchanger, the condensing
    temperature): the duties that run, if any, then lie between it and the
    rung below, and the bisection goes by which side refuses the trial.
    The continuous row found is returned with ``capacity_clamped='min'`` and
    the caller's request in its request keys: it delivers more than the
    request, and :func:`tmhp.cycling.steady_cycling` averages it over the
    on/off period like a minimum-speed row (``Q_min [W]`` is its duty).
    ``result`` is returned unchanged when no duty runs: the limits of the two
    sides then meet, or the discharge limit refuses every duty (it also binds
    from above, where the lift grows with the duty). A trial at the
    compressor's speed ceiling (it delivers less than its duty) bounds the
    search from above but is not returned. The trial solves run silently.

    ``condition`` identifies the operating condition (everything but the
    duty); with it and inside :func:`remembering_smallest_duties` the row
    found is reused for every smaller request at the same condition.
    """
    capacity = float(getattr(model, "hp_capacity", math.nan))
    if not (
        request > 0
        and math.isfinite(request)
        and capacity > 0
        and math.isfinite(capacity)
        and result.get("failure_reason") in _RAISED_OVER
    ):
        return result
    remembered = _remembered_smallest_duty(model, result, request, delivered_key, condition)
    if remembered is not None:
        return remembered
    memo = getattr(model, _SMALLEST_DUTY_MEMO, None)

    def trial(q: float) -> tuple[dict | None, dict]:
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            out = solve(q)
        out = out if isinstance(out, dict) else {}
        return (out if _running(out) else None), out

    # The bracket is made of ladder rungs and their midpoints, never of the
    # request: every request below the duty found gets the same row. A duty at
    # or below the request needs no solve (it is refused from below as well).
    # ``q_lo`` is refused from below; ``q_hi`` runs (``running`` is its row),
    # or it is the upper end of where a running duty can be: a duty refused
    # from above, or what the compressor delivers at its speed ceiling.
    q_lo, q_hi, running = 0.0, math.nan, None
    for fraction in _RAISE_LADDER:
        q = fraction * capacity
        if not q > request:
            q_lo = q
            continue
        state, out = trial(q)
        if state is None and out.get("failure_reason") in _RAISED_OVER:
            q_lo = q
            continue
        if state is None:
            q_hi = q
        elif state.get("capacity_clamped") is None:
            q_hi, running = q, state
        elif state.get("capacity_clamped") == "max" and _delivered(state, delivered_key) > q_lo:
            # The compressor's ceiling lies below this rung: the smallest
            # continuous duty, if there is one, is below what it delivers.
            q_hi = _delivered(state, delivered_key)
        break
    if not math.isfinite(q_hi):
        return result
    while q_hi - q_lo > _RAISE_REL_TOL * q_hi:
        mid = 0.5 * (q_lo + q_hi)
        state, out = (None, result) if mid <= request else trial(mid)
        if state is not None and state.get("capacity_clamped") is None:
            q_hi, running = mid, state
        elif running is not None or (state is None and out.get("failure_reason") in _RAISED_OVER):
            q_lo = mid
        elif state is not None and state.get("capacity_clamped") == "max" and _delivered(state, delivered_key) > q_lo:
            q_hi = min(mid, _delivered(state, delivered_key))
        else:
            q_hi = mid  # refused from above: the duties that run, if any, are below
    if running is None:
        return result
    if memo is not None and condition is not None:
        memo[condition] = dict(running)
    return _as_smallest_duty(model, running, request)


def _condensing_limit_binds(model: Any, result: Mapping[str, Any], solve_again: Callable[[], Any]) -> bool:
    """Whether a capacity ceiling the family could not name is the condensing limit.

    The space-conditioning searches (ASHP, GSHP) name a ceiling ``"cycle"``
    when the compressor is not at its speed bound and the discharge limit is
    not what refuses the next duty. If the search rejected candidates for the
    condensing limit, the same request is solved once more without that limit:
    a ceiling that then rises (or disappears) was set by it. ``solve_again``
    repeats the solve; it runs silently.
    """
    if not (
        getattr(model, "_condensing_rejected", False)
        and getattr(model, "T_cond_max", None) is not None
        and _running(result)
        and result.get("capacity_clamped") == "max"
        and result.get("capacity_binding") == BINDING_CYCLE
    ):
        return False
    ceiling = _delivered(result, "Q_max [W]")
    limit, model.T_cond_max = model.T_cond_max, None
    try:
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter("ignore")
            free = solve_again()
    except Exception:
        return False
    finally:
        model.T_cond_max = limit
    if not (isinstance(free, dict) and _running(free) and math.isfinite(ceiling)):
        return False
    return free.get("capacity_clamped") != "max" or _delivered(free, "Q_max [W]") > ceiling * (1.0 + _REDUCTION_REL_TOL)


def steady_envelope(
    duty_arg: str | None = None,
    delivered_key: str = "Q_ref_tank [W]",
    bindings: Mapping[str, str] | None = None,
) -> Callable[[_F], _F]:
    """Decorate an ``analyze_steady`` with the envelope's steady-state reporting.

    Every result gains ``efficiency_extrapolated``. With ``duty_arg`` (the
    boilers, whose requested duty is fixed), a request that fails on a limit a
    smaller duty can cure (:func:`duty_reduction_binding`) is retried at
    smaller duties; the largest one with an operating point is returned with
    ``capacity_clamped='max'``, ``capacity_binding`` naming the limit,
    ``Q_max [W]`` and ``Q_request [W]`` (the caller's request). If no smaller duty runs, the original off-state
    result stands -- unless the discharge limit refused
    the request and a larger duty runs: the smallest such duty is then returned with
    ``capacity_clamped='min'`` (:func:`raise_duty_within_envelope`), for the
    cycling decorator outside this one to average. The trial solves run
    silently. ``bindings`` renames a
    ``capacity_binding`` for the family (:func:`duty_reduction_binding`).
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
            self._condensing_rejected = False
            result = function(*bound.args, **bound.kwargs)
            if not isinstance(result, dict):
                return result
            if duty_arg is None and _condensing_limit_binds(
                self, result, lambda: function(*bound.args, **bound.kwargs)
            ):
                result["capacity_binding"] = BINDING_CONDENSING
            request = float(bound.arguments[duty_arg]) if duty_arg is not None else 0.0
            if (
                duty_arg is not None
                and request > 0
                and math.isfinite(request)
                and (
                    duty_reduction_binding(result, bindings) is not None
                    or _no_cycle_under_condensing_limit(self, result)
                )
            ):

                def solve(q: float) -> Any:
                    arguments = dict(bound.arguments)
                    arguments[duty_arg] = q
                    trial = inspect.BoundArguments(signature, arguments)  # type: ignore[arg-type]
                    return function(*trial.args, **trial.kwargs)

                condition = _condition_key(bound.arguments, duty_arg)
                remembered = _remembered_smallest_duty(self, result, request, delivered_key, condition)
                if remembered is not None:
                    result = remembered
                else:
                    result = reduce_duty_within_envelope(self, result, solve, request, delivered_key, bindings)
                    result = raise_duty_within_envelope(self, result, solve, request, delivered_key, condition)
            _annotate(self, result)
            if want_dict:
                return result
            import pandas as pd

            return pd.DataFrame([result])

        return cast(_F, wrapped)

    return decorate
