"""How much the evaporating-temperature term removes from the maps' error (#218).

The PR-N map leaves part of each measured point unexplained (the marker-against-background difference of
figures/map_heatmaps.png). te_error_parity puts the measured efficiency against the map, with and without the T_e
term, on the same 134 points and the same 7-term PR-N form (validation/compressor_maps/ossorio_te/fit.py), coloured
by T_e, with RMSE and MAPE in each panel. The script also prints the 10-fold CV RMSE (folds of fit.py) and the F test
of the one added term, for eta_ov, eta_v and f_loss (the table in the archive README).

    uv run python -m validation.compressor_maps.ossorio_te.te_error_figures
"""

from __future__ import annotations

import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402
from scripts.visualization._dmpl_common import HAIRLINE, finalize  # noqa: E402

from validation.compressor_maps.ossorio_te import figures as base  # noqa: E402  (applies the scientific preset)
from validation.compressor_maps.ossorio_te.fit import ARCHIVE, FOLDS, SEED, basis  # noqa: E402

# ╔══════════════════════════════════════════════════════════════════╗
# ║  커스텀 계수 설정                                                 ║
# ╚══════════════════════════════════════════════════════════════════╝
H_PARITY = "15cm"  # 2x2 정사각 패널 그림 높이
WSPACE = 0.6  # 두 맵(열) 사이 간격: 어느 열이 T_e 를 가졌는지 떼어 보이게 (사용자 지시 2026-11-21)
HEADER_FS = 9.5  # 열 제목 글자 [pt]
HEADER_OFFSET = 22  # 열 제목을 위 패널 제목보다 올리는 거리 [pt]
LIMS = {"eta_ov": (0.35, 0.72), "eta_v": (0.76, 1.02)}  # 측정·맵 축 범위
COLUMNS = (("2d", "map in PR, N"), ("te", r"map in PR, N, $T_e$  (adopted)"))
# ═════════════════════════════════════════════════════════════════

FIGURES = base.FIGURES
SYMBOL = base.SYMBOL
QUANTITIES = ("eta_ov", "eta_v", "f_loss")


def _fits(points: pd.DataFrame) -> dict:
    """Residuals, RMSE, MAPE, CV RMSE and the F test of the T_e term, per quantity."""
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
                rss=float(r @ r),
                p=a.shape[1],
                rmse=float(np.sqrt(np.mean(r**2))),
                mape=float(100 * np.mean(np.abs(r / y))),
                cv_rmse=float(np.sqrt(np.mean((y - cv) ** 2))),
                fitted=a @ c,
            )
        n = len(y)
        f = (res["2d"]["rss"] - res["te"]["rss"]) / (res["te"]["rss"] / (n - res["te"]["p"]))
        res["F"], res["p_value"] = float(f), float(stats.f.sf(f, 1, n - res["te"]["p"]))
        out[q] = res
    return out


def parity(points, fits) -> None:
    fig = plt.figure(figsize=dm.figsize(base.WIDTH, H_PARITY))
    gs = fig.add_gridspec(2, 3, width_ratios=[1, 1, 0.05], wspace=WSPACE, hspace=0.35)
    axes = []
    for i, q in enumerate(("eta_ov", "eta_v")):
        lo, hi = LIMS[q]
        for j, (key, header) in enumerate(COLUMNS):
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
            fit = fits[q][key]
            ax.set_title(f"{SYMBOL[q]}:  RMSE {fit['rmse']:.3f},  MAPE {fit['mape']:.1f} %", loc="left")
            ax.set_xlabel(f"map {SYMBOL[q]}")
            ax.set_ylabel(f"measured {SYMBOL[q]}")
            if i == 0:  # column title, anchored to the panel so that it follows the layout
                ax.annotate(
                    header,
                    xy=(0.5, 1.0),
                    xycoords="axes fraction",
                    xytext=(0, HEADER_OFFSET),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=HEADER_FS,
                    fontweight="bold",
                    color=base.C_INK,
                )
    base._te_colorbar(fig, fig.add_subplot(gs[:, 2]))
    base._label(axes, [])
    finalize(fig, FIGURES / "te_error_parity", formats=base.FORMATS, mt="4.5%")
    plt.close(fig)


def main() -> None:
    points = pd.read_csv(ARCHIVE / "points.csv")
    fits = _fits(points)
    for q in QUANTITIES:
        print(
            q,
            {k: (round(fits[q][k]["rmse"], 4), round(fits[q][k]["mape"], 2)) for k in ("2d", "te")},
            "CV",
            {k: round(fits[q][k]["cv_rmse"], 4) for k in ("2d", "te")},
            "F",
            round(fits[q]["F"], 2),
            "p",
            f"{fits[q]['p_value']:.1e}",
        )
    parity(points, fits)


if __name__ == "__main__":
    main()
