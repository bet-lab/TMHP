"""Private helpers for SciPy optimizer integration.

`OptimizeResult.x` / `.fun` are typed `np.ndarray | float | None` in SciPy's
stubs, so plain `float(getattr(opt_result, "x", default))` blows up the static
type checker — and would raise `TypeError` at runtime if the optimiser
returned an empty/None result. These wrappers keep the call sites readable.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Iterator
from contextlib import contextmanager

__all__ = [
    "ignore_minpack_progress_warning",
    "safe_float_attr",
    "specific_energy_objective",
    "DEFAULT_SHORTFALL_TOLERANCE",
    "PENALTY",
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
