"""Shared dynamic simulation context and control helpers.

Provides reusable dataclasses and pure functions that form the
backbone of time-stepping heat-pump simulations.  Extracted from
``AirSourceHeatPumpBoiler`` so that ``GroundSourceHeatPumpBoiler``
and future models can share the same infrastructure.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from .compressor_speed import CAPACITY_CLAMPED_MAX
from .constants import c_w, rho_w

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "ControlState",
    "CycleSolveOutcome",
    "DutyLimit",
    "DUTY_MATCH_RTOL",
    "DynamicState",
    "InStepRun",
    "EVENT_TIME_TOL_S",
    "StepContext",
    "TEMPERATURE_TOL_K",
    "bound_tolerance",
    "Subsystem",
    "SubsystemExergy",
    "SUBSYSTEM_STATE_KEY",
    "check_hp_schedule_active",
    "collect_subsystem_state",
    "delivered_tank_heat",
    "determine_heat_source_on_off",
    "determine_tank_refill_flow",
    "in_step_stop_reason",
    "resolve_step_operation",
    "scale_running_rates",
    "simulate_thermostat_step",
    "step_hp_result",
    "REDUCED_DUTY_FRACTION_KEY",
    "STOP_BOILING_LIMIT",
    "STOP_CONDENSING_LIMIT",
    "T_WATER_BOIL_C",
    "TANK_LEVEL_MIN",
    "TankStepFlows",
    "limit_tank_flows",
    "merge_running_rows",
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
    """Carried state threaded across ``step()`` calls.

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
    subsystem_state : dict[str, dict]
        State the subsystems carry from one step to the next, keyed by
        subsystem name (e.g. ``{"stc": {"pump_on": True, ...}}`` for the
        collector-loop controller: pump latch, storage-limit hold,
        stagnation lock-out). Empty at the start of a run. Each value is a
        plain dict of bools and numbers, replaced (never mutated) every
        step, so a ``DynamicState`` can be kept, copied or pickled and a step
        re-run from it gives the same result. See :class:`Subsystem` for how
        a subsystem reads and writes its entry.
    """

    T_tank_w_K: float
    tank_level: float
    is_refilling: bool
    hp_is_on_prev: bool
    dV_tank_w_out_prev: float
    hp_running_prev: bool = False
    hp_phase_age_s: float = math.inf
    subsystem_state: dict = field(default_factory=dict)  # dict[str, dict]


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


#: Key under which a subsystem's step dict carries its next-step state.
SUBSYSTEM_STATE_KEY: str = "carried_state"


def collect_subsystem_state(previous: Mapping[str, dict], sub_states: Mapping[str, Any]) -> dict[str, dict]:
    """Next ``DynamicState.subsystem_state`` from this step's subsystem dicts.

    Each subsystem whose step dict has a :data:`SUBSYSTEM_STATE_KEY` entry
    contributes a copy of it; a subsystem that reported none keeps its
    previous entry. A new dict is returned and ``previous`` is not modified,
    so the state a step was given can be stepped from again.
    """
    carried: dict[str, dict] = {name: dict(value) for name, value in previous.items()}
    for name, step_state in sub_states.items():
        if isinstance(step_state, Mapping) and step_state.get(SUBSYSTEM_STATE_KEY) is not None:
            carried[name] = dict(step_state[SUBSYSTEM_STATE_KEY])
    return carried


class Subsystem(Protocol):
    """Pluggable subsystem interface.

    Each subsystem computes its contribution for a single
    timestep and assembles result columns for the output
    DataFrame.  New subsystems (PV, battery, …) implement
    this protocol and register with the boiler model.

    Carried state
    -------------
    A subsystem with memory between steps (a controller latch, a lock-out)
    does not keep it on its own instance: it is part of the run and travels
    in ``DynamicState.subsystem_state[name]``.

    - *Read*: during a step the host model holds the previous step's states
      in ``host._subsystem_state_in`` (``{}`` at the start of a run); the
      subsystem's entry is ``host._subsystem_state_in.get(name, {})``. It
      must not be modified. The host's hooks (``_run_subsystems``,
      ``_augment_results``) read it there; a subsystem object stepped
      through this protocol has no reference to its host, so the hook that
      calls it hands the entry over.
    - *Write*: the subsystem puts its next state, a new plain dict, under
      :data:`SUBSYSTEM_STATE_KEY` (``"carried_state"``) of the dict its
      ``step()`` returned, at the latest in ``assemble_results()`` (the
      state may depend on the solved tank temperature). The host gathers
      these with :func:`collect_subsystem_state`.

    A ``step()``-driven host (ASHPB) threads the states through the
    ``DynamicState`` it returns; a host that only offers ``analyze_dynamic``
    (GSHPB) keeps them for the run and clears them when a run starts. Either
    way a second run with the same inputs starts from empty state.
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
    limit_stop : bool
        The cut-out ``T_limit_C`` of :func:`simulate_thermostat_step` stopped
        the compressor, or kept it from starting, while the thermostat (its
        call, or a minimum run time) would have had it run: a protection stop,
        not the thermostat reaching its upper bound.
    T_on_max_C : float | None
        Highest tank temperature at which the compressor ran in the step
        [°C]; ``None`` if it did not run.
    limit_stop_s : float | None
        Time in the step at which a cut-out first acted [s]; ``None`` without
        a ``limit_stop``.
    limit_stop_C : float | None
        The tank cut-out that did so [°C], where the caller cannot know it:
        with reduced-duty charging the step is resolved with the lower of the
        boiling point and the tank temperature at which the condensing limit
        leaves the duty the compressor has, and :func:`in_step_stop_reason`
        names the stop from it. ``None`` without a ``limit_stop``, and for a
        run resolved with the cut-out the caller gave (``T_limit_C``), who
        names it. It labels the run and is not part of comparing two runs.
    reduced_fraction : float
        Fraction of the step the compressor ran on the duty limit, below the
        duty it runs at otherwise (0 without a ``duty_limit``); part of
        ``runtime_fraction``.
    T_reduced_mean_C : float | None
        Run-time-weighted mean tank temperature of that stretch [°C]: the
        limit's duty at it is the stretch's mean duty. ``None`` without one.
    T_full_mean_C : float | None
        Run-time-weighted mean tank temperature of the rest of the run, at
        the full duty [°C]; set only with a ``duty_limit``.
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
    limit_stop: bool = False
    reduced_fraction: float = 0.0
    T_reduced_mean_C: float | None = None
    T_full_mean_C: float | None = None
    limit_stop_C: float | None = field(default=None, compare=False)
    T_on_max_C: float | None = None
    limit_stop_s: float | None = None


