"""Ground source heat pump — physics-based cycle model with indoor unit.

Resolves a vapour-compression refrigerant cycle coupled to a borehole
heat exchanger (BHE) on the source side and an indoor-air heat exchanger
on the load side.  Supports both **cooling** (``Q_r_iu > 0``) and
**heating** (``Q_r_iu < 0``) modes.

At each time step the model finds the minimum-power operating point
(compressor + BHE pump + indoor fan) via bounded 2-D optimisation
over the evaporator and condenser approach temperature differences.

Borehole thermal response is tracked with pygfunction-based multi-borehole
g-functions, enabling robust long-term ground temperature drift modelling.
The effective borehole thermal resistance ``R_b*`` linking the borehole wall to
the circulating fluid is derived from the U-tube cross-section geometry unless it
is supplied explicitly.

Architecture mirrors ``GroundSourceHeatPumpBoiler`` for the BHE side
and ``AirSourceHeatPump`` for the indoor-unit side.
"""

from __future__ import annotations

import contextlib
import warnings
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from tqdm import tqdm

from . import calc_util as cu
from .compressor_efficiency import _eval_eff
from .compressor_envelope import check_pr_envelope
from .compressor_speed import default_displacement, solve_compressor_speed
from .constants import c_a, c_w, k_w, mu_w, rho_a, rho_w
from .enex_functions import (
    calc_exergy_flow,
    calc_fan_power_from_dV_fan,
    calc_HX_perf_for_target_heat,
)
from .g_function import precompute_gfunction
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
from .heat_exchanger import calc_ground_hx_UA_from_capacity, calc_phase_change_hx_effectiveness, resolve_fan_flow_limits
from .hx_fan import is_generic_fan_curve
from .reference_state import HXSide, RatingCondition, ReferenceStateMixin
from .refrigerant import (
    calc_ref_state,
    reportable_state,
)


