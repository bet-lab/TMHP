"""Internal-state trajectories of the fixed-boundary sweep (Level 3 figure,
dartwork-mpl ``scientific`` preset).

One figure per case, eight panels: COP, n*, refrigerant mass flow, saturation
temperatures, pressure ratio, the three efficiencies, compressor power and the
outdoor-fan fraction with the air-side temperature drop.  Rows at the compressor
speed floor are drawn hollow: there the delivered heat exceeds the request and
the region is separated, not scored.  Units are scaled so tick labels stay
within three significant digits (mass flow in g/s, power in kW).
"""

from __future__ import annotations

import argparse
from pathlib import Path

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

from tmhp.compressor_efficiency import COEFFICIENT_VERSION  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "validation" / "data" / "fixed_boundary_plr"
LETTERS = "abcdefgh"


def _nice(vmin: float, vmax: float, n: int = 4) -> tuple[float, float, float]:
    """A tick step from {1, 2, 2.5, 5} x 10^k giving about ``n`` intervals, and the enclosing limits."""
    span = max(vmax - vmin, 1e-9)
    raw = span / n
    mag = 10 ** np.floor(np.log10(raw))
    step = min((m for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=10) * mag
    lo = np.floor(vmin / step) * step
    hi = np.ceil(vmax / step) * step
    if hi == lo:
        hi = lo + step
    return float(lo), float(hi), float(step)


def _series(ax, mod, flo, x, col, color, label=None, scale=1.0):
    ax.plot(mod[x], mod[col] * scale, "o-", ms=dm.fs(-4), lw=dm.lw(0), color=color, label=label)
    ax.plot(flo[x], flo[col] * scale, "o", ms=dm.fs(-3), mfc="none", mec=color, mew=HAIRLINE * 1.5)


def _plot_case(df: pd.DataFrame, title: str, out: Path, name: str) -> None:
    fig, axes = plt.subplots(2, 4, figsize=dm.figsize("17cm", 0.62), gridspec_kw={"wspace": 0.55, "hspace": 0.42})
    mod = df[df.capacity_clamped.isna()]
    flo = df[df.capacity_clamped.notna()]
    x = "plr_request"
    panels = [
        ("cop_sys", "COP_sys [-]", 1.0),
        ("n_star", "n* = N / N_rated [-]", 1.0),
        ("m_dot_ref", "Refrigerant flow [g/s]", 1e3),
        (None, "T_sat [°C]", 1.0),
        ("pr", "Pressure ratio [-]", 1.0),
        (None, "η [-]", 1.0),
        ("E_cmp", "Compressor power [kW]", 1e-3),
        (None, "Outdoor fan fraction [-]", 1.0),
    ]
    for i, (ax, (col, lab, scale)) in enumerate(zip(axes.ravel(), panels, strict=True)):
        if col is not None:
            _series(ax, mod, flo, x, col, COLORS["accent"], scale=scale)
            lo, hi, st = _nice(df[col].min() * scale, df[col].max() * scale)
        elif lab.startswith("T_sat"):
            _series(ax, mod, flo, x, "T_evap_C", COLORS["cool"], "T_evap")
            _series(ax, mod, flo, x, "T_cond_C", COLORS["hot"], "T_cond")
            lo, hi, st = _nice(df.T_evap_C.min(), df.T_cond_C.max(), 5)
            ax.legend(loc="center right", frameon=False, fontsize=dm.fs(-3))
        elif lab.startswith("η"):
            for c, colr, lb in (
                ("eta_vol", COLORS["accent"], "η_vol"),
                ("eta_isen", COLORS["warm"], "η_isen"),
                ("eta_em", COLORS["accent2"], "η_em"),
            ):
                _series(ax, mod, flo, x, c, colr, lb)
            lo, hi, st = _nice(
                df[["eta_vol", "eta_isen", "eta_em"]].min().min(), df[["eta_vol", "eta_isen", "eta_em"]].max().max(), 5
            )
            ax.legend(loc="center right", frameon=False, fontsize=dm.fs(-3))
        else:
            _series(ax, mod, flo, x, "fan_fraction", COLORS["accent"], "fan fraction")
            lo, hi, st = 0.0, 1.0, 0.25
            ax2 = ax.twinx()
            ax2.plot(
                df[x],
                df["air_dT_K"],
                "s",
                ms=dm.fs(-4),
                ls=(0, (2.4, 1.4)),
                lw=dm.lw(0),
                color=COLORS["warm"],
                label="air ΔT",
            )
            l2, h2, s2 = _nice(0.0, df.air_dT_K.max(), 4)
            ax2.set_ylim(l2, h2)
            ax2.set_yticks(ticks(l2, h2, s2))
            ax2.set_ylabel("Air-side ΔT [K]", color=COLORS["warm"])
            ax2.tick_params(axis="y", colors=COLORS["warm"])
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, loc="lower right", frameon=False, fontsize=dm.fs(-3))
        ax.set_ylim(lo, hi)
        ax.set_yticks(ticks(lo, hi, st))
        ax.set_ylabel(lab)
        ax.set_xlim(1.04, 0.06)
        ax.set_xticks(ticks(0.2, 1.0, 0.2))
        if i >= 4:
            ax.set_xlabel("Requested PLR [-]")
        if len(flo):
            ax.axvspan(flo[x].max() + 0.02, 0.06, color=COLORS["band20"], alpha=0.35, lw=0)
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
        panel_letter(ax, LETTERS[i], x=-0.30, y=1.02)
    fig.suptitle(
        f"{title} — hollow: compressor at speed floor, delivered > requested ({COEFFICIENT_VERSION})",
        fontsize=dm.fs(-1),
        x=0.02,
        ha="left",
    )
    out.mkdir(parents=True, exist_ok=True)
    finalize(fig, out / name, formats=("svg", "png"), mt="5%")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO_ROOT / "validation" / "coefficients" / COEFFICIENT_VERSION / "figures"))
    a = ap.parse_args()
    apply_style("scientific")
    out = Path(a.out)
    a2w = pd.read_csv(DATA / "ashpb_sweep.csv")
    for (ref, t_out, t_sink), g in a2w.groupby(["refrigerant", "t_outdoor_C", "t_sink_C"]):
        _plot_case(
            g,
            f"Air-to-water 9 kW {ref}, outdoor {t_out:g} °C / tank {t_sink:g} °C",
            out,
            f"F8_fixed_boundary_ashpb_{ref}_{t_out:g}C",
        )
    a2a = pd.read_csv(DATA / "ashp_sweep.csv")
    for (duty, t_out, t_room), g in a2a.groupby(["duty", "t_outdoor_C", "t_sink_C"]):
        _plot_case(
            g,
            f"Air-to-air 3.5 kW R32 {duty}, outdoor {t_out:g} °C / room {t_room:g} °C",
            out,
            f"F8_fixed_boundary_ashp_{duty}",
        )
    print(f"figures -> {out}")


if __name__ == "__main__":
    main()
