# semi-empirical-2026-11-15 — compressor-type-specific semi-empirical efficiency model

Fitted and cross-validated coefficient archive, wired into `src/tmhp/compressor_efficiency.py` (generated block,
`python -m validation.compressor_maps.semi_empirical.emit`). Standalone-compressor data only; no heat-pump catalogue
number is read anywhere in the pipeline.

**Revised in place on 2026-11-16** (the archive had not reached `main`; the version name is kept, `coefficients.json`
→ `revision` says what changed): (1) the volumetric efficiency has a form that is positive and bounded by
construction, and the rotary level comes from the catalogue rating points of the default displacement regression;
(2) the eta_is / eta_em split is a fixed share of the loss work reaching the gas, bounded by construction; (3) the
default rotary speed shape is the Ossorio-anchored variant `b` (owner-delegated decision); the first archive's
Shao-informed arrangement stays as variant `a` in `rotary_speed_variants*`.

## What

Terminal-basis (inverter excluded) overall isentropic efficiency `eta_oi = m_dot (h_2s - h_1) / P`, the volumetric
efficiency, the split of `eta_oi` into the package's `eta_is` / `eta_em`, and a separate drive (inverter) model, for
three types:

| type | form | speed coordinate | level `a` (IQR) | eta_oi at R410A 7.2/54.4 °C, 60 rev/s | status |
|---|---|---|---|---|---|
| `scroll` | `1/eta = a phi_z(PR; rv, k, zv) + q2 n 1e5/MEP_s + cL (PR-1)/n` | `n = N/50` | 1.091 (1.069–1.147), 62 residential machines | 0.765 | fitted |
| `rotary_twin_inverter` | `1/eta = 1 + a (n 1e5/MEP_s + qf n^2 1e4 rho_1/MEP_s + cu MEP_s/1e6/n + cH (PR^((k-1)/k) - 1))` | `n = N/N_rated` (60) | 0.501 pooled; R32 0.448, R410A 0.567 | 0.756 (pooled level) | **provisional** |
| `rotary_single_fixed` | same rotary shape | `n = N/N_rated` (50) | 0.790 | 0.670 | **provisional** |

Parameters: scroll `rv = 2.975, q2 = 1.054 bar, cL = 0.0584, zv = 0.645`; rotary (variant `b`, default)
`q2 = 1 (normaliser), cu = 0.243, cH = 2.409, qf = 0.123`. `phi_z` is the built-in-volume-ratio work ratio with a
share `zv` of the over-compression loss relieved below `PR = rv^k`. Full definitions: `coefficients.json` →
`conventions`, `types.*.eta_oi`, and the docstring of `validation/compressor_maps/semi_empirical/model.py`.

Drive: `eta_inv = 1 - lam (1 + beta (60/N - 1))`, `beta = 0.618`, `lam = 0.0588` (median of five measured
inverters, IQR 0.045–0.062): 0.941 at 60 rev/s, 0.869 at 20 rev/s.

### Volumetric efficiency (revised)

    eta_v = c0 * exp(-c_re (PR^(1/k) - 1)) / (1 + c_L (PR - 1)/n + c_s/n + c_f n^2)

The exponential is the clearance re-expansion law `1 - c_re (PR^(1/k) - 1)` to first order and stays positive; the
denominator is the delivered share when gas in the chamber leaves either through the discharge (rate ~ n) or back
to suction (leakage ~ pressure difference `c_L (PR - 1)`, a share that does not scale with it `c_s`, e.g. suction
heating, and a port drop `c_f n^2`). All parameters >= 0, so `0 < eta_v <= c0` for every PR >= 1 and speed > 0 and
eta_v falls with PR and with falling speed. The first archive's linear form `c0 - c_re (...) - c_Lv (PR-1)/n`
crossed zero (rotary: at PR 11 and 15 rev/s for a machine rated at 55 rev/s).

| type | terms (parameters) | level `c0` (IQR, n) | eta_v at the regression's reference condition (n = 1) | min over the envelope grid, new / first archive |
|---|---|---|---|---|
| `scroll` | `c_re = 0.00980, c_L = 0.0207`, `n = N/50` | 1.011 (0.998–1.033), median of the unit levels of the scroll eta_v rows | — | 0.123 / −4.07 |
| `rotary_twin_inverter` | `c_re = 0.0681, c_s = 0.1234`, `n = N/N_rated` | 1.130 (1.103–1.171), 41 rating points (LG, Mitsubishi SCI) | R32 0.889, R410A 0.871 (regression 0.887) | 0.088 / −6.63 |
| `rotary_single_fixed` | same shape | 1.102 (1.078–1.121), 98 rating points (Highly, LG, Panasonic) | R32 0.866, R410A 0.852 (regression 0.855) | 0.086 / −6.65 |

Envelope grid (`eta_v_envelope.csv`): PR 1.01–15, k 0.9–1.3, n 0.05–3 (relative) or 2.5–150 rev/s (scroll).

