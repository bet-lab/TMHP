"""Air-side fan UA and power utility functions.

Airflow-based correlations for the outdoor-coil heat exchanger: a
velocity-dependent UA scaling law (``calc_UA_from_dV_fan``) and a fan power
curve (``calc_fan_power_from_dV_fan``). HX performance solving lives in
``enex_functions`` and HP schedule checking in ``dynamic_context``.
"""

from __future__ import annotations

import numpy as np

from .heat_exchanger import calc_UA_from_dV_fan as calc_UA_from_dV_fan


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
