"""Side-by-side P-h cycles for three refrigerants at the same duty.

Renders the converged refrigerant cycle on three small-multiples P-h
panels for R32, R290 (propane), and R134a — all at the same DHW
operating point. The shared y-axis lets the reader read off the
operating-pressure shift between refrigerants at a glance.

Operating point: ``T_tank_w = 55 °C``, ``T0 = 7 °C``, ``Q_ref_tank =
8 kW``. R290 in particular has much lower operating pressures than
R32 / R134a — useful intuition for refrigerant swap discussions.
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

REFRIGERANTS = ("R32", "R290", "R134a")
T_TANK_W = 55.0
T0       = 7.0
Q_COND   = 8_000


def _envelope(refrigerant: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    T_crit = CP.PropsSI("Tcrit", refrigerant)
    P_crit = CP.PropsSI("Pcrit", refrigerant) / 1_000
    T_grid = np.linspace(220.0, T_crit - 0.05, 200)
    h_liq = np.array([CP.PropsSI("H", "T", T, "Q", 0, refrigerant) for T in T_grid]) / 1_000
    h_vap = np.array([CP.PropsSI("H", "T", T, "Q", 1, refrigerant) for T in T_grid]) / 1_000
    p_sat = np.array([CP.PropsSI("P", "T", T, "Q", 0, refrigerant) for T in T_grid]) / 1_000
    h_crit = 0.5 * (h_liq[-1] + h_vap[-1])
    return (
        np.append(h_liq, h_crit),
        np.append(h_vap, h_crit),
        np.append(p_sat, P_crit),
    )


CYCLE_PATH = ["1*", "1", "2", "2*", "3*", "3", "4", "1*"]


def _cycle_points(result: dict) -> dict[str, tuple[float, float]]:
    def hp(h, p):
        return result[h] / 1_000, result[p] / 1_000
    return {
        "1*": hp("h_ref_evap_sat [J/kg]",    "P_ref_evap_sat [Pa]"),
        "1":  hp("h_ref_cmp_in [J/kg]",      "P_ref_cmp_in [Pa]"),
        "2":  hp("h_ref_cmp_out [J/kg]",     "P_ref_cmp_out [Pa]"),
        "2*": hp("h_ref_cond_sat_v [J/kg]",  "P_ref_cond_sat_v [Pa]"),
        "3*": hp("h_ref_cond_sat_l [J/kg]",  "P_ref_cond_sat_l [Pa]"),
        "3":  hp("h_ref_exp_in [J/kg]",      "P_ref_exp_in [Pa]"),
        "4":  hp("h_ref_exp_out [J/kg]",     "P_ref_exp_out [Pa]"),
    }


def main() -> None:
    apply_style("report", hashsalt="tmhp.visualization.refrigerant-compare-ph")

    fig, axes = plt.subplots(
        1, 3,
        figsize=dm.figsize("17cm", 5 / 12),
        sharey=True,
    )

    for ax, ref in zip(axes, REFRIGERANTS, strict=True):
        h_liq, h_vap, p_sat = _envelope(ref)
        ax.plot(h_liq, p_sat, color=COLORS["cool"], linewidth=dm.lw(1))
        ax.plot(h_vap, p_sat, color=COLORS["hot"],  linewidth=dm.lw(1))

        ashpb = AirSourceHeatPumpBoiler(ref=ref)
        res = ashpb.analyze_steady(T_tank_w=T_TANK_W, T0=T0, Q_ref_tank=Q_COND)
        assert isinstance(res, dict)
        pts = _cycle_points(res)

        xs = [pts[k][0] for k in CYCLE_PATH]
        ys = [pts[k][1] for k in CYCLE_PATH]
        ax.plot(xs, ys, color=COLORS["ink"], linewidth=dm.lw(0),
                linestyle=(0, (2, 2)))
        # Mark cmp,out + exp,out so the reader can pick out the high
        # / low pressure plateaus without label clutter.
        for key in ("1", "2", "3", "4"):
            x, y = pts[key]
            ax.plot(x, y, marker="o", markersize=3,
                    markerfacecolor=COLORS["ink"], markeredgecolor=COLORS["ink"],
                    linestyle="None")

        cop = float(res.get("cop_sys [-]", float("nan")))
        ax.set_title(f"{ref}\nCOP = {cop:.2f}", fontsize=dm.fs(0))
        ax.set_yscale("log")
        ax.set_xlim(0, 750)
        ax.set_ylim(1e2, 1e4)
        ax.set_xlabel("Enthalpy [kJ/kg]")
        ax.grid(True, which="both", alpha=0.25, linewidth=dm.lw(-2))

    axes[0].set_ylabel("Pressure [kPa]")

    # Single-column legend tucked into the lower-right of the rightmost
    # panel (R134a) — that quadrant is in the superheated-vapor region
    # so it sits clear of the dome and the cycle path.
    h_liq_handle, = axes[-1].plot([], [], color=COLORS["cool"],
                                   linewidth=dm.lw(1), label="Sat. liquid")
    h_vap_handle, = axes[-1].plot([], [], color=COLORS["hot"],
                                   linewidth=dm.lw(1), label="Sat. vapor")
    h_cyc_handle, = axes[-1].plot([], [], color=COLORS["ink"],
                                   linewidth=dm.lw(0),
                                   linestyle=(0, (2, 2)), label="Ref. cycle")
    axes[-1].legend(
        handles=[h_liq_handle, h_vap_handle, h_cyc_handle],
        loc="lower right", frameon=False,
        fontsize=dm.fs(-2),
    )

    out = static_path("refrigerant_compare_ph.svg").with_suffix("")
    finalize(fig, out, margin="3%")
    plt.close(fig)
    print(f"wrote {out}.svg")


if __name__ == "__main__":
    main()
