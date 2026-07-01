"""Two-panel R32 refrigerant-cycle diagram embedded in the docs tutorial.

Panel (a) is the P-h chart; panel (b) is the T-h companion. Both share
the seven cycle nodes returned by ``analyze_steady`` at a realistic DHW
operating point (``T_tank_w = 60 °C``, ``T0 = 12 °C``, ``Q_ref_tank =
8 kW``).

Rendered through the ``scientific`` dartwork-mpl preset for typography
and palette consistency with the rest of the docs gallery.
"""

from __future__ import annotations

from pathlib import Path

import CoolProp.CoolProp as CP
import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np
from _dmpl_common import COLORS, apply_style, finalize, panel_letter, static_path

from tmhp import AirSourceHeatPumpBoiler

REF = "R32"
T_TANK_W = 60.0  # °C
T0 = 12.0  # °C
Q_COND = 8_000  # W

OUTPUT_STEM: Path = static_path("mollier_cycle_R32.svg").with_suffix("")


def _saturation_envelope_ph(refrigerant: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Liquid + vapour saturation curves on a P-h chart, in (kJ/kg, kPa)."""
    T_crit = CP.PropsSI("Tcrit", refrigerant)
    P_crit = CP.PropsSI("Pcrit", refrigerant) / 1_000  # kPa
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


def _saturation_envelope_th(refrigerant: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Liquid + vapour saturation curves on a T-h chart, in (kJ/kg, °C)."""
    T_crit = CP.PropsSI("Tcrit", refrigerant)
    T_grid_K = np.linspace(220.0, T_crit - 0.05, 200)
    h_liq = np.array([CP.PropsSI("H", "T", T, "Q", 0, refrigerant) for T in T_grid_K]) / 1_000
    h_vap = np.array([CP.PropsSI("H", "T", T, "Q", 1, refrigerant) for T in T_grid_K]) / 1_000
    T_C = T_grid_K - 273.15
    h_crit = 0.5 * (h_liq[-1] + h_vap[-1])
    return (
        np.append(h_liq, h_crit),
        np.append(h_vap, h_crit),
        np.append(T_C, T_crit - 273.15),
    )


def _cycle_nodes_ph(r: dict[str, float]) -> dict[str, tuple[float, float]]:
    def hp(h_key: str, p_key: str) -> tuple[float, float]:
        return r[h_key] / 1_000, r[p_key] / 1_000

    return {
        "1*": hp("h_ref_evap_sat [J/kg]", "P_ref_evap_sat [Pa]"),
        "1": hp("h_ref_cmp_in [J/kg]", "P_ref_cmp_in [Pa]"),
        "2": hp("h_ref_cmp_out [J/kg]", "P_ref_cmp_out [Pa]"),
        "2*": hp("h_ref_cond_sat_v [J/kg]", "P_ref_cond_sat_v [Pa]"),
        "3*": hp("h_ref_cond_sat_l [J/kg]", "P_ref_cond_sat_l [Pa]"),
        "3": hp("h_ref_exp_in [J/kg]", "P_ref_exp_in [Pa]"),
        "4": hp("h_ref_exp_out [J/kg]", "P_ref_exp_out [Pa]"),
    }


def _cycle_nodes_th(r: dict[str, float]) -> dict[str, tuple[float, float]]:
    def ht(h_key: str, t_key: str) -> tuple[float, float]:
        return r[h_key] / 1_000, r[t_key]

    return {
        "1*": ht("h_ref_evap_sat [J/kg]", "T_ref_evap_sat [°C]"),
        "1": ht("h_ref_cmp_in [J/kg]", "T_ref_cmp_in [°C]"),
        "2": ht("h_ref_cmp_out [J/kg]", "T_ref_cmp_out [°C]"),
        "2*": ht("h_ref_cond_sat_v [J/kg]", "T_ref_cond_sat_v [°C]"),
        "3*": ht("h_ref_cond_sat_l [J/kg]", "T_ref_cond_sat_l [°C]"),
        "3": ht("h_ref_exp_in [J/kg]", "T_ref_exp_in [°C]"),
        "4": ht("h_ref_exp_out [J/kg]", "T_ref_exp_out [°C]"),
    }


NODE_LABEL: dict[str, str] = {
    "1": "cmp,in",
    "2": "cmp,out",
    "3": "exp,in",
    "4": "exp,out",
}
CYCLE_PATH = ["1*", "1", "2", "2*", "3*", "3", "4", "1*"]
CLOSED_NODES = ["1", "2", "3", "4"]
OPEN_NODES = ["1*", "2*", "3*"]


def _draw_envelope(ax, h_liq, h_vap, y_sat) -> None:
    ax.plot(h_liq, y_sat, color=COLORS["cool"], linewidth=dm.lw(1), label="Sat. liquid")
    ax.plot(h_vap, y_sat, color=COLORS["hot"], linewidth=dm.lw(1), label="Sat. vapor")


def _draw_cycle(ax, pts: dict[str, tuple[float, float]], label_offsets: dict[str, tuple[int, int]]) -> None:
    xs = [pts[k][0] for k in CYCLE_PATH]
    ys = [pts[k][1] for k in CYCLE_PATH]

    ax.plot(xs, ys, color=COLORS["ink"], linewidth=dm.lw(0), linestyle=(0, (2, 2)), zorder=2, label="Ref. cycle")

    for k in OPEN_NODES:
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

    for k in CLOSED_NODES:
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


def main() -> None:
    apply_style("report", hashsalt="tmhp.visualization.mollier-cycle-r32")

    ashpb = AirSourceHeatPumpBoiler(ref=REF)
    result = ashpb.analyze_steady(T_tank_w=T_TANK_W, T0=T0, Q_ref_tank=Q_COND)
    assert isinstance(result, dict)

    pts_ph = _cycle_nodes_ph(result)
    pts_th = _cycle_nodes_th(result)
    h_liq_ph, h_vap_ph, p_sat = _saturation_envelope_ph(REF)
    h_liq_th, h_vap_th, T_sat = _saturation_envelope_th(REF)

    # Two-panel layout: 17 cm column-pair, 5:2 aspect keeps each panel
    # ~8 cm wide × 6 cm tall.
    fig, (ax_ph, ax_th) = plt.subplots(1, 2, figsize=dm.figsize("17cm", 5 / 12))

    # --- Panel (a): P-h --------------------------------------------------
    _draw_envelope(ax_ph, h_liq_ph, h_vap_ph, p_sat)
    _draw_cycle(
        ax_ph,
        pts_ph,
        label_offsets={
            "1": (8, -6),
            "2": (8, 4),
            "3": (-22, 8),
            "4": (-30, -14),
        },
    )
    ax_ph.set_yscale("log")
    ax_ph.set_xlim(0, 700)
    ax_ph.set_ylim(1e2, 1e4)
    ax_ph.set_xlabel("Enthalpy [kJ/kg]")
    ax_ph.set_ylabel("Pressure [kPa]")
    ax_ph.legend(loc="upper left", frameon=False, ncol=3, bbox_to_anchor=(0.06, 1.0), fontsize=dm.fs(-1))
    ax_ph.grid(True, which="both", alpha=0.25, linewidth=dm.lw(-2))
    panel_letter(ax_ph, "a")

    # --- Panel (b): T-h --------------------------------------------------
    _draw_envelope(ax_th, h_liq_th, h_vap_th, T_sat)
    _draw_cycle(
        ax_th,
        pts_th,
        label_offsets={
            "1": (8, -4),
            "2": (-50, 4),
            "3": (-30, 8),
            "4": (-32, -14),
        },
    )

    ax_th.axhline(T_TANK_W, color=COLORS["hot"], linewidth=dm.lw(0), linestyle=(0, (3, 3)), alpha=0.9)
    ax_th.text(15, T_TANK_W + 3, f"Tank water: {T_TANK_W:.1f} °C", color=COLORS["hot"], fontsize=dm.fs(-1))
    ax_th.axhline(T0, color=COLORS["warm"], linewidth=dm.lw(0), linestyle=(0, (3, 3)), alpha=0.9)
    ax_th.text(15, T0 - 10, f"Outdoor air: {T0:.1f} °C", color=COLORS["warm"], fontsize=dm.fs(-1))

    ax_th.set_xlim(0, 700)
    ax_th.set_ylim(-20, 160)
    ax_th.set_xlabel("Enthalpy [kJ/kg]")
    ax_th.set_ylabel("Temperature [°C]")
    ax_th.legend(loc="upper left", frameon=False, ncol=3, bbox_to_anchor=(0.06, 1.0), fontsize=dm.fs(-1))
    ax_th.grid(True, which="both", alpha=0.25, linewidth=dm.lw(-2))
    panel_letter(ax_th, "b")

    finalize(fig, OUTPUT_STEM)
    print(f"wrote {OUTPUT_STEM}.svg")


if __name__ == "__main__":
    main()
