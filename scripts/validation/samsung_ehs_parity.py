"""Reproduce the parity plot from the KJACR 2026 (in press) validation paper.

Compares predictions from `AirSourceHeatPumpBoiler` against the 15 catalogue
operating points of the Samsung EHS Mono HT Quiet R32 14 kW unit, then writes a
publication-quality SVG figure to ``docs/source/_static/validation_parity.svg``.

The figure is rendered through the ``scientific`` dartwork-mpl preset so it
shares typography, line weights, and colour tokens with the rest of the docs
gallery.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np

from tmhp import AirSourceHeatPumpBoiler

# Make the shared visualization helpers importable when run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "visualization"))
from _dmpl_common import COLORS, apply_style, finalize, static_path  # noqa: E402


@dataclass(frozen=True)
class OperatingPoint:
    """One row of Samsung EHS catalogue Table 1."""

    id: int
    lwt_c: float
    t0_c: float
    q_cond_kw: float
    target_cop: float

    @property
    def t_tank_c(self) -> float:
        # Paper section 3: LWT - 2.5 K for LWT <= 60, LWT - 5 K for LWT > 60.
        if self.lwt_c <= 60.0:
            return self.lwt_c - 2.5
        return self.lwt_c - 5.0


CATALOGUE: tuple[OperatingPoint, ...] = (
    OperatingPoint(1, 40, -10, 13.45, 2.30),
    OperatingPoint(2, 40, 2, 12.42, 3.04),
    OperatingPoint(3, 40, 12, 14.65, 5.07),
    OperatingPoint(4, 40, 20, 15.69, 6.48),
    OperatingPoint(5, 40, 30, 16.98, 7.68),
    OperatingPoint(6, 50, -10, 13.89, 2.00),
    OperatingPoint(7, 50, 2, 13.27, 2.56),
    OperatingPoint(8, 50, 12, 14.76, 3.86),
    OperatingPoint(9, 50, 20, 15.97, 4.78),
    OperatingPoint(10, 50, 30, 17.48, 5.95),
    OperatingPoint(11, 65, -10, 13.97, 1.73),
    OperatingPoint(12, 65, 2, 13.71, 2.04),
    OperatingPoint(13, 65, 12, 16.38, 2.84),
    OperatingPoint(14, 65, 20, 17.48, 3.34),
    OperatingPoint(15, 65, 30, 18.84, 4.04),
)


def build_model() -> AirSourceHeatPumpBoiler:
    """Configure ASHPB with the parameter set from the paper's Table 2."""
    def eta_vol(pi: float) -> float:
        return float(1.0 - 0.035 * (pi ** (1.0 / 1.18) - 1.0))

    def eta_isen(pi: float) -> float:
        return 0.90 - 0.02 * pi

    def eta_mech(_pi: float, rps: float) -> float:
        return 0.90 - 6.25e-5 * (rps - 60.0) ** 2

    return AirSourceHeatPumpBoiler(
        ref="R32",
        V_cmp_ref=33.0e-6,
        eta_cmp_isen=eta_isen,
        eta_cmp_vol=eta_vol,
        eta_cmp=eta_mech,
        dT_superheat=5.0,
        dT_subcool=5.0,
        # The Samsung EHS Mono HT unit is a high-temperature heat pump that
        # reaches LWT 65 °C at -10 °C ambient (catalogue point 11, PR ~ 16) via
        # vapour injection, well beyond a single-stage envelope. Raise the
        # pressure-ratio ceiling so the guard does not clip catalogue points.
        PR_cycle_max=20.0,
        UA_tank_hx=2500.0,
        UA_ou_rated=2000.0,
        n_ou=0.65,
        dV_fan_a_rated=1.5,
        dP_fan_rated=60.0,
        eta_fan_rated=0.6,
    )


def run_validation() -> list[tuple[OperatingPoint, float]]:
    model = build_model()
    rows: list[tuple[OperatingPoint, float]] = []
    for op in CATALOGUE:
        result = model.analyze_steady(
            T_tank_w=op.t_tank_c,
            T0=op.t0_c,
            Q_ref_tank=op.q_cond_kw * 1000.0,
            return_dict=True,
        )
        assert isinstance(result, dict)
        cop = float(result["cop_sys [-]"])
        rows.append((op, cop))
    return rows


