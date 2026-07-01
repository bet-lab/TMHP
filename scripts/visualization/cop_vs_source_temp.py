"""ASHP vs GSHP system-COP curves against heat-source temperature.

Sweeps the heat-source inlet temperature for an air-source unit
(``T0`` ambient) and a ground-source unit (``T_source`` ground-loop
fluid) at a common condenser duty and tank set-point, and overlays both
on a single axes. The contrast makes the GSHP's narrow but stable
source-temperature envelope obvious next to the ASHP's wide but
COP-eroding one.

Operating point: tank water 55 °C, condenser duty 8 kW.
"""

from __future__ import annotations

import sys
from pathlib import Path

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np

from tmhp import AirSourceHeatPumpBoiler, GroundSourceHeatPumpBoiler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dmpl_common import COLORS, apply_style, finalize, static_path  # noqa: E402

T_TANK_W = 55.0   # °C — typical DHW set-point
Q_COND   = 8_000  # W

# Sweep ranges chosen to match each technology's realistic envelope.
T_AMB_RANGE = np.linspace(-15.0, 30.0, 19)
T_SRC_RANGE = np.linspace(-5.0, 15.0, 11)


def _cop_or_nan(result: dict | None) -> float:
    if not isinstance(result, dict):
        return float("nan")
    e_cmp = float(result.get("E_cmp [W]", 0.0))
    if e_cmp <= 0.0:
        return float("nan")
    return float(result.get("cop_sys [-]", float("nan")))


def sweep_ashp() -> tuple[np.ndarray, np.ndarray]:
    """ASHP system COP as a function of outdoor air temperature."""
    ashpb = AirSourceHeatPumpBoiler(ref="R32")
    cops = []
    for t in T_AMB_RANGE:
        res = ashpb.analyze_steady(T_tank_w=T_TANK_W, T0=float(t), Q_ref_tank=Q_COND)
        cops.append(_cop_or_nan(res if isinstance(res, dict) else None))
    return T_AMB_RANGE, np.array(cops)


def sweep_gshp() -> tuple[np.ndarray, np.ndarray]:
    """GSHP system COP as a function of ground-loop fluid inlet temperature."""
    # Keep the borehole field small/fast — we are only after the cycle
    # response, not the long-term ground response.
    gshpb = GroundSourceHeatPumpBoiler(
        ref="R410A",
        N_1=1, N_2=1,
        H_b=100.0,
        t_max_s=24 * 3600,
        dt_s=3600,
    )
    cops = []
    for t in T_SRC_RANGE:
        res = gshpb.analyze_steady(
            T_tank_w=T_TANK_W,
            T_source=float(t),
            Q_ref_tank=Q_COND,
        )
        cops.append(_cop_or_nan(res if isinstance(res, dict) else None))
    return T_SRC_RANGE, np.array(cops)


def main() -> None:
    apply_style("report", hashsalt="tmhp.visualization.cop-vs-source-temp")

    t_amb, cop_ashp = sweep_ashp()
    t_src, cop_gshp = sweep_gshp()

    fig, ax = plt.subplots(figsize=dm.figsize("13cm", "standard"))

    ax.plot(t_amb, cop_ashp, color=COLORS["warm"], linewidth=dm.lw(1),
            marker="o", markersize=4, label="ASHP (R32, source = outdoor air)")
    ax.plot(t_src, cop_gshp, color=COLORS["accent"], linewidth=dm.lw(1),
            marker="s", markersize=4, label="GSHP (R410A, source = ground loop)")

    ax.set_xlabel("Heat-source temperature [°C]")
    ax.set_ylabel("System COP [-]")
    ax.set_xlim(-16, 31)
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.25, linewidth=dm.lw(-2))
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-1))

    ax.text(
        0.98, 0.05,
        f"$T_{{\\mathrm{{tank,w}}}} = {T_TANK_W:.0f}$ °C\n"
        f"$\\dot Q_{{\\mathrm{{cond}}}} = {Q_COND/1000:.0f}$ kW",
        transform=ax.transAxes,
        va="bottom", ha="right",
        fontsize=dm.fs(-1),
        color=COLORS["ink"],
    )

    out = static_path("cop_vs_source_temp.svg").with_suffix("")
    finalize(fig, out)
    plt.close(fig)
    print(f"wrote {out}.svg")


if __name__ == "__main__":
    main()
