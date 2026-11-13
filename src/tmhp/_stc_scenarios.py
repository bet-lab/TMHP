"""Collector-loop logic shared by the STC scenario classes (ASHPB and GSHPB).

The scenario classes combine a :class:`~tmhp.subsystems.SolarThermalCollector`
with a boiler. Their ASHPB and GSHPB versions used to be two near-identical
copies; the solar side lives here once and the classes are thin compositions
``class ASHPB_STC_tank(STCTankLoop, AirSourceHeatPumpBoiler)``. The boilers
themselves are not changed: the mixins use the boilers' template hooks
(``_run_subsystems``, ``_build_residual_fn``, ``_augment_results``,
``_postprocess``).

Placements
----------
- :class:`STCPreheat` — the collector sits in the mains line feeding the tank
  (no pump; the mains pressure drives it). Its flow is the tank's make-up
  flow, solved implicitly with the tank temperature: the mixing valve's cold
  side takes mains water directly, so only the water that enters the tank
  passes the collector, and every watt the collector reports reaches the tank
  through its preheated inflow.
- :class:`STCTankLoop` — a pumped collector loop charging the tank.
- :class:`STCGroundLoop` helpers — a pumped loop charging the borehole field
  (used by ``GSHPB_STC_ground`` and ``GSHPB_STC_routed``).

Collector-loop controller (pumped loops)
----------------------------------------
A differential controller as in solar domestic-hot-water systems. Its
collector sensor reads the idle collector (the stagnation temperature at the
step's irradiance) while the pump is stopped and the running outlet while it
runs:

- a stopped pump starts when the idle collector is at least ``dT_stc_on``
  above the sink, provided the run would hold (running outlet at least
  ``dT_stc_off`` above the sink: the collector has no heat capacity here, so
  a run the stop rule would end at once is not started);
- a running pump runs on while its outlet stays at least ``dT_stc_off`` above
  the sink;
- the heat it collects must exceed the pump's electricity;
- the outlet stays below ``T_stc_boil`` (boiling protection);
- for a tank sink: the tank is below the storage high limit
  ``T_stc_tank_max`` (85 °C by default). Within the step the loop stops when
  the tank reaches the limit: the tank balance carries the solar heat times a
  run fraction that falls from 1 to 0 over the last :data:`TANK_LIMIT_RAMP_K`
  above the limit, so the step ends at the limit with the share of the step
  the loop ran (``stc_runtime_fraction [-]``). After that the loop starts
  again only once the tank is :data:`TANK_LIMIT_RESTART_K` below the limit.

With the default ISO test flow the running outlet is only a few kelvin above
the inlet, so the stop threshold governs both ways (an idle collector 6 K
above the tank would give a 0.3 K rise); the start threshold matters for
low-flow loops, where the two are comparable.

Protections
-----------
- **Freeze** (all placements). The collector carries water, so the system is
  a drain-back one: while the idle collector is below ``T_stc_freeze`` (4 °C)
  the loop does not run and the collector is drained
  (``stc_freeze_protected [-]``). The same holds while the sink a pumped loop
  would draw from is below 0 °C (a borehole field driven below freezing):
  water cannot arrive that cold. ``T_stc_freeze=None`` declares an antifreeze
  loop and switches the protection off.
- **Stagnation lock-out** (pumped loops). A collector that a protection holds
  off (storage limit, boiling protection, clock window, a router's ``"off"``)
  idles at its stagnation temperature. At or above ``T_stc_lockout`` (130 °C)
  the loop is locked out and is not started until the idle collector has
  cooled :data:`STAGNATION_RELEASE_K` below it
  (``stc_stagnation_lockout [-]``). The collector has no heat capacity in
  this model, so its idle temperature is the equilibrium one. The mains-line
  placement has no pump or controller and is not locked out; its idle
  temperature is reported as ``T_stc_stag [°C]``.

Controller state
----------------
The pump latch, the storage-limit hold and the lock-out are carried from step
to step in ``DynamicState.subsystem_state["stc"]`` (see
:class:`tmhp.dynamic_context.Subsystem`); the decision is taken at the
step-start temperatures. A run starts with the pump stopped and no hold, so
two runs with the same inputs give the same result.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pandas as pd

from . import calc_util as cu
from .constants import c_w, rho_w
from .dynamic_context import SUBSYSTEM_STATE_KEY, tank_mass_energy_residual
from .subsystems import SolarThermalCollector, stc_result_columns, warn_if_uv_ignored
from .thermodynamics import calc_energy_flow

if TYPE_CHECKING:
    from .dynamic_context import ControlState, StepContext, Subsystem

#: Width [K] above ``T_stc_tank_max`` over which the collector loop's share of
#: the step falls from 1 to 0 in the implicit tank balance. A continuous ramp
#: (instead of a jump) keeps both tank solvers (``brentq`` for ASHPB, ``fsolve``
#: for GSHPB) on a smooth function; the tank overshoots the limit by at most
#: this much.
TANK_LIMIT_RAMP_K: float = 0.05

#: The loop restarts only below ``T_stc_tank_max - SINK_LIMIT_TOL_K`` at the
#: step start. The limit itself is resolved inside the step; this tolerance
#: keeps a tank that ended the previous step at the limit (within the ramp)
#: from restarting on round-off, so the on/off flag is the same on every
#: platform.
SINK_LIMIT_TOL_K: float = 0.1

#: After the tank has reached ``T_stc_tank_max`` the loop starts again only
#: once the tank is this far below the limit: the switch-on hysteresis of a
#: solar controller's storage maximum (2 K is the usual fixed value).
TANK_LIMIT_RESTART_K: float = 2.0

#: A stagnation lock-out is released once the idle collector has cooled this
#: far below ``T_stc_lockout`` (130 → 120 °C with the default; the hysteresis
#: of a controller's collector emergency shutdown).
STAGNATION_RELEASE_K: float = 10.0

#: Freezing point of the water the collector loops carry [K].
WATER_FREEZE_K: float = 273.15

#: Collector-loop controller state at the start of a run
#: (``DynamicState.subsystem_state["stc"]``).
STC_CONTROLLER_STATE0: dict[str, bool] = {"pump_on": False, "limit_hold": False, "lockout": False}


class STCScenario:
    """Construction and solar hooks common to every STC scenario."""

    _stc: SolarThermalCollector
    # Provided by the boiler the mixin is combined with.
    stc: SolarThermalCollector | None
    T_mix_w_out_K: float
    C_tank: float
    UA_tank_wall: float
    V_tank_full: float
    T_sur_K: float
    # Subsystem states of the previous step (set by the host's step(), or kept
    # here across a run when the host has no DynamicState).
    _subsystem_state_in: dict

    def _init_stc(self, stc: SolarThermalCollector) -> None:
        self._stc = stc
        # Expose as self.stc so analyze_dynamic() passes the I_DN/I_dH schedules.
        self.stc = stc
        warn_if_uv_ignored(self)

    @staticmethod
    def _check_stc(stc: Any) -> None:
        if not isinstance(stc, SolarThermalCollector):
            raise TypeError(f"stc must be a SolarThermalCollector instance, got {type(stc)!r}")

    def _needs_solar_input(self) -> bool:
        return True

    def _stc_plane(self, ctx: StepContext, dt: float) -> dict:
        """Irradiance on the collector plane, averaged over the step."""
        return self._stc.plane_irradiance(ctx.I_DN, ctx.I_dH, ctx.current_time_s, dt)

    def _stc_eval(self, ctx: StepContext, plane: dict, T_in_K: float, *, active: bool, dV: float | None = None) -> dict:
        return self._stc.calc_performance(
            ctx.I_DN, ctx.I_dH, T_in_K, ctx.T0_K, dV_stc=dV, is_active=active, plane=plane
        )

    # ------------------------------------------------------------------
    # Controller state carried between steps
    # ------------------------------------------------------------------

    def _reset_run_state(self) -> None:
        """Start a run with an empty controller state.

        Called by hosts that keep the subsystem state for a run themselves
        (``GroundSourceHeatPumpBoilerRun.analyze_dynamic``); a ``step()``-driven
        host hands the state in through its ``DynamicState`` instead.
        """
        parent = getattr(super(), "_reset_run_state", None)
        if parent is not None:
            parent()
        self._subsystem_state_in = {}

    def _controller_in(self) -> dict[str, bool]:
        """Controller state at the end of the previous step (run start: all off)."""
        previous = getattr(self, "_subsystem_state_in", None) or {}
        return {**STC_CONTROLLER_STATE0, **previous.get("stc", {})}

    def _commit_controller(self, state: dict, pump_on: bool) -> None:
        """Write the controller state the next step starts from into ``state``.

        ``state`` is this step's subsystem dict; the flags are those of the
        step-start decision and ``pump_on`` says whether the pump was running
        at the end of the step. Hosts with a ``DynamicState`` gather the
        entry from the dict; the others read it back from
        ``_subsystem_state_in``, which is replaced, not modified.
        """
        carried = {
            "pump_on": bool(pump_on),
            "limit_hold": bool(state.get("limit_hold", False)),
            "lockout": bool(state.get("lockout", False)),
        }
        state[SUBSYSTEM_STATE_KEY] = carried
        previous = getattr(self, "_subsystem_state_in", None) or {}
        self._subsystem_state_in = {**previous, "stc": carried}

    def _freeze_protected(self, T_stag_K: float, T_in_K: float | None = None) -> bool:
        """Whether the freeze protection keeps the collector drained (no flow).

        True while the idle collector is below ``T_stc_freeze``, or the
        liquid that would enter it (``T_in_K``) is below 0 °C: the loop is
        filled with water, which cannot arrive colder than that.
        """
        limit = self._stc.T_stc_freeze
        if limit is None:
            return False
        return bool(cu.K2C(T_stag_K) < limit or (T_in_K is not None and T_in_K < WATER_FREEZE_K))

    def _loop_decision(
        self,
        ctx: StepContext,
        plane: dict,
        T_in_K: float,
        *,
        sink_max_C: float | None = None,
        enabled: bool = True,
    ) -> tuple[bool, dict, dict[str, bool]]:
        """Controller decision for a pumped loop at sink temperature ``T_in_K``.

        ``enabled`` is ``False`` when something outside the controller keeps
        the loop off (clock window, a router that sends the heat nowhere).

        Returns ``(runs, probe, flags)``: ``probe`` is the collector running
        at the loop flow from ``T_in_K``; ``flags`` are ``freeze`` (drained
        below ``T_stc_freeze``), ``limit_hold`` (tank at its storage limit,
        or not yet :data:`TANK_LIMIT_RESTART_K` below it after reaching it)
        and ``lockout`` (stagnation lock-out).
        """
        stc = self._stc
        previous = self._controller_in()
        probe = self._stc_eval(ctx, plane, T_in_K, active=True)
        gain = probe["Q_stc_w_out"] - probe["Q_stc_w_in"]
        T_stag_K = probe["T_stc_stag_K"]
        rise = probe["T_stc_w_out_K"] - T_in_K

        freeze = self._freeze_protected(T_stag_K, T_in_K)
        limit_hold = False
        if sink_max_C is not None:
            margin = TANK_LIMIT_RESTART_K if previous["limit_hold"] else SINK_LIMIT_TOL_K
            limit_hold = bool(cu.K2C(T_in_K) >= sink_max_C - margin)
        boiling = bool(probe["T_stc_pump_w_out_K"] >= cu.C2K(stc.T_stc_boil))

        lockout = False
        if stc.T_stc_lockout is not None:
            T_stag_C = cu.K2C(T_stag_K)
            lockout = bool(previous["lockout"] and T_stag_C >= stc.T_stc_lockout - STAGNATION_RELEASE_K)
            # A collector that a protection holds off idles at its stagnation temperature.
            if (not enabled or limit_hold or boiling) and T_stag_C >= stc.T_stc_lockout:
                lockout = True

        if previous["pump_on"]:
            differential = rise >= stc.dT_stc_off
        else:
            differential = T_stag_K - T_in_K >= stc.dT_stc_on and rise >= stc.dT_stc_off
        runs = bool(
            enabled
            and plane["total"] > 0.0
            and differential
            and np.isfinite(gain)
            and gain > max(0.0, stc.E_stc_pump)
            and not (freeze or limit_hold or boiling or lockout)
        )
        return runs, probe, {"freeze": freeze, "limit_hold": limit_hold, "lockout": lockout}

    # ------------------------------------------------------------------
    # Tank loop (shared by STCTankLoop and the tank route of GSHPB_STC_routed)
    # ------------------------------------------------------------------

    def _tank_loop_state(self, ctx: StepContext, dt: float, plane: dict | None = None, *, enabled: bool = True) -> dict:
        """Step state of a pumped loop charging the tank (decision at the step start)."""
        plane = self._stc_plane(ctx, dt) if plane is None else plane
        runs, probe, flags = self._loop_decision(
            ctx, plane, ctx.T_tank_w_K, sink_max_C=self._stc.T_stc_tank_max, enabled=enabled
        )
        result = probe if runs else self._stc_eval(ctx, plane, ctx.T_tank_w_K, active=False)
        return {
            "stc_active": runs,
            "stc_result": result,
            "plane": plane,
            "T_tank_w_in_override_K": None,
            "E_subsystem": self._stc.E_stc_pump if runs else 0.0,
            "Q_contribution": 0.0,
            **flags,
        }

    @staticmethod
    def _limit_share(value_C: float, limit_C: float) -> float:
        """Run share that falls from 1 to 0 over :data:`TANK_LIMIT_RAMP_K` above ``limit_C``."""
        return float(min(1.0, max(0.0, 1.0 - (value_C - limit_C) / TANK_LIMIT_RAMP_K)))

    def _tank_limit_share(self, T_K: float) -> float:
        """Share of the step the loop runs when the step ends at tank temperature ``T_K``."""
        return self._limit_share(cu.K2C(T_K), self._stc.T_stc_tank_max)

    def _boil_share(self, result: dict) -> float:
        """Share of the step the loop runs before its outlet reaches ``T_stc_boil``.

        The step-start decision only admits a loop whose outlet is below the
        boiling protection; the outlet rises with the end-of-step inlet, so the
        protection is applied again within the step, like the storage limit.
        """
        return self._limit_share(cu.K2C(result["T_stc_pump_w_out_K"]), self._stc.T_stc_boil)

    def _tank_loop_heat(self, ctx: StepContext, state: dict, T_K: float) -> tuple[float, float, dict | None]:
        """``(heat into the tank [W], run share, running result)`` at end-of-step tank ``T_K``.

        The loop draws tank water at ``T_K`` (implicit in the tank temperature)
        and returns it heated by the collector and the pump; it stops (share 0)
        when its outlet is no warmer than the tank, and runs the share of the
        step before the tank reaches ``T_stc_tank_max`` or its outlet reaches
        ``T_stc_boil``, whichever comes first.
        """
        if not state.get("stc_active", False):
            return 0.0, 0.0, None
        r = self._stc_eval(ctx, state["plane"], T_K, active=True)
        gain = r["Q_stc_w_out"] - r["Q_stc_w_in"]
        if not gain > 0.0:
            return 0.0, 0.0, r
        share = min(self._tank_limit_share(T_K), self._boil_share(r))
        return share * (gain + self._stc.E_stc_pump), share, r

    def _tank_residual(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        dt_s: float,
        T_tank_w_in_K_n: float,
        T_sup_w_K_n: float,
        tank_level: float,
        heat_fn,
        override_fn=None,
    ):
        """Tank residual of the boiler with an extra heat rate ``heat_fn(T)`` [W] and inlet override."""

        def residual(T_cand_K: float) -> float:
            # fsolve (GSHPB) passes a 1-element array, brentq (ASHPB) a float.
            T_cand_K = float(np.asarray(T_cand_K, dtype=float).reshape(-1)[0])
            sub = {
                "Q_contribution": heat_fn(T_cand_K) if heat_fn is not None else 0.0,
                "T_tank_w_in_override_K": override_fn(T_cand_K) if override_fn is not None else None,
            }
            return tank_mass_energy_residual(
                [T_cand_K, tank_level],
                ctx,
                ctrl,
                dt_s,
                T_tank_w_in_K_n,
                T_sup_w_K_n,
                self.T_mix_w_out_K,
                self.C_tank,
                self.UA_tank_wall,
                self.V_tank_full,
                {"stc": cast("Subsystem", None)},
                {"stc": sub},
                T_sur_K=self.T_sur_K,
            )[0]

        return residual

    def _tank_loop_columns(self, ctx: StepContext, state: dict, T_solved_K: float) -> tuple[dict, float, bool]:
        """Result columns of a tank loop at the solved tank temperature: ``(columns, E_pump, active)``.

        Also commits the controller state: the pump is on at the end of the
        step when the loop ran the whole step (a loop stopped at a limit
        within the step ends it stopped).
        """
        heat, share, running = self._tank_loop_heat(ctx, state, T_solved_K)
        active = share > 0.0 and running is not None
        result = (
            running if active and running is not None else self._stc_eval(ctx, state["plane"], T_solved_K, active=False)
        )
        cols = stc_result_columns(result, T_solved_K, self._stc.loop_flow, share if active else 0.0)
        gain = share * (result["Q_stc_w_out"] - result["Q_stc_w_in"]) if active else 0.0
        e_pump = share * self._stc.E_stc_pump if active else 0.0
        cols["Q_solar_tank [W]"] = gain
        self._commit_controller(state, pump_on=active and share >= 1.0)
        return cols, e_pump, active

    # ------------------------------------------------------------------
    # Ground loop
    # ------------------------------------------------------------------

    def _ground_loop_state(
        self, ctx: StepContext, dt: float, T_ground_K: float, plane: dict | None = None, *, enabled: bool = True
    ) -> dict:
        """Step state of a pumped loop charging the borehole field from ``T_ground_K``.

        The loop has no in-step limit, so the controller state is committed
        here: the pump ends the step as it ran it.
        """
        plane = self._stc_plane(ctx, dt) if plane is None else plane
        runs, probe, flags = self._loop_decision(ctx, plane, T_ground_K, enabled=enabled)
        result = probe if runs else self._stc_eval(ctx, plane, T_ground_K, active=False)
        state = {
            "stc_active": runs,
            "stc_result": result,
            "plane": plane,
            "T_tank_w_in_override_K": None,
            "E_subsystem": 0.0,
            "Q_contribution": 0.0,
            "E_stc_pump": self._stc.E_stc_pump if runs else 0.0,
            "Q_solar_ground": (probe["Q_stc_w_out"] - probe["Q_stc_w_in"]) if runs else 0.0,
            **flags,
        }
        self._commit_controller(state, pump_on=runs)
        return state

    # ------------------------------------------------------------------
    # Exergy (tank and preheat placements)
    # ------------------------------------------------------------------

    def _stc_exergy(self, df: pd.DataFrame, *, tank_boundary: bool) -> pd.DataFrame:
        """STC exergy columns; ``tank_boundary`` adds the loop's in/out flows to ``Xc_tank``.

        .. math::

            \\dot X_{c,stc} = \\dot X_{sol} + \\dot X_{stc,w,in} + \\dot E_{pump}
                - \\dot X_{stc,pump,w,out} - \\dot X_{l,stc}

        with step-average flows (``dV_stc`` is the run share times the loop
        flow). The preheat placement needs no tank correction: its preheated
        inflow is the tank inlet ``T_tank_w_in``.
        """
        from .thermodynamics import calc_exergy_flow

        if "T_stc_w_in [°C]" not in df.columns or "T_stc_w_out [°C]" not in df.columns:
            return df
        T0_K = cu.C2K(df["T0 [°C]"])
        T_stc_w_in_K = cu.C2K(df["T_stc_w_in [°C]"])
        T_stc_w_out_K = cu.C2K(df["T_stc_w_out [°C]"])
        T_stc_pump_w_out_K = cu.C2K(df.get("T_stc_pump_w_out [°C]", df["T_stc_w_out [°C]"]))
        T_stc_K = cu.C2K(df["T_stc [°C]"])
        G_stc = c_w * rho_w * df["dV_stc [m3/s]"].fillna(0)

        df["X_stc_w_in [W]"] = calc_exergy_flow(G_stc, T_stc_w_in_K, T0_K)
        df["X_stc_w_out [W]"] = calc_exergy_flow(G_stc, T_stc_w_out_K, T0_K)
        df["X_stc_pump_w_out [W]"] = calc_exergy_flow(G_stc, T_stc_pump_w_out_K, T0_K)
        E_pump = df["E_stc_pump [W]"].fillna(0)
        df["X_stc_pump [W]"] = E_pump
        df["X_l_stc [W]"] = df["Q_l_stc [W]"].fillna(0) * (1 - T0_K / T_stc_K.replace(0, np.nan))

        active = (
            df["stc_active [-]"].fillna(False).astype(bool)
            if "stc_active [-]" in df.columns
            else pd.Series(False, index=df.index)
        )
        Xc_raw = (
            df["X_sol_stc [W]"].fillna(0)
            + df["X_stc_w_in [W]"].fillna(0)
            + E_pump
            - df["X_stc_pump_w_out [W]"].fillna(0)
            - df["X_l_stc [W]"].fillna(0)
        )
        df["Xc_stc [W]"] = np.where(active, Xc_raw, 0.0)
        df["X_tot [W]"] = df["X_tot [W]"].add(E_pump, fill_value=0)
        if tank_boundary:
            X_in_tank_add = df["X_stc_pump_w_out [W]"].fillna(0)
            X_out_tank_add = df["X_stc_w_in [W]"].fillna(0)
            df["Xc_tank [W]"] = df["Xc_tank [W]"].add(X_in_tank_add - X_out_tank_add, fill_value=0)
        return df

    def _solar_columns(self, ctx: StepContext, state: dict, active: bool, e_pump: float) -> dict:
        """Status and input columns: ``state`` is the step's subsystem dict (protection flags)."""
        return {
            "stc_active [-]": bool(active),
            "stc_freeze_protected [-]": bool(state.get("freeze", False)),
            "stc_stagnation_lockout [-]": bool(state.get("lockout", False)),
            "I_DN_stc [W/m2]": ctx.I_DN,
            "I_dH_stc [W/m2]": ctx.I_dH,
            "E_stc_pump [W]": e_pump,
        }


