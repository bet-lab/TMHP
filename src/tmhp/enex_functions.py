"""
Utility functions for energy, entropy, and exergy analysis.

This module contains helper functions organized into the following categories:

1. Friction and Flow Functions
   - darcy_friction_factor: Calculate Darcy friction factor
   - calc_Orifice_flow_coefficient: Calculate orifice flow coefficient
   - calc_boussinessq_mixing_flow: Calculate mixing flow based on Boussinesq approximation

2. Heat Transfer Functions
   - calc_h_vertical_plate: Natural convection heat transfer coefficient
   - calc_UA_tank_arr: Tank heat loss UA calculation
   - calc_lmtd_*: Log mean temperature difference calculations
   - calc_UA_from_dV_fan: Heat transfer coefficient from fan flow rate

3. Curve Fitting Functions
   - linear_function, quadratic_function, cubic_function, quartic_function

4. Exergy and Entropy Functions
   - generate_entropy_exergy_term: Calculate entropy and exergy terms
   - calc_exergy_flow: Calculate exergy flow rate due to material flow

5. G-function Calculations (Ground Source Heat Pumps)
   - f, chi, G_FLS: Helper functions for g-function calculation

6. TDMA Solver Functions
   - TDMA: Solve tri-diagonal matrix system
   - _add_loop_advection_terms: Add forced convection terms to TDMA coefficients

7. Heat Pump Cycle Functions
   - calculate_ASHP_*_COP: Air source heat pump COP calculations
   - calculate_GSHP_COP: Ground source heat pump COP calculation
   - calc_ref_state: Calculate refrigerant cycle states (with superheating/subcooling support)
   - find_ref_loop_optimal_operation: Find optimal operation point
8. Tank Functions
   - update_tank_temperature: Update tank temperature based on energy balance

9. Schedule Functions
   - _build_dhw_usage_ratio: Build schedule ratio array

10. Balance Printing Utilities
    - print_balance: Print energy/entropy/exergy balance
"""

import math

import numpy as np
from scipy.optimize import root_scalar

from . import calc_util as cu
from .constants import c_a, c_w, rho_a, rho_w
from .cop import (
    calc_ASHP_cooling_COP as calc_ASHP_cooling_COP,
)
from .cop import (
    calc_ASHP_heating_COP as calc_ASHP_heating_COP,
)
from .cop import (
    calc_GSHP_COP as calc_GSHP_COP,
)
from .g_function import (
    G_FLS as G_FLS,
)
from .g_function import (
    air_dynamic_viscosity as air_dynamic_viscosity,
)
from .g_function import (
    air_prandtl_number as air_prandtl_number,
)
from .g_function import (
    chi as chi,
)
from .g_function import (
    f as f,
)
from .hx_fan import (
    calc_fan_power_from_dV_fan as calc_fan_power_from_dV_fan,
)
from .hx_fan import (
    calc_UA_from_dV_fan as calc_UA_from_dV_fan,
)
from .thermodynamics import (
    calc_energy_flow as calc_energy_flow,
)
from .thermodynamics import (
    calc_exergy_flow as calc_exergy_flow,
)
from .thermodynamics import (
    calc_refrigerant_exergy as calc_refrigerant_exergy,
)
from .thermodynamics import (
    convert_electricity_to_exergy as convert_electricity_to_exergy,
)
from .thermodynamics import (
    generate_entropy_exergy_term as generate_entropy_exergy_term,
)
from .uv_treatment import (
    calc_uv_exposure_time as calc_uv_exposure_time,
)
from .uv_treatment import (
    calc_uv_lamp_power as calc_uv_lamp_power,
)
from .uv_treatment import (
    get_uv_params_from_turbidity as get_uv_params_from_turbidity,
)

__all__ = [
    # Locally defined helpers
    "calc_HX_perf_for_target_heat",
    "calc_Orifice_flow_coefficient",
    "calc_boussinessq_mixing_flow",
    "calc_mixing_valve_flows",
    "calc_mixing_valve_temp",
    "calc_stc_performance",
    "cubic_function",
    "linear_function",
    "print_balance",
    "quadratic_function",
    "quartic_function",
    "update_tank_temperature",
    # Re-exports (facade)
    "G_FLS",
    "air_dynamic_viscosity",
    "air_prandtl_number",
    "calc_ASHP_cooling_COP",
    "calc_ASHP_heating_COP",
    "calc_energy_flow",
    "calc_exergy_flow",
    "calc_fan_power_from_dV_fan",
    "calc_GSHP_COP",
    "calc_refrigerant_exergy",
    "calc_UA_from_dV_fan",
    "calc_uv_exposure_time",
    "calc_uv_lamp_power",
    "chi",
    "convert_electricity_to_exergy",
    "f",
    "generate_entropy_exergy_term",
    "get_uv_params_from_turbidity",
]


