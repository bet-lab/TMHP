"""Extract the author's 134 R290 rows and audit the published loss interpretation.

Requires pdftotext and CoolProp. Run with --thesis-pdf pointing to the full PDF
at THESIS_URL; no digitized or invented state values are introduced. The rows
retain the rounded values printed in Appendix 1, PDF pages 244 through 252.
"""

import argparse
import csv
import hashlib
import json
import math
import subprocess
from pathlib import Path

import CoolProp
from CoolProp.CoolProp import PropsSI

THESIS_URL = "https://riunet.upv.es/bitstreams/6612b905-7a7a-436d-b73c-ac1bc903c27a/download"
THESIS_SHA256 = "66f32f7ba6190c527317be55a66d584a3b85588c7263e539d578c937f8f0fa47"
FIELDS = (
    "Tevap_C",
    "Tcond_C",
    "speed_rps",
    "Tsuc_C",
    "Tdis_C",
    "mdot_g_s",
    "compressor_power_W",
    "compressor_plus_inverter_power_W",
    "author_eta_v",
    "author_eta_is",
    "author_eta_comp",
)


def extract_rows(pdf: Path) -> list[dict]:
    """Read numeric Appendix 1 rows, preserving PDF page and printed precision."""
    content = subprocess.check_output(["pdftotext", "-layout", str(pdf), "-"]).decode()
    active = False
    rows = []
    for page_number, page in enumerate(content.split("\f"), 1):
        if "Appendix 1:" in page and "Compressor Data" in page:
            active = True
        elif active and "Appendix 2:" in page:
            break
        if not active:
            continue
        page_row = 0
        for line in page.splitlines():
            values = line.split()
            if len(values) != len(FIELDS):
                continue
            try:
                numbers = [float(value) for value in values]
            except ValueError:
                continue
            if not all(math.isfinite(value) for value in numbers):
                raise ValueError(f"Nonfinite source row on PDF page {page_number}")
            page_row += 1
            rows.append(
                {
                    "point_id": f"thesis-r290-{len(rows) + 1:03d}",
                    "source_pdf_page": page_number,
                    "source_page_row": page_row,
                    **dict(zip(FIELDS, values, strict=True)),
                }
            )
    if len(rows) != 134:
        raise ValueError(f"Expected the 134 printed Appendix 1 rows, got {len(rows)}")
    speeds = {float(row["speed_rps"]) for row in rows}
    if speeds != {30, 50, 70, 90, 110}:
        raise ValueError(f"Unexpected speed groups: {speeds}")
    return rows


def evaluate(row: dict) -> dict:
    """Audit loss and discharge temperature using measured total electrical input.

    The source formulas are 2024 Eqs. 10/17, Tables 3/4, and thesis Eq. 3.22 with
    Table 3.5. Pressure is absolute bar inside correlations; speed is shaft rev/s.
    CoolProp HEOS replaces the author's REFPROP 10 and is reported explicitly.
    """
    values = {key: float(row[key]) for key in FIELDS}
    pe = PropsSI("P", "T", values["Tevap_C"] + 273.15, "Q", 1, "Propane")
    pc = PropsSI("P", "T", values["Tcond_C"] + 273.15, "Q", 1, "Propane")
    pr = pc / pe
    speed_ratio = values["speed_rps"] / 70
    pel = values["compressor_plus_inverter_power_W"]
    mass = values["mdot_g_s"] / 1000
    h1 = PropsSI("H", "P", pe, "T", values["Tsuc_C"] + 273.15, "Propane")
    h2 = PropsSI("H", "P", pc, "T", values["Tdis_C"] + 273.15, "Propane")
    s1 = PropsSI("S", "P", pe, "T", values["Tsuc_C"] + 273.15, "Propane")
    h2s = PropsSI("H", "P", pc, "S", s1, "Propane")
    loss = 0.103 - 0.00390 * pr + 0.00800 * pr / speed_ratio - 14.8 / pel
    eta_em_author = values["author_eta_comp"] / values["author_eta_is"]
    eta_em_heos = mass * (h2 - h1) / pel
    tdis = PropsSI("T", "P", pc, "H", h1 + (1 - loss) * pel / mass, "Propane") - 273.15
    pe_bar, pc_bar = pe / 1e5, pc / 1e5
    kv = -0.026 * (speed_ratio - 104.386 / 70) ** 2 + 1 + 0.026 * (1 - 104.386 / 70) ** 2
    mass_pred_g_s = (-0.810 + 6.815 * pe_bar - 0.019 * pc_bar - 0.008 * pe_bar * pc_bar) * speed_ratio * kv
    kc = 0.185 * (speed_ratio - 72.916 / 70) ** 2 + 1 - 0.185 * (1 - 72.916 / 70) ** 2
    specific_kj_kg = (-0.251 + 21.030 * (pc_bar + 6.696) / (pe_bar + 0.511)) * kc
    power_pred_W = mass_pred_g_s * specific_kj_kg
    return {
        "point_id": row["point_id"],
        "PR_HEOS": pr,
        "loss_Table5_measured_power": loss,
        "eta_em_author_ratio": eta_em_author,
        "eta_em_HEOS": eta_em_heos,
        "eta_em_complement": 1 - loss,
        "tdis_pred_from_measured_mdot_power_C": tdis,
        "tdis_residual_K": tdis - values["Tdis_C"],
        "eta_ov_HEOS": mass * (h2s - h1) / pel,
        "eta_ov_HEOS_minus_author": mass * (h2s - h1) / pel - values["author_eta_comp"],
        "mdot_Eq10_g_s": mass_pred_g_s,
        "mdot_Eq10_residual_g_s": mass_pred_g_s - values["mdot_g_s"],
        "power_Eq17_W": power_pred_W,
        "power_Eq17_residual_W": power_pred_W - pel,
    }


