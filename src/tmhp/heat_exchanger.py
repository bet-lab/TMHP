"""Heat-exchanger component models, separate from fans and correlations."""

import math

import numpy as np
from scipy.optimize import root_scalar

from . import calc_util as cu
from .constants import c_a, rho_a


def calc_phase_change_hx_effectiveness(UA: float, m_dot: float, cp: float) -> float:
    """Effectiveness of a constant-temperature refrigerant / single-phase HX.

    UA [W/K], m_dot [kg/s], cp [J/(kg K)]. Zero UA or flow gives zero duty.
    """
    if not all(math.isfinite(v) for v in (UA, m_dot, cp)) or UA < 0 or m_dot < 0 or cp <= 0:
        raise ValueError("UA and m_dot must be nonnegative; cp must be positive (all finite)")
    return 1.0 - math.exp(-UA / (m_dot * cp)) if m_dot > 0 else 0.0


def calc_phase_change_hx_capacity(UA: float, m_dot: float, cp: float, T_fluid_in: float, T_ref_sat: float) -> float:
    """Available heat-transfer magnitude [W]; temperatures must use the same units."""
    if not all(math.isfinite(v) for v in (T_fluid_in, T_ref_sat)):
        raise ValueError("Temperatures must be finite")
    return calc_phase_change_hx_effectiveness(UA, m_dot, cp) * m_dot * cp * abs(T_fluid_in - T_ref_sat)


def calc_UA_from_dV_fan(
    dV_fan: float,
    dV_fan_rated: float,
    A_cross: float,
    UA: float,
    exponent: float = 0.71,
) -> float:
    """Calculate velocity-dependent UA via lumped scaling (Wang et al., 2000).

    Parameters
    ----------
    dV_fan : float
        Current fan flow rate [m³/s].
    dV_fan_rated : float
        Rated fan flow rate [m³/s].
    A_cross : float
        Heat exchanger cross-sectional area [m²].
    UA : float
        Rated UA value [W/K].
    exponent : float
        Exponent for velocity scaling. Default is 0.71 for a 1-row configuration.

    Returns
    -------
    float
        Scaled UA value [W/K].

    Notes
    -----
    Instead of the Dittus-Boelter tube-side exponent (0.8), this uses
    a simplified lumped exponent (default 0.71). This derivation assumes a 1-row
    plain fin-and-tube configuration (N=1) where the Colburn j-factor
    is proportional to Re^-0.29, leading to h ∝ V^0.71. Multi-row coils may
    use exponents between 0.5 and 0.8 depending on configuration.
    Reference: Wang et al. (2000), DOI: 10.1016/S0017-9310(99)00333-6
    """
    v = dV_fan / A_cross if A_cross > 0 else 0
    v_rated = dV_fan_rated / A_cross if A_cross > 0 else 0
    return float(UA * (v / v_rated) ** exponent)


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
