"""Figures 1-7 of the refrigerant-flow UA sensitivity study (plan Sec. 10-11).

Style follows ``validation/compressor_maps/final_figures.py``: the shared
dartwork-mpl bootstrap in ``scripts/visualization/_dmpl_common``, explicit
ticks, filled markers for the continuously modulating rows and open markers
for rows at the compressor speed floor (which carry no sensitivity verdict).

Every figure writes PNG + SVG and its own source table, all into
``validation/results/refrigerant_ua_sensitivity_once/``.

Run after ``simulate``::

    uv run python3 -m validation.refrigerant_ua_sensitivity.figures [--only 01 06]
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
from validation.refrigerant_ua_sensitivity.simulate import (  # noqa: E402
    CSV_NAME,
    F_REF_GRID,
    F_REF_MAIN,
    M_GRID,
    M_MAIN,
    OUT_DIR,
)

MS = 2.6
MAIN_VARIANTS = ["baseline"] + [f"f{f:.2f}_m{M_MAIN:.1f}" for f in F_REF_MAIN]
VAR_COLOR = {
    "baseline": COLORS["ink"],
    f"f{F_REF_MAIN[0]:.2f}_m{M_MAIN:.1f}": COLORS["cool"],
    f"f{F_REF_MAIN[1]:.2f}_m{M_MAIN:.1f}": COLORS["accent"],
    f"f{F_REF_MAIN[2]:.2f}_m{M_MAIN:.1f}": COLORS["hot"],
}
VAR_LABEL = {
    "baseline": "baseline ($f_{ref}$ = 0)",
    **{f"f{f:.2f}_m{M_MAIN:.1f}": rf"$f_{{ref}}$ = {f:.2f}" for f in F_REF_MAIN},
}
DUTY_TITLE = {
    "heating": "Heating, 7 °C outdoor / 20 °C room",
    "cooling": "Cooling, 35 °C outdoor / 27 °C room",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _load() -> pd.DataFrame:
    d = pd.read_csv(OUT_DIR / CSV_NAME)
    return d[d["pass"] != "unpatched"].copy()


def _save(fig, name: str, **margins) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for extra in (0, 2, 4, 6, 8):
        try:
            kw = {k: f"{float(v.rstrip('%')) + extra}%" for k, v in margins.items()}
            finalize(fig, OUT_DIR / name, formats=("svg", "png"), margin=f"{2 + extra}%", **kw)
            break
        except RuntimeError as exc:
            if extra == 8:
                raise exc
    plt.close(fig)


def _grid(ax) -> None:
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)


def _split(g: pd.DataFrame):
    ok = g[g.failure_reason == "none"].sort_values("plr_request")
    return ok[ok.capacity_clamped.isna()], ok[ok.capacity_clamped == "min"]


def _series(ax, g, col, color, label=None, scale=1.0, ls="solid"):
    """Filled markers = continuous modulation; open markers = speed floor."""
    mod, floor = _split(g)
    ax.plot(100 * mod.plr_request, mod[col] * scale, ls=ls, lw=dm.lw(0), color=color, marker="o", ms=MS, label=label)
    if len(floor):
        ax.plot(
            100 * floor.plr_request,
            floor[col] * scale,
            ls="none",
            marker="o",
            ms=MS,
            mfc="white",
            mec=color,
            mew=HAIRLINE,
        )


def _plr_axis(ax, xlabel=True) -> None:
    ax.set_xlim(0, 100)
    ax.set_xticks(ticks(0, 100, 20))
    if xlabel:
        ax.set_xlabel("Requested part-load ratio [%]")
    _grid(ax)


def _panel(ax, d, duty, col, ylabel, ylim, ystep, scale=1.0, title=None, legend=None):
    for v in MAIN_VARIANTS:
        g = d[(d.duty == duty) & (d.variant == v)]
        _series(ax, g, col, VAR_COLOR[v], label=VAR_LABEL[v], scale=scale)
    ax.set_ylabel(ylabel)
    ax.set_ylim(*ylim)
    ax.set_yticks(ticks(ylim[0], ylim[1], ystep))
    if title:
        ax.set_title(title, fontsize=dm.fs(-2), loc="left")
    if legend:
        ax.legend(loc=legend, frameon=False, fontsize=dm.fs(-3.5))
    _plr_axis(ax)


def _source(d, cols, name) -> None:
    keep = ["variant", "f_ref", "m_exp", "duty", "plr_request", "capacity_clamped", "failure_reason", *cols]
    d[keep].to_csv(OUT_DIR / name, index=False)


# ---------------------------------------------------------------------------
# 01  refrigerant mass-flow ratio
# ---------------------------------------------------------------------------
def fig01(d) -> None:
    sub = d[d.variant.isin(MAIN_VARIANTS)]
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.40), gridspec_kw={"wspace": 0.30})
    for ax, duty, letter in zip(axes, ("heating", "cooling"), "ab", strict=True):
        _panel(
            ax,
            sub,
            duty,
            "x_ref",
            r"$x_{ref}$ = $\dot m_{ref}$ / $\dot m_{ref,rated}$ [-]",
            (0, 1.0),
            0.2,
            title=DUTY_TITLE[duty],
            legend="upper left" if duty == "heating" else None,
        )
        ax.plot([0, 100], [0, 1.0], color=COLORS["muted"], lw=HAIRLINE, ls=":", zorder=0)
        panel_letter(ax, letter)
    _source(sub, ["x_ref", "m_dot_ref", "m_dot_ref_rated", "n_star"], "fig01_mdot_ratio_vs_plr.csv")
    _save(fig, "01_mdot_ratio_vs_plr", mt="6%")


# ---------------------------------------------------------------------------
# 02  UA ratio  (core figure)
# ---------------------------------------------------------------------------
def fig02(d) -> None:
    sub = d[d.variant.isin(MAIN_VARIANTS)]
    fig, axes = plt.subplots(2, 2, figsize=dm.figsize("17cm", 0.68), gridspec_kw={"wspace": 0.30, "hspace": 0.45})
    spec = [
        ("heating", "ua_ratio_ou", "Outdoor coil (evaporator)"),
        ("heating", "ua_ratio_iu", "Indoor coil (condenser)"),
        ("cooling", "ua_ratio_ou", "Outdoor coil (condenser)"),
        ("cooling", "ua_ratio_iu", "Indoor coil (evaporator)"),
    ]
    for ax, (duty, col, coil), letter in zip(axes.ravel(), spec, "abcd", strict=True):
        _panel(
            ax,
            sub,
            duty,
            col,
            r"$UA$ / $UA_{rated}$ [-]",
            (0.4, 1.0),
            0.2,
            title=f"{DUTY_TITLE[duty].split(',')[0]} — {coil}",
            legend="upper left" if letter == "a" else None,
        )
        panel_letter(ax, letter)
    _source(sub, ["ua_ratio_ou", "ua_ratio_iu", "UA_ou", "UA_iu", "x_ref", "fan_fraction_ou"], "fig02_ua_ratio_vs_plr.csv")
    _save(fig, "02_ua_ratio_vs_plr", mt="5%")


# ---------------------------------------------------------------------------
# 03  effectiveness
# ---------------------------------------------------------------------------
def fig03(d) -> None:
    sub = d[d.variant.isin(MAIN_VARIANTS)]
    fig, axes = plt.subplots(2, 2, figsize=dm.figsize("17cm", 0.68), gridspec_kw={"wspace": 0.30, "hspace": 0.45})
    spec = [
        ("heating", "epsilon_ou", "Outdoor coil (evaporator)"),
        ("heating", "epsilon_iu", "Indoor coil (condenser)"),
        ("cooling", "epsilon_ou", "Outdoor coil (condenser)"),
        ("cooling", "epsilon_iu", "Indoor coil (evaporator)"),
    ]
    for ax, (duty, col, coil), letter in zip(axes.ravel(), spec, "abcd", strict=True):
        _panel(
            ax,
            sub,
            duty,
            col,
            r"Effectiveness $\varepsilon$ [-]",
            (0.45, 0.75),
            0.1,
            title=f"{DUTY_TITLE[duty].split(',')[0]} — {coil}",
            legend="lower left" if letter == "a" else None,
        )
        panel_letter(ax, letter)
    _source(sub, ["epsilon_ou", "epsilon_iu", "NTU_ou", "NTU_iu"], "fig03_epsilon_vs_plr.csv")
    _save(fig, "03_epsilon_vs_plr", mt="5%")


# ---------------------------------------------------------------------------
# 04  saturation temperatures
# ---------------------------------------------------------------------------
def fig04(d) -> None:
    sub = d[d.variant.isin(MAIN_VARIANTS)]
    fig, axes = plt.subplots(2, 2, figsize=dm.figsize("17cm", 0.68), gridspec_kw={"wspace": 0.32, "hspace": 0.45})
    spec = [
        ("heating", "T_evap_C", r"$T_{evap}$ [°C]", (-1, 3), 1),
        ("heating", "T_cond_C", r"$T_{cond}$ [°C]", (24, 30), 2),
        ("cooling", "T_evap_C", r"$T_{evap}$ [°C]", (18, 22), 1),
        ("cooling", "T_cond_C", r"$T_{cond}$ [°C]", (40, 44), 1),
    ]
    for ax, (duty, col, lab, ylim, ystep), letter in zip(axes.ravel(), spec, "abcd", strict=True):
        _panel(
            ax,
            sub,
            duty,
            col,
            lab,
            ylim,
            ystep,
            title=DUTY_TITLE[duty].split(",")[0],
            legend="lower left" if letter == "a" else None,
        )
        panel_letter(ax, letter)
    _source(sub, ["T_evap_C", "T_cond_C", "T_dis_C", "pr"], "fig04_saturation_temperature_vs_plr.csv")
    _save(fig, "04_saturation_temperature_vs_plr", mt="5%")


# ---------------------------------------------------------------------------
# 05  pressure ratio
# ---------------------------------------------------------------------------
def fig05(d) -> None:
    sub = d[d.variant.isin(MAIN_VARIANTS)]
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.40), gridspec_kw={"wspace": 0.30})
    lims = {"heating": ((1.8, 2.4), 0.1), "cooling": ((1.6, 2.0), 0.1)}
    for ax, duty, letter in zip(axes, ("heating", "cooling"), "ab", strict=True):
        ylim, ystep = lims[duty]
        _panel(
            ax,
            sub,
            duty,
            "pr",
            r"Pressure ratio $r_p$ [-]",
            ylim,
            ystep,
            title=DUTY_TITLE[duty],
            legend="lower right" if duty == "heating" else None,
        )
        panel_letter(ax, letter)
    _source(sub, ["pr", "T_evap_C", "T_cond_C"], "fig05_pressure_ratio_vs_plr.csv")
    _save(fig, "05_pressure_ratio_vs_plr", mt="6%")


# ---------------------------------------------------------------------------
# 06  system COP  (final core figure)
# ---------------------------------------------------------------------------
def fig06(d) -> None:
    sub = d[d.variant.isin(MAIN_VARIANTS)]
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("17cm", 0.42), gridspec_kw={"wspace": 0.30})
    lims = {"heating": ((5.0, 6.5), 0.25), "cooling": ((4.75, 6.5), 0.25)}
    for ax, duty, letter in zip(axes, ("heating", "cooling"), "ab", strict=True):
        ylim, ystep = lims[duty]
        _panel(
            ax,
            sub,
            duty,
            "cop_sys",
            r"System $\mathrm{COP}$ [-]",
            ylim,
            ystep,
            title=DUTY_TITLE[duty],
            legend="lower left" if duty == "heating" else None,
        )
        # Mark the lowest continuously modulating point.
        mod, _ = _split(sub[(sub.duty == duty) & (sub.variant == "baseline")])
        ax.axvline(100 * mod.plr_request.min(), color=COLORS["muted"], lw=HAIRLINE, ls=(0, (3, 2)), zorder=0)
        ax.text(
            100 * mod.plr_request.min() + 1.5,
            ylim[0] + 0.06 * (ylim[1] - ylim[0]),
            "compressor\nspeed floor",
            fontsize=dm.fs(-4),
            color=COLORS["muted"],
            va="bottom",
        )
        panel_letter(ax, letter)
    _source(sub, ["cop_sys", "E_tot", "E_cmp", "E_ou_fan", "E_iu_fan", "q_delivered_W"], "fig06_cop_vs_plr.csv")
    _save(fig, "06_cop_vs_plr", mt="6%")


# ---------------------------------------------------------------------------
# 07  parameter sensitivity heatmap
# ---------------------------------------------------------------------------
def _grid_metrics(d) -> pd.DataFrame:
    rows = []
    for (duty, f_ref, m_exp), g in d.groupby(["duty", "f_ref", "m_exp"]):
        mm = metrics_for(g)
        mod, _ = _split(g)
        low = mod.iloc[0]
        rows.append(
            {
                "duty": duty,
                "f_ref": f_ref,
                "m_exp": m_exp,
                "variant": g.variant.iat[0],
                "PLR_low": mm["PLR_low"],
                "COP_rated": mm["COP_rated"],
                "COP_peak": mm["COP_peak"],
                "PLR_peak": mm["PLR_peak"],
                "COP_low": mm["COP_low"],
                "shape": mm["shape"],
                "dCOP_low_pct": 100.0 * mm["dCOP_low"],
                "lowload_rise_pct": 100.0 * (mm["COP_low"] / mm["COP_rated"] - 1.0),
                "x_ref_low": float(low.x_ref),
                "ua_ratio_ou_low": float(low.ua_ratio_ou),
                "ua_ratio_iu_low": float(low.ua_ratio_iu),
                "T_evap_low": float(low.T_evap_C),
                "T_cond_low": float(low.T_cond_C),
                "pr_low": float(low.pr),
            }
        )
    return pd.DataFrame(rows)


def fig07(d) -> None:
    met = _grid_metrics(d)
    met.to_csv(OUT_DIR / "fig07_parameter_sensitivity_heatmap.csv", index=False)
    grid = met[met.f_ref.isin(F_REF_GRID) & met.m_exp.isin(M_GRID)]
    base = {r.duty: r for r in met[met.f_ref == 0].itertuples()}

    fig, axes = plt.subplots(2, 2, figsize=dm.figsize("16cm", 0.76), gridspec_kw={"wspace": 0.45, "hspace": 0.60})
    specs = [
        ("dCOP_low_pct", r"$\Delta\mathrm{COP}_{low}$ [%]", "magma_r", (0.0, 1.0)),
        ("lowload_rise_pct", "Low-load COP rise [%]", "viridis", None),
    ]
    for row, (col, lab, cmap, vlim) in enumerate(specs):
        for cidx, duty in enumerate(("heating", "cooling")):
            ax = axes[row, cidx]
            g = grid[grid.duty == duty]
            piv = g.pivot(index="m_exp", columns="f_ref", values=col).sort_index()
            vmin, vmax = vlim if vlim else (float(piv.values.min()), float(piv.values.max()))
            if vmax - vmin < 1e-9:
                vmax = vmin + 1.0
            im = ax.pcolormesh(
                np.arange(piv.shape[1] + 1),
                np.arange(piv.shape[0] + 1),
                piv.values,
                cmap=cmap,
                vmin=vmin,
                vmax=vmax,
            )
            for i in range(piv.shape[0]):
                for j in range(piv.shape[1]):
                    v = piv.values[i, j]
                    rel = (v - vmin) / (vmax - vmin) if vmax > vmin else 0.5
                    ax.text(
                        j + 0.5,
                        i + 0.5,
                        f"{v:.2f}",
                        ha="center",
                        va="center",
                        fontsize=dm.fs(-3),
                        color="white" if (cmap == "viridis" and rel < 0.55) or (cmap != "viridis" and rel > 0.55) else "black",
                    )
            ax.set_xticks(np.arange(piv.shape[1]) + 0.5)
            ax.set_xticklabels([f"{c:g}" for c in piv.columns])
            ax.set_yticks(np.arange(piv.shape[0]) + 0.5)
            ax.set_yticklabels([f"{r:g}" for r in piv.index])
            ax.set_xlabel(r"refrigerant-side resistance fraction $f_{ref}$ [-]")
            ax.set_ylabel(r"exponent $m$ [-]")
            b = base[duty]
            ref = b.dCOP_low_pct if col == "dCOP_low_pct" else b.lowload_rise_pct
            ax.set_title(
                f"{duty.capitalize()} — baseline {ref:.2f} %"
                + ("  (peak to lowest continuous PLR)" if col == "dCOP_low_pct" else "  (COP$_{low}$/COP$_{rated}$ − 1)"),
                fontsize=dm.fs(-2),
                loc="left",
            )
            cb = fig.colorbar(im, ax=ax, pad=0.03, fraction=0.05)
            cb.ax.tick_params(labelsize=dm.fs(-3.5))
            cb.locator = matplotlib.ticker.MaxNLocator(nbins=4)
            cb.update_ticks()
            cb.set_label(lab, fontsize=dm.fs(-3))
            panel_letter(ax, "abcd"[row * 2 + cidx], x=-0.22)
    _save(fig, "07_parameter_sensitivity_heatmap", mt="8%")
    return met


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()
    apply_style()
    d = _load()
    todo = {"01": fig01, "02": fig02, "03": fig03, "04": fig04, "05": fig05, "06": fig06, "07": fig07}
    for k, fn in todo.items():
        if a.only and k not in a.only:
            continue
        fn(d)
        print("wrote", k)


if __name__ == "__main__":
    main()
