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

.. _ashp-air-density:

Air density
===========

A fan moves a volume of air and a coil exchanges heat with a mass of it. Both
coils take the density of the air they draw in — outdoor air at ``T0`` for the
outdoor coil, room air at ``T_a_room`` for the indoor coil — as dry air at one
standard atmosphere (:func:`tmhp.air_properties.air_density`):

.. list-table::
   :header-rows: 1
   :widths: 30 14 14 14 14 14

   * - Inlet air [°C]
     - −25
     - −7
     - 15
     - 27
     - 35
   * - Density [kg/m³]
     - 1.422
     - 1.326
     - 1.225
     - 1.176
     - 1.145

The airflows the model reports and accepts (``dV_ou_a``, ``dV_iu_a``, the fan
references and limits) are volumes at the coil inlet, so the air mass flow of a
coil is ``air_density(T_in) * dV`` and its heat capacity rate
``c_a * air_density(T_in) * dV``. That one mass flow carries the coil's NTU,
the outlet temperature and the fan heat, so within a row

.. code-block:: text

   c_a * rho(T_in) * dV * (T_out - T_in) = heat from the refrigerant + fan power

holds on each coil (``Q_ref_ou``, ``E_ou_fan`` on the outdoor side;
``Q_ref_iu``, ``E_iu_fan`` on the indoor side; the refrigerant heat is negative
where the coil is the evaporator). ``Q_a_iu_in`` / ``Q_a_iu_out`` and the air
exergy flows use the same mass flow. A row averaged over on/off cycling or
frost keeps the identity on the indoor side; on the outdoor side the fan power
is averaged with the runtime fraction and the airflow with the capacity ratio,
so the two sides differ by that share of the fan power.

At a fixed fan volume flow, cold outdoor air therefore carries more heat
capacity than the 15 °C standard air earlier releases assumed at every
temperature (1.225 kg/m³): 16 % more at −25 °C, 6.5 % less at 35 °C. Room air
is 2 to 5 % lighter than that standard air (20 to 30 °C). For the default unit
against those releases:

- **Heating.** The outdoor coil gains air mass in the cold (the evaporating
  temperature is 0.1 K higher below 0 °C) and the indoor coil loses 1.7 % at a
  20 °C room (the condensing temperature is 0.1 to 0.2 K higher). The two
  nearly cancel: the COP is 0.3 % lower at 7 °C / 20 °C at nameplate capacity
  and within 0.2 % at half load down to −15 °C.
- **Cooling.** Both coils move warm, lighter air. At 35 °C outdoors and a
  27 °C room the evaporating temperature at nameplate capacity is 0.5 K lower
  and the COP 1.9 % lower (1.3 % at half load); at a 30 °C room the COP at
  nameplate capacity is up to 4 % lower.
- **Ceiling.** The largest duty the indoor coil carries inside the approach
  bounds (``dT_approach_bounds``) falls with the room-air density, by 1.3 % at
  a 20 °C room. Where that ceiling is close to the request it now binds: at
  −7 °C / 20 °C the frosted unit delivers 98.8 % of its nameplate
  (``capacity_clamped == "max"``) where it delivered 100 %.

The volume flows are the fan's actual flows at the coil inlet. Rating airflows
are commonly quoted as standard air (1.204 kg/m³, the mass flow divided by that
density); at 20 °C the two are the same number, at a 27 °C room the actual flow
is 2.4 % larger and at 35 °C outdoors 5 % larger. Pass the actual flow when the
difference matters.

What stays on the volume flow: the coil conductance follows the fan's volume
flow (``UA_rated * (dV / dV_ref) ** n``), and the fan power follows its curve
in the volume flow at the rated static pressure. The fan laws give a pressure
rise, and so a fan power, proportional to the air density at a fixed volume
flow; that is not applied. Applied, it would add 16 % to the outdoor fan's
power at −25 °C, which is 0.5 to 0.7 % of the total input (1.2 % at most), and
take 0.3 to 0.4 % off the total input at 35 °C. Humidity is left out — per cubic metre moist air
carries 0.2 to 0.4 % more sensible heat capacity than dry air at room and
summer conditions — and so is altitude (one standard atmosphere; 18 % less
density at 1 600 m).

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

The ceiling is the unit running **flat out**: the compressor at ``rps_max`` and
both fans at their largest airflow. A coil carries a duty with the smallest
temperature difference at its largest airflow, so this is the least lift — the
smallest pressure ratio and the coolest discharge — at which that speed's duty
is delivered; if this point is outside the compressor envelope, so is every
other approach pair delivering the duty. It is solved directly (both approach
temperatures as a fixed point, about twenty cycle evaluations), not searched
for. Where it is outside the envelope, or beyond what a coil carries inside
the approach bounds, a unit's controller holds the compressor back, and the
ceiling is the largest duty whose least-lift point is an operating point,
bisected to 0.1 %.

