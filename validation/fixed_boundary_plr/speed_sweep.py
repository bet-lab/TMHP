"""COP against compressor speed at fixed boundary temperatures.

Structurally this is Fig. 7 of Shao et al. (2004),
doi:10.1016/j.ijrefrig.2004.02.008 -- COP on the ordinate, compressor speed on
the abscissa, one curve per operating condition -- drawn for TMHP instead of
for a manufacturer map, and three times over:

``(a) compressor block``
    ``COP = eta_oi(PR, n*) (h_suc - h_liq) / dh_is`` at fixed saturation
    temperatures.  The correlations make the speed dependence a single shared
    factor ``s(n*)``, so the nine curves are parallel and none of them has an
    optimum: whatever optimal speed a heat pump shows is not in the compressor
    block.

``(b) air-to-water, (c) air-to-air``
    The assembled models at fixed boundary temperatures.  Speed cannot be
    prescribed -- the models solve it from the requested duty -- so the duty is
    swept and the speed the solver returns is used as the abscissa, which is
    the same curve read the other way round.  Rows where the solver hit the
    speed floor or ceiling are dropped: there the abscissa stops moving.

Output: ``validation/data/fixed_boundary_plr/speed_sweep.csv`` and the figure
``F9_cop_vs_speed`` next to the coefficient archive.

Run::

    uv run python3 -m validation.fixed_boundary_plr.speed_sweep
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

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler  # noqa: E402
from tmhp.compressor_efficiency import (  # noqa: E402
    COEFFICIENT_VERSION,
    eta_isen_default,
    make_eta_em,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "data" / "fixed_boundary_plr"

# --- panel (a): the compressor block on its own -----------------------------
CMP_REF = "R32"
CMP_SH_K, CMP_SC_K = 5.0, 5.0
CMP_RPS_RATED = 40.0  # air-to-water rated speed; the abscissa is n*, so the value only sets the grid
CMP_TE = (-5.0, 0.0, 7.0)
CMP_TC = (40.0, 50.0, 60.0)
N_STAR = np.arange(0.35, 2.51, 0.025)

# --- panels (b, c): the assembled models ------------------------------------
A2W_CAPACITY, A2W_REF = 9000.0, "R32"
A2W_OUTDOOR = (-7.0, 2.0, 7.0)
A2W_TANK = (35.0, 45.0, 55.0)
A2W_FRACTIONS = tuple(round(0.30 + 0.075 * i, 3) for i in range(29))  # 0.30 .. 2.40

A2A_CAPACITY, A2A_REF = 3500.0, "R32"
A2A_OUTDOOR = (30.0, 35.0, 40.0)
A2A_ROOM = (24.0, 27.0, 30.0)
A2A_FRACTIONS = tuple(round(0.20 + 0.075 * i, 3) for i in range(28))  # 0.20 .. 2.23

HOT_COLOR = (COLORS["cool"], COLORS["warm"], COLORS["hot"])  # rising hot-side level
COLD_DASH = ((0, (1.2, 1.2)), (0, (4.0, 1.6)), "solid")  # rising cold-side level


def compressor_block() -> pd.DataFrame:
    """Panel (a): the three correlations evaluated at fixed saturation temperatures."""
    eta_em = make_eta_em(CMP_RPS_RATED)
    rows = []
    for tc in CMP_TC:
        p_dis = CP.PropsSI("P", "T", tc + 273.15, "Q", 1, CMP_REF)
        h_liq = CP.PropsSI("H", "P", p_dis, "T", tc - CMP_SC_K + 273.15, CMP_REF)
        for te in CMP_TE:
            p_suc = CP.PropsSI("P", "T", te + 273.15, "Q", 1, CMP_REF)
            t_suc = te + CMP_SH_K + 273.15
            h_suc = CP.PropsSI("H", "P", p_suc, "T", t_suc, CMP_REF)
            s_suc = CP.PropsSI("S", "P", p_suc, "T", t_suc, CMP_REF)
            dh_is = CP.PropsSI("H", "P", p_dis, "S", s_suc, CMP_REF) - h_suc
            pr = p_dis / p_suc
            for n in N_STAR:
                eta_oi = eta_isen_default(pr) * eta_em(pr, n * CMP_RPS_RATED)
                rows.append(
                    {
                        "panel": "compressor",
                        "hot_C": tc,
                        "cold_C": te,
                        "n_star": n,
                        "pr": pr,
                        "cop": eta_oi * (h_suc - h_liq) / dh_is,
                    }
                )
    return pd.DataFrame(rows)


def air_to_water() -> pd.DataFrame:
    rows = []
    for tank in A2W_TANK:
        for outdoor in A2W_OUTDOOR:
            model = AirSourceHeatPumpBoiler(hp_capacity=A2W_CAPACITY, ref=A2W_REF)
            for f in A2W_FRACTIONS:
                r = model.analyze_steady(T_tank_w=tank, T0=outdoor, Q_ref_tank=A2W_CAPACITY * f, return_dict=True)
                assert isinstance(r, dict)
                rows.append(
                    {
                        "panel": "air_to_water",
                        "hot_C": tank,
                        "cold_C": outdoor,
                        "plr_request": f,
                        "n_star": float(r.get("n_star [-]", float("nan"))),
                        "pr": float(r.get("pr_cmp [-]", float("nan"))),
                        "cop": float(r.get("cop_sys [-]", float("nan"))),
                        "q_delivered_W": float(r.get("Q_ref_tank [W]", float("nan"))),
                        "capacity_clamped": r.get("capacity_clamped"),
                        "failure_reason": r.get("failure_reason", "none"),
                    }
                )
    return pd.DataFrame(rows)


def air_to_air() -> pd.DataFrame:
    rows = []
    for outdoor in A2A_OUTDOOR:
        for room in A2A_ROOM:
            model = AirSourceHeatPump(hp_capacity=A2A_CAPACITY, ref=A2A_REF)
            for f in A2A_FRACTIONS:
                r = model.analyze_steady(
                    Q_r_iu=A2A_CAPACITY * f, T0=outdoor, T_a_room=room, return_dict=True, verbose=False
                )
                assert isinstance(r, dict)
                rows.append(
                    {
                        "panel": "air_to_air",
                        "hot_C": outdoor,
                        "cold_C": room,
                        "plr_request": f,
                        "n_star": float(r.get("n_star [-]", float("nan"))),
                        "pr": float(r.get("pr_cmp [-]", float("nan"))),
                        "cop": float(r.get("cop_sys [-]", float("nan"))),
                        "q_delivered_W": abs(float(r.get("Q_ref_iu [W]", float("nan")))),
                        "capacity_clamped": r.get("capacity_clamped"),
                        "failure_reason": r.get("failure_reason", "none"),
                    }
                )
    return pd.DataFrame(rows)


def _modulating(df: pd.DataFrame) -> pd.DataFrame:
    """Only the rows where speed is a free variable -- outside them the abscissa stops moving."""
    ok = df[(df.failure_reason == "none") & df.capacity_clamped.isna() & df.cop.notna()]
    return ok[np.isfinite(ok.n_star)]


def _panel(ax, df: pd.DataFrame, hots: tuple, colds: tuple, hot_fmt: str, cold_fmt: str) -> None:
    for tc, color in zip(hots, HOT_COLOR, strict=True):
        for te, dash in zip(colds, COLD_DASH, strict=True):
            d = df[(df.hot_C == tc) & (df.cold_C == te)].sort_values("n_star")
            if len(d) < 2:
                continue
            ax.plot(d.n_star, d.cop, color=color, lw=dm.lw(0), ls=dash)
            peak = d.loc[d.cop.idxmax()]
            if d.n_star.min() < peak.n_star < d.n_star.max():
                ax.plot(peak.n_star, peak.cop, "o", ms=dm.fs(-3), color=color, mec="white", mew=HAIRLINE)
    handles = [
        plt.Line2D([], [], color=c, lw=dm.lw(0), label=hot_fmt.format(t)) for t, c in zip(hots, HOT_COLOR, strict=True)
    ]
    handles += [
        plt.Line2D([], [], color=COLORS["muted"], lw=dm.lw(0), ls=d, label=cold_fmt.format(t))
        for t, d in zip(colds, COLD_DASH, strict=True)
    ]
    ax.legend(
        handles=handles,
        loc="lower center",
        ncol=2,
        frameon=False,
        fontsize=dm.fs(-3.5),
        labelspacing=0.15,
        handletextpad=0.4,
        columnspacing=0.9,
        borderaxespad=0.1,
    )


def figure(cmp_df: pd.DataFrame, a2w: pd.DataFrame, a2a: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=dm.figsize("17cm", 0.40), gridspec_kw={"wspace": 0.34})

    ax = axes[0]
    _panel(ax, cmp_df, CMP_TC, CMP_TE, "T_cond {:g} °C", "T_evap {:g} °C")
    ax.set_ylim(0, 6)
    ax.set_yticks(ticks(0, 6, 1.5))
    ax.set_ylabel("Compressor COP [-]")
    ax.set_title(f"compressor block only, {CMP_REF}", loc="left", fontsize=dm.fs(-1.5))
    panel_letter(ax, "a", x=-0.28)

    ax = axes[1]
    _panel(ax, _modulating(a2w), A2W_TANK, A2W_OUTDOOR, "tank {:g} °C", "outdoor {:g} °C")
    ax.set_ylim(0, 6)
    ax.set_yticks(ticks(0, 6, 1.5))
    ax.set_ylabel("System COP [-]")
    ax.set_title(f"air-to-water {A2W_CAPACITY / 1000:g} kW {A2W_REF}, heating", loc="left", fontsize=dm.fs(-1.5))
    panel_letter(ax, "b", x=-0.28)

    ax = axes[2]
    _panel(ax, _modulating(a2a), A2A_OUTDOOR, A2A_ROOM, "outdoor {:g} °C", "room {:g} °C")
    ax.set_ylim(0, 9)
    ax.set_yticks(ticks(0, 9, 3))
    ax.set_ylabel("System EER [-]")
    ax.set_title(f"air-to-air {A2A_CAPACITY / 1000:g} kW {A2A_REF}, cooling", loc="left", fontsize=dm.fs(-1.5))
    panel_letter(ax, "c", x=-0.28)

    for ax in axes:
        ax.axvline(1.0, color=COLORS["muted"], lw=HAIRLINE, ls=(0, (1.0, 2.0)))
        ax.set_xlim(0.2, 2.6)
        ax.set_xticks(ticks(0.5, 2.5, 0.5))
        ax.set_xlabel("Relative speed n* = N / N_rated [-]")
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)

    out.mkdir(parents=True, exist_ok=True)
    finalize(fig, out / "F9_cop_vs_speed", formats=("svg", "png"), mt="6%")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO_ROOT / "validation" / "coefficients" / COEFFICIENT_VERSION / "figures"))
    a = ap.parse_args()
    apply_style("scientific")

    cmp_df = compressor_block()
    a2w = air_to_water()
    a2a = air_to_air()
    figure(cmp_df, a2w, a2a, Path(a.out))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = OUT_DIR / "speed_sweep.csv"
    frames = [cmp_df.assign(coefficient_version=COEFFICIENT_VERSION)]
    frames += [d.assign(coefficient_version=COEFFICIENT_VERSION) for d in (a2w, a2a)]
    pd.concat(frames, ignore_index=True).to_csv(out_csv, index=False)

    for name, df in (("air-to-water", a2w), ("air-to-air", a2a)):
        mod = _modulating(df)
        print(f"{name}: {len(df)} runs, {len(mod)} modulating; n* {mod.n_star.min():.2f}-{mod.n_star.max():.2f}")
        span = mod.groupby(["hot_C", "cold_C"]).n_star.agg(["min", "max"])
        peaks = mod.loc[mod.groupby(["hot_C", "cold_C"]).cop.idxmax()].set_index(["hot_C", "cold_C"])
        interior = ((peaks.n_star > span["min"] + 1e-6) & (peaks.n_star < span["max"] - 1e-6)).sum()
        print(f"   COP peak strictly inside each curve's own speed range: {interior}/{len(peaks)} conditions")
        print(peaks.join(span, rsuffix="_range")[["min", "max", "n_star", "cop"]].round(2).to_string())
    print(f"\n-> {out_csv}")


if __name__ == "__main__":
    main()
