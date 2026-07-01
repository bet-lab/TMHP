"""Daily energy balance for the ASHPB_PV_ESS scenario.

Runs a one-day dynamic simulation with a representative clear-day
irradiance profile and a moderate DHW draw, then renders two side-by-
side panels:

  (a) timeseries — PV generation, HP electrical load, grid import,
      ESS charge and ESS discharge power. Storing and releasing share
      a colour family (light vs. dark teal) so the eye links them.
  (b) stacked-bar daily energy ledger — kWh of PV that went directly
      to HP load, that charged the ESS, that was dumped; plus how much
      of the HP load came from PV, from ESS discharge, and from grid
      import. ``PV→ESS`` and ``ESS→HP`` use the same teal pair as the
      timeseries lines, so the reader can trace the two flows across
      panels at a glance.

The second panel is what makes the design tradeoff readable: a small
ESS dumps a lot of midday PV; a small PV system pulls in a lot of grid.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np

from tmhp import ASHPB_PV_ESS
from tmhp.subsystems import EnergyStorageSystem, PhotovoltaicSystem

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dmpl_common import COLORS, apply_style, finalize, panel_letter, static_path  # noqa: E402

SIM_HOURS = 24
DT_S = 60
N_STEPS = SIM_HOURS * 3600 // DT_S

# Same hue, different value: storing (PV→ESS) is the lighter shade,
# releasing (ESS→HP) the darker — so the two flows read as related
# without being confused with each other. The left-panel timeseries
# uses saturated line colours; the right-panel bars use the same hue
# family one-to-three steps lighter so the per-segment kWh
# annotations sit on top of legible negative space without losing
# the visual link across panels.
ESS_CHG_COLOR = "oc.teal4"
ESS_DIS_COLOR = "oc.teal8"
PV_BAR_COLOR = "oc.yellow3"
ESS_CHG_BAR = "oc.teal2"
ESS_DIS_BAR = "oc.teal5"
DUMP_BAR = "oc.gray3"
GRID_BAR = "oc.indigo4"

# Defaults (A_pv=5 m², 1 kWh) leave 75% of midday PV unused. Sizing
# was tuned together so the ledger shows movement on every column:
# the 8 kWh ESS soaks up most of the midday surplus from 10 m² of PV
# (≈10 kWh/day) without saturating immediately, the evening HP peak
# is partly covered by the discharged ESS, and grid import still
# accounts for the morning peak — the regime where the tradeoff is
# legible. Pushing PV/ESS further drives dump → 0 and grid → 0, which
# is a less interesting design point to illustrate.
A_PV_M2 = 10.0
C_ESS_J = 28_800_000  # 8 kWh


def _dhw_profile(n: int) -> np.ndarray:
    t_h = np.arange(n) * DT_S / 3600.0

    def peak(center_h: float, sigma_h: float, peak_lpm: float) -> np.ndarray:
        return (peak_lpm / 60_000.0) * np.exp(-0.5 * ((t_h - center_h) / sigma_h) ** 2)

    return cast(np.ndarray, peak(7.0, 0.35, 12.0) + peak(20.0, 0.55, 8.0))


def _t0_profile(n: int) -> np.ndarray:
    t_h = np.arange(n) * DT_S / 3600.0
    return 8.0 + 7.0 * np.sin((t_h - 9.0) * np.pi / 12.0)


def _clearsky_irradiance(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Cosine clearsky profile peaking at solar noon. Returns (I_DN, I_dH)."""
    t_h = np.arange(n) * DT_S / 3600.0
    day_factor = np.clip(np.sin((t_h - 6.0) * np.pi / 12.0), 0.0, 1.0)
    i_dn = 800.0 * day_factor
    i_dh = 150.0 * day_factor
    return i_dn, i_dh


