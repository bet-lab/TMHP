==========================
Compressor reference state
==========================

Every compressor-based model -- :class:`tmhp.AirSourceHeatPump`,
:class:`tmhp.GroundSourceHeatPump`, :class:`tmhp.AirSourceHeatPumpBoiler`,
:class:`tmhp.GroundSourceHeatPumpBoiler`, :class:`tmhp.WaterSourceHeatPumpBoiler`
and the composed variants built on them -- derives rated speed and refrigerant
mass flow from one physical *reference state*:

* ``rps_rated`` identifies the nominal-capacity operating point and the reported
  speed ratio, ``n* = rps / rps_rated``. BITZER efficiencies use actual shaft
  speed in rev/s, without relative-speed normalization;
* ``m_dot_ref_rated`` normalizes the refrigerant-side ground-HX resistance,
  ``m_dot_ref / m_dot_ref_rated`` (``variable_ground_hx_UA=True``).

The constructor builds that state from the inputs every model already needs,
so ``hp_capacity`` and the refrigerant are enough:

.. code-block:: text

    hp_capacity
       -> V_cmp_ref            (given, or capacity-scaled default)
       -> rating condition     (family standard, or rated_condition=...)
       -> rps_rated            Q_model(rps) = hp_capacity
       -> m_dot_ref_rated      refrigerant mass flow of the same state
       -> normal simulation

.. code-block:: python

    from tmhp import GroundSourceHeatPump

    hp = GroundSourceHeatPump(hp_capacity=8000, variable_ground_hx_UA=True)
    hp.rps_rated                    # solved rated speed [rev/s]
    hp.m_dot_ref_rated              # solved rated refrigerant flow [kg/s]
    hp.reference_state.cop          # load duty / compressor input at rating
    hp.rated_condition.standard     # which rating point was used

Capacity-only initialization constructs a representative compressor; it does
not identify the exact manufacturer compressor.

When manufacturer compressor data are unavailable, ``V_cmp_ref`` is estimated
from nominal heat-pump capacity
(:func:`tmhp.compressor_speed.default_displacement`). The rated compressor
speed and reference refrigerant mass flow are then solved self-consistently at
the model's standard/reference rating condition.

What each symbol means
======================

.. list-table::
    :header-rows: 1
    :widths: 22 78

    * - Symbol
      - Meaning (identical in every model)
    * - ``hp_capacity``
      - Rated/reference capacity: load-side duty at the rating condition.
    * - ``V_cmp_ref``
      - Compressor swept displacement [m3/rev].
    * - ``rps_rated``
      - Speed that delivers ``hp_capacity`` at the rating condition.
    * - ``rps_min`` / ``rps_max``
      - Hardware/control speed limits. ``rps_rated`` is generally *not*
        ``rps_max``: rated capacity is not maximum capacity.
    * - ``m_dot_ref_rated``
      - Refrigerant mass flow of the same reference state.

Explicit values always win. ``V_cmp_ref``, ``rps_rated`` and
``m_dot_ref_rated`` given to the constructor are used as they are; only the
ones left as ``None`` are solved. With an explicit ``rps_rated`` the model
behaves exactly as before this feature (baseline correlations built at that
speed), and ``m_dot_ref_rated`` is still derived from the rating condition.
Scalar and callable efficiency inputs are unchanged; the reference solve
evaluates a caller's callable as given and the baseline correlations at the
actual candidate shaft speed.

Rating conditions
=================

The rating point is *not* the condition being simulated. Each family has a
standard rating point; air coils in TMHP are dry, so wet-bulb conditions of
the standards are not used. The boiler models' condenser is immersed in a
lumped tank whose temperature is set to the standard's water outlet
temperature.

