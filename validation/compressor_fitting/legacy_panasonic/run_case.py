"""Run the public catalogue API and preserve calibration/holdout evidence."""

import json
import platform
import time
from dataclasses import asdict

import CoolProp
import numpy as np
import pandas as pd
from case import CALIBRATION_BOUNDS, EQUIPMENT_SETTINGS, ROOT, catalogue, make_model, points

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
    """Fit six fixed rows and evaluate three unseen outdoor-temperature rows."""
    start = time.monotonic()
    train, test = catalogue("calibration"), catalogue("holdout")
    hp = make_model()
    print("Evaluate uncalibrated holdout", flush=True)
    held_before = hp.predict_catalogue(points(test), mode="heating")
    save("holdout_before.json", [asdict(p) for p in held_before])
    print("Fit six catalogue rows; frozen equipment and partition", flush=True)
    report = hp.fit(
        points(train),
        mode="heating",
        parameters=("overall_efficiency", "volumetric_efficiency", "heat_loss_fraction"),
        lambda_bounds=CALIBRATION_BOUNDS,
        baseline_label=BASELINE_VERSION,
    )
    save("fit_report.json", asdict(report))
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
            bounds=CALIBRATION_BOUNDS,
            calibration_ids=train.id.tolist(),
            holdout_ids=test.id.tolist(),
            elapsed_s=time.monotonic() - start,
            assumptions=[
                "Outdoor DB=printed WB+1 K, not a measured dry bulb",
                "UA derived with assumed effectiveness 0.70/0.65",
                "44.9/50 W are fan reference inputs; actual fan powers follow existing TMHP fan curves",
                "Airflow limits 20–100% reference and approach 1–35 K are numerical case assumptions",
                "R290 scroll map transferred to R32 rotary; not independently identified compressor efficiencies",
                "Speed 30–110 rev/s restricts to source-map speed support, not manufacturer-certified envelope",
            ],
        ),
    )
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
