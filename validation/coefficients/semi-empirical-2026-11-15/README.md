# semi-empirical-2026-11-15 — compressor-type-specific semi-empirical efficiency model

Fitted and cross-validated coefficient archive, wired into `src/tmhp/compressor_efficiency.py` (generated block,
`python -m validation.compressor_maps.semi_empirical.emit`). Standalone-compressor data only; no heat-pump catalogue
number is read anywhere in the pipeline.

**Revised in place on 2026-11-16** (the archive had not reached `main`; the version name is kept, `coefficients.json`
→ `revision` says what changed): (1) the volumetric efficiency has a form that is positive and bounded by
construction, and the rotary level comes from the catalogue rating points of the default displacement regression;
(2) the eta_is / eta_em split is a fixed share of the loss work reaching the gas, bounded by construction; (3) the
default rotary speed shape is the Ossorio-anchored variant `b` (owner-delegated decision); the first archive's
Shao-informed arrangement stays as variant `a` in `rotary_speed_variants*`. Second pass the same day: (4) the rotary
eta_oi level is given per refrigerant wherever rating points exist (R290 from the Highly 2024 inverter twins), and
(5) the uncertainty of eta_v above the rotary data (PR > 6.8) is recorded. Third pass: (6) the default rotary speed
shape is the Shao-informed variant `a`, chosen by a rule written down before the device measurement (EN 14825
trajectory shape; "Rotary speed-shape variants" below); variant `b` stays in `rotary_speed_variants*` and in the
library (`rotary_speed_variant="ossorio_anchored"`). **Consequence: the EN 14825 trajectory-shape check is no longer
independent validation for the rotary types.**

## What

Terminal-basis (inverter excluded) overall isentropic efficiency `eta_oi = m_dot (h_2s - h_1) / P`, the volumetric
efficiency, the split of `eta_oi` into the package's `eta_is` / `eta_em`, and a separate drive (inverter) model, for
three types:

| type | form | speed coordinate | level `a` (IQR) | eta_oi at R410A 7.2/54.4 °C, 60 rev/s | status |
|---|---|---|---|---|---|
| `scroll` | `1/eta = a phi_z(PR; rv, k, zv) + q2 n 1e5/MEP_s + cL (PR-1)/n` | `n = N/50` | 1.091 (1.069–1.147), 62 residential machines | 0.765 | fitted |
| `rotary_twin_inverter` | `1/eta = 1 + a (n 1e5/MEP_s + cL (PR-1)/n + cu MEP_s/1e6/n)` (variant `a`) | `n = N/N_rated` (60) | per refrigerant (below); pooled 1.271 | 0.717 (pooled level) | **provisional** |
| `rotary_single_fixed` | same rotary shape | `n = N/N_rated` (50) | per refrigerant (below); pooled 1.729 | 0.667 (pooled) | **provisional** |

### Rotary eta_oi level per refrigerant (revised 2026-11-16, second pass)

A refrigerant gets its own level (median over its rating points) where the type has at least three rating points of
it; every other refrigerant takes the pooled level (median over all rating points of the type) — an extrapolation in
suction pressure / MEP_s.

| type | refrigerant | rating points | level `a` median (IQR), default variant `a` | variant `b` |
|---|---|---|---|---|
| twin | R32 | 23, LG + Mitsubishi SCI | 1.316 (1.276–1.436) | 0.448 |
| twin | R410A | 18, LG + Mitsubishi SCI | 1.173 (1.127–1.220) | 0.567 |
| twin | R290 | 7, Highly 2024 inverter twins (power basis not printed, read as excluding the inverter: **assumed**) | 1.145 (1.133–1.271) | 0.790 |
| twin | any other (R134a, R1234yf, R454B, R452B, R22, ...) | pooled over the 48 | 1.271 (1.161–1.380) | 0.523 |
| single | R22 | 10, Panasonic | 1.791 (1.732–1.860) | 0.802 |
| single | R410A | 46, LG + Panasonic | 1.693 (1.579–1.816) | 0.784 |
| single | any other (R32, R290, ...) | pooled over the 56 + AREP #26 | 1.729 (1.620–1.855) | 0.790 |

