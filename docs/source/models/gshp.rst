===================================================
Ground-source heat pump (GSHP — space conditioning)
===================================================

.. |gshp| raw:: html

   <span class="glossary" data-term="gshp">GSHP</span>

|gshp| conditions a building zone, drawing or rejecting heat through
the same g-function borehole heat exchanger as GSHPB.

Overview
========

The class is :class:`tmhp.GroundSourceHeatPump`. Use it when the heat
pump's job is space conditioning rather than DHW production.

For quick parametric studies that do not need the full refrigerant
cycle, :class:`tmhp.GroundSourceHeatPumpEmpirical` provides a simpler
EnergyPlus EquationFit COP model with the same borehole-response
backbone.

Base usage
==========

.. code-block:: python

   from tmhp import GroundSourceHeatPump

   gshp = GroundSourceHeatPump(
       ref="R410A",
       N_1=1, N_2=1,
       H_b=150.0,
   )

   # See API reference below for the full constructor and
   # analyze_steady / analyze_dynamic signatures.

Defaults that follow the capacity
=================================

``GroundSourceHeatPump(hp_capacity=..., ref=...)`` is a complete machine.
Everything on the ground side and the indoor airflow is sized from the
capacity, so the rating-point COP is the same from 1 to 100 kW:

.. list-table::
   :header-rows: 1
   :widths: 26 44 30

   * - Input left out
     - Default
     - 4 kW / 30 kW
   * - ``ground_flow_ref_lpm``
     - ``0.75 × hp_capacity / (ρc × 3 K)``: the rated source-side heat
       changes the loop temperature by 3 K (about 4.8 K in cooling, where
       the loop carries 1.2 × the capacity). Parallel boreholes share it
       equally.
     - 15.4 / 115.3 L/min (25 % ethylene glycol; 14.3 / 107.5 of water)
   * - ``E_pmp``
     - ASHRAE pumping grade B, 75 W per ton (21.3 W per kW) of capacity.
       Used when the hydraulic pump model is off.
     - 85 / 640 W
   * - ``N_1``, ``N_2``, ``H_b`` (all three)
     - Smallest near-square field of 100 m boreholes that keeps the rated
       source-side heat within 50 W/m.
     - 1 × 1 / 3 × 2
   * - ``dV_iu_fan_a_rated``
     - 5.37e-5 m³/s per W (400 cfm per ton).
     - 773 / 5 800 m³/h

The helpers are :func:`tmhp.ground_defaults.default_ground_flow_lpm`,
:func:`tmhp.ground_defaults.default_borefield_layout` and
:func:`tmhp.pump.default_ground_pump_power`. A value you supply replaces its
default. Giving any one of ``N_1``, ``N_2`` or ``H_b`` fixes the borefield as
given (the others take 1, 1 and 100 m); the flow and the pump power still
follow the capacity unless you give them too.

Two sizing slips are reported with a ``UserWarning`` at construction instead
of being solved silently:

* a U-tube that is **laminar** at the reference flow (``Re < 2300``, for
  example a flow that was not scaled with the number of parallel boreholes).
  The borehole resistance is evaluated for laminar flow (film coefficient
  ``Nu = 3.66``, interpolated to the turbulent correlation between
  ``Re`` 2300 and 4000) and roughly doubles. The Reynolds number is the
  model attribute ``Re_bhe_ref``.
* a borefield you sized that carries more than **80 W/m** at the rated
  source-side heat (attribute ``q_bhe_rated``).

At the rated duty the default indoor coil (``UA_iu_rated = 0.08 ×
hp_capacity``) at this airflow needs an indoor approach of 21.6 K: supply air
at 35 °C and condensing at 41.6 °C in heating from a 20 °C room, supply air
at 12 °C and evaporating at 5.4 °C in cooling from 27 °C (the coil is dry).
``indoor_approach_max_K`` therefore defaults to 25 K.

The indoor coil takes the density of the room air it draws in, as the coils of
:doc:`ashp` do (:ref:`ashp-air-density`): ``dV_iu_a [m3/s]`` and the fan
references and limits are volumes at the coil inlet, and the coil NTU, the fan
heat, the reported air energy flows and the air exergy all use the mass flow
``air_density(T_a_room) * dV_iu_a``. Room air at 20 to 27 °C is 1.7 to 4.0 %
lighter than the 15 °C standard air (1.225 kg/m³) earlier releases used. For
the default 4 kW unit: at a 0 °C borehole wall and a 20 °C room the system COP
is 0.4 % lower at nameplate (3.508 → 3.495) and 0.2 % lower at half load;
at the ISO 13256-1 cooling point (liquid 25 °C, room 27 °C) the compressor
runs 1.4 % faster at nameplate.

