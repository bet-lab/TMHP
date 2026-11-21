"""Figures of the default maps, their data and the comparison that chose them (#218).

Reads   the archive (points.csv, coefficients.json, comparison/)
Writes  figures/map_heatmaps.png                    maps at three evaporating temperatures, measured points on them
        figures/map_slices.png                      maps against the measured points at three speeds
        figures/te_residuals.png                    residual against T_e with and without the T_e term
        figures/correlation_points_on_quadratic.png the 2026-11-01 PR-N quadratic against the correlation it came from
        figures/catalogue_fit.png                   Fujitsu catalogue calibration of the four candidate maps

    uv run python -m validation.compressor_maps.ossorio_te.figures
"""

from __future__ import annotations

import json

import CoolProp.CoolProp as CP
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from validation.compressor_maps.ossorio_te.fit import ARCHIVE, FLUID, TE_TERM, basis

FIGURES = ARCHIVE / "figures"
COMPARISON = ARCHIVE / "comparison"
LABELS = {
    "eta_v": "volumetric efficiency $\\eta_v$",
    "eta_ov": "overall efficiency $\\eta_{ov}$",
    "f_loss": "$f_{loss}$",
}
TE_LEVELS = (-30.0, -20.0, -10.0, 0.0, 10.0, 20.0, 25.0)
TE_CMAP = plt.get_cmap("coolwarm")
TE_NORM = matplotlib.colors.Normalize(-30.0, 25.0)
DIRECTIONS = {
    "current_quadratic": ("current PR-N quadratic (#90)", "#8c8c8c"),
    "a_pr_n": ("(a) PR-N, measured", "#2563eb"),
    "b_pr_n_te": ("(b) PR-N + $T_e$ (adopted)", "#d97706"),
    "c_terminal_inverter": ("(c) terminal + inverter Eq. 5", "#7c3aed"),
}


def _maps() -> dict:
    return json.loads((ARCHIVE / "coefficients.json").read_text())["maps"]


def _pr_range(te: float) -> tuple[float, float]:
    """Pressure ratios inside the data's (PR, T_e) hull at one evaporating temperature."""
    hull = json.loads((ARCHIVE / "coefficients.json").read_text())["domain"]["hull_PR_Te"]
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


def _te_colorbar(fig, axes) -> None:
    bar = fig.colorbar(matplotlib.cm.ScalarMappable(norm=TE_NORM, cmap=TE_CMAP), ax=axes, shrink=0.8, pad=0.01)
    bar.set_label("evaporating temperature $T_e$ [°C]")


def map_heatmaps(points: pd.DataFrame, maps: dict) -> None:
    levels = (-20.0, 0.0, 20.0)
    ranges = {"eta_v": (0.80, 1.0), "eta_ov": (0.40, 0.72), "f_loss": (0.06, 0.26)}
    pr, rps = np.meshgrid(np.linspace(1.4, 12.7, 160), np.linspace(25, 115, 120))
    fig, axes = plt.subplots(3, 3, figsize=(13, 11), sharex=True, sharey=True, constrained_layout=True)
    for i, quantity in enumerate(TE_TERM):
        lo, hi = ranges[quantity]
        for j, te in enumerate(levels):
            ax = axes[i, j]
            z = _eval(maps, quantity, pr.ravel(), rps.ravel(), np.full(pr.size, te)).reshape(pr.shape)
            mesh = ax.pcolormesh(pr, rps, z, cmap="viridis", vmin=lo, vmax=hi, shading="auto")
            lo_pr, hi_pr = _pr_range(te)
            for x0, x1 in ((1.4, lo_pr), (hi_pr, 12.7)):
                ax.axvspan(x0, x1, color="white", alpha=0.55, lw=0)
            for y0, y1 in ((25, 30), (110, 115)):
                ax.axhspan(y0, y1, color="white", alpha=0.55, lw=0)
            at = points[np.isclose(points.T_evap_C, te, atol=0.5)]
            ax.scatter(at.PR, at.N_rps, c=at[quantity], cmap="viridis", vmin=lo, vmax=hi, s=70, edgecolors="white")
            ax.set_title(f"{LABELS[quantity]}, $T_e$ = {te:g} °C ({len(at)} points)", fontsize=10)
            if i == 2:
                ax.set_xlabel("pressure ratio PR")
            if j == 0:
                ax.set_ylabel("speed N [rev/s]")
        fig.colorbar(mesh, ax=axes[i, :], shrink=0.9, pad=0.01)
    fig.suptitle(
        "Default maps (background) and the measured points at that $T_e$ (markers, same colour scale)\n"
        "A marker that matches its background is a point the map reproduces; pale: outside the data "
        "((PR, $T_e$) hull, 30-110 rev/s); f_loss has no $T_e$ term"
    )
    fig.savefig(FIGURES / "map_heatmaps.png", dpi=130)
    plt.close(fig)


