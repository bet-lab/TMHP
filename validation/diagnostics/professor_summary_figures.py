"""Figures for the part-load COP narrative page (coefficients v2026-09-24).

The baseline PLR-COP map is re-read from ``final_report`` and a non-vanishing
electrical term is added in post-processing::

    COP(W) = Q_delivered / (E_tot + W)

``W_CANDIDATE`` (50 W) is the level at which the low-load shape changes; the
measured Keymark ``P_TO`` proxy (11.6 W, ``validation/auxiliary_power/``) is
kept in the shape-metric table for reference. Neither is a fitted TMHP
coefficient -- ``P_TO`` is thermostat-off power, a measured lower bound on
parasitic power.

Every figure here is drawn for the page, so it follows two page rules that the
originating scripts do not:

* **one duty per figure.** Heating and cooling never share a canvas; they are
  separate files with the duty in the name. A reader comparing two duties
  scrolls, which is cheaper than squinting.
* **typography sized for a Notion column.** ``FS_BASE`` is well above the
  ``report`` preset, legends sit above the axes rather than inside them, and
  the panel count per figure is kept low so the downscale to ~700 px leaves
  the text readable without zooming.

P1  before / after PLR-COP map by outdoor temperature   (heating | cooling)
P2  Delta COP against PLR                     (artifact, not on the page)
P3  peak shift: PLR at the COP maximum        (artifact, not on the page)
P4  catalogue COP parity, without and with the fixed term (ASHP | ASHPB)
P5  fixed-power sensitivity, COP against PLR  ] marker-coded replots of the
P6  fixed-power fraction against PLR          ] sensitivity runs, whose own
P7  minimum fan power sensitivity             ] figures use line style alone
P8  the three fitted efficiencies against relative speed  (replot of F1)
P9  baseline PLR-COP map, current model                   (replot of F5)
P10 measured P_TO proxy applied to the part-load curve    (supplementary)
P11 minimum airflow sensitivity                           (supplementary)
P12 rated fan power sensitivity                           (supplementary)
P13 PLR-COP map with and without a fan power floor        (re-solved, not post-processed)
P14 catalogue COP parity with and without that same floor  (re-solved, not post-processed)

P8-P12 are replots of figures owned by ``final_figures.py``,
``auxiliary_power/pto_proxy.py`` and ``fan_model_sensitivity_once.py``. They
read the same CSVs -- nothing is re-simulated -- and exist only so the page
carries one typography and one duty-per-figure rule throughout. The original
scripts are left untouched.

Run::

    uv run python3 -m validation.diagnostics.professor_summary_figures
"""

from __future__ import annotations

import argparse

import dartwork_mpl as dm
import matplotlib

matplotlib.use("Agg")
import matplotlib as mpl  # noqa: E402
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

from tmhp import compressor_efficiency as ce  # noqa: E402
from validation.compressor_maps.schema import REPO_ROOT  # noqa: E402
from validation.fixed_boundary_plr.shape_metrics import metrics_for  # noqa: E402

SIM = REPO_ROOT / "validation" / "data" / "final_report"
OUT = REPO_ROOT / "validation" / "data" / "professor_summary"
RESULTS = REPO_ROOT / "validation" / "results"
FIG_DIR = REPO_ROOT / "validation" / "coefficients" / ce.COEFFICIENT_VERSION / "figures" / "professor"
LP = RESULTS / "low_load_fixed_power_sh_sc_once"
FAN = RESULTS / "fan_model_sensitivity_once"

W_MEASURED = 11.64285714285714  # Keymark P_TO, air-to-air single split, constant
W_CANDIDATE = 50.0
CASES = (
    (0.0, "Before — no fixed power"),
    (W_MEASURED, f"After — measured $P_{{TO}}$ {W_MEASURED:.1f} W"),
    (W_CANDIDATE, f"After — candidate {W_CANDIDATE:.0f} W"),
)
SHORT = (
    (0.0, "Before"),
    (W_MEASURED, r"After · measured $P_{TO}$ 11.6 W"),
    (W_CANDIDATE, "After · candidate 50 W"),
)
MS = 3.0

# The ``report`` preset sits at 8 pt, which survives a PDF but not the
# downscale into a Notion column. Everything on this page is drawn at
# ``FS_BASE`` instead; ``dm.fs`` is relative to ``font.size``, so the existing
# ``dm.fs(...)`` call sites move with it.
FS_BASE = 11.0


def _style() -> None:
    apply_style()
    mpl.rcParams.update({
        "font.size": FS_BASE,
        "axes.labelsize": FS_BASE,
        "axes.titlesize": FS_BASE,
        "xtick.labelsize": FS_BASE - 1.0,
        "ytick.labelsize": FS_BASE - 1.0,
        "legend.fontsize": FS_BASE - 0.5,
    })


