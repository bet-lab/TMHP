"""How much the evaporating-temperature term removes from the maps' error (#218): four candidate figures.

The PR-N map leaves part of each measured point unexplained (the marker-against-background difference of
figures/map_heatmaps.png). These figures compare that residual with and without the T_e term, on the same 134 points
and the same 7-term PR-N form (validation/compressor_maps/ossorio_te/fit.py):

        te_error_residual_map    residual on the axes of map_heatmaps.png (PR, N at T_e = -20 / 0 / 20 degrees C)
        te_error_by_level        mean residual (and spread) at each tested T_e, both maps
        te_error_parity          measured against map, coloured by T_e, both maps
        te_error_summary         RMSE and 10-fold CV RMSE for eta_v, eta_ov and f_loss, with the F test of the T_e term

    uv run python -m validation.compressor_maps.ossorio_te.te_error_figures
"""

from __future__ import annotations

import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import TwoSlopeNorm  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from scipy import stats  # noqa: E402
from scripts.visualization._dmpl_common import HAIRLINE, finalize, ticks  # noqa: E402

from validation.compressor_maps.ossorio_te import figures as base  # noqa: E402  (applies the scientific preset)
from validation.compressor_maps.ossorio_te.fit import ARCHIVE, FOLDS, SEED, basis  # noqa: E402

# ╔══════════════════════════════════════════════════════════════════╗
# ║  커스텀 계수 설정                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝
C_2D = base.C_SECONDARY  # PR, N 맵 (blue-600)
C_TE = base.C_HIGHLIGHT  # PR, N, T_e 맵 (orange-500, 강조색)
RES_CMAP = "RdBu_r"  # 부호 있는 잔차: 대칭 발산 색
RES_LIM = {"eta_ov": 0.08, "eta_v": 0.03}  # 잔차 색 범위 (±)
S_RES = 30  # 잔차 표식 크기 (한 T_e 에서는 점이 겹치지 않는다)
H_MAP = "19cm"  # 4x3 잔차 그림 높이
H_PARITY = "15cm"  # 2x2 정사각 패널 그림 높이
LEVELS = (-30, -20, -10, 0, 10, 20, 25)  # 시험한 증발 온도
# ═════════════════════════════════════════════════════════════════

FIGURES = base.FIGURES
SYMBOL = base.SYMBOL
QUANTITIES = ("eta_ov", "eta_v", "f_loss")


def _fits(points: pd.DataFrame) -> dict:
    """Residuals, RMSE, CV RMSE and the F test of the T_e term, per quantity."""
    folds = np.random.default_rng(SEED).permutation(len(points)) % FOLDS
    out = {}
    for q in QUANTITIES:
        y = points[q].to_numpy()
        res = {}
        for key, a in (("2d", basis(points.PR, points.N_rps)), ("te", basis(points.PR, points.N_rps, points.T_evap_C))):
            c = np.linalg.lstsq(a, y, rcond=None)[0]
            cv = np.empty_like(y)
            for k in range(FOLDS):
                tr = folds != k
                cv[~tr] = a[~tr] @ np.linalg.lstsq(a[tr], y[tr], rcond=None)[0]
            r = y - a @ c
            res[key] = dict(
                resid=r,
                rss=float(r @ r),
                p=a.shape[1],
                rmse=float(np.sqrt(np.mean(r**2))),
                cv_rmse=float(np.sqrt(np.mean((y - cv) ** 2))),
                fitted=a @ c,
            )
        n = len(y)
        f = (res["2d"]["rss"] - res["te"]["rss"]) / (res["te"]["rss"] / (n - res["te"]["p"]))
        res["F"], res["p_value"] = float(f), float(stats.f.sf(f, 1, n - res["te"]["p"]))
        out[q] = res
    return out


