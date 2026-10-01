"""Figures of the refrigerant-only PLR-COP sensitivity study (plan v2, Sec. 4).

One file per case rather than a three-panel row: at the width Notion renders,
three panels side by side halve the type size, and the three cases are read
one after another anyway.  Case tags are ``H`` (ASHP heating), ``C`` (ASHP
cooling) and ``B`` (ASHPB heating).

Style follows ``validation/hx_ua_sensitivity/figures.py``: the shared
dartwork-mpl bootstrap in ``scripts/visualization/_dmpl_common``, explicit
ticks, filled markers for the continuously modulating rows and open markers
for rows at the compressor speed floor (which carry no shape verdict).

Figures
-------
``{tag}1``  COP vs PLR, compressor and system            (plan Fig. 1 + 2)
``{tag}2``  normalised COP vs PLR, compressor and system (plan Fig. 3)
``{tag}3``  n* and r_p vs PLR                            (plan Fig. 4)
``{tag}4``  three efficiency heatmaps + trajectories     (plan Fig. 5)
``{tag}5``  stacked compressor + fan power, fan fraction (plan Fig. 6)
``S1``      low-load shape summary across cases          (plan Fig. 7)
``S2``      control: same machine vs machine sized per fluid
``S3``      why: isentropic-cycle COP x compressor efficiency

Every figure writes PNG + SVG and its own source table into
``validation/results/refrigerant_only_plr_cop/``.

Run after ``simulate``::

    uv run python3 -m validation.refrigerant_only_plr_cop.figures [--only H1 S1]
"""

from __future__ import annotations

import argparse

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

from validation.refrigerant_only_plr_cop.simulate import (  # noqa: E402
    CASE_LABEL,
    CASE_TAG,
    CSV_NAME,
    FIT_DOMAIN_N_STAR,
    FIT_DOMAIN_PR,
    METRICS_NAME,
    OUT_DIR,
    REFRIGERANTS,
)

MS = 2.6