def rmse(residuals: list[float]) -> float:
    """Return unweighted root mean square over every supplied residual."""
    if not residuals:
        raise ValueError("RMSE requires at least one observation")
    return math.sqrt(sum(value * value for value in residuals) / len(residuals))


def write_csv(path: Path, rows: list[dict]) -> None:
    """Write a complete row-preserving CSV with explicit field names."""
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    """Extract and verify the supplied PDF, then write reproducible audit outputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thesis-pdf", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    digest = hashlib.sha256(args.thesis_pdf.read_bytes()).hexdigest()
    if digest != THESIS_SHA256:
        raise ValueError("PDF hash differs from the visually checked source")
    rows = extract_rows(args.thesis_pdf)
    calculated = [evaluate(row) for row in rows]
    results = {
        "source_url": THESIS_URL,
        "source_sha256": digest,
        "source_pdf_pages": [244, 252],
        "source_equation_pdf_page": 116,
        "source_table35_pdf_page": 119,
        "n": len(rows),
        "excluded_rows": 0,
        "CoolProp_version": CoolProp.__version__,
        "property_backend": "HEOS::Propane",
        "power_boundary": "compressor plus inverter, measured",
        "RMSE_eta_em_as_printed_journal_Eq22": rmse(
            [r["loss_Table5_measured_power"] - r["eta_em_author_ratio"] for r in calculated]
        ),
        "RMSE_eta_em_thesis_Eq3_22_vs_author_ratio": rmse(
            [r["eta_em_complement"] - r["eta_em_author_ratio"] for r in calculated]
        ),
        "RMSE_eta_em_thesis_Eq3_22_vs_HEOS": rmse([r["eta_em_complement"] - r["eta_em_HEOS"] for r in calculated]),
        "RMSE_tdis_from_measured_mdot_power_K": rmse([r["tdis_residual_K"] for r in calculated]),
        "RMSE_eta_ov_HEOS_vs_author": rmse([r["eta_ov_HEOS_minus_author"] for r in calculated]),
        "RMSE_mass_Eq10_g_s": rmse([r["mdot_Eq10_residual_g_s"] for r in calculated]),
        "RMSE_total_power_Eq17_W": rmse([r["power_Eq17_residual_W"] for r in calculated]),
        "interpretation_status": "evidence-backed: author thesis Eq3.22, matching coefficients, all 134 printed rows",
        "limitations": [
            "The journal fits use 133 rows; the thesis prints 134. The omitted journal row is not identified.",
            "The thesis prints rounded measurements and efficiencies; source precision is preserved.",
            "The paper used REFPROP 10.0; this audit uses CoolProp HEOS.",
            "The discharge-temperature audit conditions on measured mass flow and total power; it is not an end-to-end heat-pump validation.",
            "The 134 rows are source-model training data, not an independent test set.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "ossorio_thesis_r290_134.csv", rows)
    write_csv(args.output_dir / "ossorio_thesis_r290_134_audit.csv", calculated)
    (args.output_dir / "audit_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
