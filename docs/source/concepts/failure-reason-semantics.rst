============================
``failure_reason`` semantics
============================

Every ``analyze_steady`` result carries a ``failure_reason`` key. It
is a diagnostic *report* — independent of whether the result dict
contains usable cycle numbers — that lets callers branch on *why* a
step looks the way it does without having to inspect the cycle
internals. This page is the reference for what each value means and
when to branch on it.

The four values
===============

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
      - Yes — numbers are usable but should be treated as
        approximate. ``converged == False``.
    * - ``optimizer_failed``
      - SciPy couldn't satisfy its own success criteria, even
        though the cycle returned a state.
      - Yes — numbers exist, but the evaporating-temperature
        choice is not provably optimal.
    * - ``cycle_invalid``
      - The cycle itself was infeasible at the requested
        operating point. The model falls back to off-mode
        (``E_cmp = 0``, ``Q_ref_tank = 0``).
      - No — only off-mode placeholders.

How to branch on it
===================

The safest pattern depends on which question you're asking.

**"Is this step physically meaningful?"** Use the cycle output
directly. Off-mode rows have ``E_cmp [W] == 0``:

.. code-block:: python

   ok = df["E_cmp [W]"] > 0

This is robust across all four ``failure_reason`` values and is
also the recommended check inside ``analyze_dynamic``, where
``failure_reason`` is per-step.

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
treat the four values themselves as the stable contract, not the
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
    iteration, but the cycle still produced a state. The result
    is usable as an approximation — ``converged`` is set to
    ``False`` so you can filter rows downstream.

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
    ``dT_ref_evap``, just not a provably optimal one.

    **What to do.** The numbers are reasonable but not
    optimisation-grade — fine for most aggregations, suspect for
    point-comparison work. Wider initial bounds on
    ``dT_ref_evap`` usually clears this.

.. dropdown:: ``cycle_invalid`` — cycle was infeasible at this point
    :icon: x-circle
    :color: danger

    ``_calc_state`` raised, or returned a non-dict, at the
    requested ``T_tank_w`` / ``T0`` / ``Q_ref_tank``. The model
    falls back to off-mode placeholders (``E_cmp = 0``,
    ``Q_ref_tank = 0``).

    **What to do.** Usually the requested duty is unreachable
    for the given geometry. Consider increasing the design
    ε-NTU, fan flow, or condenser area; or accept that the
    operating point is genuinely off-limits and let the
    off-mode row stand.

Off-mode fallback
=================

When ``failure_reason == "cycle_invalid"``, the steady-state path
emits a ``RuntimeWarning`` and substitutes an off-mode result:
``hp_is_on = False``, all power and duty fields zeroed. This keeps
``analyze_dynamic`` rows aligned (one row per step, regardless of
feasibility) so the resulting DataFrame is safe to vectorise over.

Where this is defined in the code
=================================

The diagnostic flag is set inside ``analyze_steady`` for the five
core models. See the docstrings under:

- :doc:`../models/ashpb`
- :doc:`../models/gshpb`
- :doc:`../models/wshpb`
- :doc:`../models/ashp`
- :doc:`../models/gshp`
