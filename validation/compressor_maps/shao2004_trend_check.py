"""Redraw Shao et al. (2004) Figs. 1-3 with the shipped TMHP compressor block.

The paper's Figs. 1-3 are the *manufacturer's* calorimeter performance curves of
one rolling-piston rotary inverter compressor (Mitsubishi RHV207FEM, 20.7 cm3),
re-fitted by the authors to Eqs. (1)-(2) with the per-frequency coefficients of
Table 1: cooling capacity, refrigerant mass flow and motor power input against
evaporation temperature, for three condensing temperatures and four supply
frequencies, at the map condition (11 K return-gas superheat, 8.3 K liquid
subcooling, 35 degC ambient).

This module evaluates the same three quantities from the TMHP defaults --
displacement and rated speed of the same machine, nothing else fitted to it --
and plots both on the paper's own axes::

    m_dot = rho_suc V N eta_vol(PR, n*)
    P_el  = m_dot dh_is / (eta_isen(PR) eta_em(n*))
    Q_evap = m_dot (h_suc - h_liquid at Tc - 8.3 K)

The paper fits no capacity polynomial -- its Fig. 1 is the fitted mass flow
times the map-condition enthalpy difference -- so the same enthalpy difference
is applied to the printed mass flow here.  That enthalpy difference is a pure
CoolProp quantity at the map condition, identical for both curves, so the
capacity deviation is by construction the mass-flow deviation: panels (d) and
(e) coincide, and (d) is kept only so each deviation sits under the quantity it
belongs to.  The bottom row separates a *level* offset (the correlations
describe a different population of machines) from a *trend* error (they bend the
wrong way in Te, Tc or speed); the COP deviation, which is not a panel, is
printed by ``main``.

Line encoding, both rows: colour = supply frequency, solid = the manufacturer's
map, dash-dot = TMHP.  One condensing temperature is drawn (50 degC, the middle
of the paper's three); the printed statistics still run over all three.

Run::

    uv run python3 -m validation.compressor_maps.shao2004_trend_check
"""

from __future__ import annotations

import argparse
from pathlib import Path

import CoolProp.CoolProp as CP
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

from tmhp.compressor_efficiency import (  # noqa: E402
    COEFFICIENT_VERSION,
    eta_isen_default,
    make_eta_em,
    make_eta_vol,
)
from validation.compressor_maps.parse.shao2004 import MITSU_TABLE1, SC_K, SH_K, _quad  # noqa: E402
from validation.compressor_maps.schema import DATA_DIR, REPO_ROOT  # noqa: E402

FLUID = "R22"
V_DISP_M3 = 20.7e-6  # rolling-piston displacement [m3/rev]
RPS_RATED = 60.0  # base frequency of the machine; two-pole motor, so 1 Hz = 1 rev/s
TE = np.arange(-10.0, 15.01, 0.5)
TC_GRID = (40.0, 50.0, 60.0)
FREQ_STYLE = {
    30.0: (COLORS["ess"], "30 Hz  (n* 0.5)"),
    60.0: (COLORS["accent"], "60 Hz  (n* 1.0)"),
    90.0: (COLORS["warm"], "90 Hz  (n* 1.5)"),
    120.0: (COLORS["hot"], "120 Hz (n* 2.0)"),
}
# The deviation statistics run over the whole (Te, Tc, f) grid, but the figure
# draws one condensing temperature only: with three of them the dash pattern had
# to carry Tc, which left nothing to separate the manufacturer's curve from the
# model's. At a single Tc the dash pattern is free to do that job -- solid for
# the map, dash-dot for TMHP -- and colour still carries the supply frequency.
TC_PLOT = 50.0
PAPER_DASH = "solid"
MODEL_DASH = (0, (5.0, 1.3, 1.0, 1.3))  # dash-dot
LW = -0.5  # one relative width for every curve: manufacturer and model alike


