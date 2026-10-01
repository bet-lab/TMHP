"""Render the one-page native HWPX with explicit, available font substitutes.

Requires pyhwpxlib[all]==0.18.3 and cairosvg==2.9.1 plus system Noto CJK and
Liberation fonts. Native HWPX font names remain unchanged. This is a Linux
renderer check, not a Hancom Office automation claim.
"""

import argparse
import json
import re
from pathlib import Path

import cairocffi as cairo
import cairosvg
from cairosvg.parser import Tree
from cairosvg.surface import PDFSurface
from pyhwpxlib.rhwp_bridge import RhwpEngine

HERE = Path(__file__).resolve().parent
SERIF = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"
SANS = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
LATIN = "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"
FONT_MAP = {name: SERIF for name in ("바탕", "바탕체", "휴먼명조", "한양신명조")}
FONT_MAP.update({name: SANS for name in ("맑은 고딕", "맑은고딕", "HY중고딕", "(한)신중고딕")})
FONT_MAP["Times New Roman"] = LATIN


def replace_font(match):
    name = match.group(1).split(",")[0]
    if name == "Times New Roman":
        family = "Liberation Serif"
    else:
        family = "Noto Serif CJK KR" if FONT_MAP.get(name) == SERIF else "Noto Sans CJK KR"
    return f'font-family="{family}"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hwpx", type=Path)
    parser.add_argument("output", type=Path, help="Output basename, without extension")
    args = parser.parse_args()
    assert all(Path(p).exists() for p in FONT_MAP.values()), "Install the documented system fonts"
    content = json.loads((HERE / "manuscript.json").read_text())
    engine = RhwpEngine(font_map=FONT_MAP)
    doc = engine.load(args.hwpx)
    assert doc.page_count == 1, f"Expected one page, got {doc.page_count}"
    svg = re.sub(r'font-family="([^"]+)"', replace_font, doc.render_page_svg(0))
    pdf = args.output.with_suffix(".pdf")
    with pdf.open("wb") as stream:
        surface = PDFSurface(Tree(bytestring=svg.encode()), stream, dpi=96)
        surface.cairo.set_metadata(cairo.PDF_METADATA_TITLE, content["title_ko"])
        surface.cairo.set_metadata(cairo.PDF_METADATA_AUTHOR, "조하빈; 박수현; 최원준")
        surface.cairo.set_metadata(cairo.PDF_METADATA_SUBJECT, content["title_en"])
        surface.cairo.set_metadata(cairo.PDF_METADATA_KEYWORDS, content["keywords"].removeprefix("Key words: "))
        surface.finish()
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(args.output.with_suffix(".png")), scale=1.5)
    print(json.dumps({"pages": doc.page_count, "pdf": str(pdf), "font_substitutions": FONT_MAP}, ensure_ascii=False))


if __name__ == "__main__":
    main()
