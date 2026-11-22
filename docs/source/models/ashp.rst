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

.. _ashp-fan-control:

Fan flows: the point of least power
===================================

The model has no fan controller. At each call it searches the two approach
temperatures for the operating point of least total electrical input
(compressor and both fans) that carries the duty; each coil's air flow
follows from its approach, inside the fan's flow limits. The search stops
within 0.1 W of that input. Where no point carries the duty it ranks by the
shortfall instead, and on the minimum speed by the input per unit of heat
with the cycling loss. A real unit runs the indoor fan at the step its user
selects and the outdoor fan on its controller's map, so the result is close
to the best the fans can do in this model, not a prediction of a
controller.

How much rides on it, for the default 4 kW unit (cooling at 35 °C outdoor and
a 27 °C room, heating at 7 °C outdoor and a 20 °C room). The search runs the
indoor fan at its largest flow, 0.232 m³/s, in three of the four cases. In
heating at 2 kW it stops at 0.229 m³/s, where the total input is within 0.1 W
of the input at the largest flow, inside the tolerance of the search:

.. list-table:: System COP by fan flow
   :header-rows: 1
   :widths: 40 15 15 15 15

   * - Fan flows
     - Cooling 2 kW
     - Heating 2 kW
     - Cooling 4 kW
     - Heating 4 kW
   * - Least power (the model)
     - 5.28
     - 5.38
     - 3.99
     - 4.45
   * - Indoor fan held at 70 % of its largest flow
     - 4.99
     - 5.16
     - 3.35
     - 3.95
   * - Indoor fan held at 50 %
     - 4.44
     - 4.73
     - 2.65
     - 3.37
   * - Both fans held at their largest flow
     - 5.00
     - 5.00
     - 3.99
     - 4.43

With the indoor flow at half, the COP is 16 % and 12 % lower at half load and
34 % and 24 % lower at nameplate load: the coil needs a larger temperature
difference to carry the duty, and the compressor pays for it. Holding both
fans at full flow costs 5 % and 7 % at half load — the outdoor fan draws
more than the compressor saves — and at most 0.4 % at nameplate load. At half
the indoor flow the unit no longer carries its nameplate either: its coil
takes 3.90 kW in cooling and gives 3.98 kW in heating (refrigerant side),
and the figures in that row are at those duties. A fan is held at one flow
with equal limits:

.. code-block:: python

   from tmhp import AirSourceHeatPump

   largest = AirSourceHeatPump().dV_iu_fan_a_max
   low_fan = AirSourceHeatPump(dV_iu_fan_a_min=0.5 * largest, dV_iu_fan_a_max=0.5 * largest)

**Rated conditions and use.** A capacity table or a certified rating is
measured at an indoor airflow the manufacturer declares, and the catalogue
comparison of :doc:`/validation/index` gives the model that published airflow
as the indoor fan's largest flow. The indoor fan at its largest flow is
therefore the condition the catalogues were measured at: that comparison is
like with like. In use the indoor fan runs at the step its user selects, or at
what an automatic setting picks, and the COP falls as the rows above show. The
outdoor fan is the unit's own to control in either case, and the search slows
it with the load as a controller does (0.61 to 0.43 m³/s from nameplate to
half load in cooling).

For a seasonal figure of a unit whose fan setting is known, hold the fan
there; where it is not, the model's figure is the favourable end of what the
fan setting leaves open. A building simulation that knows the room airflow
of each hour cannot pass it per call yet: the flow limits are constructor
arguments.

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
exergy flows use the same mass flow. On a wet indoor coil
(:ref:`ashp-wet-coil`) the left-hand side is the sensible part only: the
air's temperature change carries the coil's sensible heat and the fan power,
``-Q_room_sensible [W]``, and the refrigerant takes up the latent heat
``Q_iu_latent [W]`` besides (default 4 kW unit, 35/27 °C, 50 % room humidity:
−3 217.9 W on the air, of which 23.1 W of fan heat, and 759.0 W latent for
the 4 000 W the refrigerant takes up). A row averaged over on/off cycling or
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
  a 20 °C room. It is the heating ceiling of the default unit in mild
  weather: at 7 °C / 20 °C, 189 % of its nameplate
  (``capacity_binding == "cycle"``).

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

