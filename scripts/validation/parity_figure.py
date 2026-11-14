"""Parity figure: predicted against published COP, across the whole catalogue set.

Reads whatever ``validation/parity/run`` last wrote, so adding a catalogue and
rerunning the harness is all it takes to have the figure and the documentation
table follow.

The question the figure has to answer is not "does the model match a machine"
but "does it keep matching when the working fluid and the size change", so
refrigerant is the colour and nominal capacity the marker size. A model that
had been quietly tuned to one unit would show up as one tight cluster on the
diagonal and the rest scattered.

Only ``adopted`` catalogues are scored: the statistics written on a panel are
those of the validation set, the numbers the documentation quotes. Catalogues
on ``hold`` (``validation.parity.spec``) are still drawn, as hollow grey rings
under the scored points, so the figure shows what is held back and where it
sits without counting it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "visualization"))
from _dmpl_common import (  # noqa: E402
    COLORS,
    GRIDLINE,
    HAIRLINE,
    apply_style,
    finalize,
    static_path,
    ticks,
)

RESULTS = REPO_ROOT / "validation" / "results"

REFRIGERANT_COLOR = {
    "R32": COLORS["accent"],
    "R410A": COLORS["warm"],
    "R290": COLORS["ess"],
    "R134a": COLORS["load"],
}

#: Units on hold: drawn, not scored.
HOLD_COLOR = "#8a8f98"
HOLD_ALPHA = 0.55

PANELS = (
    ("ASHPB", "a", "Air-to-water (heating)"),
    ("ASHP", "b", "Air-to-air (heating and cooling)"),
)


def load_points() -> pd.DataFrame:
    frames = [pd.read_csv(p) for p in sorted(RESULTS.glob("*.csv")) if p.name != "summary.csv"]
    if not frames:
        raise SystemExit("no results under validation/results/ -- run\n  uv run python -m validation.parity.run")
    df = pd.concat(frames, ignore_index=True)
    return df[df.usable]


def panel_statistics(points: pd.DataFrame) -> dict[str, float]:
    """What a panel states about its points: adopted catalogues only."""
    scored = points[points.status == "adopted"]
    return {
        "units": int(scored.slug.nunique()),
        "points": int(len(scored)),
        "mae": float(scored.abs_error.mean()),
        "mape": float(scored.abs_pct_error.mean()),
        "within10": float((scored.abs_pct_error <= 10.0).mean() * 100.0),
    }


def _marker_size(capacity_kw: pd.Series) -> np.ndarray:
    # Area proportional to capacity, floored so the smallest unit stays visible.
    return dm.fs(2) + dm.fs(1) * np.sqrt(capacity_kw.to_numpy() / capacity_kw.max())


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.parity")
    df = load_points()

    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.52))
    _panels: list = []
    for ax, (model_class, letter, title) in zip(axes, PANELS, strict=True):
        sub = df[df.model_class == model_class]
        if sub.empty:
            ax.set_visible(False)
            continue
        lo = max(0.5, float(min(sub.cop_target.min(), sub.cop_pred.min())) * 0.9)
        hi = float(max(sub.cop_target.max(), sub.cop_pred.max())) * 1.05
        line = np.linspace(lo, hi, 200)

        ax.fill_between(line, 0.80 * line, 1.20 * line, color=COLORS["band20"], alpha=0.55, linewidth=0, label="±20 %")
        ax.fill_between(line, 0.90 * line, 1.10 * line, color=COLORS["band10"], alpha=0.70, linewidth=0, label="±10 %")
        ax.plot(line, line, linestyle=":", color=COLORS["muted"], linewidth=dm.lw(0))

        adopted = sub[sub.status == "adopted"]
        held = sub[sub.status == "hold"]
        for refrigerant, group in adopted.groupby("refrigerant"):
            ax.scatter(
                group.cop_target,
                group.cop_pred,
                s=_marker_size(group.nominal_kW),
                color=REFRIGERANT_COLOR.get(refrigerant, COLORS["ink"]),
                alpha=0.75,
                linewidth=HAIRLINE,
                edgecolor="white",
                zorder=4,
                label=f"{refrigerant} ({group.nominal_kW.nunique()} sizes)",
            )
        if not held.empty:
            # On hold: same place and size, hollow, one layer below the scored points.
            ax.scatter(
                held.cop_target,
                held.cop_pred,
                s=_marker_size(held.nominal_kW),
                facecolor="none",
                edgecolor=HOLD_COLOR,
                alpha=HOLD_ALPHA,
                linewidth=HAIRLINE,
                zorder=3,
            )

        # The offset cluster is deliberately left unlabelled here. Which units
        # they are, and why they sit where they do, is a three-cause story that
        # `validation.analysis.residual_decomposition` tells properly -- product
        # efficiency, dehumidification and a maximum-capacity table, only the
        # first of which is the defaults' doing. An arrow and two lines of text
        # across the data can only carry the single-cause version of that, and
        # the single-cause version is the one that turned out to be wrong. It
        # belongs in the caption of whatever embeds this, not on the axes.

        stats = panel_statistics(sub)
        ax.text(
            0.04,
            0.96,
            f"{stats['units']} units, {stats['points']} points\n"
            f"MAE {stats['mae']:.2f}   MAPE {stats['mape']:.1f} %\n"
            f"{stats['within10']:.0f} % within ±10 %",
            transform=ax.transAxes,
            va="top",
            ha="left",
            fontsize=dm.fs(-1),
            color=COLORS["ink"],
        )
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        # Declared ticks, not an automatic locator: the two panels span
        # different COP ranges and a locator would give them different
        # intervals, which invites comparing grids that do not correspond.
        step = 2.0 if hi - lo > 6.0 else 1.0
        grid = ticks(float(np.ceil(lo / step) * step), float(np.floor(hi / step) * step), step)
        ax.set_xticks(grid)
        ax.set_yticks(grid)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("Published COP [-]")
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
        handles, labels = ax.get_legend_handles_labels()
        if not held.empty:
            handles.append(
                Line2D(
                    [],
                    [],
                    linestyle="none",
                    marker="o",
                    markerfacecolor="none",
                    markeredgecolor=HOLD_COLOR,
                    markeredgewidth=HAIRLINE,
                    markersize=np.sqrt(dm.fs(2) + dm.fs(1) * 0.8),
                )
            )
            refrigerants = "/".join(sorted(held.refrigerant.unique()))
            labels.append(f"on hold, not scored ({refrigerants}, {held.slug.nunique()} units)")
        ax.legend(handles, labels, loc="lower right", frameon=False, fontsize=dm.fs(-2), handletextpad=0.4)
        ax.set_title(title, loc="left", fontsize=dm.fs(0))
        _panels.append((ax, letter))

    axes[0].set_ylabel("Predicted COP [-]")
    out = static_path("validation_parity.svg").with_suffix("")
    dm.label_axes([a for a, _ in _panels], labels=[l for _, l in _panels])
    finalize(fig, out, mt="6%", formats=("svg", "png"))
    plt.close(fig)
    print(f"wrote {out}.svg")
    overall = panel_statistics(df)
    held = df[df.status == "hold"]
    print(f"  adopted: {overall['units']} units, {overall['points']} points, MAPE {overall['mape']:.1f} %")
    print(f"  on hold (drawn, not scored): {held.slug.nunique()} units, {len(held)} points")


if __name__ == "__main__":
    main()
