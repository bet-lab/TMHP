"""Figures of the default maps, their data and the comparison that chose them (#218).

Reads   the archive (points.csv, coefficients.json, comparison/)
Writes  figures/<name>.png and .pdf (dartwork-mpl ``scientific`` preset, 17 cm wide):

        map_heatmaps                     the maps at T_e = -20 / 0 / 20 degC, the measured points at that T_e on them
        map_slices                       the maps at 30 / 70 / 110 rev/s against the measured points
        te_residuals                     residual against T_e with and without the T_e term
        correlation_points_on_quadratic  the 2026-11-01 PR-N quadratic against the correlation it came from
        catalogue_fit                    Fujitsu catalogue calibration of the four candidate maps

The captions are in the archive README; the figures carry no titles of their own.

    uv run python -m validation.compressor_maps.ossorio_te.figures
"""

from __future__ import annotations

import json

import CoolProp.CoolProp as CP
import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from scripts.visualization._dmpl_common import HAIRLINE, apply_style, finalize, ticks  # noqa: E402

from validation.compressor_maps.ossorio_te.fit import ARCHIVE, FLUID, TE_TERM, basis  # noqa: E402

apply_style("scientific", svg_fonttype=None)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  커스텀 계수 설정 (이곳만 수정하면 그림 전체에 반영됨)           ║
# ╚══════════════════════════════════════════════════════════════════╝

# ── 크기 ──────────────────────────────────────────────────────────
WIDTH = "17cm"  # 2단 폭
H_GRID = "15cm"  # 3x3 패널 그림 높이
H_ROW = "6.2cm"  # 1x2·1x3 패널 그림 높이
FORMATS = ("png", "pdf")  # PNG(확인·이슈용) + PDF(벡터)

# ── 색: figure-design-language Semantic Palette ───────────────────
C_BASELINE = "#475569"  # slate-600: 교체되는 2차식, 기준
C_SECONDARY = "#2563EB"  # blue-600: (a) PR-N 맵
C_HIGHLIGHT = "#F97316"  # orange-500: 채택한 (b) PR-N + T_e 맵 (그림당 강조색 하나)
C_BACKGROUND = "#CBD5E1"  # slate-300: 제외한 (c), 자료 범위 음영
C_INK = "#1F2937"  # 실측점·0선
MAP_CMAP = "viridis"  # 효율 값(연속, 명도 단조)
TE_CMAP = LinearSegmentedColormap.from_list(
    "te", plt.get_cmap("cividis")(np.linspace(0.0, 0.88, 256))
)  # 증발 온도(연속, 명도 단조; 가장 밝은 끝은 흰 배경에서 안 보여 잘라 냄)
TE_NORM = Normalize(-30.0, 25.0)
OUTSIDE_ALPHA = 0.55  # 자료 범위 밖 흰 덮개의 불투명도

# ── 표식 ──────────────────────────────────────────────────────────
S_POINT = 14  # 실측점 크기 (points^2)
S_POINT_HEAT = 18  # 히트맵 위 실측점 크기
LW_MAP = 1.4  # 맵 선 두께 (자료 안)
LW_OUTSIDE = 0.8  # 맵 선 두께 (자료 밖, 점선)
LW_OLD = 1.0  # 교체되는 2차식 선 두께

# ── 축 범위·눈금 ──────────────────────────────────────────────────
PR_LIM, PR_TICKS = (1.4, 12.7), ticks(2, 12, 2)
PR_LIM_PTS = (1.2, 13.2)  # slices: room for the markers at PR 1.44 and 12.67
LEGEND_FS = 7.0  # 범례·값 글자 (Elsevier 7 pt 바닥; preset 기본 6 pt 대신)
N_LIM, N_TICKS = (25.0, 115.0), ticks(30, 110, 20)
VALUE_RANGE = {"eta_v": (0.80, 1.00), "eta_ov": (0.40, 0.72), "f_loss": (0.06, 0.26)}  # 히트맵 색 범위
CBAR_TICKS = {"eta_v": ticks(0.80, 1.00, 0.05), "eta_ov": ticks(0.40, 0.70, 0.10), "f_loss": ticks(0.10, 0.25, 0.05)}
Y_LIM = {"eta_v": (0.76, 1.02), "eta_ov": (0.30, 0.78), "f_loss": (0.04, 0.30)}
Y_STEP = {"eta_v": 0.05, "eta_ov": 0.10, "f_loss": 0.05}