def linear_function(x, a, b):
    """Linear function: y = a*x + b"""
    return a * x + b


def quadratic_function(x, a, b, c):
    """Quadratic function: y = a*x² + b*x + c"""
    return a * x**2 + b * x + c


def cubic_function(x, a, b, c, d):
    """Cubic function: y = a*x³ + b*x² + c*x + d"""
    return a * x**3 + b * x**2 + c * x + d


def quartic_function(x, a, b, c, d, e):
    """Quartic function: y = a*x⁴ + b*x³ + c*x² + d*x + e"""
    return a * x**4 + b * x**3 + c * x**2 + d * x + e


def print_balance(balance, decimal=2):
    """
    Print energy, entropy, or exergy balance dictionary in a formatted way.

    This function prints balance information for subsystems, categorizing entries
    into in, out, consumed, and generated categories.

    Parameters
    ----------
    balance : dict
        Dictionary containing balance information for subsystems.
        Structure: {subsystem_name: {category: {symbol: value}}}
        Categories: 'in', 'out', 'con' (consumed), 'gen' (generated)
    decimal : int, optional
        Number of decimal places for output (default: 2)

    Returns
    -------
    None
        Only prints output

    Example
    -------
    >>> balance = {
    ...     "hot water tank": {
    ...         "in": {"E_heater": 5000.0},
    ...         "out": {"Q_w_tank": 4500.0, "Q_l_tank": 400.0},
    ...         "con": {"X_c_tank": 100.0}
    ...     }
    ... }
    >>> print_balance(balance)
    """
    total_length = 50

    balance_type = "energy"
    unit = "[W]"

    # Determine balance type and unit from dictionary structure
    for _subsystem, category_dict in balance.items():
        for category, _terms in category_dict.items():
            if "gen" in category:
                balance_type = "entropy"
                unit = "[W/K]"
            elif "con" in category:
                balance_type = "exergy"

    # Print balance for each subsystem
    for subsystem, category_dict in balance.items():
        text = f"{subsystem.upper()} {balance_type.upper()} BALANCE:"
        print(f"\n\n{text}" + "=" * (total_length - len(text)))

        for category, terms in category_dict.items():
            print(f"\n{category.upper()} ENTRIES:")

            for symbol, value in terms.items():
                print(f"{symbol}: {round(value, decimal)} {unit}")


# COP, G-function, Air property, and TDMA functions are now in dedicated
# modules.  Re-exported here for backward compatibility.
# See: cop.py, g_function.py, tdma.py


# ============================================================================
# Exergy and Entropy Functions
# ============================================================================


# ============================================================================
# Flow and Mixing Functions
# ============================================================================


def calc_mixing_valve_temp(T_tank_w_K, T_tank_w_in_K, T_mix_w_out_K):
    """Calculate 3-way mixing valve output temperature and mixing ratio.

    Mixes hot tank water with cold mains water to achieve the target
    service temperature ``T_mix_w_out_K``.

    Parameters
    ----------
    T_tank_w_K : float
        Current tank water temperature [K].
    T_tank_w_in_K : float
        Mains (cold) water supply temperature [K].
    T_mix_w_out_K : float
        Target delivery temperature [K].

    Returns
    -------
    dict
        ``{'alp': float, 'T_mix_w_out': float, 'T_mix_w_out_K': float}``
        - ``alp``: hot-water fraction [0–1]
        - ``T_mix_w_out``: actual service temperature [°C]
        - ``T_mix_w_out_K``: actual service temperature [K]
    """
    den = max(1e-6, T_tank_w_K - T_tank_w_in_K)
    alp = min(1.0, max(0.0, (T_mix_w_out_K - T_tank_w_in_K) / den))

    T_mix_w_out_val_K = T_tank_w_K if alp >= 1.0 else alp * T_tank_w_K + (1 - alp) * T_tank_w_in_K

    T_mix_w_out_val = cu.K2C(T_mix_w_out_val_K)
    return {
        "alp": alp,
        "T_mix_w_out": T_mix_w_out_val,
        "T_mix_w_out_K": T_mix_w_out_val_K,
    }


