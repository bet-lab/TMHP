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
    assert reference["ground_UA_rated_W_K"] == 1600 and reference["load_UA_rated_W_K"] == 800
    config = json.loads((HERE / "data/config.json").read_text())
    inputs = config["model"]
    assert inputs["ground_flow_ref_lpm"] == inputs["ground_flow_constant_lpm"] == 24
    assert inputs["ground_flow_min_lpm"] == 9.6 and inputs["ground_flow_max_lpm"] == 36
    assert inputs["indoor_approach_max_K"] == 25
    assert config["compressor_efficiency_model"] == "baseline-v2026-09-24"
    assert not any(k in inputs for k in ("eta_cmp_isen", "eta_cmp_vol", "eta_cmp", "eta_v", "eta_em"))
    efficiency_check = json.loads((HERE / "data/compressor_efficiency_verification.json").read_text())
    assert efficiency_check["all_selected_baseline_parity"] and efficiency_check["selected_points"] == 16
    assert efficiency_check["model_source_commit"] == verification["source_commit"]
    assert efficiency_check["efficiency_module_sha256"] == fingerprint["efficiency_module_sha256"]
    assert (
        hashlib.sha256((HERE / "data/compressor_efficiency_comparison.csv").read_bytes()).hexdigest()
        == efficiency_check["comparison_sha256"]
    )
    assert verification["efficiency_and_mass_flow_checks"]
    with (HERE / "data/simulation_results.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert verification["constant_flow_24_lpm_checked"]
    assert verification["aux_pressure_scaling_checked"]
    assert verification["all_feasible_water_and_fan_flows_within_bounds_checked"]
    assert math.isclose(
        inputs["dp_aux_ref"], 138000 - config["aux_pressure_drop_selection"]["BHE_ref_Pa"], abs_tol=1e-9
    )
    assert inputs["dp_aux_exponent"] == 2.0
    assert "121.03 kPa" in manuscript["abstract"][2]
    requested_rows = rows
    assert len(requested_rows) == 16
    failed_rows = [r for r in requested_rows if r["converged"] == "False"]
    assert len(failed_rows) == 2 and {float(r["plr"]) for r in failed_rows} == {0.3}
    for r in failed_rows:
        assert (not r["cop_sys [-]"] or math.isnan(float(r["cop_sys [-]"]))) and float(r["E_tot [W]"]) == 0
        assert r["failure_reason"] != "none"
    rows = [r for r in requested_rows if r["converged"] == "True"]
    assert len(rows) == manuscript["claims"]["feasible_selected_points"] == verification["feasible_selected_points"]
    baseline = {float(r["plr"]): r for r in rows if r["kind"] == "constant"}
    optimum = {float(r["plr"]): r for r in rows if r["kind"] == "optimal"}
    assert set(baseline) == set(optimum) == {i / 10 for i in range(4, 11)}
    for row in rows:
        assert row["converged"] == row["hx_feasible"] == "True"
        flow = float(row["ground_flow_L_min"])
        assert 9.6 - 1e-10 <= flow <= 36 + 1e-10
        if row["kind"] == "constant":
            assert math.isclose(flow, 24, abs_tol=1e-12)
            assert math.isclose(float(row["E_pmp [W]"]), 92, abs_tol=1e-9)
        dp_bhe, dp_aux, dp_total = (
            float(row[k])
            for k in (
                "ground_pressure_drop_bhe [Pa]",
                "ground_pressure_drop_aux [Pa]",
                "ground_pressure_drop_total [Pa]",
            )
        )
        assert math.isclose(dp_total, dp_bhe + dp_aux, rel_tol=1e-12)
        assert math.isclose(dp_aux, inputs["dp_aux_ref"] * (flow / 24) ** 2, rel_tol=1e-12)
        assert inputs["dV_iu_fan_a_min"] <= float(row["dV_iu_a [m3/s]"]) <= inputs["dV_iu_fan_a_max"]
        assert row["mode"] == "cooling" and float(row["T_a_room [°C]"]) == 26
        total = sum(float(row[k]) for k in ("E_cmp [W]", "E_pmp [W]", "E_iu_fan [W]"))
        assert math.isclose(total, float(row["E_tot [W]"]), rel_tol=1e-10)
        assert math.isclose(float(row["cop_sys [-]"]), float(row["Q_ref_iu [W]"]) / total, rel_tol=1e-10)
        assert math.isclose(float(row["Q_ref_iu [W]"]), 8000 * float(row["plr"]), rel_tol=1e-9)
        assert math.isclose(
            float(row["E_cmp_ref [W]"]), float(row["eta_em [-]"]) * float(row["E_cmp [W]"]), rel_tol=1e-10
        )
        assert all(0 < float(row[k]) <= 1 for k in ("eta_is [-]", "eta_v [-]", "eta_em [-]"))
        rps = float(row["cmp_rps [rev/s]"])
        assert math.isclose(float(row["n_star [-]"]), rps / inputs["rps_rated"], rel_tol=1e-10)
        assert math.isclose(
            float(row["m_dot_ref [kg/s]"]),
            inputs["V_cmp_ref"] * float(row["rho_ref_cmp_in [kg/m3]"]) * float(row["eta_v [-]"]) * rps,
            rel_tol=1e-10,
        )
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
    bounds = sorted(p for p, r in optimum.items() if r["ground_flow_at_max"] == "True")
    assert bounds == manuscript["claims"]["upper_bound_plrs"] == verification["selected_upper_bound_plrs"]
    lower_bounds = sorted(p for p, r in optimum.items() if r["ground_flow_at_min"] == "True")
    assert lower_bounds == manuscript["claims"]["lower_bound_plrs"] == verification["selected_lower_bound_plrs"]
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
    assert len(mcp["calls"]) == 15
    assert mcp["skipped_infeasible_plrs"] == [0.3]
    assert all(call["result"].startswith("✅ Data structure valid") for call in mcp["calls"][1:])
    assert {call["figure"] for call in mcp["calls"][1:]} == {
        "fig_1_part_load",
        "fig_total_power_objective",
        "fig_hx_feasibility",
        "fig_flow_max_sensitivity",
        "fig_compressor_speed",
        "fig_compressor_efficiencies",
        "fig_compressor_pressure_ratio",
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
    assert (
        sensitivity["failed_points_excluded_from_savings"] and sensitivity["identical_actual_point_component_physics"]
    )
    report = {
        "one_page": True,
        "figures": 1,
        "panels": 4,
        "requested_selected_points": len(requested_rows),
        "feasible_selected_points": len(rows),
        "failed_selected_points_excluded": len(failed_rows),
        "claims_match_csv": calculated,
        "upper_bound_plrs": bounds,
        "lower_bound_plrs": lower_bounds,
        "total_power_and_COP_checked": True,
        "callable_baseline_and_mass_flow_checked": True,
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
