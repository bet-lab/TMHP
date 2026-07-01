=====================================
Water-source heat pump boiler (WSHPB)
=====================================

.. |wshpb| raw:: html

   <span class="glossary" data-term="wshpb">WSHPB</span>

Source side is a water loop with a prescribed inlet temperature;
sink side is the shared DHW tank demand model used by the boiler
families.

Overview
========

The class is :class:`tmhp.WaterSourceHeatPumpBoiler`. Unlike GSHPB,
|wshpb| takes the source-side inlet temperature as a schedule input
rather than computing it from a borehole field — useful when the
water loop is driven by an external simulation or measurement.

Base usage
==========

.. code-block:: python

   from tmhp import WaterSourceHeatPumpBoiler

   wshpb = WaterSourceHeatPumpBoiler(ref="R134a")

   result = wshpb.analyze_steady(
       T_tank_w=55.0,
       T_source=15.0,     # water-loop inlet [°C]
       Q_ref_tank=8_000,
   )

Source-side mechanics
=====================

A single ε-NTU heat exchanger between the refrigerant evaporator
and the source-side water loop. No borehole transient — the loop
inlet temperature is whatever the user supplies.

Sink-side mechanics
===================

Shared DHW tank sink with an implicit per-step energy balance.

API reference
=============

.. automodule:: tmhp.water_source_heat_pump_boiler
    :members:
    :undoc-members:
    :show-inheritance:
