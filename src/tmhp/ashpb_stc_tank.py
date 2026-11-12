"""ASHPB with a SolarThermalCollector — pumped collector loop charging the tank.

The collector loop draws tank water, heats it and returns it to the tank. The
loop is run by a differential controller with a storage high limit
(``T_stc_tank_max``, 85 °C by default) and boiling protection; see
:mod:`tmhp._stc_scenarios` (shared with the GSHPB version).

Usage
-----
::

    from tmhp import SolarThermalCollector, ASHPB_STC_tank

    stc = SolarThermalCollector(A_stc=4.0, start_time="2026-06-01")
    model = ASHPB_STC_tank(stc=stc)
    df = model.analyze_dynamic(...)
"""

from __future__ import annotations

from ._stc_scenarios import STCTankLoop
from .air_source_heat_pump_boiler import AirSourceHeatPumpBoiler


class ASHPB_STC_tank(STCTankLoop, AirSourceHeatPumpBoiler):
    """:class:`~tmhp.AirSourceHeatPumpBoiler` + :class:`~tmhp.subsystems.SolarThermalCollector` in *tank-loop* placement.

    The behaviour and energy columns are those of
    :class:`tmhp._stc_scenarios.STCTankLoop`: per step
    ``Q_sol_stc = (Q_stc_w_out - Q_stc_w_in) + Q_l_stc`` and
    ``Q_stc_w_out - Q_stc_w_in = Q_solar_tank``; ``E_tot`` includes ``E_stc_pump``.

    Parameters
    ----------
    stc : SolarThermalCollector
        Collector, loop pump and controller settings, site and clock.
    **kwargs
        Forwarded to :class:`~tmhp.AirSourceHeatPumpBoiler`.

    Raises
    ------
    TypeError
        If *stc* is not a :class:`SolarThermalCollector` instance.
    """