FS_TITLE = 0.0  # relative to FS_BASE, for dm.fs()
FS_LEGEND = -0.5
FS_ANNOT = -0.5


def _save(fig, name: str, **margins) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    for extra in (0, 2, 4, 6, 8):
        try:
            kw = {k: f"{float(v.rstrip('%')) + extra}%" for k, v in margins.items()}
            finalize(fig, FIG_DIR / name, formats=("svg", "png"), margin=f"{2 + extra}%", **kw)
            break
        except RuntimeError as exc:
            if extra == 8:
                raise exc
    plt.close(fig)


def _top_legend(fig, ax, *, ncol: int | None = None, fs: float | None = None,
                max_width: float = 0.94) -> float:
    """Put ``ax``'s legend above the axes, centred on the figure, in one row.

    ``simple_layout`` measures axes content only, so a figure-level legend is
    invisible to it. The height it occupies is measured here and returned as a
    figure fraction for the caller to hand back as the top margin.

    A single row of seven outdoor temperatures is wider than the canvas at page
    type size, and a legend that runs off the edge is worse than a small one,
    so the size steps down until the row fits rather than wrapping to two.
    """
    handles, labels = ax.get_legend_handles_labels()
    base = dm.fs(fs if fs is not None else FS_LEGEND)
    frac = 0.0
    for size in (base, base - 0.5, base - 1.0, base - 1.5, base - 2.0, base - 2.5):
        leg = fig.legend(
            handles, labels,
            loc="upper center", bbox_to_anchor=(0.5, 1.0), ncol=ncol or len(handles),
            frameon=False, fontsize=size,
            handlelength=1.3, handletextpad=0.35, columnspacing=0.9, borderaxespad=0.0,
        )
        fig.canvas.draw()
        bb = leg.get_window_extent()
        frac = float(bb.height / fig.bbox.height)
        if bb.width / fig.bbox.width <= max_width or size == base - 2.5:
            return frac
        leg.remove()
    return frac


def _title_headroom(fig) -> float:
    """Height a left-aligned axes title needs, as a figure fraction.

    ``simple_layout`` does not measure ``set_title(loc="left")`` reliably (see
    ``_dmpl_common.finalize``), so a figure whose only top decoration is such a
    title gets no headroom for it and the title lands under the legend. Axes
    carrying a panel letter are skipped: the letter sits above the title and is
    measured, so its headroom already covers both.
    """
    fig.canvas.draw()
    need = 0.0
    for ax in fig.axes:
        if any(t.get_position()[1] > 1.0 for t in ax.texts):
            continue
        # ``ax.title`` is the centre title and is empty under ``loc="left"``;
        # the left-aligned one is a separate artist.
        for key in ("title", "_left_title", "_right_title"):
            t = getattr(ax, key, None)
            if t is None or not t.get_text():
                continue
            need = max(need, float(t.get_window_extent().height / fig.bbox.height))
    return need


def _save_top_legend(fig, ax, name: str, *, ncol: int | None = None, fs: float | None = None,
                     gap: float = 2.0, **margins) -> None:
    frac = _top_legend(fig, ax, ncol=ncol, fs=fs) + _title_headroom(fig)
    _save(fig, name, mt=f"{100 * frac + gap:.1f}%", **margins)


def _grid(ax) -> None:
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)


_STEPS = (0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0)


def _auto_ylim(values, max_ticks: int = 8) -> tuple[float, float, float]:
    """Round limits that hug the data, with the tick step that fits them.

    A shared ``(1, 11)`` across every map made the panels comparable and the
    curves flat; the shape is the message here, so the limits follow the data
    and the caption says so. ``max_ticks`` keeps the gridlines countable.
    """
    v = np.asarray([x for x in np.ravel(np.asarray(values, dtype=float)) if np.isfinite(x)])
    lo, hi = float(v.min()), float(v.max())
    pad = 0.06 * max(hi - lo, 1e-9)
    lo, hi = lo - pad, hi + pad
    for step in _STEPS:
        f_lo, f_hi = np.floor(lo / step) * step, np.ceil(hi / step) * step
        if round((f_hi - f_lo) / step) <= max_ticks - 1:
            return round(f_lo, 6), round(f_hi, 6), step
    return round(f_lo, 6), round(f_hi, 6), _STEPS[-1]


def _with_fixed(d: pd.DataFrame, w: float) -> pd.DataFrame:
    g = d.copy()
    g["cop_sys"] = g.q_delivered_W / (g.E_tot + w)
    g["cr_actual"] = g.plr_delivered  # name the shape-metric helper expects
    return g


def _mod_floor(g: pd.DataFrame):
    ok = g[g.failure_reason == "none"].sort_values("plr_request")
    return ok[ok.capacity_clamped.isna()], ok[ok.capacity_clamped == "min"]


