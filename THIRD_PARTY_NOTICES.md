# Third-party notices

TMHP is distributed under the MIT License (see `LICENSE`). The files listed
below contain or derive from software distributed under its own terms.

## EnergyPlus version 24.2.0 plant fixture

`tests/energyplus/plant.idf` adapts the EnergyPlus version 24.2.0
`testfiles/PlantLoadProfile.idf` example to a TMHP-controlled heating loop.
The fixture's copyright notice, conditions, and disclaimer are reproduced in
`tests/energyplus/ENERGYPLUS-LICENSE.txt`.

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

## Heat Pump Keymark records, via hplib

- Source: [hplib](https://github.com/FZJ-IEK3-VSA/hplib) (Forschungszentrum
  Juelich), `input/csv` at commit `125c2ef6c3fc38043368daac1352557fd2a90106`,
  doi:10.5281/zenodo.5521597. The CSV files republish the declared values of the
  Heat Pump Keymark certificates published by the European Heat Pump Association.
- License of the software and bundled documents: MIT. Copyright line (verbatim
  from the license):

  > Copyright (c) 2021 Tjarko Tjaden, Hauke Hoops, RE-Lab-Projects

  hplib's README places the database CSV files it builds from these records under
  CC BY 4.0.

TMHP contains no hplib code. It contains aggregate statistics computed from those
records (quantiles and ranges per test point, per-model ratios of declared COP):

| Path | Relation |
| --- | --- |
| `validation/data/keymark_en14825_summary.csv`, `validation/data/keymark_en14825_gradient.csv` | Quantiles of the declared COP of the air-to-water population, and of its COP(D)/COP(A) ratio. |
| `validation/data/keymark_a2a_declared.csv`, `validation/data/keymark_a2a_summary.csv` | Declared EN 14825 heating and cooling values of the air-to-air certificates. |
| `validation/data/auxiliary_power/keymark_pto_*.csv` | Values read from the same certificates for the auxiliary-power study. |

`validation/data/provenance/keymark_en14825_gradient.json` records the commit,
the archive checksum and the input checksum of the gradient file; the extraction
code is `validation/extraction/keymark_declared.py` and
`validation/extraction/keymark_gradient.py`. Whether the table rows of the
air-to-air files need more than this attribution is the maintainers' call.

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
