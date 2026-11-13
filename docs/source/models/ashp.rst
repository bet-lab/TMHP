================================================
Air-source heat pump (ASHP — space conditioning)
================================================

.. |ashp| raw:: html

   <span class="glossary" data-term="ashp">ASHP</span>

|ashp| conditions a building zone (heating + cooling) rather than
charging a DHW tank. The refrigerant cycle and outdoor-coil source
side are shared with the air-source boiler family; what differs is
the demand side — a zone energy balance instead of a tank.

Overview
========

The class is :class:`tmhp.AirSourceHeatPump`. Use it when the heat
pump's job is space conditioning rather than DHW production.

Base usage
==========

.. code-block:: python

   from tmhp import AirSourceHeatPump

   ashp = AirSourceHeatPump(ref="R32")

   # See API reference below for the full constructor and
   # analyze_steady / analyze_dynamic signatures.

Source-side mechanics
=====================

Outdoor coil with variable-speed fan and an ε-NTU air-side heat
exchanger — the shared air-source environmental-side model.

Sink-side mechanics
===================

A zone temperature / load proxy stands in for the building. The
caller supplies indoor-unit load as ``Q_r_iu``: positive values
select cooling, negative values select heating, and zero values
represent off operation. There is no tank energy balance.

Indoor airflow
==============

The rated indoor airflow defaults to ``5.37e-5 m³/s`` per watt of
``hp_capacity`` — 400 cfm per ton, 193 m³/h per kW, the airflow at which unitary
equipment is rated (``tmhp.hx_fan.INDOOR_AIRFLOW_PER_W``). Pass
``dV_iu_fan_a_rated`` when the unit's own figure is known; wall-mounted split
units publish 170 to 310 m³/h per kW at their high fan speed.

The airflow decides the supply-air temperature and, through the indoor coil's
approach, most of the temperature lift. At the default a unit delivering its
nameplate capacity supplies air about 15 K from the room: 35 °C in heating at a
20 °C room, 12 °C in cooling at a 27 °C room, with the refrigerant saturated
17 K from the room air. (The earlier default, 720 m³/h per kW, moved four times
that air: supply air 5 K from the room, a condensing temperature near 30 °C,
and a rated COP above what such equipment declares.) The indoor fan draws about
0.5 % of the nameplate capacity at rated flow, both fans together 1.3 to 2.5 %.

Because the fan power is small against the compressor's, the least-power
operating point keeps the indoor fan at its largest airflow at every load; the
supply-air temperature therefore approaches the room temperature at part load.
A unit that slows its fan for comfort is described with ``dV_iu_fan_a_max``.

.. note::

   The approach-temperature search domain (``dT_approach_bounds``, 1–20 K) is
   close to the 17 K the indoor coil runs at nameplate capacity, so in mild
   weather it caps the delivered heat near 120 % of the nameplate
   (``capacity_clamped == "max"``, ``capacity_binding == "cycle"``). That cap
   is the search box, not the machine: widen the bounds — the validation
   harness uses ``(1, 30)`` — when larger duties are to be explored.

Part load: cycling and unmet load
=================================

The compressor modulates between ``rps_min`` and ``rps_max``. A request outside
what the unit delivers over that range is still answered, and the row says how.

**Below the minimum capacity** the compressor cycles on and off, and the row is
the mean over the cycling period (``capacity_clamped == "min"``):

- ``Q_ref_iu [W]`` equals the request. ``Q_min [W]`` is what the unit delivers
  running continuously at its minimum speed, and ``capacity_ratio [-]`` is the
  request divided by it (the *CR* of EN 14825).
- The electrical input carries the EN 14825 degradation for a unit delivering
  to air: ``COP = COP_min · (1 − Cd · (1 − CR))`` with ``Cd = 0.25``, the value
  the standard prescribes when a unit has not been tested for it.
  ``part_load_factor [-]`` is that bracket. It applies to the refrigeration
  machine — compressor and outdoor fan — so ``cop_ref [-]`` is the continuous
  value times it, and ``cop_sys [-]`` falls slightly less (about one percent)
  because the indoor fan is not degraded.
- ``runtime_fraction [-]`` is the share of the time the compressor runs,
  ``CR / part_load_factor``. Compressor and outdoor-fan power (``E_cmp``,
  ``E_ou_fan``) are the running values times that fraction. Heat and flow
  rates (``Q_ref_iu``, ``Q_ref_ou``, ``Q_a_iu_in``, ``Q_a_iu_out``,
  ``m_dot_ref``, ``dV_iu_a``, ``dV_ou_a``) are the running values times *CR*,
  and so is the indoor fan's power (``E_iu_fan``): it runs while heat is
  delivered. The refrigerant cycle and both air streams thus stay in balance
  with the delivered heat, and the room still receives the coil duty plus the
  indoor fan's heat. Temperatures, pressures, compressor speed and
  efficiencies are those of the running machine.
