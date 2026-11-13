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
efficiency fit was read inside its data (``efficiency_extrapolated``).
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
      - Construction fails: ``ReferenceStateError``
        (``reference_capacity_inconsistent``), naming the condensing
        temperature and the critical temperature
      - :doc:`refrigerant-and-coolprop`
    * - User-defined ``HEOS::`` mixtures
      - Not supported. The constructor raises ``ValueError`` (the default
        ``T_cond_max`` is not defined for a mixture); with
        ``T_cond_max=None`` it does not return
      - :doc:`refrigerant-and-coolprop`
    * - Two-stage compression, vapour or liquid injection, economisers
      - The cycle is single-stage. Where a real unit would need them the
        point is outside the envelope: ``failure_reason`` is
        ``"pr_above_max"`` or ``"discharge_temp_above_max"``, or the duty is
        reduced (``capacity_binding``)
      - :doc:`/developer/reference-operating-limits`
    * - Operation beyond the compressor envelope
      - Condensing above ``T_cond_max``, discharge above ``T_dis_max``, a
        pressure ratio above 11, or the shipped isentropic fit below 0.30:
        reduced duty or the off state, never a running row
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
    * - Compressor efficiencies from one data set
      - Volumetric, isentropic and electro-mechanical efficiency are three
        fits to scroll-compressor calculation data over pressure ratio
        1.50–7.15 and 33.8–72.5 rev/s. Outside that box the fit is held or
        continued, not measured
      - ``efficiency_extrapolated == True``. A default machine with a
        refrigerant of low volumetric capacity (R134a, R1234yf) is rated
        above the box; give it a larger ``V_cmp_ref``
        (:doc:`refrigerant-and-coolprop`, :doc:`/validation/defaults`)
    * - Adiabatic compressor
      - No shell heat loss: every loss ends up in the discharge gas
      - ``T_ref_cmp_out [°C]`` is an upper estimate, so the discharge limit
        binds early rather than late
    * - Wet suction without an efficiency penalty
      - When the discharge control lets the suction go wet, the dry-suction
        efficiency fits are used unchanged
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
      - ASHP (both coils) and ASHPB take the density of the air a coil draws
        in — outdoor air at ``T0``, room air at ``T_a_room`` — as dry
        ideal-gas air at 101 325 Pa
        (:func:`tmhp.air_properties.air_density`). The specific heat is
        constant (1005 J/(kg K)); humidity and altitude are left out, and fan
        power does not follow the density. The indoor coil of GSHP keeps
        15 °C standard air (1.225 kg/m³)
      - Not flagged. ``dV_ou_a [m3/s]`` and ``dV_iu_a [m3/s]`` are volumes at
        the coil inlet; at 1 600 m the real density is 18 % lower
        (:ref:`ashp-air-density`)
    * - Sensible cooling only
      - The indoor coil is dry: no dehumidification, so the cooling duty is
        the sensible one
      - ``Q_ref_iu [W]`` in cooling is below a catalogue's *total* capacity at
        a low sensible heat ratio (:doc:`/validation/index`, *Cooling and
        latent load*)
    * - Frost as a correlation (ASHP)
      - Below 5 °C outdoor the EnergyPlus frost multipliers are applied to a
        frost-free cycle. They depend on outdoor temperature and humidity,
        not on the coil the model solves, and a defrost is in the mean, not
        resolved in time
      - ``defrost_fraction [-]``, ``frost_capacity_factor [-]``,
        ``E_defrost [W]``; ``Q_ref_iu_frost_free [W]`` is the cycle that was
        solved (:doc:`/models/ashp`)
    * - No frost correction for the air-source boiler
      - ASHPB solves the frost-free cycle at every outdoor temperature
      - Not flagged. Below about 5 °C outdoor its COP and capacity are those
        of a coil that never frosts

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

A short checklist for any result
================================

.. code-block:: python

   ok = (df["failure_reason"] == "none") & df["converged"]   # an operating point (or a legitimate off row)
   averaged = df["runtime_fraction [-]"] < 1                 # a time average, not a continuous point

Then, where they exist: ``capacity_clamped == "max"`` (in a boiler's dynamic
frame, a ``capacity_binding`` column) for a row that delivered less than
asked, ``Q_unmet [W]`` or ``Q_dhw_unmet [W]`` for demand
that was not served, ``efficiency_extrapolated`` and ``discharge_control`` for
points at the edge of the compressor data. Warnings raised during a run (``UserWarning`` at
construction, ``RuntimeWarning`` for a point that fell back to off) say the
same things once, in words.
