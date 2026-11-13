# Third-party notices

TMHP is distributed under the MIT License (see `LICENSE`). The files listed
below contain or derive from software distributed under its own terms.

## geolink

- Project: geolink (bet-lab/geolink), a ground thermal response research library
- License: MIT
- Copyright line (verbatim from the license):

  > Copyright (c) 2026 betlab

TMHP files that derive from geolink:

| Path | Relation |
| --- | --- |
| `src/tmhp/borehole_capacity.py` | The equivalent-cylinder borehole section (`EquivalentCylinderBorehole`, `CapacityStepResponse`, the finite-volume grid and backward-Euler march, the analytical line-source and hollow-cylinder wall responses) is ported from `src/geolink/borehole/equivalent_cylinder.py` at revision `4ff6238`: conductivity mapping only, with the target resistance supplied by the caller. The fluid-transit response, the dynamic g-function and the model hooks in the same file are original to TMHP. |

No other geolink source is included. `src/tmhp/integrations/geolink.py` calls
geolink at run time when it is installed and contains none of its code.

Full license text of geolink:

```
MIT License

Copyright (c) 2026 betlab

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Methods implemented from the literature (no code included)

- **Equivalent-radius mapping** of the borehole section, `r_eq = sqrt(n) r_po`,
  after Xu and Spitler (2006). GHEDesigner (BSD-3-Clause) implements the same
  mapping; geolink compared its implementation with GHEDesigner's short
  time-step response. Neither geolink's ported file nor TMHP contains
  GHEDesigner code.
- **Multipole borehole resistances, g-functions and pipe networks** are
  computed by calling [pygfunction](https://github.com/MassimoCimmino/pygfunction)
  (BSD-3-Clause), a run-time dependency. geolink's own port of pygfunction's
  multipole and U-tube models was *not* brought into TMHP.
