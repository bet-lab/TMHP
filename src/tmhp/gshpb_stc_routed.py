"""GSHPB with a SolarThermalCollector routed per-timestep to the ground OR tank.

Unifies :class:`~tmhp.gshpb_stc_ground.GSHPB_STC_ground` and
:class:`~tmhp.gshpb_stc_tank.GSHPB_STC_tank`: at **each timestep** a routing
decision sends the collected solar heat to *either* the borehole field (seasonal
/ inter-seasonal ground charging, raising the HP source temperature and COP) *or*
the storage tank (immediate DHW preheat) — **exclusively, never both at once**.

The two destinations enter the dynamic solve through different couplings, so the
routed model gates each per step:

- ``tank``   : net ``Q_STC`` enters the tank energy balance (implicit residual),
               exactly as ``GSHPB_STC_tank``; the ground load is untouched.
- ``ground`` : net solar heat is subtracted from the HP ground load before the
               borehole superposition, exactly as ``GSHPB_STC_ground``; the tank
               balance is untouched.
- ``off``    : no solar; the step reduces to the base GSHPB.

Routing as a control input
--------------------------
The decision is produced by a callable ``solar_router`` evaluated every step, so
it is a natural per-timestep **decision variable for an MPC** — an optimiser can
supply the route in place of the default policy. The default
(:func:`default_solar_router`) is a greedy rule: serve the tank when it is below
its lower setpoint (a DHW need), otherwise dump surplus solar into the ground.
The router is only consulted when solar is actually available (positive
irradiance on the collector plane and the collector schedule on); the chosen
destination is still gated by the collector-loop controller of
:mod:`tmhp._stc_scenarios` (outlet at least ``dT_stc_off`` above that sink, gain
above the pump power, below the tank's ``T_stc_tank_max`` on the tank route,
freeze protection, stagnation lock-out), falling back to ``off`` if it would
not run.

With zero irradiance the collector is inactive and results reduce to the base
GSHPB exactly (only extra reporting columns are added).

Energy accounting
-----------------
The result carries the collector columns of :class:`~tmhp.GSHPB_STC_tank`
(``Q_sol_stc``, ``Q_stc_w_in``, ``Q_stc_w_out``, ``Q_stc_pump_w_out``,
``Q_l_stc``, ``T_stc_*`` …).  While the collector is active every step closes::

    Q_sol_stc = (Q_stc_w_out - Q_stc_w_in) + Q_l_stc
    Q_stc_w_out - Q_stc_w_in = Q_solar_tank + Q_solar_ground

(exclusive routing: one of the two terms is zero).  On the tank route the
collector is reported at the *solved* tank temperature, i.e. the heat that
actually entered the tank balance.  ``E_tot [W]`` includes the collector-loop
pump ``E_stc_pump [W]``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from . import calc_util as cu
from ._run_reset import GroundSourceHeatPumpBoilerRun
from ._stc_scenarios import STCScenario
from .subsystems import SolarThermalCollector, stc_result_columns

if TYPE_CHECKING:
    from .dynamic_context import ControlState, StepContext

_ROUTES = ("ground", "tank", "off")


def default_solar_router(*, T_tank_w: float, T_tank_lower: float, **_: Any) -> str:
    """Greedy exclusive policy: serve the tank when cold, else charge the ground.

    Called only when solar is available. Returns ``"tank"`` while the tank is
    below its lower setpoint (immediate DHW need has priority), otherwise
    ``"ground"`` to bank surplus solar in the borehole field for later COP gain.
    Extra keyword state (``hour_of_day``, ``T_bhe``, ``T0``) is accepted and
    ignored so a custom router can use it without changing this signature.
    """
    return "tank" if T_tank_w < T_tank_lower else "ground"


class GSHPB_STC_routed(STCScenario, GroundSourceHeatPumpBoilerRun):
    """GSHPB + SolarThermalCollector with a per-timestep ground/tank router."""

    def __init__(
        self,
        *,
        stc: SolarThermalCollector,
        solar_router: Callable[..., str] | None = None,
        **kwargs: Any,
    ) -> None:
        self._check_stc(stc)
        # Do NOT pass stc to super().__init__ — keeps self._subsystems empty; the
        # solar coupling is applied explicitly by the per-route hooks below.
        super().__init__(**kwargs)
        self._init_stc(stc)
        self._router = solar_router if solar_router is not None else default_solar_router
        self._q_solar_ground_W: float = 0.0

    def _reset_run_state(self) -> None:
        super()._reset_run_state()
        self._q_solar_ground_W = 0.0

    # ------------------------------------------------------------------
    def _get_activation_flags(self, hour_of_day: float) -> dict[str, bool]:
        return {"stc": self._stc.is_preheat_on(hour_of_day)}

    def _run_subsystems(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        dt: float,
        T_tank_w_in_K: float,
    ) -> dict[str, dict[str, Any]]:
        """Decide the route, then run the chosen loop's controller at its sink."""
        plane = self._stc_plane(ctx, dt)
        available = plane["total"] > 0.0 and ctx.activation_flags.get("stc", False)

        route = "off"
        if available:
            route = self._router(
                hour_of_day=ctx.hour_of_day,
                T_tank_w=cu.K2C(ctx.T_tank_w_K),
                T_tank_lower=self.T_tank_w_lower_bound,
                T_bhe=self.T_bhe,
                T0=cu.K2C(ctx.T0_K),
            )
            if route not in _ROUTES:
                raise ValueError(f"solar_router returned {route!r}; expected one of {_ROUTES}")

        self._q_solar_ground_W = 0.0
        if route == "tank":
            state = self._tank_loop_state(ctx, dt, plane)
            state["E_stc_pump"] = state["E_subsystem"]
            state["Q_solar_ground"] = 0.0
        elif route == "ground":
            state = self._ground_loop_state(ctx, dt, cu.C2K(self.T_bhe), plane)
            self._q_solar_ground_W = state["Q_solar_ground"]
        else:
            # Held off (window closed, no sun, or the router said "off"): the
            # controller still tracks freeze protection and stagnation lock-out.
            state = self._tank_loop_state(ctx, dt, plane, enabled=False)
            state["E_stc_pump"] = 0.0
            state["Q_solar_ground"] = 0.0
        if not state["stc_active"]:
            route = "off"  # infeasible at this sink — do not auto-switch destination
            self._q_solar_ground_W = 0.0
        state["route"] = route
        return {"stc": state}

    def _build_residual_fn(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        dt_s: float,
        T_tank_w_in_K_n: float,
        T_sup_w_K_n: float,
        tank_level: float,
        sub_states: dict[str, dict[str, Any]],
    ) -> Callable[[float], float]:
        """Inject the collector heat into the tank balance only on the tank route."""
        state = sub_states.get("stc", {})
        if state.get("route") != "tank":
            # ground / off: solar does not enter the tank balance — base residual.
            return cast(
                Callable[[float], float],
                super()._build_residual_fn(
                    ctx,
                    ctrl,
                    dt_s,
                    T_tank_w_in_K_n,
                    T_sup_w_K_n,
                    tank_level,
                    sub_states,
                ),
            )
        return cast(
            Callable[[float], float],
            self._tank_residual(
                ctx,
                ctrl,
                dt_s,
                T_tank_w_in_K_n,
                T_sup_w_K_n,
                tank_level,
                lambda T: self._tank_loop_heat(ctx, state, T)[0],
            ),
        )

    def _ground_injection_W(self) -> float:
        """Solar heat the collector loop injects into the borehole field this step [W]."""
        return self._q_solar_ground_W

    def _augment_results(
        self,
        r: dict[str, Any],
        ctx: StepContext,
        ctrl: ControlState,
        sub_states: dict[str, dict[str, Any]],
        T_solved_K: float,
    ) -> dict[str, Any]:
        state = sub_states.get("stc", {})
        route = state.get("route", "off")
        if route == "tank":
            # The tank balance evaluated the collector at the solved tank
            # temperature; report that evaluation, not the start-of-step probe.
            cols, e_pump, active = self._tank_loop_columns(ctx, state, T_solved_K)
        else:
            sr = state.get("stc_result", {})
            active = bool(state.get("stc_active", False))
            e_pump = float(state.get("E_stc_pump", 0.0))
            inlet = sr.get("T_stc_w_in_K", ctx.T_tank_w_K)
            cols = stc_result_columns(sr, inlet, self._stc.loop_flow, 1.0 if active else 0.0)
            cols["Q_solar_tank [W]"] = 0.0
            if route == "off":
                self._commit_controller(state, pump_on=False)
        r["solar_route [-]"] = route
        r["Q_solar_ground [W]"] = float(state.get("Q_solar_ground", 0.0))
        # The collector-loop pump is electricity the system draws.
        r["E_tot [W]"] = float(r.get("E_tot [W]", 0.0)) + e_pump
        r.update(self._solar_columns(ctx, state, active, e_pump))
        r.update(cols)
        return r