The rated indoor airflow defaults to the largest airflow wall-mounted indoor
units of that capacity publish: ``0.0320 × kW + 0.1035`` m³/s for a
cooling-rated unit, 0.232 m³/s (209 m³/h per kW) at the default 4 kW
(:func:`tmhp.default_inputs.estimate_max_airflow`;
:doc:`/validation/defaults`, *Maximum fan airflow*).
``indoor_unit_type="floor_standing"`` selects that class instead. Pass
``dV_iu_fan_a_rated`` when the unit's own figure is known — a cassette or
ducted unit has no catalogue class and needs it. The rated airflow is also the
fan's maximum.

The airflow decides the supply-air temperature and, through the indoor coil's
approach, most of the temperature lift. At the default a unit delivering its
nameplate capacity supplies air about 14 K from the room: 34 °C in heating at a
20 °C room, 12 °C in cooling at a 27 °C room, with the refrigerant saturated
16 K from the room air. (Until November 2026 the default was 193 m³/h per kW,
400 cfm per ton — the airflow unitary equipment is rated at rather than its
largest; before that, 720 m³/h per kW moved four times that air: supply air
5 K from the room, a condensing temperature near 30 °C, and a rated COP above
what such equipment declares.) The indoor fan draws about 0.6 % of the
nameplate capacity at rated flow, both fans together 1.5 to 2.1 %.

Because the fan power is small against the compressor's, the least-power
operating point keeps the indoor fan at its largest airflow, to the tolerance
of the search, from nameplate down to half load (:ref:`ashp-fan-control`).
Below that, in heating, the compressor's input is small enough for the fan's
to count, and the search turns the fan down: for the default unit at 7 °C
outdoor and a 20 °C room, to 86 % of its largest airflow at 1.5 kW. The
supply-air temperature therefore approaches the room temperature at part load.
A unit that slows its fan for comfort is described with ``dV_iu_fan_a_max``.

.. note::

   The approach-temperature search domain (``dT_approach_bounds``) is 1 to
   30 K. A default unit runs its indoor coil 12 to 24 K from the room air at
   nameplate capacity, depending on its size (2 to 30 kW), so at the rating
   points the box is not what limits it. In mild weather it is: at 7/20 °C
   the delivered heat of a default unit is capped by the indoor coil at its
   largest airflow and the widest approach (``capacity_clamped == "max"``,
   ``capacity_binding == "cycle"``), at 189 % of the nameplate at 4 kW, 155 %
   at 8 kW and 137 % at 15 kW; a larger unit moves less air per kW. Until
   November 2026 the default was 1 to 20 K, which capped the
   default 4 kW unit near 125 % of its nameplate and cut the rated capacity of
   units above 8 kW.

.. _ashp-wet-coil:

Wet indoor coil (cooling)
=========================

Without a room humidity the indoor coil is dry: all of the coil heat is
sensible and the model removes no moisture. Give the room's relative humidity
and a cooling coil that runs below the room air's dew point also condenses
water:

.. code-block:: python

   from tmhp import AirSourceHeatPump

   ashp = AirSourceHeatPump()
   row = ashp.analyze_steady(Q_r_iu=4000, T0=35.0, T_a_room=27.0, RH_room=0.5)

   row["Q_ref_iu [W]"]                # 4000: what the refrigerant takes up
   row["Q_iu_sensible [W]"]           # 3241
   row["Q_iu_latent [W]"]             # 759
   row["SHR_iu [-]"]                  # 0.81
   row["m_dot_iu_condensate [kg/s]"]  # 3.08e-4 (1.1 kg/h)
   row["Q_room_sensible [W]"]         # 3218: sensible, less the indoor fan's 23 W
   row["w_iu_a_out [kg/kg]"]          # 0.01007 (room air: 0.01120)

``RH_room`` is a fraction (0–1) at ``T_a_room`` and one standard atmosphere;
:meth:`~tmhp.AirSourceHeatPump.max_capacity` takes the same argument and
``analyze_dynamic`` a ``RH_room_schedule``. Heating does not read it (the
indoor coil is the condenser there), nor does an off point, and ``None`` — the
default — is the dry coil, with every result as before. A schedule holds one
entry per step (a list, an array or a Series). Only the cooling steps are
read, each as ``RH_room`` is: a cooling step whose entry is not a number in
0–1 stops the run with a ``ValueError`` that names the step, and what a
heating or an off step holds — a missing value, say — is not looked at.

