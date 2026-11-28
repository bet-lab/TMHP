"""Refit catalogue input candidates and compare on identical held-out catalogue rows."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import yaml
from validation.default_inputs import archive
from validation.default_inputs.compressor_displacement import build as displacement
from validation.default_inputs.fan_airflow import build as airflow
from validation.default_inputs.regression import metrics, predict

HERE = Path(__file__).parent


def read_json(path: Path):
    """Read one UTF-8 archive or supplement file."""
    return json.loads(path.read_text(encoding="utf-8"))


def load_rows(domain):
    """Normalize the supplement through the same builders used by the shipped archive."""
    folder = HERE / domain
    builder = displacement if domain == "displacement" else airflow
    base = builder.build_rows()
    sources = {s["source_id"]: s for s in read_json(folder / "sources.json")}
    observations = archive.read_observations(folder, sources, builder.OBSERVATION_COLUMNS)
    exclusions = folder / "exclusions.json"
    rejected = read_json(exclusions) if exclusions.exists() else {}
    assert set(rejected) <= {row["record_id"] for row in observations}
    protocols = {}
    if domain == "displacement":
        protocols = builder.load_json("protocols.json")
        protocols.update(read_json(folder / "protocols.json"))
    candidates = []
    for obs in observations:
        source = sources[obs["source_id"]]
        if obs["record_id"] in rejected:
            obs["exclusion_reason"] = rejected[obs["record_id"]]
        if domain == "displacement":
            row = displacement.normalize(obs, source, protocols)
            if row["eligible"] and row["eta_vol_rps_per_f"] > 1:
                raise ValueError(f"{row['record_id']}: candidate implies volumetric efficiency above one")
        else:
            row = airflow.normalize(obs, source)
            if row["eligible"] and row["airflow_basis"] != "explicit_max":
                raise ValueError(f"{row['record_id']}: supplement requires an explicit maximum fan setting")
        candidates.append(row)
    return base, candidates


def common_metrics(before, after, field):
    """Compare two nested fits only on shared held-out rows, with the same fold definitions."""
    key = field + "_predictions"
    old = {row["record_id"]: row for row in before[key]}
    new = {row["record_id"]: row for row in after[key]}
    common = sorted(old.keys() & new.keys())
    for record_id in common:
        left, right = old[record_id], new[record_id]
        assert left["observed"] == right["observed"] and left["fold"] == right["fold"]
        assert left["fold"] not in left["train_groups"] and right["fold"] not in right["train_groups"]
    return {"before": metrics([old[k] for k in common]), "after": metrics([new[k] for k in common])}


def independent_new_rows(domain, after, rows):
    """Score old and expanded fits on new rows, with each row's manufacturer withheld."""
    new_predictions = {row["record_id"]: row for row in after["manufacturer_predictions"]}
    previous = []
    proposed = []
    # The caller passes baseline rows together with added rows. Holding out the
    # maker here also covers newly introduced manufacturers absent from the old fit.
    old_rows, candidates = rows
    for maker in sorted({row["manufacturer"] for row in candidates}):
        train = [row for row in old_rows if row["manufacturer"] != maker]
        if not train:
            continue
        if domain == "displacement":
            c = displacement.fit_eta_vol_rps_per_f(train)
        else:
            law = archive.fit_law(train, archive.select_kind(train))
        for row in (row for row in candidates if row["manufacturer"] == maker):
            if row["record_id"] not in new_predictions:
                continue
            value = (
                displacement.predict_displacement(row, c)
                if domain == "displacement"
                else float(predict(law, row["capacity_kW"]))
            )
            previous.append(dict(observed=row["value"], predicted=value))
            fold = new_predictions[row["record_id"]]
            assert fold["fold"] == maker and maker not in fold["train_groups"]
            proposed.append(fold)
    return {"before": metrics(previous), "after": metrics(proposed)}


