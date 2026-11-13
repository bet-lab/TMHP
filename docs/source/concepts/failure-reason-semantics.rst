============================
``failure_reason`` semantics
============================

Every ``analyze_steady`` result carries a ``failure_reason`` key. It
is a diagnostic *report* — independent of whether the result dict
contains usable cycle numbers — that lets callers branch on *why* a
step looks the way it does without having to inspect the cycle
internals. This page is the reference for what each value means and
when to branch on it.

Invalid input versus an unsolvable point
========================================

The two are kept apart. An input outside its physical range — NaN or infinity,
a negative capacity, UA, flow or load, an efficiency outside (0, 1], a
temperature that cannot be °C (a kelvin value such as ``Ts=300``), a lower tank
set point above the upper one, a schedule whose length is not the step count, or
a ``simulation_period_sec`` that ``dt_s`` does not divide — raises
``ValueError`` before anything is computed, with the message
``"<name> must be …, got <value>"``. Valid inputs for which no cycle closes are
not errors: they return a result whose ``failure_reason`` says why. A zero boiler
load (``Q_ref_tank = 0``) is valid and returns the off result with
``failure_reason == "none"``; a negative or NaN one is invalid.

The values
==========

``analyze_steady`` can return the values in the tables below. The first seven
are common to every released family (ASHPB, GSHPB, WSHPB, ASHP, GSHP); the
remaining ones are specific to one family or to the ground-coupled solve path.

Common to all families
----------------------

.. list-table::
    :header-rows: 1
    :widths: 25 35 40

    * - Value
      - Means
      - Result dict carries cycle numbers?
    * - ``none``
      - Cycle closed and the SciPy optimiser converged.
      - Yes — trust ``E_cmp``, ``Q_ref_*``, ``cop_*``.
    * - ``hx_not_converged``
      - HX residual exceeded tolerance, but the cycle itself
        produced a state.
      - Depends on the family. ASHPB, GSHPB, WSHPB, ASHP and every
        ``analyze_dynamic`` row: no — off-mode (``hp_is_on = False``,
        ``E_cmp = 0``, ``Q_ref_tank = 0``, NaN COP). GSHP
        ``analyze_steady``: yes — the unconverged cycle state, to be
        treated as approximate. ``converged == False`` throughout. On the
        boilers this reason is reported only when no smaller duty runs
        either (see *A request above a limit* below).
    * - ``optimizer_failed``
      - SciPy couldn't satisfy its own success criteria, even
        though the cycle returned a state.
      - Depends on the family. ASHPB, GSHPB, WSHPB, ASHP and every
        ``analyze_dynamic`` row: no — off-mode. GSHP
        ``analyze_steady``: yes — numbers exist, but the
        evaporating-temperature choice is not provably optimal.
    * - ``cycle_invalid``
      - The cycle itself was infeasible at the requested
        operating point, or the solved cycle is not an operating
        point (``failure_detail`` says which check rejected it). The
        model falls back to off-mode (``E_cmp = 0``,
        ``Q_ref_tank = 0``).
      - No — only off-mode placeholders.
    * - ``pr_above_max``
      - The compressor pressure ratio exceeded ``PR_cycle_max``
        (default 11 for every family, the single-stage scroll limit). The
        single-stage envelope is rejected rather than clamped, so no cycle
        state is produced. Reported instead of ``cycle_invalid`` when the
        last pressure-ratio check was a ceiling violation. A boiler
        request above the ceiling that a smaller duty meets is not a
        failure (see *A request above a limit* below).
      - No — only off-mode placeholders (``E_cmp = 0``,
        ``Q_ref_tank = 0``).
    * - ``discharge_temp_above_max``
      - The compressor discharge temperature exceeds ``T_dis_max``
        (default 150 °C for R32, 135 °C for every other refrigerant;
        ``T_dis_max=None`` switches the check off) even under the
        discharge-temperature control (suction superheat down to 0 K, then
        wet suction down to ``x_suction_min``, default 0.9), at every duty the
        model tried: the request and, when it is above the limit, smaller
        duties down to the speed floor. A request that a *smaller* duty meets is
        not a failure: it returns that duty with ``capacity_clamped ==
        "max"`` and ``capacity_binding == "discharge_temperature"``. A
        dynamic boiler step does the same: it runs at the reduced duty (its
        run time and step-average heat follow from it) and is an off step
        with this reason only when no duty fits. The other limits a smaller
        duty cures are answered the same way (see *A request above a limit*
        below).
      - No — only off-mode placeholders (``E_cmp = 0``,
        ``Q_ref_tank = 0``).
    * - ``condensing_temp_above_max``
      - The condensing pressure exceeds the dew pressure at ``T_cond_max``,
        the top of the compressor envelope (default by refrigerant: R410A,
        R32, R407C 65 °C; R290, R1234yf 75 °C; R134a 80 °C; R717 70 °C; never
        above 0.9 of the critical pressure; ``T_cond_max=None`` switches
        the check off), at every duty the model tried. The condensing
        temperature is the demand-side temperature plus the condenser
        approach, so a smaller duty lowers it: a boiler request that a
        *smaller* duty meets is not a failure, it returns the duty that
        condenses on the limit with ``capacity_clamped == "max"`` and
        ``capacity_binding == "condensing_temperature"`` (steady and
        dynamic). The reason is reported when no duty fits — the tank, room
        or ground is itself at the limit — and for a boiler request whose
        condensing temperature would be above the critical point (formerly
        ``cycle_invalid``) when a tenth of it is still above the limit.
      - No — only off-mode placeholders (``E_cmp = 0``,
        ``Q_ref_tank = 0``).

    * - ``eta_isen_below_min``
      - The shipped isentropic-efficiency fit, read outside its data (the
        low-speed continuation), is below ``ETA_ISEN_MIN`` = 0.30 at the
        solved point (ASHP, GSHP: at every candidate). The fit never goes
        below 0.336 inside its data. Not checked for scalar or user
        efficiencies.
      - No — only off-mode placeholders.

