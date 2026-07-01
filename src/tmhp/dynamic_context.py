"""Shared dynamic simulation context and control helpers.

Provides reusable dataclasses and pure functions that form the
backbone of time-stepping heat-pump simulations.  Extracted from
``AirSourceHeatPumpBoiler`` so that ``GroundSourceHeatPumpBoiler``
and future models can share the same infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from .constants import c_w, rho_w

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "ControlState",
    "DynamicState",
    "StepContext",
    "Subsystem",
    "SubsystemExergy",
    "check_hp_schedule_active",
    "determine_heat_source_on_off",
    "determine_tank_refill_flow",
    "tank_mass_energy_residual",
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
        Heat-pump on/off state from the previous step (cycling hysteresis).
    dV_tank_w_out_prev : float
        Previous step's tank draw-off flow [m³/s]; consumed by the next
        step's refill decision.
    """

    T_tank_w_K: float
    tank_level: float
    is_refilling: bool
    hp_is_on_prev: bool
    dV_tank_w_out_prev: float


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
    if T_tank_w_C <= T_lower:
        is_on: bool = True
    elif T_tank_w_C >= T_upper:
        is_on = False
    else:
        is_on = is_on_prev

    return is_on and check_hp_schedule_active(
        hour_of_day,
        on_schedule,
    )


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
            req: float = (1.0 - lv) * V_tank_full
            if dV_tank_w_in_refill * dt <= req:
                dV_tank_w_in = dV_tank_w_in_refill
    elif tank_always_full:
        return None, is_refilling  # sentinel
    else:
        lo: float = tank_level_lower_bound
        hi: float = tank_level_upper_bound
        if not is_refilling and lv < lo - 1e-6:
            is_refilling = True
        if is_refilling:
            req = (hi - lv) * V_tank_full
            if dV_tank_w_in_refill * dt <= req:
                dV_tank_w_in = dV_tank_w_in_refill
            chk: float = lv + dV_tank_w_in * dt / V_tank_full
            if chk >= hi - 1e-6:
                is_refilling = False

    return dV_tank_w_in, is_refilling


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

    Subsystem energy contributions and tank-inlet temperature
    overrides are read from ``sub_states``.

    Parameters
    ----------
    x : list[float]
        ``[T_next_K, level_next]``.
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

    den: float = max(1e-6, T_next - T_sup_w_K)
    alp: float = min(
        1.0,
        max(0.0, (T_mix_w_out_K - T_sup_w_K) / den),
    )
    dV_tank_w_out: float = alp * ctx.dV_mix_w_out
    dV_tank_w_in: float = dV_tank_w_out if ctrl.dV_tank_w_in_ctrl is None else ctrl.dV_tank_w_in_ctrl

    r_mass: float = level_next - ctx.tank_level - (dV_tank_w_in - dV_tank_w_out) * dt / V_tank_full

    C_curr: float = C_tank * max(0.001, ctx.tank_level)
    C_next: float = C_tank * max(0.001, level_next)
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