def _plr_curve(ax, g, color, label=None):
    mod, floor = _mod_floor(g)
    ax.plot(100 * mod.plr_request, mod.cop_sys, lw=dm.lw(0), color=color, marker="o", ms=MS, label=label)
    if len(floor):
        ax.plot(100 * floor.plr_request, floor.cop_sys, ls="none", marker="o", ms=MS,
                mfc="white", mec=color, mew=HAIRLINE)


def _map_panel(ax, g, cmap_name, title, ylim, ystep, xlabel=True, ylabel=True, label=True):
    temps = sorted(g.t_outdoor_C.unique())
    cmap = plt.get_cmap(cmap_name)
    for i, t in enumerate(temps):
        col = cmap(0.1 + 0.8 * i / max(len(temps) - 1, 1))
        _plr_curve(ax, g[g.t_outdoor_C == t], col, label=f"{t:g} °C" if label else None)
    ax.set_xlim(0, 100)
    ax.set_xticks(ticks(0, 100, 20))
    ax.set_ylim(*ylim)
    ax.set_yticks(ticks(ylim[0], ylim[1], ystep))
    if xlabel:
        ax.set_xlabel("Requested part-load ratio [%]")
    if ylabel:
        ax.set_ylabel(r"System $\mathrm{COP}$ [-]")
    ax.set_title(title, fontsize=dm.fs(FS_TITLE), loc="left")
    _grid(ax)


DUTIES = {
    "heating": {"cmap": "viridis", "head": "Heating, room 20 °C"},
    "cooling": {"cmap": "plasma", "head": "Cooling, room 27 °C"},
}


# ---------------------------------------------------------------------------
# P1  before / after map, one duty per figure
# ---------------------------------------------------------------------------
def p1_before_after() -> None:
    d = pd.read_csv(SIM / "plr_map_ashp.csv")
    cols = ((0.0, "Before — no fixed power"), (W_CANDIDATE, f"After — {W_CANDIDATE:.0f} W fixed power"))
    for duty, style in DUTIES.items():
        panels = [_with_fixed(d[d.duty == duty], w) for w, _ in cols]
        ok = [g[g.failure_reason == "none"].cop_sys for g in panels]
        lo, hi, step = _auto_ylim(pd.concat(ok))
        fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.44))
        for c, ((w, tag), g) in enumerate(zip(cols, panels, strict=True)):
            _map_panel(axes[c], g, style["cmap"], tag,
                       (lo, hi), step, ylabel=(c == 0), label=(c == 0))
            panel_letter(axes[c], "ab"[c])
        _save_top_legend(fig, axes[0], f"P1_before_after_plr_cop_{duty}")

    out = []
    for duty in ("heating", "cooling"):
        for w, tag in CASES:
            g = _with_fixed(d[d.duty == duty], w)
            for t, gg in g.groupby("t_outdoor_C"):
                m = metrics_for(gg)
                out.append({"duty": duty, "W_fixed_W": w, "t_outdoor_C": t, **m})
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(out).to_csv(OUT / "p1_p3_shape_metrics.csv", index=False)


# ---------------------------------------------------------------------------
# P9  baseline map, current model, one duty per figure
# ---------------------------------------------------------------------------
def p9_baseline_map() -> None:
    d = pd.read_csv(SIM / "plr_map_ashp.csv")
    for duty, style in DUTIES.items():
        g = _with_fixed(d[d.duty == duty], 0.0)
        lo, hi, step = _auto_ylim(g[g.failure_reason == "none"].cop_sys)
        fig, ax = plt.subplots(figsize=dm.figsize("15cm", 0.52))
        _map_panel(ax, g, style["cmap"], f"ASHP · {style['head']}", (lo, hi), step)
        _save_top_legend(fig, ax, f"P9_baseline_plr_cop_{duty}")


