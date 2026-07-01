===========
Quick start
===========

This page walks through a single steady-state evaluation of the
``AirSourceHeatPumpBoiler`` model — the cheapest call you can make
against the library, and the fastest way to confirm your install
works. Once this runs, move on to the time-stepping flow in
:doc:`first-dynamic-simulation`.

The example uses ASHPB because it is the validated reference case and
its inputs are easy to inspect: tank temperature, outdoor air
temperature, and condenser duty. The same refrigerant argument, COP
fields, and diagnostic semantics carry over to the other cycle-resolved
source/sink model families. The load inputs and heat-duty output names
are model-specific: boilers use tank charge, while ASHP/GSHP use the
indoor-unit load ``Q_r_iu``.

A single steady-state operating point
=====================================

``analyze_steady`` evaluates the refrigerant cycle at one fixed
operating point — tank water at 55 °C, outdoor air at 5 °C, target
condenser duty 8 kW — without solving the tank energy balance.

.. code-block:: python

   from tmhp import AirSourceHeatPumpBoiler

   ashpb = AirSourceHeatPumpBoiler(ref="R32")
   result = ashpb.analyze_steady(
       T_tank_w=55.0,
       T0=5.0,
       Q_ref_tank=8_000.0,
   )

   print(f"COP (refrigerant) : {result['cop_ref [-]']:.2f}")
   print(f"COP (system)      : {result['cop_sys [-]']:.2f}")
   print(f"Heating capacity  : {result['Q_ref_tank [W]'] / 1e3:.2f} kW")
   print(f"Compressor power  : {result['E_cmp [W]'] / 1e3:.2f} kW")
   print(f"Evap. sat. temp.  : {result['T_ref_evap_sat [°C]']:.1f} °C")
   print(f"Cond. sat. temp.  : {result['T_ref_cond_sat_v [°C]']:.1f} °C")

``analyze_steady`` returns a flat ``dict`` whose keys carry their
units in brackets (for example ``E_cmp [W]``). Pass
``return_dict=False`` to get a single-row ``pandas.DataFrame`` with
the same columns instead.

Swapping the refrigerant
========================

The refrigerant is just a constructor argument; no recalibration is
required. Any fluid CoolProp recognises works:

.. code-block:: python

   from tmhp import AirSourceHeatPumpBoiler

   AirSourceHeatPumpBoiler(ref="R290")     # propane
   AirSourceHeatPumpBoiler(ref="R744")     # CO₂ (transcritical)
   AirSourceHeatPumpBoiler(ref="R410A")
   AirSourceHeatPumpBoiler(ref="R134a")

Next steps
==========

.. container:: next-steps

   :doc:`Run a 24-hour dynamic simulation <first-dynamic-simulation>` — step ``analyze_dynamic`` through outdoor and demand schedules over a full day.

   :doc:`Browse the full API <../api/index>` — every model class, subsystem, and helper module documented in one place.
