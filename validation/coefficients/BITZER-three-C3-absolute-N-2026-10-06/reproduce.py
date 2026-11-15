"""Refit the shipped scroll coefficients from tracked raw points and compare.

``BITZER-three-C3-absolute-N-2026-10-06`` was fitted to 3,899 valid scroll points
of the BITZER web software: the 2,467 of ``validation/bitzer_efficiency_pilot/raw/
bitzer_raw_points.csv`` and 1,432 low-lift points (SST 10/15 degC, SDT 25-55 degC,
35-75 Hz, 13 compressor-refrigerant combinations; 1,638 requests) that were not
tracked. ``fit_input_lowlift_raw.csv`` is that low-lift set, requested again on
2026-11-15 with the pilot's own ``scripts/batch.py`` (``BZ_SST=10,15
BZ_SDT=25,30,35,40,45,50,55``). Thirteen points requested again inside the
tracked grid returned the tracked values unchanged.

Each efficiency is an ordinary least-squares fit of
``b0 + b1 PR + b2 N + b3 PR^2 + b4 N^2 + b5 PR N`` to the values derived by the
pilot's ``scripts/derive.py``. This script repeats that fit and writes
``reproduction.json``: refitted coefficients, shipped coefficients, and the
largest difference in predicted efficiency over the fitting points.

    uv run python validation/coefficients/BITZER-three-C3-absolute-N-2026-10-06/reproduce.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PILOT = ROOT / "validation" / "bitzer_efficiency_pilot"
sys.path.insert(0, str(PILOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from derive import derive  # noqa: E402

from tmhp import compressor_efficiency as shipped  # noqa: E402

TOLERANCE = 1e-9  # predicted efficiency, absolute


def valid_scroll_rows(path: Path) -> list[dict]:
    with path.open() as fh:
        rows = [derive(r) for r in csv.DictReader(fh) if r["compressor_type"] == "scroll"]
    return [r for r in rows if r.get("valid_bitzer") == "yes"]


def main() -> int:
    tracked = valid_scroll_rows(PILOT / "raw" / "bitzer_raw_points.csv")
    lowlift = valid_scroll_rows(HERE / "fit_input_lowlift_raw.csv")
    rows = tracked + lowlift
    pr = np.array([float(r["pressure_ratio"]) for r in rows])
    n = np.array([float(r["N_rps_assumed"]) for r in rows])
    design = np.column_stack([np.ones(len(rows)), pr, n, pr**2, n**2, pr * n])
    report: dict = {
        "coefficient_version": shipped.COEFFICIENT_VERSION,
        "points": {"tracked_pilot": len(tracked), "lowlift_regenerated": len(lowlift), "total": len(rows)},
        "pressure_ratio": [float(pr.min()), float(pr.max())],
        "speed_rps": [float(n.min()), float(n.max())],
        "shipped_validity_box": {
            "pressure_ratio": [shipped.PR_FIT_MIN, shipped.PR_FIT_MAX],
            "speed_rps": [shipped.RPS_FIT_MIN, shipped.RPS_FIT_MAX],
        },
        "tolerance_abs": TOLERANCE,
        "efficiencies": {},
    }
    worst = 0.0
    for key, coef_shipped in (
        ("eta_v", shipped.COEFFICIENTS_ETA_V),
        ("eta_is", shipped.COEFFICIENTS_ETA_IS),
        ("eta_em", shipped.COEFFICIENTS_ETA_EM),
    ):
        y = np.array([float(r[key]) for r in rows])
        refit = np.linalg.lstsq(design, y, rcond=None)[0]
        difference = float(np.abs(design @ refit - design @ np.array(coef_shipped)).max())
        worst = max(worst, difference)
        report["efficiencies"][key] = {
            "refit": [float(c) for c in refit],
            "shipped": list(coef_shipped),
            "max_abs_prediction_difference": difference,
            "rmse": float(np.sqrt(np.mean((design @ refit - y) ** 2))),
        }
        print(f"{key}: max |refit - shipped| over {len(rows)} points = {difference:.3e}")
    report["reproduced"] = worst <= TOLERANCE
    (HERE / "reproduction.json").write_text(json.dumps(report, indent=2) + "\n")
    print("reproduced" if report["reproduced"] else "NOT reproduced", f"(points {len(rows)})")
    return 0 if report["reproduced"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
