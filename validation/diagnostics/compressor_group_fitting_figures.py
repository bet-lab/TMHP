"""Figures F1-F3 of the compressor group-fitting sensitivity.

Reads only the CSVs written by ``compressor_group_fitting_once``; no refit and
no resimulation happen here, so a figure can be redrawn without paying for the
cross-validation again.

F1  the fitted efficiency curves against relative speed, pooled against every
    subgroup, at three representative pressure ratios.
F2  leave-one-compressor-out error of each subgroup fit, beside the error the
    pooled fit makes on the *same rows* -- the overfitting check.
F3  the part-load COP of the assembled heat pump under each coefficient set.

Typography follows the Notion column (``FS_BASE`` 11 pt) rather than the
``report`` preset's 8 pt, which does not survive the downscale.
"""

from __future__ import annotations

from pathlib import Path

import dartwork_mpl as dm
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from scripts.visualization._dmpl_common import (  # noqa: E402
    COLORS,
    GRIDLINE,
    HAIRLINE,
    finalize,
    panel_letter,
    ticks,
)
from validation.diagnostics.professor_summary_figures import (  # noqa: E402
    _grid,
    _style,
    _title_headroom,
    _top_legend,
)

MS = 3.0
PR_SHOW = (2.0, 3.0, 4.5)
N_GRID = np.linspace(0.20, 1.40, 121)

#: Colour and dash per group.  Case A (refrigerant) is solid, case B (size)
#: dashed, case C (refrigerant x size) dotted, so the reader can tell which
#: split a curve belongs to without reading the legend.
STYLE = {
    "pooled": (COLORS["ink"], "solid", "o"),
    "ref=R410A": (COLORS["accent"], "solid", "s"),
    "ref=R32": (COLORS["accent3"], "solid", "^"),
    "ref=R407C": (COLORS["warm"], "solid", "D"),
    "ref=R290": (COLORS["ess"], "solid", "v"),
    "ref=R22": (COLORS["load"], "solid", "P"),
    "size=Small": (COLORS["cool"], "dashed", "s"),
    "size=Medium": (COLORS["ess"], "dashed", "^"),
    "size=Large": (COLORS["load"], "dashed", "D"),
    "R410A x Small": (COLORS["cool"], (0, (1, 1.6)), "s"),
    "R410A x Medium": (COLORS["ess"], (0, (1, 1.6)), "^"),
    "R410A x Large": (COLORS["load"], (0, (1, 1.6)), "D"),
    "type=scroll": (COLORS["accent2"], "dashdot", "o"),
    "type=rotary": (COLORS["hot"], "dashdot", "X"),
}
LABEL = {
    "pooled": "Pooled (76)",
    "ref=R410A": "R410A",
    "ref=R32": "R32",
    "ref=R407C": "R407C",
    "ref=R290": "R290",
    "ref=R22": "R22",
    "size=Small": "Small",
    "size=Medium": "Medium",
    "size=Large": "Large",
    "R410A x Small": "R410A · Small",
    "R410A x Medium": "R410A · Medium",
    "R410A x Large": "R410A · Large",
    "type=scroll": "Scroll",
    "type=rotary": "Rotary",
}
F1_GROUPS = (
    "pooled",
    "ref=R410A",
    "ref=R32",
    "ref=R407C",
    "size=Small",
    "size=Medium",
    "size=Large",
    "type=rotary",
)
F2_ORDER = (
    "ref=R410A",
    "ref=R32",
    "ref=R407C",
    "ref=R290",
    "ref=R22",
    "size=Small",
    "size=Medium",
    "size=Large",
    "R410A x Small",
    "R410A x Medium",
    "R410A x Large",
    "type=scroll",
    "type=rotary",
)
F3_GROUPS = ("pooled", "ref=R32", "ref=R410A", "size=Small", "size=Medium", "size=Large", "type=rotary")
CASE_TITLE = {
    "ashp_heating": "ASHP · heating",
    "ashp_cooling": "ASHP · cooling",
    "ashpb_heating": "ASHPB · heating",
}


# ---------------------------------------------------------------------------
# the correlations, evaluated outside the library so a group's coefficients can
# be swapped without touching module state
# ---------------------------------------------------------------------------
def eta_vol_of(row: pd.Series, pr: float, n: np.ndarray) -> np.ndarray:
    u = np.maximum(0.0, 1.0 / n - 1.0)
    return np.maximum(0.50, 1.0 - row["eta_vol:a"] * (pr - 1.0) - row["eta_vol:b"] * u)


def eta_oi_of(row: pd.Series, pr: float, n: np.ndarray) -> np.ndarray:
    g = row["eta_oi:A"] - row["eta_oi:B"] * pr - row["eta_oi:C"] / pr
    u = np.maximum(0.0, 1.0 / n - 1.0)
    return g * np.maximum(0.0, 1.0 - row["eta_oi:c"] * (pr - 1.0) * u)


def eta_em_of(pr: float, n: np.ndarray) -> np.ndarray:
    """Common to every group: the split is one machine's evidence and is not refitted."""
    import tmhp.compressor_efficiency as ce

    s = n * (1.0 + ce.ETA_EM_N0) / (n + ce.ETA_EM_N0)
    x = max(pr - 1.0, 0.05)
    m = x / (x + ce.ETA_EM_P0) * (3.0 - 1.0 + ce.ETA_EM_P0) / (3.0 - 1.0) if ce.ETA_EM_P0 > 0 else 1.0
    return ce.ETA_EM_REF * s * m


# ---------------------------------------------------------------------------
def _save(fig, out_dir: Path, name: str, **margins) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for extra in (0, 2, 4, 6, 8, 12):
        try:
            kw = {k: f"{float(v.rstrip('%')) + extra}%" for k, v in margins.items()}
            finalize(fig, out_dir / name, formats=("svg", "png"), margin=f"{2 + extra}%", **kw)
            break
        except RuntimeError as exc:
            if extra == 12:
                raise exc
    plt.close(fig)
    print("wrote", name)


def _save_top_legend(fig, ax, out_dir: Path, name: str, *, ncol=None, gap: float = 2.0, **margins) -> None:
    frac = _top_legend(fig, ax, ncol=ncol) + _title_headroom(fig)
    _save(fig, out_dir, name, mt=f"{100 * frac + gap:.1f}%", **margins)


# ---------------------------------------------------------------------------
# F1 -- efficiency curves
# ---------------------------------------------------------------------------
def figure_f1(out_dir: Path, coef: pd.DataFrame, n_min_data: float) -> None:
    c = coef.set_index("group")
    fig, axes = plt.subplots(3, 3, figsize=dm.figsize("17cm", 0.92), squeeze=False, sharex=True)
    rows = (
        ("eta_vol", r"$\eta_{\mathrm{vol}}$ [-]", lambda r, pr, n: eta_vol_of(r, pr, n)),
        ("eta_oi", r"$\eta_{\mathrm{is}}\,\eta_{\mathrm{em}}$ [-]", lambda r, pr, n: eta_oi_of(r, pr, n)),
        ("eta_isen", r"$\eta_{\mathrm{is}}$ [-]", lambda r, pr, n: eta_oi_of(r, pr, n) / eta_em_of(pr, n)),
    )
    for i, (_key, ylab, fn) in enumerate(rows):
        for j, pr in enumerate(PR_SHOW):
            ax = axes[i][j]
            ax.axvspan(N_GRID[0], n_min_data, color=COLORS["band20"], alpha=0.35, lw=0)
            for gkey in F1_GROUPS:
                if gkey not in c.index or not np.isfinite(c.loc[gkey, "eta_vol:a"]):
                    continue
                col, ls, _ = STYLE[gkey]
                ax.plot(
                    N_GRID,
                    fn(c.loc[gkey], pr, N_GRID),
                    color=col,
                    ls=ls,
                    lw=dm.lw(1) if gkey == "pooled" else dm.lw(0),
                    label=LABEL[gkey] if (i == 0 and j == 0) else None,
                )
            _grid(ax)
            ax.set_xlim(N_GRID[0], N_GRID[-1])
            ax.set_xticks(ticks(0.2, 1.4, 0.2))
            if i == 0:
                ax.set_title(f"$r_p$ = {pr:g}", fontsize=dm.fs(-1.0), loc="left")
            if j == 0:
                ax.set_ylabel(ylab)
                panel_letter(ax, "abc"[i], x=-0.28)
            if i == 2:
                ax.set_xlabel(r"Relative speed $n^*$ [-]")
    for i in range(3):  # one y-range per row, so the three lifts are comparable
        lo = min(ax.get_ylim()[0] for ax in axes[i])
        hi = max(ax.get_ylim()[1] for ax in axes[i])
        for ax in axes[i]:
            ax.set_ylim(lo, hi)
    _save_top_legend(fig, axes[0][0], out_dir, "F1_group_efficiency_curves", ncol=4, ml="4%")


