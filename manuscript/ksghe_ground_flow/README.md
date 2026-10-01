# KSGHE compact cooling paper — TMHP #64

`manuscript.json` is the editable text source; `GSHP_variable_flow.hwpx`, `.pdf`
and `.png` are the final native document, verified one-page PDF and preview.
This revision follows `../../docs/plans/gshp-capacity-based-ground-ua.md` and retains
the compact format from the earlier cooling revision. It uses the genuine ZIP/XML
`template_261001.hwpx`. Authors, all affiliations,
conference heading, contact footer, page margins and text font sizes are retained.
English title spacing and figure-table layout are adjusted to fit the full title,
affiliations and a single four-panel figure. Acknowledgement is RS-2025-00512551 only.
The previous `template_converted.hwpx` remains a historical v7 conversion asset;
it is not the input to this revision.

## Build and verify

From this directory, with cached dependencies or normal network access:

```bash
python3 build_hwpx.py template_261001.hwpx GSHP_variable_flow.hwpx
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project --with 'pyhwpxlib[all]==0.18.3' \
  --with cairosvg python render_pdf.py GSHP_variable_flow.hwpx GSHP_variable_flow
python3 check_paper.py GSHP_variable_flow.hwpx GSHP_variable_flow.pdf
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project --with 'pyhwpxlib[all]==0.18.3' \
  --with cairosvg pyhwpxlib validate GSHP_variable_flow.hwpx --mode both --json
```

`render_pdf.py` uses the listed system Noto CJK and Liberation Serif fonts,
embeds the substituted PDF fonts and refreshes the HWPX cover preview. HWPX
retains the native font names. Rendering was checked with Linux pyhwpxlib/rhwp;
Hancom Office was not used. The supplied PDF is the checked one-page rendering.

## Numerical evidence

The active simulation is `enex-engine/01_active/gshp_ground_flow/`, with all old
heating data frozen in its `archive/heating_v7/`. Paper-local `data/` and `figure/`
are exact copies of the new cooling outputs, enabling checks independently of
that separate repository. `validation_notes.md` records assumptions and residuals.

Conditions: R410A cooling 8 kW; room 26 °C; fixed ground wall 15 °C; compressor
isentropic/volumetric/electromechanical efficiencies 0.70/0.90/0.80; two 100 m boreholes at 6 m spacing; rated total water
flow 24 L/min; flow bounds 0.4–1.0; pump efficiency 0.60; PLR 0.3–1.0. Actual
default rated ground/load UA is 1440/640 W/K. Refrigerant reference flow is
0.04151110907913401 kg/s, calculated from the constant-flow full-load baseline,
not a prescribed surrogate. Common pipe losses are zero; pressure-ratio floor
is 1.5. This is an uncalibrated steady example, not a measured or catalogue COP.

Optimization and system COP both include compressor, ground-loop pump and
indoor-fan power. The 16 selected points are feasible; the independent full scan
has 184 feasible points out of 184. Flow is 45.3–100.0% of rated; pump savings
reach 88.3%, with up to 14.1% higher fan power. Total savings are 0–4.15% and
system COP gains 0–4.33%. PLR 0.8–1.0 selects the upper bound, matching constant
flow, rather than representing an interior stationary optimum.

The single Fig. 1 is 1×4: pump W, fan W, compressor kW, system COP. All active
figures use Dartwork-mpl `scientific`. `figure/mcp_review.json` contains actual
MCP lint and plot-data tool calls; `visual_validation.json` contains runtime
layout checks. MCP data/code checking is separate from visual PDF inspection.

`qa.json` verifies every body and caption string in the PDF, numerical claims,
total power/COP, required authors/affiliations, one figure/four panels, one page,
embedded fonts and artifact hashes. `hwpx_validation.json` records both strict
and compatibility checks. `diff/capacity_ua_revision_20261001.{pdf,diff}` provides a
visual before/after comparison and native-text diff against the preceding 800 W/K compact revision.
Recreate it with `make_revision_diff.py <before_revision.pdf> --before-hwpx <before_revision.hwpx>`.

Issues: [model #63](https://github.com/bet-lab/TMHP/issues/63),
[study enex-engine #40](https://github.com/bet-lab/enex-engine/issues/40),
[paper #64](https://github.com/bet-lab/TMHP/issues/64).
CI interpreter-label mismatch remains separately tracked in #57; no unverified
four-version compatibility claim is made.

Ground UA scales as 0.18 × rated capacity; 0.18 K⁻¹ is an engineering sizing
assumption requiring calibration. Indoor UA is independent. Compressor electrical
input is gas compression work / 0.8, with motor losses outside the refrigerant
cycle. Volumetric efficiency 0.9 determines speed. Gas work closes refrigerant
heat balances; electrical input enters total power and system COP.

Previous 800 W/K and unity-efficiency cooling assets are frozen in the active
study's `archive/cooling_800_unity_efficiencies/`. New differences combine UA
and efficiency changes. Execution started at model commit 1d92b8c; a subsequent
documentation-only heading correction changed repository HEAD during the run.
`data/provenance_reconciliation.json` records identical numerical source at
start and completion. The original execution hashes are retained.
