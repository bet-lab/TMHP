"""Build the compressor-displacement archive: observations -> measurements -> fits -> release record.

Hand-edited inputs (everything else in this folder is generated):
  sources.json            one entry per document: URL, evidence file, SHA-256, pages, per-protocol row defaults
  protocols.json          rating protocols and the printed test-condition labels that map to them
  observations/<id>.csv   one file per source, one row per printed table row, values as printed
  errata.csv              corrections to observation cells (checked against the old value) and exclusions

Printed values are never changed. The build adds, per row, the suction density rho_1 and the enthalpy
difference dh = h_1 - h_liquid at the row's rating point (CoolProp; dew-point pressures as in
tmhp.compressor_speed), and the effective speed eta_vol * rps = Q_nom / (V_cmp_ref * rho_1 * dh), Q_nom being the printed capacity. Rows are
then pooled per compressor basis (type and speed basis), not per refrigerant: the fitted eta_vol * rps / f
gives V_cmp_ref = Q_nom / (rho_1 * dh * eta_vol * rps) for any refrigerant CoolProp knows (issue #86).

  uv run python -m validation.default_inputs.compressor_displacement.build
  uv run python -m validation.default_inputs.compressor_displacement.build --release VERSION --note "what changed"
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import CoolProp.CoolProp as CP
from validation.default_inputs import archive
from validation.default_inputs.regression import metrics

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
SCOPE = "Compressor catalogue displacement: per-protocol regressions (diagnostic) and per-basis physical normalization (issue #86)"
SHIPPING_GATE = {"families": 3, "manufacturers": 2}


def load_json(name: str):
    return archive.load_json(HERE, name)


def number(text: str) -> float | None:
    return float(text) if text.strip() else None


def suction_state(refrigerant: str, T_evap_C: float, T_suction_C: float, T_cond_C: float, T_liquid_C: float):
    """rho_1 [kg/m3] at the compressor inlet and dh = h_1 - h_liquid [J/kg] at a catalogue rating point.

    Saturation pressures are dew-point pressures, as in tmhp.compressor_speed.refrigerant_aware_displacement.
    """
    p_evap = CP.PropsSI("P", "T", T_evap_C + 273.15, "Q", 1, refrigerant)
    p_cond = CP.PropsSI("P", "T", T_cond_C + 273.15, "Q", 1, refrigerant)
    rho_1 = CP.PropsSI("D", "T", T_suction_C + 273.15, "P", p_evap, refrigerant)
    h_1 = CP.PropsSI("H", "T", T_suction_C + 273.15, "P", p_evap, refrigerant)
    h_liquid = CP.PropsSI("H", "T", T_liquid_C + 273.15, "P", p_cond, refrigerant)
    return float(rho_1), float(h_1 - h_liquid)


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
    rho_1, dh = suction_state(
        row["refrigerant_normalized"], row["T_evap_C"], row["T_suction_C"], row["T_cond_C"], row["T_liquid_C"]
    )
    row.update(rho_1_kg_m3=rho_1, dh_J_kg=dh, rho_1_dh_J_m3=rho_1 * dh)
    row["eta_vol_rps"] = row["capacity_W"] / (row["value"] * 1e-6 * row["rho_1_dh_J_m3"])
    row["eta_vol_rps_per_f"] = row["eta_vol_rps"] / row["frequency_Hz"]
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


def _weights(rows: list[dict]) -> list[float]:
    counts = defaultdict(int)
    for r in rows:
        counts[r["family_id"]] += 1
    return [1 / counts[r["family_id"]] for r in rows]


def fit_eta_vol_rps_per_f(rows: list[dict]) -> float:
    """Family-balanced geometric mean of eta_vol * rps / f (equal relative error weight up and down)."""
    w = _weights(rows)
    return math.exp(sum(wi * math.log(r["eta_vol_rps_per_f"]) for wi, r in zip(w, rows, strict=True)) / sum(w))


def predict_displacement(row: dict, eta_vol_rps_per_f: float) -> float:
    """V_cmp_ref [cm3/rev] = Q_nom / (rho_1 * dh * eta_vol * rps) at the row's own rating point and speed."""
    return row["capacity_W"] / (row["rho_1_dh_J_m3"] * eta_vol_rps_per_f * row["frequency_Hz"]) * 1e6


