"""Cross-check the cooling paper against its frozen numerical evidence."""

import argparse
import csv
import hashlib
import json
import math
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
NS = {"hp": "http://www.hancom.co.kr/hwpml/2011/paragraph"}


def compact(text):
    return "".join(text.split())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hwpx", type=Path)
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    manuscript = json.loads((HERE / "manuscript.json").read_text())
    reference = json.loads((HERE / "data/reference.json").read_text())
    verification = json.loads((HERE / "data/verification.json").read_text())
    fingerprint = json.loads((HERE / "data/case_fingerprint.json").read_text())
    assert hashlib.sha256((HERE / "data/config.json").read_bytes()).hexdigest() == fingerprint["config_sha256"]
    assert fingerprint["source_commit"] == verification["source_commit"]
    assert reference["ground_UA_rated_W_K"] == 1440 and reference["load_UA_rated_W_K"] == 640
    config = json.loads((HERE / "data/config.json").read_text())
    inputs = config["model"]
    assert inputs["ground_flow_ref_lpm"] == inputs["ground_flow_constant_lpm"] == 24
    assert inputs["ground_flow_min_lpm"] == 9.6 and inputs["ground_flow_max_lpm"] == 36
    assert inputs["indoor_approach_max_K"] == 25
    with (HERE / "data/simulation_results.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 16
    baseline = {float(r["plr"]): r for r in rows if r["kind"] == "constant"}
    optimum = {float(r["plr"]): r for r in rows if r["kind"] == "optimal"}
    assert set(baseline) == set(optimum) == {i / 10 for i in range(3, 11)}
    for row in rows:
        assert row["converged"] == row["hx_feasible"] == "True"
        assert row["mode"] == "cooling" and float(row["T_a_room [°C]"]) == 26
        total = sum(float(row[k]) for k in ("E_cmp [W]", "E_pmp [W]", "E_iu_fan [W]"))
        assert math.isclose(total, float(row["E_tot [W]"]), rel_tol=1e-10)
        assert math.isclose(float(row["cop_sys [-]"]), float(row["Q_ref_iu [W]"]) / total, rel_tol=1e-10)
        assert math.isclose(float(row["Q_ref_iu [W]"]), 8000 * float(row["plr"]), rel_tol=1e-9)
        assert math.isclose(float(row["E_cmp_ref [W]"]), 0.8 * float(row["E_cmp [W]"]), rel_tol=1e-10)
        assert float(row["eta_v [-]"]) == 0.9 and float(row["eta_em [-]"]) == 0.8
        assert math.isclose(
            float(row["Q_ref_ground [W]"]), float(row["Q_ref_iu [W]"]) + float(row["E_cmp_ref [W]"]), rel_tol=1e-10
        )
    calculated = {
        "minimum_flow_percent": min(100 * float(r["ground_flow_ref_ratio"]) for r in optimum.values()),
        "maximum_flow_percent": max(100 * float(r["ground_flow_ref_ratio"]) for r in optimum.values()),
        "maximum_pump_saving_percent": max(
            100 * (1 - float(r["E_pmp [W]"]) / float(baseline[p]["E_pmp [W]"])) for p, r in optimum.items()
        ),
        "maximum_fan_increase_percent": max(
            100 * (float(r["E_iu_fan [W]"]) / float(baseline[p]["E_iu_fan [W]"]) - 1) for p, r in optimum.items()
        ),
        "maximum_total_power_saving_percent": max(
            100 * (1 - float(r["E_tot [W]"]) / float(baseline[p]["E_tot [W]"])) for p, r in optimum.items()
        ),
        "maximum_system_COP_gain_percent": max(
            100 * (float(r["cop_sys [-]"]) / float(baseline[p]["cop_sys [-]"]) - 1) for p, r in optimum.items()
        ),
        "m_dot_ref_rated_kg_s": reference["m_dot_ref_rated_kg_s"],
    }
    for key, value in calculated.items():
        assert math.isclose(manuscript["claims"][key], value, abs_tol=1e-10), key
    bounds = sorted(p for p, r in optimum.items() if r["flow_bound_active"] == "True")
    assert bounds == manuscript["claims"]["upper_bound_plrs"] == verification["selected_upper_bound_plrs"]
    for p in bounds:
        assert math.isclose(
            float(optimum[p]["ground_flow_ref_ratio"]),
            inputs["ground_flow_max_lpm"] / inputs["ground_flow_ref_lpm"],
            rel_tol=1e-12,
        )
        assert float(optimum[p]["E_tot [W]"]) <= float(baseline[p]["E_tot [W]"])
    assert len(manuscript["abstract"]) == 3 and len(manuscript["results"]) == 2
    result_text = "\n".join(manuscript["results"])
    for required in (
        f"{calculated['minimum_flow_percent']:.1f}–{calculated['maximum_flow_percent']:.1f}%",
        f"{calculated['maximum_total_power_saving_percent']:.2f}%",
        f"{calculated['maximum_system_COP_gain_percent']:.2f}%",
        "16개",
    ):
        assert required in result_text
    required_text = [manuscript[key] for key in ("title_ko", "title_en", "caption_1", "acknowledgement")]
    required_text += manuscript["abstract"] + manuscript["results"]
    with ZipFile(args.hwpx) as z:
        sec = ET.fromstring(z.read("Contents/section0.xml"))
        native_text = compact("\n".join("".join(t.itertext()) for t in sec.findall(".//hp:t", NS)))
        assert len(sec.findall(".//hp:pic", NS)) == 1
        assert z.read("BinData/image1.png") == (HERE / "figure/fig_1_part_load.png").read_bytes()
        assert "BinData/image2.png" not in z.namelist()
        assert z.read("mimetype") == b"application/hwp+zip"
        for text in required_text:
            assert compact(text) in native_text
    pdf_text = compact(subprocess.check_output(["pdftotext", "-layout", str(args.pdf), "-"], text=True))
    for required in required_text + [
        "조하빈",
        "박수현",
        "최원준",
        "Ph.D. Student",
        "M.S. Student",
        "Associate Professor",
        "Extended Abstract",
        "Results and Discussions",
        "Acknowledgement",
        "wonjun.choi@jnu.ac.kr",
        "2026 한국지열·수열에너지학회 학술발표대회",
    ]:
        assert compact(required) in pdf_text, required
    assert not any(token in pdf_text for token in ("[CITE]", "TODO", "??", "RS-2023-00277318", "Fig.2"))
    info = subprocess.check_output(["pdfinfo", str(args.pdf)], text=True)
    assert any(line.split() == ["Pages:", "1"] for line in info.splitlines())
    assert manuscript["title_ko"] in info
    fonts = subprocess.check_output(["pdffonts", str(args.pdf)], text=True)
    assert fonts.splitlines()[2:]
    for line in fonts.splitlines()[2:]:
        assert line.split()[-5] == "yes", line
    mcp = json.loads((HERE / "figure/mcp_review.json").read_text())
    assert mcp["calls"][0]["result"] == []
    assert len(mcp["calls"]) == 11
    assert all(call["result"].startswith("✅ Data structure valid") for call in mcp["calls"][1:])
    assert {call["figure"] for call in mcp["calls"][1:]} == {
        "fig_1_part_load",
        "fig_total_power_objective",
        "fig_hx_feasibility",
        "fig_flow_max_sensitivity",
    }
    visual = json.loads((HERE / "figure/visual_validation.json").read_text())
    assert visual["style"] == "scientific" and all(
        not issues for issues in visual["dartwork_mpl_render_checks"].values()
    )
    inspected = visual["visual_inspection"]["fig_1_part_load"]
    for suffix in ("png", "pdf"):
        assert (
            inspected[f"{suffix}_sha256"]
            == hashlib.sha256((HERE / f"figure/fig_1_part_load.{suffix}").read_bytes()).hexdigest()
        )
    sensitivity = json.loads((HERE / "data/flow_max_sensitivity_verification.json").read_text())
    assert sensitivity["all_selected_feasible"] and sensitivity["identical_actual_point_component_physics"]
    report = {
        "one_page": True,
        "figures": 1,
        "panels": 4,
        "selected_points": len(rows),
        "claims_match_csv": calculated,
        "upper_bound_plrs": bounds,
        "total_power_and_COP_checked": True,
        "fixed_reference_and_explicit_flow_bounds_checked": True,
        "sensitivity_fairness_checked": True,
        "all_manuscript_text_present_in_PDF": True,
        "authors_affiliations_acknowledgement_present": True,
        "pdf_fonts_embedded": True,
        "scientific_style_and_MCP_checks": True,
        "hwpx_sha256": hashlib.sha256(args.hwpx.read_bytes()).hexdigest(),
        "pdf_sha256": hashlib.sha256(args.pdf.read_bytes()).hexdigest(),
        "renderer_limit": "Linux pyhwpxlib/rhwp and font substitutes; not tested in Hancom Office",
    }
    (HERE / "qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
