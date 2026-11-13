"""Shared rules of the liquid-source boilers (GSHPB, WSHPB).

Two things live here because both boilers need them identically:

1. **Capacity-scaled defaults.** A machine described by ``hp_capacity`` and a
   refrigerant alone gets heat exchangers, a source-loop flow, a circulation
   pump and (GSHPB) a borehole field sized for that capacity, so the rated
   performance does not depend on the capacity chosen.
2. **Cycle closures.** The suction superheat a source stream can provide, and
   the tank heat exchanger re-solved against the heat actually delivered when
   the compressor sits on a speed bound.

Default sizing rules
--------------------
``UA_tank_hx = hp_capacity / 5``
    A 5 K condensing approach at the rated duty, the rule of the air-source
    boiler (:class:`~tmhp.AirSourceHeatPumpBoiler`).
``UA_source = 0.18 * hp_capacity``
    The liquid-to-refrigerant evaporator conductance per watt of capacity, the
    coefficient of the space-conditioning ground-source model
    (``ground_hx_ua_per_capacity``).
source-loop flow ``= 0.75 * hp_capacity / (rho c * 3 K)``
    The rated source-side heat (0.75 of the condenser duty, i.e. COP 4) carried
    at a 3 K loop temperature difference: 3.58 L/min per kW.
``E_pmp = 75 W/ton * hp_capacity``
    The upper bound of ASHRAE ground-loop pumping grade B, 75 W of pump input
    per ton (3.517 kW) = 21.3 W per kW (Kavanaugh & Rafferty 2014, Table 6.2;
    the same table :func:`tmhp.pump.calc_aux_loss_from_ashrae_grade` reads its
    pressure budgets from: grade B ends at 210 kPa at 3 L/min per kW, which at
    the table's 70 % hydraulic efficiency is 15 W per kW at the shaft, i.e.
    7.5 hp per 100 tons, and 75 W per ton at the motor terminals).
borehole field (GSHPB)
    With none of ``N_1``, ``N_2``, ``H_b`` given, boreholes of the default
    depth are added until the rated source-side heat is at most 50 W per metre
    of borehole, on a near-square grid. A field the caller describes is used
    as given; above 80 W/m a ``UserWarning`` names the load.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable
from typing import TypeVar

import CoolProp.CoolProp as CP
from scipy.optimize import brentq

from .constants import c_w, mu_w, rho_w

_Outcome = TypeVar("_Outcome")

#: Rated condensing approach of the default tank heat exchanger [K].
TANK_HX_APPROACH_RATED_K = 5.0
#: Default source-side (evaporator) conductance per watt of capacity [W/K per W].
SOURCE_HX_UA_PER_CAPACITY = 0.18
#: Source-side heat at the rated point as a fraction of ``hp_capacity`` (COP 4).
SOURCE_HEAT_FRACTION_RATED = 0.75
#: Loop temperature difference the default source flow carries that heat at [K].
SOURCE_LOOP_DT_RATED_K = 3.0
#: Default pump input per watt of capacity: ASHRAE grade B upper bound, 75 W per ton.
PUMP_POWER_PER_CAPACITY = 75.0 / 3516.85
#: Default borehole depth [m] (also the default length of one submerged coil).
BOREHOLE_DEPTH_DEFAULT_M = 200.0
#: Rated source-side heat per metre the default borehole field is sized for [W/m].
BOREHOLE_LOAD_DESIGN_W_PER_M = 50.0
#: Rated source-side heat per metre above which a caller's field draws a warning [W/m].
BOREHOLE_LOAD_WARN_W_PER_M = 80.0
#: Reynolds number below which pipe flow is laminar.
RE_LAMINAR = 2300.0
#: Evaporator approach band every operating-point search starts from [K].
APPROACH_BOUNDS_K = (1.0, 20.0)
#: Smallest evaporator approach searched when the balance lies below that band [K].
APPROACH_MIN_K = 0.05
#: Tolerance of the in-step source-temperature closure [K].
SOURCE_CLOSURE_TOL_K = 1.0e-4
#: Tank-side approach tolerance of the speed-bound closure [K].
_CLOSURE_XTOL_K = 1.0e-6
#: Secant steps the closure takes before it falls back to bracketing.
_CLOSURE_SECANT_STEPS = 8


def default_tank_hx_UA(hp_capacity: float) -> float:
    """Default tank heat-exchanger conductance [W/K]: 5 K approach at ``hp_capacity``."""
    return hp_capacity / TANK_HX_APPROACH_RATED_K


def default_source_hx_UA(hp_capacity: float) -> float:
    """Default source-side evaporator conductance [W/K]."""
    return SOURCE_HX_UA_PER_CAPACITY * hp_capacity


def rated_source_heat(hp_capacity: float) -> float:
    """Source-side heat of the rated point the defaults are sized for [W]."""
    return SOURCE_HEAT_FRACTION_RATED * hp_capacity


def default_source_flow_lpm(hp_capacity: float) -> float:
    """Default total source-loop flow [L/min]: rated source heat at a 3 K loop difference."""
    return rated_source_heat(hp_capacity) / (rho_w * c_w * SOURCE_LOOP_DT_RATED_K) * 60000.0


def default_pump_power(hp_capacity: float) -> float:
    """Default source-loop pump input [W]: ASHRAE grade B upper bound (75 W per ton)."""
    return PUMP_POWER_PER_CAPACITY * hp_capacity


def default_borehole_grid(hp_capacity: float, H_b: float = BOREHOLE_DEPTH_DEFAULT_M) -> tuple[int, int]:
    """Smallest near-square ``(N_1, N_2)`` keeping the rated load at or below 50 W/m."""
    needed = rated_source_heat(hp_capacity) / (BOREHOLE_LOAD_DESIGN_W_PER_M * H_b)
    n = max(1, math.ceil(needed - 1e-9))
    n_1 = math.ceil(math.sqrt(n) - 1e-9)
    n_2 = math.ceil(n / n_1 - 1e-9)
    return n_1, n_2


def rated_linear_load(hp_capacity: float, total_length: float) -> float:
    """Rated source-side heat per metre of exchanger [W/m]."""
    return rated_source_heat(hp_capacity) / total_length


def warn_if_field_overloaded(hp_capacity: float, n_exchangers: int, length_each: float, what: str) -> None:
    """``UserWarning`` when a caller's field carries more than 80 W/m at the rated point."""
    load = rated_linear_load(hp_capacity, n_exchangers * length_each)
    if load > BOREHOLE_LOAD_WARN_W_PER_M:
        warnings.warn(
            f"{what}: the rated source-side heat ({rated_source_heat(hp_capacity):.0f} W = "
            f"{SOURCE_HEAT_FRACTION_RATED:g} x hp_capacity) is {load:.0f} W per metre of the "
            f"{n_exchangers} x {length_each:g} m field, above {BOREHOLE_LOAD_WARN_W_PER_M:g} W/m; "
            "the loop fluid will run far below the undisturbed source temperature "
            f"(the default field is sized for {BOREHOLE_LOAD_DESIGN_W_PER_M:g} W/m)",
            UserWarning,
            stacklevel=4,
        )