def map_slices(points: pd.DataFrame, maps: dict) -> None:
    speeds = (30.0, 70.0, 110.0)
    pr = np.linspace(1.4, 12.7, 200)
    fig, axes = plt.subplots(3, 3, figsize=(13, 11), sharex=True, constrained_layout=True)
    for i, quantity in enumerate(TE_TERM):
        for j, n in enumerate(speeds):
            ax = axes[i, j]
            at = points[points.N_rps == n]
            ax.scatter(at.PR, at[quantity], c=TE_CMAP(TE_NORM(at.T_evap_C)), s=36, edgecolors="black", linewidths=0.4)
            for te in (-20.0, 0.0, 20.0):
                y = _eval(maps, quantity, pr, np.full_like(pr, n), np.full_like(pr, te))
                lo_pr, hi_pr = _pr_range(te)
                inside = (pr >= lo_pr) & (pr <= hi_pr)
                ax.plot(pr, np.where(inside, y, np.nan), color=TE_CMAP(TE_NORM(te)), lw=2)
                ax.plot(pr, y, color=TE_CMAP(TE_NORM(te)), lw=0.8, ls=":")
            ax.plot(pr, _quadratic(quantity, pr, np.full_like(pr, n)), color="black", ls="--", lw=1.2)
            ax.set_ylim({"eta_v": (0.75, 1.02), "eta_ov": (0.3, 0.78), "f_loss": (0.04, 0.3)}[quantity])
            ax.set_title(f"{LABELS[quantity]}, N = {n:g} rev/s", fontsize=10)
            ax.grid(alpha=0.3)
            if i == 2:
                ax.set_xlabel("pressure ratio PR")
    _te_colorbar(fig, axes)
    fig.suptitle(
        "Default maps at $T_e$ = -20 / 0 / 20 °C (solid inside the data's (PR, $T_e$) hull, dotted outside) "
        "and measured points coloured by $T_e$;\ndashed: the 2026-11-01 PR-N quadratic it replaces "
        "(f_loss has no $T_e$ term: one line)"
    )
    fig.savefig(FIGURES / "map_slices.png", dpi=130)
    plt.close(fig)