def plot_parity(rows: list[tuple[OperatingPoint, float]], out_stem: Path) -> None:
    target = np.array([op.target_cop for op, _ in rows])
    pred = np.array([cop for _, cop in rows])

    abs_err = np.abs(pred - target)
    mae = float(abs_err.mean())
    mape = float((abs_err / target).mean() * 100.0)

    lo, hi = 1.0, 8.0
    line = np.linspace(lo, hi, 200)

    fig, ax = plt.subplots(figsize=dm.figsize("11cm", "square"))
    ax.fill_between(line, 0.80 * line, 1.20 * line, color=COLORS["band20"],
                    alpha=0.55, label="±20 % error", linewidth=0)
    ax.fill_between(line, 0.90 * line, 1.10 * line, color=COLORS["band10"],
                    alpha=0.70, label="±10 % error", linewidth=0)
    ax.plot(line, line, linestyle=":", color=COLORS["muted"], linewidth=dm.lw(0))

    ax.scatter(target, pred, s=dm.fs(8), color=COLORS["accent"], zorder=4,
               edgecolor="white", linewidth=dm.lw(-1))
    for (op, cop) in rows:
        ax.annotate(
            str(op.id),
            (op.target_cop, cop),
            textcoords="offset points",
            xytext=(3, -3),
            fontsize=dm.fs(-1),
            color=COLORS["ink"],
        )

    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("Target COP [-]")
    ax.set_ylabel("Predicted COP [-]")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, alpha=0.25, linewidth=dm.lw(-2))
    ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-1))

    # Inline error budget — saves the reader a trip to the body table.
    ax.text(
        0.04, 0.96,
        f"MAE = {mae:.2f}\nMAPE = {mape:.1f} %",
        transform=ax.transAxes,
        va="top", ha="left",
        fontsize=dm.fs(-1),
        color=COLORS["ink"],
    )

    finalize(fig, out_stem)
    plt.close(fig)

    print(f"MAE  = {mae:.3f}")
    print(f"MAPE = {mape:.2f}%")
    print(f"SVG  = {out_stem}.svg")


def render_markdown_table(rows: list[tuple[OperatingPoint, float]]) -> str:
    """Render a Markdown results table suitable for the README."""
    target = np.array([op.target_cop for op, _ in rows])
    pred = np.array([cop for _, cop in rows])
    abs_err = np.abs(pred - target)
    pct_err = abs_err / target * 100.0
    mae = float(abs_err.mean())
    mape = float(pct_err.mean())

    header = (
        "| $\\mathrm{ID}$ "
        "| $T_{\\mathrm{LWT}}~[^\\circ\\mathrm{C}]$ "
        "| $T_0~[^\\circ\\mathrm{C}]$ "
        "| $\\dot{Q}_{\\mathrm{cond}}~[\\mathrm{kW}]$ "
        "| $\\mathrm{COP}_{\\mathrm{target}}$ "
        "| $\\mathrm{COP}_{\\mathrm{pred}}$ "
        "| $\\mathrm{AE}$ "
        "| $\\mathrm{APE}$ |\n"
        "|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"
    )

    def _fmt_t0(t: float) -> str:
        return f"−{abs(t):.0f}" if t < 0 else f"{t:.0f}"

    body_lines = [
        (
            f"| {op.id} | {op.lwt_c:.0f} | {_fmt_t0(op.t0_c)} | {op.q_cond_kw:.2f} | "
            f"{op.target_cop:.2f} | {cop:.2f} | {ae:.2f} | {pe:.1f} % |"
        )
        for (op, cop), ae, pe in zip(rows, abs_err, pct_err, strict=True)
    ]
    footer = f"| | | | | | **Mean** | **{mae:.2f}** | **{mape:.1f} %** |"
    return "\n".join([header, *body_lines, footer])


def main() -> None:
    apply_style("report", hashsalt="tmhp.validation.samsung-ehs-parity")
    rows = run_validation()
    # save_formats appends extensions, so strip ".svg" from the stem.
    out_stem = static_path("validation_parity.svg").with_suffix("")
    plot_parity(rows, out_stem)
    print()
    print("Markdown table (paste into README):")
    print(render_markdown_table(rows))


if __name__ == "__main__":
    main()
