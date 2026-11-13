"""Water source heat pump boiler — physics-based cycle model.

Resolves a vapour-compression refrigerant cycle coupled to a surface-water
source (e.g. river) on the evaporator side and a lumped-capacitance hot-water
tank on the condenser side. At each time step the model finds the
minimum-power operating point via 1D Brent optimization over the evaporator
approach temperature difference, while the condenser temperature is solved
analytically.

The water source is treated as an infinite-capacity thermal boundary: the
evaporator draws heat from the source-water stream through an NTU-based heat
exchanger without depleting it, so no long-term source temperature drift is
tracked. (This distinguishes the model from the ground source variant, which
resolves borehole g-functions.)
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd
from tqdm import tqdm

from . import calc_util as cu
from ._boiler_results import complete_capacity_keys, fill_missing, report_condenser_dew_point, step_request
from ._opt_utils import safe_float_attr
from ._source_boiler import (
    BOREHOLE_DEPTH_DEFAULT_M,
    BOREHOLE_LOAD_DESIGN_W_PER_M,
    approach_bracket,
    capped_superheat,
    close_source_temperature,
    close_tank_hx,
    default_pump_power,
    default_source_flow_lpm,
    default_source_hx_UA,
    default_tank_hx_UA,
    rated_source_heat,
    warn_if_field_overloaded,
    warn_if_laminar,
    warn_if_setpoint_unreachable,
)
from ._tank_solve import solve_lumped_tank
from ._validation import validate_ctor, validate_dynamic, validate_steady
from .compressor_efficiency import InvalidCompressorEfficiency, _eval_eff, reject_invalid_efficiency
from .compressor_envelope import (
    PR_CYCLE_MAX_SINGLE_STAGE,
    RATED_SPEED_SEARCH_MAX,
    X_SUCTION_MIN_DEFAULT,
    check_pr_envelope,
    discharge_controlled,
    envelope_violation,
    limit_tank_setpoints_to_envelope,
    reduce_duty_within_envelope,
    resolve_condensing_temp_max,
    resolve_discharge_temp_max,
    resolve_x_suction_min,
    steady_envelope,
)
from .compressor_speed import default_displacement, solve_compressor_speed
from .constants import c_w, rho_w
from .cycling import CC_WATER_DEFAULT, CyclingDegradation, report_boiler_cycling, resolve_cycling, steady_cycling
from .dhw import dhw_service_columns, warn_if_dhw_unmet
from .dynamic_context import (
    TANK_LEVEL_MIN,
    ControlState,
    CycleSolveOutcome,
    StepContext,
    check_hp_schedule_active,
    delivered_tank_heat,
    determine_heat_source_on_off,
    determine_tank_refill_flow,
    resolve_step_operation,
    step_hp_result,
    tank_mass_energy_residual,
    tank_step_flows,
    unphysical_cycle_detail,
    warn_if_tank_boiling,
)
from .enex_functions import (
    calc_mixing_valve_flows,
    calc_mixing_valve_temp,
)
from .heat_exchanger import calc_phase_change_hx_effectiveness
from .heat_transfer import calc_simple_tank_UA
from .reference_state import HXSide, RatingCondition, ReferenceStateMixin
from .refrigerant import calc_ref_state, props_si, reportable_state
from .source_fluid import (
    SOURCE_FLUID_FREEZING,
    WATER_LOOP_FLUID_DEFAULT,
    SourceFluid,
    limit_duty_to_source_freeze,
    resolve_source_fluid,
    source_freeze_limited,
)
from .subsystems import add_uv_power_to_e_tot
from .thermodynamics import calc_exergy_flow

if TYPE_CHECKING:
    from .subsystems import SolarThermalCollector


#: Saturation states an off-mode row reports as NaN next to the cycle state points.
_OFF_STATE_NAN_KEYS: tuple[str, ...] = (
    "T_ref_evap_sat [°C]",
    "T_ref_cond_sat_v [°C]",
    "T_ref_cond_sat_l [°C]",
    "P_ref_evap_sat [Pa]",
    "P_ref_cond_sat_v [Pa]",
    "P_ref_cond_sat_l [Pa]",
    "h_ref_evap_sat [J/kg]",
    "h_ref_cond_sat_v [J/kg]",
    "h_ref_cond_sat_l [J/kg]",
)


class WaterSourceHeatPumpBoiler(ReferenceStateMixin):
    """Water source heat pump boiler with surface-water source and lumped-tank model.

    The refrigerant cycle is resolved via CoolProp with user-specified
    superheat / subcool margins. An optimizer minimises total cycle
    electrical input subject to NTU-based evaporator constraints against the
    source-water temperature and analytical condenser temperature relations.

    ``hp_capacity`` and ``ref`` are enough to build a machine; the sizes left
    as ``None`` follow the capacity (:mod:`tmhp._source_boiler`), so the rated
    cycle is the same at every capacity: ``UA_tank_hx = hp_capacity / 5``,
    ``UA_water = 0.18 * hp_capacity``, source-loop flow ``dV_b_f_lpm =
    0.75 * hp_capacity / (rho c * 3 K)`` (3.58 L/min per kW), ``E_pmp =
    75 W/ton * hp_capacity`` (21.3 W per kW), and a submerged coil ``H_b`` of
    200 m or as long as the rated source-side heat needs at 50 W/m. A value
    the caller gives is used as given. ``UserWarning`` is raised for a
    caller's coil above 80 W/m, for a laminar default flow, and for an upper
    tank set point whose condensing temperature is at or above the
    refrigerant's critical temperature.

    In every running result the suction superheat is at most the evaporator
    approach less ``dT_hx_min``; on a compressor speed bound the tank heat
    exchanger is closed on the heat actually delivered (``dT_ref_tank [K]``,
    ``tank_hx_residual [W]``). In ``analyze_dynamic`` the cycle of a step is
    solved against the fluid the coil returns from that step's source water
    (``T_source_w_schedule``) while it carries that step's own extraction.

    ``source_fluid`` names the liquid circulating in the coil
    (:mod:`tmhp.source_fluid`): ``"water"`` (default) or an antifreeze mixture
    such as ``"MPG-25%"``. The loop liquid is never colder than its operating
    limit, the freezing point plus 3 K (3 °C for water): a duty that would
    chill it further is reduced to the largest one that does not
    (``capacity_binding == "source_fluid_freezing"``), and when no duty does
    the compressor stays off with ``failure_reason == "source_fluid_freezing"``.
    Source water colder than about 6 °C at full duty therefore needs an
    antifreeze mixture in the coil.

    **Below the minimum capacity.** ``analyze_steady`` reports a request below
    what the compressor delivers at ``rps_min`` as the mean of on/off cycling
    (``capacity_clamped == "min"``, :mod:`tmhp.cycling`): ``Q_ref_tank [W]``
    equals the request, ``runtime_fraction [-]`` is the share of the time the
    compressor runs, and the electrical input carries the EN 14825 degradation
    of a unit delivering to water -- ``Cd_cycling`` is its coefficient ``Cc``
    (0.9: while the compressor is off the unit draws a tenth of its running
    input; 1.0: nothing; a tested unit has ``Cc = 1 - P_off / P_on``).
    ``Cd_cycling=None`` returns the continuous minimum-speed point, which
    delivers more than the request. Dynamic runs are not affected: there the
    tank thermostat switches the compressor, which runs at ``hp_capacity`` for
    ``runtime_fraction [-]`` of a step.
    """

    # Heat and power rates of the running cycle; a step reports them
    # multiplied by the fraction of the step the compressor ran. Source-loop
    # flows stay at their running values.
    _RUNTIME_SCALED_KEYS: tuple[str, ...] = (
        "E_cmp [W]",
        "E_pmp [W]",
        "E_tot [W]",
        "Q_bhe [W]",
        "Q_ref_tank [W]",
        "Q_ref_water [W]",
        "Q_tank_load [W]",
        "m_dot_ref [kg/s]",
    )
    #: The same rates as ``analyze_steady`` averages them when the unit cycles
    #: (:meth:`_report_part_load`): heat and refrigerant rates with the
    #: capacity ratio, electrical inputs with the power fraction. ``Q_bhe``
    #: follows from the averaged terms.
    _CYCLING_RATE_KEYS: tuple[str, ...] = ("Q_ref_tank [W]", "Q_ref_water [W]", "m_dot_ref [kg/s]")
    _CYCLING_POWER_KEYS: tuple[str, ...] = ("E_cmp [W]", "E_pmp [W]")
    _CYCLING_SOURCE_HEAT_KEY: str | None = "Q_ref_water [W]"
    _RATING_FAMILY = "WSHPB"
    _RATING_DEFAULT_MODE = "heating"

    def __init__(
        self,
        # 1. Refrigerant / cycle / compressor
        ref: str = "R290",
        V_cmp_ref: float | None = None,
        eta_cmp_isen: float | Callable | None = None,
        eta_cmp_vol: float | Callable | None = None,
        eta_cmp: float | Callable | None = None,
        # 2. Heat exchanger UA
        UA_tank_hx: float | None = None,
        UA_water: float | None = None,
        # 3. Tank / control / load
        T0: float = 0.0,
        Ts: float = 16.0,
        T_tank_w_upper_bound: float = 65.0,
        T_tank_w_lower_bound: float = 55.0,
        T_mix_w_out: float = 40.0,
        T_tank_w_in: float = 15.0,
        hp_capacity: float = 8000.0,
        dV_mix_w_out_max: float = 0.0001,
        # Tank / insulation
        r0: float = 0.2,
        H: float = 0.8,
        x_shell: float = 0.01,
        x_ins: float = 0.05,
        k_shell: float = 25,
        k_ins: float = 0.03,
        h_o: float = 15,
        # 4. Submerged source-water coil (N_1 x N_2 parallel pipes, H_b = coil length [m])
        N_1: int | None = None,
        N_2: int | None = None,
        B: float = 6.0,
        D_b: float = 0,
        H_b: float | None = None,
        r_b: float = 0.08,
        R_b: float | None = None,
        k_g: float = 1.5,
        k_p: float = 0.4,
        r_out: float = 0.016,
        r_in: float = 0.013,
        D_s: float = 0.025,
        dV_b_f_lpm: float | None = None,
        k_s: float = 2.0,
        c_s: float = 800,
        rho_s: float = 2000,
        E_pmp: float | None = None,
        v_river: float = 0.5,
        # 6. Superheat / subcool
        dT_superheat: float = 5.0,
        dT_subcool: float = 5.0,
        # 7. Tank fluid limits
        tank_always_full: bool = True,
        prevent_simultaneous_flow: bool = False,
        tank_level_lower_bound: float = 0.5,
        tank_level_upper_bound: float = 1.0,
        dV_tank_w_in_refill: float = 0.001,
        # 8. Operation Schedule
        hp_on_schedule: list[tuple[float, float]] | None = None,
        min_on_time_s: float = 0.0,
        min_off_time_s: float = 0.0,
        # 9. Subsystems
        stc: SolarThermalCollector | None = None,
        pv=None,
        uv=None,
        # 10. Simulation scope (for precomputing g-functions)
        t_max_s: float = 8760 * 3600,
        dt_s: float = 3600,
        T_sur: float = 20.0,
        dT_hx_min: float = 0.5,
        # Compressor pressure-ratio envelope (PR = P_cond / P_evap); the
        # ceiling is the single-stage scroll limit (see compressor_envelope).
        PR_cycle_min: float = 1.5,
        PR_cycle_max: float = PR_CYCLE_MAX_SINGLE_STAGE,
        # Compressor speed bounds [rev/s]; rps_max=None -> default_rps_max(rps_rated)
        rps_min: float = 15.0,
        rps_max: float | None = None,
        *,
        rps_rated: float | None = None,
        T_dis_max: float | Literal["auto"] | None = "auto",
        x_suction_min: float | None = X_SUCTION_MIN_DEFAULT,
        T_cond_max: float | Literal["auto"] | None = "auto",
        m_dot_ref_rated: float | None = None,
        rated_condition: Mapping[str, Any] | RatingCondition | None = None,
        # Deprecated:
        refrigerant: str | None = None,
        V_disp_cmp: float | None = None,
        UA_tank: float | None = None,  # deprecated alias for UA_tank_hx
        UA_cond_design: float | None = None,
        UA_evap_design: float | None = None,
        # On/off cycling below the minimum continuous capacity in analyze_steady:
        # EN 14825 degradation coefficient Cc of a unit delivering to water
        # (None: no cycling, the row over-delivers at the minimum speed).
        Cd_cycling: CyclingDegradation | float | None = CC_WATER_DEFAULT,
        source_fluid: str | SourceFluid | None = None,
    ) -> None:
        validate_ctor("WSHPB", locals())
        #: EN 14825 cycling degradation of ``analyze_steady`` (None: continuous at minimum speed).
        self.cycling: CyclingDegradation | None = resolve_cycling(Cd_cycling, "water")
        # Liquid of the source loop: its properties enter the source-side
        # balances, its freezing point the operating limit (tmhp.source_fluid).
        fluid = resolve_source_fluid(source_fluid, WATER_LOOP_FLUID_DEFAULT)
        self.source_fluid: SourceFluid = fluid
        #: Set once a dynamic run has reported the freezing limit (one warning per run).
        self._source_freeze_warned: bool = False
        # Capacity-scaled defaults (tmhp._source_boiler): a machine given only
        # hp_capacity and a refrigerant keeps the same rated performance at any
        # capacity. Every value the caller supplies is used as given.
        flow_defaulted = dV_b_f_lpm is None
        if dV_b_f_lpm is None:
            dV_b_f_lpm = default_source_flow_lpm(hp_capacity, fluid.rho, fluid.cp)
        if N_1 is None and N_2 is None and H_b is None:
            # The coil is as long as the rated source-side heat needs at 50 W/m
            # (never shorter than the 200 m of the default 8 kW machine).
            N_1, N_2 = 1, 1
            H_b = max(BOREHOLE_DEPTH_DEFAULT_M, rated_source_heat(hp_capacity) / BOREHOLE_LOAD_DESIGN_W_PER_M)
        else:
            N_1 = 1 if N_1 is None else N_1
            N_2 = 1 if N_2 is None else N_2
            H_b = BOREHOLE_DEPTH_DEFAULT_M if H_b is None else H_b
            warn_if_field_overloaded(hp_capacity, 1, H_b, "WaterSourceHeatPumpBoiler source-water coil")
        if E_pmp is None:
            E_pmp = default_pump_power(hp_capacity)
        if refrigerant is not None:
            import warnings

            warnings.warn(
                "WaterSourceHeatPumpBoiler(refrigerant=...) is deprecated; use ref=... instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            ref = refrigerant

        # Resolve deprecated mapping
        if V_cmp_ref is None:
            V_cmp_ref = V_disp_cmp if V_disp_cmp is not None else default_displacement(hp_capacity)
        if UA_tank_hx is None:
            UA_tank_hx = (
                UA_tank
                if UA_tank is not None
                else (UA_cond_design if UA_cond_design is not None else default_tank_hx_UA(hp_capacity))
            )
        if UA_water is None:
            UA_water = UA_evap_design if UA_evap_design is not None else default_source_hx_UA(hp_capacity)
        warn_if_setpoint_unreachable(
            what="WaterSourceHeatPumpBoiler",
            ref=ref,
            T_tank_w_upper_bound=T_tank_w_upper_bound,
            hp_capacity=hp_capacity,
            UA_tank_hx=UA_tank_hx,
        )

        self.tank_physical = {
            "r0": r0,
            "H": H,
            "x_shell": x_shell,
            "x_ins": x_ins,
            "k_shell": k_shell,
            "k_ins": k_ins,
            "h_o": h_o,
        }
        self.UA_tank_wall = calc_simple_tank_UA(**self.tank_physical)
        self.T_sur_K = cu.C2K(T_sur)
        self.V_tank_full: float = math.pi * r0**2 * H
        self.C_tank = c_w * rho_w * self.V_tank_full

        self.ref = ref
        self.V_cmp_ref = V_cmp_ref

        self.UA_tank_hx = UA_tank_hx
        self.UA_water = UA_water

        self.T0_K = cu.C2K(T0)
        self.Ts = Ts
        self.Ts_K = cu.C2K(self.Ts)
        self.T_bhe_f_in = Ts
        self.T_bhe_f_in_K = self.Ts_K

        self.hp_capacity = hp_capacity
        self.hp_on_schedule = hp_on_schedule or [(0.0, 24.0)]
        # Validated by validate_ctor (finite, >= 0).
        self.min_on_time_s: float = float(min_on_time_s)
        self.min_off_time_s: float = float(min_off_time_s)
        self.dV_mix_w_out_max = dV_mix_w_out_max
        self.T_tank_w_upper_bound = T_tank_w_upper_bound
        self.T_tank_w_lower_bound = T_tank_w_lower_bound
        self.T_mix_w_out = T_mix_w_out
        self.T_mix_w_out_K = cu.C2K(T_mix_w_out)
        self.T_tank_w_in = T_tank_w_in
        self.T_tank_w_in_K = cu.C2K(T_tank_w_in)

        self.tank_always_full = tank_always_full
        self.prevent_simultaneous_flow = prevent_simultaneous_flow
        self.tank_level_lower_bound = tank_level_lower_bound
        self.tank_level_upper_bound = tank_level_upper_bound
        self.dV_tank_w_in_refill = dV_tank_w_in_refill

        self.dT_superheat = dT_superheat
        self.dT_subcool = dT_subcool
        self.dT_hx_min: float = dT_hx_min
        # Compressor pressure-ratio envelope (floor -> clamp, ceiling -> reject)
        self.PR_cycle_min: float = PR_cycle_min
        self.PR_cycle_max: float = PR_cycle_max
        # Compressor speed search bounds [rev/s]
        self.rps_min: float = rps_min
        # rps_max=None: searched up to RATED_SPEED_SEARCH_MAX for the rated
        # speed, then replaced by default_rps_max(rps_rated) (reference_state).
        self._rps_max_derived: bool = rps_max is None
        self.rps_max: float = RATED_SPEED_SEARCH_MAX if rps_max is None else rps_max
        # Discharge-temperature limit [degC] ("auto": refrigerant default; None: off)
        self.T_dis_max: float | None = resolve_discharge_temp_max(T_dis_max, ref)
        # Maximum condensing temperature [degC] ("auto": refrigerant default; None: off)
        self.T_cond_max: float | None = resolve_condensing_temp_max(T_cond_max, ref)
        limit_tank_setpoints_to_envelope(self, "WaterSourceHeatPumpBoiler")
        # Discharge-temperature control: lowest suction quality (None: no control)
        self.x_suction_min: float | None = resolve_x_suction_min(x_suction_min)
        #: Suction quality of the cycle being evaluated (< 1 only inside the control).
        self._x_suction: float = 1.0
        # Records the PR-envelope event of the most recent _calc_state call
        # (None | ("pr_below_min", pr, bound) | ("pr_above_max", pr, bound)).
        self._last_pr_event: tuple[str, float, float] | None = None

        # BHE properties
        self.N_1 = N_1
        self.N_2 = N_2
        self.B = B
        self.D_b = D_b
        self.H_b = H_b
        self.r_b = r_b
        self.k_s = k_s
        self.c_s = c_s
        self.rho_s = rho_s
        self.alp_s = k_s / (c_s * rho_s)
        self.E_pmp = E_pmp
        self.dV_b_f_m3s = dV_b_f_lpm * cu.L2m3 / cu.m2s
        self.v_river = v_river
        if flow_defaulted:
            warn_if_laminar(self.dV_b_f_m3s, max(1, N_1 * N_2), r_in, "WaterSourceHeatPumpBoiler", fluid.rho, fluid.mu)

        if R_b is None:
            from .g_function import calc_submerged_coil_thermal_resistance

            n_boreholes = max(1, self.N_1 * self.N_2)
            m_flow_total = self.dV_b_f_m3s * fluid.rho
            m_flow_pipe = m_flow_total / n_boreholes

            self.R_b = calc_submerged_coil_thermal_resistance(
                r_out=r_out,
                r_in=r_in,
                D_s=D_s,
                k_p=k_p,
                m_flow_pipe=m_flow_pipe,
                rho_f=fluid.rho,
                mu_f=fluid.mu,
                cp_f=fluid.cp,
                k_f=fluid.k,
                v_river=self.v_river,
            )
        else:
            self.R_b = R_b

        # Compressor reference state (rated speed, rated mass flow)
        efficiencies = self._initialize_reference_state(
            rps_rated=rps_rated,
            m_dot_ref_rated=m_dot_ref_rated,
            efficiencies={"eta_cmp_isen": eta_cmp_isen, "eta_cmp_vol": eta_cmp_vol, "eta_cmp": eta_cmp},
            rated_condition=rated_condition,
        )
        self.eta_cmp_isen = efficiencies["eta_cmp_isen"]
        self.eta_cmp_vol = efficiencies["eta_cmp_vol"]
        self.eta_cmp = efficiencies["eta_cmp"]

        # Subsystems
        self.stc = stc
        self.pv = pv
        self._subsystems: dict[str, Any] = {}
        if stc is not None:
            self._subsystems["stc"] = stc
        if pv is not None:
            self._subsystems["pv"] = pv
        if uv is not None:
            self._subsystems["uv"] = uv

        self.Q_tank_LOAD_OFF_TOL: float = 50.0  # W

        self.dt_s: float = dt_s

        # Simulation state tracking (dynamically updated in analyze_dynamic)
        self.time: np.ndarray = np.array([])
        self.dt: float = dt_s
        self._opt_evals: int = 0
        self.T_bhe_f: float = self.Ts
        self.T_bhe: float = self.Ts
        self.T_bhe_f_out: float = self.Ts
        self.T_bhe_f_out_K: float = self.Ts_K
        self.Q_bhe: float = 0.0
        #: Source-water temperature of the step being simulated [K] (analyze_dynamic).
        self._T_source_w_step_K: float = self.Ts_K

        # NOTE: Removed self.dV_mix_w_out, self.dV_tank_w_in, self.dV_mix_sup_w_in
        # They will be passed inside `flow_state: dict`.

    @staticmethod
    def _calc_tank_flow_context(
        dV_mix_w_out: float,
        T_tank_w_K: float,
        T_tank_w_in_K: float,
        T_mix_w_out_K: float,
        dV_tank_w_in_override: float | None = None,
    ) -> dict:
        mix_state = calc_mixing_valve_temp(T_tank_w_K, T_tank_w_in_K, T_mix_w_out_K)
        flows = calc_mixing_valve_flows(dV_mix_w_out, mix_state["alp"])
        dV_tank_w_out = flows["dV_hot_in"]
        dV_tank_w_in = dV_tank_w_out if dV_tank_w_in_override is None else dV_tank_w_in_override
        return {
            "alp": mix_state["alp"],
            "dV_mix_w_out": dV_mix_w_out,
            "dV_tank_w_out": dV_tank_w_out,
            "dV_tank_w_in": dV_tank_w_in,
            "dV_mix_sup_w_in": flows["dV_cold_in"],
        }

    def _calc_off_state(self, T_tank_w: float, T0: float, flow_state: dict) -> dict:
        """Off-mode row: no heat, no power, and no refrigerant state.

        The compressor is stopped, so the cycle state points do not exist:
        they are NaN, as in the other boilers (they used to carry the source
        and tank temperatures as placeholders, which read like a cycle).
        """
        T_tank_w_K = cu.C2K(T_tank_w)
        mix = calc_mixing_valve_temp(T_tank_w_K, self.T_tank_w_in_K, self.T_mix_w_out_K)

        result: dict = reportable_state(
            calc_ref_state(
                T_evap_K=self.T_bhe_f_in_K,
                T_cond_K=T_tank_w_K,
                refrigerant=self.ref,
                eta_cmp_isen=1.0,
                dT_superheat=self.dT_superheat,
                dT_subcool=self.dT_subcool,
                is_active=False,
            )
        )
        result.update(dict.fromkeys(_OFF_STATE_NAN_KEYS, np.nan))
        result.update(
            {
                "hp_is_on": False,
                "converged": True,
                "converged_rps": True,
                "capacity_clamped": None,
                "T_tank_w [°C]": T_tank_w,
                "T0 [°C]": T0,
                "T_mix_w_out [°C]": cu.K2C(mix["T_mix_w_out_K"]),
                "T_tank_w_in [°C]": self.T_tank_w_in,
                "Ts [°C]": self.Ts,
                "T_bhe [°C]": getattr(self, "T_bhe", self.Ts),
                "T_bhe_f [°C]": getattr(self, "T_bhe_f", self.Ts),
                "T_bhe_f_in [°C]": cu.K2C(getattr(self, "T_bhe_f_in_K", self.Ts_K)),
                "T_bhe_f_out [°C]": cu.K2C(getattr(self, "T_bhe_f_out_K", self.Ts_K)),
                "T_cond [°C]": T_tank_w,
                "dV_mix_w_out [m3/s]": flow_state.get("dV_mix_w_out", 0.0),
                "dV_tank_w_in [m3/s]": flow_state.get("dV_tank_w_in", 0.0),
                "dV_tank_w_out [m3/s]": flow_state.get("dV_tank_w_out", 0.0),
                "dV_mix_sup_w_in [m3/s]": flow_state.get("dV_mix_sup_w_in", 0.0),
                "dV_bhe_f [m3/s]": self.dV_b_f_m3s,
                "Q_bhe [W]": 0.0,
                "Q_ref_tank [W]": 0.0,
                "Q_ref_water [W]": 0.0,
                "Q_tank_load [W]": 0.0,
                "E_cmp [W]": 0.0,
                "E_pmp [W]": 0.0,
                "E_tot [W]": 0.0,
                "m_dot_ref [kg/s]": 0.0,
                "cmp_rpm [rpm]": 0.0,
                "cop_ref [-]": np.nan,
                "cop_sys [-]": np.nan,
            }
        )
        return result

    def _rating_side(self, role: str, mode: str, T_in_C: float) -> HXSide:
        """Source-water evaporator (source) and tank-immersed condenser (load)."""
        if role == "source":
            return HXSide("water", T_in_C, self.UA_water, self.dV_b_f_m3s)
        return HXSide("tank", T_in_C, self.UA_tank_hx)

    @discharge_controlled(reject=False)
    @reject_invalid_efficiency
    def _calc_state(
        self,
        dT_ref_water: float,
        T_tank_w: float,
        Q_tank_load: float,
        T0: float,
        *,
        flow_state: dict,
        source_temperature_K: float | None = None,
    ) -> dict | None:
        if Q_tank_load <= 0:
            return self._calc_off_state(T_tank_w, T0, flow_state)

        # 1. Analytical Condenser Approach Temperature
        dT_ref_tank = Q_tank_load / self.UA_tank_hx

        T_tank_w_K = cu.C2K(T_tank_w)

        # The source fluid leaving the coil and entering the heat pump
        T_source_K = safe_float_attr(self, "T_bhe_f_out_K", cu.C2K(15.0))
        if source_temperature_K is not None:
            T_source_K = source_temperature_K

        fluid = self.source_fluid
        m_dot_cp_b = self.dV_b_f_m3s * fluid.rho * fluid.cp
        T_water_in_K = T_source_K + (self.E_pmp / m_dot_cp_b)

        T_water_sat_K = T_water_in_K - dT_ref_water

        # The suction gas leaves the evaporator no hotter than the entering
        # source water less the minimum HX temperature difference (#96 C11).
        actual_dT_superheat = capped_superheat(self.dT_superheat, dT_ref_water, self.dT_hx_min)

        def _cycle_unchecked(dT_tank: float, rps_fixed: float | None = None) -> dict | None:
            """Refrigerant cycle at one tank-side approach [K]; ``None`` if infeasible.

            ``rps_fixed`` holds the compressor at a given speed instead of
            solving it for the requested duty (the speed-bound closure below).
            """
            # Low-lift feasibility is enforced downstream by the compressor
            # pressure-ratio floor (PR_cycle_min); a separate fixed minimum lift is
            # redundant and non-transferable across refrigerants/operating levels.
            actual_dT_subcool = min(self.dT_subcool, max(0.0, dT_tank - self.dT_hx_min))
            T_cond_K = T_tank_w_K + dT_tank

            # Refrigerant cycle with a unit isentropic efficiency: base states and pressure ratio.
            cs_i = calc_ref_state(
                T_evap_K=T_water_sat_K,
                T_cond_K=T_cond_K,
                refrigerant=self.ref,
                eta_cmp_isen=1.0,
                dT_superheat=actual_dT_superheat,
                x_suction=self._x_suction,
                dT_subcool=actual_dT_subcool,
            )
            P_evap = cs_i["P_ref_cmp_in [Pa]"]
            P_cond = cs_i["P_ref_cmp_out [Pa]"]
            ratio = P_cond / P_evap if P_evap > 0 else 1.0

            # Compressor pressure-ratio envelope guard (see compressor_envelope.py).
            # Ceiling -> reject; floor -> clamp the cycle onto PR_cycle_min by holding
            # the evaporator (water-source) pressure and projecting the condensing
            # (tank-side) pressure, then refresh the state. Recorded for the
            # analyze_steady hint; no print here (runs inside the optimiser loop).
            self._last_pr_event = None
            pr_event_i: tuple[str, float, float] | None = None
            pr_kind = check_pr_envelope(ratio, self.PR_cycle_min, self.PR_cycle_max)
            if pr_kind == "pr_above_max":
                self._last_pr_event = ("pr_above_max", ratio, self.PR_cycle_max)
                return None
            if pr_kind == "pr_below_min":
                pr_event_i = ("pr_below_min", ratio, self.PR_cycle_min)
                self._last_pr_event = pr_event_i
                P_cond_clamp = self.PR_cycle_min * P_evap
                T_cond_K = props_si("T", "P", P_cond_clamp, "Q", 0, self.ref)
                cs_i = calc_ref_state(
                    T_evap_K=T_water_sat_K,
                    T_cond_K=T_cond_K,
                    refrigerant=self.ref,
                    eta_cmp_isen=1.0,
                    dT_superheat=actual_dT_superheat,
                    x_suction=self._x_suction,
                    dT_subcool=actual_dT_subcool,
                )
                P_evap = cs_i["P_ref_cmp_in [Pa]"]
                P_cond = cs_i["P_ref_cmp_out [Pa]"]
                ratio = P_cond / P_evap if P_evap > 0 else self.PR_cycle_min

            h_cmp_in = cs_i["h_ref_cmp_in [J/kg]"]
            h_exp_in = cs_i["h_ref_exp_in [J/kg]"]
            rho_cmp_in = cs_i["rho_ref_cmp_in [kg/m3]"]
            if (h_cmp_in - h_exp_in) <= 0:
                return None

            try:
                s_cmp_in = cs_i["s_ref_cmp_in [J/(kg·K)]"]
                h_cmp_out_isen = props_si("H", "P", P_cond, "S", s_cmp_in, self.ref)
            except ValueError:
                h_cmp_out_isen = h_cmp_in

            # Compressor speed (rev/s) at which the condenser duty matches the requested tank load.
            def _residual_rps(rps):
                eta_vol_r = _eval_eff(self.eta_cmp_vol, ratio, rps)
                eta_isen_r = _eval_eff(self.eta_cmp_isen, ratio, rps)
                h_cmp_out_local = h_cmp_in + (h_cmp_out_isen - h_cmp_in) / eta_isen_r
                dh_cond_local = h_cmp_out_local - h_exp_in
                m_dot = self.V_cmp_ref * rho_cmp_in * eta_vol_r * rps
                return (m_dot * dh_cond_local) - Q_tank_load

            if rps_fixed is None:
                rps_i, converged_i, clamped_i = solve_compressor_speed(_residual_rps, self.rps_min, self.rps_max)
            else:
                rps_i, converged_i, clamped_i = rps_fixed, True, None

            eta_vol_i = _eval_eff(self.eta_cmp_vol, ratio, rps_i)
            eta_isen_i = _eval_eff(self.eta_cmp_isen, ratio, rps_i)
            eta_em_i = _eval_eff(self.eta_cmp, ratio, rps_i)

            # Final state with the speed-resolved isentropic efficiency.
            cs_i = calc_ref_state(
                T_evap_K=T_water_sat_K,
                T_cond_K=T_cond_K,
                refrigerant=self.ref,
                eta_cmp_isen=eta_isen_i,
                dT_superheat=actual_dT_superheat,
                x_suction=self._x_suction,
                dT_subcool=actual_dT_subcool,
            )
            rho_cmp_in = cs_i["rho_ref_cmp_in [kg/m3]"]
            h_cmp_in = cs_i["h_ref_cmp_in [J/kg]"]
            h_cmp_out = cs_i["h_ref_cmp_out [J/kg]"]
            h_exp_in = cs_i["h_ref_exp_in [J/kg]"]
            if (h_cmp_out - h_exp_in) <= 0:
                return None
            m_dot_i = self.V_cmp_ref * rho_cmp_in * eta_vol_i * rps_i
            return {
                "cs": cs_i,
                "rps": rps_i,
                "converged_rps": converged_i,
                "capacity_clamped": clamped_i,
                "pr_event": pr_event_i,
                "pr": ratio,
                "eta_vol": eta_vol_i,
                "eta_isen": eta_isen_i,
                "eta_em": eta_em_i,
                "m_dot_ref": m_dot_i,
                # The approach the cycle actually runs at: equal to the input
                # unless the pressure-ratio floor moved the condensing state.
                "dT_tank": T_cond_K - T_tank_w_K,
                "Q_tank": m_dot_i * (h_cmp_out - h_exp_in),
                "Q_source": m_dot_i * (h_cmp_in - cs_i["h_ref_exp_out [J/kg]"]),
                "E_cmp": m_dot_i * (h_cmp_out - h_cmp_in) / eta_em_i,
            }

        def _cycle(dT_tank: float, rps_fixed: float | None = None) -> dict | None:
            """:func:`_cycle_unchecked`, with a state CoolProp cannot evaluate counted as infeasible."""
            try:
                return _cycle_unchecked(dT_tank, rps_fixed)
            except InvalidCompressorEfficiency:
                raise
            except Exception:
                return None

        # 2. Refrigerant cycle at the approach of the requested duty
        state = _cycle(dT_ref_tank)
        if state is None:
            return None
        if state["capacity_clamped"] is not None:
            # On a speed bound the compressor delivers a different heat than
            # was requested: re-solve the tank-side approach against the heat
            # it actually delivers (tmhp._source_boiler.close_tank_hx). When
            # the balance cannot be bracketed the request-based state stands
            # and `tank_hx_residual [W]` reports the open balance.
            closed_state = close_tank_hx(_cycle, state, dT_ref_tank, self.UA_tank_hx)
            if closed_state is not None:
                state = closed_state
        self._last_pr_event = state["pr_event"]

        cycle_states = state["cs"]
        cmp_rps = state["rps"]
        converged_rps = state["converged_rps"]
        capacity_clamped = state["capacity_clamped"]
        ratio_P_cmp = state["pr"]
        val_eta_vol = state["eta_vol"]
        val_eta_isen = state["eta_isen"]
        val_eta_electro_mech = state["eta_em"]
        m_dot_ref = state["m_dot_ref"]
        dT_ref_tank = state["dT_tank"]
        Q_ref_tank = state["Q_tank"]
        Q_ref_water = state["Q_source"]
        E_cmp = state["E_cmp"]
        h_ref_exp_in = cycle_states["h_ref_exp_in [J/kg]"]
        # Residual of the tank-side closure: zero wherever the compressor meets
        # the request, and driven to zero on a speed bound.
        tank_hx_residual = Q_ref_tank - self.UA_tank_hx * dT_ref_tank

        # 4. NTU Evaporator Analysis
        eps = calc_phase_change_hx_effectiveness(self.UA_water, self.dV_b_f_m3s * fluid.rho, fluid.cp)
        Q_water_actual = eps * m_dot_cp_b * (T_water_in_K - T_water_sat_K)
        err = Q_ref_water - Q_water_actual

        # 5. Source-water coil state
        Q_bhe = Q_ref_water - self.E_pmp

        # Fluid enters the coil at T_bhe_f_in_K
        T_bhe_f_in_K = T_water_in_K - Q_ref_water / m_dot_cp_b
        T_bhe_f_out_K = T_source_K

        T_bhe_f = (cu.K2C(T_bhe_f_in_K) + cu.K2C(T_bhe_f_out_K)) / 2
        # Source water the coil needs to return fluid at T_source with this extraction.
        T_bhe = cu.K2C(T_bhe_f_in_K + Q_bhe / (self._coil_effectiveness() * m_dot_cp_b))

        # 6. Assemble
        result: dict = reportable_state(cycle_states)
        result.update(
            {
                "hp_is_on": True,
                "converged": converged_rps,
                "converged_rps": converged_rps,
                "capacity_clamped": capacity_clamped,
                "err_Q_water [W]": err,
                "T_ref_evap_sat [°C]": cu.K2C(cycle_states.get("T_ref_evap_sat_K", np.nan)),
                "T_ref_cond_sat_v [°C]": cu.K2C(cycle_states.get("T_ref_cond_sat_l_K", np.nan)),
                "T_ref_cond_sat_l [°C]": cu.K2C(cycle_states.get("T_ref_cond_sat_l_K", np.nan)),
                "T0 [°C]": T0,
                "T_ref_cmp_in [°C]": cu.K2C(cycle_states.get("T_ref_cmp_in_K", np.nan)),
                "T_ref_cmp_out [°C]": cu.K2C(cycle_states.get("T_ref_cmp_out_K", np.nan)),
                "T_ref_exp_in [°C]": cu.K2C(cycle_states.get("T_ref_exp_in_K", np.nan)),
                "T_ref_exp_out [°C]": cu.K2C(cycle_states.get("T_ref_exp_out_K", np.nan)),
                "T_cond [°C]": cu.K2C(cycle_states.get("T_ref_cond_sat_l_K", np.nan)),
                "T_tank_w [°C]": T_tank_w,
                "T_mix_w_out [°C]": self.T_mix_w_out,
                "T_tank_w_in [°C]": self.T_tank_w_in,
                "Ts [°C]": self.Ts,
                "T_bhe [°C]": T_bhe,
                "T_bhe_f [°C]": T_bhe_f,
                "T_bhe_f_in [°C]": cu.K2C(T_bhe_f_in_K),
                "T_bhe_f_out [°C]": cu.K2C(T_bhe_f_out_K),
                "dV_bhe_f [m3/s]": self.dV_b_f_m3s,
                "dV_mix_w_out [m3/s]": flow_state.get("dV_mix_w_out", 0.0),
                "dV_tank_w_in [m3/s]": flow_state.get("dV_tank_w_in", 0.0),
                "dV_tank_w_out [m3/s]": flow_state.get("dV_tank_w_out", 0.0),
                "dV_mix_sup_w_in [m3/s]": flow_state.get("dV_mix_sup_w_in", 0.0),
                "P_ref_evap_sat [Pa]": cycle_states.get("P_ref_cmp_in [Pa]", np.nan),
                "P_ref_cond_sat_l [Pa]": cycle_states.get("P_ref_exp_in [Pa]", np.nan),
                "m_dot_ref [kg/s]": m_dot_ref,
                "cmp_rpm [rpm]": cmp_rps * 60,
                "n_star [-]": cmp_rps / self.rps_rated,
                "pr_cmp [-]": ratio_P_cmp,
                "eta_cmp_vol [-]": val_eta_vol,
                "eta_cmp_isen [-]": val_eta_isen,
                "eta_cmp [-]": val_eta_electro_mech,
                "h_ref_evap_sat [J/kg]": props_si(
                    "H", "P", cycle_states.get("P_ref_cmp_in [Pa]", 1e5), "Q", 1, self.ref
                ),
                "h_ref_cond_sat_v [J/kg]": props_si(
                    "H", "P", cycle_states.get("P_ref_cmp_out [Pa]", 1e6), "Q", 1, self.ref
                ),
                "h_ref_cond_sat_l [J/kg]": h_ref_exp_in,
                "Q_tank_load [W]": Q_tank_load,
                # Tank heat-exchanger closure: the approach the cycle ran at and
                # how far the condenser is from carrying the delivered duty.
                "dT_ref_tank [K]": dT_ref_tank,
                "tank_hx_residual [W]": tank_hx_residual,
                "Q_ref_tank [W]": Q_ref_tank,
                "Q_ref_water [W]": Q_ref_water,
                "Q_bhe [W]": Q_bhe,
                "E_cmp [W]": E_cmp,
                "E_pmp [W]": self.E_pmp,
                "E_tot [W]": E_cmp + self.E_pmp,
                "cop_ref [-]": (Q_ref_tank / E_cmp) if E_cmp > 0 else np.nan,
                "cop_sys [-]": (Q_ref_tank / (E_cmp + self.E_pmp) if (E_cmp + self.E_pmp) > 0 else np.nan),
            }
        )
        return result

    def _infeasible_cycle_reason(self) -> str:
        """Reason for a ``None`` cycle: the pressure-ratio ceiling or plain infeasibility."""
        event = getattr(self, "_last_pr_event", None)
        return "pr_above_max" if event is not None and event[0] == "pr_above_max" else "cycle_invalid"

    def _optimize_operation(
        self,
        T_tank_w: float,
        Q_tank_load: float,
        T0: float,
        *,
        flow_state: dict,
        source_temperature_K: float | None = None,
    ) -> CycleSolveOutcome:
        """Solve the evaporator approach at which the source-water HX carries the cycle.

        Returns a :class:`CycleSolveOutcome` naming the failure reason, if any.
        ``source_temperature_K`` is the fluid temperature entering the heat
        pump (default: the model's current ``T_bhe_f_out_K``).
        """
        from scipy.optimize import brentq

        self._opt_evals = 0
        evaluable = False

        def _objective(dT_water):
            nonlocal evaluable
            self._opt_evals += 1
            perf = self._calc_state(
                dT_ref_water=dT_water,
                T_tank_w=T_tank_w,
                Q_tank_load=Q_tank_load,
                T0=T0,
                flow_state=flow_state,
                source_temperature_K=source_temperature_K,
            )
            if perf is None:
                raise ValueError(f"Cycle impossible at dT_water={dT_water}")

            err = perf.get("err_Q_water [W]", np.nan)
            if np.isnan(err):
                raise ValueError(f"NaN error at dT_water={dT_water}")

            evaluable = True
            return err

        reason = "optimizer_failed"
        detail: str | None = None
        try:
            try:
                bracket = approach_bracket(_objective)
            except ValueError:
                # A cycle existed at some approach but none balances the
                # source-water HX, or no approach gave a cycle at all.
                reason = "hx_not_converged" if evaluable else self._infeasible_cycle_reason()
                raise
            # Closed far below the energy-balance check that follows (0.01 W at
            # small duties): with the capacity-scaled source conductance an
            # approach known to 1e-4 K leaves about 0.1 W open.
            opt_x = (
                bracket[0]
                if bracket[0] == bracket[1]
                else brentq(_objective, *bracket, xtol=1e-9, rtol=1e-12, maxiter=100)
            )
            final = self._calc_state(
                opt_x, T_tank_w, Q_tank_load, T0, flow_state=flow_state, source_temperature_K=source_temperature_K
            )
            if final is None:
                reason = self._infeasible_cycle_reason()
                raise ValueError("No cycle at the solved approach")
            if not final.get("converged_rps", False):
                raise ValueError("Reference operating speed did not converge")
            residual = float(final["err_Q_water [W]"])
            required = float(final["Q_ref_water [W]"])
            if not (math.isfinite(residual) and math.isfinite(required) and required > 0):
                reason = "hx_not_converged"
                raise ValueError("Source-water heat exchanger has no finite energy balance")
            if abs(residual) > max(0.01, 1e-5 * required):
                reason = "hx_not_converged"
                raise ValueError("Source-water heat exchanger did not close the energy balance")
            detail = unphysical_cycle_detail(final, self.hp_capacity)
            if detail is not None:
                reason = "cycle_invalid"
                raise ValueError(f"Solved cycle is not an operating point ({detail})")
            envelope = envelope_violation(final, self)
            if envelope is not None:
                reason = envelope
                raise ValueError("Solved cycle is outside the compressor envelope")
            return CycleSolveOutcome(True, float(opt_x), "none")
        except Exception:
            return CycleSolveOutcome(False, float("nan"), reason, detail)

    def _failed_cycle_result(
        self, T_tank_w: float, T0: float, flow_state: dict, reason: str, detail: str | None = None
    ) -> dict:
        """Off-mode row for a step the heat pump was asked to run but could not."""
        result = self._calc_off_state(T_tank_w, T0, flow_state)
        result.update({"converged": False, "failure_reason": reason})
        if detail is not None:
            result["failure_detail"] = detail
        return result

    def _thermostat_call(self, ctx: StepContext, is_on_prev: bool) -> bool:
        """Thermostat call for heat at the start of the step.

        See :meth:`GroundSourceHeatPumpBoiler._thermostat_call`.
        """
        call = determine_heat_source_on_off(
            T_tank_w_C=cu.K2C(ctx.T_tank_w_K),
            T_lower=self.T_tank_w_lower_bound,
            T_upper=self.T_tank_w_upper_bound,
            is_on_prev=is_on_prev,
            hour_of_day=ctx.hour_of_day,
            on_schedule=self.hp_on_schedule,
        )
        return call and self.hp_capacity > self.Q_tank_LOAD_OFF_TOL

    def _step_flow_state(self, ctx: StepContext, T_tank_w: float) -> dict:
        """Mixing-valve flows of the step evaluated at tank temperature ``T_tank_w`` [°C]."""
        # The step-start temperature keeps its exact Kelvin value.
        T_tank_w_K = ctx.T_tank_w_K if T_tank_w == cu.K2C(ctx.T_tank_w_K) else cu.C2K(T_tank_w)
        return self._calc_tank_flow_context(
            dV_mix_w_out=ctx.dV_mix_w_out,
            T_tank_w_K=T_tank_w_K,
            T_tank_w_in_K=self.T_tank_w_in_K,
            T_mix_w_out_K=self.T_mix_w_out_K,
        )

    def _off_cycle_result(self, ctx: StepContext, T_tank_w: float) -> dict:
        """Off-mode row (compressor stopped, nothing asked of it) at ``T_tank_w`` [°C]."""
        perf = self._calc_off_state(T_tank_w, cu.K2C(ctx.T0_K), flow_state=self._step_flow_state(ctx, T_tank_w))
        perf["failure_reason"] = "none"
        return perf

    def _solve_cycle_at(self, ctx: StepContext, T_tank_w: float) -> dict:
        """Cycle delivering ``hp_capacity`` to a tank at ``T_tank_w`` [°C].

        A running result, or an off-mode row carrying the reason the call
        could not run; see :meth:`GroundSourceHeatPumpBoiler._solve_cycle_at`
        (including the duty reduction outside the discharge limit and on the
        source fluid's freezing limit).
        """

        def at_duty(q: float) -> dict:
            return self._solve_cycle_at_duty(ctx, T_tank_w, q)

        def within_envelope(q: float) -> dict:
            return reduce_duty_within_envelope(self, at_duty(q), at_duty, q)

        return limit_duty_to_source_freeze(
            self,
            within_envelope(self.hp_capacity),
            within_envelope,
            self.hp_capacity,
            lambda detail: self._failed_cycle_result(
                T_tank_w, cu.K2C(ctx.T0_K), self._step_flow_state(ctx, T_tank_w), SOURCE_FLUID_FREEZING, detail
            ),
            once=True,
            what="analyze_dynamic",
        )

    def _solve_cycle_at_duty(self, ctx: StepContext, T_tank_w: float, Q_tank_load: float) -> dict:
        """:meth:`_solve_cycle_at` for a requested duty ``Q_tank_load`` [W]."""
        T0_C = cu.K2C(ctx.T0_K)
        flow_state = self._step_flow_state(ctx, T_tank_w)
        T_water_K = self._T_source_w_step_K

        def solve(T_source_K: float) -> tuple[CycleSolveOutcome, dict | None]:
            opt = self._optimize_operation(
                T_tank_w, Q_tank_load, T0_C, flow_state=flow_state, source_temperature_K=T_source_K
            )
            if not opt.success:
                return opt, None
            return opt, self._calc_state(
                opt.x, T_tank_w, Q_tank_load, T0_C, flow_state=flow_state, source_temperature_K=T_source_K
            )

        # The cycle is fed with the fluid the coil returns while it carries
        # this cycle's own extraction from this step's source water (#96 G1):
        # neither the previous step's fluid nor the constructor's ``Ts``.
        opt_res, solved = close_source_temperature(
            solve,
            lambda result: self._source_fluid_K(T_water_K, float(result["Q_bhe [W]"]))[2],
            # First guess: the extraction of a COP-4 cycle at this duty.
            self._source_fluid_K(T_water_K, max(0.0, rated_source_heat(Q_tank_load) - self.E_pmp))[2],
            T_water_K,
        )
        if solved is None and opt_res.success:
            # Every cycle solve succeeded but the loop did not close.
            opt_res = CycleSolveOutcome(False, float("nan"), "hx_not_converged")
        if solved is None:
            # Called but not solved: the compressor does not run this step.
            reason = opt_res.failure_reason if not opt_res.success else "cycle_invalid"
            return self._failed_cycle_result(T_tank_w, T0_C, flow_state, reason, opt_res.detail)
        solved.update({"converged": True, "failure_reason": "none"})
        return solved

    def _coil_effectiveness(self) -> float:
        """Effectiveness of the submerged coil as a single-stream heat exchanger [-]."""
        m_cp = self.source_fluid.cp * self.source_fluid.rho * self.dV_b_f_m3s
        return -math.expm1(-self.H_b / (self.R_b * m_cp))

    def _source_fluid_K(self, T_water_K: float, Q_load: float) -> tuple[float, float, float]:
        """Mean, coil-inlet and coil-outlet loop fluid temperature [K] for an extraction ``Q_load`` [W].

        A pipe of length ``H_b`` and per-length resistance ``R_b`` lying in
        source water of uniform temperature is a single-stream heat
        exchanger: ``Q = eps * m cp * (T_water - T_in)`` with
        ``eps = 1 - exp(-H_b / (R_b * m cp))``, and ``T_out = T_in + Q / (m cp)``.
        The fluid therefore never leaves the coil warmer than the water it
        takes heat from. (A mean-temperature form, ``T_mean = T_water - q R_b``
        with the loop difference split evenly around it, puts the outlet above
        the source water once ``NTU = H_b / (R_b m cp)`` exceeds 2 -- 2.2 for
        the default coil.)
        """
        m_cp = self.source_fluid.cp * self.source_fluid.rho * self.dV_b_f_m3s
        if m_cp <= 0.0 or Q_load == 0.0:
            return T_water_K, T_water_K, T_water_K
        T_in_K = T_water_K - Q_load / (self._coil_effectiveness() * m_cp)
        T_out_K = T_in_K + Q_load / m_cp
        return 0.5 * (T_in_K + T_out_K), T_in_K, T_out_K

    # =============================================================
    # Hooks
    # =============================================================

    def _get_activation_flags(self, hour_of_day: float) -> dict[str, bool]:
        flags = {}
        if self.stc is not None:
            flags["stc"] = self.stc.is_preheat_on(hour_of_day)
        return flags

    def _needs_solar_input(self) -> bool:
        return self.stc is not None

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
        def residual(T_cand_K: float) -> float:
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
                self._subsystems,
                sub_states,
                T_sur_K=self.T_sur_K,
            )[0]

        return residual

    def _run_subsystems(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        dt: float,
        T_tank_w_in_K: float,
    ) -> dict[str, dict]:
        states = {}
        for name, sub in self._subsystems.items():
            if hasattr(sub, "step"):
                states[name] = sub.step(ctx, ctrl, dt, T_tank_w_in_K)
        return states

    def _augment_results(
        self,
        r: dict,
        ctx: StepContext,
        ctrl: ControlState,
        sub_states: dict[str, dict],
        T_solved_K: float,
    ) -> dict:
        for name, sub in self._subsystems.items():
            if hasattr(sub, "assemble_results"):
                sub_record = sub.assemble_results(
                    ctx,
                    ctrl,
                    sub_states.get(name, {}),
                    T_solved_K,
                )
                r.update(sub_record)
        # E_tot is the whole electric draw: the lamp's step-average power joins
        # the compressor and the source-loop pump (the cycle assembled E_tot
        # before the subsystems reported).
        if "uv" in self._subsystems:
            add_uv_power_to_e_tot(r)
        return r

    def _postprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.postprocess_exergy(df)

    def _assemble_core_results(
        self, ctx: StepContext, ctrl: ControlState, T_solved_K: float, level_solved: float, perf: dict, flow_state: dict
    ) -> dict:
        r = perf.copy()
        r["T_tank_w [°C]"] = cu.K2C(T_solved_K)
        r["T0 [°C]"] = cu.K2C(ctx.T0_K)
        r["hp_is_on"] = ctrl.is_on

        Q_tank_loss = self.UA_tank_wall * (T_solved_K - self.T_sur_K)
        mix = calc_mixing_valve_temp(T_solved_K, self.T_tank_w_in_K, self.T_mix_w_out_K)
        T_mix_K = mix["T_mix_w_out_K"]
        r["T_mix_w_out [°C]"] = cu.K2C(T_mix_K)

        # The mixed water delivered: the demand, less what a level-varying tank
        # stopped at its lowest level could not supply.
        dV_mix_w_out = flow_state["dV_mix_w_out"]
        r["Q_tank_loss [W]"] = Q_tank_loss
        r["dV_mix_w_out [m3/s]"] = dV_mix_w_out
        r["dV_tank_w_in [m3/s]"] = flow_state["dV_tank_w_in"]
        r["dV_tank_w_out [m3/s]"] = flow_state["dV_tank_w_out"]
        r["dV_mix_sup_w_in [m3/s]"] = flow_state["dV_mix_sup_w_in"]
        r["tank_level [-]"] = 1.0  # lumped capacitance

        if not self.tank_always_full or (self.tank_always_full and self.prevent_simultaneous_flow):
            r["tank_level [-]"] = level_solved
            r["dV_mix_w_out_unmet [m3/s]"] = flow_state.get("dV_mix_w_out_unmet", 0.0)

        # Hot-water service: the heat the draw schedule asked for at the set
        # point and the part not delivered (water below the set point, or a
        # draw the tank could not supply).
        r.update(dhw_service_columns(ctx.dV_mix_w_out, dV_mix_w_out, T_mix_K, self.T_mix_w_out_K, ctx.T_sup_w_K))

        # A diagnostic of the steady solve, not a time-series quantity: keep it
        # on the analyze_steady dict and out of the dynamic frame.
        # `failure_reason` stays: it is always a string, one per step. The
        # capacity keys, `capacity_clamped` with them, are on every step
        # (_complete_dynamic_row).
        r.pop("failure_detail", None)

        return self._complete_dynamic_row(r, ctx)

    #: Keys a running ``analyze_steady`` row has and an off or failed one lacks, with that row's value.
    _STEADY_ROW_DEFAULTS: dict[str, Any] = {
        "eta_cmp [-]": math.nan,
        "eta_cmp_isen [-]": math.nan,
        "eta_cmp_vol [-]": math.nan,
        "err_Q_water [W]": math.nan,
        "n_star [-]": math.nan,
        "pr_cmp [-]": math.nan,
        "dT_ref_tank [K]": math.nan,
        "tank_hx_residual [W]": math.nan,
        "failure_detail": None,
    }

    #: Keys a running step has and an off step lacks, with the off step's value.
    _DYNAMIC_ROW_DEFAULTS: dict[str, Any] = {
        **{key: value for key, value in _STEADY_ROW_DEFAULTS.items() if key != "failure_detail"},
        "dT_ref_superheat [K]": math.nan,
        "x_ref_cmp_in [-]": math.nan,
        "discharge_control": False,
    }

    def _complete_dynamic_row(self, r: dict, ctx: StepContext) -> dict:
        """The keys every step carries, whatever the step did (in place).

        Time at the start of the step (``time [s]``, ``time [h]``, as ASHP and
        GSHP), the capacity keys (:func:`tmhp._boiler_results.complete_capacity_keys`:
        the request is ``hp_capacity`` on a step the thermostat called the
        compressor, 0 otherwise), the running-only keys of
        :attr:`_DYNAMIC_ROW_DEFAULTS` and the condenser dew point.
        """
        complete_capacity_keys(r, step_request(r, self.hp_capacity), step_average=True)
        fill_missing(r, self._DYNAMIC_ROW_DEFAULTS)
        report_condenser_dew_point(r, self.ref)
        r["time [s]"] = float(ctx.current_time_s)
        r["time [h]"] = float(ctx.current_hour)
        return r

    def _complete_steady_result(self, result: dict, Q_ref_tank: float) -> dict:
        """The keys every ``analyze_steady`` row carries, whatever the point (in place).

        The capacity keys (``Q_request [W]`` is the requested ``Q_ref_tank``
        on every row), the running-only keys of :attr:`_STEADY_ROW_DEFAULTS`, and the condenser dew point.
        """
        complete_capacity_keys(result, Q_ref_tank)
        fill_missing(result, self._STEADY_ROW_DEFAULTS)
        return report_condenser_dew_point(result, self.ref)

    # _compute_bhe_superposition removed: river water operates as infinite capacity boundary.

    # =============================================================
    # Orchestration
    # =============================================================

    def analyze_dynamic(
        self,
        simulation_period_sec: float,
        dt_s: float,
        T_tank_w_init_C: float,
        dhw_usage_schedule,
        T0_schedule,
        I_DN_schedule=None,
        I_dH_schedule=None,
        T_sup_w_schedule=None,
        T_source_w_schedule=None,
        tank_level_init: float = 1.0,
        result_save_csv_path=None,
        T_sur_schedule=None,
    ) -> pd.DataFrame:
        validate_dynamic(locals())

        time = np.arange(0, simulation_period_sec, dt_s)
        tN = len(time)
        T0_schedule = np.array(T0_schedule)

        if I_DN_schedule is None:
            I_DN_schedule = np.zeros(tN)
        if I_dH_schedule is None:
            I_dH_schedule = np.zeros(tN)

        if T_sup_w_schedule is not None:
            T_sup_w_arr = np.array(T_sup_w_schedule, dtype=float)
        else:
            T_sup_w_arr = np.full(tN, cu.K2C(self.T_tank_w_in_K))

        if T_source_w_schedule is not None:
            T_source_w_arr = np.array(T_source_w_schedule, dtype=float)
        else:
            T_source_w_arr = np.full(tN, self.Ts)

        if T_sur_schedule is not None:
            T_sur_arr = np.array(T_sur_schedule, dtype=float)
        else:
            T_sur_arr = np.full(tN, cu.K2C(self.T_sur_K))

        results_data = []

        self.time = time
        self.dt = dt_s

        T_tank_w_K = cu.C2K(T_tank_w_init_C)
        tank_level = tank_level_init
        level_varies = not self.tank_always_full or self.prevent_simultaneous_flow
        if level_varies and tank_level_init < TANK_LEVEL_MIN:
            raise ValueError(
                f"tank_level_init={tank_level_init!r}: a tank whose level varies starts with at least "
                f"TANK_LEVEL_MIN={TANK_LEVEL_MIN} of water (an empty tank has no temperature)"
            )
        is_on_prev = False
        running_prev = False
        phase_age_s = math.inf
        is_refilling = False

        self.T_bhe = self.Ts
        self.T_bhe_f_in = self.Ts
        self.T_bhe_f_in_K = self.Ts_K
        self.T_bhe_f_out = self.Ts
        # The first step's evaporator reads the Kelvin value: reset it too, or a
        # second run starts from the first run's last source temperature (#96 T13).
        self.T_bhe_f_out_K = self.Ts_K
        self.Q_bhe = 0.0
        self._source_freeze_warned = False

        # DHW schedule handling: direct m³/s flow array
        dhw_flow_m3s = np.asarray(dhw_usage_schedule, dtype=float)
        if len(dhw_flow_m3s) != tN:
            raise ValueError(f"dhw_usage_schedule length ({len(dhw_flow_m3s)}) != tN ({tN})")

        _use_solar = self._needs_solar_input()

        for n in tqdm(range(tN), desc="WSHPB Simulating"):
            t_s = time[n]
            hr = t_s * cu.s2h
            hour_of_day = (t_s % (24 * 3600)) * cu.s2h

            self.T_sur_K = cu.C2K(T_sur_arr[n])

            T0_K = cu.C2K(T0_schedule[n])
            T_sup_w_n = T_sup_w_arr[n]
            T_sup_w_K_n = cu.C2K(T_sup_w_n)

            # Set boundary condition for water source
            T_source_w_n = T_source_w_arr[n]
            if np.isnan(T_source_w_n):
                # Fallback to last known BHE wall temperature when source schedule has NaN.
                # WARNING: if all schedule values are NaN, this silently uses self.Ts (init value).
                if n == 0 or n % 43200 == 0:  # Log at start and every ~30 days
                    import warnings

                    warnings.warn(
                        f"[WSHPB] T_source_w_schedule[{n}] is NaN — falling back to "
                        f"T_bhe={self.T_bhe:.2f}°C. Check NIER data quality.",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                T_source_w_n = self.T_bhe  # fallback to last known valid

            # Source water of this step, known before the cycle is solved: the
            # coil state every row reports (and the off-mode row reads) is this
            # step's, never the previous step's.
            self.T_bhe = T_source_w_n
            self._T_source_w_step_K = cu.C2K(T_source_w_n)
            self.T_bhe_f_in_K = self.T_bhe_f_out_K = self._T_source_w_step_K

            # Subsystem activation
            activation_flags = self._get_activation_flags(hour_of_day)

            dV_mix_w_out = dhw_flow_m3s[n]

            ctx = StepContext(
                n=n,
                current_time_s=t_s,
                current_hour=hr,
                hour_of_day=hour_of_day,
                T0=T0_schedule[n],
                T0_K=T0_K,
                activation_flags=activation_flags,
                T_tank_w_K=T_tank_w_K,
                tank_level=tank_level,
                dV_mix_w_out=dV_mix_w_out,
                I_DN=I_DN_schedule[n] if _use_solar else 0.0,
                I_dH=I_dH_schedule[n] if _use_solar else 0.0,
                T_sup_w_K=T_sup_w_K_n,
            )

            # --- Phase A: Control Decisions ---
            # The thermostat call carries the hysteresis; the compressor runs
            # (and heats the tank) only when the called cycle was solved.
            call_start = self._thermostat_call(ctx, is_on_prev)
            T_start_C = cu.K2C(ctx.T_tank_w_K)

            # Refill logic
            flow_state_guess = self._calc_tank_flow_context(
                dV_mix_w_out=ctx.dV_mix_w_out,
                T_tank_w_K=ctx.T_tank_w_K,
                T_tank_w_in_K=T_sup_w_K_n,
                T_mix_w_out_K=self.T_mix_w_out_K,
            )
            dV_tank_w_in_ctrl, is_refilling = determine_tank_refill_flow(
                dt=dt_s,
                tank_level=ctx.tank_level,
                dV_tank_w_out=flow_state_guess["dV_tank_w_out"],
                V_tank_full=self.V_tank_full,
                tank_always_full=self.tank_always_full,
                prevent_simultaneous_flow=self.prevent_simultaneous_flow,
                tank_level_lower_bound=self.tank_level_lower_bound,
                tank_level_upper_bound=self.tank_level_upper_bound,
                dV_tank_w_in_refill=self.dV_tank_w_in_refill,
                is_refilling=is_refilling,
            )

            # Heat-source-free control state: subsystems do not depend on the
            # compressor, and the free tank balance is read from its residual.
            ctrl_free = ControlState(is_on=False, Q_heat_source=0.0, dV_tank_w_in_ctrl=dV_tank_w_in_ctrl)
            sub_states = self._run_subsystems(ctx, ctrl_free, dt_s, T_sup_w_K_n)

            alp_prev: float = min(
                1.0, max(0.0, (self.T_mix_w_out_K - T_sup_w_K_n) / max(1e-6, ctx.T_tank_w_K - T_sup_w_K_n))
            )
            dV_tank_w_out_prev = alp_prev * ctx.dV_mix_w_out
            dV_tank_w_in_prev = dV_tank_w_out_prev if dV_tank_w_in_ctrl is None else dV_tank_w_in_ctrl
            tank_vol_change_prev = (dV_tank_w_in_prev - dV_tank_w_out_prev) * dt_s
            level_next_approx = min(1.0, max(0.0, ctx.tank_level + tank_vol_change_prev / self.V_tank_full))
            tank_level_solve = max(TANK_LEVEL_MIN, level_next_approx)

            res_fn_free = self._build_residual_fn(
                ctx=ctx,
                ctrl=ctrl_free,
                dt_s=dt_s,
                T_tank_w_in_K_n=T_sup_w_K_n,
                T_sup_w_K_n=T_sup_w_K_n,
                tank_level=tank_level_solve,
                sub_states=sub_states,
            )
            # Thermostat events inside the step (#96 T6): the compressor runs
            # until the tank reaches the upper bound and starts when it
            # reaches the lower bound, not for whole steps.
            run, cycle_result = resolve_step_operation(
                T_start_K=ctx.T_tank_w_K,
                call_start=call_start,
                running_prev=running_prev,
                phase_age_s=phase_age_s,
                scheduled=check_hp_schedule_active(ctx.hour_of_day, self.hp_on_schedule),
                T_lower=self.T_tank_w_lower_bound,
                T_upper=self.T_tank_w_upper_bound,
                dt=dt_s,
                C_curr=self.C_tank * max(TANK_LEVEL_MIN, ctx.tank_level),
                C_next=self.C_tank * max(TANK_LEVEL_MIN, tank_level_solve),
                C_tank=self.C_tank,
                residual_free=res_fn_free,
                solve_cycle=lambda T_C, _fraction, ctx=ctx: self._solve_cycle_at(ctx, T_C),
                Q_request=self.hp_capacity,
                min_on_time_s=self.min_on_time_s,
                min_off_time_s=self.min_off_time_s,
            )
            hp_result = step_hp_result(
                run,
                cycle_result,
                lambda ctx=ctx, T_start_C=T_start_C: self._off_cycle_result(ctx, T_start_C),
                self._RUNTIME_SCALED_KEYS,
            )
            is_on_prev = run.call_end
            running_prev = run.running_end
            phase_age_s = run.phase_age_end_s
            hp_is_on = bool(hp_result.get("hp_is_on", False))

            ctrl = ControlState(
                is_on=hp_is_on,
                Q_heat_source=delivered_tank_heat(hp_result),
                dV_tank_w_in_ctrl=dV_tank_w_in_ctrl,
            )

            # --- Phase B: Implicit Solving ---
            res_fn = self._build_residual_fn(
                ctx=ctx,
                ctrl=ctrl,
                dt_s=dt_s,
                T_tank_w_in_K_n=T_sup_w_K_n,
                T_sup_w_K_n=T_sup_w_K_n,
                tank_level=tank_level_solve,
                sub_states=sub_states,
            )

            # A solve that does not return a root is recovered with the same
            # residual (tmhp._tank_solve): heat pump, draw-off, loss and
            # subsystem heat stay in the balance, and a step that cannot be
            # solved is named in a warning.
            T_solved_K = solve_lumped_tank(
                res_fn,
                ctx.T_tank_w_K,
                T_start_K=ctx.T_tank_w_K,
                dt=dt_s,
                C_curr=self.C_tank * max(TANK_LEVEL_MIN, ctx.tank_level),
                C_next=self.C_tank * max(TANK_LEVEL_MIN, tank_level_solve),
                C_tank=self.C_tank,
                who=type(self).__name__,
                step=n,
            )
            warn_if_tank_boiling(T_solved_K)

            # Flows and end level the solved balance carried
            # (tmhp.dynamic_context.tank_step_flows, the function the residual
            # itself uses): a level-varying tank stops the draw at
            # TANK_LEVEL_MIN and the make-up at full, and what the draw could
            # not supply is unmet hot water. They used to be the unlimited
            # draw, so a tank drained below its make-up reported level 0 and
            # water it did not hold (#96 T1: +9.9 kWh, +32 %, in 1 h).
            flows = tank_step_flows(T_solved_K, ctx, ctrl, dt_s, T_sup_w_K_n, self.T_mix_w_out_K, self.V_tank_full)
            flow_state_final = flows.as_flow_state()
            level_next = flows.level_next

            # Source-water loop. The row's Q_bhe is the step average of an
            # extraction that flows only while the compressor runs. Reported
            # fluid temperatures are step-time averages (linear in the load,
            # so the average load's values; continuous in the run fraction);
            # T_bhe_f*_running are the fluid while the compressor runs (NaN when
            # it did not), for the evaporator's source-side exergy. The cycle
            # was solved against that running fluid (:meth:`_solve_cycle_at_duty`).
            Q_bhe_avg = hp_result.get("Q_bhe [W]", 0.0) if hp_is_on else 0.0
            self.Q_bhe = Q_bhe_avg

            # Infinite heat capacity boundary (River/Lake), no ground interference
            T_bhe_K = self._T_source_w_step_K

            T_bhe_f_K, self.T_bhe_f_in_K, T_bhe_f_out_K = self._source_fluid_K(T_bhe_K, Q_bhe_avg)
            self.T_bhe_f = cu.K2C(T_bhe_f_K)
            self.T_bhe_f_in = cu.K2C(self.T_bhe_f_in_K)
            self.T_bhe_f_out = cu.K2C(T_bhe_f_out_K)
            self.T_bhe_f_out_K = T_bhe_f_out_K
            fraction = float(hp_result.get("runtime_fraction [-]", 1.0)) if hp_is_on else 0.0
            if hp_is_on and fraction > 0.0:
                running = tuple(cu.K2C(x) for x in self._source_fluid_K(T_bhe_K, Q_bhe_avg / fraction))
            else:
                running = (np.nan, np.nan, np.nan)
            hp_result["T_bhe_f_running [°C]"] = running[0]
            hp_result["T_bhe_f_in_running [°C]"] = running[1]
            hp_result["T_bhe_f_out_running [°C]"] = running[2]

            # Apply BHE state to hp_result
            hp_result["Ts [°C]"] = (
                T_source_w_n  # Override: record timestep-specific river water temp (not the static init value self.Ts)
            )
            hp_result["T_bhe [°C]"] = self.T_bhe
            hp_result["T_bhe_f [°C]"] = self.T_bhe_f
            hp_result["T_bhe_f_in [°C]"] = self.T_bhe_f_in
            hp_result["T_bhe_f_out [°C]"] = self.T_bhe_f_out

            # Assemble step results
            step_record = self._assemble_core_results(ctx, ctrl, T_solved_K, level_next, hp_result, flow_state_final)
            self._augment_results(step_record, ctx, ctrl, sub_states, T_solved_K)
            results_data.append(step_record)

            # Step forward
            T_tank_w_K = T_solved_K
            tank_level = level_next

        # No forward fill: a NaN here is a quantity that does not exist on that
        # step (COP, compressor and refrigerant states while the compressor is
        # stopped). Tank, source and flow columns are written on every step.
        results_df = pd.DataFrame(results_data)
        results_df = self._postprocess(results_df)

        if result_save_csv_path:
            results_df.to_csv(result_save_csv_path, index=False)

        warn_if_dhw_unmet(results_df, dt_s, type(self).__name__, cu.K2C(self.T_mix_w_out_K))
        return results_df

    def _steady_freeze_off(self, arguments: Mapping[str, Any], detail: str) -> dict:
        """Off-state ``analyze_steady`` result of a point the freezing limit refuses."""
        flow_state = {"dV_mix_w_out": 0.0, "dV_tank_w_out": 0.0, "dV_tank_w_in": 0.0, "dV_mix_sup_w_in": 0.0}
        result = self._failed_cycle_result(
            arguments["T_tank_w"], arguments["T0"], flow_state, SOURCE_FLUID_FREEZING, detail
        )
        result.update({"Q_tank_loss [W]": 0.0, "tank_level [-]": 1.0})
        return result

    def _report_part_load(self, result: dict, Q_ref_tank: float) -> None:
        """Write the cycling keys of an ``analyze_steady`` row; average it if the unit cycles.

        A row on the compressor's speed floor that delivers more than the
        request becomes the mean over the on/off cycling period
        (:func:`tmhp.cycling.report_boiler_cycling`): the rates a dynamic step
        averages with its run time are multiplied by ``capacity_ratio [-]``,
        the electrical inputs by ``capacity_ratio / part_load_factor``.
        """
        report_boiler_cycling(
            result,
            self.cycling,
            Q_ref_tank,
            rate_keys=self._CYCLING_RATE_KEYS,
            power_keys=self._CYCLING_POWER_KEYS,
            source_heat_key=self._CYCLING_SOURCE_HEAT_KEY,
            hp_capacity=self.hp_capacity,
        )
        self._complete_steady_result(result, Q_ref_tank)

    @steady_cycling(duty_arg="Q_ref_tank")
    @source_freeze_limited(duty_arg="Q_ref_tank", off="_steady_freeze_off")
    @steady_envelope(duty_arg="Q_ref_tank")
    def analyze_steady(
        self,
        T_tank_w: float,
        T_source: float,
        Q_ref_tank: float,
        T0: float = 0.0,
        *,
        return_dict: bool = True,
    ) -> dict | pd.DataFrame:
        """Run a steady-state performance snapshot.

        Evaluates the refrigerant cycle at a given operating point
        (``T_tank_w``, ``T_source``, ``Q_ref_tank``) **without** solving the tank energy
        balance or tracking dynamic flows.

        Parameters
        ----------
        T_tank_w : float
            Tank water temperature [°C] — treated as a given input.
        T_source : float
            Source fluid temperature entering the heat pump [°C].
        Q_ref_tank : float
            Target condenser heat rate [W].
        T0 : float
            Dead-state / outdoor-air temperature [°C] (for exergy calculations).
        return_dict : bool
            If ``True`` return dict; else single-row DataFrame.

        Returns
        -------
        dict | pd.DataFrame
            Cycle state plus diagnostic flags. Notable keys:

            - ``"converged"`` (bool) — True only when the evaporator approach
              closed the source-water heat exchanger and the cycle is an
              operating point.
            - ``"failure_reason"`` (str) — ``"none"`` for a solved point;
              otherwise ``"cycle_invalid"`` (no cycle, or a cycle that is
              not an operating point, see
              :func:`tmhp.dynamic_context.unphysical_cycle_detail`),
              ``"pr_above_max"`` (pressure-ratio ceiling),
              ``"discharge_temp_above_max"`` (even a reduced duty exceeds
              ``T_dis_max``; a request above the limit that a smaller duty
              meets returns that duty with ``capacity_clamped="max"`` and
              ``capacity_binding="discharge_temperature"``),
              ``"condensing_temp_above_max"`` (the same for the condensing
              limit ``T_cond_max``: the tank is too hot for any duty; a
              reduced duty reports ``capacity_binding="condensing_temperature"``),
              ``"hx_not_converged"`` (the source-water heat exchanger cannot
              carry the duty at any approach), ``"optimizer_failed"`` or
              ``"source_fluid_freezing"`` (the loop liquid would run below its
              operating limit, 3 °C for water, at every duty; a request that
              a smaller duty keeps on the limit returns that duty with
              ``capacity_clamped="max"`` and
              ``capacity_binding="source_fluid_freezing"``; see
              :mod:`tmhp.source_fluid`).
            - ``"failure_detail"`` (str, failures only) — which check
              rejected a ``"cycle_invalid"`` point.
            - ``"capacity_clamped"``, ``"capacity_binding"``, ``"Q_max [W]"``
              and ``"Q_request [W]"`` are on every row (``None`` or NaN where
              they do not apply; ``"Q_request [W]"`` is the ``Q_ref_tank``
              asked for), and an off or failed row carries every key of a
              running one (NaN where it has no value): the keys do not depend
              on the point. ``"T_ref_cond_sat_v [°C]"`` is the dew point of
              the condensing pressure, above ``"T_ref_cond_sat_l [°C]"`` by
              the glide for a zeotropic blend.
            - ``"runtime_fraction [-]"``, ``"capacity_ratio [-]"``,
              ``"part_load_factor [-]"``, ``"Q_min [W]"`` and
              ``"E_cycling_loss [W]"`` — on every row. A request below the
              duty at ``rps_min`` (``capacity_clamped == "min"``) is reported
              as the mean of on/off cycling: ``Q_ref_tank [W]`` equals the
              request (``Q_min [W]`` is the continuous duty), heat and
              refrigerant rates are the running machine's times
              ``capacity_ratio [-]`` -- the compressor runs that share of the
              time, ``runtime_fraction [-]`` -- and the electrical inputs
              (compressor and source pump) carry the EN 14825 degradation
              for a unit delivering to water (``Cd_cycling``: the running
              values times ``capacity_ratio / part_load_factor``), so both
              COPs are the continuous ones times the part-load factor.
              ``Q_bhe [W]`` is the mean source-side heat less that pump
              power. Temperatures, pressures and speed are those of the
              running machine. With ``Cd_cycling=None`` the row is the
              continuous minimum-speed point and ``Q_ref_tank [W]`` exceeds
              the request.

            Every reason other than ``"none"`` comes with an off-mode state:
            ``Q_ref_tank [W] = E_cmp [W] = 0`` and NaN COP. No unsolved point
            is returned with cycle numbers.
        """
        validate_steady(locals())
        import contextlib
        import warnings

        # Empty flow state as steady state ignores dynamic withdrawal/refill
        flow_state = {
            "dV_mix_w_out": 0.0,
            "dV_tank_w_out": 0.0,
            "dV_tank_w_in": 0.0,
            "dV_mix_sup_w_in": 0.0,
            "alp": 0.0,
        }

        # Override T_bhe_f_out_K so that _calc_state uses T_source correctly
        self.T_bhe_f_out_K = cu.C2K(T_source)

        if Q_ref_tank <= 0:
            result = self._calc_off_state(
                T_tank_w=T_tank_w,
                T0=T0,
                flow_state=flow_state,
            )
        else:
            opt_result = self._optimize_operation(
                T_tank_w=T_tank_w,
                Q_tank_load=Q_ref_tank,
                T0=T0,
                flow_state=flow_state,
            )
            result = None
            if opt_result.success:
                with contextlib.suppress(Exception):
                    result = self._calc_state(
                        dT_ref_water=opt_result.x,
                        T_tank_w=T_tank_w,
                        Q_tank_load=Q_ref_tank,
                        T0=T0,
                        flow_state=flow_state,
                    )

            # A successful search has already checked that the cycle closes
            # both heat exchangers and is an operating point; anything else is
            # reported with the search's own reason and an off-mode state.
            if result is None:
                failure_reason = opt_result.failure_reason if not opt_result.success else "cycle_invalid"
                detail = opt_result.detail
                warnings.warn(
                    f"analyze_steady: fell back to HP-off state "
                    f"(reason={failure_reason!r}"
                    + (f", detail={detail!r}" if detail else "")
                    + f", T_tank_w={T_tank_w:.1f}°C, T_source={T_source:.1f}°C, "
                    f"Q_ref_tank={Q_ref_tank:.0f}W). "
                    "Consider increasing UA_rated or fan-flow rated.",
                    RuntimeWarning,
                    stacklevel=2,
                )
                result = self._failed_cycle_result(T_tank_w, T0, flow_state, failure_reason, detail)
            else:
                result["converged"] = True
                result["failure_reason"] = "none"

            # Pressure-ratio envelope hint for the final operating point (one
            # message per call; per-probe events inside the optimiser are
            # silent). Floor -> clamp (cycle still solved); ceiling -> reject
            # (HP-off fallback).
            pr_event = self._last_pr_event
            if pr_event is not None:
                kind, pr_val, bound = pr_event
                if kind == "pr_below_min":
                    print(
                        f"[PR guard] clamp 하한(below PR_cycle_min): "
                        f"PR={pr_val:.3f} -> {bound:.2f} "
                        f"(T_tank_w={T_tank_w:.1f}°C, T_source={T_source:.1f}°C, "
                        f"Q_ref_tank={Q_ref_tank:.0f}W)"
                    )
                else:  # pr_above_max
                    print(
                        f"[PR guard] reject 상한(above PR_cycle_max): "
                        f"PR={pr_val:.3f} > {bound:.2f} "
                        f"(T_tank_w={T_tank_w:.1f}°C, T_source={T_source:.1f}°C, "
                        f"Q_ref_tank={Q_ref_tank:.0f}W)"
                    )

        if result is None:
            result = {}

        if result:
            # Steady state doesn't have tank loss because we don't solve tank mass/energy balance
            result["Q_tank_loss [W]"] = 0.0
            result.setdefault("failure_reason", "none")  # zero load: a valid off result, not a failure
            result["tank_level [-]"] = 1.0  # steady-state: always_full

        if return_dict:
            return result
        return pd.DataFrame([result])

    def postprocess_exergy(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute GSHPB-specific exergy variables."""
        from .thermodynamics import (
            calc_energy_flow,
            calc_refrigerant_exergy,
            convert_electricity_to_exergy,
            source_loop_exergy_flows,
        )

        df = df.copy()
        T0_K = cu.C2K(df["T0 [°C]"])
        T_tank_K = cu.C2K(df["T_tank_w [°C]"])

        df["Q_tank_w_out [W]"] = calc_energy_flow(c_w * rho_w * df["dV_tank_w_out [m3/s]"].fillna(0), T_tank_K, T0_K)

        # 1. Refrigerant state points
        df = calc_refrigerant_exergy(df, self.ref, T0_K)
        df = convert_electricity_to_exergy(df)

        # 2. Exergy flows of the source loop: evaluated at the running state
        # and averaged over the step like the heat and power in the row.
        X_bhe_in, X_bhe_out, X_water_in, X_water_out = source_loop_exergy_flows(df, T0_K)
        # Loop boundary flows (step averages) so the source-side balance can be read from the frame.
        df["X_bhe_in [W]"] = X_bhe_in
        df["X_bhe_out [W]"] = X_bhe_out

        Q_ref_tank = df["Q_ref_tank [W]"].fillna(0)
        Q_ref_water = df["Q_ref_water [W]"].fillna(0)
        df["X_ref_tank [W]"] = np.where(
            Q_ref_tank > 0,
            # Condenser heat at the bubble-point condensing temperature: the value
            # T_ref_cond_sat_v repeated before it reported a zeotrope's dew point.
            Q_ref_tank * (1 - T0_K / cu.C2K(df["T_ref_cond_sat_l [°C]"])),
            0.0,
        )
        df["X_ref_water [W]"] = np.where(
            Q_ref_water > 0,
            Q_ref_water * (1 - T0_K / cu.C2K(df["T_ref_evap_sat [°C]"])),
            0.0,
        )

        df["X_tank_w_in [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_tank_w_in [m3/s]"].fillna(0), cu.C2K(df["T_tank_w_in [°C]"]), T0_K
        )
        df["X_tank_w_out [W]"] = calc_exergy_flow(c_w * rho_w * df["dV_tank_w_out [m3/s]"].fillna(0), T_tank_K, T0_K)

        df["X_mix_w_out [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_mix_w_out [m3/s]"].fillna(0), cu.C2K(df["T_mix_w_out [°C]"]), T0_K
        )
        df["X_mix_sup_w_in [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_mix_sup_w_in [m3/s]"].fillna(0), cu.C2K(df["T_tank_w_in [°C]"]), T0_K
        )

        df["X_tank_loss [W]"] = df["Q_tank_loss [W]"] * (1 - T0_K / T_tank_K)

        tank_lvl = df["tank_level [-]"].fillna(1.0) if "tank_level [-]" in df.columns else 1.0
        C_tank_actual = self.C_tank * tank_lvl
        T_tank_K_prev = T_tank_K.shift(1)
        df["Xst_tank [W]"] = (1 - T0_K / T_tank_K) * C_tank_actual * (T_tank_K - T_tank_K_prev) / self.dt
        df.loc[df.index[0], "Xst_tank [W]"] = 0.0

        import typing

        # Subsystems exergy
        X_sub_tot_add = typing.cast(typing.Any, 0.0)
        X_sub_in_tank_add = typing.cast(typing.Any, 0.0)
        X_sub_out_tank_add = typing.cast(typing.Any, 0.0)

        for _name, sub in self._subsystems.items():
            if hasattr(sub, "calc_exergy"):
                ex_res = sub.calc_exergy(df, T0_K)
                if ex_res is not None:
                    for col_name, s in ex_res.columns.items():
                        df[col_name] = s
                    X_sub_tot_add = X_sub_tot_add + ex_res.X_tot_add
                    X_sub_in_tank_add = X_sub_in_tank_add + ex_res.X_in_tank_add
                    X_sub_out_tank_add = X_sub_out_tank_add + ex_res.X_out_tank_add

        # Components Destruction
        df["X_tot [W]"] = df["E_cmp [W]"] + df["E_pmp [W]"] + df.get("X_uv [W]", 0.0) + X_sub_tot_add

        df["Xc_cmp [W]"] = df["X_cmp [W]"] + df["X_ref_cmp_in [W]"] - df["X_ref_cmp_out [W]"]
        ref_tank_active = Q_ref_tank > 0
        df["Xc_ref_tank [W]"] = np.where(
            ref_tank_active,
            (df["X_ref_cmp_out [W]"] - df["X_ref_exp_in [W]"]) - df["X_ref_tank [W]"],
            0.0,
        )
        df["Xc_exp [W]"] = df["X_ref_exp_in [W]"] - df["X_ref_exp_out [W]"]
        df["Xc_water [W]"] = (X_water_in - X_water_out) - df["X_ref_water [W]"]
        df["Xc_pmp [W]"] = df["E_pmp [W]"] - (X_water_in - X_bhe_out)

        X_in_tank = (
            df["X_ref_tank [W]"].fillna(0)
            + df["X_tank_w_in [W]"].fillna(0)
            + df.get("X_uv [W]", 0.0)
            + X_sub_in_tank_add
        )
        X_out_tank = df["Xst_tank [W]"] + df["X_tank_w_out [W]"].fillna(0) + X_sub_out_tank_add
        df["Xc_tank [W]"] = X_in_tank - X_out_tank

        df["Xc_mix [W]"] = (
            df["X_tank_w_out [W]"].fillna(0) + df["X_mix_sup_w_in [W]"].fillna(0) - df["X_mix_w_out [W]"].fillna(0)
        )

        # Efficiency
        df["X_eff_ref [-]"] = df["X_ref_tank [W]"] / df["X_cmp [W]"].replace(0, np.nan)
        df["X_eff_sys [-]"] = df["X_ref_tank [W]"] / df["X_tot [W]"].replace(0, np.nan)

        return df
