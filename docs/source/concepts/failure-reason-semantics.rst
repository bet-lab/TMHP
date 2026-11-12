============================
``failure_reason`` semantics
============================

Every ``analyze_steady`` result carries a ``failure_reason`` key. It
is a diagnostic *report* — independent of whether the result dict
contains usable cycle numbers — that lets callers branch on *why* a
step looks the way it does without having to inspect the cycle
internals. This page is the reference for what each value means and
when to branch on it.

The values
==========

``analyze_steady`` can return the values in the tables below. The first five
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
      - Depends on the family. GSHPB, WSHPB, ASHP and every
        ``analyze_dynamic`` row: no — off-mode (``E_cmp = 0``,
        ``Q_ref_tank = 0``, NaN COP). ASHPB ``analyze_steady``: no —
        only the HX diagnostics, without duty or power keys. GSHP
        ``analyze_steady``: yes — the unconverged cycle state, to be
        treated as approximate. ``converged == False`` throughout.
    * - ``optimizer_failed``
      - SciPy couldn't satisfy its own success criteria, even
        though the cycle returned a state.
      - Depends on the family. GSHPB, WSHPB, ASHP and every
        ``analyze_dynamic`` row: no — off-mode. ASHPB and GSHP
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
        (default 20 for the boilers and WSHPB, 5 for ASHP and GSHP). The single-stage envelope is rejected rather
        than clamped, so no cycle state is produced. Reported instead
        of ``cycle_invalid`` when the last pressure-ratio check was a
        ceiling violation.
      - No — only off-mode placeholders (``E_cmp = 0``,
        ``Q_ref_tank = 0``).

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
        in this path.
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

``failure_detail``
------------------

A boiler (ASHPB, GSHPB, WSHPB) ``analyze_steady`` result whose cycle the
solver accepted but which is not an operating point is reported as
``cycle_invalid`` and carries one more key, ``failure_detail``, naming the
check (``tmhp.dynamic_context.unphysical_cycle_detail``):

.. list-table::
    :header-rows: 1
    :widths: 35 65

    * - ``failure_detail``
      - Means
    * - ``duty_or_power_undefined``
      - Delivered heat or compressor power is non-finite or not positive.
    * - ``discharge_state_undefined``
      - The compressor discharge temperature or enthalpy is non-finite —
        typically an isentropic efficiency near zero.
    * - ``min_speed_duty_above_capacity``
      - The compressor sits on its speed floor (``capacity_clamped ==
        "min"``) yet delivers more than the nameplate ``hp_capacity``.

The key is present only on those results; it is not a dynamic-frame
column.

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
    ``analyze_steady`` returns that state, as an approximation; GSHPB,
    WSHPB, ASHP and every ``analyze_dynamic`` row report an off-mode
    row with zero heat and power, and ASHPB's ``analyze_steady``
    returns only the HX diagnostics. ``converged`` is ``False`` so you
    can filter rows downstream.

    **What to do.** Treat ``converged`` as the source of truth
    for "trust this row?" decisions. If the rate of
    ``hx_not_converged`` is uncomfortably high, oversize the
    affected HX (design ε-NTU or area) so the iteration has
    more headroom near phase boundaries.

.. dropdown:: ``optimizer_failed`` — SciPy didn't satisfy its own criteria
    :icon: alert
    :color: warning

    The outer SciPy optimiser exited with ``success == False``.
    The cycle still has a state at the optimiser's best
    ``dT_ref_evap``, just not a provably optimal one. ASHPB and GSHP
    ``analyze_steady`` return that state; GSHPB, WSHPB, ASHP and every
    ``analyze_dynamic`` row report an off-mode row instead.

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

When ``failure_reason`` is ``"cycle_invalid"``, ``"pr_above_max"`` or
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
