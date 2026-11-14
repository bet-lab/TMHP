"""
Simulation summary output functions.
"""

import math

import pandas as pd

from .dhw import DHW_REQUEST_KEY, DHW_UNMET_KEY, summarize_dhw_service
from .operating_limits import limit_for, summarize_operating_limits


def _running(df: pd.DataFrame, column: str, active_mask: pd.Series) -> pd.Series:
    """Values of a step-average rate column while the compressor runs.

    Dynamic results report heat, power and air flow averaged over each step
    (the running value times ``runtime_fraction [-]``); statistics of the
    running machine divide that back out. Frames without the column (steady
    results) are already running values.
    """
    values = df.loc[active_mask, column]
    if "runtime_fraction [-]" not in df.columns:
        return values
    fraction = df.loc[active_mask, "runtime_fraction [-]"].astype(float)
    return values / fraction.where(fraction > 0)


def _print_convergence_status(df: pd.DataFrame) -> None:
    """Print convergence statistics."""
    # 1. Convergence Status
    converged_all = bool(df["converged"].all())
    print(f"[Convergence Status] All converged: {converged_all}")
    if not converged_all:
        nonconverged_count = (~df["converged"]).sum()
        print(f"  - Non-converged steps: {nonconverged_count} / {len(df)}")
    print("-" * 50)


def _print_compressor_stats(df: pd.DataFrame, active_mask: pd.Series) -> None:
    """Print compressor RPM statistics."""
    # 2. Compressor Statistics
    cmp_rpm_active = df.loc[active_mask, "cmp_rpm [rpm]"]
    print("[Compressor Speed]")
    if not cmp_rpm_active.empty:
        print(f"  - Min: {cmp_rpm_active.min():.1f} rpm | Max: {cmp_rpm_active.max():.1f} rpm")
        print(f"  - Avg (active): {cmp_rpm_active.mean():.1f} rpm")
    else:
        print("  - No active data.")
    print("-" * 50)


def _print_fan_stats(
    df: pd.DataFrame, active_mask: pd.Series, dV_ou_a_design: float, simulation_time_step: int
) -> None:
    """Print fan flow rate, velocity, pressure, power, and efficiency ratio."""
    # 3. Fan Flow Rate Statistics
    fan_active = _running(df, "dV_ou_a [m3/s]", active_mask)
    print("[Fan Flow Rate]")
    if not fan_active.empty:
        fan_avg = fan_active.mean()
        fan_avg_pct = (fan_avg / dV_ou_a_design) * 100
        print(f"  - Min: {fan_active.min():.3f} m³/s | Max: {fan_active.max():.3f} m³/s")
        print(f"  - Avg: {fan_avg:.3f} m³/s ({fan_avg_pct:.1f}% of design)")
    else:
        print("  - No active data.")
    print("-" * 50)

    # 3-1. Fan Velocity & Pressure Statistics
    if "v_ou_a [m/s]" in df.columns:
        v_fan_active = df.loc[active_mask, "v_ou_a [m/s]"]
        print("[Fan Velocity]")
        if not v_fan_active.empty:
            print(f"  - Min: {v_fan_active.min():.2f} m/s | Max: {v_fan_active.max():.2f} m/s")
            print(f"  - Avg: {v_fan_active.mean():.2f} m/s")
        else:
            print("  - No active data.")
        print("-" * 50)

    if "dP_ou_fan_static [Pa]" in df.columns and "dP_ou_fan_dynamic [Pa]" in df.columns:
        dP_static = df.loc[active_mask, "dP_ou_fan_static [Pa]"]
        dP_dynamic = df.loc[active_mask, "dP_ou_fan_dynamic [Pa]"]

        print("[Fan Pressure (Static / Dynamic)]")
        if not dP_static.empty:
            print(
                f"  - Static  : Avg {dP_static.mean():.1f} Pa | Min {dP_static.min():.1f} Pa | Max {dP_static.max():.1f} Pa"
            )
            print(
                f"  - Dynamic : Avg {dP_dynamic.mean():.1f} Pa | Min {dP_dynamic.min():.1f} Pa | Max {dP_dynamic.max():.1f} Pa"
            )
        else:
            print("  - No active data.")
        print("-" * 50)

    # 4. Fan Power Statistics
    fan_p_active = _running(df, "E_ou_fan [W]", active_mask)
    print("[Fan Power Use]")
    if not fan_p_active.empty:
        print(f"  - Min: {fan_p_active.min():.1f} W | Max: {fan_p_active.max():.1f} W")
        print(f"  - Avg: {fan_p_active.mean():.1f} W")
    else:
        print("  - No active data.")
    print("-" * 50)

    # 5. System Efficiency Metrics
    total_fan_energy = df["E_ou_fan [W]"].sum() * simulation_time_step
    total_energy = df["E_tot [W]"].sum() * simulation_time_step
    fan_ratio = (total_fan_energy / total_energy * 100) if total_energy > 0 else 0
    print(f"[Fan Power Ratio] {fan_ratio:.1f}% (Typical: 5-10%)")
    print("-" * 50)


