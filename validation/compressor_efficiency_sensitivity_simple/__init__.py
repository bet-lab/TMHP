"""Simplified compressor-efficiency sensitivity of the fixed-boundary PLR–COP curve.

Seven cases per duty.  ``BASE`` holds the three compressor efficiencies at
representative constants (η_v 0.90, η_is 0.70, η_em 0.90).  Each of the six
sensitivity cases gives *one* efficiency a relative multiplier in *one*
driver -- relative speed ``n* = N / N_rated`` (centre 1.00, the rated point)
or pressure ratio ``P_r`` (centre 2.00) -- and keeps the other two at their
BASE constants.  η_is and η_em take the ∩ quadratic ``1 − 0.60 (x − x_c)²``;
η_v takes the linear decrease ``1 − 0.10 (x − x_c)`` so that the delivered
flow stays monotonic in speed (see ``functions``).

No reference point is read from the shipped model, no anchors are derived
from a control run, and the shipped correlations (``CURRENT``) are not part of
the comparison.  The functions are perturbations for a sensitivity test, not
candidate correlations; ``src/tmhp`` defaults are untouched and every case is
passed to :class:`tmhp.AirSourceHeatPump` as efficiency callables.

The earlier anchored study lives, archived, in
``validation/compressor_efficiency_sensitivity``.
"""