@dataclass(frozen=True)
class DutyLimit:
    """Largest duty a running compressor gives a mixed tank on an operating limit.

    ``K * (T_zero_C - T)`` [W] at tank temperature ``T`` [°C]: a limit on the
    condensing temperature of a compressor whose tank heat exchanger needs an
    approach proportional to the duty (``T_zero_C``: the tank temperature at
    which the limit leaves no duty; ``K`` [W/K]: the heat the exchanger
    carries per kelvin of approach). ``Q_min`` [W] is the smallest duty the
    compressor runs at continuously: where the limit leaves less
    (:attr:`stop_C`) it stops.
    """

    T_zero_C: float
    K: float
    Q_min: float = 0.0

    def duty(self, T_C: float) -> float:
        """Duty the limit allows at tank temperature ``T_C`` [W].

        ``K (T_zero_C - T_C)``, and at or below :attr:`stop_C` never less than
        ``Q_min``. That is the line's own value there; the bound only keeps
        the last digits from saying otherwise (``stop_C`` is
        ``T_zero_C - Q_min / K`` in floating point, and the line computed back
        from it falls a few 1e-11 W short of ``Q_min`` for most numbers), so
        that a compressor on the limit is never resolved below its smallest
        continuous duty.
        """
        on_line = self.K * (self.T_zero_C - T_C)
        return on_line if self.stop_C < T_C else max(on_line, self.Q_min)

    def knee_C(self, Q_on: float) -> float:
        """Tank temperature above which the limit allows less than ``Q_on`` [°C]."""
        return self.T_zero_C - Q_on / self.K

    @property
    def stop_C(self) -> float:
        """Tank temperature at which the limit allows no more than ``Q_min`` [°C]."""
        return self.T_zero_C - self.Q_min / self.K


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
    run_limit_s: float = math.inf,
    Q_hold: float = 0.0,
    duty_limit: DutyLimit | None = None,
    rest_before_restart: bool = False,
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
        Tank cut-out [°C], for a tank whose water is the coil's: it stops a
        running compressor whatever its minimum run time, and above it the
        compressor does not start. The call is the thermostat's (``T_lower``,
        ``T_upper``); the cut-out does not end it. While the call stands at
        the cut-out the compressor holds the tank there: it runs the share of
        the time that balances the tank's free heat at ``T_limit_C`` (cycling
        on its protection, or a variable-speed unit at the duty the limit
        allows) until the call ends or the step does, as one run (one start
        at most). That is the limit of a restart with no differential (start
        as soon as the tank is below the cut-out, stop at it), which the
        fast-cycling limit below would reach after 1000 events; a tank above
        the cut-out cools to it first (an event). The default is the boiling
        point (constructors refuse bounds above 100 °C, so it acts when a
        minimum run time holds the compressor past the upper bound); GSHPB
        and WSHPB pass the tank temperature at which their compressor
        envelope stops them (:func:`tmhp.compressor_envelope.condensing_tank_cutout`),
        so the protection stop happens inside the step, not at a step
        boundary.
    run_limit_s, Q_hold : float
        Cut-out of the water around a multi-node tank's coil, which can be
        hotter than the mean ``T`` this model follows: after ``run_limit_s``
        of running in the step [s] (the time the boiler found the coil's
        water to reach its envelope stop; 0: already there; ``inf``: not in
        this step) the compressor stops, and while the call stands it holds
        that water there by giving ``Q_hold`` [W] (running ``Q_hold / Q_on``
        of the time; 0: it stays off for the rest of the step).

    duty_limit : DutyLimit | None
        A compressor that reduces its duty on an operating limit instead of
        stopping (an inverter on its condensing limit). Above the tank
        temperature at which the limit allows ``Q_on``
        (:meth:`DutyLimit.knee_C`) the running compressor delivers the
        limit's duty, ``K (T_zero - T)``, so the tank follows
        ``C dT/dt = K (T_zero - T) + P(T)`` -- again a linear equation,
        solved exactly between events; the knee is an event. The tank cut-out
        is then where the limit leaves no more than the smallest continuous
        duty (:attr:`DutyLimit.stop_C`, never above ``T_limit_C``; the knee
        when the floor is not below ``Q_on``), and a call that stands there is
        held by cycling at that duty (or, with ``rest_before_restart`` and a
        minimum rest time, followed through its rests and restarts) -- unless
        the tank loses more there than
        that duty: the compressor then runs on, and the tank settles below the
        cut-out where the limit's duty is the loss. Above the cut-out the
        compressor never runs, so it never delivers less than ``Q_min`` on the
        limit (a tank within :data:`TEMPERATURE_TOL_K` above the cut-out is at
        it). The run reports the stretch on the limit
        separately (``reduced_fraction``, ``T_reduced_mean_C``,
        ``T_full_mean_C``): the mean duty of the stretch is the limit's duty at
        its mean temperature, exactly, whatever the step. Mixed tanks only:
        not combined with ``run_limit_s`` / ``Q_hold``. ``None``: the
        compressor delivers ``Q_on`` whenever it runs.
    rest_before_restart : bool
        What a compressor stopped at the tank cut-out does while the call
        stands. ``False`` (the default): it holds the tank there as described
        under ``T_limit_C`` -- a mean of on/off cycling without a differential,
        reported as one run: one start, ``running_end`` true, and
        ``phase_age_end_s`` counting from that start although the compressor
        was on for ``runtime_fraction`` of the time; the minimum run and rest
        times are not applied to the cycling inside the hold. ``True``: with a
        minimum rest time (``min_off_time_s > 0``) the stop is a real one. The
        compressor rests for that time while the tank cools, restarts (an
        event: a counted start, the phase age from it) and runs back to the
        cut-out, so the run time, the starts and the timers describe the same
        compressor. Without a minimum rest time the hold stands: it is what
        that cycling tends to as the rest time goes to zero. Reduced-duty
        charging (:func:`resolve_step_operation`) sets it.

    A stop or a blocked start either cut-out causes while the thermostat
    would have run sets ``limit_stop`` (with a ``duty_limit`` also
    ``limit_stop_C``, the cut-out the limit gave).

    More than 1000 switching events in one step (a band too narrow for the
    step) are closed by the fast-cycling limit: the tank holds the
    temperature it reached and the compressor runs the fraction of the rest
    of the step that balances the tank there.
    """
    if duty_limit is not None and math.isfinite(Q_on) and Q_on > 0.0:
        if math.isfinite(run_limit_s) or Q_hold != 0.0:
            raise ValueError("duty_limit is the mixed tank's: it is not combined with run_limit_s / Q_hold")
        return _simulate_duty_limited_step(
            T_start_C=T_start_C,
            call_start=call_start,
            running_prev=running_prev,
            phase_age_s=phase_age_s,
            scheduled=scheduled,
            T_lower=T_lower,
            T_upper=T_upper,
            dt=dt,
            C=C,
            free_heat=free_heat,
            Q_on=float(Q_on),
            min_on_time_s=min_on_time_s,
            min_off_time_s=min_off_time_s,
            T_limit_C=T_limit_C,
            duty_limit=duty_limit,
            rest_before_restart=rest_before_restart,
        )
    # The call's upper bound. The cut-out is never below it for a tank
    # whose coil water is the tank (it is the boiling point or the envelope
    # stop the set points are capped at); for a multi-node tank it can be.
    T_hi = T_upper
    # Bound width for this band (never more than a quarter of it).
    tol = bound_tolerance(T_lower, T_hi)
    (T_a, P_a), (T_b, P_b) = free_heat
    slope = (P_b - P_a) / (T_b - T_a) if T_b != T_a else 0.0
    p_const = P_a - slope * T_a
    q_on = float(Q_on) if math.isfinite(Q_on) and Q_on > 0.0 else 0.0
    # Holding heat at the tank cut-out: what balances the free heat there.
    q_hold_tank = max(0.0, -(p_const + slope * T_limit_C)) if math.isfinite(T_limit_C) else 0.0
    # Holding heat at the coil cut-out of a multi-node tank: as given.
    q_hold_coil = float(Q_hold) if math.isfinite(Q_hold) and Q_hold > 0.0 else 0.0
    run_limit = float(run_limit_s) if run_limit_s >= 0.0 else 0.0
    # A stop at the cut-out is followed by the minimum rest time, not by a hold.
    rests = rest_before_restart and min_off_time_s > EVENT_TIME_TOL_S

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
    limit_stop = False
    holding = False
    q_hold = 0.0
    tw_on_first: float | None = None
    tw_on_max = -math.inf
    limit_stop_t: float | None = None

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
        above_cut = tw > T_limit_C + TEMPERATURE_TOL_K
        at_tank_cut = tw >= T_limit_C - TEMPERATURE_TOL_K and not above_cut
        at_coil_cut = on_time >= run_limit - EVENT_TIME_TOL_S
        at_limit = above_cut or at_tank_cut or at_coil_cut
        # The heat that holds the water where it is cut out (none above the tank cut-out).
        q_hold = 0.0 if above_cut else (q_hold_coil if at_coil_cut else q_hold_tank)
        hold = want and at_limit and q_hold > 0.0 and q_on > 0.0 and not rests
        # Holding is decided again at every event: the call can end meanwhile.
        holding = False
        if running:
            if hold:
                # The call stands at the cut-out: it holds the water there.
                holding, limit_stop = True, True
            elif not scheduled or at_limit or (not want and age >= min_on_time_s - EVENT_TIME_TOL_S):
                if scheduled and at_limit and (want or age < min_on_time_s - EVENT_TIME_TOL_S):
                    # The cut-out, not the thermostat, stopped it.
                    limit_stop = True
                running, age = False, 0.0
            elif q_on <= 0.0:
                # Running at the end of the previous step but no cycle exists
                # at this step's conditions: the compressor stops.
                running, age, unavailable = False, 0.0, True
        elif want and age >= min_off_time_s - EVENT_TIME_TOL_S and not unavailable:
            if hold:
                running, age, holding, limit_stop = True, 0.0, True, True
                starts += 1
            elif at_limit:
                # Called with the water at (or above) the cut-out and nothing
                # to hold: the compressor stays off; above the tank cut-out it
                # waits for the tank to cool to it (an event below).
                limit_stop = True
            elif q_on > 0.0:
                running, age = True, 0.0
                starts += 1
            else:
                unavailable = True
        if t == 0.0:
            running_start = running
        if limit_stop and limit_stop_t is None:
            limit_stop_t = t

        # --- next event ---
        rem = dt - t
        if holding:
            # Held at the cut-out until the call ends (the tank reaches the
            # upper bound) or the step does. At the tank cut-out the hold
            # balances the free heat, so the tank stays there.
            duty = min(1.0, q_hold / q_on)
            drive = p_const + duty * q_on
            step, target = rem, None
            if tw < T_hi - tol:
                t_hi = _time_to_reach(tw, T_hi, drive, slope, C)
                if t_hi < rem - EVENT_TIME_TOL_S:
                    step, target = t_hi, T_hi
            T_new = target if target is not None else _advance(tw, step, drive, slope, C)
            if tw_on_first is None:
                tw_on_first = tw
            tw_on_max = max(tw_on_max, tw, T_new)
            on_time += duty * step
            on_T_integral += duty * step * 0.5 * (tw + T_new)
            tw = T_new
            age += step
            t = t + step if target is not None else dt
            continue
        if n_events >= 1000 and q_on > 0.0 and scheduled:
            # Fast-cycling limit: hold the tank where it is.
            duty = min(1.0, max(0.0, -(p_const + slope * tw) / q_on))
            if duty > 0.0:
                tw_on_max = max(tw_on_max, tw)
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
            if on_time < run_limit - EVENT_TIME_TOL_S:
                events.append((run_limit - on_time, None))
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
            if want and above_cut:
                # Called above the tank cut-out: the tank cools to it, then holds.
                events.append((_time_to_reach(tw, T_limit_C, drive, slope, C), T_limit_C))
            if rests and want and at_tank_cut:
                # Resting at the cut-out: it may start once the tank is clear of it.
                T_clear = T_limit_C - 2.0 * TEMPERATURE_TOL_K
                events.append((_time_to_reach(tw, T_clear, drive, slope, C), T_clear))
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
            tw_on_max = max(tw_on_max, tw, T_new)
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
        limit_stop=limit_stop,
        T_on_max_C=tw_on_max if on_time > 0.0 else None,
        limit_stop_s=limit_stop_t,
    )


def _temperature_integral(T0: float, T1: float, step: float, rate_const: float, slope: float, C: float) -> float:
    """``∫T dt`` over ``step`` seconds of ``C·dT/dt = rate_const + slope·T`` that took ``T0`` to ``T1``.

    Exact: integrating the equation gives ``C (T1 - T0) = rate_const·step +
    slope·∫T dt``. Where the slope is too small for that difference to carry
    digits (``|slope|·step / C < 1e-6``) the temperature is a straight line to
    the same order and the trapezoid is used.
    """
    if abs(slope) * step < 1e-6 * C:
        return 0.5 * (T0 + T1) * step
    return (C * (T1 - T0) - rate_const * step) / slope


def _simulate_duty_limited_step(
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
    min_on_time_s: float,
    min_off_time_s: float,
    T_limit_C: float,
    duty_limit: DutyLimit,
    rest_before_restart: bool = False,
) -> InStepRun:
    """:func:`simulate_thermostat_step` for a compressor that reduces its duty on ``duty_limit``.

    The event model of the mixed tank with one more piece: above the knee the
    running compressor delivers ``K (T_zero - T)`` instead of ``Q_on``. The
    transitions are those of :func:`simulate_thermostat_step`; the cut-out is
    the limit's stop.
    """
    T_hi = T_upper
    tol = bound_tolerance(T_lower, T_hi)
    (T_a, P_a), (T_b, P_b) = free_heat
    slope = (P_b - P_a) / (T_b - T_a) if T_b != T_a else 0.0
    p_const = P_a - slope * T_a
    q_on = Q_on
    K, T_zero = float(duty_limit.K), float(duty_limit.T_zero_C)
    knee = duty_limit.knee_C(q_on)
    # The cut-out: where the limit leaves no more than the smallest continuous
    # duty. With a floor at or above the running duty there is no reduced
    # stretch and the compressor stops at the knee.
    T_cut = min(T_limit_C, max(duty_limit.stop_C, knee))

    def duty_at(tank: float) -> float:
        return min(q_on, max(0.0, duty_limit.duty(tank)))

    # A stretch on the limit exists only between the knee and a stop above it:
    # with a floor at or above the running duty the compressor has one duty.
    has_stretch = duty_limit.stop_C > knee

    def on_limit(tank: float) -> bool:
        """Whether a compressor running at tank temperature ``tank`` is on the limit (at the knee: if warming)."""
        if not has_stretch:
            return False
        if tank > knee + TEMPERATURE_TOL_K:
            return True
        if tank < knee - TEMPERATURE_TOL_K:
            return False
        return p_const + slope * tank + q_on > 0.0

    # Duty the compressor runs at when it holds the cut-out, and the heat that holds the tank there.
    q_cut = duty_at(T_cut)
    cut_on_limit = has_stretch and T_cut > knee + TEMPERATURE_TOL_K
    q_hold_tank = max(0.0, -(p_const + slope * T_cut)) if math.isfinite(T_cut) else 0.0
    # A stop at the cut-out is followed by the minimum rest time, not by a hold.
    rests = rest_before_restart and min_off_time_s > EVENT_TIME_TOL_S

    t = 0.0
    tw = T_start_C
    running = bool(running_prev)
    age = float(phase_age_s)
    call = bool(call_start)
    # Run time [s] and ∫T dt [K s] at the full duty (0) and on the limit (1).
    on_time = [0.0, 0.0]
    on_T_integral = [0.0, 0.0]
    starts = 0
    running_start = running
    limit_stop = False
    holding = False
    tw_on_first: float | None = None
    tw_on_max = -math.inf
    limit_stop_t: float | None = None

    for n_events in range(1_000_001):
        # --- instantaneous transitions at (t, tw): as simulate_thermostat_step ---
        if scheduled:
            if tw <= T_lower + tol:
                call = True
            elif tw >= T_hi - tol:
                call = False
        if not scheduled:
            call = False
        if t >= dt:
            break
        want = call and scheduled
        above_cut = tw > T_cut + TEMPERATURE_TOL_K
        at_cut = tw >= T_cut - TEMPERATURE_TOL_K
        q_hold = 0.0 if above_cut else q_hold_tank
        # At the cut-out the compressor has ``q_cut``. Where the tank loses
        # less than that, it holds the tank there by cycling. Where the tank
        # loses as much or more, it cannot hold it and is not stopped either:
        # it runs on, and as the tank cools the limit leaves it more duty, so
        # the tank settles below the cut-out where that duty is the loss. (A
        # hold at the constant ``q_cut`` would be right for a compressor with
        # one duty, the model without a limit; here the duty follows the tank.)
        outrun = want and at_cut and not above_cut and q_cut > 0.0 and q_hold >= q_cut
        at_limit = at_cut and not outrun
        hold = want and at_limit and q_hold > 0.0 and q_cut > 0.0 and not rests
        if (outrun or hold) and tw > T_cut:
            # Within the bound width above the cut-out the tank is at it (the
            # last digits of the previous step's root solve): the compressor
            # runs on, or holds, from the cut-out itself, never from above it,
            # where the limit leaves less than the smallest continuous duty.
            tw = T_cut
        holding = False
        if running:
            if hold:
                holding, limit_stop = True, True
            elif not scheduled or at_limit or (not want and age >= min_on_time_s - EVENT_TIME_TOL_S):
                if scheduled and at_limit and (want or age < min_on_time_s - EVENT_TIME_TOL_S):
                    limit_stop = True
                running, age = False, 0.0
        elif want and age >= min_off_time_s - EVENT_TIME_TOL_S:
            if hold:
                running, age, holding, limit_stop = True, 0.0, True, True
                starts += 1
            elif at_limit:
                limit_stop = True
            else:
                running, age = True, 0.0
                starts += 1
        if t == 0.0:
            running_start = running
        if limit_stop and limit_stop_t is None:
            limit_stop_t = t

        # --- next event ---
        rem = dt - t
        if holding:
            # Held at the cut-out, cycling at the duty the limit leaves there
            # (which covers the loss: ``q_hold < q_cut``), until the call ends
            # or the step does.
            share = q_hold / q_cut
            drive = p_const + share * q_cut
            step, target = rem, None
            if tw < T_hi - tol:
                t_hi = _time_to_reach(tw, T_hi, drive, slope, C)
                if t_hi < rem - EVENT_TIME_TOL_S:
                    step, target = t_hi, T_hi
            T_new = target if target is not None else min(_advance(tw, step, drive, slope, C), max(tw, T_cut))
            if tw_on_first is None:
                tw_on_first = tw
            tw_on_max = max(tw_on_max, tw, T_new)
            index = 1 if cut_on_limit else 0
            on_time[index] += share * step
            on_T_integral[index] += share * step * 0.5 * (tw + T_new)
            tw = T_new
            age += step
            t = t + step if target is not None else dt
            continue
        if n_events >= 1000 and scheduled:
            # Fast-cycling limit: hold the tank where it is, at the duty the
            # limit leaves there. Above the cut-out the compressor does not
            # run; within the bound width of it, it has the cut-out's duty.
            T_here = min(tw, T_cut)
            q_here = 0.0 if above_cut else duty_at(T_here)
            share = min(1.0, max(0.0, -(p_const + slope * tw) / q_here)) if q_here > 0.0 else 0.0
            index = 1 if on_limit(T_here) else 0
            if share > 0.0:
                tw_on_max = max(tw_on_max, T_here)
            on_time[index] += share * rem
            on_T_integral[index] += share * rem * T_here
            age = 0.0
            t = dt
            continue
        reduced = running and on_limit(tw)
        if reduced:
            # C·dT/dt = K·(T_zero - T) + P(T)
            rate_const, rate_slope = p_const + K * T_zero, slope - K
        else:
            rate_const, rate_slope = p_const + (q_on if running else 0.0), slope
        events: list[tuple[float, float | None]] = [(rem, None)]
        if running:
            if tw < T_cut - TEMPERATURE_TOL_K:
                events.append((_time_to_reach(tw, T_cut, rate_const, rate_slope, C), T_cut))
            if age < min_on_time_s - EVENT_TIME_TOL_S:
                events.append((min_on_time_s - age, None))
            if want and tw < T_hi - tol:
                events.append((_time_to_reach(tw, T_hi, rate_const, rate_slope, C), T_hi))
            if abs(tw - knee) > TEMPERATURE_TOL_K:
                # Onto the limit from below, or back off it when a draw cools the tank.
                events.append((_time_to_reach(tw, knee, rate_const, rate_slope, C), knee))
        else:
            if want and age < min_off_time_s - EVENT_TIME_TOL_S:
                events.append((min_off_time_s - age, None))
            if scheduled and not call and tw > T_lower + tol:
                events.append((_time_to_reach(tw, T_lower, rate_const, rate_slope, C), T_lower))
            if scheduled and call and tw < T_hi - tol:
                events.append((_time_to_reach(tw, T_hi, rate_const, rate_slope, C), T_hi))
            if want and above_cut:
                events.append((_time_to_reach(tw, T_cut, rate_const, rate_slope, C), T_cut))
            if rests and want and at_cut and not above_cut:
                # Resting at the cut-out: it may start once the tank is clear of it.
                T_clear = T_cut - 2.0 * TEMPERATURE_TOL_K
                events.append((_time_to_reach(tw, T_clear, rate_const, rate_slope, C), T_clear))
        step, target = min((e for e in events if e[0] >= 0.0), key=lambda e: e[0])
        if step >= rem - EVENT_TIME_TOL_S:
            step, target = rem, None
        T_new = _advance(tw, step, rate_const, rate_slope, C)
        if target is not None and step < rem:
            T_new = target
        if running:
            # A running compressor is at or below the cut-out (above it, it was
            # stopped) and the cut-out is an event: the stretch ends there at
            # the latest, whatever the last digit of the exponential says.
            T_new = min(T_new, max(tw, T_cut))
            if tw_on_first is None:
                tw_on_first = tw
            tw_on_max = max(tw_on_max, tw, T_new)
            index = 1 if reduced else 0
            on_time[index] += step
            on_T_integral[index] += _temperature_integral(tw, T_new, step, rate_const, rate_slope, C)
        tw = T_new
        t = t + step if step < rem else dt
        age += step
    else:  # pragma: no cover - defensive: event loop did not terminate
        raise RuntimeError("in-step thermostat did not settle")

    total = on_time[0] + on_time[1]
    # Every temperature of the stretch on the limit is at or below the cut-out,
    # so its mean is too: the division's last digit must not say otherwise
    # (the limit's duty at that mean is the stretch's duty, never below ``Q_min``).
    T_reduced_mean = min(on_T_integral[1] / on_time[1], T_cut) if on_time[1] > 0.0 else None
    return InStepRun(
        runtime_fraction=min(1.0, max(0.0, total / dt)) if dt > 0 else 0.0,
        running_start=running_start,
        running_end=running,
        call_end=call,
        starts=starts,
        phase_age_end_s=age,
        T_on_mean_C=(on_T_integral[0] + on_T_integral[1]) / total if total > 0.0 else None,
        heat_unavailable=False,
        T_on_first_C=tw_on_first,
        limit_stop=limit_stop,
        reduced_fraction=min(1.0, max(0.0, on_time[1] / dt)) if dt > 0 else 0.0,
        T_reduced_mean_C=T_reduced_mean,
        T_full_mean_C=on_T_integral[0] / on_time[0] if on_time[0] > 0.0 else None,
        limit_stop_C=T_cut if limit_stop else None,
        T_on_max_C=tw_on_max if total > 0.0 else None,
        limit_stop_s=limit_stop_t,
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
    T_limit_C: float = T_WATER_BOIL_C,
    run_limit_s: float = math.inf,
    Q_hold: float = 0.0,
    duty_line: tuple[float, float] | None = None,
    min_duty: Callable[[], float | None] | None = None,
    solve_cycle_at_duty: Callable[[float, float, float], dict] | None = None,
    rate_keys: tuple[str, ...] = (),
    duty_limit_failure: str | None = None,
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

    ``T_limit_C`` (tank cut-out, default the boiling point), ``run_limit_s``
    and ``Q_hold`` (coil cut-out of a multi-node tank and its holding heat)
    are passed to :func:`simulate_thermostat_step`: a boiler gives its
    envelope stop, so the compressor stops there inside the step whatever
    its minimum run time.

    **Reduced-duty charging** (``duty_line``, ``min_duty``,
    ``solve_cycle_at_duty``, ``rate_keys`` all given; a mixed tank). The
    boiler's compressor does not stop where its requested duty meets an
    operating limit: it runs on at the largest duty the limit allows.
    ``duty_line = (T_zero_C, K)`` is that duty as a function of the tank
    temperature, ``K (T_zero_C - T)`` [W] (:class:`DutyLimit`), and
    ``min_duty()`` the smallest duty the compressor runs at continuously in
    this step's conditions [W] (``None``: no duty runs below the request, the
    stop stands); it is called -- it costs cycle solves -- only for a step
    that gets to the limit. Such a step is resolved with the events of
    :func:`simulate_thermostat_step` on that limit, and its run has up to two
    stretches, each with one cycle:

    - at the full duty, the cycle ``solve_cycle`` gives at the stretch's mean
      tank temperature (as above);
    - on the limit, ``solve_cycle_at_duty(T_C, Q, runtime_fraction)`` at the
      stretch's mean tank temperature and the limit's duty there. The limit is
      linear in the tank temperature, so that is the stretch's mean duty
      exactly, and the condensing pressure is the limit's all along: only the
      duty varies over the stretch, and the cycle at its mean is the midpoint
      rule in that one variable.

    The cycle returned is then the run-time-weighted mean of the two
    (:func:`merge_running_rows` over ``rate_keys``), which
    :func:`step_hp_result` averages over the step like any other. Each
    stretch's cycle is given the run time that carries the step's mean load at
    its own duty. A step that does not get to the limit is resolved exactly as
    without these arguments, with the cut-out at the limit's knee. The
    round-off confirmation below (the implicit solve of the full step) assumes
    one heat rate for the step and is not applied to a step on the limit.

    The events take the limit's duty for the stretch above the knee; the cycle
    solved there says whether the compressor delivers it. It does (to
    :data:`DUTY_MATCH_RTOL`) when the condensing limit is the one that governs.
    When it does not, another limit holds the compressor below the condensing
    limit's duty (the source fluid's freezing limit, the discharge
    temperature), or leaves it no operating point, and the step is resolved
    with what its cycles deliver:

    - the cycle runs at less, and the cycle at the start of the run says that
      the other limit's duty falls with the tank temperature: that limit is
      followed as a line of its own through the two cycles, in closed form
      like the condensing limit, to where it leaves the smallest continuous
      duty, meets the condensing limit, or a cut-out acts;
    - the cycle runs at less and that duty does not fall with the tank
      temperature: the events are solved again with it (one duty), the cycle
      again at the mean tank temperature of that run, and the run ends where
      that duty meets the condensing limit;
    - where one limit hands over to the other inside the step, the rest of
      the step is resolved again from that tank temperature and joined to the
      run, the row being the run-time-weighted mean of the cycles
      (:func:`merge_running_rows`). ``reduced_fraction`` counts only the time
      on the condensing limit, and the row names the limit its run spent the
      longest time under;
    - the cycle has no operating point (for another reason than the limit
      itself, ``duty_limit_failure``): the compressor does not run
      (``heat_unavailable``) and the row is the cycle's off-mode row with its
      ``failure_reason``, as for any step whose cycle cannot be solved. When
      the stretch's mean lies more than :data:`CYCLE_RETRY_SPAN_K` above the
      temperature the run starts from, the cycle at that start decides
      instead (as for the full-duty stretch).

    A run under another limit is not taken above a tank temperature at which
    no cycle of the step ran unless the cycle there runs too; where the other
    limit leaves no operating point inside it, the run ends at the warmest
    temperature a cycle did run at. A cycle the other limit holds at or below
    the smallest continuous duty counts as not running. A cycle the condensing
    limit itself refuses at the duty the limit's line gives
    (``failure_reason == duty_limit_failure``) keeps the stop. The hand-overs
    of one step are bounded; a step that uses them up ends its run without a
    ``limit_stop`` (it has not met a cut-out) and the next step goes on. The
    documentation has the measured agreement between step sizes.

    With these arguments a compressor stopped at a cut-out rests for its
    minimum rest time before it restarts (``rest_before_restart`` of
    :func:`simulate_thermostat_step`).

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
        T_limit_C=T_limit_C,
        run_limit_s=run_limit_s,
        Q_hold=Q_hold,
    )
    reduce = duty_line is not None and min_duty is not None and solve_cycle_at_duty is not None
    if reduce:
        assert duty_line is not None
        # Up to the knee the compressor is the one that stops there: the step
        # is resolved with that cut-out first, and on the limit only if it
        # gets there.
        T_limit_given = T_limit_C
        T_limit_C = min(T_limit_C, DutyLimit(*duty_line).knee_C(Q_request))
        kwargs["T_limit_C"] = T_limit_C
        # A stop at a cut-out is a real one: the minimum rest time follows it.
        kwargs["rest_before_restart"] = True
    provisional = simulate_thermostat_step(Q_on=Q_request, **kwargs)  # type: ignore[arg-type]
    if reduce and provisional.limit_stop:
        on_limit = _resolve_duty_limited(
            {**kwargs, "T_limit_C": T_limit_given},
            Q_request,
            duty_line,  # type: ignore[arg-type]
            min_duty,  # type: ignore[arg-type]
            solve_cycle,
            solve_cycle_at_duty,  # type: ignore[arg-type]
            rate_keys,
            duty_limit_failure,
        )
        if on_limit is not None:
            return on_limit
    if provisional.T_on_mean_C is None:
        return (_stopped_at(provisional, T_limit_C) if reduce else provisional), None
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
    if reduce and run.limit_stop and not provisional.limit_stop:
        # The cycle delivers another duty than the request and the run now
        # gets to the limit: resolve it there.
        on_limit = _resolve_duty_limited(
            {**kwargs, "T_limit_C": T_limit_given},
            Q_request,
            duty_line,  # type: ignore[arg-type]
            min_duty,  # type: ignore[arg-type]
            solve_cycle,
            solve_cycle_at_duty,  # type: ignore[arg-type]
            rate_keys,
            duty_limit_failure,
        )
        if on_limit is not None:
            return on_limit

    T_hi = min(T_upper, T_limit_C)
    age_through = (phase_age_s if running_prev == run.running_start else 0.0) + dt
    if (
        run.running_start
        and not run.running_end
        and run.starts == (0 if running_prev else 1)
        and min_on_time_s == 0
        and not run.limit_stop
    ):
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
    return (_stopped_at(run, T_limit_C) if reduce else run), cycle_result


def _stopped_at(run: InStepRun, T_cut_C: float) -> InStepRun:
    """``run`` with the cut-out it was resolved with, if that cut-out acted (``limit_stop_C``)."""
    return dataclasses.replace(run, limit_stop_C=T_cut_C) if run.limit_stop else run


DUTY_MATCH_RTOL: float = 1e-3
"""Fraction by which a cycle may fall short of the duty the condensing limit
leaves and still count as running on that limit.

