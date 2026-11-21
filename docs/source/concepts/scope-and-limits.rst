================
Scope and limits
================

TMHP aims at a useful accuracy from a nameplate (:doc:`/getting-started/minimal-input`),
not at reproducing one machine from its drawings. That bargain has a price:
some things are outside the models and are refused, and others are computed
with a simplification. This page lists both, and for each one says how it
shows in a result — the key to look at, the warning raised — so that a number
is not trusted further than the model behind it.

The limits of the compressor envelope and the meaning of every
``failure_reason`` have their own pages
(:doc:`/developer/reference-operating-limits`, :doc:`failure-reason-semantics`);
they are linked here, not repeated.

What a running result claims
============================

A row with ``hp_is_on == True``, ``converged == True`` and
``failure_reason == "none"`` is a closed single-stage vapour-compression
cycle: the energy balance holds, heat flows from warm to cold in every heat
exchanger, and the compressor is inside its envelope. It does *not* claim
that the request was met (``capacity_clamped``, ``Q_unmet [W]``), that the
compressor ran the whole time (``runtime_fraction [-]``), or that the
compressor efficiency was read inside its data (``efficiency_extrapolated``).
:doc:`result-keys` describes these keys.

Refused: outside the models
===========================

Nothing in this table is returned as if it had been met. The request fails
at construction with an error, returns the off state with a reason, or —
where a smaller duty stays inside the limit — runs at that duty with the limit
named in ``capacity_binding``.

.. list-table::
    :header-rows: 1
    :widths: 28 36 36

    * - Not modelled
      - What happens
      - Read more
    * - Transcritical cycles (R744 at heating temperatures)
      - Construction fails. A water heater: ``ValueError``, the critical
        temperature is not above the delivery set point. The other families:
        ``ReferenceStateError`` (``reference_capacity_inconsistent``), naming
        the condensing temperature and the critical temperature
      - :doc:`refrigerant-and-coolprop`
    * - User-defined ``HEOS::`` mixtures
      - Not supported. The constructor raises ``ValueError`` (the default
        ``T_cond_max`` is not defined for a mixture); with
        ``T_cond_max=None`` it does not return
      - :doc:`refrigerant-and-coolprop`
    * - Two-stage compression, vapour or liquid injection, economisers
      - The cycle is single-stage. Where a real unit would need them the
        point is outside the envelope (``failure_reason`` is
        ``"pr_above_max"`` or ``"discharge_temp_above_max"``), the duty is
        reduced (``capacity_binding``), or the point runs and reports the
        single-stage COP: the injection machines of the validation set run
        10 to 23 % below their catalogues at −7 °C (:ref:`high-lift-deficit`)
      - :doc:`/developer/reference-operating-limits`
    * - Operation beyond the compressor envelope
      - Condensing above ``T_cond_max``, discharge above ``T_dis_max`` or a
        pressure ratio above 11: reduced duty, on/off cycling at the smallest duty inside the envelope
        (a request refused only at low speed), or the off state -- never a
        running row outside it
      - :doc:`failure-reason-semantics`
    * - A source loop colder than its liquid allows
      - The loop liquid is never colder than its freezing point plus 3 K
        (25 % ethylene glycol in GSHP and GSHPB: −8.0 °C; water in WSHPB:
        3 °C). A heating duty that would chill it further is reduced
        (``capacity_binding == "source_fluid_freezing"``); when none fits the
        unit is off with ``failure_reason == "source_fluid_freezing"``
      - :ref:`source-fluid`
    * - A machine that cannot exist
      - A nameplate the default compressor cannot deliver at the rating
        point, or one whose rating point is outside the envelope, is refused
        at construction (``ReferenceStateError``)
      - :doc:`/models/reference-state`
    * - Inputs outside their physical range
      - ``ValueError`` before anything is computed (NaN, a negative capacity
        or flow, a kelvin value where °C is expected, a schedule of the wrong
        length)
      - :doc:`failure-reason-semantics`
    * - Water-source space conditioning
      - Not a released model: there is no class for it
      - :doc:`/models/index`