def pipe_reynolds(volume_flow_m3s: float, n_parallel: int, r_in: float) -> float:
    """Reynolds number of water in one of ``n_parallel`` pipes of inner radius ``r_in``."""
    m_dot_pipe = volume_flow_m3s * rho_w / max(1, n_parallel)
    return 4.0 * m_dot_pipe / (math.pi * 2.0 * r_in * mu_w)


def warn_if_laminar(volume_flow_m3s: float, n_parallel: int, r_in: float, what: str) -> None:
    """``UserWarning`` when the default source flow is laminar in the pipes."""
    reynolds = pipe_reynolds(volume_flow_m3s, n_parallel, r_in)
    if reynolds < RE_LAMINAR:
        warnings.warn(
            f"{what}: the default source-loop flow ({volume_flow_m3s * 60000.0:.2f} L/min over "
            f"{n_parallel} parallel pipe(s) of {2000.0 * r_in:.0f} mm bore) is laminar, Re = {reynolds:.0f} "
            f"< {RE_LAMINAR:.0f}; the pipe-side heat transfer is then poor. Give the loop flow, fewer "
            "parallel pipes or a smaller pipe",
            UserWarning,
            stacklevel=4,
        )


def warn_if_setpoint_unreachable(
    *,
    what: str,
    ref: str,
    T_tank_w_upper_bound: float,
    hp_capacity: float,
    UA_tank_hx: float,
) -> None:
    """``UserWarning`` when the upper set point needs a condensing state the refrigerant lacks.

    Charging the tank to ``T_tank_w_upper_bound`` at the rated duty condenses at
    ``T_tank_w_upper_bound + hp_capacity / UA_tank_hx``. At or above the
    critical temperature the subcritical cycle has no condenser, so every call
    for heat above ``T_crit - hp_capacity / UA_tank_hx`` would be a failed step
    (``cycle_invalid``) and the tank would never reach its set point.
    """
    T_crit_C = float(CP.PropsSI("Tcrit", ref)) - 273.15
    approach = hp_capacity / UA_tank_hx
    T_cond_C = T_tank_w_upper_bound + approach
    if T_cond_C >= T_crit_C:
        warnings.warn(
            f"{what}: reaching T_tank_w_upper_bound={T_tank_w_upper_bound:g} °C at hp_capacity needs a "
            f"condensing temperature of {T_cond_C:.1f} °C (set point + {approach:.1f} K tank heat-exchanger "
            f"approach), at or above the critical temperature of {ref} ({T_crit_C:.1f} °C). No cycle exists "
            f"there: calls for heat with the tank above about {T_crit_C - approach:.0f} °C will fail "
            "(failure_reason='cycle_invalid') and the tank will not reach its set point. Lower "
            "T_tank_w_upper_bound, enlarge UA_tank_hx, or use a refrigerant with a higher critical "
            "temperature (e.g. R134a, R290)",
            UserWarning,
            stacklevel=4,
        )


