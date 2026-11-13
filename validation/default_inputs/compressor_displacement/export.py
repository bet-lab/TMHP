"""Export the latest released displacement relation into the installed package (issue #86).

Runtime needs, per compressor basis, eta_vol * rps / f with its evidence and hold-out errors; it evaluates
rho_1 * dh itself with CoolProp, so V_cmp_ref = Q_nom / (rho_1 * dh * eta_vol * rps) works for any
refrigerant. Per-protocol regressions and held-out predictions stay in regression.json. Only a build recorded
in releases.json is exported.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
TARGET = ROOT / "src/tmhp/data/compressor_displacement_regression.json"
CV_FIELDS = ("n", "MAPE_pct", "RMSE", "bias_pct")


def _cv(block: dict) -> dict:
    return {k: block[k] for k in CV_FIELDS} if block.get("valid") else block


def export() -> dict:
    report = json.loads((HERE / "regression.json").read_text())
    release = json.loads((HERE / "releases.json").read_text())[-1]
    if release["data_sha256"] != report["data_sha256"]:
        raise ValueError("regression.json is not the latest release; run build --release first")
    output = {
        "version": release["version"],
        "data_sha256": report["data_sha256"],
        "formula": "V_cmp_ref = Q_nom / (rho_1 * dh * eta_vol * rps)",
        "units": {"Q_nom": "W", "rho_1": "kg/m3", "dh": "J/kg", "eta_vol_rps": "rev/s", "V_cmp_ref": "m3/rev"},
        "property_convention": (
            "rho_1 at the evaporating dew-point pressure and suction temperature; dh = h_1 - h_liquid with the liquid "
            "at the condensing dew-point pressure (CoolProp), as in tmhp.compressor_speed"
        ),
        "capacity_duty": "cooling",
        "shipping_gate": report["shipping_gate"],
        "bases": {},
    }
    for basis, fit in sorted(report["normalized_fits"].items()):
        law = fit["selected_law"]
        output["bases"][basis] = {
            "eta_vol_rps_per_f": law["eta_vol_rps_per_f"],
            "frequency_Hz": law["frequency_Hz"],
            "eta_vol_rps": law["eta_vol_rps"],
            "n": fit["n"],
            "n_families": len(fit["families"]),
            "manufacturers": fit["manufacturers"],
            "refrigerants": fit["refrigerants"],
            "protocols": {p: report["protocols"][p] for p in fit["protocols"]},
            "capacity_range_W": fit["capacity_range_W"],
            "capacity_trend_log_slope": fit["capacity_trend_log_slope"],
            "nested_family_cv": _cv(fit["nested_family_cv"]),
            "nested_manufacturer_cv": _cv(fit["nested_manufacturer_cv"]),
            "nested_refrigerant_cv": _cv(fit["nested_refrigerant_cv"]),
            "meets_shipping_gate": fit["meets_shipping_gate"],
            "source_ids": fit["source_ids"],
        }
    TARGET.write_text(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return output


if __name__ == "__main__":
    result = export()
    print(json.dumps({"version": result["version"], "bases": sorted(result["bases"])}, ensure_ascii=False))
