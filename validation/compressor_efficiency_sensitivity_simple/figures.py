"""Figures 1–4 of the simplified sensitivity study (dartwork-mpl ``scientific``).

* ``fig1_functions``   -- the assumed efficiency functions, (a) against n*, (b) against P_r
* ``fig2_cop_heating`` -- 2 × 3 PLR–COP panels, BASE + one case each
* ``fig3_cop_cooling`` -- the same for cooling
* ``fig4_summary``     -- ΔCOP [%] against BASE at the common low-load PLR, both duties

The numbering follows the published page.  An operating-state figure (n* and
P_r against PLR) was produced earlier and dropped from the page; Figure 1
already carries the swept ranges as shaded bands.

PLR increases left → right on every axis.  Hollow markers are rows at the
compressor speed floor (``capacity_clamped == "min"``); the shaded band is that
region.  Rows where a synthetic efficiency sat on the harness clip would be
drawn with a cross -- none occur in the shipped data, and the caption says so.

Each ``fig*`` function returns the figure; :func:`main` saves PNG + SVG under
``validation/data/compressor_efficiency_sensitivity_simple/figures/``.  This
module does not import ``tmhp`` so it can run in a plotting-only environment.

Run::

    uv run python3 -m validation.compressor_efficiency_sensitivity_simple.figures
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

from .config import (  # noqa: E402
    A_N,
    B_V,
    CASES,
    DRIVER_LABEL,
    DUTIES,
    EFF_LABEL,
    ETA_BASE,
    FIG_DIR,
    N_STAR_C,
    OUT_DIR,
    PR_C,
    SHAPE,
)
from .functions import multiplier  # noqa: E402
from .metrics import load_all, summarise  # noqa: E402

LETTERS = "abcdef"
MS = 0.5  # marker-size scale; 37 points per axis

EFF_COLOR = {"eta_cmp_vol": COLORS["accent"], "eta_cmp_isen": COLORS["warm"], "eta_cmp": COLORS["accent2"]}
DUTY_COLOR = {"heating": COLORS["warm"], "cooling": COLORS["cool"]}
DUTY_TITLE = {"heating": "Heating, outdoor 7 °C / room 20 °C", "cooling": "Cooling, outdoor 35 °C / room 27 °C"}
BASE_STYLE = {"color": COLORS["ink"], "ls": "-"}
X_LIM = (0.06, 1.04)
X_TICKS = ticks(0.2, 1.0, 0.2)


def case_title(case: str, with_shape: bool = False) -> str:
    eff, drv = CASES[case]
    title = f"{case}: {EFF_LABEL[eff]} = f({DRIVER_LABEL[drv]})"
    return f"{title} ({SHAPE[eff]})" if with_shape else title


def _nice(vmin: float, vmax: float, n: int = 4) -> tuple[float, float, float]:
    span = max(vmax - vmin, 1e-9)
    raw = span / n
    mag = 10 ** np.floor(np.log10(raw))
    step = min((m for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=10) * mag
    lo, hi = np.floor(vmin / step) * step, np.ceil(vmax / step) * step
    if hi == lo:
        hi = lo + step
    return float(lo), float(hi), float(step)


def _yaxis(ax, vmin: float, vmax: float, n: int = 4) -> None:
    lo, hi, st = _nice(vmin, vmax, n)
    ax.set_ylim(lo, hi)
    ax.set_yticks(ticks(lo, hi, st))


def _xaxis(ax, label: bool = True) -> None:
    ax.set_xlim(*X_LIM)
    ax.set_xticks(X_TICKS)
    if label:
        ax.set_xlabel("Requested PLR [-]")
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)


def _floor_band(ax, *frames: pd.DataFrame) -> None:
    floors = [g[~g.modulating].plr_request.max() for g in frames if (~g.modulating).any()]
    if floors:
        ax.axvspan(X_LIM[0], max(floors) + 0.0125, color=COLORS["band20"], alpha=0.35, lw=0)


def _series(ax, g: pd.DataFrame, col: str, *, color, ls="-", label=None, scale=1.0) -> None:
    g = g.sort_values("plr_request")
    mod, flo = g[g.modulating], g[~g.modulating]
    ax.plot(mod.plr_request, mod[col] * scale, "o", ls=ls, ms=dm.fs(-4) * MS, lw=dm.lw(0), color=color, label=label)
    if len(flo):
        ax.plot(flo.plr_request, flo[col] * scale, "o", ms=dm.fs(-3) * MS, mfc="none", mec=color, mew=HAIRLINE * 1.5)
    clip = g[g.eta_clipped] if "eta_clipped" in g else g.iloc[0:0]
    if len(clip):
        ax.plot(clip.plr_request, clip[col] * scale, "x", ms=dm.fs(-2) * MS, color=color, mew=HAIRLINE * 2)


def _base_range(frames: dict[str, pd.DataFrame], col: str) -> dict[str, tuple[float, float]]:
    out = {}
    for duty, df in frames.items():
        mod = df[(df.case == "BASE") & df.modulating]
        out[duty] = (float(mod[col].min()), float(mod[col].max()))
    return out


# ---------------------------------------------------------------------------
# Figure 1 -- assumed efficiency functions
# ---------------------------------------------------------------------------
def fig1(frames: dict[str, pd.DataFrame]):
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("15cm", 0.42), gridspec_kw={"wspace": 0.32})
    panels = (
        ("n_star", np.linspace(0.20, 1.20, 300), N_STAR_C, r"Relative compressor speed $n^* = N / N_{rated}$ [-]"),
        ("p_r", np.linspace(1.40, 2.60, 300), PR_C, r"Pressure ratio $P_r = P_{dis} / P_{suc}$ [-]"),
    )
    ys: list[np.ndarray] = []
    for ax, (driver, x, xc, xlabel), letter in zip(axes, panels, "ab", strict=True):
        for duty, (lo, hi) in _base_range(frames, driver).items():
            ax.axvspan(lo, hi, color=DUTY_COLOR[duty], alpha=0.10, lw=0, label=f"BASE sweep range, {duty}")
        for eff, eta0 in ETA_BASE.items():
            y = eta0 * np.array([multiplier(v, driver, SHAPE[eff]) for v in x])
            ys.append(y)
            ax.plot(
                x,
                y,
                lw=dm.lw(0.5),
                color=EFF_COLOR[eff],
                label=rf"{EFF_LABEL[eff]}, $\eta_0$ = {eta0:.2f} ({SHAPE[eff]})",
            )
        ax.axvline(xc, color=COLORS["muted"], lw=HAIRLINE, ls=(0, (2, 2)))
        ax.set_xlim(x[0], x[-1])
        ax.set_xlabel(xlabel)
        ax.set_ylabel("Efficiency [-]")
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
        panel_letter(ax, letter, x=-0.18, y=1.08)
    # One y range for both panels, so the two drivers are read at the same scale.
    lo, hi, step = _nice(float(np.min(np.concatenate(ys))), 1.0, 3)
    for ax, (_, _, xc, _) in zip(axes, panels, strict=True):
        ax.set_ylim(lo, hi)
        ax.set_yticks(ticks(lo, hi, step))
        ax.text(
            xc,
            hi + 0.01 * (hi - lo),
            f"centre {xc:.2f}",
            ha="center",
            va="bottom",
            fontsize=dm.fs(-3),
            color=COLORS["muted"],
        )
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles, labels, loc="lower center", ncol=5, frameon=False, fontsize=dm.fs(-3), bbox_to_anchor=(0.5, 0.0)
    )
    fig.suptitle(
        rf"Assumed efficiency functions: $\eta_{{is}}, \eta_{{em}}$: $\eta_{{i,0}}\,[1 - {A_N:.2f}\,(x - x_c)^2]$;  "
        rf"$\eta_v$: $\eta_{{v,0}}\,[1 - {B_V:.2f}\,(x - x_c)]$;  centres $n^*_c$ {N_STAR_C:.2f}, $P_{{r,c}}$ {PR_C:.2f}",
        fontsize=dm.fs(-1),
        x=0.02,
        ha="left",
    )
    return fig


# ---------------------------------------------------------------------------
# Figures 2 / 3 -- 2 x 3 PLR-COP panels for one duty
# ---------------------------------------------------------------------------
def fig_cop(duty: str, df: pd.DataFrame, summary: pd.DataFrame):
    fig, axes = plt.subplots(2, 3, figsize=dm.figsize("17cm", 0.58), gridspec_kw={"wspace": 0.30, "hspace": 0.42})
    base = df[df.case == "BASE"]
    mod = df[df.modulating]
    ymin, ymax = mod.cop_sys.min(), mod.cop_sys.max()
    s = summary.set_index("case")
    for i, (ax, case) in enumerate(zip(axes.ravel(), CASES, strict=True)):
        eff, _ = CASES[case]
        g = df[df.case == case]
        _series(ax, base, "cop_sys", label="BASE", **BASE_STYLE)
        _series(ax, g, "cop_sys", color=EFF_COLOR[eff], label=case)
        _floor_band(ax, base, g)
        _yaxis(ax, ymin, ymax, 3)
        _xaxis(ax, label=i >= 3)
        ax.set_ylabel(r"$COP_{sys}$ [-]" if i % 3 == 0 else "")
        note = f"ΔCOP = {s.loc[case, 'd_cop_low_pct']:+.1f} % at PLR {s.loc[case, 'plr_low']:g}"
        if bool(s.loc[case, "internal_max"]):
            note += f"\nCOP peaks inside the range, at PLR {s.loc[case, 'plr_cop_max']:g}"
        ax.text(0.97, 0.04, note, transform=ax.transAxes, ha="right", va="bottom", fontsize=dm.fs(-3))
        ax.set_title(case_title(case, with_shape=True), fontsize=dm.fs(-1), loc="left")
        panel_letter(ax, LETTERS[i], x=-0.22, y=1.04)
        if i == 0:
            ax.legend(loc="lower left", frameon=False, fontsize=dm.fs(-3))
    fig.suptitle(
        f"PLR–COP, {DUTY_TITLE[duty]} -- hollow: compressor at speed floor (delivered > requested)",
        fontsize=dm.fs(-1),
        x=0.02,
        ha="left",
    )
    return fig


# ---------------------------------------------------------------------------
# Figure 4 -- ΔCOP at the common low-load PLR
# ---------------------------------------------------------------------------
def fig_summary(summaries: dict[str, pd.DataFrame]):
    fig, ax = plt.subplots(figsize=dm.figsize("12cm", 0.55))
    cases = list(CASES)
    x = np.arange(len(cases))
    w = 0.38
    allv = []
    for k, (duty, s) in enumerate(summaries.items()):
        ss = s.set_index("case").loc[cases]
        vals = ss.d_cop_low_pct.to_numpy()
        allv.append(vals)
        bars = ax.bar(
            x + (k - 0.5) * w, vals, w, color=DUTY_COLOR[duty], lw=0, label=f"{duty} (PLR {ss.plr_low.iloc[0]:g})"
        )
        span = max(abs(np.concatenate(allv)).max(), 1e-9)
        for b, v in zip(bars, vals, strict=True):
            v = 0.0 if abs(v) < 0.005 else v
            ax.text(
                b.get_x() + b.get_width() / 2,
                v + (0.02 if v >= 0 else -0.02) * span,
                f"{v:.1f}",
                ha="center",
                va="bottom" if v >= 0 else "top",
                fontsize=dm.fs(-3),
            )
    ax.axhline(0.0, color=COLORS["ink"], lw=HAIRLINE)
    ax.set_xticks(x)
    ax.set_xticklabels([case_title(c).replace(f"{c}: ", f"{c}\n") for c in cases], fontsize=dm.fs(-2))
    ax.set_ylabel(r"$\Delta COP$ vs BASE at low PLR [%]")
    v = np.concatenate(allv)
    lo, hi, st = _nice(min(v.min(), 0.0) * 1.3, max(v.max(), 0.0) * 1.3 + 0.5, 5)
    ax.set_ylim(lo, hi)
    ax.set_yticks(ticks(lo, hi, st))
    ax.grid(True, axis="y", alpha=0.25, linewidth=GRIDLINE)
    ax.legend(loc="lower left", frameon=False, fontsize=dm.fs(-3))
    fig.suptitle(
        r"$\Delta COP\,[\%] = (COP_{case} - COP_{BASE}) / COP_{BASE} \times 100$ at the lowest PLR every case still modulates",
        fontsize=dm.fs(-1),
        x=0.02,
        ha="left",
    )
    return fig


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--geometry-dir",
        default=None,
        help="also write each finalized figure's size and axes positions as JSON here, so the same "
        "figure can be rebuilt with identical layout in another process (dartwork-mpl MCP validation)",
    )
    a = ap.parse_args()
    apply_style("scientific")
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    frames, _ = load_all()
    summaries = {}
    for duty in DUTIES:
        s = summarise(frames[duty])
        s.to_csv(OUT_DIR / f"summary_{duty}.csv", index=False)
        summaries[duty] = s
    outputs = {
        "fig1_functions": (fig1(frames), {"mt": "9%", "mb": "10%"}),
        "fig2_cop_heating": (fig_cop("heating", frames["heating"], summaries["heating"]), {"mt": "5%"}),
        "fig3_cop_cooling": (fig_cop("cooling", frames["cooling"], summaries["cooling"]), {"mt": "5%"}),
        "fig4_summary": (fig_summary(summaries), {"mt": "6%"}),
    }
    for stem, (fig, margins) in outputs.items():
        finalize(fig, FIG_DIR / stem, formats=("svg", "png"), **margins)
        if a.geometry_dir:
            geometry = {
                "size_in": [float(v) for v in fig.get_size_inches()],
                "axes": [list(map(float, ax.get_position().bounds)) for ax in fig.axes],
            }
            (Path(a.geometry_dir) / f"{stem}.json").write_text(json.dumps(geometry))
        plt.close(fig)
        print(f"wrote {stem}.png/.svg")


if __name__ == "__main__":
    main()