.. note::

   Until November 2026 the defaults were fixed at the values of the 4 kW
   machine (20.04 L/min, 100 W, one borehole) and the indoor airflow was
   0.0002 m³/s per W (2 880 m³/h for 4 kW). A larger machine then ran on the
   small loop: at 30 kW the system COP of rated heating against a 0 °C
   borehole wall fell from 3.8 to 2.1 and some rating points had no
   solution; 100 kW could not be constructed. To reproduce
   results from before, pass ``ground_flow_ref_lpm=20.04, E_pmp=100, N_1=1,
   N_2=1, dV_iu_fan_a_rated=0.0002 * hp_capacity,
   indoor_approach_max_K=20``. Fan limits given as absolute flows
   (``dV_iu_fan_a_min/max``) are read against the new reference airflow
   unless ``dV_iu_fan_a_rated`` is given as well.

Source-side mechanics
=====================

Same g-function-based borehole as :doc:`gshpb`. See that page for the
detailed mechanic and the g-function figure. As there, the borehole is
dynamic by default (heat capacity of fluid, pipe and grout, fluid transit;
``borehole_dynamics=False`` gives the steady borehole, see
:doc:`ground-response`): with 20 min on / 40 min off the fluid entering the
heat pump is 2.0 K warmer in heating and 3.4 K cooler in cooling than the
steady borehole gives, and ``T_bhe [°C]`` is the physical borehole wall.

``source_fluid`` names the ground-loop liquid (:ref:`source-fluid`): 25 %
ethylene glycol by default (``"MEG-25%"``, freezing at -11.0 °C), or
``"water"``, or another mixture such as ``"MPG-30%"``. The loop liquid is
never colder than its freezing point plus 3 K -- -8.0 °C for the default,
3 °C for water. A heating duty that would chill it further is reduced to the
largest one that does not (``capacity_clamped == "max"``,
``capacity_binding == "source_fluid_freezing"``), and when none does the unit
is off with ``failure_reason == "source_fluid_freezing"``. On the compressor's
speed floor the running machine has no smaller state, so against a prescribed
wall or fluid temperature (``analyze_steady``) a minimum-speed machine that
leaves the liquid below the limit is off at every request. In a dynamic step
the borehole wall answers the *mean* load: a unit that cycles with shorter
runs sees a warmer loop, and the step is reduced to the largest mean duty
whose running liquid stays on the limit (a ``"max"`` row that is an on/off
mean: ``runtime_fraction [-]`` and ``capacity_ratio [-]`` below 1, ``Q_unmet
[W]`` the part not delivered). Cooling
warms the loop and is not affected unless the ground itself is below the
limit. The ISO 13256-1 ground-loop heating point (liquid entering at 0 °C,
leaving near -3 °C) and a 0 °C borehole wall are antifreeze conditions, which
the default runs:

.. code-block:: python

   gshp = GroundSourceHeatPump()                                        # 4 kW, 25 % ethylene glycol
   gshp.analyze_steady(Q_r_iu=-4000, T0=0, T_a_room=20, T_bhe_wall=0)   # leaves at -6.3 °C, system COP 3.50
   GroundSourceHeatPump(source_fluid="water").analyze_steady(
       Q_r_iu=-4000, T0=0, T_a_room=20, T_bhe_wall=0
   )                                                                    # off: source_fluid_freezing

The default flow keeps the 3 K loop difference with the loop liquid's heat
capacity (15.4 L/min of the default glycol for 4 kW, 14.3 of water) and the
borehole resistance is evaluated with its viscosity at 0 °C, the liquid inlet
of the heating rating point (0.168 m K/W against 0.163 for water at 4 kW;
see :mod:`tmhp.source_fluid` for the design temperature of the viscosity). In the default 26 mm U-tube the default glycol is
turbulent down to about 2.6 kW per borehole; a smaller machine, or 25-30 %
propylene glycol below about 4 kW per borehole, is laminar: the constructor
then warns and ``R_b`` is about half as large again, so give a larger
``ground_flow_ref_lpm`` for such a loop. What does not follow the loop liquid
(the hydraulic pump model, the coupled ground-flow solver, the dynamic
borehole response) is listed in :ref:`source-fluid`.

In ``analyze_dynamic`` the ground fluid is closed inside each step: the
borehole wall of step ``n`` is the step mean of its response to every load
so far, the step's own included, and the fluid temperature follows from it
through ``R_b*`` and the loop flow. The cycle of a row is solved with the
``T_bhe_f_out [°C]`` reported in that row, so a heating row always has its
evaporating temperature below the fluid and a cooling row its condensing
temperature above it. (Earlier versions solved the cycle with the fluid left
by the previous step; after an off period the first step then ran on
undisturbed ground.)

