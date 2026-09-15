"""Level-1 figures (dartwork-mpl ``scientific`` preset): coverage, fitted
correlations against the compressor data, cross-validation by family and by
stratum.  Writes SVG + PNG to ``--out`` (default: the coefficient archive).

Every axis declares its ticks; quantities are scaled so tick labels carry at
most three significant digits.
"""

from __future__ import annotations

import argparse
import json
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

from tmhp.compressor_efficiency import (  # noqa: E402
    COEFFICIENT_VERSION,
    ETA_EM_REF,
    ETA_OI_A,
    ETA_OI_B,
    ETA_OI_C,
    eta_isen_default,
    eta_oi_product,
    make_eta_vol,
    speed_factor_em,
)
from validation.compressor_maps.schema import DATA_DIR, REPO_ROOT  # noqa: E402

SOURCE_STYLE = {
    "copeland_opi": (COLORS["accent"], "Copeland OPI, scroll (AHRI 540 maps)"),
    "shao_2004": (COLORS["warm"], "Shao 2004, rotary (Mitsubishi / SANYO / Hitachi maps)"),
    "cuevas_lebrun_2009": (COLORS["hot"], "Cuevas & Lebrun 2009, scroll R134a (tests)"),
    "guth_atakan_2023": (COLORS["ess"], "Guth & Atakan 2023, scroll R290 (published fit)"),
    "highly_catalogue_2024": (COLORS["pv"], "Highly 2024, rotary R290 (rated points)"),
}
LEGACY_LABEL = "pre-refit v1"


def _load() -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / "points_fit_ready.csv", low_memory=False)
    return df[(~df.exclude_fixed) & df.point_ok].copy()


def _save(fig, out: Path, name: str, **margins) -> None:
    out.mkdir(parents=True, exist_ok=True)
    finalize(fig, out / name, formats=("svg", "png"), **margins)
    plt.close(fig)


def _pr_axis(ax) -> None:
    ax.set_xlim(1.0, 8.0)
    ax.set_xticks(ticks(1.0, 8.0, 1.0))
    ax.set_xlabel("Pressure ratio [-]")


def fig_coverage(df: pd.DataFrame, out: Path) -> None:
    fig, ax = plt.subplots(figsize=dm.figsize("12cm", "standard"))
    for src, g in df.groupby("source_id"):
        color, label = SOURCE_STYLE[src]
        ax.scatter(
            g.PR,
            g.n_star,
            s=dm.fs(2),
            alpha=0.35,
            color=color,
            edgecolors="none",
            label=f"{label} – {g.compressor_key.nunique()} machine{'s' if g.compressor_key.nunique() > 1 else ''}",
        )
    ax.axhline(1.0, color=COLORS["muted"], lw=HAIRLINE, ls=":")
    _pr_axis(ax)
    ax.set_ylim(0.0, 2.2)
    ax.set_yticks(ticks(0.0, 2.0, 0.5))
    ax.set_ylabel("Relative speed n* = N / N_rated [-]")
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax.legend(loc="upper right", frameon=False, fontsize=dm.fs(-2.5), handletextpad=0.4, labelspacing=0.3)
    _save(fig, out, "F1_coverage")


def fig_eta_vol(df: pd.DataFrame, out: Path) -> None:
    d = df[~df.vdisp_suspect]
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.42), gridspec_kw={"wspace": 0.5})
    ax = axes[0]
    sc = ax.scatter(
        d.PR, d.eta_vol, c=d.n_star, cmap="viridis", s=dm.fs(2), alpha=0.5, edgecolors="none", vmin=0.25, vmax=1.5
    )
    pr = np.linspace(1.0, 8.0, 100)
    f = make_eta_vol(1.0)
    for ns, ls in ((1.0, "solid"), (0.5, (0, (4, 1.6))), (0.27, (0, (1, 1.2)))):
        ax.plot(pr, [f(x, ns) for x in pr], color=COLORS["ink"], ls=ls, lw=dm.lw(0), label=f"fit, n* = {ns:g}")
    _pr_axis(ax)
    ax.set_ylim(0.6, 1.05)
    ax.set_yticks(ticks(0.6, 1.0, 0.1))
    ax.set_ylabel("Volumetric efficiency η_vol [-]")
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax.legend(loc="lower left", frameon=False, fontsize=dm.fs(-2.5))
    cb = fig.colorbar(sc, ax=ax, pad=0.02, fraction=0.05)
    cb.set_label("n* [-]")
    cb.set_ticks(ticks(0.25, 1.5, 0.25))
    panel_letter(ax, "a")

    ax = axes[1]
    mid = d[d.PR.between(2.5, 3.5)]
    for src, g in mid.groupby("source_id"):
        color, label = SOURCE_STYLE[src]
        ax.scatter(
            g.n_star, g.eta_vol, s=dm.fs(2.5), alpha=0.55, color=color, edgecolors="none", label=label.split(",")[0]
        )
    ns = np.linspace(0.2, 2.1, 100)
    ax.plot(ns, [f(3.0, x) for x in ns], color=COLORS["ink"], lw=dm.lw(1), label="fit, PR = 3")
    ax.set_xlim(0.0, 2.2)
    ax.set_xticks(ticks(0.0, 2.0, 0.5))
    ax.set_xlabel("Relative speed n* [-]")
    ax.set_ylim(0.6, 1.05)
    ax.set_yticks(ticks(0.6, 1.0, 0.1))
    ax.set_ylabel("η_vol at PR 2.5–3.5 [-]")
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-2.5))
    panel_letter(ax, "b")
    _save(fig, out, "F2_eta_vol", mt="4%")