# ---------------------------------------------------------------------------
# P2  Delta COP   (artifact, not on the page)
# ---------------------------------------------------------------------------
def p2_delta_cop() -> None:
    d = pd.read_csv(SIM / "plr_map_ashp.csv")
    fig, axes = plt.subplots(2, 2, figsize=dm.figsize("17cm", 0.60))
    rows = (("heating", "viridis", "Heating 20 °C"), ("cooling", "plasma", "Cooling 27 °C"))
    recs = []
    for r, (duty, cmap_name, head) in enumerate(rows):
        base = _with_fixed(d[d.duty == duty], 0.0)
        for c, (w, tag) in enumerate(SHORT[1:]):
            ax = axes[r][c]
            new = _with_fixed(d[d.duty == duty], w)
            temps = sorted(base.t_outdoor_C.unique())
            cmap = plt.get_cmap(cmap_name)
            for i, t in enumerate(temps):
                col = cmap(0.1 + 0.8 * i / max(len(temps) - 1, 1))
                b = base[(base.t_outdoor_C == t) & (base.failure_reason == "none")].sort_values("plr_request")
                n = new[(new.t_outdoor_C == t) & (new.failure_reason == "none")].sort_values("plr_request")
                ax.plot(100 * b.plr_request, n.cop_sys.to_numpy() - b.cop_sys.to_numpy(),
                        lw=dm.lw(0), color=col, marker="o", ms=MS, label=f"{t:g} °C")
                for plr, cb, cn in zip(b.plr_request, b.cop_sys, n.cop_sys):
                    recs.append({"duty": duty, "W_fixed_W": w, "t_outdoor_C": t, "plr_request": plr,
                                 "cop_before": cb, "cop_after": cn, "delta_cop": cn - cb})
            ax.axhline(0, color=COLORS["muted"], lw=HAIRLINE)
            ax.set_xlim(0, 100)
            ax.set_xticks(ticks(0, 100, 20))
            if r == 1:
                ax.set_xlabel("Requested part-load ratio [%]")
            ax.set_ylabel(r"$\Delta \mathrm{COP}$ [-]")
            ax.set_title(f"{head} · {tag}", fontsize=dm.fs(-2.0), loc="left")
            if c == 0:
                ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-3.0),
                          title="outdoor", title_fontsize=dm.fs(-3.0), ncol=2)
            _grid(ax)
            panel_letter(ax, "abcd"[r * 2 + c])
    _save(fig, "P2_delta_cop", mt="8%")
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(recs).to_csv(OUT / "p2_delta_cop.csv", index=False)


# ---------------------------------------------------------------------------
# P3  peak shift   (artifact, not on the page)
# ---------------------------------------------------------------------------
def p3_peak_shift() -> None:
    m = pd.read_csv(OUT / "p1_p3_shape_metrics.csv")
    colors = (COLORS["ink"], COLORS["accent"], COLORS["warm"])
    marks = ("o", "s", "^")
    fig, axes = plt.subplots(2, 2, figsize=dm.figsize("17cm", 0.60))
    for r, duty in enumerate(("heating", "cooling")):
        for c, (col, ylab, ylim, ystep) in enumerate(
            (("PLR_peak", "PLR at the COP maximum [%]", (0, 100), 20),
             ("COP_low_over_rated", r"$\mathrm{COP}_{low} / \mathrm{COP}_{rated}$ [-]", (0.8, 1.4), 0.1))):
            ax = axes[r][c]
            for (w, tag), cc, mk in zip(CASES, colors, marks):
                g = m[(m.duty == duty) & (m.W_fixed_W == w)].sort_values("t_outdoor_C")
                y = 100 * g[col] if col == "PLR_peak" else g[col]
                ax.plot(g.t_outdoor_C, y, lw=dm.lw(0), color=cc, marker=mk, ms=MS + 0.6, label=tag)
            if c == 1:
                ax.axhline(1.0, color=COLORS["muted"], lw=HAIRLINE)
            ax.set_ylim(*ylim)
            ax.set_yticks(ticks(ylim[0], ylim[1], ystep))
            if r == 1:
                ax.set_xlabel("Outdoor temperature [°C]")
            ax.set_ylabel(ylab)
            ax.set_title(f"{duty.capitalize()}", fontsize=dm.fs(-2.0), loc="left")
            if r == 0 and c == 0:
                ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-3.0))
            _grid(ax)
            panel_letter(ax, "abcd"[r * 2 + c])
    _save(fig, "P3_peak_shift", mt="8%")


# ---------------------------------------------------------------------------
# P4  catalogue parity with the same fixed term
# ---------------------------------------------------------------------------
def _stats(pred, obs):
    e = (np.asarray(pred) - np.asarray(obs)) / np.asarray(obs) * 100.0
    return float(np.mean(np.abs(e))), float(np.mean(e)), int(len(e))


def _parity_axes(ax, lo, hi, step, xlabel, ylabel):
    x = np.array([lo, hi])
    ax.fill_between(x, x * 0.9, x * 1.1, color=COLORS["band10"], alpha=0.35, lw=0, label="±10 %")
    ax.plot(x, x, color=COLORS["muted"], lw=HAIRLINE, label="1:1")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xticks(ticks(lo, hi, step))
    ax.set_yticks(ticks(lo, hi, step))
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_aspect("equal")
    _grid(ax)


