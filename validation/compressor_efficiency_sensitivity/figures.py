"""Figures 1-6 of the compressor-efficiency sensitivity study (dartwork-mpl ``scientific``).

Per duty (heating, cooling):

* ``fig1_curves``   -- the synthetic efficiency curves against n* and against PR
* ``fig2_plr_cop``  -- 2 × 3 panels, each with C0 / CURRENT / one case
* ``fig3_dcop``     -- ΔCOP % against C0, speed-based and PR-based panels
* ``fig4_eta_traj`` -- the varied efficiency the solver actually experienced, vs PLR
* ``fig5_state``    -- n*, PR, refrigerant flow and compressor power vs PLR, all cases

Across duties:

* ``fig6_summary``  -- ΔCOP at the common low-load point and the effective
  sensitivity index S = (ΔCOP/COP_C0) / (Δη/η_ref)

Hollow markers are rows at the compressor speed floor (``capacity_clamped ==
"min"``); the shaded band is that region.  Output: PNG + SVG under
``validation/data/compressor_efficiency_sensitivity/figures/``.
"""

from __future__ import annotations

import argparse
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

from tmhp.compressor_efficiency import COEFFICIENT_VERSION  # noqa: E402

from .metrics import DRIVER_LABEL, EFF_LABEL, load, summarise  # noqa: E402
from .sweep import CASES, OUT_DIR  # noqa: E402
from .synthetic_efficiencies import CapShape, cap_multiplier  # noqa: E402

FIG_DIR = OUT_DIR / "figures"
LETTERS = "abcdefgh"
MS = 0.5  # marker-size scale; ~37 points per axis merge at full size

EFF_COLOR = {"eta_cmp_vol": COLORS["accent"], "eta_cmp_isen": COLORS["warm"], "eta_cmp": COLORS["accent2"]}
DRIVER_LS = {"n_star": "-", "pr": (0, (3.0, 1.6))}
CASE_COLOR = {case: EFF_COLOR[eff] for case, (eff, _) in CASES.items()}
CASE_LS = {case: DRIVER_LS[drv] for case, (_, drv) in CASES.items()}
CASE_LABEL = {case: f"{case}: {EFF_LABEL[eff]}({DRIVER_LABEL[drv]})" for case, (eff, drv) in CASES.items()}
C0_STYLE = {"color": COLORS["ink"], "ls": "-"}
CUR_STYLE = {"color": COLORS["muted"], "ls": (0, (1.2, 1.2))}
DUTY_TITLE = {"heating": "Heating, outdoor 7 °C / room 20 °C", "cooling": "Cooling, outdoor 35 °C / room 27 °C"}
X_LIM = (1.04, 0.06)
X_TICKS = ticks(0.2, 1.0, 0.2)


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


def _floor_band(ax, df_case: pd.DataFrame) -> None:
    flo = df_case[~df_case.modulating]
    if len(flo):
        ax.axvspan(flo.plr_request.max() + 0.0125, X_LIM[1], color=COLORS["band20"], alpha=0.35, lw=0)


def _series(ax, g: pd.DataFrame, col: str, *, color, ls="-", label=None, scale=1.0, marker=True):
    mod, flo = g[g.modulating], g[~g.modulating]
    fmt = "o" if marker else ""
    ax.plot(mod.plr_request, mod[col] * scale, fmt, ls=ls, ms=dm.fs(-4) * MS, lw=dm.lw(0), color=color, label=label)
    if marker and len(flo):
        ax.plot(flo.plr_request, flo[col] * scale, "o", ms=dm.fs(-3) * MS, mfc="none", mec=color, mew=HAIRLINE * 1.5)


