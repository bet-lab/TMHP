"""Integrated System Model: Air Source Heat Pump Boiler (ASHPB).

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

   - **Condenser:** Placed inside the tank (hydronic), utilizing a static overall heat
     transfer coefficient (UA_tank_hx).
   - **Evaporator:** Air-coupled outdoor unit, utilizing a dynamic overall heat transfer
     coefficient (UA_ou) that scales non-linearly with fan airflow (Colburn j-factor analogy).
5. **Thermal Storage Tank:**
   Modeled with lumped-capacitance and DHW mixing logic.

At each time step, the model finds the minimum-power operating point
(compressor + fan) via bounded 1-D optimisation (Brent's method) over
the evaporator approach temperature difference.

.. note::
   See the project paper (KJACR 2025) for the underlying refrigerant-cycle
   theory and component-level modelling assumptions of this system.

Optional sub-components (injected via constructor):
- ``SolarThermalCollector`` — tank-circuit or mains-preheat placement
- (future) ``PVPanel`` — photovoltaic integration

Tank-level management and UV disinfection are built-in features
configured through constructor parameters.
"""

import contextlib
import inspect
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import brentq, minimize_scalar
from tqdm import tqdm

from . import calc_util as cu
from ._opt_utils import safe_float_attr
from .compressor_envelope import check_pr_envelope
from .constants import c_a, c_w, rho_a, rho_w
from .dynamic_context import (
    ControlState,
    DynamicState,
    StepContext,
    determine_heat_source_on_off,
    determine_tank_refill_flow,
    tank_mass_energy_residual,
)
from .enex_functions import (
    calc_HX_perf_for_target_heat,
    calc_mixing_valve_flows,
    calc_mixing_valve_temp,
)
from .heat_transfer import calc_simple_tank_UA
from .hx_fan import calc_fan_power_from_dV_fan
from .refrigerant import calc_ref_state
from .subsystems import PhotovoltaicSystem, SolarThermalCollector
from .thermodynamics import calc_energy_flow