def approach_bracket(objective: Callable[[float], float]) -> tuple[float, float]:
    """Bracket the evaporator approach [K] at which the source heat exchanger carries the cycle.

    ``objective(x)`` is the evaporator duty the cycle needs less the duty the
    heat exchanger transfers at approach ``x``; it raises ``ValueError`` where
    no cycle exists. The needed duty falls and the transferred duty rises with
    the approach, so the balance is decreasing in ``x``:

    - negative at the low end of :data:`APPROACH_BOUNDS_K`: the balance lies
      below 1 K -- a compressor at low speed against a heat exchanger sized
      for the full capacity -- and is bracketed down to :data:`APPROACH_MIN_K`;
    - positive at both ends: the heat exchanger cannot carry the duty at any
      approach (``ValueError``, no search);
    - otherwise the sampled search of
      :func:`tmhp.reference_state._evaluable_bracket`, which also covers ends
      at which no cycle exists.
    """
    from .reference_state import _evaluable_bracket

    seen: dict[float, float | ValueError] = {}

    def cached(x: float) -> float:
        if x not in seen:
            try:
                seen[x] = float(objective(x))
            except ValueError as exc:
                seen[x] = exc
        value = seen[x]
        if isinstance(value, ValueError):
            raise value
        return value

    def finite(x: float) -> float | None:
        try:
            value = cached(x)
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    lo, hi = APPROACH_BOUNDS_K
    f_lo, f_hi = finite(lo), finite(hi)
    if f_lo is not None and f_lo < 0.0:
        # Already over-absorbing at the smallest approach of the band: the
        # balance lies below it. A sign change down to APPROACH_MIN_K is a
        # bracket whatever the shape of the residual.
        f_min = finite(APPROACH_MIN_K)
        if f_min is not None and f_min >= 0.0:
            return (APPROACH_MIN_K, APPROACH_MIN_K) if f_min == 0.0 else (APPROACH_MIN_K, lo)
        if f_hi is not None and f_hi < 0.0:
            raise ValueError(f"the source heat exchanger over-absorbs down to an approach of {APPROACH_MIN_K:g} K")
    elif f_lo is not None and f_hi is not None and f_lo > 0.0 and f_hi > 0.0:
        raise ValueError(f"the source heat exchanger cannot carry the duty at an approach of {hi:g} K")
    return _evaluable_bracket(cached, lo, hi, endpoints_first=True)


def capped_superheat(dT_superheat: float, dT_approach: float, dT_hx_min: float) -> float:
    """Suction superheat the source stream can provide [K].

    The refrigerant leaves the evaporator no hotter than the entering source
    stream less the minimum heat-exchanger temperature difference, so the
    superheat is at most the evaporator approach less ``dT_hx_min``.
    """
    return min(dT_superheat, max(0.0, dT_approach - dT_hx_min))


