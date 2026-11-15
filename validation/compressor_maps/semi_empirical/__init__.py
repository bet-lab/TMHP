"""Compressor-type-specific semi-empirical efficiency model: fitting pipeline and coefficient archive.

Standalone-compressor data only (no heat-pump catalogue numbers anywhere). Three types:

* ``scroll`` (no injection) -- form with a built-in volume ratio (``phi``),
* ``rotary_twin_inverter`` (the residential heat-pump default) and ``rotary_single_fixed`` (reference) -- one
  rotary shape (no built-in volume ratio) with a level per type; both *provisional* (see the archive README).

Injection compressors (Dardenne 2015, Tello 2017) are excluded. The drive (inverter) is modelled separately
(``inverter``) and divided out of every source whose power was measured at the inverter input, so the fitted
quantity is the terminal-basis overall isentropic efficiency.

Modules
    sources    one row per source: family, origin, power basis and its evidence, role in the fit
    assemble   the fit-ready table with exclusion bookkeeping (``build``)
    thermo     k, MEP_s, phi
    inverter   drive efficiency forms and their cross-validation
    model      eta_oi forms, eta_v form, shipped-form baseline; weighted Huber fits with unit levels
    cv         LOCO / LOSO / edge hold-outs / step slopes
    split      eta_is / eta_em split from measured discharge temperatures
    pipeline   backward elimination, speed coordinate, levels, checks
    verify     the prototype numbers (p01/p03) as a special case
    figures    archive figures
    run        writes ``validation/coefficients/semi-empirical-2026-11-15/``

Regenerate (deterministic for a given environment; about three minutes on 8 cores)::

    uv run python -m validation.compressor_maps.derive          # points_derived.csv (needs points_copeland_opi.csv)
    uv run python -m validation.compressor_maps.semi_empirical  # the archive

The Copeland points file is untracked (not redistributable); without it ``assemble`` stops with the command that
regenerates it.

Smoke run (no archive written; a few seconds after the table is built)::

    uv run python -c "
    from validation.compressor_maps.semi_empirical import assemble, verify
    d = assemble.build()
    print(d.use_oi.sum(), verify.reproduce(d)['lowlift'])"

prints the number of usable eta_oi rows (12,444) and the prototype's low-lift bias (+0.0192, RMSE 0.0400).
"""
