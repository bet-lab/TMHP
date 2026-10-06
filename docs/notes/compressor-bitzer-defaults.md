# BITZER three-efficiency defaults (2026-10-06)

The production compressor defaults follow [Compressor 3 efficiency](https://app.notion.com/p/3ee6947d125d80f4830ffcb408fa647d): the archived BITZER scroll fit evaluates each of volumetric, isentropic and effective electro-mechanical efficiency independently as

`eta = b0 + b1*PR + b2*N + b3*PR**2 + b4*N**2 + b5*PR*N`.

`N` is actual shaft speed in rev/s, directly passed by every model and the reference-state initializer. `rps_rated` is used only when a factory callable is invoked without a speed; it never rescales an explicitly supplied speed. There is no BASE/REL normalization, product splitting, efficiency clipping or boundary hold. Scalar and custom callable overrides remain supported. Reference-state mass-flow normalization for the ground heat exchanger remains independent of this compressor input convention.

The coefficients are frozen from the Notion study's `data/three_efficiency_surfaces.json`, scroll group (3,899 points). The complete fit, convex hulls, coefficients CSV and refit audit are under `validation/coefficients/BITZER-three-C3-absolute-N-2026-10-06/`. The study uses manufacturer calculations at superheat/subcooling 10/0 K, not experimental logs; effective eta_em denotes refrigerant enthalpy rise divided by input power.

The raw polynomial is allowed to extrapolate beyond the data hull (N=33.83–72.50 rev/s). Heat-pump states still require finite efficiencies in (0,1]. In particular the 150 rev/s default search ceiling is outside the data and may give invalid efficiencies. The speed solver uses the source study's capacity-peak search and lower root when the high endpoint is invalid or the duty turns down. It reports failure if the low endpoint is invalid. This preserves the study's actual policy rather than inventing a low-speed hold or efficiency floor. Some high-lift boiler conditions are therefore rejected; the old model's full-envelope validity guarantees do not apply.

The 2026-09-24 correlations are retained in `tmhp.compressor_efficiency_legacy` for historical reproductions. Historical reports and plots merged from the validation branch describe their archived fit, not new BITZER predictions. Fresh parity/seasonal results must be regenerated before claiming those historical metrics for BITZER.

The current capacity-scaled displacement, automatic rating state, independent fan-flow limits and ground-loop implementations from main were retained where the older validation branch conflicted. The validation branch's refrigerant-aware displacement helper is available as `refrigerant_aware_displacement`; changing production sizing is outside this compressor-logic update.
