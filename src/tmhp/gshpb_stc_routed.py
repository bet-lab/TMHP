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
irradiance and the collector schedule on); the chosen destination is still gated
by a net-heat-gain feasibility check (collector outlet hotter than the sink),
falling back to ``off`` if infeasible.

With zero irradiance the collector is inactive and results reduce to the base
GSHPB exactly (only extra reporting columns are added).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from . import calc_util as cu
from .constants import c_w, rho_w
from .ground_source_heat_pump_boiler import GroundSourceHeatPumpBoiler
from .subsystems import SolarThermalCollector

if TYPE_CHECKING:
    import numpy as np

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


class GSHPB_STC_routed(GroundSourceHeatPumpBoiler):
    """GSHPB + SolarThermalCollector with a per-timestep ground/tank router."""

    def __init__(
        self,
        *,
        stc: SolarThermalCollector,
        solar_router: Callable[..., str] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(stc, SolarThermalCollector):
            raise TypeError(f"stc must be a SolarThermalCollector instance, got {type(stc)!r}")
        # Do NOT pass stc to super().__init__ — keeps self._subsystems empty; the
        # solar coupling is applied explicitly by the per-route hooks below.
        super().__init__(**kwargs)
        self._stc: SolarThermalCollector = stc
        self.stc = stc  # enables I_DN/I_dH schedules in analyze_dynamic
        self._router = solar_router if solar_router is not None else default_solar_router
        self._q_solar_ground_W: float = 0.0

    # ------------------------------------------------------------------
    def _needs_solar_input(self) -> bool:
        return True

    def _get_activation_flags(self, hour_of_day: float) -> dict[str, bool]:
        return {"stc": self._stc.is_preheat_on(hour_of_day)}

    def _run_subsystems(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        dt: float,
        T_tank_w_in_K: float,
    ) -> dict[str, dict[str, Any]]:
        """Decide the route, evaluate the collector at the route's inlet, store state."""
        has_sun = (ctx.I_DN + ctx.I_dH) > 0.0
        available = has_sun and ctx.activation_flags.get("stc", False)

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
        stc_active = False
        e_pump = 0.0
        # Inlet differs by destination: tank water for the tank route, borehole
        # wall for the ground route. Default (off) probes at the tank for reporting.
        inlet_K = ctx.T_tank_w_K if route != "ground" else cu.C2K(self.T_bhe)

        if route in ("tank", "ground"):
            probe = self._stc.calc_performance(
                I_DN_stc=ctx.I_DN, I_dH_stc=ctx.I_dH, T_stc_w_in_K=inlet_K, T0_K=ctx.T0_K, is_active=True,
            )
            if probe["T_stc_w_out_K"] > inlet_K:  # net heat gain at the chosen sink
                stc_active = True
                stc_result = probe
                e_pump = self._stc.E_stc_pump
                if route == "ground":
                    self._q_solar_ground_W = probe["Q_stc_w_out"] - probe["Q_stc_w_in"]
            else:
                route = "off"  # infeasible at this sink — do not auto-switch destination
                stc_result = self._stc.calc_performance(
                    I_DN_stc=ctx.I_DN, I_dH_stc=ctx.I_dH, T_stc_w_in_K=inlet_K, T0_K=ctx.T0_K, is_active=False,
                )
        else:
            stc_result = self._stc.calc_performance(
                I_DN_stc=ctx.I_DN, I_dH_stc=ctx.I_dH, T_stc_w_in_K=inlet_K, T0_K=ctx.T0_K, is_active=False,
            )

        return {
            "stc": {
                "route": route,
                "stc_active": stc_active,
                "stc_result": stc_result,
                "T_tank_w_in_override_K": None,
                # Tank route: pump electricity enters the tank balance (as STC_tank).
                # Ground route: pump tracked separately (as STC_ground), not in tank.
                "E_subsystem": e_pump if route == "tank" else 0.0,
                "Q_contribution": 0.0,
                "E_stc_pump": e_pump,
                "Q_solar_ground": self._q_solar_ground_W,
            }
        }

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
        """Inject ``Q_STC`` into the tank balance only on the tank route."""
        if sub_states.get("stc", {}).get("route") != "tank":
            # ground / off: solar does not enter the tank balance — base residual.
            return cast(
                Callable[[float], float],
                super()._build_residual_fn(
                    ctx, ctrl, dt_s, T_tank_w_in_K_n, T_sup_w_K_n, tank_level, sub_states,
                ),
            )

        stc_active: bool = sub_states.get("stc", {}).get("stc_active", False)
        E_pump: float = self._stc.E_stc_pump if stc_active else 0.0

        def residual(T_cand_K: float) -> float:
            stc_r = self._stc.calc_performance(
                I_DN_stc=ctx.I_DN, I_dH_stc=ctx.I_dH, T_stc_w_in_K=T_cand_K, T0_K=ctx.T0_K, is_active=stc_active,
            )
            Q_stc_net: float = stc_r["Q_stc_w_out"] - stc_r["Q_stc_w_in"]

            den: float = max(1e-6, T_cand_K - T_sup_w_K_n)
            alp: float = min(1.0, max(0.0, (self.T_mix_w_out_K - T_sup_w_K_n) / den))
            dV_out: float = alp * ctx.dV_mix_w_out
            dV_in: float = dV_out if ctrl.dV_tank_w_in_ctrl is None else ctrl.dV_tank_w_in_ctrl

            Q_flow: float = c_w * rho_w * (dV_in * T_tank_w_in_K_n - dV_out * T_cand_K)
            Q_loss: float = self.UA_tank_wall * (T_cand_K - self.T_sur_K)
            C_curr: float = self.C_tank * max(0.001, ctx.tank_level)
            C_next: float = self.C_tank * max(0.001, tank_level)

            r: float = (
                C_next * T_cand_K
                - C_curr * ctx.T_tank_w_K
                - dt_s * (ctrl.Q_heat_source + E_pump + Q_stc_net + Q_flow - Q_loss)
            )
            return r / self.C_tank

        return residual

    def _compute_bhe_superposition(
        self,
        n: int,
        time_arr: np.ndarray,
        hp_result: dict[str, Any],
        hp_is_on: bool,
    ) -> None:
        """Inject solar heat into the ground only on the ground route (q_sol != 0)."""
        q_sol = self._q_solar_ground_W
        if q_sol != 0.0:
            base_q = hp_result.get("Q_bhe [W]", 0.0) if hp_is_on else 0.0
            hp_result["Q_bhe [W]"] = base_q - q_sol  # extraction-positive convention
            hp_is_on = True  # drive the ground response even when the HP is off
        super()._compute_bhe_superposition(n, time_arr, hp_result, hp_is_on)

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
        sr = state.get("stc_result", {})
        q_net = float(sr.get("Q_stc_w_out", 0.0)) - float(sr.get("Q_stc_w_in", 0.0))
        r["solar_route [-]"] = route
        r["stc_active [-]"] = bool(state.get("stc_active", False))
        r["Q_solar_tank [W]"] = q_net if route == "tank" else 0.0
        r["Q_solar_ground [W]"] = float(state.get("Q_solar_ground", 0.0))
        r["E_stc_pump [W]"] = float(state.get("E_stc_pump", 0.0))
        if "T_stc_w_out_K" in sr:
            r["T_stc_w_out [°C]"] = cu.K2C(float(sr["T_stc_w_out_K"]))
        return r
