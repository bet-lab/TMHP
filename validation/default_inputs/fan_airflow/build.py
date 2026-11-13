"""Build the fan-airflow archive: observations -> measurements -> fits -> release record.

Maximum published fan airflow [m3/s] against rated capacity, per mode, side and indoor type
(wall-mounted, floor-standing; outdoor units of those 1:1 single splits). Ducted and cassette units
are out of scope. Hand-edited inputs, as in ../compressor_displacement:
  sources.json  observations/<source_id>.csv  errata.csv

  uv run python -m validation.default_inputs.fan_airflow.build
  uv run python -m validation.default_inputs.fan_airflow.build --release VERSION --note "what changed"
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from validation.default_inputs import archive

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
INDOOR_TYPES = ("wall_mounted", "floor_standing")
# Recorded but not fitted: the same unit can be ceiling-mounted, so its type is ambiguous.
RECORDED_ONLY_TYPES = ("floor_ceiling_convertible",)
CAPACITY_TO_W = {"W": 1.0, "kW": 1000.0, "Btu/h": 0.29307107}
AIRFLOW_TO_M3_S = {"m3/s": 1.0, "m3/min": 1 / 60, "m3/h": 1 / 3600, "l/s": 1e-3, "CFM": 0.0283168466 / 60}
SCOPE = "Maximum published fan airflow of 1:1 single splits (wall-mounted, floor-standing, their outdoor units)"


def load_json(name: str):
    return archive.load_json(HERE, name)


def stratum(row: dict) -> str:
    equipment = row["indoor_type"] if row["side"] == "indoor" else "single_split"
    return f"airflow|{row['mode']}|{row['side']}|{equipment}"


def normalize(obs: dict, source: dict) -> dict:
    if obs["side"] not in ("indoor", "outdoor") or obs["mode"] not in ("cooling", "heating"):
        raise ValueError(f"{obs['record_id']}: side/mode must be indoor|outdoor and cooling|heating")
    if obs["indoor_type"] not in INDOOR_TYPES + RECORDED_ONLY_TYPES:
        raise ValueError(f"{obs['record_id']}: indoor_type {obs['indoor_type']!r} is out of scope")
    for unit, table in ((obs["capacity_unit"], CAPACITY_TO_W), (obs["airflow_unit"], AIRFLOW_TO_M3_S)):
        if unit not in table:
            raise ValueError(f"{obs['record_id']}: unknown unit {unit!r}")
    row = {k: obs[k] for k in OBSERVATION_COLUMNS if k not in ("source_pdf_page", "capacity_text", "airflow_text")}
    row.update(
        source_id=obs["source_id"],
        kind="airflow",
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
    )
    row["capacity_kW"] = row["capacity_W"] / 1000 if row["capacity_W"] else None
    token = re.match(r"[A-Z-]*\d+", obs["indoor_model"])
    row["model_group_id"] = obs["manufacturer"] + ":" + (token.group() if token else obs["indoor_model"])
    row["eligible"], row["exclusion_reason"] = True, ""
    # Indoor units print several fan steps, and only a labelled top step is a maximum. Outdoor units
    # usually print one airflow per mode; that single value is the outdoor fan's rated maximum.
    if obs["side"] == "indoor" and not obs["fan_step"]:
        row.update(eligible=False, exclusion_reason="indoor airflow without a printed fan step is not a maximum")
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


def fit_groups(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        if row["eligible"]:
            groups[row["stratum"]].append(row)
    return {key: {"stratum": key, **archive.fit_group(group)} for key, group in sorted(groups.items())}


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