def calc_mixing_valve_flows(
    dV_mix_out: float,
    alp: float
) -> dict:
    """
    Calculate volumetric flow rates at a 3-way mixing valve given a mixing ratio.

    Parameters
    ----------
    dV_mix_out : float
        Total requested service/mixed flow rate [m³/s].
    alp : float
        Hot water mixing ratio [0-1].

    Returns
    -------
    dict
        Dictionary containing generic mixing valve mass balances:
        - `dV_hot_in`: Flow rate drawn from the hot source [m³/s]
        - `dV_cold_in`: Flow rate drawn from the cold source [m³/s]
        - `dV_mix_out`: Total mixed flow rate [m³/s]
    """
    dV_hot_in = alp * dV_mix_out
    dV_cold_in = (1.0 - alp) * dV_mix_out

    return {
        "dV_hot_in": dV_hot_in,
        "dV_cold_in": dV_cold_in,
        "dV_mix_out": dV_mix_out,
    }


# UV functions have been moved to uv_treatment.py.
# Re-exported above via ``from .uv_treatment import …``


def calc_Orifice_flow_coefficient(D0, D1):
    """
    Calculate the orifice flow coefficient based on diameters.

    Flow configuration::

         ---------------
          ->      |
          D0     D1 ->
          ->      |
         ---------------

    Parameters
    ----------
    D0 : float
        Pipe diameter [m]
    D1 : float
        Hole diameter [m]

    Returns
    -------
    C_d : float
        Orifice flow coefficient (dimensionless)

    Notes
    -----
    This is a simplified calculation. A more complete implementation
    should be based on physical equations.
    """
    m = D1 / D0  # Opening ratio
    return m**2


def calc_boussinessq_mixing_flow(T_upper, T_lower, A, dz, C_d=0.1):
    """
    Calculate mixing flow rate between two adjacent nodes based on Boussinesq approximation.

    Mixing occurs only when the lower node temperature is higher than the upper node,
    creating a gravitationally unstable condition.

    Parameters
    ----------
    T_upper : float
        Upper node temperature [K]
    T_lower : float
        Lower node temperature [K]
    A : float
        Tank cross-sectional area [m²]
    dz : float
        Node height [m]
    C_d : float, optional
        Flow coefficient (empirical constant), default 0.1

    Returns
    -------
    dV_mix : float
        Volumetric flow rate exchanged between nodes [m3/s]

    Notes
    -----
    TODO: C_d value should be calculated based on physical equations.
    """
    from .constants import beta, g

    if T_upper < T_lower:
        # Upper is colder (higher density) -> unstable -> mixing occurs
        delta_T = T_lower - T_upper
        dV_mix = C_d * A * math.sqrt(2 * g * beta * delta_T * dz)
        return dV_mix  # From top to bottom
    else:
        # Stable condition -> no mixing
        return 0.0


# ============================================================================
# Tank Heat Transfer Functions
# ============================================================================


# ============================================================================
# TDMA Solver Functions
# ============================================================================


# TDMA and advection-term functions have been moved to tdma.py.
# Re-exported above via ``from .tdma import …`` for backward compatibility.


# calc_UA_from_dV_fan has been moved to hx_fan.py.
# Re-exported above via ``from .hx_fan import …``


