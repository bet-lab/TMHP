"""Check evidence files and observation-to-measurement integrity of the fan-airflow archive.

Needs the local evidence files (validation/evidence/, not in Git); build does not.
  uv run python -m validation.default_inputs.fan_airflow.audit
"""

from __future__ import annotations

import json
import math
from collections import Counter

import pymupdf

from validation.default_inputs.archive import sha

from .build import AIRFLOW_TO_M3_S, CAPACITY_TO_W, HERE, ROOT, load_json


def run() -> dict:
    rows = load_json("measurements.json")
    regression = load_json("regression.json")
    sources = {s["source_id"]: s for s in load_json("sources.json")}
    for s in sources.values():
        path = ROOT / s["evidence_path"]
        if s.get("files"):  # page-by-page web evidence (flipbook): every saved file is hashed
            for f in s["files"]:
                assert sha(ROOT / f["path"]) == f["sha256"], f["path"]
        else:
            assert sha(path) == s["sha256"], path
        if "html_path" in s:
            assert sha(ROOT / s["html_path"]) == s["html_sha256"], s["html_path"]
        if path.suffix.lower() == ".pdf":
            with pymupdf.open(path) as pdf:
                assert len(pdf) == s["pages"], (s["source_id"], len(pdf))
    for r in rows:
        source = sources[r["source_id"]]
        assert source["role"] == "observations"
        if source.get("pages"):
            assert 1 <= r["source_pdf_page"] <= source["pages"], r["record_id"]
        assert r["source_sha256"] == source["sha256"]
        assert math.isclose(r["value_raw"] * AIRFLOW_TO_M3_S[r["airflow_unit"]], r["value"], rel_tol=1e-12)
        if r["capacity_W"] is not None:
            assert math.isclose(r["capacity_raw"] * CAPACITY_TO_W[r["capacity_unit"]], r["capacity_W"], rel_tol=1e-12)
    for group in regression["fits"].values():
        for key in ("family_predictions", "model_group_predictions", "manufacturer_predictions"):
            p = group[key]
            assert all(x["fold"] not in x["train_groups"] for x in p)
            if p:
                assert len(p) == group["n"]
    assert sha(HERE / "measurements.json") == regression["data_sha256"]
    releases = json.loads((HERE / "releases.json").read_text()) if (HERE / "releases.json").exists() else []
    report = {
        "records": len(rows),
        "eligible": sum(r["eligible"] for r in rows),
        "excluded": Counter(r["exclusion_reason"] for r in rows if not r["eligible"]),
        "strata": {k: v["n"] for k, v in regression["fits"].items()},
        "source_count": len(sources),
        "data_sha256": regression["data_sha256"],
        "is_latest_release": bool(releases) and releases[-1]["data_sha256"] == regression["data_sha256"],
        "all_checks_passed": True,
    }
    (HERE / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return report


if __name__ == "__main__":
    run()