def holdout(rows: list[dict], field: str) -> list[dict]:
    out = []
    for group in sorted({r[field] for r in rows}):
        train = [r for r in rows if r[field] != group]
        if not train:
            continue
        c = fit_eta_vol_rps_per_f(train)
        for r in (r for r in rows if r[field] == group):
            out.append(
                dict(
                    record_id=r["record_id"],
                    family_id=r["family_id"],
                    model_id=r["model_id"],
                    fold=group,
                    observed=r["value"],
                    predicted=predict_displacement(r, c),
                    capacity_W=r["capacity_W"],
                    eta_vol_rps_per_f_train=c,
                    train_groups=sorted({t[field] for t in train}),
                )
            )
    return out


def capacity_trend(rows: list[dict]) -> float:
    """Family-weighted slope of ln(eta_vol * rps / f) against ln(Q_nom); 0 means no size dependence."""
    w = _weights(rows)
    x = [math.log(r["capacity_W"]) for r in rows]
    y = [math.log(r["eta_vol_rps_per_f"]) for r in rows]
    xm = sum(wi * xi for wi, xi in zip(w, x, strict=True)) / sum(w)
    ym = sum(wi * yi for wi, yi in zip(w, y, strict=True)) / sum(w)
    sxx = sum(wi * (xi - xm) ** 2 for wi, xi in zip(w, x, strict=True))
    sxy = sum(wi * (xi - xm) * (yi - ym) for wi, xi, yi in zip(w, x, y, strict=True))
    return sxy / sxx if sxx > 0 else 0.0


def fit_basis(group: list[dict]) -> dict:
    c = fit_eta_vol_rps_per_f(group)
    frequency = {r["frequency_Hz"] for r in group}
    if len(frequency) != 1:
        raise ValueError(f"basis mixes speed bases {sorted(frequency)}")
    f = frequency.pop()
    by = defaultdict(list)
    for r in group:
        by[f"{r['manufacturer']}|{r['refrigerant_normalized']}|{r['protocol']}"].append(r["eta_vol_rps_per_f"])
    in_sample = [dict(observed=r["value"], predicted=predict_displacement(r, c)) for r in group]
    families = sorted({r["family_id"] for r in group})
    makers = sorted({r["manufacturer"] for r in group})
    fits = {key: holdout(group, key) for key in ("family_id", "manufacturer", "refrigerant_normalized")}
    return {
        "n": len(group),
        "manufacturers": makers,
        "families": families,
        "refrigerants": sorted({r["refrigerant_normalized"] for r in group}),
        "protocols": sorted({r["protocol"] for r in group}),
        "selected_law": {
            "kind": "physical_normalized",
            "formula": "V_cmp_ref = Q_nom / (rho_1 * dh * eta_vol * rps)",
            "eta_vol_rps_per_f": c,
            "frequency_Hz": f,
            "eta_vol_rps": c * f,
        },
        "capacity_range_W": [min(r["capacity_W"] for r in group), max(r["capacity_W"] for r in group)],
        "value_range": [min(r["value"] for r in group), max(r["value"] for r in group)],
        "eta_vol_rps_per_f_by_group": {
            k: {"n": len(v), "median": sorted(v)[len(v) // 2], "min": min(v), "max": max(v)}
            for k, v in sorted(by.items())
        },
        "capacity_trend_log_slope": capacity_trend(group),
        "in_sample": metrics(in_sample),
        "nested_family_cv": metrics(fits["family_id"]),
        "nested_manufacturer_cv": metrics(fits["manufacturer"])
        if len(makers) > 1
        else {"status": "single_manufacturer"},
        "nested_refrigerant_cv": metrics(fits["refrigerant_normalized"])
        if len({r["refrigerant_normalized"] for r in group}) > 1
        else {"status": "single_refrigerant"},
        "family_predictions": fits["family_id"],
        "manufacturer_predictions": fits["manufacturer"],
        "refrigerant_predictions": fits["refrigerant_normalized"],
        "meets_shipping_gate": len(families) >= SHIPPING_GATE["families"]
        and len(makers) >= SHIPPING_GATE["manufacturers"],
        "source_ids": sorted({r["source_id"] for r in group}),
    }


def fit_bases(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        if row["eligible"]:
            groups[row["basis"]].append(row)
    return {basis: {"basis": basis, **fit_basis(group)} for basis, group in sorted(groups.items())}


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
        "normalized_fits": fit_bases(rows),
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
