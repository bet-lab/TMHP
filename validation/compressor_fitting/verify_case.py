"""Re-evaluate frozen applied scales and separately report excluded solver-edge rows."""

import hashlib
import json
from dataclasses import asdict

import numpy as np
from case import ROOT, catalogue, make_model, points
from run_case import metrics, save

from tmhp.calibration import EfficiencyScales, scaled_efficiencies


def main():
    """Check final predictions using the public adapter without repeating calibration."""
    report = json.loads((ROOT / "fit_report.json").read_text())
    hp = make_model()
    edge = catalogue("diagnostic_solver_edge")
    edge_before = hp.predict_catalogue(points(edge), mode="heating")
    hp.eta_cmp_vol, hp.eta_cmp_isen, hp.eta_cmp = scaled_efficiencies(
        (hp.eta_cmp_vol, hp.eta_cmp_isen, hp.eta_cmp), EfficiencyScales(**report["applied_scales"])
    )
    checked = 0
    for split, saved in [
        ("calibration", report["final_points"]),
        ("holdout", json.loads((ROOT / "holdout_after.json").read_text())),
    ]:
        actual = hp.predict_catalogue(points(catalogue(split)), mode="heating")
        assert all(row.valid for row in actual), f"{split}: invalid repeated prediction"
        for key in ("COP_model", "Q_model_W", "power_W", "speed_rps", "pressure_ratio", "h2_J_kg"):
            np.testing.assert_allclose(
                [getattr(p, key) for p in actual], [p[key] for p in saved], rtol=1e-10, atol=1e-8
            )
        checked += len(actual)
    edge_after = hp.predict_catalogue(points(edge), mode="heating")
    save(
        "diagnostic_solver_edge.json",
        {
            "excluded_before_fitting": True,
            "reason": "Whole outdoor24 C group excluded after one baseline optimizer failure, before COP fitting",
            "before": {"metrics": metrics(edge_before), "points": [asdict(p) for p in edge_before]},
            "after": {"metrics": metrics(edge_after), "points": [asdict(p) for p in edge_after]},
        },
    )
    files = [
        "src/tmhp/calibration.py",
        "src/tmhp/compressor_baseline.py",
        "src/tmhp/air_source_heat_pump.py",
        "validation/compressor_fitting/case.py",
        "validation/compressor_fitting/catalogue_points.csv",
        "validation/compressor_fitting/fit_report.json",
        "validation/compressor_fitting/metrics.json",
    ]
    repo = ROOT.parents[1]
    save(
        "reproduction_check.json",
        {
            "method": "Public predict_catalogue repeated with frozen applied scales; not a second optimizer run",
            "points_rechecked": checked,
            "rtol": 1e-10,
            "atol": 1e-8,
            "final_predictions_reproduced": True,
            "all_stages_completed": all(stage["status"] == "completed" for stage in report["stages"]),
            "hashes": {name: hashlib.sha256((repo / name).read_bytes()).hexdigest() for name in files},
        },
    )
    save("baseline_predictions.json", report["initial_points"])
    print(
        f"Reproduced {checked} final points; solver-edge validity {sum(p.valid for p in edge_before)}/5 -> {sum(p.valid for p in edge_after)}/5"
    )


if __name__ == "__main__":
    main()