def calc_HX_perf_for_target_heat(
    Q_ref_target,
    T_a_in_C=None,
    T_ref_sat_K=None,
    A_cross=None,
    UA_rated=None,
    dV_fan_rated=None,
    is_active=True,
    exponent=0.71,
    # Legacy parameters for backward compatibility
    T_ou_a_in_C=None,
    T_ref_evap_sat_K=None,
    T_ref_cond_sat_l_K=None,
    UA_design=None,
    dV_fan_design=None,
):
    """Numerically solve for the air-side flow rate of an ε-NTU heat exchanger.

    Given a target heat transfer duty, find the airflow that delivers it,
    accounting for the velocity dependence of UA via the Wang et al. (2000)
    fin-and-tube correlation (UA ∝ velocity^0.71).

    Parameters
    ----------
    Q_ref_target : float
        Target heat transfer rate [W] (always positive).
    T_a_in_C : float, optional
        Air-side inlet temperature [°C].
    T_ref_sat_K : float, optional
        Refrigerant saturation temperature [K] on the constant-temperature side.
    A_cross : float
        Heat-exchanger cross-sectional area [m²].
    UA_rated : float
        Rated UA [W/K].
    dV_fan_rated : float
        Rated fan volumetric flow rate [m³/s].

    is_active : bool
        Active flag.
    exponent : float
        UA scaling exponent (default: 0.71).

    # Legacy aliases (optional)
    T_ou_a_in_C : float, optional
        Backward-compat alias for ``T_a_in_C``.
    T_ref_evap_sat_K : float, optional
        Backward-compat alias for ``T_ref_sat_K``.
    T_ref_cond_sat_l_K : float, optional
        Unused; kept only to preserve the older function signature.

    Returns
    -------
    dict
        Dictionary with the following keys:

        - ``dV_fan`` — required air-side flow rate [m³/s]
        - ``UA`` — overall heat-transfer coefficient at the solution point [W/K]
        - ``T_a_mid_C`` — air temperature between the heat exchanger and the fan [°C]
        - ``Q_air`` — heat-transfer rate at the operating point [W]
        - ``epsilon`` — effectiveness at the operating point [–]
        - ``converged`` — whether the solver converged

        All numeric values are ``np.nan`` when ``is_active=False``.
    """
    # Backward-compat: accept legacy parameter names.
    if T_a_in_C is None:
        T_a_in_C = T_ou_a_in_C
    if T_ref_sat_K is None:
        T_ref_sat_K = T_ref_evap_sat_K
    if UA_rated is None:
        UA_rated = UA_design
    if dV_fan_rated is None:
        dV_fan_rated = dV_fan_design

    if not is_active or T_a_in_C is None or T_ref_sat_K is None:
        return {
            "converged": True,
            "dV_fan": np.nan,
            "UA": np.nan,
            "T_a_mid_C": np.nan,
            "Q_air": np.nan,
            "epsilon": np.nan,
            # Legacy keys
            "T_ou_a_mid": np.nan,
            "Q_ou_air": np.nan,
        }

    T_a_in_K = cu.C2K(T_a_in_C)

    if abs(Q_ref_target) < 1e-6:
        return {
            "converged": True,
            "dV_fan": 0.0,
            "UA": 0.0,
            "T_a_mid_C": T_a_in_C,
            "Q_air": 0.0,
            "epsilon": 0.0,
            # Legacy keys
            "T_ou_a_mid": T_a_in_C,
            "Q_ou_air": 0.0,
        }

    def _error_function(dV_fan):
        if dV_fan <= 0:
            return -Q_ref_target
        UA = calc_UA_from_dV_fan(dV_fan, dV_fan_rated, A_cross, UA_rated, exponent)
        C_air = c_a * rho_a * dV_fan
        epsilon = 1 - np.exp(-UA / C_air)
        # Heat transfer Q = C_air * epsilon * abs(T_air_in - T_ref_sat)
        Q_air = C_air * epsilon * abs(T_a_in_K - T_ref_sat_K)
        return Q_air - Q_ref_target

    # Search range: 5% to 100% of rated flow
    dV_min = dV_fan_rated * 0.05
    dV_max = dV_fan_rated

    try:
        sol = root_scalar(_error_function, bracket=[dV_min, dV_max], method="bisect")
        dV_sol = sol.root
        converged = sol.converged
    except ValueError:
        err_min = _error_function(dV_min)
        err_max = _error_function(dV_max)
        # Optimization loop penalty handling: return failure flag
        return {
            "converged": False,
            "dV_fan": np.nan,
            "UA": np.nan,
            "T_a_mid_C": np.nan,
            "Q_air": np.nan,
            "epsilon": np.nan,
            "min_limit": bool(err_min > 0),
            "max_limit": bool(err_max < 0),
            "T_ou_a_mid": np.nan,
            "Q_ou_air": np.nan,
        }

    # Final calculations at solved point
    UA_sol = calc_UA_from_dV_fan(dV_sol, dV_fan_rated, A_cross, UA_rated, exponent)
    C_air_sol = c_a * rho_a * dV_sol
    eps_sol = 1 - np.exp(-UA_sol / C_air_sol) if dV_sol > 0 else 0.0

    # Exit air temperature (before fan heat if any)
    T_a_mid_K = T_a_in_K - (T_a_in_K - T_ref_sat_K) * eps_sol
    T_a_mid_C = cu.K2C(T_a_mid_K)
    Q_air_sol = C_air_sol * abs(T_a_in_K - T_a_mid_K)

    return {
        "converged": converged,
        "dV_fan": dV_sol,
        "UA": UA_sol,
        "T_a_mid_C": T_a_mid_C,
        "Q_air": Q_air_sol,
        "epsilon": eps_sol,
        "min_limit": False,
        "max_limit": False,
        # Legacy keys
        "T_ou_a_mid": T_a_mid_C,
        "Q_ou_air": Q_air_sol,
    }