def p4_parity() -> None:
    d = pd.read_csv(SIM / "fig4_cop_parity.csv")
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.50))
    recs = []
    for ax, mc, lo, hi, step, head in ((axes[0], "ASHP", 1, 9, 2, "ASHP · Daikin RXM-A, 5 units"),
                                       (axes[1], "ASHPB", 1, 7, 1, "ASHPB · Panasonic 9 + Samsung 1")):
        g = d[d.model_class == mc]
        q = g.q_kW * 1000.0
        _parity_axes(ax, lo, hi, step, "Catalogue COP [-]", "TMHP COP [-]")
        lines = [f"{'':>5} {'MAPE':>5} {'bias':>6} {'±10 %':>5}"]
        series = ((0.0, "no fixed power", COLORS["ink"], "o"),
                  (W_CANDIDATE, "with fixed power", COLORS["warm"], "^"))
        for w, tag, cc, mk in series:
            pred = q / (q / g.cop_pred + w)
            mape, bias, n = _stats(pred, g.cop_target)
            within = float((np.abs((pred - g.cop_target) / g.cop_target) <= 0.10).mean() * 100)
            ax.plot(g.cop_target, pred, mk, ms=MS, alpha=0.65, mew=0, color=cc, label=tag)
            lines.append(f"{('base' if not w else 'fixed'):>5} {mape:5.2f} {bias:+6.2f} {within:5.0f}")
            recs.append({"model_class": mc, "W_fixed_W": w, "MAPE_pct": mape, "bias_pct": bias,
                         "within_10pct": within, "n": n})
        # The unit description moved into the title: at page type size the
        # header line no longer fits inside the panel next to the stats block.
        ax.set_title(head, fontsize=dm.fs(FS_TITLE), loc="left")
        ax.text(0.04, 0.96, "\n".join(lines), transform=ax.transAxes, va="top",
                fontsize=dm.fs(FS_ANNOT), family="monospace")
        panel_letter(ax, "ab"[0 if mc == "ASHP" else 1])
    _save_top_legend(fig, axes[0], "P4_parity_before_after")
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(recs).to_csv(OUT / "p4_parity_metrics.csv", index=False)


# ---------------------------------------------------------------------------
# P5 / P6 / P7  marker-coded replots of the sensitivity runs
#
# The originals separate their series by line style alone, which does not
# survive printing or a colour-blind reader. Same data, same selection: the
# series are told apart by marker shape here.
# ---------------------------------------------------------------------------
CONDS = {
    "heating": (("heating_7", "ASHP · Heating 7/20 °C (main)"),
                ("heating_m7", "ASHP · Heating −7/20 °C (secondary)")),
    "cooling": (("cooling_35", "ASHP · Cooling 35/27 °C"),),
}
W_SERIES = ((0.0, COLORS["ink"], "o"), (25.0, COLORS["accent"], "s"),
            (50.0, COLORS["warm"], "^"), (100.0, COLORS["accent3"], "D"))


def _series(ax, g, col, color, marker, label=None, ls="solid"):
    """House convention: hollow markers past the compressor speed floor."""
    g = g.sort_values("PLR_request")
    mod = g[g.capacity_clamped.isna()]
    floor = g[g.capacity_clamped == "min"]
    ax.plot(100 * mod.PLR_request, mod[col], ls=ls, lw=dm.lw(0), color=color,
            marker=marker, ms=MS, label=label)
    if len(floor):
        if marker:
            ax.plot(100 * floor.PLR_request, floor[col], ls="none", marker=marker, ms=MS,
                    mfc="white", mec=color, mew=HAIRLINE)
        else:
            ax.plot(100 * floor.PLR_request, floor[col], ls=(0, (1, 2)), lw=dm.lw(0), color=color)


def _xaxis(ax, label=True):
    ax.set_xlim(0, 100)
    ax.set_xticks(ticks(0, 100, 20))
    if label:
        ax.set_xlabel("Requested part-load ratio [%]")
    _grid(ax)


def _fig_for(duty: str, aspect_1: float = 0.60, aspect_n: float = 0.46):
    """One row of panels, one per condition of ``duty``."""
    conds = CONDS[duty]
    if len(conds) == 1:
        return plt.subplots(1, 1, figsize=dm.figsize("12cm", aspect_1), squeeze=False), conds
    return plt.subplots(1, len(conds), figsize=dm.figsize("17cm", aspect_n), squeeze=False), conds


def p5_p6_fixed_power() -> None:
    d = pd.read_csv(LP / "low_load_fixed_power_sh_sc_once.csv")
    d = d[(d.sh_sc_mode == "mode_A") & (d.SH_set == 3.0) & (d.SC_set == 3.0) & (d.failure_reason == "none")]

    for duty in CONDS:
        (fig, axes), conds = _fig_for(duty)
        axes = axes[0]
        for j, (cond, title) in enumerate(conds):
            ax = axes[j]
            for w, cc, mk in W_SERIES:
                _series(ax, d[(d.condition == cond) & (d.W_fixed == w)], "COP_sys", cc, mk,
                        label=f"$W_{{fixed}}$ = {w:g} W" if j == 0 else None)
            _xaxis(ax)
            ax.set_ylabel(r"System $\mathrm{COP}$ [-]")
            ax.set_title(title, fontsize=dm.fs(FS_TITLE), loc="left")
            if len(conds) > 1:
                panel_letter(ax, "abc"[j])
        _save_top_legend(fig, axes[0], f"P5_fixed_power_cop_{duty}")

    for duty in CONDS:
        (fig, axes), conds = _fig_for(duty)
        axes = axes[0]
        for j, (cond, title) in enumerate(conds):
            ax = axes[j]
            for w, cc, mk in W_SERIES[1:]:
                _series(ax, d[(d.condition == cond) & (d.W_fixed == w)], "fixed_power_fraction", cc, mk,
                        label=f"{w:g} W" if j == 0 else None)
            _xaxis(ax)
            ax.set_ylim(0, 0.5)
            ax.set_yticks(ticks(0, 0.5, 0.1))
            ax.set_ylabel(r"$W_{fixed}\,/\,E_{tot}$ [-]")
            ax.set_title(title, fontsize=dm.fs(FS_TITLE), loc="left")
            if len(conds) > 1:
                panel_letter(ax, "abc"[j])
        _save_top_legend(fig, axes[0], f"P6_fixed_power_fraction_{duty}")


