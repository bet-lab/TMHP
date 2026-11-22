"""Which variables the default maps need: ln PR or PR, and whether N and T_e need a scale.

Reads   validation/coefficients/ossorio-r290-te-2026-11-21/points.csv (the 134 measured points of fit.py)
Writes  validation/compressor_maps/ossorio_te/form_compare/scores.json
        validation/compressor_maps/ossorio_te/form_compare/form_compare.png and .pdf

Each candidate is fitted to the 134 points by least squares and scored with the 10-fold CV folds of fit.py.
N is in rev/s and T_e (evaporating saturation temperature) in degC, both unscaled, except in ``current``.

    current        [1, l, l^2, l^3, n, n^2, l n, t], l = ln PR, n = N/70, t = T_e/10 (the library maps)
    current_raw    the same terms with N and T_e unscaled: the same function, other coefficients
    ln_quadratic   [1, l, l^2, N, N^2, l N, T_e]
    pr_quadratic   [1, PR, PR^2, N, N^2, PR N, T_e]
    pr_full_2nd    pr_quadratic + [PR T_e, T_e^2, N T_e]

``f_loss`` never has a T_e term (fit.py). The figure compares ``current`` with ``pr_quadratic``.

    uv run python -m validation.compressor_maps.ossorio_te.form_compare
"""

from __future__ import annotations

import json
from pathlib import Path

import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from scripts.visualization._dmpl_common import HAIRLINE, apply_style, finalize  # noqa: E402

from validation.compressor_maps.ossorio_te.fit import ARCHIVE, FOLDS, SEED, TE_TERM  # noqa: E402

apply_style("scientific", svg_fonttype=None)

# ── config ─────────────────────────────────────────────────────────────
SIZE = ("17cm", "11.5cm")
FORMATS = ("png", "pdf")
OUT = Path(__file__).resolve().parent / "form_compare"
N_SLICE = 70.0  # [rev/s] speed of the drawn slices
TE_SLICES = ((0.0, "o", "-"), (-20.0, "^", "--"))  # T_e [degC], marker, line style
TE_BAND = 3.0  # [K] measured points within this of a slice T_e are drawn on it
C_CURRENT = "#2563EB"  # blue-600: the library maps
C_PR = "#F97316"  # orange-500: plain PR quadratic
C_INK = "#1F2937"
FS = 7.0
# ═════════════════════════════════════════════════════════════════════


def _columns(form: str, pr, rps, te):
    l = np.log(pr)  # noqa: E741
    one = np.ones_like(pr)
    if form == "current":
        n, t = rps / 70.0, None if te is None else te / 10.0
        cols = [one, l, l**2, l**3, n, n**2, l * n]
    elif form == "current_raw":
        t = te
        cols = [one, l, l**2, l**3, rps, rps**2, l * rps]
    elif form == "ln_quadratic":
        t = te
        cols = [one, l, l**2, rps, rps**2, l * rps]
    elif form in ("pr_quadratic", "pr_full_2nd"):
        t = te
        cols = [one, pr, pr**2, rps, rps**2, pr * rps]
        if form == "pr_full_2nd" and te is not None:
            cols += [pr * te, te**2, rps * te]
    else:
        raise ValueError(form)
    return np.column_stack(cols + ([t] if te is not None else []))


FORMS = ("current", "current_raw", "ln_quadratic", "pr_quadratic", "pr_full_2nd")


def design(form: str, quantity: str, pr, rps, te) -> np.ndarray:
    pr, rps = np.asarray(pr, dtype=float), np.asarray(rps, dtype=float)
    te = np.asarray(te, dtype=float) if TE_TERM[quantity] else None
    return _columns(form, pr, rps, te)


def score(points: pd.DataFrame) -> tuple[dict, dict]:
    folds = np.random.default_rng(SEED).permutation(len(points)) % FOLDS
    scores, coefficients = {}, {}
    for form in FORMS:
        scores[form], coefficients[form] = {}, {}
        for quantity in TE_TERM:
            a = design(form, quantity, points.PR, points.N_rps, points.T_evap_C)
            y = points[quantity].to_numpy()
            c = np.linalg.lstsq(a, y, rcond=None)[0]
            cv = np.empty_like(y)
            for k in range(FOLDS):
                train = folds != k
                cv[~train] = a[~train] @ np.linalg.lstsq(a[train], y[train], rcond=None)[0]
            residual = a @ c - y
            scores[form][quantity] = dict(
                terms=int(a.shape[1]),
                rmse=round(float(np.sqrt(np.mean(residual**2))), 4),
                cv_rmse=round(float(np.sqrt(np.mean((cv - y) ** 2))), 4),
                max_abs_error=round(float(np.max(np.abs(residual))), 3),
                condition_number=float(f"{np.linalg.cond(a):.3g}"),
            )
            coefficients[form][quantity] = c
    return scores, coefficients


