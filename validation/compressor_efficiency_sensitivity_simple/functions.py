"""The two synthetic efficiency functions and the constant, as ``(P_r, rps)`` callables.

Every case uses the same relative multiplier::

    m(x) = 1 − a (x − x_c)²          x = n* (x_c 0.60)  or  x = P_r (x_c 2.00),  a = 0.60

applied to one BASE constant, ``η_i(x) = η_{i,0} · m(x)``.  Nothing is fitted
and nothing is anchored to a control run.

The multiplier is only meaningful over the ``n*`` / ``P_r`` range the PLR sweep
actually traverses (about 0.25–0.98 and 1.6–2.3 for the 3.5 kW unit).  The
speed search, however, brackets ``rps`` over the whole envelope (``n*`` up to
2.5), where the quadratic is negative, so the callables clip to
``ETA_CLIP = (0.1, 1.0)`` and the ``n*`` multiplier is held at its ``n* = 1``
value above rated speed (``N_STAR_HOLD``); without the hold a falling
``η_v(n*)`` makes capacity non-monotonic in speed and the bracketing speed
solver clamps every point to ``rps_max``.  :func:`clipped` says whether a
*reported* operating point sits on that clip; the sweep flags such rows and the
figures and page have to show them.
"""

from __future__ import annotations

from collections.abc import Callable

from .config import A_N, A_P, ETA_CLIP, N_STAR_C, N_STAR_HOLD, PR_C

__all__ = ["EfficiencyFn", "multiplier", "raw_eta", "clipped", "make_speed_case", "make_pr_case", "constant"]

EfficiencyFn = Callable[[float, float], float]
CENTRE = {"n_star": (N_STAR_C, A_N), "p_r": (PR_C, A_P)}


def multiplier(x: float, driver: str) -> float:
    """``1 − a (x − x_c)²`` for ``driver`` in ``{"n_star", "p_r"}``; not clipped."""
    x_c, a = CENTRE[driver]
    return 1.0 - a * (x - x_c) ** 2


def raw_eta(eta0: float, x: float, driver: str) -> float:
    return eta0 * multiplier(x, driver)


def clipped(eta0: float, x: float, driver: str) -> bool:
    """True when the unclipped value lies outside ``ETA_CLIP`` (the callable returned a clip bound)."""
    v = raw_eta(eta0, x, driver)
    return v < ETA_CLIP[0] or v > ETA_CLIP[1]


def _clip(v: float) -> float:
    return min(max(v, ETA_CLIP[0]), ETA_CLIP[1])


def make_speed_case(eta0: float, rps_rated: float) -> EfficiencyFn:
    """``η(P_r, rps) = η₀ · m(min(rps / rps_rated, N_STAR_HOLD))``; the pressure ratio is ignored.

    The hold above rated speed is a solver safety (see ``config.N_STAR_HOLD``);
    every reported operating point lies below it.
    """

    def eta(pressure_ratio: float, rps: float) -> float:
        del pressure_ratio
        return _clip(raw_eta(eta0, min(rps / rps_rated, N_STAR_HOLD), "n_star"))

    return eta


def make_pr_case(eta0: float) -> EfficiencyFn:
    """``η(P_r, rps) = η₀ · m(P_r)``; the speed is ignored."""

    def eta(pressure_ratio: float, rps: float) -> float:
        del rps
        return _clip(raw_eta(eta0, pressure_ratio, "p_r"))

    return eta


def constant(eta0: float) -> EfficiencyFn:
    """A two-argument constant so every case goes through the same call path."""

    def eta(pressure_ratio: float, rps: float) -> float:
        del pressure_ratio, rps
        return eta0

    return eta
