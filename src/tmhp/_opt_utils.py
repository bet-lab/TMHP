"""Private helpers for SciPy optimizer integration.

`OptimizeResult.x` / `.fun` are typed `np.ndarray | float | None` in SciPy's
stubs, so plain `float(getattr(opt_result, "x", default))` blows up the static
type checker — and would raise `TypeError` at runtime if the optimiser
returned an empty/None result. These wrappers keep the call sites readable.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from contextlib import contextmanager

__all__ = [
    "ignore_minpack_progress_warning",
    "safe_float_attr",
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
