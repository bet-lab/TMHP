"""Review figures for the decided map form (#247): error of each form, and what the decided form changes.

Reads   the archive points.csv (via form_compare) and the map data domain (coefficients.json)
Writes  validation/compressor_maps/ossorio_te/form_compare/review_errors.png and .pdf
        validation/compressor_maps/ossorio_te/form_compare/review_change.png and .pdf
        validation/compressor_maps/ossorio_te/form_compare/review.json

Decided form: eta_v and f_loss plain PR quadratics, eta_ov (overall efficiency) the ln PR cubic, N and T_e unscaled.
"Change" compares the library maps (ln PR cubic, N/70, T_e/10) with the decided form at 6000 random operating points
inside the data domain (speeds 30-110 rev/s, the convex (PR, T_e) hull of the measured points).

    uv run python -m validation.compressor_maps.ossorio_te.form_review
"""

from __future__ import annotations

import json

import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402
from scripts.visualization._dmpl_common import HAIRLINE, apply_style, finalize  # noqa: E402

from validation.compressor_maps.ossorio_te.fit import ARCHIVE  # noqa: E402
from validation.compressor_maps.ossorio_te.form_compare import OUT, design, score  # noqa: E402

apply_style("scientific", svg_fonttype=None)

# ── config ─────────────────────────────────────────────────────────────
SIZE_ERRORS = ("17cm", "6.4cm")
SIZE_CHANGE = ("17cm", "5.8cm")
FORMATS = ("png", "pdf")
N_POINTS = 6000
SEED = 1
C_LN3 = "#0F766E"  # teal-700: ln PR cubic
C_LN2 = "#94A3B8"  # slate-400: ln PR quadratic
C_PR2 = "#F97316"  # orange-500: PR quadratic
C_INK = "#1F2937"
FS = 7.0
NAMES = {"eta_v": r"Volumetric $\eta_v$", "eta_ov": r"Overall $\eta_{ov}$", "f_loss": r"Loss fraction $f_{loss}$"}
DECIDED = {"eta_v": "pr_quadratic", "eta_ov": "current_raw", "f_loss": "pr_quadratic"}
# ═════════════════════════════════════════════════════════════════════


def errors_figure(scores: dict) -> None:
    fig, axs = plt.subplots(1, 3, figsize=dm.figsize(*SIZE_ERRORS), gridspec_kw=dict(wspace=0.38))
    forms = [
        ("current_raw", "ln PR cubic", C_LN3),
        ("ln_quadratic", "ln PR quadratic", C_LN2),
        ("pr_quadratic", "PR quadratic", C_PR2),
    ]
    for ax, q in zip(axs, ("eta_v", "eta_ov", "f_loss"), strict=True):
        x = np.arange(3)
        for xi, (f, lab, col) in zip(x, forms, strict=True):
            s = scores[f][q]
            ax.bar(xi, s["cv_rmse"], 0.62, color=col, label=lab)
            ax.text(
                xi,
                s["cv_rmse"] * 1.02,
                f"{s['cv_rmse']:.4f}\n({s['terms']} terms)",
                ha="center",
                va="bottom",
                fontsize=FS * 0.9,
            )
            if DECIDED[q] == f:
                ax.annotate(
                    "adopted",
                    (xi, s["cv_rmse"]),
                    xytext=(0, 21),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=FS,
                    fontweight="bold",
                    color=C_INK,
                )
        ax.set_xticks(x, ["ln PR\ncubic", "ln PR\nquadratic", "PR\nquadratic"])
        top = max(scores[f][q]["cv_rmse"] for f, _, _ in forms)
        ax.set_ylim(0, top * 1.75)
        ax.set_title(NAMES[q], loc="left", fontsize=8.5)
        ax.grid(axis="y", lw=HAIRLINE, alpha=0.4)
    axs[0].set_ylabel("10-fold CV RMSE, 134 points [-]")
    finalize(fig, OUT / "review_errors", formats=FORMATS)


def change_figure(scores_coef: dict) -> dict:
    hull = json.loads((ARCHIVE / "coefficients.json").read_text())["domain"]["hull_PR_Te"]
    poly = MPath(hull)
    rng = np.random.default_rng(SEED)
    pts = []
    while len(pts) < N_POINTS:
        pr, te = rng.uniform(1.4, 12.7), rng.uniform(-31.0, 26.0)
        if poly.contains_point((pr, te)):
            pts.append((pr, rng.uniform(30.0, 110.0), te))
    pr, n, te = (np.array(v) for v in zip(*pts, strict=True))

    def ev(form, q):
        return design(form, q, pr, n, te) @ scores_coef[form][q]

    d_v = ev("pr_quadratic", "eta_v") - ev("current", "eta_v")
    d_f = ev("pr_quadratic", "f_loss") - ev("current", "f_loss")
    d_ov = ev("current_raw", "eta_ov") - ev("current", "eta_ov")
    eo = ev("current", "eta_ov")
    d_is = 100 * ((eo / (1 - ev("pr_quadratic", "f_loss"))) / (eo / (1 - ev("current", "f_loss"))) - 1)
    fig, axs = plt.subplots(1, 3, figsize=dm.figsize(*SIZE_CHANGE), gridspec_kw=dict(wspace=0.35))
    out = {}
    for ax, v, lab, col, key in (
        (axs[0], d_v, r"$\Delta\eta_v$ [-]", C_LN3, "eta_v"),
        (axs[1], d_f, r"$\Delta f_{loss}$ [-]", C_PR2, "f_loss"),
        (axs[2], d_is, r"$\Delta\eta_{isen}$ [%]", "#1D4ED8", "eta_isen_pct"),
    ):
        ax.hist(v, bins=40, color=col)
        ax.axvline(0, color=C_INK, lw=HAIRLINE)
        p95 = float(np.percentile(np.abs(v), 95))
        mx = float(np.max(np.abs(v)))
        ax.set_title(f"95 % within ±{p95:.3g}, max {mx:.3g}", loc="left", fontsize=FS)
        ax.set_xlabel(lab)
        out[key] = dict(p95=p95, max=mx, median=float(np.median(np.abs(v))))
    axs[0].set_ylabel(f"operating points (of {N_POINTS})")
    out["eta_ov_max_abs"] = float(np.max(np.abs(d_ov)))
    finalize(fig, OUT / "review_change", formats=FORMATS)
    return out


def main() -> None:
    points = pd.read_csv(ARCHIVE / "points.csv")
    scores, coefficients = score(points)
    errors_figure(scores)
    change = change_figure(coefficients)
    payload = dict(
        decided={q: f for q, f in DECIDED.items()},
        cv_rmse={f: {q: scores[f][q]["cv_rmse"] for q in scores[f]} for f in scores},
        change_inside_domain=change,
        scaling_check=dict(
            note="current (N/70, T_e/10) and current_raw (N, T_e) are the same function: equal RMSE and CV",
            current={q: scores["current"][q]["rmse"] for q in scores["current"]},
            current_raw={q: scores["current_raw"][q]["rmse"] for q in scores["current_raw"]},
        ),
    )
    (OUT / "review.json").write_text(json.dumps(payload, indent=1) + "\n")
    print(json.dumps(payload, indent=1))


if __name__ == "__main__":
    main()