# ---------------------------------------------------------------------------
# F2 -- cross-validation
# ---------------------------------------------------------------------------
def _case_breaks(order: list[str], d: pd.DataFrame) -> list[float]:
    """Tick positions between two neighbouring groups that belong to different cases."""
    cases = [d.loc[o, "case"] for o in order]
    return [i - 0.5 for i in range(1, len(order)) if cases[i] != cases[i - 1]]


def figure_f2(out_dir: Path, cv: pd.DataFrame) -> None:
    d = cv.set_index("group")
    order = [g for g in F2_ORDER if g in d.index]
    x = np.arange(len(order), dtype=float)
    fig, axes = plt.subplots(2, 2, figsize=dm.figsize("17cm", 0.78), squeeze=False, sharex=True)
    panels = (
        (0, 0, "eta_vol_loco", r"$\eta_{\mathrm{vol}}$ · LOCO"),
        (0, 1, "eta_oi_loco", r"$\eta_{\mathrm{is}}\eta_{\mathrm{em}}$ · LOCO"),
        (1, 0, "eta_vol_st", r"$\eta_{\mathrm{vol}}$ · speed transfer"),
        (1, 1, "eta_oi_st", r"$\eta_{\mathrm{is}}\eta_{\mathrm{em}}$ · speed transfer"),
    )
    for i, j, stem, title in panels:
        ax = axes[i][j]
        g = d.loc[order, f"{stem}_group"].to_numpy(dtype=float)
        p = d.loc[order, f"{stem}_pooled"].to_numpy(dtype=float)
        ax.bar(x - 0.20, p, width=0.38, color=COLORS["muted"], label="pooled fit")
        ax.bar(x + 0.20, g, width=0.38, color=COLORS["accent"], label="subgroup fit")
        top = np.nanmax(np.concatenate([p, g]))
        for k, (gv, pv) in enumerate(zip(g, p, strict=True)):
            if not (np.isfinite(gv) and np.isfinite(pv)):
                continue
            # The pair is hard to read when the two bars are within a few
            # tenths of a point, so the gap is printed rather than eyeballed.
            ax.text(
                x[k],
                max(gv, pv) + 0.02 * top,
                f"{gv - pv:+.1f}",
                ha="center",
                va="bottom",
                fontsize=dm.fs(-4.0),
                color=COLORS["hot"] if gv > pv else COLORS["ess"],
            )
        for sep in _case_breaks(order, d):  # A | B | C | D
            ax.axvline(sep, color=COLORS["muted"], lw=HAIRLINE, ls=(0, (2, 2)))
        _grid(ax)
        ax.set_axisbelow(True)
        ax.set_ylim(0, 1.18 * top)
        ax.set_xlim(-0.7, len(order) - 0.3)
        ax.set_ylabel("weighted MAPE [%]")
        ax.set_title(title, fontsize=dm.fs(-1.5), loc="left")
        if i == 1:
            ax.set_xticks(x)
            ax.set_xticklabels(
                [f"{LABEL[o]} ({int(d.loc[o, 'eta_oi_n_machines'])})" for o in order],
                fontsize=dm.fs(-3.0),
                rotation=40,
                ha="right",
            )
    _save_top_legend(fig, axes[0][0], out_dir, "F2_group_cross_validation", ncol=2)


