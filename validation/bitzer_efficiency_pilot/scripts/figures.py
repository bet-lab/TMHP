"""Raw scatter, fitted heatmap (+ raw overlay), parity and residual plots per group.

usage: figures.py EFFICIENCY_POINTS.csv REGRESSION_DIR FIG_DIR [MODEL]
Heatmaps are masked outside the convex hull of the raw (r_p, n*) points.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.path import Path as MPath
from scipy.spatial import ConvexHull

LABEL = {"eta_v": r"$\eta_v$", "eta_is": r"$\eta_{is}$", "eta_em": r"$\eta_{em}$", "eta_oi": r"$\eta_{is}\eta_{em}$"}
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "<", ">", "h"]
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False, "savefig.dpi": 200})


def design(model, rp, x):
    one = np.ones_like(rp)
    if model == "C1":
        return np.column_stack([one, rp, x])
    if model == "C2":
        return np.column_stack([one, rp, x, rp * x])
    return np.column_stack([one, rp, x, rp**2, x**2, rp * x])


def crange(v):
    lo, hi = np.nanpercentile(v, [1, 99])
    pad = 0.02 * (hi - lo + 1e-9)
    return lo - pad, hi + pad


def save(fig, path):
    fig.savefig(path.with_suffix(".png"), bbox_inches="tight")
    fig.savefig(path.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def main(src, regdir, figdir, model="C3"):
    fig_dir = Path(figdir)
    fig_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(src)
    df = df[df.valid_bitzer == "yes"].copy()
    df["grp"] = df.compressor_type + "_" + df.refrigerant
    coef = pd.read_csv(Path(regdir) / "regression_coefficients.csv")
    for grp, g in df.groupby("grp"):
        comps = sorted(g.compressor_id.unique())
        mk = {c: MARKERS[i % len(MARKERS)] for i, c in enumerate(comps)}
        # visual-only vertical offset so machines sharing a grid point stay visible
        off = {c: (i - (len(comps) - 1) / 2) * 0.012 for i, c in enumerate(comps)}
        pts = g[["pressure_ratio", "normalized_speed"]].values
        hull = MPath(pts[ConvexHull(pts).vertices])
        for eff in ["eta_v", "eta_is", "eta_em", "eta_oi"]:
            gg = g.dropna(subset=[eff])
            if eff in ("eta_is", "eta_em"):
                gg = gg[~gg.sanity_flags.fillna("").str.contains("additional_cooling_required")]
            if gg.empty:
                continue
            vmin, vmax = crange(gg[eff].values)
            # raw scatter
            fig, ax = plt.subplots(figsize=(5.6, 4.0))
            for c in comps:
                s = gg[gg.compressor_id == c]
                sc = ax.scatter(
                    s.pressure_ratio,
                    s.normalized_speed + off[c],
                    c=s[eff],
                    cmap="viridis",
                    vmin=vmin,
                    vmax=vmax,
                    marker=mk[c],
                    s=26,
                    edgecolors="none",
                    label=c,
                )
            fig.colorbar(sc, ax=ax, label=LABEL[eff])
            ax.set_xlabel(r"pressure ratio $r_p$ [-]")
            ax.set_ylabel(r"$n^* = f/50$ Hz [-]  (markers offset ±0.012 per machine)")
            ax.set_title(f"{LABEL[eff]} raw, {grp.replace('_', ' / ')} ({len(comps)} compressors)", fontsize=10)
            leg = ax.legend(fontsize=7, frameon=False, bbox_to_anchor=(1.32, 1), loc="upper left")
            for h in leg.legend_handles:
                h.set_color("0.3")
            save(fig, fig_dir / f"{eff}_scatter_{grp}")
            # fitted heatmap
            row = coef[
                (coef.efficiency == eff)
                & (coef.level == "C")
                & (coef.group == grp)
                & (coef.speed_var == "nstar")
                & (coef.model == model)
            ]
            if row.empty:
                continue
            a = row[[f"a{i}" for i in range(6)]].values[0]
            a = a[~np.isnan(a)]
            rp = np.linspace(gg.pressure_ratio.min(), gg.pressure_ratio.max(), 200)
            ns = np.linspace(gg.normalized_speed.min(), gg.normalized_speed.max(), 200)
            R, N = np.meshgrid(rp, ns)
            Z = (design(model, R.ravel(), N.ravel()) @ a).reshape(R.shape)
            inside = hull.contains_points(np.column_stack([R.ravel(), N.ravel()]), radius=1e-9).reshape(R.shape)
            Z = np.ma.masked_where(~inside, Z)
            fig, ax = plt.subplots(figsize=(5.6, 4.0))
            im = ax.pcolormesh(R, N, Z, cmap="viridis", vmin=vmin, vmax=vmax, shading="auto", rasterized=True)
            cs = ax.contour(R, N, Z, levels=8, colors="w", linewidths=0.5, alpha=0.7)
            ax.clabel(cs, fontsize=6, fmt="%.2f")
            for c in comps:
                s = gg[gg.compressor_id == c]
                ax.scatter(
                    s.pressure_ratio,
                    s.normalized_speed,
                    facecolors="none",
                    edgecolors="k",
                    linewidths=0.4,
                    marker=mk[c],
                    s=18,
                )
            fig.colorbar(im, ax=ax, label=LABEL[eff] + " fitted")
            ax.set_xlabel(r"pressure ratio $r_p$ [-]")
            ax.set_ylabel(r"$n^* = f/50$ Hz [-]")
            r = row.iloc[0]
            ax.set_title(f"{LABEL[eff]} fit {model}, {grp.replace('_', ' / ')}  LOCO RMSE {r.CV_RMSE:.3f}", fontsize=10)
            save(fig, fig_dir / f"{eff}_heatmap_{grp}")
            # parity + residuals
            Xg = design(model, gg.pressure_ratio.values, gg.normalized_speed.values)
            fit = Xg @ a
            res = gg[eff].values - fit
            fig, axs = plt.subplots(1, 3, figsize=(12, 3.6))
            for c in comps:
                m = (gg.compressor_id == c).values
                axs[0].scatter(gg[eff].values[m], fit[m], s=10, marker=mk[c], label=c)
                axs[1].scatter(gg.pressure_ratio.values[m], res[m], s=10, marker=mk[c])
                axs[2].scatter(gg.normalized_speed.values[m], res[m], s=10, marker=mk[c])
            lo, hi = vmin, vmax
            axs[0].plot([lo, hi], [lo, hi], "k-", lw=0.8)
            axs[0].set_xlabel(f"observed {LABEL[eff]}")
            axs[0].set_ylabel(f"fitted {LABEL[eff]}")
            for ax_, xl in ((axs[1], r"$r_p$ [-]"), (axs[2], r"$n^*$ [-]")):
                ax_.axhline(0, color="k", lw=0.8)
                ax_.set_xlabel(xl)
                ax_.set_ylabel("residual (obs - fit)")
            axs[0].legend(fontsize=6, frameon=False)
            fig.suptitle(f"{LABEL[eff]} {model}, {grp.replace('_', ' / ')}", fontsize=10)
            fig.tight_layout()
            save(fig, fig_dir / f"diag_{eff}_{grp}")
            # the combined panel above holds all three; single files below keep the plan's names
            for kind, xv, yv, xl, yl in (
                ("parity", gg[eff].values, fit, f"observed {LABEL[eff]}", f"fitted {LABEL[eff]}"),
                ("residual_rp", gg.pressure_ratio.values, res, r"$r_p$ [-]", "residual"),
                ("residual_speed", gg.normalized_speed.values, res, r"$n^*$ [-]", "residual"),
            ):
                f2, a2 = plt.subplots(figsize=(4, 3.4))
                for c in comps:
                    m = (gg.compressor_id == c).values
                    a2.scatter(xv[m], yv[m], s=9, marker=mk[c])
                if kind == "parity":
                    a2.plot([lo, hi], [lo, hi], "k-", lw=0.8)
                else:
                    a2.axhline(0, color="k", lw=0.8)
                a2.set_xlabel(xl)
                a2.set_ylabel(yl)
                a2.set_title(f"{LABEL[eff]} {grp.replace('_', ' / ')}", fontsize=9)
                save(f2, fig_dir / f"{kind}_{eff}_{grp}")


if __name__ == "__main__":
    main(*sys.argv[1:])
