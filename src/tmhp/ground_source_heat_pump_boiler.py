"""Integrated System Model: Ground Source Heat Pump Boiler (GSHPB).

This system class orchestrates the dynamic interaction between distinct thermodynamic
sub-components to simulate the overall heating performance. While implemented as an
integrated model, its physical calculations represent the behavior of:

1. **Refrigerant Cycle (Vapor-Compression):**
   Evaluates thermodynamic states using CoolProp, enforcing superheat/subcool margins.
2. **Heat Pump Compressor:**
   Models the compression process using isentropic and volumetric efficiencies to compute
   the actual discharge enthalpy and mass flow rate. The compressor power is determined
   from the enthalpy difference and the mass flow rate.
3. **Expansion Valve:**
   Modeled as an isenthalpic expansion device (constant enthalpy) that throttles the
   refrigerant from the condensing pressure down to the evaporating pressure.
4. **Heat Exchangers (Condenser & Evaporator):**

   - **Condenser:** Placed inside the hot-water tank (hydronic), utilizing a static
     overall heat transfer coefficient (UA_tank_hx).
   - **Evaporator:** Coupled to a borehole heat exchanger (BHE) fluid loop, acting as
     a secondary heat exchanger to absorb heat from the circulating ground fluid.
5. **Thermal Storage Tank:**
   Modeled with lumped-capacitance and DHW mixing logic by default
   (``tank_model="lumped"``). ``"stratified"`` and ``"hybrid"`` resolve the
   tank in nodes; the condenser is then a coil that heats only the nodes
   colder than the refrigerant, and the cycle condenses at the temperature
   that coil needs.
6. **Ground Source Heat Exchanger (Borefield):**
   A dynamic multi-borehole simulation using pygfunction-based g-functions. It tracks
   the transient thermal response of the ground, enabling robust modeling of long-term
   ground temperature drift due to continuous heat extraction.

At each time step the model finds the minimum-power operating point via 1D Brent
optimization over the evaporator approach temperature difference, while the
condenser temperature is solved analytically.

.. note::
   See the project paper for the underlying refrigerant-cycle theory and the
   pygfunction-based borehole heat-exchanger model used here.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd
from tqdm import tqdm

from . import calc_util as cu
from ._source_boiler import (
    BOREHOLE_DEPTH_DEFAULT_M,
    approach_bracket,
    capped_superheat,
    close_source_temperature,
    close_tank_hx,
    default_borehole_grid,
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
from .borehole_capacity import configure_borehole_dynamics
from .compressor_efficiency import InvalidCompressorEfficiency, _eval_eff, reject_invalid_efficiency
from .compressor_envelope import (
    PR_CYCLE_MAX_SINGLE_STAGE,
    RATED_SPEED_SEARCH_MAX,
    X_SUCTION_MIN_DEFAULT,
    check_pr_envelope,
    discharge_controlled,
    envelope_violation,
    reduce_duty_within_envelope,
    resolve_discharge_temp_max,
    resolve_x_suction_min,
    steady_envelope,
)
from .compressor_speed import default_displacement, solve_compressor_speed
from .constants import c_w, k_w, mu_w, rho_w
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
    scale_running_rates,
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
from .g_function import precompute_gfunction
from .ground_coupling import (
    AggregateGFunctionCoupler,
    GroundCoupler,
    physical_wall_rise,
    reset_ground_coupler,
    run_window_preview,
)
from .ground_flow_control import (
    close_ground_temperature,
    select_ground_flow,
    solve_ground_approach,
)
from .ground_loop import (
    calc_borefield_linear_load,
    calc_borehole_count,
    calc_total_borehole_length,
    configure_ground_flow,
    ground_flow_state,
    ground_hx_UA,
    ground_result_diagnostics,
    resolve_ground_flow_rates,
)
from .heat_exchanger import calc_phase_change_hx_effectiveness
from .heat_transfer import calc_simple_tank_UA
from .hybrid_tank import HybridStratifiedTank
from .reference_state import HXSide, RatingCondition, ReferenceStateMixin
from .refrigerant import calc_ref_state, props_si, reportable_state
from .stratified_tank import StratifiedTank
from .subsystems import add_uv_power_to_e_tot
from .thermodynamics import calc_exergy_flow

if TYPE_CHECKING:
    from .subsystems import SolarThermalCollector

#: Multi-node tank backends of ``tank_model`` (``"lumped"`` is the single node).
NODAL_TANK_MODELS: tuple[str, ...] = ("stratified", "hybrid")

#: The mixing valve of a multi-node tank is set on the water that leaves the
#: top during the step; the draw flow is iterated until that temperature moves
#: by less than this [K], at most :data:`NODAL_DRAW_ITERATIONS` times.
NODAL_DRAW_TOL_K: float = 1.0e-3
NODAL_DRAW_ITERATIONS: int = 6

#: The condensing temperature of a multi-node tank step is at least the one at
#: which the coil, with this multiple of its conductance, passes the step's
#: heat to the nodes as they are at the end of the step. 2: the end-of-step
#: approach is at least half the nominal ``Q / UA_tank_hx``.
NODAL_END_CONDUCTANCE: float = 2.0

#: Share of the step's condenser heat below which a multi-node tank that could
#: not take it counts as having taken none (the compressor did not run).
NODAL_MIN_RUN_SHARE: float = 1.0e-6


class GroundSourceHeatPumpBoiler(ReferenceStateMixin):
    """Ground source heat pump boiler with BHE and lumped-tank model.

    The refrigerant cycle is resolved via CoolProp with user-specified
    superheat / subcool margins. An optimizer minimises total cycle
    electrical input subject to NTU-based evaporator constraints and
    analytical condenser temperature relations.

    ``hp_capacity`` and ``ref`` are enough to build a machine; the sizes left
    as ``None`` follow the capacity (:mod:`tmhp._source_boiler`), so the rated
    cycle is the same at every capacity:

    - ``UA_tank_hx = hp_capacity / 5`` [W/K] (5 K condensing approach);
    - ``UA_ground = 0.18 * hp_capacity`` [W/K];
    - source-loop flow ``ground_flow_ref_lpm = 0.75 * hp_capacity / (rho c * 3 K)``
      (3.58 L/min per kW);
    - ``E_pmp = 75 W/ton * hp_capacity`` (21.3 W per kW, ASHRAE pumping grade B
      upper bound; used when ``hydraulic_pump`` is off);
    - ``N_1``, ``N_2``, ``H_b``: with none given, 200 m boreholes on a
      near-square grid, as many as keep the rated source-side heat
      (``0.75 * hp_capacity``) at or below 50 W/m (one borehole at 8 kW).

    A value the caller gives is used as given. ``UserWarning`` is raised for a
    caller's field above 80 W/m, for a laminar default flow in the U-tubes, and
    for an upper tank set point whose condensing temperature
    (``T_tank_w_upper_bound + hp_capacity / UA_tank_hx``) is at or above the
    refrigerant's critical temperature.

    In every running result the suction superheat is at most the evaporator
    approach less ``dT_hx_min``; on a compressor speed bound the tank heat
    exchanger is closed on the heat actually delivered (``dT_ref_tank [K]``,
    ``tank_hx_residual [W]``); and each dynamic step's cycle is solved against
    the ground fluid of that same step.

    **Borehole dynamics.** ``borehole_dynamics=None`` (default) adds the heat
    capacity of fluid, pipe and grout and the fluid transit through the
    U-tube to the ground response, from the geometry and properties already
    given (:mod:`tmhp.borehole_capacity`): the fluid no longer moves by
    ``q' R_b`` (3 to 5 K) the instant the compressor starts or stops. It is
    used wherever the model can use it; with ``variable_Rb``, a variable
    ground flow, a ``ground_coupler`` of the caller's or a cross-section that
    cannot be mapped, the model keeps the steady borehole.
    ``model.borehole_dynamics`` (``"transit"`` or ``"off"``) and
    ``model.borehole_dynamics_note`` say which and why. ``True`` raises
    instead of falling back; ``False`` is the steady borehole (a resistance
    without heat capacity), with the results the model gave before this
    default. ``T_bhe [°C]`` is the physical borehole wall in both.
    """

    # Heat and power rates of the running cycle; a step reports them
    # multiplied by the fraction of the step the compressor ran. Ground-loop
    # flows stay at their running values (an off step reports the nominal
    # loop flow too).
    _RUNTIME_SCALED_KEYS: tuple[str, ...] = (
        "E_cmp [W]",
        "E_cmp_plus_pmp [W]",
        "E_pmp [W]",
        "E_tot [W]",
        "Q_bhe [W]",
        "Q_ref_ground [W]",
        "Q_ref_tank [W]",
        "Q_tank_load [W]",
        "m_dot_ref [kg/s]",
    )
    _RATING_FAMILY = "GSHPB"
    _RATING_DEFAULT_MODE = "heating"

    def __init__(
        self,
        # 1. Refrigerant / cycle / compressor
        ref: str = "R410A",
        V_cmp_ref: float | None = None,
        eta_cmp_isen: float | Callable | None = None,
        eta_cmp_vol: float | Callable | None = None,
        eta_cmp: float | Callable | None = None,
        # 2. Heat exchanger UA
        UA_tank_hx: float | None = None,
        UA_ground: float | None = None,
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
        # Tank model: "lumped" (single-node, default — unchanged legacy
        # behaviour) or "stratified" (multi-node, geolink×tmhp G2 tank backend).
        tank_model: str = "lumped",
        n_tank_nodes: int = 10,
        condenser_node: int | None = None,
        # 4. Borehole heat exchanger (Field + Params)
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
        # 6. Superheat / subcool
        dT_superheat: float = 3.0,
        dT_subcool: float = 3.0,
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
        boundary_condition: str = "uniform_temperature",
        # Ground response backend (dependency inversion). Default = single
        # field-average g-function (legacy behaviour). Inject a richer coupler
        # (e.g. geolink's resolved multi-borehole network) to replace the lumped
        # g-function with full borehole-to-borehole superposition.
        ground_coupler: GroundCoupler | None = None,
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
        # Borehole heat capacity and fluid transit: None = on where the model
        # can use them, True = required, False = steady borehole. Keyword-only:
        # the positional order of the arguments above is unchanged.
        borehole_dynamics: bool | None = None,
        # Deprecated:
        refrigerant: str | None = None,
        V_disp_cmp: float | None = None,
        eta_cmp_mech: float | Callable | None = None,
        UA_tank: float | None = None,  # deprecated alias for UA_tank_hx
        UA_cond_design: float | None = None,
        UA_evap_design: float | None = None,
        ground_flow_ref_lpm: float | None = None,
        ground_flow_constant_lpm: float | None = None,
        ground_flow_min_lpm: float | None = None,
        ground_flow_max_lpm: float | None = None,
        ground_flow_control: str = "constant",
        variable_ground_flow: bool = False,
        variable_ground_hx_UA: bool = False,
        variable_Rb: bool = False,
        hydraulic_pump: bool = False,
        pump_efficiency: float = 0.6,
        pump_map: Mapping[str, Any] | None = None,
        pipe_inner_diameter: float | None = None,
        pipe_roughness: float = 1e-6,
        dp_common: float | None = None,
        dp_aux_ref: float = 0.0,
        dp_aux_exponent: float = 2.0,
        ground_flow_min_ratio: float | None = None,
        ground_flow_max_ratio: float | None = None,
        m_dot_ref_rated: float | None = None,
        rated_condition: Mapping[str, Any] | RatingCondition | None = None,
        ground_hx_fluid_fraction: float = 0.5,
        ground_hx_refrigerant_fraction: float = 0.3,
        ground_hx_constant_fraction: float = 0.2,
        ground_hx_fluid_exponent: float = 0.8,
        ground_hx_refrigerant_exponent: float = 0.8,
    ) -> None:
        validate_ctor("GSHPB", locals())
        # Capacity-scaled defaults (tmhp._source_boiler): a machine given only
        # hp_capacity and a refrigerant keeps the same rated performance at any
        # capacity. Every value the caller supplies is used as given.
        flow_defaulted = dV_b_f_lpm is None and ground_flow_ref_lpm is None
        if N_1 is None and N_2 is None and H_b is None:
            H_b = BOREHOLE_DEPTH_DEFAULT_M
            N_1, N_2 = default_borehole_grid(hp_capacity, H_b)
        else:
            N_1 = 1 if N_1 is None else N_1
            N_2 = 1 if N_2 is None else N_2
            H_b = BOREHOLE_DEPTH_DEFAULT_M if H_b is None else H_b
            warn_if_field_overloaded(hp_capacity, calc_borehole_count(N_1, N_2), H_b, "GroundSourceHeatPumpBoiler")
        if E_pmp is None:
            E_pmp = default_pump_power(hp_capacity)
        ground_rates = resolve_ground_flow_rates(
            default_ref_lpm=default_source_flow_lpm(hp_capacity),
            legacy_ref_lpm=dV_b_f_lpm,
            min_ratio=ground_flow_min_ratio,
            max_ratio=ground_flow_max_ratio,
            ref_lpm=ground_flow_ref_lpm,
            constant_lpm=ground_flow_constant_lpm,
            min_lpm=ground_flow_min_lpm,
            max_lpm=ground_flow_max_lpm,
        )
        self.ground_flow_ref_lpm = ground_rates["ref_lpm"]
        self.ground_flow_constant_lpm = ground_rates["constant_lpm"]
        self.ground_flow_min_lpm = ground_rates["min_lpm"]
        self.ground_flow_max_lpm = ground_rates["max_lpm"]
        dV_b_f_lpm = self.ground_flow_ref_lpm
        if refrigerant is not None:
            import warnings

            warnings.warn(
                "GroundSourceHeatPumpBoiler(refrigerant=...) is deprecated; use ref=... instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            ref = refrigerant

        # Resolve deprecated mapping
        if V_cmp_ref is None:
            V_cmp_ref = V_disp_cmp if V_disp_cmp is not None else default_displacement(hp_capacity)
        if eta_cmp is None:
            eta_cmp = eta_cmp_mech
        if UA_tank_hx is None:
            UA_tank_hx = (
                UA_tank
                if UA_tank is not None
                else (UA_cond_design if UA_cond_design is not None else default_tank_hx_UA(hp_capacity))
            )
        if UA_ground is None:
            UA_ground = UA_evap_design if UA_evap_design is not None else default_source_hx_UA(hp_capacity)

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
        self.UA_ground = UA_ground

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
        # Discharge-temperature control: lowest suction quality (None: no control)
        self.x_suction_min: float | None = resolve_x_suction_min(x_suction_min)
        #: Suction quality of the cycle being evaluated (< 1 only inside the control).
        self._x_suction: float = 1.0
        # Records the PR-envelope event of the most recent _calc_state call
        # (None | ("pr_below_min", pr, bound) | ("pr_above_max", pr, bound)).
        self._last_pr_event: tuple[str, float, float] | None = None

        # BHE properties
        self.n_boreholes = calc_borehole_count(N_1, N_2)
        self.total_borehole_length = calc_total_borehole_length(self.n_boreholes, H_b)
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
        self.dV_b_f_m3s = dV_b_f_lpm / 60000
        if flow_defaulted:
            warn_if_laminar(self.dV_b_f_m3s, self.n_boreholes, r_in, "GroundSourceHeatPumpBoiler")

        if R_b is None:
            from .borehole import (
                calc_effective_borehole_thermal_resistance,
                calc_local_borehole_thermal_resistance,
            )

            n_boreholes = max(1, self.N_1 * self.N_2)
            m_flow_total = self.dV_b_f_m3s * rho_w
            m_flow_pipe = m_flow_total / n_boreholes

            R_b_local, R_a = calc_local_borehole_thermal_resistance(
                k_s=self.k_s,
                k_g=k_g,
                k_p=k_p,
                r_b=self.r_b,
                r_out=r_out,
                r_in=r_in,
                D_s=D_s,
                m_flow_pipe=m_flow_pipe,
                rho_f=rho_w,
                mu_f=mu_w,
                cp_f=c_w,
                k_f=k_w,
            )
            self.R_b = calc_effective_borehole_thermal_resistance(
                R_b=R_b_local,
                R_a=R_a,
                H=self.H_b,
                m_flow_pipe=m_flow_pipe,
                cp_f=c_w,
                boundary_condition=boundary_condition,
            )
        else:
            self.R_b = R_b

        if not all(np.isfinite(v) and v > 0 for v in (self.UA_tank_hx, self.UA_ground)):
            raise ValueError("Heat exchanger UA values must be positive and finite")
        warn_if_setpoint_unreachable(
            what="GroundSourceHeatPumpBoiler",
            ref=ref,
            T_tank_w_upper_bound=T_tank_w_upper_bound,
            hp_capacity=hp_capacity,
            UA_tank_hx=self.UA_tank_hx,
        )
        if not 0 < self.rps_min < self.rps_max or not np.isfinite(self.rps_max):
            raise ValueError("Require finite 0 < rps_min < rps_max")
        # Compressor reference state at rated (fixed) UA, before variable
        # ground-HX UA is configured with the m_dot_ref_rated it produces.
        efficiencies = self._initialize_reference_state(
            rps_rated=rps_rated,
            m_dot_ref_rated=m_dot_ref_rated,
            efficiencies={
                "eta_cmp_isen": eta_cmp_isen,
                "eta_cmp_vol": eta_cmp_vol,
                "eta_cmp": eta_cmp,
            },
            rated_condition=rated_condition,
        )
        self.eta_cmp_isen = efficiencies["eta_cmp_isen"]
        self.eta_cmp_vol = efficiencies["eta_cmp_vol"]
        self.eta_cmp = efficiencies["eta_cmp"]
        self._ground_settings = configure_ground_flow(
            control=ground_flow_control,
            variable_ground_flow=variable_ground_flow,
            variable_UA=variable_ground_hx_UA,
            variable_Rb=variable_Rb,
            hydraulic_pump=hydraulic_pump,
            volume_flow_ref=self.dV_b_f_m3s,
            volume_flow_constant=self.ground_flow_constant_lpm / 60000,
            volume_flow_min=self.ground_flow_min_lpm / 60000,
            volume_flow_max=self.ground_flow_max_lpm / 60000,
            n_boreholes=self.n_boreholes,
            H_b=self.H_b,
            R_b=self.R_b,
            R_b_supplied=R_b is not None,
            pump_power=self.E_pmp,
            pump_efficiency=pump_efficiency,
            pump_map=dict(pump_map) if pump_map is not None else None,
            pipe_inner_diameter=pipe_inner_diameter if pipe_inner_diameter is not None else 2 * r_in,
            pipe_roughness=pipe_roughness,
            dp_common=dp_common,
            dp_aux_ref=dp_aux_ref,
            dp_aux_exponent=dp_aux_exponent,
            m_dot_ref_rated=self.m_dot_ref_rated,
            ua_fractions=(
                ground_hx_fluid_fraction,
                ground_hx_refrigerant_fraction,
                ground_hx_constant_fraction,
            ),
            ua_exponents=(ground_hx_fluid_exponent, ground_hx_refrigerant_exponent),
            boundary_condition=boundary_condition,
            geometry=dict(
                k_s=k_s,
                k_g=k_g,
                k_p=k_p,
                r_b=r_b,
                r_out=r_out,
                r_in=r_in,
                D_s=D_s,
                rho_f=rho_w,
                mu_f=mu_w,
                k_f=k_w,
            ),
        )
        self.ground_flow_control = self._ground_settings["control"]

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

        # Tank backend selection (swappable). Default "lumped" keeps the
        # single-node path (self._tank stays None). "stratified" (multi-node
        # finite volume, sub-stepped to Courant <= 1) and "hybrid" (the same
        # with a tracked thermocline) build a multi-node tank from the same
        # geometry for the always-full buffer-tank case. The condenser is a
        # coil immersed in the tank: UA_tank_hx spread over the nodes
        # (condenser_node=None) or at one node; it heats only nodes colder
        # than the condensing temperature (see _solve_nodal_tank). The
        # thermostat reads the volume-average temperature, the hot draw leaves
        # the top. Subsystems and partial fill are not supported in these modes.
        self.tank_model = tank_model
        self.condenser_node = condenser_node
        self._tank: StratifiedTank | HybridStratifiedTank | None = None
        #: Result of the multi-node tank's last step (None for the lumped tank).
        self._tank_step: dict | None = None
        if tank_model in NODAL_TANK_MODELS:
            if self._subsystems:
                raise NotImplementedError(f"{tank_model} tank_model does not yet support subsystems (stc/pv/uv)")
            if not tank_always_full:
                raise NotImplementedError(
                    f"{tank_model} tank_model assumes an always-full tank (tank_always_full=True)"
                )
            if condenser_node is not None and not -n_tank_nodes <= condenser_node < n_tank_nodes:
                raise ValueError(f"condenser_node must index one of the {n_tank_nodes} nodes — got {condenser_node}")
            geometry: dict[str, Any] = dict(
                n_nodes=n_tank_nodes,
                volume=self.V_tank_full,
                height=self.tank_physical["H"],
                ua=self.UA_tank_wall,
            )
            if tank_model == "stratified":
                self._tank = StratifiedTank(**geometry, max_courant=1.0)
            else:
                self._tank = HybridStratifiedTank(**geometry)
        elif tank_model != "lumped":
            raise ValueError(f"tank_model must be 'lumped', 'stratified' or 'hybrid' — got {tank_model!r}")

        # Precompute g-function
        self.dt_s: float = dt_s
        self._gfunc_interp = precompute_gfunction(
            N_1=N_1,
            N_2=N_2,
            B=B,
            H_b=H_b,
            D_b=D_b,
            r_b=r_b,
            alpha_s=self.alp_s,
            k_s=k_s,
            t_max_s=t_max_s,
            dt_s=dt_s,
        )

        # Ground-response backend: default wraps the single field-average
        # g-function above (byte-identical to the legacy inline superposition);
        # an injected coupler takes over the borehole-wall temperature response.
        self._ground_coupler: GroundCoupler = (
            ground_coupler if ground_coupler is not None else AggregateGFunctionCoupler(self._gfunc_interp)
        )
        # Borehole heat capacity and fluid transit (tmhp.borehole_capacity):
        # on wherever the model can use them, see the class docstring.
        #: ``"transit"``, ``"lumped"`` or ``"off"``: the borehole response in use.
        self.borehole_dynamics: str = "off"
        #: Why it is not ``"transit"`` (empty otherwise).
        self.borehole_dynamics_note: str = ""
        configure_borehole_dynamics(self, borehole_dynamics, injected_coupler=ground_coupler is not None)
        # The coupler's run-window preview for the run in progress (set by
        # analyze_dynamic; None with a steady borehole, see _running_wall_K).
        self._run_window: Callable[[int, np.ndarray, float, float], float] | None = None

        # Simulation state tracking (dynamically updated in analyze_dynamic)
        self.time: np.ndarray = np.array([])
        self.dt: float = dt_s
        self._opt_evals: int = 0
        self.T_bhe_f: float = self.Ts
        self.T_bhe: float = self.Ts
        self.T_bhe_f_out: float = self.Ts
        self.T_bhe_f_out_K: float = self.Ts_K
        self.Q_bhe: float = 0.0

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

    def _rating_side(self, role: str, mode: str, T_in_C: float) -> HXSide:
        """Ground-loop evaporator (source) and tank-immersed condenser (load)."""
        if role == "source":
            return HXSide("water", T_in_C, self.UA_ground, self.dV_b_f_m3s)
        return HXSide("tank", T_in_C, self.UA_tank_hx)

    @discharge_controlled(reject=False)
    @reject_invalid_efficiency
    def _calc_state(
        self,
        dT_ref_ground: float,
        T_tank_w: float,
        Q_tank_load: float,
        T0: float,
        *,
        flow_state: dict,
        ground_flow_ratio: float = 1.0,
        source_temperature_K: float | None = None,
    ) -> dict | None:
        self._last_pr_event = None
        loop = ground_flow_state(self._ground_settings, ground_flow_ratio)
        is_active = Q_tank_load > 0

        # 1. Analytical Condenser Approach Temperature
        dT_ref_tank = Q_tank_load / self.UA_tank_hx if is_active else 0.0
        T_tank_w_K = cu.C2K(T_tank_w)
        _t_bhe = getattr(self, "T_bhe_f_out_K", None)
        T_b_out_K = float(_t_bhe) if _t_bhe is not None else cu.C2K(15.0)
        if source_temperature_K is not None:
            T_b_out_K = source_temperature_K
        m_dot_cp_b = loop["dV"] * rho_w * c_w
        T_ground_in_K = T_b_out_K + (loop["E_pmp"] / m_dot_cp_b)
        T_ground_sat_K = T_ground_in_K - dT_ref_ground
        T_tank_sat_K = T_tank_w_K + dT_ref_tank

        if not is_active:
            # Flow state (explicit parameter, no side-effect reads)
            dV_tank_w_out = flow_state.get("dV_tank_w_out", 0.0)
            dV_tank_w_in = flow_state.get("dV_tank_w_in", 0.0)
            dV_mix_sup_w_in = flow_state.get("dV_mix_sup_w_in", 0.0)
            dV_mix_w_out_val = flow_state.get("dV_mix_w_out", 0.0)

            if dV_mix_w_out_val == 0:
                T_mix_w_out_val = np.nan
            else:
                mix = calc_mixing_valve_temp(
                    T_tank_w_K,
                    self.T_tank_w_in_K,
                    self.T_mix_w_out_K,
                )
                T_mix_w_out_val = cu.K2C(mix["T_mix_w_out_K"])

            cs = calc_ref_state(
                T_evap_K=T_ground_sat_K,
                T_cond_K=T_tank_sat_K,
                refrigerant=self.ref,
                eta_cmp_isen=1.0,
                mode="heating",
                dT_superheat=self.dT_superheat,
                dT_subcool=self.dT_subcool,
                is_active=False,
            )

            inactive_result = reportable_state(cs)
            inactive_result.update(
                {
                    "hp_is_on": False,
                    "converged": True,
                    "converged_rps": True,
                    "capacity_clamped": None,
                    "err_Q_ground [W]": 0.0,
                    # Temperatures [°C]
                    "T_tank_w [°C]": T_tank_w,
                    "T0 [°C]": T0,
                    "T_mix_w_out [°C]": T_mix_w_out_val,
                    "T_tank_w_in [°C]": self.T_tank_w_in,
                    "Ts [°C]": self.Ts,
                    "T_bhe [°C]": getattr(self, "T_bhe", self.Ts),
                    "T_bhe_f [°C]": getattr(self, "T_bhe_f", self.Ts),
                    "T_bhe_f_in [°C]": cu.K2C(getattr(self, "T_bhe_f_in_K", self.Ts_K)),
                    "T_bhe_f_out [°C]": cu.K2C(getattr(self, "T_bhe_f_out_K", self.Ts_K)),
                    "T_cond [°C]": T_tank_w,
                    # Volume flow rates [m3/s]
                    "dV_mix_w_out [m3/s]": (dV_mix_w_out_val if dV_mix_w_out_val > 0 else np.nan),
                    "dV_tank_w_out [m3/s]": (dV_tank_w_out if dV_tank_w_out > 0 else np.nan),
                    "dV_tank_w_in [m3/s]": (dV_tank_w_in if dV_tank_w_in > 0 else np.nan),
                    "dV_mix_sup_w_in [m3/s]": (dV_mix_sup_w_in if dV_mix_sup_w_in > 0 else np.nan),
                    "dV_bhe_f [m3/s]": loop["dV"],
                    "m_dot_ref [kg/s]": 0.0,
                    "cmp_rpm [rpm]": 0.0,
                    # Energy rates [W]
                    "Q_bhe [W]": 0.0,
                    "Q_ref_tank [W]": 0.0,
                    "Q_ref_ground [W]": 0.0,
                    "Q_tank_load [W]": 0.0,
                    "E_cmp [W]": 0.0,
                    "E_pmp [W]": 0.0,
                    "E_tot [W]": 0.0,
                    # COP metrics
                    "cop_ref [-]": np.nan,
                    "cop_sys [-]": np.nan,
                }
            )
            ground_result_diagnostics(
                inactive_result,
                self._ground_settings,
                loop,
                0.0,
                self.UA_ground,
                0.0,
                0.0,
            )
            return inactive_result

        # The suction gas leaves the evaporator no hotter than the entering
        # ground fluid less the minimum HX temperature difference (#96 C11).
        actual_dT_superheat = capped_superheat(self.dT_superheat, dT_ref_ground, self.dT_hx_min)

        def _cycle_unchecked(dT_tank: float, rps_fixed: float | None = None) -> dict | None:
            """Refrigerant cycle at one tank-side approach [K]; ``None`` if infeasible.

            ``rps_fixed`` holds the compressor at a given speed instead of
            solving it for the requested duty (the speed-bound closure below).
            """
            # Low-lift feasibility is enforced downstream by the compressor
            # pressure-ratio floor (PR_cycle_min); a separate fixed minimum lift is
            # redundant and non-transferable across refrigerants/operating levels.
            actual_dT_subcool: float = min(self.dT_subcool, max(0.0, dT_tank - self.dT_hx_min))
            T_cond_K = T_tank_w_K + dT_tank

            # Refrigerant cycle with a unit isentropic efficiency: base states and pressure ratio.
            cs_i = calc_ref_state(
                T_evap_K=T_ground_sat_K,
                T_cond_K=T_cond_K,
                refrigerant=self.ref,
                eta_cmp_isen=1.0,
                mode="heating",
                dT_superheat=actual_dT_superheat,
                x_suction=self._x_suction,
                dT_subcool=actual_dT_subcool,
                is_active=True,
            )
            P_evap = cs_i["P_ref_cmp_in [Pa]"]
            P_cond = cs_i["P_ref_cmp_out [Pa]"]
            ratio = P_cond / P_evap if P_evap > 0 else 1.0

            # Compressor pressure-ratio envelope guard (see compressor_envelope.py).
            # Ceiling -> reject; floor -> clamp the cycle onto PR_cycle_min by holding
            # the evaporator (ground) pressure and projecting the condensing
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
                    T_evap_K=T_ground_sat_K,
                    T_cond_K=T_cond_K,
                    refrigerant=self.ref,
                    eta_cmp_isen=1.0,
                    mode="heating",
                    dT_superheat=actual_dT_superheat,
                    x_suction=self._x_suction,
                    dT_subcool=actual_dT_subcool,
                    is_active=True,
                )
                P_evap = cs_i["P_ref_cmp_in [Pa]"]
                P_cond = cs_i["P_ref_cmp_out [Pa]"]
                ratio = P_cond / P_evap if P_evap > 0 else self.PR_cycle_min

            h_cmp_in = cs_i["h_ref_cmp_in [J/kg]"]
            h_exp_in = cs_i["h_ref_exp_in [J/kg]"]
            rho_cmp_in = cs_i["rho_ref_cmp_in [kg/m3]"]
            if h_cmp_in - h_exp_in <= 0:
                return None

            try:
                s_cmp_in = cs_i["s_ref_cmp_in [J/(kg·K)]"]
                h_cmp_out_isen = props_si("H", "P", P_cond, "S", s_cmp_in, self.ref)
            except ValueError:
                h_cmp_out_isen = h_cmp_in

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

            cs_i = calc_ref_state(
                T_evap_K=T_ground_sat_K,
                T_cond_K=T_cond_K,
                refrigerant=self.ref,
                eta_cmp_isen=eta_isen_i,
                mode="heating",
                dT_superheat=actual_dT_superheat,
                x_suction=self._x_suction,
                dT_subcool=actual_dT_subcool,
                is_active=True,
            )
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
                "Q_tank": m_dot_i * (cs_i["h_ref_cmp_out [J/kg]"] - h_exp_in),
                "Q_source": m_dot_i * (h_cmp_in - cs_i["h_ref_exp_out [J/kg]"]),
                "E_cmp": m_dot_i * (cs_i["h_ref_cmp_out [J/kg]"] - h_cmp_in) / eta_em_i,
            }

        def _cycle(dT_tank: float, rps_fixed: float | None = None) -> dict | None:
            """:func:`_cycle_unchecked`, with a state CoolProp cannot evaluate counted as infeasible."""
            try:
                return _cycle_unchecked(dT_tank, rps_fixed)
            except InvalidCompressorEfficiency:
                raise
            except ValueError:
                return None

        # 2. Refrigerant cycle at the approach of the requested duty
        state = _cycle_unchecked(dT_ref_tank)
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

        cs = state["cs"]
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
        Q_ref_ground = state["Q_source"]
        E_cmp = state["E_cmp"]
        h_ref_exp_in = cs["h_ref_exp_in [J/kg]"]
        # Residual of the tank-side closure: zero wherever the compressor meets
        # the request, and driven to zero on a speed bound.
        tank_hx_residual = Q_ref_tank - self.UA_tank_hx * dT_ref_tank

        UA_ground_actual = ground_hx_UA(self._ground_settings, self.UA_ground, ground_flow_ratio, m_dot_ref)

        # 4. NTU Evaporator Analysis
        eps = calc_phase_change_hx_effectiveness(UA_ground_actual, loop["dV"] * rho_w, c_w)
        Q_ground_actual = eps * m_dot_cp_b * (T_ground_in_K - T_ground_sat_K)

        # 5. BHE state
        Q_bhe = Q_ref_ground - loop["E_pmp"]
        Q_bhe_unit = calc_borefield_linear_load(Q_bhe, self.n_boreholes, self.H_b)

        # Fluid enters BHE at T_bhe_f_in_K
        T_bhe_f_in_K = T_ground_in_K - Q_ref_ground / m_dot_cp_b
        T_bhe_f_out_K = T_b_out_K

        T_bhe_f = (cu.K2C(T_bhe_f_in_K) + cu.K2C(T_bhe_f_out_K)) / 2
        T_bhe = T_bhe_f + Q_bhe_unit * loop["R_b"]

        # 6. Assemble
        active_result: dict = reportable_state(cs)
        active_result.update(
            {
                "hp_is_on": True,
                "converged": converged_rps,
                "converged_rps": converged_rps,
                "capacity_clamped": capacity_clamped,
                "err_Q_ground [W]": Q_ref_ground - Q_ground_actual,
                "T_ref_evap_sat [°C]": cu.K2C(cs.get("T_ref_evap_sat_K", np.nan)),
                "T_ref_cond_sat_v [°C]": cu.K2C(cs.get("T_ref_cond_sat_l_K", np.nan)),
                "T_ref_cond_sat_l [°C]": cu.K2C(cs.get("T_ref_cond_sat_l_K", np.nan)),
                "T0 [°C]": T0,
                "T_ref_cmp_in [°C]": cu.K2C(cs.get("T_ref_cmp_in_K", np.nan)),
                "T_ref_cmp_out [°C]": cu.K2C(cs.get("T_ref_cmp_out_K", np.nan)),
                "T_ref_exp_in [°C]": cu.K2C(cs.get("T_ref_exp_in_K", np.nan)),
                "T_ref_exp_out [°C]": cu.K2C(cs.get("T_ref_exp_out_K", np.nan)),
                "T_cond [°C]": cu.K2C(cs.get("T_ref_cond_sat_l_K", np.nan)),
                "T_tank_w [°C]": T_tank_w,
                "T_mix_w_out [°C]": self.T_mix_w_out,
                "T_tank_w_in [°C]": self.T_tank_w_in,
                "Ts [°C]": self.Ts,
                "T_bhe [°C]": T_bhe,
                "T_bhe_f [°C]": T_bhe_f,
                "T_bhe_f_in [°C]": cu.K2C(T_bhe_f_in_K),
                "T_bhe_f_out [°C]": cu.K2C(T_bhe_f_out_K),
                "dV_bhe_f [m3/s]": loop["dV"],
                "dV_mix_w_out [m3/s]": flow_state.get("dV_mix_w_out", 0.0),
                "dV_tank_w_in [m3/s]": flow_state.get("dV_tank_w_in", 0.0),
                "dV_tank_w_out [m3/s]": flow_state.get("dV_tank_w_out", 0.0),
                "dV_mix_sup_w_in [m3/s]": flow_state.get("dV_mix_sup_w_in", 0.0),
                "P_ref_evap_sat [Pa]": cs.get("P_ref_cmp_in [Pa]", np.nan),
                "P_ref_cond_sat_l [Pa]": cs.get("P_ref_exp_in [Pa]", np.nan),
                "m_dot_ref [kg/s]": m_dot_ref,
                "cmp_rpm [rpm]": cmp_rps * 60,
                "pr_cmp [-]": ratio_P_cmp,
                "eta_cmp_vol [-]": val_eta_vol,
                "eta_cmp_isen [-]": val_eta_isen,
                "eta_cmp [-]": val_eta_electro_mech,
                "cmp_rps [rev/s]": cmp_rps,
                "n_star [-]": cmp_rps / self.rps_rated,
                "eta_is [-]": val_eta_isen,
                "eta_v [-]": val_eta_vol,
                "eta_em [-]": val_eta_electro_mech,
                "h_ref_evap_sat [J/kg]": props_si("H", "P", cs.get("P_ref_cmp_in [Pa]", 1e5), "Q", 1, self.ref),
                "h_ref_cond_sat_v [J/kg]": props_si("H", "P", cs.get("P_ref_cmp_out [Pa]", 1e6), "Q", 1, self.ref),
                "h_ref_cond_sat_l [J/kg]": h_ref_exp_in,
                "Q_tank_load [W]": Q_tank_load,
                # Tank heat-exchanger closure: the approach the cycle ran at and
                # how far the condenser is from carrying the delivered duty.
                "dT_ref_tank [K]": dT_ref_tank,
                "tank_hx_residual [W]": tank_hx_residual,
                "Q_ref_tank [W]": Q_ref_tank,
                "Q_ref_ground [W]": Q_ref_ground,
                "Q_bhe [W]": Q_bhe,
                "E_cmp [W]": E_cmp,
                "E_pmp [W]": loop["E_pmp"],
                "E_tot [W]": E_cmp + loop["E_pmp"],
                "cop_ref [-]": (Q_ref_tank / E_cmp) if E_cmp > 0 else np.nan,
                "cop_sys [-]": (Q_ref_tank / (E_cmp + loop["E_pmp"])) if (E_cmp + loop["E_pmp"]) > 0 else np.nan,
            }
        )
        ground_result_diagnostics(
            active_result,
            self._ground_settings,
            loop,
            ground_flow_ratio,
            UA_ground_actual,
            Q_ground_actual,
            Q_ref_ground,
        )
        return active_result

    def _solve_ground_flow_point(
        self,
        ratio: float,
        T_tank_w: float,
        Q_tank_load: float,
        T0: float,
        flow_state: dict,
        wall_K: float | Callable[[float], float] | None,
    ) -> dict:
        def evaluate(approach: float) -> dict | None:
            if wall_K is None:
                try:
                    return self._calc_state(
                        approach,
                        T_tank_w,
                        Q_tank_load,
                        T0,
                        flow_state=flow_state,
                        ground_flow_ratio=ratio,
                    )
                except (ValueError, OverflowError, ZeroDivisionError):
                    return None
            return close_ground_temperature(
                lambda temperature: self._calc_state(
                    approach,
                    T_tank_w,
                    Q_tank_load,
                    T0,
                    flow_state=flow_state,
                    ground_flow_ratio=ratio,
                    source_temperature_K=temperature,
                ),
                wall_K,
                self.n_boreholes,
                self.H_b,
            )

        result = solve_ground_approach(evaluate, "Q_ref_tank [W]", Q_tank_load)
        if result.get("failure_reason") == "cycle_invalid" and self._last_pr_event is not None:
            result["failure_reason"] = "pressure_ratio_limit"
        return result

    def _solve_ground_flow(
        self,
        T_tank_w: float,
        Q_tank_load: float,
        T0: float,
        *,
        flow_state: dict,
        ground_flow_ratio: float | None = None,
        T_bhe_wall: float | None = None,
        wall_response: Callable[[float], float] | None = None,
    ) -> dict:
        if Q_tank_load <= 0:
            row = self._calc_state(5, T_tank_w, 0, T0, flow_state=flow_state)
            assert row is not None
            row.update({"failure_reason": "none", "hx_feasible": True})
            return row
        wall_K = (
            wall_response if wall_response is not None else (cu.C2K(T_bhe_wall) if T_bhe_wall is not None else None)
        )
        selected = select_ground_flow(
            lambda ratio: self._solve_ground_flow_point(ratio, T_tank_w, Q_tank_load, T0, flow_state, wall_K),
            self._ground_settings,
            ground_flow_ratio,
        )
        if "h_ref_cmp_in [J/kg]" not in selected:
            off = self._calc_state(5, T_tank_w, 0, T0, flow_state=flow_state) or {}
            off.update(selected)
            selected = off
        if selected.get("hp_is_on") and selected.get("failure_reason") == "none":
            detail = unphysical_cycle_detail(selected, self.hp_capacity)
            if detail is not None:
                selected = self._failed_cycle_result(T_tank_w, T0, flow_state, "cycle_invalid", detail)
            elif (envelope := envelope_violation(selected, self)) is not None:
                selected = self._failed_cycle_result(T_tank_w, T0, flow_state, envelope)
        return selected

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
        """Solve the evaporator approach at which the ground HX carries the cycle.

        Returns a :class:`CycleSolveOutcome`; on failure it names the reason
        instead of swallowing it, so callers can report the step.
        ``source_temperature_K`` is the fluid temperature entering the heat
        pump (default: the model's current ``T_bhe_f_out_K``).
        """
        from scipy.optimize import brentq

        self._opt_evals = 0
        evaluable = False

        def _objective(dT_ground):
            nonlocal evaluable
            self._opt_evals += 1
            perf = self._calc_state(
                dT_ref_ground=dT_ground,
                T_tank_w=T_tank_w,
                Q_tank_load=Q_tank_load,
                T0=T0,
                flow_state=flow_state,
                source_temperature_K=source_temperature_K,
            )
            if perf is None:
                raise ValueError(f"Cycle impossible at dT_ground={dT_ground}")

            err = perf.get("err_Q_ground [W]", np.nan)
            if np.isnan(err):
                raise ValueError(f"NaN error at dT_ground={dT_ground}")

            evaluable = True
            return err

        # Default for anything not classified below (e.g. brentq running out
        # of iterations): the cycle existed but the search did not settle.
        reason = "optimizer_failed"
        detail: str | None = None
        try:
            try:
                bracket = approach_bracket(_objective)
            except ValueError:
                # Some approach gave a cycle, yet no approach in the searched
                # band (tmhp._source_boiler.approach_bracket) balances it
                # against the ground HX: the HX cannot carry the duty.
                # Otherwise no approach gave a cycle at all.
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
            # Use the existing ground-HX feasibility tolerance, including for
            # the default constant-flow path.
            residual = float(final["err_Q_ground [W]"])
            required = float(final["Q_ref_ground [W]"])
            if not (math.isfinite(residual) and math.isfinite(required) and required > 0):
                reason = "hx_not_converged"
                raise ValueError("Ground heat exchanger has no finite energy balance")
            if abs(residual) > max(0.01, 1e-5 * required):
                reason = "hx_not_converged"
                raise ValueError("Ground heat exchanger did not close the energy balance")
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
        """Off-mode row for a step the heat pump was asked to run but could not.

        No heat, no power, no COP: the compressor did not run. ``hp_is_on`` is
        ``False`` and ``failure_reason`` carries why.
        """
        result = self._calc_state(5.0, T_tank_w, 0.0, T0, flow_state=flow_state) or {}
        result.update({"hp_is_on": False, "converged": False, "failure_reason": reason})
        if detail is not None:
            result["failure_detail"] = detail
        return result

    def _thermostat_call(self, ctx: StepContext, is_on_prev: bool) -> bool:
        """Thermostat call for heat at the start of the step.

        Hysteresis on the tank temperature with the operating schedule
        applied (and no call when ``hp_capacity`` is below the off
        tolerance). :func:`tmhp.dynamic_context.resolve_step_operation`
        follows the call through the step.
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

    def _ground_flow_result(
        self, ctx: StepContext, T_tank_w: float, Q_tank_load: float, runtime_fraction: float = 1.0
    ) -> dict:
        """Coupled ground-flow solve (``ground_flow_control`` active) for one request.

        The fluid-to-wall resistance and the loop temperature difference act
        on the running extraction inside the flow solve. The wall they start
        from is that of :meth:`_running_wall_K`: with the steady borehole the
        step mean under the step-average load (a compressor that runs
        ``runtime_fraction`` of the step loads the ground with that fraction
        of its running extraction, #108), with the dynamic borehole the mean
        over the run under the running load.
        """
        preview = getattr(self._ground_coupler, "preview_wall_temperature_rise", None)
        if preview is None:
            raise ValueError("Coupled ground flow requires a coupler with preview_wall_temperature_rise")
        fraction = min(1.0, max(0.0, float(runtime_fraction)))

        def wall_response(q_total):
            return self._running_wall_K(ctx.n, q_total, fraction * q_total, fraction)

        return self._solve_ground_flow(
            T_tank_w,
            Q_tank_load,
            cu.K2C(ctx.T0_K),
            flow_state=self._step_flow_state(ctx, T_tank_w),
            wall_response=wall_response,
        )

    def _off_cycle_result(self, ctx: StepContext, T_tank_w: float) -> dict:
        """Off-mode row (compressor stopped, nothing asked of it) at ``T_tank_w`` [°C]."""
        if self._ground_settings["active"]:
            return self._ground_flow_result(ctx, T_tank_w, 0.0)
        flow_state = self._step_flow_state(ctx, T_tank_w)
        perf = self._calc_state(5.0, T_tank_w, 0.0, cu.K2C(ctx.T0_K), flow_state=flow_state) or {}
        perf.update({"converged": True, "failure_reason": "none"})
        return perf

    def _solve_cycle_at(self, ctx: StepContext, T_tank_w: float, runtime_fraction: float = 1.0) -> dict:
        """Cycle delivering ``hp_capacity`` to a tank at ``T_tank_w`` [°C].

        A running result, or an off-mode row (no heat to the tank, no heat
        from the ground, no power) carrying the reason the call could not run.
        The condenser heat is what the solved cycle delivers, never the
        requested capacity. ``runtime_fraction`` is the expected run time of
        the step, for the coupled ground-flow path (see
        :meth:`_ground_flow_result`). Outside the discharge limit the step
        runs at the largest duty inside it, as ``analyze_steady`` does
        (:func:`reduce_duty_within_envelope`).
        """
        result = self._solve_cycle_at_duty(ctx, T_tank_w, self.hp_capacity, runtime_fraction)
        return reduce_duty_within_envelope(
            self,
            result,
            lambda q: self._solve_cycle_at_duty(ctx, T_tank_w, q, runtime_fraction),
            self.hp_capacity,
        )

    def _solve_cycle_at_duty(
        self, ctx: StepContext, T_tank_w: float, Q_tank_load: float, runtime_fraction: float = 1.0
    ) -> dict:
        """:meth:`_solve_cycle_at` for a requested duty ``Q_tank_load`` [W]."""
        if self._ground_settings["active"]:
            return self._ground_flow_result(ctx, T_tank_w, Q_tank_load, runtime_fraction)

        T0_C = cu.K2C(ctx.T0_K)
        flow_state = self._step_flow_state(ctx, T_tank_w)
        opt_res, solved = self._solve_at_step_source(ctx, T_tank_w, Q_tank_load, T0_C, flow_state, runtime_fraction)
        if solved is None:
            # The compressor did not run: no condenser heat to the tank, no
            # heat drawn from the ground, no power.
            reason = opt_res.failure_reason if not opt_res.success else "cycle_invalid"
            return self._failed_cycle_result(T_tank_w, T0_C, flow_state, reason, opt_res.detail)
        solved.update({"converged": True, "failure_reason": "none"})
        return solved

    def _running_source_K(self, n: int, Q_bhe_run: float, fraction: float, R_b: float, m_cp_b: float) -> float:
        """Fluid temperature leaving the borefield while the compressor extracts ``Q_bhe_run`` [W].

        The same relations :meth:`_compute_bhe_superposition` reports the
        running fluid with: the wall of :meth:`_running_wall_K` (the
        coupler's uncommitted preview, a compressor running ``fraction`` of
        the step), and the fluid-to-wall resistance and the loop temperature
        difference acting on the running net extraction.
        """
        Q_inj = float(self._ground_injection_W())
        T_wall_K = self._running_wall_K(n, Q_bhe_run - Q_inj, fraction * Q_bhe_run - Q_inj, fraction)
        return self._borehole_fluid_K(T_wall_K, Q_bhe_run - Q_inj, R_b, m_cp_b)[2]

    def _running_wall_K(self, n: int, Q_run: float, Q_avg: float, fraction: float) -> float:
        """Wall temperature [K] the running fluid of step ``n`` is computed from (uncommitted).

        ``Q_run`` [W] is the net field load while the compressor runs,
        ``Q_avg`` [W] its average over the step, ``fraction`` the part of the
        step the compressor runs.

        *Steady borehole* (and any coupler without a run window): the step
        mean of the wall under ``Q_avg``. The wall of a borehole without heat
        capacity hardly moves within a step, and the fluid follows the
        running load at once through ``q' R_b``.

        *Dynamic borehole* (a fluid-referenced response, see
        :mod:`tmhp.ground_coupling`): the mean over the run,
        ``[t_n, t_n + fraction·dt]``, under ``Q_run``. The response then
        cancels the ``q' R_b`` and ``Q / (2 m c_p)`` the host applies to the
        same ``Q_run``, so the running fluid starts from the fluid of the
        step before and is continuous in ``fraction``. With the step-average
        load only that share would cancel, and ``(1 − fraction)`` of the
        steady drop would appear at once. The run is taken as one block at
        the start of the step; the history keeps the step-average load.
        """
        window = self._run_window
        if window is not None:
            q_run = calc_borefield_linear_load(Q_run, self.n_boreholes, self.H_b)
            return float(self.Ts_K - window(n, self.time, q_run, fraction * self.dt))
        preview = self._ground_coupler.preview_wall_temperature_rise  # type: ignore[attr-defined]
        q_avg = calc_borefield_linear_load(Q_avg, self.n_boreholes, self.H_b)
        return float(self.Ts_K - float(preview(n, self.time, q_avg)))

    def _solve_at_step_source(
        self,
        ctx: StepContext,
        T_tank_w: float,
        Q_tank_load: float,
        T0_C: float,
        flow_state: dict,
        runtime_fraction: float,
    ) -> tuple[CycleSolveOutcome, dict | None]:
        """Cycle of a step, fed with the ground fluid its own extraction produces (#96 G1).

        The evaporator used to read the fluid temperature the previous step
        left behind, so a row could report an evaporating temperature above
        its own source fluid. The cycle and the borefield are now closed
        inside the step (:func:`tmhp._source_boiler.close_source_temperature`).
        A ground coupler without ``preview_wall_temperature_rise`` (a legacy
        external backend) cannot evaluate an uncommitted load; with it the
        cycle keeps reading the previous step's outlet.
        """

        def solve(T_source_K: float | None) -> tuple[CycleSolveOutcome, dict | None]:
            opt = self._optimize_operation(
                T_tank_w, Q_tank_load, T0_C, flow_state=flow_state, source_temperature_K=T_source_K
            )
            if not opt.success:
                return opt, None
            return opt, self._calc_state(
                opt.x, T_tank_w, Q_tank_load, T0_C, flow_state=flow_state, source_temperature_K=T_source_K
            )

        if getattr(self._ground_coupler, "preview_wall_temperature_rise", None) is None:
            return solve(None)
        fraction = min(1.0, max(0.0, float(runtime_fraction)))
        loop = ground_flow_state(self._ground_settings, 1.0)
        m_cp_b = c_w * rho_w * loop["dV"]

        def outlet_K(result: dict) -> float:
            return self._running_source_K(ctx.n, float(result["Q_bhe [W]"]), fraction, loop["R_b"], m_cp_b)

        # First guess: the extraction of a COP-4 cycle at this duty.
        Q_guess = max(0.0, rated_source_heat(Q_tank_load) - loop["E_pmp"])
        outcome, solved = close_source_temperature(
            solve,
            outlet_K,
            self._running_source_K(ctx.n, Q_guess, fraction, loop["R_b"], m_cp_b),
            self._running_source_K(ctx.n, 0.0, fraction, loop["R_b"], m_cp_b),
        )
        if solved is None and outcome.success:
            # Every cycle solve succeeded but the loop did not close.
            outcome = CycleSolveOutcome(False, float("nan"), "hx_not_converged")
        return outcome, solved

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
        self,
        ctx: StepContext,
        ctrl: ControlState,
        T_solved_K: float,
        level_solved: float,
        perf: dict,
        flow_state: dict,
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

        if self._tank is not None and self._tank_step is not None:
            # Multi-node tank: T_tank_w is the volume average (what the stored
            # energy and the thermostat use). The water accounts use what the
            # tank step itself did: its wall loss and the temperature of the
            # water that left the top, which is also what the mixing valve got.
            tank = self._tank_step
            T_out_K = cu.C2K(tank["T_draw_out"])
            alp = flow_state["alp"]
            r["Q_tank_loss [W]"] = tank["Q_loss"]
            T_mix_K = alp * T_out_K + (1.0 - alp) * tank["T_makeup_K"]
            r["T_mix_w_out [°C]"] = cu.K2C(T_mix_K)
            r["T_tank_w_in [°C]"] = cu.K2C(tank["T_makeup_K"])
            r["T_tank_w_out [°C]"] = tank["T_draw_out"]
            r["T_tank_w_top [°C]"] = tank["T_top"]
            r["T_tank_w_bottom [°C]"] = tank["T_outlet"]

        # Hot-water service: the heat the draw schedule asked for at the set
        # point and the part not delivered (water below the set point, or a
        # draw the tank could not supply).
        r.update(dhw_service_columns(ctx.dV_mix_w_out, dV_mix_w_out, T_mix_K, self.T_mix_w_out_K, ctx.T_sup_w_K))

        # Diagnostics of the steady solve, not time-series quantities: keep them
        # on the analyze_steady dict and out of the dynamic frame (an object
        # column of None/str breaks numeric frame comparisons downstream).
        # `failure_reason` stays: it is always a string, one per step.
        r.pop("capacity_clamped", None)
        r.pop("failure_detail", None)

        return r

    def _ground_injection_W(self) -> float:
        """Heat other sources inject into the borehole field this step [W].

        Step average, extraction-negative in ``Q_bhe`` (a solar collector loop
        charging the ground returns it here). The base model has none.
        """
        return 0.0

    def _borehole_fluid_K(
        self, T_bhe_K: float, Q_load_W: float, R_b: float, m_cp_b: float
    ) -> tuple[float, float, float]:
        """Mean, inlet and outlet borehole fluid temperature [K] for a field load [W]."""
        q_unit = calc_borefield_linear_load(Q_load_W, self.n_boreholes, self.H_b)
        T_f_K = T_bhe_K - q_unit * R_b
        # Assume symmetrical temperature approach around average BHE fluid temperature
        dT_half = float((q_unit * self.total_borehole_length / m_cp_b) / 2) if m_cp_b > 0 else 0.0
        return T_f_K, T_f_K - dT_half, T_f_K + dT_half

    def _compute_bhe_superposition(
        self,
        n: int,
        time_arr: np.ndarray,
        hp_result: dict,
        hp_is_on: bool,
    ) -> None:
        """Ground response and borehole fluid temperatures for one step.

        Loads are kept by source. The compressor extracts only while it runs:
        its step average is the row's ``Q_bhe [W]``, its running extraction
        that divided by ``runtime_fraction [-]``. Heat other sources inject
        (:meth:`_ground_injection_W`) flows for the whole step. The borehole
        loop circulates at the source-loop flow whenever either load is on.

        - Ground history and wall (#108 step-mean response): the step-average
          net load.
        - Reported fluid temperatures ``T_bhe_f``/``_in``/``_out``: step-time
          averages. The fluid-to-wall drop (borehole resistance) and the loop
          temperature difference are linear in the load, so the average is the
          net average load's value; it is continuous in the run fraction and
          tends to the injection-only state as the compressor's run vanishes.
        - The cycle of the step was solved against the running fluid of this
          same step (:meth:`_solve_at_step_source`); no row reads the fluid
          of the step before it.
        - ``T_bhe_f*_running``: the fluid while the compressor runs (its
          running extraction net of the injection); NaN when it did not run.
          The source-side exergy of the evaporator uses these. They start
          from the wall of :meth:`_running_wall_K`, as the cycle did.
        - ``T_bhe``: the borehole wall, step mean. With the dynamic borehole
          the coupler's value is fluid-referenced (the wall that gives the
          dynamic fluid through the steady relations, which is what the
          fluid temperatures above are computed from); the reported wall is
          the coupler's ``borehole_wall_rise``.
        """
        Q_inj = float(self._ground_injection_W())
        ran = bool(hp_is_on)
        fraction = float(hp_result.get("runtime_fraction [-]", 1.0)) if ran else 0.0
        Q_hp_avg = float(hp_result.get("Q_bhe [W]", 0.0)) if ran else 0.0
        Q_net_avg = Q_hp_avg - Q_inj
        if Q_inj != 0.0:
            hp_result["Q_bhe [W]"] = Q_net_avg  # net field load, extraction-positive
        Q_bhe_unit = calc_borefield_linear_load(Q_net_avg, self.n_boreholes, self.H_b)
        running_load = ran and fraction > 0.0
        Q_run = Q_hp_avg / fraction - Q_inj if running_load else 0.0
        # The run's wall is a preview: read it before this step's load is recorded.
        T_run_wall_K = (
            self._running_wall_K(n, Q_run, Q_net_avg, fraction)
            if running_load and self._run_window is not None
            else None
        )

        # Ground thermal response (pulse-history temporal superposition) is
        # delegated to the swappable ground coupler; the default reproduces the
        # legacy single-g-function superposition byte-for-byte, while an injected
        # backend (e.g. geolink's network) replaces it with resolved
        # borehole-to-borehole superposition. See ground_coupling.py.
        coupler = self._ground_coupler
        dT_bhe = coupler.wall_temperature_rise(n, time_arr, Q_bhe_unit)

        # Fluid-referenced wall (the physical wall for a steady borehole).
        T_bhe_K = cu.C2K(self.Ts - dT_bhe)
        self.T_bhe = self.Ts - physical_wall_rise(coupler, n, time_arr, dT_bhe)
        R_b = hp_result.get("R_b_eff [mK/W]", self.R_b)
        m_cp_b = c_w * rho_w * hp_result.get("dV_bhe_f [m3/s]", self.dV_b_f_m3s)
        self.Q_bhe = Q_bhe_unit * self.total_borehole_length

        T_bhe_f_K, self.T_bhe_f_in_K, T_bhe_f_out_K = self._borehole_fluid_K(T_bhe_K, Q_net_avg, R_b, m_cp_b)
        self.T_bhe_f = cu.K2C(T_bhe_f_K)
        self.T_bhe_f_in = cu.K2C(self.T_bhe_f_in_K)
        # Sync the Kelvin attribute too. _calc_state reads self.T_bhe_f_out_K to set
        # the evaporator condition; without this it stays at its __init__ default (Ts_K),
        # so in multi-step analyze_dynamic the evaporator/COP never follow the g-driven
        # ground temperature drift. (analyze_steady sets this attribute explicitly,
        # which is why only the dynamic path was affected.)
        self.T_bhe_f_out_K = T_bhe_f_out_K
        self.T_bhe_f_out = cu.K2C(T_bhe_f_out_K)

        # Apply BHE state to hp_result (so it is visible correctly)
        hp_result["T_bhe [°C]"] = self.T_bhe
        hp_result["T_bhe_f [°C]"] = self.T_bhe_f
        hp_result["T_bhe_f_in [°C]"] = self.T_bhe_f_in
        hp_result["T_bhe_f_out [°C]"] = self.T_bhe_f_out
        if running_load:
            run = self._borehole_fluid_K(T_bhe_K if T_run_wall_K is None else T_run_wall_K, Q_run, R_b, m_cp_b)
            running = tuple(cu.K2C(x) for x in run)
        else:
            running = (np.nan, np.nan, np.nan)
        hp_result["T_bhe_f_running [°C]"] = running[0]
        hp_result["T_bhe_f_in_running [°C]"] = running[1]
        hp_result["T_bhe_f_out_running [°C]"] = running[2]

    # =============================================================
    # Tank backends (swappable)
    # =============================================================

    def _solve_lumped_tank(
        self,
        ctx,
        ctrl,
        dt_s,
        T_sup_w_K_n,
        tank_level_solve,
        sub_states,
        dV_tank_w_out_prev,
    ):
        """Single-node tank: implicit solve of the energy balance over the step.

        Returns the end temperature, the flows of the step at that temperature
        and the end level. The reported flows and level are the ones the
        balance was solved with; they used to be the unlimited draw, so a tank
        drained below its make-up reported level 0 and water it did not hold
        (#96 T1: +9.9 kWh, +32 %, in 1 h).

        A solve that does not return a root is recovered by
        :func:`tmhp._tank_solve.recover_tank_temperature` with the same
        residual, so the heat pump, the draw-off, the loss and the subsystem
        heat stay in the balance, and a step that cannot be solved is named in
        a warning instead of being replaced silently.
        """
        res_fn = self._build_residual_fn(
            ctx=ctx,
            ctrl=ctrl,
            dt_s=dt_s,
            T_tank_w_in_K_n=T_sup_w_K_n,
            T_sup_w_K_n=T_sup_w_K_n,
            tank_level=tank_level_solve,
            sub_states=sub_states,
        )
        T_solved_K = solve_lumped_tank(
            res_fn,
            ctx.T_tank_w_K,
            T_start_K=ctx.T_tank_w_K,
            dt=dt_s,
            C_curr=self.C_tank * max(TANK_LEVEL_MIN, ctx.tank_level),
            C_next=self.C_tank * max(TANK_LEVEL_MIN, tank_level_solve),
            C_tank=self.C_tank,
            who=type(self).__name__,
            step=ctx.n,
        )

        # Flows and end level the solved balance carried
        # (tmhp.dynamic_context.tank_step_flows, the function the residual
        # itself uses): a level-varying tank stops the draw at TANK_LEVEL_MIN
        # and the make-up at full, and what the draw could not supply is unmet
        # hot water. An always-full tank takes in what it gives.
        flows = tank_step_flows(T_solved_K, ctx, ctrl, dt_s, T_sup_w_K_n, self.T_mix_w_out_K, self.V_tank_full)
        return T_solved_K, flows.as_flow_state(), flows.level_next

    # ---- multi-node tank ("stratified", "hybrid") -----------------------------

    def _tank_is_one_mixed_node(self) -> bool:
        """A one-node stratified tank is the fully mixed (lumped) tank; a one-node hybrid tank is plug flow."""
        return isinstance(self._tank, StratifiedTank) and self._tank.n == 1

    def _condenser_ua(self) -> np.ndarray:
        """Conductance of the immersed condenser coil in each tank node [W/K].

        ``UA_tank_hx`` spread evenly over the nodes (a coil spanning the
        tank), or all of it at ``condenser_node``.
        """
        assert self._tank is not None
        n = self._tank.n
        if self.condenser_node is None:
            return np.full(n, self.UA_tank_hx / n)
        ua = np.zeros(n)
        ua[self.condenser_node] = self.UA_tank_hx
        return ua

    @staticmethod
    def _condensing_level(T_nodes: np.ndarray, ua: np.ndarray, q: float) -> float:
        """Condensing temperature [°C] at which the coil passes ``q`` [W] to these nodes.

        The ``L`` with ``Σ ua_i·(L − T_i)⁺ = q``: the coil heats only the nodes
        colder than the refrigerant, so the colder the water around it, the
        lower the condensing temperature the duty needs.
        """
        has = ua > 0.0
        order = np.argsort(T_nodes[has], kind="stable")
        T_sorted, ua_sorted = T_nodes[has][order], ua[has][order]
        ua_cum = np.cumsum(ua_sorted)
        level = (q + np.cumsum(ua_sorted * T_sorted)) / ua_cum  # with the j+1 coldest nodes heated
        for j in range(T_sorted.size - 1):
            if level[j] <= T_sorted[j + 1]:
                return float(level[j])
        return float(level[-1])

    def _nodal_sink_temperature(
        self,
        T_mean_C: float,
        q: float,
        runtime_fraction: float,
        dt_s: float,
        draw_flow: float,
        T_makeup_C: float,
    ) -> float:
        """Tank-side temperature [°C] the cycle condenses against for duty ``q`` [W].

        The cycle model takes one tank temperature ``T`` and condenses at
        ``T + q / UA_tank_hx``. For a multi-node tank the condensing
        temperature the coil needs is its level ``L``
        (:meth:`_condensing_level`), so the cycle is given ``L − q / UA_tank_hx``
        and condenses exactly at ``L``. Two requirements set ``L``:

        - the run's mean: ``L`` of the node profile half-way through the run
          (a trial tank step of half the run time), expressed relative to that
          profile's mean so that the cycle follows ``T_mean_C``, the mean
          temperature the thermostat model gives for the run (the midpoint
          rule the lumped tank uses);
        - the end of the step: the coil must still pass the step's heat to the
          nodes as they are at the end of the step. ``L`` is not taken below
          the temperature that does so with the coil's conductance times
          :data:`NODAL_END_CONDUCTANCE` (a trial step that solves the exchanger
          temperature). This binds when the tank warms by more than about the
          coil's approach within one step (a large ``UA_tank_hx``, or a long
          step at full run time); the condensing temperature the row reports
          is then the end-of-step one, a first-order (conservative) estimate.

        With one fully mixed node the result is ``T_mean_C``: the lumped tank.
        """
        tank = self._tank
        assert tank is not None
        if self._tank_is_one_mixed_node():
            return T_mean_C
        ua = self._condenser_ua()
        fraction = min(1.0, max(0.0, runtime_fraction))
        trial = dict(draw_flow=draw_flow, T_makeup=T_makeup_C, T_amb=cu.K2C(self.T_sur_K))
        profile = tank.T
        if fraction > 0.0:
            T_start_level = self._condensing_level(profile, ua, q)
            profile = tank.preview(0.5 * fraction * dt_s, hx_ua=ua, T_hx=T_start_level, q_hx=q, **trial)["T"]
        level = T_mean_C + (self._condensing_level(profile, ua, q) - float(np.mean(profile)))
        if fraction > 0.0:
            end = tank.preview(dt_s, hx_ua=NODAL_END_CONDUCTANCE * fraction * ua, q_hx=fraction * q, **trial)
            level = max(level, float(end["T_hx"]))
        return level - q / self.UA_tank_hx

    def _nodal_draw_flow(self, ctx: StepContext, T_out_C: float, T_sup_w_K_n: float) -> dict:
        """Mixing-valve flows for hot water leaving the tank at ``T_out_C`` [°C]."""
        return self._calc_tank_flow_context(
            dV_mix_w_out=ctx.dV_mix_w_out,
            T_tank_w_K=cu.C2K(T_out_C),
            T_tank_w_in_K=T_sup_w_K_n,
            T_mix_w_out_K=self.T_mix_w_out_K,
        )

    def _solve_cycle_for_tank(self, ctx: StepContext, T_C: float, runtime_fraction: float, dt_s: float) -> dict:
        """Cycle of the step for the tank backend in use (``T_C``: run-mean tank temperature [°C]).

        Lumped tank: :meth:`_solve_cycle_at`. Multi-node tank: the same solve
        against the coil's tank-side temperature for each duty tried
        (:meth:`_nodal_sink_temperature`). If the cycle has no operating point
        against the mid-run profile (the water around the coil gets too hot
        within the run), the cycle that starts the run is used; the tank step
        then stops the compressor when that water reaches the condensing
        temperature.
        """
        tank = self._tank
        if tank is None:
            return self._solve_cycle_at(ctx, T_C, runtime_fraction)
        T_makeup_C = cu.K2C(ctx.T_sup_w_K)
        draw_flow = self._nodal_draw_flow(ctx, float(tank.T[0]), ctx.T_sup_w_K)["dV_tank_w_out"]

        def solve(fraction_seen: float) -> dict:
            def at_duty(q: float) -> dict:
                T_sink = self._nodal_sink_temperature(T_C, q, fraction_seen, dt_s, draw_flow, T_makeup_C)
                return self._solve_cycle_at_duty(ctx, T_sink, q, runtime_fraction)

            return reduce_duty_within_envelope(self, at_duty(self.hp_capacity), at_duty, self.hp_capacity)

        result = solve(runtime_fraction)
        if delivered_tank_heat(result) <= 0.0 and not self._tank_is_one_mixed_node() and runtime_fraction > 0.0:
            at_start = solve(0.0)
            if delivered_tank_heat(at_start) > 0.0:
                result = at_start
        return result

    def _solve_nodal_tank(self, ctx, ctrl, dt_s, T_sup_w_K_n, hp_result):
        """Advance the multi-node tank one step; returns the (possibly cut) heat-pump row too.

        Condenser heat: node ``i`` receives ``k·UA_i·(T_cond − T_i)⁺`` with
        ``T_cond`` the condensing temperature of the step's cycle and ``T_i``
        the node's end-of-step temperature, ``k`` set so that the nodes receive
        the step's condenser heat. A node as hot as the refrigerant receives
        nothing, and no node is heated past ``T_cond`` (nor past the tank's
        boiling limit). If the coil's nodes
        cannot take the heat below ``T_cond`` the compressor is taken to have
        stopped when they reached it: the row's run time, heat and power are
        cut to what was delivered, and the thermostat call stands. (A single
        fully mixed node has nothing to split: the heat goes in as in the
        lumped tank.)

        Draw-off: the mixing valve is set on the water that actually leaves
        the top during the step (iterated, :data:`NODAL_DRAW_TOL_K`).
        """
        tank = self._tank
        assert tank is not None
        T_makeup_C = cu.K2C(T_sup_w_K_n)
        common = dict(T_makeup=T_makeup_C, T_amb=cu.K2C(self.T_sur_K))
        q_step = float(ctrl.Q_heat_source)
        one_node = self._tank_is_one_mixed_node()
        if q_step > 0.0 and one_node:
            common.update(q_source=np.array([q_step]))
        elif q_step > 0.0:
            T_cond = min(float(hp_result["T_ref_cond_sat_v [°C]"]), float(hp_result["T_ref_cond_sat_l [°C]"]))
            # Liquid water: the coil heats nothing past the tank's boiling limit either.
            common.update(hx_ua=self._condenser_ua(), T_hx=min(T_cond, tank.T_boil), q_hx=q_step)

        T_out_C = float(tank.T[0])
        flow_state = self._nodal_draw_flow(ctx, T_out_C, T_sup_w_K_n)
        if ctx.dV_mix_w_out > 0.0:
            for _ in range(NODAL_DRAW_ITERATIONS):
                T_new = tank.preview(dt_s, draw_flow=flow_state["dV_tank_w_out"], **common)["T_draw_out"]
                moved = abs(T_new - T_out_C)
                T_out_C = T_new
                flow_state = self._nodal_draw_flow(ctx, T_out_C, T_sup_w_K_n)
                if moved < NODAL_DRAW_TOL_K:
                    break

        out = tank.step(dt_s, draw_flow=flow_state["dV_tank_w_out"], **common)
        out["T_makeup_K"] = T_sup_w_K_n
        self._tank_step = out

        if q_step > 0.0 and one_node:
            out["q_hx"], out["Q_hx"] = np.array([q_step]), q_step  # the node's heat, as the coil's
        limited = bool(q_step > 0.0 and out["hx_limited"])
        if limited:
            share = out["Q_hx"] / q_step
            if share < NODAL_MIN_RUN_SHARE:
                # Nothing could be delivered: the compressor did not run.
                hp_result = self._failed_cycle_result(
                    cu.K2C(ctx.T_tank_w_K),
                    cu.K2C(ctx.T0_K),
                    self._step_flow_state(ctx, cu.K2C(ctx.T_tank_w_K)),
                    "hx_not_converged",
                )
                hp_result["runtime_fraction [-]"] = 0.0
                hp_result["hp_starts [-]"] = 0
            else:
                fraction = float(hp_result.get("runtime_fraction [-]", 1.0))
                scale_running_rates(hp_result, share, self._RUNTIME_SCALED_KEYS)
                hp_result["runtime_fraction [-]"] = fraction * share

        T_solved_K = cu.C2K(float(np.mean(out["T"])))
        return T_solved_K, flow_state, 1.0, hp_result, limited

    # =============================================================
    # Orchestration
    # =============================================================

    def step(self, state, inputs: dict, dt_s: float):
        """Reject point-state stepping — GSHPB is history-dependent (#165 P0b).

        ``_compute_bhe_superposition`` reads the *entire* past borehole
        load-pulse history each timestep, so a point-state ``step()`` kernel
        (as defined for ASHP/ASHPB) would silently corrupt the ground
        temperature drift. Use ``analyze_dynamic()``, or implement a ``step()``
        that carries the borehole pulse vector inside its state.
        """
        raise NotImplementedError(
            "GroundSourceHeatPumpBoiler is history-dependent (borehole "
            "superposition reads the full past load history each step) and "
            "cannot be driven by a point-state step() kernel; use "
            "analyze_dynamic(), or carry the borehole pulse vector in state."
        )

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

        self.T_bhe_f = self.Ts
        self.T_bhe = self.Ts
        self.T_bhe_f_in = self.Ts
        self.T_bhe_f_in_K = self.Ts_K
        self.T_bhe_f_out = self.Ts
        # The first step's evaporator reads the Kelvin value: reset it too, or a
        # second run starts from the first run's last source temperature (#96 T13).
        self.T_bhe_f_out_K = self.Ts_K
        self.Q_bhe = 0.0

        # Initialise the ground coupler's pulse-history state for this run.
        reset_ground_coupler(self._ground_coupler, tN, time, dt_s)
        self._run_window = run_window_preview(self._ground_coupler)

        # Initialise the multi-node tank profile (uniform at the init temp).
        if self._tank is not None:
            self._tank.reset(T_tank_w_init_C)
            self._tank_step = None

        # DHW schedule handling: direct m³/s flow array
        dhw_flow_m3s = np.asarray(dhw_usage_schedule, dtype=float)
        if len(dhw_flow_m3s) != tN:
            raise ValueError(f"dhw_usage_schedule length ({len(dhw_flow_m3s)}) != tN ({tN})")

        _use_solar = self._needs_solar_input()

        for n in tqdm(range(tN), desc="GSHPB Simulating"):
            t_s = time[n]
            hr = t_s * cu.s2h
            hour_of_day = (t_s % (24 * 3600)) * cu.s2h

            self.T_sur_K = cu.C2K(T_sur_arr[n])

            T0_K = cu.C2K(T0_schedule[n])
            T_sup_w_n = T_sup_w_arr[n]
            T_sup_w_K_n = cu.C2K(T_sup_w_n)

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
            # only when the cycle was solved, and only then heats the tank and
            # loads the ground.
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
                1.0,
                max(
                    0.0,
                    (self.T_mix_w_out_K - T_sup_w_K_n) / max(1e-6, ctx.T_tank_w_K - T_sup_w_K_n),
                ),
            )
            dV_tank_w_out_prev = alp_prev * ctx.dV_mix_w_out
            dV_tank_w_in_prev = dV_tank_w_out_prev if dV_tank_w_in_ctrl is None else dV_tank_w_in_ctrl
            tank_vol_change_prev = (dV_tank_w_in_prev - dV_tank_w_out_prev) * dt_s
            level_next_approx = min(1.0, max(0.0, ctx.tank_level + tank_vol_change_prev / self.V_tank_full))
            tank_level_solve = max(TANK_LEVEL_MIN, level_next_approx)

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
                residual_free=self._build_residual_fn(
                    ctx=ctx,
                    ctrl=ctrl_free,
                    dt_s=dt_s,
                    T_tank_w_in_K_n=T_sup_w_K_n,
                    T_sup_w_K_n=T_sup_w_K_n,
                    tank_level=tank_level_solve,
                    sub_states=sub_states,
                ),
                solve_cycle=lambda T_C, fraction, ctx=ctx: self._solve_cycle_for_tank(ctx, T_C, fraction, dt_s),
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
            # Tank update — swappable backend: the single-node implicit balance,
            # or the multi-node tank ("stratified", "hybrid").
            if self._tank is None:
                T_solved_K, flow_state_final, level_next = self._solve_lumped_tank(
                    ctx,
                    ctrl,
                    dt_s,
                    T_sup_w_K_n,
                    tank_level_solve,
                    sub_states,
                    dV_tank_w_out_prev,
                )
            else:
                T_solved_K, flow_state_final, level_next, hp_result, cut_out = self._solve_nodal_tank(
                    ctx, ctrl, dt_s, T_sup_w_K_n, hp_result
                )
                if cut_out:
                    # The coil's water reached the condensing temperature before
                    # the thermostat was satisfied: the compressor has stopped,
                    # the call for heat stands.
                    is_on_prev, running_prev, phase_age_s = True, False, 0.0
                    hp_is_on = bool(hp_result.get("hp_is_on", False))
                    ctrl = ControlState(
                        is_on=hp_is_on,
                        Q_heat_source=delivered_tank_heat(hp_result),
                        dV_tank_w_in_ctrl=dV_tank_w_in_ctrl,
                    )
            warn_if_tank_boiling(T_solved_K)

            # --- Phase C: BHE Temporal Superposition ---
            self._compute_bhe_superposition(
                n=n,
                time_arr=time,
                hp_result=hp_result,
                hp_is_on=hp_is_on,
            )

            # Assemble step results
            step_record = self._assemble_core_results(ctx, ctrl, T_solved_K, level_next, hp_result, flow_state_final)
            self._augment_results(step_record, ctx, ctrl, sub_states, T_solved_K)
            results_data.append(step_record)

            # Step forward
            T_tank_w_K = T_solved_K
            tank_level = level_next

        # No forward fill: a NaN here is a quantity that does not exist on that
        # step (COP, compressor and refrigerant states while the compressor is
        # stopped). Tank, ground and flow columns are written on every step.
        results_df = pd.DataFrame(results_data)
        results_df = self._postprocess(results_df)

        if result_save_csv_path:
            results_df.to_csv(result_save_csv_path, index=False)

        warn_if_dhw_unmet(results_df, dt_s, type(self).__name__, cu.K2C(self.T_mix_w_out_K))
        return results_df

    @steady_envelope(duty_arg="Q_ref_tank")
    def analyze_steady(
        self,
        T_tank_w: float,
        T_source: float,
        Q_ref_tank: float,
        T0: float = 0.0,
        *,
        return_dict: bool = True,
        ground_flow_ratio: float | None = None,
        ground_flow_lpm: float | None = None,
        T_bhe_wall: float | None = None,
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
              closed the ground heat exchanger and the cycle is an operating
              point.
            - ``"failure_reason"`` (str) — ``"none"`` for a solved point;
              otherwise ``"cycle_invalid"`` (no cycle, or a cycle that is
              not an operating point, see
              :func:`tmhp.dynamic_context.unphysical_cycle_detail`),
              ``"pr_above_max"`` (pressure-ratio ceiling),
              ``"discharge_temp_above_max"`` (even a reduced duty exceeds
              ``T_dis_max``; a request above the limit that a smaller duty
              meets returns that duty with ``capacity_clamped="max"`` and
              ``capacity_binding="discharge_temperature"``),
              ``"hx_not_converged"`` (the ground heat exchanger cannot
              carry the duty at any approach) or ``"optimizer_failed"``.
              With the coupled ground-flow controller the reasons of
              :mod:`tmhp.ground_flow_control` apply.
            - ``"failure_detail"`` (str, failures only) — which check
              rejected a ``"cycle_invalid"`` point.

            Every reason other than ``"none"`` comes with an off-mode state:
            ``hp_is_on=False``, ``Q_ref_tank [W] = E_cmp [W] = 0`` and NaN
            COP. No unsolved point is returned with cycle numbers.
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

        if ground_flow_ratio is not None and ground_flow_lpm is not None:
            raise ValueError("Supply ground_flow_lpm or deprecated ground_flow_ratio, not both")
        if ground_flow_lpm is not None:
            ground_flow_ratio = ground_flow_lpm / self.ground_flow_ref_lpm
        elif ground_flow_ratio is not None:
            import warnings

            warnings.warn(
                "ground_flow_ratio is deprecated; use ground_flow_lpm (ratio is always to reference).",
                DeprecationWarning,
                stacklevel=2,
            )
        if self._ground_settings["active"] or ground_flow_ratio is not None:
            result = self._solve_ground_flow(
                T_tank_w,
                Q_ref_tank,
                T0,
                flow_state=flow_state,
                ground_flow_ratio=ground_flow_ratio,
                T_bhe_wall=T_bhe_wall,
            )
            result.update({"Q_tank_loss [W]": 0.0, "tank_level [-]": 1.0})
            return result if return_dict else pd.DataFrame([result])

        if Q_ref_tank <= 0:
            result = self._calc_state(
                dT_ref_ground=5.0,
                T_tank_w=T_tank_w,
                Q_tank_load=0.0,
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
                        dT_ref_ground=opt_result.x,
                        T_tank_w=T_tank_w,
                        Q_tank_load=Q_ref_tank,
                        T0=T0,
                        flow_state=flow_state,
                    )

            # Pressure-ratio envelope hint for the final operating point
            # (one message per call; per-probe events are silent). Floor ->
            # clamp (cycle still solved); ceiling -> reject (HP-off fallback).
            pr_event = self._last_pr_event
            if pr_event is not None:
                kind, pr_val, bound = pr_event
                if kind == "pr_below_min":
                    print(
                        f"[PR guard] clamp 하한(below PR_cycle_min): "
                        f"PR={pr_val:.3f} -> {bound:.2f} "
                        f"(T_tank_w={T_tank_w:.1f}°C, T_source={T_source:.1f}°C, Q_ref_tank={Q_ref_tank:.0f}W)"
                    )
                else:  # pr_above_max
                    print(
                        f"[PR guard] reject 상한(above PR_cycle_max): "
                        f"PR={pr_val:.3f} > {bound:.2f} "
                        f"(T_tank_w={T_tank_w:.1f}°C, T_source={T_source:.1f}°C, Q_ref_tank={Q_ref_tank:.0f}W)"
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
                try:
                    result = self._failed_cycle_result(T_tank_w, T0, flow_state, failure_reason, detail)
                except Exception:
                    result = {
                        "hp_is_on": False,
                        "converged": False,
                        "failure_reason": failure_reason,
                        "Q_ref_tank [W]": 0.0,
                        "Q_ref_ground [W]": 0.0,
                        "Q_bhe [W]": 0.0,
                        "E_cmp [W]": 0.0,
                        "E_pmp [W]": 0.0,
                        "E_tot [W]": 0.0,
                        "T_tank_w [°C]": T_tank_w,
                        "T0 [°C]": T0,
                    }
            else:
                result["converged"] = True
                result["failure_reason"] = "none"

        if result is not None:
            # Steady state doesn't have tank loss because we don't solve tank mass/energy balance
            result["Q_tank_loss [W]"] = 0.0
            result.setdefault("failure_reason", "none")  # zero load: a valid off result, not a failure
            result["tank_level [-]"] = 1.0  # steady-state: always_full

        if result is None:
            result = {}
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

        # Water leaves a multi-node tank at its top (T_tank_w_out), not at the mean.
        T_tank_out_K = cu.C2K(df["T_tank_w_out [°C]"]) if "T_tank_w_out [°C]" in df.columns else T_tank_K

        df["Q_tank_w_out [W]"] = calc_energy_flow(
            c_w * rho_w * df["dV_tank_w_out [m3/s]"].fillna(0), T_tank_out_K, T0_K
        )

        # 1. Refrigerant state points
        df = calc_refrigerant_exergy(df, self.ref, T0_K)
        df = convert_electricity_to_exergy(df)

        # 2. Exergy flows of the source loop: evaluated at the running state
        # and averaged over the step like the heat and power in the row.
        X_bhe_in, X_bhe_out, X_ground_in, X_ground_out = source_loop_exergy_flows(df, T0_K)
        # Loop boundary flows (step averages) so the source-side balance can be read from the frame.
        df["X_bhe_in [W]"] = X_bhe_in
        df["X_bhe_out [W]"] = X_bhe_out

        Q_ref_tank = df["Q_ref_tank [W]"].fillna(0)
        Q_ref_ground = df["Q_ref_ground [W]"].fillna(0)
        df["X_ref_tank [W]"] = np.where(
            Q_ref_tank > 0,
            Q_ref_tank * (1 - T0_K / cu.C2K(df["T_ref_cond_sat_v [°C]"])),
            0.0,
        )
        df["X_ref_ground [W]"] = np.where(
            Q_ref_ground > 0,
            Q_ref_ground * (1 - T0_K / cu.C2K(df["T_ref_evap_sat [°C]"])),
            0.0,
        )

        df["X_tank_w_in [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_tank_w_in [m3/s]"].fillna(0),
            cu.C2K(df["T_tank_w_in [°C]"]),
            T0_K,
        )
        df["X_tank_w_out [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_tank_w_out [m3/s]"].fillna(0), T_tank_out_K, T0_K
        )

        df["X_mix_w_out [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_mix_w_out [m3/s]"].fillna(0),
            cu.C2K(df["T_mix_w_out [°C]"]),
            T0_K,
        )
        df["X_mix_sup_w_in [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_mix_sup_w_in [m3/s]"].fillna(0),
            cu.C2K(df["T_tank_w_in [°C]"]),
            T0_K,
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
        df["Xc_ground [W]"] = (X_ground_in - X_ground_out) - df["X_ref_ground [W]"]
        df["Xc_pmp [W]"] = df["E_pmp [W]"] - (X_ground_in - X_bhe_out)

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
