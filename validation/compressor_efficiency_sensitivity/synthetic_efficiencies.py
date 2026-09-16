"""Synthetic ∩-shaped efficiency multipliers for the sensitivity cases.

The shape is a piecewise quadratic in the driver ``x`` (``n*`` or ``PR``)::

    F(x) = 1 - d_low  * ((x - x_opt) / (x_low  - x_opt))^2    x <= x_opt
    F(x) = 1 - d_high * ((x - x_opt) / (x_high - x_opt))^2    x >  x_opt

so ``F(x_opt) = 1``, ``F(x_low) = 1 - d_low`` and ``F(x_high) = 1 - d_high``.
Outside ``[x_low, x_high]`` the multiplier is *held* at the endpoint value:
the speed search brackets ``rps`` over the whole envelope (up to 150 rev/s,
``n* = 2.5``) and a quadratic followed that far would go negative.  Holding
also keeps the perturbation inside the range the control run defined it on.

The anchors ``x_low / x_opt / x_high`` are not chosen by hand: the sweep reads
them off the constant-efficiency control run (``C0``) as the driver value at
the last modulating point before the speed floor, at PLR 0.60, and at PLR 1.00.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

__all__ = ["CapShape", "cap_multiplier", "make_speed_case", "make_pr_case", "constant"]

EfficiencyFn = Callable[[float, float], float]


@dataclass(frozen=True)
class CapShape:
    """Anchors and depths of one ∩ curve; ``x`` is ``n*`` or ``PR``."""

    x_low: float
    x_opt: float
    x_high: float
    d_low: float = 0.10
    d_high: float = 0.05

    def __post_init__(self) -> None:
        if not (self.x_low < self.x_opt < self.x_high):
            raise ValueError(f"anchors must satisfy x_low < x_opt < x_high, got {self}")
        if not (0.0 <= self.d_low < 1.0 and 0.0 <= self.d_high < 1.0):
            raise ValueError(f"depths must lie in [0, 1), got {self}")

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def cap_multiplier(x: float, shape: CapShape) -> float:
    """Normalised multiplier ``F(x)``; 1 at ``x_opt``, held flat outside the anchors."""
    x = min(max(x, shape.x_low), shape.x_high)
    if x <= shape.x_opt:
        r = (x - shape.x_opt) / (shape.x_low - shape.x_opt)
        return 1.0 - shape.d_low * r * r
    r = (x - shape.x_opt) / (shape.x_high - shape.x_opt)
    return 1.0 - shape.d_high * r * r


def make_speed_case(eta_peak: float, shape: CapShape, rps_rated: float) -> EfficiencyFn:
    """``eta(PR, rps) = eta_peak * F(rps / rps_rated)`` -- the pressure ratio is ignored."""

    def eta(pressure_ratio: float, rps: float) -> float:
        del pressure_ratio
        return eta_peak * cap_multiplier(rps / rps_rated, shape)

    return eta


def make_pr_case(eta_peak: float, shape: CapShape) -> EfficiencyFn:
    """``eta(PR, rps) = eta_peak * F(PR)`` -- the speed is ignored."""

    def eta(pressure_ratio: float, rps: float) -> float:
        del rps
        return eta_peak * cap_multiplier(pressure_ratio, shape)

    return eta


def constant(eta: float) -> EfficiencyFn:
    """A two-argument constant, so every case passes through the same call path."""

    def fn(pressure_ratio: float, rps: float) -> float:
        del pressure_ratio, rps
        return eta

    return fn
