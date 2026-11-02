"""Export real public-fit search calls separately from the frozen presentation results."""

import csv
import hashlib
import json
import math
import platform
import time
from dataclasses import asdict
from pathlib import Path

import CoolProp
from case import ASSUMPTIONS, EQUIPMENT_SETTINGS, ROOT, catalogue, make_model, points
from run_case import metrics

from tmhp.compressor_baseline import BASELINE_VERSION

OUTPUT = ROOT / "search_trace"


def write_json(name: str, payload: object) -> None:
    """Write strict JSON under the isolated trace export directory only."""
    (OUTPUT / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def write_csv(name: str, rows: list[dict]) -> None:
    """Write ordered call/point rows with JSON-encoded collection fields."""
    with (OUTPUT / name).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v) if isinstance(v, (list, tuple, dict)) else v for k, v in row.items()})


def digest(path: Path) -> str:
    """Hash a source or frozen result to prove provenance and preservation."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare_predictions(actual, expected: list[dict]) -> None:
    """Require all frozen requested rows and their physical predictions to reproduce."""
    assert len(actual) == len(expected)
    for new, old in zip(actual, expected, strict=True):
        for name, value in asdict(new).items():
            prior = old[name]
            if isinstance(value, float) and prior is not None:
                assert math.isclose(value, prior, rel_tol=1e-10, abs_tol=1e-8), (name, value, prior)
            elif isinstance(value, tuple):
                assert list(value) == prior, (name, value, prior)
            else:
                assert value == prior, (name, value, prior)


def main() -> None:
    """Run four calibration rows and sixteen holdouts; export actual calls without interpolation."""
    OUTPUT.mkdir(exist_ok=True)
    started = time.monotonic()
    preserved = [
        "fit_report.json",
        "metrics.json",
        "holdout_before.json",
        "holdout_after.json",
        "point_predictions.csv",
    ]
    original_hashes = {name: digest(ROOT / name) for name in preserved}
    original = json.loads((ROOT / "fit_report.json").read_text())
    train, holdout = catalogue("calibration"), catalogue("holdout")
    hp = make_model()
    print("Actual API search: four catalogue rows, library default bounds", flush=True)
    report = hp.fit(
        points(train),
        mode="heating",
        parameters=("overall_efficiency", "volumetric_efficiency", "heat_loss_fraction"),
        baseline_label=BASELINE_VERSION,
    )
    write_json("fit_report.json", asdict(report))
    write_json(
        "trace.json",
        {
            "schema_version": 1,
            "catalogue_model": "ASYG30LMTA/AOYG30LMTA",
            "mode": "heating",
            "calibration_ids": train.id.tolist(),
            "calibration_points": points(train),
            "unique_evaluations": report.evaluations,
            "call_count": len(report.trace),
            "events": [asdict(entry) for entry in report.trace],
            "notes": [
                "Sequence is actual evaluate-call order, including cache reads; no interpolated scales are recorded",
                "candidate_id identifies exact scale tuples; cache_hit means no new physics solve",
                "objective_returned is a callback return, not a claim about SciPy internal acceptance",
                "COP objective defers discharge_state to loss feasibility; constraint_rmse requires every final constraint",
                "Only actual fitting calls use the four calibration rows; holdouts are not used to choose scales",
            ],
        },
    )
    calls, point_rows = [], []
    for entry in report.trace:
        row = asdict(entry)
        point_results = row.pop("points")
        row.update(row.pop("scales"))
        calls.append(row)
        for index, (point_id, result) in enumerate(zip(train.id, point_results, strict=True)):
            point_rows.append(
                {**row, "point_index": index, "point_id": point_id, **{f"point_{k}": v for k, v in result.items()}}
            )
    write_csv("calls.csv", calls)
    write_csv("points.csv", point_rows)
    print(f"Trace exported: {len(report.trace)} calls / {report.evaluations} unique candidates", flush=True)
    if not report.applied:
        raise RuntimeError(f"Fit was not applied: {report.reason}; diagnostic trace retained")
    assert asdict(report.applied_scales) == original["applied_scales"]
    assert report.evaluations == original["evaluations"]
    assert math.isclose(report.final_cop_rmse, original["final_cop_rmse"], rel_tol=1e-12)
    compare_predictions(report.initial_points, original["initial_points"])
    compare_predictions(report.final_points, original["final_points"])
    for stage, expected in zip(report.stages, original["stages"], strict=True):
        assert asdict(stage.scales) == expected["scales"]
        compare_predictions(stage.points, expected["points"])
    print("Check sixteen held-out rows before and after, outside fitting", flush=True)
    before = make_model().predict_catalogue(points(holdout), mode="heating")
    after = hp.predict_catalogue(points(holdout), mode="heating")
    compare_predictions(before, json.loads((ROOT / "holdout_before.json").read_text()))
    compare_predictions(after, json.loads((ROOT / "holdout_after.json").read_text()))
    write_json(
        "holdout.json",
        {"ids": holdout.id.tolist(), "before": [asdict(p) for p in before], "after": [asdict(p) for p in after]},
    )
    write_json(
        "metrics.json",
        {
            "calibration": {"before": metrics(report.initial_points), "after": metrics(report.final_points)},
            "holdout": {"before": metrics(before), "after": metrics(after)},
        },
    )
    assert original_hashes == {name: digest(ROOT / name) for name in preserved}
    write_json(
        "manifest.json",
        {
            "schema_version": 1,
            "python": platform.python_version(),
            "CoolProp": CoolProp.__version__,
            "baseline": BASELINE_VERSION,
            "equipment": EQUIPMENT_SETTINGS,
            "assumptions": ASSUMPTIONS,
            "calibration_rows": len(train),
            "holdout_rows": len(holdout),
            "call_count": len(report.trace),
            "unique_evaluations": report.evaluations,
            "cache_hits": sum(entry.cache_hit for entry in report.trace),
            "final_scales": asdict(report.applied_scales),
            "final_cop_rmse": report.final_cop_rmse,
            "frozen_final_and_stage_predictions_reproduced": True,
            "frozen_files_unchanged": original_hashes,
            "source_hashes": {
                name: digest(ROOT.parents[1] / name)
                for name in [
                    "src/tmhp/calibration.py",
                    "validation/compressor_fitting/case.py",
                    "validation/compressor_fitting/run_search_trace.py",
                ]
            },
            "elapsed_s": time.monotonic() - started,
        },
    )
    print(
        json.dumps(
            {"scales": asdict(report.applied_scales), "rmse": report.final_cop_rmse, "output": str(OUTPUT)}, indent=2
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
