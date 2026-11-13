"""Source-loop fluids for tests whose subject is not the freezing limit.

Many scenarios written before the limit existed circulate "water" at or below
0 °C: the brine rating points (liquid entering at 0 °C), boreholes loaded far
beyond their design, a 0 °C borehole wall. They test the cycle closure, the
compressor envelope or the ground coupling, not the loop liquid. They keep
their numbers by running on :data:`ANTIFREEZE_WITH_WATER_PROPERTIES`: a
:class:`~tmhp.source_fluid.SourceFluid` with the properties of water and a
freezing point far below any of them. ``tests/test_source_fluid_freezing.py``
covers the limit itself and real mixtures.
"""

from tmhp.constants import c_w, k_w, mu_w, rho_w
from tmhp.source_fluid import SourceFluid

#: Water's properties, freezing at -60 °C (operating limit -57 °C).
ANTIFREEZE_WITH_WATER_PROPERTIES = SourceFluid("antifreeze with the properties of water", -60.0, rho_w, c_w, mu_w, k_w)