Before settling on constants, the archive tests whether one pooled level fits R32, R410A and R290 once the loss
terms are right (`level_by_refrigerant.csv`, `coefficients.json` → `selection.rotary_level_by_refrigerant`).
Leave one refrigerant out over the twin rating points, **default variant `a`**: a pooled level alone predicts the
held-out refrigerant with RMSE 0.026 (R290) / 0.031 (R32) / 0.028 (R410A); the best single extra loss term fitted on
the other two refrigerants (`cH`) gives 0.027 / 0.031 / 0.023; each refrigerant around its own median level scatters
0.026 / 0.021 / 0.021. Among variant `a`'s backward elimination candidates, the shapes with the most uniform levels
(`q0+q2+cL+cu+cH`, spread 1.02) have LOCO shape 0.0319 against 0.0318 for the selected shape (spread 1.15): under
variant `a` the refrigerant dependence of the level is small and a near-uniform level costs nothing in the shape
cross-validation. The archive's rule (own level wherever a refrigerant has ≥ 3 rating points) is kept unchanged, so
the library still uses per-refrigerant levels. Under variant `b` the same test gave a pooled level 0.092 / 0.071 /
0.034 (R290 / R32 / R410A) and uniform-level shapes 0.034–0.040 against 0.021 — the dependence sat in `cH`, which
variant `a` does not carry.

Independent checks (not fit data):

| check | variant `a` (default): pooled LG/SCI level / R290 level | variant `b`: pooled LG/SCI level / R290 level |
|---|---|---|
| GMCC R290 inverter-input points EDTN210 / LDTN180: implied inverter efficiency (drive model at 60 rev/s 0.941) | 0.923 / 0.937 · 0.896 / 0.907 | 0.807 / 0.802 · 0.892 / 0.896 |
| Highly 2024 R290 inverter **single** rotary (out of sample), data / model | 1.066 / 1.036 | 0.939 / 1.036 |
| Panasonic fixed-speed single R290 (6, flagged "under development"; pooled single level, R290 has no eligible single points), data / model | 0.958 | 0.847 |

The library uses the R290 level for R290. With it the GMCC check implies an inverter efficiency of 0.90–0.91 under
variant `a` and 0.89–0.90 under `b` — it does not separate the shapes; only with the pooled LG/SCI level (R290 then
an extrapolation) does it (0.92–0.94 under `a`, 0.80–0.81 under `b`). Under `b` the pooled single level was ~15 %
optimistic for the R290 fixed-speed singles; under `a` it is 4 %. The pooled level for refrigerants without rating
points (R134a, R1234yf) remains an extrapolation in suction pressure.

