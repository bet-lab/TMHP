"""Check the paper's numerical claims against recorded data and native output."""

import argparse
import csv
import hashlib
import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NS = {"hp": "http://www.hancom.co.kr/hwpml/2011/paragraph"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hwpx", type=Path)
    parser.add_argument("pdf", type=Path)
    args = parser.parse_args()
    manuscript = json.loads((HERE / "manuscript.json").read_text())
    text = "\n".join(manuscript["results"])
    with (ROOT / "validation/gshp_ground_flow/results/operating_points.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    baseline = {float(r["plr"]): r for r in rows if r["kind"] == "constant"}
    optimum = [r for r in rows if r["kind"] == "optimal"]
    flows, pumps, cops = [], [], []
    for row in optimum:
        ref = baseline[float(row["plr"])]
        assert row["converged"] == ref["converged"] == "True"
        flows.append(100 * float(row["ground_flow_ratio"]))
        pumps.append(100 * (1 - float(row["E_pmp [W]"]) / float(ref["E_pmp [W]"])))
        cops.append(100 * (float(row["cop_sys [-]"]) / float(ref["cop_sys [-]"]) - 1))
        if float(row["plr"]) >= 0.6:
            assert float(row["cop_ref [-]"]) < float(ref["cop_ref [-]"])
    ranges = {}
    for name, values in (("flow_percent", flows), ("pump_saving_percent", pumps), ("system_COP_gain_percent", cops)):
        phrase = f"{min(values):.1f}–{max(values):.1f}%"
        assert phrase in text, (name, phrase)
        ranges[name] = phrase
    with ZipFile(args.hwpx) as z:
        sec = ET.fromstring(z.read("Contents/section0.xml"))
        native_text = "\n".join("".join(t.itertext()) for t in sec.findall(".//hp:t", NS))
        assert len(sec.findall(".//hp:pic", NS)) == 2
        assert z.read("mimetype") == b"application/hwp+zip"
        for key in ("title_ko", "title_en", "acknowledgement"):
            assert manuscript[key] in native_text, key
        for paragraph in manuscript["abstract"] + manuscript["results"]:
            assert paragraph in native_text
        assert "RS-2023-00277318" not in native_text
    pdf_text = subprocess.check_output(["pdftotext", "-layout", str(args.pdf), "-"], text=True)
    compact = "".join(pdf_text.split())
    for required in (
        "조하빈",
        "박수현",
        "최원준",
        "Associate Professor",
        "Extended Abstract",
        "Fig. 1",
        "Fig. 2",
        "Results and Discussions",
        "Acknowledgement",
        "RS-2025-00512551",
        "wonjun.choi@jnu.ac.kr",
    ):
        assert "".join(required.split()) in compact, required
    assert not any(token in compact for token in ("[CITE]", "TODO", "??", "RS-2023-00277318"))
    info = subprocess.check_output(["pdfinfo", str(args.pdf)], text=True)
    assert any(line.split() == ["Pages:", "1"] for line in info.splitlines())
    assert manuscript["title_ko"] in info
    fonts = subprocess.check_output(["pdffonts", str(args.pdf)], text=True)
    for line in fonts.splitlines()[2:]:
        # last columns: emb sub uni object ID
        assert line.split()[-5] == "yes", line
    report = {
        "one_page": True,
        "figures": 2,
        "claims_match_csv": ranges,
        "authors_affiliations_acknowledgement_present": True,
        "pdf_fonts_embedded": True,
        "hwpx_sha256": hashlib.sha256(args.hwpx.read_bytes()).hexdigest(),
        "pdf_sha256": hashlib.sha256(args.pdf.read_bytes()).hexdigest(),
        "renderer_limit": "Linux pyhwpxlib/rhwp and font substitutes; not tested in Hancom Office",
    }
    (HERE / "qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