FAN_CONDS = {"heating": ("heating_7", "ASHP · Heating 7/20 °C"),
             "cooling": ("cooling_35", "ASHP · Cooling 35/27 °C")}


def p7_fan_min_power() -> None:
    d = pd.read_csv(FAN / "fan_model_sensitivity_once.csv")
    d = d[d.failure_reason == "none"]
    series = (("baseline", "current TMHP", COLORS["ink"], "o"),
              ("pmin_05", r"$P_{min}$ = 5 %", COLORS["cool"], "s"),
              ("pmin_10", r"$P_{min}$ = 10 %", COLORS["ess"], "^"),
              ("pmin_20", r"$P_{min}$ = 20 %", COLORS["accent"], "D"),
              ("pmin_30", r"$P_{min}$ = 30 %", COLORS["warm"], "v"))
    for duty, (cond, title) in FAN_CONDS.items():
        fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.46))
        for c, (col, ylab) in enumerate((("E_fan_total", "Fan power, both fans [W]"),
                                         ("COP_sys", r"System $\mathrm{COP}$ [-]"))):
            ax = axes[c]
            for cfg, lbl, cc, mk in series:
                _series(ax, d[(d.condition == cond) & (d.config == cfg)], col, cc, mk,
                        label=lbl if c == 0 else None)
            _xaxis(ax)
            ax.set_ylabel(ylab)
            ax.set_title(title, fontsize=dm.fs(FS_TITLE), loc="left")
            panel_letter(ax, "ab"[c])
        _save_top_legend(fig, axes[0], f"P7_fan_min_power_{duty}")


# ---------------------------------------------------------------------------
# P8  the three fitted efficiencies against relative speed  (replot of F1)
# ---------------------------------------------------------------------------
def p14_fan_floor_parity() -> None:
    """Catalogue parity for the fan floor, laid out exactly like P4.

    The two figures are read side by side, so they share axes, marker roles and
    the stats block. Only points that solve under *both* floors are drawn --
    otherwise the comparison silently changes its own sample.
    """
    d = pd.read_csv(OUT / "fan_floor_parity.csv")
    key = ["slug", "model_class", "mode", "t_source_C", "t_sink_C", "q_kW", "cop_target"]
    base = d[d.p_min_frac == 0.0][key + ["cop_pred"]].rename(columns={"cop_pred": "cop_base"})
    flo = d[d.p_min_frac > 0.0][key + ["cop_pred"]].rename(columns={"cop_pred": "cop_floor"})
    m = base.merge(flo, on=key)

    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.50))
    recs = []
    for ax, mc, lo, hi, step, head in ((axes[0], "ASHP", 1, 9, 2, "ASHP · Daikin RXM-A, 5 units"),
                                       (axes[1], "ASHPB", 1, 7, 1, "ASHPB · Panasonic 9 + Samsung 1")):
        g = m[m.model_class == mc]
        _parity_axes(ax, lo, hi, step, "Catalogue COP [-]", "TMHP COP [-]")
        lines = [f"{'':>5} {'MAPE':>5} {'bias':>6} {'±10 %':>5}"]
        # Same marker roles, colours, size and alpha as P4, so the two parity
        # figures can be read against each other. The two sets here nearly
        # coincide, so the baseline circles sit under the triangles rather
        # than beside them -- that overlap is the finding.
        for col, tag, cc, mk, key2 in (("cop_base", "no fan floor", COLORS["ink"], "o", "base"),
                                       ("cop_floor", "with fan floor", COLORS["warm"], "^", "fan")):
            pred = g[col]
            mape, bias, n = _stats(pred, g.cop_target)
            within = float((np.abs((pred - g.cop_target) / g.cop_target) <= 0.10).mean() * 100)
            ax.plot(g.cop_target, pred, mk, ms=MS, alpha=0.65, mew=0, color=cc, label=tag)
            lines.append(f"{key2:>5} {mape:5.2f} {bias:+6.2f} {within:5.0f}")
            recs.append({"model_class": mc, "p_min_frac": 0.0 if key2 == "base" else 0.30,
                         "MAPE_pct": mape, "bias_pct": bias, "within_10pct": within, "n": n})
        ax.set_title(head, fontsize=dm.fs(FS_TITLE), loc="left")
        ax.text(0.04, 0.96, "\n".join(lines), transform=ax.transAxes, va="top",
                fontsize=dm.fs(FS_ANNOT), family="monospace")
        panel_letter(ax, "ab"[0 if mc == "ASHP" else 1])
    _save_top_legend(fig, axes[0], "P14_parity_fan_floor")
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(recs).to_csv(OUT / "p14_parity_fan_floor_metrics.csv", index=False)


def p13_fan_floor_map() -> None:
    """Same before/after map as P1, but the "after" is a re-solved fan floor.

    ``P_min`` = 30 % of rated is the level whose modulation-range shape matches
    the 50 W fixed-power case, and 30 % of two 70 W fans is ~42 W -- the same
    order as that 50 W, which is the point.
    """
    d = pd.read_csv(OUT / "fan_floor_plr_map.csv")
    d = d.rename(columns={"T_outdoor": "t_outdoor_C", "PLR_request": "plr_request", "COP_sys": "cop_sys"})
    cols = (("baseline", "Before — current fan model"), ("pmin_30", r"After — $P_{min}$ = 30 % of rated"))
    for duty, style in DUTIES.items():
        panels = [d[(d.duty == duty) & (d.config == cfg)] for cfg, _ in cols]
        ok = [g[g.failure_reason == "none"].cop_sys for g in panels]
        lo, hi, step = _auto_ylim(pd.concat(ok))
        fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.44))
        for c, ((_cfg, tag), g) in enumerate(zip(cols, panels, strict=True)):
            _map_panel(axes[c], g, style["cmap"], tag, (lo, hi), step,
                       ylabel=(c == 0), label=(c == 0))
            panel_letter(axes[c], "ab"[c])
        _save_top_legend(fig, axes[0], f"P13_fan_floor_plr_cop_{duty}")


def p8_efficiency(rated: float = 50.0) -> None:
    ns = np.linspace(0.25, 2.0, 141)
    prs = (2.0, 3.0, 4.5)
    styles = [(0, (1, 1.2)), "solid", (0, (4, 1.6))]
    ev, ei, ee = ce.make_eta_vol(rated), ce.make_eta_isen(rated), ce.make_eta_em(rated)
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.36),
                             gridspec_kw={"wspace": 0.42})
    labels = (r"$\eta_v$ [-]", r"$\eta_{is}$ [-]", r"$\eta_{em}$ [-]")
    fns = (lambda pr, n: ev(pr, n * rated), lambda pr, n: ei(pr, n * rated), lambda pr, n: ee(pr, n * rated))
    for k, (ax, lab, fn, letter) in enumerate(zip(axes, labels, fns, "abc", strict=True)):
        for pr, ls in zip(prs, styles, strict=True):
            ax.plot(ns, [fn(pr, n) for n in ns], color=COLORS["ink"], ls=ls, lw=dm.lw(0.5),
                    label=rf"$r_p$ = {pr:g}" if k == 0 else None)
        ax.axvspan(0.25, 0.375, color=COLORS["band20"], alpha=0.25, lw=0)
        ax.set_xlim(0.2, 2.0)
        ax.set_xticks(ticks(0.2, 2.0, 0.6))
        # One label under the middle panel: at page type size three copies
        # of it run into each other.
        if k == 1:
            ax.set_xlabel(r"Relative speed $n^*$ = $N/N_{rated}$ [-]")
        ax.set_ylabel(lab)
        _grid(ax)
        panel_letter(ax, letter)
    axes[0].set_ylim(0.6, 1.0)
    axes[1].set_ylim(0.3, 0.9)
    axes[2].set_ylim(0.6, 1.0)
    _save_top_legend(fig, axes[0], "P8_efficiency_vs_speed")


# ---------------------------------------------------------------------------
# P10  the measured P_TO proxy on the part-load curve   (supplementary)
# ---------------------------------------------------------------------------
def p10_pto_proxy() -> None:
    d = pd.read_csv(LP / "low_load_fixed_power_sh_sc_once.csv")
    d = d[(d.sh_sc_mode == "mode_A") & (d.SH_set == 3.0) & (d.SC_set == 3.0)
          & (d.failure_reason == "none") & (d.W_fixed == 0.0)]
    series = ((0.0, COLORS["ink"], "o", "current TMHP"),
              (W_MEASURED, COLORS["ess"], "s", rf"+ measured $P_{{TO}}$ ({W_MEASURED:.0f} W)"),
              (25.0, COLORS["accent"], "^", "+ 25 W"),
              (50.0, COLORS["warm"], "D", "+ 50 W"))
    for duty, (cond, title) in FAN_CONDS.items():
        g = d[d.condition == cond].sort_values("PLR_request")
        fig, ax = plt.subplots(figsize=dm.figsize("12cm", 0.58))
        for w, cc, mk, lbl in series:
            gg = g.copy()
            gg["cop_w"] = gg.Q_delivered_W / (gg.E_tot_baseline + w)
            _series(ax, gg, "cop_w", cc, mk, label=lbl)
        _xaxis(ax)
        ax.set_ylabel(r"System $\mathrm{COP}$ [-]")
        ax.set_title(title, fontsize=dm.fs(FS_TITLE), loc="left")
        _save_top_legend(fig, ax, f"P10_pto_proxy_{duty}")


# ---------------------------------------------------------------------------
# P11 / P12  fan sensitivities that change nothing   (supplementary)
#
# A variant that lands exactly on the baseline is the message, so the baseline
# keeps the markers and every variant is a dash pattern drawn on top of it --
# "no effect" then reads as dashes sitting on markers rather than as a series
# that went missing under the one drawn last.
# ---------------------------------------------------------------------------
DASHES = ("solid", (0, (5, 1.6)), (0, (2.4, 1.3)), (0, (1, 1.3)))


def p11_min_airflow() -> None:
    d = pd.read_csv(FAN / "fan_model_sensitivity_once.csv")
    d = d[d.failure_reason == "none"]
    series = (("baseline", "current TMHP", COLORS["ink"], "o"),
              ("floor_20", r"$x_{min}$ = 20 %", COLORS["cool"], ""),
              ("floor_30", r"$x_{min}$ = 30 %", COLORS["accent"], ""),
              ("floor_40", r"$x_{min}$ = 40 %", COLORS["hot"], ""))
    quants = (("COP_sys", r"System $\mathrm{COP}$ [-]"),
              ("V_air_ratio_ou", r"Outdoor $V_{air}\,/\,V_{air,rated}$ [-]"),
              ("E_fan_total", "Fan power, both fans [W]"))
    for duty, (cond, _title) in FAN_CONDS.items():
        fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.36),
                                 gridspec_kw={"wspace": 0.60})
        for j, (col, ylab) in enumerate(quants):
            ax = axes[j]
            for k, (cfg, lbl, cc, mk) in enumerate(series):
                _series(ax, d[(d.condition == cond) & (d.config == cfg)], col, cc, mk,
                        label=lbl if j == 0 else None, ls=DASHES[k])
            # Three panels of one condition: the title would repeat three
            # times and the x-label with it, so both live in the caption.
            _xaxis(ax, label=(j == 1))
            ax.set_ylabel(ylab)
            panel_letter(ax, "abc"[j], x=-0.26)
        _save_top_legend(fig, axes[0], f"P11_min_airflow_{duty}", ml="3%")


def p12_rated_fan_power() -> None:
    d = pd.read_csv(FAN / "fan_model_sensitivity_once.csv")
    d = d[d.failure_reason == "none"]
    series = (("baseline", "100 % (current TMHP)", COLORS["ink"], "o"),
              ("power_070", "70 % of rated", COLORS["cool"], ""),
              ("power_130", "130 % of rated", COLORS["hot"], ""))
    for duty, (cond, title) in FAN_CONDS.items():
        fig, ax = plt.subplots(figsize=dm.figsize("12cm", 0.58))
        for k, (cfg, lbl, cc, mk) in enumerate(series):
            _series(ax, d[(d.condition == cond) & (d.config == cfg)], "COP_sys", cc, mk,
                    label=lbl, ls=DASHES[k])
        _xaxis(ax)
        ax.set_ylabel(r"System $\mathrm{COP}$ [-]")
        ax.set_title(title, fontsize=dm.fs(FS_TITLE), loc="left")
        _save_top_legend(fig, ax, f"P12_rated_fan_power_{duty}")


FIGURES = (("P1", p1_before_after), ("P2", p2_delta_cop), ("P3", p3_peak_shift), ("P4", p4_parity),
           ("P5/P6", p5_p6_fixed_power), ("P7", p7_fan_min_power), ("P8", p8_efficiency),
           ("P9", p9_baseline_map), ("P10", p10_pto_proxy), ("P11", p11_min_airflow),
           ("P12", p12_rated_fan_power), ("P13", p13_fan_floor_map),
           ("P14", p14_fan_floor_parity))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", metavar="KEY", help="e.g. --only P13 P4")
    args = ap.parse_args()
    _style()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in FIGURES:
        if args.only and name not in args.only:
            continue
        print(name, flush=True)
        fn()
    print(f"figures -> {FIG_DIR}")


if __name__ == "__main__":
    main()