The coil is the apparatus-dew-point / bypass-factor coil, the family
EnergyPlus uses for DX cooling coils, written so that it reduces to the dry
coil. With room air at :math:`T_{in}`, :math:`w_{in}`, the evaporating
temperature :math:`T_e`, the air heat capacity rate :math:`C` and the
effectiveness :math:`\varepsilon = 1 - e^{-UA/C}` of the dry coil,

.. math::

   Q_s = C\,\varepsilon\,(T_{in} - T_e), \qquad
   Q_l = C\,\varepsilon\,\frac{h_{fg}}{c_a}\,\max\bigl(0,\; w_{in} - w_{sat}(T_e)\bigr),

and the refrigerant takes up :math:`Q_s + Q_l` (``Q_ref_iu [W]``). The air
leaves the coil at :math:`T_{in} - \varepsilon\,(T_{in} - T_e)` and
:math:`w_{in} - \varepsilon\,\max(0,\, w_{in} - w_{sat}(T_e))`: on the straight
line from the room state to saturated air at the coil, a fraction
:math:`\varepsilon` of the way. A wet coil is therefore the dry coil with its
driving temperature difference enlarged by
:math:`(h_{fg}/c_a)\,\max(0,\, w_{in} - w_{sat}(T_e))`
(:func:`tmhp.heat_exchanger.wet_coil_latent_difference`); where the coil is at
or above the room's dew point that term is zero and the coil equation is the
dry coil's. The airflow that carries the duty, the two approach temperatures
and the capacity ceiling are solved as before, with that driving difference.

That statement is about the coil equation, not about every result. The
searches around it treat a request the fans' smallest airflows refuse
differently once a room humidity is given (*the two ends of the operating
range*, below), also where no water condenses: the default 4 kW unit refuses
``analyze_steady(1150, 0, 27)`` (0 W, ``"cycle_invalid"``) and delivers it
with ``RH_room=0.02`` (1 150 W, by cycling at 1 153 W) — with no latent heat
either way, the coil at −1.4 °C being far above that room's dew point of
−24 °C. Room air with no water vapour at all (``RH_room=0``) is solved as the
dry coil is.

What the model assumes:

- **The surface is at the refrigerant saturation temperature.** The dry coil
  already works that way through its overall ``UA``; the wet coil keeps it, so
  condensation starts where the evaporating temperature — not a warmer fin
  surface — reaches the dew point.
- **The same ``UA`` wet and dry.** No wet-surface enhancement and no change of
  fin efficiency; ``UA_iu_rated`` and its airflow law are those of the dry
  coil. The default (0.16 W/K per W of nameplate) is a provisional rule that
  was not derived for a wet coil.
- **Lewis number 1.** Heat and water vapour are transferred with the same
  effectiveness, which is what puts the leaving air on the straight line.
- **Leaving air no wetter than saturated.** The saturation curve is convex,
  so for a room close to saturation, or a coil far below the dew point, the
  straight line passes above it. The leaving air is then put on the curve at
  the same enthalpy — as the DX cooling coil of EnergyPlus corrects a
  supersaturated outlet (``CalcDoe2DXCoil``; the coil is the
  apparatus-dew-point / bypass-factor coil of its Engineering Reference): the
  total heat and the airflow are unchanged, the supply
  air is warmer and drier than on the line and more of the heat is latent
  (:func:`tmhp.heat_exchanger.saturated_leaving_state`). The default unit at
  35/27 °C reaches it at its capacity ceiling from 80 % room humidity, and at
  the nameplate duty only above 95 %.
- **Condensate leaves at the coil temperature**, and :math:`h_{fg}` is the
  latent heat of water at :math:`T_e` (CoolProp, IAPWS-95: 2 477 kJ/kg at
  10 °C; the triple-point value below 0.01 °C). The sensible cooling of the
  vapour that condenses is left out (about 1 % of the latent heat).
- **Dry-air heat capacity and density** for :math:`C`, as everywhere in the
  air-side models (`Air density`_); :math:`w_{in}` and :math:`w_{sat}` are
  CoolProp humid-air values at 101 325 Pa.
