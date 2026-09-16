# Simplified compressor-efficiency sensitivity of the PLR–COP curve

Seven cases per duty. **BASE** holds the three compressor efficiencies at
representative constants; each sensitivity case gives **one** efficiency the
**same** relative quadratic multiplier in **one** driver and keeps the other two
at BASE.

| Case | Driver | Varied | Others |
| --- | --- | --- | --- |
| `BASE` | — | none | η_v 0.95, η_is 0.70, η_em 0.90 |
| `N-V` / `N-I` / `N-E` | `n* = N / N_rated` | η_v / η_is / η_em | BASE constants |
| `P-V` / `P-I` / `P-E` | `P_r = P_dis / P_suc` | η_v / η_is / η_em | BASE constants |

```
η_i(n*) = η_{i,0} [1 − 0.60 (n* − 0.60)²]
η_i(P_r) = η_{i,0} [1 − 0.60 (P_r − 2.00)²]
```

Nothing is fitted, no reference efficiency is read from the shipped model, and
no anchors are derived from a control run. The shipped correlations
(`CURRENT`) are not part of the comparison. The functions are perturbations
for a sensitivity test, not candidate correlations; `src/tmhp` is untouched and
every case is passed to `AirSourceHeatPump(eta_cmp_vol=…, eta_cmp_isen=…,
eta_cmp=…)` as `(P_r, rps)` callables.

## Conditions

Air-to-air `AirSourceHeatPump`, 3.5 kW R32: heating outdoor 7 °C / room 20 °C,
cooling outdoor 35 °C / room 27 °C. Requested PLR 0.10 → 1.00 in 0.025 steps.
Everything else is the model default (`rps_rated` 60, `rps_min` 15, `rps_max`
150 rev/s, `V_cmp_ref` from `default_displacement`, `UA_ou_rated =
hp_capacity / 5`, `UA_iu_rated = 0.8 × UA_ou_rated`, fans `hp_capacity ×
0.0002`).

## Range and clipping

The multiplier is only interpreted over the `n*` / `P_r` range the sweep
traverses. Because the speed search brackets the whole envelope, the callables
clip to `0.1 ≤ η ≤ 1.0`; a reported row that sits on the clip is flagged
`eta_clipped` and drawn with a cross. None occur.

## Rows at the speed floor

`capacity_clamped == "min"` rows stay in the CSV, are drawn hollow, and are
excluded from every metric. The low-load comparison point is the lowest PLR at
which all seven cases still modulate.

## Run

```bash
uv run python3 -m validation.compressor_efficiency_sensitivity_simple.sweep
uv run python3 -m validation.compressor_efficiency_sensitivity_simple.figures
uv run python3 -m validation.compressor_efficiency_sensitivity_simple.notion_publish --replace
```

Output: `validation/data/compressor_efficiency_sensitivity_simple/` —
`results_<duty>.csv`, `parameters_<duty>.json`, `summary_<duty>.csv`,
`figures/fig{1..5}_*.png|svg`.

The earlier anchored study (`PLR_ref`, `low / opt / high`, `S_low`, `CURRENT`
comparison) is archived unchanged in `validation/compressor_efficiency_sensitivity/`.