.. list-table::
    :header-rows: 1
    :widths: 12 12 22 22 32

    * - Family
      - Mode
      - Source inlet
      - Load inlet
      - Standard
    * - ASHP
      - cooling
      - outdoor air 35 °C
      - indoor air 27 °C
      - ISO 5151:2017 T1 (heating: H1, 7 °C / 20 °C)
    * - GSHP
      - cooling
      - ground-loop liquid 25 °C
      - indoor air 27 °C
      - ISO 13256-1:2021 ground-loop (heating: 0 °C / 20 °C)
    * - ASHPB
      - heating
      - outdoor air 7 °C
      - tank 55 °C
      - EN 14511-2:2022 A7/W55
    * - GSHPB
      - heating
      - brine 0 °C
      - tank 55 °C
      - EN 14511-2:2022 B0/W55
    * - WSHPB
      - heating
      - water 10 °C
      - tank 55 °C
      - EN 14511-2:2022 W10/W55

Secondary flows are each model's own reference flows (``dV_*_fan_a_ref``,
``ground_flow_ref_lpm``, ``dV_b_f_lpm``) and both heat exchangers use their
rated UA. A manufacturer or user rating point replaces the standard one:

.. code-block:: python

    from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler

    AirSourceHeatPump(rated_condition={"mode": "heating"})   # ISO 5151 H1
    AirSourceHeatPumpBoiler(rated_condition={"source_T_C": 2.0, "load_T_C": 50.0})

Accepted keys are ``mode``, ``source_T_C``, ``load_T_C`` and ``standard``; a
complete :class:`tmhp.reference_state.RatingCondition` is also accepted.

How it is solved
================

At the rating point the load-side duty is ``hp_capacity``, so the load-side
saturation temperature follows from that heat exchanger directly. In heating,
the solver tries actual speeds inside ``[rps_min, rps_max]``. At each speed it
closes the source evaporator's mass-flow and heat-exchanger balance, then solves
``Q_cond(rps) - hp_capacity = 0`` over evaluable speed intervals. This avoids
assuming condenser duty increases from the minimum speed: raw speed-dependent
efficiencies can produce a decreasing segment and multiple mass-flow branches.
In cooling, the known evaporator duty fixes mass flow and speed, then an outer
root closes the source condenser. Every efficiency is evaluated at the actual
candidate speed, and both final heat-exchanger balances are verified. The solve uses
fixed rated UA: variable ground-HX UA needs ``m_dot_ref_rated``, which is what
is being solved for, so it is activated only afterwards. Each object solves
once; identical machines share a cached result.

The interval search refines a bounded set of actual, finite evaluations; invalid
points split a bracket rather than supplying extrapolated duties. A custom fit
with an arbitrarily narrow valid domain can still evade this finite search.
An error saying that no finite balance could be bracketed reports that search
limitation; it does not prove that every possible custom-fit state is physically
infeasible. Declare the supported speed interval or supply an explicit rated
speed and refrigerant mass flow when such a fit requires a narrower search.

When it fails
=============

A rated capacity the machine cannot reach is an inconsistent machine
definition, not a numerical event. The constructor raises
:class:`tmhp.reference_state.ReferenceStateError` (a ``ValueError``) whose
message starts with ``reference_capacity_inconsistent`` when

* the speed needed is outside ``[rps_min, rps_max]`` -- typically a
  displacement that does not match the capacity, e.g. a low-pressure fluid
  such as R600a with the R32-based default displacement;
* the rating point requires a supercritical condenser -- e.g. R744 at a
  W55 boiler rating point, since TMHP cycles are subcritical;
* the rated heat exchangers cannot carry the rated duty.

The speed is never clamped onto a bound: that would deliver a different
capacity and silently redefine the machine. Supply ``V_cmp_ref``,
``rps_rated`` or a reachable ``rated_condition``. With an explicit
``rps_rated`` an unreachable rating point only leaves ``m_dot_ref_rated``
unset (with a ``RuntimeWarning``), which matters only if
``variable_ground_hx_UA=True``.

The pressure-ratio envelope (``PR_cycle_min`` / ``PR_cycle_max``) is an
operating limit and is not imposed on the rating point;
``reference_state.pressure_ratio`` reports it.