ASHPB only
----------

.. list-table::
    :header-rows: 1
    :widths: 25 35 40

    * - Value
      - Means
      - Result dict carries cycle numbers?
    * - ``t_cond_below_t_in``
      - The condensing temperature was at or below the tank/loop inlet
        water temperature, so heat cannot flow to the water at that
        operating point. Takes precedence over ``pr_above_max`` and
        ``cycle_invalid`` when it is the reason the cycle returned no
        state.
      - No — only off-mode placeholders.

Ground-coupled solve path (GSHP and GSHPB with an active ground loop)
---------------------------------------------------------------------

When the ground loop is solved jointly with the cycle (the ground-flow and
approach solvers in ``ground_flow_control``), a point that cannot be met is
returned as a *failed requested point*: ``converged == False``,
``hp_is_on == False``, heat and power columns set to ``0.0`` and COP columns
``NaN``. The reason is one of the following (the common values above can
also appear).

.. list-table::
    :header-rows: 1
    :widths: 30 70

    * - Value
      - Means
    * - ``pressure_ratio_limit``
      - The cycle was invalid and the last pressure-ratio check hit
        ``PR_cycle_min`` or ``PR_cycle_max``. It replaces ``cycle_invalid``
        in this path. (An envelope rejection keeps its own reason:
        ``discharge_temp_above_max``, ``condensing_temp_above_max`` or
        ``eta_isen_below_min``.)
    * - ``compressor_min_speed``
      - The requested duty is below what the compressor delivers at
        ``rps_min`` (``capacity_clamped == "min"``), so the exact duty cannot
        be met.
    * - ``compressor_max_speed``
      - The requested duty is above what the compressor delivers at
        ``rps_max`` (``capacity_clamped == "max"``).
    * - ``compressor_capacity_limit``
      - The delivered duty differs from the request for another reason
        than the min/max speed clamp.
    * - ``compressor_speed_limit``
      - The compressor-speed solve did not converge and the row carried no
        more specific reason.
    * - ``load_hx_capacity_insufficient``
      - The load-side heat exchanger (indoor coil or tank condenser) could
        not transfer the required duty.
    * - ``ground_hx_capacity_insufficient``
      - The ground heat exchanger could not supply the required evaporator
        duty at any approach temperature in the search band.

