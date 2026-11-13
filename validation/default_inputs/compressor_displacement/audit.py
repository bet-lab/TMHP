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

from .build import HERE, ROOT, load_json


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
            "ASHRAE fixed50 and ARI variable60 are distinct protocols and never pooled into a single refrigerant law.",
            "Only LG provides the near-15 kW matched R32/R410A comparison; multi-manufacturer coverage is strongest in the lower-capacity fixed50 protocol.",
            "Highly web snapshots are browser-generated evidence of official HTML, not original manufacturer PDFs.",
        ],
    }
    (HERE / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "limitations"}, ensure_ascii=False))
    return report


if __name__ == "__main__":
    run()