Simplified: computed, but approximately
=======================================

These are inside the models and return normal results. The third column says
which way the simplification leans and where it can be seen.

The refrigerant cycle
---------------------

.. list-table::
    :header-rows: 1
    :widths: 28 36 36

    * - Simplification
      - What it means
      - How it shows
    * - Compressor efficiencies by compressor type
      - Volumetric, isentropic and electro-mechanical efficiency come from
        semi-empirical forms fitted to standalone-compressor data, one set per
        ``compressor_type`` (``rotary_twin_inverter`` by default, ``scroll``,
        ``rotary_single_fixed``). The level is the manufacturers' published
        level; independent compressor measurements sit 5–15 % lower. The
        rotary types are provisional: their data span pressure ratio
        1.94–6.84 and 30–120 rev/s. The electrical input is that of the
        inverter input (drive loss about 6 % at 60 rev/s, more at low speed).
        Outside the data the forms are followed as they are, not held
      - ``efficiency_extrapolated == True`` outside the data hull of the type,
        often at ordinary points: every speed below 30 rev/s, and pressure
        ratios above 6.84 (hot water in cold weather) or below 1.94. The
        default air-source models and boilers are rated inside the hull with
        every refrigerant; the default ``GroundSourceHeatPump`` is rated below
        it (pressure ratio 1.6–1.8). :doc:`/validation/defaults`
    * - Adiabatic compressor
      - No shell heat loss: every loss ends up in the discharge gas
      - ``T_ref_cmp_out [°C]`` is an upper estimate, so the discharge limit
        binds early rather than late
    * - Wet suction without an efficiency penalty
      - When the discharge control lets the suction go wet, the efficiency
        forms, fitted to dry-suction data, are used unchanged
      - Rows with ``discharge_control == True`` are optimistic by a few
        percent in COP, and their duty at the edge of the limit is an upper
        estimate (:doc:`/developer/reference-operating-limits`)
    * - No pressure drop
      - Heat exchangers and lines are isobaric and the expansion is
        isenthalpic; superheat and subcooling are set values, not the result
        of a valve or charge model
      - Not flagged. ``P_ref_cmp_out [Pa]`` equals the condensing pressure in
        every row
    * - One conductance per heat exchanger
      - Each coil is a single ε-NTU exchanger against the refrigerant's
        saturation temperature, with a ``UA`` that follows the flow.
        Desuperheating and subcooling zones, fin geometry and refrigerant
        distribution are not resolved
      - Not flagged. The default ``UA`` rules and their spread are in
        :doc:`/validation/defaults`
    * - Quasi-steady cycle in time
      - A dynamic step solves a steady cycle; refrigerant migration and the
        warm-up after a start are not simulated. Thermal mass is in the tank
        water and the borehole only
      - Starts cost nothing in a dynamic boiler run (``hp_starts [-]`` counts
        them). Below the minimum speed, ASHP and GSHP rows and every
        ``analyze_steady`` apply the EN 14825 degradation instead
        (``E_cycling_loss [W]``)
    * - No standby power
      - An off row draws nothing: crankcase heater, controls and an idling
        pump are not modelled, except through the EN 14825 ``Cc`` of a
        boiler's steady on/off mean
      - ``E_tot [W]`` is 0 on off rows; add a standby load yourself for an
        annual balance

Air side
--------

