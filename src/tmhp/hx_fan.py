"""Air-side fan UA and power utility functions.

Airflow-based correlations for the outdoor-coil heat exchanger: a
velocity-dependent UA scaling law (``calc_UA_from_dV_fan``) and a fan power
curve (``calc_fan_power_from_dV_fan``). HX performance solving lives in
``enex_functions`` and HP schedule checking in ``dynamic_context``.
"""

from __future__ import annotations

import numpy as np

from .heat_exchanger import calc_UA_from_dV_fan as calc_UA_from_dV_fan

ASHRAE_VSD_COEFFICIENTS = dict(c1=0.0013, c2=0.1470, c3=0.9506, c4=-0.0998, c5=0.0)


def is_generic_fan_curve(vsd_coeffs: dict | None) -> bool:
    """Identify the unmodified ASHRAE Appendix G Method 2 correlation."""
    coefficients = vsd_coeffs or {}
    return coefficients.get("curve_type") != "custom" and all(
        coefficients.get(k, v) == v for k, v in ASHRAE_VSD_COEFFICIENTS.items()
    )


def calc_ashrae_fan_power_ratio(flow_ratio: float) -> float:
    """Raw Appendix G Method 2 fit for table parity, not a flow controller.

    Table G3.1.3.15 Method 1 spans 0--1. Its x=0.1 row can be compared
    mathematically, but generic operating control separately enforces x>=0.15.
    Source: https://www.ashrae.org/file%20library/technical%20resources/standards%20and%20guidelines/standards%20addenda/90.1-2016/90_1_2016_be_bm_bn_bo_bp_br_bs_bu_bv_cf_cl_cm_cq_ct_cu_cv_cw_cy_20210324.pdf
    """
    if not np.isfinite(flow_ratio) or not 0 <= flow_ratio <= 1:
        raise ValueError("Raw ASHRAE fit requires 0 <= airflow/reference <= 1")
    x = flow_ratio
    return float(0.0013 + 0.1470 * x + 0.9506 * x**2 - 0.0998 * x**3)


def calc_fan_operating_point(
    dV_fan: float,
    fan_params: dict,
    vsd_coeffs: dict | None,
    is_active: bool = True,
) -> dict:
    """Clamp fan flow to its model/control limits, then compute power.

    Source: ANSI/ASHRAE/IES Standard 90.1-2016, Appendix G,
    Table G3.1.3.15, Method 2:
    P* = 0.0013 + 0.1470*x + 0.9506*x**2 - 0.0998*x**3.
    x = actual airflow / fixed reference airflow. The generic correlation is
    restricted to 0.15 <= x <= 1.0. ASHRAE 2025 Fundamentals Chapter 19
    cautions against extrapolation below a minimum ratio (example 0.15).
    90.1-2022 Addendum u is supporting multizone-VAV turndown evidence;
    its informative foreword discusses 16% power at 15% airflow, not a
    normative 16% requirement or a universal electrical floor. The amended
    body 6.5.3.2.1(b) removes the old 30% power sentence without adding 16%.
    Its airflow turndown provision includes the design minimum outdoor air.
    Evidence: references/fan_model/01_ashrae_90_1_fan_curve.png through
    04_ashrae_addendum_u_normative.png, captured in Windows Chrome via CDP.
    Generic 0.15--1.0 is a TMHP assumption, not a heat-pump requirement.
    Nondefault user coefficients define a custom curve with explicit limits.
    A raw demand is clamped here; HX solvers must separately close heat duty.

    Parameters
    ----------
    dV_fan : float
        Current flow rate [m³/s].
    fan_params : dict
        Must contain ``fan_design_flow_rate`` and ``fan_design_power``.
    vsd_coeffs : dict
        VSD Curve coefficients (``c1`` through ``c5``).
    is_active : bool
        If False, reports zero actual flow and ``np.nan`` power.

    Returns
    -------
    dict
        Actual flow, ratio, power [W] and min/max limit flags.
    """
    if not is_active:
        return dict(
            power_W=np.nan, actual_flow=0.0, flow_ratio_to_ref=0.0, fan_flow_min_limit=False, fan_flow_max_limit=False
        )
    # A failed HX candidate reports NaN airflow; preserve that diagnostic so
    # the cycle optimizer can reject it without raising an input exception.
    if np.isnan(dV_fan):
        return dict(
            power_W=np.nan,
            actual_flow=np.nan,
            flow_ratio_to_ref=np.nan,
            fan_flow_min_limit=False,
            fan_flow_max_limit=False,
        )

    fan_design_flow_rate = fan_params.get(
        "fan_ref_flow_rate", fan_params.get("fan_rated_flow_rate", fan_params.get("fan_design_flow_rate"))
    )
    fan_design_power = fan_params.get(
        "fan_ref_power", fan_params.get("fan_rated_power", fan_params.get("fan_design_power"))
    )

    if fan_design_flow_rate is None or fan_design_power is None:
        raise ValueError(
            "fan_rated_flow_rate/fan_design_flow_rate and fan_rated_power/fan_design_power must be provided in fan_params"
        )

    if dV_fan < 0:
        raise ValueError("fan flow rate must be greater than 0")
    if (
        not np.isfinite(dV_fan)
        or not np.isfinite(fan_design_flow_rate)
        or fan_design_flow_rate <= 0
        or not np.isfinite(fan_design_power)
        or fan_design_power < 0
    ):
        raise ValueError("Fan flow/reference must be finite and reference positive; reference power nonnegative")

    from .heat_exchanger import resolve_fan_flow_limits

    coefficients = vsd_coeffs or {}
    low, high = resolve_fan_flow_limits(
        fan_design_flow_rate,
        fan_params.get("fan_min_flow_rate"),
        fan_params.get("fan_max_flow_rate"),
        custom_curve=not is_generic_fan_curve(coefficients),
    )
    actual = min(max(dV_fan, low), high)
    c1 = coefficients.get("c1", 0.0013)
    c2 = coefficients.get("c2", 0.1470)
    c3 = coefficients.get("c3", 0.9506)
    c4 = coefficients.get("c4", -0.0998)
    c5 = coefficients.get("c5", 0.0)

    x = actual / fan_design_flow_rate  # fixed normalization; custom limits may exceed reference
    PLR = c1 + c2 * x + c3 * x**2 + c4 * x**3 + c5 * x**4
    PLR = max(0.0, PLR)

    return dict(
        power_W=float(fan_design_power * PLR),
        actual_flow=actual,
        flow_ratio_to_ref=x,
        fan_flow_min_limit=dV_fan <= low,
        fan_flow_max_limit=dV_fan >= high,
        flow_min=low,
        flow_max=high,
    )


def calc_fan_power_from_dV_fan(
    dV_fan: float,
    fan_params: dict,
    vsd_coeffs: dict | None,
    is_active: bool = True,
) -> float:
    """Electrical power [W] at the bounded fan operating point.

    ASHRAE Appendix G Method 2 uses its unchanged empirical coefficients
    only over 0.15--1.0 of reference airflow. See calc_fan_operating_point
    for actual flow and limit flags. No arbitrary electrical power floor.
    """
    return float(calc_fan_operating_point(dV_fan, fan_params, vsd_coeffs, is_active)["power_W"])
