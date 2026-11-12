# Compressor catalogue fitting — Panasonic heating case

The existing model classes and BITZER defaults are preserved. This case explicitly
selects an Ossorio R290 scroll research surrogate and transfers it to an R32 rotary
heat pump. That transfer is an assumption, not compressor identification.

## Reproduce

From the repository root, with project dependencies installed:

```bash
PYTHONPATH=src:validation/compressor_fitting python validation/compressor_fitting/build_baseline.py
PYTHONPATH=src:validation/compressor_fitting python validation/compressor_fitting/run_case.py
PYTHONPATH=src:validation/compressor_fitting python validation/compressor_fitting/plot_results.py
```

`api_example.py` expands the six catalogue rows as literal temperature/output/COP
tuples for users. `case.py` contains every fixed equipment input and multiplier
bound. The complete run also predicts three held-out rows, writes every point and
stage, and aborts result generation when a candidate was not applied. Typical
runtime for this case is about 14 minutes on the recorded machine.

## Data and assumptions

- Panasonic **CS-Z35TKEW / CU-Z35TKE**, service manual PAPAMY1701023CE,
  PDF page 163, section 20.2.3. The table contains TC and IP in watts;
  COP is derived as TC/IP. The compressor is R32 rotary 9RS102XGA21, 10.30 cm³/rev.
- Source: [Panasonic service manual](https://www.klimakoncept.hr/upload/2-pdf-klimakoncept/1-klima-uredaji/5-panasonic/panasonic-z20-25-35-42-50-71-servisne-upute-klimakoncept.pdf).
- Calibration: indoor DB 16/20/24 °C × outdoor WB 2/12 °C (six rows).
  Holdout: the same indoor conditions × outdoor WB 7 °C (three rows).
  **Outdoor DB = printed WB + 1 K is an assumption**, not a measurement.
- The remaining 42 cooling rows have a latent-load boundary; six cold heating
  rows may include frost effects. All 57 rows are retained in the input CSV, and
  the excluded groups are not included in the reported metrics.
- UA 306.3298/766.8609 W/K comes from assumed effectiveness 0.70/0.65.
  Rated fan powers 44.9/50 W are reference inputs; actual powers follow existing
  TMHP curves. Airflows span 20–100% of their fixed reference values.
- Approach bounds **1–35 K** are fixed numerical settings. The original 20 K
  upper bound could not meet a 4,080 W catalogue output before calibration;
  35 K was selected for baseline feasibility and is held fixed during fitting.
  Speed 30–110 rev/s follows source-map support, not a certified target envelope.

## Roles and measured results

Only λov minimizes COP RMSE. λv and λloss move only when their respective
capacity/speed and discharge constraints fail; they are not jointly identified
from COP. Here both auxiliary scales remain 1 because adjustment is unnecessary.

| Scale | Applied value |
|---|---:|
| λov | 1.0228346439429379 |
| λv | 1.0 |
| λloss | 1.0 |

| Metric | Calibration: before → after | Holdout: before → after |
|---|---:|---:|
| COP RMSE | 0.433857 → 0.429129 | 0.503821 → 0.440215 |
| COP RMSE reduction | 1.09% | 12.62% |
| Power RMSE [W] | 123.933 → **129.052** | 113.122 → 97.077 |
| Valid/requested points | 6/6 → 6/6 | 3/3 → 3/3 |

The calibration power error **increases**, despite lower COP RMSE. These two
residuals have different weighting even though they derive from the same heat
and power observations. A single scale cannot remove the temperature-dependent
residual pattern; broad accuracy or generalization is not established by three
interpolated holdout rows.

## Baseline provenance

[Ossorio et al. (2024)](https://doi.org/10.1016/j.applthermaleng.2024.122666)
models mass flow and power as functions of absolute pressures and shaft speed.
This implementation generates 567 states and approximates them with six terms
`1, PR, N, PR², N², PR*N`. Generated points are not experimental observations.
Approximation RMSE is 0.924/4.053/0.291 percentage points for ηv/ηov/floss.
The full domain is PR 1.475–7.492, N 30–110 rev/s; rectangular domain membership
does not establish physical validity. The plots show PR 2–7.

The author's thesis Eq. 3.22 resolves the journal Eq. 22 complement ambiguity:
`eta_em = 1 - f_loss`, with Table 5 coefficients defining `f_loss`.
The thesis prints 134 measured R290 rows; the journal reports 133. We retain all
134 and do not guess the omitted row. See [the source audit](source_audit/audit.md)
and `audit_results.json` for methods, original PDF page references and residuals.
The audit uses source-model development data and is not independent validation.

Rebuild the locally retained source table from the public thesis PDF:

```bash
python validation/compressor_fitting/source_audit/reproduce_source_audit.py --thesis-pdf /path/to/thesis.pdf
```

Source PDFs, reproduced page crops and extracted original tables remain local,
following the repository's evidence policy. Their links, extraction code, hashes,
source audit results and our generated scientific plots are retained here.

## Artifacts and verification

`fit_report.json` retains initial, candidate and applied scales plus stage status.
`point_predictions.csv` contains every requested point at each fitting stage.
`metrics.json` never filters failed points to obtain a success-only RMSE.
`run_manifest.json` freezes the equipment, versions, bounds and partition.
`figures/axis_audit.json` records explicit ranges, intervals and endpoint ticks.

Related regression suite: **110 passed, 1 skipped**; the skip is the pre-existing
Keymark test with unavailable raw data. New tests include failed-fit rollback,
no-feasible-COP-candidate rejection, repeated-fit baseline preservation, source
version inference, coefficient reproduction and efficiency energy identities.
Ruff and targeted mypy pass. The new ASHP adapter is the only class-specific
fitting API added; no GSHP or boiler fitting support is implied.

Implementation tracking: [issue #89](https://github.com/bet-lab/TMHP/issues/89).