A cycle asked for the limit's duty at a tank temperature on the limit delivers
it to the solver's last digits when the condensing limit is the one that
governs (measured ratio 1.000000 on the documented charges of the three boiler
families). One that delivers less by more than this was reduced further, by
another limit; :func:`resolve_step_operation` then resolves the step with the
duty the cycle delivers.
"""

#: Event solves a stage of :func:`_resolve_duty_limited` makes at most: the
#: request, the duty the cycle delivers instead, that duty once its cycle is
#: solved at the run's own mean temperature, and one more.
_DUTY_LIMIT_PASSES = 4
#: Times :func:`_resolve_duty_limited` resolves the rest of a step again from
#: the tank temperature at which one limit hands over to the other (a run at
#: one duty meeting the condensing limit; the other limit's line meeting it).
#: A step that uses them up has not met a cut-out: its run ends there without
#: a ``limit_stop``, and the next step takes the charge on.
_DUTY_LIMIT_STAGES = 8
#: Smallest tank-temperature span [K] between two cycles from which the slope
#: of another limit's duty is taken (:func:`_resolve_duty_limited`). The duty a
#: cycle delivers is reproducible to a fraction of a watt, so a slope of a few
#: hundred W/K is well defined over this span; below it the run moves the tank
#: so little that one duty is as good.
_LINE_SPAN_K = 0.02
#: Times the line of another limit is fitted before the run is resolved at one
#: duty instead: the first through the cycles at the start of the run and at
#: the mean the events first gave it, the others through the start and the
#: cycle at the run's own mean, until that cycle delivers the line's duty.
_LINE_FITS = 4
#: Slope of another limit's duty over the tank temperature, as a fraction of the
#: condensing limit's (``K``), below which that limit is taken as flat (or
#: rising) and the run under it resolved at one duty.
_LINE_MIN_SLOPE = 0.05
#: Trial temperatures :func:`_resolve_duty_limited` solves, each half-way, to
#: find where an envelope that closes inside a run still has an operating
#: point: the run ends within a sixteenth of the unconfirmed span of that
#: point (two trials left a long step a few tenths of a kelvin short of where
#: short steps end the charge).
_CLOSING_BISECTIONS = 4


def _weighted(a: float | None, weight_a: float, b: float | None, weight_b: float) -> float | None:
    """Mean of two optional values by their weights (``None``: the value does not exist)."""
    if a is None or weight_a <= 0.0:
        return b if weight_b > 0.0 else None
    if b is None or weight_b <= 0.0:
        return a
    return (weight_a * a + weight_b * b) / (weight_a + weight_b)


def _joined_runs(first: InStepRun, t_first: float, second: InStepRun, t_second: float) -> InStepRun:
    """One step made of ``first`` over its first ``t_first`` seconds and ``second`` over the ``t_second`` after.

    ``second`` was resolved from the state ``first`` ended in, so its end
    state, its starts and its phase age are the step's. Run time, the time on
    the limit and the mean temperatures add by time. The cut-out that ended
    ``first`` is where ``second`` took over, not a stop of the step: the
    step's ``limit_stop`` is ``second``'s.
    """
    dt = t_first + t_second
    on_1, on_2 = first.runtime_fraction * t_first, second.runtime_fraction * t_second
    reduced_1, reduced_2 = first.reduced_fraction * t_first, second.reduced_fraction * t_second
    tops = [T for T in (first.T_on_max_C, second.T_on_max_C) if T is not None]
    return InStepRun(
        runtime_fraction=min(1.0, (on_1 + on_2) / dt),
        running_start=first.running_start,
        running_end=second.running_end,
        call_end=second.call_end,
        starts=first.starts + second.starts,
        phase_age_end_s=second.phase_age_end_s,
        T_on_mean_C=_weighted(first.T_on_mean_C, on_1, second.T_on_mean_C, on_2),
        heat_unavailable=False,
        T_on_first_C=first.T_on_first_C if first.T_on_first_C is not None else second.T_on_first_C,
        limit_stop=second.limit_stop,
        reduced_fraction=min(1.0, (reduced_1 + reduced_2) / dt),
        T_reduced_mean_C=_weighted(first.T_reduced_mean_C, reduced_1, second.T_reduced_mean_C, reduced_2),
        T_full_mean_C=_weighted(first.T_full_mean_C, on_1 - reduced_1, second.T_full_mean_C, on_2 - reduced_2),
        limit_stop_C=second.limit_stop_C,
        T_on_max_C=max(tops) if tops else None,
        limit_stop_s=None if second.limit_stop_s is None else t_first + second.limit_stop_s,
    )


def _resolve_duty_limited(
    kwargs: dict,
    Q_request: float,
    duty_line: tuple[float, float],
    min_duty: Callable[[], float | None],
    solve_cycle: Callable[[float, float], dict],
    solve_cycle_at_duty: Callable[[float, float, float], dict],
    rate_keys: tuple[str, ...],
    duty_limit_failure: str | None = None,
) -> tuple[InStepRun, dict | None] | None:
    """A step that gets to the duty limit: its run and the mean cycle of its stretches.

    The reduced-duty part of :func:`resolve_step_operation` (which describes
    it). ``kwargs`` are the thermostat's arguments with the caller's cut-out.
    ``None`` when the step keeps the stop: no continuous duty below the
    request (or below what the full-duty cycle delivers), or a cycle the limit
    itself refuses on its own line. A step resolved here therefore never runs
    above the limit's stop, and never on the limit at less than the smallest
    continuous duty; the events are those of the duty its cycles deliver; and
    a run another limit governs is not taken above a tank temperature at which
    no cycle of the step ran unless the cycle there runs too.

    A run at one duty ends where that duty meets the condensing limit. Where
    the cycle still runs there, at less, the compressor does not stop: the
    rest of the step is resolved again from that tank temperature (a stage,
    :data:`_DUTY_LIMIT_STAGES` at most), with the duty the other limit leaves
    there, and the step is the two runs joined (:func:`_joined_runs`) with the
    run-time-weighted mean of their cycles.
    """
    floor = min_duty()
    if floor is None or not (math.isfinite(floor) and 0.0 <= floor < Q_request):
        return None
    limit = DutyLimit(duty_line[0], duty_line[1], float(floor))
    # Warmest tank at which a cycle of this step has run above the smallest continuous duty [degC].
    T_ran = -math.inf

    def ran(cycle: Mapping[str, Any], T_C: float) -> float:
        """Heat the cycle solved at ``T_C`` delivers [W]; where it runs above the smallest duty is remembered."""
        nonlocal T_ran
        q = delivered_tank_heat(cycle)
        if q > limit.Q_min:
            T_ran = max(T_ran, T_C)
        return q

    def stretches(run: InStepRun, q_full: float) -> tuple[float, float, float, float]:
        """Run fractions at the full duty and on the limit, the limit's mean duty, the step's mean load [W]."""
        f_reduced = run.reduced_fraction if run.T_reduced_mean_C is not None else 0.0
        f_full = max(0.0, run.runtime_fraction - f_reduced) if run.T_full_mean_C is not None else 0.0
        q_reduced = limit.duty(run.T_reduced_mean_C) if run.T_reduced_mean_C is not None else 0.0
        return f_full, f_reduced, q_reduced, f_full * q_full + f_reduced * q_reduced

    def equivalent_fraction(load: float, q: float) -> float:
        """Run time at duty ``q`` that carries the step's mean load: what the stretch's source sees."""
        return min(1.0, load / q) if q > 0.0 else 1.0

    def no_cycle(kw: dict, cycle: dict | None) -> tuple[InStepRun, dict | None]:
        """No operating point: the compressor does not run, and the row is the cycle's own (its failure)."""
        return simulate_thermostat_step(Q_on=0.0, **kw), cycle

    def up_to(kw: dict, q: float, T_cut_C: float) -> InStepRun:
        """The step of a compressor with the one duty ``q`` and its cut-out at ``T_cut_C``."""
        cut = min(kw["T_limit_C"], T_cut_C)
        return _stopped_at(simulate_thermostat_step(Q_on=q, **{**kw, "T_limit_C": cut}), cut)

    def confirmed(kw: dict, run: InStepRun, cycle: dict | None, q: float) -> tuple[InStepRun, dict | None] | None:
        """A run at the one duty ``q`` another limit governs, not taken past where that limit closes.

        One cycle stands for the run (the duty it delivers at one tank
        temperature). Where the run goes above every tank temperature at which
        a cycle of this step ran, the cycle is solved at the run's warmest
        point; if it has no operating point there, the envelope closes inside
        the run. The run then ends at the warmest temperature a cycle did run
        at, looked for half-way to the closed point :data:`_CLOSING_BISECTIONS`
        times -- or, when that is where the run starts, the compressor does
        not run and the row is the failure of the coldest cycle that did not.
        A cycle the other limit holds at or below the smallest continuous duty
        counts as not running, as everywhere in this resolution (the stop
        stands there: ``None``).
        """
        if cycle is None:
            return None
        T_top = run.T_on_max_C
        if T_top is None or T_top <= T_ran + TEMPERATURE_TOL_K:
            return run, cycle

        def probe_at(T_C: float) -> dict:
            # Asked for the most the condensing limit leaves there, so that a
            # cycle that does not run names the limit that stops it.
            most = limit.duty(T_C)
            if most < Q_request:
                return solve_cycle_at_duty(T_C, most, run.runtime_fraction)
            return solve_cycle(T_C, run.runtime_fraction)

        probe = probe_at(T_top)
        if ran(probe, T_top) > limit.Q_min:
            return run, cycle
        T_first = run.T_on_first_C
        T_closed = T_top
        if T_first is not None and T_ran >= T_first - TEMPERATURE_TOL_K:
            for _ in range(_CLOSING_BISECTIONS):
                T_mid = 0.5 * (T_ran + T_closed)
                trial = probe_at(T_mid)
                if ran(trial, T_mid) <= limit.Q_min:
                    T_closed, probe = T_mid, trial
        if T_first is None or T_ran <= T_first + TEMPERATURE_TOL_K:
            return no_cycle(kw, probe) if delivered_tank_heat(probe) <= 0.0 else None
        # Stopped by the other limit, short of the condensing limit: not one of the cut-outs.
        return dataclasses.replace(up_to(kw, q, T_ran), limit_stop=False, limit_stop_C=None, limit_stop_s=None), cycle

    def onward(
        kw: dict,
        whole: tuple[InStepRun, dict | None],
        up_to_s: Callable[[float], InStepRun],
        T_from_C: float,
        stages_left: int,
    ) -> tuple[InStepRun, dict | None]:
        """``whole`` (a run that ends at ``T_from_C`` because the other limit takes over there), and on from there.

        ``up_to_s(t)`` is the same run over the first ``t`` seconds of the
        step. The rest of the step is resolved from ``T_from_C`` as a step of
        its own and joined to it. If nothing runs on from there, ``whole``
        stands: the compressor stops there. With no stage left the rest is
        not resolved, and ``whole`` is returned without the stop: the step has
        not met a cut-out, and the next one goes on from where it ends.
        """
        run, row = whole
        t_from, dt = run.limit_stop_s, float(kw["dt"])
        if t_from is None or run.runtime_fraction <= 0.0 or row is None:
            return whole
        if not (EVENT_TIME_TOL_S < t_from < dt - EVENT_TIME_TOL_S):
            return whole
        if stages_left <= 0:
            return dataclasses.replace(run, limit_stop=False, limit_stop_C=None, limit_stop_s=None), row
        # Up to the moment the run gets there: the state the rest starts from.
        first = up_to_s(t_from)
        rest = {
            **kw,
            "T_start_C": T_from_C,
            "dt": dt - t_from,
            "call_start": first.call_end,
            "running_prev": first.running_end,
            "phase_age_s": first.phase_age_end_s,
        }
        resolved = stage(rest, stages_left - 1)
        if resolved is None:
            return whole
        second, row_2 = resolved
        ran_2 = second.runtime_fraction * (dt - t_from)
        if ran_2 <= 0.0 or row_2 is None or delivered_tank_heat(row_2) <= 0.0:
            return whole
        ran_1 = first.runtime_fraction * t_from
        return _joined_runs(first, t_from, second, dt - t_from), merge_running_rows(row, ran_1, row_2, ran_2, rate_keys)

    def at_one_duty(kw: dict, q: float, cycle: dict | None, stages_left: int) -> tuple[InStepRun, dict | None] | None:
        """The step of a compressor with the one duty ``q``, up to where that duty meets the limit, and on from there.

        At that tank temperature the limit leaves exactly ``q``. If the cycle
        still runs there (at less: the other limit's duty), the compressor
        goes on (``onward``). If nothing runs on from there, the compressor
        stops at it, as a compressor with one duty does.
        """
        T_knee = limit.knee_C(q)
        whole = confirmed(kw, up_to(kw, q, T_knee), cycle, q)
        if whole is None:
            return None
        run = whole[0]
        if not (run.limit_stop and run.limit_stop_C is not None and run.limit_stop_C >= T_knee):
            return whole
        cut = min(kw["T_limit_C"], T_knee)
        return onward(
            kw,
            whole,
            lambda t: simulate_thermostat_step(Q_on=q, **{**kw, "T_limit_C": cut, "dt": t}),
            T_knee,
            stages_left,
        )

    def along_other_limit(
        kw: dict, T_s: float, q_s: float, T_r: float, q_r: float, fraction: float, stages_left: int
    ) -> tuple[InStepRun, dict | None] | None:
        """A run under another limit whose duty falls with the tank temperature, followed along that limit.

        The cycle at the start of the run (``T_s``) delivers ``q_s`` and the
        one at a warmer tank (``T_r``) delivers ``q_r``, both asked for the
        duty the condensing limit leaves and held below it. The other limit's
        duty is taken as the straight line through the two, which makes it a
        :class:`DutyLimit` of its own: the tank follows it in closed form, as
        it follows the condensing limit, to where it leaves the smallest
        continuous duty, to where it meets the condensing limit (the rest of
        the step is then resolved from there, ``onward``), or to a cut-out of
        the step. The run's cycle is the one at its mean tank temperature,
        whose duty is the run's mean duty on a line; if it is off the line by
        more than :data:`DUTY_MATCH_RTOL`, the line is taken through the start
        and that cycle instead. The warmest point of the run is confirmed as
        for a run at one duty (``confirmed``).

        ``None`` when this does not apply and the run is resolved at one duty:
        a limit whose duty does not fall with the tank temperature
        (:data:`_LINE_MIN_SLOPE`), a tank that cools during the run, a cycle
        that does not run at the run's mean.
        """

        def along(K_other: float) -> tuple[DutyLimit, float, float, InStepRun]:
            line = DutyLimit(T_s + q_s / K_other, K_other, limit.Q_min)
            # Where the other limit's line meets the condensing limit's (a shallower line catches up with it).
            T_meet = math.inf
            if K_other < limit.K:
                T_meet = max(T_s, (limit.K * limit.T_zero_C - K_other * line.T_zero_C) / (limit.K - K_other))
            cut = min(kw["T_limit_C"], limit.stop_C, T_meet)
            return line, T_meet, cut, simulate_thermostat_step(Q_on=q_s, **{**kw, "duty_limit": line, "T_limit_C": cut})

        def usable(run: InStepRun) -> bool:
            """Whether the run stayed on the line (a tank that cools runs at the start's duty below it)."""
            on_line = run.T_reduced_mean_C is not None and run.reduced_fraction > 0.0
            return on_line and run.runtime_fraction - run.reduced_fraction <= 1e-9

        K_other = (q_s - q_r) / (T_r - T_s)
        if not K_other > _LINE_MIN_SLOPE * limit.K:
            return None
        line, T_meet, cut, run = along(K_other)
        # The cycle at the run's mean has to deliver the line's duty there:
        # that is the run's mean duty, so the heat the events integrate is the
        # heat of the row. A limit that is not that line is taken through the
        # start and the cycle at the run's own mean, until the two agree.
        middle: dict | None = None
        for _ in range(_LINE_FITS):
            if not usable(run):
                return None
            assert run.T_reduced_mean_C is not None
            T_m = run.T_reduced_mean_C
            middle = solve_cycle_at_duty(T_m, limit.duty(T_m), fraction)
            q_m = ran(middle, T_m)
            if q_m <= limit.Q_min:
                return None
            if abs(q_m - line.duty(T_m)) <= DUTY_MATCH_RTOL * q_m:
                break
            if T_m - T_s < _LINE_SPAN_K:
                return None
            K_other = (q_s - q_m) / (T_m - T_s)
            if not K_other > _LINE_MIN_SLOPE * limit.K:
                return None
            line, T_meet, cut, run = along(K_other)
            middle = None
        if middle is None:
            return None  # the events and the cycle did not come to agree: one duty, which does by construction

        def labelled(sim: InStepRun) -> InStepRun:
            """The run as one under the other limit: none of its time is on the condensing limit."""
            return dataclasses.replace(sim, reduced_fraction=0.0, T_reduced_mean_C=None, T_full_mean_C=sim.T_on_mean_C)

        def again_up_to(t: float) -> InStepRun:
            return labelled(simulate_thermostat_step(Q_on=q_s, **{**kw, "duty_limit": line, "T_limit_C": cut, "dt": t}))

        # The warmest point of the run, if no cycle of the step has run that
        # warm: what the cycle there delivers says whether the other limit has
        # closed before it, ends there, or goes on.
        T_top = run.T_on_max_C
        q_top: float | None = None
        if T_top is not None and T_top > T_ran + TEMPERATURE_TOL_K:
            q_top = ran(solve_cycle_at_duty(T_top, limit.duty(T_top), fraction), T_top)
            if q_top < limit.Q_min * (1.0 - DUTY_MATCH_RTOL):
                T_closed = T_top
                for _ in range(_CLOSING_BISECTIONS):
                    T_mid = 0.5 * (T_ran + T_closed)
                    if ran(solve_cycle_at_duty(T_mid, limit.duty(T_mid), fraction), T_mid) <= limit.Q_min:
                        T_closed = T_mid
                if T_ran <= T_s + TEMPERATURE_TOL_K:
                    return None
                capped = simulate_thermostat_step(Q_on=q_s, **{**kw, "duty_limit": line, "T_limit_C": min(cut, T_ran)})
                # Stopped where the other limit closes, short of the condensing limit: not one of the cut-outs.
                return dataclasses.replace(
                    labelled(capped), limit_stop=False, limit_stop_C=None, limit_stop_s=None
                ), middle
        whole = (labelled(run), middle)
        if not (run.limit_stop and run.limit_stop_C is not None and T_top is not None):
            return whole
        if run.limit_stop_C < cut - TEMPERATURE_TOL_K:
            # The run ended where the line leaves the smallest duty. If the
            # cycle delivers more than that there (or one ran warmer still),
            # the other limit has not ended and the line was short of it: on
            # from there.
            goes_on = (
                q_top > limit.Q_min * (1.0 + DUTY_MATCH_RTOL) if q_top is not None else T_ran > T_top + _LINE_SPAN_K
            )
            return onward(kw, whole, again_up_to, run.limit_stop_C, stages_left) if goes_on else whole
        if T_meet < min(kw["T_limit_C"], limit.stop_C) - TEMPERATURE_TOL_K and run.limit_stop_C >= T_meet:
            # The other limit's line has met the condensing limit's: on from there, on the condensing limit.
            return onward(kw, whole, again_up_to, T_meet, stages_left)
        return whole

    def condensing_first(
        kw: dict, q_on: float, T_s: float, T_r: float, q_r: float, fraction: float, stages_left: int
    ) -> tuple[InStepRun, dict | None] | None:
        """A run that starts on the condensing limit and comes under another limit further up.

        The cycle at the start (``T_s``) delivers the condensing limit's duty;
        the one at ``T_r`` delivers less (``q_r``). Somewhere between the two
        the other limit takes over. A cycle half-way says where: if the
        condensing limit still governs there, the run is on it up to there;
        otherwise the other limit's line through that cycle and the one at
        ``T_r`` meets the condensing limit's below it, and the run is on the
        condensing limit up to where they meet. The rest of the step is
        resolved from that tank temperature (``onward``), so the time on the
        condensing limit is counted as such at any step.

        ``None`` when the start is itself where the two meet (the other
        limit's line is then followed from the start), or when the cycle of
        the stretch does not deliver the condensing limit's duty after all.
        """
        T_half = 0.5 * (T_s + T_r)
        q_half = ran(solve_cycle_at_duty(T_half, limit.duty(T_half), fraction), T_half)
        if q_half <= 0.0:
            return None
        if q_half >= limit.duty(T_half) * (1.0 - DUTY_MATCH_RTOL):
            T_meet = T_half
        else:
            K_other = (q_half - q_r) / (T_r - T_half)
            if not K_other > limit.K:
                return None  # not a line that comes down onto the condensing limit's
            T_meet = (K_other * (T_half + q_half / K_other) - limit.K * limit.T_zero_C) / (K_other - limit.K)
            if not (T_s + _LINE_SPAN_K < T_meet <= T_half):
                return None
        cut = min(kw["T_limit_C"], T_meet)
        on_condensing = {**kw, "duty_limit": limit, "T_limit_C": cut}
        run = simulate_thermostat_step(Q_on=q_on, **on_condensing)
        if run.T_reduced_mean_C is None or run.runtime_fraction - run.reduced_fraction > 1e-9:
            return None
        T_m = run.T_reduced_mean_C
        cycle = solve_cycle_at_duty(T_m, limit.duty(T_m), fraction)
        if not ran(cycle, T_m) >= limit.duty(T_m) * (1.0 - DUTY_MATCH_RTOL):
            return None
        whole = (run, cycle)
        there = run.limit_stop and run.limit_stop_C is not None and run.limit_stop_C >= cut - TEMPERATURE_TOL_K
        if not there or cut >= min(kw["T_limit_C"], limit.stop_C) - TEMPERATURE_TOL_K:
            return whole
        return onward(
            kw, whole, lambda t: simulate_thermostat_step(Q_on=q_on, **{**on_condensing, "dt": t}), cut, stages_left
        )

    def stage(kw: dict, stages_left: int) -> tuple[InStepRun, dict | None] | None:
        """One stage: the thermostat's arguments ``kw`` (a step, or the rest of one) resolved on the limit."""
        on_limit = {**kw, "duty_limit": limit}
        # Duty the compressor runs at off the condensing limit, and the cycle
        # that delivers it (None: not solved yet for the run the events give).
        q_run = Q_request
        full_result: dict | None = None
        # A running cycle at ``q_run`` solved for a stretch the events took for the limit's.
        spare: dict | None = None
        # Tank temperature ``full_result`` was solved at, and whether it has
        # been solved again at the mean of the run its own duty gives.
        T_full, refined = math.nan, False
        for _ in range(_DUTY_LIMIT_PASSES):
            run = simulate_thermostat_step(Q_on=q_run, **on_limit)
            if run.T_on_mean_C is None:
                return run, full_result
            f_full, f_reduced, q_reduced, load = stretches(run, q_run)
            if f_full > 0.0 and full_result is None:
                assert run.T_full_mean_C is not None
                T_eval = run.T_full_mean_C
                fraction = equivalent_fraction(load, q_run)
                full_result = solve_cycle(T_eval, fraction)
                q_full = ran(full_result, T_eval)
                T_full = T_eval
                T_first = run.T_on_first_C
                if q_full <= 0.0 and spare is None and T_first is not None and T_eval - T_first > CYCLE_RETRY_SPAN_K:
                    retry = solve_cycle(T_first, fraction)
                    if ran(retry, T_first) > 0.0:
                        full_result, q_full, T_full = retry, delivered_tank_heat(retry), T_first
                if q_full <= limit.Q_min and spare is not None:
                    # A cycle did run at ``q_run`` in this step, at another
                    # tank temperature: the run at that duty, as far as the
                    # cycle goes on running.
                    return at_one_duty(kw, q_run, spare, stages_left)
                if q_full <= 0.0:
                    # No cycle at the full duty: the compressor does not run, as without the limit.
                    return no_cycle(kw, full_result)
                if q_full <= limit.Q_min:
                    # Another limit already holds the cycle at or below the
                    # smallest duty found on this one: there is no stretch
                    # between the two to run on, and the step keeps the stop
                    # (at the knee of the request, below this limit's stop).
                    return None
                if q_full != q_run:
                    # The events again, with the duty the cycle delivers.
                    q_run = q_full
                    continue
            elif (
                f_full > 0.0
                and not refined
                and q_run != Q_request
                and run.T_full_mean_C is not None
                and abs(run.T_full_mean_C - T_full) > CYCLE_RETRY_SPAN_K
            ):
                # The cycle delivered another duty than the events took, and
                # the run that duty gives has its mean somewhere else (it
                # warms the tank more slowly): the cycle once more, there.
                refined = True
                again = solve_cycle(run.T_full_mean_C, equivalent_fraction(load, q_run))
                q_again = ran(again, run.T_full_mean_C)
                if q_again > limit.Q_min:
                    full_result, T_full = again, run.T_full_mean_C
                    if q_again != q_run:
                        q_run = q_again
                        continue
            if f_reduced <= 0.0:
                cycle = full_result if full_result is not None else spare
                return (run, cycle) if q_run == Q_request else confirmed(kw, run, cycle, q_run)
            assert run.T_reduced_mean_C is not None
            reduced_result = solve_cycle_at_duty(run.T_reduced_mean_C, q_reduced, equivalent_fraction(load, q_reduced))
            q_delivered = ran(reduced_result, run.T_reduced_mean_C)
            if q_delivered > 0.0 and q_delivered >= q_reduced * (1.0 - DUTY_MATCH_RTOL):
                # The condensing limit governs the stretch, as the events took it.
                if f_full <= 0.0:
                    return run, reduced_result
                return run, merge_running_rows(full_result, f_full, reduced_result, f_reduced, rate_keys)  # type: ignore[arg-type]
            refused = duty_limit_failure is not None and reduced_result.get("failure_reason") == duty_limit_failure
            if q_delivered <= 0.0 and refused:
                # The limit itself refuses the duty its line gives here: the stop stands.
                return None
            # Another limit governs above the knee: it holds the cycle below
            # the duty the condensing limit leaves, or leaves it no operating
            # point.
            if f_full > 0.0:
                # The compressor runs up to the knee at its full duty, and on
                # from there at what the other limit leaves, if anything.
                return at_one_duty(kw, q_run, full_result, stages_left)
            T_first = run.T_on_first_C
            T_mean = run.T_reduced_mean_C
            at_start: dict | None = None
            if spare is None and T_first is not None and q_delivered > 0.0 and T_mean - T_first >= _LINE_SPAN_K:
                # The whole run is the other limit's, and the cycle at its
                # mean says what that limit leaves there: with the cycle at
                # its start, the other limit as a line.
                fraction = equivalent_fraction(load, q_reduced)
                at_start = solve_cycle_at_duty(T_first, limit.duty(T_first), fraction)
                q_start = ran(at_start, T_first)
                if q_start >= limit.duty(T_first) * (1.0 - DUTY_MATCH_RTOL):
                    # The run starts on the condensing limit: on it as far as it governs.
                    handed = condensing_first(kw, q_run, T_first, T_mean, q_delivered, fraction, stages_left)
                    if handed is not None:
                        return handed
                if q_start > limit.Q_min:
                    followed = along_other_limit(kw, T_first, q_start, T_mean, q_delivered, fraction, stages_left)
                    if followed is not None:
                        return followed
            if q_delivered <= limit.Q_min:
                # No operating point at the mean the events gave the run, or
                # one at or below the smallest continuous duty.
                well_below = T_first is not None and T_mean - T_first > CYCLE_RETRY_SPAN_K
                if spare is None and T_first is not None and (well_below or q_delivered > 0.0):
                    # The cycle that starts the run: when the run starts well
                    # below that mean, or when the cycle at the mean runs but
                    # at no more than the smallest duty (the other limit ends
                    # between the two: the start says whether it has ended).
                    # The duty it delivers there, and the events again with
                    # it; the cycle is then solved at the mean of that slower
                    # run.
                    retry = (
                        at_start
                        if at_start is not None
                        else solve_cycle_at_duty(T_first, limit.duty(T_first), equivalent_fraction(load, q_reduced))
                    )
                    if ran(retry, T_first) > limit.Q_min:
                        q_run, full_result, spare = delivered_tank_heat(retry), None, retry
                        continue
                # Nothing runs: the cycle's failure; or the stop stands, as for
                # the full-duty cycle above.
                return no_cycle(kw, reduced_result) if q_delivered <= 0.0 else None
            # The whole run was taken for the limit's and is the other limit's:
            # the events again with the duty that limit leaves.
            q_run, full_result, spare = q_delivered, None, reduced_result
        return at_one_duty(kw, q_run, full_result if full_result is not None else spare, stages_left)

    return stage(kwargs, _DUTY_LIMIT_STAGES)