def _print_heat_exchange_stats(df: pd.DataFrame, active_mask: pd.Series) -> None:
    """Print heat exchanger temperature differences."""
    # 6. Heat Exchange Performance: Outdoor Air
    if "T_ou_a_in [°C]" in df.columns and "T_ou_a_out [°C]" in df.columns:
        print("[Outdoor Air Temperature Difference (In - Out)]")
        if active_mask.any():
            delta_T = df.loc[active_mask, "T_ou_a_in [°C]"] - df.loc[active_mask, "T_ou_a_out [°C]"]
            print(f"  - Avg Delta T: {delta_T.mean():.2f} K | Max Delta T: {delta_T.max():.2f} K")
        else:
            print("  - No active data.")
        print("-" * 50)

    # 7. Heat Exchange Performance: Temp Differences
    print("[Heat Exchanger Temperature Differences]")

    # Condenser (T_cond - T_tank_w)
    if "T_ref_cond_sat_l [°C]" in df.columns and "T_tank_w [°C]" in df.columns:
        T_cond = df.loc[active_mask, "T_ref_cond_sat_l [°C]"]
        T_tank_w = df.loc[active_mask, "T_tank_w [°C]"]

        if not T_cond.empty and not T_tank_w.empty:
            dT_cond = T_cond - T_tank_w
            print(
                f"  - Condenser (T_cond - T_tank) Avg: {dT_cond.mean():.2f} K | Min: {dT_cond.min():.2f} K | Max: {dT_cond.max():.2f} K"
            )
        else:
            print("  - Condenser: No data")

    # Evaporator (T_air_in - T_evap) & (T_air_in - T_air_out)
    if "T_ou_a_in [°C]" in df.columns and "T_ref_evap_sat [°C]" in df.columns and "T_ou_a_out [°C]" in df.columns:
        T_air_in = df.loc[active_mask, "T_ou_a_in [°C]"]
        T_evap_sat = df.loc[active_mask, "T_ref_evap_sat [°C]"]
        T_air_out = df.loc[active_mask, "T_ou_a_out [°C]"]

        if not T_air_in.empty:
            dT_evap_drive = T_air_in - T_evap_sat
            dT_air_drop = T_air_in - T_air_out

            print(f"  - Evap Drive (T_air_in - T_evap) Avg: {dT_evap_drive.mean():.2f} K")
            print(f"  - Air Drop (T_air_in - T_air_out) Avg: {dT_air_drop.mean():.2f} K")
        else:
            print("  - Evaporator: No data")


def print_dhw_service_summary(df: pd.DataFrame, simulation_time_step: float) -> None:
    """Print the hot-water service of a boiler dynamic result.

    Requested, delivered and unmet hot-water heat [kWh], the number of draw
    steps left short and the coldest delivery
    (:func:`tmhp.dhw.summarize_dhw_service`). Works on the result of any
    boiler model or combination; prints nothing for a frame without
    ``Q_dhw_request [W]`` / ``Q_dhw_unmet [W]``.

    Parameters
    ----------
    df : pd.DataFrame
        Simulation result DataFrame.
    simulation_time_step : float
        Time step [s].
    """
    if DHW_REQUEST_KEY not in df.columns or DHW_UNMET_KEY not in df.columns:
        return
    s = summarize_dhw_service(df, simulation_time_step)
    print("[Hot Water Service]")
    print(f"  - Requested: {s['Q_dhw_request [kWh]']:.2f} kWh | Delivered: {s['Q_dhw_delivered [kWh]']:.2f} kWh")
    print(
        f"  - Unmet: {s['Q_dhw_unmet [kWh]']:.2f} kWh ({100.0 * s['dhw_unmet_fraction [-]']:.1f}%) "
        f"on {s['dhw_unmet_steps [-]']} of {s['dhw_draw_steps [-]']} draw steps"
    )
    if math.isfinite(s["T_mix_w_out_min [°C]"]):
        print(f"  - Coldest delivery: {s['T_mix_w_out_min [°C]']:.1f} °C")
    print("-" * 50)


