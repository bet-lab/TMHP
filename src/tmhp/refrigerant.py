"""
Refrigerant cycle calculations and optimization.
"""

from collections.abc import Callable
from typing import Any

import CoolProp.CoolProp as CP
import numpy as np

from . import calc_util as cu

__all__ = [
    "calc_ref_state",
    "create_lmtd_constraints",
    "find_ref_loop_optimal_operation",
]


def calc_ref_state(
    T_evap_K: float,  # evaporating temperature [K] (treated as saturation T)
    T_cond_K: float,  # condensing temperature [K] (treated as saturation T)
    refrigerant: str,  # refrigerant name
    eta_cmp_isen: float | Callable,  # compressor isentropic efficiency (scalar or callable)
    mode: str = "heating",  # operating mode ('heating' or 'cooling')
    dT_superheat: float = 0.0,  # [K] evaporator outlet superheat (State 1* → 1)
    dT_subcool: float = 0.0,  # [K] condenser outlet subcool (State 3* → 3)
    is_active: bool = True,  # active flag (returns nan-filled dict when False)
    rps: float | None = None,  # compressor speed [rps]
) -> dict[str, Any]:
    """Compute the four thermodynamic state points of the refrigerant cycle.

    The vapour-compression cycle has four canonical state points:

    - State 1 (``cmp_in``):  compressor inlet  — low-pressure superheated vapour
      at the evaporator outlet.
    - State 2 (``cmp_out``): compressor outlet — high-pressure superheated vapour
      at the condenser inlet.
    - State 3 (``exp_in``):  expansion-valve inlet  — high-pressure subcooled
      liquid at the condenser outlet.
    - State 4 (``exp_out``): expansion-valve outlet — low-pressure two-phase
      mixture at the evaporator inlet.

    Keys in the returned dict are always assigned by the physical compressor /
    expander ports; the ``mode`` argument is preserved verbatim under the
    ``"mode"`` key but does not change the key naming.

    Note
    ----
    Whether a given heat exchanger acts as the evaporator or the condenser in
    heating versus cooling mode is decided by the caller (``_calc_state``),
    which chooses ``T_evap_K`` and ``T_cond_K`` accordingly before calling
    this function.
    """

    # When inactive, short-circuit with a dict of NaNs so downstream code can
    # still index by key without special-casing missing fields.
    if not is_active:
        return {
            "P_ref_cmp_in [Pa]": np.nan,
            "P_ref_cmp_out [Pa]": np.nan,
            "P_ref_exp_in [Pa]": np.nan,
            "P_ref_exp_out [Pa]": np.nan,
            "P_ref_evap_sat [Pa]": np.nan,
            "P_ref_cond_sat_l [Pa]": np.nan,
            "P_ref_cond_sat_v [Pa]": np.nan,
            "T_ref_cmp_in_K": np.nan,
            "T_ref_cmp_out_K": np.nan,
            "T_ref_exp_in_K": np.nan,
            "T_ref_exp_out_K": np.nan,
            "T_ref_evap_sat_K": np.nan,
            "T_ref_cond_sat_v_K": np.nan,
            "T_ref_cond_sat_l_K": np.nan,
            "T_ref_cmp_in [°C]": np.nan,
            "T_ref_cmp_out [°C]": np.nan,
            "T_ref_exp_in [°C]": np.nan,
            "T_ref_exp_out [°C]": np.nan,
            "T_ref_evap_sat [°C]": np.nan,
            "T_ref_cond_sat_v [°C]": np.nan,
            "T_ref_cond_sat_l [°C]": np.nan,
            "h_ref_cmp_in [J/kg]": np.nan,
            "h_ref_cmp_out [J/kg]": np.nan,
            "h_ref_cond_sat_v [J/kg]": np.nan,
            "h_ref_exp_in [J/kg]": np.nan,
            "h_ref_exp_out [J/kg]": np.nan,
            "h_ref_evap_sat [J/kg]": np.nan,
            "h_ref_cond_sat_l [J/kg]": np.nan,
            "s_ref_cmp_in [J/(kg·K)]": np.nan,
            "s_ref_cmp_out [J/(kg·K)]": np.nan,
            "s_ref_cond_sat_v [J/(kg·K)]": np.nan,
            "s_ref_exp_in [J/(kg·K)]": np.nan,
            "s_ref_exp_out [J/(kg·K)]": np.nan,
            "s_ref_evap_sat [J/(kg·K)]": np.nan,
            "s_ref_cond_sat_l [J/(kg·K)]": np.nan,
            "rho_ref_cmp_in [kg/m3]": np.nan,
            "mode": mode,
        }

    # Step 1: saturation temperatures and pressures.
    T_ref_evap_sat_K = T_evap_K
    T_ref_cond_sat_l_K = T_cond_K

    P_evap = CP.PropsSI("P", "T", T_ref_evap_sat_K, "Q", 1, refrigerant)
    P_cond = CP.PropsSI("P", "T", T_ref_cond_sat_l_K, "Q", 0, refrigerant)

    # Saturation-state enthalpy / entropy at the evaporator and condenser.
    h_ref_evap_sat = CP.PropsSI("H", "T", T_ref_evap_sat_K, "Q", 1, refrigerant)
    s_ref_evap_sat = CP.PropsSI("S", "T", T_ref_evap_sat_K, "Q", 1, refrigerant)
    h_ref_cond_sat_l = CP.PropsSI("H", "T", T_ref_cond_sat_l_K, "Q", 0, refrigerant)
    s_ref_cond_sat_l = CP.PropsSI("S", "T", T_ref_cond_sat_l_K, "Q", 0, refrigerant)

    # Step 2: State 1 — actual superheated vapour at the compressor inlet.
    T_ref_cmp_in_K = T_ref_evap_sat_K + dT_superheat

    if abs(dT_superheat) < 1e-6:
        h_ref_cmp_in = h_ref_evap_sat
        s_ref_cmp_in = s_ref_evap_sat
        rho_ref_cmp_in = CP.PropsSI("D", "T", T_ref_evap_sat_K, "Q", 1, refrigerant)
    else:
        h_ref_cmp_in = CP.PropsSI("H", "T", T_ref_cmp_in_K, "P", P_evap, refrigerant)
        s_ref_cmp_in = CP.PropsSI("S", "T", T_ref_cmp_in_K, "P", P_evap, refrigerant)
        rho_ref_cmp_in = CP.PropsSI("D", "T", T_ref_cmp_in_K, "P", P_evap, refrigerant)

    # Step 3: State 2 — high-pressure superheated vapour at the compressor outlet.
    h2_isen = CP.PropsSI("H", "P", P_cond, "S", s_ref_cmp_in, refrigerant)

    if callable(eta_cmp_isen):
        import inspect

        sig = inspect.signature(eta_cmp_isen)
        if len(sig.parameters) == 2 and rps is not None:
            val_eta_cmp_isen = eta_cmp_isen(P_cond / P_evap, rps)
        else:
            val_eta_cmp_isen = eta_cmp_isen(P_cond / P_evap)
    else:
        val_eta_cmp_isen = eta_cmp_isen

    h_ref_cmp_out = h_ref_cmp_in + (h2_isen - h_ref_cmp_in) / val_eta_cmp_isen
    try:
        T_ref_cmp_out_K = CP.PropsSI("T", "P", P_cond, "H", h_ref_cmp_out, refrigerant)
        s_ref_cmp_out = CP.PropsSI("S", "P", P_cond, "H", h_ref_cmp_out, refrigerant)
    except ValueError:
        # H is too high — it exceeds CoolProp's Tmax for this fluid
        # (e.g. 435 K for R32). Do NOT clip h_ref_cmp_out, since that would
        # silently break the energy balance; just record T and s as NaN.
        T_ref_cmp_out_K = np.nan
        s_ref_cmp_out = np.nan

    # Step 3.5: State 2* — point where the high-pressure stream first reaches
    # the condenser saturation vapour line.
    T_ref_cond_sat_v_K = T_ref_cond_sat_l_K
    P_ref_cond_sat_v = P_cond
    h_ref_cond_sat_v = CP.PropsSI("H", "P", P_cond, "Q", 1, refrigerant)
    s_ref_cond_sat_v = CP.PropsSI("S", "P", P_cond, "Q", 1, refrigerant)

    # Step 4: State 3 — actual subcooled liquid at the expansion-valve inlet.
    T_ref_exp_in_K = T_ref_cond_sat_l_K - dT_subcool

    if abs(dT_subcool) < 1e-6:
        h_ref_exp_in = h_ref_cond_sat_l
        s_ref_exp_in = s_ref_cond_sat_l
    else:
        h_ref_exp_in = CP.PropsSI("H", "T", T_ref_exp_in_K, "P", P_cond, refrigerant)
        s_ref_exp_in = CP.PropsSI("S", "T", T_ref_exp_in_K, "P", P_cond, refrigerant)

    # Step 5: State 4 — two-phase mixture at the expansion-valve outlet
    # (isenthalpic expansion: h_4 = h_3).
    h_ref_exp_out = h_ref_exp_in
    T_ref_exp_out_K = CP.PropsSI("T", "P", P_evap, "H", h_ref_exp_out, refrigerant)
    s_ref_exp_out = CP.PropsSI("S", "P", P_evap, "H", h_ref_exp_out, refrigerant)

    result = {
        "P_ref_cmp_in [Pa]": P_evap,
        "P_ref_cmp_out [Pa]": P_cond,
        "P_ref_exp_in [Pa]": P_cond,
        "P_ref_exp_out [Pa]": P_evap,
        "P_ref_evap_sat [Pa]": P_evap,
        "P_ref_cond_sat_l [Pa]": P_cond,
        "P_ref_cond_sat_v [Pa]": P_ref_cond_sat_v,
        "T_ref_cmp_in_K": T_ref_cmp_in_K,
        "T_ref_cmp_out_K": T_ref_cmp_out_K,
        "T_ref_exp_in_K": T_ref_exp_in_K,
        "T_ref_exp_out_K": T_ref_exp_out_K,
        "T_ref_evap_sat_K": T_ref_evap_sat_K,
        "T_ref_cond_sat_v_K": T_ref_cond_sat_v_K,
        "T_ref_cond_sat_l_K": T_ref_cond_sat_l_K,
        "T_ref_cmp_in [°C]": cu.K2C(T_ref_cmp_in_K),
        "T_ref_cmp_out [°C]": cu.K2C(T_ref_cmp_out_K),
        "T_ref_exp_in [°C]": cu.K2C(T_ref_exp_in_K),
        "T_ref_exp_out [°C]": cu.K2C(T_ref_exp_out_K),
        "T_ref_evap_sat [°C]": cu.K2C(T_ref_evap_sat_K),
        "T_ref_cond_sat_l [°C]": cu.K2C(T_ref_cond_sat_l_K),
        "T_ref_cond_sat_v [°C]": cu.K2C(T_ref_cond_sat_v_K),
        "h_ref_cmp_in [J/kg]": h_ref_cmp_in,
        "h_ref_cmp_out [J/kg]": h_ref_cmp_out,
        "h_ref_cond_sat_v [J/kg]": h_ref_cond_sat_v,
        "h_ref_exp_in [J/kg]": h_ref_exp_in,
        "h_ref_exp_out [J/kg]": h_ref_exp_out,
        "h_ref_evap_sat [J/kg]": h_ref_evap_sat,
        "h_ref_cond_sat_l [J/kg]": h_ref_cond_sat_l,
        "s_ref_cmp_in [J/(kg·K)]": s_ref_cmp_in,
        "s_ref_cmp_out [J/(kg·K)]": s_ref_cmp_out,
        "s_ref_cond_sat_v [J/(kg·K)]": s_ref_cond_sat_v,
        "s_ref_exp_in [J/(kg·K)]": s_ref_exp_in,
        "s_ref_exp_out [J/(kg·K)]": s_ref_exp_out,
        "s_ref_evap_sat [J/(kg·K)]": s_ref_evap_sat,
        "s_ref_cond_sat_l [J/(kg·K)]": s_ref_cond_sat_l,
        "rho_ref_cmp_in [kg/m3]": rho_ref_cmp_in,
        "mode": mode,
    }

    return result


