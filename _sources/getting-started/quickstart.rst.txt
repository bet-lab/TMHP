Quick Start
===========

Running your first ASHPB simulation
-------------------------------------

The example below runs a single steady-state evaluation of
``AirSourceHeatPumpBoiler.analyze_steady(...)``. It evaluates the refrigerant
cycle at a fixed operating point — tank water at 55 °C, outdoor air at 5 °C,
target condenser heat 8 kW — without solving the tank energy balance.

.. code-block:: python

   from physics_hp import AirSourceHeatPumpBoiler

   ashpb = AirSourceHeatPumpBoiler(ref="R32")
   result = ashpb.analyze_steady(T_tank_w=55.0, T0=5.0, Q_ref_cond=8_000.0)

   print(f"COP (refrigerant) : {result['cop_ref [-]']:.2f}")
   print(f"COP (system)      : {result['cop_sys [-]']:.2f}")
   print(f"Heating capacity  : {result['Q_ref_cond [W]'] / 1e3:.2f} kW")
   print(f"Compressor power  : {result['E_cmp [W]'] / 1e3:.2f} kW")
   print(f"Evap. sat. temp.  : {result['T_ref_evap_sat [°C]']:.1f} °C")
   print(f"Cond. sat. temp.  : {result['T_ref_cond_sat_v [°C]']:.1f} °C")

``analyze_steady`` returns a flat ``dict`` whose keys carry the unit in
brackets (e.g. ``"E_cmp [W]"``). Pass ``return_dict=False`` to get a single-row
``pandas.DataFrame`` with the same columns instead.

For time-stepping simulations use :py:meth:`AirSourceHeatPumpBoiler.analyze_dynamic`,
which consumes a weather/load DataFrame and returns a per-step DataFrame.
