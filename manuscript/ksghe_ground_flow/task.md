# Compressor efficiency callable baseline revision

- [x] Share unchanged shipped baseline across five models; preserve scalar and 1/2-argument callable API (model PR73).
- [x] Solve GSHP speed/mass flow/discharge work with actual efficiencies.
- [x] Reproduce and fix pressure-floor subcooling inconsistency and narrow HX boundary search (issue74/PR75); existing goldens and physical tolerances remain unchanged.
- [x] Set user-rated ground/indoor UA to 800/1600 W/K and remove capacity-multiplier wording from the paper.
- [x] Preserve 39 prior scalar files and the completed callable UA1440/640 checkpoint with hashes.
- [x] Rerun 184 main, 182 additional sensitivity, 88 stress and 16 matched scalar comparison points with the final model.
- [x] Verify 48 selected sensitivity/24 prescribed fairness points and unchanged 0.5 W independent-grid/range-widening checks.
- [x] Record executed source/module/config/script fingerprints and actual speed/efficiency/PR diagnostics.
- [x] Rebuild seven scientific graphs; pass 16 actual Dartwork-mpl MCP calls, runtime and visual checks.
- [x] Build native one-page HWPX/PDF; inspect complete paper and visual/native-text diff.
- [x] Remove 물리 기반 / Physics-Based from both titles; use Variable-flow in the paper figure/caption; retain requested bracket units, short y labels, ticks and letters.
- [x] Deliver user artifacts byte-identically with recorded hashes.

Merge completion: TMHP#71/PR73 and #74/PR75 (model), enex-engine#44 (study), TMHP#72 (paper); issue/PR conversations record final checks and merges.