def tmhp_point(te_c: float, tc_c: float, rps: float) -> tuple[float, float, float]:
    """Mass flow [kg/s], electrical power [W] and cooling capacity [W] from the defaults."""
    p_suc = CP.PropsSI("P", "T", te_c + 273.15, "Q", 1, FLUID)
    p_dis = CP.PropsSI("P", "T", tc_c + 273.15, "Q", 1, FLUID)
    t_suc = te_c + SH_K + 273.15
    rho_suc = CP.PropsSI("D", "P", p_suc, "T", t_suc, FLUID)
    h_suc = CP.PropsSI("H", "P", p_suc, "T", t_suc, FLUID)
    s_suc = CP.PropsSI("S", "P", p_suc, "T", t_suc, FLUID)
    dh_is = CP.PropsSI("H", "P", p_dis, "S", s_suc, FLUID) - h_suc
    h_liq = CP.PropsSI("H", "P", p_dis, "T", tc_c - SC_K + 273.15, FLUID)

    pr = p_dis / p_suc
    m_dot = rho_suc * V_DISP_M3 * rps * make_eta_vol(RPS_RATED)(pr, rps)
    p_el = m_dot * dh_is / (eta_isen_default(pr) * make_eta_em(RPS_RATED)(pr, rps))
    return m_dot, p_el, m_dot * (h_suc - h_liq)


def build() -> pd.DataFrame:
    rows = []
    for f, (a, b) in MITSU_TABLE1.items():
        for tc in TC_GRID:
            for te in TE:
                m_paper_kgh = _quad(a, tc, te)
                p_paper_w = _quad(b, tc, te)
                m_model, p_model, q_model = tmhp_point(te, tc, f)
                # the paper prints no capacity polynomial: its Fig. 1 is m_dot x (h_suc - h_liq),
                # so the same enthalpy difference is applied to the printed mass flow
                q_paper_w = q_model * (m_paper_kgh / 3600.0) / m_model
                rows.append(
                    {
                        "f_Hz": f,
                        "n_star": f / RPS_RATED,
                        "T_cond_C": tc,
                        "T_evap_C": te,
                        "m_paper_kgh": m_paper_kgh,
                        "m_model_kgh": m_model * 3600.0,
                        "P_paper_W": p_paper_w,
                        "P_model_W": p_model,
                        "Q_paper_W": q_paper_w,
                        "Q_model_W": q_model,
                        "COP_paper": q_paper_w / p_paper_w,
                        "COP_model": q_model / p_model,
                    }
                )
    df = pd.DataFrame(rows)
    for q, col, mod in (
        ("m", "m_paper_kgh", "m_model_kgh"),
        ("P", "P_paper_W", "P_model_W"),
        ("Q", "Q_paper_W", "Q_model_W"),
        ("COP", "COP_paper", "COP_model"),
    ):
        df[f"dev_{q}_pct"] = (df[mod] / df[col] - 1.0) * 100.0
    return df


def _curve(df: pd.DataFrame, f: float) -> pd.DataFrame:
    return df[(df.f_Hz == f) & (df.T_cond_C == TC_PLOT)].sort_values("T_evap_C")


def _band(ax, df: pd.DataFrame, paper_col: str, model_col: str, scale: float) -> None:
    for f, (color, _label) in FREQ_STYLE.items():
        d = _curve(df, f)
        ax.plot(d.T_evap_C, d[paper_col] * scale, color=color, lw=dm.lw(LW), ls=PAPER_DASH, alpha=0.9)
        ax.plot(d.T_evap_C, d[model_col] * scale, color=color, lw=dm.lw(LW), ls=MODEL_DASH, alpha=0.9)


def _dev(ax, df: pd.DataFrame, col: str) -> None:
    ax.axhline(0.0, color=COLORS["ink"], lw=HAIRLINE)
    for f, (color, _label) in FREQ_STYLE.items():
        d = _curve(df, f)
        ax.plot(d.T_evap_C, d[col], color=color, lw=dm.lw(LW), ls=MODEL_DASH)


