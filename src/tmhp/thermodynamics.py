"""
Thermodynamic property and exergy calculations.
"""

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import pandas as pd

import CoolProp.CoolProp as CP

__all__ = [
    "calc_energy_flow",
    "calc_exergy_flow",
    "calc_refrigerant_exergy",
    "convert_electricity_to_exergy",
    "generate_entropy_exergy_term",
]


def generate_entropy_exergy_term(
    fluid: str,
    T: float,
    P: float,
    Q: float,
    T0: float,
    P0: float,
    phase: Literal["gas", "liquid", "twophase"] = "gas",
) -> tuple[float, float, float]:
    """Calculate entropy, enthalpy, and exergy.

    Parameters
    ----------
    fluid : str
        Fluid name.
    T : float
        Temperature [K].
    P : float
        Pressure [Pa].
    Q : float
        Quality (0 to 1).
    T0 : float
        Dead state temperature [K].
    P0 : float
        Dead state pressure [Pa].
    phase : Literal['gas', 'liquid', 'twophase'], optional
        Fluid phase. Default is 'gas'.

    Returns
    -------
    tuple[float, float, float]
        Entropy [J/kg-K], Enthalpy [J/kg], Exergy [J/kg].
    """
    if phase == "twophase":
        s = CP.PropsSI("S", "T", T, "Q", Q, fluid)
        h = CP.PropsSI("H", "T", T, "Q", Q, fluid)
    else:
        s = CP.PropsSI("S", "T", T, "P", P, fluid)
        h = CP.PropsSI("H", "T", T, "P", P, fluid)

    s0 = CP.PropsSI("S", "T", T0, "P", P0, fluid)
    h0 = CP.PropsSI("H", "T", T0, "P", P0, fluid)
    exergy = (h - h0) - T0 * (s - s0)

    return s, h, exergy


def calc_energy_flow(G, T, T0):
    """Calculate energy flow rate.

    Parameters
    ----------
    G : float or pd.Series
        Heat capacity flow rate (mass_flow * Cp) [W/K].
    T : float or pd.Series
        Current temperature [K].
    T0 : float or pd.Series
        Reference/dead state temperature [K].

    Returns
    -------
    float or pd.Series
        Energy flow rate [W].
    """
    return G * (T - T0)


def calc_exergy_flow(G, T, T0):
    """Calculate exergy flow rate.

    Parameters
    ----------
    G : float or pd.Series
        Heat capacity flow rate (mass_flow * Cp) [W/K].
    T : float or pd.Series
        Current temperature [K].
    T0 : float or pd.Series
        Reference/dead state temperature [K].

    Returns
    -------
    float or pd.Series
        Exergy flow rate [W].
    """
    import numpy as np
    import pandas as pd

    is_series = isinstance(T, pd.Series) or isinstance(T0, pd.Series) or isinstance(G, pd.Series)
    if is_series:
        # Vectorised path: mask rows where T <= 0 or T0 <= 0 to zero so the
        # log doesn't blow up on invalid inputs.
        invalid = (T <= 0) | (T0 <= 0)
        T_safe = np.where(T <= 0, 1.0, T)
        T0_safe = np.where(T0 <= 0, 1.0, T0)
        result = G * ((T_safe - T0_safe) - T0_safe * np.log(T_safe / T0_safe))
        if isinstance(result, pd.Series):
            return result.mask(invalid, 0.0)
        result = np.where(invalid, 0.0, result)
        return pd.Series(result, index=T.index if isinstance(T, pd.Series) else None)
    else:
        if T <= 0 or T0 <= 0:
            return 0.0
        return float(G * ((T - T0) - T0 * np.log(T / T0)))