# ---------------------------------------------------------------------------
# F3 -- part-load COP
# ---------------------------------------------------------------------------
def _split(g: pd.DataFrame):
    ok = g[(g.failure_reason == "none") & g.converged].sort_values("plr_request")
    return ok[ok.capacity_clamped.isna()], ok[ok.capacity_clamped == "min"]


def figure_f3(out_dir: Path, sweep: pd.DataFrame) -> None:
    cases = [c for c in CASE_TITLE if c in set(sweep.case)]
    fig, axes = plt.subplots(2, len(cases), figsize=dm.figsize("17cm", 0.70), squeeze=False, sharex=True)
    base = {c: sweep[(sweep.case == c) & (sweep.group == "pooled")].set_index("plr_request").cop_sys for c in cases}
    for j, case in enumerate(cases):
        top, bot = axes[0][j], axes[1][j]
        for gkey in F3_GROUPS:
            g = sweep[(sweep.case == case) & (sweep.group == gkey)]
            if not len(g):
                continue
            col, ls, mk = STYLE[gkey]
            mod, floor = _split(g)
            top.plot(
                100 * mod.plr_request,
                mod.cop_sys,
                color=col,
                ls=ls,
                lw=dm.lw(0),
                marker=mk,
                ms=MS,
                label=LABEL[gkey] if j == 0 else None,
            )
            if len(floor):
                top.plot(
                    100 * floor.plr_request,
                    floor.cop_sys,
                    ls="none",
                    marker=mk,
                    ms=MS,
                    mfc="white",
                    mec=col,
                    mew=HAIRLINE,
                )
            rel = 100 * (g.set_index("plr_request").cop_sys / base[case] - 1.0)
            mod_r, floor_r = _split(g.assign(_rel=rel.reindex(g.plr_request).to_numpy()))
            bot.plot(100 * mod_r.plr_request, mod_r._rel, color=col, ls=ls, lw=dm.lw(0), marker=mk, ms=MS)
            if len(floor_r):
                bot.plot(
                    100 * floor_r.plr_request,
                    floor_r._rel,
                    ls="none",
                    marker=mk,
                    ms=MS,
                    mfc="white",
                    mec=col,
                    mew=HAIRLINE,
                )
        bot.axhline(0.0, color=COLORS["muted"], lw=HAIRLINE)
        for ax in (top, bot):
            _grid(ax)
            ax.set_xlim(0, 100)
            ax.set_xticks(ticks(0, 100, 20))
        top.set_title(CASE_TITLE[case], fontsize=dm.fs(-1.5), loc="left")
        if j == len(cases) // 2:
            bot.set_xlabel("Requested part-load ratio [%]")
        if j == 0:
            top.set_ylabel("System COP [-]")
            bot.set_ylabel("ΔCOP vs pooled [%]")
            panel_letter(top, "a", x=-0.30)
            panel_letter(bot, "b", x=-0.30)
    lo = min(ax.get_ylim()[0] for ax in axes[1])
    hi = max(ax.get_ylim()[1] for ax in axes[1])
    for ax in axes[1]:
        ax.set_ylim(lo, hi)
    _save_top_legend(fig, axes[0][0], out_dir, "F3_group_plr_cop", ncol=4, ml="3%")


# ---------------------------------------------------------------------------
def make_figures(out_dir: Path) -> None:
    _style()
    coef = pd.read_csv(out_dir / "group_coefficients.csv")
    cv = pd.read_csv(out_dir / "group_cv.csv")
    import json

    n_min = float(json.loads((out_dir / "meta.json").read_text())["n_star_min"])
    figure_f1(out_dir, coef, n_min)
    figure_f2(out_dir, cv)
    sweep_path = out_dir / "plr_group_sensitivity.csv"
    if sweep_path.exists():
        figure_f3(out_dir, pd.read_csv(sweep_path))