A request above a limit (boilers)
---------------------------------

An inverter unit asked for more than an
operating limit allows does not stop: its controller lowers the compressor
frequency until the limit holds. This applies to the boilers (asked for a
fixed duty) and to ``AirSourceHeatPump``, which reports the same
``capacity_binding`` names plus ``"cycle"`` for a coil at its largest airflow
and the widest approach the search admits. So when the requested duty has no operating
point for one of the reasons below and a smaller duty has one,
``analyze_steady`` and each dynamic step return the **largest duty that runs**
(found to 1 % of the request), not a failure:

.. list-table::
    :header-rows: 1
    :widths: 34 26 40

    * - The request fails with
      - ``capacity_binding``
      - Why a smaller duty helps
    * - ``condensing_temp_above_max``, or ``cycle_invalid`` without a
        ``failure_detail`` (no cycle: condensing at or above the critical
        point) when the duties that run end on the condensing limit
      - ``"condensing_temperature"``
      - A smaller condenser approach lowers the condensing pressure. The
        duty that condenses on the limit is found by a secant on the
        condensing temperature (three cycle solves), not by the ladder.
    * - ``discharge_temp_above_max``, or ``cycle_invalid`` with
        ``failure_detail == "discharge_state_undefined"``
      - ``"discharge_temperature"``
      - A smaller condenser approach lowers the condensing pressure and
        with it the discharge temperature.
    * - ``pr_above_max``
      - ``"pressure_ratio"``
      - The condenser and the source heat exchanger both need a smaller
        approach, so the pressure ratio falls.
    * - ``hx_not_converged``
      - ``"outdoor_coil"`` (ASHPB), ``"source_heat_exchanger"`` (GSHPB, WSHPB)
      - The source heat exchanger has less heat to absorb. Not tried when
        ``failure_detail == "outdoor_coil_excess_at_min_airflow"``: a
        smaller duty makes that worse.
    * - ``eta_isen_below_min``
      - ``"compressor_efficiency"``
      - A lower pressure ratio moves the shipped fit back above its floor.

The result is a running point with ``capacity_clamped == "max"``,
``Q_max [W]`` (the duty delivered) and ``Q_request [W]`` (the duty asked for);
``capacity_binding`` names the limit the next larger duty tried ran into, or
``"pressure_ratio"`` when the point returned sits on ``PR_cycle_max`` (the
source heat exchanger then asks for a larger approach than the ceiling leaves
it). ``"compressor"`` remains the value for the speed ceiling ``rps_max``.
When no smaller duty runs, the result is the off state with the request's own
``failure_reason``.

Not reduced: ``cycle_invalid`` for any other cause, ``t_cond_below_t_in`` and
``optimizer_failed``. A request with no cycle at all is, with the boilers, a
condensing temperature -- the tank temperature plus the condenser approach
the duty needs -- at or above the critical point. With the condensing limit
it is treated as beyond that limit: reduced onto it when a smaller duty runs
and the duties that run end on the limit, reported as
``condensing_temp_above_max`` when a tenth of the request is still above the
limit, and otherwise left as it is. With ``T_cond_max=None`` it stays a
failure: the largest smaller duty would sit on the critical point itself, a
state no machine runs at.

``failure_detail``
------------------

Two kinds of failure carry one more key, ``failure_detail``, that says which
check failed.

A boiler (ASHPB, GSHPB, WSHPB) ``analyze_steady`` result whose cycle the
solver accepted but which is not an operating point is reported as
``cycle_invalid``; ``failure_detail`` names the check
(``tmhp.dynamic_context.unphysical_cycle_detail``). An ASHPB
``analyze_steady`` result reported as ``hx_not_converged`` because no outdoor
approach lets the outdoor coil carry the evaporator duty within the fan's flow
limits names the limit it fails at (the off-mode row carries it; the request
is first retried at smaller duties as described above). The outdoor-approach search solves the band
of approaches the fan can serve from the coil's capacity margins at its largest
and smallest airflow; these values say why that band is empty.

