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
which puts `eta_is` above one at 15-16 rows. The axes were therefore chosen on the measured
compressor (#218).

## Figures

`validation/compressor_maps/ossorio_te/figures.py` writes `figures/`:

| file | content |
|---|---|
| `map_heatmaps.png` | the maps at T_e = -20 / 0 / 20 degrees C, with the measured points at that T_e on the same colour scale |
| `map_slices.png` | the maps at three speeds and three T_e against the measured points; dashed, the quadratic they replace |
| `te_residuals.png` | residual against T_e with and without the T_e term |
| `correlation_points_on_quadratic.png` | the 2026-11-01 quadratic against the 2024 correlation it was fitted to, inside and outside its grid |
| `catalogue_fit.png` | the Fujitsu calibration of the four candidates |

## Reproduce

    uv run python -m validation.compressor_maps.ossorio_te.fit       # points.csv, coefficients.json
    uv run python -m validation.compressor_maps.ossorio_te.emit      # constants of the library module
    uv run python -m validation.compressor_maps.ossorio_te.figures   # figures/

`tests/test_compressor_default_map.py` checks three things: the library block equals this archive,
the archive refits from `points.csv`, and the maps reproduce the archived errors.