def figure(df: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(2, 3, figsize=dm.figsize("17cm", 0.62), gridspec_kw={"wspace": 0.42, "hspace": 0.45})
    top = (
        ("Q_paper_W", "Q_model_W", 1e-3, "Cooling capacity [kW]", 0.0, 14.0, 2.0, "a", "Fig. 1"),
        ("m_paper_kgh", "m_model_kgh", 1.0, "Refrigerant mass flow [kg/h]", 0.0, 280.0, 40.0, "b", "Fig. 2"),
        ("P_paper_W", "P_model_W", 1e-3, "Motor power input [kW]", 0.0, 4.5, 1.0, "c", "Fig. 3"),
    )
    for ax, (pcol, mcol, scale, lab, lo, hi, step, letter, src) in zip(axes[0], top, strict=True):
        _band(ax, df, pcol, mcol, scale)
        ax.set_ylim(lo, hi)
        ax.set_yticks(ticks(lo, hi, step))
        ax.set_ylabel(lab)
        ax.set_title(f"Shao 2004 {src}", loc="left", fontsize=dm.fs(-1.5))
        ax.set_xlim(-10, 15)
        ax.set_xticks(ticks(-10, 15, 5))
        ax.set_xlabel("Evaporation temperature [°C]")
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
        panel_letter(ax, letter, x=-0.26)

    # each deviation sits under the quantity it belongs to: (d)|(a), (e)|(b), (f)|(c)
    dev = (
        ("dev_Q_pct", "Cooling-capacity deviation [%]", "d", "= (e) by construction"),
        ("dev_m_pct", "Mass-flow deviation [%]", "e", ""),
        ("dev_P_pct", "Power deviation [%]", "f", ""),
    )
    for ax, (col, lab, letter, note) in zip(axes[1], dev, strict=True):
        _dev(ax, df, col)
        if note:
            ax.text(
                0.5,
                0.04,
                note,
                transform=ax.transAxes,
                ha="center",
                va="bottom",
                fontsize=dm.fs(-3.5),
                color=COLORS["ink"],
            )
        drawn = df[df.T_cond_C == TC_PLOT][col]
        span = float(np.ceil(max(abs(drawn.min()), abs(drawn.max())) / 10.0) * 10.0)
        ax.set_ylim(-span, span)
        ax.set_yticks(ticks(-span, span, span / 2.0))
        ax.set_ylabel(lab)
        ax.set_xlim(-10, 15)
        ax.set_xticks(ticks(-10, 15, 5))
        ax.set_xlabel("Evaporation temperature [°C]")
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
        panel_letter(ax, letter, x=-0.26)

    # one legend for the whole figure: the encoding is shared by all six panels,
    # so it sits above them rather than eating plot area in one of them. ncol=3
    # fills column-major -- frequencies in the first two columns, source in the
    # third -- which keeps the block two rows tall and narrower than the canvas.
    handles = [plt.Line2D([], [], color=c, lw=dm.lw(LW), label=lab) for c, lab in FREQ_STYLE.values()]
    handles += [
        plt.Line2D([], [], color=COLORS["ink"], lw=dm.lw(LW), ls=PAPER_DASH, label="manufacturer map (paper)"),
        plt.Line2D(
            [],
            [],
            color=COLORS["ink"],
            lw=dm.lw(LW),
            ls=MODEL_DASH,
            label=f"TMHP defaults {COEFFICIENT_VERSION}",
        ),
    ]
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
        ncol=3,
        frameon=False,
        fontsize=dm.fs(-3.5),
        labelspacing=0.25,
        columnspacing=1.6,
        handletextpad=0.4,
        title=f"condensing temperature {TC_PLOT:g} °C",
        title_fontsize=dm.fs(-3.5),
    )
    out.mkdir(parents=True, exist_ok=True)
    finalize(fig, out / "F12_shao2004_map_trend", formats=("svg", "png"), mt="10%")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO_ROOT / "validation" / "coefficients" / COEFFICIENT_VERSION / "figures"))
    a = ap.parse_args()
    apply_style("scientific")
    df = build()
    figure(df, Path(a.out))
    csv = DATA_DIR / "shao2004_trend_check.csv"
    df.to_csv(csv, index=False)

    pd.set_option("display.width", 200)
    print(f"TMHP {COEFFICIENT_VERSION} vs Shao 2004 manufacturer map (Mitsubishi RHV207FEM, {FLUID})")
    cols = ["dev_m_pct", "dev_P_pct", "dev_COP_pct"]
    print(df.groupby("f_Hz")[cols].agg(["mean", "min", "max"]).round(1).to_string())
    print("\nby condensing temperature")
    print(df.groupby("T_cond_C")[cols].mean().round(1).to_string())
    print("\nby evaporation temperature")
    print(df.groupby("T_evap_C")[cols].mean().round(1).iloc[::5].to_string())
    print(f"\n-> {csv}")


if __name__ == "__main__":
    main()
