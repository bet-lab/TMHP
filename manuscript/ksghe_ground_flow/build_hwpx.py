"""Revise the supplied 261001 native HWPX with one full-width figure.

Usage: python build_hwpx.py template_261001.hwpx output.hwpx
"""

import copy
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

HERE = Path(__file__).resolve().parent
NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
}
for key, value in NS.items():
    ET.register_namespace(key, value)


def text_of(p):
    return "".join("".join(t.itertext()) for t in p.findall("hp:run/hp:t", NS))


def replace_text(p, text, char):
    for run in p.findall("hp:run", NS):
        if run.find("hp:t", NS) is not None:
            p.remove(run)
    run = ET.SubElement(p, f"{{{NS['hp']}}}run", charPrIDRef=char)
    node = ET.SubElement(run, f"{{{NS['hp']}}}t")
    lines = text.split("\n")
    node.text = lines[0]
    for line in lines[1:]:
        ET.SubElement(node, f"{{{NS['hp']}}}lineBreak").tail = line


def figure_table(parent, files, caption):
    table = parent.find(".//hp:tbl", NS)
    rows = table.findall("hp:tr", NS)
    assert len(rows) == 2 and len(table.findall(".//hp:pic", NS)) == 2
    width, height, caption_height = 47628, 13041, 3100
    table.set("colCnt", "1")
    table.find("hp:sz", NS).set("width", str(width))
    table.find("hp:sz", NS).set("height", str(height + caption_height))
    table.find("hp:pos", NS).set("treatAsChar", "1")
    for margin in table.findall("hp:outMargin", NS):
        margin.set("bottom", "0")
    for i, row in enumerate(rows):
        cells = row.findall("hp:tc", NS)
        for cell in cells[1:]:
            row.remove(cell)
        cell = cells[0]
        cell.find("hp:cellAddr", NS).set("colAddr", "0")
        cell.find("hp:cellSpan", NS).set("colSpan", "1")
        cell.find("hp:cellSz", NS).set("width", str(width))
        cell.find("hp:cellSz", NS).set("height", str(height if i == 0 else caption_height))
        for margin in cell.findall("hp:cellMargin", NS):
            for side in ("left", "right", "top", "bottom"):
                margin.set(side, "0")
        sub = cell.find("hp:subList", NS)
        sub.set("textWidth", str(width))
        paragraphs = sub.findall("hp:p", NS)
        p = paragraphs[0]
        for extra in paragraphs[1:]:
            sub.remove(extra)
        if i == 0:
            pic = copy.deepcopy(cell.find(".//hp:pic", NS))
            for child in list(p):
                p.remove(child)
            run = ET.SubElement(p, f"{{{NS['hp']}}}run", charPrIDRef="1")
            run.append(pic)
            for name in ("orgSz", "curSz", "sz"):
                pic.find("hp:" + name, NS).set("width", str(width))
                pic.find("hp:" + name, NS).set("height", str(height))
            pic.find("hp:imgDim", NS).set("dimwidth", str(width))
            pic.find("hp:imgDim", NS).set("dimheight", str(height))
            clip = pic.find("hp:imgClip", NS)
            clip.set("right", str(width))
            clip.set("bottom", str(height))
            rotation = pic.find("hp:rotationInfo", NS)
            rotation.set("centerX", str(width // 2))
            rotation.set("centerY", str(height // 2))
            for name, x, y in (("pt0", 0, 0), ("pt1", width, 0), ("pt2", width, height), ("pt3", 0, height)):
                point = pic.find("hp:imgRect/hc:" + name, NS)
                point.set("x", str(x))
                point.set("y", str(y))
            pic.find("hc:img", NS).set("binaryItemIDRef", "image1")
        else:
            replace_text(p, caption.replace("(b) indoor", "\n(b) indoor"), "3")
    files["BinData/image1.png"] = (HERE / "figure/fig_1_part_load.png").read_bytes()
    files.pop("BinData/image2.png", None)


def main():
    source, target = map(Path, sys.argv[1:3])
    content = json.loads((HERE / "manuscript.json").read_text())
    with ZipFile(source) as z:
        files = {name: z.read(name) for name in z.namelist()}
    sec = ET.fromstring(files["Contents/section0.xml"])
    paragraphs = sec.findall("hp:p", NS)
    assert len(paragraphs) == 10 and text_of(paragraphs[0]) == "Extended Abstract"
    for p in sec.findall(".//hp:p", NS):
        text = text_of(p)
        if text.startswith("지열히트펌프 모델 개발") or text.startswith("Development of Ground Source Heat Pump"):
            char = next(r.get("charPrIDRef") for r in p.findall("hp:run", NS) if r.find("hp:t", NS) is not None)
            replace_text(p, content["title_ko"] if text.startswith("지열") else content["title_en"], char)
    replace_text(paragraphs[1], content["abstract"][0], "1")
    replace_text(paragraphs[2], content["abstract"][1], "1")
    third = copy.deepcopy(paragraphs[2])
    third.set("id", str(max(int(p.get("id", "0")) for p in sec.findall(".//hp:p", NS)) + 1))
    replace_text(third, content["abstract"][2], "1")
    sec.insert(list(sec).index(paragraphs[3]), third)
    replace_text(paragraphs[3], content["keywords"], "1")
    figure_table(paragraphs[4], files, content["caption_1"])
    # Figure paragraph must start at the column edge, not the footer's indent.
    paragraphs[4].set("paraPrIDRef", "13")
    for p, result in zip(paragraphs[6:8], content["results"], strict=True):
        p.set("paraPrIDRef", "4")
        replace_text(p, "• " + result, "2")
    replace_text(paragraphs[9], content["acknowledgement"], "16")
    for p in sec.findall(".//hp:p", NS):
        for line in p.findall("hp:linesegarray", NS):
            p.remove(line)
    files["Contents/section0.xml"] = ET.tostring(sec, encoding="utf-8", xml_declaration=True)
    header = ET.fromstring(files["Contents/header.xml"])
    # Preserve title fonts while fitting both lines and all three affiliations.
    for spacing in header.findall('.//hh:paraPr[@id="2"]//hh:lineSpacing', NS):
        spacing.set("value", "130")
    # The supplied half-width caption carried a large first-line indent.
    for para_id in ("13", "14"):
        for indent in header.findall(f'.//hh:paraPr[@id="{para_id}"]//hc:intent', NS):
            indent.set("value", "0")
    files["Contents/header.xml"] = ET.tostring(header, encoding="utf-8", xml_declaration=True)
    opf = "{http://www.idpf.org/2007/opf/}"
    package = ET.fromstring(files["Contents/content.hpf"])
    metadata = package.find(opf + "metadata")
    metadata.find(opf + "title").text = content["title_ko"]
    for item in metadata.findall(opf + "meta"):
        if item.get("name") == "subject":
            item.text = content["title_en"]
        elif item.get("name") == "keyword":
            item.text = content["keywords"].removeprefix("Key words: ")
    manifest = package.find(opf + "manifest")
    for item in list(manifest):
        if item.get("id") == "image2":
            manifest.remove(item)
    files["Contents/content.hpf"] = ET.tostring(package, encoding="utf-8", xml_declaration=True)
    files["Preview/PrvText.txt"] = (
        chr(10)
        .join(
            [
                content["title_ko"],
                content["title_en"],
                "Extended Abstract",
                *content["abstract"],
                content["keywords"],
                content["caption_1"],
                "Results and Discussions",
                *content["results"],
                "Acknowledgement",
                content["acknowledgement"],
            ]
        )
        .encode("utf-8")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w") as z:
        for name, data in files.items():
            z.writestr(name, data, compress_type=ZIP_STORED if name == "mimetype" else ZIP_DEFLATED)
    print(target)


if __name__ == "__main__":
    main()
