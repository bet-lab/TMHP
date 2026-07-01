"""Air source heat pump — physics-based cycle model with indoor unit.

Resolves a vapour-compression refrigerant cycle coupled to
an outdoor-air heat exchanger and an indoor-air heat exchanger.
Supports both **cooling** (``Q_r_iu > 0``) and **heating** (``Q_r_iu < 0``)
modes.  The indoor load ``Q_r_iu`` is imposed externally each timestep.

At each time step the model finds the minimum-power operating point
(compressor + indoor fan + outdoor fan) via bounded 2-D optimisation
over the evaporator and condenser approach temperature differences.

Architecture mirrors ``AirSourceHeatPumpBoiler`` — uses the same
shared utility functions (``calc_ref_state``, ``calc_HX_perf_for_target_heat``,
``calc_fan_power_from_dV_fan``) and the same ``postprocess_exergy()``
pattern, but replaces the tank energy balance with direct air-side
heat exchange at the indoor unit.
"""

import contextlib
import inspect
from collections.abc import Callable

import numpy as np
import pandas as pd
from scipy.optimize import brentq, minimize
from tqdm import tqdm

from . import calc_util as cu
from ._opt_utils import safe_float_attr
from .compressor_envelope import check_pr_envelope
from .constants import c_a, rho_a
from .enex_functions import (
    calc_fan_power_from_dV_fan,
    calc_HX_perf_for_target_heat,
)
from .refrigerant import (
    calc_ref_state,
)


