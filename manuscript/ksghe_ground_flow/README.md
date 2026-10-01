# KSGHE compact cooling paper — TMHP #72

`manuscript.json` is the editable source; `GSHP_variable_flow.hwpx`, `.pdf` and `.png` are the validated native document, one-page PDF and preview. Both titles omit 물리 기반 / Physics-Based. The genuine `template_261001.hwpx` retains authors, all affiliations, conference heading, margins, body/caption font sizes, contact footer and acknowledgement RS-2025-00512551.

## Conditions and results

R410A cooling 8 kW; room26°C; fixed ground wall15°C; two100m boreholes at6m spacing. User-rated ground/indoor UA is **800/1600 W/K**, explicitly set through `UA_ground_rated` / `UA_iu_rated`. These are rounded study inputs, not detailed equipment fits. All three compressor efficiencies use unchanged shared v2026-09-24 baseline functions of pressure ratio and actual speed; rated speed60 rev/s is separate from15–150 rev/s bounds. Electrical input is gas work/eta_em(PR,rps), with motor loss outside the refrigerant cycle. The optimization includes compressor, pump and fan.

Water reference and constant command remain24 L/min. Variable bounds are9.6–36 L/min; indoor approach bounds1–25K, PR floor1.5 and pump efficiency.60 are unchanged. Refrigerant reference flow is0.0428812648kg/s, solved at constant flow/full load. Equipment curves, UA resistance fractions and exponents are uncalibrated: this is a steady example.

All16 selected main and48 selected sensitivity points are feasible. The main grid has178/184 feasible points; other candidates are excluded with recorded failure reasons;24 prescribed24L/min points reproduce component physics identically across modes and ceilings. All88 undersized-ground-HX points fail with NaN COP. Optimal flow is54.0–147.8% of reference; total savings reach3.61% and COP gains3.74%. Full-load constant/variable COP is8.2639/8.5730. Boundary optima and numerical/calibration assumptions are detailed in `validation_notes.md`.

## Reproducibility and validation

Active simulation: `enex-engine/01_active/gshp_ground_flow/`. The actual preceding fixed-efficiency .70/.90/.80 study at UA1440/640 and max36 is archived byte-for-byte in `archive/constant_efficiency_max1p5/` (39 files from enex b11115bb). The completed callable UA1440/640 checkpoint before the user's UA change is separately archived with hashes. Neither supplies final plot data. Matched scalar .70/.90/.80 runs at UA800/1600 isolate efficiency effects; historical different-UA results are explicitly identified separately.

Paper-local `data/` and `figure/` copy validated study outputs exactly. Fingerprints preserve executed model 5d4e60b279a17cf275d9777d8e817b981c577a2d, coefficient/module/config/script hashes. ModelPR73 introduced the callable baseline; modelPR75 corrects pressure-floor subcooling and explicitly resolves the ground-HX duty boundary, without changing efficiency coefficients or verification tolerances; execution provenance is not rewritten to a later HEAD. `compressor_efficiency_validation.md` records the specified Notion source, unchanged coefficients, interface parity, actual diagnostics and old/new comparisons. No tuned penalty or study monkey-patch is introduced.

All7 active graphs use Dartwork-mpl scientific style and actual MCP code/data review (16 calls), runtime checks and visual inspection. Three development plots show speed, three efficiencies and PR; hollow markers identify PR-floor points. Fig.1 is1×4 and uses Constant-flow / Variable-flow, bracket units, Fan/Comp. labels, PLR ticks .4/.6/.8/1 and letters a–d without parentheses. Optimization is explained in the methods.

`qa.json` validates every body/caption string, CSV claims, power/COP/efficiency/mass-flow closure, fixed reference, fairness, authors/affiliations, one page, embedded fonts and hashes. `hwpx_validation.json` passes strict and compatibility checks. `diff/compressor_efficiency_revision_20261002.{pdf,diff}` records visual/native-text changes against the preceding scalar/max36 paper. Linux pyhwpxlib/rhwp uses documented embedded font substitutes; Hancom Office was not used.

```bash
python3 build_hwpx.py template_261001.hwpx GSHP_variable_flow.hwpx
UV_CACHE_DIR=/tmp/uv-cache uv run --offline --no-project --with 'pyhwpxlib[all]==0.18.3' --with cairosvg python render_pdf.py GSHP_variable_flow.hwpx GSHP_variable_flow
python3 check_paper.py GSHP_variable_flow.hwpx GSHP_variable_flow.pdf
```

Issues: [pressure-floor correction#74](https://github.com/bet-lab/TMHP/issues/74), [model#71](https://github.com/bet-lab/TMHP/issues/71), [study#44](https://github.com/bet-lab/enex-engine/issues/44), [paper#72](https://github.com/bet-lab/TMHP/issues/72). Model PR73 and PR75 are merged; [study PR45](https://github.com/bet-lab/enex-engine/pull/45) is merged. Paper merge completion is recorded in issue72 and its PR. Existing CI interpreter-label mismatch is tracked in#57; no unverified four-version claim is made.