def fig_eta_oi(df: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.42), gridspec_kw={"wspace": 0.5})
    ax = axes[0]
    sc = ax.scatter(
        df.PR, df.eta_oi, c=df.n_star, cmap="viridis", s=dm.fs(2), alpha=0.5, edgecolors="none", vmin=0.25, vmax=1.5
    )
    pr = np.linspace(1.0, 8.0, 100)
    for ns, ls in ((1.0, "solid"), (0.5, (0, (4, 1.6))), (0.27, (0, (1, 1.2)))):
        ax.plot(
            pr, [eta_oi_product(x, ns) for x in pr], color=COLORS["ink"], ls=ls, lw=dm.lw(0), label=f"fit, n* = {ns:g}"
        )
    _pr_axis(ax)
    ax.set_ylim(0.3, 0.9)
    ax.set_yticks(ticks(0.3, 0.9, 0.1))
    ax.set_ylabel("η_isen · η_em, electrical-to-isentropic [-]")
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax.legend(loc="upper right", frameon=False, fontsize=dm.fs(-2.5))
    cb = fig.colorbar(sc, ax=ax, pad=0.02, fraction=0.05)
    cb.set_label("n* [-]")
    cb.set_ticks(ticks(0.25, 1.5, 0.25))
    panel_letter(ax, "a")

    ax = axes[1]
    ns = np.linspace(0.2, 2.1, 100)
    g_pr = lambda pr: ETA_OI_A - ETA_OI_B * pr - ETA_OI_C / pr  # noqa: E731
    rec = (
        df.assign(sf=df.eta_oi / df.PR.map(g_pr))
        .groupby(["source_id", "compressor_key", "N_rps"], as_index=False)
        .agg(n_star=("n_star", "median"), sf=("sf", "median"))
    )
    for src, gg in rec.groupby("source_id"):
        color, label = SOURCE_STYLE[src]
        ax.scatter(gg.n_star, gg.sf, s=dm.fs(2.5), alpha=0.6, color=color, edgecolors="none", label=label.split(",")[0])
    ax.plot(ns, [speed_factor_em(x) for x in ns], color=COLORS["ink"], lw=dm.lw(1), label="fit s(n*)")
    cu = df[(df.source_id == "cuevas_lebrun_2009") & df.eta_em.notna()]
    ax.scatter(
        cu.n_star,
        cu.eta_em / ETA_EM_REF,
        s=dm.fs(3),
        marker="D",
        color=COLORS["hot"],
        edgecolors="white",
        linewidth=HAIRLINE,
        zorder=5,
        label="Cuevas & Lebrun: measured η_em / 0.936",
    )
    ax.set_xlim(0.0, 2.2)
    ax.set_xticks(ticks(0.0, 2.0, 0.5))
    ax.set_xlabel("Relative speed n* [-]")
    ax.set_ylim(0.4, 1.4)
    ax.set_yticks(ticks(0.4, 1.4, 0.2))
    ax.set_ylabel("Speed factor η_oi / g(PR) [-]")
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax.legend(loc="upper right", frameon=False, fontsize=dm.fs(-3), ncol=2, handletextpad=0.3, columnspacing=0.8)
    panel_letter(ax, "b")
    _save(fig, out, "F3_eta_oi_split", mt="4%")


