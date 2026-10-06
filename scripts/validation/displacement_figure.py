"""How TMHP decides a compressor's swept displacement, and what that is worth.

Four panels, answering the four questions someone reading the default would
ask: what does the fluid contribute, what does that do to the answer, where did
the one free number come from, and how much does that number matter.
"""

from __future__ import annotations

import sys
from pathlib import Path

import CoolProp.CoolProp as CP
import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np

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
)

from tmhp.compressor_speed import (  # noqa: E402
    RATED_POINT_AIR_TO_WATER,
    default_displacement,
)

REFRIGERANTS = ("R32", "R410A", "R290", "R134a", "R1234yf")
REF_COLOR = {
    "R32": COLORS["accent"],
    "R410A": COLORS["warm"],
    "R290": COLORS["ess"],
    "R134a": COLORS["load"],
    # violet5 sits 28 deg from indigo6 on the hue wheel, so R32 and R1234yf
    # read as the same colour in panels (c) and (d). pink6 separates them.
    "R1234yf": COLORS["accent3"],
}

#: Panasonic Aquarea units whose compressor displacement the manufacturer's
#: compressor division publishes. The only line in the evidence base where the
#: answer can be checked against a printed number.
PANASONIC = (
    ("WH-MXC09", "R32", 9.0, 42.0),
    ("WH-MXC12", "R32", 12.0, 42.0),
    ("WH-MXC16", "R32", 16.0, 65.0),
    ("WH-WXG09", "R290", 9.0, 81.0),
    ("WH-WXG12", "R290", 12.0, 81.0),
    ("WH-WXG16", "R290", 16.0, 81.0),
    ("WH-UQ09", "R410A", 9.0, 42.4),
    ("WH-UQ12", "R410A", 12.0, 42.4),
    ("WH-UQ16", "R410A", 16.0, 65.0),
)


def volumetric_capacity(
    refrigerant: str, t_evap_c: float, t_cond_c: float, superheat: float = 5.0, subcool: float = 5.0
) -> tuple[float, float, float]:
    """Suction density, condenser-side enthalpy drop, and their product."""
    t_evap_k, t_cond_k = t_evap_c + 273.15, t_cond_c + 273.15
    p_evap = CP.PropsSI("P", "T", t_evap_k, "Q", 1, refrigerant)
    p_cond = CP.PropsSI("P", "T", t_cond_k, "Q", 1, refrigerant)
    t_suction = t_evap_k + superheat
    h1 = CP.PropsSI("H", "T", t_suction, "P", p_evap, refrigerant)
    s1 = CP.PropsSI("S", "T", t_suction, "P", p_evap, refrigerant)
    rho1 = CP.PropsSI("D", "T", t_suction, "P", p_evap, refrigerant)
    h3 = CP.PropsSI("H", "T", t_cond_k - subcool, "P", p_cond, refrigerant)
    h2s = CP.PropsSI("H", "P", p_cond, "S", s1, refrigerant)
    dh = (h1 - h3) + (h2s - h1) / 0.70
    return rho1, dh, rho1 * dh


