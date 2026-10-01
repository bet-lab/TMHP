# KSGHE one-page paper (issue #50)

Final native document: `GSHP_variable_flow.hwpx`; companion PDF and preview PNG
use the same basename. `manuscript.json` is the editable text source. The paper
replaces the previous boiler/exergy study with the v7 variable-flow GSHP study;
authors, affiliations, contact information, conference heading, page dimensions,
margins and text font sizes are retained. The grant acknowledgement contains
only RS-2025-00512551, as requested by the plan.

## Rebuild

From the repository root:

```bash
python3 manuscript/ksghe_ground_flow/build_hwpx.py \
  manuscript/ksghe_ground_flow/template_converted.hwpx \
  manuscript/ksghe_ground_flow/GSHP_variable_flow.hwpx
uv run --no-project --with 'pyhwpxlib[all]==0.18.3' --with cairosvg==2.9.1 \
  python manuscript/ksghe_ground_flow/render_pdf.py \
  manuscript/ksghe_ground_flow/GSHP_variable_flow.hwpx \
  manuscript/ksghe_ground_flow/GSHP_variable_flow
python3 manuscript/ksghe_ground_flow/check_paper.py \
  manuscript/ksghe_ground_flow/GSHP_variable_flow.hwpx \
  manuscript/ksghe_ground_flow/GSHP_variable_flow.pdf
```

The renderer requires the system Noto Serif/Sans CJK and Liberation Serif font
files listed in `render_pdf.py`. Native HWPX retains the original Korean font
names and Times New Roman. The PDF substitutes available fonts and embeds them.
**Rendering was checked with Linux pyhwpxlib/rhwp, not native Hancom Office.**
Pagination in an editor with different fonts can differ; the supplied PDF is
the verified one-page rendering.

## Original format and conversion

The supplied `251021_KSGHE_HB.hwpx` was an HWP5 OLE binary despite its extension.
It was preserved as `251021_KSGHE_HB.original.hwp` beside the user's original
path, and converted with `pyhwpxlib.hwp2hwpx.convert` to the tracked
`template_converted.hwpx`. The edited file at the original path is now genuine
ZIP/XML HWPX, with a companion `251021_KSGHE_HB.pdf`.

The builder fixes conversion artefacts: both pictures originally referenced
the same binary item, result paragraphs acquired forced page breaks, and the
header was nested in a table cell. It also updates stale line positions, sizes
the title cell to show every affiliation, equalizes figure columns, inserts
explicit caption line breaks and removes an unused spacer. It does not reduce
body font size to obtain one page. The original metadata/font table is retained
except the documented layout fixes and updated title/author metadata.

## Evidence and checks

All result claims are checked against
`validation/gshp_ground_flow/results/operating_points.csv`. The final ranges are
20.0–69.7% of rated flow, 62.8–98.7% lower pump power, and 3.4–47.7% higher system
COP relative to constant flow. The paper explicitly describes an uncalibrated
example with zero common pipe loss; these are not measured savings or
manufacturer-rated COPs. It uses the two `fig_paper_*` outputs from the tracked
CSV-based plotting script. The first figure normalizes to the constant-flow
case at the same PLR, not to a different design point.

`qa.json` records numerical cross-checks, two-figure/one-page checks, required
authors/affiliations/acknowledgement, embedded PDF fonts, and artifact hashes.
HWPX compatibility and strict structural checks passed with `pyhwpxlib validate`.
Visual inspection checked complete author/affiliation lines, both figures,
captions, results, acknowledgement and footer without clipping or overlap.
No unresolved placeholders or old grant number remain.

The background's abbreviated reference is EnergyPlus, *Engineering Reference*,
v24.2 (2024), §16.6, available from the
[official PDF](https://energyplus.net/assets/nrel_custom/pdfs/pdfs_v24.2.0/EngineeringReference.pdf).
It supports the distinction between equation fitting and component parameter
models; it does not validate the present TMHP results.

The broader model assumptions and reproducibility record are in
`validation/gshp_ground_flow/README.md`. CI interpreter-label mismatch is tracked
separately in issue #57; no unverified four-version compatibility claim is made.
