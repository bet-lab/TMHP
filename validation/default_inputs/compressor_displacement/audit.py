"""Check evidence files, observation-to-measurement integrity and held-out independence.

Needs the local evidence files (validation/evidence/, not in Git); build and export do not.
  uv run python -m validation.default_inputs.compressor_displacement.audit
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter

import pymupdf

from .build import HERE, ROOT, load_json, predict_displacement, suction_state


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run() -> dict:
    rows = load_json("measurements.json")
    regression = load_json("regression.json")
    protocols = load_json("protocols.json")
    releases = load_json("releases.json")
    sources = {s["source_id"]: s for s in load_json("sources.json")}
    for s in sources.values():
        path = ROOT / s["evidence_path"]
        assert digest(path) == s["sha256"], path
        if "html_path" in s:
            assert digest(ROOT / s["html_path"]) == s["html_sha256"], s["html_path"]
        with pymupdf.open(path) as pdf:
            assert len(pdf) == s["pages"], (s["source_id"], len(pdf))
        for key in ("conditions_source_id", "frequency_source_id"):
            for defaults in s.get("row_defaults", {}).values():
                assert defaults.get(key) in (None, *sources), (s["source_id"], key)
    assert len({r["record_id"] for r in rows}) == len(rows)
    for r in rows:
        source = sources[r["source_id"]]
        assert 1 <= r["source_pdf_page"] <= source["pages"], r["record_id"]
        assert r["source_sha256"] == source["sha256"]
        assert r["mechanical_rps_measured"] is None
        for k, v in protocols[r["protocol"]].items():
            if k not in ("description", "test_condition_labels"):
                assert r[k] == v, (r["record_id"], k)
        assert math.isclose(float(r["value_raw"]) * r["normalization_factor_to_cm3_rev"], r["value"], rel_tol=1e-12)
        rho_1, dh = suction_state(
            r["refrigerant_normalized"], r["T_evap_C"], r["T_suction_C"], r["T_cond_C"], r["T_liquid_C"]
        )
        assert math.isclose(rho_1 * dh, r["rho_1_dh_J_m3"], rel_tol=1e-9), r["record_id"]
        assert math.isclose(r["eta_vol_rps"] * r["value"] * 1e-6 * r["rho_1_dh_J_m3"], r["capacity_W"], rel_tol=1e-9)
        assert math.isclose(predict_displacement(r, r["eta_vol_rps_per_f"]), r["value"], rel_tol=1e-9)
        if r["displacement_evidence_type"] == "manufacturer_nomenclature_decoded":
            assert r["value_raw"] == int(r["value_original_text"])
        else:
            assert float(r["value_original_text"]) == r["value"]
    for group in regression["fits"].values():
        for key in ("family_predictions", "model_group_predictions", "manufacturer_predictions"):
            p = group[key]
            assert all(x["fold"] not in x["train_groups"] for x in p)
            if p:
                assert len(p) == group["n"]
    for basis, fit in regression["normalized_fits"].items():
        members = [r for r in rows if r["eligible"] and r["basis"] == basis]
        assert len(members) == fit["n"], basis
        assert len({r["frequency_Hz"] for r in members}) == 1, basis
        for key in ("family_predictions", "manufacturer_predictions", "refrigerant_predictions"):
            assert all(x["fold"] not in x["train_groups"] for x in fit[key]), (basis, key)
        assert fit["meets_shipping_gate"] == (
            len(fit["families"]) >= regression["shipping_gate"]["families"]
            and len(fit["manufacturers"]) >= regression["shipping_gate"]["manufacturers"]
        )
    assert digest(HERE / "measurements.json") == regression["data_sha256"]
    report = {
        "records": len(rows),
        "eligible": sum(r["eligible"] for r in rows),
        "excluded": [
            {k: r[k] for k in ("record_id", "model_id", "source_pdf_page", "exclusion_reason")}
            for r in rows
            if not r["eligible"]
        ],
        "errata_applied": [dict(record_id=r["record_id"], **e) for r in rows for e in r["errata_applied"]],
        "group_count": len(regression["fits"]),
        "normalized_bases": {
            k: {
                "n": v["n"],
                "manufacturers": v["manufacturers"],
                "eta_vol_rps_per_f": v["selected_law"]["eta_vol_rps_per_f"],
                "meets_shipping_gate": v["meets_shipping_gate"],
            }
            for k, v in regression["normalized_fits"].items()
        },
        "evidence_type_counts": dict(Counter(r["displacement_evidence_type"] for r in rows)),
        "source_count": len(sources),
        "data_sha256": regression["data_sha256"],
        "is_latest_release": releases[-1]["data_sha256"] == regression["data_sha256"],
        "latest_release": releases[-1]["version"],
        "all_checks_passed": True,
        "limitations": [
            "LG ambient temperature unspecified; refrigerant-side temperature boundaries and frequency/type are matched, not every environmental variable.",
            "Variable-speed 60Hz means published test/drive-frequency basis, not a measured shaft speed.",
            "LG variable-speed frequency is a cross-document manufacturer default from the current product page, not directly printed on each 2024 capacity row.",
            "Per-protocol fits are diagnostic. The shipped relation pools refrigerants and rating points within one compressor basis by dividing out rho_1*dh (CoolProp); fixed-speed 50 Hz and 60 rps inverter bases are never pooled.",
            "eta_vol*rps assumes the volumetric efficiency of a basis does not depend on refrigerant or rating point; refrigerant hold-out error measures how far that holds.",
            "SCI's numeric definitions of 'ARI' and 'ASRE-T' come from its 2016 edition (distributor-hosted, byte-identical held copy); the 2026 official edition prints only the labels.",
            "GMCC 2026 was reviewed and not used: its inverter tables do not state which of R32/R410A the printed capacity refers to.",
            "Only LG provides the near-15 kW matched R32/R410A comparison; multi-manufacturer coverage is strongest in the lower-capacity fixed50 protocol.",
            "Highly web snapshots are browser-generated evidence of official HTML, not original manufacturer PDFs.",
        ],
    }
    (HERE / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "limitations"}, ensure_ascii=False))
    return report


if __name__ == "__main__":
    run()