def te_residuals(points: pd.DataFrame, maps: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), constrained_layout=True)
    for ax, quantity in zip(axes, ("eta_ov", "eta_v"), strict=True):
        y = points[quantity].to_numpy()
        a2 = basis(points.PR, points.N_rps)
        c2 = np.linalg.lstsq(a2, y, rcond=None)[0]
        r2 = y - a2 @ c2
        r3 = y - _eval(maps, quantity, points.PR, points.N_rps, points.T_evap_C)
        jitter = np.random.default_rng(0).uniform(-1.2, 1.2, len(points))
        ax.scatter(
            points.T_evap_C + jitter - 0.7,
            r2,
            s=18,
            color="#2563eb",
            alpha=0.7,
            label=f"PR, N only (RMSE {np.sqrt(np.mean(r2**2)):.3f})",
        )
        ax.scatter(
            points.T_evap_C + jitter + 0.7,
            r3,
            s=18,
            color="#d97706",
            alpha=0.8,
            label=f"PR, N, $T_e$ (RMSE {np.sqrt(np.mean(r3**2)):.3f})",
        )
        ax.axhline(0, color="black", lw=0.8)
        ax.set_xlabel("evaporating temperature $T_e$ [°C]")
        ax.set_ylabel(f"measured - map, {LABELS[quantity]}")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=9, loc="lower right")
    fig.suptitle("Residual against $T_e$ with and without the $T_e$ term (same 134 points, same 7-term PR-N form)")
    fig.savefig(FIGURES / "te_residuals.png", dpi=130)
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
    rows = []
    for te in range(-30, 26, 5):
        for tc in range(20, 71, 5):
            if tc - te >= 15:
                rows.append(dict(T_evap_C=te, grid=-10 <= te <= 15, **_correlation(te, tc, rps)))
    corr = pd.DataFrame(rows)
    pr = np.linspace(1.3, 10.0, 200)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), constrained_layout=True)
    for ax, quantity in zip(axes, TE_TERM, strict=True):
        for inside, marker in ((True, "o"), (False, "^")):
            c = corr[corr.grid == inside]
            ax.scatter(
                c.PR,
                c[quantity],
                c=TE_CMAP(TE_NORM(c.T_evap_C)),
                marker=marker,
                s=34,
                edgecolors="black",
                linewidths=0.4,
            )
        meas = points[points.N_rps == rps]
        ax.scatter(meas.PR, meas[quantity], marker="x", color="black", s=22, linewidths=0.8)
        ax.plot(pr, _quadratic(quantity, pr, np.full_like(pr, rps)), color="black", lw=2)
        ax.axvspan(1.475, 7.492, color="0.9", zorder=0)
        ax.set_xlim(1.3, 10.0)
        ax.set_ylim({"eta_v": (0.8, 1.02), "eta_ov": (0.3, 0.78), "f_loss": (0.04, 0.26)}[quantity])
        ax.set_title(f"{LABELS[quantity]}, N = {rps:g} rev/s", fontsize=10)
        ax.set_xlabel("pressure ratio PR")
        ax.grid(alpha=0.3)
    _te_colorbar(fig, axes)
    fig.suptitle(
        "The 2026-11-01 PR-N quadratic (line) against the Ossorio 2024 correlation it was fitted to\n"
        "circles: its 567-point grid ($T_e$ -10...15 °C); triangles: the same correlation outside that grid; "
        "x: measured; shaded: PR range of the grid"
    )
    fig.savefig(FIGURES / "correlation_points_on_quadratic.png", dpi=130)
    plt.close(fig)


def catalogue_fit() -> None:
    scores = json.loads((COMPARISON / "scores.json").read_text())["catalogue_fit"]["results"]
    rows = pd.read_csv(COMPARISON / "catalogue_fit_points.csv")
    stages = (
        ("baseline", "no calibration"),
        ("lambda_only", "$\\lambda_{ov}$"),
        ("shape", "$\\lambda_{ov}$ + peak shift"),
    )
    fig, (bars, resid) = plt.subplots(1, 2, figsize=(14, 5), constrained_layout=True)
    width = 0.2
    for k, (key, (label, color)) in enumerate(DIRECTIONS.items()):
        values = [scores[key][stage]["rmse"] for stage, _ in stages]
        x = np.arange(len(stages)) + (k - 1.5) * width
        bars.bar(x, values, width, color=color, label=label)
        for xi, v in zip(x, values, strict=True):
            bars.text(xi, v + 0.01, f"{v:.2f}", ha="center", fontsize=8)
        at = rows[(rows.direction == key) & (rows.stage == "shape") & rows.COP_model.notna()]
        mean = (at.COP_model - at.COP_catalogue).groupby(at.T_outdoor_C).mean()
        resid.plot(mean.index, mean.to_numpy(), marker="o", color=color, label=label)
    bars.set_xticks(np.arange(len(stages)), [s for _, s in stages])
    bars.set_ylabel("COP RMSE, 50 catalogue rows")
    bars.legend(fontsize=9)
    bars.grid(axis="y", alpha=0.3)
    resid.axhline(0, color="black", lw=0.8)
    resid.set_xlabel("outdoor dry bulb [°C]")
    resid.set_ylabel("model - catalogue COP (mean of 5 indoor temperatures)")
    resid.set_title("after $\\lambda_{ov}$ + peak shift", fontsize=10)
    resid.grid(alpha=0.3)
    fig.suptitle(
        "Fujitsu ASYG30LMTA heating catalogue, #90 calibration protocol, four candidate maps: "
        "they end within 0.04 of each other with the same residual pattern"
    )
    fig.savefig(FIGURES / "catalogue_fit.png", dpi=130)
    plt.close(fig)


def main() -> None:
    FIGURES.mkdir(exist_ok=True)
    points = pd.read_csv(ARCHIVE / "points.csv")
    maps = _maps()
    map_heatmaps(points, maps)
    map_slices(points, maps)
    te_residuals(points, maps)
    correlation_points_on_quadratic(points)
    catalogue_fit()


if __name__ == "__main__":
    main()
