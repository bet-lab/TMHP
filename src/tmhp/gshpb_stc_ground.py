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
- ``_compute_bhe_superposition``: subtracts ``Q_solar`` from the HP ground load
  (net ``Q_bhe = HP extraction − solar injection``) before the standard ground
  superposition, so a net injection warms the borehole field. The tank energy
  balance is untouched (solar heat does not enter the tank in this scenario).

With zero irradiance the collector is inactive and results reduce to the base
GSHPB exactly.
"""

from __future__ import annotations

from . import calc_util as cu
from .ground_source_heat_pump_boiler import GroundSourceHeatPumpBoiler
from .subsystems import SolarThermalCollector


class GSHPB_STC_ground(GroundSourceHeatPumpBoiler):
    """GSHPB + SolarThermalCollector in *ground-loop* (borehole) placement."""

    def __init__(self, *, stc: SolarThermalCollector, **kwargs) -> None:
        if not isinstance(stc, SolarThermalCollector):
            raise TypeError(f"stc must be a SolarThermalCollector instance, got {type(stc)!r}")
        # Do NOT pass stc to super().__init__ — keeps self._subsystems empty so
        # the tank energy balance is unaffected (solar heat goes to the ground).
        super().__init__(**kwargs)
        self._stc: SolarThermalCollector = stc
        self.stc = stc  # enables I_DN/I_dH schedules in analyze_dynamic
        self._q_solar_ground_W: float = 0.0

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
        stc_active = (
            has_sun
            and ctx.activation_flags.get("stc", False)
            and probe["T_stc_w_out_K"] > T_in_K
        )

        if stc_active:
            stc_result = probe
            # Net heat carried into the ground by the collector loop [W].
            self._q_solar_ground_W = probe["Q_stc_w_out"] - probe["Q_stc_w_in"]
            e_pump = self._stc.E_stc_pump
        else:
            stc_result = self._stc.calc_performance(
                I_DN_stc=ctx.I_DN, I_dH_stc=ctx.I_dH, T_stc_w_in_K=T_in_K,
                T0_K=ctx.T0_K, is_active=False,
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

    def _compute_bhe_superposition(self, n, time_arr, hp_result, hp_is_on) -> None:
        """Inject the solar heat into the ground (net = HP extraction − solar)."""
        q_sol = self._q_solar_ground_W
        if q_sol != 0.0:
            base_q = hp_result.get("Q_bhe [W]", 0.0) if hp_is_on else 0.0
            hp_result["Q_bhe [W]"] = base_q - q_sol  # extraction-positive convention
            hp_is_on = True  # drive the ground response even when the HP is off
        super()._compute_bhe_superposition(n, time_arr, hp_result, hp_is_on)

    def _augment_results(self, r: dict, ctx, ctrl, sub_states, T_solved_K) -> dict:
        state = sub_states.get("stc", {})
        r["stc_active [-]"] = bool(state.get("stc_active", False))
        r["Q_solar_ground [W]"] = float(state.get("Q_solar_ground", 0.0))
        r["E_stc_pump [W]"] = float(state.get("E_stc_pump", 0.0))
        sr = state.get("stc_result", {})
        if "Q_sol_stc" in sr:
            r["Q_sol_stc [W]"] = float(sr["Q_sol_stc"])
            r["T_stc_w_out [°C]"] = cu.K2C(float(sr["T_stc_w_out_K"]))
        return r