class STCTankLoop(STCScenario):
    """Pumped collector loop charging the tank (``ASHPB_STC_tank``, ``GSHPB_STC_tank``).

    The loop draws tank water, heats it in the collector and returns it with
    the pump's heat; the collector is evaluated at the end-of-step tank
    temperature inside the implicit tank balance. ``E_tot [W]`` includes the
    pump's step-average power. Per step::

        Q_sol_stc = (Q_stc_w_out - Q_stc_w_in) + Q_l_stc
        Q_stc_w_out - Q_stc_w_in = Q_solar_tank     (heat the tank receives besides E_stc_pump)

    The loop is governed by the collector-loop controller (see
    :mod:`tmhp._stc_scenarios`), not by a clock window: differential with a
    pump latch, storage limit with a 2 K restart band, boiling and freeze
    protection, stagnation lock-out (``stc_freeze_protected [-]``,
    ``stc_stagnation_lockout [-]``).
    """

    def __init__(self, *, stc: SolarThermalCollector, **kwargs: Any) -> None:
        self._check_stc(stc)
        # Do NOT pass stc to super().__init__ — keeps self._subsystems empty.
        super().__init__(**kwargs)  # type: ignore[call-arg]
        self._init_stc(stc)

    def _get_activation_flags(self, hour_of_day: float) -> dict[str, bool]:
        return {"stc": True}

    def _run_subsystems(self, ctx: StepContext, ctrl: ControlState, dt: float, T_tank_w_in_K: float) -> dict:
        return {"stc": self._tank_loop_state(ctx, dt)}

    def _build_residual_fn(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        dt_s: float,
        T_tank_w_in_K_n: float,
        T_sup_w_K_n: float,
        tank_level: float,
        sub_states: dict,
    ):
        """Fully implicit residual: the collector heat is evaluated at each candidate ``T^{n+1}``."""
        state = sub_states.get("stc", {})
        return self._tank_residual(
            ctx,
            ctrl,
            dt_s,
            T_tank_w_in_K_n,
            T_sup_w_K_n,
            tank_level,
            lambda T: self._tank_loop_heat(ctx, state, T)[0],
        )

    def _augment_results(
        self, r: dict, ctx: StepContext, ctrl: ControlState, sub_states: dict, T_solved_K: float
    ) -> dict:
        state = sub_states["stc"]
        cols, e_pump, active = self._tank_loop_columns(ctx, state, T_solved_K)
        r["E_tot [W]"] = float(r.get("E_tot [W]", 0.0)) + e_pump
        r.update(self._solar_columns(ctx, state, active, e_pump))
        r.update(cols)
        return r

    def _postprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        df = super()._postprocess(df)  # type: ignore[misc]
        return self._stc_exergy(df, tank_boundary=True)