.. list-table::
    :header-rows: 1
    :widths: 28 36 36

    * - Simplification
      - What it means
      - How it shows
    * - Dry air at one standard atmosphere
      - Every air coil (ASHP both coils, ASHPB, the GSHP indoor coil, also in
        the empirical GSHP) takes the density of the air it draws in —
        outdoor air at ``T0``, room air at ``T_a_room`` — as dry ideal-gas air
        at 101 325 Pa (:func:`tmhp.air_properties.air_density`). The specific
        heat is constant (1005 J/(kg K)); humidity and altitude are left out,
        and fan power does not follow the density
      - Not flagged. ``dV_ou_a [m3/s]`` and ``dV_iu_a [m3/s]`` are volumes at
        the coil inlet; at 1 600 m the real density is 18 % lower
        (:ref:`ashp-air-density`)
    * - Dehumidification only on request (ASHP), never in GSHP
      - Without a room humidity the indoor coil is dry: all of the cooling
        duty is sensible and no moisture is removed. With ``RH_room``
        (``RH_room_schedule`` in a dynamic run) the ASHP coil condenses water
        below the room's dew point: an apparatus-dew-point / bypass-factor
        coil whose surface is at the evaporating temperature, with the dry
        coil's ``UA``, Lewis number 1 and condensate leaving at the coil
        temperature (:ref:`ashp-wet-coil`). Not modelled: wet-surface
        enhancement, re-evaporation while the compressor cycles, frost on the
        indoor coil, a request stated as room load, heating humidification.
        The default indoor ``UA`` was not derived for a wet coil, and the
        model's sensible heat ratio lies above the catalogues'
        (:doc:`/validation/index`, *Cooling and latent load*). The GSHP
        indoor coil is always dry
      - ``Q_iu_sensible [W]``, ``Q_iu_latent [W]``, ``SHR_iu [-]`` and
        ``m_dot_iu_condensate [kg/s]`` on every ASHP row (latent 0, ratio 1
        for a dry coil); ``RH_room [-]`` is NaN where no humidity was used.
        A dry coil's ``Q_ref_iu [W]`` is all sensible: at a low sensible heat
        ratio that is not a catalogue's *total* capacity. The exergy columns
        of the indoor air stay those of a dry air stream
    * - Fans at the flow of least power
      - Each operating point is the one of least total electrical input the
        fan limits allow, to the tolerance of the search: it sets both air
        flows of an ``AirSourceHeatPump`` and the outdoor air flow of an
        ``AirSourceHeatPumpBoiler``. A unit follows its own controller and
        the fan step its user selects, so this is the favourable end of what
        the fans leave open, not a prediction of a controller. At the four
        points of :ref:`ashp-fan-control` the default 4 kW air-to-air unit
        runs its indoor fan at its largest flow; with the
        indoor flow held at half of that, the COP is 19 to 21 % lower at
        half load and 27 to 34 % lower at nameplate load, which the unit
        then falls 1 to 3 % short of
      - Not flagged. ``dV_iu_a [m3/s]`` and ``dV_ou_a [m3/s]`` are the flows
        the search chose. Equal limits hold a fan at one flow:
        ``dV_iu_fan_a_min`` / ``dV_iu_fan_a_max`` and ``dV_ou_fan_a_min`` /
        ``dV_ou_fan_a_max`` (ASHP), ``dV_fan_a_min`` / ``dV_fan_a_max``
        (ASHPB)
    * - Frost as a correlation (ASHP, ASHPB)
      - Below 5 °C outdoor the EnergyPlus frost multipliers are applied to a
        frost-free cycle. They depend on outdoor temperature and humidity,
        not on the coil the model solves, and a defrost is in the mean, not
        resolved in time. The multipliers were fitted to air-to-air units;
        the boiler takes them unchanged, with the defrost heat drawn from
        the tank water. Below the capacity of the unit the correlation
        describes, the correction shrinks with the frost drive of a less
        loaded coil (air flow taken as independent of the load); in air too
        dry to frost at all, the EnergyPlus fixed loss stays at every load
      - ``defrost_fraction [-]``, ``frost_capacity_factor [-]``,
        ``E_defrost [W]``; ``Q_ref_iu_frost_free [W]`` (ASHP) or
        ``Q_ref_tank_frost_free [W]`` (ASHPB) is the cycle that was solved
        (:doc:`/models/ashp`, :doc:`/models/ashpb`)

Ground and source water
-----------------------