- **No frost on the indoor coil.** A coil below 0 °C keeps condensing, with
  :math:`w_{sat}` over ice.

.. list-table:: Default 4 kW unit, 35 °C outdoor, 27 °C room, 4 kW coil heat
    :header-rows: 1
    :widths: 12 12 12 14 12 12 12 14

    * - ``RH_room``
      - Dew point [°C]
      - :math:`T_e` [°C]
      - Sensible / latent [W]
      - ``SHR_iu``
      - Condensate [kg/h]
      - ``E_tot`` [W]
      - ``cop_sys``
    * - none, 0.3
      - – / 8.0
      - 10.8
      - 4000 / 0
      - 1.00
      - 0
      - 997
      - 3.99
    * - 0.4
      - 12.3
      - 11.7
      - 3790 / 210
      - 0.95
      - 0.31
      - 972
      - 4.09
    * - 0.5
      - 15.7
      - 13.9
      - 3241 / 759
      - 0.81
      - 1.11
      - 909
      - 4.37
    * - 0.6
      - 18.6
      - 16.0
      - 2730 / 1270
      - 0.68
      - 1.86
      - 854
      - 4.66
    * - 0.7
      - 21.1
      - 17.9
      - 2253 / 1747
      - 0.56
      - 2.56
      - 805
      - 4.94
    * - 0.8
      - 23.3
      - 19.7
      - 1807 / 2193
      - 0.45
      - 3.22
      - 760
      - 5.23

At a fixed total duty a wetter room needs a smaller temperature difference at
the coil, so the evaporating temperature rises — while less of the duty is
sensible cooling. In this example the electrical input falls with it (997 W
dry, 909 W at 50 %), as it does on 179 of the 180 Daikin catalogue rows
(:doc:`/validation/index`, *Cooling and latent load*). That is what these
conditions give, not a rule. Where the pressure ratio is on its floor a warmer
evaporator lifts the condensing temperature with it, and the input can rise:
a 2.5 kW unit delivering 2 kW at 15 °C outdoor and 35 °C room draws 249.0 W
dry and 251.1 W at 90 % room humidity. The request stays what it was:
``Q_r_iu`` is the coil's total heat, so a zone model that needs a given
*sensible* cooling has to ask for more than that when the coil is wet (there
is no request-on-room-load option), and takes ``Q_room_sensible [W]`` off its
air node and ``m_dot_iu_condensate [kg/s]`` off its moisture balance.

**The two ends of the operating range** move with the humidity, because the
coil carries more at a given evaporating temperature:

- *The ceiling.* ``max_capacity(..., RH_room=...)`` is the ceiling of the wet
  coil, and a request above it runs there (``capacity_clamped == "max"``,
  ``Q_unmet [W]``). An indoor coil that sets the ceiling of the dry model sets
  a higher one wet: with ``UA_iu_rated=150`` the default unit delivers
  3 465 W dry at 35/27 °C, 5 835 W at 50 % room humidity (the coil at its
  largest airflow and widest approach in both) and 7 131 W at 80 %, where the
  compressor's speed binds instead. The ceiling is found by the same search
  as the dry one, on the duty; where the least-lift point of a duty names no
  limit of the machine the search starts it again from other approaches and
  looks above it before it settles, so that a point it merely failed to find
  is not reported as the ceiling. A fan held at one flow, or with a narrow
  range, cannot leave its largest airflow the way the least-lift point
  assumes; where that point is refused by the fans' smallest airflows the
  duty is solved by the ordinary search before it counts as refused (default
  unit with its outdoor fan held at its largest flow, 15 °C outdoor and a
  saturated 35 °C room: 13.68 kW, the outdoor coil at its widest approach).
  Where the search finds no duty at all, the duty the low-load search of the
  same condition ends on (next item) runs, and the ceiling is searched
  upwards from it. And where a unit with a fan held at one flow runs flat out
  on the pressure-ratio floor, the ceiling is what the ordinary search
  delivers there, 1 to 1.5 % above the least-lift point: the outdoor approach
  asked for no longer moves the condensing temperature but still sets the
  subcooling (10 kW unit with its indoor fan held, −10 °C outdoor and a
  saturated 35 °C room: 35.43 kW against 34.99 kW).

  A request equal to the ceiling is delivered the ceiling: ``Q_ref_iu [W]`` is
  the ``Q_max [W]`` that ``max_capacity`` reported with the same ``RH_room``
  (to rounding) and ``Q_unmet [W] == 0``. How the row is labelled depends on
  which search answered it. Where the search of the request itself delivers
  that duty the row is an ordinary one (``capacity_clamped`` is ``None``; 33
  of the 180 conditions below). Where it does not, the row is the ceiling's
  own state, re-solved, with ``capacity_clamped == "max"`` and
  ``capacity_binding`` naming the limit (default 10 kW unit at 46 °C outdoor
  and a 27 °C room at 30 %: 13 771.58 W, on the condensing-temperature
  limit). That second answer is also given to a request that is under the
  ceiling by no more than 0.1 % and that the search of the request does not
  deliver. That search starts from the request's own least-lift point,
  solved to a looser tolerance than the ceiling's, and close under the
  ceiling it reads the request as beyond the limit, or as short of it flat
  out, where the ceiling search has verified an operating point. On default
  units of 2 to 10 kW (180 conditions: 20 to 46 °C outdoor, 20 to 32 °C room,
  30 to 80 %) the requests it does not deliver reach from 0.0007 % to
  0.006 % of the ceiling under it, at 147 of the conditions; every request
  0.01 % under is delivered as asked. The 0.1 % is the width the ceiling
  itself is searched to (``rel_tol`` of ``max_capacity``): the search does
  not tell duties that close apart, so the unit is run at the one it
  verified. Such a row delivers more than was asked, by at most that 0.1 %
  (0.006 % in those conditions). A request the search delivers is not
  touched, whatever its distance from the ceiling.