def print_operating_limit_summary(
    df: pd.DataFrame, simulation_time_step: float, T_tank_w_lower_bound: float | None = None
) -> None:
    """Print what the operating limits did to a dynamic result.

    The steps on which the compressor was called but did not run, by
    ``failure_reason``, the steps on which it ran below the request, by
    ``capacity_binding``, and what that left unserved
    (:func:`tmhp.operating_limits.summarize_operating_limits`), followed by
    what to do about each limit. Works on the result of any model; prints
    nothing for a frame without ``failure_reason``.

    Parameters
    ----------
    df : pd.DataFrame
        Simulation result DataFrame.
    simulation_time_step : float
        Time step [s].
    T_tank_w_lower_bound : float | None
        Lower thermostat set point of a boiler [°C], for the time the tank
        spent below it. ``None``: not printed.
    """
    if "failure_reason" not in df.columns:
        return
    s = summarize_operating_limits(df, simulation_time_step, T_tank_w_lower_bound=T_tank_w_lower_bound)
    steps = s["steps [-]"]
    print("[Operating Limits]")
    print(f"  - Called but did not run: {s['off_steps [-]']} / {steps} steps ({s['off_time [h]']:.1f} h)")
    for reason, entry in s["off_reasons"].items():
        print(f"      {reason}: {entry['steps [-]']} steps, first at {entry['first_time [s]'] / 3600.0:.2f} h")
    print(f"  - Ran below the request: {s['limited_steps [-]']} / {steps} steps ({s['limited_time [h]']:.1f} h)")
    for binding, entry in s["limited_bindings"].items():
        print(f"      {binding}: {entry['steps [-]']} steps, first at {entry['first_time [s]'] / 3600.0:.2f} h")
    if s["kind"] == "space":
        print(
            f"  - Unmet load: {s['Q_unmet [kWh]']:.2f} kWh of {s['Q_request [kWh]']:.2f} kWh "
            f"({100.0 * s['unmet_fraction [-]']:.1f}%)"
        )
    else:
        print(f"  - Heat short of the requested duty while limited: {s['Q_below_request [kWh]']:.2f} kWh")
        line = f"  - Tank minimum: {s['T_tank_w_min [°C]']:.1f} °C"
        if math.isfinite(s["time_below_lower_setpoint [h]"]):
            line += f" | Below the lower set point: {s['time_below_lower_setpoint [h]']:.1f} h"
        print(line)
    for reason in s["off_reasons"]:
        print(f"  - {reason}: {limit_for(reason=reason).remedy}")
    for binding in s["limited_bindings"]:
        print(f"  - {binding}: {limit_for(binding=binding).remedy}")
    print("-" * 50)


def print_simulation_summary(df: pd.DataFrame, simulation_time_step: int, dV_ou_a_design: float) -> None:
    """Print a comprehensive summary of simulation results.

    A boiler result also gets the hot-water service section
    (:func:`print_dhw_service_summary`): requested, delivered and unmet heat
    and the number of draw steps left short. A result with a
    ``failure_reason`` column gets the operating-limit section
    (:func:`print_operating_limit_summary`): steps on which the compressor
    was called but did not run, or ran below the request.

    Parameters
    ----------
    df : pd.DataFrame
        Simulation result DataFrame.
    simulation_time_step : int
        Time step [s].
    dV_ou_a_design : float
        Design airflow rate of outdoor unit [m3/s].
    """
    if df.empty:
        print("Empty DataFrame provided.")
        return

    required_columns = ["converged", "E_ou_fan [W]", "E_tot [W]", "dV_ou_a [m3/s]", "cmp_rpm [rpm]"]
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        raise KeyError(f"Required columns not found in DataFrame: {missing_columns}")

    active_mask = df["cmp_rpm [rpm]"] > 0

    print("=" * 50)
    _print_convergence_status(df)
    _print_compressor_stats(df, active_mask)
    _print_fan_stats(df, active_mask, dV_ou_a_design, simulation_time_step)
    _print_heat_exchange_stats(df, active_mask)
    if DHW_REQUEST_KEY in df.columns and DHW_UNMET_KEY in df.columns:
        print("-" * 50)
        print_dhw_service_summary(df, simulation_time_step)
    if "failure_reason" in df.columns:
        if not (DHW_REQUEST_KEY in df.columns and DHW_UNMET_KEY in df.columns):
            print("-" * 50)
        print_operating_limit_summary(df, simulation_time_step)
    print("=" * 50)