# ═════════════════════════════════════════════════════════════════

FIGURES = ARCHIVE / "figures"
COMPARISON = ARCHIVE / "comparison"
SYMBOL = {"eta_v": r"$\eta_v$", "eta_ov": r"$\eta_{ov}$", "f_loss": r"$f_{loss}$"}
DIRECTIONS = {
    "current_quadratic": ("PR-N quadratic of 2026-11-01", C_BASELINE, None),
    "a_pr_n": ("(a) PR, N", C_SECONDARY, None),
    "b_pr_n_te": (r"(b) PR, N, $T_e$ (adopted)", C_HIGHLIGHT, None),
    "c_terminal_inverter": ("(c) terminal power + inverter Eq. 5", C_BACKGROUND, "////"),
}


def _archive() -> dict:
    return json.loads((ARCHIVE / "coefficients.json").read_text())


def _pr_range(te: float) -> tuple[float, float]:
    """Pressure ratios inside the data's (PR, T_e) hull at one evaporating temperature."""
    hull = _archive()["domain"]["hull_PR_Te"]
    xs = []
    for (x1, y1), (x2, y2) in zip(hull, hull[1:] + hull[:1], strict=True):
        if (y1 - te) * (y2 - te) <= 0 and y1 != y2:
            xs.append(x1 + (te - y1) * (x2 - x1) / (y2 - y1))
    return (min(xs), max(xs)) if xs else (np.nan, np.nan)


def _eval(maps: dict, quantity: str, pr, rps, te) -> np.ndarray:
    a = basis(pr, rps, te if TE_TERM[quantity] else None)
    return a @ np.asarray(maps[quantity]["coefficients"])


def _quadratic(quantity: str, pr, rps) -> np.ndarray:
    fits = json.loads((COMPARISON / "pr_n_quadratic_2026_11_01.json").read_text())["fits"]
    c = fits[quantity]["coefficients"]
    pr, rps = np.asarray(pr, dtype=float), np.asarray(rps, dtype=float)
    return c[0] + c[1] * pr + c[2] * rps + c[3] * pr**2 + c[4] * rps**2 + c[5] * pr * rps


def _label(axes_first, axes_rest) -> None:
    """Panel letters: bold 10 pt, in reading order; panels without a y-axis label get theirs at the axes' edge."""
    order = sorted(
        list(axes_first) + list(axes_rest), key=lambda a: (-round(a.get_position().y1, 3), a.get_position().x0)
    )
    letters = {a: chr(ord("a") + k) for k, a in enumerate(order)}
    dm.label_axes(list(axes_first), labels=[letters[a] for a in axes_first], fontsize=10, fontweight="bold")
    if axes_rest:
        dm.label_axes(list(axes_rest), labels=[letters[a] for a in axes_rest], fontsize=10, fontweight="bold", x=-0.09)


def _minus(v: float) -> str:
    return f"{v:g}".replace("-", "−")


def _te_colorbar(fig, cax) -> None:
    bar = fig.colorbar(matplotlib.cm.ScalarMappable(norm=TE_NORM, cmap=TE_CMAP), cax=cax)
    bar.set_ticks(ticks(-30, 20, 10))
    bar.set_label(r"evaporating temperature $T_e$ [°C]")


def _value_axes(ax, quantity: str) -> None:
    lo, hi = Y_LIM[quantity]
    ax.set_ylim(lo, hi)
    step = Y_STEP[quantity]
    ax.set_yticks(ticks(np.ceil(lo / step) * step, np.floor(hi / step) * step, step))


