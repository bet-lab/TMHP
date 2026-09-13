"""Air-to-air part load: hold both air temperatures, lower the duty.

Two panels because air-to-air runs two duties and the nameplate is the cooling
one. Reads whatever ``validation.analysis.ashp_trend`` last wrote.

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
from _dmpl_common import COLORS, GRIDLINE, apply_style, finalize, panel_letter, static_path  # noqa: E402

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


def _ticks(vmin: float, vmax: float, step: float) -> np.ndarray:
    """Ticks from an explicit minimum, maximum and interval."""
    return np.arange(vmin, vmax + step * 0.5, step)


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.ashp.partload")
    sweep = pd.read_csv(SWEEP)
    sweep = sweep[(sweep.capacity_kW == SHOWN_CAPACITY_KW) & (sweep.refrigerant == SHOWN_REFRIGERANT)]

    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.42))

    for ax, (duty, letter, title) in zip(axes, PANELS, strict=True):
        part = sweep[sweep.duty == duty]
        for t_outdoor, group in part.groupby("t_outdoor_C", sort=False):
            group = group.sort_values("plr_nameplate")
            ax.plot(
                group.plr_nameplate,
                group.cop_sys,
                color=SERIES_COLOUR[duty][float(t_outdoor)],
                lw=dm.lw(0.6),
                label=f"{float(t_outdoor):.0f} °C",
            )
        ax.set_xlabel("Part load of nameplate capacity [-]")
        ax.set_xlim(0.08, 1.02)
        ax.set_xticks(_ticks(0.2, 1.0, 0.2))
        ax.grid(lw=GRIDLINE, color=COLORS["muted"], alpha=0.35)
        ax.set_axisbelow(True)
        ax.legend(title="outdoor air", frameon=False, fontsize=dm.fs(-1.5), title_fontsize=dm.fs(-1.5))
        ax.set_title(title, loc="left", fontsize=dm.fs(-1))
        panel_letter(ax, letter)

    top = float(np.ceil(sweep.cop_sys.max()))
    for ax in axes:
        ax.set_ylim(0.0, top)
        ax.set_yticks(_ticks(0.0, top, 2.0))
    axes[0].set_ylabel("System COP [-]")
    axes[1].set_ylabel("System EER [-]")

    finalize(fig, static_path("validation_ashp_part_load.svg").with_suffix(""), mt="6%", formats=("svg", "png"))


if __name__ == "__main__":
    main()
