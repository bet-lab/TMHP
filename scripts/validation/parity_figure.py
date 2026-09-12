"""Parity figure: predicted against published COP, across the whole catalogue set.

Reads whatever ``validation/parity/run`` last wrote, so adding a catalogue and
rerunning the harness is all it takes to have the figure and the documentation
table follow.

The question the figure has to answer is not "does the model match a machine"
but "does it keep matching when the working fluid and the size change", so
refrigerant is the colour and nominal capacity the marker size. A model that
had been quietly tuned to one unit would show up as one tight cluster on the
diagonal and the rest scattered.
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

RESULTS = REPO_ROOT / "validation" / "results"

REFRIGERANT_COLOR = {
    "R32": COLORS["accent"],
    "R410A": COLORS["warm"],
    "R290": COLORS["ess"],
    "R134a": COLORS["load"],
}

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


def _marker_size(capacity_kw: pd.Series) -> np.ndarray:
    # Area proportional to capacity, floored so the smallest unit stays visible.
    return dm.fs(2) + dm.fs(1) * np.sqrt(capacity_kw.to_numpy() / capacity_kw.max())


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.parity")
    df = load_points()

    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.52))
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

        for refrigerant, group in sub.groupby("refrigerant"):
            ax.scatter(
                group.cop_target,
                group.cop_pred,
                s=_marker_size(group.nominal_kW),
                color=REFRIGERANT_COLOR.get(refrigerant, COLORS["ink"]),
                alpha=0.75,
                linewidth=dm.lw(-2),
                edgecolor="white",
                zorder=4,
                label=f"{refrigerant} ({group.nominal_kW.nunique()} sizes)",
            )

        # Name any unit the defaults systematically miss. Without this the
        # air-to-air panel reads as "R-410A is predicted badly", which is the
        # wrong conclusion -- the air-to-water R-410A units are among the best
        # fits in the set. What separates the offset cluster is the product
        # line's own efficiency, not its working fluid.
        bias = sub.groupby(["slug", "unit"]).apply(
            lambda g: ((g.cop_pred - g.cop_target) / g.cop_target).mean() * 100.0,
            include_groups=False,
        )
        offset = bias[bias.abs() > 20.0]
        if not offset.empty:
            names = sorted({unit.split()[0] for _, unit in offset.index})
            worst = sub[sub.slug.isin({slug for slug, _ in offset.index})]
            ax.annotate(
                f"{' / '.join(names)}: {offset.mean():+.0f} %\n"
                "a lower-efficiency product line,\nnot a refrigerant effect",
                xy=(
                    float(worst.cop_target.quantile(0.55)),
                    float(worst.cop_pred.quantile(0.75)),
                ),
                xytext=(0.03, 0.74),
                textcoords="axes fraction",
                fontsize=dm.fs(-2),
                color=COLORS["ink"],
                ha="left",
                va="top",
                arrowprops={
                    "arrowstyle": "->",
                    "color": COLORS["muted"],
                    "linewidth": dm.lw(-1),
                    "shrinkB": 6,
                },
            )

        mae = float(sub.abs_error.mean())
        mape = float(sub.abs_pct_error.mean())
        within10 = float((sub.abs_pct_error <= 10.0).mean() * 100.0)
        ax.text(
            0.04,
            0.96,
            f"{sub.slug.nunique()} units, {len(sub)} points\n"
            f"MAE {mae:.2f}   MAPE {mape:.1f} %\n"
            f"{within10:.0f} % within ±10 %",
            transform=ax.transAxes,
            va="top",
            ha="left",
            fontsize=dm.fs(-1),
            color=COLORS["ink"],
        )
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("Published COP [-]")
        ax.grid(True, alpha=0.25, linewidth=dm.lw(-2))
        ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-2), handletextpad=0.4)
        ax.set_title(title, loc="left", fontsize=dm.fs(0))
        panel_letter(ax, letter)

    axes[0].set_ylabel("Predicted COP [-]")
    out = static_path("validation_parity.svg").with_suffix("")
    finalize(fig, out, mt="6%", formats=("svg", "png"))
    plt.close(fig)
    print(f"wrote {out}.svg")
    print(f"  {df.slug.nunique()} units, {len(df)} points, overall MAPE {df.abs_pct_error.mean():.1f} %")


if __name__ == "__main__":
    main()