# calc_fan_power_from_dV_fan and check_hp_schedule_active have been moved
# to hx_fan.py.  Re-exported above via ``from .hx_fan import …``


# get_uv_params_from_turbidity and calc_uv_exposure_time have been moved
# to uv_treatment.py.  Re-exported above via ``from .uv_treatment import …``


def update_tank_temperature(T_tank_w_K, Q_gain, UA_tank_wall, T0_K, C_tank, dt):
    """Update tank temperature using the Crank-Nicolson implicit scheme.

    The governing ODE for a lumped-capacitance tank is:

        C dT/dt = Q_gain - UA (T - T0)

    Crank-Nicolson averages the loss term across both time levels:

        T^{n+1} = [(C/dt - UA/2) T^n + Q_gain + UA T0] / (C/dt + UA/2)

    This scheme is second-order accurate in time and unconditionally
    stable, eliminating the overshoot that Forward Euler can exhibit
    when dt is large relative to the thermal time constant C/UA.

    Parameters
    ----------
    T_tank_w_K : float
        Current tank temperature [K].
    Q_gain : float
        Total heat gain rate [W] (condenser, UV, STC, refill, etc.).
    UA_tank_wall : float
        Overall tank heat-loss coefficient [W/K] (shell/insulation envelope).
    T0_K : float
        Dead-state / ambient temperature [K].
    C_tank : float
        Tank thermal capacitance [J/K] (= c_w * rho_w * V_tank * level).
    dt : float
        Time step [s].

    Returns
    -------
    float
        Updated tank temperature [K].
    """
    a = C_tank / dt
    T_tank_w_K_new = ((a - UA_tank_wall / 2) * T_tank_w_K + Q_gain + UA_tank_wall * T0_K) / (a + UA_tank_wall / 2)
    return T_tank_w_K_new


