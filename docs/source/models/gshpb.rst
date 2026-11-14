======================================
Ground-source heat pump boiler (GSHPB)
======================================

.. |gshpb| raw:: html

   <span class="glossary" data-term="gshpb">GSHPB</span>

The ``GSHPB`` family pairs the shared refrigerant cycle with a
ground-loop source side (vertical borehole field) and a DHW tank
sink.

Overview
========

|gshpb| solves the closed refrigerant cycle against a borehole heat
exchanger characterised by a precomputed **g-function**. The class is
:class:`tmhp.GroundSourceHeatPumpBoiler`. Composed variants
extend it the same way ASHPB's do:

- :class:`tmhp.GSHPB_STC_preheat`
- :class:`tmhp.GSHPB_STC_tank`
- :class:`tmhp.GSHPB_STC_ground`
- :class:`tmhp.GSHPB_STC_routed`
- :class:`tmhp.GSHPB_PV_ESS`

The base class and all variants report ``E_tot [W]`` as the whole electric
draw: compressor ``E_cmp`` + source-loop pump ``E_pmp`` + the collector-loop
pump ``E_stc_pump`` (tank, ground, routed; the mains-driven preheat has none)
+ the UV lamp ``E_uv`` (the base class and ``GSHPB_PV_ESS`` with ``uv=``; the
STC variants do not simulate a ``uv=`` lamp and warn if one is passed). The
lamp is counted once: ``GSHPB_PV_ESS`` routes the base class's ``E_tot``.
A model run twice with the same inputs gives the same result.

The collector, its irradiance inputs (direct-normal ``I_DN`` and
diffuse-horizontal ``I_dH``, transposed to the collector plane with the sun
position from ``latitude``/``longitude``/``utc_offset_h``/``start_time``), the
loop controller with its storage high limit and the preheat circuit are the
same as for the air-source variants — see :doc:`ashpb`, "Solar inputs and the
collector model". The STC scenario logic is shared by both families
(``tmhp._stc_scenarios``).

Base usage
==========

.. code-block:: python

   from tmhp import GroundSourceHeatPumpBoiler

   gshpb = GroundSourceHeatPumpBoiler(
       ref="R290",
       N_1=1, N_2=1,      # single borehole
       H_b=150.0,         # depth [m]
   )

   result = gshpb.analyze_steady(
       T_tank_w=55.0,
       T_source=10.0,     # ground-loop fluid inlet [°C]
       Q_ref_tank=8_000,
   )

Default sizing
==============

``hp_capacity`` (default 8 kW) and the refrigerant are enough to build a
machine: every other size follows the capacity, so the rated cycle is the same
at 1 kW and at 100 kW (B0/W35: condensing 5 K above the tank, evaporating about
6 K below the entering fluid, COP of the cycle 4.2 with the default
refrigerant, R290, and the default efficiency map). B0 is a brine point: the
loop liquid leaves the heat pump at -3 °C. The default loop liquid is
therefore an antifreeze, 25 % ethylene glycol; a loop declared as water is
refused there (see :ref:`source-fluid`).

.. list-table::
    :header-rows: 1
    :widths: 26 34 40

    * - Argument
      - Default
      - Rule
    * - ``UA_tank_hx``
      - ``hp_capacity / 5`` [W/K]
      - 5 K condensing approach at the rated duty (as ASHPB).
    * - ``UA_ground``
      - ``0.18 × hp_capacity`` [W/K]
      - Fluid-to-refrigerant evaporator, the coefficient of
        :class:`tmhp.GroundSourceHeatPump` (``ground_hx_ua_per_capacity``).
    * - ``ground_flow_ref_lpm``
      - ``0.75 × hp_capacity / (ρ c · 3 K)``
      - Rated source-side heat at a 3 K loop temperature difference:
        ``ρ c`` is that of ``source_fluid``: 3.84 L/min per kW of the default
        25 % ethylene glycol (30.8 L/min at 8 kW), 3.58 of water, shared evenly
        by the boreholes.
    * - ``E_pmp``
      - ``75 W/ton × hp_capacity``
      - Upper bound of ASHRAE ground-loop pumping grade B, 21.3 W per kW
        (171 W at 8 kW). Used when the hydraulic pump model
        (``hydraulic_pump``) is off; it enters ``cop_sys`` in full and warms
        the fluid entering the evaporator.
    * - ``N_1``, ``N_2``, ``H_b``
      - 200 m boreholes on a near-square grid
      - With none of the three given, boreholes are added until the rated
        source-side heat (``0.75 × hp_capacity``) is at most 50 W per metre:
        one borehole up to 13.3 kW, 2 at 14 kW, 2 × 2 at 30 kW, 3 × 3 at
        100 kW.

A value you pass is used as given (the deprecated aliases ``UA_tank``,
``UA_cond_design``, ``UA_evap_design`` and ``dV_b_f_lpm`` keep their
precedence). The constructor warns (``UserWarning``) when

- a field you describe carries more than 80 W/m at the rated point;
- the default flow is laminar in the U-tubes (``Re < 2300``, e.g. many
  boreholes on a small machine, or a viscous antifreeze mixture);
- ``T_tank_w_upper_bound`` plus the tank heat-exchanger approach
  (``hp_capacity / UA_tank_hx``) is at or above the refrigerant's critical
  temperature: no cycle exists there, so the tank could not reach the set
  point.