.. list-table::
    :header-rows: 1
    :widths: 32 18 50

    * - ``failure_detail``
      - With ``failure_reason``
      - Means
    * - ``duty_or_power_undefined``
      - ``cycle_invalid``
      - Delivered heat or compressor power is non-finite or not positive.
    * - ``discharge_state_undefined``
      - ``cycle_invalid``
      - The compressor discharge temperature or enthalpy is non-finite —
        typically a low isentropic efficiency at a high pressure ratio,
        which puts the discharge enthalpy outside the refrigerant's
        equation-of-state range.
    * - ``min_speed_duty_above_capacity``
      - ``cycle_invalid``
      - The compressor sits on its speed floor (``capacity_clamped ==
        "min"``) yet delivers more than the nameplate ``hp_capacity``.
    * - ``outdoor_coil_short_at_max_airflow``
      - ``hx_not_converged`` (ASHPB)
      - At every outdoor approach the cycle reaches (up to the
        pressure-ratio ceiling or the 20 K search bound), the outdoor coil
        absorbs less than the evaporator duty even at the fan's largest
        flow. A larger ``UA_ou_rated`` or ``dV_fan_a_max``, or a smaller
        request, moves the point into range.
    * - ``outdoor_coil_excess_at_min_airflow``
      - ``hx_not_converged`` (ASHPB)
      - At every outdoor approach, down to 1 K, the outdoor coil absorbs more
        than the evaporator duty even at the fan's smallest flow: the fan
        cannot turn down far enough. A smaller ``dV_fan_a_min`` moves the
        point into range; a real unit would cycle its fan.
    * - ``outdoor_coil_no_band``
      - ``hx_not_converged`` (ASHPB)
      - The coil is short at the largest flow at some approaches and in
        excess at the smallest flow at others, with no approach in between
        at which the fan can carry the duty.

The key is present only on those results, and only on ``analyze_steady``
dicts; it is not a dynamic-frame column (an ``analyze_dynamic`` row that could
not be solved carries ``failure_reason`` alone).

``pr_below_min`` is *not* a ``failure_reason`` value. A pressure ratio
below ``PR_cycle_min`` is clamped to the floor and the cycle is still
solved; it only prints a ``[PR guard]`` message and sets
``pr_floor_active`` on GSHP results.

How to branch on it
===================

The safest pattern depends on which question you're asking.

**"Is this step physically meaningful?"** Use the cycle output
directly. Off-mode rows have ``E_cmp [W] == 0``:

.. code-block:: python

   ok = df["E_cmp [W]"] > 0

This is robust across all ``failure_reason`` values and is
also the recommended check inside ``analyze_dynamic``, where
``failure_reason`` is per-step.

The ``analyze_dynamic`` frames of ASHPB, GSHPB and WSHPB carry a
``failure_reason`` column. A step on which the thermostat did not call
for heat, or on which the compressor ran, is ``none``. A step that was
called but whose cycle could not be solved carries the reason and is an
off-mode row: ``hp_is_on == False``, ``converged == False``, zero heat and
power, NaN COP, and no heat into the tank or out of the ground. The call
for heat persists, so the next step tries again.

**"Did the model warn me about anything?"** Look at
``failure_reason`` directly:

.. code-block:: python

   from collections import Counter

   print(Counter(df["failure_reason"]))
   # e.g. Counter({'none': 1392, 'hx_not_converged': 47, 'cycle_invalid': 1})

**"Is this row trustworthy for a metric I care about?"** Combine
the converged flag with the failure reason:

.. code-block:: python

   trustworthy = (df["converged"]) & (df["failure_reason"] == "none")

What triggers each value
========================

These are implementation details and may shift between releases —
treat the values themselves as the stable contract, not the
mechanism. Expand the cards below for the trigger conditions and
the recommended lever to pull when you hit each one.

.. dropdown:: ``none`` — everything converged
    :icon: check-circle
    :color: success

    Both the inner HX loop and the outer SciPy optimiser hit
    their success criteria. The result dict is fully populated
    and ``converged`` is ``True``.

    **No action needed.** This is the common path on a well-sized
    system at moderate ambient.

