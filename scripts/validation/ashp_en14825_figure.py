"""Air-to-air EN 14825 trajectory against the declared air-to-air records.

Load and outdoor temperature fall together, the way the standard does, so this
is a different curve from the fixed-temperature sweep next to it.

The declared population is 19 models from 3 manufacturers. Every one of them is
drawn individually rather than reduced to a band: at that size a percentile
envelope would describe the sample and not the population, and drawing it would
invite exactly the reading the sample cannot support.

Run
---
``uv run python -m scripts.validation.ashp_en14825_figure``
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
from _dmpl_common import (  # noqa: E402
    COLORS,
    GRIDLINE,
    HAIRLINE,
    apply_style,
    finalize,
    panel_letter,
    static_path,
    ticks,
)

DATA = REPO_ROOT / "validation" / "data"
DECLARED = DATA / "keymark_a2a_declared.csv"
POINTS = DATA / "ashp_en14825_points.csv"

SHOWN_CAPACITY_KW = 3.5
SHOWN_REFRIGERANT = "R32"
SHOWN_OVERSIZING = {"heating": 0.667, "cooling": 0.90}

PANELS = (
    ("heating", "a", "Heating, average climate"),
    ("cooling", "b", "Cooling, average climate"),
)
LABELS = ("A", "B", "C", "D")


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.ashp.en14825")
    declared = pd.read_csv(DECLARED)
    points = pd.read_csv(POINTS)

    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.42))

    for ax, (duty, letter, title) in zip(axes, PANELS, strict=True):
        block = declared[declared.application == duty]
        plr = [block[f"plr_{label}"].iloc[0] for label in LABELS]

        for _, row in block.iterrows():
            ax.plot(
                plr,
                [row[f"cop_{label}"] for label in LABELS],
                color=COLORS["muted"],
                lw=dm.lw(-0.6),
                alpha=0.55,
                zorder=1,
            )
        ax.plot(
            plr,
            [block[f"cop_{label}"].median() for label in LABELS],
            color=COLORS["ink"],
            lw=dm.lw(0.4),
            linestyle=(0, (4, 1.6)),
            zorder=3,
            label=f"declared, {len(block)} models",
        )

        model = points[
            (points.duty == duty)
            & (points.capacity_kW == SHOWN_CAPACITY_KW)
            & (points.refrigerant == SHOWN_REFRIGERANT)
            & (points.oversizing == SHOWN_OVERSIZING[duty])
        ].set_index("point")
        ax.plot(
            plr,
            [model.cop_sys.get(label, np.nan) for label in LABELS],
            color=COLORS["accent"],
            lw=dm.lw(0.8),
            marker="o",
            ms=dm.fs(1.8),
            mfc="white",
            mew=HAIRLINE,
            zorder=5,
            label="TMHP",
        )

        ax.set_xlabel("Part load of design duty [-]")
        ax.set_xlim(0.0, 1.08)
        ax.set_xticks(ticks(0.0, 1.0, 0.2))
        ax.grid(lw=GRIDLINE, color=COLORS["muted"], alpha=0.35)
        ax.set_axisbelow(True)
        ax.legend(frameon=False, fontsize=dm.fs(-1.5), loc="upper right")
        ax.set_title(title, loc="left", fontsize=dm.fs(-1))
        panel_letter(ax, letter)

    for ax, (duty, _, _) in zip(axes, PANELS, strict=True):
        block = declared[declared.application == duty]
        model = points[(points.duty == duty) & (points.capacity_kW == SHOWN_CAPACITY_KW)]
        highest = max(
            float(max(block[f"cop_{label}"].max() for label in LABELS)),
            float(model.cop_sys.max(skipna=True)),
        )
        step = 2.0 if highest <= 12.0 else 4.0
        top = float(np.ceil(highest / step) * step)
        ax.set_ylim(0.0, top)
        ax.set_yticks(ticks(0.0, top, step))
    axes[0].set_ylabel("COP [-]")
    axes[1].set_ylabel("EER [-]")

    finalize(fig, static_path("validation_ashp_en14825.svg").with_suffix(""), mt="6%", formats=("svg", "png"))


if __name__ == "__main__":
    main()