def create_lmtd_constraints() -> tuple[Any, Any]:
    """Create LMTD-based constraint functions for cycle optimization.

    Optimization requires that the heat transfer calculated by LMTD matches
    the heat transferred by the refrigerant cycle.

    Returns
    -------
    tuple[Any, Any]
        Tuple of constraint functions (constraint_tank, constraint_hx).
    """

    def constraint_tank(perf: dict[str, Any]) -> float:
        """Condenser constraint: Q_LMTD_cond - Q_ref_cond = 0"""
        if perf is None or "Q_cond" not in perf or "Q_cond_LMTD" not in perf:
            return 1e6
        return float(perf["Q_cond_LMTD"] - perf["Q_cond"])

    def constraint_hx(perf: dict[str, Any]) -> float:
        """Evaporator constraint: Q_LMTD_evap - Q_ref_evap = 0"""
        if perf is None or "Q_evap" not in perf or "Q_evap_LMTD" not in perf:
            return 1e6
        return float(perf["Q_evap_LMTD"] - perf["Q_evap"])

    return constraint_tank, constraint_hx


def find_ref_loop_optimal_operation(
    simulator_func: Any,
    refrigerant: str,
    load_W: float,
    initial_guess: list[float],
    bounds: list[tuple[float, float]],
    constraint_funcs: list[Any] | None = None,
) -> dict[str, Any] | None:
    """Find the optimal operation point for the refrigerant loop.

    Minimizes compressor power while satisfying target load and LMTD constraints.

    Parameters
    ----------
    simulator_func : callable
        Function that takes `[dT_ref_HX, dT_ref_tank]` and returns a perf dict.
    refrigerant : str
        Refrigerant name.
    load_W : float
        Target heat load [W].
    initial_guess : list[float]
        Initial guess for `[dT_evap, dT_cond]`.
    bounds : list[tuple[float, float]]
        Bounds for `[dT_evap, dT_cond]`.
    constraint_funcs : list[callable], optional
        List of constraint functions. Each takes `perf` and returns a value
        that should be 0.

    Returns
    -------
    dict[str, Any] | None
        Optimal performance dictionary, or None if optimization fails.
    """
    from scipy.optimize import minimize

    def objective(x: np.ndarray) -> float:
        perf = simulator_func(x)
        if perf is None or "W_comp" not in perf:
            return 1e6

        # Add penalty if load is not met
        load_diff = abs(perf.get("Q_cond", 0) - load_W)
        penalty = (load_diff / load_W) ** 2 * 1e5 if load_W > 0 else 0

        return float(perf["W_comp"] + penalty)

    constraints = []
    if constraint_funcs:
        for cf in constraint_funcs:

            def make_constraint(c_func: Any) -> Any:
                def constraint(x: np.ndarray) -> float:
                    perf = simulator_func(x)
                    return float(c_func(perf))

                return constraint

            constraints.append({"type": "eq", "fun": make_constraint(cf)})

    try:
        res = minimize(
            objective,
            initial_guess,
            bounds=bounds,
            constraints=constraints,
            method="SLSQP",
            options={"disp": False, "ftol": 1e-4, "maxiter": 50},
        )
        if res.success:
            return simulator_func(res.x)  # type: ignore[no-any-return]
    except Exception:
        pass

    return None
