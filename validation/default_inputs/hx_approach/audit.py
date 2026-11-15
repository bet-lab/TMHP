"""Check the evidence files and the transcription of the heat-exchanger approach archive.

Needs the local evidence files (validation/evidence/, not in Git); build does not.
  uv run --with pymupdf python -m validation.default_inputs.hx_approach.audit

Three checks:
1. every source file exists with the recorded SHA-256 and page count;
2. every number transcribed into an observation row is printed in the source: on the cited page, or, for values
   the row takes from another table of the same document (rated capacity, airflow), elsewhere in it. Numbers
   found nowhere are listed (values read from a figure, scanned pages); they do not fail the audit, an
   unlisted one does;
3. measurements.csv is the build of the current observations and regression.json rests on it.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter

import pymupdf

from validation.default_inputs.archive import sha

from .build import HERE, OBSERVATION_COLUMNS, ROOT

NUMERIC_FIELDS = (
    "fluid_in_text", "fluid_in_wb_text", "fluid_out_text", "flow_text", "pressure_text", "sat_temp_text",
    "duty_text", "input_power_text", "nameplate_capacity_text", "compressor_speed_text",
)  # fmt: skip
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def numbers(text: str) -> list[str]:
    return _NUMBER.findall(text.replace(",", ""))


def page_tokens(text: str) -> set[str]:
    tokens = set(numbers(text))
    # '12.0' is found as '12' and the reverse: a printed trailing zero is not a different value.
    return tokens | {t.rstrip("0").rstrip(".") for t in tokens if "." in t}


def run() -> dict:
    sources = {s["source_id"]: s for s in json.loads((HERE / "sources.json").read_text())}
    unverifiable = {}
    path = HERE / "unverifiable.json"
    if path.exists():
        unverifiable = json.loads(path.read_text())  # record_id -> {field: reason}
    pages: dict[str, list[set[str]]] = {}
    for s in sources.values():
        file = ROOT / s["evidence_path"]
        assert sha(file) == s["sha256"], file
        if file.suffix.lower() == ".pdf":
            with pymupdf.open(file) as pdf:
                assert len(pdf) == s["pages"], (s["source_id"], len(pdf))
                pages[s["source_id"]] = [page_tokens(p.get_text()) for p in pdf]
    found = Counter()
    missing = []
    for file in sorted((HERE / "observations").glob("*.csv")):
        source_id = file.stem
        with file.open(newline="") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == OBSERVATION_COLUMNS, file.name
            for row in reader:
                doc = pages.get(source_id)
                if doc is None:
                    found["source_not_pdf"] += 1
                    continue
                index = int(row["source_pdf_page"]) - 1
                assert 0 <= index < len(doc), row["record_id"]
                everywhere = set().union(*doc)
                for field in NUMERIC_FIELDS:
                    for token in numbers(row[field]):
                        short = token.rstrip("0").rstrip(".") if "." in token else token
                        if token in doc[index] or short in doc[index]:
                            found["on_cited_page"] += 1
                        elif token in everywhere or short in everywhere:
                            found["elsewhere_in_document"] += 1
                        elif field in unverifiable.get(row["record_id"], {}):
                            found["listed_unverifiable"] += 1
                        else:
                            missing.append({"record_id": row["record_id"], "field": field, "value": row[field]})
    assert not missing, f"{len(missing)} transcribed numbers are not printed in their source: {missing[:10]}"
    with (HERE / "measurements.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    regression = json.loads((HERE / "regression.json").read_text())
    assert sha(HERE / "measurements.csv") == regression["data_sha256"]
    for r in rows:
        assert r["source_sha256"] == sources[r["source_id"]]["sha256"], r["record_id"]
        r["eligible"] = r["eligible"] == "True"
    releases = json.loads((HERE / "releases.json").read_text()) if (HERE / "releases.json").exists() else []
    report = {
        "records": len(rows),
        "eligible": sum(r["eligible"] for r in rows),
        "excluded": Counter(r["exclusion_reason"] for r in rows if not r["eligible"]),
        "strata": {k: v["n"] for k, v in regression["fits"].items()},
        "source_count": len(sources),
        "transcribed_numbers": dict(found),
        "data_sha256": regression["data_sha256"],
        "is_latest_release": bool(releases) and releases[-1]["data_sha256"] == regression["data_sha256"],
        "all_checks_passed": True,
    }
    (HERE / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return report


if __name__ == "__main__":
    run()