def map_heatmaps(points: pd.DataFrame, maps: dict) -> None:
    levels = (-20.0, 0.0, 20.0)
    pr, rps = np.meshgrid(np.linspace(*PR_LIM, 180), np.linspace(*N_LIM, 120))
    fig = plt.figure(figsize=dm.figsize(WIDTH, H_GRID))
    gs = fig.add_gridspec(3, 4, width_ratios=[1, 1, 1, 0.05], wspace=0.12, hspace=0.32)
    axes = []
    for i, quantity in enumerate(TE_TERM):
        lo, hi = VALUE_RANGE[quantity]
        for j, te in enumerate(levels):
            ax = fig.add_subplot(gs[i, j])
            axes.append(ax)
            z = _eval(maps, quantity, pr.ravel(), rps.ravel(), np.full(pr.size, te)).reshape(pr.shape)
            mesh = ax.pcolormesh(pr, rps, z, cmap=MAP_CMAP, vmin=lo, vmax=hi, shading="auto", rasterized=True)
            lo_pr, hi_pr = _pr_range(te)
            for x0, x1 in ((PR_LIM[0], lo_pr), (hi_pr, PR_LIM[1])):
                ax.axvspan(x0, x1, color="white", alpha=OUTSIDE_ALPHA, lw=0)
            for y0, y1 in ((N_LIM[0], 30.0), (110.0, N_LIM[1])):
                ax.axhspan(y0, y1, color="white", alpha=OUTSIDE_ALPHA, lw=0)
            at = points[np.isclose(points.T_evap_C, te, atol=0.5)]
            ax.scatter(
                at.PR,
                at.N_rps,
                c=at[quantity],
                cmap=MAP_CMAP,
                vmin=lo,
                vmax=hi,
                s=S_POINT_HEAT,
                edgecolors="white",
                linewidths=HAIRLINE,
                zorder=3,
            )
            ax.set_xlim(*PR_LIM)
            ax.set_ylim(*N_LIM)
            ax.set_xticks(PR_TICKS)
            ax.set_yticks(N_TICKS)
            ax.set_title(f"{SYMBOL[quantity]},  $T_e$ = {_minus(te)} °C", loc="left")
            if i == 2:
                ax.set_xlabel("pressure ratio PR")
            else:
                ax.tick_params(labelbottom=False)
            if j == 0:
                ax.set_ylabel("speed N [rev/s]")
            else:
                ax.tick_params(labelleft=False)
        cax = fig.add_subplot(gs[i, 3])
        bar = fig.colorbar(mesh, cax=cax, extend="both")  # the maps and points run past the colour range
        bar.set_ticks(CBAR_TICKS[quantity])
        bar.set_label(SYMBOL[quantity])
    _label(axes[0::3], [a for k, a in enumerate(axes) if k % 3])
    finalize(fig, FIGURES / "map_heatmaps", formats=FORMATS)
    plt.close(fig)


