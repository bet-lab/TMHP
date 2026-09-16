"""Swept displacement at the air-to-air rating point, and what it rests on.

The shipped displacement figure is drawn at the air-to-water rating point,
where the rated speed was inverted from nine machines whose manufacturer
publishes a displacement. Air-to-air has no such closing step: no catalogue in
the validation set prints a displacement, so the rule runs open-loop.

Three panels rather than the air-to-water four, because two of those four are
the Panasonic inversion and there is no air-to-air equivalent to draw. The
third panel here is the question that replaces it -- how much of the answer the
one chosen number decides.

Run
---
``uv run python -m scripts.validation.displacement_a2a_figure``
"""

from __future__ import annotations

import sys
from pathlib import Path

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "visualization"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "validation"))
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
from displacement_figure import REF_COLOR, REFRIGERANTS, volumetric_capacity  # noqa: E402

from tmhp.compressor_speed import RATED_POINT_AIR_TO_AIR, RatedPoint, default_displacement  # noqa: E402

#: The two rated compressor speeds Daikin's SL-series service manual publishes,
#: either side of the 60 rev/s the library declares.
PUBLISHED_RPS = (52.0, 72.0)


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.displacement.a2a")
    point = RATED_POINT_AIR_TO_AIR
    x = np.arange(len(REFRIGERANTS))

    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.34))

    # (a) what the fluid contributes -- volumetric refrigerating capacity
    ax = axes[0]
    q_vol = np.array([volumetric_capacity(r, point.T_evap_C, point.T_cond_C)[2] for r in REFRIGERANTS])
    relative = q_vol / q_vol[REFRIGERANTS.index("R32")]
    ax.bar(x, relative, width=0.62, color=[REF_COLOR[r] for r in REFRIGERANTS], linewidth=0)
    ax.axhline(1.0, color=COLORS["ink"], lw=HAIRLINE)
    ax.set_ylabel("Volumetric capacity, R32 = 1 [-]")
    ax.set_ylim(0.0, 1.2)
    ax.set_yticks(ticks(0.0, 1.2, 0.3))
    ax.set_title("The fluid does the work", loc="left", fontsize=dm.fs(-1))
    panel_letter(ax, "a")

    # (b) the displacement that follows, per kW of nameplate cooling capacity
    ax = axes[1]
    per_kw = np.array([default_displacement(1000.0, r, point) * 1e6 for r in REFRIGERANTS])
    ax.bar(x, per_kw, width=0.62, color=[REF_COLOR[r] for r in REFRIGERANTS], linewidth=0)
    ax.set_ylabel("Displacement [cm³/rev per kW]")
    top = float(np.ceil(per_kw.max() / 2.0) * 2.0)
    ax.set_ylim(0.0, top)
    ax.set_yticks(ticks(0.0, top, 2.0))
    ax.set_title(f"At ISO 5151 T1, {point.rps:.0f} rev/s", loc="left", fontsize=dm.fs(-1))
    panel_letter(ax, "b")

    for ax in axes[:2]:
        ax.set_xticks(x)
        ax.set_xticklabels(REFRIGERANTS, fontsize=dm.fs(-1.6))
        ax.grid(axis="y", lw=GRIDLINE, color=COLORS["muted"], alpha=0.35)
        ax.set_axisbelow(True)

    # (c) how much the one chosen number decides
    ax = axes[2]
    speeds = np.arange(45.0, 80.1, 1.0)
    for refrigerant in ("R32", "R410A"):
        curve = [
            default_displacement(1000.0, refrigerant, RatedPoint(point.T_evap_C, point.T_cond_C, s, point.duty, ""))
            * 1e6
            for s in speeds
        ]
        ax.plot(speeds, curve, color=REF_COLOR[refrigerant], lw=dm.lw(0.6), label=refrigerant)
    for published in PUBLISHED_RPS:
        ax.axvline(published, color=COLORS["muted"], lw=HAIRLINE, linestyle=(0, (3, 2)))
    ax.axvline(point.rps, color=COLORS["ink"], lw=HAIRLINE)
    ax.set_xlabel("Rated compressor speed [rev/s]")
    ax.set_ylabel("Displacement [cm³/rev per kW]")
    ax.set_xlim(45.0, 80.0)
    ax.set_xticks(ticks(45.0, 80.0, 10.0))
    ax.set_ylim(0.0, 6.0)
    ax.set_yticks(ticks(0.0, 6.0, 2.0))
    ax.grid(lw=GRIDLINE, color=COLORS["muted"], alpha=0.35)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=dm.fs(-1.5), loc="upper right")
    ax.set_title("What the chosen speed decides", loc="left", fontsize=dm.fs(-1))
    panel_letter(ax, "c")

    finalize(fig, static_path("compressor_displacement_a2a.svg").with_suffix(""), mt="7%", formats=("svg", "png"))


if __name__ == "__main__":
    main()
