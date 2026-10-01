"""Figures of the 2026-09-24 refit (plan v3): evidence, fitted shapes, gates.

G1  within-machine speed contrasts of the efficiency product against pressure
    ratio, low-speed records, one panel per source -- the evidence for a
    leakage-type (lift x speed) low-speed loss
G2  the three fitted efficiencies against n* at PR 2 / 3 / 4.5, previous
    version (frozen v2026-09-15b) against current
G3  fixed-boundary PLR-COP, current / heat-exchanger-only / previous, one
    panel per sweep case
G4  Gate A: rated-point relative error per unit, and catalogue residual
    against n* and PR

Writes SVG + PNG next to the coefficient archive (``--out``).
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scripts.visualization._dmpl_common import (  # noqa: E402
    COLORS,
    GRIDLINE,
    HAIRLINE,
    apply_style,
    finalize,
    panel_letter,
    ticks,
)

from tmhp import compressor_efficiency as cur  # noqa: E402
from validation.compressor_maps.schema import DATA_DIR, REPO_ROOT  # noqa: E402

PLR_DIR = REPO_ROOT / "validation" / "data" / "fixed_boundary_plr"
RESULTS = REPO_ROOT / "validation" / "results" / "gate_a"
PREVIOUS = REPO_ROOT / "validation" / "coefficients" / "v2026-09-15b" / "frozen_forms.py"
SOURCE_LABEL = {
    "copeland_opi": "Copeland OPI scrolls",
    "cuevas_lebrun_2009": "Cuevas & Lebrun 2009",
    "guth_atakan_2023": "Guth & Atakan 2023",
    "shao_2004": "Shao et al. 2004 rotaries",
}


def _prev():
    spec = importlib.util.spec_from_file_location("tmhp_frozen_v20260915b", PREVIOUS)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _save(fig, out: Path, name: str, **margins) -> None:
    out.mkdir(parents=True, exist_ok=True)
    finalize(fig, out / name, formats=("svg", "png"), **margins)
    plt.close(fig)


def g1_contrasts(out: Path) -> None:
    d = pd.read_csv(DATA_DIR / "within_machine_contrasts.csv")
    lo = d[(d.n_star < 0.75)].copy()
    lo["n_bin"] = pd.cut(
        lo.n_star, [0, 0.3, 0.45, 0.6, 0.75], labels=["n* < 0.30", "0.30–0.45", "0.45–0.60", "0.60–0.75"]
    )
    srcs = [s for s in SOURCE_LABEL if s in set(lo.source_id)]
    fig, axes = plt.subplots(1, len(srcs), figsize=dm.figsize("17cm", 0.36), sharey=True)
    cmap = plt.get_cmap("viridis")
    for ax, src in zip(np.atleast_1d(axes), srcs, strict=True):
        g = lo[lo.source_id == src]
        for i, (lbl, gg) in enumerate(g.groupby("n_bin", observed=True)):
            gg = gg.assign(pb=pd.cut(gg.PR, np.arange(1.0, 8.5, 0.5)))
            med = gg.groupby("pb", observed=True).agg(
                PR=("PR", "median"), d=("d_ln_eta_oi", "median"), n=("PR", "size")
            )
            med = med[med.n >= 2]
            ax.scatter(gg.PR, 100 * gg.d_ln_eta_oi, s=4, alpha=0.18, color=cmap(0.15 + 0.25 * i), edgecolors="none")
            ax.plot(med.PR, 100 * med.d, "o-", ms=3.2, lw=dm.lw(0), color=cmap(0.15 + 0.25 * i), label=str(lbl))
        ax.axhline(0, color=COLORS["muted"], lw=HAIRLINE, ls=":")
        ax.set_xlim(1.0, 8.0)
        ax.set_xticks(ticks(1.0, 8.0, 1.0))
        ax.set_xlabel("Pressure ratio [-]")
        ax.set_title(f"{SOURCE_LABEL[src]} ({g.compressor_key.nunique()})", fontsize=dm.fs(-1.5), loc="left")
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
        ax.legend(loc="lower left", frameon=False, fontsize=dm.fs(-3), title="record", title_fontsize=dm.fs(-3))
        panel_letter(ax, "abcd"[srcs.index(src)])
    axes[0].set_ylim(-60, 25)
    axes[0].set_yticks(ticks(-60, 20, 20))
    axes[0].set_ylabel("Δ ln(η_is·η_em) vs rated record [%]")
    _save(fig, out, "G1_within_machine_lowspeed", mt="6%")


def g2_shapes(out: Path) -> None:
    prev = _prev()
    rated = 50.0
    ns = np.linspace(0.2, 2.0, 120)
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.36))
    prs = (2.0, 3.0, 4.5)
    styles = [(0, (1, 1.2)), "solid", (0, (4, 1.6))]
    ev_c, ei_c, ee_c = cur.make_eta_vol(rated), cur.make_eta_isen(rated), cur.make_eta_em(rated)
    ev_p, ee_p = prev.make_eta_vol(rated), prev.make_eta_em(rated)
    for ax, (name, fc, fp) in zip(
        axes,
        [
            ("η_vol", lambda pr, n: ev_c(pr, n * rated), lambda pr, n: ev_p(pr, n * rated)),
            ("η_is", lambda pr, n: ei_c(pr, n * rated), lambda pr, n: prev.eta_isen_default(pr)),
            ("η_em", lambda pr, n: ee_c(pr, n * rated), lambda pr, n: ee_p(pr, n * rated)),
        ],
        strict=True,
    ):
        for pr, ls in zip(prs, styles, strict=True):
            ax.plot(ns, [fp(pr, n) for n in ns], color=COLORS["muted"], ls=ls, lw=dm.lw(-0.5))
            ax.plot(ns, [fc(pr, n) for n in ns], color=COLORS["ink"], ls=ls, lw=dm.lw(0.5), label=f"PR {pr:g}")
        ax.set_xlim(0.2, 2.0)
        ax.set_xticks(ticks(0.2, 2.0, 0.3))
        ax.set_xlabel("Relative speed n* [-]")
        ax.set_ylabel(f"{name} [-]")
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
        panel_letter(ax, "abc"[list(axes).index(ax)])
    axes[0].set_ylim(0.6, 1.0)
    axes[1].set_ylim(0.3, 0.9)
    axes[2].set_ylim(0.6, 1.0)
    axes[1].legend(
        loc="lower right",
        frameon=False,
        fontsize=dm.fs(-2.5),
        title="current (grey: v2026-09-15b)",
        title_fontsize=dm.fs(-3),
    )
    _save(fig, out, "G2_efficiency_shapes", mt="4%")


def g3_plr(out: Path) -> None:
    d = pd.read_csv(PLR_DIR / "decomposition.csv")
    cases = list(d.groupby(["model_class", "duty", "refrigerant", "t_outdoor_C", "t_sink_C"]).groups)
    n = len(cases)
    ncol = 3
    nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=dm.figsize("17cm", 0.30 * nrow), sharex=True)
    axes = np.atleast_1d(axes).ravel()
    style = {
        "current": (COLORS["ink"], "solid", "current"),
        "hx_only": (COLORS.get("accent", "tab:orange"), (0, (4, 1.6)), "heat exchangers only"),
        "previous": (COLORS["muted"], "solid", "v2026-09-15b"),
    }
    for ax, key in zip(axes, cases, strict=False):
        g = d[
            (d.model_class == key[0])
            & (d.duty == key[1])
            & (d.refrigerant == key[2])
            & (d.t_outdoor_C == key[3])
            & (d.t_sink_C == key[4])
        ]
        for var, (c, ls, lbl) in style.items():
            gv = g[(g.variant == var) & (g.failure_reason == "none")].sort_values("plr_request")
            mod = gv[gv.capacity_clamped.isna()]
            floor = gv[gv.capacity_clamped == "min"]
            ax.plot(mod.plr_request, mod.cop_sys, color=c, ls=ls, lw=dm.lw(0), marker="o", ms=2.6, label=lbl)
            if len(floor):
                ax.plot(
                    floor.plr_request, floor.cop_sys, color=c, ls="none", marker="o", ms=2.6, mfc="white", mew=HAIRLINE
                )
        ax.set_title(f"{key[0]} {key[1]} {key[2]} {key[3]:g}/{key[4]:g} °C", fontsize=dm.fs(-1.5), loc="left")
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
        ax.set_xlim(0.1, 1.0)
        ax.set_xticks(ticks(0.2, 1.0, 0.2))
        panel_letter(ax, "abcdefgh"[list(axes).index(ax)])
    for ax in axes[n:]:
        ax.axis("off")
    for ax in axes[-ncol:]:
        ax.set_xlabel("Requested part-load ratio [-]")
    for ax in axes[::ncol]:
        ax.set_ylabel("COP [-]")
    axes[0].legend(loc="lower right", frameon=False, fontsize=dm.fs(-2.5))
    _save(fig, out, "G3_plr_cop_decomposition", mt="4%")


def g4_gate_a(out: Path) -> None:
    r = pd.read_csv(RESULTS / "rated_points.csv")
    r = r[r.status == "adopted"]
    b = pd.read_csv(RESULTS / "residual_bins.csv")
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.36))
    ax = axes[0]
    lbl = r.unit.str.replace(" / ", "/") + "\n" + r.rating.str.split(" ").str[0]
    colors = [COLORS["ink"] if ok else "tab:red" for ok in r.within_10pct]
    ax.barh(np.arange(len(r)), r.rel_err_pct, color=colors, height=0.7)
    ax.set_yticks(np.arange(len(r)))
    ax.set_yticklabels(lbl, fontsize=dm.fs(-3.5))
    ax.axvline(-10, color=COLORS["muted"], lw=HAIRLINE, ls=":")
    ax.axvline(10, color=COLORS["muted"], lw=HAIRLINE, ls=":")
    ax.axvline(0, color=COLORS["muted"], lw=HAIRLINE)
    ax.set_xlim(-20, 20)
    ax.set_xticks(ticks(-20, 20, 10))
    ax.set_xlabel("Rated-point COP error [%]")
    ax.invert_yaxis()
    panel_letter(ax, "a")
    for ax, axis, xl in zip(axes[1:], ("n_star", "pressure_ratio"), ("n* bin", "pressure-ratio bin"), strict=True):
        bb = b[b.axis == axis]
        for cls, mk in (("ASHPB", "s"), ("ASHP", "o")):
            g = bb[bb.model_class == cls]
            x = np.arange(len(g))
            ax.errorbar(
                x + (0.1 if cls == "ASHP" else -0.1),
                g.bias_pct,
                yerr=g.sd_pct,
                fmt=mk,
                ms=3.5,
                lw=HAIRLINE,
                capsize=2,
                color=COLORS["ink"] if cls == "ASHPB" else COLORS["muted"],
                label=f"{cls} (n = {int(g.n.sum())})",
            )
            ax.set_xticks(x)
            ax.set_xticklabels(g["bin"], rotation=45, ha="right", fontsize=dm.fs(-3))
        ax.axhline(0, color=COLORS["muted"], lw=HAIRLINE)
        ax.set_ylim(-30, 30)
        ax.set_yticks(ticks(-30, 30, 10))
        ax.set_ylabel("COP bias ± sd [%]")
        ax.set_xlabel(xl)
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
        ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-3))
        panel_letter(ax, "bc"[list(axes[1:]).index(ax)])
    _save(fig, out, "G4_gate_a", ml="6%", mt="4%", mb="10%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out", default=str(REPO_ROOT / "validation" / "coefficients" / cur.COEFFICIENT_VERSION / "figures")
    )
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()
    apply_style()
    out = Path(a.out)
    todo = {"G1": g1_contrasts, "G2": g2_shapes, "G3": g3_plr, "G4": g4_gate_a}
    for k, fn in todo.items():
        if a.only and k not in a.only:
            continue
        try:
            fn(out)
            print("wrote", k)
        except Exception as exc:  # noqa: BLE001
            print(f"{k} skipped: {exc}")


if __name__ == "__main__":
    main()
