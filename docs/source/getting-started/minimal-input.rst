=============
Minimal input
=============

TMHP is meant to run from a nameplate, not from a component datasheet. For
every released family three statements describe a machine:

1. **which family** — the class says what the heat comes from and where it
   goes (and, for a ground loop, that it is filled with antifreeze);
2. **the capacity** — ``hp_capacity`` [W], the nameplate duty at the family's
   rating point;
3. **the refrigerant** — ``ref``, a CoolProp fluid name.

Everything else has a default, and the defaults that describe the *size* of
the machine follow the capacity, so a 3 kW and a 30 kW unit of one family are
the same machine at two scales. This page says what each family needs from
you, what it fills in, and where each rule comes from.

What you give
=============

.. list-table::
    :header-rows: 1
    :widths: 14 20 22 44

    * - Family
      - Class (defaults)
      - ``hp_capacity`` is the duty at
      - An operating point needs
    * - ASHP — air to air, heating and cooling
      - :class:`~tmhp.AirSourceHeatPump` (4 kW, R32)
      - ISO 5151 T1 cooling: outdoor 35 °C, indoor 27 °C
      - ``Q_r_iu`` (load: negative heating, positive cooling), ``T0``
        (outdoor air), ``T_a_room``
    * - GSHP — ground to air, heating and cooling
      - :class:`~tmhp.GroundSourceHeatPump` (4 kW, R32)
      - ISO 13256-1 ground-loop cooling: liquid entering 25 °C, indoor 27 °C
      - ``Q_r_iu``, ``T0``, ``T_a_room``; the ground is ``Ts`` (16 °C) unless
        ``T_bhe_wall`` is given
    * - ASHPB — air to hot-water tank
      - :class:`~tmhp.AirSourceHeatPumpBoiler` (15 kW, R134a)
      - EN 14511 A7/W55
      - ``T_tank_w``, ``T0`` (outdoor air), ``Q_ref_tank`` (heat to the tank)
    * - GSHPB — ground to hot-water tank
      - :class:`~tmhp.GroundSourceHeatPumpBoiler` (8 kW, R290)
      - EN 14511 B0/W55
      - ``T_tank_w``, ``T_source`` (loop fluid entering the heat pump),
        ``Q_ref_tank``
    * - WSHPB — water loop to hot-water tank
      - :class:`~tmhp.WaterSourceHeatPumpBoiler` (8 kW, R290)
      - EN 14511 W10/W55
      - ``T_tank_w``, ``T_source`` (source water entering), ``Q_ref_tank``

The rating points are those of :doc:`/models/reference-state`: at
construction the compressor's rated speed is solved so that the machine
delivers exactly ``hp_capacity`` there, whatever the refrigerant.

.. code-block:: python

   from tmhp import (
       AirSourceHeatPump,
       AirSourceHeatPumpBoiler,
       GroundSourceHeatPump,
       GroundSourceHeatPumpBoiler,
       WaterSourceHeatPumpBoiler,
   )

   # Space conditioning: the load is signed (negative = heating).
   ashp = AirSourceHeatPump(hp_capacity=5_000, ref="R32")
   a2a = ashp.analyze_steady(Q_r_iu=-5_000, T0=7.0, T_a_room=20.0)

   gshp = GroundSourceHeatPump(hp_capacity=5_000, ref="R32")
   g2a = gshp.analyze_steady(Q_r_iu=-5_000, T0=0.0, T_a_room=20.0)

   # Water heating: the load is the heat the tank receives.
   ashpb = AirSourceHeatPumpBoiler(hp_capacity=9_000, ref="R290")
   a2w = ashpb.analyze_steady(T_tank_w=50.0, T0=7.0, Q_ref_tank=9_000)

   gshpb = GroundSourceHeatPumpBoiler(hp_capacity=9_000, ref="R290")
   g2w = gshpb.analyze_steady(T_tank_w=50.0, T_source=10.0, Q_ref_tank=9_000)

   wshpb = WaterSourceHeatPumpBoiler(hp_capacity=9_000, ref="R290")
   w2w = wshpb.analyze_steady(T_tank_w=50.0, T_source=10.0, Q_ref_tank=9_000)

   for name, row in [("ASHP", a2a), ("GSHP", g2a), ("ASHPB", a2w), ("GSHPB", g2w), ("WSHPB", w2w)]:
       print(f"{name:6s} {row['failure_reason']:5s} COP {row['cop_sys [-]']:.2f}")