- *The smallest continuous duty in mild weather.* At a low outdoor temperature
  the cycle sits on the pressure-ratio floor (1.5), which holds the condensing
  temperature well above the outdoor air, and each coil has to carry its duty
  at no less than its fan's smallest airflow. The evaporating temperature that
  would make room for the outdoor coil makes a wet indoor coil carry more
  than a small duty, so below some duty no pair of approaches runs
  continuously: 1.54 kW for the default unit at 10 °C outdoor, 27 °C room and
  80 % (the dry coil runs down to its minimum speed there, 1.15 kW). A
  smaller request is met by cycling at that duty, exactly as below the minimum
  speed (``capacity_clamped == "min"``, ``Q_min [W]``; 1 200 W: runtime
  fraction 0.823). Fans with a high smallest flow put that duty above twice
  the nameplate: with both at 65 % of the largest flow it is 8.44 kW at 15 °C
  outdoor and a saturated 35 °C room, and a 4 kW request cycles there
  (runtime fraction 0.546). The search for that duty is a bisection by which
  side refuses the trial, to 0.5 %; where it closes with nothing running it
  goes on to 0.1 %, because the duties that run can be a window narrower than
  the first width (next paragraph). Where it still finds no duty, the state
  the unit runs flat out at — the one the ceiling search starts with — is
  tried before the request is given up (2.5 kW unit with both fans held at
  their largest flow, 8 °C outdoor, 35 °C room at 90 %: a 2.5 kW request is
  met by cycling at the 9.61 kW the unit runs flat out at, runtime fraction
  0.319). Where that state lies under the request the unit runs there, as for
  any request above the ceiling. Where no duty is found to run — fans with
  almost no flow range, in cold weather — the unit is off with
  ``failure_reason == "hx_not_converged"``: a coil cannot carry so little
  inside its fan's flow range.

