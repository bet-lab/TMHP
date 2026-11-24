"""Emit the compressor-efficiency-map widget's data from the library (#253).

Every number comes from ``tmhp.compressor_efficiency`` (coefficients, version, data domain, map
functions) or from the measured ``points.csv`` of that coefficient version; nothing is retyped here.
The widget evaluates the maps in JavaScript from the same coefficients, so the file also carries
``check_points`` the library evaluated: ``tests/test_docs_compressor_map_data.py`` makes the JS
evaluator reproduce them. The data and evaluation contract is
``docs/notes/compressor-map-widget-contract.md``.

The output is deterministic (no timestamps, no commit hashes) so the tracked JSON can be diffed.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

from scripts.data._common import write_json
from tmhp.compressor_efficiency import (
    COEFFICIENT_VERSION,
    DOMAIN,
    MAP_COEFFICIENTS,
    map_eta_ov,
    map_eta_v,
    map_f_loss,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
POINTS_CSV = REPO_ROOT / "validation" / "coefficients" / COEFFICIENT_VERSION / "points.csv"
OUTPUT = "compressor-maps.json"

SCHEMA = 1

VARIABLES = {
    "l": "natural log of the pressure ratio r_p = P_cmp,out / P_cmp,in",
    "N": "shaft speed [rev/s], as is",
    "T_evap": "evaporating saturation temperature [degC], as is",
}
TERMS = {
    "eta_v": ["1", "l", "l^2", "l^3", "N", "N^2", "l*N", "T_evap"],
    "eta_ov": ["1", "l", "l^2", "l^3", "N", "N^2", "l*N", "T_evap"],
    "f_loss": ["1", "l", "l^2", "l^3", "N", "N^2", "l*N"],
}
SOURCES = {
    "data": "ossorio_thesis_appendix1",
    "same_tests": "ossorio_navarro_2023",
    "published_correlations": "ossorio_2024",
}

# The cases the contract names, in its order: (PR, N [rev/s], T_evap [degC]).
NAMED_CHECK_POINTS = [
    (3.785, 70.0, 0.0),  # the eta_ov peak
    (1.5, 70.0, 25.0),  # eta_v > 1, shown raw
    (1.0, 70.0, 0.0),  # l = 0
    (1.2, 70.0, 0.0),  # inside the speed range, outside the hull
    (3.0, 15.0, 0.0),  # below 30 rev/s
    (3.0, 120.0, 0.0),  # above 110 rev/s
    (12.0, 70.0, -30.0),  # high lift near the hull edge
    (3.0, 70.0, -10.0),  # the T_evap effect the module docstring quotes
    (3.0, 70.0, 15.0),
]
# The further grid, across PR 1.3-12, N 15-120, T_evap -30...25.
GRID_PR = (1.3, 2.5, 4.0, 7.0, 12.0)
GRID_N = (15.0, 40.0, 70.0, 120.0)
GRID_T = (-30.0, -5.0, 25.0)


def _hull_vertices() -> list[list[float]]:
    return [[float(pr), float(t)] for pr, t in DOMAIN.hull_PR_Te.vertices]


def check_point(PR: float, N_rps: float, T_evap_C: float) -> dict[str, Any]:
    """One point evaluated by the library; raw map values, ``eta_is`` null where 1 - f_loss <= 0."""
    eta_v = map_eta_v(PR, N_rps, T_evap_C)
    eta_ov = map_eta_ov(PR, N_rps, T_evap_C)
    f_loss = map_f_loss(PR, N_rps)
    eta_is = eta_ov / (1.0 - f_loss) if 1.0 - f_loss > 0.0 else None
    return {
        "PR": PR,
        "N_rps": N_rps,
        "T_evap_C": T_evap_C,
        "eta_v": eta_v,
        "eta_ov": eta_ov,
        "f_loss": f_loss,
        "eta_is": eta_is,
        "in_domain": DOMAIN.contains(PR, N_rps, T_evap_C),
    }


def build_check_points() -> list[dict[str, Any]]:
    cases = list(NAMED_CHECK_POINTS)
    # One hull vertex: the boundary counts as inside. N is mid-range so only the hull decides.
    pr_v, t_v = DOMAIN.hull_PR_Te.vertices[0]
    cases.append((float(pr_v), 70.0, float(t_v)))
    cases += [(pr, n, t) for pr in GRID_PR for n in GRID_N for t in GRID_T]
    return [check_point(*case) for case in cases]


def find_peak_eta_ov() -> dict[str, float]:
    """argmax of eta_ov over (PR, N) inside the data domain at T_evap = 0 degC.

    ``T_evap`` enters eta_ov additively, so the location does not depend on it; the domain does, and
    T_evap = 0 is the slice used. A fine grid over the bounding box, masked to the domain, picks the
    start; a bounded optimiser refines it, and the refinement is kept only if it stays inside.
    """
    prs = [pr for pr, _ in DOMAIN.hull_PR_Te.vertices]
    n_lo, n_hi = DOMAIN.rps_range
    bounds = [(min(prs), max(prs)), (n_lo, n_hi)]

    pr_grid = np.linspace(*bounds[0], 801)
    n_grid = np.linspace(*bounds[1], 801)
    # Only points inside the data domain at T_evap = 0 count: the bounding box of the hull is wider
    # than the hull, and a future map could peak in the corner the hull leaves out.
    values = np.array(
        [[map_eta_ov(pr, n, 0.0) if DOMAIN.contains(pr, n, 0.0) else -np.inf for n in n_grid] for pr in pr_grid]
    )
    i, j = np.unravel_index(int(np.argmax(values)), values.shape)

    result = minimize(
        lambda x: -map_eta_ov(float(x[0]), float(x[1]), 0.0),
        x0=[pr_grid[i], n_grid[j]],
        bounds=bounds,
        method="L-BFGS-B",
        options={"ftol": 1e-15, "gtol": 1e-12},
    )
    pr, n = float(result.x[0]), float(result.x[1])
    # Keep the optimum only when it is not worse than the grid start (it is a refinement of it).
    if not DOMAIN.contains(pr, n, 0.0) or map_eta_ov(pr, n, 0.0) < values[i, j]:
        pr, n = float(pr_grid[i]), float(n_grid[j])
    # The map is very flat along N near its peak (Hessian eigenvalue ~ -5e-5): optimiser starts that
    # differ in the grid change N in the fourth decimal. Round to what the optimum determines
    # (r_p to 1e-4, N to 0.01 rev/s) so the tracked JSON does not depend on the optimiser's path.
    return {"PR": round(pr, 4), "N_rps": round(n, 2)}


def read_measured_points() -> list[dict[str, Any]]:
    with POINTS_CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        {
            "id": row["point_id"],
            "T_evap_C": float(row["T_evap_C"]),
            "T_cond_C": float(row["T_cond_C"]),
            "N_rps": float(row["N_rps"]),
            "PR": float(row["PR"]),
            "eta_v": float(row["eta_v"]),
            "eta_ov": float(row["eta_ov"]),
            "f_loss": float(row["f_loss"]),
        }
        for row in rows
    ]


def build_payload() -> dict[str, Any]:
    n_lo, n_hi = DOMAIN.rps_range
    peak = find_peak_eta_ov()
    if not all(math.isfinite(v) for v in peak.values()):
        raise SystemExit(f"peak search failed: {peak}")
    return {
        "schema": SCHEMA,
        "coefficient_version": COEFFICIENT_VERSION,
        "variables": VARIABLES,
        "terms": TERMS,
        "coefficients": {name: list(values) for name, values in MAP_COEFFICIENTS.items()},
        "domain": {"N_rps": [float(n_lo), float(n_hi)], "hull_PR_Te": _hull_vertices()},
        "peak_eta_ov": peak,
        "measured_points": read_measured_points(),
        "check_points": build_check_points(),
        "sources": SOURCES,
    }


def main() -> None:
    payload = build_payload()
    path = write_json(OUTPUT, payload)
    peak = payload["peak_eta_ov"]
    print(
        f"wrote {path}: {len(payload['measured_points'])} measured points, "
        f"{len(payload['check_points'])} check points, peak eta_ov at r_p={peak['PR']}, N={peak['N_rps']}"
    )


if __name__ == "__main__":
    main()