# ---------------------------------------------------------------------------
# Figure 1 -- synthetic efficiency curves
# ---------------------------------------------------------------------------
def fig1(duty: str, params: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("15cm", 0.42), gridspec_kw={"wspace": 0.35})
    eta_ref = params["eta_ref"]
    for ax, (driver, xlabel), letter in zip(
        axes, (("n_star", "n* = N / N_rated [-]"), ("pr", "Pressure ratio PR [-]")), "ab", strict=True
    ):
        shape = CapShape(**params["anchors"][driver])
        pad = 0.12 * (shape.x_high - shape.x_low)
        x = np.linspace(shape.x_low - pad, shape.x_high + pad, 300)
        f = np.array([cap_multiplier(v, shape) for v in x])
        for eff in ("eta_cmp_vol", "eta_cmp_isen", "eta_cmp"):
            ax.plot(
                x,
                eta_ref[eff] * f,
                lw=dm.lw(0.5),
                color=EFF_COLOR[eff],
                label=f"{EFF_LABEL[eff]} (ref {eta_ref[eff]:.3f})",
            )
        for xv, name in ((shape.x_low, "low"), (shape.x_opt, "opt"), (shape.x_high, "high")):
            ax.axvline(xv, color=COLORS["muted"], lw=HAIRLINE, ls=(0, (2, 2)))
            ax.text(xv, 1.005, f"{name}\n{xv:.2f}", ha="center", va="bottom", fontsize=dm.fs(-3), color=COLORS["muted"])
        ax.set_ylim(0.55, 1.0)
        ax.set_yticks(ticks(0.6, 1.0, 0.1))
        ax.set_xlabel(xlabel)
        ax.set_ylabel("Efficiency [-]")
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
        ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-3))
        panel_letter(ax, letter, x=-0.18, y=1.10)
    fig.suptitle(
        f"Synthetic cap-shaped curves, {duty}: same normalised penalty on every efficiency "
        f"(low −{100 * params['sweep']['d_low']:.0f} %, high −{100 * params['sweep']['d_high']:.0f} %)",
        fontsize=dm.fs(-1),
        x=0.02,
        ha="left",
    )
    finalize(fig, FIG_DIR / f"fig1_curves_{duty}", formats=("svg", "png"), mt="9%")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Figure 2 -- 2 x 3 PLR-COP panels
# ---------------------------------------------------------------------------
def fig2(duty: str, df: pd.DataFrame, summary: pd.DataFrame) -> None:
    fig, axes = plt.subplots(2, 3, figsize=dm.figsize("17cm", 0.58), gridspec_kw={"wspace": 0.32, "hspace": 0.40})
    c0, cur = df[df.case == "C0"], df[df.case == "CURRENT"]
    ymin, ymax = df[df.modulating].cop_sys.min(), df[df.modulating].cop_sys.max()
    s = summary.set_index("case")
    for i, (ax, case) in enumerate(zip(axes.ravel(), CASES, strict=True)):
        g = df[df.case == case]
        _series(ax, c0, "cop_sys", label="C0 (constant)", **C0_STYLE)
        _series(ax, cur, "cop_sys", label=f"CURRENT ({COEFFICIENT_VERSION})", **CUR_STYLE)
        _series(ax, g, "cop_sys", color=CASE_COLOR[case], ls=CASE_LS[case], label=CASE_LABEL[case])
        _floor_band(ax, c0)
        _yaxis(ax, ymin, ymax, 5)
        _xaxis(ax, label=i >= 3)
        ax.set_ylabel("COP_sys [-]" if i % 3 == 0 else "")
        note = f"ΔCOP@PLR {s.loc[case, 'plr_low']:.2f}: {s.loc[case, 'd_cop_low_pct']:+.1f} %"
        note += f"\nmax COP at PLR {s.loc[case, 'plr_cop_max']:.2f}" + (
            " (internal)" if s.loc[case, "internal_max"] else ""
        )
        ax.text(0.03, 0.97, note, transform=ax.transAxes, ha="left", va="top", fontsize=dm.fs(-3))
        ax.set_title(CASE_LABEL[case], fontsize=dm.fs(-1), loc="left")
        panel_letter(ax, LETTERS[i], x=-0.22, y=1.04)
        if i == 0:
            ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-3))
    fig.suptitle(
        f"PLR-COP, {DUTY_TITLE[duty]} -- hollow: compressor at speed floor (delivered > requested)",
        fontsize=dm.fs(-1),
        x=0.02,
        ha="left",
    )
    finalize(fig, FIG_DIR / f"fig2_plr_cop_{duty}", formats=("svg", "png"), mt="5%")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Figure 3 -- ΔCOP % against C0
# ---------------------------------------------------------------------------
def _dcop_pct(df: pd.DataFrame, case: str) -> pd.DataFrame:
    c0 = df[df.case == "C0"].set_index("plr_request")
    g = df[df.case == case].set_index("plr_request")
    j = g.join(c0[["cop_sys", "modulating"]], rsuffix="_c0")
    j["d_pct"] = (j.cop_sys - j.cop_sys_c0) / j.cop_sys_c0 * 100.0
    j["modulating"] = j.modulating & j.modulating_c0
    return j.reset_index()


