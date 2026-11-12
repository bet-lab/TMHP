"""Private helpers for SciPy optimizer integration.

`OptimizeResult.x` / `.fun` are typed `np.ndarray | float | None` in SciPy's
stubs, so plain `float(getattr(opt_result, "x", default))` blows up the static
type checker — and would raise `TypeError` at runtime if the optimiser
returned an empty/None result. These wrappers keep the call sites readable.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from scipy.optimize import brentq

__all__ = [
    "ignore_minpack_progress_warning",
    "safe_float_attr",
    "specific_energy_objective",
    "operating_point_objective",
    "margin_feasible_interval",
    "DEFAULT_SHORTFALL_TOLERANCE",
    "PENALTY",
    "OBJ_CAPACITY_LIMITED",
    "OBJ_INFEASIBLE",
]


@contextmanager
def ignore_minpack_progress_warning() -> Iterator[None]:
    """Keep MINPACK slow-progress diagnostics from changing solver semantics.

    SciPy's ``fsolve`` can emit a RuntimeWarning that the iteration is not
    making good progress while still returning the trajectory accepted by the
    legacy model. When callers run with warnings promoted to errors, that
    diagnostic becomes an exception and broad fallback handlers can take a
    different numerical path. Suppressing only this known MINPACK diagnostic
    keeps warnings-as-errors from changing accepted model outputs.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="The iteration is not making good progress.*",
            category=RuntimeWarning,
        )
        yield


def safe_float_attr(obj: object, name: str, default: float) -> float:
    """Read ``obj.name`` and coerce to ``float``, returning ``default`` if
    the attribute is missing, ``None``, or not numeric.

    Intended for ``OptimizeResult.x`` / ``OptimizeResult.fun`` where the
    optimiser may legitimately fail and leave the field unpopulated.
    """
    value = getattr(obj, name, None)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Operating-point objective shared by the air-source models
# ---------------------------------------------------------------------------
#: Allowed shortfall fraction before a candidate operating point is infeasible.
#: Sits above the tolerances of the speed and coil solvers.
DEFAULT_SHORTFALL_TOLERANCE = 0.02
#: Sentinel for infeasible or invalid candidates.
PENALTY = 1.0e6


def specific_energy_objective(
    E_tot: float,
    Q_delivered: float,
    Q_request: float,
    Q_storable: float = 0.0,
    *,
    eps: float = DEFAULT_SHORTFALL_TOLERANCE,
    penalty: float = PENALTY,
) -> float:
    """Electrical input per unit of credited heat, scaled to the request.

    Why not plain ``E_tot``: once the compressor sits at its minimum speed the
    candidates the optimiser compares no longer deliver the same heat. A wider
    evaporator approach starves the outdoor coil, delivers *less* heat and
    draws *less* power, so minimising the absolute input picks the starved
    candidate even when its COP is far worse -- the outdoor fan is driven to
    its 5 % bound and the air-side temperature drop reaches 17 K against a
    catalogue maximum near 8 K. Comparing candidates on cost per unit of useful
    heat, and refusing those that do not meet the request, removes that
    artefact without touching the compressor correlations.

    Definition::

        infeasible     Q_delivered < Q_request (1 - eps)   -> graded penalty
        credited heat  Q_credit = min(Q_delivered, Q_request + Q_storable)
        objective      J = E_tot / Q_credit * Q_request

    ``J`` equals ``E_tot`` when the request is met exactly, so the search is
    unchanged wherever the compressor can still modulate. Surplus is credited
    only up to what the sink can take (``Q_storable``): a hot-water tank with
    thermal headroom stores it, a room does not.
    """
    if not (E_tot > 0.0) or not math.isfinite(E_tot) or not (Q_request > 0.0) or not math.isfinite(Q_delivered):
        return penalty
    shortfall = (Q_request - Q_delivered) / Q_request
    if shortfall > eps:
        return penalty * (1.0 + shortfall)
    Q_credit = min(Q_delivered, Q_request + max(0.0, Q_storable))
    if Q_credit <= 0.0:
        return penalty
    return E_tot / Q_credit * Q_request


# ---------------------------------------------------------------------------
# Three-band operating-point objective (space-conditioning models)
# ---------------------------------------------------------------------------
# The bands must stay ordered and far enough apart that no power figure or
# shortfall can cross from one band into the next:
#   1. ordinary points            -> specific energy, in watts;
#   2. compressor at ``rps_max``  -> OBJ_CAPACITY_LIMITED + shortfall, so the
#      search returns the machine's ceiling at this condition;
#   3. points the cycle cannot reach -> OBJ_INFEASIBLE (or above).
# ``AirSourceHeatPump`` defines the same values inline; they are kept equal.

#: Objective floor for a capacity-limited operating point (valid operation).
OBJ_CAPACITY_LIMITED = 1.0e9
#: Objective value for an operating point the cycle cannot reach (invalid).
OBJ_INFEASIBLE = 1.0e12


def operating_point_objective(
    E_tot: float,
    Q_delivered: float,
    Q_request: float,
    capacity_clamped: str | None,
    *,
    Q_storable: float = math.inf,
) -> float:
    """Rank one candidate operating point on the three-band scale above.

    ``capacity_clamped == "max"`` means the compressor already runs at its
    speed ceiling and still falls short: such candidates are ranked by the
    shortfall, so the search returns the largest delivery. Every other point
    is ranked by :func:`specific_energy_objective` with the full delivered
    heat credited, which keeps the choice among minimum-speed candidates on
    cost per unit of heat instead of picking the starved coil.
    """
    if not (math.isfinite(E_tot) and E_tot > 0.0 and math.isfinite(Q_delivered)):
        return OBJ_INFEASIBLE
    delivered = abs(Q_delivered)
    request = abs(Q_request)
    if capacity_clamped == "max":
        return OBJ_CAPACITY_LIMITED + max(request - delivered, 0.0)
    return specific_energy_objective(E_tot, delivered, request, Q_storable, penalty=OBJ_INFEASIBLE)


def margin_feasible_interval(
    capacity_margin: Callable[[float], float],
    excess_margin: Callable[[float], float],
    lower: float,
    upper: float,
    *,
    xtol: float = 1e-7,
) -> tuple[float, float] | None:
    """Sub-interval of ``[lower, upper]`` where a modulated flow can carry a duty.

    ``capacity_margin(x)`` is the signed duty margin at the largest flow
    (``>= 0``: the duty can be carried) and ``excess_margin(x)`` the margin at
    the smallest flow (``<= 0``: the flow can still be turned down to the
    duty). Both must be nondecreasing in ``x``, as a heat exchanger's capacity
    at a fixed flow grows with its temperature difference, and
    ``excess_margin <= capacity_margin`` (less flow carries less heat).

    The band edges are solved as roots, not sampled, so a band narrower than
    any grid spacing is still found -- down to a single point when both flow
    limits coincide. Returns ``None`` when the band is empty.
    """
    if not (math.isfinite(lower) and math.isfinite(upper) and lower <= upper):
        raise ValueError("Require finite lower <= upper")
    if capacity_margin(upper) < 0.0 or excess_margin(lower) > 0.0:
        return None
    low = lower if capacity_margin(lower) >= 0.0 else float(brentq(capacity_margin, lower, upper, xtol=xtol))
    high = upper if excess_margin(upper) <= 0.0 else float(brentq(excess_margin, lower, upper, xtol=xtol))
    if low > high + 10.0 * xtol:
        return None
    return low, max(low, high)