The compressor envelope also ends at a condensing pressure (``T_cond_max``,
by refrigerant: R410A, R32, R407C 65 °C; R290 75 °C; R134a 80 °C; see
:doc:`/developer/reference-operating-limits`). The cycle condenses at the
tank temperature plus the tank heat-exchanger approach, so the highest tank
temperature the machine heats at its nameplate duty is ``T_cond_max`` less
``hp_capacity / UA_tank_hx``. A ``T_tank_w_upper_bound`` above that cut-out
is capped at it (``UserWarning``; ``T_tank_w_setpoints_requested`` keeps the
values asked for), and the thermostat stops the heat pump there.
``analyze_steady`` against a hotter tank returns the duty that condenses on
the limit (``capacity_clamped == "max"``, ``capacity_binding ==
"condensing_temperature"``), or the off state with
``failure_reason == "condensing_temp_above_max"`` when no duty fits. A 65 °C
set point is inside the envelope of R290 and
R134a; R410A, R32 and R407C reach about 60 °C. This is why the default
refrigerant is R290 (it was R410A): the default machine charges its tank to
the default 65 °C set point at 70 °C condensing, 5 K inside the limit, at the
nameplate duty all the way. With ``ref="R410A"`` give
``T_tank_w_upper_bound=60``.

Cycle closures
==============

Two relations hold in every running result (``analyze_steady`` and each
``analyze_dynamic`` row).

- **Suction superheat.** The gas leaves the evaporator no hotter than the
  entering ground fluid: the superheat is ``dT_superheat`` or the evaporator
  approach less ``dT_hx_min``, whichever is smaller (the same cap as ASHPB).
- **Tank heat exchanger on a speed bound.** When the request is below what the
  compressor delivers at ``rps_min`` (``capacity_clamped == "min"``) or above
  its ``rps_max`` duty (``"max"``), the condensing temperature is solved from
  the heat actually delivered, ``Q_ref_tank = UA_tank_hx × dT_ref_tank``, not
  from the request. ``dT_ref_tank [K]`` is the approach the cycle ran at and
  ``tank_hx_residual [W]`` the remaining imbalance (zero unless the closure
  could not be bracketed, as on the pressure-ratio floor). On the speed floor
  the running machine no longer depends on the request.

The evaporator approach is searched between 1 and 20 K and, when the heat
exchanger already carries the duty at 1 K (a compressor at low speed against
an evaporator sized for the full capacity), down to 0.05 K.

.. _source-fluid:

Ground-loop liquid and its freezing limit
=========================================

``source_fluid`` says what circulates between the borefield and the
evaporator (:mod:`tmhp.source_fluid`). The same argument and the same rule
apply to :class:`tmhp.GroundSourceHeatPump` and
:class:`tmhp.WaterSourceHeatPumpBoiler`. Left out, the two ground-source
models run on **25 % ethylene glycol** (``"MEG-25%"``) and the water-source
boiler on **water**: a ground loop is rated and operated with brine (EN 14511
B0, ISO 13256-1 liquid entering at 0 °C), and of the common fills this one
stays turbulent in the default U-tube at the default flow.

.. list-table::
    :header-rows: 1
    :widths: 22 16 18 14 16 14

    * - ``source_fluid``
      - Freezing point
      - Operating limit
      - ``ρ c`` vs water
      - Viscosity
      - Conductivity
    * - ``"MEG-25%"`` (default of GSHP and GSHPB)
      - -11.0 °C
      - -8.0 °C
      - 0.93
      - 3.7 mPa s
      - 0.465 W/(m K)
    * - ``"water"`` (default of WSHPB)
      - 0.0 °C
      - 3.0 °C
      - 1.00
      - 1.0 mPa s
      - 0.606 W/(m K)
    * - ``"MPG-20%"``
      - -7.2 °C
      - -4.2 °C
      - 0.96
      - 4.3 mPa s
      - 0.471 W/(m K)
    * - ``"MPG-25%"``
      - -9.8 °C
      - -6.8 °C
      - 0.95
      - 5.5 mPa s
      - 0.450 W/(m K)
    * - ``"MPG-30%"``
      - -12.8 °C
      - -9.8 °C
      - 0.94
      - 7.1 mPa s
      - 0.428 W/(m K)
    * - ``"MEG-30%"``
      - -14.6 °C
      - -11.6 °C
      - 0.91
      - 4.3 mPa s
      - 0.446 W/(m K)
    * - ``"ethanol-20%"``
      - -11.1 °C
      - -8.1 °C
      - 1.02
      - 5.2 mPa s
      - 0.448 W/(m K)

``MPG`` is propylene glycol, ``MEG`` ethylene glycol; the number is the
concentration by mass, anything from 5 to 60 % (``"MPG-33%"``). Freezing point
and properties of a mixture are read from CoolProp's incompressible mixtures
(``INCOMP::MEG``, ``MPG``, ``MEA``: the fits of Å. Melinder, *Properties of
Secondary Working Fluids for Indirect Systems*, IIR, 2010) at 0 °C, the liquid
inlet of the brine rating points, and are constants during a run; water keeps
the constants the models have always used (1000 kg/m³, 4186 J/(kg K),
1.0 mPa s, 0.606 W/(m K)). The Reynolds number of the source side (the
laminar-flow warning and the film inside the U-tube in ``R_b``) takes the
viscosity at the heating rating inlet instead, 0 °C for GSHP and GSHPB and
10 °C for WSHPB: the table's value for a mixture, and for water 1.79 mPa s
(ground loop) or 1.31 mPa s (water-source boiler) rather than 1.0
(:meth:`tmhp.source_fluid.SourceFluid.viscosity`, attribute
``mu_bhe_f_design``).
A :class:`tmhp.source_fluid.SourceFluid` built by hand stands for any other
liquid or any other limit.

