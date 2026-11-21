# Compressor efficiency coefficient archive

One folder per release of the library's default compressor efficiency models.

`ossorio-r290-te-2026-11-21/` is the fit behind the maps the library ships
(`src/tmhp/compressor_efficiency.py`,
`COEFFICIENT_VERSION = "ossorio-r290-te-2026-11-21"`): three polynomial maps in
the pressure ratio, the speed and the evaporating temperature, fitted to 134
measured points of one R290 scroll (#217, #218). Its README describes the data,
the fit and the comparison that chose the axes;
`validation/compressor_maps/ossorio_te/emit.py` writes the constants of the
library module from it.

`semi-empirical-2026-11-15/` is the release before (compressor-type-specific
semi-empirical forms with a drive model), discarded by #217. Its folder and the
pipeline under `validation/compressor_maps/semi_empirical/` are kept as a
record; that pipeline's `emit.py` no longer has a block to write.

The earlier releases are records. Their maps are no longer in the library; the
code that evaluates them is kept, number for number, in `recorded_maps/` for
the validation studies that compare against them and for the tests whose
scenarios were recorded on machines built with them (#164):

- `BITZER-three-C3-absolute-N-2026-10-06/` -- three quadratics in the pressure
  ratio and the shaft speed, fitted to BITZER scroll data
  (`recorded_maps/bitzer_quadratic_2026_10_06.py`). Its `reproduce.py` refits
  them from the tracked raw points -- the pilot's 2,467 scroll points and the
  1,432 low-lift points of `fit_input_lowlift_raw.csv` -- and writes
  `reproduction.json` (largest difference in predicted efficiency against the
  released coefficients: 3e-13).
- `v2026-09-24/` -- the correlations before that
  (`recorded_maps/v2026_09_24.py`). The constants there are the fitted values
  rounded for publication (largest relative difference ~1e-4). The table and
  the paragraphs below describe that archive.
- `v2026-09-15/`, `v2026-09-15b/`, `v1-legacy/` -- older sets. The studies
  that compare against them load `v2026-09-15b/frozen_forms.py` and
  `v1-legacy/legacy_forms.py` from those folders.

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