@dataclass
class AirSourceHeatPumpBoiler:
    """Air source heat pump boiler with outdoor-air evaporator.

    The refrigerant cycle is resolved via CoolProp with
    user-specified superheat / subcool margins.  The condenser
    approach temperature is determined analytically
    (``dT_ref_tank = Q_ref_tank / UA_tank_hx``), and a bounded
    1-D optimiser (Brent's method) minimises total electrical
    input (``E_cmp + E_ou_fan``) over the evaporator approach.
    """

    def __init__(
        self,
        # 1. Refrigerant / cycle / compressor -----------
        ref: str = "R134a",
        V_cmp_ref: float | None = None,
        eta_cmp_isen: float | Callable | None = None,
        eta_cmp_vol: float | Callable | None = None,
        eta_cmp: float | Callable | None = None,
        dT_superheat: float = 5.0,
        dT_subcool: float = 5.0,
        # 2. Heat exchanger -----------------------------
        UA_tank_hx: float | None = None,
        UA_ou_rated: float | None = None,
        n_ou: float = 0.65,
        # 3. Outdoor unit fan ---------------------------
        dV_fan_a_rated: float | None = None,
        dP_fan_rated: float | None = None,
        A_cross: float | None = None,
        eta_fan_rated: float | None = None,
        # 4. Tank / control / load ----------------------
        T_tank_w_upper_bound: float = 65.0,
        T_tank_w_lower_bound: float = 60.0,
        T_mix_w_out: float = 40.0,
        T_sup_w: float = 15.0,
        hp_capacity: float = 15000.0,
        dV_mix_w_out_max: float = 0.0045,
        # Tank insulation
        r0: float = 0.2,
        H: float = 1.2,
        x_shell: float = 0.005,
        x_ins: float = 0.05,
        k_shell: float = 25,
        k_ins: float = 0.03,
        h_o: float = 15,
        # 5. Tank water level management ----------------
        tank_always_full: bool = True,
        tank_level_lower_bound: float = 0.5,
        tank_level_upper_bound: float = 1.0,
        dV_tank_w_in_refill: float = 0.001,
        prevent_simultaneous_flow: bool = False,
        # 7. HP operating schedule ----------------------
        hp_on_schedule: list[tuple[float, float]] | None = None,
        # 8. Subsystems (class-based injection) ---------
        stc: SolarThermalCollector | None = None,
        pv: PhotovoltaicSystem | None = None,
        uv=None,
        # ASHRAE 90.1-2022 VSD coefficients
        vsd_coeffs: dict | None = None,
        # Surrounding temperature parameter
        T_sur: float = 20.0,
        # Cycle guard: minimum condenser-to-evaporator saturation lift [K].
        dT_hx_min: float = 0.5,
        # Compressor pressure-ratio envelope (PR = P_cond / P_evap).
        # High-lift DHW validation points reach PR 16-18 (condensing
        # 55-75 degC from a 0-12 degC source), so the default boiler ceiling is
        # 20 here
        # (unlike the space-conditioning ASHP/GSHP, which cap at 5).
        PR_cycle_min: float = 1.5,
        PR_cycle_max: float = 20.0,
        # Compressor speed search bounds [rev/s]
        rps_min: float = 10.0,
        rps_max: float = 150.0,
        # Deprecated compat arguments:
        V_disp_cmp: float | None = None,
        eta_cmp_electro_mech: float | Callable | None = None,
        UA_tank: float | None = None,  # deprecated alias for UA_tank_hx
        UA_cond_design: float | None = None,
        UA_evap_design: float | None = None,
        dV_ou_fan_a_design: float | None = None,
        dP_ou_fan_design: float | None = None,
        A_cross_ou: float | None = None,
        eta_ou_fan_design: float | None = None,
        vsd_coeffs_ou: dict | None = None,
    ):
        # Resolve deprecated mapping
        if V_cmp_ref is None:
            V_cmp_ref = V_disp_cmp if V_disp_cmp is not None else 0.0002
        if eta_cmp is None:
            eta_cmp = eta_cmp_electro_mech if eta_cmp_electro_mech is not None else 0.855
        if UA_tank_hx is None:
            UA_tank_hx = UA_tank if UA_tank is not None else UA_cond_design
        if UA_ou_rated is None:
            UA_ou_rated = UA_evap_design
        if dV_fan_a_rated is None:
            dV_fan_a_rated = dV_ou_fan_a_design
        if dP_fan_rated is None:
            dP_fan_rated = dP_ou_fan_design if dP_ou_fan_design is not None else 60.0
        if A_cross is None:
            A_cross = A_cross_ou
        if eta_fan_rated is None:
            eta_fan_rated = eta_ou_fan_design if eta_ou_fan_design is not None else 0.6
        if vsd_coeffs is None:
            vsd_coeffs = vsd_coeffs_ou

        if hp_on_schedule is None:
            hp_on_schedule = [(0.0, 24.0)]
        if vsd_coeffs is None:
            vsd_coeffs = {
                "c1": 0.0013,
                "c2": 0.1470,
                "c3": 0.9506,
                "c4": -0.0998,
                "c5": 0.0,
            }

        # --- 1. Refrigerant / cycle / compressor ---
        self.ref: str = ref
        self.V_cmp_ref: float = V_cmp_ref

        # Isentropic Efficiency
        if eta_cmp_isen is not None:
            self.eta_cmp_isen: float | Callable = eta_cmp_isen
        else:
            self.eta_cmp_isen = 0.80

        # Volumetric Efficiency
        if eta_cmp_vol is not None:
            self.eta_cmp_vol: float | Callable = eta_cmp_vol
        else:
            self.eta_cmp_vol = lambda r: 0.95 - 0.05 * r

        self.eta_cmp: float | Callable = eta_cmp

        self.dT_superheat: float = dT_superheat
        self.dT_subcool: float = dT_subcool
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
        # If not explicitly provided, the condenser UA dynamically scales to induce a
        # ~10.0 K approach temperature difference, which corresponds to the standard
        # performance specifications for industrial heat pumps.
        # Ref: Application of Industrial Heat Pumps. Annex 35 Final Report (IEA Heat Pump Centre, 2014)
        if UA_tank_hx is None:
            self.UA_tank_hx = hp_capacity / 6.0
        else:
            self.UA_tank_hx = UA_tank_hx

        # The default evaporator UA is determined to ensure an approximate air-side
        # temperature drop of 7.0 K across the outdoor unit, aligning with empirical
        # laboratory observations of standard residential units.
        # Ref: Residential Air Source Heat Pump Water Heater Performance Testing (ORNL, Baxter 2011, DOI: 10.3390/su17052234)
        if UA_ou_rated is None:
            self.UA_ou_rated = self.UA_tank_hx * 0.8
        else:
            self.UA_ou_rated = UA_ou_rated

        self.n_ou: float = n_ou

        # --- 3. Outdoor unit fan ---
        # Default fan flow rate is scaled at 0.0002 m^3/s per W (or 720 CMH per kW),
        # representing an optimal ratio of airflow volume to thermal capacity.
        # This provides enough margin so that nominal optimization operates at ~80% fan ratio.
        if dV_fan_a_rated is None:
            self.dV_fan_a_rated = hp_capacity * 0.00015
        else:
            self.dV_fan_a_rated = dV_fan_a_rated

        self.dP_fan_rated: float = dP_fan_rated
        self.eta_fan_rated: float = eta_fan_rated

        # Default coil face area assumes a nominal frontal air velocity of 2.0 m/s.
        # This velocity is selected specifically to maintain pressure drop profiles
        # within optimal ranges for typical plain fin-and-tube configurations.
        # Ref: Heat transfer and friction characteristics of plain fin-and-tube heat exchangers, part II (Wang et al., 2000, DOI: 10.1016/S0017-9310(99)00333-6)
        if A_cross is None:
            self.A_cross = self.dV_fan_a_rated / 2.0  # Capped at 2.0 m/s face velocity
        else:
            self.A_cross = A_cross

        self.E_fan_rated: float = self.dV_fan_a_rated * self.dP_fan_rated / self.eta_fan_rated
        self.vsd_coeffs: dict = vsd_coeffs
        self.fan_params: dict = {
            "fan_rated_flow_rate": self.dV_fan_a_rated,
            "fan_rated_power": self.E_fan_rated,
        }

        # --- 4. Tank geometry and thermal props ---
        self.tank_physical: dict = {
            "r0": r0,
            "H": H,
            "x_shell": x_shell,
            "x_ins": x_ins,
            "k_shell": k_shell,
            "k_ins": k_ins,
            "h_o": h_o,
        }
        self.UA_tank_wall: float = calc_simple_tank_UA(
            **self.tank_physical,
        )
        self.T_sur_K: float = cu.C2K(T_sur)
        self.V_tank_full: float = math.pi * r0**2 * H
        self.C_tank: float = c_w * rho_w * self.V_tank_full

        self.dV_mix_w_out_max: float = dV_mix_w_out_max
        self.T_tank_w_upper_bound: float = T_tank_w_upper_bound
        self.T_tank_w_lower_bound: float = T_tank_w_lower_bound
        self.T_sup_w: float = T_sup_w
        self.T_sup_w_K: float = cu.C2K(T_sup_w)
        self.T_tank_w_in: float = T_sup_w
        self.T_mix_w_out: float = T_mix_w_out
        self.T_tank_w_in_K: float = cu.C2K(T_sup_w)
        self.T_mix_w_out_K: float = cu.C2K(T_mix_w_out)

        # --- 5. Tank water level management ---
        self.tank_always_full: bool = tank_always_full
        self.tank_level_lower_bound: float = tank_level_lower_bound
        self.tank_level_upper_bound: float = tank_level_upper_bound
        self.dV_tank_w_in_refill: float = dV_tank_w_in_refill
        self.prevent_simultaneous_flow: bool = prevent_simultaneous_flow

        # --- 6. HP operating schedule ---
        self.hp_on_schedule: list[tuple[float, float]] = hp_on_schedule

        # --- 7. Subsystems ---
        self.stc: SolarThermalCollector | None = stc
        self.pv: PhotovoltaicSystem | None = pv
        self._subsystems: dict[str, Any] = {}
        if stc is not None:
            self._subsystems["stc"] = stc
        if pv is not None:
            self._subsystems["pv"] = pv
        if uv is not None:
            self._subsystems["uv"] = uv

        # Flow-rate sync variables
        self.dV_tank_w_in: float = 0.0
        self.dV_tank_w_out: float = 0.0
        self.dV_mix_sup_w_in: float = 0.0
        self.dV_mix_w_out: float = 0.0

    # =============================================================
    # Refrigerant cycle physics (ASHP-specific)
    # =============================================================

    def _calc_state(
        self,
        dT_ref_ou: float,
        T_tank_w: float,
        Q_ref_tank: float,
        T0: float,
        *,
        flow_state: dict,
    ) -> dict | None:
        """Evaluate refrigerant cycle at a given operating point.

        Parameters
        ----------
        dT_ref_ou : float
            Evaporator approach ΔT [K].
        T_tank_w : float
            Tank water temperature [°C].
        Q_ref_tank : float
            Target condenser heat rate [W].
        T0 : float
            Dead-state / outdoor-air temperature [°C].
        flow_state : dict
            Explicit mixing-valve / tank-flow context. Must contain:
            ``dV_mix_w_out``, ``dV_tank_w_out``, ``dV_tank_w_in``,
            ``dV_mix_sup_w_in``. Replaces former implicit ``self.dV_*`` reads.

        Returns
        -------
        dict | None
            Cycle performance dictionary; ``None`` if infeasible.
        """
        dT_ref_tank: float = Q_ref_tank / self.UA_tank_hx if Q_ref_tank > 0 else 0.0

        T_tank_w_K: float = cu.C2K(T_tank_w)
        T0_K: float = cu.C2K(T0)

        T_ou_sat_K: float = T0_K - dT_ref_ou
        T_tank_sat_K: float = T_tank_w_K + dT_ref_tank

        is_active: bool = Q_ref_tank > 0.0

        if not is_active:
            # Flow state (explicit parameter, no side-effect reads)
            dV_tank_w_out: float = flow_state["dV_tank_w_out [m3/s]"]
            dV_tank_w_in: float = flow_state["dV_tank_w_in [m3/s]"]
            dV_mix_sup_w_in: float = flow_state["dV_mix_sup_w_in [m3/s]"]
            dV_mix_w_out_val: float = flow_state["dV_mix_w_out [m3/s]"]

            if dV_mix_w_out_val == 0:
                T_mix_w_out_val: float = np.nan
                T_mix_w_out_val_K: float = np.nan
            else:
                mix: dict = calc_mixing_valve_temp(
                    T_tank_w_K,
                    self.T_sup_w_K,
                    self.T_mix_w_out_K,
                )
                T_mix_w_out_val = mix["T_mix_w_out"]
                T_mix_w_out_val_K = mix["T_mix_w_out_K"]

            # Energy balance: Q_tank_w_in + Q_ref_tank = Q_tank_w_out + Q_tank_loss + dU_tank/dt
            Q_tank_w_in: float = calc_energy_flow(G=c_w * rho_w * dV_tank_w_in, T=self.T_tank_w_in_K, T0=T0_K)
            Q_tank_w_out: float = calc_energy_flow(G=c_w * rho_w * dV_tank_w_out, T=T_tank_w_K, T0=T0_K)
            Q_mix_sup_w_in: float = calc_energy_flow(G=c_w * rho_w * dV_mix_sup_w_in, T=self.T_sup_w_K, T0=T0_K)
            Q_mix_w_out: float = calc_energy_flow(G=c_w * rho_w * dV_mix_w_out_val, T=T_mix_w_out_val_K, T0=T0_K)

            cs = calc_ref_state(
                T_evap_K=T_ou_sat_K,
                T_cond_K=T_tank_sat_K,
                refrigerant=self.ref,
                eta_cmp_isen=self.eta_cmp_isen,
                mode="heating",
                dT_superheat=self.dT_superheat,
                dT_subcool=0.0,
                is_active=False,
                rps=0.0,
            )

            result: dict = cs.copy()
            result.update(
                {
                    "hp_is_on": False,
                    "converged": True,
                    "converged_rps": True,
                    "fan_flow_min_limit": False,
                    "fan_flow_max_limit": False,
                    # Temperatures [°C]
                    "T_ou_a_in [°C]": T0,
                    "T_ou_a_mid [°C]": T0,
                    "T_ou_a_out [°C]": T0,
                    "T_tank_w [°C]": T_tank_w,
                    "T_sup_w [°C]": self.T_sup_w,
                    "T_tank_w_in [°C]": self.T_tank_w_in,
                    "T_mix_w_out [°C]": T_mix_w_out_val,
                    "T0 [°C]": T0,
                    # Volume flow rates [m3/s]
                    "dV_ou_a [m3/s]": 0.0,
                    "v_ou_a [m/s]": 0.0,
                    "dV_mix_w_out [m3/s]": (dV_mix_w_out_val if dV_mix_w_out_val > 0 else np.nan),
                    "dV_tank_w_out [m3/s]": (dV_tank_w_out if dV_tank_w_out > 0 else np.nan),
                    "dV_tank_w_in [m3/s]": (dV_tank_w_in if dV_tank_w_in > 0 else np.nan),
                    "dV_mix_sup_w_in [m3/s]": (dV_mix_sup_w_in if dV_mix_sup_w_in > 0 else np.nan),
                    "m_dot_ref [kg/s]": 0.0,
                    "cmp_rpm [rpm]": 0.0,
                    # Energy rates [W]
                    "E_ou_fan [W]": 0.0,
                    "Q_ref_ou [W]": 0.0,
                    "Q_ou_a [W]": 0.0,
                    "E_cmp [W]": 0.0,
                    "Q_ref_tank [W]": 0.0,
                    "Q_tank_w_in [W]": Q_tank_w_in,
                    "Q_tank_w_out [W]": Q_tank_w_out,
                    "Q_mix_sup_w_in [W]": Q_mix_sup_w_in,
                    "Q_mix_w_out [W]": Q_mix_w_out,
                    "E_tot [W]": 0.0,
                    # COP metrics
                    "cop_ref [-]": np.nan,
                    "cop_sys [-]": np.nan,
                }
            )
            return result

        # --- Active state calculations ---
        # Low-lift feasibility is enforced downstream by the compressor
        # pressure-ratio floor (PR_cycle_min); a separate fixed minimum lift is
        # redundant and non-transferable across refrigerants/operating levels.
        actual_dT_subcool: float = min(self.dT_subcool, max(0.0, dT_ref_tank - self.dT_hx_min))
        actual_dT_superheat: float = min(self.dT_superheat, max(0.0, dT_ref_ou - self.dT_hx_min))

        def _eval_eff(eff, r_p, rps):
            if eff is None:
                return 1.0
            if callable(eff):
                sig = inspect.signature(eff)
                if len(sig.parameters) == 2:
                    return eff(r_p, rps)
                return eff(r_p)
            return float(eff)

        # Same name (`cs`) is annotated up in the inactive branch (~L339);
        # re-annotating here triggers mypy [no-redef] even though the
        # inactive branch returns unconditionally. Use plain assignment.
        cs = calc_ref_state(
            T_evap_K=T_ou_sat_K,
            T_cond_K=T_tank_sat_K,
            refrigerant=self.ref,
            eta_cmp_isen=1.0,  # Temporary dummy value to get basic states
            mode="heating",
            dT_superheat=actual_dT_superheat,
            dT_subcool=actual_dT_subcool,
            is_active=True,
            rps=None,
        )

        ratio_P_cmp = cs["P_ref_cmp_out [Pa]"] / cs["P_ref_cmp_in [Pa]"] if cs["P_ref_cmp_in [Pa]"] > 0 else 1.0

        # Compressor pressure-ratio envelope guard (see compressor_envelope.py).
        # Ceiling -> reject; floor -> clamp the cycle onto PR_cycle_min by holding
        # the evaporator (outdoor) pressure and projecting the condensing
        # (tank-side) pressure, then refresh the state. Recorded for the
        # analyze_steady hint; no print here (runs inside the optimiser loop).
        self._last_pr_event = None
        pr_event = check_pr_envelope(ratio_P_cmp, self.PR_cycle_min, self.PR_cycle_max)
        if pr_event == "pr_above_max":
            self._last_pr_event = ("pr_above_max", ratio_P_cmp, self.PR_cycle_max)
            return None
        if pr_event == "pr_below_min":
            self._last_pr_event = ("pr_below_min", ratio_P_cmp, self.PR_cycle_min)
            import CoolProp.CoolProp as CP

            P_evap_clamp = cs["P_ref_cmp_in [Pa]"]
            P_cond_clamp = self.PR_cycle_min * P_evap_clamp
            T_tank_sat_K = CP.PropsSI("T", "P", P_cond_clamp, "Q", 0, self.ref)
            cs = calc_ref_state(
                T_evap_K=T_ou_sat_K,
                T_cond_K=T_tank_sat_K,
                refrigerant=self.ref,
                eta_cmp_isen=1.0,  # Temporary dummy value to get basic states
                mode="heating",
                dT_superheat=actual_dT_superheat,
                dT_subcool=actual_dT_subcool,
                is_active=True,
                rps=None,
            )
            ratio_P_cmp = (
                cs["P_ref_cmp_out [Pa]"] / cs["P_ref_cmp_in [Pa]"] if cs["P_ref_cmp_in [Pa]"] > 0 else self.PR_cycle_min
            )

        P_cond = cs["P_ref_cmp_out [Pa]"]
        s_cmp_in = cs["s_ref_cmp_in [J/(kg·K)]"]
        h_cmp_in = cs["h_ref_cmp_in [J/kg]"]
        h_exp_in = cs["h_ref_exp_in [J/kg]"]

        # Compute isentropic enthalpy once before loop
        try:
            import CoolProp.CoolProp as CP

            h_ref_cmp_out_isen = CP.PropsSI("H", "P", P_cond, "S", s_cmp_in, self.ref)
        except ValueError:
            h_ref_cmp_out_isen = h_cmp_in

        def _residual_rps(rps):
            val_eta_vol = _eval_eff(self.eta_cmp_vol, ratio_P_cmp, rps)
            val_eta_isen = _eval_eff(self.eta_cmp_isen, ratio_P_cmp, rps)

            h_cmp_out = h_cmp_in + (h_ref_cmp_out_isen - h_cmp_in) / val_eta_isen
            dh_cond_local = h_cmp_out - h_exp_in

            m_dot = self.V_cmp_ref * cs["rho_ref_cmp_in [kg/m3]"] * val_eta_vol * rps
            return (m_dot * dh_cond_local) - Q_ref_tank

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

        # Post-evaluate state with final isentropic efficiency
        cs = calc_ref_state(
            T_evap_K=T_ou_sat_K,
            T_cond_K=T_tank_sat_K,
            refrigerant=self.ref,
            eta_cmp_isen=val_eta_isen,
            mode="heating",
            dT_superheat=actual_dT_superheat,
            dT_subcool=actual_dT_subcool,
            is_active=True,
            rps=cmp_rps,
        )

        m_dot_ref = self.V_cmp_ref * cs["rho_ref_cmp_in [kg/m3]"] * val_eta_vol * cmp_rps
        Q_ref_tank_calc = m_dot_ref * (cs["h_ref_cmp_out [J/kg]"] - cs["h_ref_exp_in [J/kg]"])
        Q_ref_ou = m_dot_ref * (cs["h_ref_cmp_in [J/kg]"] - cs["h_ref_exp_out [J/kg]"])
        E_cmp = m_dot_ref * (cs["h_ref_cmp_out [J/kg]"] - cs["h_ref_cmp_in [J/kg]"]) / val_eta_electro_mech

        HX_perf_ou: dict = calc_HX_perf_for_target_heat(
            Q_ref_target=Q_ref_ou,
            T_ou_a_in_C=T0,
            T_ref_evap_sat_K=cs["T_ref_evap_sat_K"],
            T_ref_cond_sat_l_K=cs["T_ref_cond_sat_l_K"],
            A_cross=self.A_cross,
            UA_rated=self.UA_ou_rated,
            dV_fan_rated=self.dV_fan_a_rated,
            is_active=True,
            exponent=self.n_ou,
        )

        if HX_perf_ou.get("converged", True) is False:
            return {
                "converged": False,
                "_hx_diag": HX_perf_ou,
                "converged_rps": bool(converged_rps),
                "fan_flow_min_limit": HX_perf_ou.get("min_limit", False),
                "fan_flow_max_limit": HX_perf_ou.get("max_limit", False),
            }

        dV_ou_a: float = HX_perf_ou["dV_fan"]
        v_ou_a: float = dV_ou_a / self.A_cross
        T_ou_a_mid: float = HX_perf_ou["T_ou_a_mid"]
        Q_ou_a: float = HX_perf_ou["Q_ou_air"]

        E_ou_fan: float = calc_fan_power_from_dV_fan(
            dV_fan=dV_ou_a,
            fan_params=self.fan_params,
            vsd_coeffs=self.vsd_coeffs,
            is_active=True,
        )

        T_ou_a_out: float = T_ou_a_mid + E_ou_fan / (c_a * rho_a * dV_ou_a)

        # --- Flow state (explicit parameter, no side-effect reads) ---
        # No type annotations here on purpose: the inactive branch above
        # (around L316) already binds these names; redeclaring with
        # `name: float = ...` triggers mypy [no-redef] even though the
        # earlier branch returns unconditionally.
        dV_tank_w_out = flow_state["dV_tank_w_out [m3/s]"]
        dV_tank_w_in = flow_state["dV_tank_w_in [m3/s]"]
        dV_mix_sup_w_in = flow_state["dV_mix_sup_w_in [m3/s]"]
        dV_mix_w_out_val = flow_state["dV_mix_w_out [m3/s]"]

        if dV_mix_w_out_val == 0:
            T_mix_w_out_val = np.nan
            T_mix_w_out_val_K = np.nan
        else:
            mix = calc_mixing_valve_temp(
                T_tank_w_K,
                self.T_sup_w_K,
                self.T_mix_w_out_K,
            )
            T_mix_w_out_val = mix["T_mix_w_out"]
            T_mix_w_out_val_K = mix["T_mix_w_out_K"]

        # Energy balance: Q_tank_w_in + Q_ref_tank = Q_tank_w_out + Q_tank_loss + dU_tank/dt
        Q_tank_w_in = calc_energy_flow(G=c_w * rho_w * dV_tank_w_in, T=self.T_tank_w_in_K, T0=T0_K)
        Q_tank_w_out = calc_energy_flow(G=c_w * rho_w * dV_tank_w_out, T=T_tank_w_K, T0=T0_K)
        Q_mix_sup_w_in = calc_energy_flow(G=c_w * rho_w * dV_mix_sup_w_in, T=self.T_sup_w_K, T0=T0_K)
        Q_mix_w_out = calc_energy_flow(G=c_w * rho_w * dV_mix_w_out_val, T=T_mix_w_out_val_K, T0=T0_K)

        result = cs.copy()

        result.update(
            {
                "hp_is_on": True,
                "converged": bool(converged_rps),
                "converged_rps": bool(converged_rps),
                "fan_flow_min_limit": HX_perf_ou.get("min_limit", False),
                "fan_flow_max_limit": HX_perf_ou.get("max_limit", False),
                # Temperatures [°C]
                "T_ou_a_in [°C]": T0,
                "T_ou_a_mid [°C]": T_ou_a_mid,
                "T_ou_a_out [°C]": T_ou_a_out,
                "T_tank_w [°C]": T_tank_w,
                "T_sup_w [°C]": self.T_sup_w,
                "T_tank_w_in [°C]": self.T_tank_w_in,
                "T_mix_w_out [°C]": T_mix_w_out_val,
                "T0 [°C]": T0,
                # Volume flow rates [m3/s]
                "dV_ou_a [m3/s]": dV_ou_a,
                "v_ou_a [m/s]": v_ou_a,
                "dV_mix_w_out [m3/s]": (dV_mix_w_out_val if dV_mix_w_out_val > 0 else np.nan),
                "dV_tank_w_out [m3/s]": (dV_tank_w_out if dV_tank_w_out > 0 else np.nan),
                "dV_tank_w_in [m3/s]": (dV_tank_w_in if dV_tank_w_in > 0 else np.nan),
                "dV_mix_sup_w_in [m3/s]": (dV_mix_sup_w_in if dV_mix_sup_w_in > 0 else np.nan),
                "m_dot_ref [kg/s]": m_dot_ref,  # Mass flow rate [kg/s]
                "cmp_rpm [rpm]": cmp_rps * 60,  # Compressor speed [rpm]
                # Energy rates [W]
                "E_ou_fan [W]": E_ou_fan,
                "Q_ref_ou [W]": Q_ref_ou,
                "Q_ou_a [W]": Q_ou_a,
                "E_cmp [W]": E_cmp,
                "Q_ref_tank [W]": Q_ref_tank_calc,
                "Q_tank_w_in [W]": Q_tank_w_in,
                "Q_tank_w_out [W]": Q_tank_w_out,
                "Q_mix_sup_w_in [W]": Q_mix_sup_w_in,
                "Q_mix_w_out [W]": Q_mix_w_out,
                "E_tot [W]": E_cmp + E_ou_fan,
                # COP metrics (analogous to X_eff_ref / X_eff_sys)
                "cop_ref [-]": (Q_ref_tank_calc / E_cmp if E_cmp > 0 else np.nan),
                "cop_sys [-]": (Q_ref_tank_calc / (E_cmp + E_ou_fan) if (E_cmp + E_ou_fan) > 0 else np.nan),
            }
        )

        return result

    def _optimize_operation(
        self,
        T_tank_w: float,
        Q_ref_tank: float,
        T0: float,
        *,
        flow_state: dict,
    ):
        """Find min-power operating point (Brent 1-D).

        Parameters
        ----------
        T_tank_w : float
            Tank water temperature [°C].
        Q_ref_tank : float
            Target condenser heat rate [W].
        T0 : float
            Dead-state temperature [°C].
        flow_state : dict
            Explicit flow context passed through to ``_calc_state()``.

        Returns
        -------
        scipy.optimize.OptimizeResult
        """

        def _objective(dT_ref_ou: float) -> float:
            perf: dict | None = self._calc_state(
                dT_ref_ou=dT_ref_ou,
                T_tank_w=T_tank_w,
                Q_ref_tank=Q_ref_tank,
                T0=T0,
                flow_state=flow_state,
            )
            if perf is None or not perf.get("converged", False):
                return 1e6

            E_tot: float = float(perf.get("E_tot [W]", 1e6))
            if E_tot <= 0 or np.isnan(E_tot):
                return 1e6

            return E_tot

        return minimize_scalar(
            _objective,
            bounds=(1.0, 20.0),
            method="bounded",
            options={"maxiter": 200, "xatol": 1e-6},
        )

    # =============================================================
    # Steady-state analysis
    # =============================================================

    def analyze_steady(
        self,
        T_tank_w: float,
        T0: float,
        Q_ref_tank: float,
        *,
        return_dict: bool = True,
    ) -> dict | pd.DataFrame:
        """Run a steady-state performance snapshot.

        Evaluates the refrigerant cycle at a given operating point
        (T_tank_w, T0, Q_ref_tank) **without** solving the tank energy
        balance or tracking dynamic flows.

        Parameters
        ----------
        T_tank_w : float
            Tank water temperature [°C] — treated as a given input.
        T0 : float
            Dead-state / outdoor-air temperature [°C].
        Q_ref_tank : float
            Target condenser heat rate [W].
        return_dict : bool
            If True return dict; else single-row DataFrame.

        Returns
        -------
        dict | pd.DataFrame
            Cycle state plus diagnostic flags.

            Two keys are useful for branching:

            - ``"converged"`` (bool) — True only when the HX optimisation and
              the SciPy optimiser both succeeded.
            - ``"failure_reason"`` (str) — one of ``"none"``,
              ``"cycle_invalid"``, ``"hx_not_converged"``, or
              ``"optimizer_failed"``.

            ASHPB returns the cycle numbers (``E_cmp``, ``Q_ref_tank``, ...)
            whenever ``_calc_state`` produced a dict at all. A
            ``failure_reason`` of ``"hx_not_converged"`` therefore does not
            invalidate the result: it only means the HX residual exceeded
            tolerance and the converged flag is False. Off-mode fallback
            (E_cmp=0) only occurs when the cycle itself was infeasible.
        """
        import warnings

        # Empty flow state as steady state ignores dynamic withdrawal/refill
        flow_state = {
            "dV_mix_w_out [m3/s]": 0.0,
            "dV_tank_w_out [m3/s]": 0.0,
            "dV_tank_w_in [m3/s]": 0.0,
            "dV_mix_sup_w_in [m3/s]": 0.0,
            "alp": 0.0,
        }

        if Q_ref_tank <= 0:
            result = self._calc_state(
                dT_ref_ou=5.0,
                T_tank_w=T_tank_w,
                Q_ref_tank=0.0,
                T0=T0,
                flow_state=flow_state,
            )
        else:
            opt_result = self._optimize_operation(
                T_tank_w=T_tank_w,
                Q_ref_tank=Q_ref_tank,
                T0=T0,
                flow_state=flow_state,
            )
            result = None
            with contextlib.suppress(Exception):
                result = self._calc_state(
                    dT_ref_ou=safe_float_attr(opt_result, "x", 5.0),
                    T_tank_w=T_tank_w,
                    T0=T0,
                    Q_ref_tank=Q_ref_tank,
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
                        f"(T_tank_w={T_tank_w:.1f}°C, T0={T0:.1f}°C, Q_ref_tank={Q_ref_tank:.0f}W)"
                    )
                else:  # pr_above_max
                    print(
                        f"[PR guard] reject 상한(above PR_cycle_max): "
                        f"PR={pr_val:.3f} > {bound:.2f} "
                        f"(T_tank_w={T_tank_w:.1f}°C, T0={T0:.1f}°C, Q_ref_tank={Q_ref_tank:.0f}W)"
                    )

            # Diagnose what (if anything) went wrong. failure_reason is a
            # *report*; the fallback trigger condition is preserved as
            # `result is None or not isinstance(result, dict)` to match
            # the historical behaviour of this branch.
            opt_success = bool(getattr(opt_result, "success", False))
            if result is None or not isinstance(result, dict):
                failure_reason = (
                    "pr_above_max" if pr_event is not None and pr_event[0] == "pr_above_max" else "cycle_invalid"
                )
            elif not result.get("converged", False):
                failure_reason = "hx_not_converged"
            elif not opt_success:
                failure_reason = "optimizer_failed"
            else:
                failure_reason = "none"

            if result is None or not isinstance(result, dict):
                warnings.warn(
                    f"analyze_steady: fell back to HP-off state "
                    f"(reason={failure_reason!r}, "
                    f"T_tank_w={T_tank_w:.1f}°C, T0={T0:.1f}°C, "
                    f"Q_ref_tank={Q_ref_tank:.0f}W, "
                    f"opt_success={opt_success}, "
                    f"opt_x={safe_float_attr(opt_result, 'x', float('nan')):.2f}, "
                    f"opt_fun={safe_float_attr(opt_result, 'fun', float('nan')):.3g}). "
                    "Consider increasing UA_rated or fan-flow rated.",
                    RuntimeWarning,
                    stacklevel=2,
                )
                try:
                    result = self._calc_state(
                        dT_ref_ou=5.0,
                        T_tank_w=T_tank_w,
                        Q_ref_tank=0.0,
                        T0=T0,
                        flow_state=flow_state,
                    )
                except Exception:
                    # `dict[str, object]` so the later string-valued
                    # `failure_reason` assignment doesn't violate inferred
                    # `dict[str, bool | float]`.
                    result = {
                        "hp_is_on": False,
                        "converged": False,
                        "failure_reason": failure_reason,
                        "Q_ref_tank [W]": 0.0,
                        "Q_ref_ou [W]": 0.0,
                        "E_cmp [W]": 0.0,
                        "E_ou_fan [W]": 0.0,
                        "E_tot [W]": 0.0,
                        "T_tank_w [°C]": T_tank_w,
                        "T0 [°C]": T0,
                    }
                if isinstance(result, dict):
                    result["converged"] = False
                    result["failure_reason"] = failure_reason
            else:
                # `result` is a valid dict — keep it, but tag the diagnostic
                # so callers can branch on `failure_reason` even when the
                # cycle / HX returned a usable answer.
                result["converged"] = opt_success and result.get("converged", True)
                result["failure_reason"] = failure_reason

        if result is None or not isinstance(result, dict):
            raise RuntimeError("Simulation failed to produce a valid result dictionary.")

        # Steady state doesn't have tank loss because we don't solve tank mass/energy balance
        result["Q_tank_loss [W]"] = 0.0
        result["tank_level [-]"] = 1.0  # steady-state: always_full

        if return_dict:
            return result
        return pd.DataFrame([result])

    # =============================================================
    # Private helpers for analyze_dynamic
    # =============================================================

    @staticmethod
    def _calc_tank_flow_context(
        dV_mix_w_out: float,
        T_tank_w_K: float,
        T_sup_w_K: float,
        T_mix_w_out_K: float,
        dV_tank_w_in_override: float | None = None,
    ) -> dict:
        """Compute mixing-valve / tank-flow context (no side-effects).

        Parameters
        ----------
        dV_mix_w_out : float
            Service-water draw-off volumetric flow rate [m³/s].
        T_tank_w_K : float
            Current tank water temperature [K].
        T_sup_w_K : float
            Mains supply temperature [K].
        T_mix_w_out_K : float
            Mixing-valve target outlet temperature [K].
        dV_tank_w_in_override : float | None
            If not None, overrides the symmetry assumption
            ``dV_tank_w_in = dV_tank_w_out`` (e.g. refill control).

        Returns
        -------
        dict
            Keys: ``dV_mix_w_out``, ``dV_tank_w_out``, ``dV_tank_w_in``,
            ``dV_mix_sup_w_in``.
        """
        mix_state = calc_mixing_valve_temp(T_tank_w_K, T_sup_w_K, T_mix_w_out_K)
        flows = calc_mixing_valve_flows(dV_mix_w_out, mix_state["alp"])
        dV_tank_w_out: float = flows["dV_hot_in"]
        dV_tank_w_in: float = dV_tank_w_out if dV_tank_w_in_override is None else dV_tank_w_in_override
        return {
            "dV_mix_w_out [m3/s]": dV_mix_w_out,
            "dV_tank_w_out [m3/s]": dV_tank_w_out,
            "dV_tank_w_in [m3/s]": dV_tank_w_in,
            "dV_mix_sup_w_in [m3/s]": flows["dV_cold_in"],
            "alp": mix_state["alp"],
        }

    def _determine_hp_state(
        self,
        ctx: StepContext,
        hp_is_on_prev: bool,
    ) -> tuple[bool, dict, float]:
        """HP on/off + cycle optimisation for one step.

        Parameters
        ----------
        ctx : StepContext
            Current-step immutable context.
        hp_is_on_prev : bool
            HP state at previous step.

        Returns
        -------
        tuple[bool, dict, float]
            ``(hp_is_on, hp_result, Q_ref_tank)``.
        """
        T_tank_w: float = cu.K2C(ctx.T_tank_w_K)

        hp_is_on: bool = determine_heat_source_on_off(
            T_tank_w_C=T_tank_w,
            T_lower=self.T_tank_w_lower_bound,
            T_upper=self.T_tank_w_upper_bound,
            is_on_prev=hp_is_on_prev,
            hour_of_day=ctx.hour_of_day,
            on_schedule=self.hp_on_schedule,
        )

        Q_ref_tank: float = self.hp_capacity if hp_is_on else 0.0

        # Build explicit flow_state — no side-effects on self.dV_*
        flow_state: dict = self._calc_tank_flow_context(
            dV_mix_w_out=ctx.dV_mix_w_out,
            T_tank_w_K=ctx.T_tank_w_K,
            T_sup_w_K=self.T_sup_w_K,
            T_mix_w_out_K=self.T_mix_w_out_K,
        )

        if Q_ref_tank == 0:
            hp_result = self._calc_state(
                5.0,
                T_tank_w,
                0.0,
                ctx.T0,
                flow_state=flow_state,
            )
        else:
            opt = self._optimize_operation(
                T_tank_w,
                Q_ref_tank,
                ctx.T0,
                flow_state=flow_state,
            )
            hp_result = self._calc_state(
                float(getattr(opt, "x", 5.0)),
                T_tank_w,
                Q_ref_tank,
                ctx.T0,
                flow_state=flow_state,
            )

        if hp_result is None:
            hp_result = {}

        return (
            hp_is_on,
            hp_result,
            float(hp_result.get("Q_ref_tank [W]", 0.0)),
        )

    def _assemble_core_results(
        self,
        ctx: StepContext,
        ctrl: ControlState,
        T_solved_K: float,
        level_solved: float,
        ier: int,
    ) -> dict:
        """Build HP-core result dict at solved state.

        Subsystem results are appended separately by
        each subsystem's ``assemble_results()``.
        """
        den: float = max(
            1e-6,
            T_solved_K - self.T_sup_w_K,
        )
        alp: float = min(
            1.0,
            max(
                0.0,
                (self.T_mix_w_out_K - self.T_sup_w_K) / den,
            ),
        )
        dV_tank_w_out: float = alp * ctx.dV_mix_w_out
        dV_tank_w_in: float = dV_tank_w_out if ctrl.dV_tank_w_in_ctrl is None else ctrl.dV_tank_w_in_ctrl

        self.dV_tank_w_out = dV_tank_w_out
        self.dV_tank_w_in = dV_tank_w_in
        self.dV_mix_w_out = ctx.dV_mix_w_out
        self.dV_mix_sup_w_in = (1 - alp) * ctx.dV_mix_w_out

        T_mix_w_out_val: float = (
            calc_mixing_valve_temp(
                T_solved_K,
                self.T_sup_w_K,
                self.T_mix_w_out_K,
            )["T_mix_w_out"]
            if ctx.dV_mix_w_out > 0
            else np.nan
        )

        r: dict = {}
        r.update(ctrl.result)
        r.update(
            {
                "hp_is_on": ctrl.is_on,
                "Q_tank_loss [W]": (self.UA_tank_wall * (T_solved_K - self.T_sur_K)),
                "T_tank_w [°C]": cu.K2C(T_solved_K),
                "T_mix_w_out [°C]": T_mix_w_out_val,
                "T_tank_w_in [°C]": cu.K2C(self.T_tank_w_in_K),
                "T_sup_w [°C]": cu.K2C(self.T_sup_w_K),
            }
        )

        if not self.tank_always_full or (self.tank_always_full and self.prevent_simultaneous_flow):
            r["tank_level [-]"] = level_solved

        return r

    # =============================================================
    # Template Method Hooks (override in scenario subclasses)
    # =============================================================

    def _get_activation_flags(
        self,
        hour_of_day: float,
    ) -> dict[str, bool]:
        """Return per-subsystem schedule activation flags for *hour_of_day*.

        Returns a dict mapping subsystem name → ``True`` if the
        subsystem should be active at this hour.

        Default: delegates to ``self.stc.is_preheat_on()`` when an
        STC is attached (backward-compat); returns ``{}`` otherwise.
        Scenario subclasses override to implement custom schedules.
        """
        if self.stc is not None:
            return {"stc": self.stc.is_preheat_on(hour_of_day)}
        return {}

    def _needs_solar_input(self) -> bool:
        """Return True if any subsystem requires solar irradiance (I_DN, I_dH).

        Default: checks if self.stc or self.pv exists (backward-compat).
        Scenario subclasses should override this if they don't attach
        components directly to self.stc/self.pv.
        """
        return self.stc is not None or self.pv is not None

    def _build_residual_fn(
        self,
        ctx: "StepContext",
        ctrl: "ControlState",
        dt_s: float,
        T_tank_w_in_K_n: float,
        T_sup_w_K_n: float,
        tank_level: float,
        sub_states: dict,
    ):  # -> Callable[[float], float]
        """Return the 1-D energy-balance residual function for *brentq*.

        Default implementation: passes *sub_states* as fixed values
        (backward-compatible, semi-implicit).

        Scenario subclasses override this to re-evaluate their
        subsystem physics at ``T_cand`` during every iteration
        of the nonlinear solver, achieving a fully implicit solve.

        Parameters
        ----------
        ctx : StepContext
            Current-step immutable context.
        ctrl : ControlState
            HP control decisions.
        dt_s : float
            Time-step size [s].
        T_tank_w_in_K_n : float
            Mains water inlet temperature [K] (fixed for this step).
        T_sup_w_K_n : float
            Mains supply temperature [K] (for mixing valve).
        tank_level : float
            Pre-computed next-step tank level approximation.
        sub_states : dict
            Subsystem states computed by ``_run_subsystems()``
            (frozen at ``T_tank_n``; override to unfreeze).

        Returns
        -------
        Callable[[float], float]
            ``f(T_cand_K) -> residual`` for use with ``root_scalar``.
        """

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
        ctx: "StepContext",
        ctrl: "ControlState",
        dt: float,
        T_tank_w_in_K: float,
    ) -> dict[str, dict]:
        """Step all attached subsystems and return their state dicts.

        Default: iterates ``self._subsystems`` (backward-compat).
        Scenario subclasses override this to call specific subsystems
        without touching ``self._subsystems``.
        """
        sub_states: dict[str, dict] = {}
        for name, sub in self._subsystems.items():
            sub_states[name] = sub.step(ctx, ctrl, dt, T_tank_w_in_K)
        return sub_states

    def _augment_results(
        self,
        r: dict,
        ctx: "StepContext",
        ctrl: "ControlState",
        sub_states: dict[str, dict],
        T_solved_K: float,
    ) -> dict:
        """Append subsystem result columns to the step result dict.

        Default: iterates ``self._subsystems.assemble_results()`` (backward-compat).
        Scenario subclasses override to call specific subsystem assemblers.
        """
        for name, sub in self._subsystems.items():
            r.update(sub.assemble_results(ctx, ctrl, sub_states[name], T_solved_K))
        return r

    def _postprocess(self, df: "pd.DataFrame") -> "pd.DataFrame":
        """Post-process the result DataFrame (exergy calculations).

        Default: delegates to ``self.postprocess_exergy()`` (backward-compat).
        Scenario subclasses override to append subsystem-specific exergy columns
        after calling ``super()._postprocess(df)``.
        """
        return self.postprocess_exergy(df)

    # =============================================================
    # Public single-timestep kernel (#165 P0) — analyze_dynamic loops over it
    # =============================================================

    def make_initial_state(self, T_tank_w_init_C: float, tank_level_init: float = 1.0) -> DynamicState:
        """Build the initial carried state for a ``step()``-driven run."""
        return DynamicState(
            T_tank_w_K=cu.C2K(T_tank_w_init_C),
            tank_level=tank_level_init,
            is_refilling=False,
            hp_is_on_prev=False,
            dV_tank_w_out_prev=0.0,
        )

    def step(
        self,
        state: DynamicState,
        inputs: dict,
        dt_s: float,
    ) -> tuple[DynamicState, dict]:
        """Advance one timestep from *state* under *inputs*.

        Returns ``(new_state, result_row)``. Re-entrant: every cross-step
        quantity is read from ``state`` (never ``self``), so an external
        co-simulation master (FMI/EnergyPlus) can drive the model one exchange
        at a time and re-run it independently. The per-step environment is set
        on ``self`` afresh from ``inputs`` each call (transient scratch shared
        with the cycle/tank helpers).

        Parameters
        ----------
        state : DynamicState
            Carried state from ``make_initial_state`` or the previous ``step``.
        inputs : dict
            Per-step drivers: ``n`` (int), ``current_time_s`` [s], ``T0`` [°C],
            ``dV_mix_w_out`` [m³/s], ``T_sup_w`` [°C], ``T_sur`` [°C], and
            optional ``I_DN`` / ``I_dH`` [W/m²] (default 0).
        dt_s : float
            Timestep [s].
        """
        n = inputs["n"]
        t_s: float = inputs["current_time_s"]
        hr: float = t_s * cu.s2h
        hour_of_day: float = (t_s % (24 * cu.h2s)) * cu.s2h

        # Per-step mains water supply temperature
        T_sup_w_n: float = inputs["T_sup_w"]
        T_sup_w_K_n: float = cu.C2K(T_sup_w_n)
        T_tank_w_in_K_n: float = T_sup_w_K_n

        # Sync self fields for _calc_state compat (transient, set fresh per step)
        self.T_sup_w = T_sup_w_n
        self.T_sup_w_K = T_sup_w_K_n
        self.T_tank_w_in = T_sup_w_n
        self.T_tank_w_in_K = T_tank_w_in_K_n

        # Per-step surrounding temperature
        self.T_sur_K = cu.C2K(inputs["T_sur"])

        # Subsystem activation schedule — delegated to Hook
        activation_flags: dict[str, bool] = self._get_activation_flags(hour_of_day)

        ctx: StepContext = StepContext(
            n=n,
            current_time_s=t_s,
            current_hour=hr,
            hour_of_day=hour_of_day,
            T0=inputs["T0"],
            T0_K=cu.C2K(inputs["T0"]),
            activation_flags=activation_flags,
            T_tank_w_K=state.T_tank_w_K,
            tank_level=state.tank_level,
            dV_mix_w_out=inputs["dV_mix_w_out"],
            I_DN=inputs.get("I_DN", 0.0),
            I_dH=inputs.get("I_dH", 0.0),
            T_sup_w_K=T_sup_w_K_n,
        )

        # --- Phase A: control decisions ---
        hp_is_on, hp_result, Q_ref_cond = self._determine_hp_state(ctx, state.hp_is_on_prev)

        dV_tank_w_in_ctrl, is_refilling = determine_tank_refill_flow(
            dt=dt_s,
            tank_level=ctx.tank_level,
            dV_tank_w_out=state.dV_tank_w_out_prev,
            V_tank_full=self.V_tank_full,
            tank_always_full=self.tank_always_full,
            prevent_simultaneous_flow=self.prevent_simultaneous_flow,
            tank_level_lower_bound=self.tank_level_lower_bound,
            tank_level_upper_bound=self.tank_level_upper_bound,
            dV_tank_w_in_refill=self.dV_tank_w_in_refill,
            is_refilling=state.is_refilling,
        )

        ctrl: ControlState = ControlState(
            is_on=hp_is_on,
            Q_heat_source=Q_ref_cond,
            dV_tank_w_in_ctrl=dV_tank_w_in_ctrl,
            result=hp_result,
        )

        # --- Phase A-2: subsystem step (via Hook) ---
        sub_states: dict[str, dict] = self._run_subsystems(
            ctx,
            ctrl,
            dt_s,
            T_tank_w_in_K_n,
        )

        # --- Phase B: implicit solve (1D over T_next since mass is explicit) ---
        # Uncouple mass explicitly:
        alp_prev: float = min(
            1.0, max(0.0, (self.T_mix_w_out_K - T_sup_w_K_n) / max(1e-6, ctx.T_tank_w_K - T_sup_w_K_n))
        )
        dV_tank_w_out_prev = alp_prev * ctx.dV_mix_w_out
        dV_tank_w_in_prev = dV_tank_w_out_prev if ctrl.dV_tank_w_in_ctrl is None else ctrl.dV_tank_w_in_ctrl
        level_next_approx = ctx.tank_level + (dV_tank_w_in_prev - dV_tank_w_out_prev) * dt_s / self.V_tank_full
        tank_level = max(0.001, min(1.0, level_next_approx))

        residual_1d = self._build_residual_fn(
            ctx,
            ctrl,
            dt_s,
            T_tank_w_in_K_n,
            T_sup_w_K_n,
            tank_level,
            sub_states,
        )

        from scipy.optimize import root_scalar

        try:
            res = root_scalar(residual_1d, bracket=[cu.C2K(0.0), cu.C2K(100.0)], method="brentq")
            converged = getattr(res, "converged", False)
            root_val = getattr(res, "root", np.nan)
            if converged and not np.isnan(root_val):
                T_tank_w_K = float(root_val)
                ier = 1
            else:
                raise ValueError(f"Not converged or NaN: {res}")
        except Exception:  # Fallback to explicit step if anything fails
            # Exception ignored; explicit Euler fallback will correctly handle the state
            # Explicit Euler step for energy:
            # r_energy = C_curr * T_next - C_curr * T_curr - dt * (Q_total - UA*(T_curr - T0)) = 0
            Q_hp_val = ctrl.Q_heat_source
            alp_curr = min(1.0, max(0.0, (self.T_mix_w_out_K - T_sup_w_K_n) / max(1e-6, ctx.T_tank_w_K - T_sup_w_K_n)))
            dV_out_curr = alp_curr * ctx.dV_mix_w_out
            Q_flow_curr = c_w * rho_w * dV_out_curr * (T_sup_w_K_n - ctx.T_tank_w_K)
            Q_loss_curr = self.UA_tank_wall * (ctx.T_tank_w_K - self.T_sur_K)
            Q_tot = Q_hp_val + Q_flow_curr - Q_loss_curr  # Assumes sub_total = 0 explicitly for fallback

            T_tank_w_K = ctx.T_tank_w_K + dt_s * Q_tot / self.C_tank
            ier = 0
            if np.isnan(T_tank_w_K) and n < 10:
                pass  # Silenced NaN fallback debug print

        # --- Phase C: core + subsystem results (via Hook) ---
        r: dict = self._assemble_core_results(
            ctx,
            ctrl,
            T_tank_w_K,
            tank_level,
            ier,
        )
        r = self._augment_results(r, ctx, ctrl, sub_states, T_tank_w_K)

        new_state = DynamicState(
            T_tank_w_K=T_tank_w_K,
            tank_level=tank_level,
            is_refilling=is_refilling,
            hp_is_on_prev=hp_is_on,
            dV_tank_w_out_prev=self.dV_tank_w_out,
        )
        return new_state, r

    # =============================================================
    # Main dynamic simulation
    # =============================================================

    def analyze_dynamic(
        self,
        simulation_period_sec: int,
        dt_s: int,
        T_tank_w_init_C: float,
        dhw_usage_schedule,
        T0_schedule,
        I_DN_schedule=None,
        I_dH_schedule=None,
        T_sup_w_schedule=None,
        tank_level_init: float = 1.0,
        result_save_csv_path: str | None = None,
        T_sur_schedule=None,
    ) -> pd.DataFrame:
        """Run a time-stepping dynamic simulation.

        Fully implicit scheme: ``fsolve`` solves for
        ``[T_next, level_next]`` each timestep.

        Parameters
        ----------
        simulation_period_sec : int
            Total simulation duration [s].
        dt_s : int
            Time step size [s].
        T_tank_w_init_C : float
            Initial tank temperature [°C].
        dhw_usage_schedule : np.ndarray
            DHW volumetric flow rate per step [m³/s].
        T0_schedule : array-like
            Outdoor temperature per step [°C].
        I_DN_schedule : array-like | None
            Direct-normal irradiance per step [W/m²].
        I_dH_schedule : array-like | None
            Diffuse-horizontal irradiance [W/m²].
        T_sup_w_schedule : array-like | None
            Mains water supply temperature per step [°C].
            If ``None``, the constructor value ``T_sup_w``
            is used for every step (backward-compatible).
        tank_level_init : float
            Initial fractional tank level (0–1).
        result_save_csv_path : str | None
            Optional CSV output path.

        Returns
        -------
        pd.DataFrame
            Per-timestep result DataFrame.
        """

        time: np.ndarray = np.arange(
            0,
            simulation_period_sec,
            dt_s,
        )
        tN: int = len(time)

        T0_schedule = np.array(T0_schedule)
        if len(T0_schedule) != tN:
            raise ValueError(
                f"T0_schedule length ({len(T0_schedule)}) != time length ({tN})",
            )
        if I_DN_schedule is not None and len(I_DN_schedule) != tN:
            raise ValueError(
                f"I_DN_schedule length ({len(I_DN_schedule)}) != tN ({tN})",
            )
        if I_dH_schedule is not None and len(I_dH_schedule) != tN:
            raise ValueError(
                f"I_dH_schedule length ({len(I_dH_schedule)}) != tN ({tN})",
            )

        # T_sup_w schedule: fallback to constructor constant
        if T_sup_w_schedule is not None:
            T_sup_w_arr: np.ndarray = np.array(
                T_sup_w_schedule,
                dtype=float,
            )
            if len(T_sup_w_arr) != tN:
                raise ValueError(
                    f"T_sup_w_schedule length ({len(T_sup_w_arr)}) != tN ({tN})",
                )
        else:
            T_sup_w_arr = np.full(tN, self.T_sup_w)

        # T_sur schedule: fallback to constructor constant
        if T_sur_schedule is not None:
            T_sur_arr: np.ndarray = np.array(
                T_sur_schedule,
                dtype=float,
            )
            if len(T_sur_arr) != tN:
                raise ValueError(
                    f"T_sur_schedule length ({len(T_sur_arr)}) != tN ({tN})",
                )
        else:
            T_sur_arr = np.full(tN, cu.K2C(self.T_sur_K))

        self.time: np.ndarray = time
        self.dt: int = dt_s

        # DHW schedule handling: direct m³/s flow array
        self.dhw_flow_m3s: np.ndarray = np.asarray(
            dhw_usage_schedule,
            dtype=float,
        )
        if len(self.dhw_flow_m3s) != tN:
            raise ValueError(
                f"dhw_usage_schedule length ({len(self.dhw_flow_m3s)}) != tN ({tN})",
            )

        results_data: list[dict] = []

        # STC/PV schedule flags — resolve I_DN/I_dH per step before step()
        _use_solar: bool = self._needs_solar_input()

        state: DynamicState = self.make_initial_state(T_tank_w_init_C, tank_level_init)

        for n in tqdm(range(tN), desc="ASHPB Simulating"):
            inputs: dict = {
                "n": n,
                "current_time_s": time[n],
                "T0": T0_schedule[n],
                "dV_mix_w_out": self.dhw_flow_m3s[n],
                "T_sup_w": T_sup_w_arr[n],
                "T_sur": T_sur_arr[n],
                "I_DN": (I_DN_schedule[n] if _use_solar and I_DN_schedule is not None else 0.0),
                "I_dH": (I_dH_schedule[n] if _use_solar and I_dH_schedule is not None else 0.0),
            }
            state, r = self.step(state, inputs, dt_s)
            results_data.append(r)

        results_df: pd.DataFrame = pd.DataFrame(results_data)
        results_df = self._postprocess(results_df)
        if result_save_csv_path:
            results_df.to_csv(
                result_save_csv_path,
                index=False,
            )
        return results_df

    # =============================================================
    # Exergy post-processing (ASHP-specific)
    # =============================================================

    def postprocess_exergy(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute ASHP-specific exergy variables.

        Owns the full HP exergy topology:

        1. Refrigerant state-point exergy (CoolProp)
        2. Electricity = exergy (compressor, OU fan, UV)
        3. Air exergy (outdoor unit)
        4. Heat exchanger Carnot exergy (condenser, evaporator)
        5. Water exergy (tank inlet/outlet, mixing valve)
        6. Heat loss exergy, tank stored exergy
        7. Subsystem exergy via ``calc_exergy()`` protocol
        8. Component-level exergy destruction
        9. Exergetic efficiency metrics

        Parameters
        ----------
        df : pd.DataFrame
            Result DataFrame from ``analyze_dynamic()``.

        Returns
        -------
        pd.DataFrame
            DataFrame with exergy columns appended.
        """
        from .thermodynamics import (
            calc_exergy_flow,
            calc_refrigerant_exergy,
            convert_electricity_to_exergy,
        )

        df = df.copy()
        T0_K = cu.C2K(df["T0 [°C]"])
        T_tank_K = cu.C2K(df["T_tank_w [°C]"])

        # ── 1. Refrigerant exergy (uses pre-computed h/s from calc_ref_state)
        df = calc_refrigerant_exergy(df, self.ref, T0_K)

        # ── 2. Electricity = exergy ────────────────────────
        df = convert_electricity_to_exergy(df)

        # ── 3. Air exergy (outdoor unit) ───────────────────
        if "dV_ou_a [m3/s]" in df.columns and "T_ou_a_in [°C]" in df.columns:
            G_a = c_a * rho_a * df["dV_ou_a [m3/s]"]
            Tin = cu.C2K(df["T_ou_a_in [°C]"])
            Tmid = cu.C2K(df["T_ou_a_mid [°C]"])
            Tout = cu.C2K(df["T_ou_a_out [°C]"]) if "T_ou_a_out [°C]" in df.columns else Tin
            df["X_a_ou_in [W]"] = calc_exergy_flow(G_a, Tin, T0_K)
            df["X_a_ou_out [W]"] = calc_exergy_flow(G_a, Tout, T0_K)
            df["X_a_ou_mid [W]"] = calc_exergy_flow(G_a, Tmid, T0_K)

        # ── 4. HX exergy (Carnot form) ─────────────────────
        Q_ref_tank = df["Q_ref_tank [W]"].fillna(0)
        Q_ref_ou = df["Q_ref_ou [W]"].fillna(0)
        df["X_ref_tank [W]"] = np.where(
            Q_ref_tank > 0,
            Q_ref_tank * (1 - T0_K / cu.C2K(df["T_ref_cond_sat_v [°C]"])),
            0.0,
        )
        df["X_ref_ou [W]"] = np.where(
            Q_ref_ou > 0,
            Q_ref_ou * (1 - T0_K / cu.C2K(df["T_ref_evap_sat [°C]"])),
            0.0,
        )

        # ── 5. Water exergy (inlet / outlet) ───────────────
        df["X_tank_w_in [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_tank_w_in [m3/s]"].fillna(0),
            cu.C2K(df["T_tank_w_in [°C]"]),
            T0_K,
        )
        df["X_tank_w_out [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_tank_w_out [m3/s]"].fillna(0),
            cu.C2K(df["T_tank_w [°C]"]),
            T0_K,
        )
        df["X_mix_w_out [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_mix_w_out [m3/s]"].fillna(0),
            cu.C2K(df["T_mix_w_out [°C]"]),
            T0_K,
        )
        df["X_mix_sup_w_in [W]"] = calc_exergy_flow(
            c_w * rho_w * df["dV_mix_sup_w_in [m3/s]"].fillna(0),
            cu.C2K(df["T_sup_w [°C]"]),
            T0_K,
        )

        # ── 6. Heat loss exergy ────────────────────────────
        df["X_tank_loss [W]"] = df["Q_tank_loss [W]"] * (1 - T0_K / T_tank_K)

        # ── 7. Tank stored exergy ──────────────────────────
        tank_level = df["tank_level [-]"] if "tank_level [-]" in df.columns else 1.0
        C_tank_actual = self.C_tank * tank_level
        T_tank_K_prev = T_tank_K.shift(1)
        df["Xst_tank [W]"] = (1 - T0_K / T_tank_K) * C_tank_actual * (T_tank_K - T_tank_K_prev) / self.dt
        df.loc[df.index[0], "Xst_tank [W]"] = 0.0

        # ── 8. Removed Subsystem exergy (protocol) ─────────
        # Subsystems handle their own exergy via _postprocess hook.

        # ── 9. Total exergy input (system-level) ──────────
        X_tot = df["E_cmp [W]"] + df["E_ou_fan [W]"]
        if "X_uv [W]" in df.columns:
            X_tot = X_tot + df["X_uv [W]"].fillna(0)
        df["X_tot [W]"] = X_tot

        # ── 10. Component exergy destruction ───────────────
        # Xc = ΣX_in − ΣX_out ≥ 0 (2nd law)
        df["Xc_cmp [W]"] = df["X_cmp [W]"] + df["X_ref_cmp_in [W]"] - df["X_ref_cmp_out [W]"]
        ref_tank_active = Q_ref_tank > 0
        df["Xc_ref_tank [W]"] = np.where(
            ref_tank_active,
            df["X_ref_cmp_out [W]"] - df["X_ref_exp_in [W]"] - df["X_ref_tank [W]"],
            0.0,
        )
        df["Xc_exp [W]"] = df["X_ref_exp_in [W]"] - df["X_ref_exp_out [W]"]
        df["Xc_ref_ou [W]"] = (df["X_ref_exp_out [W]"] + df["X_a_ou_in [W]"]) - (
            df["X_ref_cmp_in [W]"] + df["X_a_ou_mid [W]"]
        )
        df["Xc_ou_fan [W]"] = df["X_ou_fan [W]"] + df["X_a_ou_mid [W]"] - df["X_a_ou_out [W]"]
        df["Xc_mix [W]"] = df["X_tank_w_out [W]"] + df["X_mix_sup_w_in [W]"] - df["X_mix_w_out [W]"]

        # 10g. Storage tank
        X_in_tank = df["X_ref_tank [W]"].fillna(0) + df["X_tank_w_in [W]"].fillna(0)
        if "X_uv [W]" in df.columns:
            X_in_tank = X_in_tank + df["X_uv [W]"].fillna(0)

        X_out_tank = df["Xst_tank [W]"]  # X_tank_loss is intentionally excluded here: it is treated as
        # part of the tank's exergy consumption rather than an outflow.
        if "X_tank_w_out [W]" in df.columns:
            X_out_tank = X_out_tank + df["X_tank_w_out [W]"].fillna(0)

        df["Xc_tank [W]"] = X_in_tank - X_out_tank

        # ── 11. Exergetic efficiency metrics ───────────────
        df["X_eff_ref [-]"] = df["X_ref_tank [W]"] / df["X_cmp [W]"].replace(0, np.nan)
        df["X_eff_sys [-]"] = df["X_ref_tank [W]"] / df["X_tot [W]"].replace(0, np.nan)

        df["X_eff_tank [-]"] = 1 - df["Xc_tank [W]"] / X_in_tank.replace(0, np.nan)

        X_in_mix = df["X_tank_w_out [W]"].fillna(0) + df["X_mix_sup_w_in [W]"].fillna(0)
        df["X_eff_mix [-]"] = 1 - df["Xc_mix [W]"] / X_in_mix.replace(0, np.nan)

        X_in_cmp = df["X_cmp [W]"] + df["X_ref_cmp_in [W]"]
        df["X_eff_cmp [-]"] = 1 - df["Xc_cmp [W]"] / X_in_cmp.replace(0, np.nan)

        df["X_eff_ref_tank [-]"] = 1 - df["Xc_ref_tank [W]"] / df["X_ref_cmp_out [W]"].replace(0, np.nan)

        df["X_eff_exp [-]"] = 1 - df["Xc_exp [W]"] / df["X_ref_exp_in [W]"].replace(0, np.nan)

        a_ou_in = df["X_a_ou_in [W]"].fillna(0) if "X_a_ou_in [W]" in df.columns else 0.0
        X_in_ref_ou = df["X_ref_exp_out [W]"] + a_ou_in
        df["X_eff_ref_ou [-]"] = 1 - df["Xc_ref_ou [W]"] / X_in_ref_ou.replace(0, np.nan)

        a_ou_mid = df["X_a_ou_mid [W]"].fillna(0) if "X_a_ou_mid [W]" in df.columns else 0.0
        X_in_ou_fan = df["X_ou_fan [W]"] + a_ou_mid
        df["X_eff_ou_fan [-]"] = 1 - df["Xc_ou_fan [W]"] / X_in_ou_fan.replace(0, np.nan)

        return df