**The limit.** The loop liquid is never colder than its freezing point plus
3 K. In heating the coldest liquid is the one leaving the evaporator
(``T_bhe_f_in [°C]``). The 3 K is the distance between that liquid and the
evaporator surface: with the default source heat exchanger and flow the
refrigerant evaporates 2.9 K below the leaving liquid at the rated duty, so
the margin keeps the refrigerant, and the plate it boils on, at or above the
freezing point. It is also inside the range of manufacturers' installation
rules, which ask for a freezing point 10-15 °F below the lowest *entering*
loop temperature (2.6-5.3 K below the leaving liquid at a 3 K loop
difference) and for antifreeze whenever the loop may enter below 40-45 °F.

**At the limit** a variable-speed unit lowers its compressor speed, and so does
the model (``analyze_steady`` applies the limits in this order: the compressor
envelope with its condensing limit reduces the duty first, the loop liquid's
limit is held on what the envelope allows, and a request below the
minimum-speed duty is then reported as the mean of on/off operation; a
compressor already on its speed floor has no smaller state, so there the
limit switches it off). A duty that would chill the liquid below the limit is reduced to the
largest one that does not: the result runs with ``capacity_clamped == "max"``
and ``capacity_binding == "source_fluid_freezing"``, ``Q_max [W]`` being what it
delivers. When no duty helps -- the liquid already arrives below the limit, or
it leaves below it even at the compressor's lowest speed -- the compressor does
not run: the off state with ``failure_reason == "source_fluid_freezing"`` (heat
and power 0, COP NaN) and a ``tmhp.OperatingLimitWarning`` that names the
liquid's limit and the temperature it would have run at. In ``analyze_dynamic``
the limit is applied to the liquid while the compressor runs
(``T_bhe_f_in_running [°C]``); reduced steps carry ``capacity_binding``, off
steps the ``failure_reason``, and the warning at the end of the run counts both
(:ref:`operating-limit-warning`).

.. code-block:: python

   gshpb = GroundSourceHeatPumpBoiler()                        # 8 kW, 25 % ethylene glycol
   gshpb.analyze_steady(T_tank_w=35, T_source=0, Q_ref_tank=8000)    # 8.0 kW, leaves at -3.0 °C
   gshpb.analyze_steady(T_tank_w=35, T_source=-6, Q_ref_tank=8000)   # 5.3 kW, leaves at -7.9 °C
   gshpb.analyze_steady(T_tank_w=35, T_source=-10, Q_ref_tank=8000)  # off: source_fluid_freezing

   water = GroundSourceHeatPumpBoiler(source_fluid="water")
   water.analyze_steady(T_tank_w=45, T_source=10, Q_ref_tank=8000)   # 8.0 kW, leaves at 7.0 °C
   water.analyze_steady(T_tank_w=45, T_source=5, Q_ref_tank=8000)    # 5.5 kW, leaves at 3.0 °C
   water.analyze_steady(T_tank_w=45, T_source=0, Q_ref_tank=8000)    # off: source_fluid_freezing

**What the loop liquid changes.** Its ``ρ c`` enters the loop temperature
difference, the evaporator effectiveness and the default flow (which keeps the
3 K); its viscosity and conductivity enter the borehole resistance ``R_b`` (the
film inside the U-tube) and the laminar-flow warning. Five things do not
follow it:

- ``UA_ground`` is the conductance you give or ``0.18 × hp_capacity``; it is not
  derated for the mixture's poorer film coefficient. Pass a smaller value when
  you know it.
- The default pump power is the ASHRAE grade B allowance for the capacity,
  whatever the liquid. A grade is a property of the whole installation, pipe
  sizes included, so a glycol loop designed to grade B has it too.
- The pipe loss of the hydraulic pump model (``hydraulic_pump``, ``pump_map``,
  ``ground_flow_control="optimal_power"``), the flow-dependent borehole
  resistance (``variable_Rb``) and the fluid-temperature closure of the
  coupled ground-flow solver are evaluated with the properties of water
  (:mod:`tmhp.ground_loop`, :mod:`tmhp.ground_flow_control`), also for the
  default glycol. The pump power of the hydraulic model is then understated
  (the pipe loss is computed at 1.0 instead of 3.7 mPa s), and the loop
  temperature of the coupled solver is closed with a heat-capacity flow 7 %
  too large: a 16 °C borehole wall is reported as 15.97 °C. For these options
  ``source_fluid="water"`` is the consistent choice above 6 °C. A model whose
  ``source_fluid`` you chose says so in a ``UserWarning``; one left on its
  default does not.
- :meth:`tmhp.borefield.FieldGroundResponse.attach` evaluates the borehole
  resistance of its field for water.

- The dynamic borehole response (``borehole_dynamics``, on by default) is built
  with the heat capacity and density of water
  (:func:`tmhp.borehole_capacity.enable_borehole_dynamics`). It cancels the
  loop temperature difference ``Q / (2 ṁ c)`` at the start of a run with
  water's ``ṁ c``, 7 % larger than that of the default flow of the default
  glycol: the liquid entering the heat pump then starts 0.11 K above the
  ground instead of at it (8 kW machine, rated duty).

