"""Constants shared by the sweep, metrics, figures and publisher.

Kept free of any ``tmhp`` import so the figure module can be executed in a
plain plotting environment (the dartwork-mpl validation subprocess) against
the CSVs alone.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "data" / "compressor_efficiency_sensitivity_simple"
FIG_DIR = OUT_DIR / "figures"

CAPACITY_W = 3500.0
REF = "R32"
#: duty -> (outdoor air ``T0`` [°C], room air ``T_a_room`` [°C]); the fixed-boundary conditions.
BOUNDARY: dict[str, tuple[float, float]] = {"heating": (7.0, 20.0), "cooling": (35.0, 27.0)}
DUTIES = tuple(BOUNDARY)
#: requested PLR, computed 1.000 → 0.100 in 0.025 steps; figures sort ascending.
PLR_GRID = tuple(float(v) for v in np.round(np.arange(1.0, 0.0999, -0.025), 3))

#: BASE constants, keyed by the constructor keyword of :class:`tmhp.AirSourceHeatPump`.
ETA_BASE: dict[str, float] = {"eta_cmp_vol": 0.95, "eta_cmp_isen": 0.70, "eta_cmp": 0.90}
EFF_KEYS = tuple(ETA_BASE)

#: relative quadratic multiplier  1 − a (x − x_c)²
N_STAR_C = 0.60
A_N = 0.60
PR_C = 2.00
A_P = 0.60
#: harness safety clip on every synthetic efficiency; a clipped *reported* row is flagged.
ETA_CLIP = (0.1, 1.0)
#: above n* = 1.15 the n* multiplier is held at its n* = 1.15 value. Every reported
#: operating point lies below it (the sweep peaks at n* 1.13, N-V heating at PLR 1.0), and
#: 1.15 sits just below n* ≈ 1.17 where η_v(n*)·n* -- i.e. refrigerant mass flow -- would start to
#: fall with speed. The hold exists because the speed solver brackets rps up to rps_max
#: (n* 2.5); with the bare quadratic capacity is non-monotonic in speed there and the
#: bracket misses the real root (observed: N-V clamped to rps_max at every PLR).
N_STAR_HOLD = 1.15

#: case -> (varied efficiency, driver)
CASES: dict[str, tuple[str, str]] = {
    "N-V": ("eta_cmp_vol", "n_star"),
    "N-I": ("eta_cmp_isen", "n_star"),
    "N-E": ("eta_cmp", "n_star"),
    "P-V": ("eta_cmp_vol", "p_r"),
    "P-I": ("eta_cmp_isen", "p_r"),
    "P-E": ("eta_cmp", "p_r"),
}
CASE_ORDER = ("BASE", *CASES)

EFF_LABEL = {"eta_cmp_vol": r"$\eta_v$", "eta_cmp_isen": r"$\eta_{is}$", "eta_cmp": r"$\eta_{em}$"}
EFF_TEXT = {"eta_cmp_vol": "η_v", "eta_cmp_isen": "η_is", "eta_cmp": "η_em"}
DRIVER_LABEL = {"n_star": r"$n^*$", "p_r": r"$P_r$"}
DRIVER_TEXT = {"n_star": "n*", "p_r": "P_r"}