.. list-table::
    :header-rows: 1
    :widths: 28 36 36

    * - Simplification
      - What it means
      - How it shows
    * - Loop liquid with constant properties
      - The properties of ``source_fluid`` are those of the mixture at 0 °C
        (water: 1000 kg/m³, 4186 J/(kg K)) and do not change with temperature
        during a run. ``UA_ground`` is not derated for an antifreeze's poorer
        film coefficient
      - Not flagged. ``T_bhe_f_in [°C]`` is the loop temperature the freezing
        limit acts on
    * - Parts of the ground model that still assume water
      - The hydraulic pump model, the flow-dependent borehole resistance,
        the coupled ground-flow solver and the dynamic borehole response use
        the properties of water also for the default glycol
      - Pump power of the hydraulic model is understated for a glycol loop,
        and loop temperatures differ by about 0.1 K; a model whose
        ``source_fluid`` you chose says so in a ``UserWarning``
        (:ref:`source-fluid`)
    * - Ground as a line-source response
      - Homogeneous ground at a constant undisturbed temperature, boreholes
        through a g-function and a borehole resistance; no seasonal surface
        wave, no gradient, no groundwater flow
      - ``T_bhe [°C]``; the limits of the dynamic borehole are listed in
        :doc:`/models/ground-response`, *Limits*
    * - ``analyze_steady`` has no ground history
      - A steady ground-source point is solved against the temperature you
        give (``T_source``, ``T_bhe_wall``, or the undisturbed ``Ts``); the
        ground does not respond
      - ``T_bhe [°C]`` of a steady row without ``T_bhe_wall`` is
        back-calculated from the load and can lie above ``Ts`` while heat is
        extracted. Use ``analyze_dynamic`` for the ground response
    * - Source water as a temperature (WSHPB)
      - The source is a prescribed water temperature around a submerged
        coil; the water body itself is not modelled
      - ``Ts [°C]`` repeats the input (:doc:`/models/wshpb`)

Demand side
-----------

.. list-table::
    :header-rows: 1
    :widths: 28 36 36

    * - Simplification
      - What it means
      - How it shows
    * - No building model
      - ASHP and GSHP take the load and the room temperature as inputs. A
        load that is not met does not cool the room
      - ``Q_unmet [W]`` reports the shortfall; feeding it back is the
        caller's job (:doc:`/integrations/index`)
    * - Fully mixed tank by default
      - The boilers' tank is one node; only GSHPB offers a multi-node tank
        (``tank_model``). Tank properties are those of water at constant
        density and specific heat
      - ``T_tank_w [°C]`` is the single tank temperature
        (:doc:`/models/gshpb`, *Tank backends*)
    * - Hot water that is not delivered
      - A draw the tank cannot serve at the set point is not an error
      - ``Q_dhw_unmet [W]`` per step and one ``DHWUnmetWarning`` per run
        (:ref:`hot-water-service`)
    * - A compressor that cannot run
      - A point outside the operating range of the machine -- a discharge
        temperature, condensing pressure or pressure ratio above the limit,
        a source liquid that would freeze -- is the off state or a reduced
        duty, not an error
      - ``failure_reason`` / ``capacity_binding`` on the row and one
        ``OperatingLimitWarning`` per steady call or dynamic run
        (:ref:`operating-limit-warning`); ``operating_range()`` for the
        standard test conditions (:ref:`operating-range`)
    * - Time step and thermostat
      - The thermostat switches inside a step, but one cycle is solved per
        step
      - ``runtime_fraction [-]``; step-size guidance in
        :ref:`time-step-and-thermostat`

What has been compared with data
================================

The air-source families are compared with manufacturer catalogues, with no
parameter fitted to them; the ground- and water-source families share the
same cycle core but have not been compared with unit data. The numbers, the
units held back and the reasons are in :doc:`/validation/index`.

.. _high-lift-deficit:

High lift: hot water in cold weather
------------------------------------

The acceptance gate of the sixth round of work
(``validation/gate/high_lift_gate.yaml``; ``uv run python -m
validation.gate.evaluate`` prints where it stands) is the catalogue COP within
10 % at A−7/W55 on the three Panasonic WH-UQ units, which publish their
displacement, airflow and compressor (a rotary without injection, the
machine the single-stage cycle represents). With the present defaults two of
the three points are within it and the third is not evaluated:

.. list-table::
    :header-rows: 1
    :widths: 26 14 14 16 30

    * - Unit, A−7/W55
      - Catalogue COP
      - Model
      - Difference
      - At the solved point
    * - WH-UQ09HE8 (R410A, 9 kW)
      - 2.02
      - 1.962
      - −2.9 %
      - pressure ratio 7.4, 90 rev/s, isentropic efficiency 0.806
    * - WH-UQ12HE8 (R410A, 12 kW)
      - 1.92
      - not evaluated
      - capacity short
      - pressure ratio 7.6, at the 120 rev/s ceiling: 96 % of the duty
    * - WH-UQ16HE8 (R410A, 16 kW)
      - 1.86
      - 1.890
      - +1.6 %
      - pressure ratio 8.3, 119 rev/s, isentropic efficiency 0.797

WH-UQ12HE8 runs at its speed ceiling and delivers 96 % of the published duty,
so its COP is not compared (``capacity_short``, :doc:`/validation/index`).
With the ceiling raised the model meets the duty at 126 rev/s; the default
ceiling is 120 rev/s (:doc:`/developer/reference-operating-limits`). The gate
is therefore not met as it is written: one of its three points has no
comparable COP.

All three points are solved above the rotary data: the rotary forms rest on
data up to a pressure ratio of 6.84 (``efficiency_extrapolated`` is true at
each), and the rotary volumetric efficiency there is the form followed past
its data. How far that reaches is recorded with the coefficients
(``validation/coefficients/semi-empirical-2026-11-15/eta_v_high_pr.csv``): at
a pressure ratio of 8 the three PR slopes the archive compares give 0.728 to
0.763 for R32 at 60 rev/s.

In the previous release, with the BITZER quadratic map, the three points ran
−19.9, −15.6 and −12.9 % below the catalogue; that map held its isentropic
efficiency at 0.371 beyond a pressure ratio of 7.15. Of the machines without
injection, the six rotaries of the parity set, the 17 points evaluated at −7 °C run
+2.6 % on average (:doc:`/validation/index`).

**Vapour injection, which the model does not have.** The Panasonic T-CAP M
units (WH-WXG09/12/16, R290) are sold with an injection scroll; a
single-stage cycle (`Refused: outside the models`_) cannot hold their
efficiency or their discharge temperature at these pressure ratios. Their
A−7/W55 and A−7/W65 points (−22.7 and −20.8 % at 9 kW, −16.6 and −15.8 % at
12 kW, −9.7 and −10.9 % at 16 kW) are therefore reported beside the gate, not
part of it. They stay in the parity results of :doc:`/validation/index`,
where the four injection machines (with the Samsung flash-injection unit) run
−9.6 % on average and the six without injection −2.2 %.

For a result: at outdoor temperatures below about 2 °C and leaving water at
55 °C or more, a single-stage machine is read from compressor forms past
their data, and a machine with injection is under-predicted, by 10 to 23 %
at these points.

A short checklist for any result
================================

.. code-block:: python

   ok = (df["failure_reason"] == "none") & df["converged"]   # an operating point (or a legitimate off row)
   averaged = df["runtime_fraction [-]"] < 1                 # a time average, not a continuous point

Then, where they exist: ``capacity_clamped == "max"`` for a row that delivered less than
asked, ``Q_unmet [W]`` or ``Q_dhw_unmet [W]`` for demand
that was not served, ``efficiency_extrapolated`` and ``discharge_control`` for
points at the edge of the compressor data. ``tmhp.OperatingLimitWarning`` says the
same things once, in words: at construction for a set point the compressor
envelope does not reach, from ``analyze_steady`` for a point that does not run
or runs below the request, and at the end of ``analyze_dynamic`` with the
steps stopped or limited and what they left unserved
(``df.attrs["operating_limits"]``; :ref:`operating-limit-warning`). Before a
simulation, ``hp.operating_range()`` solves the standard test conditions of
the machine's kind and says at which it runs, at which it runs reduced, and
what stops the rest (:ref:`operating-range`).
