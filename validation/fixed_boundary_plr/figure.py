"""Internal-state trajectories of the fixed-boundary sweep (Level 3 figure).

One row per case, eight panels: COP, n*, m_dot, T_evap/T_cond, PR, the three
efficiencies, W_cmp and the outdoor-fan fraction / air-side dT.  Rows at the
compressor speed floor are drawn hollow: there the delivered heat exceeds the
request (CR_actual > PLR_request) and the region is *separated*, not scored.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from tmhp.compressor_efficiency import COEFFICIENT_VERSION  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "validation" / "data" / "fixed_boundary_plr"
plt.rcParams.update({"font.size": 8, "axes.spines.top": False, "axes.spines.right": False})


def _plot_case(df: pd.DataFrame, title: str, out: Path, name: str) -> None:
    fig, axes = plt.subplots(2, 4, figsize=(12, 5.6))
    mod = df[df.capacity_clamped.isna()]
    flo = df[df.capacity_clamped.notna()]
    x = "plr_request"
    panels = [
        ("cop_sys", "COP_sys [-]"),
        ("n_star", "n* = N/N_rated [-]"),
        ("m_dot_ref", "refrigerant mass flow [kg/s]"),
        (None, "T_evap / T_cond [°C]"),
        ("pr", "pressure ratio [-]"),
        (None, "η_vol / η_isen / η_em [-]"),
        ("E_cmp", "compressor power [W]"),
        (None, "fan fraction [-] / air ΔT [K]"),
    ]
    for ax, (col, lab) in zip(axes.ravel(), panels, strict=True):
        if col is not None:
            ax.plot(mod[x], mod[col], "o-", ms=3, color="#1f77b4")
            ax.plot(flo[x], flo[col], "o", ms=4, mfc="none", color="#1f77b4")
        elif lab.startswith("T_evap"):
            for c, colr, lb in (("T_evap_C", "#2ca02c", "T_evap"), ("T_cond_C", "#d62728", "T_cond")):
                ax.plot(mod[x], mod[c], "o-", ms=3, color=colr, label=lb)
                ax.plot(flo[x], flo[c], "o", ms=4, mfc="none", color=colr)
            ax.legend(fontsize=7)
        elif lab.startswith("η"):
            for c, colr, lb in (
                ("eta_vol", "#1f77b4", "η_vol"),
                ("eta_isen", "#ff7f0e", "η_isen"),
                ("eta_em", "#9467bd", "η_em"),
            ):
                ax.plot(mod[x], mod[c], "o-", ms=3, color=colr, label=lb)
                ax.plot(flo[x], flo[c], "o", ms=4, mfc="none", color=colr)
            ax.legend(fontsize=7)
        else:
            ax.plot(mod[x], mod["fan_fraction"], "o-", ms=3, color="#1f77b4", label="fan fraction")
            ax.plot(flo[x], flo["fan_fraction"], "o", ms=4, mfc="none", color="#1f77b4")
            ax2 = ax.twinx()
            ax2.plot(df[x], df["air_dT_K"], "s--", ms=3, color="#8c564b", label="air ΔT")
            ax2.set_ylabel("air ΔT [K]", color="#8c564b")
            ax2.spines["top"].set_visible(False)
            ax.legend(fontsize=7, loc="lower right")
        ax.set_ylabel(lab)
        ax.set_xlabel("requested PLR = Q_req / Q_nominal")
        ax.set_xlim(1.02, 0.08)
        if len(flo):
            ax.axvspan(flo[x].max() + 0.02, 0.08, color="grey", alpha=0.10)
    fig.suptitle(
        f"{title} — hollow markers: compressor at speed floor, delivered > requested ({COEFFICIENT_VERSION})", y=1.01
    )
    fig.tight_layout()
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{name}.png", dpi=250, bbox_inches="tight")
    fig.savefig(out / f"{name}.svg", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO_ROOT / "validation" / "coefficients" / COEFFICIENT_VERSION / "figures"))
    a = ap.parse_args()
    out = Path(a.out)
    a2w = pd.read_csv(DATA / "ashpb_sweep.csv")
    for (ref, t_out, t_sink), g in a2w.groupby(["refrigerant", "t_outdoor_C", "t_sink_C"]):
        _plot_case(
            g,
            f"F8 air-to-water 9 kW {ref}, outdoor {t_out:g} °C / tank {t_sink:g} °C",
            out,
            f"F8_fixed_boundary_ashpb_{ref}_{t_out:g}C",
        )
    a2a = pd.read_csv(DATA / "ashp_sweep.csv")
    for (duty, t_out, t_room), g in a2a.groupby(["duty", "t_outdoor_C", "t_sink_C"]):
        _plot_case(
            g,
            f"F8 air-to-air 3.5 kW R32 {duty}, outdoor {t_out:g} °C / room {t_room:g} °C",
            out,
            f"F8_fixed_boundary_ashp_{duty}",
        )
    print(f"figures -> {out}")


if __name__ == "__main__":
    main()