def map_slices(points: pd.DataFrame, maps: dict) -> None:
    speeds = (30.0, 70.0, 110.0)
    pr = np.linspace(*PR_LIM_PTS, 240)
    fig = plt.figure(figsize=dm.figsize(WIDTH, H_GRID))
    gs = fig.add_gridspec(3, 4, width_ratios=[1, 1, 1, 0.05], wspace=0.10, hspace=0.32)
    axes = []
    for i, quantity in enumerate(TE_TERM):
        for j, n in enumerate(speeds):
            ax = fig.add_subplot(gs[i, j])
            axes.append(ax)
            at = points[points.N_rps == n]
            ax.scatter(
                at.PR,
                at[quantity],
                c=at.T_evap_C,
                cmap=TE_CMAP,
                norm=TE_NORM,
                s=S_POINT,
                edgecolors="white",
                linewidths=HAIRLINE,
                zorder=3,
            )
            if TE_TERM[quantity]:
                for te in (-20.0, 0.0, 20.0):
                    y = _eval(maps, quantity, pr, np.full_like(pr, n), np.full_like(pr, te))
                    lo_pr, hi_pr = _pr_range(te)
                    inside = (pr >= lo_pr) & (pr <= hi_pr)
                    color = TE_CMAP(TE_NORM(te))
                    ax.plot(pr, np.where(inside, y, np.nan), color=color, lw=LW_MAP)
                    ax.plot(pr, np.where(inside, np.nan, y), color=color, lw=LW_OUTSIDE, ls=":")
            else:
                ax.plot(pr, _eval(maps, quantity, pr, np.full_like(pr, n), None), color=C_INK, lw=LW_MAP)
            ax.plot(pr, _quadratic(quantity, pr, np.full_like(pr, n)), color=C_INK, lw=LW_OLD, ls="--")
            ax.set_xlim(*PR_LIM_PTS)
            ax.set_xticks(PR_TICKS)
            _value_axes(ax, quantity)
            if j:
                ax.tick_params(labelleft=False)
            ax.set_title(f"{SYMBOL[quantity]},  N = {n:g} rev/s", loc="left")
            if i == 2:
                ax.set_xlabel("pressure ratio PR")
            else:
                ax.tick_params(labelbottom=False)
            if j == 0:
                ax.set_ylabel(SYMBOL[quantity])
    handles = [
        Line2D([], [], color=TE_CMAP(TE_NORM(0.0)), lw=LW_MAP, label=r"map at $T_e$ = −20, 0, 20 °C"),
        Line2D([], [], color=TE_CMAP(TE_NORM(0.0)), lw=LW_OUTSIDE, ls=":", label="map outside the data"),
        Line2D([], [], color=C_INK, lw=LW_OLD, ls="--", label="PR-N quadratic of 2026-11-01"),
        Line2D(
            [],
            [],
            color="none",
            marker="o",
            markerfacecolor=TE_CMAP(TE_NORM(0.0)),
            markeredgecolor="white",
            markersize=6,
            label="measured",
        ),
    ]
    axes[7].legend(handles=handles, loc="upper left", handlelength=1.8, fontsize=LEGEND_FS)
    _te_colorbar(fig, fig.add_subplot(gs[:, 3]))
    _label(axes[0::3], [a for k, a in enumerate(axes) if k % 3])
    finalize(fig, FIGURES / "map_slices", formats=FORMATS)
    plt.close(fig)


def te_residuals(points: pd.DataFrame, maps: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize(WIDTH, H_ROW))
    jitter = np.random.default_rng(0).uniform(-1.2, 1.2, len(points))
    for ax, quantity in zip(axes, ("eta_ov", "eta_v"), strict=True):
        y = points[quantity].to_numpy()
        a2 = basis(points.PR, points.N_rps)
        r2 = y - a2 @ np.linalg.lstsq(a2, y, rcond=None)[0]
        r3 = y - _eval(maps, quantity, points.PR, points.N_rps, points.T_evap_C)
        ax.axhline(0.0, color=C_INK, lw=HAIRLINE)
        ax.scatter(
            points.T_evap_C + jitter - 0.8,
            r2,
            s=S_POINT,
            marker="o",
            color=C_SECONDARY,
            edgecolors="white",
            linewidths=HAIRLINE,
            label=f"PR, N  (RMSE {np.sqrt(np.mean(r2**2)):.3f})",
        )
        ax.scatter(
            points.T_evap_C + jitter + 0.8,
            r3,
            s=S_POINT,
            marker="^",
            color=C_HIGHLIGHT,
            edgecolors="white",
            linewidths=HAIRLINE,
            label=rf"PR, N, $T_e$  (RMSE {np.sqrt(np.mean(r3**2)):.3f})",
        )
        ax.set_xlim(-35, 30)
        ax.set_xticks(ticks(-30, 20, 10))
        ax.set_xlabel(r"evaporating temperature $T_e$ [°C]")
        ax.set_ylabel(f"measured − map, {SYMBOL[quantity]}")
        ax.legend(loc="lower right", markerscale=1.6, fontsize=LEGEND_FS)
    axes[0].set_ylim(-0.11, 0.09)
    axes[0].set_yticks(ticks(-0.10, 0.08, 0.02))
    axes[1].set_ylim(-0.04, 0.03)
    axes[1].set_yticks(ticks(-0.04, 0.03, 0.01))
    _label(list(axes), [])
    finalize(fig, FIGURES / "te_residuals", formats=FORMATS)
    plt.close(fig)


