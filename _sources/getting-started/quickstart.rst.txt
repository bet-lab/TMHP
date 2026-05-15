Quick Start
===========

Running your first ASHPB simulation
-------------------------------------

The example below runs a single steady-state evaluation of the ``AirSourceHeatPumpBoiler`` model at a winter operating condition (outdoor −10°C, LWT 65°C):

.. code-block:: python

   import sys
   sys.path.insert(0, ".")  # Add repo root (for standalone use)

   from air_source_heat_pump_boiler import AirSourceHeatPumpBoiler

   # --- Initialize model ---
   ashpb = AirSourceHeatPumpBoiler(refrigerant="R32")

   # --- Set operating boundary conditions ---
   ashpb.T_amb    = -10.0   # Outdoor air temperature [°C]
   ashpb.T_w_tank = 65.0    # Target leaving water temperature [°C]

   # --- Run one time step (60-second resolution) ---
   result = ashpb.step(dt=60)

   print(f"COP               : {result.COP:.2f}")
   print(f"Heating capacity  : {result.Q_cond / 1e3:.2f} kW")
   print(f"Compressor power  : {result.W_cmp  / 1e3:.2f} kW")
   print(f"Evap. sat. temp.  : {result.T_evap_sat:.1f} °C")
   print(f"Cond. sat. temp.  : {result.T_cond_sat:.1f} °C")

When used as a submodule of ``enex_analysis_engine``:

.. code-block:: python

   from enex_analysis import AirSourceHeatPumpBoiler

   ashpb = AirSourceHeatPumpBoiler(refrigerant="R32")
   ashpb.T_amb    = -10.0
   ashpb.T_w_tank = 65.0
   result = ashpb.step(dt=60)