def calc_refrigerant_exergy(
    df: "pd.DataFrame",
    ref: str,
    T0_K: "pd.Series",
    P0: float = 101325,
) -> "pd.DataFrame":
    """Calculate refrigerant state-point exergy using pre-computed properties.

    Uses the entropy (``s_ref_*``) and enthalpy (``h_ref_*``) columns
    already present in ``df`` (produced by ``calc_ref_state``) to compute
    specific exergy and exergy flow rate for each refrigerant state point.

    Dead-state properties (h0, s0) are evaluated at (T0, P0) for the
    given refrigerant using CoolProp (vectorized via unique T0 values).

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame containing pre-computed enthalpy ``h_ref_* [J/kg]``,
        entropy ``s_ref_* [J/(kg·K)]``, and mass-flow
        ``m_dot_ref [kg/s]`` columns.
    ref : str
        CoolProp refrigerant identifier (e.g. ``'R410A'``).
    T0_K : pd.Series
        Dead-state (environment) temperature per row [K].
    P0 : float
        Dead-state pressure [Pa] (default ``101325``).

    Returns
    -------
    pd.DataFrame
        ``df`` with columns added per state point:
        ``x_ref_{name} [J/kg]``, ``X_ref_{name} [W]``.

    Notes
    -----
    - Exergy equation: x = (h − h0) − T0·(s − s0)  [J/kg]
    - Exergy flow: X = ṁ · x  [W]
    - Rows with NaN enthalpy/entropy propagate NaN naturally.
    """
    import numpy as np
    import pandas as pd

    # State points: (name, enthalpy_col, entropy_col)
    _STATES = [
        ("cmp_in", "h_ref_cmp_in [J/kg]", "s_ref_cmp_in [J/(kg·K)]"),
        ("cmp_out", "h_ref_cmp_out [J/kg]", "s_ref_cmp_out [J/(kg·K)]"),
        ("exp_in", "h_ref_exp_in [J/kg]", "s_ref_exp_in [J/(kg·K)]"),
        ("exp_out", "h_ref_exp_out [J/kg]", "s_ref_exp_out [J/(kg·K)]"),
    ]

    # Dead-state properties — vectorized via unique T0 values
    t0_unique = T0_K.dropna().unique()
    h0_map: dict[float, float] = {}
    s0_map: dict[float, float] = {}
    for t0 in t0_unique:
        try:
            h0_map[t0] = CP.PropsSI("H", "T", float(t0), "P", P0, ref)
            s0_map[t0] = CP.PropsSI("S", "T", float(t0), "P", P0, ref)
        except Exception:
            h0_map[t0] = np.nan
            s0_map[t0] = np.nan

    h0: pd.Series = T0_K.map(h0_map)
    s0: pd.Series = T0_K.map(s0_map)

    m_dot: pd.Series = df["m_dot_ref [kg/s]"] if "m_dot_ref [kg/s]" in df.columns else pd.Series(np.nan, index=df.index)

    for name, h_col, s_col in _STATES:
        if h_col not in df.columns or s_col not in df.columns:
            continue
        h = df[h_col]
        s = df[s_col]
        # Specific exergy [J/kg]: x = (h - h0) - T0 * (s - s0)
        x_val = (h - h0) - T0_K * (s - s0)
        # Exergy flow [W]: X = m_dot * x
        X_val = m_dot * x_val
        df[f"x_ref_{name} [J/kg]"] = x_val
        df[f"X_ref_{name} [W]"] = X_val

    return df


def convert_electricity_to_exergy(
    df: "pd.DataFrame",
) -> "pd.DataFrame":
    """Copy all electricity columns (``E_*``) to exergy columns (``X_*``).

    Electrical energy is 100 %% pure exergy, so ``X = E`` for all
    electricity-consumption columns.

    The function searches for columns matching the pattern
    ``E_xxx [W]`` and creates corresponding ``X_xxx [W]`` columns.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with ``E_xxx [W]`` columns.

    Returns
    -------
    pd.DataFrame
        ``df`` with ``X_xxx [W]`` columns added.
    """
    for col in df.columns:
        if str(col).startswith("E_") and str(col).endswith(" [W]"):
            # E_cmp [W] → X_cmp [W]
            x_col: str = "X_" + str(col)[2:]
            df[x_col] = df[col]
    return df