def close_tank_hx(
    cycle: Callable[[float, float | None], dict | None],
    state0: dict,
    dT_request: float,
    UA_tank_hx: float,
) -> dict | None:
    """Re-solve the tank-side approach against the heat a speed-bound compressor delivers.

    ``cycle(dT_tank, rps_fixed)`` evaluates the refrigerant cycle at a
    condensing approach ``dT_tank`` [K] and returns a state carrying
    ``"Q_tank"`` [W], ``"dT_tank"`` [K] (the approach the cycle ran at),
    ``"rps"``, ``"capacity_clamped"`` and ``"converged_rps"`` (or ``None`` when
    no cycle exists there). ``state0`` is that state at the approach of the
    *requested* duty, ``dT_request``, with the speed solved for the request.

    Outside its modulating band the compressor delivers a different heat than
    was requested, so an approach derived from the request describes a
    condenser transferring a duty the cycle does not produce. The speed is
    held at the bound and the approach becomes the unknown of
    ``Q_delivered(dT) = UA_tank_hx * dT`` -- the closure of
    :class:`~tmhp.AirSourceHeatPumpBoiler`. Returns the closed state, or
    ``None`` when the balance cannot be bracketed (the caller keeps
    ``state0``; the pressure-ratio floor, which fixes the condensing state,
    is one such case).
    """
    rps_bound = state0["rps"]
    r0 = state0["Q_tank"] - UA_tank_hx * state0["dT_tank"]
    if r0 == 0.0:
        return state0
    if state0.get("pr_event") is not None:
        # The pressure-ratio floor fixes the condensing state: the heat and the
        # approach no longer follow dT, so there is nothing to close.
        return None
    tolerance = _CLOSURE_XTOL_K * UA_tank_hx

    def finish(closed: dict) -> dict:
        closed["capacity_clamped"] = state0["capacity_clamped"]
        closed["converged_rps"] = state0["converged_rps"]
        return closed

    # Secant from the fixed point dT = Q / UA. The delivered heat changes by
    # about 1 % per kelvin of approach while UA * dT changes by UA, so the
    # residual is nearly linear and two or three cycles close it.
    x_prev, r_prev = dT_request, r0
    x = state0["Q_tank"] / UA_tank_hx
    for _ in range(_CLOSURE_SECANT_STEPS):
        if not (math.isfinite(x) and x > 0.0):
            break
        st = cycle(x, rps_bound)
        if st is None or st.get("pr_event") is not None:
            break
        r = float(st["Q_tank"] - UA_tank_hx * st["dT_tank"])
        if not math.isfinite(r):
            break
        if abs(r) <= tolerance:
            return finish(st)
        if r == r_prev:
            break
        x_prev, r_prev, x = x, r, x - r * (x - x_prev) / (r - r_prev)

    # Bracketing fallback (the closure of AirSourceHeatPumpBoiler).
    def residual(dT: float) -> float:
        st = cycle(dT, rps_bound)
        if st is None:
            return math.nan
        return float(st["Q_tank"] - UA_tank_hx * st["dT_tank"])

    lo = hi = dT_request
    if r0 > 0.0:
        # Over-delivery (speed floor): the approach has to widen.
        for _ in range(12):
            hi = hi * 1.5 + 0.5
            r_hi = residual(hi)
            if not math.isfinite(r_hi):
                return None
            if r_hi <= 0.0:
                break
        else:
            return None
    else:
        # Shortfall (speed ceiling): the approach has to narrow.
        for _ in range(12):
            lo = lo * 0.6
            if lo < 1.0e-3:
                return None
            r_lo = residual(lo)
            if not math.isfinite(r_lo):
                return None
            if r_lo >= 0.0:
                break
        else:
            return None
    try:
        dT_closed = brentq(residual, lo, hi, xtol=_CLOSURE_XTOL_K, rtol=1.0e-12, maxiter=100)
    except (ValueError, RuntimeError):
        return None
    closed = cycle(float(dT_closed), rps_bound)
    if closed is None:
        return None
    return finish(closed)


def close_source_temperature(
    solve: Callable[[float], tuple[_Outcome, dict | None]],
    outlet_K: Callable[[dict], float],
    T_guess_K: float,
    T_unloaded_K: float,
    *,
    tol_K: float = SOURCE_CLOSURE_TOL_K,
    max_iter: int = 20,
) -> tuple[_Outcome, dict | None]:
    """Solve the cycle against the source temperature its own extraction produces.

    ``solve(T_K)`` returns ``(outcome, result)`` for the heat pump fed with
    source fluid at ``T_K`` (``result`` is ``None`` when it has no operating
    point there); ``outlet_K(result)`` is the temperature at which the source
    loop returns fluid to the heat pump while it extracts that result's heat.
    The operating point is the fixed point ``outlet_K(solve(T)) = T``: the
    evaporator sees the fluid of the same step, not of the step before
    (#96 G1).

    The extraction changes little with the source temperature, so the fixed
    point is strongly contracting; a secant step on the second iterate closes
    it in two or three cycle solves. ``T_unloaded_K`` is the source
    temperature without extraction (the warmest the loop can be): when no
    cycle exists at the guess, it is tried before the call is given up.
    Returns ``(outcome, result)`` of the closed point, or the failed solve's
    ``(outcome, None)``.
    """
    T = T_guess_K
    outcome, result = solve(T)
    if result is None and T_unloaded_K != T:
        T = T_unloaded_K
        outcome, result = solve(T)
    if result is None:
        return outcome, None
    previous: tuple[float, float] | None = None
    for _ in range(max_iter):
        assert result is not None
        error = outlet_K(result) - T
        if not math.isfinite(error):
            return outcome, None
        if abs(error) <= tol_K:
            return outcome, result
        T_next = T + error
        if previous is not None and error != previous[1]:
            secant = T - error * (T - previous[0]) / (error - previous[1])
            if math.isfinite(secant) and abs(secant - T) <= 2.0 * abs(error) + tol_K:
                T_next = secant
        # A step that leaves the cycle's domain is halved towards the last solved point.
        for _halving in range(8):
            next_outcome, next_result = solve(T_next)
            if next_result is not None:
                break
            T_next = 0.5 * (T + T_next)
        else:
            return next_outcome, None
        previous = (T, error)
        T, outcome, result = T_next, next_outcome, next_result
    return outcome, None
