.. Physics-Based Heat Pump Models documentation master file

=============================
Physics-Based Heat Pump Models
=============================

.. rst-class:: lead

   **First-principles dynamic models for air-source, ground-source, and water-source heat pump systems**

This Python library provides physics-based dynamic heat pump models that solve the thermodynamic refrigerant cycle at every time step using CoolProp. Unlike conventional empirical curve-fit approaches used in EnergyPlus or TRNSYS, these models enable evaluation across a wide range of refrigerants and operating conditions without manufacturer-specific data.

.. grid:: 2
    :gutter: 3

    .. grid-item-card:: 🚀 Getting Started
        :link: getting-started/index
        :link-type: doc

        Installation guide and your first simulation in minutes.

    .. grid-item-card:: 🔧 API Reference
        :link: api/index
        :link-type: doc

        Complete API documentation for all heat pump model classes and modules.

Why Physics-Based Models?
==========================

Conventional building energy simulation tools rely on empirical curve fits:

- ❌ **Limited operating range**: only valid for the manufacturer's test conditions
- ❌ **Refrigerant-locked**: cannot be adapted to alternative refrigerants
- ❌ **No cycle visibility**: the internal thermodynamic state is hidden

This library addresses all three limitations:

✓ **Broad validity**: solves the thermodynamic cycle from first principles at any operating condition

✓ **Refrigerant-agnostic**: supports any subcritical refrigerant via CoolProp (R32, R410A, R290, R134a, ...)

✓ **Full state visibility**: exposes every refrigerant state point at each time step

Models Available
=================

**Air-Source Heat Pump Boiler (ASHPB)**

* :class:`~air_source_heat_pump_boiler.AirSourceHeatPumpBoiler` — Full dynamic model with ε-NTU condenser and optimal evaporating temperature search
* :class:`~ashpb_stc_preheat.ASHPB_STC_preheat` — ASHPB with solar thermal collector preheat
* :class:`~ashpb_stc_tank.ASHPB_STC_tank` — ASHPB + STC with stratified storage tank
* :class:`~ashpb_pv_ess.ASHPB_PV_ESS` — ASHPB + PV + Energy Storage System

**Ground-Source Heat Pump Boiler (GSHPB)**

* :class:`~ground_source_heat_pump_boiler.GroundSourceHeatPumpBoiler` — Full dynamic model with g-function borehole
* :class:`~gshpb_stc_preheat.GSHPB_STC_preheat` — GSHPB with STC preheat
* :class:`~gshpb_stc_tank.GSHPB_STC_tank` — GSHPB + STC with stratified tank
* :class:`~gshpb_pv_ess.GSHPB_PV_ESS` — GSHPB + PV + ESS

**Water-Source Heat Pump Boiler (WSHPB)**

* :class:`~water_source_heat_pump_boiler.WaterSourceHeatPumpBoiler` — Dynamic WSHPB model

Validation
===========

The ASHPB model has been validated against commercial catalogue data (Samsung EHS 14 kW) at **15 operating points** (LWT 40/50/65°C × outdoor −10 to 30°C):

.. list-table::
   :header-rows: 1
   :widths: 40 30

   * - Metric
     - Value
   * - Mean Absolute Error (MAE)
     - 0.354
   * - Mean Absolute Percentage Error (MAPE)
     - 10.09 %

.. toctree::
   :maxdepth: 2
   :caption: Documentation
   :hidden:

   getting-started/index
   api/index

.. toctree::
   :maxdepth: 1
   :caption: Project Links
   :hidden:

   GitHub Repository <https://github.com/bet-lab/physics_hp_models>
   enex_analysis_engine <https://github.com/bet-lab/enex_analysis_engine>

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