#: R32 is the reference the displacement is taken from, so it is drawn in ink.
REF_COLOR = {
    "R32": COLORS["ink"],
    "R410A": "oc.blue6",
    "R290": "oc.teal7",
    "R407C": "oc.orange6",
    "R134a": "oc.grape6",
    "R22": "oc.red6",
}
CASES = ("ASHP_heating", "ASHP_cooling", "ASHPB_heating")
PASS_LABEL = {
    "fixed_disp": "same machine (R32 displacement)",
    "own_disp": "machine sized per fluid",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _load() -> tuple[pd.DataFrame, pd.DataFrame]:
    d = pd.read_csv(OUT_DIR / CSV_NAME)
    m = pd.read_csv(OUT_DIR / METRICS_NAME)
    return d, m


def _save(fig, name: str, **margins) -> None:
    """Lay out, verify, and write PNG + SVG.

    ``mb`` defaults above the uniform margin because the x-label descender
    reaches roughly 0.02 in below the axes box, and the SVG ships live text
    (``svg.fonttype="none"``): a viewer without Roboto Light substitutes a
    heavier face with a deeper descender, which clips a flush bottom edge.
    """
    layout_and_write(fig, name, **margins)
    plt.close(fig)


def layout_and_write(fig, name: str, **margins) -> None:
    """The layout half of :func:`_save`, without closing the figure.

    ``mcp_replay`` needs the figure alive after it has been laid out and
    written, so the two steps are split rather than copied.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    margins.setdefault("mb", "5%")
    for extra in (0, 2, 4, 6, 8, 12):
        try:
            kw = {k: f"{float(str(v).rstrip('%')) + extra}%" for k, v in margins.items()}
            finalize(fig, OUT_DIR / name, formats=("svg", "png"), margin=f"{2 + extra}%", **kw)
            return
        except RuntimeError as exc:
            if extra == 12:
                raise exc


def _grid(ax) -> None:
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)


def _rows(d, pass_name: str, case: str, ref: str) -> pd.DataFrame:
    g = d[(d["pass"] == pass_name) & (d.case == case) & (d.refrigerant == ref)]
    return g[g.failure_reason == "none"].sort_values("PLR_requested")


def _split(g: pd.DataFrame):
    return g[g.speed_bound.isna()], g[g.speed_bound == "min"]


def _series(ax, g, col, color, label=None, norm=None, ls="solid", scale=1.0):
    """Filled markers = continuous modulation; open markers = speed floor."""
    mod, floor = _split(g)
    f = 1.0 if norm is None else norm
    ax.plot(100 * mod.PLR_requested, mod[col] * scale / f, ls=ls, lw=dm.lw(0),
            color=color, marker="o", ms=MS, label=label)
    if len(floor):
        ax.plot(100 * floor.PLR_requested, floor[col] * scale / f, ls="none",
                marker="o", ms=MS, mfc="white", mec=color, mew=HAIRLINE)


def _plr_axis(ax, xlabel: bool = True, step: float = 20.0) -> None:
    ax.set_xlim(0, 100)
    ax.set_xticks(ticks(0, 100, step))
    if xlabel:
        ax.set_xlabel("Requested part-load ratio [%]")
    _grid(ax)


#: dartwork-mpl's validator calls an axis crowded above 4 labels per inch.
#: Seven labels clear that only on axes longer than 1.75 in, which this figure
#: set does not have once a row carries three panels.
MAX_TICKS = 6


def _span_ticks(lo: float, hi: float, max_n: int = 5):
    """At most ``max_n`` round ticks inside ``[lo, hi]``.

    For axes whose limits come from the data (the efficiency surfaces) rather
    than from :func:`_fit_step`, so matplotlib's default locator would other-
    wise put eight or ten labels on a 1.4 in panel.
    """
    candidates = sorted(m * 10.0**d for d in range(-3, 4) for m in (1.0, 2.0, 2.5, 5.0))
    for step in candidates:  # ascending: take the densest step that still fits
        first = np.ceil(lo / step) * step
        n = int(np.floor((hi - first) / step)) + 1
        if 2 <= n <= max_n:
            return np.round(first + step * np.arange(n), 10)
    return np.linspace(lo, hi, max_n)


def _ylim(values, step: float, pad: float = 0.0) -> tuple[float, float]:
    v = np.asarray([x for x in np.ravel(values) if np.isfinite(x)], dtype=float)
    lo = np.floor((v.min() - pad) / step) * step
    hi = np.ceil((v.max() + pad) / step) * step
    if hi - lo < step * 1.5:
        hi = lo + step * 2
    return float(lo), float(hi)


def _fit_step(values, step: float, pad: float = 0.0, max_ticks: int | None = None):
    limit = MAX_TICKS if max_ticks is None else max_ticks
    for factor in (1.0, 2.0, 2.5, 4.0, 5.0, 10.0, 20.0):
        s = step * factor
        lo, hi = _ylim(values, s, pad)
        if round((hi - lo) / s) + 1 <= limit:
            return lo, hi, s
    return lo, hi, s


def _axis(ax, values, ylabel: str, step: float, *, title=None, legend=None, ncol=1, xlabel=True,
          max_ticks: int | None = None) -> None:
    lo, hi, step = _fit_step(values, step, max_ticks=max_ticks)
    ax.set_ylabel(ylabel)
    ax.set_ylim(lo, hi)
    ax.set_yticks(ticks(lo, hi, step))
    if title:
        ax.set_title(title, fontsize=dm.fs(-2), loc="left")
    if legend:
        ax.legend(loc=legend, frameon=False, fontsize=dm.fs(-3.5), ncol=ncol,
                  handlelength=1.6, columnspacing=1.0)
    _plr_axis(ax, xlabel)


def _source(d: pd.DataFrame, cols: list[str], name: str) -> None:
    keep = ["pass", "case", "refrigerant", "PLR_requested", "speed_bound"] + cols
    d[[c for c in keep if c in d.columns]].to_csv(OUT_DIR / name, index=False)


def _refs_present(d, pass_name: str, case: str) -> list[str]:
    return [r for r in REFRIGERANTS if len(_rows(d, pass_name, case, r))]


# ---------------------------------------------------------------------------
# {tag}1  COP vs PLR -- compressor and system            (plan Fig. 1 + 2)
# ---------------------------------------------------------------------------
def fig1(d, case: str) -> None:
    tag = CASE_TAG[case]
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("15cm", 0.42), gridspec_kw={"wspace": 0.30})
    comp, sysv = [], []
    for ref in _refs_present(d, "fixed_disp", case):
        g = _rows(d, "fixed_disp", case, ref)
        _series(axes[0], g, "COP_comp", REF_COLOR[ref], label=ref)
        _series(axes[1], g, "COP_sys", REF_COLOR[ref])
        mod, _ = _split(g)
        comp += list(mod.COP_comp)
        sysv += list(mod.COP_sys)
    _axis(axes[0], comp, "Compressor COP  $Q/E_{comp}$ [-]", 0.5,
          title=CASE_LABEL[case], legend="lower left", ncol=2)
    _axis(axes[1], sysv, "System COP  $Q/(E_{comp}+E_{fan})$ [-]", 0.5,
          title="Same rows, fan power included")
    for ax, letter in zip(axes, "ab", strict=True):
        panel_letter(ax, letter)
    _source(d[(d["pass"] == "fixed_disp") & (d.case == case)],
            ["COP_comp", "COP_sys", "W_comp", "W_fan", "W_tot"], f"{tag}1_cop_vs_plr.csv")
    _save(fig, f"{tag}1_cop_vs_plr", mt="6%")


# ---------------------------------------------------------------------------
# {tag}2  normalised COP vs PLR                          (plan Fig. 3)
# ---------------------------------------------------------------------------
def fig2(d, case: str) -> None:
    tag = CASE_TAG[case]
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("15cm", 0.42), gridspec_kw={"wspace": 0.30})
    vals = [[], []]
    for ref in _refs_present(d, "fixed_disp", case):
        g = _rows(d, "fixed_disp", case, ref)
        mod, _ = _split(g)
        if mod.empty:
            continue
        for i, col in enumerate(("COP_comp", "COP_sys")):
            base = float(mod.loc[mod.PLR_requested.idxmax(), col])
            _series(axes[i], g, col, REF_COLOR[ref], label=ref if i == 0 else None, norm=base)
            vals[i] += list(mod[col] / base)
    _axis(axes[0], vals[0], "$COP_{comp}^{*} = COP_{comp}(PLR)/COP_{comp}(1)$", 0.1,
          title=CASE_LABEL[case], legend="lower left", ncol=2)
    _axis(axes[1], vals[1], "$COP_{sys}^{*} = COP_{sys}(PLR)/COP_{sys}(1)$", 0.1,
          title="Same normalisation, fan power included")
    for ax, letter in zip(axes, "ab", strict=True):
        ax.axhline(1.0, color=COLORS["muted"], lw=HAIRLINE, ls=":", zorder=0)
        panel_letter(ax, letter)
    _save(fig, f"{tag}2_normalised_cop", mt="6%")


# ---------------------------------------------------------------------------
# {tag}3  operating point vs PLR                         (plan Fig. 4)
# ---------------------------------------------------------------------------
def fig3(d, case: str) -> None:
    tag = CASE_TAG[case]
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("15cm", 0.42), gridspec_kw={"wspace": 0.30})
    n, p = [], []
    for ref in _refs_present(d, "fixed_disp", case):
        g = _rows(d, "fixed_disp", case, ref)
        _series(axes[0], g, "n_star", REF_COLOR[ref], label=ref)
        _series(axes[1], g, "r_p", REF_COLOR[ref])
        mod, _ = _split(g)
        n += list(mod.n_star)
        p += list(mod.r_p)
    _axis(axes[0], n, "Relative speed  $n^{*}=N/N_{rated}$ [-]", 0.25,
          title=CASE_LABEL[case], legend="upper left", ncol=2)
    _axis(axes[1], p, "Pressure ratio  $r_p=p_{dis}/p_{suc}$ [-]", 0.25,
          title="Same rows, compressor lift")
    axes[0].axhline(1.0, color=COLORS["muted"], lw=HAIRLINE, ls=":", zorder=0)
    for ax, letter in zip(axes, "ab", strict=True):
        panel_letter(ax, letter)
    _source(d[(d["pass"] == "fixed_disp") & (d.case == case)],
            ["n_star", "N", "r_p", "p_suc", "p_dis", "T_evap_sat", "T_cond_sat", "m_ref"],
            f"{tag}3_operating_point.csv")
    _save(fig, f"{tag}3_operating_point", mt="6%")


# ---------------------------------------------------------------------------
# {tag}4  efficiency surfaces + trajectories             (plan Fig. 5)
# ---------------------------------------------------------------------------
def _surfaces(n_grid, p_grid):
    """The three shipped efficiency laws on an (n*, r_p) grid.

    They are written in terms of ``n*`` and the pressure ratio only, so the
    background is the same for every case and every refrigerant -- the whole
    point of the figure is that only the trajectory moves.
    """
    from tmhp.compressor_efficiency import RPS_REF, make_eta_em, make_eta_isen, make_eta_vol

    ev, ei, ee = make_eta_vol(RPS_REF), make_eta_isen(RPS_REF), make_eta_em(RPS_REF)
    N, P = np.meshgrid(n_grid, p_grid)
    out = {}
    for name, fn in (("eta_v", ev), ("eta_is", ei), ("eta_em", ee)):
        out[name] = np.vectorize(lambda p, n, f=fn: f(p, n * RPS_REF))(P, N)
    return N, P, out


ETA_TITLE = {
    "eta_v": "Volumetric  $\\eta_v$",
    "eta_is": "Isentropic  $\\eta_{is}$",
    "eta_em": "Electro-mechanical  $\\eta_{em}$",
}
PLR_MARKS = (1.00, 0.76, 0.52, 0.28)


def fig4(d, case: str) -> None:
    tag = CASE_TAG[case]
    sub = d[(d["pass"] == "fixed_disp") & (d.case == case) & (d.failure_reason == "none")]
    n_lo = max(0.0, float(sub.n_star.min()) - 0.15)
    n_hi = float(sub.n_star.max()) + 0.15
    p_lo = max(1.0, float(sub.r_p.min()) - 0.2)
    p_hi = float(sub.r_p.max()) + 0.2
    n_grid = np.linspace(n_lo, n_hi, 160)
    p_grid = np.linspace(p_lo, p_hi, 160)
    N, P, surf = _surfaces(n_grid, p_grid)

    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("16cm", 0.34), gridspec_kw={"wspace": 0.34})
    for ax, key in zip(axes, ("eta_v", "eta_is", "eta_em"), strict=True):
        z = surf[key]
        cf = ax.contourf(N, P, z, levels=14, cmap="viridis", alpha=0.85)
        cb = fig.colorbar(cf, ax=ax, pad=0.03, fraction=0.05)
        cb.set_ticks(_span_ticks(float(z.min()), float(z.max()), 5))
        cb.ax.tick_params(labelsize=dm.fs(-4))
        cb.outline.set_linewidth(HAIRLINE)
        # outside the compressor-map fitting domain the laws extrapolate
        for lo, hi, vertical in ((n_lo, FIT_DOMAIN_N_STAR[0], True), (FIT_DOMAIN_N_STAR[1], n_hi, True),
                                 (p_lo, FIT_DOMAIN_PR[0], False), (FIT_DOMAIN_PR[1], p_hi, False)):
            if hi <= lo:
                continue
            if vertical:
                ax.axvspan(lo, hi, facecolor="white", alpha=0.45, hatch="////",
                           edgecolor=COLORS["muted"], linewidth=0.0, zorder=2)
            else:
                ax.axhspan(lo, hi, facecolor="white", alpha=0.45, hatch="////",
                           edgecolor=COLORS["muted"], linewidth=0.0, zorder=2)
        for ref in _refs_present(d, "fixed_disp", case):
            g = _rows(d, "fixed_disp", case, ref)
            mod, floor = _split(g)
            ax.plot(mod.n_star, mod.r_p, lw=dm.lw(0), color=REF_COLOR[ref], zorder=4,
                    label=ref if key == "eta_v" else None)
            if len(floor):
                ax.plot(floor.n_star, floor.r_p, lw=dm.lw(0), ls=":", color=REF_COLOR[ref], zorder=4)
            marks = mod[mod.PLR_requested.isin(PLR_MARKS)]
            ax.plot(marks.n_star, marks.r_p, ls="none", marker="o", ms=MS,
                    mfc="white", mec=REF_COLOR[ref], mew=HAIRLINE, zorder=5)
        ax.set_xlim(n_lo, n_hi)
        ax.set_ylim(p_lo, p_hi)
        # Limits come from the sweep, so the default locator is free to put
        # eight labels on a 1.4 in panel; pin the count instead.
        ax.set_xticks(_span_ticks(n_lo, n_hi, 5))
        ax.set_yticks(_span_ticks(p_lo, p_hi, 6))
        ax.set_xlabel("$n^{*}$ [-]")
        # The panel name sits inside the axes: a set_title here would collide
        # with the figure-level caption at this aspect ratio.
        ax.text(0.97, 0.96, ETA_TITLE[key], transform=ax.transAxes, ha="right", va="top",
                fontsize=dm.fs(-3), zorder=6,
                bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none", "pad": 1.5})
        ax.tick_params(labelsize=dm.fs(-3.5))
    axes[0].set_ylabel("$r_p$ [-]")
    axes[0].legend(loc="lower right", frameon=False, fontsize=dm.fs(-4), ncol=2,
                   handlelength=1.2, columnspacing=0.8, labelspacing=0.25)
    fig.suptitle(f"{CASE_LABEL[case]} — hatched: outside the compressor-map fitting domain",
                 fontsize=dm.fs(-2), x=0.01, ha="left")
    _save(fig, f"{tag}4_efficiency_surface", mt="8%")


# ---------------------------------------------------------------------------
# {tag}5  power split                                    (plan Fig. 6)
# ---------------------------------------------------------------------------
def fig5(d, case: str) -> None:
    tag = CASE_TAG[case]
    refs = _refs_present(d, "fixed_disp", case)
    fig, axes = plt.subplots(2, 3, figsize=dm.figsize("16cm", 0.62),
                             gridspec_kw={"wspace": 0.62, "hspace": 0.62})
    flat = axes.ravel()
    pmax = max(float(_rows(d, "fixed_disp", case, r).W_tot.max()) for r in refs)
    lo, hi, step = _fit_step([0.0, pmax], 100.0, max_ticks=4)
    for ax, ref in zip(flat, refs, strict=False):
        g = _rows(d, "fixed_disp", case, ref).sort_values("PLR_requested")
        x = 100 * g.PLR_requested
        ax.fill_between(x, 0, g.W_comp, color=REF_COLOR[ref], alpha=0.55, lw=0, label="$E_{comp}$")
        ax.fill_between(x, g.W_comp, g.W_tot, color=COLORS["muted"], alpha=0.75, lw=0, label="$E_{fan}$")
        mod, floor = _split(g)
        if len(floor):
            ax.axvspan(0, 100 * float(floor.PLR_requested.max()), facecolor=COLORS["band20"],
                       alpha=0.35, lw=0, zorder=0)
        ax.set_ylim(lo, hi)
        ax.set_yticks(ticks(lo, hi, step))
        ax.set_title(ref, fontsize=dm.fs(-2.5), loc="left")
        _plr_axis(ax, xlabel=False, step=25.0)
        ax.tick_params(labelsize=dm.fs(-3.5))
        tw = ax.twinx()
        tw.plot(x, 100 * g.fan_fraction, color=COLORS["hot"], lw=dm.lw(-0.5), ls="dashed",
                label="$E_{fan}/E_{tot}$")
        tw.set_ylim(0, 30)
        tw.set_yticks(ticks(0, 30, 10))
        tw.tick_params(labelsize=dm.fs(-3.5), colors=COLORS["hot"])
        if ax in (flat[2], flat[5]):
            tw.set_ylabel("$E_{fan}/E_{tot}$ [%]", fontsize=dm.fs(-3), color=COLORS["hot"])
    for ax in flat[len(refs):]:
        ax.set_visible(False)
    for ax in (flat[3], flat[4], flat[5]):
        if ax.get_visible():
            ax.set_xlabel("Requested PLR [%]", fontsize=dm.fs(-3))
    flat[0].set_ylabel("Power [W]", fontsize=dm.fs(-3))
    flat[3].set_ylabel("Power [W]", fontsize=dm.fs(-3))
    h, lab = flat[0].get_legend_handles_labels()
    flat[0].legend(h, lab, loc="lower right", frameon=False, fontsize=dm.fs(-4),
                   handlelength=1.2, labelspacing=0.25)
    fig.suptitle(f"{CASE_LABEL[case]} — shaded band: at the compressor speed floor",
                 fontsize=dm.fs(-2), x=0.01, ha="left")
    _source(d[(d["pass"] == "fixed_disp") & (d.case == case)],
            ["W_comp", "W_fan", "W_ou_fan", "W_iu_fan", "W_tot", "fan_fraction"],
            f"{tag}5_power_split.csv")
    _save(fig, f"{tag}5_power_split", mt="8%")


# ---------------------------------------------------------------------------
# S1  low-load shape summary                             (plan Fig. 7)
# ---------------------------------------------------------------------------
def figS1(d, m) -> None:
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("16cm", 0.34), gridspec_kw={"wspace": 0.28})
    sub = m[m["pass"] == "fixed_disp"]
    width = 0.38
    for ax, case in zip(axes, CASES, strict=True):
        g = sub[sub.case == case].set_index("refrigerant").reindex(REFRIGERANTS).dropna(how="all")
        idx = np.arange(len(g))
        ax.bar(idx - width / 2, g.R_common_comp, width, color=COLORS["accent"], label="$R_{comp}$")
        ax.bar(idx + width / 2, g.R_common_sys, width, color=COLORS["warm"], label="$R_{sys}$")
        ax.axhline(1.0, color=COLORS["ink"], lw=HAIRLINE)
        ax.set_xticks(idx)
        ax.set_xticklabels(g.index, rotation=45, ha="right", fontsize=dm.fs(-4))
        lo, hi, step = _fit_step(list(g.R_common_comp) + list(g.R_common_sys) + [1.0], 0.05)
        hi += step  # headroom for the in-axes caption -- a set_title here
        ax.set_ylim(lo, hi)  # would collide with the figure-level caption
        ax.set_yticks(ticks(lo, hi - step, step))  # no tick in the headroom
        ax.tick_params(axis="y", labelsize=dm.fs(-3.5))
        ax.grid(True, axis="y", alpha=0.25, linewidth=GRIDLINE)
        plr = 100 * float(g.PLR_common.iloc[0])
        ax.text(0.03, 0.96, f"{CASE_TAG[case]}  {case.replace('_', ' ')}\nread at {plr:.0f} % PLR",
                transform=ax.transAxes, ha="left", va="top", fontsize=dm.fs(-3.5), linespacing=1.4)
    axes[0].set_ylabel("COP(low PLR) / COP(rated) [-]")
    axes[0].legend(loc="upper right", frameon=False, fontsize=dm.fs(-4), handlelength=1.2)
    fig.suptitle("Same machine (R32 displacement), every fluid read at the deepest PLR they all modulate at",
                 fontsize=dm.fs(-2), x=0.01, ha="left")
    sub.to_csv(OUT_DIR / "S1_shape_summary.csv", index=False)
    _save(fig, "S1_shape_summary", mt="8%")


# ---------------------------------------------------------------------------
# S2  control: same machine vs machine sized per fluid
# ---------------------------------------------------------------------------
def figS2(d, m) -> None:
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("16cm", 0.36), gridspec_kw={"wspace": 0.30})
    width = 0.38
    for ax, case in zip(axes, CASES, strict=True):
        a = m[(m["pass"] == "fixed_disp") & (m.case == case)].set_index("refrigerant")
        b = m[(m["pass"] == "own_disp") & (m.case == case)].set_index("refrigerant")
        refs = [r for r in REFRIGERANTS if r in a.index]
        idx = np.arange(len(refs))
        va = a.reindex(refs).R_common_comp
        vb = b.reindex(refs).R_common_comp
        ax.bar(idx - width / 2, va, width, color=COLORS["accent"], label=PASS_LABEL["fixed_disp"])
        ax.bar(idx + width / 2, vb, width, color=COLORS["ess"], label=PASS_LABEL["own_disp"])
        ax.axhline(1.0, color=COLORS["ink"], lw=HAIRLINE)
        ax.set_xticks(idx)
        ax.set_xticklabels(refs, rotation=45, ha="right", fontsize=dm.fs(-4))
        vals = [v for v in list(va) + list(vb) + [1.0] if np.isfinite(v)]
        lo, hi, step = _fit_step(vals, 0.05)
        hi += 1.6 * step  # headroom for the in-axes caption and the legend
        ax.set_ylim(lo, hi)
        ax.set_yticks(ticks(lo, hi - 0.6 * step, step))
        ax.tick_params(axis="y", labelsize=dm.fs(-3.5))
        ax.grid(True, axis="y", alpha=0.25, linewidth=GRIDLINE)
        keep = [r for r in refs if r != "R407C"]
        sa = float(np.nanmax(va[keep]) - np.nanmin(va[keep]))
        sb = float(np.nanmax(vb[keep]) - np.nanmin(vb[keep]))
        ra = float(np.corrcoef(a.reindex(keep).n_star_rated, va[keep])[0, 1])
        ax.text(0.03, 0.97,
                f"{CASE_TAG[case]}  {case.replace('_', ' ')}\n"
                f"spread without R407C  {sa:.3f} $\\to$ {sb:.3f},  $r(n^{{*}}_{{rated}})$ = {ra:+.2f}",
                transform=ax.transAxes, ha="left", va="top", fontsize=dm.fs(-3.5), linespacing=1.4)
    axes[0].set_ylabel("$R_{comp}$ at common low PLR [-]")
    axes[0].legend(loc="lower right", frameon=False, fontsize=dm.fs(-4.5), handlelength=1.2,
                   labelspacing=0.25)
    fig.suptitle("Control: with R32's displacement the fluids separate along rated $n^{*}$;  size each machine and they merge",
                 fontsize=dm.fs(-2), x=0.01, ha="left")
    _save(fig, "S2_sizing_control", mt="8%")


# ---------------------------------------------------------------------------
# S3  why: isentropic-cycle COP x compressor efficiency
# ---------------------------------------------------------------------------
def figS3(d) -> None:
    """``COP_comp = COP_cycle * eta_is * eta_em`` -- the two factors, normalised.

    ``COP_cycle`` is the compressor COP with both efficiencies divided out, so
    it carries the cycle state (how much lift the machine has to climb) and
    the efficiency product carries the position on the speed surface.
    """
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("16cm", 0.36), gridspec_kw={"wspace": 0.32})
    for ax, case in zip(axes, CASES, strict=True):
        vals = []
        for ref in _refs_present(d, "own_disp", case):
            g = _rows(d, "own_disp", case, ref).copy()
            g["eta_oi"] = g.eta_is * g.eta_em
            g["COP_cycle"] = g.COP_comp / g.eta_oi
            mod, _ = _split(g)
            if mod.empty:
                continue
            i = mod.PLR_requested.idxmax()
            _series(ax, g, "COP_cycle", REF_COLOR[ref], norm=float(mod.loc[i, "COP_cycle"]),
                    label=ref if case == CASES[0] else None)
            _series(ax, g, "eta_oi", REF_COLOR[ref], norm=float(mod.loc[i, "eta_oi"]), ls="dashed")
            vals += list(mod.COP_cycle / mod.loc[i, "COP_cycle"]) + list(mod.eta_oi / mod.loc[i, "eta_oi"])
        lo, hi, step = _fit_step(vals, 0.05)
        ax.set_ylim(lo, hi)
        ax.set_yticks(ticks(lo, hi, step))
        ax.axhline(1.0, color=COLORS["muted"], lw=HAIRLINE, ls=":", zorder=0)
        ax.set_title(f"{CASE_TAG[case]}  {case.replace('_', ' ')}", fontsize=dm.fs(-3), loc="left")
        ax.tick_params(axis="y", labelsize=dm.fs(-3.5))
        _plr_axis(ax)
    axes[0].set_ylabel("Value / value at rated [-]")
    axes[0].legend(loc="lower left", frameon=False, fontsize=dm.fs(-4), ncol=2,
                   handlelength=1.2, columnspacing=0.8, labelspacing=0.25)
    fig.suptitle("Machine sized per fluid — solid: isentropic-cycle COP $Q/(\\dot m\\,\\Delta h_{is})$;  "
                 "dashed: $\\eta_{is}\\eta_{em}$",
                 fontsize=dm.fs(-2), x=0.01, ha="left")
    _save(fig, "S3_decomposition", mt="10%")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()
    apply_style(hashsalt="refrigerant-only-plr-cop")
    d, m = _load()

    jobs = {}
    for case in CASES:
        tag = CASE_TAG[case]
        jobs[f"{tag}1"] = lambda c=case: fig1(d, c)
        jobs[f"{tag}2"] = lambda c=case: fig2(d, c)
        jobs[f"{tag}3"] = lambda c=case: fig3(d, c)
        jobs[f"{tag}4"] = lambda c=case: fig4(d, c)
        jobs[f"{tag}5"] = lambda c=case: fig5(d, c)
    jobs["S1"] = lambda: figS1(d, m)
    jobs["S2"] = lambda: figS2(d, m)
    jobs["S3"] = lambda: figS3(d)

    want = a.only or list(jobs)
    for name in want:
        if name not in jobs:
            raise SystemExit(f"unknown figure {name}; have {sorted(jobs)}")
        jobs[name]()
        print(f"  {name}")
    print(f"wrote {len(want)} figures to {OUT_DIR}")


if __name__ == "__main__":
    main()
