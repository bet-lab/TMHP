"""Air-to-water EN 14825 trend: run the trend + ablation and stamp the coefficient version."""

from __future__ import annotations

import json
from pathlib import Path

from tmhp.compressor_efficiency import COEFFICIENT_VERSION
from validation.analysis import en14825_trend

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "validation" / "data"


def main() -> None:
    en14825_trend.main()
    stamp = {
        "product": "en14825_seasonal_trend/air_to_water",
        "coefficient_version": COEFFICIENT_VERSION,
        "files": ["en14825_trend_points.csv", "en14825_trend_verdict.csv", "coefficient_ablation_parity.csv"],
    }
    (DATA / "en14825_seasonal_trend_a2w_manifest.json").write_text(json.dumps(stamp, indent=1))
    print(f"stamped coefficient version {COEFFICIENT_VERSION}")


if __name__ == "__main__":
    main()