The flow optimiser of ``ground_flow_control="optimal_power"`` does not know
the limit either: it picks the flow of least total power, which near the limit
can be a low flow with a large loop temperature difference. The duty is then
reduced, or the point refused, where a larger flow would have run. Prescribe
the flow (constant control, ``ground_flow_lpm``) for a loop that runs near its
limit.

At 0 °C a glycol mixture is four to seven times as viscous as water. In the
default 26 mm U-tube the default 3 K flow of the default 25 % ethylene glycol
is turbulent down to about 2.6 kW per borehole (``Re`` 2300 at 10 L/min;
3520 for a 4 kW borehole, 7040 for 8 kW). Below that, and for propylene
glycol of the same strength below about 4 kW per borehole, the flow is
laminar, the borehole resistance rises by half and the constructor warns:
give a larger ``ground_flow_ref_lpm`` or a smaller pipe.

.. note::

   Until November 2026 the loop liquid was water with no freezing point: a
   cold source returned a running cycle with "water" at -3 °C (B0) or -12 °C.
   The ground-source models now default to 25 % ethylene glycol, which runs
   down to -8.0 °C and whose properties move the results a little (system
   COP of the 4 kW ``GroundSourceHeatPump`` against a 0 °C borehole wall
   3.508, 3.520 before, and 3.495 since its indoor coil takes the room-air
   density; B0/W35 and B0/W55 of this boiler are the same with
   the glycol as they were with water, 3.870 and 2.651 with R290), and a loop
   declared as water stops at 3 °C. To reproduce the
   earlier numbers exactly -- the properties of water and no limit to speak
   of -- pass a hand-built fluid:

   .. code-block:: python

      from tmhp.source_fluid import SourceFluid

      legacy = SourceFluid("water without a freezing point", -60.0, rho=1000.0, cp=4186.0, mu=0.001, k=0.606)
      gshpb = GroundSourceHeatPumpBoiler(source_fluid=legacy)

.. _boiler-steady-cycling:

Steady state below the minimum capacity
=======================================

A request below what the compressor delivers at ``rps_min`` is met by
switching it on and off. ``analyze_steady`` reports such a point
(``capacity_clamped == "min"``) as the mean over the cycling period, with the
same keys as :doc:`ashp` and :doc:`gshp` (:mod:`tmhp.cycling`):

- ``Q_ref_tank [W]`` equals the request. ``Q_min [W]`` is the duty of the
  running machine and ``capacity_ratio [-]`` the request divided by it (the
  *CR* of EN 14825).
- ``runtime_fraction [-]``, the share of the time the compressor runs, equals
  *CR*: heat and refrigerant rates (``Q_ref_tank``, ``Q_ref_ground``,
  ``m_dot_ref``) are the running values times it. This is how a dynamic step
  reports a compressor that ran part of the step.
- The electrical inputs (``E_cmp``, ``E_pmp``, ``E_tot``) carry the EN 14825
  degradation for a unit delivering to water,
  ``COP = COP_min · CR / (Cc · CR + 1 − Cc)``. The standard takes the start-up
  loss of a hydronic unit as negligible and charges the power the unit draws
  while its compressor is off; ``Cc`` is one minus that power over the
  running input. The mean input is therefore the running input times
  ``CR + (1 − Cc)(1 − CR)``, and both COPs are the continuous ones times
  ``part_load_factor [-]``. ``E_cycling_loss [W]`` is the off-state part.
- ``Q_bhe [W]`` is the mean ground-side heat less the mean pump power.
- Temperatures, pressures and speed are those of the running machine; the
  loop flow keeps its running value, as in a dynamic step.

``Cd_cycling`` is that coefficient ``Cc`` (the constructor argument has the
same name in every family). The default 0.9 is the value the standard
prescribes for a unit that has not been tested: an off-state draw of a tenth
of the running input. For a unit with a measured standby power use
``Cd_cycling = 1 − P_off / P_on``; ``1.0`` is loss-free cycling; ``None``
returns the continuous minimum-speed point, which delivers more than the
request (the behaviour before this report existed).

.. list-table:: EN 14825 degradation with ``Cc = 0.9``
   :header-rows: 1
   :widths: 25 25 25 25

   * - ``capacity_ratio``
     - ``part_load_factor``
     - Mean input / running input
     - ``runtime_fraction``
   * - 0.1
     - 0.526
     - 0.190
     - 0.1
   * - 0.2
     - 0.714
     - 0.280
     - 0.2
   * - 0.4
     - 0.870
     - 0.460
     - 0.4
   * - 0.6
     - 0.938
     - 0.640
     - 0.6
   * - 0.8
     - 0.976
     - 0.820
     - 0.8
   * - 1.0
     - 1.000
     - 1.000
     - 1.0

The default machine (8 kW, R290) at a 0 °C source and a 35 °C tank delivers
1.81 kW at its minimum speed. A 400 W request used to return those 1.81 kW at
574 W of input; it now returns 400 W at 171 W (system COP 2.34 against 3.16
for the running machine), and a 1 kW request 1 kW at 342 W (COP 2.92). (With
R410A, whose rated speed is lower, the minimum-speed duty is 3.05 kW.)

