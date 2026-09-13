"""Does conductance scale with capacity? The question the UA rule rests on.

``UA_ou_rated = hp_capacity / 5.0`` is a *capacity-based* rule, and a rule of
that shape is only admissible if conductance is proportional to duty across
the population. If the exponent were not one, every size would need its own
number and no single divisor could exist.

Three populations on one pair of axes: the two component rating standards the
divisor was inverted from, and the twelve heat-pump outdoor coils computed
from published geometry. Each catalogue population carries its own fit --
pooling them tilts the exponent, because evaporators sit at low duty with a
high conductance per kilowatt and condensers at high duty with a low one, so a
single line through both measures the mixture rather than the scaling.

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


def _fit(q: np.ndarray, ua: np.ndarray) -> tuple[float, float, float]:
    """Least squares in log space: ``UA = a * Q**b``, with the R² of that fit."""
    keep = (q > 0.0) & (ua > 0.0)
    lx, ly = np.log(q[keep]), np.log(ua[keep])
    slope, intercept = np.polyfit(lx, ly, 1)
    residual = ly - (intercept + slope * lx)
    r2 = 1.0 - float(np.sum(residual**2) / np.sum((ly - ly.mean()) ** 2))
    return float(np.exp(intercept)), float(slope), r2


def main() -> None:
    apply_style("scientific", hashsalt="tmhp.validation.ua.scaling")

    evaporator = pd.read_csv(DATA / "en328_evaporator_inversion.csv")
    evaporator = evaporator[evaporator.is_primary]
    condenser = pd.read_csv(DATA / "env327_condenser_inversion.csv")
    heatpump = pd.read_csv(DATA / "hp_outdoor_coil_ua_geometry.csv")

    fig, ax = plt.subplots(figsize=dm.figsize("12cm", 0.70))

    for frame, colour, name in (
        (evaporator, COLORS["accent"], "Unit coolers · EN 328"),
        (condenser, COLORS["ess"], "Air-cooled condensers · ENV 327"),
    ):
        q = frame.Q_kW.to_numpy(float)
        ua = frame.UA_W_K.to_numpy(float)
        ax.scatter(q, ua, c=colour, alpha=0.30, linewidths=0, label=f"{name} ({len(frame)})")
        a, b, r2 = _fit(q, ua)
        span = np.array([q.min(), q.max()])
        ax.plot(span, a * span**b, "--", color=colour, zorder=4, label=f"$UA = {a:.0f}\\,Q^{{{b:.3f}}}$,  R² {r2:.3f}")

    ax.scatter(
        heatpump.Q_cond_kW,
        heatpump.UA_W_K,
        c=COLORS["warm"],
        marker="D",
        edgecolors="white",
        zorder=6,
        label=f"Heat-pump outdoor coils · geometry ({len(heatpump)})",
    )

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Coil duty $Q$ [kW]")
    ax.set_ylabel("Conductance $UA$ [W/K]")
    ax.set_xlim(0.25, 3000.0)
    ax.set_ylim(30.0, 6.0e5)
    ax.set_xticks([1.0, 1.0e1, 1.0e2, 1.0e3])
    ax.set_yticks([1.0e2, 1.0e3, 1.0e4, 1.0e5])
    ax.grid(True)
    ax.legend(loc="upper left")

    finalize(fig, static_path("ua_capacity_scaling.svg").with_suffix(""), formats=("svg", "png"))


if __name__ == "__main__":
    main()
