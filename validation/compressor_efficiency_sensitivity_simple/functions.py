"""The synthetic efficiency functions and the constant, as ``(P_r, rps)`` callables.

Two relative multipliers share the same centres (``n*`` 0.60, ``P_r`` 2.00)::

    quadratic  m(x) = 1 − a (x − x_c)²      a = 0.60   -- η_is, η_em  (∩ shape)
    linear     m(x) = 1 − b (x − x_c)       b = 0.10   -- η_v         (falls with speed and with lift)

applied to one BASE constant, ``η_i(x) = η_{i,0} · m(x)``.  Nothing is fitted
and nothing is anchored to a control run.

Why η_v is linear: the speed search brackets ``rps`` over the whole envelope
(``n*`` up to 2.5) and reads the delivered duty ∝ ``η_v · n*``.  A ∩ η_v makes
that product peak at ``n* ≈ 1.17`` and fall beyond it, so the bracket sees no
sign change and clamps every point to ``rps_max``.  With the linear form the
product ``n* (1 − b (n* − x_c))`` keeps rising until ``n* = 5.3``, outside the
bracket, and no hold is needed.  η_is and η_em do not enter the delivered
flow, so their ∩ shape leaves the search well posed.

The callables still clip to ``ETA_CLIP = (0.1, 1.0)`` for safety far outside
the operating range; :func:`clipped` says whether a *reported* operating point
sits on that clip, the sweep flags such rows and the figures draw them.
"""

from __future__ import annotations

from collections.abc import Callable

from .config import A_N, A_P, B_V, ETA_CLIP, N_STAR_C, PR_C

__all__ = ["EfficiencyFn", "multiplier", "raw_eta", "clipped", "make_speed_case", "make_pr_case", "constant"]

EfficiencyFn = Callable[[float, float], float]
CENTRE = {"n_star": N_STAR_C, "p_r": PR_C}
CURVATURE = {"n_star": A_N, "p_r": A_P}


def multiplier(x: float, driver: str, shape: str) -> float:
    """Relative multiplier for ``driver`` in ``{"n_star", "p_r"}`` and ``shape`` in ``{"quadratic", "linear"}``."""
    d = x - CENTRE[driver]
    if shape == "quadratic":
        return 1.0 - CURVATURE[driver] * d * d
    if shape == "linear":
        return 1.0 - B_V * d
    raise ValueError(f"unknown shape {shape!r}")


def raw_eta(eta0: float, x: float, driver: str, shape: str) -> float:
    return eta0 * multiplier(x, driver, shape)


def clipped(eta0: float, x: float, driver: str, shape: str) -> bool:
    """True when the unclipped value lies outside ``ETA_CLIP`` (the callable returned a clip bound)."""
    v = raw_eta(eta0, x, driver, shape)
    return v < ETA_CLIP[0] or v > ETA_CLIP[1]


def _clip(v: float) -> float:
    return min(max(v, ETA_CLIP[0]), ETA_CLIP[1])


def make_speed_case(eta0: float, rps_rated: float, shape: str) -> EfficiencyFn:
    """``η(P_r, rps) = η₀ · m(rps / rps_rated)``; the pressure ratio is ignored."""

    def eta(pressure_ratio: float, rps: float) -> float:
        del pressure_ratio
        return _clip(raw_eta(eta0, rps / rps_rated, "n_star", shape))

    return eta


def make_pr_case(eta0: float, shape: str) -> EfficiencyFn:
    """``η(P_r, rps) = η₀ · m(P_r)``; the speed is ignored."""

    def eta(pressure_ratio: float, rps: float) -> float:
        del rps
        return _clip(raw_eta(eta0, pressure_ratio, "p_r", shape))

    return eta


def constant(eta0: float) -> EfficiencyFn:
    """A two-argument constant so every case goes through the same call path."""

    def eta(pressure_ratio: float, rps: float) -> float:
        del pressure_ratio, rps
        return eta0

    return eta
