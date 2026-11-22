"""Rated-load frost multipliers against the outdoor temperature, by frost-point band (#231).

Eight panels: the COP multiplier (top) and the capacity multiplier (bottom) of
the frost adjustment at the rated load, at four outdoor humidities, for a
frost-point band of 0, 3 (the default) and 6 K and without one (the fixed
losses whatever the air deposits; the rest of the frost model unchanged).
Reads what ``validation.frost_band_sensitivity.study`` wrote; no model runs
here.

The figure carries axes, series and nothing else. What the shape means belongs
in the text that embeds it (``docs/source/models/ashp.rst``, "Frost and
defrost").

Run
---
``uv run python -m scripts.validation.frost_band_figure``
"""

from __future__ import annotations

import sys
from pathlib import Path

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "visualization"))
from _dmpl_common import COLORS, GRIDLINE, apply_style, finalize, static_path, ticks  # noqa: E402

MULTIPLIERS = REPO_ROOT / "validation" / "results" / "frost_band_sensitivity" / "multipliers.csv"

#: Outdoor relative humidities of the four columns [-].
HUMIDITIES = (0.60, 0.70, 0.85, 1.00)
#: (column of the CSV, axis label) of the two rows.
QUANTITIES = (("cop_multiplier", "COP multiplier [-]"), ("capacity_multiplier", "Capacity multiplier [-]"))
#: band label of the CSV -> (legend, colour, line style, line-width step). The
#: three widths differ in hue and in dash, so that the series do not rest on
#: colour alone; the default is the solid, heavier line.
SERIES = {
    "none": ("no band", COLORS["muted"], (0, (1, 1.5)), 0.3),
    "0": ("0 K", COLORS["warm"], (0, (4, 1.5)), 0.3),
    "3": ("3 K (default)", COLORS["accent"], "-", 1.0),
    "6": ("6 K", COLORS["ess"], (0, (5, 1.5, 1, 1.5)), 0.3),
}


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.frost.band")
    table = pd.read_csv(MULTIPLIERS, dtype={"band": str})

    fig, axes = plt.subplots(2, len(HUMIDITIES), figsize=dm.figsize("17cm", 0.55), sharex=True, sharey=True)
    for row, (column, label) in enumerate(QUANTITIES):
        for col, humidity in enumerate(HUMIDITIES):
            ax = axes[row, col]
            part = table[(table.RH - humidity).abs() < 1e-9]
            for band, (legend, colour, style, width) in SERIES.items():
                series = part[part.band == band].sort_values("T_out_C")
                ax.plot(series.T_out_C, series[column], color=colour, ls=style, lw=dm.lw(width), label=legend)
            ax.grid(lw=GRIDLINE, color=COLORS["muted"], alpha=0.35)
            ax.set_axisbelow(True)
            if row == 0:
                ax.set_title(f"{100.0 * humidity:.0f} % relative humidity", loc="left", fontsize=dm.fs(-1))
            else:
                ax.set_xlabel("Outdoor dry bulb [°C]")
            if col == 0:
                ax.set_ylabel(label)
    axes[0, 0].set_xlim(-30.0, 5.0)
    axes[0, 0].set_xticks(ticks(-30.0, 0.0, 10.0))
    axes[0, 0].set_ylim(0.74, 1.01)
    axes[0, 0].set_yticks(ticks(0.75, 1.0, 0.05))
    # One key for the eight panels, above them: inside any panel it would sit on a curve.
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=len(SERIES), frameon=False, fontsize=dm.fs(-1))

    finalize(fig, static_path("frost_band_sensitivity.svg").with_suffix(""), formats=("svg", "png"), mt="6%")


if __name__ == "__main__":
    main()