.. dropdown:: ``hx_not_converged`` — HX residual exceeded tolerance
    :icon: alert
    :color: warning

    The HX residual didn't drop below tolerance inside the inner
    iteration, but the cycle still produced a state. Only GSHP's
    ``analyze_steady`` returns that state, as an approximation; ASHPB,
    GSHPB, WSHPB, ASHP and every ``analyze_dynamic`` row report an
    off-mode row with zero heat and power. ``converged`` is ``False`` so
    you can filter rows downstream. A boiler reports it only when no
    smaller duty runs: otherwise it runs at the largest duty its source
    heat exchanger carries (``capacity_clamped == "max"``).

    **What to do.** Treat ``converged`` as the source of truth
    for "trust this row?" decisions. If the rate of
    ``hx_not_converged`` is uncomfortably high, oversize the
    affected HX (design ε-NTU or area) so the iteration has
    more headroom near phase boundaries. On ASHPB, ``failure_detail``
    says which fan limit the outdoor coil fails at (see above).

.. dropdown:: ``optimizer_failed`` — SciPy didn't satisfy its own criteria
    :icon: alert
    :color: warning

    The outer SciPy optimiser exited with ``success == False``.
    The cycle still has a state at the optimiser's best
    ``dT_ref_evap``, just not a provably optimal one. GSHP
    ``analyze_steady`` returns that state; ASHPB, GSHPB, WSHPB, ASHP and
    every ``analyze_dynamic`` row report an off-mode row instead.

    **What to do.** Where numbers are returned they are reasonable but not
    optimisation-grade — fine for most aggregations, suspect for
    point-comparison work. Wider initial bounds on
    ``dT_ref_evap`` usually clears this.

.. dropdown:: ``cycle_invalid`` — cycle was infeasible at this point
    :icon: x-circle
    :color: danger

    ``_calc_state`` raised, or returned a non-dict, at the
    requested ``T_tank_w`` / ``T0`` / ``Q_ref_tank`` — or, for the
    boilers, returned a state that is not an operating point (see
    ``failure_detail`` above). The model falls back to off-mode
    placeholders (``E_cmp = 0``, ``Q_ref_tank = 0``).

    **What to do.** Usually the requested duty is unreachable
    for the given geometry. Consider increasing the design
    ε-NTU, fan flow, or condenser area; or accept that the
    operating point is genuinely off-limits and let the
    off-mode row stand.

.. dropdown:: ``pr_above_max`` — pressure ratio above the envelope
    :icon: x-circle
    :color: danger

    The final operating point needs a compressor pressure ratio
    above ``PR_cycle_max`` (checked by
    :func:`tmhp.compressor_envelope.check_pr_envelope`). The model
    falls back to off-mode placeholders, and a ``[PR guard]`` message is
    printed.

    **What to do.** Reduce the lift (lower ``T_tank_w`` or raise the
    source temperature), or raise ``PR_cycle_max`` if a higher
    single-stage ratio is acceptable for your compressor.

.. dropdown:: ``discharge_temp_above_max`` — discharge temperature above the limit
    :icon: x-circle
    :color: danger

    The solved operating point (boilers) or every candidate the search
    tried (ASHP, GSHP) discharges above ``T_dis_max`` even with the suction
    superheat at 0 K and the suction quality at ``x_suction_min``, and no
    smaller duty brings it inside. A point the control *does* hold runs with
    ``discharge_control == True``; its ``dT_ref_superheat [K]`` and
    ``x_ref_cmp_in [-]`` say how. The defaults are compressor protection settings
    (:mod:`tmhp.compressor_envelope`). With the default efficiency map this
    is chiefly high-pressure-ratio operation of R32: at ``PR > 7`` the
    fitted isentropic efficiency is 0.37--0.45 and the cycle model puts all
    compressor loss into the gas, so the computed discharge reaches
    200--290 °C. Real units at those conditions use injection (liquid or
    vapour) or two-stage compression, which the single-stage cycle does not
    describe.

    **What to do.** Treat the point as outside the single-stage envelope.
    For a sensitivity study, ``T_dis_max=None`` removes the discharge limit
    (the efficiency floor stays); a different protection setting is ``T_dis_max=<°C>``, a wetter
    allowed suction ``x_suction_min=<x>`` (``None`` switches the control off).