def eta_ov_peak(coefficients: dict, form: str) -> float:
    """Pressure ratio of the eta_ov maximum at N_SLICE and T_e = 0 degC."""
    pr = np.linspace(1.5, 10.0, 3000)
    v = design(form, "eta_ov", pr, np.full_like(pr, N_SLICE), np.zeros_like(pr)) @ coefficients[form]["eta_ov"]
    return round(float(pr[np.argmax(v)]), 2)


def figure(points: pd.DataFrame, scores: dict, coefficients: dict) -> None:
    fig, axs = plt.subplots(2, 2, figsize=dm.figsize(*SIZE), gridspec_kw=dict(wspace=0.28, hspace=0.42))
    at_slice = points[points.N_rps == N_SLICE]

    def slice_panel(ax, quantity: str, ylabel: str, ylim: tuple[float, float]) -> None:
        for te, marker, ls in TE_SLICES:
            sel = at_slice[(at_slice.T_evap_C - te).abs() < TE_BAND]
            pr = np.geomspace(sel.PR.min() / 1.08, sel.PR.max() * 1.08, 200)
            for form, color in (("current", C_CURRENT), ("pr_quadratic", C_PR)):
                a = design(form, quantity, pr, np.full_like(pr, N_SLICE), np.full_like(pr, te))
                ax.plot(pr, a @ coefficients[form][quantity], color=color, ls=ls, lw=1.0)
            ax.scatter(
                sel.PR, sel[quantity], marker=marker, s=14, facecolor="white", edgecolor=C_INK, lw=HAIRLINE, zorder=3
            )
        ax.set_xlabel("pressure ratio PR")
        ax.set_ylabel(ylabel)
        ax.set_ylim(*ylim)
        ax.grid(axis="y", lw=HAIRLINE, alpha=0.4)
        cur, prq = scores["current"][quantity]["rmse"], scores["pr_quadratic"][quantity]["rmse"]
        ax.text(
            0.98,
            0.04,
            f"RMSE, 134 points: ln PR cubic {cur:.4f}, PR quadratic {prq:.4f}",
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            fontsize=FS,
        )

    slice_panel(axs[0, 0], "eta_ov", r"$\eta_{ov}$", (0.30, 0.74))
    ax = axs[0, 1]
    for form, color, marker in (("current", C_CURRENT, "o"), ("pr_quadratic", C_PR, "s")):
        a = design(form, "eta_ov", points.PR, points.N_rps, points.T_evap_C)
        ax.scatter(
            points.PR,
            points.eta_ov - a @ coefficients[form]["eta_ov"],
            s=9,
            marker=marker,
            facecolor="none",
            edgecolor=color,
            lw=HAIRLINE,
        )
    ax.axhline(0.0, color=C_INK, lw=HAIRLINE)
    ax.set_xlabel("pressure ratio PR")
    ax.set_ylabel(r"measured − map, $\eta_{ov}$")
    ax.set_ylim(-0.2, 0.2)
    ax.grid(axis="y", lw=HAIRLINE, alpha=0.4)
    slice_panel(axs[1, 0], "eta_v", r"$\eta_v$", (0.80, 1.00))
    slice_panel(axs[1, 1], "f_loss", r"$f_{loss}$", (0.0, 0.30))
    handles = [
        Line2D([], [], color=C_CURRENT, lw=1.0, label="ln PR cubic (library)"),
        Line2D([], [], color=C_PR, lw=1.0, label="PR quadratic"),
        Line2D(
            [],
            [],
            color=C_INK,
            lw=1.0,
            marker="o",
            mfc="white",
            ms=3.5,
            mew=HAIRLINE,
            label=r"$T_e \approx 0$ °C, 70 rev/s",
        ),
        Line2D(
            [],
            [],
            color=C_INK,
            lw=1.0,
            ls="--",
            marker="^",
            mfc="white",
            ms=3.5,
            mew=HAIRLINE,
            label=r"$T_e \approx -20$ °C, 70 rev/s",
        ),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=4, fontsize=FS, frameon=False, bbox_to_anchor=(0.5, 1.0))
    dm.label_axes(axs.ravel().tolist())
    finalize(fig, OUT / "form_compare", formats=FORMATS)


def main() -> None:
    points = pd.read_csv(ARCHIVE / "points.csv")
    scores, coefficients = score(points)
    OUT.mkdir(exist_ok=True)
    payload = dict(
        points=len(points),
        cv=dict(folds=FOLDS, seed=SEED),
        scores=scores,
        eta_ov_peak_PR_at_70rps_Te0={form: eta_ov_peak(coefficients, form) for form in FORMS},
    )
    (OUT / "scores.json").write_text(json.dumps(payload, indent=1) + "\n")
    figure(points, scores, coefficients)
    print(json.dumps(payload, indent=1))


if __name__ == "__main__":
    main()