#: Ratio keys of a boiler row recomputed from merged rates: (key, heat, power).
_MERGED_RATIOS: tuple[tuple[str, str, str], ...] = (
    ("cop_ref [-]", "Q_ref_tank [W]", "E_cmp [W]"),
    ("cop_sys [-]", "Q_ref_tank [W]", "E_tot [W]"),
)
#: Capacity keys a merged row takes from one stretch (see :func:`merge_running_rows`).
_MERGED_CAPACITY_KEYS: tuple[str, ...] = ("capacity_clamped", "capacity_binding", "Q_request [W]")


#: Limit of a cycle at the compressor's speed ceiling, which its row does not
#: name (``capacity_clamped == "max"`` without a ``capacity_binding``).
_SPEED_CEILING = object()


def _limit_of(row: Mapping[str, Any]) -> Any:
    """The limit that holds a running cycle below its request (``None``: none does)."""
    binding = row.get("capacity_binding")
    if binding:
        return binding
    return _SPEED_CEILING if row.get("capacity_clamped") == CAPACITY_CLAMPED_MAX else None


class _MergedRow(dict):
    """A merged running row that remembers the cycles it was merged from.

    ``pieces``: ``(row, run time)`` of every cycle, in the order they ran.
    Merging a merged row with another cycle merges all of them at once
    (:func:`merge_running_rows`), so what a row of several stretches reports
    does not depend on the order in which they were put together.
    """

    pieces: list[tuple[Mapping[str, Any], float]]


