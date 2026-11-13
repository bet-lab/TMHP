"""Shared mechanics of the default-input evidence archives (compressor displacement, fan airflow).

Each archive folder holds hand-edited sources.json, observations/<source_id>.csv and errata.csv,
and generated measurements.*, regression.json and an append-only releases.json.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from validation.default_inputs.regression import KINDS, cv_fixed, fit_law, metrics, nested_cv, select_kind

SELECTION = (
    "Family-balanced fits; nested leave-one-family-out. Inner simplest within2% best RMSE. "
    "Secondary nested model-token-group holdout prevents suffix variants leaking."
)


# Canonical refrigerant names keyed by the upper-case label without hyphens or footnote marks.
REFRIGERANTS = {name.upper(): name for name in ("R22", "R32", "R290", "R410A", "R454B", "R454C", "R134a", "R1234yf")}


def normalize_refrigerant(text: str) -> str:
    """Printed refrigerant label -> canonical name ('R-32', 'R32(*1)' -> 'R32'); unknown labels raise."""
    key = re.sub(r"\(\*?\d*\)|\*\d*", "", text).replace("-", "").replace(" ", "").upper()
    if key not in REFRIGERANTS:
        raise ValueError(f"unknown refrigerant label {text!r}")
    return REFRIGERANTS[key]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(folder: Path, name: str):
    return json.loads((folder / name).read_text())


def read_observations(folder: Path, registry: dict, columns: list[str]) -> list[dict]:
    """One CSV per observation source; exact columns; record_id unique across files."""
    rows = []
    for path in sorted((folder / "observations").glob("*.csv")):
        source_id = path.stem
        if registry.get(source_id, {}).get("role") != "observations":
            raise ValueError(f"{path.name}: no observation source {source_id!r} in sources.json")
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != columns:
                raise ValueError(f"{path.name}: columns must be exactly {columns}")
            rows += [dict(r, source_id=source_id) for r in reader]
    if len({o["record_id"] for o in rows}) != len(rows):
        raise ValueError("record_id must be unique across observation files")
    return rows


def apply_errata(folder: Path, rows: list[dict], correctable: set[str]) -> None:
    """Corrections are checked against the current cell; field 'exclude' drops a row with a reason."""
    by_id = {r["record_id"]: r for r in rows}
    with (folder / "errata.csv").open(newline="") as f:
        for e in csv.DictReader(f):
            row = by_id.get(e["record_id"])
            if row is None:
                raise ValueError(f"errata: unknown record {e['record_id']}")
            if e["field"] == "exclude":
                row["exclusion_reason"] = e["to_value"]
            elif e["field"] not in correctable:
                raise ValueError(f"errata: field {e['field']!r} is not correctable")
            else:
                if row[e["field"]] != e["from_value"]:
                    raise ValueError(
                        f"errata {e['record_id']}.{e['field']}: expected {e['from_value']!r}, found {row[e['field']]!r}"
                    )
                row[e["field"]] = e["to_value"]
            row.setdefault("errata_applied", []).append(
                {k: e[k] for k in ("field", "from_value", "to_value", "evidence", "reason", "date")}
            )


def fit_group(group: list[dict]) -> dict:
    """Selected law, candidates and nested hold-outs for one stratum (x = capacity_W/1000, y = value)."""
    family = nested_cv(group)
    maker = nested_cv(group, "manufacturer") if len({r["manufacturer"] for r in group}) > 1 else []
    upper = [r for r in group if r["capacity_W"] >= 7500]
    return {
        "n": len(group),
        "manufacturers": sorted({r["manufacturer"] for r in group}),
        "families": sorted({r["family_id"] for r in group}),
        "selected_law": fit_law(group, select_kind(group)),
        "candidates": {k: {"law": fit_law(group, k), "family_cv": metrics(cv_fixed(group, k))} for k in KINDS},
        "nested_family_cv": metrics(family),
        "nested_model_group_cv": metrics(nested_cv(group, "model_group_id")),
        "nested_manufacturer_cv": metrics(maker) if maker else {"status": "single_manufacturer; unavailable"},
        "family_predictions": family,
        "model_group_predictions": nested_cv(group, "model_group_id"),
        "manufacturer_predictions": maker,
        "capacity_range_W": [min(r["capacity_W"] for r in group), max(r["capacity_W"] for r in group)],
        "value_range": [min(r["value"] for r in group), max(r["value"] for r in group)],
        "lower_capacity_n": len(group) - len(upper),
        "upper_capacity_n": len(upper),
        "upper_capacity_families": sorted({r["family_id"] for r in upper}),
        "source_ids": sorted({r["source_id"] for r in group}),
    }


def write_measurements(folder: Path, rows: list[dict]) -> str:
    (folder / "measurements.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    with (folder / "measurements.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r}))
        writer.writeheader()
        writer.writerows(rows)
    return sha(folder / "measurements.json")


def summary(regression: dict) -> dict:
    return {
        key: {
            "n": fit["n"],
            "manufacturers": fit["manufacturers"],
            "capacity_range_W": fit["capacity_range_W"],
            "selected_law": fit["selected_law"],
            "nested_family_RMSE": fit["nested_family_cv"]["RMSE"],
            "nested_family_MAPE_pct": fit["nested_family_cv"]["MAPE_pct"],
        }
        for key, fit in regression["fits"].items()
    }


def diff(previous: dict, current: dict) -> list[str]:
    changes = []
    for key in sorted(set(previous) | set(current)):
        old, new = previous.get(key), current.get(key)
        if old is None or new is None:
            changes.append(f"{key}: {'added' if old is None else 'removed'}")
            continue
        if old["n"] != new["n"]:
            changes.append(f"{key}: n {old['n']} -> {new['n']}")
        if old["selected_law"]["kind"] != new["selected_law"]["kind"]:
            changes.append(f"{key}: law {old['selected_law']['kind']} -> {new['selected_law']['kind']}")
        elif old["selected_law"] != new["selected_law"]:
            changes.append(f"{key}: coefficients {old['selected_law']} -> {new['selected_law']}")
        if old["capacity_range_W"] != new["capacity_range_W"]:
            changes.append(f"{key}: capacity range {old['capacity_range_W']} -> {new['capacity_range_W']} W")
    return changes


def record_release(folder: Path, regression: dict, release: str | None, note: str) -> list[str]:
    """Print changes since the latest release; with a version, append this build to releases.json."""
    path = folder / "releases.json"
    releases = json.loads(path.read_text()) if path.exists() else []
    current = summary(regression)
    changes = diff(releases[-1]["groups"], current) if releases else ["initial release"]
    if release:
        if any(r["version"] == release for r in releases):
            raise ValueError(f"release {release} already recorded")
        releases.append(
            {
                "version": release,
                "date": dt.date.today().isoformat(),
                "data_sha256": regression["data_sha256"],
                **regression["dataset"],
                "changes": changes,
                "note": note,
                "groups": current,
            }
        )
        path.write_text(json.dumps(releases, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({**regression["dataset"], "changes_since_latest_release": changes}, ensure_ascii=False))
    return changes