def panasonic_metrics(law):
    """Evaluate the existing A2W independent Panasonic cohort with a given airflow law."""
    cases = HERE.parents[1] / "catalogs"
    observations = [yaml.safe_load(path.read_text()) for path in sorted(cases.glob("panasonic_wh_*.yaml"))]
    assert len(observations) == 9, "Panasonic hold-out cohort changed"
    return metrics(
        [
            dict(
                observed=row["published_inputs"]["rated_air_flow_m3_s"],
                predicted=float(predict(law, row["nominal_capacity_kW"])),
            )
            for row in observations
        ]
    )


def compare_domain(domain):
    """Refit changed strata only and retain failures and evaluation boundaries in the result."""
    base, additions = load_rows(domain)
    group_key = "basis" if domain == "displacement" else "stratum"
    fit_group = displacement.fit_basis if domain == "displacement" else archive.fit_group
    new_by_group = defaultdict(list)
    for row in additions:
        if row["eligible"]:
            new_by_group[row[group_key]].append(row)
    groups = {}
    baseline_exclusions = []
    for group, rows in sorted(new_by_group.items()):
        old_rows = [row for row in base if row["eligible"] and row[group_key] == group]
        if not old_rows:
            raise ValueError(f"{group}: no like-for-like baseline for the new rows")
        old_ids = {row["record_id"] for row in old_rows}
        seen_models = {(row["manufacturer"], row["model_id"], row.get("refrigerant_normalized")) for row in old_rows}
        for row in rows:
            model = (row["manufacturer"], row["model_id"], row.get("refrigerant_normalized"))
            if row["record_id"] in old_ids or model in seen_models:
                raise ValueError(f"{row['record_id']}: candidate duplicates a model in {group}")
            seen_models.add(model)
        screened = old_rows
        if domain == "displacement":
            screened = [row for row in old_rows if row["eta_vol_rps_per_f"] <= 1]
            baseline_exclusions.extend(
                {"record_id": row["record_id"], "eta_vol_rps_per_f": row["eta_vol_rps_per_f"]}
                for row in old_rows
                if row["eta_vol_rps_per_f"] > 1
            )
        before, after = fit_group(old_rows), fit_group(screened + rows)
        groups[group] = {
            "before": {k: before[k] for k in ("n", "families", "manufacturers", "selected_law", "capacity_range_W")},
            "after": {k: after[k] for k in ("n", "families", "manufacturers", "selected_law", "capacity_range_W")},
            "common_family_holdout": common_metrics(before, after, "family"),
            "common_manufacturer_holdout": common_metrics(before, after, "manufacturer"),
            "new_rows_manufacturer_holdout": independent_new_rows(domain, after, (old_rows, rows)),
            "all_rows_family_holdout": {"before": before["nested_family_cv"], "after": after["nested_family_cv"]},
            "all_rows_manufacturer_holdout": {
                "before": before["nested_manufacturer_cv"],
                "after": after["nested_manufacturer_cv"],
            },
        }
        if len(screened) < len(old_rows):
            groups[group]["added_only_law"] = fit_group(old_rows + rows)["selected_law"]
        if group == "airflow|heating|outdoor|air_to_water":
            groups[group]["panasonic_external_holdout"] = {
                phase: panasonic_metrics(fit["selected_law"]) for phase, fit in (("before", before), ("after", after))
            }
            groups[group]["explicit_max_sensitivity"] = {
                "before": airflow.explicit_max_sensitivity(old_rows, before),
                "after": airflow.explicit_max_sensitivity(screened + rows, after),
            }
    return {
        "groups": groups,
        "baseline_exclusions_from_refit": baseline_exclusions,
        "excluded": [
            {"record_id": row["record_id"], "reason": row["exclusion_reason"]}
            for row in additions
            if not row["eligible"]
        ],
    }


def main():
    """Write the reproducible comparison without changing the shipped archive or library data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "comparison.json")
    args = parser.parse_args()
    report = {domain: compare_domain(domain) for domain in ("displacement", "airflow")}
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
