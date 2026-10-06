"""Does conductance scale with capacity? The question the UA rule rests on.

``UA_ou_rated = hp_capacity / 5.0`` is a *capacity-based* rule, and a rule of
that shape is only admissible if conductance is proportional to duty across
the population. If the exponent were not one, every size would need its own
number and no single divisor could exist.

Two populations on one pair of axes -- the component rating standards the
divisor was inverted from. Each carries its own fit: pooling them tilts the
exponent, because evaporators sit at low duty with a high conductance per
kilowatt and condensers at high duty with a low one, so a single line through
both measures the mixture rather than the scaling.

Reading belongs in the caption; the axes carry the fit exponents because those
are the quantity the panel exists to report.

Run
---
``uv run python -m scripts.validation.ua_scaling_figure``
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
    apply_style,
    finalize,
    static_path,
)

DATA = REPO_ROOT / "validation" / "data"

#: A quarter of the preset's default marker area (``lines.markersize**2`` = 36).
#: 1,414 points at the default size overlap into a solid mass and the spread
#: inside each population stops being visible.
MARKER_AREA = 9.0

#: Outline weight for the hollow markers. The preset's ``lines.linewidth`` of
#: 1.0 is a third of a 3 pt marker's diameter, which fills the ring back in.
STROKE = 0.5

#: Legend handles keep the original marker area, so the key stays readable at
#: a size the data would not tolerate.
LEGEND_MARKERSCALE = 2.0

#: Semi-transparent rings, so that where they pile up the density itself
#: becomes readable rather than the outline of whichever point drew last.
POPULATION_ALPHA = 0.5

#: ``(source, label, subscript, marker colour, fit colour)``. The two
#: open-color steps either side of the shared token -- one lighter for the
#: cloud, two darker for the line -- keep the fit legible against its own
#: scatter without introducing a second hue per series. The subscript says
#: which side of the cycle each population was inverted from, because the two
#: sit in different bands and the reason is the rating standard, not the
#: hardware: EN 328 declares an 8 K approach, ENV 327 a 15 K one.
POPULATIONS = (
    ("en328_evaporator_inversion.csv", "Unit coolers · EN 328", "evap", "oc.indigo5", "oc.indigo8"),
    ("env327_condenser_inversion.csv", "Air-cooled condensers · ENV 327", "cond", "oc.teal5", "oc.teal8"),
)

#: The rule TMHP adopts, in W/K per kW of that coil's own duty. It is the
#: geometric mean of the two population prefactors (159.7) and the median of
#: their pooled band, i.e. the middle of the band the two standards agree on
#: rather than a preference for either committee's approach temperature. As a
#: divisor it reads as a 6.25 K equivalent LMTD.
ADOPTED_SLOPE = 160.0


def _fit(q: np.ndarray, ua: np.ndarray) -> tuple[float, float, float]:
    """Least squares in log space: ``UA = a * Q**b``, with the R² of that fit."""
    keep = (q > 0.0) & (ua > 0.0)
    lx, ly = np.log(q[keep]), np.log(ua[keep])
    slope, intercept = np.polyfit(lx, ly, 1)
    residual = ly - (intercept + slope * lx)
    r2 = 1.0 - float(np.sum(residual**2) / np.sum((ly - ly.mean()) ** 2))
    return float(np.exp(intercept)), float(slope), r2


def main() -> None:
    apply_style("scientific", hashsalt="tmhp.validation.ua.scaling", svg_fonttype=None)

    fig, ax = plt.subplots(figsize=dm.figsize("12cm", 0.70))

    for source, name, sub, marker_colour, fit_colour in POPULATIONS:
        frame = pd.read_csv(DATA / source)
        if "is_primary" in frame:
            frame = frame[frame.is_primary]
        q = frame.Q_kW.to_numpy(float)
        ua = frame.UA_W_K.to_numpy(float)
        ax.scatter(
            q,
            ua,
            s=MARKER_AREA,
            facecolors="none",
            edgecolors=marker_colour,
            linewidths=STROKE,
            alpha=POPULATION_ALPHA,
            label=f"{name} ({len(frame)})",
        )
        a, b, r2 = _fit(q, ua)
        span = np.array([q.min(), q.max()])
        ax.plot(
            span,
            a * span**b,
            "--",
            color=fit_colour,
            zorder=4,
            label=f"$UA_{{\\mathrm{{{sub}}}}} = {a:.0f}\\,Q_{{\\mathrm{{{sub}}}}}^{{{b:.3f}}}$,  R² {r2:.3f}",
        )

    adopted = np.array([0.3, 2000.0])
    ax.plot(
        adopted,
        ADOPTED_SLOPE * adopted,
        color=COLORS["ink"],
        zorder=5,
        label=f"TMHP default  $UA = {ADOPTED_SLOPE:.0f}\\,Q$",
    )

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Coil duty  $Q_\\mathrm{evap}$, $Q_\\mathrm{cond}$  [kW]")
    ax.set_ylabel("Conductance  $UA_\\mathrm{evap}$, $UA_\\mathrm{cond}$  [W/K]")
    ax.set_xlim(0.25, 3000.0)
    ax.set_ylim(30.0, 6.0e5)
    ax.set_xticks([1.0, 1.0e1, 1.0e2, 1.0e3])
    ax.set_yticks([1.0e2, 1.0e3, 1.0e4, 1.0e5])
    ax.grid(True)
    ax.legend(loc="upper left", markerscale=LEGEND_MARKERSCALE)

    finalize(fig, static_path("ua_capacity_scaling.svg").with_suffix(""), formats=("svg", "png"))


if __name__ == "__main__":
    main()
