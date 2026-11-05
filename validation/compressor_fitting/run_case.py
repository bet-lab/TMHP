"""Run the public catalogue API and preserve calibration/holdout evidence."""

import json
import platform
import time
from dataclasses import asdict

import CoolProp
import numpy as np
import pandas as pd
from case import ASSUMPTIONS, EQUIPMENT_SETTINGS, HEATING_REFERENCE_W, ROOT, catalogue, make_model, points

from tmhp.compressor_baseline import BASELINE_VERSION


def metrics(rows):
    """Report all-row RMSE; a failed point prevents a success-only metric."""
    good = [r for r in rows if r.valid]
    result = {"requested": len(rows), "valid": len(good), "failed": len(rows) - len(good)}
    if len(good) != len(rows):
        return {**result, "COP_RMSE": None, "power_RMSE_W": None}
    ce = np.array([r.COP_model - r.COP_catalogue for r in rows])
    pe = np.array([r.power_W - r.Q_requested_W / r.COP_catalogue for r in rows])
    return {**result, "COP_RMSE": float(np.sqrt(np.mean(ce**2))), "power_RMSE_W": float(np.sqrt(np.mean(pe**2)))}


def save(name, payload):
    """Serialize only finite JSON numbers to the reproducible case directory."""
    (ROOT / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def main():
    """Fit four fixed rows and evaluate sixteen held-out indoor-temperature rows."""
    start = time.monotonic()
    train, test = catalogue("calibration"), catalogue("holdout")
    hp = make_model()
    print("Evaluate uncalibrated holdout", flush=True)
    held_before = hp.predict_catalogue(points(test), mode="heating")
    save("holdout_before.json", [asdict(p) for p in held_before])
    print("Fit four catalogue rows; frozen equipment and partition; library default lambda bounds", flush=True)
    report = hp.fit(
        points(train),
        mode="heating",
        parameters=("overall_efficiency", "volumetric_efficiency", "heat_loss_fraction"),
        baseline_label=BASELINE_VERSION,
    )
    save("fit_report.json", asdict(report))
    save("baseline_predictions.json", [asdict(p) for p in report.initial_points])
    print("Fit finished", report.success, asdict(report.candidate_scales), flush=True)
    if not report.applied:
        for name in ("metrics.json", "point_predictions.csv", "holdout_after.json", "run_manifest.json"):
            (ROOT / name).unlink(missing_ok=True)
        raise RuntimeError(f"Calibration was not applied: {report.reason}; see fit_report.json")
    held_after = hp.predict_catalogue(points(test), mode="heating")
    save("holdout_after.json", [asdict(p) for p in held_after])
    result = {
        "calibration": {"before": metrics(report.initial_points), "after": metrics(report.final_points)},
        "holdout": {"before": metrics(held_before), "after": metrics(held_after)},
    }
    for split in result.values():
        before, after = split["before"]["COP_RMSE"], split["after"]["COP_RMSE"]
        split["COP_RMSE_improvement_pct"] = (before - after) / before * 100 if before and after is not None else None
    save("metrics.json", result)
    records = []
    for split, frame, stages in [
        (
            "calibration",
            train,
            [
                ("before", report.initial_points),
                *[(s.role, s.points) for s in report.stages],
                ("after", report.final_points),
            ],
        ),
        ("holdout", test, [("before", held_before), ("after", held_after)]),
    ]:
        for stage, rows in stages:
            records.extend(
                dict(point_id=pid, split=split, stage=stage, **asdict(p)) for pid, p in zip(frame.id, rows, strict=True)
            )
    pd.DataFrame(records).to_csv(ROOT / "point_predictions.csv", index=False)
    save(
        "run_manifest.json",
        dict(
            python=platform.python_version(),
            CoolProp=CoolProp.__version__,
            baseline=BASELINE_VERSION,
            equipment=EQUIPMENT_SETTINGS,
            bounds=report.lambda_bounds,
            heating_reference_W=HEATING_REFERENCE_W,
            catalogue_model="ASYG30LMTA/AOYG30LMTA",
            calibration_ids=train.id.tolist(),
            holdout_ids=test.id.tolist(),
            elapsed_s=time.monotonic() - start,
            assumptions=ASSUMPTIONS,
        ),
    )
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
