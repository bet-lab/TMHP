"""Export the latest released displacement fits into the installed package (issue #86).

Runtime needs only the selected law, its observed capacity range and its evidence; candidate laws
and held-out predictions stay in regression.json. Only a build recorded in releases.json is exported.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
TARGET = ROOT / "src/tmhp/data/compressor_displacement_regression.json"


def export() -> dict:
    report = json.loads((HERE / "regression.json").read_text())
    release = json.loads((HERE / "releases.json").read_text())[-1]
    if release["data_sha256"] != report["data_sha256"]:
        raise ValueError("regression.json is not the latest release; run build --release first")
    protocols = json.loads((HERE / "protocols.json").read_text())
    output = {
        "version": release["version"],
        "data_sha256": report["data_sha256"],
        "capacity_input_unit": "W",
        "law_capacity_unit": "kW",
        "output_unit": "cm3/rev",
        "capacity_duty": "cooling",
        "protocols": report["protocols"],
        "selection": report["selection"],
        "rules": {},
    }
    for key, fit in sorted(report["fits"].items()):
        protocol, refrigerant = key.split("|")
        output["rules"][f"{protocols[protocol]['compressor_type']}|{refrigerant}"] = {
            "protocol": protocol,
            "selected_law": fit["selected_law"],
            "capacity_range_W": fit["capacity_range_W"],
            "n": fit["n"],
            "n_families": len(fit["families"]),
            "manufacturers": fit["manufacturers"],
            "nested_family_cv": fit["nested_family_cv"],
            "source_ids": fit["source_ids"],
            "status": "component_regression_only",
        }
    TARGET.write_text(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return output


if __name__ == "__main__":
    result = export()
    print(json.dumps({"version": result["version"], "rules": sorted(result["rules"])}, ensure_ascii=False))