def fig3(duty: str, df: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("15cm", 0.42), gridspec_kw={"wspace": 0.30})
    allv = []
    for ax, driver, letter in zip(axes, ("n_star", "pr"), "ab", strict=True):
        cur = _dcop_pct(df, "CURRENT")
        _series(ax, cur, "d_pct", label="CURRENT", **CUR_STYLE)
        allv.append(cur[cur.modulating].d_pct)
        for case, (_, drv) in CASES.items():
            if drv != driver:
                continue
            j = _dcop_pct(df, case)
            _series(ax, j, "d_pct", color=CASE_COLOR[case], ls=CASE_LS[case], label=CASE_LABEL[case])
            allv.append(j[j.modulating].d_pct)
        ax.axhline(0.0, color=COLORS["ink"], lw=HAIRLINE)
        _floor_band(ax, df[df.case == "C0"])
        _xaxis(ax)
        ax.set_ylabel("ΔCOP vs C0 [%]")
        ax.set_title(f"Driver: {DRIVER_LABEL[driver]}", fontsize=dm.fs(-1), loc="left")
        ax.legend(loc="lower left", frameon=False, fontsize=dm.fs(-3))
        panel_letter(ax, letter, x=-0.18, y=1.04)
    v = pd.concat(allv)
    for ax in axes:
        _yaxis(ax, min(v.min(), 0.0), max(v.max(), 0.0), 5)
    fig.suptitle(f"ΔCOP relative to the constant control, {DUTY_TITLE[duty]}", fontsize=dm.fs(-1), x=0.02, ha="left")
    finalize(fig, FIG_DIR / f"fig3_dcop_{duty}", formats=("svg", "png"), mt="6%")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Figure 4 -- efficiency the solver experienced
# ---------------------------------------------------------------------------
def fig4(duty: str, df: pd.DataFrame, params: dict) -> None:
    fig, axes = plt.subplots(2, 3, figsize=dm.figsize("17cm", 0.58), gridspec_kw={"wspace": 0.32, "hspace": 0.40})
    cur = df[df.case == "CURRENT"]
    eta_ref = params["eta_ref"]
    for i, (ax, (case, (eff, _))) in enumerate(zip(axes.ravel(), CASES.items(), strict=True)):
        g = df[df.case == case]
        ax.axhline(eta_ref[eff], label="C0 / reference", **C0_STYLE, lw=HAIRLINE * 1.5)
        for frac, lab in ((1 - params["sweep"]["d_low"], "−10 %"), (1 - params["sweep"]["d_high"], "−5 %")):
            ax.axhline(eta_ref[eff] * frac, color=COLORS["muted"], lw=HAIRLINE, ls=(0, (2, 2)))
            ax.text(
                X_LIM[0] - 0.01,
                eta_ref[eff] * frac,
                lab,
                ha="left",
                va="bottom",
                fontsize=dm.fs(-4),
                color=COLORS["muted"],
            )
        _series(ax, cur, eff, label="CURRENT", **CUR_STYLE)
        _series(ax, g, eff, color=CASE_COLOR[case], ls=CASE_LS[case], label=CASE_LABEL[case])
        _floor_band(ax, g)
        lo = min(g[eff].min(), cur[eff].min(), eta_ref[eff] * 0.88)
        hi = max(g[eff].max(), cur[eff].max(), eta_ref[eff] * 1.02)
        _yaxis(ax, lo, hi, 4)
        _xaxis(ax, label=i >= 3)
        ax.set_ylabel(f"{EFF_LABEL[eff]} [-]")
        ax.set_title(CASE_LABEL[case], fontsize=dm.fs(-1), loc="left")
        panel_letter(ax, LETTERS[i], x=-0.22, y=1.04)
        if i == 0:
            ax.legend(loc="lower left", frameon=False, fontsize=dm.fs(-3))
    fig.suptitle(f"Efficiency trajectory actually traversed, {DUTY_TITLE[duty]}", fontsize=dm.fs(-1), x=0.02, ha="left")
    finalize(fig, FIG_DIR / f"fig4_eta_traj_{duty}", formats=("svg", "png"), mt="5%")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Figure 5 -- state feedback
