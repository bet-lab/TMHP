"""g-function vs. dimensionless time for rectangular borehole fields.

Compares three field geometries on a log-time axis:

  - single borehole (1×1)
  - 2×2 field at 6 m spacing
  - 4×4 field at 6 m spacing

All boreholes share the same depth (150 m) and radius (8 cm). The
log-spaced x-axis (``ln(t/t_s)`` with ``t_s = H²/(9α)``) is the
conventional way to present g-functions and lets the reader see both
the early-time single-borehole response and the late-time field
interference plateau.
"""

from __future__ import annotations

import sys
from pathlib import Path

import dartwork_mpl as dm
import matplotlib.pyplot as plt
import numpy as np

from tmhp.g_function import precompute_gfunction

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dmpl_common import COLORS, apply_style, finalize, static_path  # noqa: E402

# Ground / borehole parameters (representative values, not site-specific).
H_B = 150.0        # m, borehole length
D_B = 4.0          # m, buried depth
R_B = 0.08         # m, borehole radius
B   = 6.0          # m, borehole spacing
ALPHA_S = 1.0e-6   # m²/s
K_S     = 2.0      # W/(m K)
T_MAX_S = 30 * 365 * 24 * 3600.0  # 30 years
DT_S    = 3600.0


FIELDS = [
    ("1×1 (single)",   1, 1, COLORS["accent"]),
    ("2×2 field",      2, 2, COLORS["accent2"]),
    ("4×4 field",      4, 4, COLORS["hot"]),
]


def main() -> None:
    apply_style("report", hashsalt="tmhp.visualization.g-function-curve")

    # Sample the interpolators on a common log-time grid for plotting.
    t_eval = np.geomspace(3600.0, T_MAX_S, 200)
    t_s = H_B**2 / (9.0 * ALPHA_S)
    ln_t_ts = np.log(t_eval / t_s)

    fig, ax = plt.subplots(figsize=dm.figsize("13cm", "standard"))

    for label, n1, n2, color in FIELDS:
        g = precompute_gfunction(
            N_1=n1, N_2=n2,
            B=B, H_b=H_B, D_b=D_B, r_b=R_B,
            alpha_s=ALPHA_S, k_s=K_S,
            t_max_s=T_MAX_S, dt_s=DT_S,
        )
        # Multiply by 2π·k_s to recover the conventional dimensionless g.
        g_dim = g(t_eval) * (2 * np.pi * K_S)
        ax.plot(ln_t_ts, g_dim, color=color, linewidth=dm.lw(1), label=label)

    ax.set_xlabel(r"$\ln(t / t_s)$, with $t_s = H^2 / (9\alpha_s)$")
    ax.set_ylabel(r"$g(t/t_s, B/H)$ [-]")
    ax.grid(True, alpha=0.25, linewidth=dm.lw(-2))
    ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-1))

    ax.text(
        0.98, 0.05,
        f"$H = {H_B:.0f}$ m, $B = {B:.0f}$ m\n"
        f"$\\alpha_s = {ALPHA_S:.1e}$ m²/s",
        transform=ax.transAxes,
        va="bottom", ha="right",
        fontsize=dm.fs(-1),
        color=COLORS["ink"],
    )

    out = static_path("g_function_curve.svg").with_suffix("")
    finalize(fig, out)
    plt.close(fig)
    print(f"wrote {out}.svg")


if __name__ == "__main__":
    main()
