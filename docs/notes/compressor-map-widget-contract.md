# Compressor-map widget: data and evaluation contract (#253)

The one contract the data generator, the widget and the page follow. Change it here first; the
briefs point to this file and do not restate it.

## Files

| Role | Path |
|---|---|
| Generator | `scripts/data/gen_compressor_map_data.py` (run as `python -m scripts.data.gen_compressor_map_data`) |
| Data (tracked, like `validation-points.json`) | `docs/source/_static/data/compressor-maps.json` |
| Pure evaluator (ES module, no DOM) | `docs/source/_static/js/widgets/compressor-map-model.js` |
| Widget (DOM, drawing) | `docs/source/_static/js/widgets/compressor-map.js` |
| Raw-HTML include | `docs/source/_static/html/compressor_map_widget.html` (`.. raw:: html` `:file:`, as `cycle_widget.html`) |
| Page | `docs/source/concepts/compressor-efficiency.rst` |
| Tests | `tests/test_docs_compressor_map_data.py` |

## JSON (`schema: 1`)

Written with `scripts.data._common.write_json`; deterministic (no timestamps, no commit hashes); floats
written by Python `repr` round-trip (full precision). No NaN: a value that does not exist is `null`.

```jsonc
{
  "schema": 1,
  "coefficient_version": "ossorio-r290-te-2026-11-21",   // tmhp.compressor_efficiency.COEFFICIENT_VERSION
  "variables": {
    "l": "natural log of the pressure ratio r_p = P_cmp,out / P_cmp,in",
    "N": "shaft speed [rev/s], as is",
    "T_evap": "evaporating saturation temperature [degC], as is"
  },
  "terms": {                                    // the order of the coefficients
    "eta_v":  ["1", "l", "l^2", "l^3", "N", "N^2", "l*N", "T_evap"],
    "eta_ov": ["1", "l", "l^2", "l^3", "N", "N^2", "l*N", "T_evap"],
    "f_loss": ["1", "l", "l^2", "l^3", "N", "N^2", "l*N"]
  },
  "coefficients": { "eta_v": [8 floats], "eta_ov": [8 floats], "f_loss": [7 floats] },  // MAP_COEFFICIENTS, unchanged
  "domain": {
    "N_rps": [30.0, 110.0],
    "hull_PR_Te": [[PR, T_evap], ...]            // tmhp.compressor_efficiency.DOMAIN.hull_PR_Te.vertices (CCW, as the library holds them)
  },
  "peak_eta_ov": { "PR": float, "N_rps": float },  // argmax of eta_ov over (PR, N) inside the data domain at T_evap = 0, found numerically;
                                                   // the T_evap term is additive, so the location does not depend on T_evap
  "measured_points": [                           // validation/coefficients/<version>/points.csv, all 134 rows, in file order
    { "id": "thesis-r290-001", "T_evap_C": -30.22, "T_cond_C": 39.98, "N_rps": 30.0, "PR": 8.2267,
      "eta_v": 0.814, "eta_ov": 0.476, "f_loss": 0.213 }
  ],
  "check_points": [                              // evaluated by the library; the JS evaluator must reproduce them
    { "PR": 3.785, "N_rps": 70.0, "T_evap_C": 0.0,
      "eta_v": float, "eta_ov": float, "f_loss": float, "eta_is": float | null, "in_domain": true }
  ],
  "sources": {                                   // citation keys the page's References section resolves
    "data": "ossorio_thesis_appendix1",
    "same_tests": "ossorio_navarro_2023",
    "published_correlations": "ossorio_2024"
  }
}
```

## Evaluation rules (Python is the reference; JS must agree)

- `l = ln(PR)`; `t7 = [1, l, l*l, l*l*l, N, N*N, l*N]`.
- `eta_v = c_v · [...t7, T_evap]`, `eta_ov = c_ov · [...t7, T_evap]`, `f_loss = c_f · t7`.
- `eta_is = eta_ov / (1 - f_loss)`; `null` when `1 - f_loss <= 0`.
- Raw map values are shown and checked **as they are**. The library's cycle rule (a value above 1 is set to 1,
  a nonpositive value is infeasible; `_eval_eff`) is a separate fact the widget may annotate but never applies
  silently to the numbers it reports.
- `in_domain` = `N_rps` in `[30, 110]` **and** `(PR, T_evap)` inside the hull (library `MapDomain.contains`,
  `rtol = 0`). Points exactly on the boundary count as inside, as in the library.
- `PR <= 0` or non-finite input: no value (the library raises); JS returns `null`.

## Check points (the generator must include at least these)

| Case | Why it is there |
|---|---|
| (3.785, 70, 0) | the η_ov peak |
| (1.5, 70, 25) | η_v > 1 (≈ 1.015): the widget must show it raw |
| (1.0, 70, 0) | l = 0 |
| (1.2, 70, 0) | inside the speed range, outside the hull |
| (3.0, 15, 0) | below 30 rev/s (`rps_min` reaches here) |
| (3.0, 120, 0) | above 110 rev/s |
| (12.0, 70, -30) | high lift near the hull edge |
| one hull vertex | boundary counts as inside |
| (3.0, 70, -10) and (3.0, 70, 15) | the T_evap effect the module docstring quotes (0.62 → 0.69) |
| a grid of ≥ 20 further points | across PR 1.3–12, N 15–120, T_evap −30…25 |

## Agreement test

`tests/test_docs_compressor_map_data.py`:

1. The JSON's version, coefficients, domain and measured points equal the library and `points.csv`.
2. Regenerating the JSON reproduces the tracked file byte for byte.
3. `node` evaluates every check point through `compressor-map-model.js` and the result matches the JSON
   to relative 1e-9 (absolute 1e-12 near zero); `null` matches `null`; `in_domain` matches exactly.
   - `node` missing: skip with the reason **unless** `CI` is set, then fail. (GitHub-hosted Ubuntu runners
     ship `node`.)

## Widget conventions (from the compressor-map deck rules, 2026-11-23)

- Pressure ratio is written `r_p` (never PR or Π) in labels and text; speed `N`; evaporating temperature `T_evap`.
- Axis label = `Name (symbol) [unit]`; dimensionless quantities get no `[-]`.
- Colour roles: violet = a map of (r_p, N, T_evap) (η_v, η_ov, η_is); teal = a map of (r_p, N) (f_loss);
  orange = outside the measured data range only; blue sequential = efficiency magnitude in a heatmap;
  black/foreground diamond = the η_ov peak. Speed lines within one quantity are a single-hue ordinal ramp.
- Measured points sit at their measured coordinates, never jittered.
- Theme: Shibuya CSS variables (`--sy-c-*`) with fallbacks, as `cycle_widget.html`; light and dark both work.
- No CDN. D3 comes from the self-hosted bundle `docs/source/_static/js/lib/d3.v7.custom.min.js`
  (`D3_BUILD.md`); it lacks `contours` and the colour interpolators. Either extend the bundle by the
  documented procedure or draw those parts without D3.
- Width: works from 320 px to the content column; no horizontal page scroll.

## Out of scope for #253

The calibration section and its sliders (λ_v, κ_ov, λ_f, shape transforms) wait for #189.
