"""Private helpers for SciPy optimizer integration.

`OptimizeResult.x` / `.fun` are typed `np.ndarray | float | None` in SciPy's
stubs, so plain `float(getattr(opt_result, "x", default))` blows up the static
type checker — and would raise `TypeError` at runtime if the optimiser
returned an empty/None result. These wrappers keep the call sites readable.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

from scipy.optimize import brentq

__all__ = [
    "ignore_minpack_progress_warning",
    "safe_float_attr",
    "specific_energy_objective",
    "operating_point_objective",
    "margin_feasible_interval",
    "margin_band_on_valid_range",
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
    samples: Iterable[float] = (),
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

    ``samples`` are points inside ``[lower, upper]`` at which the margins are
    already known (a caller that caches its evaluations pays nothing for
    them). They only narrow the bracket each root is solved in: the lower
    edge is taken in the first sampled cell where the capacity margin turns
    nonnegative, the upper edge in the last cell where the excess margin is
    still nonpositive. The result is the same root a full-interval solve
    finds when the margins are monotone.
    """
    if not (math.isfinite(lower) and math.isfinite(upper) and lower <= upper):
        raise ValueError("Require finite lower <= upper")
    if capacity_margin(upper) < 0.0 or excess_margin(lower) > 0.0:
        return None
    points = sorted({lower, upper, *(float(s) for s in samples if lower < s < upper)})
    cells = list(zip(points, points[1:], strict=False))
    if capacity_margin(lower) >= 0.0:
        low = lower
    else:
        a, b = next((a, b) for a, b in cells if capacity_margin(b) >= 0.0)
        low = float(brentq(capacity_margin, a, b, xtol=xtol))
    if excess_margin(upper) <= 0.0:
        high = upper
    else:
        a, b = next((a, b) for a, b in reversed(cells) if excess_margin(a) <= 0.0)
        high = float(brentq(excess_margin, a, b, xtol=xtol))
    if low > high + 10.0 * xtol:
        return None
    return low, max(low, high)


def margin_band_on_valid_range(
    margins: Callable[[float], tuple[float, float] | None],
    lower: float,
    upper: float,
    *,
    coarse: int = 5,
    xtol: float = 1e-7,
    edge_tol: float = 1e-2,
    hint: float | None = None,
    hint_halfwidth: float = 1.0,
) -> tuple[float, float] | None:
    """:func:`margin_feasible_interval` for margins that exist only on part of ``[lower, upper]``.

    ``margins(x)`` returns ``(capacity_margin, excess_margin)`` -- the signed
    duty margins at the largest and the smallest flow -- or ``None`` where the
    candidate does not exist at all (a cycle outside the compressor envelope,
    an inner balance with no solution). ``coarse`` equally spaced samples
    locate the part of the interval on which the margins exist; it is taken to
    be one run of samples. Where the run stops at a sample without margins and
    the band needs the stretch beyond it (the duty is not yet carried at the
    last sample with margins, or the flow cannot yet be turned down at the
    first), the run is extended towards that sample by bisection to
    ``edge_tol``. The band edges are then solved as roots.

    ``hint`` is a point expected to lie in the band (the previous solution of
    a neighbouring problem). A band found strictly inside
    ``hint +/- hint_halfwidth`` is returned without the coarse samples; one
    that touches that bracket is solved on the whole interval instead, so the
    hint changes the cost and never the result.

    Returns ``None`` when the band is empty, and also when the margins vanish
    between two samples that have them (the run assumption does not hold).
    """
    if not (math.isfinite(lower) and math.isfinite(upper) and lower <= upper):
        raise ValueError("Require finite lower <= upper")
    if coarse < 2:
        raise ValueError("coarse must be at least 2")

    class _Missing(Exception):
        pass

    def capacity(x: float) -> float:
        value = margins(x)
        if value is None:
            raise _Missing
        return value[0]

    def excess(x: float) -> float:
        value = margins(x)
        if value is None:
            raise _Missing
        return value[1]

    def solve(a: float, b: float, samples: Iterable[float] = ()) -> tuple[float, float] | None:
        try:
            return margin_feasible_interval(capacity, excess, a, b, xtol=xtol, samples=samples)
        except (_Missing, ValueError, RuntimeError):
            return None

    def extend(good: float, bad: float) -> float:
        """Last point towards ``bad`` at which the margins still exist."""
        while abs(bad - good) > edge_tol:
            mid = 0.5 * (good + bad)
            if margins(mid) is None:
                bad = mid
            else:
                good = mid
        return good

    if hint is not None and lower < upper:
        a, b = max(lower, hint - hint_halfwidth), min(upper, hint + hint_halfwidth)
        at_a, at_b = margins(a), margins(b)
        if at_a is not None and at_b is not None and at_a[1] <= 0.0 <= at_b[0]:
            band = solve(a, b)
            if band is not None and (band[0] > a or a == lower) and (band[1] < b or b == upper):
                return band

    points = [lower + (upper - lower) * i / (coarse - 1) for i in range(coarse)] if lower < upper else [lower]
    values = [margins(x) for x in points]
    valid = [i for i, value in enumerate(values) if value is not None]
    if not valid:
        return None
    first, last = valid[0], valid[-1]
    a, b = points[first], points[last]
    at_first, at_last = values[first], values[last]
    assert at_first is not None and at_last is not None
    if at_last[0] < 0.0:
        if last == len(points) - 1:
            return None
        b = extend(b, points[last + 1])
        at_b = margins(b)
        if at_b is None or at_b[0] < 0.0:
            return None
    if at_first[1] > 0.0:
        if first == 0:
            return None
        a = extend(a, points[first - 1])
        at_a = margins(a)
        if at_a is None or at_a[1] > 0.0:
            return None
    return solve(a, b, samples=[points[i] for i in valid])
