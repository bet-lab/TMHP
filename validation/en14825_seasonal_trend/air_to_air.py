"""Air-to-air EN 14825 trend (19 Keymark models): run and stamp the coefficient version."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tmhp.compressor_efficiency import COEFFICIENT_VERSION
from validation.analysis import ashp_trend

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "validation" / "data"


def main() -> None:
    sys.argv = [sys.argv[0]]  # ashp_trend parses its own CLI; run the full computation
    ashp_trend.main()
    stamp = {
        "product": "en14825_seasonal_trend/air_to_air",
        "coefficient_version": COEFFICIENT_VERSION,
        "files": ["ashp_en14825_points.csv", "ashp_en14825_verdict.csv", "ashp_part_load_sweep.csv"],
    }
    (DATA / "en14825_seasonal_trend_a2a_manifest.json").write_text(json.dumps(stamp, indent=1))
    print(f"stamped coefficient version {COEFFICIENT_VERSION}")


if __name__ == "__main__":
    main()
