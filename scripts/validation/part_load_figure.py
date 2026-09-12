"""Part-load figure: is the modelled COP-against-load trend the right shape?

Two curves, because two different questions get confused with each other.

**Panel (a) -- fixed temperatures.** Hold the source and sink where they are and
take load away. COP rises, because the heat exchangers grow relative to the duty
while the lift shrinks. It keeps rising until the compressor reaches its speed
floor; below that the machine cannot follow the load any further and the curve
turns over. That turnover is the minimum modulation limit, not the efficiency
correlations -- the figure marks where the floor is reached so the reader can
see which mechanism is acting.

**Panel (b) -- the certification trajectory.** EN 14825 lowers the flow
temperature as it lowers the load, so its four test points are a different curve
from panel (a): both the duty and the lift fall together. Heat Pump Keymark
publishes the measured result for thousands of machines, and the modelled
trajectory has to sit inside that band and rise the same way. The band here is
the p10-p90 of 9,062 certified air-to-water records, read straight from the
certificates by ``validation.extraction.keymark_declared``.

Panel (b) is the one that decides whether the trend is right. Panel (a) shows
what the model does with a question the certification data never asks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "visualization"))
from _dmpl_common import COLORS, apply_style, finalize, panel_letter, static_path  # noqa: E402

from tmhp import AirSourceHeatPumpBoiler  # noqa: E402

KEYMARK_SUMMARY = REPO_ROOT / "validation" / "data" / "keymark_en14825_summary.csv"

#: Fixed-temperature sweep: outdoor air and leaving water held, load varied.
SWEEP_CONDITIONS = (
    (7.0, 35.0, COLORS["accent"]),
    (7.0, 45.0, COLORS["warm"]),
    (-7.0, 45.0, COLORS["cool"]),
)
LOAD_FRACTIONS = np.linspace(1.0, 0.12, 26)

#: EN 14825 average climate, low-temperature application: outdoor air, leaving
#: water and part load of the design heating demand at each test point.
EN14825_LOW = (
    ("A", -7.0, 34.0, 0.88),
    ("B", 2.0, 30.0, 0.54),
    ("C", 7.0, 27.0, 0.35),
    ("D", 12.0, 24.0, 0.15),
)

NOMINAL_W = 9000.0
#: Design heating demand for the trajectory. The EN 14825 part loads are
#: fractions of the design demand, not of the machine's full capacity, so a
#: machine is normally oversized against it.
DESIGN_LOAD_W = 6000.0
SINK_OFFSET_K = 2.5


def sweep_fixed_temperatures() -> list[dict]:
    model = AirSourceHeatPumpBoiler(hp_capacity=NOMINAL_W, ref="R32")
    rows = []
    for t_outdoor, lwt, color in SWEEP_CONDITIONS:
        for fraction in LOAD_FRACTIONS:
            result = model.analyze_steady(
                T_tank_w=lwt - SINK_OFFSET_K,
                T0=t_outdoor,
                Q_ref_tank=NOMINAL_W * fraction,
                return_dict=True,
            )
            assert isinstance(result, dict)
            if result.get("failure_reason", "none") != "none":
                continue
            rows.append(
                {
                    "t_outdoor_C": t_outdoor,
                    "lwt_C": lwt,
                    "color": color,
                    "plr": fraction,
                    "cop": float(result["cop_sys [-]"]),
                    "rps": float(result["cmp_rpm [rpm]"]) / 60.0,
                    "at_floor": abs(float(result["cmp_rpm [rpm]"]) / 60.0 - model.rps_min) < 1e-6,
                }
            )
    return rows


def trajectory_en14825() -> list[dict]:
    model = AirSourceHeatPumpBoiler(hp_capacity=NOMINAL_W, ref="R32")
    rows = []
    for label, t_outdoor, lwt, load_ratio in EN14825_LOW:
        result = model.analyze_steady(
            T_tank_w=lwt - SINK_OFFSET_K,
            T0=t_outdoor,
            Q_ref_tank=DESIGN_LOAD_W * load_ratio,
            return_dict=True,
        )
        assert isinstance(result, dict)
        rows.append(
            {
                "point": label,
                "plr": load_ratio,
                "t_outdoor_C": t_outdoor,
                "lwt_C": lwt,
                "q_required_W": DESIGN_LOAD_W * load_ratio,
                "q_delivered_W": float(result["Q_ref_tank [W]"]),
                "clamped": result.get("capacity_clamped") is not None,
                "rps": float(result["cmp_rpm [rpm]"]) / 60.0,
                "cop": float(result["cop_sys [-]"]) if result.get("failure_reason") == "none" else np.nan,
                "failure_reason": result.get("failure_reason", "none"),
            }
        )
    return rows


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.part-load")
    sweep = pd.DataFrame(sweep_fixed_temperatures())
    trajectory = pd.DataFrame(trajectory_en14825())

    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.46))

    # --- (a) fixed temperatures -------------------------------------------
    ax = axes[0]
    for (t_outdoor, lwt, color), (_, group) in zip(
        SWEEP_CONDITIONS, sweep.groupby(["t_outdoor_C", "lwt_C"], sort=False), strict=False
    ):
        group = group.sort_values("plr")
        ax.plot(
            group.plr, group.cop, color=color, linewidth=dm.lw(1), label=f"air {t_outdoor:.0f} °C, water {lwt:.0f} °C"
        )
        floor = group[group.at_floor]
        if not floor.empty:
            edge = floor.plr.max()
            ax.plot(
                [edge],
                [float(floor.loc[floor.plr.idxmax(), "cop"])],
                marker="v",
                markersize=dm.fs(-1),
                color=color,
                zorder=5,
            )
    ax.axvspan(
        0.0,
        float(sweep[sweep.at_floor].plr.max()) if sweep.at_floor.any() else 0.0,
        color=COLORS["band20"],
        alpha=0.35,
        linewidth=0,
    )
    # The x axis is inverted, so axes-fraction 0 is full load and 1 is zero
    # load: the shaded floor region sits on the right-hand side.
    ax.text(
        0.97,
        0.06,
        "compressor at\nits speed floor",
        transform=ax.transAxes,
        fontsize=dm.fs(-2),
        color=COLORS["muted"],
        va="bottom",
        ha="right",
    )
    ax.set_xlabel("Part load of nominal capacity [-]")
    ax.set_ylabel("System COP [-]")
    ax.set_xlim(0.0, 1.05)
    ax.invert_xaxis()
    ax.grid(True, alpha=0.25, linewidth=dm.lw(-2))
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-2))
    ax.set_title("Load falls, temperatures held", loc="left", fontsize=dm.fs(0))
    panel_letter(ax, "a")

    # --- (b) EN 14825 trajectory against the certified band ---------------
    ax = axes[1]
    if KEYMARK_SUMMARY.exists():
        band = pd.read_csv(KEYMARK_SUMMARY)
        band = band[band.application == "low"].sort_values("plr")
        ax.fill_between(
            band.plr,
            band.cop_p10,
            band.cop_p90,
            color=COLORS["band20"],
            alpha=0.5,
            linewidth=0,
            label=f"Keymark p10–p90 (n = {int(band.n.iloc[0]):,})",
        )
        ax.fill_between(
            band.plr,
            band.cop_p25,
            band.cop_p75,
            color=COLORS["band10"],
            alpha=0.6,
            linewidth=0,
            label="Keymark p25–p75",
        )
        ax.plot(
            band.plr, band.cop_median, color=COLORS["muted"], linestyle="--", linewidth=dm.lw(0), label="Keymark median"
        )
    good = trajectory.dropna(subset=["cop"])
    ax.plot(
        good.plr,
        good.cop,
        color=COLORS["accent"],
        linewidth=dm.lw(1),
        zorder=5,
        label="TMHP, defaults",
    )
    free = good[~good.clamped]
    held = good[good.clamped]
    ax.scatter(
        free.plr,
        free.cop,
        s=dm.fs(6),
        color=COLORS["accent"],
        zorder=6,
        edgecolor="white",
        linewidth=dm.lw(-2),
    )
    if not held.empty:
        # Open marker where the compressor is at its floor and the machine is
        # therefore delivering more than the test point asks for. Just over half
        # of certified machines declare the same thing -- a higher heat output
        # at point D than at point C -- and the model reproduces it without
        # anything having been put in by hand to make it.
        ax.scatter(
            held.plr,
            held.cop,
            s=dm.fs(7),
            facecolor="white",
            zorder=6,
            edgecolor=COLORS["accent"],
            linewidth=dm.lw(1),
            label="delivering more than\nthe point requires",
        )
    for _, row in good.iterrows():
        ax.annotate(
            f"{row['point']}  {row['t_outdoor_C']:.0f}/{row['lwt_C']:.0f} °C",
            (row.plr, row.cop),
            textcoords="offset points",
            xytext=(4, -10),
            fontsize=dm.fs(-2),
            color=COLORS["ink"],
        )
    ax.set_xlabel("Part load of design heating demand [-]")
    ax.set_ylabel("System COP [-]")
    ax.invert_xaxis()
    ax.grid(True, alpha=0.25, linewidth=dm.lw(-2))
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-2))
    ax.set_title("Load and flow temperature fall together (EN 14825)", loc="left", fontsize=dm.fs(0))
    panel_letter(ax, "b")

    out = static_path("validation_part_load.svg").with_suffix("")
    finalize(fig, out, mt="6%")
    plt.close(fig)

    print(f"wrote {out}.svg")
    print(trajectory.to_string(index=False))


if __name__ == "__main__":
    main()