def _row_pieces(row: Mapping[str, Any], weight: float) -> list[tuple[Mapping[str, Any], float]]:
    """The cycles ``row`` stands for with their run times, scaled so that they add up to ``weight``."""
    if not isinstance(row, _MergedRow):
        return [(row, weight)]
    total = sum(t for _, t in row.pieces)
    return [(piece, t * weight / total) for piece, t in row.pieces] if total > 0.0 else [(row, weight)]


def merge_running_rows(
    full: Mapping[str, Any], f_full: float, reduced: Mapping[str, Any], f_reduced: float, rate_keys: tuple[str, ...]
) -> dict:
    """Mean running cycle of a step that ran ``f_full`` of it on one cycle and ``f_reduced`` on another.

    Every finite rate in ``rate_keys`` (heat, power, flow) is the
    run-time-weighted mean of the two cycles, so the row times the step's run
    time is the step's energy. ``cop_ref [-]`` and ``cop_sys [-]`` are the
    ratios of the merged heat and power. Every other quantity -- temperatures,
    pressures, speed, efficiencies -- is a state of one machine at one moment
    and has no mean: it is that of the cycle that ran longer (the full-duty
    one at a tie). The capacity keys are those of ``reduced`` (the step ran
    below its request for part of the time) with ``Q_max [W]`` the mean running
    duty, so ``Q_max [W] * runtime_fraction [-]`` is the row's step-average
    heat as on every reduced step. When ``full`` is itself held below the
    request by a limit (it names one in ``capacity_binding``) and ran at least
    as long, the capacity keys are its own: the row names the limit that
    governed most of the run.

    Either argument may itself be a row this function returned (a step
    resolved in several stretches). The cycles it was merged from are then
    merged again, all at once, with their own run times: the state quantities
    are those of the one cycle that ran longest, and the capacity keys are
    those of the limit with the longest run time in total over all the cycles
    under it (the earliest at a tie), taken from the longest-running cycle
    under that limit. Merging two at a time would name the limit of whichever
    pair was merged last.

    A cycle is under the limit it names in ``capacity_binding``. One at the
    compressor's speed ceiling names none: its row has
    ``capacity_clamped == "max"`` only, and the boiler writes
    ``capacity_binding = "compressor"`` when it completes the row
    (:func:`tmhp._boiler_results.complete_capacity_keys`). Such a cycle counts
    as under that limit here, and a merged row it governs carries its keys --
    ``capacity_clamped == "max"`` and no ``capacity_binding`` -- so that the
    boiler completes it the same way.
    """
    pieces = [*_row_pieces(full, f_full), *_row_pieces(reduced, f_reduced)]
    total = sum(t for _, t in pieces)
    # The cycle that ran longest (the earliest at a tie).
    longest = max(range(len(pieces)), key=lambda i: (pieces[i][1], -i))
    row = _MergedRow(pieces[longest][0])
    row.pieces = pieces
    for key in rate_keys:
        values = [piece.get(key) for piece, _ in pieces]
        if all(_finite_number(value) for value in values):
            weighted = [t * float(value) for (_, t), value in zip(pieces, values, strict=True)]  # type: ignore[arg-type]
            row[key] = math.fsum(weighted) / total
    for key, heat_key, power_key in _MERGED_RATIOS:
        heat, power = row.get(heat_key), row.get(power_key)
        if key in row and _finite_number(heat) and _finite_number(power) and float(power) > 0.0:  # type: ignore[arg-type]
            row[key] = float(heat) / float(power)  # type: ignore[arg-type]
    # Run time under each limit, in the order the limits first appear.
    limits = [_limit_of(piece) for piece, _ in pieces]
    time_under: dict[Any, float] = {}
    for limit, (_, t) in zip(limits, pieces, strict=True):
        if limit is not None:
            time_under[limit] = time_under.get(limit, 0.0) + t
    if time_under:
        governing = max(time_under, key=lambda name: time_under[name])
        under = [i for i, limit in enumerate(limits) if limit == governing]
        capacity = pieces[max(under, key=lambda i: (pieces[i][1], -i))][0]
        # The governing cycle's keys, and none of another cycle's: a key it
        # does not have (the binding of a cycle at the speed ceiling) is the
        # boiler's to complete.
        for key in _MERGED_CAPACITY_KEYS:
            row.pop(key, None)
        for key in _MERGED_CAPACITY_KEYS:
            if key in capacity and not (key == "capacity_binding" and not capacity[key]):
                row[key] = capacity[key]
    else:
        # No cycle under a limit: the keys of the last one, as they are.
        for key in _MERGED_CAPACITY_KEYS:
            if key in pieces[-1][0]:
                row[key] = pieces[-1][0][key]
    if _finite_number(row.get("Q_ref_tank [W]")):
        row["Q_max [W]"] = float(row["Q_ref_tank [W]"])
    return row