A dynamic run is not affected. There the tank thermostat switches the
compressor, which runs at ``hp_capacity`` — not at its minimum speed — for
``runtime_fraction [-]`` of a step, and the row already is the step mean
(next section). No standby power is added to the off time of a dynamic run:
the 10 % default belongs to the standard's part-load points, and applied to
a storage heater whose compressor runs a fifth of the day it would add more
than 40 % to the daily electricity.

Dynamic operation
=================

``analyze_dynamic`` follows the tank thermostat (``T_tank_w_lower_bound``
/ ``T_tank_w_upper_bound``, 55 / 65 °C) inside each step: a step reports
the fraction of it the compressor ran (``runtime_fraction [-]``), its
starts (``hp_starts [-]``), and heat, power and ground heat averaged
over the step. ``min_on_time_s`` / ``min_off_time_s`` (default 0) hold
the compressor to a minimum run and rest time. See
:ref:`time-step-and-thermostat`.

On a step the compressor runs only part of, loads are kept by source: the
compressor extracts only while it runs, a solar collector charging the
ground (``GSHPB_STC_ground``, ``GSHPB_STC_routed``) injects for the whole
step. The ground history and the borehole wall take the step-average net
load (the ground's step-mean response). The reported fluid temperatures
``T_bhe_f*`` are step-time averages -- the borehole resistance and loop
temperature difference are linear in the load, so continuous in the run
fraction and equal to the injection-only state when the compressor does
not run. ``T_bhe_f*_running`` are the fluid while the compressor runs (its
running extraction net of the injection), which the evaporator's
source-side exergy uses. With the dynamic borehole (the default) they are
the mean over the run itself, from the borehole's response to the running
load over that part of the step, so they do not jump when the thermostat
switches inside a step (:doc:`ground-response`); ``T_bhe`` is the physical
borehole wall.

The cycle of a step is solved against that running fluid of the *same*
step: the borehole's response to the step's own load, the borehole
resistance and the loop temperature difference are closed together with
the cycle (to 10\ :sup:`-4` K), so in every running row
``T_ref_evap_sat < T_bhe_f_out_running`` (and, with the steady borehole,
``T_bhe_f_out_running <= T_bhe_f_out``). (Earlier releases
fed the evaporator the previous step's outlet, which could put the
evaporating temperature above the row's own fluid.) A ground coupler
without ``preview_wall_temperature_rise`` cannot evaluate an uncommitted
load; with such a backend the cycle still reads the previous step's outlet.

The constructor's ``dt_s`` (default 3600 s) is not the simulation step:
it sets the shortest lag time tabulated for the g-function (never below
one hour; shorter lags are interpolated linearly from zero). Pass the
simulation step to ``analyze_dynamic``. Hourly steps keep the tank
within its bounds, but they average hot-water draws over the hour; use
10–15 min steps or shorter when the draw profile matters.


Source-side mechanics
=====================

For ground-source models, the source-side dynamics are encoded in a
**g-function** — the dimensionless thermal response of a borehole
field to a unit heat-extraction step. TMHP precomputes the
g-function once via
`pygfunction <https://github.com/MassimoCimmino/pygfunction>`_ and
interpolates it during the simulation, so the per-step cost stays
constant whether the field is one borehole or a hundred.

The precomputed grid covers ``1.5 × t_max_s`` (default one year). A longer
simulation does not extrapolate it: the g-function is recomputed by
pygfunction to cover the requested time (about 0.2 s for a 10 × 10 field), so
a multi-year run gives the same ground temperatures whatever ``t_max_s`` was.
Each step ``n`` reports the borehole-wall and fluid temperatures averaged over
``[t_n, t_n + dt]``, including the response to that step's own load; with an
hourly load history the hourly means are the same for ``dt`` = 60, 600 or
3600 s. Below one hour the g-function alone is the straight line from zero to
its one-hour value and a borehole resistance has no heat capacity, which
would move the source temperature by 3 to 5 K at every start and stop. The
models therefore add, by default and from the inputs they already have, the
heat capacity of fluid, pipe and grout, the short-time wall response and the
fluid transit through the U-tube (``borehole_dynamics``; ``False`` gives the
steady borehole, and ``model.borehole_dynamics`` says which one is in use).
A compressor that runs for part of a step sees the fluid of its own run, so
its source temperature is continuous through thermostat switching. Bore
fields other than a rectangular grid are set up with :mod:`tmhp.borefield`.
All of this is described in :doc:`ground-response`.

.. figure:: ../_static/g_function_curve.svg
    :alt: g-function vs ln(t/t_s) for three rectangular borehole
        field geometries: 1×1, 2×2, and 4×4.
    :align: center
    :width: 100%

    Dimensionless g-function for three rectangular borehole-field
    geometries. The 1 × 1 field is the single-borehole baseline;
    2 × 2 and 4 × 4 diverge as borehole-to-borehole thermal
    interference accumulates over the multi-year horizon. Generated
    by ``scripts/visualization/g_function_curve.py``.

Sink-side mechanics
===================

Single-node DHW tank, implicit per-step solve — the same demand-side
sink used by the air-source and water-source boiler families. A step whose
implicit solve returns no root is solved again with the same energy balance
by bracketing over 0-200 °C; if the balance has no solution there the step is
taken explicitly with the whole heat rate (heat pump, draw-off, loss,
subsystems) and a ``tmhp._tank_solve.TankSolveWarning`` names it. The tank
temperature is not held at the mains temperature: a tank in a cold room cools
below it.

Every step reports the hot-water service: ``Q_dhw_request [W]`` is the
scheduled draw at the set point ``T_mix_w_out`` counted from the mains
temperature, ``Q_dhw_unmet [W]`` the part not delivered because the water
left the mixing valve below the set point (the tank was colder than it) or
a level-varying tank cut the draw. ``analyze_dynamic`` warns once
(``tmhp.dhw.DHWUnmetWarning``) when a run leaves demand unmet; see
:ref:`hot-water-service` for the run totals.

A step on which the thermostat called but the compressor could not run (an
off row with a ``failure_reason``), or ran below the nameplate duty
(``capacity_clamped == "max"``), is not reported while the run goes on.
``analyze_dynamic`` ends with one ``tmhp.OperatingLimitWarning`` that counts
such steps by limit and says what they did to the tank, and the same totals
are in ``df.attrs["operating_limits"]``; ``analyze_steady`` warns once about a
point that does not run or runs reduced (:ref:`operating-limit-warning`).

A tank whose level varies (``tank_always_full=False`` or
``prevent_simultaneous_flow``) follows the rules of the air-source boiler
(:doc:`ashpb`, Sink-side mechanics): the draw stops at ``TANK_LEVEL_MIN``
(0.1 % of the volume), the make-up at full, and the reported
``tank_level [-]``, ``dV_tank_w_out [m3/s]`` and ``dV_tank_w_in [m3/s]``
are the ones the step's energy balance was solved with.
``dV_mix_w_out [m3/s]`` is then the mixed water delivered and
``dV_mix_w_out_unmet [m3/s]`` the demand the tank could not supply. Such a
run starts with ``tank_level_init`` of at least ``TANK_LEVEL_MIN``.

Tank backends
-------------

``tank_model`` selects how the tank itself is resolved (always-full tank,
no ``stc``/``pv``/``uv`` subsystem in the multi-node modes). A multi-node tank
keeps its volume -- every litre drawn is replaced at once -- so it cannot
hold the draw and the make-up apart: ``prevent_simultaneous_flow=True`` with
``"stratified"`` or ``"hybrid"`` raises ``ValueError`` (use the lumped tank
for a level that falls during a draw):

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - ``tank_model``
     - Tank
   * - ``"lumped"`` (default)
     - One fully mixed node.
   * - ``"stratified"``
     - ``n_tank_nodes`` nodes (:class:`tmhp.stratified_tank.StratifiedTank`,
       sub-stepped to a Courant number of 1): smooth, numerically diffusive.
   * - ``"hybrid"``
     - The same nodes with a tracked thermocline
       (:class:`tmhp.hybrid_tank.HybridStratifiedTank`): a sharp front
       between drawn and stored water.

In the multi-node modes the condenser is a coil immersed in the tank:
``UA_tank_hx`` spread evenly over the nodes, or all of it at
``condenser_node`` (0 = top). Heat flows only into water colder than the
refrigerant:

- The coil exchanges while the compressor runs. Over a step node ``i``
  receives ``f·UA_i·(T_end − T_i)⁺``, with ``f`` the row's
  ``runtime_fraction``, ``T_i`` the node's end-of-step temperature and
  ``T_end`` the exchanger temperature at which the nodes receive the step's
  condenser heat ``Q_ref_tank``, solved by the tank step: the condensing
  temperature the run ends on. A node as hot as the refrigerant receives
  nothing and no node is heated past ``T_end``.
- The cycle condenses at the temperature that coil needs. With cold water
  at the bottom and hot water above the refrigerant temperature, only the
  cold nodes take heat and the cycle condenses near them, below what the
  mean tank temperature would give; in a mixed tank the two agree, and a
  one-node ``"stratified"`` tank reproduces ``"lumped"``.
- The cycle of a step is solved at the **mean** of the condensing
  temperatures its run passes through, as the lumped tank solves it at the
  mean tank temperature of the run: the power of a step is the power along
  that path, not the power at the temperature the run ends on. The mean is
  the thermostat's mean tank temperature of the run plus how far the coil's
  condensing level lies above the tank's mean, read from trial steps of the
  node model over the run. ``T_ref_cond_sat_l [°C]`` is that mean;
  ``T_ref_cond_sat_l_peak [°C]`` is the highest condensing temperature of
  the step (the level the run ends on when it warms its water), which is
  the one no heated water exceeds. Before, a step took the level it ended
  on whenever the tank warmed by more than the coil's approach in it, so a
  long step paid for a hotter condenser than its run had: a coil over the
  whole tank, 55 to 65 °C without a draw, 0.348 kWh at 60 s steps against
  0.364 kWh at 600 s (+4.7 %; now 0.348 and 0.347, the lumped tank's).
- The condensing temperature a step ends on is not taken above the
  condensing limit. A step whose heat would need more is held instead: the
  heat goes to the coil's nodes below the envelope stop and none is heated
  past it; if they cannot take all of it the row's ``runtime_fraction``,
  heat and power are what they took
  (``hp_stop_reason == "condensing_limit"``) and the thermostat call
  stands. It is the same run in the next step, not a new start.
- A coil that reaches its envelope stop while the mean the thermostat reads
  is at the upper set point (within 0.5 K) has satisfied the thermostat:
  the call ends and the tank cools to the lower bound before the next
  start, as in the lumped tank. The two coincide whenever the set point is
  the envelope's own -- a requested set point above the envelope stop is
  capped at it (R32: 65 to 60 °C) -- and the coil spans the tank.
- The water around the coil can reach the envelope stop (the tank
  temperature at which the nameplate duty condenses on ``T_cond_max``,
  above) while the mean the thermostat reads is still below the set point.
  The in-step thermostat stops the compressor when the coil's condensing
  level reaches the limit: after the run time at the nameplate duty that
  trial steps of the node model give for it (not at a mean temperature: with
  a large draw the coil's water warms while the mean falls). While the call
  stands it then holds the coil's water there: it runs the share of the rest
  of the step that delivers the heat the water loses (to the draw, the nodes
  below, the wall), never more than the nameplate duty at any moment -- when
  a cold front reaches the coil inside a long step the hold lasts until it
  would take more, and the compressor then runs flat out while the coil's
  water cools. The row is a normal
  one (``failure_reason == "none"``, ``hp_stop_reason ==
  "condensing_limit"``), and the condensing temperature never needs to go
  above the limit. Before, each such step was an off row with
  ``condensing_temp_above_max`` and two or three cycle solves, and the
  results depended on the step (a top coil, R290, 6 h: 353 of 360 steps at
  60 s, 30 of 36 at 600 s); the heat delivered now agrees from 60 s to 600 s
  steps (0.2738 kWh in that run), and so does the electricity (0.0899 and
  0.0897 kWh; 0.0924 and 0.0953 before).

Results of a multi-node tank agree between 60 s and 600 s steps within
about 2 % of heat and 5 % of electricity, the lumped tank's own margin
(a top coil under a 6 L/min draw that empties the tank in an hour, R290:
5.93 and 6.05 kWh of heat, 1.90 and 1.95 kWh of electricity; 1.91 and
2.12 kWh of electricity before). What remains is the node model's time
resolution: the cold front of a draw smears faster in long steps
(``docs/tank_backends/tank_backends.md``). The trial steps that read the
coil's level draw what the step itself does (the mixing valve takes more
tank water as the top cools), and the top node ends a step at the envelope
stop at most.

The thermostat reads the volume-average temperature ``T_tank_w [°C]``, so
on/off timing and delivered heat follow the lumped tank; with a coil that
reaches the top, the top node can sit several kelvin above
``T_tank_w_upper_bound`` while staying below ``T_cond``. Its tank balance
draws the hot water from the top node, not at the mean: the two take the
same heat while both are above the mixing set point, and a tank a draw has
left cold under a hot top still gives the scheduled heat (the lumped
balance would give lukewarm water, and a mean that rises while the tank's
falls). Hot water leaves
from the top: the mixing valve, ``T_mix_w_out``, ``Q_tank_w_out`` and
``X_tank_w_out`` use ``T_tank_w_out [°C]``, the temperature of the water that
left during the step. The multi-node modes add ``T_tank_w_out``,
``T_tank_w_top``, ``T_tank_w_bottom`` and ``T_ref_cond_sat_l_peak`` to the
result, and ``Q_tank_loss``
is the wall loss of the node profile. Each run closes
``Σ Q_ref_tank·dt = C_tank·ΔT_tank_w + Σ ρc·dV_tank_w_out·(T_tank_w_out −
T_tank_w_in)·dt + Σ Q_tank_loss·dt``. The storage exergy ``Xst_tank`` is still
evaluated at the mean temperature.

Composed variants
=================

Usage patterns for the tank-side variants follow the same demand-side
composition pattern as the air-source boiler family — see :doc:`ashpb`
for full STC preheat and PV + ESS examples. The ground-side STC
variants route collected solar heat into the borehole field instead
of, or in exclusive alternation with, the tank.

.. tab-set::
   :class: composition-tabs

   .. tab-item:: Base

      The standalone GSHPB: borehole-field g-function source, R290 cycle,
      DHW tank charge.

      .. code-block:: python

         from tmhp import GroundSourceHeatPumpBoiler

         gshpb = GroundSourceHeatPumpBoiler(
             ref="R290",
             N_1=1, N_2=1,
             H_b=150.0,
         )
         result = gshpb.analyze_steady(
             T_tank_w=55.0,
             T_source=10.0,
             Q_ref_tank=8_000,
         )

   .. tab-item:: + STC preheat

      Adds a flat-plate STC that preheats mains water entering the
      tank. Reduces the tank-charge duty the heat pump has to deliver.

      .. code-block:: python

         from tmhp import GSHPB_STC_preheat
         from tmhp.subsystems import SolarThermalCollector

         stc = SolarThermalCollector(A_stc=4.0, stc_tilt=35.0, stc_azimuth=180.0)
         model = GSHPB_STC_preheat(stc=stc, ref="R290", N_1=1, N_2=1, H_b=150.0)

   .. tab-item:: + STC tank loop

      A pumped collector loop draws tank water, heats it and returns it,
      under a differential controller with a storage high limit
      (``T_stc_tank_max``, 85 °C).

      .. code-block:: python

         from tmhp import GSHPB_STC_tank
         from tmhp.subsystems import SolarThermalCollector

         stc = SolarThermalCollector(A_stc=4.0, stc_tilt=35.0, stc_azimuth=180.0)
         model = GSHPB_STC_tank(stc=stc, ref="R290", N_1=1, N_2=1, H_b=150.0)

   .. tab-item:: + STC ground

      STC injects collected heat into the borehole loop for seasonal
      ground charging; the DHW tank balance remains heat-pump-only.

      .. code-block:: python

         from tmhp import GSHPB_STC_ground
         from tmhp.subsystems import SolarThermalCollector

         stc = SolarThermalCollector(A_stc=4.0, stc_tilt=35.0, stc_azimuth=180.0)
         model = GSHPB_STC_ground(stc=stc, ref="R290", N_1=1, N_2=1, H_b=150.0)

   .. tab-item:: + STC routed

      A per-step router sends solar heat either to the tank or to the
      borehole field. The default policy serves a cold tank first, then
      charges the ground.

      .. code-block:: python

         from tmhp import GSHPB_STC_routed
         from tmhp.subsystems import SolarThermalCollector

         stc = SolarThermalCollector(A_stc=4.0, stc_tilt=35.0, stc_azimuth=180.0)
         model = GSHPB_STC_routed(stc=stc, ref="R290", N_1=1, N_2=1, H_b=150.0)

   .. tab-item:: + PV / ESS

      Photovoltaic generation + ESS preferentially feeds the
      compressor and auxiliaries.

      .. code-block:: python

         from tmhp import GSHPB_PV_ESS
         from tmhp.subsystems import EnergyStorageSystem, PhotovoltaicSystem

         model = GSHPB_PV_ESS(
             pv=PhotovoltaicSystem(),
             ess=EnergyStorageSystem(),
             ref="R290",
             N_1=1, N_2=1, H_b=150.0,
         )

STC preheat
-----------

.. autoclass:: tmhp.GSHPB_STC_preheat
    :members:
    :show-inheritance:
    :no-index:

STC tank loop
-------------

.. autoclass:: tmhp.GSHPB_STC_tank
    :members:
    :show-inheritance:
    :no-index:

STC to ground loop
------------------

Besides ``Q_solar_ground`` the result carries the collector columns of the tank
variant (``Q_sol_stc``, ``Q_stc_w_in``, ``Q_stc_w_out``, ``Q_stc_pump_w_out``,
``Q_l_stc``, ``T_stc_*``, ``dV_stc``, ``E_stc_pump``), so the collector balance
``Q_sol_stc = (Q_stc_w_out - Q_stc_w_in) + Q_l_stc`` and
``Q_stc_w_out - Q_stc_w_in = Q_solar_ground`` can be checked step by step.
The loop runs inside the ``preheat_start_hour``–``preheat_end_hour`` window
when the collector controller allows it (outlet at least ``dT_stc_off`` above
the borehole wall, gain above the pump power). The freeze protection and the
stagnation lock-out described in :doc:`ashpb` apply
(``stc_freeze_protected [-]``, ``stc_stagnation_lockout [-]``): the loop does
not run while the idle collector is below ``T_stc_freeze`` or the borehole
wall is below 0 °C (the loop carries water; ``T_stc_freeze=None`` for an
antifreeze loop), and a collector that idled above ``T_stc_lockout`` while
the clock window was closed is not started until it has cooled.

.. autoclass:: tmhp.GSHPB_STC_ground
    :members:
    :show-inheritance:
    :no-index:

STC routed between tank and ground
----------------------------------

The result carries the same collector columns as :class:`~tmhp.GSHPB_STC_tank`.
While the collector is active, every step closes
``Q_sol_stc = (Q_stc_w_out - Q_stc_w_in) + Q_l_stc`` and
``Q_stc_w_out - Q_stc_w_in = Q_solar_tank + Q_solar_ground`` (one of the two is
zero). On the tank route the collector is reported at the solved tank
temperature, i.e. the heat that entered the tank balance, and charging stops
at ``T_stc_tank_max`` within the step as in :class:`~tmhp.GSHPB_STC_tank`.
A router that returns ``"off"`` in strong sun leaves the collector idle: at
or above ``T_stc_lockout`` the loop is then locked out
(``stc_stagnation_lockout [-]``) and neither route runs until the collector
has cooled.

.. autoclass:: tmhp.GSHPB_STC_routed
    :members:
    :show-inheritance:
    :no-index:

PV + ESS
--------

Routing and balances are the same as for :class:`~tmhp.ASHPB_PV_ESS` (see
:doc:`ashpb`): no grid export (surplus is ``E_dump``), every loss is a column
(``Q_l_ctrl``, ``Q_l_inv``, ``Q_l_ess``) and each step closes
``E_pv_out = Q_l_ctrl + E_pv_to_inv + E_ess_chg + E_dump`` and
``E_tot = E_inv_out + E_grid_import`` with ``E_tot = E_cmp + E_pmp + E_uv``.

.. autoclass:: tmhp.GSHPB_PV_ESS
    :members:
    :show-inheritance:
    :no-index:

API reference
=============

.. automodule:: tmhp.ground_source_heat_pump_boiler
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.gshpb_stc_preheat
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.gshpb_stc_tank
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.gshpb_stc_ground
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.gshpb_stc_routed
    :members:
    :undoc-members:
    :show-inheritance:

.. automodule:: tmhp.gshpb_pv_ess
    :members:
    :undoc-members:
    :show-inheritance:

See :doc:`ground-flow` for opt-in flow-dependent component physics, optimal pump
control, source boundaries, and feasibility diagnostics.
