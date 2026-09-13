"""Three separations of the refrigerant / manufacturer / rating-standard block.

Reads what ``validation.analysis.refrigerant_diagnosis`` wrote, plus the parity
results. Axes and series only; the reading belongs in the caption.

Run
---
``uv run python -m scripts.validation.refrigerant_diagnosis_figure``
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
from _dmpl_common import COLORS, GRIDLINE, HAIRLINE, apply_style, finalize, panel_letter, static_path  # noqa: E402

DATA = REPO_ROOT / "validation" / "data"
RESULTS = REPO_ROOT / "validation" / "results"
SWEEP = DATA / "refrigerant_nameplate_sweep.csv"
MATCHED = DATA / "refrigerant_matched_point.csv"

REF_COLOR = {"R32": COLORS["accent"], "R410A": COLORS["warm"], "R290": COLORS["ess"]}

#: ``(label, model class, refrigerant)`` -- the rating standard is part of the
#: label because it is the variable the panel exists to expose.
ROWS = (
    ("R410A\nA2W · EN 14511", "ASHPB", "R410A"),
    ("R290\nA2W · EN 14511", "ASHPB", "R290"),
    ("R32\nA2W · EN 14511", "ASHPB", "R32"),
    ("R32\nA2A · EN 14511", "ASHP", "R32"),
    ("R410A\nA2A · AHRI 210/240", "ASHP", "R410A"),
)


def _ticks(vmin: float, vmax: float, step: float) -> np.ndarray:
    """Ticks from an explicit minimum, maximum and interval."""
    return np.arange(vmin, vmax + step * 0.5, step)


def _parity() -> pd.DataFrame:
    frames = [pd.read_csv(p) for p in sorted(RESULTS.glob("*.csv")) if p.name != "summary.csv"]
    df = pd.concat(frames, ignore_index=True)
    df = df[df.usable].copy()
    df["signed"] = (df.cop_pred - df.cop_target) / df.cop_target * 100.0
    return df


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.refrigerant")
    parity = _parity()
    sweep = pd.read_csv(SWEEP)
    matched = pd.read_csv(MATCHED)

    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.42))

    # (a) error by refrigerant, with the rating standard on the label
    ax = axes[0]
    for i, (_label, model_class, refrigerant) in enumerate(ROWS):
        block = parity[(parity.model_class == model_class) & (parity.refrigerant == refrigerant)]
        y = len(ROWS) - 1 - i
        ax.barh(y, block.abs_pct_error.mean(), height=0.58, color=REF_COLOR[refrigerant], linewidth=0)
        ax.plot(
            [block.signed.mean()],
            [y],
            marker="o",
            ms=dm.fs(2.4),
            mfc="white",
            mec=COLORS["ink"],
            mew=HAIRLINE,
            zorder=5,
            label="signed bias" if i == 0 else None,
        )
    ax.axvline(0.0, color=COLORS["ink"], lw=HAIRLINE)
    ax.set_yticks(range(len(ROWS)))
    ax.set_yticklabels([row[0] for row in ROWS][::-1], fontsize=dm.fs(-1.5), linespacing=1.25)
    ax.set_xlabel("COP error [%]")
    ax.set_xlim(-10.0, 40.0)
    ax.set_xticks(_ticks(-10.0, 40.0, 10.0))
    ax.set_ylim(-0.6, len(ROWS) - 0.4)
    ax.grid(axis="x", lw=GRIDLINE, color=COLORS["muted"], alpha=0.35)
    ax.set_axisbelow(True)
    ax.legend(loc="upper right", frameon=False, fontsize=dm.fs(-1.6))
    ax.set_title("Bar = MAPE, ring = bias", loc="left", fontsize=dm.fs(-1))
    panel_letter(ax, "a", x=-0.13)

    # (b) one matched operating point, both machines
    ax = axes[1]
    width = 0.32
    for i, row in matched.iterrows():
        colour = REF_COLOR[row.refrigerant]
        ax.bar(
            i - width / 2,
            row.declared_cop,
            width=width,
            color=colour,
            linewidth=0,
            label="declared" if i == 0 else None,
        )
        ax.bar(
            i + width / 2,
            row.predicted_cop,
            width=width,
            color=colour,
            alpha=0.32,
            linewidth=HAIRLINE,
            edgecolor=colour,
            label="TMHP" if i == 0 else None,
        )
    ax.set_xticks(range(len(matched)))
    ax.set_xticklabels(
        [f"{row.unit.split()[0]}\n{row.refrigerant}" for _, row in matched.iterrows()],
        fontsize=dm.fs(-1.4),
        linespacing=1.25,
    )
    ax.set_ylabel("COP [-]")
    ax.set_ylim(0.0, 6.0)
    ax.set_yticks(_ticks(0.0, 6.0, 2.0))
    ax.grid(axis="y", lw=GRIDLINE, color=COLORS["muted"], alpha=0.35)
    ax.set_axisbelow(True)
    ax.legend(loc="upper center", frameon=False, fontsize=dm.fs(-1.6), ncol=2, columnspacing=0.9)
    ax.set_title("Matched point, 35 °C outdoor", loc="left", fontsize=dm.fs(-1))
    panel_letter(ax, "b")

    # (c) nameplate multiplier sweep
    ax = axes[2]
    styles = {
        "fujitsu_asuh09lpas": ("ASUH09LPAS", COLORS["warm"], "-"),
        "fujitsu_asuh12lpas": ("ASUH12LPAS", COLORS["hot"], "-"),
        "daikin_rxm25a": ("RXM25A (control)", COLORS["accent"], "--"),
    }
    for slug, (label, colour, dash) in styles.items():
        block = sweep[sweep.slug == slug].sort_values("multiplier", ascending=False)
        ax.plot(
            block.multiplier,
            block.mape_pct,
            dash,
            color=colour,
            lw=dm.lw(0.6),
            marker="o",
            ms=dm.fs(1.7),
            mfc="white",
            mew=HAIRLINE,
            mec=colour,
            label=label,
        )
    ax.set_xlabel("Nameplate multiplier [-]")
    ax.set_ylabel("COP MAPE [%]")
    ax.set_xlim(1.03, 0.57)
    ax.set_xticks(_ticks(0.6, 1.0, 0.1))
    top = float(np.ceil(sweep.mape_pct.max() / 10.0) * 10.0)
    ax.set_ylim(0.0, top)
    ax.set_yticks(_ticks(0.0, top, 10.0))
    ax.grid(lw=GRIDLINE, color=COLORS["muted"], alpha=0.35)
    ax.set_axisbelow(True)
    ax.legend(loc="lower left", frameon=False, fontsize=dm.fs(-1.6))
    ax.set_title("Only the nameplate moves", loc="left", fontsize=dm.fs(-1))
    panel_letter(ax, "c")

    finalize(fig, static_path("validation_refrigerant_diagnosis.svg").with_suffix(""), mt="6%", formats=("svg", "png"))


if __name__ == "__main__":
    main()
