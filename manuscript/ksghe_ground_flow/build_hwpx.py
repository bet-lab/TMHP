"""Update the supplied HWP-converted template without changing font sizes.

Usage: python build_hwpx.py template_converted.hwpx output.hwpx
The original HWP5 binary must be retained separately.
"""

import copy
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
}
for key, value in NS.items():
    ET.register_namespace(key, value)


def replace_text(p, text, char=None):
    """Replace direct text runs while preserving header/footer/object controls."""
    runs = p.findall("hp:run", NS)
    if char is None:
        char = next(r.get("charPrIDRef") for r in runs if r.find("hp:t", NS) is not None)
    for run in runs:
        if run.find("hp:t", NS) is not None:
            p.remove(run)
    run = ET.SubElement(p, f"{{{NS['hp']}}}run", charPrIDRef=char)
    t = ET.SubElement(run, f"{{{NS['hp']}}}t")
    lines = text.split("\n")
    t.text = lines[0]
    for line in lines[1:]:
        ET.SubElement(t, f"{{{NS['hp']}}}lineBreak").tail = line


def main():
    source, target = map(Path, sys.argv[1:3])
    content = json.loads((HERE / "manuscript.json").read_text())
    with ZipFile(source) as z:
        files = {name: z.read(name) for name in z.namelist()}
    sec = ET.fromstring(files["Contents/section0.xml"])
    header = ET.fromstring(files["Contents/header.xml"])
    paragraphs = sec.findall(".//hp:p", NS)
    title_table = sec.find("hp:p/hp:run/hp:tbl", NS)
    title_table.find("hp:tr/hp:tc/hp:cellSz", NS).set("height", "20500")
    title_table.find("hp:sz", NS).set("height", "24532")
    for row in title_table.findall("hp:tr", NS)[1:]:
        for run in row.findall(".//hp:run", NS):
            run.set("charPrIDRef", "11")  # original decorative row is 2 pt high
    replace_text(paragraphs[1], content["title_ko"])
    replace_text(paragraphs[6], content["title_en"])
    replace_text(paragraphs[2], "2025 한국지열·수열에너지학회 학술발표대회", "19")
    hp = header.find(".//hh:paraPr[@id='16']", NS)
    hp.set("tabPrIDRef", "0")
    hp.find("hh:align", NS).set("horizontal", "RIGHT")
    hp.find("hh:margin/hc:intent", NS).set("value", "0")
    for t in paragraphs[3].findall("hp:run/hp:t", NS):
        if t.text:
            t.text = t.text.replace("․", "·")
    body = paragraphs[14]
    replace_text(body, content["abstract"][0], "37")
    pos = list(sec).index(body)
    for i, text in enumerate(content["abstract"][1:], 1):
        p = copy.deepcopy(body)
        replace_text(p, text, "37")
        sec.insert(pos + i, p)
    replace_text(paragraphs[15], content["keywords"], "37")
    sec.remove(paragraphs[16])  # remove the empty paragraph before the figures
    replace_text(paragraphs[20], content["caption_1"], "35")
    replace_text(paragraphs[21], content["caption_2"], "35")
    for p, text in zip((paragraphs[23], paragraphs[24]), content["results"], strict=True):
        replace_text(p, "• " + text, "25")
    replace_text(paragraphs[26], content["acknowledgement"], "19")
    # HWP5 conversion erroneously maps the result-list property to page breaks.
    header.find(".//hh:paraPr[@id='12']/hh:breakSetting", NS).set("pageBreakBefore", "0")
    # Move the original page header out of a table cell (renderer compatibility).
    control = paragraphs[1].find("hp:run/hp:ctrl", NS)
    if control is not None:
        for run in paragraphs[1].findall("hp:run", NS):
            if control in list(run):
                run.remove(control)
        sec.find("hp:p/hp:run", NS).append(control)
    # Two equal-width figures; keep the two-column table and original caption type.
    table = paragraphs[17].find("hp:run/hp:tbl", NS)
    table.find("hp:outMargin", NS).set("bottom", "0")
    width, height = 22677, 12189  # 8 x 4.3 cm, native HWP units (1/7200 inch)
    table.find("hp:sz", NS).set("height", str(height + 4000))
    for cell in table.findall("hp:tr/hp:tc", NS):
        cell.find("hp:cellSz", NS).set("width", "23812")
        cell.find("hp:subList", NS).set("textWidth", "23812")
        cell.find("hp:cellSz", NS).set(
            "height", str(height + 300) if cell.find(".//hp:pic", NS) is not None else "3500"
        )
    for i, pic in enumerate(table.findall(".//hp:pic", NS), 1):
        for name in ("orgSz", "curSz", "sz"):
            e = pic.find("hp:" + name, NS)
            e.set("width", str(width))
            e.set("height", str(height))
        pic.find("hp:imgDim", NS).set("dimwidth", str(width))
        pic.find("hp:imgDim", NS).set("dimheight", str(height))
        for name, x, y in (("pt0", 0, 0), ("pt1", width, 0), ("pt2", width, height), ("pt3", 0, height)):
            e = pic.find("hp:imgRect/hc:" + name, NS)
            e.set("x", str(x))
            e.set("y", str(y))
        pic.find("hc:img", NS).set("binaryItemIDRef", f"BIN000{i}")
        for p in table.findall(".//hp:p", NS):
            if pic in list(p.iter()):
                p.set("paraPrIDRef", "17")
        figure = "behavior" if i == 1 else "cop"
        files[f"BinData/BIN000{i}.png"] = (
            ROOT / f"figures/mpl/gshp_ground_flow/output/fig_paper_{i}_{figure}.png"
        ).read_bytes()
    # Stale line positions belong to the original text; let the editor reflow.
    for p in sec.findall(".//hp:p", NS):
        for lines in p.findall("hp:linesegarray", NS):
            p.remove(lines)
    for name, root in (("Contents/section0.xml", sec), ("Contents/header.xml", header)):
        files[name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    opf = "{http://www.idpf.org/2007/opf/}"
    package = ET.fromstring(files["Contents/content.hpf"])
    metadata = package.find(opf + "metadata")
    metadata.find(opf + "title").text = content["title_ko"]
    for item in metadata.findall(opf + "meta"):
        if item.get("name") == "creator":
            item.text = "조하빈; 박수현; 최원준"
        elif item.get("name") == "subject":
            item.text = content["title_en"]
        elif item.get("name") == "keyword":
            item.text = content["keywords"].removeprefix("Key words: ")
    files["Contents/content.hpf"] = ET.tostring(package, encoding="utf-8", xml_declaration=True)
    files["Preview/PrvText.txt"] = "\n".join(
        [
            content["title_ko"],
            content["title_en"],
            "Extended Abstract",
            *content["abstract"],
            content["keywords"],
            content["caption_1"],
            content["caption_2"],
            "Results and Discussions",
            *content["results"],
            "Acknowledgement",
            content["acknowledgement"],
        ]
    ).encode("utf-8")
    with ZipFile(target, "w") as z:
        for name, data in files.items():
            z.writestr(name, data, compress_type=ZIP_STORED if name == "mimetype" else ZIP_DEFLATED)
    print(target)


if __name__ == "__main__":
    main()
