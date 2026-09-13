"""Is the shipped compressor coefficient set the one EN 14825 supports?

Three panels, and the third is the one that decides.

**(a) and (b)** put four compressor descriptions on the certified trajectory,
for the low- and medium-temperature applications. The shaded bands are the
p10-p90 and p25-p75 of the Heat Pump Keymark declared COP at the same four test
points, read off the certificates by ``validation.extraction.keymark_declared``.
A description that had no losses at all, or that had losses with no speed
dependence, has to show up here as the wrong curve rather than merely a shifted
one.

**(c)** places the same four descriptions against both things a coefficient set
has to satisfy at once: how far the EN 14825 trajectory sits from the certified
median, and how well the model reproduces published catalogue COP. Neither axis
alone picks a winner -- a set can be centred on the certified median and still
miss every individual machine -- so the figure is a plane, not a ranking.

Regenerate the inputs first::

    uv run python -m validation.analysis.en14825_trend
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
POINTS = DATA / "en14825_trend_points.csv"
BAND = DATA / "keymark_en14825_summary.csv"
PARITY = DATA / "coefficient_ablation_parity.csv"

#: The configuration the trajectory is drawn for. The verdict table covers
#: fifteen of them; one has to be drawn.
SHOWN = {"capacity_kW": 9.0, "refrigerant": "R32", "oversizing": 0.667}

VARIANT_STYLE = {
    "ideal": ("no compressor losses", COLORS["load"], (0, (1, 1.2))),
    "constant": ("losses, no speed term", COLORS["warm"], (0, (4, 1.6))),
    "absolute-speed": ("speed shape, absolute", COLORS["cool"], (0, (2.4, 1.4))),
    "defaults": ("TMHP defaults", COLORS["accent"], "solid"),
}

#: Where each variant's name sits in panel (c): offset in points and the
#: horizontal anchor. The four land in one corner plus one far outlier, so a
#: single rule puts labels off the canvas.
PANEL_C_LABEL = {
    "ideal": (-8, 6, "right"),
    "constant": (8, 4, "left"),
    "absolute-speed": (8, 6, "left"),
    "defaults": (8, -11, "left"),
}

APPLICATIONS = (
    ("low", "a", "Low-temperature application (W35 class)"),
    ("medium", "b", "Medium-temperature (W55 class)"),
)


def level_deviation(points: pd.DataFrame, band: pd.DataFrame) -> pd.Series:
    """Mean |COP / certified median - 1| over the eight declared test points.

    Absolute rather than signed: a set that is 15 % high at one point and 15 %
    low at another has not described the certified trajectory, and a signed
    average would report it as perfect.
    """
    medians = band.set_index(["application", "point"]).cop_median
    sub = points[
        (points.capacity_kW == SHOWN["capacity_kW"])
        & (points.refrigerant == SHOWN["refrigerant"])
        & (points.oversizing == SHOWN["oversizing"])
    ].copy()
    sub["ratio"] = [row.cop / medians.loc[(row.application, row.point)] for row in sub.itertuples()]
    return (sub.ratio - 1.0).abs().groupby(sub.variant).mean() * 100.0


def main() -> None:
    for path in (POINTS, BAND, PARITY):
        if not path.exists():
            raise SystemExit(
                f"missing {path.relative_to(REPO_ROOT)} -- run\n  uv run python -m validation.analysis.en14825_trend"
            )

    apply_style("report", hashsalt="tmhp.validation.en14825")
    points = pd.read_csv(POINTS)
    band = pd.read_csv(BAND)
    parity = pd.read_csv(PARITY).set_index("variant")

    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.36))

    # --- (a), (b) the certified trajectory ---------------------------------
    for ax, (application, letter, title) in zip(axes[:2], APPLICATIONS, strict=True):
        b = band[band.application == application].sort_values("plr")
        ax.fill_between(
            b.plr,
            b.cop_p10,
            b.cop_p90,
            color=COLORS["band20"],
            alpha=0.5,
            linewidth=0,
            label=f"Keymark p10–p90 (n = {int(b.n.iloc[0]):,})",
        )
        ax.fill_between(b.plr, b.cop_p25, b.cop_p75, color=COLORS["band10"], alpha=0.6, linewidth=0, label="p25–p75")
        ax.plot(b.plr, b.cop_median, color=COLORS["muted"], linestyle="--", linewidth=HAIRLINE, label="median")

        sub = points[
            (points.application == application)
            & (points.capacity_kW == SHOWN["capacity_kW"])
            & (points.refrigerant == SHOWN["refrigerant"])
            & (points.oversizing == SHOWN["oversizing"])
        ]
        for key, (label, color, dash) in VARIANT_STYLE.items():
            g = sub[sub.variant == key].sort_values("plr")
            if g.empty:
                continue
            ax.plot(
                g.plr,
                g.cop,
                color=color,
                linestyle=dash,
                linewidth=dm.lw(1) if key == "defaults" else dm.lw(0),
                zorder=6 if key == "defaults" else 4,
                label=label,
            )
            if key == "defaults":
                ax.scatter(g.plr, g.cop, s=dm.fs(4), color=color, edgecolor="white", linewidth=HAIRLINE, zorder=7)

        for _, row in sub[sub.variant == "defaults"].iterrows():
            ax.annotate(
                row.point,
                (row.plr, row.cop),
                textcoords="offset points",
                xytext=(0, 7),
                ha="center",
                fontsize=dm.fs(-2),
                color=COLORS["ink"],
            )
        # Every one of the four descriptions drops together at medium C, which
        # is what says the drop is not a property of any of them. It is the
        # outdoor-fan turndown: below the compressor speed floor the model
        # sheds capacity by starving the coil, and the evaporator approach runs
        # out to the optimiser's bound. Marked rather than hidden.
        if application == "medium":
            artifact = sub[(sub.variant == "defaults") & (sub.point == "C")]
            if not artifact.empty:
                ax.scatter(
                    artifact.plr,
                    artifact.cop,
                    s=dm.fs(11),
                    facecolor="none",
                    edgecolor=COLORS["ink"],
                    linewidth=HAIRLINE * 1.6,
                    zorder=8,
                    label="outdoor-fan turndown artefact",
                )
        ax.set_ylim(0.0, 12.0)
        ax.set_yticks(ticks(0.0, 12.0, 3.0))
        ax.set_xlabel("Part load of design heating demand [-]")
        ax.set_xlim(0.05, 0.98)
        ax.set_xticks(ticks(0.2, 0.8, 0.2))
        ax.invert_xaxis()
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
        ax.set_title(title, loc="left", fontsize=dm.fs(-1))
        # The two panels share every series, so the full key is drawn once.
        # Panel (b) keeps only the entry that is unique to it.
        if application == "low":
            ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-2.5), handletextpad=0.5, labelspacing=0.25)
        else:
            handles, labels = ax.get_legend_handles_labels()
            keep = [(h, lbl) for h, lbl in zip(handles, labels, strict=True) if "artefact" in lbl]
            if keep:
                ax.legend(
                    [h for h, _ in keep],
                    [lbl for _, lbl in keep],
                    loc="upper left",
                    frameon=False,
                    fontsize=dm.fs(-2.5),
                    handletextpad=0.5,
                )
        panel_letter(ax, letter)
    axes[0].set_ylabel("Declared COP [-]")

    # --- (c) both requirements at once -------------------------------------
    ax = axes[2]
    deviation = level_deviation(points, band)
    for key, (label, color, _) in VARIANT_STYLE.items():
        if key not in parity.index or key not in deviation.index:
            continue
        x = float(parity.loc[key, "MAPE_air_to_water_pct"])
        y = float(deviation[key])
        ax.scatter(
            [x],
            [y],
            s=dm.fs(9) if key == "defaults" else dm.fs(6),
            color=color,
            edgecolor="white",
            linewidth=HAIRLINE,
            zorder=5,
        )
        dx, dy, ha = PANEL_C_LABEL[key]
        ax.annotate(
            label,
            (x, y),
            textcoords="offset points",
            xytext=(dx, dy),
            ha=ha,
            fontsize=dm.fs(-2),
            color=color,
        )
    ax.set_xlabel("Catalogue parity, air-to-water MAPE [%]")
    ax.set_ylabel("EN 14825 distance from\ncertified median [%]")
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax.set_title("Both requirements at once", loc="left", fontsize=dm.fs(-1))
    lo_x, hi_x = ax.get_xlim()
    lo_y, hi_y = ax.get_ylim()
    # Declared ticks rather than a locator -- see scripts/visualization/_dmpl_common.ticks.
    top_x = float(np.ceil(hi_x * 1.10 / 20.0) * 20.0)
    top_y = float(np.ceil(hi_y * 1.12 / 20.0) * 20.0)
    ax.set_xlim(-top_x * 0.06, top_x)
    ax.set_ylim(0.0, top_y)
    ax.set_xticks(ticks(0.0, top_x, 20.0))
    ax.set_yticks(ticks(0.0, top_y, 20.0))
    panel_letter(ax, "c")

    out = static_path("validation_en14825_verdict.svg").with_suffix("")
    finalize(fig, out, mt="7%", formats=("svg", "png"))
    plt.close(fig)
    print(f"wrote {out}.svg")
    print("\nEN 14825 distance from certified median [%]:")
    print(deviation.round(1).to_string())
    print("\ncatalogue parity:")
    print(parity.to_string(float_format=lambda v: f"{v:.2f}"))


if __name__ == "__main__":
    main()
