# Simplified compressor-efficiency sensitivity of the PLR–COP curve

Seven cases per duty. **BASE** holds the three compressor efficiencies at
representative constants; each sensitivity case gives **one** efficiency a
relative multiplier in **one** driver (∩ quadratic for η_is and η_em, linear
decrease for η_v; shared centres) and keeps the other two at BASE.

| Case | Driver | Varied | Others |
| --- | --- | --- | --- |
| `BASE` | — | none | η_v 0.90, η_is 0.70, η_em 0.90 |
| `N-V` / `N-I` / `N-E` | `n* = N / N_rated` | η_v / η_is / η_em | BASE constants |
| `P-V` / `P-I` / `P-E` | `P_r = P_dis / P_suc` | η_v / η_is / η_em | BASE constants |

```
η_is, η_em (∩ quadratic):  η_i(n*)  = η_{i,0} [1 − 0.60 (n* − 1.00)²]
                           η_i(P_r) = η_{i,0} [1 − 0.60 (P_r − 2.00)²]
η_v (linear):              η_v(n*)  = η_{v,0} [1 − 0.10 (n* − 1.00)]
                           η_v(P_r) = η_{v,0} [1 − 0.10 (P_r − 2.00)]
```

The centres put every efficiency at its best at the rated speed, so the swept
range (n* 0.25–0.99) lies almost entirely on the falling side.

η_v is linear because the speed search reads the delivered flow ∝ η_v · n* and
brackets n* up to 2.5: a ∩ η_v makes that product peak inside the bracket and
the search then clamps every point to `rps_max`. With slope 0.10 the product
keeps rising until n* 5.5, so no hold is needed. η_v,0 is 0.90 rather than
0.95 because the linear multiplier rises left of its centre and 0.95 would put
η_v above 1 at the speed floor.

Nothing is fitted, no reference efficiency is read from the shipped model, and
no anchors are derived from a control run. The shipped correlations
(`CURRENT`) are not part of the comparison. The functions are perturbations
for a sensitivity test, not candidate correlations; `src/tmhp` is untouched and
every case is passed to `AirSourceHeatPump(eta_cmp_vol=…, eta_cmp_isen=…,
eta_cmp=…)` as `(P_r, rps)` callables.

## Conditions

Air-to-air `AirSourceHeatPump`, 3.5 kW R32: heating outdoor 7 °C / room 20 °C,
cooling outdoor 35 °C / room 27 °C. Requested PLR 0.10 → 1.00 in 0.025 steps.
Both conductances are held at `UA_ou_rated = UA_iu_rated = 700` W/K, so the two
duties differ only by the boundary air temperatures. Everything else is the
model default (`rps_rated` 60, `rps_min` 15, `rps_max` 150 rev/s, `V_cmp_ref`
from `default_displacement`, fans `hp_capacity × 0.0002`).

## Range and clipping

The multipliers are only interpreted over the `n*` / `P_r` range the sweep
traverses. The callables clip to `0.1 ≤ η ≤ 1.0` far outside it; a reported
row that sits on the clip is flagged `eta_clipped` and drawn with a cross.
None occur.

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
`figures/fig{1..4}_*.png|svg`. The figure numbers follow the published page;
an operating-state figure was produced earlier and dropped from it.

The earlier anchored study (`PLR_ref`, `low / opt / high`, `S_low`, `CURRENT`
comparison) is archived unchanged in `validation/compressor_efficiency_sensitivity/`.
