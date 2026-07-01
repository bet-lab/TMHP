"""T-s (temperature-entropy) diagram for R32 at the same operating point
as the P-h cycle figure.

The T-s view makes the isentropic-compression assumption (vertical 1→2)
and the irreversible expansion (4 sits below 3 on s) visually obvious.
This is the complement to ``mollier_cycle_R32.py`` for readers who think
in entropy rather than enthalpy.

Operating point: ``T_tank_w = 60 °C``, ``T0 = 12 °C``, ``Q_ref_tank =
8 kW``. Same as the P-h figure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import CoolProp.CoolProp as CP
import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np

from tmhp import AirSourceHeatPumpBoiler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dmpl_common import COLORS, apply_style, finalize, static_path  # noqa: E402

REF = "R32"
T_TANK_W = 60.0
T0 = 12.0
Q_COND = 8_000


def _envelope_ts(refrigerant: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Saturation envelope on a T-s plane: (s_liq, s_vap, T_C)."""
    T_crit = CP.PropsSI("Tcrit", refrigerant)
    T_grid_K = np.linspace(220.0, T_crit - 0.05, 200)
    s_liq = np.array([CP.PropsSI("S", "T", T, "Q", 0, refrigerant) for T in T_grid_K]) / 1_000
    s_vap = np.array([CP.PropsSI("S", "T", T, "Q", 1, refrigerant) for T in T_grid_K]) / 1_000
    T_C = T_grid_K - 273.15
    s_crit = 0.5 * (s_liq[-1] + s_vap[-1])
    return (
        np.append(s_liq, s_crit),
        np.append(s_vap, s_crit),
        np.append(T_C, T_crit - 273.15),
    )


CYCLE_PATH = ["1*", "1", "2", "2*", "3*", "3", "4", "1*"]
CLOSED = ["1", "2", "3", "4"]
OPEN = ["1*", "2*", "3*"]
NODE_LABEL = {"1": "cmp,in", "2": "cmp,out", "3": "exp,in", "4": "exp,out"}


def _cycle_ts(result: dict) -> dict[str, tuple[float, float]]:
    def st(s_key: str, t_key: str) -> tuple[float, float]:
        return result[s_key] / 1_000, result[t_key]

    return {
        "1*": st("s_ref_evap_sat [J/(kg·K)]", "T_ref_evap_sat [°C]"),
        "1": st("s_ref_cmp_in [J/(kg·K)]", "T_ref_cmp_in [°C]"),
        "2": st("s_ref_cmp_out [J/(kg·K)]", "T_ref_cmp_out [°C]"),
        "2*": st("s_ref_cond_sat_v [J/(kg·K)]", "T_ref_cond_sat_v [°C]"),
        "3*": st("s_ref_cond_sat_l [J/(kg·K)]", "T_ref_cond_sat_l [°C]"),
        "3": st("s_ref_exp_in [J/(kg·K)]", "T_ref_exp_in [°C]"),
        "4": st("s_ref_exp_out [J/(kg·K)]", "T_ref_exp_out [°C]"),
    }


def main() -> None:
    apply_style("report", hashsalt="tmhp.visualization.mollier-ts-r32")

    ashpb = AirSourceHeatPumpBoiler(ref=REF)
    res = ashpb.analyze_steady(T_tank_w=T_TANK_W, T0=T0, Q_ref_tank=Q_COND)
    assert isinstance(res, dict)
    pts = _cycle_ts(res)
    s_liq, s_vap, T_sat = _envelope_ts(REF)

    fig, ax = plt.subplots(figsize=dm.figsize("13cm", "standard"))

    ax.plot(s_liq, T_sat, color=COLORS["cool"], linewidth=dm.lw(1), label="Sat. liquid")
    ax.plot(s_vap, T_sat, color=COLORS["hot"], linewidth=dm.lw(1), label="Sat. vapor")

    xs = [pts[k][0] for k in CYCLE_PATH]
    ys = [pts[k][1] for k in CYCLE_PATH]
    ax.plot(xs, ys, color=COLORS["ink"], linewidth=dm.lw(0), linestyle=(0, (2, 2)), zorder=2, label="Ref. cycle")

    for k in OPEN:
        x, y = pts[k]
        ax.plot(
            x,
            y,
            marker="o",
            markersize=4,
            markerfacecolor="white",
            markeredgecolor=COLORS["ink"],
            markeredgewidth=dm.lw(0),
            linestyle="None",
            zorder=3,
        )
    label_offsets = {"1": (8, -6), "2": (8, -12), "3": (-30, 8), "4": (-30, -14)}
    for k in CLOSED:
        x, y = pts[k]
        ax.plot(
            x,
            y,
            marker="o",
            markersize=4,
            markerfacecolor=COLORS["ink"],
            markeredgecolor=COLORS["ink"],
            linestyle="None",
            zorder=3,
        )
        dx, dy = label_offsets[k]
        ax.annotate(
            NODE_LABEL[k], (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=dm.fs(-1), color=COLORS["ink"]
        )

    # Reference temperature lines: tank water + ambient.
    ax.axhline(T_TANK_W, color=COLORS["hot"], linewidth=dm.lw(0), linestyle=(0, (3, 3)), alpha=0.7)
    ax.text(2.35, T_TANK_W + 3, f"Tank water: {T_TANK_W:.0f} °C", color=COLORS["hot"], fontsize=dm.fs(-1), ha="right")
    ax.axhline(T0, color=COLORS["warm"], linewidth=dm.lw(0), linestyle=(0, (3, 3)), alpha=0.7)
    ax.text(2.35, T0 - 9, f"Outdoor air: {T0:.0f} °C", color=COLORS["warm"], fontsize=dm.fs(-1), ha="right")

    ax.set_xlim(0.8, 2.4)
    ax.set_ylim(-30, 160)
    ax.set_xlabel("Entropy [kJ/(kg·K)]")
    ax.set_ylabel("Temperature [°C]")
    ax.grid(True, alpha=0.25, linewidth=dm.lw(-2))
    ax.legend(loc="upper left", frameon=False, ncol=3, fontsize=dm.fs(-1))

    out = static_path("mollier_ts_R32.svg").with_suffix("")
    finalize(fig, out)
    plt.close(fig)
    print(f"wrote {out}.svg")


if __name__ == "__main__":
    main()