class STCPreheat(STCScenario):
    """Collector in the mains line feeding the tank (``ASHPB_STC_preheat``, ``GSHPB_STC_preheat``).

    Circuit: mains → collector → tank make-up inlet; the mixing valve's cold
    side takes mains water directly. The collector flow is the tank's make-up
    flow, which the mixing valve sets from the end-of-step tank temperature,
    so the collector is solved inside the implicit tank balance and its heat
    enters the tank exactly (``Q_solar_tank``). The core columns
    ``T_tank_w_in [°C]`` / ``Q_tank_w_in [W]`` are the preheated inflow, so the
    tank exergy already includes the solar gain.

    The mains pressure drives the collector: no pump power. The collector is
    bypassed (inactive) outside the ``preheat_start_hour``–``preheat_end_hour``
    window, when no water is drawn and when it would cool the feed; when its
    outlet reaches ``T_stc_tank_max`` (or ``T_stc_boil``) it is bypassed for the
    rest of the step (``stc_runtime_fraction [-]``). ``T_stc_w_in`` is the mains
    temperature.

    Freeze protection: while the idle collector is below ``T_stc_freeze`` it is
    bypassed and drained (``stc_freeze_protected [-]``). There is no pump and
    no controller in this placement, so the stagnation lock-out does not apply
    (``stc_stagnation_lockout [-]`` stays ``False``): between draws the
    collector idles at ``T_stc_stag [°C]``, which on a clear day is far above
    100 °C, and the next draw passes through it.
    """

    def __init__(self, *, stc: SolarThermalCollector, **kwargs: Any) -> None:
        self._check_stc(stc)
        super().__init__(**kwargs)  # type: ignore[call-arg]
        # Mains pressure drives the loop: no pump power. Applied to a copy, so the
        # caller's collector keeps its pump for other models.
        self._init_stc(dataclasses.replace(stc, E_stc_pump=0.0))

    def _get_activation_flags(self, hour_of_day: float) -> dict[str, bool]:
        return {"stc": self._stc.is_preheat_on(hour_of_day)}

    def _feed_flow(self, ctx: StepContext, ctrl: ControlState, T_K: float) -> float:
        """Tank make-up flow [m³/s] with the tank at ``T_K`` (the mixing valve's hot share)."""
        if ctrl.dV_tank_w_in_ctrl is not None:
            return float(ctrl.dV_tank_w_in_ctrl)
        den = max(1e-6, T_K - ctx.T_sup_w_K)
        alp = min(1.0, max(0.0, (self.T_mix_w_out_K - ctx.T_sup_w_K) / den))
        return float(alp * ctx.dV_mix_w_out)

    def _preheat(
        self, ctx: StepContext, ctrl: ControlState, plane: dict, T_K: float
    ) -> tuple[float, float, dict, float]:
        """``(flow, feed temperature [K], collector result, in-line share)`` with the tank at ``T_K``.

        The collector outlet rises as the flow falls, and the flow follows the
        end-of-step tank temperature, so the outlet limit (``T_stc_tank_max``,
        and ``T_stc_boil`` above it) is applied within the step: the collector
        is in line for the share of the step before its outlet reaches the
        limit and bypassed for the rest (the share falls from 1 to 0 over
        :data:`TANK_LIMIT_RAMP_K` above it). The feed temperature is the
        step average, ``T_mains + share·(T_out − T_mains)``.
        """
        flow = self._feed_flow(ctx, ctrl, T_K)
        T_mains = ctx.T_sup_w_K
        if flow <= 0.0:
            return 0.0, T_mains, self._stc_eval(ctx, plane, T_mains, active=False, dV=1e-6), 0.0
        r = self._stc_eval(ctx, plane, T_mains, active=True, dV=flow)
        T_out_C = cu.K2C(r["T_stc_w_out_K"])
        share = min(
            self._limit_share(T_out_C, self._stc.T_stc_tank_max),
            self._limit_share(T_out_C, self._stc.T_stc_boil),
        )
        rise = max(0.0, r["T_stc_w_out_K"] - T_mains)
        return flow, T_mains + share * rise, r, share

    def _run_subsystems(self, ctx: StepContext, ctrl: ControlState, dt: float, T_tank_w_in_K: float) -> dict:
        plane = self._stc_plane(ctx, dt)
        freeze = self._freeze_protected(self._stc.stagnation_temperature_K(plane, ctx.T0_K))
        active = False
        if ctx.activation_flags.get("stc", False) and plane["total"] > 0.0 and not freeze:
            flow, T_pre, probe, _ = self._preheat(ctx, ctrl, plane, ctx.T_tank_w_K)
            active = bool(
                flow > 0.0
                and probe["T_stc_w_out_K"] > T_tank_w_in_K
                and cu.K2C(probe["T_stc_w_out_K"]) < self._stc.T_stc_tank_max
            )
        return {
            "stc": {
                "stc_active": active,
                "plane": plane,
                "T_tank_w_in_override_K": None,
                "E_subsystem": 0.0,
                "Q_contribution": 0.0,
                "freeze": freeze,
                "lockout": False,
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
        sub_states: dict,
    ):
        """Tank residual with the feed preheated at the flow the candidate ``T^{n+1}`` draws."""
        state = sub_states.get("stc", {})
        if not state.get("stc_active", False):
            return self._tank_residual(ctx, ctrl, dt_s, T_tank_w_in_K_n, T_sup_w_K_n, tank_level, None)
        plane = state["plane"]
        return self._tank_residual(
            ctx,
            ctrl,
            dt_s,
            T_tank_w_in_K_n,
            T_sup_w_K_n,
            tank_level,
            None,
            override_fn=lambda T: self._preheat(ctx, ctrl, plane, T)[1],
        )

    def _augment_results(
        self, r: dict, ctx: StepContext, ctrl: ControlState, sub_states: dict, T_solved_K: float
    ) -> dict:
        state = sub_states["stc"]
        active = bool(state.get("stc_active", False))
        T_mains = ctx.T_sup_w_K
        share = 0.0
        if active:
            flow, T_pre, result, share = self._preheat(ctx, ctrl, state["plane"], T_solved_K)
            active = flow > 0.0 and share > 0.0
        if active:
            gain = share * (result["Q_stc_w_out"] - result["Q_stc_w_in"])
            r["T_tank_w_in [°C]"] = cu.K2C(T_pre)
            r["Q_tank_w_in [W]"] = calc_energy_flow(c_w * rho_w * flow, T_pre, ctx.T0_K)
        else:
            flow, gain, share = self._feed_flow(ctx, ctrl, T_solved_K), 0.0, 0.0
            result = self._stc_eval(ctx, state["plane"], T_mains, active=False)
        r.update(self._solar_columns(ctx, state, active, 0.0))
        r.update(stc_result_columns(result, T_mains, flow, share))
        r["Q_solar_tank [W]"] = gain
        return r

    def _postprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        df = super()._postprocess(df)  # type: ignore[misc]
        df = self._stc_exergy(df, tank_boundary=False)
        return self._mixer_cold_side_exergy(df)

    @staticmethod
    def _mixer_cold_side_exergy(df: pd.DataFrame) -> pd.DataFrame:
        """Exergy of the mixing valve's cold side at the mains temperature.

        Only the tank make-up passes through the collector; the valve's cold
        side takes unheated mains water. A base model that evaluates
        ``X_mix_sup_w_in`` at ``T_tank_w_in`` (GSHPB) would credit that water
        with the preheat, so it is re-evaluated at the mains temperature
        (``T_stc_w_in``) and ``Xc_mix`` follows. Unchanged where the base model
        already uses the mains temperature (ASHPB).
        """
        from .thermodynamics import calc_exergy_flow

        need = {"X_mix_sup_w_in [W]", "Xc_mix [W]", "dV_mix_sup_w_in [m3/s]", "T_stc_w_in [°C]", "T0 [°C]"}
        if not need <= set(df.columns):
            return df
        x_base = df["X_mix_sup_w_in [W]"]
        x_mains = calc_exergy_flow(
            c_w * rho_w * df["dV_mix_sup_w_in [m3/s]"].fillna(0),
            cu.C2K(df["T_stc_w_in [°C]"]),
            cu.C2K(df["T0 [°C]"]),
        )
        x_mains = x_mains.where(df["T_stc_w_in [°C]"].notna(), x_base)  # rows without a collector record
        df["Xc_mix [W]"] = df["Xc_mix [W]"] + (x_mains.fillna(0) - x_base.fillna(0))
        df["X_mix_sup_w_in [W]"] = x_mains
        return df