TANK_BOILING_WARNING = (
    "tank water above 100 °C: the heat pump stops at its upper set point (at the latest at 100 °C) and "
    "the solar collector loops at their storage limit (T_stc_tank_max, 85 °C by default), so this heat "
    "comes from another source: a subsystem that heats the tank without a limit of its own (e.g. a UV "
    "lamp), or a collector storage limit set at 100 °C. "
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


#: ``hp_stop_reason`` of a step the compressor envelope stopped inside
#: (:func:`in_step_stop_reason`).
STOP_CONDENSING_LIMIT: str = "condensing_limit"
#: ``hp_stop_reason`` of a step the boiling cut-out stopped inside.
STOP_BOILING_LIMIT: str = "boiling_limit"
#: Row key of a boiler built with ``condensing_limit_control="reduce_duty"``:
#: the fraction of the step the compressor ran at reduced duty on the
#: condensing limit (``InStepRun.reduced_fraction``; 0 when it did not). It is
#: part of ``runtime_fraction [-]``.
REDUCED_DUTY_FRACTION_KEY: str = "reduced_duty_fraction [-]"


def in_step_stop_reason(run: InStepRun, T_limit_C: float, run_limit_s: float = math.inf) -> str | None:
    """``hp_stop_reason`` of a step: why a cut-out stopped the compressor, ``None`` if none did.

    The stop is the one the step was resolved with (``run.limit_stop_C``;
    ``T_limit_C``, the cut-out the boiler passed, for a run that does not
    carry it). A tank cut-out below the boiling point, or a coil cut-out (a
    finite ``run_limit_s``), is the envelope stop
    (:data:`STOP_CONDENSING_LIMIT`); otherwise it is the boiling point
    (:data:`STOP_BOILING_LIMIT`). With reduced-duty charging the stop that
    acts is the lower of the boiling point and the tank temperature at which
    the condensing limit leaves the smallest continuous duty, which a boiler
    does not know before the step is resolved: the run says which it was.
    Such a stop is a normal end of the run (no ``failure_reason``): the heat
    pump has charged the tank as far as its compressor allows.
    """
    if not run.limit_stop:
        return None
    if run.limit_stop_C is not None:
        T_limit_C = run.limit_stop_C
    envelope = T_limit_C < T_WATER_BOIL_C or math.isfinite(run_limit_s)
    return STOP_CONDENSING_LIMIT if envelope else STOP_BOILING_LIMIT


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
        refrigerant's equation-of-state range.
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
        if refilling and level >= hi - LEVEL_TOL:
            # Carried in open but already at (or above) the upper bound: the
            # previous step latched the valve on its start-temperature draw
            # and solved a smaller one. Close it; a closing time computed
            # from here would be negative.
            refilling = False
            continue
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
    # Neither port runs backwards.
    dV_tank_w_in = max(0.0, dV_tank_w_in)
    dV_tank_w_out = max(0.0, dV_tank_w_out)
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

    def as_flow_state(self) -> dict[str, float]:
        """The flows as the ``flow_state`` dict the source boilers report from.

        ``dV_mix_w_out`` is the delivered mixed water and ``dV_mix_sup_w_in``
        the mains water the valve added to it.
        """
        return {
            "alp": self.alp,
            "dV_mix_w_out": self.dV_mix_w_out,
            "dV_tank_w_out": self.dV_tank_w_out,
            "dV_tank_w_in": self.dV_tank_w_in,
            "dV_mix_sup_w_in": (1.0 - self.alp) * self.dV_mix_w_out,
            "dV_mix_w_out_unmet": self.dV_mix_w_out_unmet,
        }


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