Sink-side mechanics
===================

A zone temperature / load proxy stands in for the building, as in
:doc:`ashp`. The indoor-unit load ``Q_r_iu`` selects operating mode:
positive values are cooling, negative values are heating, and zero
values are off operation.

Part load: cycling and unmet load
=================================

The compressor modulates between ``rps_min`` and ``rps_max``. A request
outside what the unit delivers over that range is still answered, and the row
says how — the same convention as :doc:`ashp`.

**Below the minimum capacity** the compressor cycles on and off, and the row
is the mean over the cycling period (``capacity_clamped == "min"``):

- ``Q_ref_iu [W]`` equals the request. ``Q_min [W]`` is what the unit delivers
  running continuously at its minimum speed, and ``capacity_ratio [-]`` is the
  request divided by it (the *CR* of EN 14825).
- The electrical input carries the EN 14825 degradation for a unit delivering
  to air: ``COP = COP_min · (1 − Cd · (1 − CR))`` with ``Cd = 0.25``, the value
  the standard prescribes when a unit has not been tested for it
  (``part_load_factor [-]`` is that bracket; the table in :doc:`ashp` lists
  it). ``runtime_fraction [-]``, the share of the time the compressor runs, is
  ``CR / part_load_factor``.
- Compressor and loop-pump power (``E_cmp``, ``E_pmp``) are the running values
  times the runtime fraction. The pump is taken to run with the compressor —
  the usual interlock of a residential flow centre, and what the model already
  does in its off state, where ``E_pmp`` is zero. Heat and flow rates
  (``Q_ref_iu``, ``Q_ref_ground``, ``Q_a_iu_in``, ``Q_a_iu_out``,
  ``m_dot_ref``, ``dV_iu_a``, ``dV_bhe_f``) are the running values times *CR*,
  and so is the indoor fan's power (``E_iu_fan``): it runs while heat is
  delivered. The refrigerant cycle and the room air thus stay in balance with
  the delivered heat. Temperatures, pressures, compressor speed and
  efficiencies are those of the running machine.
- ``Q_bhe [W]`` is the mean load on the borefield: the mean ground-side duty
  less (heating) or plus (cooling) the mean pump power.
- ``E_cycling_loss [W]`` is the mean electrical power the compressor and the
  pump draw beyond *CR* times their continuous power. It is not part of any
  heat flow of the row: the ground-side duty of the mean row is the running
  duty times *CR*.

The default unit running continuously at its minimum speed delivers about
1.7 kW in heating from a 0 °C borehole wall and 2.4 kW in cooling into a 25 °C
one — 41 % and 59 % of the nameplate. Until this model every request below
that was answered with the minimum capacity itself: 8.3 times a 200 W heating
request and 11.8 times a cooling one, and over a mild 24 h schedule 18 %
(heating) and 29 % (cooling) more heat than was asked for.

.. list-table:: 4 kW R32 unit, heating at 20 °C room and 0 °C borehole wall
   :header-rows: 1
   :widths: 16 16 16 16 18 18

   * - Request
     - ``Q_ref_iu``
     - ``runtime_fraction``
     - ``E_tot``
     - ``cop_sys``
     - ``Q_bhe``
   * - 200 W
     - 200 W
     - 0.155
     - 66 W
     - 3.08
     - 148 W
   * - 1 000 W
     - 1 000 W
     - 0.669
     - 286 W
     - 3.54
     - 749 W
   * - 1 659 W (``Q_min``)
     - 1 659 W
     - 1.000
     - 430 W
     - 3.91
     - 1 252 W

``Cd_cycling`` sets the coefficient: a number replaces 0.25, ``0`` is
loss-free cycling, and ``None`` restores the earlier behaviour (continuous
operation at the minimum speed, ``Q_ref_iu [W]`` above the request,
``runtime_fraction [-]`` of 1). The operating-point search weighs the
degradation, so the minimum-speed point may sit a few watts from the one found
with ``Cd_cycling=None``.

**In a dynamic run** a cycling step loads the ground with its mean: the pulse
added to the ground history is the row's ``Q_bhe [W]``, recorded once, and the
borehole wall of the step (``T_bhe [°C]``) answers that mean load. The fluid
temperatures of the row (``T_bhe_f [°C]``, ``T_bhe_f_in [°C]``,
``T_bhe_f_out [°C]``) are those of the running machine — the borehole
resistance and the loop temperature difference act on the running load — so
the row's cycle and its fluid still belong together. Where they start from
depends on the borehole response (:doc:`ground-response`):

- with the dynamic borehole (the default), from the response over the run
  itself under the running load. The run is taken as one block at the start
  of the step, ``mean load / running load`` of it long, as for a boiler
  whose compressor runs part of a step. The heat capacity of the borehole
  then keeps the fluid from dropping by the running load's full ``q' R_b``
  the moment a short run begins, and the fluid joins that of a continuous
  step as the request reaches the minimum capacity;
