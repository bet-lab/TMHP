"""Source temperature and system COP of an ASHP and a GSHP over the same three winter days.

Both units are the same machine apart from the source: R290, 8 kW nominal,
heating a 50 °C tank at 6 kW. What differs is the temperature each one draws
heat from:

* the air-source unit sees the outdoor air, a synthetic profile around 0 °C --
  a 5 K daily half-swing (coldest at 06:00) on a slow 3 K cold spell;
* the ground-source unit sees the ground-loop fluid leaving the borehole field,
  ``T_bhe_f_out [°C]`` of the GSHPB's own borehole response to a week of
  hot-water production before the plotted days and to the days themselves.

Panel (a) draws the two source temperatures; panel (b) the system COP of each
unit at that hour's source temperature (``analyze_steady``, the library
defaults -- the air-source unit with its frost and defrost). The COP is the
steady state of the running machine at each hour, not a thermostat cycle, and
the load is hot water only.

Writes ``docs/source/_static/source_cop_timeseries.svg`` and its provenance
record ``docs/source/_static/provenance/source_cop_timeseries.json``.
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np

os.environ.setdefault("TQDM_DISABLE", "1")

from tmhp import AirSourceHeatPumpBoiler, GroundSourceHeatPumpBoiler  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dmpl_common import (  # noqa: E402
    COLORS,
    GRIDLINE,
    apply_style,
    finalize,
    panel_letter,
    record_figure,
    static_path,
    ticks,
)

REF = "R290"
CAPACITY_W = 8_000.0
DUTY_W = 6_000.0  # heat into the tank
T_TANK_W = 50.0  # °C
DT_S = 600
DAYS = 3
PRECONDITION_DAYS = 7  # hot-water production before the plotted days, outdoor air at 0 °C

#: Both units are built as the library ships them (the comparison is of the
#: source, not of a setting): R290, the same nominal capacity, defaults otherwise.
MODEL_SETTINGS: dict = {"ref": REF, "hp_capacity": CAPACITY_W}


def outdoor_air(t_h: np.ndarray) -> np.ndarray:
    """Three winter days around 0 °C: a 5 K daily half-swing (coldest at 06:00) on a slow 3 K cold spell."""
    return 5.0 * np.sin(2.0 * np.pi * (t_h - 12.0) / 24.0) - 3.0 * np.sin(np.pi * t_h / (DAYS * 24.0))


def dhw_draw(n: int) -> np.ndarray:
    """Morning and evening draws every day [m3/s]."""
    per_day = int(86400 / DT_S)
    use = np.zeros(n)
    for d in range(n // per_day):
        use[d * per_day + 42 : d * per_day + 48] = 1.0e-4
        use[d * per_day + 114 : d * per_day + 123] = 8.0e-5
    return use


def simulate() -> dict:
    """Source temperatures every step and hourly system COP of both units."""
    per_day = int(86400 / DT_S)
    n_pre, n = PRECONDITION_DAYS * per_day, DAYS * per_day
    t_h = np.arange(n) * DT_S / 3600.0
    t0 = outdoor_air(t_h)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # Ground-loop fluid entering the heat pump: the GSHPB's own borehole
        # response to a week of hot-water production, then the plotted days.
        gshpb = GroundSourceHeatPumpBoiler(**MODEL_SETTINGS)
        t0_all = np.concatenate([np.full(n_pre, 0.0), t0])
        df = gshpb.analyze_dynamic((n_pre + n) * DT_S, DT_S, T_TANK_W, dhw_draw(n_pre + n), t0_all)
        t_loop = df["T_bhe_f_out [°C]"].to_numpy()[n_pre:]

        # System COP of the running machine at the same duty and tank
        # temperature, hour by hour (the air-source unit with frost and defrost).
        ashpb = AirSourceHeatPumpBoiler(**MODEL_SETTINGS)
        hours = np.arange(0, DAYS * 24 + 1)
        idx = np.minimum((hours * 3600 // DT_S).astype(int), n - 1)
        cop_a, cop_g = [], []
        for i in idx:
            ra = ashpb.analyze_steady(T_tank_w=T_TANK_W, T0=float(t0[i]), Q_ref_tank=DUTY_W)
            rg = gshpb.analyze_steady(T_tank_w=T_TANK_W, T_source=float(t_loop[i]), Q_ref_tank=DUTY_W)
            cop_a.append(float(ra["cop_sys [-]"]) if ra["hp_is_on"] else np.nan)
            cop_g.append(float(rg["cop_sys [-]"]) if rg["hp_is_on"] else np.nan)
    return {
        "t_h": t_h,
        "T0_C": t0,
        "T_loop_C": t_loop,
        "hours": hours,
        "T0_at_hour_C": t0[idx],
        "T_loop_at_hour_C": t_loop[idx],
        "cop_ashp": np.array(cop_a),
        "cop_gshp": np.array(cop_g),
    }


def summary(data: dict) -> dict:
    """What the provenance record keeps: the hourly values and the means drawn in the legend."""
    return {
        "hours": data["hours"],
        "T0_at_hour_C": data["T0_at_hour_C"],
        "T_loop_at_hour_C": data["T_loop_at_hour_C"],
        "cop_ashp": data["cop_ashp"],
        "cop_gshp": data["cop_gshp"],
        "mean_cop_ashp": float(np.nanmean(data["cop_ashp"])),
        "mean_cop_gshp": float(np.nanmean(data["cop_gshp"])),
    }


def main() -> None:
    apply_style("report", hashsalt="tmhp.visualization.source-cop-timeseries")
    data = simulate()
    rec = summary(data)
    t_h, hours = data["t_h"], data["hours"]
    mean_a, mean_g = rec["mean_cop_ashp"], rec["mean_cop_gshp"]

    c_air, c_gnd = COLORS["warm"], COLORS["accent"]
    lw = dm.lw(1)
    fig, (ax_t, ax_c) = plt.subplots(2, 1, sharex=True, figsize=dm.figsize("15cm", 0.72), height_ratios=[1.0, 1.25])

    ax_t.plot(t_h, data["T0_C"], color=c_air, linewidth=lw, label="Outdoor air (ASHP source)")
    ax_t.plot(t_h, data["T_loop_C"], color=c_gnd, linewidth=lw, label="Ground-loop fluid (GSHP source)")
    ax_t.axhline(0.0, color=COLORS["muted"], linewidth=GRIDLINE, zorder=0)
    ax_t.set_ylabel("Source temperature [°C]")
    ax_t.set_ylim(-10.0, 20.0)
    ax_t.set_yticks(ticks(-10.0, 20.0, 10.0))
    ax_t.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax_t.legend(loc="upper left", frameon=False, ncol=2, fontsize=dm.fs(-1))
    panel_letter(ax_t, "a")

    ax_c.plot(hours, data["cop_ashp"], color=c_air, linewidth=lw, label=f"ASHP, mean {mean_a:.2f}")
    ax_c.plot(hours, data["cop_gshp"], color=c_gnd, linewidth=lw, label=f"GSHP, mean {mean_g:.2f}")
    ax_c.set_ylabel("System COP [-]")
    ax_c.set_xlabel("Time [h]")
    ax_c.set_xlim(0.0, DAYS * 24.0)
    ax_c.set_xticks(ticks(0.0, DAYS * 24.0, 12.0))
    ax_c.set_ylim(1.5, 4.5)
    ax_c.set_yticks(ticks(1.5, 4.5, 0.5))
    ax_c.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax_c.legend(loc="upper left", frameon=False, ncol=2, fontsize=dm.fs(-1))
    ax_c.text(
        0.995,
        0.04,
        f"{REF}, {DUTY_W / 1000:.0f} kW into a {T_TANK_W:.0f} °C tank",
        transform=ax_c.transAxes,
        ha="right",
        va="bottom",
        fontsize=dm.fs(-2),
        color=COLORS["ink"],
    )
    panel_letter(ax_c, "b")

    out = static_path("source_cop_timeseries.svg").with_suffix("")
    finalize(fig, out)
    plt.close(fig)
    record_figure(
        "source_cop_timeseries",
        "scripts/visualization/source_cop_timeseries.py",
        {
            **MODEL_SETTINGS,
            "duty_W": DUTY_W,
            "T_tank_w_C": T_TANK_W,
            "dt_s": DT_S,
            "days": DAYS,
            "precondition_days": PRECONDITION_DAYS,
            "settings": "library defaults for both units (ASHPB with frost and defrost)",
        },
        "source temperature and hourly system COP of an ASHP and a GSHP, same refrigerant, capacity and duty",
        rec,
    )
    print(
        f"T0 {data['T0_C'].min():.1f}..{data['T0_C'].max():.1f} °C, "
        f"loop {data['T_loop_C'].min():.1f}..{data['T_loop_C'].max():.1f} °C; "
        f"mean COP ASHP {mean_a:.2f}, GSHP {mean_g:.2f}"
    )
    print(f"wrote {out}.svg")


if __name__ == "__main__":
    main()
