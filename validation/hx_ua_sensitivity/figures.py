"""Figures of the heat-exchanger UA sensitivity study (UA plan v3, Sec. 3-4).

Heating and cooling are written as *separate files* (``H*`` / ``C*``), because
the report page shows them in separate sections and a two-duty panel row halves
the type size at the width Notion renders.

Style follows ``validation/refrigerant_ua_sensitivity/figures.py``: the shared
dartwork-mpl bootstrap in ``scripts/visualization/_dmpl_common``, explicit
ticks, filled markers for the continuously modulating rows and open markers for
rows at the compressor speed floor (which carry no shape verdict).

Every figure writes PNG + SVG and its own source table into
``validation/results/hx_ua_sensitivity_once/``.

Run after ``simulate``::

    uv run python3 -m validation.hx_ua_sensitivity.figures [--only H1 C1]
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

from validation.fixed_boundary_plr.shape_metrics import metrics_for  # noqa: E402
from validation.hx_ua_sensitivity.simulate import (  # noqa: E402
    CSV_NAME,
    JOINT_SCALES,
    OUT_DIR,
    SPLIT_CASES,
)

MS = 2.6

#: Warm = starved coil, black = as shipped, cool = oversized coil.
SCALE_COLOR = {
    0.25: "oc.red6",
    0.5: "oc.orange6",
    0.75: "oc.yellow7",
    1.0: COLORS["ink"],
    1.5: "oc.blue5",
    2.0: "oc.indigo7",
}
SPLIT_COLOR = {
    "joint_1": COLORS["ink"],
    "ou_0.5_iu_1": "oc.orange6",
    "ou_2_iu_1": "oc.indigo7",
    "ou_1_iu_0.5": "oc.red6",
    "ou_1_iu_2": "oc.teal6",
}
SPLIT_LABEL = {
    "joint_1": "as shipped",
    "ou_0.5_iu_1": "outdoor 0.5x",
    "ou_2_iu_1": "outdoor 2x",
    "ou_1_iu_0.5": "indoor 0.5x",
    "ou_1_iu_2": "indoor 2x",
}
DUTY_TITLE = {
    "heating": "Heating, 7 °C outdoor / 20 °C room",
    "cooling": "Cooling, 35 °C outdoor / 27 °C room",
}
DUTY_TAG = {"heating": "H", "cooling": "C"}
COIL_ROLE = {
    "heating": ("outdoor coil = evaporator", "indoor coil = condenser"),
    "cooling": ("indoor coil = evaporator", "outdoor coil = condenser"),
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _load() -> pd.DataFrame:
    d = pd.read_csv(OUT_DIR / CSV_NAME)
    return d[d["pass"] != "default"].copy()


def _save(fig, name: str, **margins) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for extra in (0, 2, 4, 6, 8):
        try:
            kw = {k: f"{float(str(v).rstrip('%')) + extra}%" for k, v in margins.items()}
            finalize(fig, OUT_DIR / name, formats=("svg", "png"), margin=f"{2 + extra}%", **kw)
            break
        except RuntimeError as exc:
            if extra == 8:
                raise exc
    plt.close(fig)


def _grid(ax) -> None:
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)


def _split_rows(g: pd.DataFrame):
    ok = g[g.failure_reason == "none"].sort_values("plr_request")
    return ok[ok.capacity_clamped.isna()], ok[ok.capacity_clamped == "min"]


def _series(ax, g, col, color, label=None, scale=1.0, ls="solid", norm=None):
    """Filled markers = continuous modulation; open markers = speed floor."""
    mod, floor = _split_rows(g)
    f = 1.0 if norm is None else norm
    ax.plot(
        100 * mod.plr_request,
        mod[col] * scale / f,
        ls=ls,
        lw=dm.lw(0),
        color=color,
        marker="o",
        ms=MS,
        label=label,
    )
    if len(floor):
        ax.plot(
            100 * floor.plr_request,
            floor[col] * scale / f,
            ls="none",
            marker="o",
            ms=MS,
            mfc="white",
            mec=color,
            mew=HAIRLINE,
        )


def _plr_axis(ax, xlabel: bool = True) -> None:
    ax.set_xlim(0, 100)
    ax.set_xticks(ticks(0, 100, 20))
    if xlabel:
        ax.set_xlabel("Requested part-load ratio [%]")
    _grid(ax)


#: Tick counts above this crowd the short panels this figure set uses.
MAX_TICKS = 7


def _ylim(values, step: float, pad: float = 0.0) -> tuple[float, float]:
    """Snap an axis to whole multiples of ``step`` around the data.

    Explicit ticks are house style, but this figure set has fourteen panels
    whose ranges move whenever the sweep is rerun; snapping to the step keeps
    the gridlines reproducible without hand-tuning each one.
    """
    v = np.asarray([x for x in np.ravel(values) if np.isfinite(x)], dtype=float)
    lo = np.floor((v.min() - pad) / step) * step
    hi = np.ceil((v.max() + pad) / step) * step
    if hi - lo < step * 1.5:
        hi = lo + step * 2
    return float(lo), float(hi)


def _fit_step(values, step: float, pad: float = 0.0) -> tuple[float, float, float]:
    """Coarsen ``step`` by 2x / 2.5x until the axis carries at most MAX_TICKS."""
    for factor in (1.0, 2.0, 4.0, 5.0, 10.0):
        s = step * factor
        lo, hi = _ylim(values, s, pad)
        if round((hi - lo) / s) + 1 <= MAX_TICKS:
            return lo, hi, s
    return lo, hi, s


def _axis(ax, values, ylabel: str, step: float, *, title=None, legend=None, ncol=1) -> None:
    lo, hi, step = _fit_step(values, step)
    ax.set_ylabel(ylabel)
    ax.set_ylim(lo, hi)
    ax.set_yticks(ticks(lo, hi, step))
    if title:
        ax.set_title(title, fontsize=dm.fs(-2), loc="left")
    if legend:
        ax.legend(loc=legend, frameon=False, fontsize=dm.fs(-3.5), ncol=ncol, handlelength=1.6, columnspacing=1.0)
    _plr_axis(ax)


def _joint(d: pd.DataFrame, duty: str) -> pd.DataFrame:
    return d[(d["pass"] == "joint") & (d.duty == duty)]


def _by_scale(d: pd.DataFrame, duty: str):
    """(scale, rows) for every joint scale that produced usable rows."""
    sub = _joint(d, duty)
    for s in JOINT_SCALES:
        g = sub[sub.scale_ou == s]
        if len(g[g.failure_reason == "none"]):
            yield s, g


def _label(s: float) -> str:
    return "as shipped (1.0x)" if s == 1.0 else f"{s:g}x"


def _rated_value(g: pd.DataFrame, col: str) -> float:
    """Value at the highest continuously modulating PLR of this variant."""
    mod, _ = _split_rows(g)
    return float(mod[col].iloc[-1]) if len(mod) else float("nan")


def _source(d: pd.DataFrame, cols: list[str], name: str) -> None:
    keep = [
        "variant",
        "scale_ou",
        "scale_iu",
        "duty",
        "plr_request",
        "capacity_clamped",
        "failure_reason",
        *cols,
    ]
    d[keep].to_csv(OUT_DIR / name, index=False)


# ---------------------------------------------------------------------------
# 1  COP vs PLR -- the sensitivity figure
# ---------------------------------------------------------------------------
def fig1(d, duty: str) -> None:
    tag = DUTY_TAG[duty]
    sub = _joint(d, duty)
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("15cm", 0.42), gridspec_kw={"wspace": 0.32})
    # The starved coils cannot reach nameplate duty, so the shape panel is
    # normalised at the highest PLR *every* case still modulates through --
    # normalising each case at its own top point would compare curves anchored
    # at different loads.
    ref_plr = min(_split_rows(g)[0].plr_request.max() for _, g in _by_scale(d, duty))
    vals, rel = [], []
    for s, g in _by_scale(d, duty):
        _series(axes[0], g, "cop_sys", SCALE_COLOR[s], label=_label(s))
        mod, _ = _split_rows(g)
        vals += list(mod.cop_sys)
        base = float(mod.loc[mod.plr_request == ref_plr, "cop_sys"].iloc[0])
        _series(axes[1], g, "cop_sys", SCALE_COLOR[s], norm=base)
        rel += list(mod.cop_sys / base)
    _axis(axes[0], vals, "System COP [-]", 1.0, title=DUTY_TITLE[duty], legend="lower right", ncol=2)
    _axis(
        axes[1],
        rel,
        f"System COP / COP at {100 * ref_plr:.0f} % PLR [-]",
        0.1,
        title=f"Shape, each case normalised at {100 * ref_plr:.0f} % PLR",
    )
    axes[1].axhline(1.0, color=COLORS["muted"], lw=HAIRLINE, ls=":", zorder=0)
    for ax, letter in zip(axes, "ab", strict=True):
        panel_letter(ax, letter)
    _source(sub, ["cop_sys", "cop_ref", "E_cmp", "E_tot"], f"{tag}1_cop_vs_plr.csv")
    _save(fig, f"{tag}1_cop_vs_plr", mt="6%")


# ---------------------------------------------------------------------------
# 2  refrigerant temperatures and approach temperatures
# ---------------------------------------------------------------------------
def fig2(d, duty: str) -> None:
    tag = DUTY_TAG[duty]
    sub = _joint(d, duty)
    evap_role, cond_role = COIL_ROLE[duty]
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("15cm", 0.42), gridspec_kw={"wspace": 0.32})
    temps, apps = [], []
    for s, g in _by_scale(d, duty):
        _series(axes[0], g, "T_cond_C", SCALE_COLOR[s], label=_label(s))
        _series(axes[0], g, "T_evap_C", SCALE_COLOR[s], ls="dashed")
        _series(axes[1], g, "approach_cond_K", SCALE_COLOR[s])
        _series(axes[1], g, "approach_evap_K", SCALE_COLOR[s], ls="dashed")
        mod, _ = _split_rows(g)
        temps += list(mod.T_cond_C) + list(mod.T_evap_C)
        apps += list(mod.approach_cond_K) + list(mod.approach_evap_K)
    _axis(axes[0], temps, "Saturation temperature [°C]", 10.0, title=DUTY_TITLE[duty], legend="center right", ncol=2)
    _axis(axes[1], apps, "Approach temperature [K]", 2.0, title=f"solid {cond_role}, dashed {evap_role}")
    axes[0].text(
        0.03,
        0.06,
        "solid: condensing\ndashed: evaporating",
        transform=axes[0].transAxes,
        fontsize=dm.fs(-3.5),
        color=COLORS["muted"],
        va="bottom",
    )
    for ax, letter in zip(axes, "ab", strict=True):
        panel_letter(ax, letter)
    _source(
        sub,
        ["T_evap_C", "T_cond_C", "approach_evap_K", "approach_cond_K", "lift_ref_K", "UA_evap", "UA_cond"],
        f"{tag}2_temperatures_vs_plr.csv",
    )
    _save(fig, f"{tag}2_temperatures_vs_plr", mt="6%")


# ---------------------------------------------------------------------------
# 3  pressure ratio and compressor speed
# ---------------------------------------------------------------------------
def fig3(d, duty: str) -> None:
    tag = DUTY_TAG[duty]
    sub = _joint(d, duty)
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("15cm", 0.42), gridspec_kw={"wspace": 0.32})
    prs, ns = [], []
    for s, g in _by_scale(d, duty):
        _series(axes[0], g, "pr", SCALE_COLOR[s], label=_label(s))
        _series(axes[1], g, "n_star", SCALE_COLOR[s])
        mod, _ = _split_rows(g)
        prs += list(mod.pr)
        ns += list(mod.n_star)
    _axis(axes[0], prs, "Pressure ratio $p_{dis}/p_{suc}$ [-]", 0.25, title=DUTY_TITLE[duty], legend="upper left", ncol=2)
    _axis(axes[1], ns, "Relative speed $n^*$ = rps / rps$_{rated}$ [-]", 0.2, title="Compressor speed")
    for ax, letter in zip(axes, "ab", strict=True):
        panel_letter(ax, letter)
    _source(sub, ["pr", "n_star", "rps", "lift_ref_K"], f"{tag}3_pressure_ratio_vs_plr.csv")
    _save(fig, f"{tag}3_pressure_ratio_vs_plr", mt="6%")


# ---------------------------------------------------------------------------
# 4  compressor efficiencies and power
# ---------------------------------------------------------------------------
def fig4(d, duty: str) -> None:
    tag = DUTY_TAG[duty]
    sub = _joint(d, duty)
    fig, axes = plt.subplots(2, 2, figsize=dm.figsize("15cm", 0.80), gridspec_kw={"wspace": 0.34, "hspace": 0.50})
    spec = [
        ("eta_isen", r"Isentropic efficiency $\eta_{is}$ [-]", 0.02),
        ("eta_vol", r"Volumetric efficiency $\eta_{v}$ [-]", 0.02),
        ("eta_em", r"Electro-mechanical efficiency $\eta_{em}$ [-]", 0.02),
        ("E_cmp", "Compressor power [W]", 100.0),
    ]
    for ax, (col, ylabel, step), letter in zip(axes.ravel(), spec, "abcd", strict=True):
        vals = []
        for s, g in _by_scale(d, duty):
            _series(ax, g, col, SCALE_COLOR[s], label=_label(s) if col == "eta_isen" else None)
            mod, _ = _split_rows(g)
            vals += list(mod[col])
        _axis(
            ax,
            vals,
            ylabel,
            step,
            title=DUTY_TITLE[duty] if col == "eta_isen" else None,
            legend="lower right" if col == "eta_isen" else None,
            ncol=2,
        )
        panel_letter(ax, letter)
    _source(sub, ["eta_isen", "eta_vol", "eta_em", "E_cmp", "pr", "n_star"], f"{tag}4_compressor_vs_plr.csv")
    _save(fig, f"{tag}4_compressor_vs_plr", mt="5%")


# ---------------------------------------------------------------------------
# 5  COP definition: compressor-only against system
# ---------------------------------------------------------------------------
DEF_SCALES = (0.5, 1.0, 2.0)


def fig5(d, duty: str) -> None:
    tag = DUTY_TAG[duty]
    sub = _joint(d, duty)
    fig, ax = plt.subplots(figsize=dm.figsize("9cm", 0.72))
    vals = []
    for s in DEF_SCALES:
        g = sub[sub.scale_ou == s]
        if not len(g[g.failure_reason == "none"]):
            continue
        _series(ax, g, "cop_ref", SCALE_COLOR[s], label=_label(s), ls="dashed")
        _series(ax, g, "cop_sys", SCALE_COLOR[s])
        mod, _ = _split_rows(g)
        vals += list(mod.cop_ref) + list(mod.cop_sys)
    _axis(ax, vals, "COP [-]", 1.0, title=DUTY_TITLE[duty], legend="lower right")
    ax.text(
        0.03,
        0.95,
        r"dashed: $COP_{cmp} = Q/W_{cmp}$" "\n" r"solid: $COP_{sys} = Q/(W_{cmp}+W_{fan})$",
        transform=ax.transAxes,
        fontsize=dm.fs(-3.5),
        color=COLORS["muted"],
        va="top",
    )
    _source(sub, ["cop_ref", "cop_sys", "E_cmp", "E_fan", "E_tot", "fan_share"], f"{tag}5_cop_definition.csv")
    _save(fig, f"{tag}5_cop_definition", mt="6%")


# ---------------------------------------------------------------------------
# 6  power split and fan share, at the shipped UA
# ---------------------------------------------------------------------------
def fig6(d, duty: str) -> None:
    tag = DUTY_TAG[duty]
    g = _joint(d, duty)
    g = g[g.scale_ou == 1.0]
    mod, floor = _split_rows(g)
    fig, ax = plt.subplots(figsize=dm.figsize("9cm", 0.72))
    x = 100 * mod.plr_request
    ax.fill_between(x, 0, mod.E_cmp, color=COLORS["accent"], alpha=0.85, lw=0, label="compressor")
    ax.fill_between(x, mod.E_cmp, mod.E_cmp + mod.E_fan, color=COLORS["warm"], alpha=0.85, lw=0, label="fans")
    _, hi, step = _fit_step(list(mod.E_cmp + mod.E_fan) + [0.0], 100.0)
    ax.set_ylim(0, hi)
    ax.set_yticks(ticks(0, hi, step))
    ax.set_ylabel("Electrical power [W]")
    ax.set_title(DUTY_TITLE[duty], fontsize=dm.fs(-2), loc="left")
    _plr_axis(ax)

    ax2 = ax.twinx()
    ax2.plot(
        x,
        100 * mod.fan_share,
        color=COLORS["ink"],
        lw=dm.lw(0),
        ls="dashed",
        marker="o",
        ms=MS,
        label="fan share (right axis)",
    )
    handles = ax.get_legend_handles_labels()
    extra = ax2.get_legend_handles_labels()
    ax.legend(
        handles[0] + extra[0],
        handles[1] + extra[1],
        loc="upper left",
        frameon=False,
        fontsize=dm.fs(-3.5),
    )
    if len(floor):
        ax2.plot(
            100 * floor.plr_request,
            100 * floor.fan_share,
            ls="none",
            marker="o",
            ms=MS,
            mfc="white",
            mec=COLORS["ink"],
            mew=HAIRLINE,
        )
    f_lo, f_hi, f_step = _fit_step(list(100 * mod.fan_share), 2.5)
    ax2.set_ylim(f_lo, f_hi)
    ax2.set_yticks(ticks(f_lo, f_hi, f_step))
    ax2.set_ylabel(r"Fan share $W_{fan}/(W_{cmp}+W_{fan})$ [%]")
    _source(g, ["E_cmp", "E_ou_fan", "E_iu_fan", "E_fan", "E_tot", "fan_share"], f"{tag}6_power_split.csv")
    _save(fig, f"{tag}6_power_split", mt="6%", mr="4%")


# ---------------------------------------------------------------------------
# 7  outdoor against indoor coil
# ---------------------------------------------------------------------------
def fig7(d, duty: str) -> None:
    tag = DUTY_TAG[duty]
    wanted = ["joint_1"] + [f"ou_{a:g}_iu_{b:g}" for a, b in SPLIT_CASES]
    sub = d[(d.duty == duty) & (d.variant.isin(wanted))]
    fig, ax = plt.subplots(figsize=dm.figsize("9cm", 0.72))
    vals = []
    for v in wanted:
        g = sub[sub.variant == v]
        if not len(g[g.failure_reason == "none"]):
            continue
        _series(ax, g, "cop_sys", SPLIT_COLOR[v], label=SPLIT_LABEL[v])
        mod, _ = _split_rows(g)
        vals += list(mod.cop_sys)
    _axis(ax, vals, "System COP [-]", 1.0, title=DUTY_TITLE[duty], legend="lower right")
    _source(sub, ["cop_sys", "approach_evap_K", "approach_cond_K", "pr"], f"{tag}7_coil_split.csv")
    _save(fig, f"{tag}7_coil_split", mt="6%")


# ---------------------------------------------------------------------------
# shape-metric table
# ---------------------------------------------------------------------------
def shape_table(d: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (duty, variant), g in d.groupby(["duty", "variant"]):
        m = metrics_for(g)
        m.update(
            duty=duty,
            variant=variant,
            scale_ou=float(g.scale_ou.iloc[0]),
            scale_iu=float(g.scale_iu.iloc[0]),
            pass_name=str(g["pass"].iloc[0]),
            n_failed=int((g.failure_reason != "none").sum()),
        )
        mod, _ = _split_rows(g)
        if len(mod):
            m["approach_evap_rated_K"] = float(mod.approach_evap_K.iloc[-1])
            m["approach_evap_low_K"] = float(mod.approach_evap_K.iloc[0])
            m["approach_cond_rated_K"] = float(mod.approach_cond_K.iloc[-1])
            m["approach_cond_low_K"] = float(mod.approach_cond_K.iloc[0])
            m["pr_rated"] = float(mod.pr.iloc[-1])
            m["pr_low"] = float(mod.pr.iloc[0])
            m["eta_isen_rated"] = float(mod.eta_isen.iloc[-1])
            m["eta_isen_low"] = float(mod.eta_isen.iloc[0])
            m["fan_share_rated"] = float(mod.fan_share.iloc[-1])
            m["fan_share_low"] = float(mod.fan_share.iloc[0])
        rows.append(m)
    t = pd.DataFrame(rows).sort_values(["duty", "pass_name", "scale_ou", "scale_iu"])
    t.to_csv(OUT_DIR / "shape_metrics.csv", index=False)
    return t


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", default=None, help="figure ids, e.g. H1 C4")
    a = ap.parse_args()
    apply_style("report", hashsalt="hx-ua-sensitivity")
    d = _load()

    builders = {1: fig1, 2: fig2, 3: fig3, 4: fig4, 5: fig5, 6: fig6, 7: fig7}
    for duty, tag in DUTY_TAG.items():
        for n, fn in builders.items():
            name = f"{tag}{n}"
            if a.only and name not in a.only:
                continue
            fn(d, duty)
            print(f"wrote {name}")

    t = shape_table(d)
    print(t[["duty", "variant", "shape", "COP_rated", "COP_peak", "PLR_peak", "COP_low_over_rated"]].to_string(index=False))
    print(f"wrote {OUT_DIR / 'shape_metrics.csv'}")


if __name__ == "__main__":
    main()