class AirSourceHeatPump:
    """Air source heat pump with indoor-unit air heat exchange.

    The refrigerant cycle is resolved via CoolProp with
    user-specified superheat / subcool margins.  A bounded
    2-D optimiser minimises total electrical input
    (``E_cmp + E_iu_fan + E_ou_fan``) over the evaporator
    and condenser approach temperatures.
    """

    def __init__(
        self,
        # 1. Refrigerant / cycle / compressor -----------
        ref: str = "R32",
        V_cmp_ref: float | None = None,
        eta_cmp_isen: float | Callable | None = None,
        eta_cmp_vol: float | Callable | None = None,
        eta_cmp: float | Callable | None = None,
        dT_superheat: float = 3.0,
        dT_subcool: float = 3.0,
        # 2. Heat exchanger UA ---------------------------
        UA_ou_rated: float | None = None,
        UA_iu_rated: float | None = None,
        n_ou: float = 0.65,
        n_iu: float = 0.65,
        # 3. Outdoor unit fan ----------------------------
        dV_ou_fan_a_rated: float | None = None,
        dP_ou_fan_rated: float | None = None,
        A_cross_ou: float | None = None,
        eta_ou_fan_rated: float | None = None,
        # 4. Indoor unit fan -----------------------
        dV_iu_fan_a_rated: float | None = None,
        dP_iu_fan_rated: float | None = None,
        A_cross_iu: float | None = None,
        eta_iu_fan_rated: float | None = None,
        # 5. System capacity / room ----------------------
        hp_capacity: float = 4000.0,
        T_a_room: float = 27.0,
        # 6. Cycle guard ---------------------------------
        dT_cycle_min: float = 20.0,
        dT_hx_min: float = 0.5,
        # Compressor pressure-ratio envelope (PR = P_cond / P_evap)
        PR_cycle_min: float = 1.5,
        PR_cycle_max: float = 10.0,
        # Compressor speed search bounds [rev/s]
        rps_min: float = 10.0,
        rps_max: float = 150.0,
        # ASHRAE 90.1-2022 VSD coefficients
        vsd_coeffs_ou: dict | None = None,
        vsd_coeffs_iu: dict | None = None,
        # Deprecated:
        V_disp_cmp: float | None = None,
        eta_cmp_mech: float | Callable | None = None,
        UA_cond_rated: float | None = None,
        UA_evap_rated: float | None = None,
        n_cond: float | None = None,
        n_evap: float | None = None,
        UA_cond_design: float | None = None,
        UA_evap_design: float | None = None,
        dV_ou_fan_a_design: float | None = None,
        dP_ou_fan_design: float | None = None,
        eta_ou_fan_design: float | None = None,
        dV_iu_fan_a_design: float | None = None,
        dP_iu_fan_design: float | None = None,
        eta_iu_fan_design: float | None = None,
    ):
        import warnings

        # Resolve deprecated mapping
        if V_cmp_ref is None:
            V_cmp_ref = V_disp_cmp if V_disp_cmp is not None else 0.0001
        if eta_cmp is None:
            eta_cmp = eta_cmp_mech if eta_cmp_mech is not None else 0.855
        # UA_cond/evap_design → UA_cond/evap_rated (oldest names, two hops)
        if UA_cond_rated is None:
            UA_cond_rated = UA_cond_design
        if UA_evap_rated is None:
            UA_evap_rated = UA_evap_design
        # UA_cond/evap_rated → UA_ou/iu_rated (physical-unit rename, issue #183)
        if UA_cond_rated is not None or UA_evap_rated is not None:
            warnings.warn(
                "UA_cond_rated/UA_evap_rated are deprecated and will be removed in a future"
                " release. Use UA_ou_rated/UA_iu_rated (physical unit identity).",
                DeprecationWarning,
                stacklevel=2,
            )
            if UA_ou_rated is None:
                UA_ou_rated = UA_cond_rated
            if UA_iu_rated is None:
                UA_iu_rated = UA_evap_rated
        if n_cond is not None or n_evap is not None:
            warnings.warn(
                "n_cond/n_evap are deprecated and will be removed in a future release."
                " Use n_ou/n_iu.",
                DeprecationWarning,
                stacklevel=2,
            )
            if n_cond is not None:
                n_ou = n_cond
            if n_evap is not None:
                n_iu = n_evap
        if dV_ou_fan_a_rated is None:
            dV_ou_fan_a_rated = dV_ou_fan_a_design
        if dP_ou_fan_rated is None:
            dP_ou_fan_rated = dP_ou_fan_design if dP_ou_fan_design is not None else 60.0
        if eta_ou_fan_rated is None:
            eta_ou_fan_rated = eta_ou_fan_design if eta_ou_fan_design is not None else 0.6
        if dV_iu_fan_a_rated is None:
            dV_iu_fan_a_rated = dV_iu_fan_a_design
        if dP_iu_fan_rated is None:
            dP_iu_fan_rated = dP_iu_fan_design if dP_iu_fan_design is not None else 60.0
        if eta_iu_fan_rated is None:
            eta_iu_fan_rated = eta_iu_fan_design if eta_iu_fan_design is not None else 0.6

        if vsd_coeffs_ou is None:
            vsd_coeffs_ou = {
                "c1": 0.0013,
                "c2": 0.1470,
                "c3": 0.9506,
                "c4": -0.0998,
                "c5": 0.0,
            }
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
        self.eta_cmp_isen: float | Callable | None = eta_cmp_isen
        self.eta_cmp_vol: float | Callable | None = eta_cmp_vol
        self.eta_cmp: float | Callable = eta_cmp
        self.dT_superheat: float = dT_superheat
        self.dT_subcool: float = dT_subcool
        self.dT_cycle_min: float = dT_cycle_min
        self.dT_hx_min: float = dT_hx_min
        # Compressor pressure-ratio envelope (floor -> clamp, ceiling -> reject)
        self.PR_cycle_min: float = PR_cycle_min
        self.PR_cycle_max: float = PR_cycle_max
        # Compressor speed search bounds [rev/s]
        self.rps_min: float = rps_min
        self.rps_max: float = rps_max
        # Records the PR-envelope event of the most recent _calc_state call
        # (None | ("pr_below_min", pr, bound) | ("pr_above_max", pr, bound)).
        self._last_pr_event: tuple[str, float, float] | None = None
        self.hp_capacity: float = hp_capacity

        # --- 2. Heat exchanger UA ---
        if UA_ou_rated is None:
            self.UA_ou_rated = hp_capacity / 10.0
        else:
            self.UA_ou_rated = UA_ou_rated

        if UA_iu_rated is None:
            self.UA_iu_rated = self.UA_ou_rated * 0.8
        else:
            self.UA_iu_rated = UA_iu_rated

        self.n_ou: float = n_ou
        self.n_iu: float = n_iu

        # --- 3. Outdoor unit fan ---
        if dV_ou_fan_a_rated is None:
            self.dV_ou_fan_a_rated = hp_capacity * 0.0002
        else:
            self.dV_ou_fan_a_rated = dV_ou_fan_a_rated

        self.dP_ou_fan_rated: float = dP_ou_fan_rated
        self.eta_ou_fan_rated: float = eta_ou_fan_rated

        if A_cross_ou is None:
            self.A_cross_ou = self.dV_ou_fan_a_rated / 2.0
        else:
            self.A_cross_ou = A_cross_ou

        self.E_ou_fan_rated: float = (
            self.dV_ou_fan_a_rated * self.dP_ou_fan_rated / self.eta_ou_fan_rated
        )
        self.vsd_coeffs_ou: dict = vsd_coeffs_ou
        self.fan_params_ou: dict = {
            "fan_rated_flow_rate": self.dV_ou_fan_a_rated,
            "fan_rated_power": self.E_ou_fan_rated,
        }

        # --- 4. Indoor unit fan ---
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

        self.E_iu_fan_rated: float = (
            self.dV_iu_fan_a_rated * self.dP_iu_fan_rated / self.eta_iu_fan_rated
        )
        self.vsd_coeffs_iu: dict = vsd_coeffs_iu
        self.fan_params_iu: dict = {
            "fan_rated_flow_rate": self.dV_iu_fan_a_rated,
            "fan_rated_power": self.E_iu_fan_rated,
        }

        # --- 5. Room temperature ---
        self.T_a_room: float = T_a_room


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
        dT_ref_evap : float
            Evaporator approach ΔT [K].
        dT_ref_cond : float
            Condenser approach ΔT [K].
        Q_r_iu : float
            Indoor thermal load [W].
            Positive = cooling (indoor unit is evaporator).
            Negative = heating (indoor unit is condenser).
        T0 : float
            Dead-state / outdoor-air temperature [°C].
        T_a_room : float
            Room air temperature [°C].

        Returns
        -------
        dict | None
            Cycle performance dictionary; ``None`` if infeasible.
        """
        T0_K: float = cu.C2K(T0)
        T_a_room_K: float = cu.C2K(T_a_room)
        is_active: bool = Q_r_iu != 0.0

        if not is_active:
            cs: dict = calc_ref_state(
                T_evap_K=T0_K,
                T_cond_K=T0_K,
                refrigerant=self.ref,
                eta_cmp_isen=self.eta_cmp_isen if self.eta_cmp_isen is not None else 1.0,
                mode="off",
                dT_superheat=self.dT_superheat,
                dT_subcool=self.dT_subcool,
                is_active=False,
            )
            result = cs.copy()
            result.update(
                {
                    "hp_is_on": False,
                    "converged": True,
                    "converged_rps": True,
                    "ou_fan_flow_min_limit": False,
                    "ou_fan_flow_max_limit": False,
                    "iu_fan_flow_min_limit": False,
                    "iu_fan_flow_max_limit": False,
                    # Temperatures [°C]
                    "T_ou_a_in [°C]": T0,
                    "T_ou_a_mid [°C]": T0,
                    "T_ou_a_out [°C]": T0,
                    "T_iu_a_in [°C]": T_a_room,
                    "T_iu_a_mid [°C]": T_a_room,
                    "T_iu_a_out [°C]": T_a_room,
                    "T_a_room [°C]": T_a_room,
                    "T0 [°C]": T0,
                    # Volume flow rates [m3/s]
                    "dV_ou_a [m3/s]": 0.0,
                    "v_ou_a [m/s]": 0.0,
                    "dV_iu_a [m3/s]": 0.0,
                    "v_iu_a [m/s]": 0.0,
                    "m_dot_ref [kg/s]": 0.0,
                    "cmp_rpm [rpm]": 0.0,
                    # Energy rates [W]
                    "E_iu_fan [W]": 0.0,
                    "E_ou_fan [W]": 0.0,
                    # Heat duties by physical location (mode-independent labels)
                    "Q_ref_iu [W]": 0.0,
                    "Q_ref_ou [W]": 0.0,
                    "E_cmp [W]": 0.0,
                    "E_tot [W]": 0.0,
                    # COP metrics
                    "cop_ref [-]": np.nan,
                    "cop_sys [-]": np.nan,
                }
            )
            return result

        if Q_r_iu > 0:
            # Cooling mode: indoor = evaporator, outdoor = condenser
            mode = "cooling"
            T_evap_sat_K = T_a_room_K - dT_ref_evap     # evap below room
            T_cond_sat_K = T0_K + dT_ref_cond            # cond above outdoor
        else:
            # Heating mode: indoor = condenser, outdoor = evaporator
            mode = "heating"
            T_evap_sat_K = T0_K - dT_ref_evap            # evap below outdoor
            T_cond_sat_K = T_a_room_K + dT_ref_cond      # cond above room

        # Guard: evap must be below cond with required minimal lift
        if (T_cond_sat_K - T_evap_sat_K) <= self.dT_cycle_min:
            return None

        actual_dT_subcool: float = min(self.dT_subcool, max(0.0, dT_ref_cond - self.dT_hx_min))
        actual_dT_superheat: float = min(self.dT_superheat, max(0.0, dT_ref_evap - self.dT_hx_min))

        def _eval_eff(eff, r_p, rps) -> float:
            if eff is None:
                return 1.0
            if callable(eff):
                sig = inspect.signature(eff)
                if len(sig.parameters) == 2:
                    return float(eff(r_p, rps))
                return float(eff(r_p))
            return float(eff)

        # Same name (`cs`) is annotated up in the inactive branch (~L206);
        # re-annotating here triggers mypy [no-redef] even though the
        # inactive branch returns unconditionally. Use plain assignment.
        cs = calc_ref_state(
            T_evap_K=T_evap_sat_K,
            T_cond_K=T_cond_sat_K,
            refrigerant=self.ref,
            eta_cmp_isen=1.0,  # Temporary
            mode=mode,
            dT_superheat=actual_dT_superheat,
            dT_subcool=actual_dT_subcool,
            is_active=True,
        )

        h_cmp_in: float = cs["h_ref_cmp_in [J/kg]"]
        h_exp_in: float = cs["h_ref_exp_in [J/kg]"]
        h_exp_out: float = cs["h_ref_exp_out [J/kg]"]
        rho_in: float = cs["rho_ref_cmp_in [kg/m3]"]
        P_evap = cs["P_ref_cmp_in [Pa]"]
        P_cond = cs["P_ref_cmp_out [Pa]"]

        ratio_P_cmp = P_cond / P_evap if P_evap > 0 else 1.0

        # Compressor pressure-ratio envelope guard. PR is the physically primary
        # limit (a fixed temperature lift maps to PR non-linearly per refrigerant
        # and operating level). Ceiling -> reject (outside single-stage envelope);
        # floor -> clamp the cycle onto PR_cycle_min (continuous low-lift
        # transition) by holding P_evap and projecting P_cond. Both events are
        # recorded on self._last_pr_event for the analyze_steady hint; no print
        # here (this runs inside the optimiser loop).
        self._last_pr_event = None
        pr_event = check_pr_envelope(ratio_P_cmp, self.PR_cycle_min, self.PR_cycle_max)
        if pr_event == "pr_above_max":
            self._last_pr_event = ("pr_above_max", ratio_P_cmp, self.PR_cycle_max)
            return None
        if pr_event == "pr_below_min":
            self._last_pr_event = ("pr_below_min", ratio_P_cmp, self.PR_cycle_min)
            # Clamp: hold P_evap, project P_cond = PR_cycle_min * P_evap, invert
            # the saturation curve for the constrained condensing temperature,
            # then refresh the cycle state at the clamped condition.
            import CoolProp.CoolProp as CP
            P_cond = self.PR_cycle_min * P_evap
            T_cond_sat_K = CP.PropsSI("T", "P", P_cond, "Q", 0, self.ref)
            cs = calc_ref_state(
                T_evap_K=T_evap_sat_K,
                T_cond_K=T_cond_sat_K,
                refrigerant=self.ref,
                eta_cmp_isen=1.0,  # Temporary
                mode=mode,
                dT_superheat=actual_dT_superheat,
                dT_subcool=actual_dT_subcool,
                is_active=True,
            )
            h_cmp_in = cs["h_ref_cmp_in [J/kg]"]
            h_exp_in = cs["h_ref_exp_in [J/kg]"]
            h_exp_out = cs["h_ref_exp_out [J/kg]"]
            rho_in = cs["rho_ref_cmp_in [kg/m3]"]
            P_evap = cs["P_ref_cmp_in [Pa]"]
            P_cond = cs["P_ref_cmp_out [Pa]"]
            ratio_P_cmp = P_cond / P_evap if P_evap > 0 else self.PR_cycle_min

        try:
            import CoolProp.CoolProp as CP
            s_cmp_in = cs["s_ref_cmp_in [J/(kg·K)]"]
            h_ref_cmp_out_isen = CP.PropsSI("H", "P", P_cond, "S", s_cmp_in, self.ref)
        except ValueError:
            h_ref_cmp_out_isen = h_cmp_in

        def _residual_rps(rps):
            val_eta_vol = _eval_eff(self.eta_cmp_vol, ratio_P_cmp, rps)
            val_eta_isen = _eval_eff(self.eta_cmp_isen, ratio_P_cmp, rps)
            h_cmp_out_local = h_cmp_in + (h_ref_cmp_out_isen - h_cmp_in) / val_eta_isen

            dh_cond_local = h_cmp_out_local - h_exp_in
            dh_evap_local = h_cmp_in - h_exp_out

            m_dot = self.V_cmp_ref * rho_in * val_eta_vol * rps
            if mode == "cooling":
                return (m_dot * dh_evap_local) - abs(Q_r_iu)
            else:
                return (m_dot * dh_cond_local) - abs(Q_r_iu)

        try:
            cmp_rps = brentq(_residual_rps, self.rps_min, self.rps_max)
            converged_rps = True
        except ValueError:
            res_min = _residual_rps(self.rps_min)
            res_max = _residual_rps(self.rps_max)
            cmp_rps = self.rps_min if abs(res_min) < abs(res_max) else self.rps_max
            converged_rps = False

        val_eta_vol = _eval_eff(self.eta_cmp_vol, ratio_P_cmp, cmp_rps)
        val_eta_isen = _eval_eff(self.eta_cmp_isen, ratio_P_cmp, cmp_rps)
        val_eta_electro_mech = _eval_eff(self.eta_cmp, ratio_P_cmp, cmp_rps)

        cs = calc_ref_state(
            T_evap_K=T_evap_sat_K,
            T_cond_K=T_cond_sat_K,
            refrigerant=self.ref,
            eta_cmp_isen=val_eta_isen,
            mode=mode,
            dT_superheat=actual_dT_superheat,
            dT_subcool=actual_dT_subcool,
            is_active=True,
        )

        h_cmp_out_final = cs["h_ref_cmp_out [J/kg]"]
        m_dot_ref = self.V_cmp_ref * rho_in * val_eta_vol * cmp_rps
        Q_ref_cond = m_dot_ref * (h_cmp_out_final - h_exp_in)
        Q_ref_evap = m_dot_ref * (h_cmp_in - h_exp_out)
        # Map refrigerant-role duties to physical-location duties for output.
        # Heating: IU = condenser, OU = evaporator; cooling: roles swap.
        Q_ref_iu = Q_ref_cond if mode == "heating" else Q_ref_evap
        Q_ref_ou = Q_ref_evap if mode == "heating" else Q_ref_cond
        E_cmp = (m_dot_ref * (h_cmp_out_final - h_cmp_in)) / val_eta_electro_mech

        # Reject negative compressor power (unphysical)
        if E_cmp <= 0:
            return None

        # ── Outdoor unit HX ──
        # The outdoor coil is always parameterised by UA_ou_rated regardless of mode.
        # In cooling it acts as condenser; in heating as evaporator — but the physical
        # geometry (and therefore UA) is unchanged.
        if mode == "cooling":
            # Outdoor = condenser → ref rejects heat → air is heated
            ou_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_cond,
                T_a_in_C=T0,
                T_ref_sat_K=T_cond_sat_K,
                A_cross=self.A_cross_ou,
                UA_rated=self.UA_ou_rated,
                dV_fan_rated=self.dV_ou_fan_a_rated,
                is_active=True,
                exponent=self.n_ou,
            )
        else:
            # Outdoor = evaporator → ref absorbs heat → air is cooled
            ou_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_evap,
                T_a_in_C=T0,
                T_ref_sat_K=T_evap_sat_K,
                A_cross=self.A_cross_ou,
                UA_rated=self.UA_ou_rated,
                dV_fan_rated=self.dV_ou_fan_a_rated,
                is_active=True,
                exponent=self.n_ou,
            )

        dV_ou_a: float = ou_hx["dV_fan"]
        T_ou_a_mid: float = ou_hx["T_a_mid_C"]
        E_ou_fan: float = calc_fan_power_from_dV_fan(
            dV_fan=dV_ou_a,
            fan_params=self.fan_params_ou,
            vsd_coeffs=self.vsd_coeffs_ou,
            is_active=True,
        )
        T_ou_a_out: float = (
            T_ou_a_mid + E_ou_fan / (c_a * rho_a * dV_ou_a)
            if dV_ou_a > 0 else T0
        )
        v_ou_a: float = dV_ou_a / self.A_cross_ou

        # ── Indoor unit HX ──
        # The indoor coil is always parameterised by UA_iu_rated regardless of mode.
        if mode == "cooling":
            # Indoor = evaporator → ref absorbs heat → air is cooled
            iu_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_evap,
                T_a_in_C=T_a_room,
                T_ref_sat_K=T_evap_sat_K,
                A_cross=self.A_cross_iu,
                UA_rated=self.UA_iu_rated,
                dV_fan_rated=self.dV_iu_fan_a_rated,
                is_active=True,
                exponent=self.n_iu,
            )
        else:
            # Indoor = condenser → ref rejects heat → air is heated
            iu_hx = calc_HX_perf_for_target_heat(
                Q_ref_target=Q_ref_cond,
                T_a_in_C=T_a_room,
                T_ref_sat_K=T_cond_sat_K,
                A_cross=self.A_cross_iu,
                UA_rated=self.UA_iu_rated,
                dV_fan_rated=self.dV_iu_fan_a_rated,
                is_active=True,
                exponent=self.n_iu,
            )

        dV_iu_a: float = iu_hx["dV_fan"]
        T_iu_a_mid: float = iu_hx["T_a_mid_C"]
        E_iu_fan: float = calc_fan_power_from_dV_fan(
            dV_fan=dV_iu_a,
            fan_params=self.fan_params_iu,
            vsd_coeffs=self.vsd_coeffs_iu,
            is_active=True,
        )
        T_iu_a_out: float = (
            T_iu_a_mid + E_iu_fan / (c_a * rho_a * dV_iu_a)
            if dV_iu_a > 0 else T_a_room
        )
        v_iu_a: float = dV_iu_a / self.A_cross_iu

        # --- Check convergence for both HXs ---
        if not (ou_hx.get("converged", True) and iu_hx.get("converged", True)):
            return {
                "converged": False,
                "_ou_diag": ou_hx,
                "_iu_diag": iu_hx,
                "converged_rps": bool(converged_rps),
                "ou_fan_flow_min_limit": ou_hx.get("min_limit", False),
                "ou_fan_flow_max_limit": ou_hx.get("max_limit", False),
                "iu_fan_flow_min_limit": iu_hx.get("min_limit", False),
                "iu_fan_flow_max_limit": iu_hx.get("max_limit", False),
            }

        # Check overall convergence
        is_converged = ou_hx.get("converged", True) and iu_hx.get("converged", True) and converged_rps
        E_tot: float = E_cmp + E_ou_fan + E_iu_fan

        # Same name (`result`) is annotated up in the inactive branch (~L216);
        # plain assignment to avoid the mypy [no-redef] false positive.
        result = cs.copy()
        result.update(
            {
                "hp_is_on": True,
                "mode": mode,
                "converged": bool(is_converged),
                "converged_rps": bool(converged_rps),
                "ou_fan_flow_min_limit": ou_hx.get("min_limit", False),
                "ou_fan_flow_max_limit": ou_hx.get("max_limit", False),
                "iu_fan_flow_min_limit": iu_hx.get("min_limit", False),
                "iu_fan_flow_max_limit": iu_hx.get("max_limit", False),
                # Temperatures [°C]
                "T_ou_a_in [°C]": T0,
                "T_ou_a_mid [°C]": T_ou_a_mid,
                "T_ou_a_out [°C]": T_ou_a_out,
                "T_iu_a_in [°C]": T_a_room,
                "T_iu_a_mid [°C]": T_iu_a_mid,
                "T_iu_a_out [°C]": T_iu_a_out,
                "T_a_room [°C]": T_a_room,
                "T0 [°C]": T0,
                # Volume flow rates [m3/s]
                "dV_ou_a [m3/s]": dV_ou_a,
                "v_ou_a [m/s]": v_ou_a,
                "dV_iu_a [m3/s]": dV_iu_a,
                "v_iu_a [m/s]": v_iu_a,
                "m_dot_ref [kg/s]": m_dot_ref,
                "cmp_rpm [rpm]": cmp_rps * 60,
                # Energy rates [W]
                "E_iu_fan [W]": E_iu_fan,
                "E_ou_fan [W]": E_ou_fan,
                # Heat duties by physical location (mode-mapped): in heating the
                # indoor unit is the condenser and the outdoor unit the evaporator;
                # in cooling the roles swap. Reported by location so the labels are
                # mode-independent and the consumer never sees the cond/evap
                # bookkeeping (the refrigerant-perspective cond/evap remain only in
                # the refrigerant-state keys T/P/h/s_ref_*_sat and in refrigerant.py).
                "Q_ref_iu [W]": Q_ref_iu,
                "Q_ref_ou [W]": Q_ref_ou,
                "E_cmp [W]": E_cmp,
                "E_tot [W]": E_tot,
                # COP metrics (indoor-unit duty basis; == |Q_r_iu| at convergence)
                "cop_ref [-]": (
                    Q_ref_iu / E_cmp if E_cmp > 0 else np.nan
                ),
                "cop_sys [-]": (
                    Q_ref_iu / E_tot if E_tot > 0 else np.nan
                ),
            }
        )
        return result

    def _optimize_operation(
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float,
    ):
        """Find min-power operating point (2-D bounded optimisation).

        Parameters
        ----------
        Q_r_iu : float
            Indoor thermal load [W].
        T0 : float
            Dead-state temperature [°C].
        T_a_room : float
            Room air temperature [°C].

        Returns
        -------
        scipy.optimize.OptimizeResult
        """

        def _objective(params) -> float:
            dT_ref_evap, dT_ref_cond = params
            perf: dict | None = self._calc_state(
                dT_ref_evap=dT_ref_evap,
                dT_ref_cond=dT_ref_cond,
                Q_r_iu=Q_r_iu,
                T0=T0,
                T_a_room=T_a_room,
            )
            if perf is None or not perf.get("converged", False):
                return 1e6

            E_tot: float = float(perf.get("E_tot [W]", 1e6))
            if E_tot <= 0 or np.isnan(E_tot):
                return 1e6

            return E_tot

        # Phase 1: coarse grid pre-scan to find a converging starting point.
        # A single fixed x0=(15,15) fails silently when the entire search space
        # is a penalty region — Nelder-Mead cannot escape because all objective
        # evaluations return the same 1e6 sentinel. Scanning a coarse grid first
        # finds a valid basin (if one exists) so Phase 2 refines from there.
        _candidates = [
            (3.0, 3.0), (5.0, 5.0), (8.0, 8.0), (12.0, 12.0), (15.0, 15.0),
            (3.0, 8.0), (8.0, 3.0), (5.0, 12.0), (12.0, 5.0), (10.0, 10.0),
        ]
        best_x0 = [15.0, 15.0]
        best_val = 1e6
        for cand in _candidates:
            val = _objective(cand)
            if val < best_val:
                best_val = val
                best_x0 = list(cand)

        # Phase 2: Nelder-Mead refinement from the best candidate found above.
        return minimize(
            _objective,
            x0=best_x0,
            bounds=[(1.0, 20.0), (1.0, 20.0)],
            method="Nelder-Mead",
            options={"maxiter": 200, "xatol": 1e-3, "fatol": 1e-1},
        )

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
        postprocess: bool = True,
        verbose: bool = True,
    ) -> dict | pd.DataFrame:
        """Run a steady-state performance snapshot.

        Parameters
        ----------
        Q_r_iu : float
            Indoor thermal load [W]. >0 cooling, <0 heating, 0 off.
        T0 : float
            Dead-state / outdoor-air temperature [°C].
        T_a_room : float | None
            Room air temperature [°C]. Uses constructor default if None.
        return_dict : bool
            If True return dict; else single-row DataFrame.
        postprocess : bool
            If True, apply postprocess_exergy to the output.
        verbose : bool
            If True, print warnings upon convergence failure.

        Returns
        -------
        dict | pd.DataFrame
            Cycle state plus diagnostic flags.

            Two keys are useful for branching:

            - ``"converged"`` (bool) — True only when the inner HX optimisation
              and the SciPy optimiser both succeeded.
            - ``"failure_reason"`` (str) — one of ``"none"``, ``"cycle_invalid"``
              (the refrigerant cycle itself was infeasible),
              ``"hx_not_converged"`` (cycle OK but the HX residual exceeded
              tolerance), or ``"optimizer_failed"`` (SciPy reported
              ``success=False``).

            ASHP triggers an off-mode fallback for any of the non-``"none"``
            reasons — ``E_cmp [W]`` will be 0 and the COP keys will be NaN
            in that case. Treat ``failure_reason != "none"`` as "do not
            trust the numbers".
        """
        import warnings

        if T_a_room is None:
            T_a_room = self.T_a_room

        if Q_r_iu == 0:
            result: dict | None = self._calc_state(
                dT_ref_evap=5.0,
                dT_ref_cond=5.0,
                Q_r_iu=0.0,
                T0=T0,
                T_a_room=T_a_room,
            )
            if result is not None:
                result["failure_reason"] = "none"
        else:
            opt_result = self._optimize_operation(
                Q_r_iu=Q_r_iu,
                T0=T0,
                T_a_room=T_a_room,
            )
            result = None
            with contextlib.suppress(Exception):
                result = self._calc_state(
                    dT_ref_evap=opt_result.x[0],
                    dT_ref_cond=opt_result.x[1],
                    Q_r_iu=Q_r_iu,
                    T0=T0,
                    T_a_room=T_a_room,
                )

            # Pressure-ratio envelope hint for the final operating point
            # (one message per call; the per-probe events inside the optimiser
            # loop are silent). Floor -> clamp (cycle still solved); ceiling ->
            # reject (falls back to HP-off below).
            pr_event = self._last_pr_event
            if verbose and pr_event is not None:
                kind, pr_val, bound = pr_event
                if kind == "pr_below_min":
                    print(
                        f"[PR guard] clamp 하한(below PR_cycle_min): "
                        f"PR={pr_val:.3f} -> {bound:.2f} "
                        f"(Q_r_iu={Q_r_iu:.0f}W, T0={T0:.1f}°C, T_a_room={T_a_room:.1f}°C)"
                    )
                else:  # pr_above_max
                    print(
                        f"[PR guard] reject 상한(above PR_cycle_max): "
                        f"PR={pr_val:.3f} > {bound:.2f} "
                        f"(Q_r_iu={Q_r_iu:.0f}W, T0={T0:.1f}°C, T_a_room={T_a_room:.1f}°C)"
                    )

            # opt_success=True with opt_fun>=1e6 is a false success: the
            # optimiser converged but never escaped the penalty region.
            opt_fun = float(getattr(opt_result, "fun", 1e6))
            opt_success = bool(getattr(opt_result, "success", False)) and opt_fun < 1e6
            if result is None:
                # Distinguish a pressure-ratio ceiling rejection from a generic
                # invalid cycle so downstream consumers see the specific cause.
                failure_reason = (
                    "pr_above_max"
                    if pr_event is not None and pr_event[0] == "pr_above_max"
                    else "cycle_invalid"
                )
            elif not result.get("converged", False):
                failure_reason = "hx_not_converged"
            elif not opt_success:
                failure_reason = "optimizer_failed"
            else:
                failure_reason = "none"

            if failure_reason != "none":
                if verbose:
                    warnings.warn(
                        f"analyze_steady: fell back to HP-off state "
                        f"(reason={failure_reason!r}, "
                        f"Q_r_iu={Q_r_iu:.0f}W, T0={T0:.1f}°C, "
                        f"T_a_room={T_a_room:.1f}°C, "
                        f"opt_success={opt_success}, "
                        f"opt_x=({opt_result.x[0]:.2f}, {opt_result.x[1]:.2f}), "
                        f"opt_fun={safe_float_attr(opt_result, 'fun', float('nan')):.3g}). "
                        "Consider increasing UA_ou_rated/UA_iu_rated or fan-flow rated.",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                result = self._calc_state(
                    dT_ref_evap=5.0,
                    dT_ref_cond=5.0,
                    Q_r_iu=0.0,
                    T0=T0,
                    T_a_room=T_a_room,
                )
                if result is not None:
                    result["converged"] = False
                    result["failure_reason"] = failure_reason
            else:
                # By construction (failure_reason == "none") result is a dict.
                assert result is not None
                result["converged"] = True
                result["failure_reason"] = "none"

        if result is None:
            result = {}

        if postprocess and result:
            df_temp = pd.DataFrame([result])
            df_temp = self.postprocess_exergy(df_temp)
            result = df_temp.iloc[0].to_dict()

        if return_dict:
            return result
        return pd.DataFrame([result]) if result else pd.DataFrame()

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
        """Run a time-stepping dynamic simulation.

        Parameters
        ----------
        simulation_period_sec : int
            Total simulation duration [s].
        dt_s : int
            Time step size [s].
        Q_r_iu_schedule : array-like
            Indoor thermal load per step [W].
        T0_schedule : array-like
            Outdoor temperature per step [°C].
        T_a_room_schedule : array-like | None
            Room air temperature per step [°C].
            If None, uses constructor default.
        result_save_csv_path : str | None
            Optional CSV output path.

        Returns
        -------
        pd.DataFrame
            Per-timestep result DataFrame.
        """
        time: np.ndarray = np.arange(0, simulation_period_sec, dt_s)
        tN: int = len(time)

        T0_schedule = np.array(T0_schedule)
        Q_r_iu_schedule = np.array(Q_r_iu_schedule, dtype=float)

        if len(T0_schedule) != tN:
            raise ValueError(
                f"T0_schedule length ({len(T0_schedule)}) != time length ({tN})"
            )
        if len(Q_r_iu_schedule) != tN:
            raise ValueError(
                f"Q_r_iu_schedule length ({len(Q_r_iu_schedule)}) != time length ({tN})"
            )

        if T_a_room_schedule is not None:
            T_a_room_arr = np.array(T_a_room_schedule, dtype=float)
            if len(T_a_room_arr) != tN:
                raise ValueError(
                    f"T_a_room_schedule length ({len(T_a_room_arr)}) != tN ({tN})"
                )
        else:
            T_a_room_arr = np.full(tN, self.T_a_room)

        self.time = time
        self.dt = dt_s

        results_data: list[dict] = []

        for n in tqdm(range(tN), desc="ASHP Simulating"):
            t_s: float = time[n]
            hr: float = t_s * cu.s2h

            Q_r_iu_n: float = Q_r_iu_schedule[n]
            T0_n: float = T0_schedule[n]
            T_a_room_n: float = T_a_room_arr[n]

            # Use analyze_steady for robust calculation and fallback handling
            hp_result = self.analyze_steady(
                Q_r_iu=Q_r_iu_n,
                T0=T0_n,
                T_a_room=T_a_room_n,
                return_dict=True,
                postprocess=False,  # Exergy postprocessing applied in bulk at the end
                verbose=False,      # Suppress warnings during long dynamic loops
            )

            # Add time columns
            hp_result["time [s]"] = t_s
            hp_result["time [h]"] = hr

            results_data.append(hp_result)

        results_df: pd.DataFrame = pd.DataFrame(results_data)
        results_df = self.postprocess_exergy(results_df)
        if result_save_csv_path:
            results_df.to_csv(result_save_csv_path, index=False)
        return results_df

    # =============================================================
    # Exergy post-processing
    # =============================================================

    def postprocess_exergy(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute ASHP-specific exergy variables.

        Mirrors ``AirSourceHeatPumpBoiler.postprocess_exergy()``
        with adaptations for indoor-unit air exchange.

        Pipeline:

        1. Refrigerant state-point exergy (CoolProp)
        2. Electricity = exergy (compressor, IU fan, OU fan)
        3. Air exergy (outdoor unit + indoor unit)
        4. HX Carnot exergy (condenser, evaporator)
        5. Component-level exergy destruction
        6. Exergetic efficiency metrics
        """
        from .enex_functions import (
            calc_exergy_flow,
            calc_refrigerant_exergy,
            convert_electricity_to_exergy,
        )

        df = df.copy()

        # Guard: if T0 [°C] is missing (very defensive), skip
        if "T0 [°C]" not in df.columns:
            return df

        T0_K = cu.C2K(df["T0 [°C]"])

        # ── 1. Refrigerant exergy ────────────────────────
        if "h_ref_cmp_in [J/kg]" in df.columns:
            df = calc_refrigerant_exergy(df, self.ref, T0_K)
        else:
            return df  # OFF-only DataFrame, skip exergy

        # ── 2. Electricity = exergy ─────────────────────
        df = convert_electricity_to_exergy(df)
        # Add indoor fan exergy (electricity = exergy)
        if "E_iu_fan [W]" in df.columns:
            df["X_iu_fan [W]"] = df["E_iu_fan [W]"]

        # ── 3. Air exergy (outdoor unit) ────────────────
        if "dV_ou_a [m3/s]" in df.columns and "T_ou_a_in [°C]" in df.columns:
            G_a_ou = c_a * rho_a * df["dV_ou_a [m3/s]"].fillna(0)
            Tin_ou = cu.C2K(df["T_ou_a_in [°C]"])
            Tmid_ou = cu.C2K(df["T_ou_a_mid [°C]"])
            Tout_ou = (
                cu.C2K(df["T_ou_a_out [°C]"]) if "T_ou_a_out [°C]" in df.columns else Tin_ou
            )
            df["X_a_ou_in [W]"] = calc_exergy_flow(G_a_ou, Tin_ou, T0_K)
            df["X_a_ou_out [W]"] = calc_exergy_flow(G_a_ou, Tout_ou, T0_K)
            df["X_a_ou_mid [W]"] = calc_exergy_flow(G_a_ou, Tmid_ou, T0_K)

        # ── 3b. Air exergy (indoor unit) ────────────────
        if "dV_iu_a [m3/s]" in df.columns and "T_iu_a_in [°C]" in df.columns:
            G_a_iu = c_a * rho_a * df["dV_iu_a [m3/s]"].fillna(0)
            Tin_iu = cu.C2K(df["T_iu_a_in [°C]"])
            Tmid_iu = cu.C2K(df["T_iu_a_mid [°C]"])
            Tout_iu = (
                cu.C2K(df["T_iu_a_out [°C]"]) if "T_iu_a_out [°C]" in df.columns else Tin_iu
            )
            df["X_a_iu_in [W]"] = calc_exergy_flow(G_a_iu, Tin_iu, T0_K)
            df["X_a_iu_out [W]"] = calc_exergy_flow(G_a_iu, Tout_iu, T0_K)
            df["X_a_iu_mid [W]"] = calc_exergy_flow(G_a_iu, Tmid_iu, T0_K)

        # ── 4. HX Carnot exergy (mode-aware IU/OU) ─────
        # calc_ref_state always uses mode="heating" internally:
        #   cmp_out → condenser inlet (high-pressure superheated)
        #   exp_in  → condenser outlet (high-pressure subcooled)
        #   exp_out → evaporator inlet (low-pressure two-phase)
        #   cmp_in  → evaporator outlet (low-pressure superheated)
        #
        # Mapping to physical units:
        #   Heating: IU = condenser, OU = evaporator
        #   Cooling: IU = evaporator, OU = condenser
        # The Carnot factor for each location uses the saturation temperature of
        # the refrigerant role it plays in the current mode. Output exergy is
        # labelled by location (X_ref_iu/X_ref_ou); the refrigerant-state
        # saturation keys remain cond/evap (refrigerant-intrinsic).
        if {"T_ref_cond_sat_v [°C]", "T_ref_evap_sat [°C]", "mode"} <= set(df.columns):
            is_heating = df["mode"] == "heating"
            T_iu_sat_K = cu.C2K(
                df["T_ref_cond_sat_v [°C]"].where(is_heating, df["T_ref_evap_sat [°C]"])
            )
            T_ou_sat_K = cu.C2K(
                df["T_ref_evap_sat [°C]"].where(is_heating, df["T_ref_cond_sat_v [°C]"])
            )
            df["X_ref_iu [W]"] = df["Q_ref_iu [W]"] * (1 - T0_K / T_iu_sat_K)
            df["X_ref_ou [W]"] = df["Q_ref_ou [W]"] * (1 - T0_K / T_ou_sat_K)

        # ── 5. Total exergy input ───────────────────────
        X_tot = df["E_cmp [W]"] + df["E_ou_fan [W]"].fillna(0) + df["E_iu_fan [W]"].fillna(0)
        df["X_tot [W]"] = X_tot

        # ── 6. Component exergy destruction (IU/OU naming) ──
        # Air exergy helper Series
        X_a_ou_in = df.get("X_a_ou_in [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_ou_mid = df.get("X_a_ou_mid [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_ou_out = df.get("X_a_ou_out [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_iu_in = df.get("X_a_iu_in [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_iu_mid = df.get("X_a_iu_mid [W]", pd.Series(0.0, index=df.index)).fillna(0)
        X_a_iu_out = df.get("X_a_iu_out [W]", pd.Series(0.0, index=df.index)).fillna(0)

        if "X_cmp [W]" not in df.columns:
            return df

        # Mode masks
        is_heating = df["mode"] == "heating"
        is_cooling = df["mode"] == "cooling"

        # ── 6a. Compressor (X_in, Xc, X_out) ──
        df["X_in_cmp [W]"] = df["X_cmp [W]"] + df["X_ref_cmp_in [W]"]
        df["X_out_cmp [W]"] = df["X_ref_cmp_out [W]"]
        df["Xc_cmp [W]"] = df["X_in_cmp [W]"] - df["X_out_cmp [W]"]

        # ── 6b. Expansion valve (X_in, Xc, X_out) ──
        df["X_in_exp [W]"] = df["X_ref_exp_in [W]"]
        df["X_out_exp [W]"] = df["X_ref_exp_out [W]"]
        df["Xc_exp [W]"] = df["X_in_exp [W]"] - df["X_out_exp [W]"]

        # ── 6c. Indoor Unit HX (mode-aware: X_in, Xc, X_out) ──
        # Heating: IU = condenser → ref enters from cmp_out, exits to exp_in
        # Cooling: IU = evaporator → ref enters from exp_out, exits to cmp_in
        X_in_iu_hx = pd.Series(0.0, index=df.index)
        X_out_iu_hx = pd.Series(0.0, index=df.index)
        X_in_iu_hx[is_heating] = df.loc[is_heating, "X_ref_cmp_out [W]"] + X_a_iu_in[is_heating]
        X_out_iu_hx[is_heating] = df.loc[is_heating, "X_ref_exp_in [W]"] + X_a_iu_mid[is_heating]
        X_in_iu_hx[is_cooling] = df.loc[is_cooling, "X_ref_exp_out [W]"] + X_a_iu_in[is_cooling]
        X_out_iu_hx[is_cooling] = df.loc[is_cooling, "X_ref_cmp_in [W]"] + X_a_iu_mid[is_cooling]
        df["X_in_iu_hx [W]"] = X_in_iu_hx
        df["X_out_iu_hx [W]"] = X_out_iu_hx
        df["Xc_iu_hx [W]"] = X_in_iu_hx - X_out_iu_hx

        # ── 6d. Outdoor Unit HX (mode-aware: X_in, Xc, X_out) ──
        # Heating: OU = evaporator → ref enters from exp_out, exits to cmp_in
        # Cooling: OU = condenser → ref enters from cmp_out, exits to exp_in
        X_in_ou_hx = pd.Series(0.0, index=df.index)
        X_out_ou_hx = pd.Series(0.0, index=df.index)
        X_in_ou_hx[is_heating] = df.loc[is_heating, "X_ref_exp_out [W]"] + X_a_ou_in[is_heating]
        X_out_ou_hx[is_heating] = df.loc[is_heating, "X_ref_cmp_in [W]"] + X_a_ou_mid[is_heating]
        X_in_ou_hx[is_cooling] = df.loc[is_cooling, "X_ref_cmp_out [W]"] + X_a_ou_in[is_cooling]
        X_out_ou_hx[is_cooling] = df.loc[is_cooling, "X_ref_exp_in [W]"] + X_a_ou_mid[is_cooling]
        df["X_in_ou_hx [W]"] = X_in_ou_hx
        df["X_out_ou_hx [W]"] = X_out_ou_hx
        df["Xc_ou_hx [W]"] = X_in_ou_hx - X_out_ou_hx

        # ── 6e. Outdoor fan (X_in, Xc, X_out) ──
        df["X_in_ou_fan [W]"] = df["X_ou_fan [W]"].fillna(0) + X_a_ou_mid
        df["X_out_ou_fan [W]"] = X_a_ou_out
        df["Xc_ou_fan [W]"] = df["X_in_ou_fan [W]"] - df["X_out_ou_fan [W]"]

        # ── 6f. Indoor fan (X_in, Xc, X_out) ──
        df["X_in_iu_fan [W]"] = df["X_iu_fan [W]"].fillna(0) + X_a_iu_mid
        df["X_out_iu_fan [W]"] = X_a_iu_out
        df["Xc_iu_fan [W]"] = df["X_in_iu_fan [W]"] - df["X_out_iu_fan [W]"]

        # ── 7. Exergetic efficiency metrics ─────────────
        # System exergetic efficiency
        df["X_eff_sys [-]"] = (
            (X_a_iu_out - X_a_iu_in) / df["X_tot [W]"].replace(0, np.nan)
        )

        # Compressor exergetic efficiency
        df["X_eff_cmp [-]"] = 1 - df["Xc_cmp [W]"] / df["X_in_cmp [W]"].replace(0, np.nan)

        # Expansion valve exergetic efficiency
        df["X_eff_exp [-]"] = 1 - df["Xc_exp [W]"] / df["X_in_exp [W]"].replace(0, np.nan)

        # Indoor unit HX exergetic efficiency
        df["X_eff_iu_hx [-]"] = 1 - df["Xc_iu_hx [W]"] / df["X_in_iu_hx [W]"].replace(0, np.nan)

        # Outdoor unit HX exergetic efficiency
        df["X_eff_ou_hx [-]"] = 1 - df["Xc_ou_hx [W]"] / df["X_in_ou_hx [W]"].replace(0, np.nan)

        # Outdoor fan exergetic efficiency
        df["X_eff_ou_fan [-]"] = 1 - df["Xc_ou_fan [W]"] / df["X_in_ou_fan [W]"].replace(0, np.nan)

        # Indoor fan exergetic efficiency
        df["X_eff_iu_fan [-]"] = 1 - df["Xc_iu_fan [W]"] / df["X_in_iu_fan [W]"].replace(0, np.nan)

        return df
