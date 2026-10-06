"""Emit the documentation site's validation data from the parity harness output.

This used to rebuild one hard-coded unit by re-running the model here, with its
own copy of the parameter set. That copy had drifted away from the library
defaults, so the error figures published on the site were not the errors a user
of the library would get -- the exact failure mode the whole exercise exists to
prevent.

Now it reads ``validation/results/``, which
``uv run python -m validation.parity.run`` writes by applying the library
defaults unchanged. Adding a catalogue and rerunning the harness is enough; the
site follows.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from scripts.data._common import write_json

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS = REPO_ROOT / "validation" / "results"


def _clean(value):
    """JSON has no NaN; the widget reads null and shows a dash."""
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def build_validation_points() -> list[dict]:
    frames = [pd.read_csv(p) for p in sorted(RESULTS.glob("*.csv")) if p.name != "summary.csv"]
    if not frames:
        raise SystemExit("no results under validation/results/ -- run\n  uv run python -m validation.parity.run")
    df = pd.concat(frames, ignore_index=True)

    out: list[dict] = []
    for _, row in df.iterrows():
        usable = bool(row.usable)
        delta = (row.cop_pred - row.cop_target) / row.cop_target * 100.0 if usable and row.cop_target else float("nan")
        out.append(
            {
                "unit": row.unit,
                "slug": row.slug,
                "manufacturer": row.manufacturer,
                "model_class": row.model_class,
                "case_id": int(row.point_id),
                "refrigerant": row.refrigerant,
                "nominal_kw": _clean(float(row.nominal_kW)),
                "mode": row["mode"],
                "t_source_c": _clean(float(row.t_source_C)),
                "t_sink_c": _clean(float(row.t_sink_C)),
                "q_cat_kw": _clean(float(row.q_kW)),
                "cop_cat": _clean(float(row.cop_target)),
                "cop_mod": _clean(float(row.cop_pred)),
                "delta_pct": _clean(float(delta)),
                "failure_reason": row.failure_reason,
                "usable": usable,
            }
        )
    return out


def build_validation_summary() -> dict:
    summary_path = RESULTS / "summary.csv"
    if not summary_path.exists():
        raise SystemExit("validation/results/summary.csv missing -- run the parity harness")
    summary = pd.read_csv(summary_path)

    usable = int(summary.points_usable.sum())
    total = int(summary.points.sum())
    weighted = float((summary.cop_MAPE_pct * summary.points_usable).sum() / max(usable, 1))
    return {
        "units": int(len(summary)),
        "points": total,
        "points_evaluated": usable,
        "cop_mape_pct": round(weighted, 2),
        "refrigerants": sorted(summary.refrigerant.unique().tolist()),
        "capacity_range_kw": [
            float(summary.nominal_kW.min()),
            float(summary.nominal_kW.max()),
        ],
        "rows": [{k: _clean(v) for k, v in record.items()} for record in summary.to_dict(orient="records")],
    }


def main() -> None:
    points = build_validation_points()
    summary = build_validation_summary()
    write_json("validation-points.json", points)
    write_json("validation-summary.json", summary)
    print(
        f"validation-points.json: {len(points)} points across {summary['units']} units; "
        f"weighted COP MAPE {summary['cop_mape_pct']} %"
    )


if __name__ == "__main__":
    main()
