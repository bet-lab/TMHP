"""Extract a single published heating table and render unaltered source excerpts.

Usage: uv run --no-project --with pymupdf==1.26.7 python THIS.py
Requires the locally preserved manufacturer PDF; never modifies source PDFs.
"""

import csv
import hashlib
import json
import re
import subprocess
from pathlib import Path

from render_evidence_crops import render_crop

ROOT = Path(__file__).resolve().parent
PDF = Path("/home/habin/papers/temp/compressor_factor/lambda_and_catalogues_20261019/PDF/Fujitsu_ASYG30LMTA_full.pdf")
PAPER = Path(
    "/home/habin/papers/temp/261102 presentation/Ossorio-SantiagoMarchante-AvellanedaNavarro-Peris - Development of compact empirical models for v.pdf"
)
URL = "https://www.viacomvs.com/wf-doc/fujitsu-klima-uredjaj-zidni-inverter-asyg30lmta-aoyg30lmta-technical-manual.pdf"
EXPECTED = "39abcd854c3e44fe8f544adce5efce8102f32516ec6c56583f4f35cf5ad06a35"


def main():
    assert hashlib.sha256(PDF.read_bytes()).hexdigest() == EXPECTED
    text = subprocess.check_output(["pdftotext", "-layout", str(PDF), "-"], text=True)
    page = text.split("\f")[20]
    table = page.split("Model: ASYG30LMTA")[1].split("Model: ASYG36LMTA")[0]
    rows = []
    for line in table.splitlines():
        parts = line.split()
        if len(parts) != 12 or not all(re.fullmatch(r"-?\d+(?:\.\d+)?", p) for p in parts):
            continue
        numbers = list(map(float, parts))
        out_db, out_wb = numbers[:2]
        for i, indoor in enumerate([16, 18, 20, 22, 24]):
            q, power = numbers[2 + 2 * i : 4 + 2 * i]
            rows.append(
                dict(
                    point_id=f"fujitsu30_heat_db{out_db:g}_in{indoor}",
                    model="ASYG30LMTA/AOYG30LMTA",
                    mode="heating",
                    indoor_db_C=indoor,
                    outdoor_db_C=out_db,
                    outdoor_wb_C=out_wb,
                    Q_catalogue_kW=q,
                    PI_catalogue_kW=power,
                    COP_catalogue=q / power,
                    input_temperature_basis="published dry bulb; no WB conversion",
                    source_pdf_page=21,
                    source_printed_page=17,
                    table_basis="manufacturer calculated maximum-capacity table",
                    proposed_split=("diagnostic_cold" if out_db < 7 else ("train" if indoor == 20 else "holdout")),
                    frost_note=(
                        "7C does not guarantee frost-free evaporator"
                        if out_db == 7
                        else (
                            "cold diagnostic; no defrost model"
                            if out_db < 7
                            else "warm-ambient subset; coil frost must still be checked"
                        )
                    ),
                )
            )
    assert len(rows) == 50
    assert len({r["point_id"] for r in rows}) == 50
    assert len([r for r in rows if r["proposed_split"] == "train"]) == 5
    assert len([r for r in rows if r["proposed_split"] == "holdout"]) == 20
    old_path = PDF.parent.parent / "Fujitsu_candidate_268points.csv"
    old = [
        r for r in csv.DictReader(old_path.open()) if r["model"] == "ASYG30LMTA/AOYG30LMTA" and r["mode"] == "heating"
    ]
    assert len(old) == 50
    for a, b in zip(rows, old, strict=True):
        for new_key, old_key in [
            ("indoor_db_C", "Tin_DB_C"),
            ("outdoor_db_C", "Tout_DB_C"),
            ("outdoor_wb_C", "Tout_WB_C"),
            ("Q_catalogue_kW", "Q_kW"),
            ("PI_catalogue_kW", "PI_kW"),
        ]:
            assert float(a[new_key]) == float(b[old_key])
    with (ROOT / "fujitsu_asyg30lmta_heating_50.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    output = ROOT / "crops"
    output.mkdir(exist_ok=True)
    recipes = [
        (
            PDF,
            dict(
                name="fujitsu30_original_heating_table",
                pdf_page=21,
                printed_page=17,
                rect_pdf_points=[55, 28, 555, 273],
                highlight_phrases=["calculated based on the maximum capacity", "°CDB", "°CWB", "1,380"],
                source_url=URL,
            ),
        ),
        (
            PAPER,
            dict(
                name="2024_original_table5_r290",
                pdf_page=14,
                rect_pdf_points=[34, 367, 563, 476],
                highlight_phrases=[],
                highlight_rects=[[113, 392, 176, 471]],
                source_url="https://doi.org/10.1016/j.applthermaleng.2024.122666",
            ),
        ),
        (
            PAPER,
            dict(
                name="2024_table5_r290_excerpt",
                pdf_page=14,
                rect_pdf_points=[34, 392, 181, 476],
                highlight_phrases=[],
                highlight_rects=[[113, 392, 176, 471]],
                source_url="https://doi.org/10.1016/j.applthermaleng.2024.122666",
            ),
        ),
    ]
    manifest = [render_crop(source, recipe, output) for source, recipe in recipes]
    (output / "catalogue_table5_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {"heating_points": len(rows), "source_sha256": EXPECTED, "crops": [r["output"] for r in manifest]}, indent=2
        )
    )


if __name__ == "__main__":
    main()
