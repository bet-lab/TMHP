"""Compressor operating-envelope guard (pressure-ratio based).

The physically primary limit on a vapour-compression cycle is the compressor
**pressure ratio** ``PR = P_cond / P_evap``, not a fixed temperature lift: the
lift required to reach a given ``PR`` maps non-linearly through the refrigerant
saturation curve (Clausius--Clapeyron) and therefore differs by refrigerant and
operating level. A fixed minimum lift (e.g. 20 K) over- or under-states the true
physical minimum depending on conditions; a ``PR`` floor is transferable.

This module provides the shared envelope decision used by every heat-pump model
so the floor/ceiling logic and reason codes stay consistent. Each model owns its
own ``PR_cycle_min`` / ``PR_cycle_max`` attributes (user-configurable) and calls
:func:`check_pr_envelope`; the caller then **clamps** the floor (project the
cycle onto ``PR_cycle_min`` for a continuous low-lift transition) and **rejects**
the ceiling (outside the single-stage envelope).

Physical rationale: scroll axial-loading / built-in volume ratio, oil-feed
pressure differential, and isentropic-efficiency collapse all place the floor
near ``PR ~ 1.5`` (1.3--2.0 band). For the ceiling, the AC-scroll hardware
self-unload limit sits near ``PR ~ 11:1`` and the single-stage modelling
boundary near ``PR ~ 7--8``; the heat-pump models nonetheless default
``PR_cycle_max`` to ``20`` as a deliberately generous sanity bound so that
legitimate high-lift domestic-hot-water operation (condensing at 55--75 degC
from a 0--12 degC source) is admitted while still rejecting non-physical
pressure ratios (Cuevas & Lebrun 2009; Bertsch & Groll 2008; Gayeski et al.
2011).
"""

from __future__ import annotations

__all__ = ["check_pr_envelope", "PR_BELOW_MIN", "PR_ABOVE_MAX"]

#: Reason code: pressure ratio is below ``PR_cycle_min`` (floor -> clamp).
PR_BELOW_MIN = "pr_below_min"
#: Reason code: pressure ratio is above ``PR_cycle_max`` (ceiling -> reject).
PR_ABOVE_MAX = "pr_above_max"


def check_pr_envelope(pr: float, pr_min: float, pr_max: float) -> str | None:
    """Classify a compressor pressure ratio against the operating envelope.

    Parameters
    ----------
    pr
        Compressor pressure ratio ``P_cond / P_evap`` (dimensionless, >= 1).
    pr_min
        Lower envelope bound (floor). Below this the scrolls unload / oil-feed
        differential and isentropic efficiency collapse.
    pr_max
        Upper envelope bound (ceiling). Above this a single-stage cycle is no
        longer representative.

    Returns
    -------
    str | None
        ``None`` when ``pr_min <= pr <= pr_max`` (feasible); otherwise
        :data:`PR_BELOW_MIN` or :data:`PR_ABOVE_MAX`.
    """
    if pr < pr_min:
        return PR_BELOW_MIN
    if pr > pr_max:
        return PR_ABOVE_MAX
    return None