What stops the unit is in ``capacity_binding``, with the names the
water-heating models use (:doc:`/concepts/failure-reason-semantics`):

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - ``capacity_binding``
     - What the next larger duty runs into
   * - ``"compressor"``
     - The speed range: the compressor is at ``rps_max``.
   * - ``"discharge_temperature"``
     - ``T_dis_max``, with the suction already as wet as the
       discharge-temperature control allows.
   * - ``"pressure_ratio"``
     - ``PR_cycle_max``.
   * - ``"compressor_efficiency"``
     - The floor of the shipped isentropic fit outside its data.
   * - ``"cycle"``
     - A coil at its largest airflow and the widest approach the search
       admits (``dT_approach_bounds``). The compressor is clear of its own
       limits and the other fan still has a range, so the row is the
       least-power point at that duty.

The same holds below the ceiling. The envelope is not monotone in the duty: at
mid speed the compressor is less efficient and its discharge hotter than flat
out, so a duty can be refused although a larger one runs (default unit, −25 °C
outdoor / 24 °C room: 2.3 kW has no operating point, 3.6 kW has). Such a
request runs at the largest duty below it that has one, again with
``capacity_clamped == "max"`` and the binding that names the limit, as the
water-heating models do. A request is switched off (``failure_reason``) only
when no smaller duty runs either, and the reason is then the limit its own
least-lift point breaks — not the one an arbitrary probe of the search
happened to break.

.. note::

   The ceiling is not the largest heat any pair of approach temperatures
   delivers at ``rps_max``. An adiabatic compressor turns its losses into
   delivered heat, so starving a coil of air (a higher pressure ratio, a lower
   isentropic efficiency) can raise the heating duty by up to a tenth while the
   COP falls by up to a quarter; no unit runs its fans that way. Earlier
   versions ranked capacity-limited candidates by delivered heat and returned
   such states — the default R290 unit at −15 °C / 18 °C with the outdoor fan at
   18 % of its airflow and a COP of 1.9 against 2.8.

What the cycling model does not contain: the indoor fan is taken to run only
while heat is delivered, no standby or crankcase power is added while the unit is off,
and a start is not resolved in time — the degradation is the standard's
seasonal-average figure, not a transient simulation.

Frost and defrost
=================

In heating the outdoor coil runs below the outdoor air. Below about 5 °C it is
below freezing and colder than the air's dew point, so frost begins to build on it,
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

and no correction above an outdoor temperature of 5 °C. EnergyPlus switches
the whole correction on at that limit; here it grows from nothing at 5 °C to
its full size at 2 °C, the frosting rating condition of EN 14511 (A2), as the
share :math:`w` of the runtime the coil frosts:

.. math::

   w = \min\left(1,\ \frac{5 - T_{out}}{3}\right), \qquad
   t_{frac} \leftarrow w\,t_{frac}, \qquad
   \text{multiplier} \leftarrow 1 - w\,(1 - \text{multiplier})

— the mean of a unit that frosts for that share and runs frost-free for the
rest. At and below 2 °C and above 5 °C nothing differs from EnergyPlus. A
request ``Q`` has to
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
     - 0
     - 1
     - 1
   * - 3.5
     - −5.7
     - 0.054
     - 0.890
     - 0.925
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
correction off, and a :class:`tmhp.DefrostModel` sets the outdoor limit, the
width of the transition below it and the default humidity.

Three things differ from EnergyPlus. The transition above replaces a switch:
in EnergyPlus the correction is in full at 5.0 °C and absent at 5.1 °C, where
its cost is at its largest, so the COP of the default unit stepped from 3.49 to
4.64 between 5.0 and 5.5 °C. The 5 °C limit is a setting of the defrost
controller, not the temperature at which a coil frosts over, and a coil near
freezing frosts on part of its surface and for part of the time.
``DefrostModel(transition_K=0)`` restores the switch. The other two are there
so that nothing has to be entered: EnergyPlus asks for a curve of defrost
power, and here the compressor draws, while it defrosts, what it draws while it
heats; and the coil temperature is EnergyPlus' correlation rather than the
evaporating temperature this model solves — the constants were fitted with
that estimate.

What it leaves out: dry air is not frost-free in the source model (the
frosted-coil multipliers 0.875 and 0.954 apply whenever the outdoor air is
below the limit, whatever its humidity), and that is kept; and the room is not
cooled in time during a defrost — its effect is in the mean.
:meth:`~tmhp.AirSourceHeatPump.max_capacity` reports the same mean
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
