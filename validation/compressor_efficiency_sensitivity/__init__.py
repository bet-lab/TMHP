"""Controlled sensitivity of the fixed-boundary PLR-COP curve to each compressor efficiency.

One efficiency at a time (``eta_cmp_vol`` / ``eta_cmp_isen`` / ``eta_cmp``) is given
a synthetic cap-shaped (∩) dependence on either the relative speed ``n* = rps /
rps_rated`` or the pressure ratio, with the same normalised penalty in every
case (10 % at the low end, 5 % at the high end); the other two are held at the
constant reference level read from the shipped model at PLR 0.65.  Two controls
frame the six cases: ``C0`` (all three constant) and ``CURRENT`` (the shipped
correlations, :data:`tmhp.compressor_efficiency.COEFFICIENT_VERSION`).

Nothing here is a candidate correlation.  The curves are perturbations built to
separate *which* efficiency, and *which* driver, moves the system-level PLR-COP
curve; the production defaults in ``src/tmhp`` are not touched -- every case is
passed to the model through its efficiency-callable arguments.

Run::

    uv run python3 -m validation.compressor_efficiency_sensitivity.sweep
    uv run python3 -m validation.compressor_efficiency_sensitivity.figures
"""