Parameters: scroll `rv = 2.975, q2 = 1.054 bar, cL = 0.0584, zv = 0.645`; rotary (variant `a`, default)
`q2 = 1 (normaliser), cL = 0.0340, cu = 0.1188`; variant `b`: `cu = 0.243, cH = 2.409, qf = 0.123`. `phi_z` is the built-in-volume-ratio work ratio with a
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
* **eta_v level by refrigerant (audit, not acted on).** The level stays one pooled value per type (the displacement
  regression's convention: the refrigerant enters through fluid properties). Per-refrigerant medians over pooled:
  twin R32 0.977, R410A 1.044; single R22 1.010, R290 1.018, R32 0.958, R410A 0.990; Highly 2024 R290 twins (not in
  the regression) data / model 1.064. R290 evidence is mixed (single +1.8 %, Highly twins +6.4 %), within the
  R32/R410A spread of the twins.
* **eta_v above the data (PR > 6.8): stated uncertainty.** R32, 60 rev/s, n = 1, each slope re-levelled on the twin
  rating points (`eta_v_high_pr.csv`, `types.*.eta_v.uncertainty_above_data`):

  | PR slope | c_re | PR 6 | PR 8 | PR 10 | top-20 %-PR bias AREP #26 / Shao | shape RMSE AREP #26 / Shao |
  |---|---|---|---|---|---|---|
  | selected (Shao, AREP #26, Ossorio; machine weights) | 0.0681 | 0.796 | 0.736 | 0.684 | −0.013 / +0.002 | 0.016 / 0.013 |
  | AREP #26 alone (c_s held) | 0.0546 | 0.812 | 0.763 | 0.719 | −0.001 / +0.022 | 0.014 / 0.019 |
  | Shao 2004 alone | 0.0726 | 0.791 | 0.728 | 0.673 | −0.017 / −0.004 | 0.016 / 0.012 |

  The archive's rule (each machine weight 1, LOCO) selects the joint fit; leave-one-source-out shape errors of the
  selected form are Shao 0.020, AREP #26 0.016, Ossorio 0.012. No clearance fraction for rolling pistons can be
  cited from the sources at hand: Molinaroli 2017 (four Tecumseh machines) lumps clearance re-expansion and leakage
  into one nozzle area (A_TOT 0.009–0.059 mm²). `c_re` carries every PR-dependent loss that does not fall with speed
  and is not a clearance fraction.
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
| `backward_elimination.csv` | every candidate scored in the eta_oi term elimination (arrangement a; both variants' in `rotary_speed_variants_backward.csv`) |
| `model_selection.csv` | finalists, start form, shipped-form baseline and the default (variant a) on every criterion |
| `loco.csv`, `loso.csv`, `edge_holdout.csv`, `step_slopes.csv`, `parameter_stability.csv` | cross-validation tables |
| `eta_v_selection.csv`, `eta_v_backward.csv`, `eta_v_speed_ratios.csv`, `eta_v_rating_points.csv`, `eta_v_envelope.csv` | eta_v forms and their CV, the elimination, speed ratios against the data, the rating points with their `c0`, bounds over the envelope grid |
| `level_by_refrigerant.csv`, `eta_v_high_pr.csv` | the pooled-level test over the twin rating points; eta_v at PR 6/8/10 under three PR slopes |
| `split.csv`, `inverter_cv.csv` | eta_is/eta_em split forms, drive model cross-validation |
| `levels.csv` | eta_oi level per machine (scroll) and per rating point (rotary) |
| `grid_vs_shipped.csv` | eta_oi at PR 2–10 × N 20/40/60/90 (R410A, SDT 45 °C, SH 10 K) next to the shipped eta_is·eta_em |
| `rotary_speed_variants.json`, `rotary_speed_variants_cv.csv`, `rotary_speed_variants_backward.csv` | both rotary speed-shape variants (`default: "a"`) |
| `figures/` | eta_oi vs PR; residuals by PR and speed; speed dependence; drive model; level vs displacement |

## Rotary speed-shape variants

Variant `a` ("Shao-informed", the first archive's arrangement, the **default** since the third pass,
`rotary_speed_variant="shao_informed"`) fits Shao 2004, Ossorio 2019 and AREP #26 in one shared shape; the archive's
selection rule (backward elimination on the LOCO shape RMSE) keeps `q2, cL, cu` (`qf`: 0.0320 with, 0.0318 without,
not kept). Variant `b` ("Ossorio-anchored", the default of the first revision, `rotary_speed_variant="ossorio_anchored"`)
gives every Shao 2004 speed record its own level, so Shao informs only the within-record PR shape and the speed terms
rest on Ossorio 2019; the rule then keeps `q2, qf, cu, cH`, and the high-speed term `qf` is kept by the explicit rule
(LOCO shape 0.0209 with, 0.0215 without).

**How the default was chosen.** The compressor data do not decide between the two: Shao and Ossorio disagree on the
low-speed fall and no third rotary speed source exists. The rule, written down before the device measurement
(project design notes §14, written before the measurement; they also record that an earlier tree had
already pointed in this direction): use the EN 14825 part-load trajectory **shape** of the certified Keymark air-to-water population
(9,062 low- / 9,044 medium-temperature records) on the study's 30 cases (5 machines × 3 oversizings × 2
applications, `validation.analysis.en14825_trend`, water side at a fixed flow), primary metric = number of cases
whose COP(D)/COP(A) lies inside the population's p10–p90 (the study's C3), a difference of 3 or more decides;
secondary = median |ln(D/A ÷ population median)|. Measured on the UA-defaults tree (`aa2e5097` + the study's
water-side fix): **variant `a` 30 / 30, variant `b` 24 / 30; secondary 0.041 against 0.115** (D/A in p25–p75: 28
against 11). The rule selects `a`.

**What this costs.** The trajectory shape check is now part of the fit for the rotary types and is **no longer
independent validation** of them. The trajectory level (C2: every point inside its p10–p90), the catalogue parity
set and the A-7/W55 gate were not used in the choice and remain independent.

**Evidence pointing the other way** (recorded, not used by the rule):
- B/A and C/A sit closer to the ratio of the population medians under `b`: median over the low-temperature cases
  1.54 / 2.07 under `b` against 1.42 / 1.82 under `a`, population-median ratios 1.52 / 2.06. This is a weaker
  comparison than D/A's — only per-point quantiles are published, and a ratio of medians is not the median of the
  ratio.
- The level check C2 is 16 / 30 under `b` against 9 / 30 under `a`.
- The compressor-level cross-validation scores are not comparable across the two arrangements (`b` takes the Shao
  speed variation out of the residual by construction), but as computed: LOCO shape 0.0209 (`b`) against 0.0318
  (`a`).
- The D point (15 rev/s, PR ≈ 1.6) lies outside the rotary data in both speed (Ossorio 2019 30–120 rev/s, Shao 2004
  30–120 Hz) and pressure ratio (data ≥ 1.9, `hull_PR_N`) under either shape: both D/A values are extrapolations of
  the speed and PR terms, and the choice rests on how each extrapolates.
- With the per-refrigerant R290 level the GMCC inverter-input check gives 0.90–0.91 (`a`) against 0.89–0.90 (`b`)
  (table above) — no separation; the 0.92–0.94 against 0.80–0.81 contrast holds only with the pooled LG/SCI level.
- **The part-load curve at fixed temperatures** (measured after the choice, on the tree that carries the
  regenerated results; default 9 kW R32 air-to-water machine, frost-free, EN 14511 water flow, load reduced at held
  air and leaving-water temperatures down to the 15 rev/s speed floor). COP at the speed floor against full load,
  A7/W35 / A7/W45 / A−7/W45: `a` −16.7 / −27.0 / −34.0 %, `b` −3.9 / −12.7 / −14.5 %; the quadratic map of the
  previous release −0.9 / −3.9 / −1.3 %. Peak of the curve (load fraction, gain over full load): `a` 0.78 / 0.96 /
  0.82 with +1.7 / +0.1 / +0.6 %, `b` 0.56 / 0.74 / 0.62 with +4.9 / +1.9 / +2.9 %. For R290 the fall is smaller
  (`a` −5.0 / −16.4 / −22.9 %, `b` −1.5 / −11.4 / −10.6 %): the level `a` multiplies the speed-dependent loss terms
  and is largest for R32. The literature the project collected for this question puts the peak of an inverter
  air-to-water unit near a capacity ratio of 0.5–0.6 (or finds the COP rising monotonically towards low speed) and
  estimates the compressor-plus-inverter fall at the lowest speed at about −10 to −15 % (one measured scroll; low
  confidence). Under `b` the catalogue parity of the air-to-air units is worse (COP MAPE 9.37 % against 8.52 %,
  heating bias −10.2 % against −8.4 %) and the air-to-water set about the same (8.31 % / −5.97 % against 8.34 % /
  −5.02 %); the A−7/W55 gate points read −6.8 % and −2.6 % against −2.9 % and +1.6 %.
  If the Shao 2004 powers include the inverter (the basis is not stated), variant `a` with the separate drive model
  counts the low-speed drive loss twice.

**Owner decision of 2026-11-16, after that measurement:** the default stays `a` for this release, as the rule
chose it; the choice between the two shapes is reopened as a follow-up issue, with the co-author's review.

Model eta(N)/eta(60) at Ossorio's condition, 30 / 90 / 120 rev/s: `a` 0.905 / 0.980 / 0.932, `b`
0.968 / 0.962 / 0.908, data 0.980 / 0.978 / 0.914. Model eta(N)/eta(60) at Ossorio's condition, 30 / 90 / 120 rev/s: `a` 0.905 / 0.980 / 0.932, `b`
0.968 / 0.962 / 0.908, data 0.980 / 0.978 / 0.914. The level form (scaled) and the speed coordinate (relative) were
selected on arrangement `a` and are kept for `b`; on arrangement `b` the same rules would keep the additive level
form with the absolute coordinate and terms `q2, cH` (LOCO shape 0.0200 against 0.0209 for `b`; a tie within the
archive's 0.001 tolerance). `b` is kept as measured.

## Limits (read before use)

* **Rotary types are provisional.** Their eta_oi shape rests on five machines: Shao 2004 (R22, three
  manufacturer-map polynomials, power basis not stated), Ossorio 2019 (one measured R290 speed sweep at one
  condition) and AREP #26 (one fixed-speed machine, PR ≤ 6.3). No rotary data above PR 6.8; the inverter twin rotaries
  have one rating point each, so they set only the level.
* **Refrigerants without rating points take the pooled level.** Under the default variant `a` the twin levels
  are R32 1.32, R410A 1.17, R290 1.15 (spread 1.15; under `b` 1.76) and the R290 fixed-speed check puts the pooled
  single level 4 % optimistic (`b`: 15 %). With the R290 level the GMCC check implies an inverter efficiency of
  0.90–0.91, below the measured drive model (0.94 at 60 rev/s).
* **The rotary speed shape was chosen on the EN 14825 trajectory shape** (above): that check does not validate it.
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
* **eta_v above one.** The rotary level `c0` is 1.13 (twin) and 1.10 (single), so at a low lift and above the
  rated speed the form exceeds one (twin, rated 60 rev/s, R32: 1.02 at 90 rev/s and 1.04 at 120 rev/s, PR 1.5;
  the largest value found, 1.08, at PR 1.2 and 150 rev/s for a machine rated at 40). The library sets a value above
  one to one, which leaves a kink in eta_v there.
* **Variant `b` with wet suction.** With a two-phase suction (the discharge-temperature control) the effective
  exponent falls below one and the `cH` term turns negative: eta_oi 1.105 for R290 at PR 8, quality 0.9,
  120 rev/s. The library sets the resulting eta_is to one. Variant `a` has no `cH` term (largest eta_oi found 0.78).
* **The default speed floor lies below the rotary data.** The models' `rps_min` is 15 rev/s; the rotary sources
  start at 30 rev/s. Every point between is an extrapolation of the speed terms (the fixed-temperature curves
  above are read there).
* Out of range the forms are followed as they are; `hull_PR_N` is for an `efficiency_extrapolated` flag only.
