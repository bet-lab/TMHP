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
        4 to 19 % below their catalogues at A−7/W55 and A−7/W65
        (:ref:`high-lift-deficit`)
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
    * - One compressor's efficiencies for every machine
      - Volumetric, isentropic and electro-mechanical efficiency come from
        three maps of one 46 cm³/rev R290 scroll (134 measured points), in
        the pressure ratio, the speed and the evaporating temperature, the
        same for every refrigerant and every compressor. The electrical input
        is that of the inverter input; there is no separate drive model.
        Applied without calibration the defaults run low against the
        catalogues (COP bias −5.9 % air-to-water, −12.1 % air-to-air).
        Outside the data the maps are followed as they are, not held
      - ``efficiency_extrapolated == True`` outside the data (30–110 rev/s
        and the measured hull in pressure ratio and evaporating
        temperature), often at ordinary points: every speed below 30 rev/s
        and every speed above 110 rev/s. Every family is rated inside the
        data with each of R32, R410A, R290, R134a, R1234yf and R600a.
        :doc:`/validation/defaults`
    * - Compressor losses as on one compressor
      - The share of the electrical input that does not reach the gas (motor,
        inverter and the heat the shell gives off, ``1 − eta_cmp``) is that
        of the measured R290 scroll at the same pressure ratio and speed
      - Not flagged. ``T_ref_cmp_out [°C]`` follows the discharge
        temperatures of that compressor, not those of the machine modelled
    * - Wet suction without an efficiency penalty
      - When the discharge control lets the suction go wet, the efficiency
        maps, fitted to dry-suction data, are used unchanged
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
    * - Sensible cooling only
      - The indoor coil is dry: no dehumidification, so the cooling duty is
        the sensible one
      - ``Q_ref_iu [W]`` in cooling is below a catalogue's *total* capacity at
        a low sensible heat ratio (:doc:`/validation/index`, *Cooling and
        latent load*)
    * - Fans at the flow of least power
      - Each operating point is the one of least total electrical input the
        fan limits allow, to the tolerance of the search: it sets both air
        flows of an ``AirSourceHeatPump`` and the outdoor air flow of an
        ``AirSourceHeatPumpBoiler``. A unit follows its own controller and
        the fan step its user selects, so this is the favourable end of what
        the fans leave open, not a prediction of a controller. At the four
        points of :ref:`ashp-fan-control` the default 4 kW air-to-air unit
        runs its indoor fan at its largest flow (in heating at 2 kW, to the
        tolerance of the search); with the indoor flow held at half of that,
        the COP is 12 to 16 % lower at half load and 24 to 34 % lower at
        nameplate load, which the unit then falls 1 to 3 % short of
      - Not flagged. ``dV_iu_a [m3/s]`` and ``dV_ou_a [m3/s]`` are the flows
        the search chose. Equal limits hold a fan at one flow:
        ``dV_iu_fan_a_min`` / ``dV_iu_fan_a_max`` and ``dV_ou_fan_a_min`` /
        ``dV_ou_fan_a_max`` (ASHP), ``dV_fan_a_min`` / ``dV_fan_a_max``
        (ASHPB)
    * - Frost as a correlation (ASHP, ASHPB)
      - Below 5 °C outdoor the EnergyPlus frost multipliers are applied to a
        frost-free cycle. They depend on outdoor temperature and humidity,
        not on the coil the model solves, and a defrost is in the mean, not
        resolved in time. Air too dry to frost the correlation's coil is not
        corrected; the 3 K band over which the fixed losses come in is a
        modelling choice, not a measurement. The multipliers were fitted to air-to-air units;
        the boiler takes them unchanged, with the defrost heat drawn from
        the tank water. Below the capacity of the unit the correlation
        describes, the correction shrinks with the frost drive of a less
        loaded coil (air flow taken as independent of the load)
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
the three points are within it and the third is just outside it:

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
      - 1.817
      - −10.0 %
      - pressure ratio 7.3, 68 rev/s, isentropic efficiency 0.659
    * - WH-UQ12HE8 (R410A, 12 kW)
      - 1.92
      - 1.817
      - −5.3 %
      - pressure ratio 7.5, 92 rev/s, isentropic efficiency 0.641
    * - WH-UQ16HE8 (R410A, 16 kW)
      - 1.86
      - 1.763
      - −5.2 %
      - pressure ratio 8.0, 84 rev/s, isentropic efficiency 0.637

All three deliver the published duty below the 120 rev/s ceiling, so each
COP is compared (:doc:`/validation/index`). WH-UQ09HE8 lies just past the
tolerance, so the gate is not met as it is written.

All three points are solved inside the data of the maps (pressure ratio,
speed and evaporating temperature; ``efficiency_extrapolated`` is false at
each), which reach a pressure ratio of 12.67 at an evaporating temperature
of −30 °C. In the previous release, with the semi-empirical
models, two of the three were within the gate (−2.9 and +1.6 %) and
WH-UQ12HE8 fell short of the duty at the ceiling. Of the machines without
injection, the six rotaries of the parity set, the 18 points evaluated at
−7 °C run −3.6 % on average (:doc:`/validation/index`).

**Vapour injection, which the model does not have.** The Panasonic T-CAP M
units (WH-WXG09/12/16, R290) are sold with an injection scroll; a
single-stage cycle (`Refused: outside the models`_) cannot hold their
efficiency or their discharge temperature at these pressure ratios. Their
A−7/W55 and A−7/W65 points (−18.6 and −14.8 % at 9 kW, −11.0 and −8.7 % at
12 kW, −3.9 and −3.5 % at 16 kW) are therefore reported beside the gate, not
part of it. They stay in the parity results of :doc:`/validation/index`,
where the four injection machines (with the Samsung flash-injection unit) run
−8.5 % on average and the six without injection −4.4 %.

For a result: at outdoor temperatures below about 2 °C and leaving water at
55 °C or more, the three single-stage machines of the gate run 5 to 10 %
below their catalogues, and a machine with injection is under-predicted, by
4 to 19 % at these points.

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
