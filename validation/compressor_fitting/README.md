# Compressor catalogue fitting — Fujitsu heating case

`AirSourceHeatPump` now uses the Ossorio R290 research baseline when efficiency
inputs are omitted. Explicit scalar/callable overrides still win; the other four
model classes retain their existing defaults. No classes are consolidated.
Transferring an R290 scroll map to an R410A target is a modelling assumption,
not identification of that target compressor.

## Reproduce

From the repository root, with project dependencies installed:

```bash
PYTHONPATH=src:validation/compressor_fitting python validation/compressor_fitting/build_baseline.py
PYTHONPATH=src:validation/compressor_fitting python validation/compressor_fitting/run_case.py
PYTHONPATH=src:validation/compressor_fitting python validation/compressor_fitting/verify_case.py
PYTHONPATH=src:validation/compressor_fitting python validation/compressor_fitting/plot_results.py
```

`api_example.py` gives the exact four calibration tuples and all constructor
overrides. Neither a baseline helper nor `lambda_bounds` is required. The
optional bounds are numerical search intervals chosen before fitting, not fitted
results: the library defaults are λov/λv 0.5–1.5 and λloss 0–2.
Applied values are stored in `fit_report.json` under `applied_scales`.

## Data and fixed assumptions

- Fujitsu **ASYG30LMTA/AOYG30LMTA**, R410A. The heating table is a
  **manufacturer-calculated maximum-capacity table**, PDF p21 / printed p17;
  it is not a set of measured nominal-duty points. Both outdoor dry and wet
  bulb are published; no wet-bulb conversion is used. COP = TC/PI.
- `hp_capacity=8000 W` is the published **nominal cooling** capacity, matching
  the constructor's default rating mode. Published nominal heating 8800 W is
  a separate reference for display PLR, never substituted for the requested
  maximum-capacity table output.
- Manufacturer reference airflows: indoor 1380 and outdoor 3600 m³/h.
  Unpublished compressor displacement and UA retain TMHP's capacity defaults:
  Vdisp 37.333333 cm³/rev and indoor/outdoor UA 640/800 W/K. These are model
  assumptions, not Fujitsu specifications or parameters fitted to COP.
- Default fan pressure rise 60 Pa and efficiency 0.6 produce reference inputs
  38.333333/100 W at those airflows. The outdoor 100 W is derived from TMHP's
  defaults and **is not** the catalogue's 100 W motor-output rating. The
  published indoor fan input 60 W remains provenance evidence; this example
  retains the generic fan model. Actual powers follow the existing fan curves.
- Speed 30–110 rev/s and PR 1.5–7.5 restrict the research domain, not a
  manufacturer-certified envelope. Approach bounds 1–35 K are frozen numerical
  assumptions. Remaining constructor settings are unchanged.
- Final study: outdoor DB 7/10/15/20 °C × indoor DB 16/18/20/22/24 °C = 20 rows.
  **Four indoor-20 °C rows calibrate; sixteen other rows are held out.**
  Baseline feasibility was checked before any COP fitting.
- The original 25 warm candidates produced 24 valid solves and one
  `optimizer_failed` result at indoor22/outdoor24 °C. The entire five-row
  outdoor24 group was assigned `diagnostic_solver_edge` before fitting.
  A separate post-fit diagnostic finds all five outdoor24 rows valid, but they
  are not reintroduced into the predeclared study. `diagnostic_solver_edge.json`
  retains both attempts. No residual was used to choose this partition. The other 25 cold source rows
  remain `diagnostic_cold`; all 50 rows are preserved in the catalogue CSV.
- Outdoor7 °C does not establish frost-free operation. This model contains no
  frost/defrost physics. Generalization to cold conditions is not claimed.

## Fit roles and results

Only λov minimizes all-row COP RMSE. λv and λloss adjust only when their
capacity/speed or discharge constraints fail. They are not jointly identified
from COP. Every final point is checked again before atomic application.

| Scale | Final applied value |
|---|---:|
| λov | 1.3748851081463451 |
| λv | 1.0 |
| λloss | 0.5 |

| Metric | Calibration, four rows: before → after | Holdout, sixteen rows: before → after |
|---|---:|---:|
| COP RMSE | 0.866998 → 0.358593 | 0.899469 → 0.383344 |
| COP RMSE reduction | 58.64% | 57.38% |
| Power RMSE [W] | 600.792 → 238.057 | 618.779 → 257.586 |
| Valid/requested | 4/4 → 4/4 | 16/16 → 16/16 |