Each call returns a running point (``failure_reason == "none"``) with a system
COP of 4.74, 4.86, 3.44, 3.66 and 3.72 respectively. The keys of the result
are listed in :doc:`/concepts/result-keys`. A time series needs the same
quantities as schedules; see :doc:`first-dynamic-simulation`.

What follows the capacity
=========================

Leave these out and they scale with ``hp_capacity``; pass a value and it is
used as given. The constructed model carries the result under the same name
(``ashp.UA_ou_rated``, ``gshp.ground_flow_ref_lpm``, …), so a default can
always be read back.

.. list-table::
    :header-rows: 1
    :widths: 22 34 16 28

    * - Quantity
      - Default rule
      - Families
      - Where it comes from
    * - Compressor displacement ``V_cmp_ref``
      - 42 cm³/rev per 9 kW of nameplate (4.67 cm³/rev per kW), any
        refrigerant
      - all
      - :doc:`/validation/defaults`, *Compressor displacement*
    * - Compressor speed range
      - ``rps_rated`` solved at the rating point; ``rps_min`` 15 rev/s;
        ``rps_max`` = ``max(min(2 × rps_rated, 120), 1.5 × rps_rated)``
      - all
      - :doc:`/models/reference-state`,
        :doc:`/developer/reference-operating-limits`
    * - Outdoor-coil conductance
      - ASHP: ``UA_ou_rated = hp_capacity / 5`` [W/K]. ASHPB:
        ``0.7 × UA_tank_hx`` = ``0.14 × hp_capacity``
      - ASHP, ASHPB
      - :doc:`/validation/defaults`, *Heat-exchanger conductance*
    * - Indoor-coil conductance
      - ASHP: ``UA_iu_rated = 0.8 × UA_ou_rated`` = ``0.16 × hp_capacity``.
        GSHP: ``0.08 × hp_capacity``
      - ASHP, GSHP
      - :doc:`/validation/defaults`; :doc:`/models/gshp`
    * - Tank heat exchanger ``UA_tank_hx``
      - ``hp_capacity / 5`` [W/K]: a 5 K condensing approach at the nameplate
        duty
      - ASHPB, GSHPB, WSHPB
      - :doc:`/validation/defaults`; :doc:`/models/gshpb`, *Default sizing*
    * - Source-side heat exchanger (liquid to refrigerant)
      - ``0.18 × hp_capacity`` [W/K] (``UA_ground_rated``, ``UA_ground``,
        ``UA_water``)
      - GSHP, GSHPB, WSHPB
      - :doc:`/models/gshp`, :doc:`/models/gshpb`, :doc:`/models/wshpb`
    * - Rated outdoor airflow
      - ASHP: 720 m³/h per kW. ASHPB: 540 m³/h per kW. The fan modulates down
        to 15 % of it
      - ASHP, ASHPB
      - :doc:`/validation/defaults`, *Summary*
    * - Rated indoor airflow
      - 193 m³/h per kW (400 cfm per ton), modulating down to 15 %
      - ASHP, GSHP
      - :doc:`/models/ashp`, *Indoor airflow*
    * - Fan power
      - From the airflow at 60 Pa and 60 % fan efficiency: outdoor fan 2.0 %
        (ASHP) or 1.5 % (ASHPB) of the nameplate at rated flow, indoor fan
        0.5 %
      - ASHP, GSHP, ASHPB
      - :doc:`/models/ashp`, *Indoor airflow*
    * - Source-loop flow
      - ``0.75 × hp_capacity / (ρ c · 3 K)``: a 3 K loop temperature
        difference at the rated source-side heat, with ``ρ c`` of the loop
        liquid — 3.84 L/min per kW of the default glycol (GSHP, GSHPB), 3.58
        of water (WSHPB)
      - GSHP, GSHPB, WSHPB
      - :doc:`/models/gshp`, *Defaults that follow the capacity*
    * - Source-loop pump ``E_pmp``
      - 21.3 W per kW (75 W per ton, ASHRAE pumping grade B)
      - GSHP, GSHPB, WSHPB
      - :doc:`/models/gshp`; :doc:`/models/gshpb`, *Default sizing*
    * - Borefield ``N_1``, ``N_2``, ``H_b``
      - Smallest near-square field that keeps the rated source-side heat
        (``0.75 × hp_capacity``) within 50 W per metre: 100 m boreholes for
        GSHP, 200 m for GSHPB
      - GSHP, GSHPB
      - :doc:`/models/gshp`; :doc:`/models/gshpb`, *Default sizing*
    * - Source-water coil length ``H_b``
      - 200 m, or as long as 50 W per metre needs
      - WSHPB
      - :doc:`/models/wshpb`, *Default sizing*

