"""Export the latest released approach-temperature laws into the installed package (issues #115-#118).

Runtime needs, per stratum, the selected law, the spread of the population it came from, the duty of the
exchanger relative to the unit's nameplate and the hold-out error; candidate laws and per-fold results stay in
regression.json. Only a build recorded in releases.json is exported. Strata that miss the shipping gate are
exported with meets_shipping_gate=false so the runtime can refuse them with a reason.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
TARGET = ROOT / "src/tmhp/data/hx_approach_regression.json"
BAND = ("ITD_K", "leave_K", "eps", "UA_per_Q_hx")


def export() -> dict:
    report = json.loads((HERE / "regression.json").read_text())
    release = json.loads((HERE / "releases.json").read_text())[-1]
    if release["data_sha256"] != report["data_sha256"]:
        raise ValueError("regression.json is not the latest release; run build --release first")
    output = {
        "version": release["version"],
        "data_sha256": report["data_sha256"],
        "meaning": (
            "effective conductance of an isothermal-refrigerant exchanger that reproduces the saturation "
            "temperature at the pressure real machines print or were measured at (service or compressor port)"
        ),
        "selection": report["selection"],
        "shipping_gate": report["shipping_gate"],
        "strata": {},
    }
    for key, fit in sorted(report["fits"].items()):
        band = fit["band_p10_p50_p90"]
        selected = fit["selected_law"]
        cv = fit["candidates"][selected["kind"]] if selected else {}
        output["strata"][key] = {
            "law": selected,
            "n": fit["n"],
            "n_holdout_groups": len(fit["holdout_groups"]),
            "manufacturers": fit["manufacturers"],
            "band_p10_p50_p90": {k: band[k] for k in BAND},
            "duty_per_nameplate_p10_p50_p90": {
                mode: band[f"Q_hx_per_{mode}_nameplate"]
                for mode in ("cooling", "heating")
                if f"Q_hx_per_{mode}_nameplate" in band
            },
            "duty_range_W": fit["duty_range_W"],
            "group_holdout": {k: v for k, v in cv.get("group_holdout", {}).items() if k != "folds"},
            "manufacturer_holdout": {k: v for k, v in cv.get("manufacturer_holdout", {}).items() if k != "folds"},
            "meets_shipping_gate": fit["meets_shipping_gate"],
            "source_ids": fit["source_ids"],
        }
    TARGET.write_text(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return output


if __name__ == "__main__":
    result = export()
    print(
        json.dumps(
            {
                "version": result["version"],
                "strata": {k: v["meets_shipping_gate"] for k, v in result["strata"].items()},
            },
            ensure_ascii=False,
        )
    )