def fig_eta_isen(out: Path) -> None:
    fig, ax = plt.subplots(figsize=dm.figsize("11cm", "standard"))
    pr = np.linspace(1.2, 12.0, 200)
    ax.axvspan(1.5, 8.0, color=COLORS["band10"], alpha=0.35, lw=0, label="data range, PR 1.5–8")
    ax.plot(
        pr,
        [eta_isen_default(x) for x in pr],
        color=COLORS["accent"],
        lw=dm.lw(1),
        label=f"adopted ({COEFFICIENT_VERSION})",
    )
    ax.plot(
        pr,
        np.maximum(0.25, 0.90 - 0.02 * pr),
        color=COLORS["muted"],
        lw=dm.lw(0),
        ls=(0, (4, 1.6)),
        label=f"{LEGACY_LABEL}: 0.90 − 0.02·PR",
    )
    ax.set_xlim(1.0, 12.0)
    ax.set_xticks(ticks(2.0, 12.0, 2.0))
    ax.set_xlabel("Pressure ratio [-]")
    ax.set_ylim(0.2, 1.0)
    ax.set_yticks(ticks(0.2, 1.0, 0.2))
    ax.set_ylabel("Isentropic efficiency η_isen [-]")
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax.legend(loc="upper right", frameon=False, fontsize=dm.fs(-2.5))
    _save(fig, out, "F4_eta_isen")


def fig_loco(out: Path) -> None:
    pooled = pd.read_csv(DATA_DIR / "loco_pooled.csv")
    sel = json.loads((DATA_DIR / "selected.json").read_text())
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.45))
    for ax, kind, title, letter in zip(
        axes, ("eta_vol", "eta_oi"), ("η_vol", "η_isen · η_em"), ("a", "b"), strict=True
    ):
        d = pooled[pooled.kind == kind].sort_values("loco_wmape_pct", ascending=False)
        colors = [
            COLORS["hot"] if f == "legacy" else (COLORS["accent"] if f == sel[kind]["family"] else COLORS["band20"])
            for f in d.family
        ]
        labels = [LEGACY_LABEL if f == "legacy" else f for f in d.family]
        ax.barh(labels, d.loco_wmape_pct, color=colors, height=0.7)
        xmax = float(np.ceil(d.loco_wmape_pct.max() + 1.0))
        ax.set_xlim(0.0, xmax + 1.0)
        ax.set_xticks(ticks(0.0, xmax, 2.0 if xmax > 8 else 1.0))
        for y, v in enumerate(d.loco_wmape_pct):
            ax.text(v + 0.08, y, f"{v:.2f}", va="center", fontsize=dm.fs(-3), color=COLORS["ink"])
        ax.set_xlabel("Leave-one-compressor-out MAPE [%]")
        ax.set_title(f"{title}: candidate forms (blue = adopted, red = {LEGACY_LABEL})", loc="left", fontsize=dm.fs(-1))
        ax.grid(True, axis="x", alpha=0.25, linewidth=GRIDLINE)
        ax.tick_params(axis="y", labelsize=dm.fs(-2))
        panel_letter(ax, letter, x=-0.22)
    _save(fig, out, "F5_loco_families", mt="6%")


def fig_strata(out: Path) -> None:
    strata = pd.read_csv(DATA_DIR / "loco_strata.csv")
    sel = json.loads((DATA_DIR / "selected.json").read_text())
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.5))
    for ax, kind, title, letter in zip(
        axes, ("eta_vol", "eta_oi"), ("η_vol", "η_isen · η_em"), ("a", "b"), strict=True
    ):
        new = strata[(strata.kind == kind) & (strata.family == sel[kind]["family"])].set_index("stratum")
        old = strata[(strata.kind == kind) & (strata.family == "legacy")].set_index("stratum")
        idx = [s for s in new.index if not s.startswith("source=")] + [s for s in new.index if s.startswith("source=")]
        y = np.arange(len(idx))
        ax.barh(y + 0.2, old.loc[idx].loco_wmape_pct, height=0.4, color=COLORS["hot"], label=LEGACY_LABEL)
        ax.barh(
            y - 0.2,
            new.loc[idx].loco_wmape_pct,
            height=0.4,
            color=COLORS["accent"],
            label=f"adopted {sel[kind]['family']}",
        )
        ax.set_yticks(y)
        ax.set_yticklabels(
            [
                s.replace("source=", "")
                .replace("_", " ")
                .replace("nstar=", "n* ")
                .replace("ref=", "")
                .replace("type=", "")
                .replace("PR=", "")
                for s in idx
            ],
            fontsize=dm.fs(-2.5),
        )
        xmax = float(np.ceil(max(old.loc[idx].loco_wmape_pct.max(), new.loc[idx].loco_wmape_pct.max()) / 5.0) * 5.0)
        ax.set_xlim(0.0, xmax)
        ax.set_xticks(ticks(0.0, xmax, 5.0))
        ax.set_xlabel("LOCO MAPE [%]")
        ax.set_title(title, loc="left", fontsize=dm.fs(-1))
        ax.grid(True, axis="x", alpha=0.25, linewidth=GRIDLINE)
        ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-2.5))
        panel_letter(ax, letter, x=-0.30)
    _save(fig, out, "F6_loco_strata", mt="4%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO_ROOT / "validation" / "coefficients" / COEFFICIENT_VERSION / "figures"))
    a = ap.parse_args()
    apply_style("scientific")
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
