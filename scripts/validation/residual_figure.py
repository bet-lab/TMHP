"""Why the air-to-air residuals look the way they do -- in three pictures.

Companion to ``validation.analysis.residual_decomposition``. The point of the
figure is that the harness-wide +33 % on the Fujitsu units is not one thing,
and reading it as one thing is how an earlier version of this analysis got it
wrong.
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
from validation.analysis.residual_decomposition import (  # noqa: E402
    NAMEPLATES,
    build,
    predicted_cop,
)
from validation.analysis.residual_decomposition import (  # noqa: E402
    OUT_CSV as DECOMPOSITION_CSV,  # noqa: N811
)

BAND_CSV = REPO_ROOT / "validation" / "data"

# `dm.lw` is relative to the active preset, and under "report" it bottoms out at
# zero: lw(-2) = -1.0 and lw(-1) = 0.0. Matplotlib tolerates either on a solid
# line but raises on a dashed one, because the dash pattern is scaled by the
# width and collapses to all-zeros. Hairlines here are therefore explicit.
HAIRLINE = 0.6
GRIDLINE = 0.4
MAKER_COLOR = {"Daikin": COLORS["accent"], "Fujitsu": COLORS["warm"]}
SWEEP = [2.5 + 0.5 * i for i in range(24)]  # Q/2.5 .. Q/14


def band_divisors() -> np.ndarray:
    """Conductance band from the component catalogues, as capacity divisors."""
    evap = pd.read_csv(BAND_CSV / "en328_evaporator_inversion.csv")
    evap = evap[evap.is_primary] if "is_primary" in evap else evap
    cond = pd.read_csv(BAND_CSV / "env327_condenser_inversion.csv")
    # Component coils are rated on their own coil duty; the machine rule is
    # written against nameplate cooling capacity, so carry the 1.307 conversion.
    ua_over_q_cool = pd.concat([evap.UA_over_Q, cond.UA_over_Q]) * 1.307
    return (1.0 / ua_over_q_cool).to_numpy()


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.residual")
    # The back-out is a root find over a model solve, so it is minutes of work.
    # Reuse what `validation.analysis.residual_decomposition` already wrote.
    df = pd.read_csv(DECOMPOSITION_CSV) if DECOMPOSITION_CSV.exists() else build()

    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.42))
    fig.subplots_adjust(wspace=0.42)

    # --- (a) the back-out itself ------------------------------------------
    ax = axes[0]
    for plate in NAMEPLATES:
        cops = [predicted_cop(plate, "cooling", d) for d in SWEEP]
        colour = MAKER_COLOR[plate.manufacturer]
        ax.plot(SWEEP, cops, color=colour, linewidth=dm.lw(0), alpha=0.85)
        ax.axhline(plate.eer, color=colour, linestyle=":", linewidth=HAIRLINE, alpha=0.6)
    row = df.dropna(subset=["implied_ua_divisor"])
    ax.scatter(
        row.implied_ua_divisor,
        row.nameplate_EER,
        s=dm.fs(5),
        zorder=5,
        c=[MAKER_COLOR[m] for m in row.manufacturer],
        edgecolor="white",
        linewidth=HAIRLINE,
    )
    ax.axvline(5.0, color=COLORS["ink"], linestyle="--", linewidth=dm.lw(0))
    ax.text(5.15, ax.get_ylim()[1] * 0.97, "default Q/5", fontsize=dm.fs(-2), color=COLORS["ink"], va="top")
    ax.set_xlabel("capacity / divisor")
    ax.set_ylabel("cooling COP at the rating point")
    ax.set_title("Each machine's own COP", loc="left", fontsize=dm.fs(-1))
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    for maker, colour in MAKER_COLOR.items():
        ax.plot([], [], color=colour, linewidth=dm.lw(0), label=maker)
    ax.legend(frameon=False, fontsize=dm.fs(-2), loc="lower left")
    panel_letter(ax, "a")

    # --- (b) against the measured band ------------------------------------
    ax = axes[1]
    divisors = band_divisors()
    p10, p90 = np.percentile(divisors, [10, 90])
    ax.hist(np.clip(divisors, 2, 16), bins=48, color=COLORS["band20"], alpha=0.9, edgecolor="none")
    # `linewidth=0` alone still leaves the style preset's dashed edge, whose
    # pattern then scales to all zeros and raises. Drop the edge outright.
    ax.axvspan(p10, p90, color=COLORS["band10"], alpha=0.35, linewidth=0, edgecolor="none")
    ymax = ax.get_ylim()[1]
    for _, r in df.dropna(subset=["implied_ua_divisor"]).iterrows():
        ax.plot([r.implied_ua_divisor] * 2, [0, ymax * 0.80], color=MAKER_COLOR[r.manufacturer], linewidth=dm.lw(1))
        ax.text(
            r.implied_ua_divisor,
            ymax * 0.83,
            r.unit.split()[-1].replace("RXM", ""),
            rotation=90,
            fontsize=dm.fs(-3),
            ha="center",
            va="bottom",
            color=MAKER_COLOR[r.manufacturer],
        )
    ax.axvline(5.0, color=COLORS["ink"], linestyle="--", linewidth=dm.lw(0))
    ax.set_xlim(2, 16)
    ax.set_xlabel("implied capacity / divisor")
    ax.set_ylabel("component coils [count]")
    ax.set_title(f"Against 1,414 measured coils (p10–p90 {p10:.1f}–{p90:.1f})", loc="left", fontsize=dm.fs(-1))
    panel_letter(ax, "b")

    # --- (c) bias at the clean rating point -------------------------------
    ax = axes[2]
    order = df.sort_values("nameplate_EER")
    y = np.arange(len(order))
    ax.barh(y - 0.19, order.bias_heating_pct, 0.36, color=COLORS["cool"], label="heating")
    ax.barh(y + 0.19, order.bias_cooling_pct, 0.36, color=COLORS["hot"], label="cooling")
    ax.axvline(0, color=COLORS["ink"], linewidth=HAIRLINE)
    ax.set_yticks(y)
    ax.set_yticklabels(
        [f"{u.split()[-1]}  ({e:.2f})" for u, e in zip(order.unit, order.nameplate_EER, strict=True)],
        fontsize=dm.fs(-2),
    )
    ax.set_xlabel("bias at rating point [%]")
    ax.set_title("Sorted by nameplate EER", loc="left", fontsize=dm.fs(-1))
    ax.legend(frameon=False, fontsize=dm.fs(-2), loc="lower right")
    ax.grid(True, axis="x", alpha=0.25, linewidth=GRIDLINE)
    panel_letter(ax, "c", x=-0.42)

    out = static_path("validation_residuals.svg").with_suffix("")
    finalize(fig, out, mt="7%")
    plt.close(fig)
    print(f"wrote {out}.svg")
    print(
        df[
            ["unit", "nameplate_EER", "cooling_SHR", "bias_cooling_pct", "bias_heating_pct", "implied_ua_divisor"]
        ].to_string(index=False, float_format=lambda v: f"{v:.2f}")
    )


if __name__ == "__main__":
    main()
