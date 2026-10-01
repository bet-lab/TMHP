# Callable compressor baseline revision review

- All five TMHP families use the shared unchanged shipped v2026-09-24 coefficients; no tuned low-speed penalty. The specified Notion report and original d9fa2f3 module are documented in the model audit and compressor_efficiency_validation.md.
- GSHP solves candidate speed with eta_v in mass flow, eta_is in discharge work and eta_em in electrical input exactly once. Reference speed 60 rev/s is separate from 15–150 rev/s limits. Actual values and PR-floor flags are saved in the CSV.
- User changed the final study to explicit UA800/1600 W/K; remaining conditions are reference/constant24 L/min, min9.6/max36 L/min, 8kW R410A cooling, 26/15°C and two100m boreholes. The new refrigerant reference is 0.0428812648 kg/s.
- The actual preceding case already applied .70/.90/.80 efficiencies. Its39 active files are archived byte-for-byte at enex b11115bb; comparisons do not mislabel eta_v/em as unapplied.
- All16 paper and48 selected sensitivity points are feasible; main grid feasibility is178/184, with rejected candidates excluded. The24 identical-actual-flow points reproduce component physics. All88 undersized-HX points are rejected with NaN COP. Independent-grid checks pass without relaxing tolerances.
- Optimal flow is54.0029–147.8416% of reference; maximum total-power saving3.60563%, COP gain3.74050%. Full-load constant/variable COP is8.26393/8.57305. Boundary interpretation matches fresh sensitivity results.
- Three-efficiency, speed and PR development plots expose actual baseline behavior. Low-load eta_is is coupled to eta_em and is not assumed to decrease monotonically. Selected PR-floor count is16; hollow markers identify it.
- All7 active graphs use scientific style, actual Dartwork-mpl MCP code/data review (16 calls), runtime checks and visual inspection. An efficiency-panel label/data overlap was corrected by increasing spacing. Fig.1 uses Constant-flow / Variable-flow, bracket units, Fan/Comp. y labels, PLR ticks .4/.6/.8/1 and unparenthesized a–d.
- Reviewed the complete revised body: motivation → model and total-power objective → fixed conditions → figure → constrained results. Symbols/units and caption match the data; no citations, bibliography or unresolved cross-references are present in this compact abstract.
- Removed 물리 기반 / Physics-Based from both titles as requested. The genuine261001 template retains all authors/affiliations, acknowledgementRS-2025-00512551 and correspondence footer.
- Native HWPX strict/compat validation and one-page PDF QA pass, including every body/caption string, CSV claims, closure, embedded fonts and figure bytes. Final paper image and visual/native-text diff inspected: no clipping, overlap or missing affiliation/footer.
- Linux pyhwpxlib/rhwp with documented embedded font substitutes was used; Hancom Office was not used.

- The completed intermediate callable UA1440/640 checkpoint is archived. Scalar .70/.90/.80 comparisons were rerun at the same UA800/1600 to isolate efficiency effects; historical different-UA comparisons are labelled separately.

- Pressure-floor condenser projection now recalculates permitted subcooling from the projected temperature, and GSHP explicitly brackets the HX duty boundary (TMHP issue74/PR75). Main/sensitivity/stress/matched scalar runs use executed commit5d4e60b; model merge411a73b preserves identical module hashes. Ground-HX duty tolerance remains max(0.01 W, 1e-5×required duty); range-widening tolerance remains0.5 W. The flat PR=1.5 plot uses a physical axis range, without changing data.
