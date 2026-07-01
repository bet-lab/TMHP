"""Ground source heat pump — simple COP-based model with indoor unit.

This module provides a simplified GSHP model using the EnergyPlus
EquationFit COP correlation rather than a full refrigerant cycle
analysis. It is a lightweight alternative to the CoolProp-based
:class:`~tmhp.GroundSourceHeatPump` for quick parametric studies.

Borehole thermal response is tracked with pygfunction-based
g-functions and temporal superposition of dynamic building loads.
The effective borehole thermal resistance R_b* is automatically
computed using the pygfunction multipole method.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import calc_util as cu
from . import g_function as gf
from .constants import c_a, c_w, k_w, rho_a, rho_w
from .cop import calc_GSHP_COP
from .g_function import precompute_gfunction
from .hx_fan import calc_fan_power_from_dV_fan

# Aliases to match the borehole-fluid naming convention in the original code
c_f = c_w
rho_f = rho_w


@dataclass
class GroundSourceHeatPumpEmpirical:
    """Ground source heat pump model using the EnergyPlus EquationFit COP model.

    Uses borehole heat exchangers with pygfunction step-response
    factor array for precise soil thermal response with temporal
    superposition of dynamic building loads.

    This model computes borehole thermal resistance R_b* automatically
    using the pygfunction multipole method (Hellström 1991), and fan
    power using the ASHRAE 90.1 VSD curve.

    For a full refrigerant-cycle model, see :class:`~tmhp.GroundSourceHeatPump`.
    """

    # 1. Borehole parameters
    H_b: float = 150.0  # Borehole height [m]
    D_b: float = 2.0  # Borehole burial depth [m]
    r_b: float = 0.08  # Borehole radius [m]

    # 2. Pipe & Grout parameters
    k_p: float = 0.4  # Pipe thermal conductivity [W/mK] (HDPE)
    k_grout: float = 1.5  # Grout thermal conductivity [W/mK]
    r_out: float = 0.016  # Pipe outer radius [m] (32mm OD / 2)
    r_in: float = 0.013  # Pipe inner radius [m] (26mm ID / 2)
    D_s: float = 0.032  # Distance from borehole centre to pipe centre [m]

    # 3. Ground parameters
    k_g: float = 2.0  # Ground thermal conductivity [W/mK]
    c_g: float = 800.0  # Ground specific heat capacity [J/(kgK)]
    rho_g: float = 2000.0  # Ground density [kg/m³]
    T_g: float = 15.0  # Initial ground temperature [°C]

    # 4. Fluid parameters
    dV_f: float = 20.0  # Volumetric flow rate of fluid [L/min]

    # 5. Rated Performance & Design
    Q_rated_cooling: float = 20590.0  # [W]
    Q_rated_heating: float = 16450.0  # [W]
    E_pmp: float = 100.0  # Pump power input [W]
    dP_iu_fan_design: float = 60.0  # Design pressure drop [Pa]
    eta_iu_fan_design: float = 0.6  # Design fan efficiency

    # 6. Simulation Control
    dt_hours: int = 1
    sim_hours: int = 8760

    # 7. Runtime Inputs (per-timestep)
    Q_r_iu: float = 0.0
    T0: float = 20.0

    def __post_init__(self):
        # Initialize historical and temporal states
        self.time = 0.0
        self.dt_sec = self.dt_hours * 3600.0
        self.q_b_history = [0.0]

        # Calculate Effective Borehole Thermal Resistance R_b*
        # Single U-tube (series): each pipe leg carries the full borehole flow.
        # Using water properties at approx 15-20 degC (mu_f = 0.00114 Pa·s)
        m_flow_borehole = self.dV_f * cu.L2m3 * cu.s2m * rho_f  # Total borehole mass flow [kg/s]
        self.R_b = gf.calc_borehole_thermal_resistance(
            k_s=self.k_g,
            k_g=self.k_grout,
            k_p=self.k_p,
            r_b=self.r_b,
            r_out=self.r_out,
            r_in=self.r_in,
            D_s=self.D_s,
            H_b=self.H_b,
            m_flow_borehole=m_flow_borehole,
            rho_f=rho_f,
            mu_f=0.00114,
            cp_f=c_f,
            k_f=k_w,
        )

        # Fan parameters (VSD model)
        _hp_capacity = max(self.Q_rated_cooling, self.Q_rated_heating)
        self.dV_iu_fan_design = _hp_capacity / (rho_a * c_a * 10.0)
        self.E_iu_fan_design = self.dV_iu_fan_design * self.dP_iu_fan_design / self.eta_iu_fan_design
        self.vsd_coeffs_iu = {
            "c1": 0.0013,
            "c2": 0.1470,
            "c3": 0.9506,
            "c4": -0.0998,
            "c5": 0.0,
        }
        self.fan_params_iu = {
            "fan_design_flow_rate": self.dV_iu_fan_design,
            "fan_design_power": self.E_iu_fan_design,
        }

        # Precompute dimensional g-function interpolator [mK/W]
        alpha = self.k_g / (self.rho_g * self.c_g)
        self.g_func_interp = precompute_gfunction(
            N_1=1,
            N_2=1,
            B=6.0,
            H_b=self.H_b,
            D_b=self.D_b,
            r_b=self.r_b,
            alpha_s=alpha,
            k_s=self.k_g,
            t_max_s=self.sim_hours * 3600.0,
            dt_s=self.dt_sec,
        )

    def system_update(self):
        """Advance the model by one timestep.

        Call this method once per timestep after setting ``Q_r_iu``
        and ``T0``.  The method computes COP, temperatures, fan
        power, and component exergy balances.
        """
        # Unit conversion
        dV_f_m3s = self.dV_f * cu.s2m * cu.L2m3  # Nominal flow rate [m³/s]

        if not hasattr(self, "T0"):
            raise AttributeError("T0 must be provided before system_update().")

        # Determine mode based on load sign
        if self.Q_r_iu > 0:
            mode = "cooling"
            self.T_a_room = 27.0  # Room air temperature [°C]
            self.dT_r_ghx = 3.0  # GHX refrigerant - GHX outlet water [K]
            self.dT_r_iu = -15.0  # Indoor unit refrigerant - Indoor unit inlet air [K]
            self.T_r_iu = self.T_a_room + self.dT_r_iu  # Indoor unit refrigerant [°C]
            dT_a_iu = -10.0  # Indoor unit outlet air - Room air [K]
            dV_f_m3s_active = dV_f_m3s
            E_pmp_active = self.E_pmp  # Pump power input [W]
        elif self.Q_r_iu < 0:
            mode = "heating"
            self.T_a_room = 21.0  # Room air temperature [°C]
            self.dT_r_ghx = -3.0  # GHX refrigerant - GHX outlet water [K]
            self.dT_r_iu = 15.0  # Indoor unit refrigerant - Indoor unit inlet air [K]
            self.T_r_iu = self.T_a_room + self.dT_r_iu  # Indoor unit refrigerant [°C]
            dT_a_iu = 10.0  # Indoor unit outlet air - Room air [K]
            dV_f_m3s_active = dV_f_m3s
            E_pmp_active = self.E_pmp  # Pump power input [W]
        else:
            mode = "off"
            self.T_a_room = 22.0  # Room air temperature [°C]
            self.dT_r_ghx = 0.0
            self.T_r_ghx = self.T0
            self.T_r_iu = self.T0
            dT_a_iu = 0.0
            dV_f_m3s_active = 0.0
            E_pmp_active = 0.0

        # Temperatures in Kelvin
        self.T0_K = cu.C2K(self.T0)
        self.T_a_room_K = cu.C2K(self.T_a_room)

        self.T_a_iu_out_K = self.T_a_room_K + dT_a_iu

        self.T_r_iu_K = cu.C2K(self.T_r_iu)
        self.T_g_K = cu.C2K(self.T_g)

        # ---------------------------------------------------------------------
        # A. Pre-calculate the Historical Temperature Effect (Superposition)
        # ---------------------------------------------------------------------
        T_b_history_effect = 0.0

        for i in range(1, len(self.q_b_history)):
            delta_Q = self.q_b_history[i] - self.q_b_history[i - 1]
            elapsed_time = (len(self.q_b_history) - i + 1) * self.dt_sec

            # Use dimensional g-function from interpolator [mK/W]
            g_val_dim = float(self.g_func_interp(elapsed_time))
            T_b_history_effect += delta_Q * g_val_dim
        # ---------------------------------------------------------------------

        max_iter = 20
        tol = 1e-2
        # ------------------------------------------------------------------
        # Airflow calculation (indoor unit air volume flow rate).
        # Must be computed BEFORE the COP iteration loop.
        # ------------------------------------------------------------------

        if self.Q_r_iu == 0:
            self.dV_a = 0.0
        else:
            self.dV_a = abs(self.Q_r_iu) / (c_a * rho_a * abs(self.T_a_iu_out_K - self.T_a_room_K))

        # Synchronize dV_a_ratio with fan design flow rate
        dV_a_ratio = self.dV_a / self.dV_iu_fan_design if self.dV_iu_fan_design > 0 else 1.0
        # --------------------------------------------------------------------------

        self.T_f = self.T_g_K  # 초기값
        self.T_f_in = self.T_f
        self.T_f_out = self.T_f

        for _ in range(max_iter):
            T_f_in_old = self.T_f_in

            if mode == "cooling":
                self.COP = calc_GSHP_COP(
                    T_a_iu_in_K=self.T_a_room_K,
                    T_f_out_K=self.T_f_out,
                    dV_a_ratio=dV_a_ratio,
                    mode="cooling",
                )
                self.E_cmp = self.Q_r_iu / self.COP
            elif mode == "heating":
                self.COP = calc_GSHP_COP(
                    T_a_iu_in_K=self.T_a_room_K,
                    T_f_out_K=self.T_f_out,
                    dV_a_ratio=dV_a_ratio,
                    mode="heating",
                )
                self.E_cmp = -self.Q_r_iu / self.COP
            else:
                self.COP = 0.0
                self.E_cmp = 0.0

            self.Q_r_ghx = self.Q_r_iu + self.E_cmp
            self.q_b = (self.Q_r_ghx + E_pmp_active) / self.H_b

            # -----------------------------------------------------------------
            # B. Core Calculation: Borehole Wall Temp with Superposition
            # -----------------------------------------------------------------
            # Dimensional g-value for the current step (dt_sec) [mK/W]
            self.g_i_dim = float(self.g_func_interp(self.dt_sec))
            self.T_b_history_effect = T_b_history_effect
            self.T_b = self.T_g_K + T_b_history_effect + (self.q_b - self.q_b_history[-1]) * self.g_i_dim
            # -----------------------------------------------------------------

            self.T_f = self.T_b + self.q_b * self.R_b
            delta_T_fluid = self.q_b * self.H_b / (2 * c_f * rho_f * dV_f_m3s_active) if dV_f_m3s_active > 0 else 0.0

            self.T_f_in = self.T_f + delta_T_fluid
            self.T_f_out = self.T_f - delta_T_fluid

            if abs(self.T_f_in - T_f_in_old) < tol or mode == "off":
                break

        # Finalize refrigerant temperature based on converged fluid temperature
        self.T_r_ghx_K = self.T_f_out + self.dT_r_ghx

        # ---------------------------------------------------------------------
        # C. Store the finalized load to history for the next timestep
        # ---------------------------------------------------------------------
        self.q_b_history.append(self.q_b)
        self.time += self.dt_hours
        # ---------------------------------------------------------------------

        # Temperature
        self.T_a_iu_in_K = self.T_a_room_K
        self.T_a_iu_in = self.T_a_room
        self.T_a_iu_out = cu.K2C(self.T_a_iu_out_K)

        # Fan power (VSD model from hx_fan)
        self.E_fan_iu = calc_fan_power_from_dV_fan(
            dV_fan=self.dV_a,
            fan_params=self.fan_params_iu,
            vsd_coeffs=self.vsd_coeffs_iu,
            is_active=(self.Q_r_iu != 0.0),
        )

        # System COP calculation
        total_pwr = self.E_cmp + self.E_fan_iu + E_pmp_active
        if total_pwr > 0:
            self.COP_sys = abs(self.Q_r_iu) / total_pwr
        else:
            self.COP_sys = 0.0

        # Helper for thermal exergy
        def get_thermal_exergy(c, rho, dV, T_stream, T_env):
            if T_stream <= 0 or dV <= 0:
                return 0.0
            return c * rho * dV * ((T_stream - T_env) - T_env * math.log(T_stream / T_env))

        # -------------------------------------------------------------
        # Exergy of air streams
        # -------------------------------------------------------------
        self.X_a_iu_in = get_thermal_exergy(c_a, rho_a, self.dV_a, self.T_a_iu_in_K, self.T0_K)
        self.X_a_iu_out = get_thermal_exergy(c_a, rho_a, self.dV_a, self.T_a_iu_out_K, self.T0_K)

        # -------------------------------------------------------------
        # Exergy of refrigerant streams
        # -------------------------------------------------------------
        if self.Q_r_iu == 0:
            self.X_g = 0.0
            self.X_b = 0.0
            self.X_r_iu = 0.0
            self.X_r_ghx = 0.0
        else:
            self.X_g = (1 - self.T0_K / self.T_g_K) * (-self.q_b * self.H_b)
            self.X_b = (1 - self.T0_K / self.T_b) * (-self.q_b * self.H_b)
            self.X_r_iu = -self.Q_r_iu * (1 - self.T0_K / self.T_r_iu_K)
            self.X_r_ghx = -self.Q_r_ghx * (1 - self.T0_K / self.T_r_ghx_K)

        self.T_r_ghx = cu.K2C(self.T_r_ghx_K)

        # -------------------------------------------------------------
        # Exergy of water streams
        # -------------------------------------------------------------
        self.X_f_in = get_thermal_exergy(c_f, rho_f, dV_f_m3s_active, self.T_f_in, self.T0_K)
        self.X_f_out = get_thermal_exergy(c_f, rho_f, dV_f_m3s_active, self.T_f_out, self.T0_K)

        # -------------------------------------------------------------
        # Component exergy balance
        # -------------------------------------------------------------
        if mode == "off":
            self.X_in_g = self.X_out_g = self.X_c_g = 0.0
            self.X_in_ghx = self.X_out_ghx = self.X_c_ghx = 0.0
            self.X_in_r = self.X_out_r = self.X_c_r = 0.0
            self.X_in_iu = self.X_out_iu = self.X_c_iu = 0.0
        else:
            # Ground
            self.X_in_g = self.X_g
            self.X_out_g = self.X_b
            self.X_c_g = self.X_in_g - self.X_out_g

            # Ground heat exchanger
            self.X_in_ghx = self.X_b + E_pmp_active
            self.X_out_ghx = self.X_r_ghx
            self.X_c_ghx = self.X_in_ghx - self.X_out_ghx

            # Refrigerant loop
            self.X_in_r = self.X_r_ghx + self.E_cmp
            self.X_out_r = self.X_r_iu
            self.X_c_r = self.X_in_r - self.X_out_r

            # Indoor unit
            self.X_in_iu = self.E_fan_iu + self.X_r_iu
            self.X_out_iu = self.X_a_iu_out - self.X_a_iu_in
            self.X_c_iu = self.X_in_iu - self.X_out_iu

        # -------------------------------------------------------------
        # Exergy efficiency
        # -------------------------------------------------------------
        if self.Q_r_iu == 0:
            self.X_eff = 0.0
        else:
            self.X_eff = (self.X_a_iu_out - self.X_a_iu_in) / (self.E_fan_iu + self.E_cmp + E_pmp_active)

        # -------------------------------------------------------------
        # Structured exergy balance
        # -------------------------------------------------------------
        self.exergy_bal = {
            "indoor unit": {
                "in": {
                    "X_r_iu": self.X_r_iu,
                    "E_fan_iu": self.E_fan_iu,
                },
                "out": {
                    "X_a_iu_out": self.X_a_iu_out,
                    "X_a_iu_in": self.X_a_iu_in,
                },
                "con": {"X_c_iu": self.X_c_iu},
            },
            "refrigerant loop": {
                "in": {
                    "X_r_ghx": self.X_r_ghx,
                    "E_cmp": self.E_cmp,
                },
                "out": {"X_r_iu": self.X_r_iu},
                "con": {"X_c_r": self.X_c_r},
            },
            "ground heat exchanger": {
                "in": {
                    "X_b": self.X_b,
                    "E_pmp": E_pmp_active,
                },
                "out": {"X_r_ghx": self.X_r_ghx},
                "con": {"X_c_ghx": self.X_c_ghx},
            },
            "ground": {
                "in": {"X_g": self.X_g},
                "out": {"X_b": self.X_b},
                "con": {"X_c_g": self.X_c_g},
            },
        }
