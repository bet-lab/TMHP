"""Level-1 figures: coverage, fitted surfaces against the compressor data, LOCO per machine.

Writes PNG (300 dpi) + SVG to the directory given by ``--out`` (default: the
coefficient archive ``figures/``).  Plain matplotlib so the script has no
dependency beyond the library's own.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tmhp.compressor_efficiency import (  # noqa: E402
    COEFFICIENT_VERSION,
    ETA_EM_REF,
    eta_isen_default,
    eta_oi_product,
    make_eta_vol,
    speed_factor_em,
)
from validation.compressor_maps.schema import DATA_DIR, REPO_ROOT  # noqa: E402

PALETTE = {
    "copeland_opi": "#1f77b4",
    "cuevas_lebrun_2009": "#d62728",
    "guth_atakan_2023": "#2ca02c",
    "highly_catalogue_2024": "#ff7f0e",
}
LABEL = {
    "copeland_opi": "Copeland OPI (scroll, AHRI 540)",
    "cuevas_lebrun_2009": "Cuevas & Lebrun 2009 (scroll R134a)",
    "guth_atakan_2023": "Guth & Atakan 2023 (scroll R290, published fit)",
    "highly_catalogue_2024": "Highly 2024 (rotary R290, rated)",
}
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 110})


def _load():
    df = pd.read_csv(DATA_DIR / "points_fit_ready.csv", low_memory=False)
    return df[(~df.exclude_fixed) & df.point_ok].copy()


def _save(fig, out: Path, name: str):
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{name}.png", dpi=300, bbox_inches="tight")
    fig.savefig(out / f"{name}.svg", bbox_inches="tight")
    plt.close(fig)


def fig_coverage(df, out):
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    for src, g in df.groupby("source_id"):
        ax.scatter(
            g.PR,
            g.n_star,
            s=8,
            alpha=0.35,
            color=PALETTE[src],
            label=f"{LABEL[src]} — {g.compressor_key.nunique()} machines",
            edgecolors="none",
        )
    ax.set_xlabel("pressure ratio PR")
    ax.set_ylabel("relative speed n* = N / N_rated")
    ax.set_title("F1  Coverage of the standalone-compressor data used for the fit")
    ax.axhline(1.0, color="k", lw=0.5, ls=":")
    ax.legend(fontsize=7, loc="upper right")
    _save(fig, out, "F1_coverage")


def fig_eta_vol(df, out):
    d = df[~df.vdisp_suspect]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4.0))
    ax = axes[0]
    sc = ax.scatter(d.PR, d.eta_vol, c=d.n_star, cmap="viridis", s=8, alpha=0.5, edgecolors="none", vmin=0.25, vmax=1.3)
    pr = np.linspace(1.5, 8, 100)
    for ns, ls in ((1.0, "-"), (0.5, "--"), (0.27, ":")):
        f = make_eta_vol(1.0)
        ax.plot(pr, [f(x, ns) for x in pr], color="k", ls=ls, lw=1.2, label=f"fit, n* = {ns}")
    ax.set_xlabel("pressure ratio PR")
    ax.set_ylabel("volumetric efficiency η_vol")
    ax.set_ylim(0.6, 1.05)
    ax.legend(fontsize=7)
    ax.set_title("(a) η_vol vs PR, colour = n*")
    fig.colorbar(sc, ax=ax, label="n*")
    ax = axes[1]
    # speed dependence at PR 2.5-3.5
    mid = d[d.PR.between(2.5, 3.5)]
    for src, g in mid.groupby("source_id"):
        ax.scatter(g.n_star, g.eta_vol, s=10, alpha=0.5, color=PALETTE[src], label=LABEL[src], edgecolors="none")
    ns = np.linspace(0.2, 1.5, 100)
    ax.plot(ns, [make_eta_vol(1.0)(3.0, x) for x in ns], color="k", lw=1.4, label="fit, PR = 3")
    ax.set_xlabel("relative speed n*")
    ax.set_ylabel("η_vol (PR 2.5–3.5)")
    ax.set_ylim(0.7, 1.05)
    ax.legend(fontsize=6.5)
    ax.set_title("(b) speed dependence at mid lift")
    fig.suptitle(f"F2  Volumetric efficiency — data and adopted correlation ({COEFFICIENT_VERSION})", y=1.02)
    _save(fig, out, "F2_eta_vol")


def fig_eta_oi(df, out):
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4.0))
    ax = axes[0]
    sc = ax.scatter(
        df.PR, df.eta_oi, c=df.n_star, cmap="viridis", s=8, alpha=0.5, edgecolors="none", vmin=0.25, vmax=1.3
    )
    pr = np.linspace(1.5, 8, 100)
    for ns, ls in ((1.0, "-"), (0.5, "--"), (0.27, ":")):
        ax.plot(pr, [eta_oi_product(x, ns) for x in pr], color="k", ls=ls, lw=1.2, label=f"fit, n* = {ns}")
    ax.set_xlabel("pressure ratio PR")
    ax.set_ylabel("η_isen · η_em (electrical → isentropic)")
    ax.set_ylim(0.3, 0.85)
    ax.legend(fontsize=7)
    ax.set_title("(a) product vs PR, colour = n*")
    fig.colorbar(sc, ax=ax, label="n*")
    ax = axes[1]
    ns = np.linspace(0.2, 1.5, 100)
    ax.plot(
        ns, [ETA_EM_REF * speed_factor_em(x) for x in ns], color="k", lw=1.4, label=f"η_em = {ETA_EM_REF:.3f} · s(n*)"
    )
    cu = df[(df.source_id == "cuevas_lebrun_2009") & df.eta_em.notna()]
    ax.scatter(
        cu.n_star,
        cu.eta_em,
        s=18,
        color=PALETTE["cuevas_lebrun_2009"],
        label="Cuevas & Lebrun: measured η_em (T_dis split)",
        zorder=3,
    )
    gu = df[(df.source_id == "guth_atakan_2023")]
    ax.scatter(
        gu.n_star,
        gu.eta_em,
        s=10,
        alpha=0.5,
        color=PALETTE["guth_atakan_2023"],
        label="Guth & Atakan: published η_comp (other split)",
    )
    ax.set_xlabel("relative speed n*")
    ax.set_ylabel("electro-mechanical efficiency η_em")
    ax.set_ylim(0.6, 1.0)
    ax.legend(fontsize=6.5, loc="lower right")
    ax.set_title("(b) speed factor and the measured split anchor")
    fig.suptitle(f"F3  Electrical-to-isentropic product and its split ({COEFFICIENT_VERSION})", y=1.02)
    _save(fig, out, "F3_eta_oi_split")


def fig_eta_isen(out):
    fig, ax = plt.subplots(figsize=(6.0, 3.8))
    pr = np.linspace(1.2, 12, 200)
    ax.plot(
        pr,
        [eta_isen_default(x) for x in pr],
        color="k",
        lw=1.6,
        label=f"adopted ({COEFFICIENT_VERSION}): (A − B·PR − C/PR)/η_em,ref",
    )
    ax.plot(pr, np.maximum(0.25, 0.90 - 0.02 * pr), color="grey", lw=1.2, ls="--", label="pre-refit v1: 0.90 − 0.02·PR")
    ax.axvspan(1.5, 8.0, color="#1f77b4", alpha=0.06, label="data range (PR 1.5–8)")
    ax.set_xlabel("pressure ratio PR")
    ax.set_ylabel("isentropic efficiency η_isen")
    ax.set_ylim(0.2, 1.0)
    ax.legend(fontsize=7)
    ax.set_title("F4  Isentropic efficiency: adopted shape vs pre-refit")
    _save(fig, out, "F4_eta_isen")


def fig_loco(out):
    pooled = pd.read_csv(DATA_DIR / "loco_pooled.csv")
    sel = json.loads((DATA_DIR / "selected.json").read_text())
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.8))
    for ax, kind, title in zip(axes, ("eta_vol", "eta_oi"), ("η_vol", "η_isen·η_em"), strict=True):
        d = pooled[pooled.kind == kind].sort_values("loco_wmape_pct", ascending=False)
        colors = [
            "#d62728" if f == "legacy" else ("#2ca02c" if f == sel[kind]["family"] else "#9ecae1") for f in d.family
        ]
        ax.barh(d.family, d.loco_wmape_pct, color=colors)
        ax.set_xlabel("leave-one-compressor-out weighted MAPE [%]")
        ax.set_title(f"{title}: candidate families (green = adopted, red = pre-refit)")
        for y, v in enumerate(d.loco_wmape_pct):
            ax.text(v + 0.05, y, f"{v:.2f}", va="center", fontsize=7)
    fig.suptitle("F5  Cross-validated error of every candidate family", y=1.02)
    _save(fig, out, "F5_loco_families")


def fig_strata(out):
    strata = pd.read_csv(DATA_DIR / "loco_strata.csv")
    sel = json.loads((DATA_DIR / "selected.json").read_text())
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for ax, kind, title in zip(axes, ("eta_vol", "eta_oi"), ("η_vol", "η_isen·η_em"), strict=True):
        new = strata[(strata.kind == kind) & (strata.family == sel[kind]["family"])].set_index("stratum")
        old = strata[(strata.kind == kind) & (strata.family == "legacy")].set_index("stratum")
        idx = [s for s in new.index if not s.startswith("source=")] + [s for s in new.index if s.startswith("source=")]
        y = np.arange(len(idx))
        ax.barh(y + 0.2, old.loc[idx].loco_wmape_pct, height=0.4, color="#d62728", label="pre-refit v1")
        ax.barh(
            y - 0.2, new.loc[idx].loco_wmape_pct, height=0.4, color="#2ca02c", label=f"adopted {sel[kind]['family']}"
        )
        ax.set_yticks(y)
        ax.set_yticklabels([s.replace("source=", "").replace("_", " ") for s in idx], fontsize=7)
        ax.set_xlabel("LOCO weighted MAPE [%]")
        ax.set_title(title)
        ax.legend(fontsize=7)
    fig.suptitle("F6  Error by stratum (refrigerant, type, speed, lift, source)", y=1.02)
    _save(fig, out, "F6_loco_strata")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO_ROOT / "validation" / "coefficients" / COEFFICIENT_VERSION / "figures"))
    a = ap.parse_args()
    out = Path(a.out)
    df = _load()
    fig_coverage(df, out)
    fig_eta_vol(df, out)
    fig_eta_oi(df, out)
    fig_eta_isen(out)
    fig_loco(out)
    fig_strata(out)
    print(f"figures -> {out}")


if __name__ == "__main__":
    main()