# ---------------------------------------------------------------------------
def fig5(duty: str, df: pd.DataFrame) -> None:
    fig, axes = plt.subplots(2, 2, figsize=dm.figsize("15cm", 0.75), gridspec_kw={"wspace": 0.30, "hspace": 0.35})
    panels = (
        ("n_star", "n* = N / N_rated [-]", 1.0),
        ("pr", "Pressure ratio [-]", 1.0),
        ("m_dot_ref", "Refrigerant flow [g/s]", 1e3),
        ("E_cmp", "Compressor power [kW]", 1e-3),
    )
    for i, (ax, (col, lab, scale)) in enumerate(zip(axes.ravel(), panels, strict=True)):
        _series(ax, df[df.case == "C0"], col, label="C0", scale=scale, **C0_STYLE)
        _series(ax, df[df.case == "CURRENT"], col, label="CURRENT", scale=scale, **CUR_STYLE)
        for case in CASES:
            _series(
                ax,
                df[df.case == case],
                col,
                color=CASE_COLOR[case],
                ls=CASE_LS[case],
                label=CASE_LABEL[case],
                scale=scale,
            )
        _floor_band(ax, df[df.case == "C0"])
        mod = df[df.modulating]
        _yaxis(ax, mod[col].min() * scale, mod[col].max() * scale, 4)
        _xaxis(ax, label=i >= 2)
        ax.set_ylabel(lab)
        panel_letter(ax, LETTERS[i], x=-0.18, y=1.03)
        if i == 0:
            ax.legend(loc="upper right", frameon=False, fontsize=dm.fs(-4), ncol=2)
    fig.suptitle(f"Operating-point feedback, {DUTY_TITLE[duty]}", fontsize=dm.fs(-1), x=0.02, ha="left")
    finalize(fig, FIG_DIR / f"fig5_state_{duty}", formats=("svg", "png"), mt="5%")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Figure 6 -- summary across duties
# ---------------------------------------------------------------------------
def fig6(summaries: dict[str, pd.DataFrame]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("15cm", 0.42), gridspec_kw={"wspace": 0.30})
    cases = list(CASES)
    x = np.arange(len(cases))
    w = 0.38
    duty_color = {"heating": COLORS["warm"], "cooling": COLORS["cool"]}
    for ax, (col, lab), letter in zip(
        axes,
        (("d_cop_low_pct", "ΔCOP at common low-load PLR [%]"), ("S_low", "S = (ΔCOP/COP) / (Δη/η) [-]")),
        "ab",
        strict=True,
    ):
        for k, (duty, s) in enumerate(summaries.items()):
            ss = s.set_index("case").loc[cases]
            vals = ss[col].to_numpy()
            bars = ax.bar(
                x + (k - 0.5) * w, vals, w, color=duty_color[duty], label=f"{duty} (PLR {ss.plr_low.iloc[0]:.2f})", lw=0
            )
            for b, v in zip(bars, vals, strict=True):
                if np.isfinite(v):
                    v = 0.0 if abs(v) < 0.005 else v  # no "-0.0" labels
                    ax.text(
                        b.get_x() + b.get_width() / 2,
                        v + (0.02 if v >= 0 else -0.02) * (abs(vals[np.isfinite(vals)]).max() or 1),
                        f"{v:.1f}" if col.endswith("pct") else f"{v:.2f}",
                        ha="center",
                        va="bottom" if v >= 0 else "top",
                        fontsize=dm.fs(-4),
                    )
        ax.axhline(0.0, color=COLORS["ink"], lw=HAIRLINE)
        ax.set_xticks(x)
        ax.set_xticklabels(cases)
        ax.set_ylabel(lab)
        allv = pd.concat([s.set_index("case").loc[cases, col] for s in summaries.values()])
        lo, hi, st = _nice(min(allv.min(), 0.0) * 1.25, max(allv.max(), 0.0) * 1.25, 5)
        ax.set_ylim(lo, hi)
        ax.set_yticks(ticks(lo, hi, st))
        ax.grid(True, axis="y", alpha=0.25, linewidth=GRIDLINE)
        ax.legend(loc="lower left" if col.endswith("pct") else "upper left", frameon=False, fontsize=dm.fs(-3))
        panel_letter(ax, letter, x=-0.18, y=1.04)
    fig.suptitle(
        "Sensitivity summary at the lowest PLR every case still modulates", fontsize=dm.fs(-1), x=0.02, ha="left"
    )
    finalize(fig, FIG_DIR / "fig6_summary", formats=("svg", "png"), mt="6%")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duties", nargs="+", default=("heating", "cooling"))
    a = ap.parse_args()
    apply_style("scientific")
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, pd.DataFrame] = {}
    for duty in a.duties:
        df, params = load(duty)
        s = summarise(df, params)
        s.to_csv(OUT_DIR / f"summary_{duty}.csv", index=False)
        summaries[duty] = s
        fig1(duty, params)
        fig2(duty, df, s)
        fig3(duty, df)
        fig4(duty, df, params)
        fig5(duty, df)
        print(f"{duty}: figures 1-5 written")
    if len(summaries) > 1:
        fig6(summaries)
        print("figure 6 written")
    print(f"figures -> {Path(FIG_DIR).relative_to(OUT_DIR.parents[1])}")


if __name__ == "__main__":
    main()