.. dropdown:: ``condensing_temp_above_max`` — condensing pressure above the envelope
    :icon: x-circle
    :color: danger

    The compressor would have to discharge at a pressure above the top of
    its envelope (``T_cond_max``, stated as the saturation temperature of
    that pressure). For a boiler this is a tank too hot for the refrigerant:
    the cycle condenses at the tank temperature plus the tank heat
    exchanger's approach, and the unit reduces its duty as the tank
    approaches the limit (``capacity_binding == "condensing_temperature"``)
    until even the minimum-speed duty condenses above it. In a dynamic run
    the thermostat normally stops the heat pump before that: an upper set
    point above the tank temperature the envelope allows at the nameplate
    duty is capped there at construction (``UserWarning``,
    ``T_tank_w_setpoints_requested``). A called step past that cut-out -- a
    stratified tank whose coil-side water is hotter than the sensor, a
    minimum run time -- runs at the reduced duty or is an off row with this
    reason.

    **What to do.** Choose an upper set point the refrigerant reaches
    (``T_cond_max`` less the condenser approach at the nameplate duty,
    ``hp_capacity / UA_tank_hx``), or a refrigerant with a higher limit (R290, R134a). For a
    compressor with a different high-pressure rating give its own
    ``T_cond_max=<°C>``; ``None`` removes the limit for a sensitivity
    study. A zeotropic blend is judged at its dew point: R407C reaches the
    65 °C limit when the cycle's own (bubble-point) condensing temperature
    is 61.2 °C.

.. dropdown:: ``t_cond_below_t_in`` — condensing below the water inlet (ASHPB)
    :icon: x-circle
    :color: danger

    Inside ``_calc_state`` the condensing temperature
    ``T_cond_K <= T_tank_w_K``, so the condenser cannot heat the water.
    The cycle returns no state and the model falls back to off-mode
    placeholders.

    **What to do.** Usually the optimiser probed an evaporating
    temperature that is too low for the tank temperature. Move the
    operating point away from the tank temperature or check the
    tank-loop inputs.

.. dropdown:: Ground-coupled reasons
    :icon: alert
    :color: warning

    ``pressure_ratio_limit``, ``compressor_min_speed``,
    ``compressor_max_speed``, ``compressor_capacity_limit``,
    ``compressor_speed_limit``, ``load_hx_capacity_insufficient`` and
    ``ground_hx_capacity_insufficient`` come from the ground-coupled
    solve (see the table above). In all of them the requested duty is
    reported as *not met*: heat and power are zero and COP is ``NaN``.

    **What to do.** Check the compressor speed band against the request
    (``*_min_speed``/``*_max_speed``), or enlarge the heat exchanger that
    the name points at (``load_hx_*`` or ``ground_hx_*``).

Off-mode fallback
=================

When ``failure_reason`` is ``"cycle_invalid"``, ``"pr_above_max"``,
``"discharge_temp_above_max"``, ``"condensing_temp_above_max"``,
``"eta_isen_below_min"`` or
``"t_cond_below_t_in"`` — and, for WSHPB and for GSHPB outside the
ground-coupled path, for every reason other than ``"none"`` — the
steady-state path
emits a ``RuntimeWarning`` and substitutes an off-mode result:
``hp_is_on = False``, all power and duty fields zeroed. In
``analyze_dynamic`` every called-but-unsolved step of ASHPB, GSHPB and
WSHPB becomes the same kind of off-mode row. This keeps
``analyze_dynamic`` rows aligned (one row per step, regardless of
feasibility) so the resulting DataFrame is safe to vectorise over.

Where this is defined in the code
=================================

The diagnostic flag is set inside ``analyze_steady`` for the five
core models, and per step inside ``analyze_dynamic`` for ASHPB, GSHPB
and WSHPB. See the docstrings under:

- :doc:`../models/ashpb`
- :doc:`../models/gshpb`
- :doc:`../models/wshpb`
- :doc:`../models/ashp`
- :doc:`../models/gshp`
