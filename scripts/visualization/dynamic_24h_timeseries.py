"""24-hour dynamic simulation timeseries for the ASHPB.

Runs a one-day ``analyze_dynamic`` with a realistic synthetic DHW
profile (morning + evening peaks) and a sinusoidal outdoor temperature.
Plots three stacked panels sharing the same time axis:

    a) tank water temperature with set-point bounds
    b) condenser heat rate and compressor electrical power
    c) instantaneous and running system COP

This is the canonical "what does a dynamic run produce?" figure for
the getting-started page.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np

from tmhp import AirSourceHeatPumpBoiler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dmpl_common import COLORS, GRIDLINE, apply_style, finalize, record_figure, static_path  # noqa: E402

SIM_HOURS = 24
DT_S = 60  # 1-minute step
N_STEPS = SIM_HOURS * 3600 // DT_S
T_INIT_C = 50.0  # start mid-band so the heat-up transient is short


def _dhw_profile(n: int) -> np.ndarray:
    """Synthetic DHW draw profile [m³/s] over a 24 h horizon."""
    t_h = np.arange(n) * DT_S / 3600.0

    # Two gaussian-shaped peaks: 07:00 shower, 20:00 dishes/shower.
    def peak(center_h: float, sigma_h: float, peak_lpm: float) -> np.ndarray:
        m3s_peak = peak_lpm / 60_000.0
        return m3s_peak * np.exp(-0.5 * ((t_h - center_h) / sigma_h) ** 2)

    return cast(np.ndarray, peak(7.0, 0.35, 12.0) + peak(20.0, 0.55, 8.0))


def _ambient_profile(n: int) -> np.ndarray:
    """Sinusoidal outdoor air [°C] — min at 06:00, max at 15:00."""
    t_h = np.arange(n) * DT_S / 3600.0
    return 7.0 + 6.0 * np.sin((t_h - 9.0) * np.pi / 12.0)


def simulate():
    """The documented call, run as the getting-started page shows it: library defaults.

    The figure illustrates what ``AirSourceHeatPumpBoiler(ref="R32")`` does, so
    it follows the defaults on purpose (on-off operation below the minimum
    capacity, frost and defrost, the condensing-temperature cap) and is
    redrawn when they change; ``tests/test_study_results_current.py`` checks
    the recorded daily totals against a fresh run.
    """
    ashpb = AirSourceHeatPumpBoiler(ref="R32")
    df = ashpb.analyze_dynamic(
        simulation_period_sec=SIM_HOURS * 3600,
        dt_s=DT_S,
        T_tank_w_init_C=T_INIT_C,
        dhw_usage_schedule=_dhw_profile(N_STEPS),
        T0_schedule=_ambient_profile(N_STEPS),
    )
    return ashpb, df


def daily_totals(df) -> dict:
    """What the record keeps of a run: daily heat and electricity [kWh], end tank temperature."""
    dt_h = DT_S / 3600.0
    return {
        "Q_ref_tank_kWh": float(df["Q_ref_tank [W]"].fillna(0.0).sum() * dt_h / 1000.0),
        "E_cmp_kWh": float(df["E_cmp [W]"].fillna(0.0).sum() * dt_h / 1000.0),
        "E_tot_kWh": float(df["E_tot [W]"].fillna(0.0).sum() * dt_h / 1000.0),
        "T_tank_w_end_C": float(df["T_tank_w [°C]"].iloc[-1]),
        "hp_on_steps": int(df["hp_is_on"].fillna(False).astype(bool).sum()),
    }


def main() -> None:
    apply_style("report", hashsalt="tmhp.visualization.dynamic-24h")

    ashpb, df = simulate()

    t_h = np.arange(len(df)) * DT_S / 3600.0
    q_cond_kw = df["Q_ref_tank [W]"].to_numpy() / 1_000.0
    e_cmp_kw = df["E_cmp [W]"].to_numpy() / 1_000.0
    t_tank = df["T_tank_w [°C]"].to_numpy()

    # Running COP: cumulative Q_cond / cumulative E_cmp. Suppress the very
    # first samples where the denominator is zero or near-zero.
    cum_q = np.cumsum(q_cond_kw) * (DT_S / 3600.0)  # kWh
    cum_e = np.cumsum(e_cmp_kw) * (DT_S / 3600.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        cop_running = np.where(cum_e > 0.05, cum_q / cum_e, np.nan)
    cop_inst = df["cop_sys [-]"].to_numpy()

    fig, (ax_t, ax_p, ax_c) = plt.subplots(
        3,
        1,
        figsize=dm.figsize("15cm", 0.85),
        sharex=True,
    )
    _panels: list = []

    # --- (a) tank temperature ------------------------------------------
    ax_t.plot(t_h, t_tank, color=COLORS["hot"], linewidth=dm.lw(1), label="$T_{\\mathrm{tank,w}}$")
    ax_t.axhline(ashpb.T_tank_w_upper_bound, color=COLORS["muted"], linewidth=dm.lw(0), linestyle=(0, (3, 3)))
    ax_t.axhline(ashpb.T_tank_w_lower_bound, color=COLORS["muted"], linewidth=dm.lw(0), linestyle=(0, (3, 3)))
    # Bound labels ride symmetrically on each dashed line — ``va="bottom"``
    # for the upper line so the text sits above it; ``va="top"`` for the
    # lower line so it hangs below. Same ±0.6 °C offset on both sides
    # keeps the label clear of the dashed line without floating off.
    ax_t.text(
        0.5,
        ashpb.T_tank_w_upper_bound + 0.6,
        f"upper {ashpb.T_tank_w_upper_bound:.0f} °C",
        color=COLORS["muted"],
        fontsize=dm.fs(-2),
        va="bottom",
        ha="left",
    )
    ax_t.text(
        0.5,
        ashpb.T_tank_w_lower_bound - 0.6,
        f"lower {ashpb.T_tank_w_lower_bound:.0f} °C",
        color=COLORS["muted"],
        fontsize=dm.fs(-2),
        va="top",
        ha="left",
    )
    ax_t.set_ylabel("Tank water temp [°C]")
    _panels.append((ax_t, "a"))

    # --- (b) heat + power ---------------------------------------------
    ax_p.plot(t_h, q_cond_kw, color=COLORS["hot"], linewidth=dm.lw(0), label="$\\dot Q_{\\mathrm{cond}}$")
    ax_p.plot(t_h, e_cmp_kw, color=COLORS["accent"], linewidth=dm.lw(0), label="$E_{\\mathrm{cmp}}$")
    ax_p.fill_between(t_h, 0, q_cond_kw, color=COLORS["hot"], alpha=0.18, linewidth=0)
    ax_p.fill_between(t_h, 0, e_cmp_kw, color=COLORS["accent"], alpha=0.22, linewidth=0)
    ax_p.set_ylabel("Power [kW]")
    # Headroom above the ~15 kW Q_cond peak so the upper-right legend
    # doesn't collide with the bar tops.
    ax_p.set_ylim(top=18)
    ax_p.set_yticks(np.arange(0, 17, 4))
    ax_p.legend(loc="upper right", frameon=False, ncol=2, fontsize=dm.fs(-1))
    _panels.append((ax_p, "b"))

    # --- (c) COP -------------------------------------------------------
    # ``cop_sys`` is NaN whenever the HP is off (~89% of the day), so a
    # thin semi-transparent line collapses to invisible specks. Render
    # instantaneous as discrete markers — one dot per simulation step —
    # so the burst structure during morning/evening cycling is legible.
    ax_c.plot(
        t_h,
        cop_inst,
        color=COLORS["accent2"],
        linestyle="none",
        marker=".",
        markersize=2,
        alpha=0.7,
        label="instantaneous",
    )
    ax_c.plot(t_h, cop_running, color=COLORS["accent"], linewidth=dm.lw(1), label="running mean")
    ax_c.set_ylabel("$\\mathrm{COP}_{\\mathrm{sys}}$ [-]")
    ax_c.set_xlabel("Time of day [h]")
    ax_c.set_xlim(0, SIM_HOURS)
    ax_c.set_xticks(np.arange(0, SIM_HOURS + 1, 3))
    ax_c.legend(loc="upper right", frameon=False, ncol=2, fontsize=dm.fs(-1))
    _panels.append((ax_c, "c"))

    for ax in (ax_t, ax_p, ax_c):
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)

    out = static_path("dynamic_24h_timeseries.svg").with_suffix("")
    dm.label_axes([a for a, _ in _panels], labels=[l for _, l in _panels])
    finalize(fig, out, margin="3%")
    plt.close(fig)
    record_figure(
        "dynamic_24h_timeseries",
        "scripts/visualization/dynamic_24h_timeseries.py",
        {"ref": "R32", "settings": "library defaults (the documented call)"},
        "a 24 h run of the documented ASHPB call with two DHW draws",
        daily_totals(df),
    )
    print(f"wrote {out}.svg")


if __name__ == "__main__":
    main()
