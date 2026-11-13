"""Shared dynamic simulation context and control helpers.

Provides reusable dataclasses and pure functions that form the
backbone of time-stepping heat-pump simulations.  Extracted from
``AirSourceHeatPumpBoiler`` so that ``GroundSourceHeatPumpBoiler``
and future models can share the same infrastructure.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from .constants import c_w, rho_w

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "ControlState",
    "CycleSolveOutcome",
    "DynamicState",
    "InStepRun",
    "EVENT_TIME_TOL_S",
    "StepContext",
    "TEMPERATURE_TOL_K",
    "bound_tolerance",
    "Subsystem",
    "SubsystemExergy",
    "check_hp_schedule_active",
    "delivered_tank_heat",
    "determine_heat_source_on_off",
    "determine_tank_refill_flow",
    "resolve_step_operation",
    "scale_running_rates",
    "simulate_thermostat_step",
    "step_hp_result",
    "T_WATER_BOIL_C",
    "TANK_LEVEL_MIN",
    "TankStepFlows",
    "limit_tank_flows",
    "tank_free_heat_rate",
    "tank_mass_energy_residual",
    "tank_step_flows",
    "unphysical_cycle_detail",
    "warn_if_tank_boiling",
]


# ------------------------------------------------------------------
# Per-timestep immutable context
# ------------------------------------------------------------------


@dataclass
class StepContext:
    """Per-timestep immutable context (time, environment, demand).

    Attributes
    ----------
    n : int
        Current step index.
    current_time_s : float
        Elapsed simulation time [s].
    current_hour : float
        Elapsed simulation time [h].
    hour_of_day : float
        Hour within the current day (0–24, repeating).
    T0 : float
        Dead-state / outdoor-air temperature [°C].
    T0_K : float
        Dead-state temperature [K].
    activation_flags : dict[str, bool]
        Per-subsystem schedule activation flags for this step.
        e.g. ``{"stc": True}`` when the STC preheat window is active.
        An empty dict means no subsystem has a schedule constraint.
    T_tank_w_K : float
        Current tank water temperature [K].
    tank_level : float
        Fractional tank fill level (0–1).
    dV_mix_w_out : float
        Service water draw-off flow rate [m³/s].
    I_DN : float
        Direct-normal irradiance on collector plane [W/m²].
    I_dH : float
        Diffuse-horizontal irradiance [W/m²].
    T_sup_w_K : float
        Mains water supply temperature [K].
    """

    n: int
    current_time_s: float
    current_hour: float
    hour_of_day: float
    T0: float
    T0_K: float
    activation_flags: dict  # dict[str, bool] — subsystem schedule keys e.g. {"stc": True}
    T_tank_w_K: float
    tank_level: float
    dV_mix_w_out: float
    I_DN: float = 0.0
    I_dH: float = 0.0
    T_sup_w_K: float = 288.15  # Mains supply water [K]


# ------------------------------------------------------------------
# Control decisions produced by Phase-A helpers
# ------------------------------------------------------------------


@dataclass
class ControlState:
    """Heat-source control decisions for one timestep.

    Model-agnostic container: any boiler model populates
    these fields in its Phase-A helper.  Subsystem states
    are managed separately via ``sub_states: dict[str, dict]``.

    Attributes
    ----------
    is_on : bool
        Whether the heat source is running.
    Q_heat_source : float
        Net heat delivered to the tank from the heat
        source [W].
    dV_tank_w_in_ctrl : float | None
        Refill flow rate [m³/s].  ``None`` = always-full
        sentinel (inflow resolved inside residual).
    result : dict
        Full result dictionary from the model's
        ``_calc_state``.  Contents are model-specific.
    """

    is_on: bool
    Q_heat_source: float
    dV_tank_w_in_ctrl: float | None
    result: dict = field(default_factory=dict)


# ------------------------------------------------------------------
# Carried state for a re-entrant single-timestep advance (step())
# ------------------------------------------------------------------


@dataclass
class DynamicState:
    """Carried state threaded across ``step()`` calls (#165 P0).

    This is exactly the state that ``analyze_dynamic`` used to hold in
    loop-locals (``T_tank_w_K``, ``tank_level``, ``is_refilling``,
    ``hp_is_on_prev``) plus the one cross-step coupling that previously lived
    as a ``self.dV_tank_w_out`` side-effect (``dV_tank_w_out_prev`` here).

    ``step()`` returns a fresh ``DynamicState`` each call and reads none of
    these from ``self``, so a model can be advanced one timestep at a time by
    an external co-simulation master (FMI/EnergyPlus) and re-run independently.

    Attributes
    ----------
    T_tank_w_K : float
        Tank water temperature carried into the next step [K].
    tank_level : float
        Fractional tank fill level (0–1).
    is_refilling : bool
        Refill hysteresis latch.
    hp_is_on_prev : bool
        Call for heat at the end of the previous step (cycling hysteresis).
        A step whose cycle could not be solved keeps the call, so the next
        step retries instead of waiting for the tank to reach its lower bound.
    dV_tank_w_out_prev : float
        Previous step's tank draw-off flow [m³/s]; consumed by the next
        step's refill decision.
    hp_running_prev : bool
        Whether the compressor was running at the end of the previous step.
        Differs from ``hp_is_on_prev`` when the call stands but the cycle
        could not be solved, or a minimum on/off time holds the compressor.
    hp_phase_age_s : float
        Time the compressor has been in its current running state (on or
        off) at the end of the previous step [s]; ``inf`` at the start of a
        run. Read by the minimum on/off time rules.
    """

    T_tank_w_K: float
    tank_level: float
    is_refilling: bool
    hp_is_on_prev: bool
    dV_tank_w_out_prev: float
    hp_running_prev: bool = False
    hp_phase_age_s: float = math.inf


# ------------------------------------------------------------------
# Subsystem exergy result (AND type, immutable)
# ------------------------------------------------------------------


@dataclass(frozen=True)
class SubsystemExergy:
    """Subsystem-specific exergy calculation results.

    Each subsystem's ``calc_exergy()`` returns this object
    so that the host boiler can merge subsystem columns into
    the result DataFrame and adjust system-level totals.

    Attributes
    ----------
    columns : dict[str, pd.Series]
        Exergy columns to append (key = column name).
    X_tot_add : pd.Series | float
        Additive contribution to system total exergy input
        ``X_tot [W]`` (e.g. pump electricity).
    X_in_tank_add : pd.Series | float
        Additive exergy entering the tank boundary
        (e.g. heated return water in ``tank_circuit``).
    X_out_tank_add : pd.Series | float
        Additive exergy leaving the tank boundary
        (e.g. water drawn to STC in ``tank_circuit``).
    """

    columns: dict  # dict[str, pd.Series]
    X_tot_add: object = 0.0  # pd.Series | float
    X_in_tank_add: object = 0.0  # pd.Series | float
    X_out_tank_add: object = 0.0  # pd.Series | float


# ------------------------------------------------------------------
# Subsystem Protocol
# ------------------------------------------------------------------


class Subsystem(Protocol):
    """Pluggable subsystem interface.

    Each subsystem computes its contribution for a single
    timestep and assembles result columns for the output
    DataFrame.  New subsystems (PV, battery, …) implement
    this protocol and register with the boiler model.
    """

    def step(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        dt: float,
        T_tank_w_in_K: float,
    ) -> dict:
        """Compute subsystem state for this timestep.

        Parameters
        ----------
        ctx : StepContext
            Current-step immutable context.
        ctrl : ControlState
            Heat-source control decisions.
        dt : float
            Time-step size [s].
        T_tank_w_in_K : float
            Mains water inlet temperature [K].

        Returns
        -------
        dict
            Must include at least the following keys:

            - ``'Q_contribution'`` (float) — net energy contribution to the
              tank [W].
            - ``'E_subsystem'`` (float) — electrical power consumed [W].
            - ``'T_tank_w_in_override_K'`` (float | None) — heated tank-inlet
              temperature [K] if the subsystem modifies the inlet (e.g. mains
              preheat); ``None`` if there is no modification.
        """
        ...

    def assemble_results(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        step_state: dict,
        T_solved_K: float,
    ) -> dict:
        """Build result columns for DataFrame output.

        Parameters
        ----------
        ctx : StepContext
            Current-step immutable context.
        ctrl : ControlState
            HP control decisions.
        step_state : dict
            Dict returned by ``step()``.
        T_solved_K : float
            Solved tank temperature [K].

        Returns
        -------
        dict
            Keyed result entries for the output DataFrame.
        """
        ...

    def calc_exergy(
        self,
        df: pd.DataFrame,
        T0_K: pd.Series,
    ) -> SubsystemExergy | None:
        """Compute subsystem-level exergy items."""
        ...

    def calc_performance(self, **kwargs) -> dict:
        """Calculate performance at a specific condition."""
        ...


# ------------------------------------------------------------------
# Pure helper functions
# ------------------------------------------------------------------


def check_hp_schedule_active(
    hour: float,
    hp_on_schedule: list[tuple[float, float]],
) -> bool:
    """Check whether current hour falls within HP operating schedule.

    Parameters
    ----------
    hour : float
        Current time of day [h] (0.0–24.0).
    hp_on_schedule : list of tuple
        List of ``(start_hour, end_hour)`` operating windows.

    Returns
    -------
    bool
    """
    return any(start_hour <= hour < end_hour for start_hour, end_hour in hp_on_schedule)


#: Temperature width [K] within which the tank counts as *at* a thermostat
#: bound. The tank temperature at a step boundary comes from an implicit
#: root solve, so a tank that ends a step on a bound lands a few 1e-9 K on
#: either side of it depending on the platform's floating-point path. An
#: exact comparison then turns the next step's call -- and with it
#: ``hp_is_on`` and ``hp_starts [-]`` -- on or off by machine. 1e-6 K is far
#: above that noise and far below anything a thermostat resolves.
TEMPERATURE_TOL_K: float = 1e-6

#: Time width [s] within which a switching event counts as at the step end
#: (it then happens at the start of the next step). Event times carry the
#: same root-solve noise (~1e-10 s for a 15 kW / 151 L tank per 1e-9 K), and
#: without this width a stop or start right at a step boundary would be
#: counted in one step or the next by platform.
EVENT_TIME_TOL_S: float = 1e-6


def bound_tolerance(T_lower: float, T_upper: float) -> float:
    """Width [K] for the bound comparisons of a ``[T_lower, T_upper]`` band.

    ``TEMPERATURE_TOL_K``, but at most a quarter of the band, so the "at the
    lower bound" and "at the upper bound" tests never overlap: with an
    overlap the lower test, checked first, would keep the call on at the
    upper bound and the compressor would run past it. A degenerate band
    (``T_lower >= T_upper``) gets no width (exact comparisons).
    """
    return min(TEMPERATURE_TOL_K, max(0.0, 0.25 * (T_upper - T_lower)))


def determine_heat_source_on_off(
    T_tank_w_C: float,
    T_lower: float,
    T_upper: float,
    is_on_prev: bool,
    hour_of_day: float,
    on_schedule: list[tuple[float, float]],
) -> bool:
    """Hysteresis-based heat-source on/off decision.

    Parameters
    ----------
    T_tank_w_C : float
        Current tank water temperature [°C].
    T_lower : float
        Lower hysteresis bound [°C].
    T_upper : float
        Upper hysteresis bound [°C].
    is_on_prev : bool
        Heat-source state at the previous timestep.
    hour_of_day : float
        Hour within the day (0–24).
    on_schedule : list[tuple[float, float]]
        Active operating windows ``(start_h, end_h)``.

    Returns
    -------
    bool
        Whether the heat source should run this timestep.
    """
    # Bounds compared with a width (bound_tolerance): a tank that ended the
    # previous step on a bound is at it, whatever the root solve's last digits.
    tol = bound_tolerance(T_lower, T_upper)
    if T_tank_w_C <= T_lower + tol:
        is_on: bool = True
    elif T_tank_w_C >= T_upper - tol:
        is_on = False
    else:
        is_on = is_on_prev

    return is_on and check_hp_schedule_active(
        hour_of_day,
        on_schedule,
    )


# ------------------------------------------------------------------
# Thermostat events inside one communication step
# ------------------------------------------------------------------

T_WATER_BOIL_C: float = 100.0
"""Boiling point of the stored water at atmospheric pressure [°C].

The heat pump never drives a tank past it: the in-step thermostat treats it as
an absolute cut-out on top of the upper set point. Constructors already refuse
set points above 100 °C (:mod:`tmhp._validation`); the cut-out matters when a
minimum on-time holds the compressor past the set point.
"""


@dataclass(frozen=True)
class InStepRun:
    """Compressor operation inside one step, resolved at thermostat events.

    Attributes
    ----------
    runtime_fraction : float
        Fraction of the step the compressor ran (0-1). Heat, power and
        flow rates of a running cycle are multiplied by it to give the
        step-average rates.
    running_start : bool
        Whether the compressor was running at the start of the step.
    running_end : bool
        Whether it was running at the end of the step.
    call_end : bool
        Thermostat call for heat at the end of the step.
    starts : int
        Off-to-on transitions of the compressor inside the step (a start at
        ``t = 0`` counts when it was off at the end of the previous step).
    phase_age_end_s : float
        Time the compressor has been in its end-of-step running state [s].
    T_on_mean_C : float | None
        Run-time-weighted mean tank temperature while the compressor ran
        [°C]; ``None`` if it did not run.
    T_on_first_C : float | None
        Tank temperature when the compressor first ran in the step (the
        step start if it ran from there) [°C]; ``None`` if it did not run.
    heat_unavailable : bool
        The compressor was to run but had no heat to give (no cycle could
        be solved), so it did not; the call stands for the next step.
    """

    runtime_fraction: float
    running_start: bool
    running_end: bool
    call_end: bool
    starts: int
    phase_age_end_s: float
    T_on_mean_C: float | None
    heat_unavailable: bool
    T_on_first_C: float | None = None


def tank_free_heat_rate(
    residual,
    T_K: float,
    *,
    T_start_K: float,
    C_curr: float,
    C_next: float,
    C_tank: float,
    dt: float,
    Q_heat_source: float,
) -> float:
    """Tank heat rate at ``T_K`` from everything but the heat source [W].

    Inverts the implicit energy residual every boiler uses,
    ``r(T) = [C_next·T - C_curr·T_start - dt·(Q_heat_source + P(T))] / C_tank``,
    for ``P(T)``: draw-off enthalpy, envelope loss and subsystem heat (a
    subsystem residual that re-evaluates its physics at ``T`` is included).
    With ``C_next = C_curr`` it returns the temperature-form rate
    ``P(T) - (C_next_true - C_curr)·T/dt`` that drives ``C_curr·dT/dt``
    (see :func:`resolve_step_operation`).
    """
    r = float(residual(T_K))
    return (C_next * T_K - C_curr * T_start_K - C_tank * r) / dt - Q_heat_source


def _time_to_reach(T0: float, X: float, rate_const: float, slope: float, C: float) -> float:
    """Time for ``C·dT/dt = rate_const + slope·T`` to carry ``T0`` to ``X`` (``inf`` if never)."""
    if X == T0:
        return 0.0
    g0 = rate_const + slope * T0  # C·dT/dt at T0
    if g0 == 0.0 or (X - T0) * g0 <= 0.0:
        return math.inf  # at rest, or moving away from X
    if slope == 0.0:
        return C * (X - T0) / g0
    # T(t) = T0 + (g0/slope)·expm1(slope·t/C)  =>  t = C/slope · log1p(slope·(X-T0)/g0)
    arg = slope * (X - T0) / g0
    if arg <= -1.0:
        return math.inf  # X lies beyond the equilibrium temperature
    t = C / slope * math.log1p(arg)
    return t if t >= 0.0 else math.inf


def _advance(T0: float, t: float, rate_const: float, slope: float, C: float) -> float:
    """``T(t)`` for ``C·dT/dt = rate_const + slope·T`` from ``T0``."""
    g0 = rate_const + slope * T0
    if slope == 0.0:
        return T0 + t * g0 / C
    # A positive slope (heat gain growing with temperature) diverges; cap the
    # exponent so a degenerate tank (near-zero capacity) cannot overflow.
    return T0 + g0 / slope * math.expm1(min(slope * t / C, 700.0))


def simulate_thermostat_step(
    *,
    T_start_C: float,
    call_start: bool,
    running_prev: bool,
    phase_age_s: float,
    scheduled: bool,
    T_lower: float,
    T_upper: float,
    dt: float,
    C: float,
    free_heat: tuple[tuple[float, float], tuple[float, float]],
    Q_on: float,
    min_on_time_s: float = 0.0,
    min_off_time_s: float = 0.0,
    T_limit_C: float = T_WATER_BOIL_C,
) -> InStepRun:
    """Resolve the on/off thermostat inside one step at its switching events.

    The tank is one fully mixed node, ``C·dT/dt = s(t)·Q_on + P(T)``, where
    ``s`` is the compressor state and ``P`` the heat rate from everything else
    (draw-off, envelope loss, subsystems), taken as the straight line through
    the two ``(T [°C], P [W])`` points in ``free_heat``. Between events the
    temperature follows the exact solution of that linear equation; events
    are the tank reaching the lower bound (call on), the upper bound or
    ``T_limit_C`` (call off), and a minimum on/off time running out. The
    compressor delivers the constant condenser heat ``Q_on`` [W] while it
    runs; ``Q_on <= 0`` means no cycle exists, and a compressor asked to run
    then stays off (``heat_unavailable``).

    Parameters
    ----------
    T_start_C : float
        Tank temperature at the start of the step [°C].
    call_start : bool
        Thermostat call at the start of the step (hysteresis and schedule
        applied, as :func:`determine_heat_source_on_off` gives it).
    running_prev, phase_age_s : bool, float
        Compressor state at the end of the previous step and how long it had
        been in it [s].
    scheduled : bool
        Whether the operating schedule allows the heat pump in this step.
    T_lower, T_upper : float
        Hysteresis bounds [°C].
    dt : float
        Step length [s].
    C : float
        Tank heat capacity [J/K].
    min_on_time_s, min_off_time_s : float
        Minimum compressor run and rest times [s]. A minimum run holds the
        compressor past the upper bound (up to ``T_limit_C``); a minimum rest
        delays a start the call asks for.
    T_limit_C : float
        Absolute cut-out [°C]; the effective upper bound is
        ``min(T_upper, T_limit_C)``. The constructors refuse bounds above
        100 °C, so the cut-out acts when a minimum run time holds the
        compressor past the upper bound.

    More than 1000 switching events in one step (a band too narrow for the
    step) are closed by the fast-cycling limit: the tank holds the
    temperature it reached and the compressor runs the fraction of the rest
    of the step that balances the tank there.
    """
    T_hi = min(T_upper, T_limit_C)
    # Bound width for this band (never more than a quarter of it).
    tol = bound_tolerance(T_lower, T_hi)
    (T_a, P_a), (T_b, P_b) = free_heat
    slope = (P_b - P_a) / (T_b - T_a) if T_b != T_a else 0.0
    p_const = P_a - slope * T_a
    q_on = float(Q_on) if math.isfinite(Q_on) and Q_on > 0.0 else 0.0

    t = 0.0
    tw = T_start_C
    running = bool(running_prev)
    age = float(phase_age_s)
    call = bool(call_start)
    on_time = 0.0
    on_T_integral = 0.0
    starts = 0
    running_start = running
    unavailable = False
    tw_on_first: float | None = None

    for n_events in range(1_000_001):
        # --- instantaneous transitions at (t, tw) ---
        # Every comparison with a bound or a minimum time carries
        # TEMPERATURE_TOL_K / EVENT_TIME_TOL_S (see their definitions): the
        # booleans and counts this loop produces must not depend on the last
        # digits of a root solve.
        # At t = 0 this repeats the caller's hysteresis on the start
        # temperature, so a call that disagrees with the bounds cannot run
        # the compressor past them.
        if scheduled:
            if tw <= T_lower + tol:
                call = True
            elif tw >= T_hi - tol:
                call = False
        if not scheduled:
            call = False
        if t >= dt:
            break  # a start at the step end belongs to the next step
        want = call and scheduled
        if running:
            if (
                not scheduled
                or tw >= T_limit_C - TEMPERATURE_TOL_K
                or (not want and age >= min_on_time_s - EVENT_TIME_TOL_S)
            ):
                running, age = False, 0.0
            elif q_on <= 0.0:
                # Running at the end of the previous step but no cycle exists
                # at this step's conditions: the compressor stops.
                running, age, unavailable = False, 0.0, True
        elif (
            want and age >= min_off_time_s - EVENT_TIME_TOL_S and not unavailable and tw < T_limit_C - TEMPERATURE_TOL_K
        ):
            if q_on > 0.0:
                running, age = True, 0.0
                starts += 1
            else:
                unavailable = True
        if t == 0.0:
            running_start = running

        # --- next event ---
        rem = dt - t
        if n_events >= 1000 and q_on > 0.0 and scheduled:
            # Fast-cycling limit: hold the tank where it is.
            duty = min(1.0, max(0.0, -(p_const + slope * tw) / q_on))
            on_time += duty * rem
            on_T_integral += duty * rem * tw
            age = 0.0
            t = dt
            continue
        drive = p_const + (q_on if running else 0.0)
        # (time to the event, threshold it reaches or None)
        events: list[tuple[float, float | None]] = [(rem, None)]
        if running:
            if tw < T_limit_C - TEMPERATURE_TOL_K:
                events.append((_time_to_reach(tw, T_limit_C, drive, slope, C), T_limit_C))
            if age < min_on_time_s - EVENT_TIME_TOL_S:
                events.append((min_on_time_s - age, None))
            if want and tw < T_hi - tol:
                events.append((_time_to_reach(tw, T_hi, drive, slope, C), T_hi))
        else:
            if want and not unavailable and age < min_off_time_s - EVENT_TIME_TOL_S:
                events.append((min_off_time_s - age, None))
            if scheduled and not call and tw > T_lower + tol:
                events.append((_time_to_reach(tw, T_lower, drive, slope, C), T_lower))
            if scheduled and call and tw < T_hi - tol:
                events.append((_time_to_reach(tw, T_hi, drive, slope, C), T_hi))
        step, target = min((e for e in events if e[0] >= 0.0), key=lambda e: e[0])
        if step >= rem - EVENT_TIME_TOL_S:
            # An event at the step end belongs to the next step (EVENT_TIME_TOL_S).
            step, target = rem, None
        T_new = _advance(tw, step, drive, slope, C)
        if target is not None and step < rem:
            # Land exactly on the threshold this event reaches so the
            # comparisons above fire once.
            T_new = target
        if running:
            if tw_on_first is None:
                tw_on_first = tw
            on_time += step
            on_T_integral += 0.5 * (tw + T_new) * step
        tw = T_new
        t = t + step if step < rem else dt
        age += step
    else:  # pragma: no cover - defensive: event loop did not terminate
        raise RuntimeError("in-step thermostat did not settle")

    return InStepRun(
        runtime_fraction=min(1.0, max(0.0, on_time / dt)) if dt > 0 else 0.0,
        running_start=running_start,
        running_end=running,
        call_end=call,
        starts=starts,
        phase_age_end_s=age,
        T_on_mean_C=on_T_integral / on_time if on_time > 0.0 else None,
        heat_unavailable=unavailable,
        T_on_first_C=tw_on_first,
    )


CYCLE_RETRY_SPAN_K: float = 1.0
"""A step whose cycle has no operating point at the run's mean tank
temperature retries at the run's start temperature when that is more than
this much colder [K] (see :func:`resolve_step_operation`)."""


def _solve_tank_end(residual_free, Q: float, dt: float, C_tank: float) -> float | None:
    """End-of-step tank temperature [K] for a constant heat-source rate ``Q``."""
    from scipy.optimize import brentq

    def f(T_K: float) -> float:
        return float(residual_free(T_K)) - dt * Q / C_tank

    lo, hi = 273.15, 473.15
    try:
        if f(lo) * f(hi) > 0.0:
            return None
        return float(brentq(f, lo, hi, xtol=1e-10))
    except (ValueError, RuntimeError):
        return None


def resolve_step_operation(
    *,
    T_start_K: float,
    call_start: bool,
    running_prev: bool,
    phase_age_s: float,
    scheduled: bool,
    T_lower: float,
    T_upper: float,
    dt: float,
    C_curr: float,
    C_next: float,
    C_tank: float,
    residual_free,
    solve_cycle,
    Q_request: float,
    min_on_time_s: float = 0.0,
    min_off_time_s: float = 0.0,
) -> tuple[InStepRun, dict | None]:
    """Compressor operation in one step and the cycle it ran.

    ``residual_free(T_K)`` is the boiler's own implicit tank residual built
    with a zero heat-source rate; it supplies the heat the tank gains or
    loses from everything else (:func:`tank_free_heat_rate`), linearised
    between ``min(T_start, T_lower)`` and ``max(T_start, T_upper)``. The
    thermostat integrates ``C_curr·dT/dt`` (the start-of-step capacity); for
    a tank whose level changes in the step the free heat is taken in that
    temperature form, so the make-up enters as ``ρc·V̇_in·(T_in - T)`` and
    the draw, which leaves at the tank temperature, does not change ``T``.
    ``C_next`` is accepted for compatibility and not used.
    ``solve_cycle(T_C)`` returns the boiler's cycle result at tank
    temperature ``T_C`` (a running result, or an off-mode row with a
    ``failure_reason``); ``Q_request`` [W] is the heat it is asked for.

    Two passes, one cycle solve:

    1. the thermostat events with the compressor delivering ``Q_request``,
       which gives when it runs and the mean tank temperature while it does;
    2. the cycle solved once at that mean temperature (midpoint rule: the
       quasi-steady cycle the step's run averages to; if it has no operating
       point there, at the temperature the run starts from), and the events
       again with the heat that cycle delivers (zero if it has no operating
       point).

    ``solve_cycle(T_C, runtime_fraction)`` returns the boiler's cycle result
    at tank temperature ``T_C``; ``runtime_fraction`` is the run time the
    first pass expects, for boilers whose source responds to the
    step-average load. Each step searches its operating point from scratch:
    starting from the previous step's point would change which candidates
    the search evaluates, and so its optimum.

    Returns the :class:`InStepRun` and the cycle result (``None`` if the
    compressor never had to run in this step).

    When the linearised model puts a switching event within round-off of the
    step end, the implicit solve of the full step decides: a step the
    compressor runs through without its tank reaching the upper bound, or
    rests through without reaching the lower bound, keeps its single-rate
    result.
    """
    T0_C = T_start_K - 273.15
    T_a = min(T0_C, T_lower)
    T_b = max(T0_C, T_upper)
    if T_b - T_a < 1.0:
        T_b = T_a + 1.0
    # Temperature form of the balance at the start capacity: inverting the
    # residual with C_next = C_curr leaves P(T) - (C_next - C_curr)·T/dt, i.e.
    # the make-up's heat V̇_in·ρc·(T_in - T) instead of the absolute enthalpy
    # of the water that changes the level. The conservative step
    # C_next·T1 - C_curr·T0 = dt·(Q + P(T1)) is C_curr·(T1 - T0) = dt·(Q + P'(T1)),
    # the thermostat's equation. The two are the same for an always-full tank
    # (C_next == C_curr); ``C_next`` is accepted for the callers and unused.
    free = tuple(
        (
            T,
            tank_free_heat_rate(
                residual_free,
                T + 273.15,
                T_start_K=T_start_K,
                C_curr=C_curr,
                C_next=C_curr,
                C_tank=C_tank,
                dt=dt,
                Q_heat_source=0.0,
            ),
        )
        for T in (T_a, T_b)
    )
    kwargs = dict(
        T_start_C=T0_C,
        call_start=call_start,
        running_prev=running_prev,
        phase_age_s=phase_age_s,
        scheduled=scheduled,
        T_lower=T_lower,
        T_upper=T_upper,
        dt=dt,
        C=C_curr,
        free_heat=free,
        min_on_time_s=min_on_time_s,
        min_off_time_s=min_off_time_s,
    )
    provisional = simulate_thermostat_step(Q_on=Q_request, **kwargs)  # type: ignore[arg-type]
    if provisional.T_on_mean_C is None:
        return provisional, None
    T_eval = provisional.T_on_mean_C
    cycle_result = solve_cycle(T_eval, provisional.runtime_fraction)
    q = delivered_tank_heat(cycle_result)
    T_first = provisional.T_on_first_C
    if q <= 0.0 and T_first is not None and T_eval - T_first > CYCLE_RETRY_SPAN_K:
        # No operating point at the mean temperature (an envelope that closes
        # as the tank warms): the cycle that starts the run, when the run
        # starts well below the mean (a nearby start would fail the same way).
        retry = solve_cycle(T_first, provisional.runtime_fraction)
        if delivered_tank_heat(retry) > 0.0:
            cycle_result, q = retry, delivered_tank_heat(retry)
    run = simulate_thermostat_step(Q_on=q, **kwargs)  # type: ignore[arg-type]

    T_hi = min(T_upper, T_WATER_BOIL_C)
    age_through = (phase_age_s if running_prev == run.running_start else 0.0) + dt
    if run.running_start and not run.running_end and run.starts == (0 if running_prev else 1) and min_on_time_s == 0:
        # Ran from t = 0 and stopped once at the upper bound: confirm with the
        # implicit full-step solve that the tank really gets there.
        T_end = _solve_tank_end(residual_free, q, dt, C_tank)
        if T_end is not None and T_end - 273.15 < T_hi - bound_tolerance(T_lower, T_hi):
            run = InStepRun(1.0, True, True, True, run.starts, age_through, run.T_on_mean_C, False, T0_C)
    elif (
        not run.running_start
        and run.running_end
        and run.starts == 1
        and not call_start
        and run.runtime_fraction < 1e-3
        and min_off_time_s == 0
    ):
        # Started a moment before the step end at the lower bound: confirm
        # the tank really cools that far within the step.
        T_end = _solve_tank_end(residual_free, 0.0, dt, C_tank)
        if T_end is not None and T_end - 273.15 > T_lower + bound_tolerance(T_lower, T_hi):
            return InStepRun(0.0, False, False, False, 0, age_through, None, False), None
    return run, cycle_result


TANK_BOILING_WARNING = (
    "tank water above 100 °C: the heat pump stops at its upper set point (at the latest at 100 °C), "
    "so this heat comes from another source (e.g. a solar collector loop without a high limit, #96 T9). "
    "The lumped tank model has no boiling or relief valve; temperatures above 100 °C are not physical."
)


def warn_if_tank_boiling(T_tank_K: float) -> None:
    """Emit :data:`TANK_BOILING_WARNING` when the solved tank is above 100 °C."""
    if T_tank_K - 273.15 > T_WATER_BOIL_C + 1e-6:
        import warnings

        warnings.warn(TANK_BOILING_WARNING, RuntimeWarning, stacklevel=3)


def step_hp_result(run: InStepRun, cycle_result: dict | None, off_row, keys: tuple[str, ...]) -> dict:
    """The heat-pump part of a step's result row.

    A compressor that ran reports its cycle with the rates in ``keys``
    averaged over the step (:func:`scale_running_rates`); one that was to run
    but had no cycle reports the cycle's off-mode failure row; otherwise
    ``off_row()`` gives the off-mode row. Every row carries
    ``runtime_fraction [-]`` and ``hp_starts [-]``.
    """
    if cycle_result is not None and run.runtime_fraction > 0.0 and cycle_result.get("hp_is_on", False):
        result = scale_running_rates(dict(cycle_result), run.runtime_fraction, keys)
    else:
        result = dict(cycle_result) if cycle_result is not None and run.heat_unavailable else off_row()
        result["runtime_fraction [-]"] = 0.0
    result["hp_starts [-]"] = int(run.starts) if result.get("hp_is_on", False) else 0
    return result


def scale_running_rates(result: dict, fraction: float, keys: tuple[str, ...]) -> dict:
    """Turn a running cycle's rates into step averages (in place).

    ``keys`` are the heat, power and flow rates of the heat-pump circuit;
    each finite numeric value is multiplied by ``fraction``. State
    quantities (temperatures, pressures, COP, efficiencies, speed) keep the
    running values. Adds ``runtime_fraction [-]``.
    """
    if fraction != 1.0:
        for key in keys:
            value = result.get(key)
            if _finite_number(value):
                result[key] = float(value) * fraction  # type: ignore[arg-type]
    result["runtime_fraction [-]"] = float(fraction)
    return result


@dataclass(frozen=True)
class CycleSolveOutcome:
    """Outcome of a boiler's evaporator-approach search for one request.

    ``success`` is ``True`` only when the returned approach ``x`` gives a
    cycle that closes both heat exchangers and passes
    :func:`unphysical_cycle_detail`. Otherwise ``x`` is NaN and
    ``failure_reason`` says why (``cycle_invalid``, ``pr_above_max``,
    ``hx_not_converged`` or ``optimizer_failed``); ``detail`` refines
    ``cycle_invalid`` when the cycle existed but was not an operating point.
    """

    success: bool
    x: float
    failure_reason: str
    detail: str | None = None


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def unphysical_cycle_detail(result: Mapping[str, Any], hp_capacity: float) -> str | None:
    """Explain why a cycle state the solver accepted is not an operating point.

    The boiler solvers accept a cycle once its compressor speed meets the
    requested duty (or clamps onto a speed bound). That acceptance says
    nothing about whether the state itself exists. This check rejects the
    states that cannot be a running machine; ``None`` means the state passes.

    ``"duty_or_power_undefined"``
        Delivered heat or compressor power is non-finite or not positive.
        A running compressor does work on the refrigerant and the condenser
        rejects at least that work, so both must be positive numbers.
    ``"discharge_state_undefined"``
        The compressor discharge temperature or enthalpy is non-finite. The
        delivered heat is ``m_dot * (h_discharge - h_liquid)``; without a
        discharge state that heat has no thermodynamic basis. A low isentropic
        efficiency at a high pressure ratio produces it: ``h_isen - h_suction``
        is divided by the efficiency and the discharge enthalpy leaves the
        refrigerant's equation-of-state range (with the guarded default fit,
        R32 at PR 7-11 and eta_is 0.14-0.25; with an unguarded fit, eta_is
        near zero).
    ``"min_speed_duty_above_capacity"``
        The compressor clamped onto its speed floor (the request was below
        the duty available there) and yet delivers more than the nameplate
        ``hp_capacity``. Mass flow is proportional to speed, so at
        ``rps_min`` (below the speed at which ``hp_capacity`` is rated) the
        compressor pumps the least refrigerant it can; delivering more heat
        than at the rated speed then needs a specific condensing duty several
        times the rated one. That is the signature of the same efficiency
        collapse, not of a real machine. The condenser cannot carry it
        either: its approach temperature was sized for the requested duty,
        so the surplus has no driving temperature difference across the tank
        heat exchanger. ``hp_capacity`` is the bound because it is the only
        capacity the model knows the machine to have; no tolerance is added
        beyond floating-point noise.
    """
    q = result.get("Q_ref_tank [W]")
    e = result.get("E_cmp [W]")
    if not (_finite_number(q) and _finite_number(e)) or float(q) <= 0.0 or float(e) <= 0.0:  # type: ignore[arg-type]
        return "duty_or_power_undefined"
    for key in ("T_ref_cmp_out [°C]", "h_ref_cmp_out [J/kg]"):
        if key in result and not _finite_number(result[key]):
            return "discharge_state_undefined"
    if result.get("capacity_clamped") == "min" and float(q) > hp_capacity * (1.0 + 1e-9):  # type: ignore[arg-type]
        return "min_speed_duty_above_capacity"
    return None


def delivered_tank_heat(result: Mapping[str, Any]) -> float:
    """Condenser heat a step puts into the tank [W].

    The heat the solved cycle rejects (``Q_ref_tank [W]``), and zero unless
    the compressor actually ran. Never the requested capacity: a request the
    cycle could not meet delivers nothing.
    """
    if not result.get("hp_is_on", False):
        return 0.0
    q = result.get("Q_ref_tank [W]", 0.0)
    return float(q) if _finite_number(q) else 0.0  # type: ignore[arg-type]


def determine_tank_refill_flow(
    dt: float,
    tank_level: float,
    dV_tank_w_out: float,
    V_tank_full: float,
    tank_always_full: bool,
    prevent_simultaneous_flow: bool,
    tank_level_lower_bound: float,
    tank_level_upper_bound: float,
    dV_tank_w_in_refill: float,
    is_refilling: bool,
) -> tuple[float | None, bool]:
    """Determine refill flow rate from current level and operational mode.

    Pure tank-level management: all subsystem-specific flow
    overrides (e.g. STC mains-preheat forced refill) are the
    responsibility of the scenario class via
    ``_run_subsystems`` / ``ctrl.dV_tank_w_in_ctrl``.

    Parameters
    ----------
    dt : float
        Time-step size [s].
    tank_level : float
        Current fractional tank level (0–1).
    dV_tank_w_out : float
        Current outflow rate [m³/s].
    V_tank_full : float
        Tank full volume [m³].
    tank_always_full : bool
        Whether the tank is forced to stay full.
    prevent_simultaneous_flow : bool
        Exclusive-flow mode flag.
    tank_level_lower_bound : float
        Level lower bound for refill trigger.
    tank_level_upper_bound : float
        Level upper bound for refill cut-off.
    dV_tank_w_in_refill : float
        Refill flow rate [m³/s].
    is_refilling : bool
        Whether we are currently in a refill cycle.

    Returns
    -------
    tuple[float | None, bool]
        ``(dV_tank_w_in, is_refilling)``.
        ``None`` means always-full sentinel (no PSF).

    Notes
    -----
    ``dV_tank_w_out`` is the draw expected over the step. The returned
    make-up is a step-average rate:

    * ``tank_always_full=False``: the refill valve is followed through the
      step (:func:`_refill_within_step`). It opens when the falling level
      reaches ``tank_level_lower_bound`` and closes when it is back at
      ``tank_level_upper_bound``, at any point of the step, and admits
      ``dV_tank_w_in_refill`` while open.
    * ``prevent_simultaneous_flow``: no make-up in a step that draws;
      otherwise ``dV_tank_w_in_refill`` until the tank is full.

    Either way a step whose full-rate refill would overshoot the target takes
    the part that reaches it. It used to take none: with
    ``dV_tank_w_in_refill·dt`` above the tank volume (the default 1 L/s at
    any step above 151 s) the tank never refilled. The tank balance then
    limits both flows to what the tank can pass (:func:`limit_tank_flows`).
    """
    lv: float = tank_level
    if not tank_always_full or (tank_always_full and prevent_simultaneous_flow):
        lv = max(
            0.0,
            tank_level - (dV_tank_w_out * dt) / V_tank_full,
        )

    dV_tank_w_in: float = 0.0

    if tank_always_full and prevent_simultaneous_flow:
        if dV_tank_w_out > 0:
            is_refilling = False
        elif lv < 1.0:
            dV_tank_w_in = min(dV_tank_w_in_refill, (1.0 - lv) * V_tank_full / dt)
    elif tank_always_full:
        return None, is_refilling  # sentinel
    else:
        return _refill_within_step(
            dt,
            tank_level,
            dV_tank_w_out,
            V_tank_full,
            tank_level_lower_bound,
            tank_level_upper_bound,
            dV_tank_w_in_refill,
            is_refilling,
        )

    return dV_tank_w_in, is_refilling


LEVEL_TOL: float = 1e-6
"""Width of the refill bounds [-]: a level within it of a bound is on it."""


def _refill_within_step(
    dt: float,
    level: float,
    dV_out: float,
    V_tank_full: float,
    lo: float,
    hi: float,
    dV_refill: float,
    refilling: bool,
) -> tuple[float, bool]:
    """Refill valve of a level-varying tank followed through one step.

    The level falls at ``dV_out/V`` (the step's expected draw); the valve
    opens when it reaches ``lo`` and closes at ``hi``, at any point of the
    step, and admits ``dV_refill`` while open. Returns the step-average
    make-up rate and the valve state at the step end. Following the events
    makes the refilled volume, and so the level at each step end, the same
    for any step length (a valve that opened on the level expected at the
    step end filled earlier and more at long steps).
    """
    t = 0.0
    vol_in = 0.0
    for _ in range(1000):
        rem = dt - t
        if rem <= 0.0:
            break
        if refilling:
            net = dV_refill - dV_out
            t_close = (hi - level) * V_tank_full / net if net > 0.0 else math.inf
            if t_close >= rem:
                vol_in += dV_refill * rem
                level += net * rem / V_tank_full
                break
            vol_in += dV_refill * t_close
            t += t_close
            level, refilling = hi, False
        else:
            if level < lo - LEVEL_TOL:
                refilling = True
                continue
            t_open = max(0.0, level - lo) * V_tank_full / dV_out if dV_out > 0.0 else math.inf
            if t_open >= rem:
                break
            t += t_open
            level, refilling = lo, True
    else:
        # Valve cycling faster than the step resolves (a band far narrower than
        # one step of draw): it passes the draw, holding the level.
        vol_in += dV_out * (dt - t)
    if refilling and level >= hi - LEVEL_TOL:
        refilling = False
    return vol_in / dt, refilling


TANK_LEVEL_MIN: float = 0.001
"""Lowest fill level of a tank whose level varies [-].

A level-varying tank (``tank_always_full=False``, or
``prevent_simultaneous_flow``) keeps this much water: a draw that would take
it lower is cut to the water above it plus the step's make-up, and the cut
is reported as unmet hot water (``dV_mix_w_out_unmet [m3/s]``). The value is
the floor the tank heat capacity used to be clamped to; the clamp now has the
water behind it, so it no longer creates energy.
"""


def limit_tank_flows(
    tank_level: float,
    dV_tank_w_in: float,
    dV_tank_w_out: float,
    dt: float,
    V_tank_full: float,
    level_min: float = TANK_LEVEL_MIN,
    level_max: float = 1.0,
) -> tuple[float, float, float]:
    """Make-up and draw a tank can pass in one step, and its end level.

    The draw takes no more than the water above ``level_min`` plus the
    make-up over the step; the make-up adds no more than the room below
    ``level_max`` plus the draw. The end level is
    ``tank_level + (dV_in - dV_out)·dt / V_tank_full`` and stays within
    ``[level_min, level_max]`` (a tank already below ``level_min`` is not
    drawn from until the make-up lifts it).

    Returns
    -------
    tuple[float, float, float]
        ``(dV_tank_w_in, dV_tank_w_out, level_next)`` [m³/s, m³/s, -].
    """
    room_out = (tank_level - level_min) * V_tank_full / dt
    dV_out = min(dV_tank_w_out, max(0.0, room_out + dV_tank_w_in))
    room_in = (level_max - tank_level) * V_tank_full / dt
    dV_in = min(dV_tank_w_in, max(0.0, room_in + dV_out))
    level_next = tank_level + (dV_in - dV_out) * dt / V_tank_full
    # Round-off only: the limits above already keep the level in range.
    level_next = min(level_max, max(min(level_min, tank_level), level_next))
    return dV_in, dV_out, level_next


@dataclass(frozen=True)
class TankStepFlows:
    """Tank-side flows of one step at the end-of-step tank temperature.

    Attributes
    ----------
    alp : float
        Mixing-valve hot share at the end-of-step tank temperature [-].
    dV_tank_w_out : float
        Hot water leaving the tank for the mixing valve [m³/s].
    dV_tank_w_in : float
        Make-up water entering the tank [m³/s].
    level_next : float
        End-of-step fill level [-].
    dV_mix_w_out : float
        Mixed water delivered at the set point [m³/s]: the demand, less what
        the tank could not supply.
    dV_mix_w_out_unmet : float
        Mixed-water demand the tank could not supply [m³/s] (a level-varying
        tank at :data:`TANK_LEVEL_MIN`).
    """

    alp: float
    dV_tank_w_out: float
    dV_tank_w_in: float
    level_next: float
    dV_mix_w_out: float
    dV_mix_w_out_unmet: float


def tank_step_flows(
    T_next_K: float,
    ctx: StepContext,
    ctrl: ControlState,
    dt: float,
    T_sup_w_K: float,
    T_mix_w_out_K: float,
    V_tank_full: float,
) -> TankStepFlows:
    """Tank flows and end level of a step whose tank ends at ``T_next_K``.

    The mixing valve takes the hot share ``alp`` of the demand
    ``ctx.dV_mix_w_out`` from the tank at the end-of-step temperature
    (implicit, as :func:`tank_mass_energy_residual`). An always-full tank
    (``ctrl.dV_tank_w_in_ctrl is None``) takes in what it gives and keeps its
    level. A level-varying tank takes in ``ctrl.dV_tank_w_in_ctrl`` and both
    flows pass :func:`limit_tank_flows`; a draw it cuts delivers
    proportionally less mixed water, at the same set point.
    """
    den: float = max(1e-6, T_next_K - T_sup_w_K)
    alp: float = min(1.0, max(0.0, (T_mix_w_out_K - T_sup_w_K) / den))
    dV_out_request: float = alp * ctx.dV_mix_w_out
    if ctrl.dV_tank_w_in_ctrl is None:
        return TankStepFlows(alp, dV_out_request, dV_out_request, ctx.tank_level, ctx.dV_mix_w_out, 0.0)
    dV_in, dV_out, level_next = limit_tank_flows(
        ctx.tank_level, float(ctrl.dV_tank_w_in_ctrl), dV_out_request, dt, V_tank_full
    )
    delivered = ctx.dV_mix_w_out
    if dV_out < dV_out_request:
        delivered = ctx.dV_mix_w_out * dV_out / dV_out_request
    return TankStepFlows(alp, dV_out, dV_in, level_next, delivered, ctx.dV_mix_w_out - delivered)


def tank_mass_energy_residual(
    x: list[float],
    ctx: StepContext,
    ctrl: ControlState,
    dt: float,
    T_tank_w_in_K: float,
    T_sup_w_K: float,
    T_mix_w_out_K: float,
    C_tank: float,
    UA_tank_wall: float,
    V_tank_full: float,
    subsystems: dict[str, Subsystem],
    sub_states: dict[str, dict],
    T_sur_K: float | None = None,
) -> list[float]:
    """Energy and mass balance residuals at T^{n+1}.

    The 3-way mixing valve ratio α(T) makes the outflow a
    nonlinear function of T^{n+1}, requiring ``fsolve``.

    The energy residual is the conservative implicit balance
    ``C_tank·(L1·T1 - L0·T0) = dt·(Q + ρc·(V̇_in·T_in - V̇_out·T1) - UA·(T1 - T_sur))``
    with ``L1 = L0 + (V̇_in - V̇_out)·dt/V``. An always-full tank keeps
    ``L1 = L0``. A level-varying tank (``ctrl.dV_tank_w_in_ctrl`` set) takes
    ``L1`` and both flows from :func:`tank_step_flows` at ``T1``, so the
    water the balance adds or removes is the water that flows; the draw
    stops at :data:`TANK_LEVEL_MIN` and the make-up at full. The envelope
    loss ``UA·(T1 - T_sur)`` does not depend on the level.

    Subsystem energy contributions and tank-inlet temperature
    overrides are read from ``sub_states``.

    Parameters
    ----------
    x : list[float]
        ``[T_next_K, level_next]``. For a level-varying tank ``level_next``
        enters only the mass residual (the energy residual uses the level its
        flows give).
    ctx : StepContext
        Current-step immutable context.
    ctrl : ControlState
        Current-step HP control decisions.
    dt : float
        Time-step size [s].
    T_tank_w_in_K : float
        Mains water inlet temperature [K].
    T_sup_w_K : float
        Mains water supply temperature [K] (for mixing valve).
    T_mix_w_out_K : float
        Target mixing-valve outlet temperature [K].
    C_tank : float
        Tank thermal capacitance [J/K].
    UA_tank_wall : float
        Tank overall heat-loss coefficient [W/K] (shell/insulation envelope).
    V_tank_full : float
        Tank full volume [m³].
    subsystems : dict[str, Subsystem]
        Registered subsystem instances.
    sub_states : dict[str, dict]
        Per-subsystem state dicts from ``step()``.
    T_sur_K : float | None
        Surrounding temperature [K]. If None, defaults to ctx.T0_K.

    Returns
    -------
    list[float]
        ``[r_energy, r_mass]``.
    """
    T_next: float = x[0]
    level_next: float = x[1]

    if ctrl.dV_tank_w_in_ctrl is None:
        # Always full: the make-up equals the draw and the level holds.
        den: float = max(1e-6, T_next - T_sup_w_K)
        alp: float = min(
            1.0,
            max(0.0, (T_mix_w_out_K - T_sup_w_K) / den),
        )
        dV_tank_w_out: float = alp * ctx.dV_mix_w_out
        dV_tank_w_in: float = dV_tank_w_out

        r_mass: float = level_next - ctx.tank_level - (dV_tank_w_in - dV_tank_w_out) * dt / V_tank_full

        C_curr: float = C_tank * max(TANK_LEVEL_MIN, ctx.tank_level)
        C_next: float = C_tank * max(TANK_LEVEL_MIN, level_next)
    else:
        # Level varies: the mass the energy balance carries comes from the
        # same flows it uses (the draw at T^{n+1}, limited to what the tank
        # holds), so the end level is computed here and ``x[1]`` only enters
        # the mass residual. ``max`` guards a start below TANK_LEVEL_MIN only.
        flows = tank_step_flows(T_next, ctx, ctrl, dt, T_sup_w_K, T_mix_w_out_K, V_tank_full)
        dV_tank_w_out = flows.dV_tank_w_out
        dV_tank_w_in = flows.dV_tank_w_in
        r_mass = level_next - flows.level_next
        C_curr = C_tank * max(TANK_LEVEL_MIN, ctx.tank_level)
        C_next = C_tank * max(TANK_LEVEL_MIN, flows.level_next)
    T_sur_K = T_sur_K if T_sur_K is not None else ctx.T0_K
    Q_loss: float = UA_tank_wall * (T_next - T_sur_K)

    # Effective tank inlet temperature
    # (subsystems may override, e.g. mains preheat)
    T_in_eff: float = T_tank_w_in_K
    for s in sub_states.values():
        override: float | None = s.get(
            "T_tank_w_in_override_K",
        )
        if override is not None:
            T_in_eff = override
            break

    Q_flow_net: float = c_w * rho_w * (dV_tank_w_in * T_in_eff - dV_tank_w_out * T_next)

    # Subsystem energy contributions
    # (e.g. STC tank-circuit heat gain, pump heat)
    Q_sub_total: float = 0.0
    E_sub_total: float = 0.0
    for name in subsystems:
        ss: dict = sub_states.get(name, {})
        Q_sub_total += ss.get("Q_contribution", 0.0)
        E_sub_total += ss.get("E_subsystem", 0.0)

    Q_total: float = ctrl.Q_heat_source + E_sub_total + Q_sub_total + Q_flow_net
    r_energy: float = C_next * T_next - C_curr * ctx.T_tank_w_K - dt * (Q_total - Q_loss)

    # Scale to prevent fsolve Jacobian singularity (r_energy is O(1e5) while r_mass is O(1))
    r_energy_scaled = r_energy / C_tank

    return [r_energy_scaled, r_mass]
