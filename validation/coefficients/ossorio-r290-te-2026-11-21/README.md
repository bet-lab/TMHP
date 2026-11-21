# Default compressor efficiency maps: Ossorio R290 scroll, PR / N / T_e (2026-11-21)

The library's default compressor efficiency models (`src/tmhp/compressor_efficiency.py`,
`COEFFICIENT_VERSION = "ossorio-r290-te-2026-11-21"`). They replace the type-specific semi-empirical
models of `semi-empirical-2026-11-15/` (#217). The axes were chosen in #218. BITZER data are not used.

## Data

`ossorio_thesis_r290_134.csv` holds 134 measured points of a 46 cm3/rev R290 scroll compressor at
30, 50, 70, 90 and 110 rev/s with a superheat of about 10 K.

- **Source:** R. Ossorio, PhD thesis, Universitat Politecnica de Valencia, Appendix 1, PDF pp. 244-252,
  extracted row by row. Page and row numbers are in the CSV.
- **Same tests as:** Compressor A of Ossorio & Navarro-Peris (2023), Appl. Therm. Eng. 230:120725.
- **Correlations fitted to these data:** Ossorio, Marchante-Avellaneda & Navarro-Peris (2024),
  Appl. Therm. Eng. 244:122666, which reports 133 points. The 2026-11-01 PR-N quadratic of #90 was
  fitted to those correlations, not to the measured points.

The electrical input is **compressor plus inverter** (the thesis's `compressor_plus_inverter_power_W`).
The library has no separate drive model (#217).

## Efficiencies of a measured point

`points.csv` is written by `validation/compressor_maps/ossorio_te/fit.py` with CoolProp HEOS
(Propane):

    eta_v  = m_dot / (rho_1 V N)
    eta_ov = m_dot (h_2s - h_1) / W
    f_loss = 1 - m_dot (h_2 - h_1) / W        h_2 from the measured discharge temperature

`eta_v` and `eta_ov` agree with the author's tabulated values to 0.005 and 0.008
(`coefficients.json`, `check`).

## Maps

With `l = ln PR`, `n = N/70` (N in rev/s) and `t = T_e/10` (T_e the evaporating saturation
temperature in degrees C):

    eta_v, eta_ov = c . [1, l, l^2, l^3, n, n^2, l n, t]
    f_loss        = c . [1, l, l^2, l^3, n, n^2, l n]

The library evaluates `eta_cmp_vol = eta_v`, `eta_cmp = 1 - f_loss` and
`eta_cmp_isen = eta_ov / (1 - f_loss)`.

| map | RMSE | 10-fold CV RMSE | largest error |
|---|---|---|---|
| eta_v | 0.0075 | 0.0081 | 0.025 |
| eta_ov | 0.0206 | 0.0221 | 0.075 |
| f_loss | 0.0137 | 0.0149 | 0.041 |

Why the T_e term:
- **The measured efficiencies depend on T_e.** At one pressure ratio and speed, the measured `eta_ov`
  and `eta_v` differ with the evaporating temperature. Without the T_e term, the residual follows
  T_e (`figures/te_residuals.png`).
- **The correlation behind the old quadratic says the same.** In the 2024 correlation the specific
  consumption carries `(P_c + 6.70)/(P_e + 0.51)`, which is not the pressure ratio. At PR 3 and
  70 rev/s, its `eta_ov` rises from 0.60 to 0.70 between T_e = -10 and 15 degrees C.
- **f_loss does not need it:** a T_e term changes its error by 0.0001.

The data domain feeds the `efficiency_extrapolated` flag only. It is the speeds 30-110 rev/s and the
convex (PR, T_e) hull of the points (every condition is tested at all five speeds). Outside the domain
the maps are followed as they are.

## Comparison that chose the axes (#218)

The `comparison/` folder holds the evidence behind the choice:

- **`scores.json`, `measured_r290`:** four candidates scored against the 134 points: the 2026-11-01 PR-N
  quadratic, (a) the PR-N form above, (b) the same form with T_e (adopted), and (c) a terminal-basis
  PR-N map with the Ossorio 2023 Eq. 5 inverter (inverter A coefficients, `delta_f = (f - 50)/50`,
  which reproduce the measured inverter loss to 5.5 W).
- **`scores.json`, `catalogue_fit` and `catalogue_fit_points.csv`:** each candidate calibrated to the
  Fujitsu ASYG30LMTA heating catalogue (50 rows) with the #90 protocol: no calibration, then
  `lambda_ov`, then `lambda_ov` plus a peak shift `alpha_r`, `alpha_N`. The calibration API is not in
  the library, so this was run on branch `feat/compressor-catalogue-fitting` at `9f349df1`. The
  scripts are in #218.
- **`ossorio2024_published_coefficients.json` and `pr_n_quadratic_2026_11_01.json`:** the 2024
  correlations and the quadratic that #90 fitted to them, for `figures/correlation_points_on_quadratic.png`.

| candidate | eta_ov RMSE (10-fold CV), 134 points | catalogue COP RMSE: none / lambda / lambda + shift |
|---|---|---|
| 2026-11-01 PR-N quadratic | 0.2901 (in its grid 0.0460) | 1.228 / 0.979 / 0.695 |
| (a) PR-N | 0.0335 (0.0345) | 1.176 / 0.842 / 0.723 |
| (b) PR-N + T_e, adopted | 0.0206 (0.0221) | 1.142 / 0.812 / 0.696 |
| (c) terminal + inverter | 0.0315 (0.0325) | 1.164 / 0.833 / 0.735 |

The catalogue does not separate the candidates. All four end within 0.04 of each other, with the
same residual pattern over the outdoor temperature. The calibration also lifts `lambda_ov` to 1.3-1.7,
which puts `eta_is` above one at 15-16 rows. On the 34 rows where no candidate does so after the last
stage, the 2026-11-01 quadratic scores 0.32 (mean error +0.16) and the adopted map 0.56 (+0.46): the
catalogue, read this way, favours the old quadratic. The catalogue calibration is therefore not evidence
for the choice, and the axes were chosen on the measured compressor (#218).

## Figures

`validation/compressor_maps/ossorio_te/figures.py` draws the data figures in the dartwork-mpl `scientific`
preset, 17 cm wide, as PNG and PDF in `figures/`. The figures carry no titles; the captions are here.

| file | caption |
|---|---|
| `map_pipeline` | How the 134 measured test points become the maps. The input is the test table (thesis Appendix 1), not a performance-curve equation. Upper three rows: measured flow, power and speed, with the CoolProp states from the measured T_e, T_c, suction and discharge temperatures, give three efficiencies per point (the fit targets). Lower rows: measured T_e, T_c and N give the pressure ratio and the terms ln PR, N/70 and T_e/10 (the axes). Least squares joins them into the three maps; the cycle reads them with its own PR, N and evaporating temperature. Accent: the evaporating temperature, the axis #218 added; it reaches η_ov and η_v, not f_loss. |
| `map_heatmaps` | a–i: the maps at T_e = −20 / 0 / 20 °C (columns) for η_v, η_ov and f_loss (rows), with the measured points at that T_e on the same colour scale. A marker that matches its background is a point the map reproduces. Pale: outside the data (speeds below 30 or above 110 rev/s, pressure ratios outside the (PR, T_e) hull at that T_e). f_loss has no T_e term. |
| `map_slices` | a–i: the maps at 30 / 70 / 110 rev/s (columns) against the measured points coloured by T_e; solid at T_e = −20 / 0 / 20 °C inside the data, dotted outside it; dashed, the PR–N quadratic of 2026-11-01 they replace. |
| `te_residuals` | a, b: measured minus map for η_ov and η_v against T_e, with the 7-term PR–N form alone (circles) and with the T_e term (triangles); same 134 points. The points are spread by up to ±1.2 K around their T_e (and the two sets by ∓0.8 K) so that they do not overlap. |
| `correlation_points_on_quadratic` | a–c: at 70 rev/s, the PR–N quadratic of 2026-11-01 (line) against the Ossorio 2024 correlation it was fitted to — its 567-point grid (circles, T_e −10…15 °C) and the same correlation outside that grid (triangles) — and the measured points (×). Shaded: the PR range of the grid. |
| `catalogue_fit` | a: COP RMSE of the four candidate maps on the Fujitsu ASYG30LMTA heating catalogue (50 rows) at each stage of the #90 calibration. b: the residual over the outdoor temperature after the last stage. |

`map_pipeline` is drawn with the betlab Figure System kit; its script is kept in
`validation/compressor_maps/ossorio_te/concept/MP1_map_pipeline.py` (it runs inside the kit, not here) and the kit's
layout lint, density and spacing checks pass on it.

### How much the T_e term removes

`validation/compressor_maps/ossorio_te/te_error_figures.py` compares the 7-term PR–N form with and without the T_e term
on the same 134 points (10-fold CV with the folds of `fit.py`; F test of the one added term):

| map | RMSE, PR–N / with T_e | MAPE, PR–N / with T_e | 10-fold CV RMSE, PR–N / with T_e | F (1, 126), p |
|---|---|---|---|---|
| eta_ov | 0.0335 / 0.0206 | 4.7 % / 2.9 % | 0.0345 / 0.0221 (−36 %) | 206, 3e-28 |
| eta_v | 0.0142 / 0.0075 | 1.3 % / 0.6 % | 0.0148 / 0.0081 (−45 %) | 331, 5e-37 |
| f_loss | 0.0137 / 0.0136 | 9.3 % / 9.1 % | 0.0149 / 0.0148 (−1 %) | 2.5, 0.12 |

| file | caption |
|---|---|
| `te_error_parity` | a–d: measured against map for η_ov (a, b) and η_v (c, d), coloured by T_e. Left column: the PR–N map; right column: with the T_e term (adopted). Each panel gives the RMSE and MAPE against the 134 points. Without the term the warm points sit above the line and the cold points below it. |

## Reproduce

    uv run python -m validation.compressor_maps.ossorio_te.fit       # points.csv, coefficients.json
    uv run python -m validation.compressor_maps.ossorio_te.emit      # constants of the library module
    uv run python -m validation.compressor_maps.ossorio_te.figures   # figures/ (data figures; map_pipeline: see above)
    uv run python -m validation.compressor_maps.ossorio_te.te_error_figures   # figures/te_error_parity

`tests/test_compressor_default_map.py` checks three things: the library block equals this archive,
the archive refits from `points.csv`, and the maps reproduce the archived errors.
