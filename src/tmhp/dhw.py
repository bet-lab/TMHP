"""
Domestic Hot Water (DHW) load modeling.
"""

import numpy as np
import pandas as pd

from . import calc_util as cu

__all__ = [
    "build_dhw_usage_ratio",
    "calc_cold_water_temp",
    "calc_total_water_use_from_schedule",
    "make_dhw_schedule_from_Annex_42_profile",
]


def make_dhw_schedule_from_Annex_42_profile(
    flow_rate_array: np.ndarray, df_time_step: int, simulation_time_step: int
) -> list[tuple[str, str, float]]:
    """Generate DHW schedule list from flow profile data.

    This function implements the logic to convert a flow profile (L/min)
    into a schedule list with specified time step.

    Parameters
    ----------
    flow_rate_array : np.ndarray
        Array of flow rates [L/min]. Data should represent 24 hours.
    df_time_step : int
        Time step of the input ``flow_rate_array`` [min].
    simulation_time_step : int
        Target time step for the simulation schedule [s].

    Returns
    -------
    list[tuple[str, str, float]]
        List of tuples (start_time, end_time, fraction).
    """
    total_minutes = len(flow_rate_array) * df_time_step
    if total_minutes != 1440:
        raise ValueError(f"Input profile must cover exactly 24 hours (got {total_minutes} min)")

    peak_flow = np.max(flow_rate_array)
    schedule = []

    sim_step_min = simulation_time_step / 60
    num_sim_steps = int(1440 / sim_step_min)

    for i in range(num_sim_steps):
        start_min = i * sim_step_min
        end_min = (i + 1) * sim_step_min

        # Original logic averagng
        start_idx = int(start_min / df_time_step)
        end_idx = int(end_min / df_time_step)

        avg_flow = flow_rate_array[start_idx] if start_idx == end_idx else np.mean(flow_rate_array[start_idx:end_idx])

        frac = float(avg_flow / peak_flow) if peak_flow > 0 else 0.0

        start_h = int(start_min // 60)
        start_m = int(start_min % 60)
        end_h = int(end_min // 60)
        end_m = int(end_min % 60)

        start_str = f"{start_h:02d}:{start_m:02d}"
        end_str = f"{end_h:02d}:{end_m:02d}"
        if end_h == 24:
            end_str = "24:00"

        if frac > 0:
            schedule.append((start_str, end_str, frac))

    return schedule


def calc_total_water_use_from_schedule(
    schedule: list[tuple[str, str, float]],
    peak_load_m3s: float,
    info: bool = True,
    info_unit: str = "L",
) -> float:
    """Calculate total daily water use from a schedule.

    Parameters
    ----------
    schedule : list[tuple[str, str, float]]
        Schedule list. Each item is (start_str, end_str, ratio) format.
    peak_load_m3s : float
        Peak load water flow rate [m3/s].
    info : bool, optional
        Flag to print info. Default is True.
    info_unit : str, optional
        Unit to print info. Default is 'L'.

    Returns
    -------
    float
        Total daily water usage [m3].
    """

    def _time_to_min(t_str: str) -> float:
        parts = t_str.split(":")
        return float(parts[0]) * 60 + (float(parts[1]) if len(parts) > 1 else 0)

    total_m3 = 0.0
    for start, end, frac in schedule:
        s_min = _time_to_min(start)
        e_min = _time_to_min(end)
        dt_s = (e_min - s_min) * 60
        total_m3 += peak_load_m3s * frac * dt_s

    if info:
        if info_unit == "L":
            print(f"Total water use: {total_m3 * cu.m32L:.1f} L/day")
        else:
            print(f"Total water use: {total_m3:.3f} m3/day")

    return total_m3


def calc_cold_water_temp(df: pd.DataFrame, target_date_str: str) -> float:
    """Calculate mains water temperature using EnergyPlus algorithm.

    Uses monthly average outdoor air temperature to estimate the water mains
    temperature based on the algorithm from Hendron et al. (2004).

    References
    ----------
    Hendron, R., Anderson, R., Judkoff, R., Christensen, C., Eastment, M., & Norton, P. (2004).
    Building America Performance Analysis Procedures for Energy-Efficient Residential Buildings.
    National Renewable Energy Laboratory (NREL). https://doi.org/10.2172/15011400

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with monthly average temperatures. Must have 'month' and 'T_avg' columns.
    target_date_str : str
        Target date string in 'YYYY-MM-DD' format.

    Returns
    -------
    float
        Calculated cold water temperature [degC].
    """
    # Dynamic temperature-column lookup. The Korean keywords ('기온') are kept
    # so KMA (Korea Meteorological Administration) CSVs match out of the box
    # alongside English column names (T_avg, temp, T0, °C, ℃).
    _TEMP_PATTERNS = ["t_avg", "기온", "temp", "t0", "°c", "℃"]
    temp_col: str | None = None
    for pat in _TEMP_PATTERNS:
        matched = df.columns[df.columns.str.lower().str.contains(pat.lower())]
        if len(matched) > 0:
            temp_col = str(matched[0])
            break
    if temp_col is None:
        raise ValueError(
            f"calc_cold_water_temp: could not find a temperature column. "
            f"Columns present: {df.columns.tolist()}. "
            f"A column whose name contains one of "
            f"{_TEMP_PATTERNS} is required."
        )

    T_out_avg = df[temp_col].mean()
    T_maxdiff = (df[temp_col].max() - df[temp_col].min()) / 2

    target_date = pd.to_datetime(target_date_str)
    day_of_year = target_date.dayofyear

    # EnergyPlus mains water temperature correlation
    ratio = 0.4 + 0.01 * (T_out_avg - 4.4)
    lag = 35 - 1.0 * (T_out_avg - 4.4)

    T_mains = T_out_avg + ratio * T_maxdiff * np.sin(2 * np.pi * (day_of_year / 365 - lag / 365 - 0.25))

    return float(T_mains)


def build_dhw_usage_ratio(entries: list[tuple[str, str, float]], t_array: np.ndarray) -> np.ndarray:
    """Build schedule ratio array from schedule entries for each timestep.

    Parameters
    ----------
    entries : list[tuple[str, str, float]]
        Schedule entry list. Each item is (start_str, end_str, frac) format.
    t_array : np.ndarray
        Array of time seconds from start of day.

    Returns
    -------
    np.ndarray
        Array of fractions corresponding to ``t_array``.
    """

    def _to_sec(t_str: str) -> int:
        parts = t_str.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        s = int(parts[2]) if len(parts) > 2 else 0
        return h * cu.h2s + m * cu.m2s + s

    ratio = np.zeros_like(t_array, dtype=float)

    for start, end, frac in entries:
        s_sec = _to_sec(start)
        e_sec = _to_sec(end)

        mask = (t_array >= s_sec) & (t_array < e_sec)
        ratio[mask] = np.maximum(ratio[mask], frac)

    return ratio
