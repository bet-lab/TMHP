# BITZER three-efficiency defaults (2026-10-06)

Update (2026-11-02): `AirSourceHeatPump` now uses the Ossorio 2024 R290 research
maps for omitted efficiency inputs. The remaining ground/boiler model defaults
and the BITZER factory functions described below are unchanged. ASHP callers can
still select this archived BITZER behaviour through explicit `eta_cmp_vol`,
`eta_cmp_isen`, and `eta_cmp` overrides. The new ASHP default is a research-map
transfer assumption for other refrigerants or compressor technologies.

The BITZER compressor defaults follow [Compressor 3 efficiency](https://app.notion.com/p/3ee6947d125d80f4830ffcb408fa647d): the archived BITZER scroll fit evaluates each of volumetric, isentropic and effective electro-mechanical efficiency independently as

`eta = b0 + b1*PR + b2*N + b3*PR**2 + b4*N**2 + b5*PR*N`.

`N` is actual shaft speed in rev/s, directly passed by every model and the reference-state initializer. `rps_rated` is used only when a factory callable is invoked without a speed; it never rescales an explicitly supplied speed. There is no BASE/REL normalization or product splitting. Scalar and custom callable overrides remain supported. Reference-state mass-flow normalization for the ground heat exchanger remains independent of this compressor input convention.

The coefficients are frozen from the Notion study's `data/three_efficiency_surfaces.json`, scroll group (3,899 points). The complete fit, convex hulls, coefficients CSV and refit audit are under `validation/coefficients/BITZER-three-C3-absolute-N-2026-10-06/`. The study uses manufacturer calculations at superheat/subcooling 10/0 K, not experimental logs; effective eta_em denotes refrigerant enthalpy rise divided by input power.

Since 2026-11-12 (issue #96) the polynomials are guarded. Every evaluated efficiency satisfies 0 < eta <= 1: values above one are set to one, nonpositive or nonfinite values make the state infeasible. The fit is evaluated as is inside its data box (`VALIDITY_DOMAIN`: PR 1.50–7.15, N 33.83–72.50 rev/s, from the scroll hull). Above it in PR or N the boundary value is held, because the quadratic terms alone drive eta_is negative by PR 9 and at N 150. Below it (PR down to 1, N down to 15 rev/s, held below) the fit continues along its boundary slope only where that lowers the efficiency, so over-compression and low-speed leakage are not dropped and no bonus is awarded outside the data. `efficiency_extrapolated()` reports points outside the box and `.raw()` returns the unguarded polynomial. Discharge-temperature limits are not part of this guard.

The 2026-09-24 correlations are retained in `tmhp.compressor_efficiency_legacy` for historical reproductions. Historical reports and plots merged from the validation branch describe their archived fit, not new BITZER predictions. Fresh parity/seasonal results must be regenerated before claiming those historical metrics for BITZER.

The current capacity-scaled displacement, automatic rating state, independent fan-flow limits and ground-loop implementations from main were retained where the older validation branch conflicted. The validation branch's refrigerant-aware displacement helper is available as `refrigerant_aware_displacement`; changing production sizing is outside this compressor-logic update.
