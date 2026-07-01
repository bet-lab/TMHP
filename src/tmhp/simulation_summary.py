"""
Simulation summary output functions.
"""

import pandas as pd


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
    fan_active = df.loc[active_mask, "dV_ou_a [m3/s]"]
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
    fan_p_active = df.loc[active_mask, "E_ou_fan [W]"]
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


def print_simulation_summary(df: pd.DataFrame, simulation_time_step: int, dV_ou_a_design: float) -> None:
    """Print a comprehensive summary of simulation results.

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
    print("=" * 50)
