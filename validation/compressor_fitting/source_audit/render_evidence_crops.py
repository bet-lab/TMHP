"""Render original PDF excerpts with PDF highlight annotations, without redrawing text.

Requires PyMuPDF. All crop rectangles and source hashes are written to a manifest.
The input PDFs are opened in memory and never saved or modified.
"""

import argparse
import hashlib
import json
from pathlib import Path

import pymupdf


def render_crop(source: Path, recipe: dict, output: Path) -> dict:
    """Annotate matching source text and render the bounded original excerpt."""
    with pymupdf.open(source) as document:
        page = document[recipe["pdf_page"] - 1]
        clip = pymupdf.Rect(recipe["rect_pdf_points"])
        matches = []
        for phrase in recipe.get("highlight_phrases", []):
            for rectangle in page.search_for(phrase):
                if not clip.contains(rectangle):
                    continue
                annotation = page.add_highlight_annot(rectangle)
                annotation.set_colors(stroke=(1, 0.82, 0.15))
                annotation.set_opacity(0.35)
                annotation.update()
                matches.append({"phrase": phrase, "rect_pdf_points": list(rectangle)})
        for rectangle in recipe.get("highlight_rects", []):
            annotation = page.add_highlight_annot(pymupdf.Rect(rectangle))
            annotation.set_colors(stroke=(1, 0.82, 0.15))
            annotation.set_opacity(0.28)
            annotation.update()
            matches.append({"phrase": "equation in original PDF", "rect_pdf_points": rectangle})
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=clip, alpha=False, annots=True)
        target = output / f"{recipe['name']}.png"
        pixmap.save(target)
        return {
            **recipe,
            "source_url": recipe["source_url"],
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "output": target.name,
            "pixels": [pixmap.width, pixmap.height],
            "highlights": matches,
            "processing": "original PDF crop plus translucent PDF highlight annotations; no text replacement",
        }


def main() -> None:
    """Render six evidence excerpts from explicitly supplied full source PDFs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thesis-pdf", required=True, type=Path)
    parser.add_argument("--paper-2024", required=True, type=Path)
    parser.add_argument("--paper-2023", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "crops")
    args = parser.parse_args()
    recipes = [
        (
            args.paper_2024,
            {
                "name": "2024_reference_compressor_133_tests",
                "pdf_page": 3,
                "rect_pdf_points": [34, 96, 292, 198],
                "highlight_phrases": [
                    "variable-speed scroll compressor working with R290",
                    "30 to 110 Hz",
                    "133 different conditions",
                ],
                "source_url": "https://doi.org/10.1016/j.applthermaleng.2024.122666",
            },
        ),
        (
            args.paper_2024,
            {
                "name": "2024_table1_r290_column",
                "pdf_page": 4,
                "rect_pdf_points": [34, 74, 164, 188],
                "highlight_phrases": ["Scroll-R290", "46", "R290", "30 / 110", "133"],
                "source_url": "https://doi.org/10.1016/j.applthermaleng.2024.122666",
            },
        ),
        (
            args.thesis_pdf,
            {
                "name": "thesis_corrected_eq3_22",
                "pdf_page": 116,
                "printed_page": 88,
                "rect_pdf_points": [47, 370, 427, 538],
                "highlight_phrases": [],
                "highlight_rects": [[91, 454, 317, 488]],
                "source_url": "https://riunet.upv.es/bitstreams/6612b905-7a7a-436d-b73c-ac1bc903c27a/download",
            },
        ),
        (
            args.paper_2023,
            {
                "name": "2023_measurement_method",
                "pdf_page": 3,
                "rect_pdf_points": [34, 301, 292, 507],
                "highlight_phrases": [
                    "Standard EN13771",
                    "Coriolis",
                    "calorimetric balance",
                    "double-wattmeter",
                    "YOKOGAWA",
                    "WT1030",
                ],
                "source_url": "https://doi.org/10.1016/j.applthermaleng.2023.120725",
            },
        ),
        (
            args.paper_2023,
            {
                "name": "2023_sensor_uncertainty",
                "pdf_page": 4,
                "rect_pdf_points": [34, 50, 291, 184],
                "highlight_phrases": ["Fluke 1735", "Yokogawa WT1030", "Coriolis CMF025M"],
                "source_url": "https://doi.org/10.1016/j.applthermaleng.2023.120725",
            },
        ),
        (
            args.paper_2023,
            {
                "name": "2023_synchronous_speed",
                "pdf_page": 3,
                "rect_pdf_points": [301, 411, 564, 507],
                "highlight_phrases": ["synchronous", "slip ratio has not been considered", "mechanical speed"],
                "source_url": "https://doi.org/10.1016/j.applthermaleng.2023.120725",
            },
        ),
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = [render_crop(source, recipe, args.output_dir) for source, recipe in recipes]
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps([{key: row[key] for key in ("output", "pixels", "pdf_page")} for row in manifest], indent=2))


if __name__ == "__main__":
    main()