- with the steady borehole (``borehole_dynamics=False``), from the step-mean
  wall under the mean load: the fluid sits the running load's ``q' R_b``
  below it at once.

The running load only sets that fluid temperature; it is never added to the
history, so the partial run is not counted twice. The on/off pattern inside a
step is not resolved beyond that one block.

**Above the maximum capacity** the unit runs flat out
(``capacity_clamped == "max"``): ``Q_ref_iu [W]`` is the ceiling,
``Q_max [W]`` repeats it and ``capacity_binding`` names what set it.
``Q_request [W]`` (signed, as asked) and ``Q_unmet [W]`` (the part of the
request that is not delivered) are on every row.

What the cycling model does not contain: no standby power is added while the
compressor is off, and a start is not resolved in time — the degradation is
the standard's seasonal-average figure.

The coupled ground-flow solver (:doc:`ground-flow`: a prescribed ground flow,
or ``ground_flow_control`` other than plain constant flow) cycles the same
way. Its minimum-speed machine is the one the fixed-flow search finds at that
loop flow, averaged as above, so a prescribed flow equal to the reference
flow returns the fixed-flow row; a variable flow is chosen for the least mean
input for the request. Only with ``Cd_cycling=None`` does that solver report
a request below its minimum-speed duty as the failure
``compressor_min_speed``.

COP boundary
============

The physics-based GSHP uses the same final indoor-air delivery definition as
:doc:`ashp`: ``cop_sys [-] = abs(Q_a_iu_out - Q_a_iu_in) / E_tot``.
The actual airflow and common outdoor reference temperature define both
air energy flows. The final outlet includes indoor fan heat once. The
denominator retains the compressor, indoor fan and **entire ground-loop
pump** electricity; this boundary may differ from a standard unit rating.
``Q_r_iu``, rated capacity, PLR and the optimisation remain coil-duty based;
``cop_ref [-]`` remains coil duty divided by compressor electricity.
Active zero-flow or opposite-mode air delivery is flagged by
``indoor_heat_direction_valid = False`` and reported with NaN system COP.
Off states have zero air energy flows and NaN COP.

Seasonal performance uses integrated actual air delivery divided by integrated
system electricity, separately by mode. Legacy outputs through ``c5d673e``
use coil-duty COP; regenerate them before comparing to the final-air metric.
The empirical alternative and DHW models retain their existing definitions.

Empirical alternative
=====================

:class:`tmhp.GroundSourceHeatPumpEmpirical` uses the ClimateMaster TCH072
ground-loop curves shipped with EnergyPlus (``WaterToAirHeatPumps.idf``). The
curves give full-load capacity and power at the rated air flow, so the COP
does not change with part load (no cycling degradation is applied) and a
request above the available capacity ``Q_cap`` is served at that ceiling
with ``capacity_clamped = "max"``. Outside the fitted range — entering water
−1.25 to 32.06 °C in heating and 9.85 to 48.72 °C in cooling, entering air
9.85 to 26.51 °C dry-bulb in heating and 9.85 to 23.73 °C wet-bulb in
cooling — the unit does not run for that step and ``failure_reason`` is
``"outside_curve_domain"``. The water temperature is judged at the
converged entering water of the step, so cold ground that the step's own
heat rejection brings into range (or warm ground in heating) still runs. The room (entering-air) temperature is the
``T_a_room`` input (27 / 21 °C when omitted).

Its conventions differ from the refrigerant-cycle model: the borehole load
``q_b`` is injection-positive W/m, temperatures ``T_b``, ``T_f``, ``T_f_in``
and ``T_f_out`` are in kelvin, ``COP`` is 0 when idle, the entering-water
temperature is the mean fluid temperature minus half the loop temperature
change, and refrigerant temperatures for the exergy terms use fixed
approaches (±3 K to the ground fluid, ∓15 K to the room air). The indoor
airflow carries the served heat across a 10 K change at the density of the
room air (``dV_a``); the design airflow of the fan curve is that change in
15 °C standard air at the rated capacity. The borehole resistance takes water
at the initial ground temperature ``T_g`` (1.14 mPa s at 15 °C).

.. autoclass:: tmhp.GroundSourceHeatPumpEmpirical
    :members:
    :show-inheritance:
    :no-index:

API reference
=============

.. automodule:: tmhp.ground_source_heat_pump
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.gshp_empirical
    :members:
    :undoc-members:
    :show-inheritance:

See :doc:`ground-flow` for opt-in flow-dependent component physics, optimal pump
control, source boundaries, and feasibility diagnostics.
