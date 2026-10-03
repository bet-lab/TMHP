# Compressor efficiency coefficient archive

`v2026-09-24/` is the fit behind the coefficients shipped in
`src/tmhp/compressor_efficiency.py` (`COEFFICIENT_VERSION = "v2026-09-24"`).
The shipped constants are these fitted values rounded for publication
(largest relative difference ~1e-4).

| file | content |
| --- | --- |
| `coefficients.json` | fitted coefficients, estimator and decision notes |
| `fit_results.json`, `model_selection.csv`, `selected.json` | candidate structures and the selected one |
| `loco_pooled.csv`, `loco_strata.csv` | leave-one-compressor-out cross-validation |
| `within_machine_contrasts.csv` | per-machine speed contrasts (fixed-effects rule R4) |
| `split.json`, `data_sources.csv`, `manifest.json` | train/test split, data provenance, run manifest |

The pipeline that produced it is `validation/compressor_maps/` (`fit.py`,
`cv.py`, `split.py`, `emit_coefficients.py`; parsers under `parse/`). Its input
points under `validation/data/compressor_maps/` are regenerated from the
untracked evidence documents, which are not redistributable.

Ported from PR #52 with two omissions: the archive's `figures/` (17 MB,
regenerable) and `compressor_maps/final_figures.py`, which depends on
validation analyses (`fixed_boundary_plr`, `parity`) that are not in this
repository yet. Earlier coefficient versions remain on the PR #52 branch.
