"""UV water treatment utility functions.

Functions for UV lamp power scheduling, turbidity-based parameter
lookup, and required exposure time calculation (Radial Model).
"""

from __future__ import annotations

import math

from . import calc_util as cu


def calc_uv_lamp_power(
    current_time_s: float,
    period_sec: float,
    num_switching: int,
    exposure_sec: float,
    lamp_watts: float,
) -> float:
    """Calculate UV lamp power at a given time instant.

    The lamp switches on ``num_switching`` times per ``period_sec``,
    each activation lasting ``exposure_sec``.

    Parameters
    ----------
    current_time_s : float
        Current simulation time [s].
    period_sec : float
        Switching period (e.g. 3 h → 10800 s).
    num_switching : int
        Number of on-cycles per period.
    exposure_sec : float
        Duration of each on-cycle [s].
    lamp_watts : float
        Rated lamp power [W].

    Returns
    -------
    float
        Instantaneous lamp power [W] (0 or ``lamp_watts``).
    """
    if num_switching <= 0 or lamp_watts <= 0:
        return 0.0

    time_in_period = current_time_s % period_sec
    interval = (period_sec - num_switching * exposure_sec) / (num_switching + 1)
    for i in range(num_switching):
        start_time = interval * (i + 1) + i * exposure_sec
        if start_time <= time_in_period < start_time + exposure_sec:
            return lamp_watts
    return 0.0


def get_uv_params_from_turbidity(turbidity_ntu: float) -> dict:
    """Return UV parameters from a turbidity lookup table.

    Table data based on *Table 1. Effect of Turbidity on UVT, UV
    Absorbance, UV Intensity, and Exposure Time*.

    Parameters
    ----------
    turbidity_ntu : float
        Turbidity value [NTU].

    Returns
    -------
    dict
        Keys: ``uv_absorbance``, ``uv_transmittance_percent``,
        ``reference_intensity_mw_cm2``, ``reference_exposure_time_sec``.
    """
    # [Turbidity (NTU), % UVT, A254, Intensity (mW/cm²), Exposure (s)]
    turbidity_table = [
        [0.25, 86, 0.07, 0.40, 12.4],
        [5.0, 78, 0.11, 0.39, 12.8],
        [10.0, 71, 0.15, 0.36, 13.9],
        [20.1, 59, 0.23, 0.33, 15.0],
    ]

    turbidity_values = [row[0] for row in turbidity_table]

    def _row_to_dict(row) -> dict[str, float]:
        return {
            "uv_absorbance": row[2],
            "uv_transmittance_percent": row[1],
            "reference_intensity_mw_cm2": row[3],
            "reference_exposure_time_sec": row[4],
        }

    if turbidity_ntu <= turbidity_values[0]:
        return _row_to_dict(turbidity_table[0])
    if turbidity_ntu >= turbidity_values[-1]:
        return _row_to_dict(turbidity_table[-1])

    # Linear interpolation
    for i in range(len(turbidity_values) - 1):
        if turbidity_values[i] <= turbidity_ntu < turbidity_values[i + 1]:
            t1, t2 = turbidity_values[i], turbidity_values[i + 1]
            row1, row2 = turbidity_table[i], turbidity_table[i + 1]
            ratio = (turbidity_ntu - t1) / (t2 - t1)
            return {
                "uv_absorbance": row1[2] + ratio * (row2[2] - row1[2]),
                "uv_transmittance_percent": row1[1] + ratio * (row2[1] - row1[1]),
                "reference_intensity_mw_cm2": row1[3] + ratio * (row2[3] - row1[3]),
                "reference_exposure_time_sec": row1[4] + ratio * (row2[4] - row1[4]),
            }

    # Exact match fallback
    for i, t_val in enumerate(turbidity_values):
        if abs(turbidity_ntu - t_val) < 1e-6:
            return _row_to_dict(turbidity_table[i])

    return _row_to_dict(turbidity_table[0])


def calc_uv_exposure_time(
    radius_cm: float,
    uvc_output_W: float,
    lamp_arc_length_cm: float,
    target_dose_mj_cm2: float = 186,
    turbidity_ntu: float = 0.25,
) -> float:
    """Calculate required UV lamp exposure time via Radial Model.

    Reference: ADA453967.pdf — Radial Model for UV disinfection.

    Parameters
    ----------
    radius_cm : float
        Tank radius (lamp-to-wall distance) [cm].
    uvc_output_W : float
        UV-C output power of the lamp [W].
    lamp_arc_length_cm : float
        Arc length of the lamp [cm].
    target_dose_mj_cm2 : float
        Target germicidal dose [mJ/cm²]. Default 186 (EPA 4-log virus).
    turbidity_ntu : float
        Water turbidity [NTU].

    Returns
    -------
    float
        Required single-exposure time [min].
    """
    uv_params = get_uv_params_from_turbidity(turbidity_ntu)
    uv_absorbance = uv_params["uv_absorbance"]
    absorption_coeff = 2.303 * uv_absorbance

    # Linear power density [mW/cm]
    p_l_mw_cm = (uvc_output_W * cu.W2mW) / lamp_arc_length_cm

    # Intensity at tank wall [mW/cm²]
    intensity_mw_cm2 = (p_l_mw_cm / (2 * math.pi * radius_cm)) * math.exp(-absorption_coeff * radius_cm)

    required_time_sec = target_dose_mj_cm2 / intensity_mw_cm2
    return required_time_sec / 60