def residual_map(points, fits) -> None:
    """The residual on the axes of map_heatmaps.png (PR, N at one T_e), both maps, eta_ov and eta_v."""
    levels = (-20.0, 0.0, 20.0)  # the T_e of map_heatmaps.png
    rows = [(q, key, name) for q in ("eta_ov", "eta_v") for key, name in (("2d", "PR, N"), ("te", r"PR, N, $T_e$"))]
    fig = plt.figure(figsize=dm.figsize(base.WIDTH, H_MAP))
    gs = fig.add_gridspec(4, 4, width_ratios=[1, 1, 1, 0.05], wspace=0.12, hspace=0.42)
    axes = []
    for i, (q, key, name) in enumerate(rows):
        norm = TwoSlopeNorm(0.0, -RES_LIM[q], RES_LIM[q])
        r = fits[q][key]["resid"]
        for j, te in enumerate(levels):
            ax = fig.add_subplot(gs[i, j])
            axes.append(ax)
            at = np.isclose(points.T_evap_C.to_numpy(), te, atol=0.5)
            sc = ax.scatter(
                points.PR.to_numpy()[at],
                points.N_rps.to_numpy()[at],
                c=r[at],
                cmap=RES_CMAP,
                norm=norm,
                s=S_RES,
                edgecolors=base.C_INK,
                linewidths=0.3,
                zorder=3,
            )
            rmse = float(np.sqrt(np.mean(r[at] ** 2)))
            ax.set_title(f"$T_e$ = {base._minus(te)} °C,  RMSE {rmse:.3f}", loc="left")
            ax.set_xlim(*base.PR_LIM)
            ax.set_ylim(*base.N_LIM)
            ax.set_xticks(base.PR_TICKS)
            ax.set_yticks(base.N_TICKS)
            if i == len(rows) - 1:
                ax.set_xlabel("pressure ratio PR")
            else:
                ax.tick_params(labelbottom=False)
            if j == 0:
                ax.set_ylabel(f"{SYMBOL[q]}, map in {name}\nspeed N [rev/s]")
            else:
                ax.tick_params(labelleft=False)
        if i % 2:
            bar = fig.colorbar(sc, cax=fig.add_subplot(gs[i - 1 : i + 1, 3]), extend="both")
            bar.set_label(f"measured − map, {SYMBOL[q]}")
    base._label(axes[0::3], [a for k, a in enumerate(axes) if k % 3])
    finalize(fig, FIGURES / "te_error_residual_map", formats=base.FORMATS)
    plt.close(fig)


def by_level(points, fits) -> None:
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize(base.WIDTH, base.H_ROW))
    for ax, q in zip(axes, ("eta_ov", "eta_v"), strict=True):
        ax.axhline(0.0, color=base.C_INK, lw=HAIRLINE)
        for k, (key, color, marker, name) in enumerate(
            (("2d", C_2D, "o", "PR, N"), ("te", C_TE, "^", r"PR, N, $T_e$"))
        ):
            r = fits[q][key]["resid"]
            mean, sd = [], []
            for lv in LEVELS:
                at = np.isclose(points.T_evap_C, lv, atol=1.0)
                mean.append(r[at].mean())
                sd.append(r[at].std())
            x = np.arange(len(LEVELS)) + (k - 0.5) * 0.28
            ax.errorbar(
                x,
                mean,
                yerr=sd,
                fmt=marker,
                color=color,
                ms=4.5,
                capsize=2,
                lw=0.9,
                label=f"{name}  (RMSE {fits[q][key]['rmse']:.3f})",
            )
        ax.set_xticks(np.arange(len(LEVELS)), [base._minus(v) for v in LEVELS])
        ax.set_xlim(-0.6, len(LEVELS) - 0.4)
        ax.set_xlabel(r"evaporating temperature $T_e$ [°C]")
        ax.set_ylabel(f"mean ± s.d. of measured − map, {SYMBOL[q]}")
        ax.legend(loc="upper left", fontsize=base.LEGEND_FS)
    axes[0].set_ylim(-0.08, 0.08)
    axes[0].set_yticks(ticks(-0.08, 0.08, 0.02))
    axes[1].set_ylim(-0.03, 0.03)
    axes[1].set_yticks(ticks(-0.03, 0.03, 0.01))
    base._label(list(axes), [])
    finalize(fig, FIGURES / "te_error_by_level", formats=base.FORMATS)
    plt.close(fig)


