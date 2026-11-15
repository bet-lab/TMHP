"""Re-harvest machine-readable sources into candidate observation CSVs for review.

Writes harvest/candidates/<source_id>.csv only; it never touches observations/. Compare the candidate
with the archived file, then copy accepted rows by hand. Image-only PDFs (Panasonic) have no harvester:
transcribe them into observations/ directly and check against the rendered page.

  uv run python -m validation.default_inputs.compressor_displacement.harvest.harvest LG-ROTARY-2024
  uv run python -m validation.default_inputs.compressor_displacement.harvest.harvest HIGHLY-WEB
"""

from __future__ import annotations

import csv
import json
import re
import sys

import pymupdf

from ..build import HERE, OBSERVATION_COLUMNS, ROOT

OUT = HERE / "harvest/candidates"


def row(**values) -> dict:
    return {c: values.get(c, "") for c in OBSERVATION_COLUMNS}


def lg(source: dict) -> list[dict]:
    rows = []
    with pymupdf.open(ROOT / source["evidence_path"]) as doc:
        # Variable-speed R32/R410A ARI rows, printed pp10-11 (PDF p6). DSG is single cylinder: skipped.
        pat = r"([DG][A-Z]{1,3}\d{3}M[A-Z])\s+ARI\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d.]+)\s+([\d.]+)"
        for model, btu, watts, power, _, _ in re.findall(pat, doc[5].get_text()):
            if not model.startswith("DSG"):
                rows.append(
                    row(
                        record_id="lg:ARI60:" + model,
                        source_pdf_page=6,
                        locator=f"printed pp10–11 / Variable Speed / {model} / ARI row",
                        manufacturer="LG",
                        family_id="LG:" + model[:3],
                        model_id=model,
                        refrigerant="R32" if model[0] == "D" else "R410A",
                        protocol="ARI_60Hz_variable_twin",
                        test_condition="ARI",
                        capacity_W=watts.replace(",", ""),
                        capacity_Btu_h=btu.replace(",", ""),
                        power_W=power.replace(",", ""),
                        displacement_text=re.search(r"\d{3}", model).group(),
                        displacement_basis="lg_model_token",
                    )
                )
        # Fixed 50 Hz R410A standard (non-T3) block, printed p13 (PDF p7).
        text = doc[6].get_text()
        start = text.index("GAB046PA")
        block = text[start : text.index("Fixed Speed R410A", start)]
        pat = r"(G[A-Z]{0,2}\d{3}P[A-Z])\s+ASHRAE\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d.]+)\s+([\d.]+)"
        for model, btu, watts, power, _, _ in re.findall(pat, block):
            rows.append(
                row(
                    record_id="lg:ASHRAE50:" + model,
                    source_pdf_page=7,
                    locator=f"printed p13 / Fixed Speed R410A 50Hz / {model}",
                    manufacturer="LG",
                    family_id="LG:" + re.match(r"[A-Z]+", model).group(),
                    model_id=model,
                    refrigerant="R410A",
                    protocol="ASHRAE_50Hz_fixed_single",
                    test_condition="ASHRAE",
                    capacity_W=watts.replace(",", ""),
                    capacity_Btu_h=btu.replace(",", ""),
                    power_W=power.replace(",", ""),
                    displacement_text=re.search(r"\d{3}", model).group(),
                    displacement_basis="lg_model_token",
                )
            )
    return rows


def highly(sources: dict) -> dict[str, list[dict]]:
    """Constant-speed 50 Hz ASHRAE/T rows from the archived web tables (harvest/highly_web_tables.json)."""
    out, seen = {}, set()
    for table_source in json.loads((HERE / "harvest/highly_web_tables.json").read_text()):
        source = sources.get(table_source["source_id"])
        if source is None:
            continue
        rows = out.setdefault(source["source_id"], [])
        for index, table in enumerate(table_source.get("tables", [])):
            if len(table) < 3 or len(table[0]) != 1 or len(table[1]) < 8 or "Displacement" not in table[1][2]:
                continue
            heading = " ".join(table[0][0].split()).replace("R41 OA", "R410A")
            refrigerant = next((s for s in ["R410A", "R290", "R32", "R22"] if s in heading), None)
            if "50Hz" not in heading or not heading.startswith("Constant-") or not refrigerant:
                continue
            family = None
            for rowidx, raw in enumerate(table[2:], 2):
                cells = [" ".join(v.split()) for v in raw]
                if len(cells) == 9:
                    family = cells.pop(0)
                if len(cells) != 8 or cells[7] != "ASHRAE/T":
                    continue
                model, volume, capacity, btu = cells[:4]
                key = (model, float(capacity), float(volume), refrigerant)
                if key in seen:  # the same R290 table repeats under G and TH
                    continue
                seen.add(key)
                with pymupdf.open(ROOT / source["evidence_path"]) as doc:
                    pages = [
                        i + 1
                        for i, p in enumerate(doc)
                        if model.replace(" ", "") in p.get_text().replace(" ", "").replace("\n", "")
                    ]
                rows.append(
                    row(
                        record_id=f"highly:{source['source_id']}:{model}:{capacity}",
                        source_pdf_page=pages[0] if pages else "",
                        locator=f"table[{index}], data row[{rowidx}], heading={heading}",
                        manufacturer="Highly",
                        family_id=f"Highly:{family}",
                        model_id=model,
                        refrigerant=refrigerant,
                        protocol="ASHRAE_50Hz_fixed_single",
                        test_condition="ASHRAE/T",
                        capacity_W=capacity,
                        capacity_Btu_h=btu,
                        displacement_text=volume,
                        displacement_basis="printed",
                    )
                )
    return out


def main(target: str) -> None:
    sources = {s["source_id"]: s for s in json.loads((HERE / "sources.json").read_text())}
    if target == "LG-ROTARY-2024":
        results = {target: lg(sources[target])}
    elif target == "HIGHLY-WEB":
        results = highly(sources)
    else:
        raise SystemExit(f"No harvester for {target}; transcribe it into observations/ by hand.")
    OUT.mkdir(exist_ok=True)
    for source_id, rows in results.items():
        with (OUT / f"{source_id}.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=OBSERVATION_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        print(source_id, len(rows), "candidate rows ->", (OUT / f"{source_id}.csv").relative_to(ROOT))


if __name__ == "__main__":
    main(sys.argv[1])