**A fan held at one flow, and what the searches can miss.** A coil whose fan
runs at one flow (``dV_*_fan_a_min`` equal to ``dV_*_fan_a_max``) carries a
duty at one approach temperature only. On the pressure-ratio floor the
condensing temperature is held above the outdoor air whatever approach is
asked for, so with both fans held the two coils balance in a narrow window of
duties, or at none: the 4 kW unit at 8 °C outdoor and a saturated 35 °C room
runs continuously between 14.31 and 14.35 kW only (2.5 kW unit, same
conditions: at no duty — the outdoor coil at its one flow carries at least
1.9 % more than the cycle rejects, up to the compressor's largest speed). The
three searches —
the operating point of a request, the smallest continuous duty, the ceiling —
sample the duty: a ladder of duties and a bisection between two that differ,
to 0.1 % for the ceiling and to 0.5 % for the smallest continuous duty (0.1 %
where nothing ran at 0.5 %). They use each other's verified points as
candidates — the ceiling search starts from the duty the low-load search
ends on where it found none itself, and a low-load request takes the
flat-out state of the ceiling — but a window narrower than the spacing of
the samples can be missed by all of them. The caller then sees ``Q_max [W] == 0`` with
``binding == "infeasible"`` from ``max_capacity`` and an off row with
``failure_reason == "hx_not_converged"`` from ``analyze_steady``, although a
duty inside the window would run. Two ways out: leave the fan a flow range
(the default smallest flow is 15 % of the largest; a held fan is an input,
not a default), or request a duty inside the window. With fans that modulate
the duties that run are a wide band and the samples fall inside it.

A result does not depend on what was solved before it. Inside one
``analyze_dynamic`` run the searches of a wet cooling condition are
remembered — the ordinary search of a duty, the ceiling — but only as what
solving them again would give, so a step's row is the row of that step solved
alone, in any order of the steps. (The dry model reuses the smallest
continuous duty of a condition for the smaller requests of the same run, as
before; that duty is found by bisection to 0.5 %, and a row can differ by that
much from the same request solved alone.)

  This is the wet coil's behaviour only. Without ``RH_room`` the model is
  left as it was, result for result: a request the fans' smallest airflows
  refuse on the pressure-ratio floor is answered there with the unit off and
  ``failure_reason == "cycle_invalid"`` (default unit, 27 °C room, 0 °C
  outdoor: 1 200 W, 30 % of the nameplate, runs and 1 150 W is refused),
  where the 1 150 W request with a room humidity of 20 % cycles at 1.24 kW.
  A dry request equal to the dry ceiling is likewise answered as before: by
  the dry searches, which run the unit up to 0.012 % under it at 42 of 60
  conditions of the default units (none is answered with the unit off).

What it leaves out, beyond the assumptions above:

- **The catalogue comparison is not settled by it.** With the default coil
  the model's sensible heat ratio is above the published one on average and
  moves less with the size of the unit. The wet coil brings the cooling COP
  of the Daikin rows closer to the catalogue on average — the dry coil runs
  them low — but not evenly: above the catalogue on the rows with the most
  latent load, still well below it on those with the least
  (:doc:`/validation/index`, *Cooling and latent load*). The shipped
  validation results are those of the dry coil.
- **Re-evaporation while cycling.** Below the minimum speed the row is the
  mean over an on/off period with the latent heat scaled like every other
  rate; the water that evaporates off a wet coil while the compressor is off
  (the latent degradation of EnergyPlus) is not modelled.
- **Exergy.** ``X_a_iu_*`` are the thermal exergy of a dry air stream, with
  or without a wet coil. A moist-air exergy balance — a reference humidity
  for the dead state, the chemical exergy of the water vapour, the
  condensate — is not modelled. On a row whose coil removes water the
  refrigerant side of the indoor coil carries the latent heat and the air
  side only the temperature change, so their difference is not the coil's
  destruction (a 2.5 kW unit at 2 kW, 15 °C outdoor, 35 °C room and 90 %:
  −2.6 W). ``Xc_iu_hx [W]``, ``X_eff_iu_hx [-]`` and ``X_eff_sys [-]`` are
  therefore NaN on those rows; rows that remove no water keep their values.
  (``X_eff_sys [-]`` is the thermal exergy the room air gains over the
  electrical input, and is negative on the dry coil too wherever the outdoor
  air, the dead state, is colder than the room: cooling a room towards the
  dead state lowers its air's exergy.)
- **Rating and defaults.** The reference state, the default inputs and
  :meth:`~tmhp.AirSourceHeatPump.operating_range` are those of the dry coil.
- Heating humidification and condensate on the outdoor coil above the frost
  range are not modelled.

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

The minimum speed is not always the lower end of continuous operation. At a
large lift the compressor efficiency falls towards low speed, and the
compressor envelope (:doc:`../developer/reference-operating-limits`) can
refuse the point before ``rps_min`` is reached, for the discharge
temperature, which the falling efficiency drives above ``T_dis_max`` even
with the discharge-temperature control used up. The default 4 kW unit does
not reach that case at −15 to −25 °C outdoors and 20 to 27 °C indoors with
R32, R410A, R290, R134a, R407C or R1234yf: it runs down to its minimum speed
(:doc:`ashpb` names boiler cases that do). Where it happens, a request below
that duty, refused while a larger one runs, is met the same way as one below
the minimum speed: the unit cycles at the smallest duty the envelope admits.
The row has ``capacity_clamped == "min"``, ``Q_min [W]`` is that duty (found
by bisection on the duty, to 0.5 %), and the compressor speed of the row is
above ``rps_min``. ``failure_reason == "discharge_temp_above_max"`` remains
where no duty runs at all.

**Above the maximum capacity** the unit runs flat out
(``capacity_clamped == "max"``): ``Q_ref_iu [W]`` is the ceiling,
``Q_max [W]`` repeats it, ``capacity_binding`` names what set it and
``Q_unmet [W]`` is the part of the request that is not delivered.
``Q_request [W]`` (signed, as asked) and ``Q_unmet [W]`` are on every row, so a
schedule's unmet energy is the sum of ``Q_unmet [W]`` times the time step.

A request that is not delivered in full is also said in words:
``analyze_steady`` issues one ``tmhp.OperatingLimitWarning`` for a point that
runs at its ceiling or does not run at all (the request, what is delivered,
the limit and what removes it), and ``analyze_dynamic`` one at the end of a
run with the steps stopped or limited and the unmet energy; the totals are in
``df.attrs["operating_limits"]`` (:ref:`operating-limit-warning`).

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
   * - ``"condensing_temperature"``
     - ``T_cond_max``, the highest condensing pressure of the compressor
       envelope (:doc:`/developer/reference-operating-limits`).
   * - ``"pressure_ratio"``
     - ``PR_cycle_max``.
   * - ``"cycle"``
     - A coil at its largest airflow and the widest approach the search
       admits (``dT_approach_bounds``). The compressor is clear of its own
       limits and the other fan still has a range, so the row is the
       least-power point at that duty.

The same holds below the ceiling. The envelope need not be monotone in the
duty: where the compressor is less efficient and its discharge hotter at mid
speed than flat out, a duty can be refused although a larger one runs (the
4 kW R32 unit as a bare constructor built it until November 2026 --
18.7 cm³/rev, 0.8 m³/s outdoors, the efficiency map of then -- at −25 °C
outdoor / 24 °C room: 2.3 kW had no operating point, 3.6 kW had; the present
default unit runs at every duty up to its 3.4 kW ceiling there). Such a
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

.. _ashp-frost-load:

**Part load.** The table is the unit running flat out, which is what the
coil-temperature correlation describes. A unit that delivers less loads its
coil less: the difference between the air and the coil surface is what drives
the heat in, so at a load ratio :math:`r` it is taken :math:`r` times as
large,

.. math::

   T_{coil} = T_{out} - r\,(0.18\,T_{out} + 8.589), \qquad
   r = \min\left(1,\ \frac{\text{frost-free duty}}{\text{hp\_capacity}\;(0.18\,T_{out} + 8.589)/(0.18 \cdot 7 + 8.589)}\right)

and the correction follows the frost drive that is left: the humidity ratio
the air can leave on that coil as frost, as a share :math:`\varphi(r)` of what
it leaves on the rated-load coil. :math:`\Delta\omega` and the two fixed
losses (``1 − 0.875``, ``1 − 0.954``) are multiplied by :math:`\varphi`. At
:math:`r = 1` nothing differs from the table at any outdoor state; a coil
that rises to the air's frost point, or to 0 °C, has no frost. The
denominator of :math:`r` is the capacity of the unit the correlation
describes at that outdoor temperature — the nameplate, declared at 7 °C
(EN 14511 A7, ISO 5151 H1), reduced as the correlation's own temperature
difference shrinks (10.1 K at 8.3 °C, 5.9 K at −15 °C). A unit that delivers
0.6 of its nameplate at −15 °C because that is its capacity there is at
:math:`r = 1`, not part-loaded. The default 4 kW unit against a 20 °C room:

.. list-table:: System COP under frost, by load (84 % relative humidity)
   :header-rows: 1
   :widths: 28 18 18 18 18

   * - Request
     - 2 °C
     - 0 °C
     - −7 °C
     - −15 °C
   * - 3 kW (:math:`r = 1` at each)
     - 3.33
     - 3.23
     - 2.92
     - 2.53
   * - 2 kW
     - 4.17
     - 3.96
     - 3.39
     - 2.79
   * - 2 kW, ``load_weighted=False``
     - 3.74
     - 3.61
     - 3.23
     - 2.77
   * - 1 kW (cycling at the minimum speed at 2 and 0 °C)
     - 4.93
     - 4.74
     - 4.00
     - 3.24

``DefrostModel(load_weighted=False)`` is EnergyPlus at every load. A unit that
cycles below its minimum capacity is loaded by what the running machine
delivers. What the load term assumes: the air flow over the coil does not
follow the load — a unit that slows its outdoor fan with the compressor has a
colder coil than this, so the part-load frost here is a lower bound.

The outdoor humidity is an optional input: ``analyze_steady(..., RH_out=0.7)``
and ``analyze_dynamic(..., RH_out_schedule=...)`` take a relative humidity as a
fraction. Without it the model uses 84 %, the humidity of the EN 14511 rating
condition A2 (dry bulb 2 °C, wet bulb 1 °C). ``defrost=None`` switches the
correction off, and a :class:`tmhp.DefrostModel` sets the outdoor limit, the
width of the transition below it and the default humidity.

Four things differ from EnergyPlus: the load term above, and three more. The transition above replaces a switch:
in EnergyPlus the correction is in full at 5.0 °C and absent at 5.1 °C, where
its cost is at its largest, so the COP of the default unit steps from 3.04 to
4.29 between 5.0 and 5.5 °C at nameplate capacity. The 5 °C limit is a setting of the defrost
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
below the limit, whatever its humidity), and that is kept at the rated load.
It shows through the load term in one place: in air too dry to frost even the
rated-load coil (below −18.3 °C at 60 % relative humidity, below −39 °C at
84 %) there is no frost drive for the load to scale, and the fixed
multipliers stay at every load — at half load and 60 % humidity the COP
multiplier is 0.997 at −15 °C and 0.917 at −18.3 °C. And the room is not
cooled in time during a defrost — its effect is in the mean.
:meth:`~tmhp.AirSourceHeatPump.max_capacity` reports the same mean
ceiling, with the frost-free one as ``Q_max_frost_free [W]``.

COP boundary
============

``Q_r_iu`` is the coil requirement before fan heat, not net room delivery.
It retains its sign convention and governs compressor speed, rated capacity,
part-load ratio and the operating-point search. ``cop_ref [-]`` remains
``Q_ref_iu [W] / E_cmp [W]``. ``Q_request [W]`` and ``Q_unmet [W]`` are on the
same basis: refrigerant-side coil heat. What the room receives is reported
under its own names — ``Q_room_sensible [W]``, the coil's sensible heat with
the indoor fan's heat counted (3 977 W of a 4 000 W dry-coil request at
35/27 °C), and, with a wet coil, ``Q_iu_latent [W]``.

The space-conditioning system metric is
``abs(Q_a_iu_out [W] - Q_a_iu_in [W]) / E_tot [W]`` — with a wet coil that
sensible difference plus ``Q_iu_latent [W]``, over ``E_tot [W]``. Both air energy
flows use the actual indoor airflow and the same outdoor reference temperature.
The final outlet already includes indoor fan heat: net cooling equals coil
duty minus indoor fan power, while net heating equals coil duty plus that
power. Do not add or subtract it a second time. The denominator includes
compressor, indoor fan and outdoor fan electricity.

``indoor_heat_direction_valid`` identifies delivery in the requested mode.
Opposite-direction transfer or zero airflow during active operation gives
NaN system COP; off operation has zero air energy flows and NaN COP.
This reporting diagnostic does not alter the coil-load solver.

Without ``RH_room`` the coil is dry and the model reports sensible cooling,
not total latent-plus-sensible cooling or a certified rating. With it
(`Wet indoor coil (cooling)`_) the numerator of ``cop_sys [-]`` is what the
room air gives up in total: ``Q_room_sensible [W] + Q_iu_latent [W]``, that
is, the coil's sensible and latent heat less the indoor fan's heat.
Catalogue comparisons must use the catalogue's
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