def calc_stc_performance(
    I_DN_stc,  # direct normal irradiance [W/m²]
    I_dH_stc,  # diffuse horizontal irradiance [W/m²]
    T_stc_w_in_K,  # STC inlet water temperature (tank temperature) [K]
    T0_K,  # reference (ambient) temperature [K]
    A_stc_pipe,  # STC pipe area [m²]
    alpha_stc,  # absorptance [-]
    h_o_stc,  # outer convective HT coefficient [W/m²K]
    h_r_stc,  # air-gap radiative HT coefficient [W/m²K]
    k_ins_stc,  # insulation thermal conductivity [W/mK]
    x_air_stc,  # air-gap thickness [m]
    x_ins_stc,  # insulation thickness [m]
    dV_stc,  # STC volumetric flow rate [m³/s]
    E_pump,  # pump electrical input [W]
    is_active=True,  # active flag (default: True)
):
    """Compute the performance of a solar thermal collector (STC).

    Adapted from the ``SolarAssistedGasBoiler.system_update`` logic in the
    legacy ``enex_engine.py``.

    Parameters
    ----------
    I_DN_stc : float
        Direct normal irradiance [W/m²].
    I_dH_stc : float
        Diffuse horizontal irradiance [W/m²].
    T_stc_w_in_K : float
        Inlet water temperature to the STC (equal to the tank temperature) [K].
    T0_K : float
        Reference / ambient temperature [K].
    A_stc_pipe : float
        STC pipe area [m²].
    alpha_stc : float
        Absorptance [-].
    h_o_stc : float
        Outer convective heat-transfer coefficient [W/m²K].
    h_r_stc : float
        Air-gap radiative heat-transfer coefficient [W/m²K].
    k_ins_stc : float
        Insulation thermal conductivity [W/mK].
    x_air_stc : float
        Air-gap thickness [m].
    x_ins_stc : float
        Insulation thickness [m].
    dV_stc : float
        STC volumetric flow rate [m³/s].
    E_pump : float
        Pump electrical input [W].
    is_active : bool, optional
        Active flag (default: True). When ``False``, returns a dict filled
        with ``np.nan`` (except ``T_stc_w_out_K`` and ``T_stc_w_in_K``, which
        are set to the inlet temperature).

    Returns
    -------
    dict
        - ``I_sol_stc``      — total incident irradiance [W/m²]
        - ``Q_sol_stc``      — absorbed solar heat [W]
        - ``Q_stc_w_in``     — inlet enthalpy flow relative to ``T0_K`` [W]
        - ``Q_stc_w_out``    — outlet enthalpy flow relative to ``T0_K`` [W]
        - ``ksi_stc``        — dimensionless efficiency parameter [-]
        - ``T_stc_w_out_K``  — outlet water temperature [K]
        - ``T_stc_w_final_K``— outlet temperature including pump heat gain [K]
        - ``T_stc_w_in_K``   — inlet water temperature (echoed back) [K]
        - ``T_stc_K``        — mean absorber-plate temperature [K]
        - ``Q_l_stc``        — absorber-plate heat loss [W]

    Notes
    -----
    - All variable names are suffixed with ``_stc`` to mark them as
      STC-specific.
    - Heat losses are reported as ``Q_l_stc``.
    - Entropy and exergy quantities are intentionally not computed here; do
      them as a post-processing step on the result CSV if needed.
    """
    from .constants import k_a

    # Compute U_stc from layer resistances [m²K/W].
    R_air_stc = x_air_stc / k_a
    R_ins_stc = x_ins_stc / k_ins_stc
    R_o_stc = 1 / h_o_stc
    R_r_stc = 1 / h_r_stc

    R1_stc = (R_r_stc * R_air_stc) / (R_r_stc + R_air_stc) + R_o_stc
    R2_stc = R_ins_stc + R_o_stc

    # Overall U-value (parallel resistance network) [W/m²K].
    U1_stc = 1 / R1_stc
    U2_stc = 1 / R2_stc
    U_stc = U1_stc + U2_stc

    # When inactive, short-circuit with a NaN-filled dict (preserving inlet T).
    if not is_active:
        return {
            "I_sol_stc": np.nan,
            "Q_sol_stc": np.nan,
            "Q_stc_w_in": np.nan,
            "Q_stc_w_out": np.nan,
            "ksi_stc": np.nan,
            "T_stc_w_final_K": T_stc_w_in_K,  # echo inlet
            "T_stc_w_out_K": T_stc_w_in_K,  # echo inlet
            "T_stc_w_in_K": T_stc_w_in_K,
            "T_stc_K": np.nan,
            "Q_l_stc": np.nan,
        }

    # Total incident irradiance.
    I_sol_stc = I_DN_stc + I_dH_stc

    # Absorbed solar heat.
    Q_sol_stc = I_sol_stc * A_stc_pipe * alpha_stc

    # Heat-capacity flow rate.
    G_stc = c_w * rho_w * dV_stc

    # Inlet enthalpy flow relative to T0_K.
    Q_stc_w_in = calc_energy_flow(G_stc, T_stc_w_in_K, T0_K)

    # Dimensionless efficiency parameter.
    ksi_stc = np.exp(-A_stc_pipe * U_stc / G_stc)

    # STC outlet temperature.
    T_stc_w_out_numerator = (
        T0_K
        + (
            Q_sol_stc
            + Q_stc_w_in
            + A_stc_pipe * U_stc * (ksi_stc * T_stc_w_in_K / (1 - ksi_stc))
            + A_stc_pipe * U_stc * T0_K
        )
        / G_stc
    )

    T_stc_w_out_denominator = 1 + (A_stc_pipe * U_stc) / ((1 - ksi_stc) * G_stc)

    T_stc_w_out_K = T_stc_w_out_numerator / T_stc_w_out_denominator
    T_stc_w_final_K = T_stc_w_out_K + E_pump / G_stc
    T_stc_K = 1 / (1 - ksi_stc) * T_stc_w_out_K - ksi_stc / (1 - ksi_stc) * T_stc_w_in_K

    # STC outlet enthalpy flow relative to T0_K.
    Q_stc_w_out = calc_energy_flow(G_stc, T_stc_w_out_K, T0_K)

    # Absorber-plate heat loss.
    Q_l_stc = A_stc_pipe * U_stc * (T_stc_K - T0_K)

    return {
        "I_sol_stc": I_sol_stc,
        "Q_sol_stc": Q_sol_stc,
        "Q_stc_w_in": Q_stc_w_in,
        "Q_stc_w_out": Q_stc_w_out,
        "ksi_stc": ksi_stc,
        "T_stc_w_final_K": T_stc_w_final_K,
        "T_stc_w_out_K": T_stc_w_out_K,
        "T_stc_w_in_K": T_stc_w_in_K,
        "T_stc_K": T_stc_K,
        "Q_l_stc": Q_l_stc,
    }