def main() -> None:
    apply_style("report", hashsalt="tmhp.visualization.pv-ess-energy-balance")

    pv = PhotovoltaicSystem(A_pv=A_PV_M2)
    ess = EnergyStorageSystem(C_ess_max=C_ESS_J)
    model = ASHPB_PV_ESS(pv=pv, ess=ess, ref="R32")

    dhw = _dhw_profile(N_STEPS)
    t0 = _t0_profile(N_STEPS)
    i_dn, i_dh = _clearsky_irradiance(N_STEPS)

    df = model.analyze_dynamic(
        simulation_period_sec=SIM_HOURS * 3600,
        dt_s=DT_S,
        T_tank_w_init_C=50.0,
        dhw_usage_schedule=dhw,
        T0_schedule=t0,
        I_DN_schedule=i_dn,
        I_dH_schedule=i_dh,
    )

    t_h = np.arange(len(df)) * DT_S / 3600.0
    e_pv = df["E_pv_out [W]"].to_numpy() / 1_000.0
    e_hp = df["E_cmp [W]"].to_numpy() / 1_000.0
    e_grid = df["E_grid_import [W]"].to_numpy() / 1_000.0
    e_chg = df["E_ess_chg [W]"].to_numpy() / 1_000.0
    e_dis = df["E_ess_dis [W]"].to_numpy() / 1_000.0

    # Daily energy ledger (kWh) by integrating power over the day.
    dt_h = DT_S / 3600.0
    kwh_pv = float(e_pv.sum() * dt_h)
    kwh_grid = float(e_grid.sum() * dt_h)
    kwh_dis = float(e_dis.sum() * dt_h)
    kwh_chg = float(e_chg.sum() * dt_h)
    kwh_hp = float(e_hp.sum() * dt_h)
    # The dump column is implicit: PV that wasn't used directly and
    # couldn't be absorbed by the ESS. By energy balance:
    #   PV = direct-to-HP + ESS charge + dump
    # With direct-to-HP estimated as min(PV, HP load) per step.
    direct = np.minimum(e_pv, e_hp)
    kwh_direct = float(direct.sum() * dt_h)
    kwh_dump = max(0.0, kwh_pv - kwh_direct - kwh_chg)

    fig, (ax_t, ax_b) = plt.subplots(
        1,
        2,
        figsize=dm.figsize("17cm", 6 / 12),
        gridspec_kw={"width_ratios": [3, 2]},
    )

    # --- (a) timeseries ------------------------------------------------
    ax_t.plot(t_h, e_pv, color=COLORS["pv"], linewidth=dm.lw(1), label="PV generation")
    ax_t.plot(t_h, e_hp, color=COLORS["load"], linewidth=dm.lw(0), label="HP electrical load")
    ax_t.plot(t_h, e_grid, color=COLORS["accent"], linewidth=dm.lw(0), linestyle=(0, (3, 3)), label="Grid import")
    ax_t.plot(t_h, e_chg, color=ESS_CHG_COLOR, linewidth=dm.lw(0), label="ESS charge")
    ax_t.plot(t_h, e_dis, color=ESS_DIS_COLOR, linewidth=dm.lw(0), label="ESS discharge")
    ax_t.fill_between(t_h, 0, e_pv, color=COLORS["pv"], alpha=0.20, linewidth=0)
    ax_t.set_xlabel("Time of day [h]")
    ax_t.set_ylabel("Power [kW]")
    ax_t.set_xlim(0, SIM_HOURS)
    ax_t.set_xticks(np.arange(0, SIM_HOURS + 1, 3))
    ax_t.grid(True, alpha=0.25, linewidth=dm.lw(-2))
    ax_t.legend(loc="upper left", bbox_to_anchor=(0.06, 1.0), frameon=False, fontsize=dm.fs(-1), ncol=2)
    panel_letter(ax_t, "a")

    # --- (b) stacked ledger -------------------------------------------
    # Two bars: PV destinations vs. HP-load sources.
    x_labels = ["PV destinations", "HP-load sources"]
    x = np.arange(len(x_labels))
    width = 0.55

    def _annot(xpos: float, bottom: float, height: float) -> None:
        """Centered numeric label — unit elided since y-axis already carries it."""
        if height <= 0:
            return
        ax_b.text(
            xpos,
            bottom + height / 2,
            f"{height:.1f}",
            ha="center",
            va="center",
            fontsize=dm.fs(-1),
            color=COLORS["ink"],
        )

    # PV destinations stack
    bottom_pv = 0.0
    ax_b.bar(x[0], kwh_direct, width=width, bottom=bottom_pv, color=PV_BAR_COLOR, label="PV to HP (direct)")
    _annot(x[0], bottom_pv, kwh_direct)
    bottom_pv += kwh_direct
    ax_b.bar(x[0], kwh_chg, width=width, bottom=bottom_pv, color=ESS_CHG_BAR, label="PV to ESS")
    _annot(x[0], bottom_pv, kwh_chg)
    bottom_pv += kwh_chg
    ax_b.bar(x[0], kwh_dump, width=width, bottom=bottom_pv, color=DUMP_BAR, label="PV to dump")
    _annot(x[0], bottom_pv, kwh_dump)

    # HP-load sources stack
    bottom_hp = 0.0
    ax_b.bar(x[1], kwh_direct, width=width, bottom=bottom_hp, color=PV_BAR_COLOR)
    _annot(x[1], bottom_hp, kwh_direct)
    bottom_hp += kwh_direct
    ax_b.bar(x[1], kwh_dis, width=width, bottom=bottom_hp, color=ESS_DIS_BAR, label="ESS to HP")
    _annot(x[1], bottom_hp, kwh_dis)
    bottom_hp += kwh_dis
    ax_b.bar(x[1], kwh_grid, width=width, bottom=bottom_hp, color=GRID_BAR, label="Grid to HP")
    _annot(x[1], bottom_hp, kwh_grid)

    ax_b.set_xticks(x)
    # No xlabel on this panel — the categorical tick labels are doing
    # the axis-label job, so keep them at the default ``axes.labelsize``
    # to match the left panel's "Time of day [h]" / "Power [kW]".
    ax_b.set_xticklabels(x_labels)
    ax_b.set_ylabel("Daily energy [kWh]")
    ax_b.grid(True, alpha=0.25, linewidth=dm.lw(-2), axis="y")
    ax_b.legend(loc="upper left", bbox_to_anchor=(0.06, 1.0), frameon=False, fontsize=dm.fs(-2))
    panel_letter(ax_b, "b")

    # Totals sit above each bar — unit elided to match segment labels.
    top_pv = bottom_pv + kwh_dump
    top_hp = bottom_hp + kwh_grid
    ax_b.text(
        x[0],
        top_pv + 0.2,
        f"{kwh_pv:.1f}",
        ha="center",
        va="bottom",
        fontsize=dm.fs(0),
        color=COLORS["ink"],
    )
    ax_b.text(
        x[1],
        top_hp + 0.2,
        f"{kwh_hp:.1f}",
        ha="center",
        va="bottom",
        fontsize=dm.fs(0),
        color=COLORS["ink"],
    )

    out = static_path("pv_ess_energy_balance.svg").with_suffix("")
    finalize(fig, out, margin="3%")
    plt.close(fig)
    print(f"wrote {out}.svg")


if __name__ == "__main__":
    main()
