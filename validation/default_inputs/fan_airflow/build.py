"""Build the fan-airflow archive: observations -> measurements -> fits -> release record.

Maximum published fan airflow [m3/s] against rated capacity, per mode, side and product class:
wall-mounted and floor-standing indoor units of 1:1 single splits and their outdoor units (ASHP),
water-to-air packaged heat pumps (GSHP indoor side) and air-to-water outdoor units (ASHPB).
Ducted and cassette split indoor units are out of scope. Hand-edited inputs, as in ../compressor_displacement:
  sources.json  observations/<source_id>.csv  errata.csv

  uv run python -m validation.default_inputs.fan_airflow.build
  uv run python -m validation.default_inputs.fan_airflow.build --release VERSION --note "what changed"
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from validation.default_inputs import archive
from validation.default_inputs.regression import predict

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
OBSERVATION_COLUMNS = [
    "record_id",
    "source_pdf_page",
    "printed_page",
    "locator",
    "manufacturer",
    "family_id",
    "indoor_model",
    "outdoor_model",
    "refrigerant",
    "side",
    "indoor_type",
    "mode",
    "capacity_text",
    "capacity_unit",
    "airflow_text",
    "airflow_unit",
    "fan_step",
    "note",
]
ERRATA_FIELDS = set(OBSERVATION_COLUMNS) - {"record_id", "note"}
INDOOR_TYPES = ("wall_mounted", "floor_standing", "water_to_air")
# Outdoor rows carry the type of the indoor unit they are paired with; an air-to-water unit has none,
# so its outdoor rows carry the product class instead.
OUTDOOR_ONLY_TYPES = ("air_to_water",)
INDOOR_ONLY_TYPES = ("water_to_air",)
# A printed rated or nominal airflow of an indoor blower is the test airflow, not its largest setting.
RATED_STEP_LABELS = ("rated", "nominal", "nom", "standard")
# Recorded but not fitted: the same unit can be ceiling-mounted, so its type is ambiguous.
RECORDED_ONLY_TYPES = ("floor_ceiling_convertible",)
CAPACITY_TO_W = {"W": 1.0, "kW": 1000.0, "Btu/h": 0.29307107}
AIRFLOW_TO_M3_S = {"m3/s": 1.0, "m3/min": 1 / 60, "m3/h": 1 / 3600, "l/s": 1e-3, "CFM": 0.0283168466 / 60}
SCOPE = (
    "Maximum published fan airflow: 1:1 single splits (wall-mounted, floor-standing, their outdoor units), "
    "water-to-air packaged heat pumps and air-to-water outdoor units"
)
SHIPPING_GATE = {"families": 3, "manufacturers": 2}


def load_json(name: str):
    return archive.load_json(HERE, name)


def airflow_basis(fan_step: str) -> str:
    """How the printed value is known to be the fan's largest airflow (decision 2026-11-14, option A).

    explicit_max: a printed step or label marks the value as the largest (High, Max, top setting ...);
    single_value: one value printed without a step, or a label naming several steps over one value;
    rated: labelled rated/nominal. Outdoor single_value and rated rows are taken as the maximum
    (README rule) and the fit restricted to explicit_max rows is reported as a sensitivity.
    """
    label = fan_step.strip()
    if not label:
        return "single_value"
    if label.lower() in RATED_STEP_LABELS or label.lower().startswith("rated"):
        return "rated"
    if "/" in label:
        return "single_value"
    return "explicit_max"


def stratum(row: dict) -> str:
    if row["side"] == "indoor":
        equipment = row["indoor_type"]
    else:
        equipment = row["indoor_type"] if row["indoor_type"] in OUTDOOR_ONLY_TYPES else "single_split"
    return f"airflow|{row['mode']}|{row['side']}|{equipment}"


def normalize(obs: dict, source: dict) -> dict:
    if obs["side"] not in ("indoor", "outdoor") or obs["mode"] not in ("cooling", "heating"):
        raise ValueError(f"{obs['record_id']}: side/mode must be indoor|outdoor and cooling|heating")
    if obs["indoor_type"] not in INDOOR_TYPES + RECORDED_ONLY_TYPES + OUTDOOR_ONLY_TYPES:
        raise ValueError(f"{obs['record_id']}: indoor_type {obs['indoor_type']!r} is out of scope")
    if (obs["indoor_type"] in OUTDOOR_ONLY_TYPES and obs["side"] != "outdoor") or (
        obs["indoor_type"] in INDOOR_ONLY_TYPES and obs["side"] != "indoor"
    ):
        raise ValueError(f"{obs['record_id']}: air_to_water rows are outdoor rows, water_to_air rows are indoor rows")
    for unit, table in ((obs["capacity_unit"], CAPACITY_TO_W), (obs["airflow_unit"], AIRFLOW_TO_M3_S)):
        if unit not in table:
            raise ValueError(f"{obs['record_id']}: unknown unit {unit!r}")
    row = {k: obs[k] for k in OBSERVATION_COLUMNS if k not in ("source_pdf_page", "capacity_text", "airflow_text")}
    row.update(
        source_id=obs["source_id"],
        kind="airflow",
        # Airflow is not stratified by refrigerant; a document that prints none keeps it empty.
        refrigerant_normalized=archive.normalize_refrigerant(obs["refrigerant"]) if obs["refrigerant"] else None,
        model_id=f"{obs['indoor_model']}/{obs['outdoor_model']}",
        capacity_raw=float(obs["capacity_text"]) if obs["capacity_text"] else None,
        capacity_W=float(obs["capacity_text"]) * CAPACITY_TO_W[obs["capacity_unit"]] if obs["capacity_text"] else None,
        value_raw=float(obs["airflow_text"]),
        value=float(obs["airflow_text"]) * AIRFLOW_TO_M3_S[obs["airflow_unit"]],
        unit="m3/s",
        source_pdf_page=int(obs["source_pdf_page"]) if obs["source_pdf_page"] else None,
        source_url=source["url"],
        source_pdf=source["evidence_path"],
        source_sha256=source["sha256"],
        evidence_type=source["evidence_type"],
        rating_conditions=source.get("rating_conditions"),
        assumption=source.get("assumption"),
        errata_applied=obs.get("errata_applied", []),
        stratum=stratum(obs),
        airflow_basis=airflow_basis(obs["fan_step"]),
        capacity_basis=source.get("capacity_basis"),
    )
    row["capacity_kW"] = row["capacity_W"] / 1000 if row["capacity_W"] else None
    token = re.match(r"[A-Z-]*\d+", obs["indoor_model"])
    row["model_group_id"] = obs["manufacturer"] + ":" + (token.group() if token else obs["indoor_model"])
    row["eligible"], row["exclusion_reason"] = True, ""
    # Indoor units print several fan steps, and only a labelled top step is a maximum. Outdoor units
    # usually print one airflow per mode; that single value is the outdoor fan's rated maximum.
    if obs["side"] == "indoor" and not obs["fan_step"]:
        row.update(eligible=False, exclusion_reason="indoor airflow without a printed fan step is not a maximum")
    elif obs["side"] == "indoor" and obs["fan_step"].strip().lower() in RATED_STEP_LABELS:
        row.update(eligible=False, exclusion_reason="indoor rated/nominal airflow is the test airflow, not a maximum")
    if obs["indoor_type"] in RECORDED_ONLY_TYPES:
        row.update(eligible=False, exclusion_reason=f"{obs['indoor_type']}: indoor type ambiguous, not fitted")
    if row["capacity_W"] is None:
        row.update(eligible=False, exclusion_reason="no nominal capacity printed")
    if obs.get("exclusion_reason"):
        row.update(eligible=False, exclusion_reason=obs["exclusion_reason"])
    if not (row["value"] > 0 and (row["capacity_W"] is None or row["capacity_W"] > 0)):
        raise ValueError(f"{row['record_id']}: capacity and airflow must be positive")
    return row


def build_rows() -> list[dict]:
    registry = {s["source_id"]: s for s in load_json("sources.json")}
    observations = archive.read_observations(HERE, registry, OBSERVATION_COLUMNS)
    archive.apply_errata(HERE, observations, ERRATA_FIELDS)
    rows = [normalize(o, registry[o["source_id"]]) for o in observations]
    seen = {}
    for r in rows:
        key = (r["manufacturer"], r["model_id"], r["side"], r["mode"], r["capacity_W"], r["value"])
        if key in seen:
            raise ValueError(f"{r['record_id']} repeats {seen[key]}; keep one row per printed observation")
        seen[key] = r["record_id"]
    return rows


def explicit_max_sensitivity(group: list[dict], fit: dict) -> dict:
    """Refit with explicit_max rows only and compare the two laws at the stratum's capacity quartiles."""
    subset = [r for r in group if r["airflow_basis"] == "explicit_max"]
    if len(subset) == len(group):
        return {"status": "all rows explicit_max"}
    families = {r["family_id"] for r in subset}
    makers = {r["manufacturer"] for r in subset}
    if len(families) < 2:
        return {"status": "insufficient explicit_max rows", "n": len(subset), "families": len(families)}
    sub = archive.fit_group(subset)
    caps = sorted(r["capacity_W"] / 1000 for r in group)
    quartiles = [caps[int(q * (len(caps) - 1))] for q in (0.25, 0.5, 0.75)]
    return {
        "n": len(subset),
        "families": len(families),
        "manufacturers": sorted(makers),
        "selected_law": sub["selected_law"],
        "nested_family_MAPE_pct": sub["nested_family_cv"].get("MAPE_pct"),
        "all_rows_over_explicit_max": {
            f"{x:.2f} kW": float(predict(fit["selected_law"], x) / predict(sub["selected_law"], x)) for x in quartiles
        },
    }


