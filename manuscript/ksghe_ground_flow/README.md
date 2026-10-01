# KSGHE compact cooling paper — TMHP #68

`manuscript.json` is the editable source; `GSHP_variable_flow.hwpx`, `.pdf` and
`.png` are the validated native document, one-page PDF and preview. This revision
implements [reference/rated/max separation](../../docs/plans/reference-rated-max-gshp.md).
The genuine `template_261001.hwpx` retains authors, all affiliations, conference
heading, margins, body/caption font sizes, contact footer and acknowledgement
RS-2025-00512551. The earlier converted template and revision diffs are historical.

## Conditions and results

R410A cooling 8 kW; room 26 °C; fixed ground wall 15 °C; compressor efficiencies
0.70/0.90/0.80; two 100 m boreholes at 6 m spacing. Ground/load UA is 1440/640 W/K,
with ground UA = 0.18 × rated capacity as an engineering sizing assumption.
Electrical compressor input is gas work/0.8; motor loss remains outside the
refrigerant cycle. System COP and optimization include compressor, pump and fan.

Water reference and constant command remain 24 L/min. Variable bounds are
9.6–36 L/min; 36 L/min is 1.5 times the fixed reference. Indoor approach search
bounds are explicitly 1–25 K in every sensitivity case. Previous defaults remain
1–20 K in the model. Refrigerant reference mass flow is 0.04151109713044605 kg/s,
derived at constant flow/full load. The pressure-ratio floor is 1.5; pump efficiency
is 0.60 and common pipe losses are zero. Equipment curves, UA resistance fractions
and exponents are uncalibrated; these results are a steady example.

All 184 main points and 48 selected sensitivity points are feasible; 24 prescribed
24 L/min points reproduce component physics identically across control modes and
ceilings. All 88 undersized-ground-HX stress points fail with NaN COP. Optimal flow
is 45.3–150.0% of reference; total savings reach 5.50% and COP gains 5.81%.
Full-load COP for maxima 24/28.8/36 L/min is 6.9593/7.1906/7.3640. All three
full-load optima remain at their control maximum; no unconstrained stationary
optimum is established. Low-load differences under 0.5 W reflect numerical search
resolution; details and assumptions are in `validation_notes.md`.

## Reproducibility and validation

The active simulation is `enex-engine/01_active/gshp_ground_flow/`. Its preceding
1440 W/K/.9/.8/max24 study is preserved byte-for-byte in `archive/capacity_ua_max1p0/`.
Paper-local `data/` and `figure/` are exact copies of validated outputs. Fingerprints
capture model commit at execution start, input hash and executed study/sensitivity
script hashes. A subsequent model change only makes the default empty borehole
anchor tuple explicit for mypy; a later documentation change fixes an underline.
No result provenance is rewritten to a later HEAD.

All four active graphs use Dartwork-mpl `scientific` and actual MCP code/data
checks. Fig. 1 is 1×4 (pump W, fan W, compressor kW, system COP). Runtime validation
and visual inspection supplement MCP data checking. `qa.json` checks every body
and caption string, CSV claims, power/COP/efficiency closure, fixed reference,
sensitivity fairness, authors/affiliations, one page, embedded fonts and hashes.
`hwpx_validation.json` passes strict and compatibility checks.

`diff/reference_flow_revision_20261001.{pdf,diff}` records visual and native-text
changes against the preceding capacity-UA/max24 paper. Linux pyhwpxlib/rhwp
renders the PDF with embedded Noto CJK/Liberation Serif font substitutes; HWPX
retains native font names. Hancom Office was not used for validation.

```bash
python3 build_hwpx.py template_261001.hwpx GSHP_variable_flow.hwpx
UV_CACHE_DIR=/tmp/uv-cache uv run --offline --no-project --with 'pyhwpxlib[all]==0.18.3' \
  --with cairosvg python render_pdf.py GSHP_variable_flow.hwpx GSHP_variable_flow
python3 check_paper.py GSHP_variable_flow.hwpx GSHP_variable_flow.pdf
UV_CACHE_DIR=/tmp/uv-cache uv run --offline --no-project --with 'pyhwpxlib[all]==0.18.3' \
  --with cairosvg pyhwpxlib validate GSHP_variable_flow.hwpx --mode both --json
```

Issues: [model #67](https://github.com/bet-lab/TMHP/issues/67),
[study enex-engine #42](https://github.com/bet-lab/enex-engine/issues/42),
[paper #68](https://github.com/bet-lab/TMHP/issues/68). Merge completion is recorded
in those issues and linked PRs. CI interpreter-label mismatch is tracked in #57;
no unverified four-version compatibility claim is made.