The COP-only stage reaches RMSE 0.342730 but violates the adopted discharge
constraint at three of four calibration points. λv remains 1 because no speed
repair is needed. Reducing λloss to 0.5 restores discharge feasibility and gives
the **final** RMSE 0.358593. The loss scale is the closest feasible grid candidate,
not an additional COP-optimized coefficient. Final-stage values must be used for
reported performance; the intermediate stage is not a physical final result.

The recorded fit and holdout evaluation took approximately 151 seconds.
Machine-readable numerical results are in `metrics.json`, `fit_report.json`,
and `point_predictions.csv`. Failed points prevent an all-row RMSE; they are
never silently removed. COP and electrical power derived from the same capacity
observation are not independent validation targets.

The earlier Panasonic result is preserved under `legacy_panasonic/`, including
its input table, frozen narrower bounds, reports and run manifest. Those values
must not be labelled as Fujitsu or as runs with the current default bounds.

## Baseline provenance

[Ossorio et al. (2024)](https://doi.org/10.1016/j.applthermaleng.2024.122666)
models mass flow and power as functions of absolute pressures and shaft speed.
The implementation generates 567 states and approximates them with six terms
`1, PR, N, PR², N², PR*N`. Generated states are not experimental observations.
Approximation RMSE is 0.924/4.053/0.291 percentage points for ηv/ηov/floss.
The full domain is PR 1.475–7.492, N 30–110 rev/s; rectangular domain membership
does not by itself establish physical validity.

The author's thesis Eq.3.22 resolves the journal Eq.22 complement ambiguity:
`eta_em = 1 - f_loss`, with Table5 coefficients defining `f_loss`.
The thesis prints 134 measured R290 rows; the journal reports 133. All 134 are
retained without guessing the omitted row. See [the source audit](source_audit/audit.md)
and `audit_results.json` for methods and residuals. This audit uses source-model
development data and is not independent validation.

Source PDFs and page crops remain local under the repository's evidence policy;
source metadata, audit scripts and generated figures retain their provenance.

## Verification

The default-map, explicit-override, compressor-calibration, capacity,
reference-state, model-smoke and solver regression sets report **228 passed,
1 skipped**. Two smoke-test warnings concern existing deprecated UA names. The skip is
pre-existing Keymark raw-data unavailability. Ruff and targeted production mypy
pass. `verify_case.py` independently re-evaluates frozen applied scales through
the public catalogue adapter and separately reports the five excluded
solver-edge rows; it does not claim to rerun coefficient optimization. The ASHP adapter is the only class-specific fitting API added.

Implementation tracking: [issue #89](https://github.com/bet-lab/TMHP/issues/89).

## Actual optimizer search trace

Run the separate public API exporter without overwriting the frozen results:

```bash
PYTHONPATH=src:validation/compressor_fitting python validation/compressor_fitting/run_search_trace.py
```

`search_trace/trace.json` contains actual candidate-call order and all four
calibration rows at every call. `calls.csv` has one row per call; `points.csv` has
one row per call and calibration point. `fit_report.json`, held-out predictions,
metrics and provenance are written only inside `search_trace/`. The exporter
checks the original scales, stage/final predictions and sixteen holdouts, and
hashes the frozen result files before and after to verify they remain unchanged.

A sequence number counts actual calls, including cache reads; `candidate_id`
identifies unique scale tuples. `cache_hit=true` is not another physics solve.
`objective_returned` does not claim SciPy internally accepted the trial.
`objective_value` contains the actual COP objective return or its `1e12` invalid
penalty; outside objective calls it is null. `objective_rmse` can be finite while
the deferred discharge constraint fails, whereas `constraint_rmse` requires all
final constraints. Repairs and final acceptance are separate recorded decisions.

The exporter does not recompute holdouts or PLR curves for every candidate; holdouts are checked once before and once after fitting and never influence parameter selection.

## Candidate validity

A valid candidate requires a converged/on state, positive power and net delivery, net capacity relative error <= 1e-4, speed within the configured range, positive eta_v/eta_ov/eta_em, 0 <= loss < 1, and the adopted eta_is <= 1 / h2 >= h2s discharge constraint. The evaluator checks the original map electrical/loss domain. Eta_v <= 1 is not separately imposed. Pressure-ratio and approach limits belong to the inner solver. COP RMSE measures fitting error and does not replace validity checks.

Further map-shape calibration is tracked in issue #91. It is not implemented by this documentation cleanup.