def _correlation(te_c: float, tc_c: float, rps: float) -> dict:
    """Ossorio et al. (2024) Eqs. 10/17 and thesis Eq. 3.22, R290 scroll, superheat 10 K."""
    pub = json.loads((COMPARISON / "ossorio2024_published_coefficients.json").read_text())
    m = pub["eq10_mass_flow"]["Table3"]["scroll-R290"]
    w = pub["eq17_specific_consumption"]["Table4"]["scroll-R290"]
    loss = pub["eq22_electromechanical_loss"]["Table5"]["scroll-R290"]

    def factor(k: dict) -> float:
        return k["k4"] * ((rps - k["k5"]) / 70) ** 2 + 1 - k["k4"] * (1 - k["k5"] / 70) ** 2

    pe = CP.PropsSI("P", "T", te_c + 273.15, "Q", 1, FLUID)
    pc = CP.PropsSI("P", "T", tc_c + 273.15, "Q", 1, FLUID)
    h1, s1, rho = (CP.PropsSI(k, "P", pe, "T", te_c + 283.15, FLUID) for k in ("H", "S", "D"))
    h2s = CP.PropsSI("H", "P", pc, "S", s1, FLUID)
    p, q = pe / 1e5, pc / 1e5
    mdot = (m["k0"] + m["k1"] * p + m["k2"] * q + m["k3"] * p * q) * (rps / 70) * factor(m) / 1000
    spec = (w["k0"] + w["k1"] * (q - w["k2"]) / (p - w["k3"])) * factor(w) * 1000
    pr = pc / pe
    return dict(
        PR=pr,
        eta_v=mdot / (rho * 46e-6 * rps),
        eta_ov=(h2s - h1) / spec,
        f_loss=loss["k0"] + loss["k1"] * pr + loss["k2"] * pr / (rps / 70) + loss["k3"] / (mdot * spec),
    )


