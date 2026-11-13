"""Short-time ground response: steady borehole model against the dynamic one.

Two figures for ``docs/source/models/ground-response.rst``:

``ground_step_response.svg``
    Temperature drop of the borehole wall and of the fluid leaving the
    borehole after a constant extraction of 27 W/m starts, from one minute to
    ten days. The steady model (line source, instantaneous borehole
    resistance) is compared with the dynamic model (cylindrical wall
    response, heat capacity of fluid, pipe and grout, fluid transit).

``ground_cycling_outlet.svg``
    Borehole outlet temperature of ``GroundSourceHeatPumpBoiler`` running
    10 minutes on / 20 minutes off, with the steady borehole
    (``borehole_dynamics=False``) and with the dynamic one (the default).

Default 8 kW GSHPB borehole (200 m, r_b 80 mm, DN32 single U-tube) at 24 L/min.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np

from tmhp import GroundSourceHeatPumpBoiler
from tmhp.borehole_capacity import cylinder_source_wall
from tmhp.constants import c_w, rho_w

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dmpl_common import COLORS, GRIDLINE, HAIRLINE, apply_style, finalize, static_path  # noqa: E402

Q_LINE = 27.0  # W/m, extraction per metre of borehole
# "Steady" is the inside of the borehole (a resistance, no heat capacity); the
# ground is transient in both models.
FLOW_LPM = 24.0  # L/min through the borehole (the 8 kW default is 28.7)
LABEL_STEADY = r"steady: $R_b^*$ + FLS $g$"
LABEL_DYNAMIC = "default: with borehole dynamics"
ON_MIN, OFF_MIN = 10, 20


def _models(**kwargs) -> tuple[GroundSourceHeatPumpBoiler, GroundSourceHeatPumpBoiler]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # A water loop, as the figures were made (the models' default loop liquid is 25 % ethylene glycol).
        common = dict(ground_flow_ref_lpm=FLOW_LPM, source_fluid="water", **kwargs)
        steady = GroundSourceHeatPumpBoiler(borehole_dynamics=False, **common)
        dynamic = GroundSourceHeatPumpBoiler(borehole_dynamics=True, **common)
    return steady, dynamic


def step_response_figure() -> None:
    steady, dynamic = _models(t_max_s=30 * 86400.0)
    g_steady, g_dyn = steady._gfunc_interp, dynamic._gfunc_interp
    r_b = steady.R_b
    half = steady.H_b / (2.0 * steady.dV_b_f_m3s * rho_w * c_w)
    t = np.geomspace(60.0, 10 * 86400.0, 400)
    hours = t / 3600.0

    width, _ = dm.figsize("17cm", "wide")
    fig, (ax_w, ax_f) = plt.subplots(1, 2, figsize=(width, 0.45 * width), sharex=True)

    # Wall: steady g (line from the origin below 1 h), analytical cylinder source, dynamic wall.
    ax_w.plot(hours, Q_LINE * g_steady(t), color=COLORS["ink"], linewidth=dm.lw(0), label=LABEL_STEADY)
    ax_w.plot(
        hours,
        Q_LINE * cylinder_source_wall(t, steady.k_s, steady.alp_s, steady.r_b),
        color=COLORS["accent2"],
        linewidth=dm.lw(0),
        linestyle=(0, (4, 2)),
        label="cylinder source (analytical)",
    )
    ax_w.plot(hours, Q_LINE * g_dyn.wall(t), color=COLORS["accent"], linewidth=dm.lw(1), label=LABEL_DYNAMIC)
    ax_w.set_title("Borehole wall", loc="left", fontsize=dm.fs(0))
    ax_w.set_ylabel(f"temperature drop at {Q_LINE:.0f} W/m [K]")

    # Outlet: T_s - T_out = q' (K + R_b - H / (2 m c_p)).
    ax_f.plot(hours, Q_LINE * (g_steady(t) + r_b - half), color=COLORS["ink"], linewidth=dm.lw(0), label=LABEL_STEADY)
    ax_f.plot(hours, Q_LINE * (g_dyn(t) + r_b - half), color=COLORS["accent"], linewidth=dm.lw(1), label=LABEL_DYNAMIC)
    transit_h = g_dyn.transit_time / 3600.0
    ax_f.axvline(transit_h, color=COLORS["ink"], linewidth=HAIRLINE, linestyle=(0, (1, 2)))
    ax_f.text(
        transit_h / 1.12,
        0.5,
        f"one transit\n({g_dyn.transit_time / 60:.0f} min)",
        transform=ax_f.get_xaxis_transform(),
        fontsize=dm.fs(-2),
        color=COLORS["ink"],
        va="center",
        ha="right",
    )
    ax_f.set_title("Fluid leaving the borehole", loc="left", fontsize=dm.fs(0))

    for ax in (ax_w, ax_f):
        ax.set_xscale("log")
        ax.set_xlabel("time since the load started [h]")
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
        ax.set_ylim(bottom=0.0)
    ax_w.legend(loc="upper left", frameon=False, fontsize=dm.fs(-1))
    ax_f.legend(loc="lower right", frameon=False, fontsize=dm.fs(-1))

    out = static_path("ground_step_response.svg").with_suffix("")
    finalize(fig, out, mt="8%", formats=("svg", "png"))
    plt.close(fig)
    print(f"wrote {out}.svg")


def cycling_figure() -> None:
    dt = 60.0
    period_h = (ON_MIN + OFF_MIN) / 60.0
    schedule = [(k * period_h, k * period_h + ON_MIN / 60.0) for k in range(int(24 / period_h))]
    steady, dynamic = _models(dt_s=dt, t_max_s=4 * 86400.0, hp_on_schedule=schedule)
    n = int(26 * 3600 / dt)
    draw = np.full(n, 4.0 / 60000.0)  # a steady 4 L/min draw keeps the tank calling
    outdoor = np.full(n, 5.0)
    frames = []
    for model in (steady, dynamic):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            frames.append(model.analyze_dynamic(n * dt, dt, 55.0, draw, outdoor))
    start = int(24 * 3600 / dt)
    window = slice(start, start + int(2 * 3600 / dt) + 1)
    minutes = (np.arange(n)[window] - start) * dt / 60.0

    fig, ax = plt.subplots(figsize=dm.figsize("13cm", "standard"))
    for k in range(4):
        ax.axvspan(
            k * (ON_MIN + OFF_MIN), k * (ON_MIN + OFF_MIN) + ON_MIN, color=COLORS["ink"], alpha=0.06, linewidth=0
        )
    ax.axhline(steady.Ts, color=COLORS["ink"], linewidth=HAIRLINE, linestyle=(0, (1, 2)))
    ax.text(
        119, steady.Ts + 0.08, "undisturbed ground", ha="right", va="bottom", fontsize=dm.fs(-2), color=COLORS["ink"]
    )
    for frame, label, color, width in (
        (frames[0], LABEL_STEADY, COLORS["ink"], dm.lw(0)),
        (frames[1], LABEL_DYNAMIC, COLORS["accent"], dm.lw(1)),
    ):
        ax.plot(
            minutes,
            frame["T_bhe_f_out [°C]"].to_numpy()[window],
            color=color,
            linewidth=width,
            label=label,
            drawstyle="steps-post",
        )
    ax.set_xlabel(f"time [min] (shaded: compressor on, {ON_MIN} min on / {OFF_MIN} min off)")
    ax.set_ylabel("borehole outlet temperature [°C]")
    ax.set_xlim(0, 120)
    ax.grid(True, alpha=0.25, linewidth=GRIDLINE)
    ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-1))

    out = static_path("ground_cycling_outlet.svg").with_suffix("")
    finalize(fig, out, formats=("svg", "png"))
    plt.close(fig)
    print(f"wrote {out}.svg")


def main() -> None:
    apply_style("report", hashsalt="tmhp.visualization.ground-transient-response")
    step_response_figure()
    cycling_figure()


if __name__ == "__main__":
    main()
