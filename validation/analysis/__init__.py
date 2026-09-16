"""Post-hoc analyses that explain the parity residuals.

`validation/extraction/` derives the defaults from evidence. This package asks
a different question: given those defaults, *why* does a particular machine
land where it lands? Nothing here feeds back into a default -- that separation
is deliberate, because an analysis that both explains a residual and adjusts a
parameter is a calibration loop wearing two hats.
"""
