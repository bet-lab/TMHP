# Compressor-efficiency sensitivity of the fixed-boundary PLR–COP curve

Controlled test: **one efficiency at a time** (`eta_cmp_vol` / `eta_cmp_isen` /
`eta_cmp`) is given a synthetic ∩-shaped dependence on **one driver** (relative
speed `n* = rps / rps_rated`, or pressure ratio `PR`), with the **same normalised
penalty in every case** (−10 % at the low anchor, −5 % at the high anchor). The
other two efficiencies are constant at the reference level the shipped model
reports at PLR 0.65. Two controls frame the six cases:

| Case | Driver | Varied | Others |
| --- | --- | --- | --- |
| `C0` | — | none | all three constant (= reference) |
| `CURRENT` | PR, n* | shipped correlations (`COEFFICIENT_VERSION`) | — |
| `N-V` / `N-I` / `N-E` | `n*` | η_v / η_is / η_em | constant |
| `PR-V` / `PR-I` / `PR-E` | `PR` | η_v / η_is / η_em | constant |

The synthetic curves are **not** candidate correlations. They exist to separate
which efficiency, and which driver, moves the system-level curve. The production
defaults in `src/tmhp` are untouched: every case is passed to
`AirSourceHeatPump(eta_cmp_vol=…, eta_cmp_isen=…, eta_cmp=…)` as `(PR, rps)`
callables — the validation-only override path.

## Conditions

Air-to-air `AirSourceHeatPump`, 3.5 kW R32 (the `validation.fixed_boundary_plr`
conditions): heating outdoor 7 °C / room 20 °C, cooling outdoor 35 °C / room
27 °C. Requested PLR 1.000 → 0.100 in 0.025 steps. Everything else is the model
default (`rps_rated` 60, `rps_min` 15, `rps_max` 150, `V_cmp_ref` from
`default_displacement`, `UA_ou_rated = hp_capacity / 5`, `UA_iu_rated = 0.8 ×
UA_ou_rated`, fans `hp_capacity × 0.0002`).

## Anchors come from the control run

`sweep.py` runs `CURRENT` first and reads η_ref at PLR 0.65; then `C0` with those
constants; then reads `x_low` (last modulating PLR before the speed floor),
`x_opt` (interpolated at PLR 0.60) and `x_high` (PLR 1.00) for both drivers off
the `C0` trajectory. Outside `[x_low, x_high]` the multiplier is held at its
endpoint value (the speed search brackets the whole envelope, and a quadratic
followed to `n* = 2.5` goes negative).

## Rows at the speed floor are separated, not scored

`capacity_clamped == "min"` rows are kept in the CSV, drawn hollow, and excluded
from every metric. `plr_low` (the low-load comparison point) is the lowest PLR at
which all eight cases still modulate.

## Run

```bash
uv run python3 -m validation.compressor_efficiency_sensitivity.sweep --duty heating
uv run python3 -m validation.compressor_efficiency_sensitivity.sweep --duty cooling
uv run python3 -m validation.compressor_efficiency_sensitivity.metrics     # summary_*.csv
uv run python3 -m validation.compressor_efficiency_sensitivity.figures     # figures/*.png|svg
uv run python3 -m validation.compressor_efficiency_sensitivity.notion_publish --replace
```

Output lives in `validation/data/compressor_efficiency_sensitivity/`:
`results_<duty>.csv`, `parameters_<duty>.json`, `summary_<duty>.csv`, `figures/`.
