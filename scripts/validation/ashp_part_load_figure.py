"""Air-to-air part load: hold both air temperatures, lower the duty.

Two panels because air-to-air runs two duties and the nameplate is the cooling
one. Reads whatever ``validation.analysis.ashp_trend`` last wrote.

The horizontal axis is the delivered heat as a share of what the same unit
delivers at the same temperatures at its top speed (``AirSourceHeatPump.max_capacity``),
not of the nameplate: the nameplate is a label for one rating point and sits
at 53-71 % of the top-speed capacity at the conditions drawn, so a curve that
stops there shows only part of the speed range. A marker on each curve is where
the nameplate duty falls. Requests below the unit's speed floor all resolve to
one operating point, which is where each curve ends on the left.

The figure carries axes, series and nothing else. What the shape means, where
it stops being trustworthy and which data produced it belong in the caption of
whatever document embeds it, not painted across the data.

Run
---
``uv run python -m scripts.validation.ashp_part_load_figure``
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
from _dmpl_common import COLORS, GRIDLINE, HAIRLINE, apply_style, finalize, static_path, ticks  # noqa: E402

SWEEP = REPO_ROOT / "validation" / "data" / "ashp_part_load_sweep.csv"

#: One configuration is drawn; the verdict table covers all of them.
SHOWN_CAPACITY_KW = 3.5
SHOWN_REFRIGERANT = "R32"

PANELS = (
    ("heating", "a", "Heating, indoor air 20 °C"),
    ("cooling", "b", "Cooling, indoor air 27 °C"),
)

#: Outdoor air temperature -> colour, per duty.
SERIES_COLOUR = {
    "heating": {-7.0: COLORS["cool"], 2.0: COLORS["accent"], 7.0: COLORS["warm"]},
    "cooling": {35.0: COLORS["hot"], 30.0: COLORS["warm"], 25.0: COLORS["accent"]},
}


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.ashp.partload")
    sweep = pd.read_csv(SWEEP)
    sweep = sweep[(sweep.capacity_kW == SHOWN_CAPACITY_KW) & (sweep.refrigerant == SHOWN_REFRIGERANT)]

    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.42))
    _panels: list = []

    for ax, (duty, letter, title) in zip(axes, PANELS, strict=True):
        part = sweep[sweep.duty == duty]
        for t_outdoor, group in part.groupby("t_outdoor_C", sort=False):
            group = group.sort_values(["delivered_fraction_of_max", "plr_nameplate"])
            colour = SERIES_COLOUR[duty][float(t_outdoor)]
            ax.plot(
                group.delivered_fraction_of_max,
                group.cop_sys,
                color=colour,
                lw=dm.lw(0.6),
                label=f"{float(t_outdoor):.0f} °C",
            )
            mark = group[group.plr_nameplate == 1.0]
            ax.plot(
                mark.delivered_fraction_of_max,
                mark.cop_sys,
                marker="D",
                markersize=dm.fs(-2.5),
                linestyle="none",
                markerfacecolor=colour,
                markeredgecolor="white",
                markeredgewidth=HAIRLINE,
                zorder=5,
            )
        ax.plot(
            [],
            [],
            marker="D",
            markersize=dm.fs(-2.5),
            linestyle="none",
            color=COLORS["ink"],
            label=f"{SHOWN_CAPACITY_KW:g} kW nameplate",
        )
        ax.set_xlabel("Delivered capacity, share of maximum capacity [-]")
        ax.set_xlim(0.0, 1.02)
        ax.set_xticks(ticks(0.0, 1.0, 0.2))
        ax.grid(lw=GRIDLINE, color=COLORS["muted"], alpha=0.35)
        ax.set_axisbelow(True)
        ax.legend(title="outdoor air", frameon=False, fontsize=dm.fs(-1.5), title_fontsize=dm.fs(-1.5))
        ax.set_title(title, loc="left", fontsize=dm.fs(-1))
        _panels.append((ax, letter))

    top = float(np.ceil(sweep.cop_sys.max()))
    for ax in axes:
        ax.set_ylim(0.0, top)
        ax.set_yticks(ticks(0.0, top, 2.0))
    axes[0].set_ylabel("System COP [-]")
    axes[1].set_ylabel("System EER [-]")

    dm.label_axes([a for a, _ in _panels], labels=[l for _, l in _panels])
    finalize(fig, static_path("validation_ashp_part_load.svg").with_suffix(""), formats=("svg", "png"))


if __name__ == "__main__":
    main()
