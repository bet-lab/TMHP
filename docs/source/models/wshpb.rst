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

Default sizing
==============

As for :doc:`gshpb`, ``hp_capacity`` (default 8 kW) and the refrigerant are
enough: ``UA_tank_hx = hp_capacity / 5``, ``UA_water = 0.18 × hp_capacity``,
source-loop flow ``dV_b_f_lpm = 0.75 × hp_capacity / (ρ c · 3 K)`` (3.58 L/min
per kW) and ``E_pmp = 75 W/ton × hp_capacity`` (21.3 W per kW). The rated cycle
is then the same at every capacity (W10/W35: COP of the cycle 5.3 with R410A
and the default efficiency map). The submerged source-water coil is ``H_b``
metres long: with none of ``N_1``, ``N_2``, ``H_b`` given it is 200 m, or as
long as the rated source-side heat (``0.75 × hp_capacity``) needs at 50 W/m.
Values you pass are used as given; the constructor warns (``UserWarning``) for
a coil above 80 W/m, a laminar default flow, and a ``T_tank_w_upper_bound``
whose condensing temperature (set point + ``hp_capacity / UA_tank_hx``) is at
or above the refrigerant's critical temperature.

The cycle closures are those of :doc:`gshpb`: the suction superheat is capped
at the evaporator approach less ``dT_hx_min``, and on a compressor speed bound
the tank heat exchanger is solved against the heat actually delivered
(``dT_ref_tank [K]``, ``tank_hx_residual [W]``).

Dynamic operation
=================

``analyze_dynamic`` follows the tank thermostat (``T_tank_w_lower_bound``
/ ``T_tank_w_upper_bound``, 55 / 65 °C) inside each step: a step reports
the fraction of it the compressor ran (``runtime_fraction [-]``), its
starts (``hp_starts [-]``), and heat, power and source-water heat averaged
over the step. ``min_on_time_s`` / ``min_off_time_s`` (default 0) hold
the compressor to a minimum run and rest time. See
:ref:`time-step-and-thermostat`.

``T_source_w_schedule`` is the source-water temperature of each step (default:
the constructor's ``Ts`` at every step); the row reports it as ``Ts [°C]`` and
``T_bhe [°C]``. The cycle of a step is solved against the fluid the coil
returns while it carries that step's own extraction from that step's water, so
in every running row ``T_ref_evap_sat < T_bhe_f_out_running < Ts``. (Earlier
releases fed the evaporator the previous step's fluid, and the first step the
constructor's ``Ts``.) An off row has no refrigerant state: the cycle
state-point columns are NaN.


Source-side mechanics
=====================

A single ε-NTU heat exchanger between the refrigerant evaporator
and the source-side water loop. No borehole transient — in
``analyze_steady`` the loop inlet temperature is whatever the user
supplies (``T_source``).

In ``analyze_dynamic`` the loop runs through a coil submerged in the source
water. A pipe of length ``H_b`` and per-length resistance ``R_b`` in water of
uniform temperature is a single-stream heat exchanger,
``Q = ε ṁc (T_water − T_in)`` with ``ε = 1 − exp(−H_b / (R_b ṁc))``, so the
fluid returns at ``T_in + Q / ṁc`` and never warmer than the water it takes
heat from (0.4 K below it for the default 8 kW machine, ``NTU`` 2.2).

Sink-side mechanics
===================

Shared DHW tank sink with an implicit per-step energy balance.

Every step reports the hot-water service: ``Q_dhw_request [W]`` is the
scheduled draw at the set point ``T_mix_w_out`` counted from the mains
temperature, ``Q_dhw_unmet [W]`` the part not delivered because the water
left the mixing valve below the set point (the tank was colder than it) or
a level-varying tank cut the draw. ``analyze_dynamic`` warns once
(``tmhp.dhw.DHWUnmetWarning``) when a run leaves demand unmet; see
:ref:`hot-water-service` for the run totals.

A tank whose level varies (``tank_always_full=False`` or
``prevent_simultaneous_flow``) follows the rules of the air-source boiler
(:doc:`ashpb`, Sink-side mechanics): the draw stops at ``TANK_LEVEL_MIN``,
the make-up at full, the reported level and tank flows are the ones the
step's energy balance was solved with, and the demand the tank could not
supply is ``dV_mix_w_out_unmet [m3/s]``. Such a run starts with
``tank_level_init`` of at least ``TANK_LEVEL_MIN``.

API reference
=============

.. automodule:: tmhp.water_source_heat_pump_boiler
    :members:
    :undoc-members:
    :show-inheritance:
