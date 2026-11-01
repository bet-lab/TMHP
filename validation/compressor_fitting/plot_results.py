"""Render native-size vector slide plots with explicit axes and audited ticks."""

import json
from pathlib import Path

import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.ticker import FuncFormatter

from tmhp.compressor_baseline import OssorioR290Map

dm.style.use("presentation")
# Final Slide slots in px; SVG 1 pt = 4/3 px. No resizing after import.
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "figures"
WIDTH, HEIGHT = 1000, 360
AXIS_PR = (2, 7, 1)
AXIS_N = (30, 110, 20)
MAP_BOUNDS = {"eta_v": (80, 110, 5), "eta_ov": (35, 85, 10), "f_loss": (5, 25, 5)}
TITLES = {"eta_v": "Volumetric efficiency", "eta_ov": "Overall efficiency", "f_loss": "Heat loss fraction"}
TOKENS = json.loads((ROOT / "plot_tokens.json").read_text())["roles"]
BLUE = TOKENS["magnitude.stop_6"]["value"]
INK = TOKENS["text.label"]["value"]
GRAY = TOKENS["text.auxiliary"]["value"]
CMAP = LinearSegmentedColormap.from_list("betlab_blue", [TOKENS[f"magnitude.stop_{i}"]["value"] for i in range(1, 10)])
plt.rcParams.update(
    {
        "figure.facecolor": "none",
        "axes.facecolor": "none",
        "savefig.transparent": True,
        "font.family": TOKENS["font.latin"]["value"],
        "svg.fonttype": "none",
        "axes.labelcolor": INK,
        "text.color": INK,
        "axes.edgecolor": TOKENS["line.axis"]["value"],
    }
)
AUDIT = []


def axis(ax, dimension, spec, label):
    """Set and verify both endpoint ticks for one linear numeric axis."""
    low, high, step = spec
    assert low < high and step > 0 and abs((high - low) / step - round((high - low) / step)) < 1e-8
    ticks = np.linspace(low, high, round((high - low) / step) + 1)
    getattr(ax, f"set_{dimension}lim")(low, high)
    getattr(ax, f"set_{dimension}ticks")(ticks)
    getattr(ax, f"set_{dimension}label")(label)
    getattr(ax, f"{dimension}axis").set_major_formatter(FuncFormatter(lambda v, p: f"{v:g}"))
    AUDIT.append(dict(axis=dimension, min=low, max=high, interval=step, ticks=ticks.tolist()))


def save(fig, name):
    """Save transparent SVG/PDF plus a white review PNG without tight cropping."""
    OUT.mkdir(exist_ok=True)
    fig.canvas.draw()
    fig.savefig(OUT / f"{name}.svg", transparent=True, bbox_inches=None)
    svg = OUT / f"{name}.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
    fig.savefig(OUT / f"{name}.pdf", transparent=True, bbox_inches=None)
    fig.savefig(OUT / f"{name}.png", facecolor="white", transparent=False, dpi=180, bbox_inches=None)
    plt.close(fig)


def maps(scales=None, name="baseline_maps"):
    """Draw equal-size map panels; final contours retain the baseline for comparison."""
    fig, axes = plt.subplots(1, 3, figsize=(WIDTH / 96, HEIGHT / 96))
    fig.subplots_adjust(left=0.065, right=0.965, bottom=0.19, top=0.86, wspace=0.52)
    pr, n = np.meshgrid(np.linspace(2, 7, 70), np.linspace(30, 110, 70))
    for ax, key in zip(axes, TITLES, strict=True):
        baseline = OssorioR290Map(key)(pr, n) * 100
        factor = (
            1
            if scales is None
            else scales[{"eta_v": "lambda_eta_v", "eta_ov": "lambda_eta_ov", "f_loss": "lambda_f_loss"}[key]]
        )
        lo, hi, step = MAP_BOUNDS[key]
        levels = np.linspace(lo, hi, 21)
        im = ax.contourf(pr, n, baseline * factor, levels=levels, cmap=CMAP, extend="both")
        lines = ax.contour(
            pr, n, baseline * factor, levels=np.arange(lo, hi + step, step), colors=INK, linewidths=0.35, alpha=0.6
        )
        ax.clabel(lines, fontsize=7, fmt="%g")
        if scales is not None and abs(factor - 1) > 1e-6:
            ax.contour(
                pr, n, baseline, levels=np.arange(lo, hi + step, step), colors=GRAY, linewidths=0.8, linestyles="dashed"
            )
        ax.set_title(TITLES[key] + ("" if scales is None else f" (λ = {factor:.3f})"), fontsize=10.5, pad=12)
        axis(ax, "x", AXIS_PR, "Pressure ratio")
        axis(ax, "y", AXIS_N, "Shaft speed [rev/s]")
        ax.set_box_aspect(1)
        cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.04, shrink=0.60, ticks=np.arange(lo, hi + step, step))
        cb.ax.set_title("(%)", fontsize=8, pad=8)
        cb.ax.set_ylim(lo, hi)
        AUDIT.append(
            dict(
                axis="colorbar",
                quantity=key,
                min=lo,
                max=hi,
                interval=step,
                ticks=np.arange(lo, hi + step, step).tolist(),
            )
        )
    save(fig, name)


def sensitivity():
    """Show scalar multiplication at PR=3.5, explicitly a fixed-state map study."""
    fig, axes = plt.subplots(1, 3, figsize=(WIDTH / 96, HEIGHT / 96))
    fig.subplots_adjust(left=0.07, right=0.98, bottom=0.19, top=0.83, wspace=0.42)
    speed = np.linspace(30, 110, 100)
    ranges = {"eta_v": (60, 120, 20), "eta_ov": (30, 90, 15), "f_loss": (0, 30, 10)}
    for ax, key in zip(axes, TITLES, strict=True):
        for factor, style, color in [(0.8, ":", GRAY), (1, "--", INK), (1.2, "-", BLUE)]:
            ax.plot(
                speed, OssorioR290Map(key)(3.5, speed) * factor * 100, style, color=color, label=f"λ = {factor:.1f}"
            )
        axis(ax, "x", AXIS_N, "Shaft speed [rev/s]")
        axis(ax, "y", ranges[key], TITLES[key] + " (%)")
        ax.set_title("PR = 3.5", fontsize=10.5, pad=12)
        ax.set_box_aspect(1)
        ax.legend(fontsize=8, loc="upper right", frameon=False)
    save(fig, "lambda_sensitivity")


def parity():
    """Compare the identical train/holdout rows with independent complete axes."""
    df = pd.read_csv(ROOT / "point_predictions.csv")
    summary = json.loads((ROOT / "metrics.json").read_text())
    for quantity, truth, predicted, unit, spec in [
        ("COP", "COP_catalogue", "COP_model", "", (2, 6, 1)),
        ("power", "catalogue_power_kW", "power_kW", " [kW]", (0.6, 1.6, 0.2)),
    ]:
        df["catalogue_power_kW"] = df.Q_requested_W / df.COP_catalogue / 1000
        df["power_kW"] = df.power_W / 1000
        fig, axes = plt.subplots(1, 2, figsize=(WIDTH / 96, 400 / 96))
        fig.subplots_adjust(left=0.08, right=0.96, bottom=0.18, top=0.83, wspace=0.36)
        for ax, stage, label in zip(
            axes, ["before", "after"], ["Before calibration", "After calibration"], strict=True
        ):
            ax.plot(spec[:2], spec[:2], color=GRAY, lw=0.8)
            for split, marker, color in [("calibration", "o", BLUE), ("holdout", "s", INK)]:
                data = df[(df.stage == stage) & (df.split == split)]
                ax.scatter(
                    data[truth],
                    data[predicted],
                    marker=marker,
                    s=40,
                    facecolors=color if split == "calibration" else "none",
                    edgecolors=color,
                    label=split.title(),
                )
                assert data[truth].between(*spec[:2]).all() and data[predicted].between(*spec[:2]).all()
            axis(ax, "x", spec, "Catalogue " + quantity + unit)
            axis(ax, "y", spec, "Predicted " + quantity + unit)
            ax.set_box_aspect(1)
            ax.set_title(label, pad=12)
            key = "COP_RMSE" if quantity == "COP" else "power_RMSE_W"
            a = summary["calibration"][stage][key]
            b = summary["holdout"][stage][key]
            text = (
                f"Train RMSE: {a:.3f}\nHoldout RMSE: {b:.3f}"
                if quantity == "COP"
                else f"Train RMSE: {a:.1f} W\nHoldout RMSE: {b:.1f} W"
            )
            ax.text(0.04, 0.95, text, transform=ax.transAxes, va="top", fontsize=9)
            ax.legend(loc="lower right", frameon=False, fontsize=9)
        save(fig, "parity_" + quantity.lower())
    fig, ax = plt.subplots(figsize=(460 / 96, 340 / 96))
    fig.subplots_adjust(left=0.17, right=0.96, bottom=0.2, top=0.92)
    for split, marker in [("calibration", "o"), ("holdout", "s")]:
        for stage, color in [("before", GRAY), ("after", BLUE)]:
            data = df[(df.split == split) & (df.stage == stage)]
            ax.scatter(data.pressure_ratio, data.speed_rps, marker=marker, c=color, label=f"{split.title()} / {stage}")
    axis(ax, "x", AXIS_PR, "Pressure ratio")
    axis(ax, "y", AXIS_N, "Shaft speed [rev/s]")
    ax.set_box_aspect(1)
    ax.legend(fontsize=7, frameon=False)
    save(fig, "operating_domain")


def main():
    """Always render source-map plots; add fitted plots when a case run exists."""
    maps()
    sensitivity()
    if (ROOT / "fit_report.json").exists():
        report = json.loads((ROOT / "fit_report.json").read_text())
        maps(report["applied_scales"], "fitted_maps")
    if (ROOT / "metrics.json").exists():
        parity()
    (OUT / "axis_audit.json").write_text(json.dumps(AUDIT, indent=2) + "\n")


if __name__ == "__main__":
    main()