class GroundSourceHeatPump(ReferenceStateMixin):
    """Ground source heat pump with BHE and indoor-unit air heat exchange.

    The refrigerant cycle is resolved via CoolProp.  A bounded 2-D
    optimiser minimises total electrical input (``E_cmp + E_pmp + E_iu_fan``)
    over the evaporator and condenser approach temperatures.
    """

    _RATING_FAMILY = "GSHP"
    _RATING_DEFAULT_MODE = "cooling"

    def __init__(
        self,
        # 1. Refrigerant / cycle / compressor -----------
        ref: str = "R32",
        V_cmp_ref: float | None = None,
        eta_cmp_isen: float | Callable | None = None,
        dT_superheat: float = 5.0,
        dT_subcool: float = 5.0,
        # 2. Heat exchanger UA ---------------------------
        UA_cond: float | None = None,
        UA_evap: float | None = None,
        # 3. Indoor unit fan -----------------------------
        dV_iu_fan_a_rated: float | None = None,
        dP_iu_fan_rated: float | None = None,
        A_cross_iu: float | None = None,
        eta_iu_fan_rated: float | None = None,
        vsd_coeffs_iu: dict | None = None,
        # 4. BHE (Borehole Heat Exchanger) ---------------
        N_1: int = 1,
        N_2: int = 1,
        B: float = 6.0,
        D_b: float = 0,
        H_b: float = 100,
        r_b: float = 0.08,
        R_b: float | None = None,
        k_g: float = 1.5,
        k_p: float = 0.4,
        r_out: float = 0.016,
        r_in: float = 0.013,
        D_s: float = 0.025,
        boundary_condition: str = "uniform_temperature",
        dV_b_f_lpm: float | None = None,
        k_s: float = 2.0,
        c_s: float = 800,
        rho_s: float = 2000,
        Ts: float = 16.0,
        E_pmp: float = 100,
        # 5. System capacity / room ----------------------
        hp_capacity: float = 4000.0,
        T_a_room: float = 27.0,
        # 6. Cycle guard ---------------------------------
        dT_hx_min: float = 0.5,
        # Compressor pressure-ratio envelope (PR = P_cond / P_evap)
        PR_cycle_min: float = 1.5,
        PR_cycle_max: float = 5.0,
        # 7. Simulation scope ----------------------------
        t_max_s: float = 8760 * 3600,
        dt_s: float = 3600,
        # Deprecated:
        V_disp_cmp: float | None = None,
        UA_cond_design: float | None = None,
        UA_evap_design: float | None = None,
        dV_iu_fan_a_design: float | None = None,
        dP_iu_fan_design: float | None = None,
        eta_iu_fan_design: float | None = None,
        *,
        UA_ground_rated: float | None = None,
        ground_hx_ua_per_capacity: float = 0.18,
        UA_iu_rated: float | None = None,
        indoor_approach_min_K: float = 1.0,
        indoor_approach_max_K: float = 20.0,
        eta_cmp_vol: float | Callable | None = None,
        eta_cmp: float | Callable | None = None,
        rps_rated: float | None = None,
        eta_v: float | None = None,
        eta_em: float | None = None,
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
        rps_min: float = 15.0,
        rps_max: float = 150.0,
        dV_iu_fan_a_ref: float | None = None,
        dV_iu_fan_a_min: float | None = None,
        dV_iu_fan_a_max: float | None = None,
    ):
        ground_rates = resolve_ground_flow_rates(
            default_ref_lpm=20.04,
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
        if dV_iu_fan_a_ref is not None:
            dV_iu_fan_a_rated = dV_iu_fan_a_ref
        if (
            not all(np.isfinite(v) for v in (indoor_approach_min_K, indoor_approach_max_K))
            or not 0 < indoor_approach_min_K < indoor_approach_max_K
        ):
            raise ValueError("Require finite 0 < indoor_approach_min_K < indoor_approach_max_K")
        self.indoor_approach_min_K = indoor_approach_min_K
        self.indoor_approach_max_K = indoor_approach_max_K
        # Resolve deprecated mapping
        if V_cmp_ref is None:
            V_cmp_ref = V_disp_cmp if V_disp_cmp is not None else default_displacement(hp_capacity)
        if UA_cond is None:
            UA_cond = UA_cond_design
        if UA_evap is None:
            UA_evap = UA_evap_design
        if dV_iu_fan_a_rated is None:
            dV_iu_fan_a_rated = dV_iu_fan_a_design
        if dP_iu_fan_rated is None:
            dP_iu_fan_rated = dP_iu_fan_design if dP_iu_fan_design is not None else 60.0
        if eta_iu_fan_rated is None:
            eta_iu_fan_rated = eta_iu_fan_design if eta_iu_fan_design is not None else 0.6

        if vsd_coeffs_iu is None:
            vsd_coeffs_iu = {
                "c1": 0.0013,
                "c2": 0.1470,
                "c3": 0.9506,
                "c4": -0.0998,
                "c5": 0.0,
            }

        # --- 1. Refrigerant / cycle / compressor ---
        self.ref: str = ref
        self.V_cmp_ref: float = V_cmp_ref
        # Validation-only speed for scalar/legacy inputs (the rated speed may not
        # be known yet; scalars do not depend on it).
        rps_check = rps_rated if rps_rated is not None else rps_min
        for name, legacy in (("eta_v", eta_v), ("eta_em", eta_em)):
            if legacy is not None:
                _eval_eff(legacy, 3.0, rps_check)
                warnings.warn(
                    f"{name} is deprecated; use eta_cmp_vol / eta_cmp. Explicit new inputs take precedence.",
                    DeprecationWarning,
                    stacklevel=2,
                )
        if eta_cmp_vol is None:
            eta_cmp_vol = eta_v
        if eta_cmp is None:
            eta_cmp = eta_em
        for efficiency in (eta_cmp_isen, eta_cmp_vol, eta_cmp):
            if efficiency is not None and not callable(efficiency):
                _eval_eff(efficiency, 3.0, rps_check)
        self.dT_superheat: float = dT_superheat
        self.dT_subcool: float = dT_subcool
        self.dT_hx_min: float = dT_hx_min
        # Compressor pressure-ratio envelope (floor -> clamp, ceiling -> reject)
        self.PR_cycle_min: float = PR_cycle_min
        self.PR_cycle_max: float = PR_cycle_max
        self._last_pr_event: tuple[str, float, float] | None = None
        self.hp_capacity: float = hp_capacity
        self.rps_min, self.rps_max = rps_min, rps_max

        # --- 2. Physical heat exchangers and compressor efficiencies ---
        # Water HX sizing is independent of the air HX and of cycle mode.
        self.UA_ground_rated = (
            calc_ground_hx_UA_from_capacity(hp_capacity, ground_hx_ua_per_capacity)
            if UA_ground_rated is None
            else UA_ground_rated
        )
        self.ground_hx_ua_per_capacity = ground_hx_ua_per_capacity
        # Preserve the former cooling indoor-air default, independently of
        # ground-HX sizing. This is an air-HX assumption, not the BPHE rule.
        self.UA_iu_rated = 0.08 * hp_capacity if UA_iu_rated is None else UA_iu_rated
        if not all(np.isfinite(v) and v > 0 for v in (self.UA_ground_rated, self.UA_iu_rated)):
            raise ValueError("Physical heat exchanger rated UA values must be positive and finite")
        legacy_ua = UA_cond is not None or UA_evap is not None
        self._legacy_ground_ua = legacy_ua and UA_ground_rated is None
        self._legacy_indoor_ua = legacy_ua and UA_iu_rated is None
        if legacy_ua:
            warnings.warn(
                "UA_cond/UA_evap (including *_design) are deprecated cycle-role inputs; "
                "use UA_ground_rated and UA_iu_rated for mode-independent physical heat exchangers. "
                "Explicit physical UA inputs take precedence.",
                DeprecationWarning,
                stacklevel=2,
            )
            # Compatibility only: retain explicitly requested old role mapping.
            self.UA_cond = hp_capacity / 10.0 if UA_cond is None else UA_cond
            self.UA_evap = 0.8 * self.UA_cond if UA_evap is None else UA_evap
        else:
            # Read-compatible cooling-role aliases; no cycle computation uses
            # these aliases unless the caller explicitly used the legacy API.
            self.UA_cond, self.UA_evap = self.UA_ground_rated, self.UA_iu_rated

        # --- 3. Indoor unit fan ---
        if dV_iu_fan_a_rated is None:
            self.dV_iu_fan_a_rated = hp_capacity * 0.0002
        else:
            self.dV_iu_fan_a_rated = dV_iu_fan_a_rated

        self.dV_iu_fan_a_ref = self.dV_iu_fan_a_rated
        self.dV_iu_fan_a_min, self.dV_iu_fan_a_max = resolve_fan_flow_limits(
            self.dV_iu_fan_a_ref,
            dV_iu_fan_a_min,
            dV_iu_fan_a_max,
            custom_curve=not is_generic_fan_curve(vsd_coeffs_iu),
        )
        self.dP_iu_fan_rated: float = dP_iu_fan_rated
        self.eta_iu_fan_rated: float = eta_iu_fan_rated

        if A_cross_iu is None:
            self.A_cross_iu = self.dV_iu_fan_a_rated / 2.0
        else:
            self.A_cross_iu = A_cross_iu

        self.E_iu_fan_rated: float = self.dV_iu_fan_a_rated * self.dP_iu_fan_rated / self.eta_iu_fan_rated
        self.vsd_coeffs_iu: dict = vsd_coeffs_iu
        self.fan_params_iu: dict = {
            "fan_min_flow_rate": self.dV_iu_fan_a_min,
            "fan_max_flow_rate": self.dV_iu_fan_a_max,
            "fan_ref_flow_rate": self.dV_iu_fan_a_rated,
            "fan_rated_flow_rate": self.dV_iu_fan_a_rated,
            "fan_ref_power": self.E_iu_fan_rated,
            "fan_rated_power": self.E_iu_fan_rated,
        }

        # --- 4. BHE ---
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
        self.E_pmp: float = E_pmp
        self.dV_b_f_m3s: float = dV_b_f_lpm / 60000

        # Effective borehole thermal resistance R_b* [mK/W].
        # Mirrors GroundSourceHeatPumpBoiler: when R_b is not given explicitly,
        # derive it from the borehole cross-section (multipole method) and then
        # apply the axial short-circuit correction, so T_bhe_f = T_bhe - q_b*R_b*
        # reflects the actual U-tube geometry instead of a fixed literature value.
        if R_b is None:
            from .borehole import (
                calc_effective_borehole_thermal_resistance,
                calc_local_borehole_thermal_resistance,
            )

            n_boreholes = max(1, self.N_1 * self.N_2)
            m_flow_pipe = self.dV_b_f_m3s * rho_w / n_boreholes

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
            self.R_b_local: float = R_b_local
            self.R_a: float = R_a
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

        if not all(np.isfinite(v) and v > 0 for v in (self.UA_cond, self.UA_evap)):
            raise ValueError("Heat exchanger UA values must be positive and finite")
        if not 0 < self.rps_min < self.rps_max or not np.isfinite(self.rps_max):
            raise ValueError("Require finite 0 < rps_min < rps_max")
        # Compressor reference state at rated (fixed) UA, before variable
        # ground-HX UA is configured with the m_dot_ref_rated it produces.
        efficiencies = self._initialize_reference_state(
            rps_rated=rps_rated,
            m_dot_ref_rated=m_dot_ref_rated,
            efficiencies={"eta_cmp_isen": eta_cmp_isen, "eta_cmp_vol": eta_cmp_vol, "eta_cmp": eta_cmp},
            rated_condition=rated_condition,
        )
        self.eta_cmp_isen = efficiencies["eta_cmp_isen"]
        self.eta_cmp_vol = efficiencies["eta_cmp_vol"]
        self.eta_cmp = efficiencies["eta_cmp"]
        self.eta_v, self.eta_em = self.eta_cmp_vol, self.eta_cmp  # read-compatible aliases
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
            pipe_inner_diameter=pipe_inner_diameter if pipe_inner_diameter is not None else 2 * r_in,
            pipe_roughness=pipe_roughness,
            dp_common=dp_common,
            dp_aux_ref=dp_aux_ref,
            dp_aux_exponent=dp_aux_exponent,
            m_dot_ref_rated=self.m_dot_ref_rated,
            ua_fractions=(ground_hx_fluid_fraction, ground_hx_refrigerant_fraction, ground_hx_constant_fraction),
            ua_exponents=(ground_hx_fluid_exponent, ground_hx_refrigerant_exponent),
            boundary_condition=boundary_condition,
            geometry=dict(
                k_s=k_s, k_g=k_g, k_p=k_p, r_b=r_b, r_out=r_out, r_in=r_in, D_s=D_s, rho_f=rho_w, mu_f=mu_w, k_f=k_w
            ),
        )
        self.ground_flow_control = self._ground_settings["control"]

        self.Ts: float = Ts
        self.Ts_K: float = cu.C2K(Ts)

        # --- 5. Room temperature ---
        self.T_a_room: float = T_a_room

        # --- Precompute g-function ---
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

        # --- Simulation state ---
        self.time: np.ndarray = np.array([])
        self.dt: float = dt_s
        self.T_bhe_f: float = Ts
        self.T_bhe: float = Ts
        self.T_bhe_f_in: float = Ts
        self.T_bhe_f_in_K: float = self.Ts_K
        self.T_bhe_f_out: float = Ts
        self.T_bhe_f_out_K: float = self.Ts_K
        self.Q_bhe: float = 0.0

    # =============================================================

    # =============================================================
    # Refrigerant cycle physics
    # =============================================================

    def _calc_state(
        self,
        dT_ref_evap: float,
        dT_ref_cond: float,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        *,
        ground_flow_ratio: float = 1.0,
        source_temperature_K: float | None = None,
    ) -> dict | None:
        """Evaluate refrigerant cycle at a given operating point.

        Parameters
        ----------
        dT_ref_evap, dT_ref_cond : float
            Approach ΔT [K].
        Q_r_iu : float
            Indoor thermal load [W]. >0 cooling, <0 heating, 0 off.
        T0 : float
            Dead-state / ambient temperature [°C].
        T_a_room : float
            Room air temperature [°C].
        """
        self._last_pr_event = None
        loop = ground_flow_state(self._ground_settings, ground_flow_ratio)
        T_a_room_K = cu.C2K(T_a_room)
        T_bhe_f_out_K = float(getattr(self, "T_bhe_f_out_K", self.Ts_K))

        if source_temperature_K is not None:
            T_bhe_f_out_K = source_temperature_K

        is_active = Q_r_iu != 0.0
        m_dot_cp_b = loop["dV"] * rho_w * c_w

        if Q_r_iu < 0:
            # Heating: BHE = evaporator (absorb from ground), IU = condenser (heat room)
            mode = "heating"
            T_source_K = T_bhe_f_out_K + (loop["E_pmp"] / m_dot_cp_b)
            T_evap_sat_K = T_source_K - dT_ref_evap
            T_cond_sat_K = T_a_room_K + dT_ref_cond
            Q_ref_iu = abs(Q_r_iu)
        elif Q_r_iu > 0:
            # Cooling: IU = evaporator (cool room), BHE = condenser (reject to ground)
            mode = "cooling"
            T_source_K = T_bhe_f_out_K + (loop["E_pmp"] / m_dot_cp_b)
            T_evap_sat_K = T_a_room_K - dT_ref_evap
            T_cond_sat_K = T_source_K + dT_ref_cond
            Q_ref_iu = Q_r_iu
        else:
            mode = "off"
            T_evap_sat_K = self.Ts_K
            T_cond_sat_K = self.Ts_K
            Q_ref_iu = 0.0

        # Low-lift feasibility is enforced downstream by the compressor
        # pressure-ratio floor (PR_cycle_min); a separate fixed minimum lift is
        # redundant and non-transferable across refrigerants/operating levels.

        actual_dT_subcool: float = min(self.dT_subcool, max(0.0, dT_ref_cond - self.dT_hx_min))
        actual_dT_superheat: float = min(self.dT_superheat, max(0.0, dT_ref_evap - self.dT_hx_min))

        # Always mode="heating" for calc_ref_state (avoids key swap)
        cycle_states = calc_ref_state(
            T_evap_K=T_evap_sat_K,
            T_cond_K=T_cond_sat_K,
            refrigerant=self.ref,
            eta_cmp_isen=1.0,
            mode=mode,
            dT_superheat=actual_dT_superheat,
            dT_subcool=actual_dT_subcool,
            is_active=is_active,
        )

        # Compressor pressure-ratio envelope guard (PR = P_cond / P_evap), the
        # physically primary lift limit. Ceiling -> reject (outside the
        # single-stage envelope); floor -> clamp the cycle onto PR_cycle_min by
        # holding P_evap and projecting P_cond, then refresh the cycle state.
        self._last_pr_event = None
        if is_active:
            P_evap = cycle_states["P_ref_cmp_in [Pa]"]
            P_cond = cycle_states["P_ref_cmp_out [Pa]"]
            ratio_P_cmp = P_cond / P_evap if P_evap > 0 else 1.0
            pr_event = check_pr_envelope(ratio_P_cmp, self.PR_cycle_min, self.PR_cycle_max)
            if pr_event == "pr_above_max":
                self._last_pr_event = ("pr_above_max", ratio_P_cmp, self.PR_cycle_max)
                return None
            if pr_event == "pr_below_min":
                self._last_pr_event = ("pr_below_min", ratio_P_cmp, self.PR_cycle_min)
                import CoolProp.CoolProp as CP

                P_cond = self.PR_cycle_min * P_evap
                T_cond_sat_K = CP.PropsSI("T", "P", P_cond, "Q", 0, self.ref)
                # Projection changes the condenser's physical approach. Its
                # available subcooling must follow that projected temperature,
                # not the pre-projection trial approach used by the HX search.
                sink_K = cu.C2K(T_a_room) if mode == "heating" else T_source_K
                actual_dT_subcool = min(self.dT_subcool, max(0.0, T_cond_sat_K - sink_K - self.dT_hx_min))
                cycle_states = calc_ref_state(
                    T_evap_K=T_evap_sat_K,
                    T_cond_K=T_cond_sat_K,
                    refrigerant=self.ref,
                    eta_cmp_isen=1.0,
                    mode=mode,
                    dT_superheat=actual_dT_superheat,
                    dT_subcool=actual_dT_subcool,
                    is_active=is_active,
                )

        if is_active:
            # Diagnostic temperatures follow the same cycle and caller-supplied
            # room temperature as the indoor HX, including pressure-ratio clamps.
            indoor_sat_K = T_cond_sat_K if mode == "heating" else T_evap_sat_K
            ground_sat_K = T_evap_sat_K if mode == "heating" else T_cond_sat_K
            self.T_r_iu = cu.K2C(indoor_sat_K)
            self.dT_r_iu = self.T_r_iu - T_a_room
            self.dT_r_ghx = ground_sat_K - T_bhe_f_out_K

        cmp_rps = 0.0
        converged_rps = True
        capacity_clamped = None
        val_eta_isen = val_eta_vol = val_eta_em = np.nan
        ratio_P_cmp = np.nan
        if is_active:
            # Unit isentropic state supplies pressure, suction density and work.
            # Each speed candidate evaluates the efficiencies before predicting duty.
            P_evap = cycle_states["P_ref_cmp_in [Pa]"]
            P_cond = cycle_states["P_ref_cmp_out [Pa]"]
            ratio_P_cmp = P_cond / P_evap
            rho_suction = cycle_states["rho_ref_cmp_in [kg/m3]"]
            h_in = cycle_states["h_ref_cmp_in [J/kg]"]
            dh_isen = cycle_states["h_ref_cmp_out [J/kg]"] - h_in
            h_liquid = cycle_states["h_ref_exp_in [J/kg]"]
            h_expansion = cycle_states["h_ref_exp_out [J/kg]"]

            def residual(rps: float) -> float:
                eta_vol = _eval_eff(self.eta_cmp_vol, ratio_P_cmp, rps)
                eta_isen = _eval_eff(self.eta_cmp_isen, ratio_P_cmp, rps)
                # Validate the drive model on every candidate as well. It acts
                # on input power only, not on the refrigerant heat duty.
                _eval_eff(self.eta_cmp, ratio_P_cmp, rps)
                m_dot = self.V_cmp_ref * rho_suction * eta_vol * rps
                h_out = h_in + dh_isen / eta_isen
                dh = h_in - h_expansion if mode == "cooling" else h_out - h_liquid
                return float(m_dot * dh - Q_ref_iu)

            cmp_rps, converged_rps, capacity_clamped = solve_compressor_speed(residual, self.rps_min, self.rps_max)
            val_eta_isen = _eval_eff(self.eta_cmp_isen, ratio_P_cmp, cmp_rps)
            val_eta_vol = _eval_eff(self.eta_cmp_vol, ratio_P_cmp, cmp_rps)
            val_eta_em = _eval_eff(self.eta_cmp, ratio_P_cmp, cmp_rps)
            cycle_states = calc_ref_state(
                T_evap_K=T_evap_sat_K,
                T_cond_K=T_cond_sat_K,
                refrigerant=self.ref,
                eta_cmp_isen=val_eta_isen,
                mode=mode,
                dT_superheat=actual_dT_superheat,
                dT_subcool=actual_dT_subcool,
                is_active=True,
            )
            m_dot_ref = self.V_cmp_ref * rho_suction * val_eta_vol * cmp_rps
        else:
            m_dot_ref = 0.0

        h_cmp_out = cycle_states["h_ref_cmp_out [J/kg]"]
        h_cmp_in = cycle_states["h_ref_cmp_in [J/kg]"]
        h_exp_in = cycle_states["h_ref_exp_in [J/kg]"]
        h_exp_out = cycle_states["h_ref_exp_out [J/kg]"]
        Q_ref_cond = m_dot_ref * (h_cmp_out - h_exp_in) if is_active else 0.0
        Q_ref_evap = m_dot_ref * (h_cmp_in - h_exp_out) if is_active else 0.0
        E_cmp_ref = m_dot_ref * (h_cmp_out - h_cmp_in) if is_active else 0.0
        E_cmp = E_cmp_ref / val_eta_em if is_active else 0.0
        E_cmp_loss = E_cmp - E_cmp_ref

        if is_active and E_cmp <= 0:
            return None

        # ── BHE energy balance ──
        if mode == "heating":
            Q_bhe = Q_ref_evap - loop["E_pmp"]
            T_bhe_f_in_K = T_source_K - Q_ref_evap / m_dot_cp_b
        elif mode == "cooling":
            Q_bhe = -(Q_ref_cond + loop["E_pmp"])  # negative = heat into ground
            T_bhe_f_in_K = T_source_K + Q_ref_cond / m_dot_cp_b
        else:
            Q_bhe = 0.0
            T_bhe_f_in_K = self.T_bhe_f_in_K

        Q_bhe_unit = calc_borefield_linear_load(Q_bhe, self.n_boreholes, self.H_b) if is_active else 0.0
        T_bhe_f = (cu.K2C(T_bhe_f_in_K) + cu.K2C(T_bhe_f_out_K)) / 2
        T_bhe = T_bhe_f + Q_bhe_unit * loop["R_b"]

        UA_ground_rated, UA_iu_rated = self._rated_hx_UAs(mode)
        # ── Indoor unit HX ──
        if mode == "cooling":
            iu_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_evap,
                T_a_in_C=T_a_room,
                T_ref_sat_K=T_evap_sat_K,
                A_cross=self.A_cross_iu,
                UA_rated=UA_iu_rated,
                dV_fan_ref=self.dV_iu_fan_a_ref,
                dV_fan_min=self.dV_iu_fan_a_min,
                dV_fan_max=self.dV_iu_fan_a_max,
                custom_fan_curve=not is_generic_fan_curve(self.vsd_coeffs_iu),
                is_active=is_active,
            )
        elif mode == "heating":
            iu_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_cond,
                T_a_in_C=T_a_room,
                T_ref_sat_K=T_cond_sat_K,
                A_cross=self.A_cross_iu,
                UA_rated=UA_iu_rated,
                dV_fan_ref=self.dV_iu_fan_a_ref,
                dV_fan_min=self.dV_iu_fan_a_min,
                dV_fan_max=self.dV_iu_fan_a_max,
                custom_fan_curve=not is_generic_fan_curve(self.vsd_coeffs_iu),
                is_active=is_active,
            )
        else:
            iu_hx = {
                "dV_fan": 0.0,
                "T_a_mid_C": T_a_room,
                "converged": True,
                "min_limit": False,
                "max_limit": False,
            }

        dV_iu_a = iu_hx["dV_fan"]
        T_iu_a_mid = iu_hx["T_a_mid_C"]
        E_iu_fan = calc_fan_power_from_dV_fan(
            dV_fan=dV_iu_a,
            fan_params=self.fan_params_iu,
            vsd_coeffs=self.vsd_coeffs_iu,
            is_active=is_active,
        )
        T_iu_a_out = T_iu_a_mid + E_iu_fan / (c_a * rho_a * dV_iu_a) if is_active and dV_iu_a > 0 else T_a_room
        v_iu_a = dV_iu_a / self.A_cross_iu if is_active else 0.0

        UA_ground_actual = ground_hx_UA(self._ground_settings, UA_ground_rated, ground_flow_ratio, m_dot_ref)
        Q_ground_available = 0.0
        # BHE NTU check (heating: evaporator constraint)
        if mode == "heating" and is_active:
            eps = calc_phase_change_hx_effectiveness(UA_ground_actual, loop["dV"] * rho_w, c_w)
            T_source_K_local = T_bhe_f_out_K + (loop["E_pmp"] / m_dot_cp_b)
            Q_evap_max = eps * m_dot_cp_b * (T_source_K_local - T_evap_sat_K)
            Q_ground_available = Q_evap_max
            err_Q_evap = Q_ref_evap - Q_evap_max
        elif mode == "cooling" and is_active:
            eps = calc_phase_change_hx_effectiveness(UA_ground_actual, loop["dV"] * rho_w, c_w)
            T_source_K_local = T_bhe_f_out_K + (loop["E_pmp"] / m_dot_cp_b)
            Q_cond_max = eps * m_dot_cp_b * (T_cond_sat_K - T_source_K_local)
            Q_ground_available = Q_cond_max
            err_Q_evap = Q_ref_cond - Q_cond_max
        else:
            err_Q_evap = 0.0

        # Total electrical input
        E_pmp_active = loop["E_pmp"] if is_active else 0.0
        E_tot = E_cmp + E_pmp_active + E_iu_fan

        result = reportable_state(cycle_states)
        result.update(
            {
                "hp_is_on": is_active,
                "mode": mode,
                "converged": bool(iu_hx.get("converged", True)),
                "Q_iu_HX_available [W]": iu_hx.get("Q_air", 0.0),
                "Q_iu_ref_required [W]": Q_ref_evap if mode == "cooling" else Q_ref_cond,
                "iu_hx_capacity_margin [W]": iu_hx.get("capacity_margin_W", 0.0),
                "iu_hx_min_flow_margin [W]": iu_hx.get("min_flow_capacity_margin_W", 0.0),
                "iu_hx_max_flow_margin [W]": iu_hx.get("max_flow_capacity_margin_W", 0.0),
                "converged_rps": converged_rps,
                "capacity_clamped": capacity_clamped,
                "iu_fan_flow_min_limit": iu_hx.get("min_limit", False),
                "iu_fan_flow_max_limit": iu_hx.get("max_limit", False),
                "err_Q_evap [W]": err_Q_evap,
                # Temperatures [°C]
                "T_iu_a_in [°C]": T_a_room,
                "T_iu_a_mid [°C]": T_iu_a_mid,
                "T_iu_a_out [°C]": T_iu_a_out,
                "T_a_room [°C]": T_a_room,
                "T0 [°C]": T0,
                "Ts [°C]": self.Ts,
                "T_bhe [°C]": T_bhe,
                "T_bhe_f [°C]": T_bhe_f,
                "T_bhe_f_in [°C]": cu.K2C(T_bhe_f_in_K),
                "T_bhe_f_out [°C]": cu.K2C(T_bhe_f_out_K),
                # Volume flow rates
                "dV_iu_a [m3/s]": dV_iu_a,
                "v_iu_a [m/s]": v_iu_a,
                "dV_bhe_f [m3/s]": loop["dV"] if is_active else 0.0,
                "m_dot_ref [kg/s]": m_dot_ref,
                "cmp_rpm [rpm]": cmp_rps * 60,
                "cmp_rps [rev/s]": cmp_rps,
                "n_star [-]": cmp_rps / self.rps_rated,
                "pressure_ratio": ratio_P_cmp,
                "pr_floor_active": self._last_pr_event is not None and self._last_pr_event[0] == "pr_below_min",
                # Energy rates [W]
                "E_iu_fan [W]": E_iu_fan,
                "E_pmp [W]": E_pmp_active,
                # Heat duties by physical location (mode-mapped): in heating the indoor
                # unit is the condenser and the ground loop the evaporator; in cooling
                # the roles swap. Reported by location so the labels are mode-independent
                # and the consumer never sees the cond/evap bookkeeping (the
                # refrigerant-perspective cond/evap remain only in the refrigerant-state
                # keys T/P/h/s_ref_*_sat and in refrigerant.py).
                "Q_ref_iu [W]": Q_ref_cond if mode == "heating" else Q_ref_evap,
                "Q_ref_ground [W]": Q_ref_evap if mode == "heating" else Q_ref_cond,
                "Q_bhe [W]": Q_bhe,
                "E_cmp [W]": E_cmp,
                # Electro-mechanical losses are external to the refrigerant
                # cycle, as in the other TMHP compressor models. Condenser
                # duty includes refrigerant work, not those external losses.
                "E_cmp_ref [W]": E_cmp_ref,
                "E_cmp_loss [W]": E_cmp_loss,
                "eta_is [-]": val_eta_isen,
                "eta_v [-]": val_eta_vol,
                "eta_em [-]": val_eta_em,
                "UA_ground_rated [W/K]": UA_ground_rated,
                "UA_iu_rated [W/K]": UA_iu_rated,
                "E_tot [W]": E_tot,
                # COP (indoor-unit duty basis; == |Q_r_iu| at convergence)
                "cop_ref [-]": (
                    (Q_ref_cond if mode == "heating" else Q_ref_evap) / E_cmp if (is_active and E_cmp > 0) else np.nan
                ),
                "cop_sys [-]": (
                    (Q_ref_cond if mode == "heating" else Q_ref_evap) / E_tot if (is_active and E_tot > 0) else np.nan
                ),
            }
        )
        ground_result_diagnostics(
            result,
            self._ground_settings,
            loop,
            ground_flow_ratio,
            UA_ground_actual,
            Q_ground_available,
            result["Q_ref_ground [W]"],
        )
        if (
            (self._ground_settings["active"] or source_temperature_K is not None)
            and is_active
            and (capacity_clamped is not None or not converged_rps)
        ):
            result["converged_rps"] = False
            result["failure_reason"] = "compressor_min_speed" if capacity_clamped == "min" else "compressor_max_speed"
        return result

    def _rating_side(self, role: str, mode: str, T_in_C: float) -> HXSide:
        """Ground loop is the source side, indoor coil the load side, in either mode."""
        UA_ground, UA_iu = self._rated_hx_UAs(mode)
        if role == "source":
            return HXSide("water", T_in_C, UA_ground, self.dV_b_f_m3s)
        return HXSide("air", T_in_C, UA_iu, self.dV_iu_fan_a_ref)

    def _rated_hx_UAs(self, mode: str) -> tuple[float, float]:
        """Return ground/IU physical UA; old explicit role inputs use an adapter.

        Defaults and physical inputs remain identical in cooling and heating.
        Only deprecated role-based inputs preserve the old mode swap.
        """
        ground, indoor = self.UA_ground_rated, self.UA_iu_rated
        if self._legacy_ground_ua:
            ground = self.UA_evap if mode == "heating" else self.UA_cond
        if self._legacy_indoor_ua:
            indoor = self.UA_cond if mode == "heating" else self.UA_evap
        return ground, indoor

    # =============================================================
    # 2D Optimisation
    # =============================================================

    def _solve_ground_flow_point(
        self, ratio: float, Q_r_iu: float, T0: float, T_a_room: float, wall_K: float | Callable[[float], float]
    ) -> dict:
        """At fixed flow, close the ground HX and optimize the indoor approach."""
        from scipy.optimize import brentq, minimize_scalar

        cache: dict[float, dict] = {}
        minimum_ground_cache: dict[float, dict | None] = {}

        def evaluate_ground(load_approach: float, ground_approach: float) -> dict | None:
            evap, cond = (ground_approach, load_approach) if Q_r_iu < 0 else (load_approach, ground_approach)
            return close_ground_temperature(
                lambda temperature: self._calc_state(
                    evap, cond, Q_r_iu, T0, T_a_room, ground_flow_ratio=ratio, source_temperature_K=temperature
                ),
                wall_K,
                self.n_boreholes,
                self.H_b,
            )

        def evaluate_load_approach(load_approach: float) -> dict:
            if load_approach not in cache:
                cache[load_approach] = solve_ground_approach(
                    lambda ground: evaluate_ground(load_approach, ground), "Q_ref_iu [W]", abs(Q_r_iu)
                )
                if cache[load_approach].get("failure_reason") == "cycle_invalid" and self._last_pr_event is not None:
                    cache[load_approach]["failure_reason"] = "pressure_ratio_limit"
            return cache[load_approach]

        def objective(x: float) -> float:
            row = evaluate_load_approach(float(x))
            return float(row["E_tot [W]"]) if row.get("converged", False) else 1e30

        grid = np.linspace(self.indoor_approach_min_K, self.indoor_approach_max_K, 7)
        powers = [objective(float(x)) for x in grid]

        # PR-floor projection makes the ground-HX duty residual flat in ground
        # approach, creating a narrow feasible boundary in indoor approach.
        # Resolve that physical equality explicitly: a bounded smooth search
        # can otherwise miss it and return a higher-power point just above PRmin.
        def minimum_ground_residual(load_approach: float) -> float:
            if load_approach not in minimum_ground_cache:
                minimum_ground_cache[load_approach] = evaluate_ground(load_approach, 1.0)
            row = minimum_ground_cache[load_approach]
            if row is None:
                raise ValueError("Invalid cycle at minimum ground approach")
            return float(row["Q_ref_required [W]"] - row["Q_HX_available [W]"])

        previous = None
        for x in grid:
            x = float(x)
            try:
                residual = minimum_ground_residual(x)
            except ValueError:
                previous = None
                continue
            if previous is not None and previous[1] * residual <= 0:
                with contextlib.suppress(ValueError, RuntimeError):
                    root = float(brentq(minimum_ground_residual, previous[0], x, xtol=1e-10))
                    row = minimum_ground_cache[root]
                    if row is not None and row.get("pr_floor_active", False):
                        objective(root)
            previous = (x, residual)
        # A clamped fan can satisfy both HX duties only on the airflow
        # boundary. Solve its signed capacity margin, avoiding the zero
        # plateau of the already solved indoor duty.
        for margin_key in ("iu_hx_min_flow_margin [W]", "iu_hx_max_flow_margin [W]"):
            previous_fan = None
            for x in grid:
                x = float(x)
                margin = evaluate_load_approach(x).get(margin_key, np.nan)
                if not np.isfinite(margin):
                    previous_fan = None
                    continue
                if previous_fan is not None and previous_fan[1] * margin < 0:
                    with contextlib.suppress(ValueError, RuntimeError):
                        root = float(
                            brentq(
                                lambda approach, key=margin_key: evaluate_load_approach(float(approach))[key],
                                previous_fan[0],
                                x,
                                xtol=1e-10,
                            )
                        )
                        objective(root)
                previous_fan = (x, margin)
        feasible = [i for i, power in enumerate(powers) if power < 1e30]
        if not feasible:
            boundary = [r for r in cache.values() if r.get("converged", False)]
            if boundary:
                return min(boundary, key=lambda r: r["E_tot [W]"])
            rows = list(cache.values())
            return next(
                (r for r in rows if r["failure_reason"] == "load_hx_capacity_insufficient"),
                next((r for r in rows if r["failure_reason"] == "ground_hx_capacity_insufficient"), rows[0]),
            )
        best = min(feasible, key=lambda i: powers[i])
        optimum = minimize_scalar(
            objective,
            bounds=(float(grid[max(0, best - 1)]), float(grid[min(len(grid) - 1, best + 1)])),
            method="bounded",
            options={"xatol": 1e-3, "maxiter": 30},
        )
        objective(float(optimum.x))
        return min((r for r in cache.values() if r.get("converged", False)), key=lambda r: r["E_tot [W]"])

    def _solve_ground_flow(
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
        *,
        ground_flow_ratio: float | None = None,
        T_bhe_wall: float | None = None,
        wall_response: Callable[[float], float] | None = None,
    ) -> dict:
        if Q_r_iu == 0:
            result = self._calc_state(5, 5, 0, T0, T_a_room)
            assert result is not None
            result.update({"failure_reason": "none", "hx_feasible": True})
            result["E_iu_fan [W]"] = result["E_tot [W]"] = 0.0
            return result
        wall_K = (
            wall_response if wall_response is not None else cu.C2K(self.T_bhe if T_bhe_wall is None else T_bhe_wall)
        )
        selected = select_ground_flow(
            lambda ratio: self._solve_ground_flow_point(ratio, Q_r_iu, T0, T_a_room, wall_K),
            self._ground_settings,
            ground_flow_ratio,
        )
        if "h_ref_cmp_in [J/kg]" not in selected:
            off = self._calc_state(5, 5, 0, T0, T_a_room) or {}
            off.update(selected)
            selected = off
        return selected

    def _optimize_operation(self, Q_r_iu: float, T0: float, T_a_room: float):
        """Find min-power point: E_cmp + E_pmp + E_iu_fan."""

        def _objective(params) -> float:
            dT_evap, dT_cond = params
            perf = self._calc_state(dT_evap, dT_cond, Q_r_iu, T0, T_a_room)
            if perf is None or not perf.get("converged", False):
                return 1e6
            E_tot = float(perf.get("E_tot [W]", 1e6))
            if E_tot <= 0 or np.isnan(E_tot):
                return 1e6
            err_Q = float(perf.get("err_Q_evap [W]", 0.0))
            penalty = max(0.0, err_Q) * 1000.0
            return E_tot + penalty

        # Adaptive initial guess: ensure dT_evap + dT_cond > |T_room - T_ground|
        # so T_evap_sat < T_cond_sat from the start.
        T_ground = cu.K2C(self.T_bhe_f_out_K)
        gap = abs(T_a_room - T_ground)
        x0_dt = max(5.0, (gap + 4.0) / 2.0)  # each ΔT gets half the gap + margin

        return minimize(
            _objective,
            x0=[x0_dt, x0_dt],
            bounds=[
                (1.0, 20.0) if Q_r_iu < 0 else (self.indoor_approach_min_K, self.indoor_approach_max_K),
                (self.indoor_approach_min_K, self.indoor_approach_max_K) if Q_r_iu < 0 else (1.0, 20.0),
            ],
            method="Nelder-Mead",
            options={"maxiter": 200, "xatol": 1e-3, "fatol": 1e-1},
        )

    # =============================================================
    # BHE g-function superposition
    # =============================================================

    def _compute_bhe_superposition(
        self,
        n: int,
        time_arr: np.ndarray,
        Q_bhe_unit_pulse: np.ndarray,
        Q_bhe_unit_old: float,
        hp_result: dict,
        hp_is_on: bool,
    ) -> float:
        """Temporal superposition for BHE — from GSHPB."""
        Q_bhe_unit = (
            calc_borefield_linear_load(hp_result.get("Q_bhe [W]", 0.0), self.n_boreholes, self.H_b) if hp_is_on else 0.0
        )

        if abs(Q_bhe_unit - Q_bhe_unit_old) > 1e-6:
            Q_bhe_unit_pulse[n] = Q_bhe_unit - Q_bhe_unit_old
            Q_bhe_unit_old = Q_bhe_unit

        pulses_idx = np.flatnonzero(Q_bhe_unit_pulse[: n + 1])
        if len(pulses_idx) > 0:
            dQ = Q_bhe_unit_pulse[pulses_idx]
            tau = time_arr[n] - time_arr[pulses_idx]
            tau = np.maximum(tau, 1e-6)
            g_n_array = self._gfunc_interp(tau)
            dT_bhe = float(np.dot(dQ, g_n_array))
        else:
            dT_bhe = 0.0

        self.T_bhe = self.Ts - dT_bhe
        T_bhe_K = cu.C2K(self.T_bhe)
        T_bhe_f_K = T_bhe_K - Q_bhe_unit * hp_result.get("R_b_eff [mK/W]", self.R_b)
        self.T_bhe_f = cu.K2C(T_bhe_f_K)
        self.Q_bhe = Q_bhe_unit * self.total_borehole_length
        m_cp_b = c_w * rho_w * hp_result.get("dV_bhe_f [m3/s]", self.dV_b_f_m3s)

        dT_half = float((self.Q_bhe / m_cp_b) / 2) if m_cp_b > 0 else 0.0
        self.T_bhe_f_in_K = T_bhe_f_K - dT_half
        self.T_bhe_f_in = cu.K2C(self.T_bhe_f_in_K)
        self.T_bhe_f_out_K = T_bhe_f_K + dT_half
        self.T_bhe_f_out = cu.K2C(self.T_bhe_f_out_K)

        hp_result["T_bhe [°C]"] = self.T_bhe
        hp_result["T_bhe_f [°C]"] = self.T_bhe_f
        hp_result["T_bhe_f_in [°C]"] = self.T_bhe_f_in
        hp_result["T_bhe_f_out [°C]"] = self.T_bhe_f_out

        return Q_bhe_unit_old

    # =============================================================
    # Steady-state analysis
    # =============================================================

    def analyze_steady(
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float | None = None,
        *,
        return_dict: bool = True,
        ground_flow_ratio: float | None = None,
        ground_flow_lpm: float | None = None,
        T_bhe_wall: float | None = None,
    ) -> dict | pd.DataFrame:
        """Run a steady-state performance snapshot.

        Returns
        -------
        dict | pd.DataFrame
            Cycle state plus diagnostic flags. Notable keys:

            - ``"converged"`` (bool) — True only when the HX optimisation and
              the SciPy optimiser both succeeded.
            - ``"failure_reason"`` (str) — one of ``"none"``,
              ``"cycle_invalid"``, ``"hx_not_converged"``, or
              ``"optimizer_failed"``.

            GSHP triggers an off-mode fallback only when the refrigerant cycle
            itself was infeasible (``"cycle_invalid"``); in that case
            ``E_cmp [W]`` is 0 and COP keys are NaN. The other non-``"none"``
            values are diagnostic — the cycle numbers are populated and
            usable.
        """
        if T_a_room is None:
            T_a_room = self.T_a_room

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
                Q_r_iu, T0, T_a_room, ground_flow_ratio=ground_flow_ratio, T_bhe_wall=T_bhe_wall
            )
            return result if return_dict else pd.DataFrame([result])

        if Q_r_iu == 0:
            result = self._calc_state(5.0, 5.0, 0.0, T0, T_a_room)
            if result is None:
                result = {
                    "hp_is_on": False,
                    "converged": False,
                    "failure_reason": "cycle_invalid",
                    "Q_ref_iu [W]": 0.0,
                    "Q_ref_ground [W]": 0.0,
                    "T0 [°C]": T0,
                    "T_a_room [°C]": T_a_room,
                }
            else:
                result["failure_reason"] = "none"
        else:
            opt = self._optimize_operation(Q_r_iu, T0, T_a_room)
            result = None
            with contextlib.suppress(Exception):
                result = self._calc_state(opt.x[0], opt.x[1], Q_r_iu, T0, T_a_room)

            # Diagnose; the fallback trigger condition stays `result is None`
            # to match the historical behaviour of this branch (a converged
            # cycle with `result["converged"] == False` is still returned).
            opt_success = bool(getattr(opt, "success", False))
            pr_event = self._last_pr_event
            if result is None:
                # Distinguish a pressure-ratio ceiling rejection from a generic
                # invalid cycle so downstream consumers see the specific cause.
                failure_reason = (
                    "pr_above_max" if pr_event is not None and pr_event[0] == "pr_above_max" else "cycle_invalid"
                )
            elif not result.get("converged", False):
                failure_reason = "hx_not_converged"
            elif not opt_success:
                failure_reason = "optimizer_failed"
            else:
                failure_reason = "none"

            if result is None:
                warnings.warn(
                    f"analyze_steady: fell back to HP-off state "
                    f"(reason={failure_reason!r}, Q_r_iu={Q_r_iu:.0f}W, "
                    f"T0={T0:.1f}°C, T_a_room={T_a_room:.1f}°C, "
                    f"opt_success={opt_success}, "
                    f"opt_x=({opt.x[0]:.2f}, {opt.x[1]:.2f}), "
                    f"opt_fun={float(getattr(opt, 'fun', float('nan'))):.3g}). "
                    "Consider calibrating UA_rated or increasing the explicit fan-flow maximum.",
                    RuntimeWarning,
                    stacklevel=2,
                )
                result = self._calc_state(5.0, 5.0, 0.0, T0, T_a_room)
                if result is None:
                    result = {
                        "hp_is_on": False,
                        "converged": False,
                        "failure_reason": failure_reason,
                        "Q_ref_iu [W]": 0.0,
                        "Q_ref_ground [W]": 0.0,
                        "T0 [°C]": T0,
                        "T_a_room [°C]": T_a_room,
                    }
                else:
                    result["converged"] = False
                    result["failure_reason"] = failure_reason
            else:
                # `result` is a valid dict — keep it, attach the diagnostic.
                result["converged"] = opt_success and result.get("converged", True)
                result["failure_reason"] = failure_reason

        if return_dict:
            return result
        return pd.DataFrame([result])

    # =============================================================
    # Dynamic simulation
    # =============================================================

    def analyze_dynamic(
        self,
        simulation_period_sec: int,
        dt_s: int,
        Q_r_iu_schedule,
        T0_schedule,
        T_a_room_schedule=None,
        result_save_csv_path: str | None = None,
    ) -> pd.DataFrame:
        """Time-stepping dynamic simulation with BHE superposition."""
        time = np.arange(0, simulation_period_sec, dt_s)
        tN = len(time)

        T0_schedule = np.array(T0_schedule)
        Q_r_iu_schedule = np.array(Q_r_iu_schedule, dtype=float)

        if len(T0_schedule) != tN:
            raise ValueError(f"T0_schedule length ({len(T0_schedule)}) != tN ({tN})")
        if len(Q_r_iu_schedule) != tN:
            raise ValueError(f"Q_r_iu_schedule length ({len(Q_r_iu_schedule)}) != tN ({tN})")

        if T_a_room_schedule is not None:
            T_a_room_arr = np.array(T_a_room_schedule, dtype=float)
        else:
            T_a_room_arr = np.full(tN, self.T_a_room)

        self.time = time
        self.dt = dt_s

        # Reset BHE state
        self.T_bhe_f = self.Ts
        self.T_bhe = self.Ts
        self.T_bhe_f_in = self.Ts
        self.T_bhe_f_in_K = self.Ts_K
        self.T_bhe_f_out = self.Ts
        self.T_bhe_f_out_K = self.Ts_K
        self.Q_bhe = 0.0

        Q_bhe_unit_pulse = np.zeros(tN)
        Q_bhe_unit_old = 0.0

        results_data: list[dict] = []

        for n in tqdm(range(tN), desc="GSHP Simulating"):
            t_s = time[n]
            hr = t_s * cu.s2h
            Q_r_iu_n = Q_r_iu_schedule[n]
            T0_n = T0_schedule[n]
            T_a_room_n = T_a_room_arr[n]

            if self._ground_settings["active"]:
                # Preview the current-time wall for each candidate, without recording pulses.
                def wall_response(q_total, step=n, previous_q=Q_bhe_unit_old):
                    idx = np.flatnonzero(Q_bhe_unit_pulse[:step])
                    rise = (
                        float(
                            np.dot(Q_bhe_unit_pulse[idx], self._gfunc_interp(np.maximum(time[step] - time[idx], 1e-6)))
                        )
                        if len(idx)
                        else 0.0
                    )
                    delta = q_total / self.total_borehole_length - previous_q
                    if abs(delta) > 1e-6:
                        rise += float(delta * self._gfunc_interp(np.array([1e-6]))[0])
                    return self.Ts_K - rise

                hp_result = self._solve_ground_flow(Q_r_iu_n, T0_n, T_a_room_n, wall_response=wall_response)
            elif Q_r_iu_n == 0:
                hp_result = self._calc_state(5.0, 5.0, 0.0, T0_n, T_a_room_n)
            else:
                opt = self._optimize_operation(Q_r_iu_n, T0_n, T_a_room_n)
                hp_result = self._calc_state(opt.x[0], opt.x[1], Q_r_iu_n, T0_n, T_a_room_n)

            if not self._ground_settings["active"] and (hp_result is None or not hp_result.get("converged", False)):
                hp_result = self._calc_state(5.0, 5.0, 0.0, T0_n, T_a_room_n)
                if hp_result is None:
                    # Off-mode cycle itself failed — fall back to an inert
                    # row so downstream BHE superposition / DataFrame
                    # assembly don't see a None.
                    hp_result = {
                        "hp_is_on": False,
                        "converged": False,
                        "Q_bhe [W]": 0.0,
                    }
                else:
                    hp_result["converged"] = False

            assert hp_result is not None
            hp_is_on = bool(hp_result.get("hp_is_on", False))

            # BHE superposition
            Q_bhe_unit_old = self._compute_bhe_superposition(
                n,
                time,
                Q_bhe_unit_pulse,
                Q_bhe_unit_old,
                hp_result,
                hp_is_on,
            )

            hp_result["time [s]"] = t_s
            hp_result["time [h]"] = hr
            results_data.append(hp_result)

        results_df = pd.DataFrame(results_data)
        results_df = self.postprocess_exergy(results_df)
        if result_save_csv_path:
            results_df.to_csv(result_save_csv_path, index=False)
        return results_df

    # =============================================================
    # Exergy post-processing
    # =============================================================

    def postprocess_exergy(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute GSHP-specific exergy: 6 subsystems × (X_in, Xc, X_out)."""
        from .enex_functions import (
            calc_refrigerant_exergy,
            convert_electricity_to_exergy,
        )

        df = df.copy()
        if "T0 [°C]" not in df.columns:
            return df

        T0_K = cu.C2K(df["T0 [°C]"])

        # ── 1. Refrigerant exergy ──
        if "h_ref_cmp_in [J/kg]" not in df.columns:
            return df
        df = calc_refrigerant_exergy(df, self.ref, T0_K)

        # ── 2. Electricity = exergy ──
        df = convert_electricity_to_exergy(df)
        if "E_iu_fan [W]" in df.columns:
            df["X_iu_fan [W]"] = df["E_iu_fan [W]"]
        if "E_pmp [W]" in df.columns:
            df["X_pmp [W]"] = df["E_pmp [W]"]

        # ── 3a. Indoor unit air exergy ──
        if "dV_iu_a [m3/s]" in df.columns and "T_iu_a_in [°C]" in df.columns:
            G_a_iu = c_a * rho_a * df["dV_iu_a [m3/s]"].fillna(0)
            Tin_iu = cu.C2K(df["T_iu_a_in [°C]"])
            Tmid_iu = cu.C2K(df["T_iu_a_mid [°C]"])
            Tout_iu = cu.C2K(df["T_iu_a_out [°C]"]) if "T_iu_a_out [°C]" in df.columns else Tin_iu
            df["X_a_iu_in [W]"] = calc_exergy_flow(G_a_iu, Tin_iu, T0_K)
            df["X_a_iu_out [W]"] = calc_exergy_flow(G_a_iu, Tout_iu, T0_K)
            df["X_a_iu_mid [W]"] = calc_exergy_flow(G_a_iu, Tmid_iu, T0_K)

        # ── 3b. BHE fluid exergy ──
        if "dV_bhe_f [m3/s]" in df.columns and "T_bhe_f_in [°C]" in df.columns:
            G_b = c_w * rho_w * df["dV_bhe_f [m3/s]"].fillna(0)
            T_bhe_f_in_K = cu.C2K(df["T_bhe_f_in [°C]"])
            T_bhe_f_out_K = cu.C2K(df["T_bhe_f_out [°C]"])
            df["X_bhe_f_in [W]"] = calc_exergy_flow(G_b, T_bhe_f_in_K, T0_K)
            df["X_bhe_f_out [W]"] = calc_exergy_flow(G_b, T_bhe_f_out_K, T0_K)
            # Evaporator inlet = BHE outlet + pump work
            T_evap_in_K = T_bhe_f_out_K + df["E_pmp [W]"].fillna(0) / G_b.replace(0, np.nan)
            T_evap_in_K = T_evap_in_K.fillna(T_bhe_f_out_K)
            df["X_evap_in [W]"] = calc_exergy_flow(G_b, T_evap_in_K, T0_K)

        # ── 4. Carnot exergy (by physical location, mode-aware) ──
        # The Carnot factor for each location uses the saturation temperature of
        # the refrigerant role it plays: heating -> IU=condenser, ground=evaporator;
        # cooling -> roles swap. Output exergy is labelled by location
        # (X_ref_iu / X_ref_ground); refrigerant-state saturation keys stay cond/evap.
        if {"T_ref_cond_sat_v [°C]", "T_ref_evap_sat [°C]", "mode"} <= set(df.columns):
            is_heating = df["mode"] == "heating"
            T_iu_sat_K = cu.C2K(df["T_ref_cond_sat_v [°C]"].where(is_heating, df["T_ref_evap_sat [°C]"]))
            T_ground_sat_K = cu.C2K(df["T_ref_evap_sat [°C]"].where(is_heating, df["T_ref_cond_sat_v [°C]"]))
            df["X_ref_iu [W]"] = df["Q_ref_iu [W]"] * (1 - T0_K / T_iu_sat_K)
            df["X_ref_ground [W]"] = df["Q_ref_ground [W]"] * (1 - T0_K / T_ground_sat_K)

        # ── 5. Total exergy input ──
        X_tot = df["E_cmp [W]"] + df["E_pmp [W]"].fillna(0) + df["E_iu_fan [W]"].fillna(0)
        df["X_tot [W]"] = X_tot

        # ── 6. Component exergy destruction (X_in, Xc, X_out) ──
        X_a_iu_in = df.get("X_a_iu_in [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_iu_mid = df.get("X_a_iu_mid [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_iu_out = df.get("X_a_iu_out [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_bhe_f_in = df.get("X_bhe_f_in [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_bhe_f_out = df.get("X_bhe_f_out [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_evap_in = df.get("X_evap_in [W]", pd.Series(0.0, index=df.index)).fillna(0)

        if "X_cmp [W]" not in df.columns:
            return df

        is_heating = df["mode"] == "heating"
        is_cooling = df["mode"] == "cooling"

        # 6a. Compressor
        df["X_in_cmp [W]"] = df["X_cmp [W]"] + df["X_ref_cmp_in [W]"]
        df["X_out_cmp [W]"] = df["X_ref_cmp_out [W]"]
        df["Xc_cmp [W]"] = df["X_in_cmp [W]"] - df["X_out_cmp [W]"]

        # 6b. Expansion valve
        df["X_in_exp [W]"] = df["X_ref_exp_in [W]"]
        df["X_out_exp [W]"] = df["X_ref_exp_out [W]"]
        df["Xc_exp [W]"] = df["X_in_exp [W]"] - df["X_out_exp [W]"]

        # 6c. Indoor Unit HX (mode-aware)
        X_in_iu_hx = pd.Series(0.0, index=df.index)
        X_out_iu_hx = pd.Series(0.0, index=df.index)
        # Heating: IU = condenser
        X_in_iu_hx[is_heating] = df.loc[is_heating, "X_ref_cmp_out [W]"] + X_a_iu_in[is_heating]
        X_out_iu_hx[is_heating] = df.loc[is_heating, "X_ref_exp_in [W]"] + X_a_iu_mid[is_heating]
        # Cooling: IU = evaporator
        X_in_iu_hx[is_cooling] = df.loc[is_cooling, "X_ref_exp_out [W]"] + X_a_iu_in[is_cooling]
        X_out_iu_hx[is_cooling] = df.loc[is_cooling, "X_ref_cmp_in [W]"] + X_a_iu_mid[is_cooling]
        df["X_in_iu_hx [W]"] = X_in_iu_hx
        df["X_out_iu_hx [W]"] = X_out_iu_hx
        df["Xc_iu_hx [W]"] = X_in_iu_hx - X_out_iu_hx

        # 6d. BHE HX (mode-aware)
        X_in_bhe_hx = pd.Series(0.0, index=df.index)
        X_out_bhe_hx = pd.Series(0.0, index=df.index)
        # Heating: BHE = evaporator → ref(exp_out→cmp_in), fluid(evap_in→bhe_f_in)
        X_in_bhe_hx[is_heating] = df.loc[is_heating, "X_ref_exp_out [W]"] + X_evap_in[is_heating]
        X_out_bhe_hx[is_heating] = df.loc[is_heating, "X_ref_cmp_in [W]"] + X_bhe_f_in[is_heating]
        # Cooling: BHE = condenser → ref(cmp_out→exp_in), fluid(evap_in→bhe_f_in)
        X_in_bhe_hx[is_cooling] = df.loc[is_cooling, "X_ref_cmp_out [W]"] + X_evap_in[is_cooling]
        X_out_bhe_hx[is_cooling] = df.loc[is_cooling, "X_ref_exp_in [W]"] + X_bhe_f_in[is_cooling]
        df["X_in_bhe_hx [W]"] = X_in_bhe_hx
        df["X_out_bhe_hx [W]"] = X_out_bhe_hx
        df["Xc_bhe_hx [W]"] = X_in_bhe_hx - X_out_bhe_hx

        # 6e. Pump
        df["X_in_pmp [W]"] = df["X_pmp [W]"].fillna(0) + X_bhe_f_out
        df["X_out_pmp [W]"] = X_evap_in
        df["Xc_pmp [W]"] = df["X_in_pmp [W]"] - df["X_out_pmp [W]"]

        # 6f. Indoor fan
        df["X_in_iu_fan [W]"] = df["X_iu_fan [W]"].fillna(0) + X_a_iu_mid
        df["X_out_iu_fan [W]"] = X_a_iu_out
        df["Xc_iu_fan [W]"] = df["X_in_iu_fan [W]"] - df["X_out_iu_fan [W]"]

        # ── 7. Efficiencies ──
        df["X_eff_sys [-]"] = (X_a_iu_out - X_a_iu_in) / df["X_tot [W]"].replace(0, np.nan)
        df["X_eff_cmp [-]"] = 1 - df["Xc_cmp [W]"] / df["X_in_cmp [W]"].replace(0, np.nan)
        df["X_eff_exp [-]"] = 1 - df["Xc_exp [W]"] / df["X_in_exp [W]"].replace(0, np.nan)
        df["X_eff_iu_hx [-]"] = 1 - df["Xc_iu_hx [W]"] / df["X_in_iu_hx [W]"].replace(0, np.nan)
        df["X_eff_bhe_hx [-]"] = 1 - df["Xc_bhe_hx [W]"] / df["X_in_bhe_hx [W]"].replace(0, np.nan)
        df["X_eff_pmp [-]"] = 1 - df["Xc_pmp [W]"] / df["X_in_pmp [W]"].replace(0, np.nan)
        df["X_eff_iu_fan [-]"] = 1 - df["Xc_iu_fan [W]"] / df["X_in_iu_fan [W]"].replace(0, np.nan)

        return df