def fit_groups(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        if row["eligible"]:
            groups[row["stratum"]].append(row)
    fits = {key: {"stratum": key, **archive.fit_group(group)} for key, group in sorted(groups.items())}
    statuses = load_json("strata_status.json") if (HERE / "strata_status.json").exists() else {}
    unknown = set(statuses) - set(fits)
    if unknown:
        raise ValueError(f"strata_status.json names strata that do not exist: {sorted(unknown)}")
    for key, fit in fits.items():
        fit["status"] = statuses.get(key, {}).get("status", "shipped")
        fit["status_reason"] = statuses.get(key, {}).get("reason")
        fit["airflow_basis_counts"] = dict(sorted(Counter(r["airflow_basis"] for r in groups[key]).items()))
        fit["explicit_max_only"] = explicit_max_sensitivity(groups[key], fit)
        fit["meets_shipping_gate"] = (
            len(fit["families"]) >= SHIPPING_GATE["families"]
            and len(fit["manufacturers"]) >= SHIPPING_GATE["manufacturers"]
        )
    return fits


def run(release: str | None = None, note: str = "") -> dict:
    rows = build_rows()
    data_sha = archive.write_measurements(HERE, rows)
    regression = {
        "scope": SCOPE,
        "units": {"x": "rated capacity [kW]", "y": "largest published fan step [m3/s]"},
        "selection": archive.SELECTION,
        "dataset": {
            "records": len(rows),
            "eligible": sum(r["eligible"] for r in rows),
            "errata_applied": sum(len(r["errata_applied"]) for r in rows),
        },
        "fits": fit_groups(rows),
        "shipping_gate": SHIPPING_GATE,
        "data_sha256": data_sha,
    }
    (HERE / "regression.json").write_text(json.dumps(regression, ensure_ascii=False, indent=2) + "\n")
    archive.record_release(HERE, regression, release, note)
    return regression


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", help="record this build in releases.json under the given version")
    parser.add_argument("--note", default="", help="one line: why this release exists")
    args = parser.parse_args()
    run(args.release, args.note)
