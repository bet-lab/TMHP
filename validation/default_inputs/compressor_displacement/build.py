"""Build the compressor-displacement archive: observations -> measurements -> fits -> release record.

Hand-edited inputs (everything else in this folder is generated):
  sources.json            one entry per document: URL, evidence file, SHA-256, pages, per-protocol row defaults
  protocols.json          rating protocols and the printed test-condition labels that map to them
  observations/<id>.csv   one file per source, one row per printed table row, values as printed
  errata.csv              corrections to observation cells (checked against the old value) and exclusions

  uv run python -m validation.default_inputs.compressor_displacement.build
  uv run python -m validation.default_inputs.compressor_displacement.build --release VERSION --note "what changed"
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from decimal import Decimal
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
    "model_id",
    "refrigerant",
    "protocol",
    "test_condition",
    "capacity_W",
    "capacity_Btu_h",
    "power_W",
    "displacement_text",
    "displacement_basis",
    "note",
]
ERRATA_FIELDS = set(OBSERVATION_COLUMNS) - {"record_id", "note"}
BTU_H_TO_W = 0.29307107
DUAL_UNIT_TOLERANCE = 0.02
SCOPE = "Component catalogue regressions; selectable in TMHP as single_split_displacement_regression (issue #86)"


def load_json(name: str):
    return archive.load_json(HERE, name)


def number(text: str) -> float | None:
    return float(text) if text.strip() else None


def normalize(obs: dict, source: dict, protocols: dict) -> dict:
    protocol = protocols[obs["protocol"]]
    if obs["test_condition"] not in protocol["test_condition_labels"]:
        raise ValueError(f"{obs['record_id']}: {obs['test_condition']!r} is not a {obs['protocol']} label")
    row = {
        k: obs[k]
        for k in ("record_id", "source_id", "manufacturer", "family_id", "model_id", "refrigerant", "protocol")
    }
    row.update({k: v for k, v in protocol.items() if k not in ("description", "test_condition_labels")})
    row.update(source.get("row_defaults", {}).get(obs["protocol"], {}))
    row.update(
        test_condition=obs["test_condition"],
        mode="cooling",
        kind="compressor",
        refrigerant_normalized=archive.normalize_refrigerant(obs["refrigerant"]),
        capacity_W=number(obs["capacity_W"]),
        capacity_Btu_h=number(obs["capacity_Btu_h"]),
        power_W=number(obs["power_W"]),
        value_original_text=obs["displacement_text"],
        unit="cm3/rev",
        source_url=source["url"],
        source_pdf=source["evidence_path"],
        source_sha256=source["sha256"],
        evidence_type=source["evidence_type"],
        source_pdf_page=int(obs["source_pdf_page"]),
        printed_page=obs["printed_page"] or None,
        locator=obs["locator"] or None,
        note=obs["note"] or None,
        mechanical_rps_measured=None,
        errata_applied=obs.get("errata_applied", []),
        adoption_status="selectable library profile; not the constructor default",
    )
    if "html_path" in source:
        row["source_html"] = source["html_path"]
    if obs["displacement_basis"] == "printed":
        row.update(
            value=float(obs["displacement_text"]),
            value_raw=float(obs["displacement_text"]),
            unit_original="cm3/rev",
            normalization_factor_to_cm3_rev=1.0,
            displacement_evidence_type="explicit_numeric_geometry",
        )
    else:
        rule = source["displacement_decoding"][obs["displacement_basis"]]
        raw = int(obs["displacement_text"])
        row.update(
            value=float(Decimal(raw) * Decimal(str(rule["factor_to_cm3_rev"]))),
            value_raw=raw,
            unit_original=f"manufacturer displacement code ({rule['factor_to_cm3_rev']:g} cm3/rev)",
            normalization_factor_to_cm3_rev=rule["factor_to_cm3_rev"],
            displacement_evidence_type="manufacturer_nomenclature_decoded",
            displacement_rule=rule["rule"],
            displacement_rule_pdf_page=rule["rule_pdf_page"],
        )
    row["capacity_kW"] = row["capacity_W"] / 1000
    token = re.match(r"\d?[A-Z]+\d+", row["model_id"])
    row["model_group_id"] = row["manufacturer"] + ":" + (token.group() if token else row["model_id"])
    row["eligible"], row["exclusion_reason"] = True, ""
    if row["capacity_Btu_h"]:
        error = abs(row["capacity_Btu_h"] * BTU_H_TO_W - row["capacity_W"]) / row["capacity_W"]
        row["capacity_dual_unit_relative_error"] = error
        if error > DUAL_UNIT_TOLERANCE:
            row.update(eligible=False, exclusion_reason="manufacturer_W_and_Btu_h_capacity_columns_differ_over_2pct")
    if obs.get("exclusion_reason"):
        row.update(eligible=False, exclusion_reason=obs["exclusion_reason"])
    if not (row["capacity_W"] > 0 and row["value"] > 0):
        raise ValueError(f"{row['record_id']}: capacity and displacement must be positive")
    return row


def build_rows() -> list[dict]:
    registry = {s["source_id"]: s for s in load_json("sources.json")}
    protocols = load_json("protocols.json")
    observations = archive.read_observations(HERE, registry, OBSERVATION_COLUMNS)
    archive.apply_errata(HERE, observations, ERRATA_FIELDS)
    rows = [normalize(o, registry[o["source_id"]], protocols) for o in observations]
    seen = {}
    for r in rows:
        key = (
            r["manufacturer"],
            r["model_id"],
            r["refrigerant_normalized"],
            r["protocol"],
            r["capacity_W"],
            r["value"],
        )
        if key in seen:
            raise ValueError(f"{r['record_id']} repeats {seen[key]}; keep one row per printed observation")
        seen[key] = r["record_id"]
    return rows


def fit_groups(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        if row["eligible"]:
            groups[(row["protocol"], row["refrigerant_normalized"])].append(row)
    return {
        protocol + "|" + ref: {"protocol": protocol, "refrigerant": ref, **archive.fit_group(group)}
        for (protocol, ref), group in sorted(groups.items())
    }


def run(release: str | None = None, note: str = "") -> dict:
    rows = build_rows()
    data_sha = archive.write_measurements(HERE, rows)
    regression = {
        "scope": SCOPE,
        "protocols": {
            k: {f: v for f, v in p.items() if f != "test_condition_labels"}
            for k, p in load_json("protocols.json").items()
        },
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
