"""Export the latest released fan-airflow fits into the installed package (issue #86).

Runtime needs, per stratum, the selected law, its observed capacity range, its evidence and its hold-out
error; candidate laws and held-out predictions stay in regression.json. Only a build recorded in
releases.json is exported. Strata that miss the shipping gate are exported with meets_shipping_gate=false
so the runtime can refuse them with a reason instead of silently falling back.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
TARGET = ROOT / "src/tmhp/data/fan_airflow_regression.json"
CV_FIELDS = ("n", "MAPE_pct", "RMSE", "bias_pct")


def export() -> dict:
    report = json.loads((HERE / "regression.json").read_text())
    release = json.loads((HERE / "releases.json").read_text())[-1]
    if release["data_sha256"] != report["data_sha256"]:
        raise ValueError("regression.json is not the latest release; run build --release first")
    output = {
        "version": release["version"],
        "data_sha256": report["data_sha256"],
        "law_capacity_unit": "kW",
        "output_unit": "m3/s",
        "meaning": "largest published fan airflow (High/Max step, or the single printed outdoor value)",
        "shipping_gate": report["shipping_gate"],
        "strata": {},
    }
    for key, fit in sorted(report["fits"].items()):
        cv = fit["nested_family_cv"]
        output["strata"][key] = {
            "selected_law": fit["selected_law"],
            "capacity_range_W": fit["capacity_range_W"],
            "n": fit["n"],
            "n_families": len(fit["families"]),
            "manufacturers": fit["manufacturers"],
            "nested_family_cv": {k: cv[k] for k in CV_FIELDS} if cv.get("valid") else cv,
            "meets_shipping_gate": fit["meets_shipping_gate"],
            "status": fit["status"],
            "status_reason": fit["status_reason"],
            "source_ids": fit["source_ids"],
        }
    TARGET.write_text(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return output


if __name__ == "__main__":
    result = export()
    print(json.dumps({"version": result["version"], "strata": sorted(result["strata"])}, ensure_ascii=False))