def implied_rated_speed(refrigerant: str, capacity_kw: float, displacement_cc: float) -> float:
    """Rated speed that reproduces a published displacement."""
    point = RATED_POINT_AIR_TO_WATER
    _, dh, q_vol = volumetric_capacity(refrigerant, point.T_evap_C, point.T_cond_C)
    mass_flow = capacity_kw * 1000.0 / dh
    rho1, _, _ = volumetric_capacity(refrigerant, point.T_evap_C, point.T_cond_C)
    return mass_flow / (rho1 * 0.90 * displacement_cc * 1e-6)


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.displacement")
    point = RATED_POINT_AIR_TO_WATER
    fig, axes = plt.subplots(
        2,
        2,
        figsize=dm.figsize("17cm", 0.80),
        gridspec_kw={"hspace": 0.42, "wspace": 0.32},
    )

    # --- (a) what the fluid contributes -----------------------------------
    # Delta-h [J/kg], rho_1 [kg/m3] and their product [J/m3] carry three
    # different units, so plotting them against one shared axis makes the bar
    # heights mean nothing next to each other. Normalising every property to
    # its R32 value puts them on one dimensionless axis and states the panel's
    # actual claim: the product is what varies, and by how much.
    ax = axes[0, 0]
    props = np.array(
        [volumetric_capacity(r, point.T_evap_C, point.T_cond_C) for r in REFRIGERANTS]
    )  # columns: rho_1, delta-h, rho_1 * delta-h
    relative = props / props[REFRIGERANTS.index("R32")]
    x = np.arange(len(REFRIGERANTS))
    # A lightness ramp rather than three hues: the refrigerant palette used in
    # (b)-(d) must keep its meaning, so this panel stays achromatic and the
    # darkest bar is the product of the two lighter ones.
    series = (
        (1, r"enthalpy rise $\Delta h$", COLORS["band20"]),
        (0, r"suction density $\rho_1$", COLORS["muted"]),
        (2, r"volumetric capacity $\rho_1\Delta h$", COLORS["ink"]),
    )
    for offset, (col, label, colour) in zip((-0.27, 0.0, 0.27), series, strict=True):
        ax.bar(
            x + offset,
            relative[:, col],
            0.25,
            color=colour,
            edgecolor=COLORS["ink"],
            linewidth=0.5,
            label=label,
        )
    ax.axhline(1.0, color=COLORS["muted"], linestyle=":", linewidth=0.8, zorder=0)
    ax.set_xticks(x)
    ax.set_xticklabels(REFRIGERANTS, fontsize=dm.fs(-2))
    ax.set_ylabel("relative to R32 ( = 1)")
    ax.set_ylim(0, relative.max() * 1.45)
    ax.set_yticks([0.0, 0.5, 1.0, 1.5, 2.0])
    ax.legend(frameon=False, fontsize=dm.fs(-3), loc="upper left", ncol=1, handlelength=1.2)
    ax.set_title("The fluid does the work", loc="left", fontsize=dm.fs(0))
    ax.grid(True, axis="y", alpha=0.25, linewidth=GRIDLINE)
    panel_letter(ax, "a")

    # --- (b) what that does to the displacement ---------------------------
    ax = axes[0, 1]
    per_kw = [default_displacement(1000.0, r) * 1e6 for r in REFRIGERANTS]
    bars = ax.bar(x, per_kw, 0.6, color=[REF_COLOR[r] for r in REFRIGERANTS])
    for rect, value in zip(bars, per_kw, strict=True):
        ax.text(
            rect.get_x() + rect.get_width() / 2,
            value + 0.15,
            f"{value:.1f}",
            ha="center",
            fontsize=dm.fs(-2),
            color=COLORS["ink"],
        )
    ax.axhline(42.0 / 9.0, linestyle="--", color=COLORS["hot"], linewidth=dm.lw(0))
    # Anchored left: R32 and R410A are the only bars that sit below the old
    # rule, so this is the one stretch of the panel the annotation can occupy
    # without being drawn over a bar.
    ax.text(
        -0.38,
        42.0 / 9.0 + 0.35,
        "old fixed rule\n(same for every fluid)",
        ha="left",
        va="bottom",
        fontsize=dm.fs(-3),
        color=COLORS["hot"],
    )
    ax.set_xticks(x)
    ax.set_xticklabels(REFRIGERANTS, fontsize=dm.fs(-2))
    ax.set_ylabel(r"displacement [cm$^3$/rev per kW]")
    ax.set_title("A factor of 2.7 across fluids", loc="left", fontsize=dm.fs(0))
    ax.grid(True, axis="y", alpha=0.25, linewidth=GRIDLINE)
    panel_letter(ax, "b")

    # --- (c) where the one free number came from --------------------------
    ax = axes[1, 0]
    for cap, marker in ((9.0, "o"), (12.0, "s"), (16.0, "^")):
        xs, ys, cs = [], [], []
        for _model, ref, capacity, disp in PANASONIC:
            if capacity != cap:
                continue
            xs.append(REFRIGERANTS.index(ref))
            ys.append(implied_rated_speed(ref, capacity, disp))
            cs.append(REF_COLOR[ref])
        ax.scatter(
            xs,
            ys,
            s=dm.fs(6),
            marker=marker,
            c=cs,
            zorder=4,
            edgecolor="white",
            linewidth=HAIRLINE,
        )
        # Colour already carries refrigerant here, so the capacity legend uses
        # a neutral proxy marker rather than borrowing one fluid's colour and
        # implying that size is what the colour means.
        ax.scatter(
            [],
            [],
            s=dm.fs(6),
            marker=marker,
            color=COLORS["muted"],
            edgecolor="white",
            linewidth=HAIRLINE,
            label=f"{cap:.0f} kW",
        )
    ax.axhline(point.rps, color=COLORS["ink"], linestyle="--", linewidth=dm.lw(0))
    ax.text(
        2.45,
        point.rps + 1.2,
        f"declared default {point.rps:.0f} rev/s",
        fontsize=dm.fs(-2),
        color=COLORS["ink"],
        ha="right",
    )
    ax.set_xticks(range(3))
    ax.set_xticklabels(REFRIGERANTS[:3], fontsize=dm.fs(-2))
    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(30, 62)
    ax.set_yticks([30, 40, 50, 60])
    ax.set_ylabel("implied rated speed [rev/s]")
    ax.legend(frameon=False, fontsize=dm.fs(-2), loc="upper left", ncol=3, columnspacing=0.8)
    ax.set_title("Inverted from 9 published displacements", loc="left", fontsize=dm.fs(0))
    ax.grid(True, axis="y", alpha=0.25, linewidth=GRIDLINE)
    panel_letter(ax, "c")

    # --- (d) what the rule costs against the printed numbers --------------
    ax = axes[1, 1]
    predicted, published, colors = [], [], []
    for _model, ref, capacity, disp in PANASONIC:
        predicted.append(default_displacement(capacity * 1000.0, ref) * 1e6)
        published.append(disp)
        colors.append(REF_COLOR[ref])
    lo, hi = 20.0, 100.0
    line = np.linspace(lo, hi, 100)
    ax.fill_between(line, 0.80 * line, 1.20 * line, color=COLORS["band20"], alpha=0.55, linewidth=0, label="±20 %")
    ax.plot(line, line, linestyle=":", color=COLORS["muted"], linewidth=dm.lw(0))
    ax.scatter(published, predicted, s=dm.fs(6), c=colors, zorder=4, edgecolor="white", linewidth=HAIRLINE)
    # Colour is the only thing telling these nine dots apart, and (d) is the
    # one panel with no refrigerant on an axis to read it off. Carry the key
    # here rather than leaving the reader to infer it from (b).
    for ref in ("R32", "R410A", "R290"):
        ax.scatter([], [], s=dm.fs(6), color=REF_COLOR[ref], edgecolor="white", linewidth=HAIRLINE, label=ref)
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_aspect("equal", adjustable="box")
    # 20..100 in tens is 9 ticks on a 2 in axis, which the dartwork visual
    # check flags as crowded; twenties label the same range legibly.
    ax.set_xticks([20, 40, 60, 80, 100])
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_xlabel(r"published displacement [cm$^3$/rev]")
    ax.set_ylabel(r"rule [cm$^3$/rev]")
    ax.legend(frameon=False, fontsize=dm.fs(-3), loc="lower right", handletextpad=0.4, labelspacing=0.3)
    ax.set_title("Against the printed numbers", loc="left", fontsize=dm.fs(0))
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    panel_letter(ax, "d")

    out = static_path("compressor_displacement.svg").with_suffix("")
    finalize(fig, out, mt="4%")
    plt.close(fig)
    print(f"wrote {out}.svg")
    for _model, ref, capacity, disp in PANASONIC:
        got = default_displacement(capacity * 1000.0, ref) * 1e6
        print(
            f"  {_model:<10}{ref:<7}{capacity:>5.0f} kW  published {disp:>5.1f}  "
            f"rule {got:>5.1f}  ({got / disp - 1.0:+.0%})"
        )


if __name__ == "__main__":
    main()