* **Rotary shape.** Rows with a mass flow from the sources that measure a PR or speed dependence, each unit with its
  own level: Shao 2004 (the machine with a confirmed displacement, 72 rows, 30–120 rev/s), AREP #26 (measured mass
  flow at fixed 50 Hz, PR 2.3–6.3, R-410A and R-32; its rated eta_v under the archive's 0.85 bracket is a level
  effect, the induction-motor slip) and Ossorio 2019 (30–120 rev/s at one condition; displacement inferred from the
  printed efficiency, so it informs the speed shape only). Backward elimination from `c_re, c_L, c_s, c_f` on the
  leave-one-machine-out shape RMSE drops `c_f` and then `c_L` (`eta_v_backward.csv`). Cross-validation
  (`eta_v_selection.csv`), new form against the first archive's linear form on the same rows: LOCO shape 0.0167 /
  0.0220, LOSO mean 0.0163 / 0.0204, top-20 %-PR hold-out bias −0.015 / −0.025, lowest-speed hold-out +0.012 /
  +0.023.
* **How weakly the shape is identified.** Three machines. The speed term rests on two sources that disagree:
  eta_v(30)/eta_v(60) is 0.886 in Shao and 0.939 in Ossorio; the model gives 0.901 for both (`eta_v_speed_ratios.csv`).
  The selected `c_s/n` (a speed loss that does not grow with PR) is what Shao shows (its 30-to-60 rev/s step is about
  the same at PR 1.9 and 6.8); `c_L (PR - 1)/n` alone scores 0.0223. The PR fall rests mostly on Shao (R22, 2004);
  AREP #26 falls more slowly (0.885 → 0.753 from PR 2.3 to 6.25 against the model's 0.80 ratio), hence the negative
  top-PR bias. No rotary eta_v data above PR 6.8; the form is followed beyond it. With the two Shao machines whose
  displacement is not confirmed, the elimination keeps `c_re, c_s, c_f` (sensitivity in `coefficients.json` →
  `selection.eta_v_rotary`).
* **Rotary level.** Every eligible rating point of
  `validation/default_inputs/compressor_displacement/measurements.csv` (capacity, displacement and test frequency;
  power is not needed): `c0` of a point is its eta_v over the shape at its own condition and `n = 1`
  (`eta_v_rating_points.csv`). Same points and the same speed convention as the displacement regression (fixed-speed
  single rotaries at the 50 Hz supply frequency), so the model reproduces the regression's pooled eta_vol at the
  regression's reference condition (table). No eta_v bracket is applied to the rating points (it would select on
  the quantity measured): the first archive used 39 + 25 points with power and inside the 0.85–1.05 bracket.
  Highly 2024 R290 inverter rotaries (check only): data 0.930, model at the twin level 0.873.
* **Scroll.** Refitted in the bounded form with the archive's two terms: LOCO shape 0.0414 against 0.0412 for the
  linear form. The linear scroll form is positive above 15 rev/s but crosses zero below it at high PR, and the
  models accept any `rps_min > 0`.

### eta_is / eta_em split (revised)

A fixed share `f` of the loss work `P - m_dot w_s` reaches the gas: `eta_is = 1/(1 + f (1/eta_oi - 1))`,
`eta_em = eta_oi + f (1 - eta_oi)`; their product is `eta_oi` and both lie in (0, 1] for any `eta_oi` in (0, 1].
The first archive's constant `eta_em` (0.916 scroll, 0.858 rotary) gave `eta_is > 1` wherever `eta_oi > eta_em`
(variant `b` at low PR). Fitted on the measured eta_is (discharge temperature) of `split.split_rows`:

| family | f | rows / machines | identification |
|---|---|---|---|
| scroll | 0.769 | 818 / 4 (7 units) | unit values 0.67–1.00 (Cuevas T3 inverter-fed 1.00); LOCO RMSE eta_is 0.0398 (constant eta_em 0.0410), eta_em 0.0559 (0.0573) |
| rotary | 0.576 | 4 / 1 (AREP #26 R-410A) | one machine, no cross-validation; R-32 unit 0.684 (possible liquid injection); in-sample RMSE eta_is 0.0176 (constant 0.0182) |

## Regenerate

```
uv run python -m validation.compressor_maps.derive           # needs the untracked points_copeland_opi.csv
uv run python -m validation.compressor_maps.semi_empirical   # about 8 minutes on 8 workers; also writes rotary_speed_variants*
uv run python -m validation.compressor_maps.semi_empirical.emit   # the library's generated block
```

`coefficients.json["inputs"]` records the SHA-256 of every input file (the rating points `measurements.csv` and,
for the consistency check, the displacement `regression.json` beside it included). The Copeland point file is not
redistributable; without it the pipeline stops and prints how to regenerate it.

## Files

| file | content |
|---|---|
| `coefficients.json` | per type: form, parameters, level with IQR, speed coordinate, eta_v (form, parameters, level and its rating points, regression consistency, envelope), split, hull, power basis, provisional flag, monotonicity check; drive model; selection numbers (incl. `selection.eta_v_rotary`); sensitivity; checks (GMCC, Highly, prototype reproduction) |
| `data_sources.csv` | per source: family, role, origin, machines, rows used / excluded / not derivable, PR and speed range, power basis, whether stated or assumed, evidence |
| `exclusions.csv` | excluded rows by source, reason and which fits they leave |
| `backward_elimination.csv` | every candidate scored in the eta_oi term elimination (arrangement a; variant b's in `rotary_speed_variants_backward.csv`) |
| `model_selection.csv` | finalists, start form, shipped-form baseline and the default (variant b) on every criterion |
| `loco.csv`, `loso.csv`, `edge_holdout.csv`, `step_slopes.csv`, `parameter_stability.csv` | cross-validation tables |
| `eta_v_selection.csv`, `eta_v_backward.csv`, `eta_v_speed_ratios.csv`, `eta_v_rating_points.csv`, `eta_v_envelope.csv` | eta_v forms and their CV, the elimination, speed ratios against the data, the rating points with their `c0`, bounds over the envelope grid |
| `split.csv`, `inverter_cv.csv` | eta_is/eta_em split forms, drive model cross-validation |
| `levels.csv` | eta_oi level per machine (scroll) and per rating point (rotary) |
| `grid_vs_shipped.csv` | eta_oi at PR 2–10 × N 20/40/60/90 (R410A, SDT 45 °C, SH 10 K) next to the shipped eta_is·eta_em |
| `rotary_speed_variants.json`, `rotary_speed_variants_cv.csv`, `rotary_speed_variants_backward.csv` | both rotary speed-shape variants (`default: "b"`) |
| `figures/` | eta_oi vs PR; residuals by PR and speed; speed dependence; drive model; level vs displacement |

## Rotary speed-shape variants

Variant `b` ("Ossorio-anchored", the **default** since the revision) gives every Shao 2004 speed record its own
level, so Shao informs only the within-record PR shape and the speed terms rest on Ossorio 2019; the archive's
selection rule (backward elimination on the LOCO shape RMSE) then keeps `q2, qf, cu, cH`, and the high-speed term
`qf` is kept by the explicit rule (LOCO shape 0.0209 with, 0.0215 without). Variant `a` ("Shao-informed", the first
archive's arrangement, `rotary_speed_variant="shao_informed"` in the library) keeps `q2, cL, cu` (`qf`: 0.0320 with,
0.0318 without). Model eta(N)/eta(60) at Ossorio's condition, 30 / 90 / 120 rev/s: `a` 0.905 / 0.980 / 0.932, `b`
0.968 / 0.962 / 0.908, data 0.980 / 0.978 / 0.914. The level form (scaled) and the speed coordinate (relative) were
selected on arrangement `a` and are kept for `b`; on arrangement `b` the same rules would keep the additive level
form with the absolute coordinate and terms `q2, cH` (LOCO shape 0.0200 against 0.0209 for `b`; a tie within the
archive's 0.001 tolerance). `b` is used as measured because the decision was made on it.

## Limits (read before use)

* **Rotary types are provisional.** Their eta_oi shape rests on five machines: Shao 2004 (R22, three
  manufacturer-map polynomials, power basis not stated), Ossorio 2019 (one measured R290 speed sweep at one
  condition) and AREP #26 (one fixed-speed machine, PR ≤ 6.3). No rotary data above PR 6.8; the inverter twin rotaries
  have one rating point each, so they set only the level.
* **R290 eta_oi level with variant b.** R290 has no level of its own (pooled twin level). The R290 checks that are not
  fit data disagree with variant `b`: Highly 2024 R290 catalogue eta_oi / model 0.907 (variant `a`: 1.031), and the
  GMCC R290 specification points imply an inverter efficiency of 0.80 (variant `a`: 0.92–0.94).
* **Level depends on the power basis.** LG/SCI twin-rotary and Shao powers are read as excluding the inverter
  (assumed); Copeland's drive-input powers are divided by the median drive model (the Copeland drive is not named), so
  the scroll default level (mostly Copeland machines) carries the drive model's level uncertainty (inverter IQR
  ±1.5 % in efficiency). Measured line-fed AREP scrolls sit lower (machine `a` 1.29 vs Copeland 1.09).
* **Sources disagree on the scroll PR shape.** Copeland polynomials fall faster than the model above PR 7, BITZER and
  the R-404A AREP scroll slower; the selected scroll form over-predicts the top-20 %-PR hold-out by +0.018 (start form
  −0.007). `zv` and `rv` are not stable when Copeland is left out (`zv` → 0, `rv` −0.31).
* **The split is weakly identified.** Scroll: four measured machines; rotary: one machine, four R-410A points.
* **Rotary eta_v** shape: three machines, two speed sources that disagree (above); level from 139 rating points at
  one condition per protocol (PR about 3.4).
* Out of range the forms are followed as they are; `hull_PR_N` is for an `efficiency_extrapolated` flag only.
