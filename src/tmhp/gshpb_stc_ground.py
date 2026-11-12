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
pump electricity. The loop runs under the collector-loop controller of
:mod:`tmhp._stc_scenarios` (outlet at least ``dT_stc_off`` above the ground,
gain above the pump power) inside the collector's clock window.

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

from typing import Any

from . import calc_util as cu
from ._run_reset import GroundSourceHeatPumpBoilerRun
from ._stc_scenarios import STCScenario
from .subsystems import SolarThermalCollector, stc_result_columns


class GSHPB_STC_ground(STCScenario, GroundSourceHeatPumpBoilerRun):
    """GSHPB + SolarThermalCollector in *ground-loop* (borehole) placement.

    ``E_tot [W]`` is the whole electric draw: compressor + source-loop pump +
    the collector-loop pump ``E_stc_pump [W]`` (which runs whenever the
    collector is active).  Every step the collector balance closes::

        Q_sol_stc = (Q_stc_w_out - Q_stc_w_in) + Q_l_stc
        Q_stc_w_out - Q_stc_w_in = Q_solar_ground       (all heat goes to the ground)

    The loop runs inside the ``preheat_start_hour``–``preheat_end_hour``
    window under the collector-loop controller of :mod:`tmhp._stc_scenarios`
    (outlet at least ``dT_stc_off`` above the ground, gain above the pump
    power, below boiling).
    """

    def __init__(self, *, stc: SolarThermalCollector, **kwargs) -> None:
        self._check_stc(stc)
        # Do NOT pass stc to super().__init__ — keeps self._subsystems empty so
        # the tank energy balance is unaffected (solar heat goes to the ground).
        super().__init__(**kwargs)
        self._init_stc(stc)
        self._q_solar_ground_W: float = 0.0

    def _reset_run_state(self) -> None:
        super()._reset_run_state()
        self._q_solar_ground_W = 0.0

    # ------------------------------------------------------------------
    def _get_activation_flags(self, hour_of_day: float) -> dict[str, bool]:
        return {"stc": self._stc.is_preheat_on(hour_of_day)}

    def _run_subsystems(self, ctx, ctrl, dt: float, T_tank_w_in_K: float) -> dict:
        """Evaluate the collector at the ground inlet; store the solar heat.

        The ground-charging loop exchanges with the borehole field, so the
        collector inlet is the borehole-wall temperature ``T_bhe`` of the
        previous step (not the much colder HP evaporator fluid ``T_bhe_f``).
        """
        T_in_K = cu.C2K(self.T_bhe)
        state: dict[str, Any]
        if ctx.activation_flags.get("stc", False):
            state = self._ground_loop_state(ctx, dt, T_in_K)
        else:
            plane = self._stc_plane(ctx, dt)
            state = {
                "stc_active": False,
                "stc_result": self._stc_eval(ctx, plane, T_in_K, active=False),
                "plane": plane,
                "T_tank_w_in_override_K": None,
                "E_subsystem": 0.0,
                "Q_contribution": 0.0,
                "E_stc_pump": 0.0,
                "Q_solar_ground": 0.0,
            }
        self._q_solar_ground_W = float(state["Q_solar_ground"])
        return {"stc": state}

    def _ground_injection_W(self) -> float:
        """Solar heat the collector loop injects into the borehole field this step [W]."""
        return self._q_solar_ground_W

    def _augment_results(self, r: dict, ctx, ctrl, sub_states, T_solved_K) -> dict:
        state = sub_states.get("stc", {})
        sr = state.get("stc_result", {})
        active = bool(state.get("stc_active", False))
        e_pump = float(state.get("E_stc_pump", 0.0))
        # The collector-loop pump is electricity the system draws.
        r["E_tot [W]"] = float(r.get("E_tot [W]", 0.0)) + e_pump
        r.update(self._solar_columns(ctx, active, e_pump))
        r["Q_solar_ground [W]"] = float(state.get("Q_solar_ground", 0.0))
        r.update(
            stc_result_columns(
                sr, sr.get("T_stc_w_in_K", cu.C2K(self.T_bhe)), self._stc.loop_flow, 1.0 if active else 0.0
            )
        )
        return r
