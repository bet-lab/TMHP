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
   Modeled with lumped-capacitance and DHW mixing logic.
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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import CoolProp.CoolProp as CP
import numpy as np
import pandas as pd
from tqdm import tqdm

from . import calc_util as cu
from ._opt_utils import ignore_minpack_progress_warning, safe_float_attr
from .compressor_envelope import check_pr_envelope
from .constants import c_w, k_w, mu_w, rho_w
from .dynamic_context import (
    ControlState,
    StepContext,
    determine_heat_source_on_off,
    determine_tank_refill_flow,
    tank_mass_energy_residual,
)
from .enex_functions import (
    calc_mixing_valve_flows,
    calc_mixing_valve_temp,
)
from .g_function import precompute_gfunction
from .ground_coupling import AggregateGFunctionCoupler, GroundCoupler
from .heat_transfer import calc_simple_tank_UA
from .refrigerant import calc_ref_state
from .stratified_tank import StratifiedTank
from .thermodynamics import calc_exergy_flow

if TYPE_CHECKING:
    from .subsystems import SolarThermalCollector


class GroundSourceHeatPumpBoiler:
    """Ground source heat pump boiler with BHE and lumped-tank model.

    The refrigerant cycle is resolved via CoolProp with user-specified
    superheat / subcool margins. An optimizer minimises total cycle
    electrical input subject to NTU-based evaporator constraints and
    analytical condenser temperature relations.
    """

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
        N_1: int = 1,
        N_2: int = 1,
        B: float = 6.0,
        D_b: float = 0,
        H_b: float = 200,
        r_b: float = 0.08,
        R_b: float | None = None,
        k_g: float = 1.5,
        k_p: float = 0.4,
        r_out: float = 0.016,
        r_in: float = 0.013,
        D_s: float = 0.025,
        dV_b_f_lpm: float = 24,
        k_s: float = 2.0,
        c_s: float = 800,
        rho_s: float = 2000,
        E_pmp: float = 200,
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
        # Cycle guard: minimum condenser-to-evaporator saturation lift [K].
        # Default 20 K guards the boundary-condition reversal (source above sink);
        # pass None explicitly to disable.
        dT_cycle_min: float | None = 20.0,
        dT_hx_min: float = 0.5,
        # Compressor pressure-ratio envelope (PR = P_cond / P_evap)
        PR_cycle_min: float = 1.5,
        PR_cycle_max: float = 10.0,
        # Compressor speed search bounds [rev/s]
        rps_min: float = 10.0,
        rps_max: float = 150.0,
        *,
        # Deprecated:
        refrigerant: str | None = None,
        V_disp_cmp: float | None = None,
        eta_cmp_mech: float | Callable | None = None,
        UA_tank: float | None = None,  # deprecated alias for UA_tank_hx
        UA_cond_design: float | None = None,
        UA_evap_design: float | None = None,
    ) -> None:
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
            V_cmp_ref = V_disp_cmp if V_disp_cmp is not None else 0.0005
        if eta_cmp is None:
            eta_cmp = eta_cmp_mech if eta_cmp_mech is not None else 0.855
        if UA_tank_hx is None:
            UA_tank_hx = UA_tank if UA_tank is not None else (UA_cond_design if UA_cond_design is not None else 500.0)
        if UA_ground is None:
            UA_ground = UA_evap_design if UA_evap_design is not None else 500.0

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
        # Common heat-pump-boiler default efficiencies (shared with ASHPB/WSHPB):
        # isentropic 0.80, volumetric 0.95 - 0.05*PR (eta_cmp already resolved
        # to 0.855 above). Resolve here so an unconfigured model is not ideal.
        self.eta_cmp_isen = eta_cmp_isen if eta_cmp_isen is not None else 0.80
        self.eta_cmp_vol = eta_cmp_vol if eta_cmp_vol is not None else (lambda r: 0.95 - 0.05 * r)
        self.eta_cmp = eta_cmp

        self.UA_tank_hx = UA_tank_hx
        self.UA_ground = UA_ground

        self.T0_K = cu.C2K(T0)
        self.Ts = Ts
        self.Ts_K = cu.C2K(self.Ts)
        self.T_bhe_f_in = Ts
        self.T_bhe_f_in_K = self.Ts_K

        self.hp_capacity = hp_capacity
        self.hp_on_schedule = hp_on_schedule or [(0.0, 24.0)]
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
        self.dT_cycle_min: float | None = dT_cycle_min
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

        if R_b is None:
            from .g_function import calc_effective_borehole_thermal_resistance, calc_local_borehole_thermal_resistance

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

        # Tank backend selection (swappable). Default "lumped" keeps the legacy
        # single-node path entirely unchanged (self._tank stays None). The
        # "stratified" backend builds a multi-node tank from the same geometry;
        # it targets the always-full buffer-tank case (cycle sink/control use the
        # volume-average node temperature, the hot draw uses the top node, and
        # the HP condenser heat is spread uniformly over the nodes by default —
        # condenser_node=None — or concentrated at a single node if given).
        # Subsystems and partial-fill/refill are not yet supported in stratified mode.
        self.tank_model = tank_model
        self.condenser_node = condenser_node
        self._tank: StratifiedTank | None = None
        if tank_model == "stratified":
            if self._subsystems:
                raise NotImplementedError("stratified tank_model does not yet support subsystems (stc/pv/uv)")
            if not tank_always_full:
                raise NotImplementedError("stratified tank_model assumes an always-full tank (tank_always_full=True)")
            self._tank = StratifiedTank(
                n_nodes=n_tank_nodes,
                volume=self.V_tank_full,
                height=self.tank_physical["H"],
                ua=self.UA_tank_wall,
            )
        elif tank_model != "lumped":
            raise ValueError(f"tank_model must be 'lumped' or 'stratified' — got {tank_model!r}")

        # Precompute g-function
        self.dt_s: float = dt_s
        self._gfunc_interp = precompute_gfunction(
            N_1=N_1, N_2=N_2, B=B, H_b=H_b, D_b=D_b, r_b=r_b, alpha_s=self.alp_s, k_s=k_s, t_max_s=t_max_s, dt_s=dt_s
        )

        # Ground-response backend: default wraps the single field-average
        # g-function above (byte-identical to the legacy inline superposition);
        # an injected coupler takes over the borehole-wall temperature response.
        self._ground_coupler: GroundCoupler = (
            ground_coupler if ground_coupler is not None else AggregateGFunctionCoupler(self._gfunc_interp)
        )

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

    def _calc_state(
        self, dT_ref_ground: float, T_tank_w: float, Q_tank_load: float, T0: float, *, flow_state: dict
    ) -> dict | None:
        is_active = Q_tank_load > 0

        # 1. Analytical Condenser Approach Temperature
        dT_ref_tank = Q_tank_load / self.UA_tank_hx if is_active else 0.0
        T_tank_w_K = cu.C2K(T_tank_w)
        _t_bhe = getattr(self, "T_bhe_f_out_K", None)
        T_b_out_K = float(_t_bhe) if _t_bhe is not None else cu.C2K(15.0)
        m_dot_cp_b = self.dV_b_f_m3s * rho_w * c_w
        T_ground_in_K = T_b_out_K + (self.E_pmp / m_dot_cp_b)
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

            inactive_result = cs.copy()
            inactive_result.update(
                {
                    "hp_is_on": False,
                    "converged": True,
                    "converged_rps": True,
                    "_penalty": 0.0,
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
                    "dV_bhe_f [m3/s]": self.dV_b_f_m3s,
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
            return inactive_result

        if self.dT_cycle_min is not None and (T_tank_sat_K - T_ground_sat_K) <= self.dT_cycle_min:
            return None
        actual_dT_subcool: float = min(self.dT_subcool, max(0.0, dT_ref_tank - self.dT_hx_min))

        import inspect

        def _eval_eff(eff: float | Callable[..., float] | None, r_p: float, rps: float) -> float:
            if eff is None:
                return 1.0
            if callable(eff):
                sig = inspect.signature(eff)
                if len(sig.parameters) == 2:
                    return eff(r_p, rps)
                return eff(r_p)
            return eff

        # 2. Refrigerant Cycle Evaluation
        cs = calc_ref_state(
            T_evap_K=T_ground_sat_K,
            T_cond_K=T_tank_sat_K,
            refrigerant=self.ref,
            eta_cmp_isen=1.0,
            mode="heating",
            dT_superheat=self.dT_superheat,
            dT_subcool=actual_dT_subcool,
            is_active=True,
        )

        h_ref_cmp_in = cs["h_ref_cmp_in [J/kg]"]
        h_ref_exp_in = cs["h_ref_exp_in [J/kg]"]
        h_ref_exp_out = cs["h_ref_exp_out [J/kg]"]
        rho_ref_cmp_in = cs["rho_ref_cmp_in [kg/m3]"]
        P_evap = cs["P_ref_cmp_in [Pa]"]
        P_cond = cs["P_ref_cmp_out [Pa]"]
        ratio_P_cmp = P_cond / P_evap if P_evap > 0 else 1.0

        # Compressor pressure-ratio envelope guard (see compressor_envelope.py).
        # Ceiling -> reject; floor -> clamp the cycle onto PR_cycle_min by holding
        # the evaporator (ground) pressure and projecting the condensing
        # (tank-side) pressure, then refresh the state. Recorded for the
        # analyze_steady hint; no print here (runs inside the optimiser loop).
        self._last_pr_event = None
        pr_event = check_pr_envelope(ratio_P_cmp, self.PR_cycle_min, self.PR_cycle_max)
        if pr_event == "pr_above_max":
            self._last_pr_event = ("pr_above_max", ratio_P_cmp, self.PR_cycle_max)
            return None
        if pr_event == "pr_below_min":
            self._last_pr_event = ("pr_below_min", ratio_P_cmp, self.PR_cycle_min)
            P_cond_clamp = self.PR_cycle_min * P_evap
            T_tank_sat_K = CP.PropsSI("T", "P", P_cond_clamp, "Q", 0, self.ref)
            cs = calc_ref_state(
                T_evap_K=T_ground_sat_K,
                T_cond_K=T_tank_sat_K,
                refrigerant=self.ref,
                eta_cmp_isen=1.0,
                mode="heating",
                dT_superheat=self.dT_superheat,
                dT_subcool=actual_dT_subcool,
                is_active=True,
            )
            h_ref_cmp_in = cs["h_ref_cmp_in [J/kg]"]
            h_ref_exp_in = cs["h_ref_exp_in [J/kg]"]
            h_ref_exp_out = cs["h_ref_exp_out [J/kg]"]
            rho_ref_cmp_in = cs["rho_ref_cmp_in [kg/m3]"]
            P_evap = cs["P_ref_cmp_in [Pa]"]
            P_cond = cs["P_ref_cmp_out [Pa]"]
            ratio_P_cmp = P_cond / P_evap if P_evap > 0 else self.PR_cycle_min

        if h_ref_cmp_in - h_ref_exp_in <= 0:
            return None

        try:
            s_cmp_in = cs["s_ref_cmp_in [J/(kg·K)]"]
            h_ref_cmp_out_isen = CP.PropsSI("H", "P", P_cond, "S", s_cmp_in, self.ref)
        except ValueError:
            h_ref_cmp_out_isen = h_ref_cmp_in

        # 3. Cycle Performance
        def _residual_rps(rps):
            val_eta_vol = _eval_eff(self.eta_cmp_vol, ratio_P_cmp, rps)
            val_eta_isen = _eval_eff(self.eta_cmp_isen, ratio_P_cmp, rps)
            h_cmp_out_local = h_ref_cmp_in + (h_ref_cmp_out_isen - h_ref_cmp_in) / val_eta_isen
            dh_cond_local = h_cmp_out_local - h_ref_exp_in
            m_dot = self.V_cmp_ref * rho_ref_cmp_in * val_eta_vol * rps
            return (m_dot * dh_cond_local) - Q_tank_load

        from scipy.optimize import brentq

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
            T_evap_K=T_ground_sat_K,
            T_cond_K=T_tank_sat_K,
            refrigerant=self.ref,
            eta_cmp_isen=val_eta_isen,
            mode="heating",
            dT_superheat=self.dT_superheat,
            dT_subcool=actual_dT_subcool,
            is_active=True,
        )

        h_ref_cmp_out = cs["h_ref_cmp_out [J/kg]"]
        m_dot_ref = self.V_cmp_ref * rho_ref_cmp_in * val_eta_vol * cmp_rps
        Q_ref_tank = m_dot_ref * (h_ref_cmp_out - h_ref_exp_in)
        Q_ref_ground = m_dot_ref * (h_ref_cmp_in - h_ref_exp_out)
        E_cmp = (m_dot_ref * (h_ref_cmp_out - h_ref_cmp_in)) / val_eta_electro_mech

        # 4. NTU Evaporator Analysis
        NTU_ground = self.UA_ground / m_dot_cp_b
        eps = 1.0 - math.exp(-NTU_ground)
        Q_ground_actual = eps * m_dot_cp_b * (T_ground_in_K - T_ground_sat_K)

        # Penalize if cycle evap load exceeds physics limit
        penalty = 0.0
        if Q_ref_ground > Q_ground_actual:
            penalty = 1e4 * (Q_ref_ground - Q_ground_actual) ** 2

        # 5. BHE state
        Q_bhe = Q_ref_ground - self.E_pmp
        Q_bhe_unit = Q_bhe / self.H_b

        # Fluid enters BHE at T_bhe_f_in_K
        T_bhe_f_in_K = T_ground_in_K - Q_ref_ground / m_dot_cp_b
        T_bhe_f_out_K = T_b_out_K

        T_bhe_f = (cu.K2C(T_bhe_f_in_K) + cu.K2C(T_bhe_f_out_K)) / 2
        T_bhe = T_bhe_f + Q_bhe_unit * self.R_b

        # 6. Assemble
        active_result: dict = cs.copy()
        active_result.update(
            {
                "hp_is_on": True,
                "converged": converged_rps,
                "converged_rps": converged_rps,
                "_penalty": penalty,
                "err_Q_ground [W]": 0.0,
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
                "dV_bhe_f [m3/s]": self.dV_b_f_m3s,
                "dV_mix_w_out [m3/s]": flow_state.get("dV_mix_w_out", 0.0),
                "dV_tank_w_in [m3/s]": flow_state.get("dV_tank_w_in", 0.0),
                "dV_tank_w_out [m3/s]": flow_state.get("dV_tank_w_out", 0.0),
                "dV_mix_sup_w_in [m3/s]": flow_state.get("dV_mix_sup_w_in", 0.0),
                "P_ref_evap_sat [Pa]": cs.get("P_ref_cmp_in [Pa]", np.nan),
                "P_ref_cond_sat_l [Pa]": cs.get("P_ref_exp_in [Pa]", np.nan),
                "m_dot_ref [kg/s]": m_dot_ref,
                "cmp_rpm [rpm]": cmp_rps * 60,
                "h_ref_evap_sat [J/kg]": CP.PropsSI("H", "P", cs.get("P_ref_cmp_in [Pa]", 1e5), "Q", 1, self.ref),
                "h_ref_cond_sat_v [J/kg]": CP.PropsSI("H", "P", cs.get("P_ref_cmp_out [Pa]", 1e6), "Q", 1, self.ref),
                "h_ref_cond_sat_l [J/kg]": h_ref_exp_in,
                "Q_tank_load [W]": Q_tank_load,
                "Q_ref_tank [W]": Q_ref_tank,
                "Q_ref_ground [W]": Q_ref_ground,
                "Q_bhe [W]": Q_bhe,
                "E_cmp [W]": E_cmp,
                "E_pmp [W]": self.E_pmp,
                "E_tot [W]": E_cmp + self.E_pmp,
                "cop_ref [-]": (Q_ref_tank / E_cmp) if E_cmp > 0 else np.nan,
                "cop_sys [-]": (Q_ref_tank / (E_cmp + self.E_pmp)) if (E_cmp + self.E_pmp) > 0 else np.nan,
            }
        )
        return active_result

    def _optimize_operation(self, T_tank_w: float, Q_tank_load: float, T0: float, *, flow_state: dict):
        from scipy.optimize import brentq

        self._opt_evals = getattr(self, "_opt_evals", 0)

        def _objective(dT_ground):
            self._opt_evals += 1
            perf = self._calc_state(
                dT_ref_ground=dT_ground, T_tank_w=T_tank_w, Q_tank_load=Q_tank_load, T0=T0, flow_state=flow_state
            )
            if perf is None:
                raise ValueError(f"Cycle impossible at dT_ground={dT_ground}")

            err = perf.get("err_Q_ground [W]", np.nan)
            if np.isnan(err):
                raise ValueError(f"NaN error at dT_ground={dT_ground}")

            return err

        self._opt_evals = 0
        try:
            opt_x = brentq(_objective, 1, 20.0, xtol=1e-4, maxiter=50)

            class OptRes:
                success = True
                x = opt_x

            return OptRes()
        except Exception:

            class OptResFail:
                success = False
                x = np.nan

            return OptResFail()

    def _determine_hp_state(self, ctx: StepContext, is_on_prev: bool) -> tuple[bool, dict, float]:
        T_tank_w = cu.K2C(ctx.T_tank_w_K)

        hp_is_on = determine_heat_source_on_off(
            T_tank_w_C=T_tank_w,
            T_lower=self.T_tank_w_lower_bound,
            T_upper=self.T_tank_w_upper_bound,
            is_on_prev=is_on_prev,
            hour_of_day=ctx.hour_of_day,
            on_schedule=self.hp_on_schedule,
        )

        Q_tank_load = self.hp_capacity if hp_is_on else 0.0

        flow_state = self._calc_tank_flow_context(
            dV_mix_w_out=ctx.dV_mix_w_out,
            T_tank_w_K=ctx.T_tank_w_K,
            T_tank_w_in_K=self.T_tank_w_in_K,
            T_mix_w_out_K=self.T_mix_w_out_K,
        )

        if Q_tank_load <= self.Q_tank_LOAD_OFF_TOL:
            # OFF
            perf = self._calc_state(5.0, T_tank_w, 0.0, cu.K2C(ctx.T0_K), flow_state=flow_state)
        else:
            # ON
            opt_res = self._optimize_operation(T_tank_w, Q_tank_load, cu.K2C(ctx.T0_K), flow_state=flow_state)
            if opt_res.success:
                opt_x = float(getattr(opt_res, "x", 0.0))
                perf_opt = self._calc_state(opt_x, T_tank_w, Q_tank_load, cu.K2C(ctx.T0_K), flow_state=flow_state)
                perf = (
                    perf_opt
                    if perf_opt is not None
                    else self._calc_state(5.0, T_tank_w, 0.0, cu.K2C(ctx.T0_K), flow_state=flow_state)
                )
            else:
                perf = self._calc_state(5.0, T_tank_w, 0.0, cu.K2C(ctx.T0_K), flow_state=flow_state)

        if perf is None:
            perf = {}

        perf["hp_is_on"] = Q_tank_load > self.Q_tank_LOAD_OFF_TOL

        # Determine convergence specifically when ON
        if Q_tank_load > self.Q_tank_LOAD_OFF_TOL:
            perf["converged"] = opt_res.success if "opt_res" in locals() else False
            perf["Q_tank_load [W]"] = Q_tank_load

        return Q_tank_load > self.Q_tank_LOAD_OFF_TOL, perf, float(perf.get("Q_tank_load [W]", 0.0))

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
        r["T_mix_w_out [°C]"] = cu.K2C(mix["T_mix_w_out_K"])

        r["Q_tank_loss [W]"] = Q_tank_loss
        r["dV_mix_w_out [m3/s]"] = ctx.dV_mix_w_out
        r["dV_tank_w_in [m3/s]"] = flow_state["dV_tank_w_in"]
        r["dV_tank_w_out [m3/s]"] = flow_state["dV_tank_w_out"]
        r["dV_mix_sup_w_in [m3/s]"] = flow_state["dV_mix_sup_w_in"]
        r["tank_level [-]"] = 1.0  # lumped capacitance

        if not self.tank_always_full or (self.tank_always_full and self.prevent_simultaneous_flow):
            r["tank_level [-]"] = level_solved

        r.pop("_penalty", None)

        return r

    def _compute_bhe_superposition(
        self,
        n: int,
        time_arr: np.ndarray,
        hp_result: dict,
        hp_is_on: bool,
    ) -> None:
        Q_bhe_unit = hp_result.get("Q_bhe [W]", 0.0) / self.H_b if hp_is_on else 0.0

        # Ground thermal response (pulse-history temporal superposition) is
        # delegated to the swappable ground coupler; the default reproduces the
        # legacy single-g-function superposition byte-for-byte, while an injected
        # backend (e.g. geolink's network) replaces it with resolved
        # borehole-to-borehole superposition. See ground_coupling.py.
        dT_bhe = self._ground_coupler.wall_temperature_rise(n, time_arr, Q_bhe_unit)

        self.T_bhe = self.Ts - dT_bhe
        T_bhe_K = cu.C2K(self.T_bhe)
        T_bhe_f_K = T_bhe_K - Q_bhe_unit * self.R_b
        self.T_bhe_f = cu.K2C(T_bhe_f_K)
        self.Q_bhe = Q_bhe_unit * self.H_b
        m_cp_b = c_w * rho_w * self.dV_b_f_m3s

        # Assume symmetrical temperature approach around average BHE fluid temperature
        dT_bhe_f_half = float((self.Q_bhe / m_cp_b) / 2) if m_cp_b > 0 else 0.0
        self.T_bhe_f_in_K = T_bhe_f_K - dT_bhe_f_half
        self.T_bhe_f_in = cu.K2C(self.T_bhe_f_in_K)
        T_bhe_f_out_K = T_bhe_f_K + dT_bhe_f_half
        self.T_bhe_f_out = cu.K2C(T_bhe_f_out_K)

        # Apply BHE state to hp_result (so it is visible correctly)
        hp_result["T_bhe [°C]"] = self.T_bhe
        hp_result["T_bhe_f [°C]"] = self.T_bhe_f
        hp_result["T_bhe_f_in [°C]"] = self.T_bhe_f_in
        hp_result["T_bhe_f_out [°C]"] = self.T_bhe_f_out

    # =============================================================
    # Tank backends (swappable)
    # =============================================================

    def _solve_lumped_tank(self, ctx, ctrl, dt_s, T_sup_w_K_n, tank_level_solve, sub_states, dV_tank_w_out_prev):
        """Legacy single-node tank: implicit fsolve over (T_tank, level)."""
        from typing import cast

        from scipy.optimize import fsolve

        res_fn = self._build_residual_fn(
            ctx=ctx,
            ctrl=ctrl,
            dt_s=dt_s,
            T_tank_w_in_K_n=T_sup_w_K_n,
            T_sup_w_K_n=T_sup_w_K_n,
            tank_level=tank_level_solve,
            sub_states=sub_states,
        )

        T_guess_K = ctx.T_tank_w_K
        try:
            with ignore_minpack_progress_warning():
                T_solved_K_arr = cast(np.ndarray, fsolve(res_fn, x0=[T_guess_K]))
            T_solved_K = float(T_solved_K_arr[0])
        except Exception:
            # explicit Euler fallback
            Q_hp_val = ctrl.Q_heat_source
            Q_flow_curr = c_w * rho_w * dV_tank_w_out_prev * (T_sup_w_K_n - ctx.T_tank_w_K)
            Q_loss_curr = self.UA_tank_wall * (ctx.T_tank_w_K - self.T_sur_K)
            Q_tot = Q_hp_val + Q_flow_curr - Q_loss_curr
            T_solved_K = ctx.T_tank_w_K + dt_s * Q_tot / (self.C_tank * tank_level_solve)

        if T_solved_K <= T_sup_w_K_n:
            T_solved_K = T_sup_w_K_n

        # Flow state evaluated at solved temperature
        flow_state_final = self._calc_tank_flow_context(
            dV_mix_w_out=ctx.dV_mix_w_out,
            T_tank_w_K=T_solved_K,
            T_tank_w_in_K=T_sup_w_K_n,
            T_mix_w_out_K=self.T_mix_w_out_K,
            dV_tank_w_in_override=ctrl.dV_tank_w_in_ctrl,
        )

        tank_vol_change_final = (flow_state_final["dV_tank_w_in"] - flow_state_final["dV_tank_w_out"]) * dt_s
        level_next = min(1.0, max(0.0, ctx.tank_level + tank_vol_change_final / self.V_tank_full))
        return T_solved_K, flow_state_final, level_next

    def _solve_stratified_tank(self, ctx, ctrl, dt_s, T_sup_w_K_n):
        """Multi-node stratified tank advance (G2 backend).

        The hot draw to the load uses the top node; the HP condenser heat is
        spread uniformly over the nodes (immersed condenser spanning the tank) or
        concentrated at ``condenser_node`` if set; the cycle/control
        representative temperature is the volume-average. The tank is always full
        (level = 1).
        """
        assert self._tank is not None
        n = self._tank.n
        # Hot supply to the mixing valve is the top node.
        T_top_K = cu.C2K(float(self._tank.T[0]))
        flow_state_final = self._calc_tank_flow_context(
            dV_mix_w_out=ctx.dV_mix_w_out,
            T_tank_w_K=T_top_K,
            T_tank_w_in_K=T_sup_w_K_n,
            T_mix_w_out_K=self.T_mix_w_out_K,
            dV_tank_w_in_override=ctrl.dV_tank_w_in_ctrl,
        )

        # HP condenser heat: uniform over all nodes (default) or one node.
        if self.condenser_node is None:
            q_node = np.full(n, ctrl.Q_heat_source / n)
        else:
            q_node = np.zeros(n)
            q_node[self.condenser_node] = ctrl.Q_heat_source
        self._tank.step(
            dt_s,
            draw_flow=flow_state_final["dV_tank_w_out"],
            T_makeup=cu.K2C(T_sup_w_K_n),
            q_source=q_node,
            T_amb=cu.K2C(self.T_sur_K),
        )

        T_solved_K = cu.C2K(float(self._tank.T.mean()))
        if T_solved_K <= T_sup_w_K_n:
            T_solved_K = T_sup_w_K_n
        level_next = 1.0
        return T_solved_K, flow_state_final, level_next

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
        is_on_prev = False
        is_refilling = False

        self.T_bhe_f = self.Ts
        self.T_bhe = self.Ts
        self.T_bhe_f_in = self.Ts
        self.T_bhe_f_in_K = self.Ts_K
        self.T_bhe_f_out = self.Ts
        self.Q_bhe = 0.0

        # Initialise the ground coupler's pulse-history state for this run.
        self._ground_coupler.reset(tN, time)

        # Initialise the stratified tank profile (uniform at the init temp).
        if self._tank is not None:
            self._tank.reset(T_tank_w_init_C)

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
            hp_is_on, hp_result, Q_ref_tank = self._determine_hp_state(ctx, is_on_prev)
            is_on_prev = hp_is_on

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

            ctrl = ControlState(
                is_on=hp_is_on,
                Q_heat_source=Q_ref_tank,
                dV_tank_w_in_ctrl=dV_tank_w_in_ctrl,
            )

            # --- Phase B: Implicit Solving ---
            sub_states = self._run_subsystems(ctx, ctrl, dt_s, T_sup_w_K_n)

            alp_prev: float = min(
                1.0, max(0.0, (self.T_mix_w_out_K - T_sup_w_K_n) / max(1e-6, ctx.T_tank_w_K - T_sup_w_K_n))
            )
            dV_tank_w_out_prev = alp_prev * ctx.dV_mix_w_out
            dV_tank_w_in_prev = dV_tank_w_out_prev if ctrl.dV_tank_w_in_ctrl is None else ctrl.dV_tank_w_in_ctrl
            tank_vol_change_prev = (dV_tank_w_in_prev - dV_tank_w_out_prev) * dt_s
            level_next_approx = min(1.0, max(0.0, ctx.tank_level + tank_vol_change_prev / self.V_tank_full))
            tank_level_solve = max(0.001, level_next_approx)

            # Tank update — swappable backend. Lumped keeps the legacy implicit
            # fsolve path (byte-identical); stratified advances the multi-node tank.
            if self._tank is None:
                T_solved_K, flow_state_final, level_next = self._solve_lumped_tank(
                    ctx, ctrl, dt_s, T_sup_w_K_n, tank_level_solve, sub_states, dV_tank_w_out_prev
                )
            else:
                T_solved_K, flow_state_final, level_next = self._solve_stratified_tank(ctx, ctrl, dt_s, T_sup_w_K_n)

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

        results_df = pd.DataFrame(results_data)
        results_df.ffill(inplace=True)
        results_df = self._postprocess(results_df)

        if result_save_csv_path:
            results_df.to_csv(result_save_csv_path, index=False)

        return results_df

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

            - ``"converged"`` (bool) — True only when the HX optimisation and
              the SciPy optimiser both succeeded.
            - ``"failure_reason"`` (str) — one of ``"none"``,
              ``"cycle_invalid"``, ``"hx_not_converged"``, or
              ``"optimizer_failed"``.

            Important: GSHPB frequently reports
            ``failure_reason="hx_not_converged"`` on realistic operating
            points because its inner NTU/HX residual tolerance is strict.
            The returned ``E_cmp [W]`` / ``Q_ref_tank [W]`` / ``cop_sys [-]``
            **are still usable** in that case — only ``"cycle_invalid"``
            forces an off-mode fallback (E_cmp=0, COP=NaN). Branch on
            ``E_cmp [W] > 0`` rather than ``failure_reason == "none"`` if
            you only want to discard truly broken results.
        """
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
            with contextlib.suppress(Exception):
                opt_x = safe_float_attr(opt_result, "x", 5.0)
                result = self._calc_state(
                    dT_ref_ground=opt_x,
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

            # Diagnose; the fallback trigger condition is unchanged from the
            # historical behaviour (`result is None or not isinstance(...)`).
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
                    f"T_tank_w={T_tank_w:.1f}°C, T_source={T_source:.1f}°C, "
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
                        dT_ref_ground=5.0,
                        T_tank_w=T_tank_w,
                        Q_tank_load=0.0,
                        T0=T0,
                        flow_state=flow_state,
                    )
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
                if isinstance(result, dict):
                    result["converged"] = False
                    result["failure_reason"] = failure_reason
            else:
                # `result` is a valid dict — keep it, attach the diagnostic.
                result["converged"] = opt_success and result.get("converged", True)
                result["failure_reason"] = failure_reason

            if (
                result is not None
                and isinstance(result, dict)
                and "opt_result" in locals()
                and hasattr(opt_result, "success")
            ):
                result["converged"] = opt_result.success

        if result is not None:
            # Steady state doesn't have tank loss because we don't solve tank mass/energy balance
            result["Q_tank_loss [W]"] = 0.0
            result["tank_level [-]"] = 1.0  # steady-state: always_full

        if result is None:
            result = {}
        if return_dict:
            return result
        return pd.DataFrame([result])

    def postprocess_exergy(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute GSHPB-specific exergy variables."""
        from .thermodynamics import calc_energy_flow, calc_refrigerant_exergy, convert_electricity_to_exergy

        df = df.copy()
        T0_K = cu.C2K(df["T0 [°C]"])
        T_tank_K = cu.C2K(df["T_tank_w [°C]"])

        df["Q_tank_w_out [W]"] = calc_energy_flow(c_w * rho_w * df["dV_tank_w_out [m3/s]"].fillna(0), T_tank_K, T0_K)

        # 1. Refrigerant state points
        df = calc_refrigerant_exergy(df, self.ref, T0_K)
        df = convert_electricity_to_exergy(df)

        # 2. Exergy flows
        G_b = c_w * rho_w * df["dV_bhe_f [m3/s]"]
        T_bhe_f_in_K = cu.C2K(df["T_bhe_f_in [°C]"])
        T_bhe_f_out_K = cu.C2K(df["T_bhe_f_out [°C]"])

        # Exergy at BHE boundaries
        X_bhe_in = calc_exergy_flow(G_b, T_bhe_f_in_K, T0_K)
        X_bhe_out = calc_exergy_flow(G_b, T_bhe_f_out_K, T0_K)

        # Fluid enters evaporator after being heated by the pump
        T_ground_in_K = T_bhe_f_out_K + df["E_pmp [W]"] / G_b.replace(0, np.nan)
        X_ground_in = calc_exergy_flow(G_b, T_ground_in_K, T0_K)

        # Fluid leaves evaporator and enters BHE
        X_ground_out = X_bhe_in

        df["X_ref_tank [W]"] = df["Q_ref_tank [W]"] * (1 - T0_K / cu.C2K(df["T_ref_cond_sat_v [°C]"]))
        df["X_ref_ground [W]"] = df["Q_ref_ground [W]"] * (1 - T0_K / cu.C2K(df["T_ref_evap_sat [°C]"]))

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
        df["Xc_tank [W]"] = (df["X_ref_cmp_out [W]"] - df["X_ref_exp_in [W]"]) - df["X_ref_tank [W]"]
        df["Xc_exp [W]"] = df["X_ref_exp_in [W]"] - df["X_ref_exp_out [W]"]
        df["Xc_ground [W]"] = (X_ground_in - X_ground_out) - df["X_ref_ground [W]"]
        df["Xc_pmp [W]"] = df["E_pmp [W]"] - (X_ground_in - X_bhe_out)

        X_in_tank = df["X_ref_tank [W]"] + df["X_tank_w_in [W]"].fillna(0) + df.get("X_uv [W]", 0.0) + X_sub_in_tank_add
        X_out_tank = df["Xst_tank [W]"] + df["X_tank_w_out [W]"].fillna(0) + X_sub_out_tank_add
        df["Xc_tank [W]"] = X_in_tank - X_out_tank

        df["Xc_mix [W]"] = (
            df["X_tank_w_out [W]"].fillna(0) + df["X_mix_sup_w_in [W]"].fillna(0) - df["X_mix_w_out [W]"].fillna(0)
        )

        # Efficiency
        df["X_eff_ref [-]"] = df["X_ref_tank [W]"] / df["X_cmp [W]"].replace(0, np.nan)
        df["X_eff_sys [-]"] = df["X_ref_tank [W]"] / df["X_tot [W]"].replace(0, np.nan)

        return df
