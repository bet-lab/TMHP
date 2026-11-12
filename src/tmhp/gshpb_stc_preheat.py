"""GSHPB with a SolarThermalCollector — mains preheat placement.

The collector sits in the mains line that feeds the tank; the mixing valve's
cold side takes mains water directly. See :mod:`tmhp._stc_scenarios` (shared
with the ASHPB version).

Usage
-----
::

    from tmhp import SolarThermalCollector, GSHPB_STC_preheat

    stc = SolarThermalCollector(A_stc=4.0, start_time="2026-06-01")
    model = GSHPB_STC_preheat(stc=stc)
    df = model.analyze_dynamic(...)
"""

from __future__ import annotations

from ._run_reset import GroundSourceHeatPumpBoilerRun
from ._stc_scenarios import STCPreheat


class GSHPB_STC_preheat(STCPreheat, GroundSourceHeatPumpBoilerRun):
    """:class:`~tmhp.GroundSourceHeatPumpBoiler` + :class:`~tmhp.subsystems.SolarThermalCollector` in *mains preheat* placement.

    The behaviour and energy columns are those of
    :class:`tmhp._stc_scenarios.STCPreheat`: the collector runs at the tank's
    make-up flow and ``Q_stc_w_out - Q_stc_w_in = Q_solar_tank`` is the heat the
    preheated inflow brings into the tank.

    Parameters
    ----------
    stc : SolarThermalCollector
        Collector, site and clock (its pump power is set to 0 on a copy: the
        mains pressure drives the flow).
    **kwargs
        Forwarded to :class:`~tmhp.GroundSourceHeatPumpBoiler`.

    Raises
    ------
    TypeError
        If *stc* is not a :class:`SolarThermalCollector` instance.
    """
