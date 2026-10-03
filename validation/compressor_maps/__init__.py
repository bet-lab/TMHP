"""Standalone-compressor performance maps -> TMHP compressor-efficiency defaults.

Level 1 of the validation ladder.  Everything here works on *compressor*
measurements (manufacturer performance tables, open datasets), never on
heat-pump catalogue COP, so that the three efficiency correlations are
identified by the component they describe and the heat-pump catalogues stay
an independent system-level check.

Sub-modules
-----------
schema      normalised point record + CSV I/O
fetch       Chrome-bridge fetchers (documents only; nothing is parsed here)
parse       one module per vendor table layout -> schema rows
derive      CoolProp: PR, suction density, eta_vol, eta_oi and its split
fit         candidate correlation families, pooled robust least squares
cv          leave-one-compressor-out cross-validation and model selection
emit_coefficients   writes validation/coefficients/<version>/ and regenerates
                    the generated block in src/tmhp/compressor_efficiency.py
"""
