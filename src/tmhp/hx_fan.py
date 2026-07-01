"""Heat exchanger and fan utility functions.

Functions for velocity-dependent UA calculation, HX performance
solving, fan power curves, and HP schedule checking.
"""

from __future__ import annotations

import numpy as np


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


def calc_fan_power_from_dV_fan(
    dV_fan: float,
    fan_params: dict,
    vsd_coeffs: dict,
    is_active: bool = True,
) -> float:
    """Calculate fan power using ASHRAE 90.1 VSD Curve.

    Parameters
    ----------
    dV_fan : float
        Current flow rate [m³/s].
    fan_params : dict
        Must contain ``fan_design_flow_rate`` and ``fan_design_power``.
    vsd_coeffs : dict
        VSD Curve coefficients (``c1`` through ``c5``).
    is_active : bool
        If False, returns ``np.nan``.

    Returns
    -------
    float
        Fan power [W].
    """
    if not is_active:
        return np.nan

    fan_design_flow_rate = fan_params.get("fan_rated_flow_rate", fan_params.get("fan_design_flow_rate"))
    fan_design_power = fan_params.get("fan_rated_power", fan_params.get("fan_design_power"))

    if fan_design_flow_rate is None or fan_design_power is None:
        raise ValueError(
            "fan_rated_flow_rate/fan_design_flow_rate and fan_rated_power/fan_design_power must be provided in fan_params"
        )

    if dV_fan < 0:
        raise ValueError("fan flow rate must be greater than 0")

    c1 = vsd_coeffs.get("c1", 0.0013)
    c2 = vsd_coeffs.get("c2", 0.1470)
    c3 = vsd_coeffs.get("c3", 0.9506)
    c4 = vsd_coeffs.get("c4", -0.0998)
    c5 = vsd_coeffs.get("c5", 0.0)

    x = dV_fan / fan_design_flow_rate
    PLR = c1 + c2 * x + c3 * x**2 + c4 * x**3 + c5 * x**4
    PLR = max(0.0, PLR)

    return float(fan_design_power * PLR)