def parity(points, fits) -> None:
    fig = plt.figure(figsize=dm.figsize(base.WIDTH, H_PARITY))
    gs = fig.add_gridspec(2, 3, width_ratios=[1, 1, 0.05], wspace=0.3, hspace=0.35)
    axes = []
    lims = {"eta_ov": (0.35, 0.72), "eta_v": (0.76, 1.02)}
    for i, q in enumerate(("eta_ov", "eta_v")):
        lo, hi = lims[q]
        for j, (key, name) in enumerate((("2d", "PR, N"), ("te", r"PR, N, $T_e$"))):
            ax = fig.add_subplot(gs[i, j])
            axes.append(ax)
            ax.plot([lo, hi], [lo, hi], color=base.C_INK, lw=HAIRLINE)
            ax.scatter(
                fits[q][key]["fitted"],
                points[q],
                c=points.T_evap_C,
                cmap=base.TE_CMAP,
                norm=base.TE_NORM,
                s=base.S_POINT,
                edgecolors="white",
                linewidths=HAIRLINE,
                zorder=3,
            )
            ax.set_xlim(lo, hi)
            ax.set_ylim(lo, hi)
            ax.set_aspect("equal")
            ax.set_title(f"{SYMBOL[q]},  map in {name}:  RMSE {fits[q][key]['rmse']:.3f}", loc="left")
            ax.set_xlabel(f"map {SYMBOL[q]}")
            ax.set_ylabel(f"measured {SYMBOL[q]}")
    base._te_colorbar(fig, fig.add_subplot(gs[:, 2]))
    base._label(axes, [])
    finalize(fig, FIGURES / "te_error_parity", formats=base.FORMATS)
    plt.close(fig)


def summary(fits) -> None:
    fig, ax = plt.subplots(figsize=dm.figsize(base.WIDTH, "7cm"))
    width = 0.18
    for k, q in enumerate(QUANTITIES):
        for m, (metric, hatch) in enumerate((("rmse", None), ("cv_rmse", "////"))):
            for n, (key, color) in enumerate((("2d", C_2D), ("te", C_TE))):
                x = k + (2 * m + n - 1.5) * width
                v = fits[q][key][metric]
                ax.bar(
                    x,
                    v,
                    width,
                    color=color,
                    hatch=hatch,
                    edgecolor="white" if hatch is None else base.C_INK,
                    linewidth=HAIRLINE,
                )
                ax.text(x, v + 0.0006, f"{v:.3f}", ha="center", va="bottom", fontsize=base.LEGEND_FS, rotation=90)
        cut = 1 - fits[q]["te"]["cv_rmse"] / fits[q]["2d"]["cv_rmse"]
        p = fits[q]["p_value"]
        change = f"CV RMSE −{cut * 100:.0f} %" if cut >= 0 else f"CV RMSE +{-cut * 100:.0f} %"
        p_txt = "F test p < 1e-10" if p < 1e-10 else f"F test p = {p:.2f}"
        ax.text(k, 0.049, f"{change}\n{p_txt}", ha="center", va="top", fontsize=base.LEGEND_FS, color=base.C_INK)
    ax.set_xticks(range(len(QUANTITIES)), [SYMBOL[q] for q in QUANTITIES])
    ax.set_xlim(-0.6, len(QUANTITIES) - 0.4)
    ax.set_ylim(0, 0.05)
    ax.set_yticks(ticks(0, 0.05, 0.01))
    ax.set_ylabel("RMSE against the 134 points")
    handles = [
        Patch(facecolor=C_2D, edgecolor="white", label="map in PR, N"),
        Patch(facecolor=C_TE, edgecolor="white", label=r"map in PR, N, $T_e$"),
        Patch(facecolor="white", edgecolor=base.C_INK, label="RMSE of the fit"),
        Patch(facecolor="white", edgecolor=base.C_INK, hatch="////", label="10-fold CV RMSE"),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=4, fontsize=base.LEGEND_FS, frameon=False)
    finalize(fig, FIGURES / "te_error_summary", formats=base.FORMATS, mt="8%")
    plt.close(fig)


def main() -> None:
    points = pd.read_csv(ARCHIVE / "points.csv")
    fits = _fits(points)
    for q in QUANTITIES:
        print(
            q,
            {k: round(fits[q][k]["rmse"], 4) for k in ("2d", "te")},
            {k: round(fits[q][k]["cv_rmse"], 4) for k in ("2d", "te")},
            "F",
            round(fits[q]["F"], 2),
            "p",
            fits[q]["p_value"],
        )
    residual_map(points, fits)
    by_level(points, fits)
    parity(points, fits)
    summary(fits)


if __name__ == "__main__":
    main()