- ``E_cycling_loss [W]`` is the mean electrical power the compressor and the
  outdoor fan draw beyond *CR* times their continuous power — the cost of the
  starts.

.. list-table:: EN 14825 degradation with ``Cd = 0.25``
   :header-rows: 1
   :widths: 22 24 26 28

   * - ``capacity_ratio``
     - ``part_load_factor``
     - ``runtime_fraction``
     - Cycling loss / continuous power
   * - 0.1
     - 0.775
     - 0.129
     - 0.029
   * - 0.2
     - 0.800
     - 0.250
     - 0.050
   * - 0.4
     - 0.850
     - 0.471
     - 0.071
   * - 0.6
     - 0.900
     - 0.667
     - 0.067
   * - 0.8
     - 0.950
     - 0.842
     - 0.042
   * - 1.0
     - 1.000
     - 1.000
     - 0.000

A request of a tenth of the minimum capacity is thus met at 77.5 % of the
continuous minimum-speed COP, with the compressor running 12.9 % of the time.
Without this model every request below the floor was answered with the minimum
capacity itself — about half the nameplate for the default unit in mild
weather — whatever was asked for. ``Cd_cycling`` sets the coefficient: a number
replaces 0.25, ``0`` is loss-free cycling, and ``None`` restores the earlier
behaviour (continuous operation at the minimum speed, ``Q_ref_iu [W]`` above
the request, ``runtime_fraction [-]`` of 1). The operating-point search weighs
the degradation, so the minimum-speed point may sit a few watts from the one
found with ``Cd_cycling=None``.

**Above the maximum capacity** the unit runs flat out
(``capacity_clamped == "max"``): ``Q_ref_iu [W]`` is the ceiling,
``Q_max [W]`` repeats it, ``capacity_binding`` names what set it and
``Q_unmet [W]`` is the part of the request that is not delivered.
``Q_request [W]`` (signed, as asked) and ``Q_unmet [W]`` are on every row, so a
schedule's unmet energy is the sum of ``Q_unmet [W]`` times the time step.

What stops the unit is in ``capacity_binding``. ``"compressor"`` is a
compressor at ``rps_max``. ``"discharge_temperature"`` and
``"condensing_temperature"`` are the compressor envelope's limits
(``T_dis_max``, ``T_cond_max``; :doc:`/developer/reference-operating-limits`):
the next larger duty is deliverable once the limit is lifted. ``"cycle"`` means the air side, the
pressure-ratio envelope or the approach-temperature search domain ran out
first; the compressor then runs below its ceiling, and the row is the
least-power point at that delivery rather than one that reaches the speed
ceiling by starving a coil of air.

What the cycling model does not contain: the indoor fan is taken to run only
while heat is delivered, no standby or crankcase power is added while the unit is off,
and a start is not resolved in time — the degradation is the standard's
seasonal-average figure, not a transient simulation.

Frost and defrost
=================

In heating the outdoor coil runs below the outdoor air. Below about 5 °C it is
below freezing and colder than the air's dew point, so frost builds on it,
capacity and power fall, and the unit periodically stops heating to melt the
frost. A catalogue figure at 2 °C or −7 °C is the mean over that; the
frost-free cycle the model solves is not.

``AirSourceHeatPump`` therefore applies the frost adjustment EnergyPlus uses
for its DX heating coils (``Coil:Heating:DX:SingleSpeed``, on-demand
reverse-cycle defrost; from DOE-2.1E), with its published constants unchanged
(:mod:`tmhp.defrost`):

.. math::

   T_{coil} = 0.82\,T_{out} - 8.589, \qquad
   \Delta\omega = \max\left(10^{-6},\ \omega_{out} - \omega_{sat}(T_{coil})\right), \qquad
   t_{frac} = \frac{1}{1 + 0.01446/\Delta\omega}

.. math::

   \text{capacity multiplier} = 0.875\,(1 - t_{frac}), \qquad
   \text{power multiplier} = 0.954\,(1 - t_{frac})

and no correction above an outdoor temperature of 5 °C. A request ``Q`` has to
be met by the mean, so the cycle is solved frost-free at
``(Q + Q_defrost) / capacity multiplier`` and the row is then averaged:

- ``Q_ref_iu [W]`` is the mean delivered heat — the request, unless the larger
  frost-free duty runs into the capacity ceiling, in which case the shortfall
  is in ``Q_unmet [W]``. Frost lowers the ceiling by the capacity multiplier.
- ``defrost_fraction [-]`` is ``t_frac``; ``frost_capacity_factor [-]`` and
  ``frost_power_factor [-]`` are the two multipliers.
- ``E_cmp [W]`` is the frost-free compressor power times ``power multiplier +
  t_frac`` (heating, then defrosting); ``E_defrost [W]`` is the defrosting
  part. The outdoor fan follows the power multiplier, and the indoor fan's
  power is averaged with the heat it delivers.
- ``Q_defrost [W]`` is the heat a reverse-cycle defrost takes from the room
  (``0.01 · t_frac · (7.222 − T_out) · hp_capacity / 1.01667``).
- ``Q_ref_iu_frost_free [W]`` and ``E_tot_frost_free [W]`` are the frost-free
  cycle the row was solved at; heat and flow rates of the row are that cycle's
  times the delivered-heat factor, and state points are that cycle's.

.. list-table:: Frost adjustment at 84 % relative humidity
   :header-rows: 1
   :widths: 20 20 20 20 20

   * - Outdoor [°C]
     - Coil [°C]
     - ``defrost_fraction``
     - Capacity multiplier
     - Power multiplier
   * - 5
     - −4.5
     - 0.119
     - 0.771
     - 0.841
   * - 2
     - −6.9
     - 0.098
     - 0.789
     - 0.860
   * - −7
     - −14.3
     - 0.044
     - 0.836
     - 0.912
   * - −15
     - −20.9
     - 0.019
     - 0.859
     - 0.936

The outdoor humidity is an optional input: ``analyze_steady(..., RH_out=0.7)``
and ``analyze_dynamic(..., RH_out_schedule=...)`` take a relative humidity as a
fraction. Without it the model uses 84 %, the humidity of the EN 14511 rating
condition A2 (dry bulb 2 °C, wet bulb 1 °C). ``defrost=None`` switches the
correction off, and a :class:`tmhp.DefrostModel` sets the outdoor limit and the
default humidity.

Two things differ from EnergyPlus, both so that nothing has to be entered.
EnergyPlus asks for a curve of defrost power; here the compressor draws, while
it defrosts, what it draws while it heats. And the coil temperature is
EnergyPlus' correlation rather than the evaporating temperature this model
solves — the constants were fitted with that estimate.

What it leaves out: the correction is a step at 5 °C, where the cost is at its
largest (about a fifth of the COP), because that is how the source model is
written; and the room is not cooled in time during a defrost — its effect is in
the mean. :meth:`~tmhp.AirSourceHeatPump.max_capacity` reports the same mean
ceiling, with the frost-free one as ``Q_max_frost_free [W]``.

COP boundary
============

``Q_r_iu`` is the coil requirement before fan heat, not net room delivery.
It retains its sign convention and governs compressor speed, rated capacity,
part-load ratio and the operating-point search. ``cop_ref [-]`` remains
``Q_ref_iu [W] / E_cmp [W]``.

The space-conditioning system metric is
``abs(Q_a_iu_out [W] - Q_a_iu_in [W]) / E_tot [W]``. Both air energy
flows use the actual indoor airflow and the same outdoor reference temperature.
The final outlet already includes indoor fan heat: net cooling equals coil
duty minus indoor fan power, while net heating equals coil duty plus that
power. Do not add or subtract it a second time. The denominator includes
compressor, indoor fan and outdoor fan electricity.

``indoor_heat_direction_valid`` identifies delivery in the requested mode.
Opposite-direction transfer or zero airflow during active operation gives
NaN system COP; off operation has zero air energy flows and NaN COP.
This reporting diagnostic does not alter the coil-load solver.

This dry-coil model reports sensible cooling, not total latent-plus-sensible
cooling or a certified rating. Catalogue comparisons must use the catalogue's
own capacity and electrical-input boundary. Earlier results through commit
``c5d673e`` used ``Q_ref_iu / E_tot`` and must be labelled as coil-duty COP.
For a seasonal performance factor, sum actual mode-specific air delivery
times timestep duration and divide by summed system electricity times duration;
an arithmetic mean of hourly COP is a different statistic.

API reference
=============

.. automodule:: tmhp.air_source_heat_pump
    :members:
    :undoc-members:
    :show-inheritance:
