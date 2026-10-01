"""Sampling specification and output location for the random operating-point run.

The machine itself (capacity, refrigerant, coil conductances) and the BASE
efficiency constants are *not* redefined here: they are re-exported from
``validation.compressor_efficiency_sensitivity_simple.config`` so that the two
studies always describe the same heat pump.
"""

from __future__ import annotations

from pathlib import Path

from validation.compressor_efficiency_sensitivity_simple.config import (
    CAPACITY_W,
    CASE_ORDER,
    ETA_BASE,
    REF,
    UA_RATED,
)

__all__ = [
    "CAPACITY_W",
    "CASE_ORDER",
    "CASE_SLUG",
    "ETA_BASE",
    "N_POINTS",
    "OUT_DIR",
    "PLR_RANGE",
    "REF",
    "REPO_ROOT",
    "SEED",
    "T_IU_RANGE",
    "T_OU_RANGE",
    "UA_RATED",
]

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
OUT_DIR: Path = REPO_ROOT / "validation" / "data" / "operating_point_sample"

#: number of operating points drawn in a full run
N_POINTS: int = 10_000
#: seed of :func:`numpy.random.default_rng`; fixes the whole sample
SEED: int = 20260917

#: requested part-load ratio, relative to the fixed ``CAPACITY_W`` nameplate.
#: 1.50 is deliberately beyond what the machine can deliver at low ambient --
#: those points come back as ``capacity_clamped == "max"`` and that limit is
#: itself a result.
PLR_RANGE: tuple[float, float] = (0.10, 1.50)
#: outdoor air temperature ``T0`` [°C]
T_OU_RANGE: tuple[float, float] = (-20.0, 40.0)
#: room air temperature ``T_a_room`` [°C]
T_IU_RANGE: tuple[float, float] = (15.0, 30.0)

#: filename slug per case (ASCII, greek letters spelled out) -- the sole
#: naming authority for the archived CSVs. ``uniform_<Nk>_points_<slug>.csv``,
#: e.g. ``uniform_10k_points_default_cmp_eff.csv`` for BASE and
#: ``uniform_10k_points_etais_nstar.csv`` for the N-I case (isentropic
#: efficiency driven by rotor speed). ``<eff>_<driver>`` names which
#: efficiency was replaced by a shape and which state variable drives it;
#: BASE gets the descriptive exception because it has neither.
CASE_SLUG: dict[str, str] = {
    "BASE": "default_cmp_eff",
    "N-V": "etav_nstar",
    "N-I": "etais_nstar",
    "N-E": "etaem_nstar",
    "P-V": "etav_pr",
    "P-I": "etais_pr",
    "P-E": "etaem_pr",
}
assert set(CASE_SLUG) == set(CASE_ORDER), "CASE_SLUG drifted from the sensitivity study's CASE_ORDER"