The size that the rules give for a machine can be checked in one line:

.. code-block:: python

   from tmhp import GroundSourceHeatPump

   gshp = GroundSourceHeatPump(hp_capacity=30_000)

   print(gshp.N_1, gshp.N_2, gshp.H_b)   # 3 2 100.0  -> six 100 m boreholes
   print(gshp.ground_flow_ref_lpm)       # 115.3 L/min of 25 % ethylene glycol
   print(gshp.E_pmp)                     # 640 W
   print(gshp.UA_ground_rated)           # 5400 W/K

The constructor warns (``UserWarning``) when a size you give contradicts the
rest — a borefield above 80 W/m, a loop flow that is laminar in the U-tubes, a
tank set point the refrigerant cannot reach — instead of solving it silently.

What follows the refrigerant
============================

.. list-table::
    :header-rows: 1
    :widths: 24 46 30

    * - Quantity
      - Default rule
      - Where it comes from
    * - Rated compressor speed
      - Solved for the fluid at the rating point, so the nameplate capacity
        is kept when the refrigerant is swapped. A fluid of low volumetric
        capacity (R134a, R1234yf, R600a) then runs fast and may need a larger
        ``V_cmp_ref``
      - :doc:`/concepts/refrigerant-and-coolprop`
    * - Discharge-temperature limit ``T_dis_max``
      - 150 °C for R32, 135 °C for every other refrigerant
      - :doc:`/developer/reference-operating-limits`
    * - Condensing limit ``T_cond_max``
      - R410A, R32, R407C 65 °C; R290, R1234yf 75 °C; R134a 80 °C; R717
        70 °C; otherwise the dew point at 0.9 of the critical pressure
      - :doc:`/developer/reference-operating-limits`, *Condensing limit*
    * - Highest tank set point (boilers)
      - ``T_cond_max`` less the 5 K condenser approach: a
        ``T_tank_w_upper_bound`` above it is capped with a ``UserWarning``
        (about 60 °C for R410A and R32, 70 °C for R290)
      - :doc:`/models/gshpb`, *Default sizing*

The default refrigerants are chosen so the default machine runs inside these
limits: R32 for the space-conditioning families, R134a for ASHPB (60–65 °C
tank) and R290 for GSHPB and WSHPB (55–65 °C tank).

What is on by default
=====================

These defaults describe how a real unit behaves at the edges of its range.
Each can be switched off to see the bare cycle.

