# semi-empirical-2026-11-15 — compressor-type-specific semi-empirical efficiency model

Fitted and cross-validated coefficient archive. **Not wired into `src/`** (a later step). Standalone-compressor
data only; no heat-pump catalogue number is read anywhere in the pipeline.

## What

Terminal-basis (inverter excluded) overall isentropic efficiency `eta_oi = m_dot (h_2s - h_1) / P`, the volumetric
efficiency, the split of `eta_oi` into the package's `eta_is` / `eta_em`, and a separate drive (inverter) model, for
three types:

| type | form | speed coordinate | level `a` (IQR) | eta_oi at R410A 7.2/54.4 °C, 60 rev/s | status |
|---|---|---|---|---|---|
| `scroll` | `1/eta = a phi_z(PR; rv, k, zv) + q2 n 1e5/MEP_s + cL (PR-1)/n` | `n = N/50` | 1.091 (1.069–1.147), 62 residential machines | 0.765 | fitted |
| `rotary_twin_inverter` | `1/eta = 1 + a (n 1e5/MEP_s + cL (PR-1)/n + cu MEP_s/1e6/n)` | `n = N/N_rated` (60) | 1.276 pooled; R32 1.316, R410A 1.173 | 0.716 (pooled level) | **provisional** |
| `rotary_single_fixed` | same rotary shape | `n = N/N_rated` (50) | 1.729 (1.620–1.855) | 0.667 | **provisional** |

Parameters: scroll `rv = 2.975, q2 = 1.054 bar, cL = 0.0584, zv = 0.645`; rotary `q2 = 1 (normaliser), cL = 0.0340,
cu = 0.119`. `phi_z` is the built-in-volume-ratio work ratio with a share `zv` of the over-compression loss relieved
below `PR = rv^k`. Full definitions: `coefficients.json` → `conventions`, `types.*.eta_oi`, and the docstring of
`validation/compressor_maps/semi_empirical/model.py`.

Drive: `eta_inv = 1 - lam (1 + beta (60/N - 1))`, `beta = 0.618`, `lam = 0.0588` (median of five measured
inverters, IQR 0.045–0.062): 0.941 at 60 rev/s, 0.869 at 20 rev/s.

## Regenerate

```
uv run python -m validation.compressor_maps.derive           # needs the untracked points_copeland_opi.csv
uv run python -m validation.compressor_maps.semi_empirical   # about 8 minutes on 8 workers
```

`coefficients.json["inputs"]` records the SHA-256 of every input file. The Copeland point file is not
redistributable; without it the pipeline stops and prints how to regenerate it.

## Files

| file | content |
|---|---|
| `coefficients.json` | per type: form, parameters, level with IQR, speed coordinate, eta_v, split, hull, power basis, provisional flag, monotonicity check; drive model; selection numbers; sensitivity; checks (GMCC, Highly, prototype reproduction) |
| `data_sources.csv` | per source: family, role, origin, machines, rows used / excluded / not derivable, PR and speed range, power basis, whether stated or assumed, evidence |
| `exclusions.csv` | excluded rows by source, reason and which fits they leave |
| `backward_elimination.csv` | every candidate scored in the term elimination |
| `model_selection.csv` | finalists, start form and shipped-form baseline on every criterion |
| `loco.csv`, `loso.csv`, `edge_holdout.csv`, `step_slopes.csv`, `parameter_stability.csv` | cross-validation tables |
| `eta_v_selection.csv`, `split.csv`, `inverter_cv.csv` | eta_v forms, eta_is/eta_em split, drive model cross-validation |
| `levels.csv` | level per machine (scroll) and per rating point (rotary) |
| `grid_vs_shipped.csv` | eta_oi at PR 2–10 × N 20/40/60/90 (R410A, SDT 45 °C, SH 10 K) next to the shipped eta_is·eta_em |
| `figures/` | eta_oi vs PR; residuals by PR and speed; speed dependence; drive model; level vs displacement |

## Limits (read before use)

* **Rotary types are provisional.** Their shape rests on five machines: Shao 2004 (R22, three manufacturer-map
  polynomials, power basis not stated), Ossorio 2019 (one measured R290 speed sweep at one condition) and AREP #26
  (one fixed-speed machine, PR ≤ 6.3). No rotary data above PR 6.3; the inverter twin rotaries have one rating point
  each, so they set only the level. The relative speed coordinate was selected for the rotary family by
  leave-one-source-out (0.0246 vs 0.0301), a difference carried by one machine with a non-60 Hz base (Shao's Hitachi,
  79 Hz) and Ossorio's placeholder rated speed. The low-speed shape is a compromise between Shao (−23 % at 30 rev/s)
  and Ossorio (−2 %); read as inverter-input, Shao fits the measured sources better (LOSO in `loso.csv`).
* **Level depends on the power basis.** LG/SCI twin-rotary and Shao powers are read as excluding the inverter
  (assumed); Copeland's drive-input powers are divided by the median drive model (the Copeland drive is not named), so
  the scroll default level (mostly Copeland machines) carries the drive model's level uncertainty (inverter IQR
  ±1.5 % in efficiency). Measured line-fed AREP scrolls sit lower (machine `a` 1.29 vs Copeland 1.09).
* **Sources disagree on the scroll PR shape.** Copeland polynomials fall faster than the model above PR 7, BITZER and
  the R-404A AREP scroll slower; the selected scroll form over-predicts the top-20 %-PR hold-out by +0.018 (start form
  −0.007). `zv` and `rv` are not stable when Copeland is left out (`zv` → 0, `rv` −0.31).
* **The eta_is/eta_em split is weakly identified.** Scroll: 4 measured machines, `eta_em = 0.916` (LOCO RMSE 0.057);
  rotary: one machine, four R-410A points, `eta_em = 0.858`, no cross-validation possible.
* **Rotary eta_v** shape comes from one Shao machine with a confirmed displacement (72 rows); its `c0` from the rating
  points.
* Out of range the forms are followed as they are; `hull_PR_N` is for an `efficiency_extrapolated` flag only.
