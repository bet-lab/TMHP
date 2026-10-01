# Random operating-point sample (one CSV per compressor-efficiency case)

10 000 operating points drawn at random over a wide envelope and solved once
per case in `validation/compressor_efficiency_sensitivity_simple` -- `BASE`
(η_v 0.90, η_is 0.70, η_em 0.90 held constant) plus the six single-efficiency
sensitivity cases (one compressor efficiency replaced by a speed- or
pressure-ratio-driven shape, the other two left at BASE). The point is
coverage: that study fixes the boundary air temperatures and varies only PLR,
so its two operating lines say nothing about the rest of the envelope.

| Input | Distribution |
| --- | --- |
| Requested PLR | uniform 0.10 – 1.50 of the 3.5 kW nameplate |
| Outdoor air `T0` | uniform −20 – 40 °C |
| Room air `T_a_room` | uniform 15 – 30 °C |

All three are independent, and the **same draw** (fixed by `SEED` in
`config.py`) is reused for every case, so the seven CSVs line up row-for-row
by `point_id` and differ only in which efficiency function was applied.

The duty follows the boundary temperatures -- outdoor colder than the room is
heating, outdoor warmer is cooling -- because requesting the opposite sign
makes the refrigerant cycle infeasible and the model returns `cycle_invalid`.

PLR is referred to the **fixed nameplate**, not to the capacity available at
that ambient: `Q_request = 3500 × PLR`. A high PLR at a low ambient is
therefore expected to come back `capacity_clamped == "max"`, and that limit is
part of the result.

## File naming

`uniform_<Nk>_points_<case_slug>.csv`, with a `.parameters.json` of the same
stem next to it. `<Nk>` is the point count (`10k` for the default 10 000).
`<case_slug>` is `CASE_SLUG` in `config.py`:

| Case | Slug | Varied efficiency | Driver |
| --- | --- | --- | --- |
| `BASE` | `default_cmp_eff` | none (all three constant) | -- |
| `N-V` | `etav_nstar` | η_v (volumetric) | rotor speed `n*` |
| `N-I` | `etais_nstar` | η_is (isentropic) | rotor speed `n*` |
| `N-E` | `etaem_nstar` | η_em (electromechanical) | rotor speed `n*` |
| `P-V` | `etav_pr` | η_v | pressure ratio `P_r` |
| `P-I` | `etais_pr` | η_is | pressure ratio `P_r` |
| `P-E` | `etaem_pr` | η_em | pressure ratio `P_r` |

The name alone says count, distribution family (`uniform`) and case -- no
need to open the file or its `.parameters.json` to know what it is.

## What is kept

Every point is written, clamped and failed ones included. **Exergy is not
computed** for this archive (`analyze_steady(..., postprocess=False)`), so no
`X_...`/`Xc_...` columns appear -- an explicit exception to the rest of the
codebase, which always reports exergy alongside energy.

| Column | Meaning |
| --- | --- |
| `case` | one of `BASE`, `N-V`, `N-I`, `N-E`, `P-V`, `P-I`, `P-E` (matches the CSV's own slug) |
| `modulating` | met the request without hitting a speed limit (`capacity_clamped is None and failure_reason == "none"`) |
| `capacity_clamped` | `"min"` — pinned at `rps_min`, over-delivers; `"max"` — pinned at `rps_max`, under-delivers |
| `failure_reason` | the model's own verdict; `"exception: …"` if the call raised |
| `dT_boundary_K` | `T_iu − T_ou`. Points where this is near zero have almost no lift and an unrepresentatively high COP — the flag is kept so they can be filtered, they are **not** removed |

The harness columns above come first, then every key
`AirSourceHeatPump.analyze_steady` returns (minus exergy), under its own name.

## Run

```bash
# 200-point dry run of one case into a scratch directory
uv run python3 -m validation.operating_point_sample.sample --n 200 --case BASE --out-dir /tmp/dry
# one case against the full sample
uv run python3 -m validation.operating_point_sample.sample --case N-I
# all seven cases (~60-70 min on 30 workers)
uv run python3 -m validation.operating_point_sample.sample
```

Output: `validation/data/operating_point_sample/uniform_10k_points_<slug>.csv`
and `<same stem>.parameters.json` per case. The sample is fixed by `SEED` in
`config.py`, so every CSV rebuilds bit-for-bit; each is tens of MB and
gitignored, only the `.parameters.json` files -- the sampling spec, the
model inputs, the case's efficiency function and the outcome counts -- are
tracked.

`src/tmhp` is untouched; the efficiencies are passed to the constructor as
`(P_r, rps)` callables, exactly as in the sensitivity study.
