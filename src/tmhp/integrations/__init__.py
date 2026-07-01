"""Building-energy-simulation co-simulation adapters (#165).

Optional subpackage — each adapter needs an extra dependency that the core
``tmhp`` package does not require, so this package is **not** imported by
``tmhp/__init__.py``; ``import tmhp`` stays usable without those deps and only
``from tmhp.integrations... import ...`` pulls them in.

Adapters depend only on the public seams of the heat-pump models
(``AirSourceHeatPumpBoiler.step()`` / ``.analyze_steady()``), never on private
helpers:

* :mod:`tmhp.integrations.fmu` (#165 P1) — FMI 2.0 FMU wrapping ``step()``
  (the dynamic kernel; the FMU owns the storage-tank state). Needs ``pythonfmu``.
* :mod:`tmhp.integrations.fmu3` — FMI 3.0 Co-Simulation FMU wrapping the same
  ``step()`` seam. Needs ``pythonfmu3``.
* :mod:`tmhp.integrations.energyplus_plugin` (#165 P2) — EnergyPlus Python
  Plugin surrogating the ASHPB as a ``PlantComponent:UserDefined`` through
  ``analyze_steady()`` (the *steady-state* seam; EnergyPlus owns the
  ``WaterHeater:Mixed`` tank state). Runs inside EnergyPlus's embedded CPython,
  so it needs the bundled ``pyenergyplus`` (not pip-installable).
"""

from __future__ import annotations

__all__: list[str] = []

try:  # pragma: no cover - depends on the optional pythonfmu extra
    from .fmu import TmhpAshpbSlave  # noqa: F401 - conditional re-export

    __all__.append("TmhpAshpbSlave")
except ImportError:
    pass

try:  # pragma: no cover - depends on the optional pythonfmu3 extra
    from .fmu3 import TmhpAshpbFmi3Slave  # noqa: F401 - conditional re-export

    __all__.append("TmhpAshpbFmi3Slave")
except ImportError:
    pass

try:  # pragma: no cover - pyenergyplus is only importable inside EnergyPlus
    from .energyplus_plugin import (  # noqa: F401 - conditional re-export
        TmhpPlantInit,
        TmhpPlantSurrogate,
    )

    __all__ += ["TmhpPlantInit", "TmhpPlantSurrogate"]
except ImportError:
    pass