.. list-table::
    :header-rows: 1
    :widths: 24 46 30

    * - Behaviour
      - Default
      - Switch
    * - Compressor envelope
      - A point outside it is not returned as running: pressure ratio above
        11, discharge above ``T_dis_max`` (after suction control down to
        quality 0.9), condensing above ``T_cond_max``, isentropic fit below
        0.30. A request that a smaller duty meets runs at that duty
      - ``PR_cycle_max``, ``T_dis_max=None``, ``x_suction_min``,
        ``T_cond_max=None``
        (:doc:`/developer/reference-operating-limits`)
    * - On/off operation below the minimum speed
      - ``analyze_steady`` returns the mean of on/off cycling with the
        EN 14825 degradation: ``Cd`` 0.25 for the units delivering to air
        (ASHP, GSHP), ``Cc`` 0.9 for the boilers
      - ``Cd_cycling`` (``None``: continuous minimum-speed point)
        (:doc:`/models/ashp`, :doc:`/models/gshpb`)
    * - Frost and defrost
      - ASHP heating below 5 °C outdoor carries the EnergyPlus frost
        adjustment at 84 % relative humidity
      - ``defrost=None``; ``RH_out`` per call (:doc:`/models/ashp`)
    * - Dynamic borehole
      - GSHP and GSHPB resolve the heat capacity of fluid, pipe and grout
        and the fluid transit
      - ``borehole_dynamics=False`` (:doc:`/models/ground-response`)
    * - Source-loop liquid and its freezing limit
      - GSHP and GSHPB run on 25 % ethylene glycol (``"MEG-25%"``), WSHPB on
        water. The loop liquid is never colder than its freezing point plus
        3 K (−8.0 °C for the glycol, 3 °C for water): a duty that would chill
        it further is reduced, and when none fits the unit is off with
        ``failure_reason == "source_fluid_freezing"``
      - ``source_fluid`` (``"water"``, ``"MPG-30%"``, …)
        (:ref:`source-fluid`)
    * - Tank thermostat (boilers, dynamic)
      - Heats between ``T_tank_w_lower_bound`` and ``T_tank_w_upper_bound``,
        switching inside a step; no minimum run or rest time
      - ``min_on_time_s``, ``min_off_time_s``, ``hp_on_schedule``
        (:ref:`time-step-and-thermostat`)

What does not follow the capacity
=================================

A few defaults describe an installation rather than a machine and stay where
they are when ``hp_capacity`` changes. Check them when your case is far from
the default size.

.. list-table::
    :header-rows: 1
    :widths: 30 40 30

    * - Quantity
      - Default
      - Families
    * - Tank volume (``r0``, ``H``)
      - 151 L (ASHPB), 101 L (GSHPB, WSHPB)
      - boilers
    * - Thermostat band
      - 60–65 °C (ASHPB), 55–65 °C (GSHPB, WSHPB)
      - boilers
    * - Hot-water set point and mains temperature
      - 40 °C at the tap, 15 °C mains
      - boilers
    * - Ground
      - 16 °C undisturbed, conductivity 2.0 W/(m K), 6 m borehole spacing,
        single U-tube in a 160 mm borehole
      - GSHP, GSHPB
    * - Superheat and subcooling
      - 3 K / 3 K (ASHP, GSHPB), 5 K / 5 K (GSHP, ASHPB, WSHPB)
      - all
    * - Room temperature when none is given
      - 27 °C (the cooling rating condition) — pass ``T_a_room`` for heating
      - ASHP, GSHP

When you know more
==================

Any default can be replaced one at a time: a measured ``UA``, a catalogue
airflow, a tested ``Cd_cycling``, a compressor map as ``eta_cmp_isen``. The
validation harness does exactly that with what manufacturers publish
(:doc:`/validation/index`), and :doc:`/validation/adding-a-catalogue` shows
the pattern. What the models leave out altogether is listed in
:doc:`/concepts/scope-and-limits`.
