"""GSHPB with a SolarThermalCollector charging the **ground loop** (not the tank).

Counterpart to :class:`~tmhp.gshpb_stc_tank.GSHPB_STC_tank`. Solar heat charges
the ground **OR** the tank, never both at once — this scenario routes it to the
ground.

Physical configuration
----------------------
The collector loop circulates **ground-loop fluid** (inlet = current borehole
fluid temperature ``T_bhe_f``), is warmed by solar irradiance, and returns to the
borehole field, **injecting** the collected heat into the ground (seasonal /
inter-seasonal charging). This raises the ground temperature and therefore the
heat-pump source temperature — improving COP later — at the cost of the collector
pump electricity. Activation requires a net heat gain (collector outlet hotter
than the ground fluid).

Implementation
--------------
The base :class:`~tmhp.ground_source_heat_pump_boiler.GroundSourceHeatPumpBoiler`
is left **unchanged** (byte-identical). This subclass:

- ``_run_subsystems``: evaluates the collector at the ground-fluid inlet and
  stores the net solar heat ``Q_solar`` [W] to inject into the ground.
- ``_ground_injection_W``: hands ``Q_solar`` to the base ground superposition,
  which subtracts it from the HP ground load (net ``Q_bhe = HP extraction −
  solar injection``), so a net injection warms the borehole field. The
  injection flows for the whole step, the HP extraction only while the
  compressor runs. The tank energy
  balance is untouched (solar heat does not enter the tank in this scenario).

With zero irradiance the collector is inactive and results reduce to the base
GSHPB exactly.
"""

from __future__ import annotations

from . import calc_util as cu
from ._run_reset import GroundSourceHeatPumpBoilerRun
from .subsystems import SolarThermalCollector, stc_result_columns, warn_if_uv_ignored


class GSHPB_STC_ground(GroundSourceHeatPumpBoilerRun):
    """GSHPB + SolarThermalCollector in *ground-loop* (borehole) placement.

    ``E_tot [W]`` is the whole electric draw: compressor + source-loop pump +
    the collector-loop pump ``E_stc_pump [W]`` (which runs whenever the
    collector is active).  Every step the collector balance closes::

        Q_sol_stc = (Q_stc_w_out - Q_stc_w_in) + Q_l_stc
        Q_stc_w_out - Q_stc_w_in = Q_solar_ground       (all heat goes to the ground)
    """

    def __init__(self, *, stc: SolarThermalCollector, **kwargs) -> None:
        if not isinstance(stc, SolarThermalCollector):
            raise TypeError(f"stc must be a SolarThermalCollector instance, got {type(stc)!r}")
        # Do NOT pass stc to super().__init__ — keeps self._subsystems empty so
        # the tank energy balance is unaffected (solar heat goes to the ground).
        super().__init__(**kwargs)
        self._stc: SolarThermalCollector = stc
        self.stc = stc  # enables I_DN/I_dH schedules in analyze_dynamic
        self._q_solar_ground_W: float = 0.0
        warn_if_uv_ignored(self)

    def _reset_run_state(self) -> None:
        super()._reset_run_state()
        self._q_solar_ground_W = 0.0

    # ------------------------------------------------------------------
    def _needs_solar_input(self) -> bool:
        return True

    def _get_activation_flags(self, hour_of_day: float) -> dict[str, bool]:
        return {"stc": self._stc.is_preheat_on(hour_of_day)}

    def _run_subsystems(self, ctx, ctrl, dt: float, T_tank_w_in_K: float) -> dict:
        """Evaluate the collector at the ground inlet; store the solar heat.

        The ground-charging loop exchanges with the borehole field, so the
        collector inlet is the borehole-wall temperature ``T_bhe`` (not the much
        colder HP evaporator fluid ``T_bhe_f``). Charging runs only during solar
        hours (positive irradiance); the injected heat is therefore solar-driven
        (with the collector's ambient exchange included while the loop is cold).
        """
        T_in_K = cu.C2K(self.T_bhe)  # ground (borehole-wall) temperature, prev step
        probe = self._stc.calc_performance(
            I_DN_stc=ctx.I_DN,
            I_dH_stc=ctx.I_dH,
            T_stc_w_in_K=T_in_K,
            T0_K=ctx.T0_K,
            is_active=True,
        )
        # Activate only during solar hours (positive irradiance) and when the
        # collector outlet is hotter than the ground (net heat gain).
        has_sun = (ctx.I_DN + ctx.I_dH) > 0.0
        stc_active = has_sun and ctx.activation_flags.get("stc", False) and probe["T_stc_w_out_K"] > T_in_K

        if stc_active:
            stc_result = probe
            # Net heat carried into the ground by the collector loop [W].
            self._q_solar_ground_W = probe["Q_stc_w_out"] - probe["Q_stc_w_in"]
            e_pump = self._stc.E_stc_pump
        else:
            stc_result = self._stc.calc_performance(
                I_DN_stc=ctx.I_DN,
                I_dH_stc=ctx.I_dH,
                T_stc_w_in_K=T_in_K,
                T0_K=ctx.T0_K,
                is_active=False,
            )
            self._q_solar_ground_W = 0.0
            e_pump = 0.0

        return {
            "stc": {
                "stc_active": stc_active,
                "stc_result": stc_result,
                "T_tank_w_in_override_K": None,
                # Solar heat goes to the ground, not the tank: contribute nothing
                # to the tank energy balance.
                "E_subsystem": 0.0,
                "Q_contribution": 0.0,
                "E_stc_pump": e_pump,
                "Q_solar_ground": self._q_solar_ground_W,
            }
        }

    def _ground_injection_W(self) -> float:
        """Solar heat the collector loop injects into the borehole field this step [W]."""
        return self._q_solar_ground_W

    def _augment_results(self, r: dict, ctx, ctrl, sub_states, T_solved_K) -> dict:
        state = sub_states.get("stc", {})
        sr = state.get("stc_result", {})
        e_pump = float(state.get("E_stc_pump", 0.0))
        r["stc_active [-]"] = bool(state.get("stc_active", False))
        r["Q_solar_ground [W]"] = float(state.get("Q_solar_ground", 0.0))
        r["E_stc_pump [W]"] = e_pump
        # The collector-loop pump is electricity the system draws.
        r["E_tot [W]"] = float(r.get("E_tot [W]", 0.0)) + e_pump
        r["I_DN_stc [W/m2]"] = ctx.I_DN
        r["I_dH_stc [W/m2]"] = ctx.I_dH
        r.update(stc_result_columns(sr, sr.get("T_stc_w_in_K", cu.C2K(self.T_bhe)), self._stc.dV_stc_w))
        return r