def correlation_points_on_quadratic(points: pd.DataFrame) -> None:
    rps = 70.0
    rows = [
        dict(T_evap_C=te, grid=-10 <= te <= 15, **_correlation(te, tc, rps))
        for te in range(-30, 26, 5)
        for tc in range(20, 71, 5)
        if tc - te >= 15
    ]
    corr = pd.DataFrame(rows)
    grid_pr = json.loads((COMPARISON / "pr_n_quadratic_2026_11_01.json").read_text())["domain"]["PR"]
    pr = np.linspace(1.3, 10.0, 240)
    fig = plt.figure(figsize=dm.figsize(WIDTH, H_ROW))
    gs = fig.add_gridspec(1, 4, width_ratios=[1, 1, 1, 0.05], wspace=0.32)
    axes = [fig.add_subplot(gs[0, k]) for k in range(3)]
    for ax, quantity in zip(axes, TE_TERM, strict=True):
        ax.axvspan(*grid_pr, color=C_BACKGROUND, alpha=0.35, lw=0, zorder=0)
        for inside, marker in ((True, "o"), (False, "^")):
            c = corr[corr.grid == inside]
            ax.scatter(
                c.PR,
                c[quantity],
                c=c.T_evap_C,
                cmap=TE_CMAP,
                norm=TE_NORM,
                marker=marker,
                s=S_POINT,
                edgecolors="white",
                linewidths=HAIRLINE,
                zorder=3,
            )
        meas = points[points.N_rps == rps]
        ax.scatter(meas.PR, meas[quantity], marker="x", color=C_INK, s=S_POINT, linewidths=0.8, zorder=4)
        ax.plot(pr, _quadratic(quantity, pr, np.full_like(pr, rps)), color=C_INK, lw=LW_OLD + 0.4)
        ax.set_xlim(1.3, 10.0)
        ax.set_xticks(ticks(2, 10, 2))
        _value_axes(ax, quantity)
        ax.set_title(f"{SYMBOL[quantity]},  N = {rps:g} rev/s", loc="left")
        ax.set_xlabel("pressure ratio PR")
        ax.set_ylabel(SYMBOL[quantity])
    handles = [
        Line2D([], [], color=C_INK, lw=LW_OLD + 0.4, label="PR-N quadratic of 2026-11-01"),
        Line2D(
            [],
            [],
            color="none",
            marker="o",
            markerfacecolor=TE_CMAP(TE_NORM(0.0)),
            markeredgecolor="white",
            markersize=6,
            label="2024 correlation on its grid (N = 70 slice)",
        ),
        Line2D(
            [],
            [],
            color="none",
            marker="^",
            markerfacecolor=TE_CMAP(TE_NORM(-25.0)),
            markeredgecolor="white",
            markersize=6,
            label="same correlation outside the grid",
        ),
        Line2D([], [], color=C_INK, marker="x", ls="none", markersize=5, label="measured"),
        Patch(facecolor=C_BACKGROUND, alpha=0.35, label="PR range of the grid"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=3, handlelength=1.6, fontsize=LEGEND_FS, frameon=False)
    _te_colorbar(fig, fig.add_subplot(gs[0, 3]))
    _label(axes, [])
    finalize(fig, FIGURES / "correlation_points_on_quadratic", formats=FORMATS, mb="19%")
    plt.close(fig)


def catalogue_fit() -> None:
    scores = json.loads((COMPARISON / "scores.json").read_text())["catalogue_fit"]["results"]
    rows = pd.read_csv(COMPARISON / "catalogue_fit_points.csv")
    stages = (
        ("baseline", "no calibration"),
        ("lambda_only", r"$\lambda_{ov}$"),
        ("shape", r"$\lambda_{ov}$ + peak shift"),
    )
    fig, (bars, resid) = plt.subplots(1, 2, figsize=dm.figsize(WIDTH, H_ROW), gridspec_kw=dict(width_ratios=[1.1, 1]))
    width = 0.2
    n = len(DIRECTIONS)
    markers = ("s", "o", "^", "D")
    for k, (key, (label, color, hatch)) in enumerate(DIRECTIONS.items()):
        values = [scores[key][stage]["rmse"] for stage, _ in stages]
        x = np.arange(len(stages)) + (k - (n - 1) / 2) * width
        bars.bar(
            x,
            values,
            width,
            color=color,
            hatch=hatch,
            edgecolor="white" if hatch is None else C_BASELINE,
            linewidth=HAIRLINE,
            label=label,
        )
        for xi, v in zip(x, values, strict=True):
            bars.text(xi, v + 0.015, f"{v:.2f}", ha="center", va="bottom", fontsize=LEGEND_FS)
        at = rows[(rows.direction == key) & (rows.stage == "shape") & rows.COP_model.notna()]
        mean = (at.COP_model - at.COP_catalogue).groupby(at.T_outdoor_C).mean()
        resid.plot(
            mean.index,
            mean.to_numpy(),
            marker=markers[k],
            markersize=4,
            color=color,
            markeredgecolor=C_BASELINE if hatch else color,
            ls=":" if hatch else "-",
            label=label,
        )
    gap = 1.0 - n * width
    bars.set_xlim(-n * width / 2 - gap / 2, (len(stages) - 1) + n * width / 2 + gap / 2)
    bars.set_xticks(np.arange(len(stages)), [s for _, s in stages])
    bars.set_ylim(0, 1.4)
    bars.set_yticks(ticks(0, 1.4, 0.2))
    bars.set_ylabel("COP RMSE, 50 catalogue rows")
    handles, labels = bars.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=4, fontsize=LEGEND_FS, frameon=False)
    resid.axhline(0.0, color=C_INK, lw=HAIRLINE)
    resid.set_xlim(-17, 26)
    resid.set_xticks(ticks(-15, 25, 5))
    resid.set_ylim(-2.0, 1.2)
    resid.set_yticks(ticks(-2.0, 1.0, 0.5))
    resid.set_xlabel("outdoor dry bulb [°C]")
    resid.set_ylabel("model − catalogue COP")
    resid.set_title(r"after $\lambda_{ov}$ + peak shift", loc="left")
    resid.legend(loc="lower left", fontsize=LEGEND_FS)
    _label([bars, resid], [])
    finalize(fig, FIGURES / "catalogue_fit", formats=FORMATS, mt="12%")
    plt.close(fig)


def main() -> None:
    FIGURES.mkdir(exist_ok=True)
    points = pd.read_csv(ARCHIVE / "points.csv")
    maps = _archive()["maps"]
    map_heatmaps(points, maps)
    map_slices(points, maps)
    te_residuals(points, maps)
    correlation_points_on_quadratic(points)
    catalogue_fit()


if __name__ == "__main__":
    main()
