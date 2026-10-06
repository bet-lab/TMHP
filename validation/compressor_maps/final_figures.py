"""Figures of the final validation report (coefficients v2026-09-24).

F1  the three fitted efficiencies against relative speed at representative r_p
F2  compressor displacement: TMHP default rule against published values
F3  displacement sensitivity: lowest deliverable PLR, and PLR against speed
F4  catalogue COP parity, air-to-air / air-to-water
F5  air-to-air PLR-COP map by outdoor temperature (heating, cooling)
F6  air-to-water PLR-COP map by outdoor temperature
F7  minimum-speed diagnostic: requested vs delivered, speed, capacity, power,
    saturation temperatures
F8  speed-floor heat-exchanger closure of the air-to-water model, before/after
F9  air-to-air low-load COP decomposition (heat exchangers vs compressor)
A1  appendix: speed-transfer check of the final functions on the compressor data

Every figure writes its source table to ``validation/data/final_report/`` and
SVG + PNG to ``validation/coefficients/<version>/figures/final/``.

Run after ``final_simulate``::

    uv run python3 -m validation.compressor_maps.final_figures [--only F1 F4 ...]
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

from tmhp import compressor_efficiency as ce  # noqa: E402
from tmhp.compressor_speed import (  # noqa: E402
    RATED_POINT_AIR_TO_AIR,
    RATED_POINT_AIR_TO_WATER,
    RatedPoint,
    default_displacement,
)
from validation.compressor_maps.schema import DATA_DIR, REPO_ROOT  # noqa: E402
from validation.fixed_boundary_plr.shape_metrics import metrics_for  # noqa: E402
from validation.parity.spec import load_all  # noqa: E402

SIM = REPO_ROOT / "validation" / "data" / "final_report"
RESULTS = REPO_ROOT / "validation" / "results"
PLR_DIR = REPO_ROOT / "validation" / "data" / "fixed_boundary_plr"
FIG_DIR = REPO_ROOT / "validation" / "coefficients" / ce.COEFFICIENT_VERSION / "figures" / "final"

REF_COLORS = {"R32": COLORS["accent"], "R410A": COLORS["warm"], "R290": COLORS["ess"], "R407C": COLORS["accent3"]}
MS = 2.6


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _save(fig, name: str, **margins) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    for extra in (0, 2, 4, 6, 8):
        try:
            kw = {k: f"{float(v.rstrip('%')) + extra}%" for k, v in margins.items()}
            finalize(fig, FIG_DIR / name, formats=("svg", "png"), margin=f"{2 + extra}%", **kw)
            break
        except RuntimeError as exc:  # overflow guard: widen and retry
            if extra == 8:
                raise exc
    plt.close(fig)


def _grid(ax) -> None:
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)


def _pct(x):
    return 100.0 * x


def _mod_floor(g: pd.DataFrame):
    ok = g[g.failure_reason == "none"].sort_values("plr_request")
    return ok[ok.capacity_clamped.isna()], ok[ok.capacity_clamped == "min"], ok[ok.capacity_clamped == "max"]


def _plr_curve(ax, g, color, label=None, ls="solid", lw=None, ms=MS):
    mod, floor, ceil = _mod_floor(g)
    lw = dm.lw(0) if lw is None else lw
    ax.plot(_pct(mod.plr_request), mod.cop_sys, ls=ls, lw=lw, color=color, marker="o", ms=ms, label=label)
    if len(floor):
        ax.plot(_pct(floor.plr_request), floor.cop_sys, ls="none", marker="o", ms=ms, mfc="white", mec=color, mew=HAIRLINE)
    if len(ceil):
        ax.plot(_pct(ceil.plr_request), ceil.cop_sys, ls="none", marker="x", ms=ms + 0.6, color=color, mew=HAIRLINE)


def _parity_axes(ax, lo, hi, step, xlabel, ylabel, band=0.10):
    x = np.array([lo, hi])
    ax.fill_between(x, x * (1 - band), x * (1 + band), color=COLORS["band10"], alpha=0.35, lw=0, label=f"±{band:.0%}")
    ax.plot(x, x, color=COLORS["muted"], lw=HAIRLINE, label="1:1")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xticks(ticks(lo, hi, step))
    ax.set_yticks(ticks(lo, hi, step))
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_aspect("equal")
    _grid(ax)


def _stats(pred, obs) -> tuple[float, float, int]:
    e = (np.asarray(pred) - np.asarray(obs)) / np.asarray(obs) * 100.0
    return float(np.mean(np.abs(e))), float(np.mean(e)), int(len(e))


# ---------------------------------------------------------------------------
# F1  efficiency against relative speed
# ---------------------------------------------------------------------------
def f1_efficiency(rated: float = 50.0) -> None:
    ns = np.linspace(0.25, 2.0, 141)
    prs = (2.0, 3.0, 4.5)
    styles = [(0, (1, 1.2)), "solid", (0, (4, 1.6))]
    ev, ei, ee = ce.make_eta_vol(rated), ce.make_eta_isen(rated), ce.make_eta_em(rated)
    rows = []
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.36))
    labels = (r"$\eta_v$ [-]", r"$\eta_{is}$ [-]", r"$\eta_{em}$ [-]")
    fns = (lambda pr, n: ev(pr, n * rated), lambda pr, n: ei(pr, n * rated), lambda pr, n: ee(pr, n * rated))
    for ax, lab, fn, letter in zip(axes, labels, fns, "abc", strict=True):
        for pr, ls in zip(prs, styles, strict=True):
            y = [fn(pr, n) for n in ns]
            ax.plot(ns, y, color=COLORS["ink"], ls=ls, lw=dm.lw(0.5), label=rf"$r_p$ = {pr:g}")
            rows += [{"efficiency": lab, "r_p": pr, "n_star": n, "value": v} for n, v in zip(ns, y, strict=True)]
        ax.axvspan(0.25, 0.375, color=COLORS["band20"], alpha=0.25, lw=0)
        ax.set_xlim(0.2, 2.0)
        ax.set_xticks(ticks(0.2, 2.0, 0.3))
        ax.set_xlabel(r"Relative speed $n^*$ = $N/N_{rated}$ [-]")
        ax.set_ylabel(lab)
        _grid(ax)
        panel_letter(ax, letter)
    axes[0].set_ylim(0.6, 1.0)
    axes[1].set_ylim(0.3, 0.9)
    axes[2].set_ylim(0.6, 1.0)
    axes[1].legend(loc="lower right", frameon=False, fontsize=dm.fs(-2.5))
    pd.DataFrame(rows).to_csv(SIM / "fig1_efficiency_curves.csv", index=False)
    _save(fig, "F1_efficiency_vs_speed", mt="4%")


# ---------------------------------------------------------------------------
# F2  displacement: TMHP rule against published values
# ---------------------------------------------------------------------------
def _hp_level() -> pd.DataFrame:
    rows = []
    for c in load_all():
        disp = c.published_inputs.get("displacement_cc")
        if not disp:
            continue
        rp = RATED_POINT_AIR_TO_WATER if c.model_class == "ASHPB" else RATED_POINT_AIR_TO_AIR
        est = default_displacement(c.nominal_capacity_kW * 1000.0, c.refrigerant, rp) * 1e6
        rows.append(
            {
                "level": "heat_pump",
                "unit": c.name,
                "model_class": c.model_class,
                "refrigerant": c.refrigerant,
                "nominal_kW": c.nominal_capacity_kW,
                "rated_rps_assumed": rp.rps,
                "V_published_cm3": float(disp),
                "V_tmhp_cm3": est,
                "rel_err_pct": (est / float(disp) - 1.0) * 100.0,
            }
        )
    return pd.DataFrame(rows)


def _compressor_level() -> pd.DataFrame:
    d = pd.read_csv(DATA_DIR / "points_fit_ready.csv")
    d = d[(d.n_star == 1.0) & (d.point_ok) & (~d.vdisp_suspect) & d.Q_evap_W.notna() & d.T_evap_C.notna()]
    rows = []
    for key, g in d.groupby("compressor_key"):
        g = g.assign(dist=(g.T_evap_C - 7.2).abs() + (g.T_cond_C - 54.4).abs()).sort_values("dist")
        r = g.iloc[0]
        rp = RatedPoint(float(r.T_evap_C), float(r.T_cond_C), float(r.N_rated_rps), "cooling", r.source_id)
        est = (
            default_displacement(
                float(r.Q_evap_W),
                r.fluid,
                rp,
                dT_superheat=float(r.dT_sh_K) if np.isfinite(r.dT_sh_K) else 5.0,
                dT_subcool=float(r.dT_sc_K) if np.isfinite(r.dT_sc_K) else 5.0,
            )
            * 1e6
        )
        rows.append(
            {
                "level": "compressor",
                "unit": key,
                "model_class": r.source_id,
                "refrigerant": r.refrigerant,
                "nominal_kW": float(r.Q_evap_W) / 1000.0,
                "rated_rps_assumed": float(r.N_rated_rps),
                "T_evap_C": float(r.T_evap_C),
                "T_cond_C": float(r.T_cond_C),
                "V_published_cm3": float(r.V_disp_cm3),
                "V_tmhp_cm3": est,
                "rel_err_pct": (est / float(r.V_disp_cm3) - 1.0) * 100.0,
            }
        )
    return pd.DataFrame(rows)


def f2_displacement() -> None:
    hp = _hp_level()
    cmp_ = _compressor_level()
    pd.concat([hp, cmp_], ignore_index=True).to_csv(SIM / "fig2_displacement_parity.csv", index=False)
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.46))
    ax = axes[0]
    _parity_axes(ax, 0, 100, 20, "Published displacement [cm³/rev]", "TMHP default displacement [cm³/rev]")
    ax.fill_between([0, 100], [0, 80], [0, 120], color=COLORS["band20"], alpha=0.25, lw=0, label="±20 %")
    for ref, g in hp.groupby("refrigerant"):
        ax.plot(g.V_published_cm3, g.V_tmhp_cm3, "o", ms=3.4, color=REF_COLORS.get(ref, COLORS["ink"]), label=f"{ref} (air-to-water)")
    mape, bias, n = _stats(hp.V_tmhp_cm3, hp.V_published_cm3)
    ax.text(0.03, 0.97, f"n = {n} units\nMAPE {mape:.1f} %\nbias {bias:+.1f} %", transform=ax.transAxes, va="top", fontsize=dm.fs(-2.5))
    ax.text(0.97, 0.05, "air-to-air: no published\ndisplacement in the set", transform=ax.transAxes, va="bottom", ha="right", fontsize=dm.fs(-3), color=COLORS["muted"])
    ax.legend(loc="lower right", bbox_to_anchor=(1.0, 0.2), frameon=False, fontsize=dm.fs(-3))
    ax.set_title("Heat-pump level: nameplate and default rated speed", fontsize=dm.fs(-1.5), loc="left")
    panel_letter(ax, "a")

    ax = axes[1]
    lo, hi = 0, 160
    _parity_axes(ax, lo, hi, 40, "Published displacement [cm³/rev]", "Rule at the compressor's own rated point [cm³/rev]")
    ax.fill_between([lo, hi], [lo, hi * 0.8], [lo, hi * 1.2], color=COLORS["band20"], alpha=0.25, lw=0)
    mk = {"copeland_opi": ("o", "Copeland scroll (AHRI 540 rating row)"), "highly_catalogue_2024": ("s", "Highly rotary (catalogue rated point)")}
    for src, g in cmp_.groupby("model_class"):
        m, lbl = mk.get(src, ("^", src))
        for ref, gg in g.groupby("refrigerant"):
            ax.plot(gg.V_published_cm3, gg.V_tmhp_cm3, m, ms=3.0, mfc="none", mew=HAIRLINE * 1.4, color=REF_COLORS.get(ref, COLORS["ink"]), label=f"{lbl}, {ref}")
    mape, bias, n = _stats(cmp_.V_tmhp_cm3, cmp_.V_published_cm3)
    ax.text(0.03, 0.97, f"n = {n} machines\nMAPE {mape:.1f} %\nbias {bias:+.1f} %", transform=ax.transAxes, va="top", fontsize=dm.fs(-2.5))
    ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-3.5))
    ax.set_title("Compressor level (rated speed known)", fontsize=dm.fs(-1.5), loc="left")
    panel_letter(ax, "b")
    _save(fig, "F2_displacement_parity", mt="8%", ml="4%")


# ---------------------------------------------------------------------------
# F3  displacement sensitivity
# ---------------------------------------------------------------------------
def _case_label(r) -> str:
    return f"{r.model_class} {r.duty} {r.t_outdoor_C:g}/{r.t_sink_C:g} °C"


def f3_sensitivity() -> None:
    d = pd.read_csv(SIM / "displacement_sensitivity.csv")
    fl = d[(d.kind == "floor") & (d.failure_reason == "none")].copy()
    fl["case"] = [_case_label(r) for r in fl.itertuples()]
    fl[["case", "disp_mult", "V_disp_cm3", "plr_delivered", "n_star", "cop_sys", "capacity_clamped"]].to_csv(SIM / "fig3_min_plr.csv", index=False)
    sw = d[(d.kind == "sweep") & (d.failure_reason == "none")].copy()
    sw["case"] = [_case_label(r) for r in sw.itertuples()]
    case_colors = {}
    palette = [COLORS["accent"], COLORS["warm"], COLORS["ess"]]
    for i, c in enumerate(fl.case.unique()):
        case_colors[c] = palette[i % len(palette)]

    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.36))
    ax = axes[0]
    for c, g in fl.groupby("case"):
        g = g.sort_values("disp_mult")
        ax.plot(g.disp_mult, _pct(g.plr_delivered), "o-", ms=MS, lw=dm.lw(0), color=case_colors[c], label=c)
    ax.set_xlim(0.5, 1.5)
    ax.set_xticks(ticks(0.5, 1.5, 0.25))
    ax.set_ylim(0, 60)
    ax.set_yticks(ticks(0, 60, 10))
    ax.set_xlabel("Displacement multiplier [-]")
    ax.set_ylabel("Lowest deliverable PLR [%]")
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-3.5))
    _grid(ax)

    ls_by_mult = {0.8: (0, (1, 1.2)), 1.0: "solid", 1.2: (0, (4, 1.6))}
    for ax, mc, letter in ((axes[1], "ASHP", "b"), (axes[2], "ASHPB", "c")):
        g0 = sw[sw.model_class == mc]
        for (c, mult), g in g0.groupby(["case", "disp_mult"]):
            mod, floor, _ = _mod_floor(g)
            ax.plot(_pct(mod.plr_request), mod.n_star, ls=ls_by_mult[mult], lw=dm.lw(0), color=case_colors[c], label=f"{c.split(' ', 1)[1]} ×{mult:g}")
            if len(floor):
                ax.plot(_pct(floor.plr_request), floor.n_star, "o", ms=MS, mfc="white", mec=case_colors[c], mew=HAIRLINE)
        ax.axhline(15.0 / (60.0 if mc == "ASHP" else 40.0), color=COLORS["muted"], lw=HAIRLINE, ls=":")
        ax.set_xlim(0, 100)
        ax.set_xticks(ticks(0, 100, 20))
        ax.set_ylim(0, 1.6)
        ax.set_yticks(ticks(0, 1.6, 0.4))
        ax.set_xlabel("Requested PLR [%]")
        ax.set_ylabel(r"Relative speed $n^*$ [-]")
        ax.set_title(f"{mc}, displacement ×0.8 (dotted) / ×1.0 / ×1.2 (dashed)", fontsize=dm.fs(-2.5), loc="left")
        ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-4.5), ncol=1)
        _grid(ax)
        panel_letter(ax, letter, y=1.12)
    panel_letter(axes[0], "a", y=1.12)
    _save(fig, "F3_displacement_sensitivity", mt="10%")


# ---------------------------------------------------------------------------
# F4  catalogue COP parity
# ---------------------------------------------------------------------------
def f4_parity() -> None:
    frames = []
    for f in sorted(RESULTS.glob("*.csv")):
        if f.name == "summary.csv":
            continue
        frames.append(pd.read_csv(f))
    d = pd.concat(frames, ignore_index=True)
    d = d[(d.usable.astype(str) == "True") & (d.status == "adopted")].copy()
    rated = pd.read_csv(RESULTS / "gate_a" / "rated_points.csv")
    rated = rated[rated.status == "adopted"]
    d["is_rated"] = False
    for r in rated.itertuples():
        m = (d.slug == r.slug) & (d.t_source_C == r.t_source_C) & (d.t_sink_C == r.t_sink_C) & (d.q_kW == r.q_kW)
        d.loc[m, "is_rated"] = True
    d[["slug", "unit", "model_class", "refrigerant", "rating_standard", "mode", "t_source_C", "t_sink_C", "q_kW", "cop_target", "cop_pred", "n_star", "pr_cmp", "is_rated"]].to_csv(SIM / "fig4_cop_parity.csv", index=False)

    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.46))
    ax = axes[0]
    g = d[d.model_class == "ASHP"]
    _parity_axes(ax, 1, 9, 2, "Catalogue COP [-]", "TMHP COP [-]")
    for mode, col, lbl in (("cooling", COLORS["cool"], "cooling"), ("heating", COLORS["hot"], "heating")):
        gg = g[g["mode"] == mode]
        ax.plot(gg.cop_target, gg.cop_pred, "o", ms=2.2, alpha=0.7, mew=0, color=col, label=f"{lbl} (n = {len(gg)})")
    gr = g[g.is_rated]
    ax.plot(gr.cop_target, gr.cop_pred, "o", ms=5, mfc="none", mec=COLORS["ink"], mew=HAIRLINE * 1.5, label="rated point")
    mape, bias, n = _stats(g.cop_pred, g.cop_target)
    ax.text(0.03, 0.97, f"Daikin RXM-A, 5 units, R32, EN 14511\nMAPE {mape:.1f} %, bias {bias:+.1f} %, n = {n}", transform=ax.transAxes, va="top", fontsize=dm.fs(-2.5))
    ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-3))
    ax.set_title("Air-to-air (ASHP)", fontsize=dm.fs(-1.5), loc="left")
    panel_letter(ax, "a")

    ax = axes[1]
    g = d[d.model_class == "ASHPB"]
    _parity_axes(ax, 1, 7, 1, "Catalogue COP [-]", "TMHP COP [-]")
    for ref, gg in g.groupby("refrigerant"):
        ax.plot(gg.cop_target, gg.cop_pred, "o", ms=2.6, alpha=0.8, mew=0, color=REF_COLORS.get(ref, COLORS["ink"]), label=f"{ref} (n = {len(gg)})")
    gr = g[g.is_rated]
    ax.plot(gr.cop_target, gr.cop_pred, "o", ms=5, mfc="none", mec=COLORS["ink"], mew=HAIRLINE * 1.5, label="rated point A7/W45")
    mape, bias, n = _stats(g.cop_pred, g.cop_target)
    ax.text(0.03, 0.97, f"Panasonic 9 + Samsung 1 units, heating\nMAPE {mape:.1f} %, bias {bias:+.1f} %, n = {n}", transform=ax.transAxes, va="top", fontsize=dm.fs(-2.5))
    ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-3))
    ax.set_title("Air-to-water (ASHPB)", fontsize=dm.fs(-1.5), loc="left")
    panel_letter(ax, "b")
    _save(fig, "F4_cop_parity", mt="8%")


# ---------------------------------------------------------------------------
# F5 / F6  PLR-COP maps by outdoor temperature
# ---------------------------------------------------------------------------
def _map_panel(ax, g: pd.DataFrame, cmap_name: str, title: str, ylim: tuple[float, float], ystep: float, legend_loc: str = "upper right") -> None:
    temps = sorted(g.t_outdoor_C.unique())
    cmap = plt.get_cmap(cmap_name)
    for i, t in enumerate(temps):
        col = cmap(0.1 + 0.8 * i / max(len(temps) - 1, 1))
        _plr_curve(ax, g[g.t_outdoor_C == t], col, label=f"{t:g} °C")
    ax.set_xlim(0, 100)
    ax.set_xticks(ticks(0, 100, 20))
    ax.set_ylim(*ylim)
    ax.set_yticks(ticks(ylim[0], ylim[1], ystep))
    ax.set_xlabel("Requested part-load ratio [%]")
    ax.set_ylabel(r"System $\mathrm{COP}$ [-]")
    ax.set_title(title, fontsize=dm.fs(-1.5), loc="left")
    ax.legend(loc=legend_loc, frameon=False, fontsize=dm.fs(-3.5), title="outdoor", title_fontsize=dm.fs(-3.5), ncol=2)
    _grid(ax)


def f5_ashp_map() -> None:
    d = pd.read_csv(SIM / "plr_map_ashp.csv")
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.42))
    h = d[d.duty == "heating"]
    c = d[d.duty == "cooling"]
    _map_panel(axes[0], h, "viridis", "Heating, room 20 °C, 3.5 kW R32", (1, 11), 2)
    _map_panel(axes[1], c, "plasma", "Cooling, room 27 °C, 3.5 kW R32", (1, 11), 2, legend_loc="lower left")
    panel_letter(axes[0], "a")
    panel_letter(axes[1], "b")
    _save(fig, "F5_ashp_plr_cop_map", mt="8%")


def f6_ashpb_map() -> None:
    d = pd.read_csv(SIM / "plr_map_ashpb.csv")
    fig, ax = plt.subplots(1, 1, figsize=dm.figsize("11cm", 0.62))
    _map_panel(ax, d, "viridis", "Heating, tank 42.5 °C (leaving water ≈ 45 °C), 9 kW R32", (1, 7), 1)
    _save(fig, "F6_ashpb_plr_cop_map", mt="8%")


# ---------------------------------------------------------------------------
# F7  minimum-speed diagnostic
# ---------------------------------------------------------------------------
def f7_min_speed() -> None:
    d = pd.read_csv(SIM / "base_cases.csv")
    d = d[d.failure_reason == "none"].copy()
    d["case"] = [f"{r.model_class} {r.duty} {r.refrigerant} {r.t_outdoor_C:g}/{r.t_sink_C:g} °C" for r in d.itertuples()]
    keep = [
        "ASHP heating R32 7/20 °C",
        "ASHP cooling R32 35/27 °C",
        "ASHPB heating R32 7/42.5 °C",
        "ASHPB heating R32 -7/32.5 °C",
    ]
    d = d[d.case.isin(keep)]
    cols = {keep[0]: COLORS["hot"], keep[1]: COLORS["cool"], keep[2]: COLORS["accent"], keep[3]: COLORS["accent2"]}
    d.to_csv(SIM / "fig7_min_speed_diagnostic.csv", index=False)
    fig, axes = plt.subplots(2, 3, figsize=dm.figsize("17cm", 0.62), gridspec_kw={"wspace": 0.5, "hspace": 0.45})
    axes = axes.ravel()

    def series(ax, col, scale=1.0, y2=None):
        for c, g in d.groupby("case"):
            mod, floor, _ = _mod_floor(g)
            ax.plot(_pct(mod.plr_request), mod[col] * scale, "o-", ms=MS * 0.8, lw=dm.lw(0), color=cols[c], label=c)
            ax.plot(_pct(floor.plr_request), floor[col] * scale, "o", ms=MS * 0.8, mfc="white", mec=cols[c], mew=HAIRLINE)
            if y2 is not None:
                ax.plot(_pct(mod.plr_request), mod[y2] * scale, "s", ms=MS * 0.7, ls=(0, (2, 1.2)), lw=dm.lw(-0.5), color=cols[c])
                ax.plot(_pct(floor.plr_request), floor[y2] * scale, "s", ms=MS * 0.7, mfc="white", mec=cols[c], mew=HAIRLINE)

    ax = axes[0]
    series(ax, "plr_delivered", 100.0)
    ax.plot([0, 100], [0, 100], color=COLORS["muted"], lw=HAIRLINE, ls=":")
    ax.set_ylim(0, 100)
    ax.set_yticks(ticks(0, 100, 20))
    ax.set_ylabel("Delivered PLR [%]")
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-4))
    ax = axes[1]
    series(ax, "n_star")
    ax.set_ylim(0, 1.6)
    ax.set_yticks(ticks(0, 1.6, 0.4))
    ax.set_ylabel(r"Relative speed $n^*$ [-]")
    ax = axes[2]
    series(ax, "q_delivered_W", 1e-3)
    ax.set_ylim(0, 10)
    ax.set_yticks(ticks(0, 10, 2))
    ax.set_ylabel("Delivered capacity [kW]")
    ax = axes[3]
    series(ax, "E_cmp", 1e-3)
    ax.set_ylim(0, 3)
    ax.set_yticks(ticks(0, 3, 0.5))
    ax.set_ylabel("Compressor power [kW]")
    ax = axes[4]
    d["E_fans"] = d.E_ou_fan + d.E_iu_fan
    series(ax, "E_fans", 1e-3)
    ax.set_ylim(0, 0.15)
    ax.set_yticks(ticks(0, 0.15, 0.05))
    ax.set_ylabel("Fan power (all fans) [kW]")
    ax = axes[5]
    series(ax, "T_cond_C", y2="T_evap_C")
    ax.set_ylim(-20, 60)
    ax.set_yticks(ticks(-20, 60, 20))
    ax.set_ylabel(r"$T_{sat}$ [°C]")
    ax.text(0.03, 0.97, "circles: condensing\nsquares: evaporating", transform=ax.transAxes, fontsize=dm.fs(-4), va="top")
    for i, ax in enumerate(axes):
        ax.set_xlim(0, 100)
        ax.set_xticks(ticks(0, 100, 20))
        if i >= 3:
            ax.set_xlabel("Requested PLR [%]")
        _grid(ax)
        panel_letter(ax, "abcdef"[i], x=-0.30, y=1.06)
    _save(fig, "F7_min_speed_diagnostic", mt="5%", ml="3%")


# ---------------------------------------------------------------------------
# F9  air-to-air low-load COP decomposition
# ---------------------------------------------------------------------------
def f9_decomposition() -> None:
    dec = pd.read_csv(PLR_DIR / "decomposition.csv")
    dec = dec[(dec.model_class == "ASHP") & (dec.failure_reason == "none")]
    base = pd.read_csv(SIM / "base_cases.csv")
    base = base[(base.model_class == "ASHP") & (base.failure_reason == "none")]
    rows = []
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.36))
    duty_col = {"heating": COLORS["hot"], "cooling": COLORS["cool"]}
    for ax, duty, letter in ((axes[0], "heating", "a"), (axes[1], "cooling", "b")):
        g = dec[dec.duty == duty]
        for var, ls, lbl in (("hx_only", (0, (4, 1.6)), "heat exchangers only (efficiencies frozen at PLR 1)"), ("current", "solid", "final model")):
            _plr_curve(ax, g[g.variant == var], duty_col[duty] if var == "current" else COLORS["muted"], label=lbl, ls=ls)
        b = base[base.duty == duty]
        mod, floor, _ = _mod_floor(b)
        ax.plot(_pct(mod.plr_request), mod.cop_cmp, ls=(0, (1, 1.2)), lw=dm.lw(0), color=COLORS["ink"], label=r"compressor-only $\mathrm{COP}$ = $Q/E_{cmp}$")
        ax.set_xlim(0, 100)
        ax.set_xticks(ticks(0, 100, 20))
        ax.set_ylim(4, 9)
        ax.set_yticks(ticks(4, 9, 1))
        ax.set_xlabel("Requested PLR [%]")
        ax.set_ylabel(r"$\mathrm{COP}$ [-]")
        ax.set_title(f"{duty}, 3.5 kW R32, {'7/20' if duty == 'heating' else '35/27'} °C", fontsize=dm.fs(-1.5), loc="left")
        ax.legend(loc="upper right", frameon=False, fontsize=dm.fs(-4))
        _grid(ax)
        panel_letter(ax, letter)
        # relative contributions
        cur = g[(g.variant == "current") & g.capacity_clamped.isna()].set_index("plr_request")
        hx = g[(g.variant == "hx_only") & g.capacity_clamped.isna()].set_index("plr_request")
        idx = cur.index.intersection(hx.index)
        rated_cop = cur.loc[1.0, "cop_sys"]
        for p in idx:
            rows.append(
                {
                    "duty": duty,
                    "plr_request": p,
                    "cop_final": cur.loc[p, "cop_sys"],
                    "cop_hx_only": hx.loc[p, "cop_sys"],
                    "hx_gain_pct": (hx.loc[p, "cop_sys"] / rated_cop - 1.0) * 100.0,
                    "compressor_penalty_pct": (cur.loc[p, "cop_sys"] / hx.loc[p, "cop_sys"] - 1.0) * 100.0,
                    "net_pct": (cur.loc[p, "cop_sys"] / rated_cop - 1.0) * 100.0,
                    "n_star": cur.loc[p, "n_star"],
                    "pr": cur.loc[p, "pr"],
                    "eta_oi_over_rated": (cur.loc[p, "eta_isen"] * cur.loc[p, "eta_em"]) / (cur.loc[1.0, "eta_isen"] * cur.loc[1.0, "eta_em"]),
                    "fan_share_pct": (1.0 - cur.loc[p, "E_cmp"] / cur.loc[p, "E_tot"]) * 100.0,
                }
            )
    rel = pd.DataFrame(rows)
    rel.to_csv(SIM / "fig9_decomposition.csv", index=False)
    ax = axes[2]
    for duty, g in rel.groupby("duty"):
        g = g.sort_values("plr_request")
        ax.plot(_pct(g.plr_request), g.hx_gain_pct, ls=(0, (4, 1.6)), lw=dm.lw(0), color=duty_col[duty], label=f"{duty}: heat-exchanger gain")
        ax.plot(_pct(g.plr_request), g.compressor_penalty_pct, ls=(0, (1, 1.2)), lw=dm.lw(0), color=duty_col[duty], label=f"{duty}: compressor penalty")
        ax.plot(_pct(g.plr_request), g.net_pct, "o-", ms=MS * 0.8, lw=dm.lw(0), color=duty_col[duty], label=f"{duty}: net vs PLR 100 %")
    ax.axhline(0, color=COLORS["muted"], lw=HAIRLINE)
    ax.set_xlim(0, 100)
    ax.set_xticks(ticks(0, 100, 20))
    ax.set_ylim(-20, 40)
    ax.set_yticks(ticks(-20, 40, 10))
    ax.set_xlabel("Requested PLR [%]")
    ax.set_ylabel(r"$\Delta\mathrm{COP}$ [%]")
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-4.5))
    _grid(ax)
    panel_letter(ax, "c")
    _save(fig, "F9_ashp_lowload_decomposition", mt="6%")


# ---------------------------------------------------------------------------
# F8  speed-floor heat-exchanger closure: before / after
# ---------------------------------------------------------------------------
#: The pre-fix datasets, kept so the change can be shown rather than asserted.
#: In that model the tank-side approach came from the *requested* duty, so its
#: closure residual is exactly ``Q_delivered - Q_request``.
PRE = SIM / "pre_fix"

ASHPB_CASE = ("ASHPB", "R32", 7.0, 42.5)
ASHP_CASE = ("ASHP", "R32", 7.0, 20.0)


def _case(d: pd.DataFrame, model: str, ref: str, t_out: float, t_sink: float) -> pd.DataFrame:
    m = (
        (d.model_class == model)
        & (d.refrigerant == ref)
        & (d.t_outdoor_C == t_out)
        & (d.t_sink_C == t_sink)
        & (d.failure_reason == "none")
    )
    return d[m].sort_values("plr_request")


def _floor_split(g: pd.DataFrame):
    return g[g.capacity_clamped.isna()], g[g.capacity_clamped == "min"]


def _before_after(ax, col_new: str, col_old: str | None = None, scale: float = 1.0) -> None:
    """One quantity of the 9 kW air-to-water case, pre-fix against final."""
    col_old = col_old or col_new
    for d, color, label, ls in (
        (_case(pd.read_csv(PRE / "base_cases.csv"), *ASHPB_CASE), COLORS["muted"], "requested-duty closure (before)", (0, (4, 1.6))),
        (_case(pd.read_csv(SIM / "base_cases.csv"), *ASHPB_CASE), COLORS["accent"], "delivered-duty closure (final)", "solid"),
    ):
        col = col_old if color == COLORS["muted"] else col_new
        mod, floor = _floor_split(d)
        ax.plot(_pct(mod.plr_request), mod[col] * scale, ls=ls, lw=dm.lw(0), color=color, marker="o", ms=MS * 0.8, label=label)
        if len(floor):
            ax.plot(_pct(floor.plr_request), floor[col] * scale, ls=ls, lw=dm.lw(0), color=color)
            ax.plot(_pct(floor.plr_request), floor[col] * scale, ls="none", marker="o", ms=MS * 0.8, mfc="white", mec=color, mew=HAIRLINE)


def f8_speed_floor_closure() -> None:
    new = pd.read_csv(SIM / "base_cases.csv")
    old = pd.read_csv(PRE / "base_cases.csv")
    cases = {
        "ASHP heating 7/20 °C": (_case(new, *ASHP_CASE), COLORS["hot"]),
        "ASHP cooling 35/27 °C": (_case(new, "ASHP", "R32", 35.0, 27.0), COLORS["cool"]),
        "ASHPB heating 7/42.5 °C": (_case(new, *ASHPB_CASE), COLORS["accent"]),
    }

    fig, axes = plt.subplots(2, 4, figsize=dm.figsize("17cm", 0.50), gridspec_kw={"wspace": 0.55, "hspace": 0.50})
    axes = axes.ravel()

    # (a) requested vs delivered part-load ratio
    ax = axes[0]
    for label, (g, color) in cases.items():
        mod, floor = _floor_split(g)
        ax.plot(_pct(mod.plr_request), _pct(mod.plr_delivered), "o-", ms=MS * 0.8, lw=dm.lw(0), color=color, label=label)
        ax.plot(_pct(floor.plr_request), _pct(floor.plr_delivered), "o", ms=MS * 0.8, mfc="white", mec=color, mew=HAIRLINE)
    ax.plot([0, 100], [0, 100], color=COLORS["muted"], lw=HAIRLINE, ls=":")
    ax.set_ylim(0, 100)
    ax.set_yticks(ticks(0, 100, 20))
    ax.set_ylabel("Delivered PLR [%]")
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-4.5))

    # (b) relative compressor speed
    ax = axes[1]
    for g, color in cases.values():
        mod, floor = _floor_split(g)
        ax.plot(_pct(mod.plr_request), mod.n_star, "o-", ms=MS * 0.8, lw=dm.lw(0), color=color)
        ax.plot(_pct(floor.plr_request), floor.n_star, "o", ms=MS * 0.8, mfc="white", mec=color, mew=HAIRLINE)
    ax.axhline(15.0 / 40.0, color=COLORS["ink"], lw=HAIRLINE, ls=(0, (3, 2)))
    ax.text(3, 15.0 / 40.0 + 0.04, "air-to-water floor", fontsize=dm.fs(-4.5), color=COLORS["ink"])
    ax.axhline(15.0 / 60.0, color=COLORS["ink"], lw=HAIRLINE, ls=(0, (1, 1.5)))
    ax.text(3, 15.0 / 60.0 - 0.13, "air-to-air floor", fontsize=dm.fs(-4.5), color=COLORS["ink"])
    ax.set_ylim(0, 1.6)
    ax.set_yticks(ticks(0, 1.6, 0.4))
    ax.set_ylabel(r"Relative speed $n^*$ [-]")

    # (c) condensing temperature, before / after
    ax = axes[2]
    _before_after(ax, "T_cond_C")
    ax.set_ylim(42, 49)
    ax.set_yticks(ticks(42, 49, 1))
    ax.set_ylabel(r"$T_{cond}$ [°C]")
    ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-4.5))

    # (d) pressure ratio, before / after
    ax = axes[3]
    _before_after(ax, "pr")
    ax.set_ylim(3.0, 3.8)
    ax.set_yticks(ticks(3.0, 3.8, 0.2))
    ax.set_ylabel(r"Pressure ratio $r_p$ [-]")

    # (e) system COP, before / after
    ax = axes[4]
    _before_after(ax, "cop_sys")
    ax.set_ylim(3.5, 4.0)
    ax.set_yticks(ticks(3.5, 4.0, 0.1))
    ax.set_ylabel(r"System $\mathrm{COP}$ [-]")

    # (f) compressor power, before / after
    ax = axes[5]
    _before_after(ax, "E_cmp", scale=1e-3)
    ax.set_ylim(0.6, 2.6)
    ax.set_yticks(ticks(0.6, 2.6, 0.4))
    ax.set_ylabel(r"$E_{cmp}$ [kW]")

    # (g) outdoor-coil relief, normalised to the full-load value
    ax = axes[6]
    styles = (("UA_ou", "solid", r"$UA$"), ("NTU_ou", (0, (4, 1.6)), "NTU"), ("epsilon_ou", (0, (1, 1.3)), r"$\varepsilon$"), ("fan_fraction_ou", (0, (5, 1.5, 1, 1.5)), "airflow"))
    rows = []
    for label, (g, color) in (("ASHP heating 7/20 °C", cases["ASHP heating 7/20 °C"]), ("ASHPB heating 7/42.5 °C", cases["ASHPB heating 7/42.5 °C"])):
        if not len(g):
            continue
        full = g[g.plr_request == 1.0].iloc[0]
        for col, ls, _ in styles:
            ax.plot(_pct(g.plr_request), g[col] / full[col], ls=ls, lw=dm.lw(0), color=color)
        for r in g.itertuples():
            rows.append({"case": label, "plr_request": r.plr_request, "UA_ou": r.UA_ou, "NTU_ou": r.NTU_ou, "epsilon_ou": r.epsilon_ou, "fan_fraction_ou": r.fan_fraction_ou})
    for _, ls, lbl in styles:
        ax.plot([], [], ls=ls, lw=dm.lw(0), color=COLORS["ink"], label=lbl)
    ax.set_ylim(0, 1.6)
    ax.set_yticks(ticks(0, 1.6, 0.4))
    ax.set_ylabel("Relative to full load [-]")
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-4.5), ncol=2)
    ax.text(0.04, 0.06, "red: ASHP heating\nblue: ASHPB heating", transform=ax.transAxes, fontsize=dm.fs(-4.5), va="bottom")

    # (h) tank-side closure residual
    ax = axes[7]
    g_old = _case(old, *ASHPB_CASE)
    g_new = _case(new, *ASHPB_CASE)
    ax.semilogy(_pct(g_old.plr_request), np.abs(g_old.q_delivered_W - g_old.q_request_W).clip(lower=1e-9), ls=(0, (4, 1.6)), lw=dm.lw(0), color=COLORS["muted"], marker="o", ms=MS * 0.8, label="before")
    ax.semilogy(_pct(g_new.plr_request), np.abs(g_new.tank_hx_residual).clip(lower=1e-9), "o-", ms=MS * 0.8, lw=dm.lw(0), color=COLORS["accent"], label="final")
    ax.set_ylim(1e-8, 1e5)
    ax.set_ylabel(r"$|\,Q_{del} - UA_{tank}\,\Delta T\,|$ [W]")
    ax.legend(loc="upper right", frameon=False, fontsize=dm.fs(-4.5))

    pd.DataFrame(rows).to_csv(SIM / "fig8_coil_diagnostics.csv", index=False)
    merged = g_new[["plr_request", "plr_delivered", "n_star", "pr", "T_cond_C", "cop_sys", "E_cmp", "tank_hx_residual", "capacity_clamped"]].copy()
    merged = merged.merge(
        g_old[["plr_request", "plr_delivered", "n_star", "pr", "T_cond_C", "cop_sys", "E_cmp"]],
        on="plr_request",
        suffixes=("_final", "_before"),
    )
    merged.to_csv(SIM / "fig8_speed_floor_closure.csv", index=False)

    for i, ax in enumerate(axes):
        ax.set_xlim(0, 100)
        ax.set_xticks(ticks(0, 100, 25))
        if i >= 4:
            ax.set_xlabel("Requested PLR [%]")
        _grid(ax)
        panel_letter(ax, "abcdefgh"[i], x=-0.34, y=1.06)
    _save(fig, "F8_speed_floor_closure", mt="5%", ml="3%")


# ---------------------------------------------------------------------------
# A1  speed-transfer check on the compressor data
# ---------------------------------------------------------------------------
def a1_speed_transfer() -> None:
    d = pd.read_csv(DATA_DIR / "within_machine_contrasts.csv")
    d = d[d.n_star < 1.0].copy()
    d["pred_oi"] = np.log([ce.leakage_factor(pr, n) * ce.flow_factor(n) for pr, n in zip(d.PR, d.n_star, strict=True)])
    d["pred_vol"] = np.log(
        [(1 - ce.ETA_VOL_A * (pr - 1) - ce.ETA_VOL_B * max(0.0, 1 / n - 1)) / (1 - ce.ETA_VOL_A * (pr - 1)) for pr, n in zip(d.PR, d.n_star, strict=True)]
    )
    d.to_csv(SIM / "figA1_speed_transfer.csv", index=False)
    labels = {"copeland_opi": "Copeland scrolls", "cuevas_lebrun_2009": "Cuevas & Lebrun 2009", "guth_atakan_2023": "Guth & Atakan 2023", "shao_2004": "Shao et al. 2004 rotaries"}
    mk = {"copeland_opi": "o", "cuevas_lebrun_2009": "s", "guth_atakan_2023": "^", "shao_2004": "D"}
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.36))
    ax = axes[0]
    for src, g in d.groupby("source_id"):
        g = g.assign(pb=pd.cut(g.pred_oi * 100, np.arange(-40, 5, 2.5)))
        med = g.groupby("pb", observed=True).agg(p=("pred_oi", "median"), o=("d_ln_eta_oi", "median"), n=("PR", "size"))
        med = med[med.n >= 3]
        ax.scatter(100 * g.pred_oi, 100 * g.d_ln_eta_oi, s=3, alpha=0.12, color=COLORS["muted"], edgecolors="none")
        ax.plot(100 * med.p, 100 * med.o, mk[src], ms=3.4, color=COLORS["ink"], mfc="none", mew=HAIRLINE * 1.4, label=labels[src])
    ax.plot([-40, 5], [-40, 5], color=COLORS["accent"], lw=HAIRLINE)
    ax.set_xlim(-40, 5)
    ax.set_ylim(-60, 25)
    ax.set_xticks(ticks(-40, 0, 10))
    ax.set_yticks(ticks(-60, 20, 20))
    ax.set_xlabel(r"Predicted $\Delta\ln(\eta_{is}\eta_{em})$ vs rated speed [%]")
    ax.set_ylabel(r"Measured $\Delta\ln(\eta_{is}\eta_{em})$ [%]")
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-4))
    ax.set_title("Product, all records below rated speed", fontsize=dm.fs(-2), loc="left")
    _grid(ax)
    panel_letter(ax, "a")

    ax = axes[1]
    cp = d[d.source_id == "copeland_opi"].copy()
    cp["n_bin"] = pd.cut(cp.n_star, [0, 0.3, 0.45, 0.6, 0.8], labels=["n* < 0.30", "0.30–0.45", "0.45–0.60", "0.60–0.80"])
    cmap = plt.get_cmap("viridis")
    prs = np.linspace(1.5, 7.5, 60)
    for i, (lbl, g) in enumerate(cp.groupby("n_bin", observed=True)):
        col = cmap(0.15 + 0.25 * i)
        g = g.assign(pb=pd.cut(g.PR, np.arange(1.0, 8.5, 0.5)))
        med = g.groupby("pb", observed=True).agg(PR=("PR", "median"), o=("d_ln_eta_oi", "median"), n=("PR", "size"))
        med = med[med.n >= 2]
        ax.plot(med.PR, 100 * med.o, "o", ms=3.0, color=col, label=str(lbl))
        nmid = float(g.n_star.median())
        ax.plot(prs, [100 * np.log(ce.leakage_factor(pr, nmid)) for pr in prs], color=col, lw=dm.lw(0))
    ax.axhline(0, color=COLORS["muted"], lw=HAIRLINE, ls=":")
    ax.set_xlim(1, 8)
    ax.set_xticks(ticks(1, 8, 1))
    ax.set_ylim(-50, 15)
    ax.set_yticks(ticks(-50, 10, 10))
    ax.set_xlabel(r"Pressure ratio $r_p$ [-]")
    ax.set_ylabel(r"$\Delta\ln(\eta_{is}\eta_{em})$ vs rated speed [%]")
    ax.set_title("Copeland: bin medians (points), model (lines)", fontsize=dm.fs(-2), loc="left")
    ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-4))
    _grid(ax)
    panel_letter(ax, "b")

    ax = axes[2]
    for src, g in d[d.d_ln_eta_vol.notna()].groupby("source_id"):
        g = g.assign(pb=pd.cut(g.pred_vol * 100, np.arange(-20, 2.5, 1.0)))
        med = g.groupby("pb", observed=True).agg(p=("pred_vol", "median"), o=("d_ln_eta_vol", "median"), n=("PR", "size"))
        med = med[med.n >= 3]
        ax.scatter(100 * g.pred_vol, 100 * g.d_ln_eta_vol, s=3, alpha=0.12, color=COLORS["muted"], edgecolors="none")
        ax.plot(100 * med.p, 100 * med.o, mk[src], ms=3.4, color=COLORS["ink"], mfc="none", mew=HAIRLINE * 1.4, label=labels[src])
    ax.plot([-20, 2], [-20, 2], color=COLORS["accent"], lw=HAIRLINE)
    ax.set_xlim(-20, 2)
    ax.set_ylim(-30, 10)
    ax.set_xticks(ticks(-20, 0, 5))
    ax.set_yticks(ticks(-30, 10, 10))
    ax.set_xlabel(r"Predicted $\Delta\ln(\eta_v)$ vs rated speed [%]")
    ax.set_ylabel(r"Measured $\Delta\ln(\eta_v)$ [%]")
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-4))
    ax.set_title("Volumetric efficiency", fontsize=dm.fs(-2), loc="left")
    _grid(ax)
    panel_letter(ax, "c")
    _save(fig, "FA1_speed_transfer", mt="8%")


# ---------------------------------------------------------------------------
# Appendix B  PLR shape metrics of every simulated case
# ---------------------------------------------------------------------------
def appendix_metrics() -> None:
    frames = []
    for name in ("plr_map_ashp", "plr_map_ashpb", "base_cases"):
        df = pd.read_csv(SIM / f"{name}.csv")
        df["source_file"] = f"{name}.csv"
        frames.append(df)
    d = pd.concat(frames, ignore_index=True)
    d["cr_actual"] = d.plr_delivered
    rows = []
    for key, g in d.groupby(["source_file", "model_class", "duty", "refrigerant", "capacity_W", "t_outdoor_C", "t_sink_C"]):
        rec = dict(zip(["source_file", "model_class", "duty", "refrigerant", "capacity_W", "t_outdoor_C", "t_sink_C"], key, strict=True))
        rec.update(metrics_for(g))
        rows.append(rec)
    out = pd.DataFrame(rows)
    out.to_csv(SIM / "appendix_plr_metrics.csv", index=False)
    print(out[["source_file", "model_class", "duty", "refrigerant", "t_outdoor_C", "t_sink_C", "shape", "PLR_peak", "COP_peak", "PLR_low", "COP_low", "dCOP_low", "COP_rated"]].to_string(index=False, float_format=lambda v: f"{v:.3f}"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()
    apply_style()
    SIM.mkdir(parents=True, exist_ok=True)
    todo = {
        "F1": f1_efficiency,
        "F2": f2_displacement,
        "F3": f3_sensitivity,
        "F4": f4_parity,
        "F5": f5_ashp_map,
        "F6": f6_ashpb_map,
        "F7": f7_min_speed,
        "F8": f8_speed_floor_closure,
        "F9": f9_decomposition,
        "A1": a1_speed_transfer,
        "B": appendix_metrics,
    }
    for k, fn in todo.items():
        if a.only and k not in a.only:
            continue
        fn()
        print("wrote", k)


if __name__ == "__main__":
    main()
