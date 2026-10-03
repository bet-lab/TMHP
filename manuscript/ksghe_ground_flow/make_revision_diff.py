"""Create a visual before/after PDF and native-text diff for the HWPX revision."""

import argparse
import difflib
import subprocess
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import cairocffi as cairo

HERE = Path(__file__).resolve().parent


def text_of(path):
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("Contents/section0.xml"))
    return ["".join(t.itertext()).rstrip() + "\n" for t in root.iter("{http://www.hancom.co.kr/hwpml/2011/paragraph}t")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before_pdf", type=Path)
    parser.add_argument("--before-hwpx", type=Path, default=HERE / "template_261001.hwpx")
    args = parser.parse_args()
    target = HERE / "diff/aux_fan_revision_20261003.pdf"
    target.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ksghe-diff-") as temp:
        before = Path(temp) / "before"
        subprocess.run(
            ["pdftoppm", "-singlefile", "-scale-to", "1600", "-png", str(args.before_pdf), str(before)], check=True
        )
        surface = cairo.PDFSurface(str(target), 842, 595)
        context = cairo.Context(surface)
        context.select_font_face("Liberation Sans")
        context.set_font_size(10)
        for index, (label, path) in enumerate(
            (
                ("Before: BHE-only pump, paper UA 800 / 1600 W/K", before.with_suffix(".png")),
                ("After: auxiliary loss, 15% fan bound, corrected UA", HERE / "GSHP_variable_flow.png"),
            )
        ):
            context.move_to(20 + 421 * index, 16)
            context.show_text(label)
            img = cairo.ImageSurface.create_from_png(str(path))
            scale = min(397 / img.get_width(), 560 / img.get_height())
            context.save()
            context.translate(20 + 421 * index, 26)
            context.scale(scale, scale)
            context.set_source_surface(img)
            context.paint()
            context.restore()
        surface.finish()
    diff = difflib.unified_diff(
        text_of(args.before_hwpx),
        text_of(HERE / "GSHP_variable_flow.hwpx"),
        fromfile="previous paper: BHE-only pump, UA800/1600",
        tofile="auxiliary pressure drop, fan15-100%, UA1600/800, constant24",
        n=0,
    )
    target.with_suffix(".diff").write_text("".join(diff))
    print(target)


if __name__ == "__main__":
    main()
