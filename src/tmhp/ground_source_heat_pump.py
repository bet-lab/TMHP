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

Architecture mirrors ``GroundSourceHeatPumpBoiler`` for the BHE side
and ``AirSourceHeatPump`` for the indoor-unit side.
"""

from __future__ import annotations

import contextlib
import math
import warnings
from collections.abc import Callable

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from tqdm import tqdm

from . import calc_util as cu
from .constants import c_a, c_w, rho_a, rho_w
from .enex_functions import (
    calc_exergy_flow,
    calc_fan_power_from_dV_fan,
    calc_HX_perf_for_target_heat,
)
from .g_function import precompute_gfunction
from .refrigerant import (
    calc_ref_state,
)


class GroundSourceHeatPump:
    """Ground source heat pump with BHE and indoor-unit air heat exchange.

    The refrigerant cycle is resolved via CoolProp.  A bounded 2-D
    optimiser minimises total electrical input (``E_cmp + E_pmp + E_iu_fan``)
    over the evaporator and condenser approach temperatures.
    """

    def __init__(
        self,
        # 1. Refrigerant / cycle / compressor -----------
        ref: str = "R32",
        V_cmp_ref: float | None = None,
        eta_cmp_isen: float | Callable = 0.80,
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
        R_b: float = 0.108,
        dV_b_f_lpm: float = 20.04,
        k_s: float = 2.0,
        c_s: float = 800,
        rho_s: float = 2000,
        Ts: float = 16.0,
        E_pmp: float = 100,
        # 5. System capacity / room ----------------------
        hp_capacity: float = 4000.0,
        T_a_room: float = 27.0,
        # 6. Cycle guard ---------------------------------
        dT_cycle_min: float | None = None,
        dT_hx_min: float = 0.5,
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
    ):
        # Resolve deprecated mapping
        if V_cmp_ref is None:
            V_cmp_ref = V_disp_cmp if V_disp_cmp is not None else 0.0001
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
        self.eta_cmp_isen: float | Callable = eta_cmp_isen
        self.dT_superheat: float = dT_superheat
        self.dT_subcool: float = dT_subcool
        if dT_cycle_min is None:
            self.dT_cycle_min: float = self.dT_subcool
        else:
            self.dT_cycle_min = float(dT_cycle_min)
        self.dT_hx_min: float = dT_hx_min
        self.hp_capacity: float = hp_capacity

        # --- 2. Heat exchanger UA ---
        if UA_cond is None:
            self.UA_cond = hp_capacity / 10.0
        else:
            self.UA_cond = UA_cond

        if UA_evap is None:
            self.UA_evap = self.UA_cond * 0.8
        else:
            self.UA_evap = UA_evap

        # --- 3. Indoor unit fan ---
        if dV_iu_fan_a_rated is None:
            self.dV_iu_fan_a_rated = hp_capacity * 0.0002
        else:
            self.dV_iu_fan_a_rated = dV_iu_fan_a_rated

        self.dP_iu_fan_rated: float = dP_iu_fan_rated
        self.eta_iu_fan_rated: float = eta_iu_fan_rated

        if A_cross_iu is None:
            self.A_cross_iu = self.dV_iu_fan_a_rated / 2.0
        else:
            self.A_cross_iu = A_cross_iu

        self.E_iu_fan_rated: float = self.dV_iu_fan_a_rated * self.dP_iu_fan_rated / self.eta_iu_fan_rated
        self.vsd_coeffs_iu: dict = vsd_coeffs_iu
        self.fan_params_iu: dict = {
            "fan_rated_flow_rate": self.dV_iu_fan_a_rated,
            "fan_rated_power": self.E_iu_fan_rated,
        }

        # --- 4. BHE ---
        self.N_1 = N_1
        self.N_2 = N_2
        self.B = B
        self.D_b = D_b
        self.H_b = H_b
        self.r_b = r_b
        self.R_b = R_b
        self.k_s = k_s
        self.c_s = c_s
        self.rho_s = rho_s
        self.alp_s = k_s / (c_s * rho_s)
        self.E_pmp: float = E_pmp
        self.dV_b_f_m3s: float = dV_b_f_lpm * cu.L2m3 / cu.m2s

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
        T_a_room_K = cu.C2K(T_a_room)
        T_bhe_f_out_K = float(getattr(self, "T_bhe_f_out_K", self.Ts_K))

        is_active = Q_r_iu != 0.0
        m_dot_cp_b = self.dV_b_f_m3s * rho_w * c_w

        if Q_r_iu < 0:
            # Heating: BHE = evaporator (absorb from ground), IU = condenser (heat room)
            mode = "heating"
            self.T_a_room = 27
            self.dT_r_ghx = 3  # GHX refrigerant - GHX outlet water [K]
            self.dT_r_iu = 15  # Indoor unit refrigerant - Indoor unit inlet air [K]
            self.T_r_iu = self.T_a_room + self.dT_r_iu  # Indoor unit refrigerant [°C]
            T_source_K = T_bhe_f_out_K + (self.E_pmp / m_dot_cp_b)
            T_evap_sat_K = T_source_K - dT_ref_evap
            T_cond_sat_K = T_a_room_K + dT_ref_cond
            Q_ref_iu = abs(Q_r_iu)
        elif Q_r_iu > 0:
            # Cooling: IU = evaporator (cool room), BHE = condenser (reject to ground)
            mode = "cooling"
            self.T_a_room = 21  # Room air temperature [°C]
            self.dT_r_ghx = -3  # GHX refrigerant - GHX outlet water [K]
            self.dT_r_iu = 15  # Indoor unit refrigerant - Indoor unit inlet air [K]
            T_source_K = T_bhe_f_out_K + (self.E_pmp / m_dot_cp_b)
            T_evap_sat_K = T_a_room_K - dT_ref_evap
            T_cond_sat_K = T_source_K + dT_ref_cond
            Q_ref_iu = Q_r_iu
            self.T_r_iu = self.T_a_room + self.dT_r_iu  # Indoor unit refrigerant [°C]
        else:
            mode = "off"
            T_evap_sat_K = self.Ts_K
            T_cond_sat_K = self.Ts_K
            Q_ref_iu = 0.0

        if is_active and (T_cond_sat_K - T_evap_sat_K) < self.dT_cycle_min:
            return None

        actual_dT_subcool: float = min(self.dT_subcool, max(0.0, dT_ref_cond - self.dT_hx_min))
        actual_dT_superheat: float = min(self.dT_superheat, max(0.0, dT_ref_evap - self.dT_hx_min))

        # Always mode="heating" for calc_ref_state (avoids key swap)
        cycle_states = calc_ref_state(
            T_evap_K=T_evap_sat_K,
            T_cond_K=T_cond_sat_K,
            refrigerant=self.ref,
            eta_cmp_isen=self.eta_cmp_isen,
            mode=mode,
            dT_superheat=actual_dT_superheat,
            dT_subcool=actual_dT_subcool,
            is_active=is_active,
        )

        h_cmp_out = cycle_states["h_ref_cmp_out [J/kg]"]
        h_cmp_in = cycle_states["h_ref_cmp_in [J/kg]"]
        h_exp_in = cycle_states["h_ref_exp_in [J/kg]"]
        h_exp_out = cycle_states["h_ref_exp_out [J/kg]"]

        if mode == "cooling":
            dh_evap = h_cmp_in - h_exp_out
            m_dot_ref = Q_ref_iu / dh_evap if (is_active and abs(dh_evap) > 1e-3) else 0.0
        elif mode == "heating":
            dh_cond = h_cmp_out - h_exp_in
            m_dot_ref = Q_ref_iu / dh_cond if (is_active and abs(dh_cond) > 1e-3) else 0.0
        else:
            m_dot_ref = 0.0

        Q_ref_cond = m_dot_ref * (h_cmp_out - h_exp_in) if is_active else 0.0
        Q_ref_evap = m_dot_ref * (h_cmp_in - h_exp_out) if is_active else 0.0
        E_cmp = m_dot_ref * (h_cmp_out - h_cmp_in) if is_active else 0.0
        cmp_rps = m_dot_ref / (self.V_cmp_ref * cycle_states["rho_ref_cmp_in [kg/m3]"]) if is_active else 0.0

        if is_active and E_cmp <= 0:
            return None

        # ── BHE energy balance ──
        if mode == "heating":
            Q_bhe = Q_ref_evap - self.E_pmp
            T_bhe_f_in_K = T_source_K - Q_ref_evap / m_dot_cp_b
        elif mode == "cooling":
            Q_bhe = -(Q_ref_cond + self.E_pmp)  # negative = heat into ground
            T_bhe_f_in_K = T_source_K + Q_ref_cond / m_dot_cp_b
        else:
            Q_bhe = 0.0
            T_bhe_f_in_K = self.T_bhe_f_in_K

        Q_bhe_unit = Q_bhe / self.H_b if is_active else 0.0
        T_bhe_f = (cu.K2C(T_bhe_f_in_K) + cu.K2C(T_bhe_f_out_K)) / 2
        T_bhe = T_bhe_f + Q_bhe_unit * self.R_b

        # ── Indoor unit HX ──
        if mode == "cooling":
            iu_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_evap,
                T_a_in_C=T_a_room,
                T_ref_sat_K=T_evap_sat_K,
                A_cross=self.A_cross_iu,
                UA_rated=self.UA_evap,
                dV_fan_rated=self.dV_iu_fan_a_rated,
                is_active=is_active,
            )
        elif mode == "heating":
            iu_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_cond,
                T_a_in_C=T_a_room,
                T_ref_sat_K=T_cond_sat_K,
                A_cross=self.A_cross_iu,
                UA_rated=self.UA_cond,
                dV_fan_rated=self.dV_iu_fan_a_rated,
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

        # BHE NTU check (heating: evaporator constraint)
        if mode == "heating" and is_active:
            NTU_evap = self.UA_evap / m_dot_cp_b
            eps = 1.0 - math.exp(-NTU_evap)
            T_source_K_local = T_bhe_f_out_K + (self.E_pmp / m_dot_cp_b)
            Q_evap_max = eps * m_dot_cp_b * (T_source_K_local - T_evap_sat_K)
            err_Q_evap = Q_ref_evap - Q_evap_max
        elif mode == "cooling" and is_active:
            NTU_cond = self.UA_cond / m_dot_cp_b
            eps = 1.0 - math.exp(-NTU_cond)
            T_source_K_local = T_bhe_f_out_K + (self.E_pmp / m_dot_cp_b)
            Q_cond_max = eps * m_dot_cp_b * (T_cond_sat_K - T_source_K_local)
            err_Q_evap = Q_ref_cond - Q_cond_max
        else:
            err_Q_evap = 0.0

        # Total electrical input
        E_pmp_active = self.E_pmp if is_active else 0.0
        E_tot = E_cmp + E_pmp_active + E_iu_fan

        result = cycle_states.copy()
        result.update(
            {
                "hp_is_on": is_active,
                "mode": mode,
                "converged": bool(iu_hx.get("converged", True)),
                "converged_rps": True,
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
                "dV_bhe_f [m3/s]": self.dV_b_f_m3s if is_active else 0.0,
                "m_dot_ref [kg/s]": m_dot_ref,
                "cmp_rpm [rpm]": cmp_rps * 60,
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
        return result

    # =============================================================
    # 2D Optimisation
    # =============================================================

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
            bounds=[(1.0, 20.0), (1.0, 20.0)],
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
        Q_bhe_unit = hp_result.get("Q_bhe [W]", 0.0) / self.H_b if hp_is_on else 0.0

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
        T_bhe_f_K = T_bhe_K - Q_bhe_unit * self.R_b
        self.T_bhe_f = cu.K2C(T_bhe_f_K)
        self.Q_bhe = Q_bhe_unit * self.H_b
        m_cp_b = c_w * rho_w * self.dV_b_f_m3s

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
            if result is None:
                failure_reason = "cycle_invalid"
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
                    "Consider increasing UA_rated or fan-flow rated.",
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

            if Q_r_iu_n == 0:
                hp_result = self._calc_state(5.0, 5.0, 0.0, T0_n, T_a_room_n)
            else:
                opt = self._optimize_operation(Q_r_iu_n, T0_n, T_a_room_n)
                hp_result = self._calc_state(opt.x[0], opt.x[1], Q_r_iu_n, T0_n, T_a_room_n)

            if hp_result is None or not hp_result.get("converged", False):
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
