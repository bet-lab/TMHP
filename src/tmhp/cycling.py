"""On/off cycling below the minimum continuous capacity.

A variable-speed compressor modulates down to its minimum speed and no
further. A request below what the unit delivers there is met by switching the
compressor on and off, and every start costs something: the refrigerant has to
be redistributed and the coils brought back to their working temperatures
before the capacity is there, while the compressor already draws power. EN
14825 states that loss as a degradation of the continuous-operation COP at the
minimum capacity, as a function of the capacity ratio

.. math::

    CR = \\frac{\\text{requested heat}}{\\text{capacity at the minimum step}}
    \\qquad (0 < CR \\le 1).

Two forms are in the standard, and both are here:

* units delivering to **air** (air-to-air, water-to-air; cooling and heating)
  -- the degradation coefficient ``Cd``, default 0.25::

      COP_cyc = COP_min * (1 - Cd * (1 - CR))

* units delivering to **water** (air-to-water, water/brine-to-water) -- the
  degradation coefficient ``Cc``, default 0.9::

      COP_cyc = COP_min * CR / (Cc * CR + (1 - Cc))

The ratio ``COP_cyc / COP_min`` is the *part-load factor* ``PLF``. The
compressor then runs for the fraction ``CR / PLF`` of the time (the *runtime
fraction*): it delivers ``CR`` of the minimum capacity while drawing
``CR / PLF`` of the power it draws there.

The coefficients are the defaults the standard prescribes when a unit has not
been tested for them; a tested value replaces them without any other change.

How a result row is averaged
----------------------------
:func:`apply_cycling` turns the continuous minimum-speed operating point into
the mean over the cycling period:

* heat and flow rates are multiplied by ``CR`` -- the delivered heat equals
  the request, and the refrigerant cycle and both air streams stay in balance
  with it. Power that only moves the delivered heat (an indoor fan) belongs
  here: it runs while heat is delivered, and its heat is part of the delivery;
* the electrical powers of the refrigeration machine (compressor, outdoor fan)
  are multiplied by the runtime fraction ``CR / PLF``;
* temperatures, pressures, speed and efficiencies keep the values of the
  running machine.

The electrical energy the machine draws beyond ``CR`` times its continuous
power is the cycling loss; it is reported, and it is the only place where the
mean row is not the running row scaled by one number.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "CD_AIR_DEFAULT",
    "CC_WATER_DEFAULT",
    "CAPACITY_RATIO_KEY",
    "PART_LOAD_FACTOR_KEY",
    "RUNTIME_FRACTION_KEY",
    "MIN_CAPACITY_KEY",
    "CYCLING_LOSS_KEY",
    "CyclingDegradation",
    "CyclingState",
    "resolve_cycling",
    "apply_cycling",
    "continuous_keys",
]

#: EN 14825 default degradation coefficient of a unit delivering to air [-].
CD_AIR_DEFAULT = 0.25
#: EN 14825 default degradation coefficient of a unit delivering to water [-].
CC_WATER_DEFAULT = 0.9

#: Requested heat over the capacity at the minimum compressor speed [-].
CAPACITY_RATIO_KEY = "capacity_ratio [-]"
#: Mean COP over the continuous COP at the minimum compressor speed [-].
PART_LOAD_FACTOR_KEY = "part_load_factor [-]"
#: Fraction of the time the compressor runs [-] (the boilers' step key).
RUNTIME_FRACTION_KEY = "runtime_fraction [-]"
#: Heat delivered in continuous operation at the minimum compressor speed [W].
MIN_CAPACITY_KEY = "Q_min [W]"
#: Mean electrical power drawn beyond the continuous power times ``CR`` [W].
CYCLING_LOSS_KEY = "E_cycling_loss [W]"


@dataclass(frozen=True)
class CyclingDegradation:
    """EN 14825 degradation of the COP while the compressor cycles on and off.

    Parameters
    ----------
    coefficient : float
        ``Cd`` for ``sink="air"`` (0 <= Cd < 1; 0 is loss-free cycling) or
        ``Cc`` for ``sink="water"`` (0 < Cc <= 1; 1 is loss-free cycling).
    sink : {"air", "water"}
        Which of the two EN 14825 forms applies: the medium the unit delivers
        its heat (or cooling) to.
    """

    coefficient: float = CD_AIR_DEFAULT
    sink: Literal["air", "water"] = "air"

    def __post_init__(self) -> None:
        c = self.coefficient
        if self.sink not in ("air", "water"):
            raise ValueError(f"sink must be 'air' or 'water', got {self.sink!r}")
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c):
            raise ValueError(f"cycling degradation coefficient must be a finite number, got {c!r}")
        if self.sink == "air" and not 0.0 <= c < 1.0:
            raise ValueError(f"Cd (sink='air') must satisfy 0 <= Cd < 1, got {c!r}")
        if self.sink == "water" and not 0.0 < c <= 1.0:
            raise ValueError(f"Cc (sink='water') must satisfy 0 < Cc <= 1, got {c!r}")

    def part_load_factor(self, capacity_ratio: float) -> float:
        """``COP_cyc / COP_min`` at a capacity ratio (1.0 at or above ``CR = 1``)."""
        cr = float(capacity_ratio)
        if not math.isfinite(cr) or cr < 0.0:
            raise ValueError(f"capacity_ratio must be finite and >= 0, got {capacity_ratio!r}")
        if cr >= 1.0:
            return 1.0
        if self.sink == "air":
            return 1.0 - self.coefficient * (1.0 - cr)
        return cr / (self.coefficient * cr + (1.0 - self.coefficient))

    def runtime_fraction(self, capacity_ratio: float) -> float:
        """Fraction of the time the compressor runs, ``CR / PLF`` (0 at ``CR = 0``)."""
        cr = min(1.0, float(capacity_ratio))
        if cr <= 0.0:
            return 0.0
        return min(1.0, cr / self.part_load_factor(cr))

    def state(self, Q_request: float, Q_min: float) -> CyclingState:
        """Cycling state for a request below the minimum continuous capacity.

        ``Q_request`` and ``Q_min`` are magnitudes [W]. A request at or above
        ``Q_min`` is continuous operation: ``CR = PLF = runtime fraction = 1``.
        """
        request, floor = abs(float(Q_request)), abs(float(Q_min))
        if not (math.isfinite(request) and math.isfinite(floor) and floor > 0.0):
            raise ValueError(f"Require finite Q_request and Q_min > 0, got {Q_request!r}, {Q_min!r}")
        cr = min(1.0, request / floor)
        return CyclingState(
            capacity_ratio=cr,
            part_load_factor=self.part_load_factor(cr),
            runtime_fraction=self.runtime_fraction(cr),
        )


@dataclass(frozen=True)
class CyclingState:
    """Where a cycling unit sits between off and continuous minimum-speed operation."""

    #: Requested heat over the capacity at the minimum compressor speed [-].
    capacity_ratio: float
    #: Mean COP over the continuous COP at the minimum compressor speed [-].
    part_load_factor: float
    #: Fraction of the time the compressor runs [-].
    runtime_fraction: float

    @property
    def cycling(self) -> bool:
        return self.capacity_ratio < 1.0


#: Continuous operation: nothing to average.
CONTINUOUS = CyclingState(capacity_ratio=1.0, part_load_factor=1.0, runtime_fraction=1.0)
#: Compressor off for the whole period.
OFF = CyclingState(capacity_ratio=0.0, part_load_factor=1.0, runtime_fraction=0.0)


def resolve_cycling(
    value: CyclingDegradation | float | None,
    sink: Literal["air", "water"],
) -> CyclingDegradation | None:
    """Constructor argument -> degradation model.

    ``None`` switches cycling off (the unit is reported running continuously
    at its minimum speed, delivering more than the request); a number is the
    coefficient of the form that belongs to ``sink``; a
    :class:`CyclingDegradation` is used as given.
    """
    if value is None:
        return None
    if isinstance(value, CyclingDegradation):
        return value
    return CyclingDegradation(coefficient=value, sink=sink)


def continuous_keys(result: dict, state: CyclingState = CONTINUOUS, Q_min: float = math.nan) -> dict:
    """Write the cycling keys of a row that was not averaged (in place)."""
    result[CAPACITY_RATIO_KEY] = state.capacity_ratio
    result[PART_LOAD_FACTOR_KEY] = state.part_load_factor
    result[RUNTIME_FRACTION_KEY] = state.runtime_fraction
    result[MIN_CAPACITY_KEY] = Q_min
    result[CYCLING_LOSS_KEY] = 0.0
    return result


def apply_cycling(
    result: dict,
    state: CyclingState,
    *,
    delivered_key: str,
    rate_keys: Iterable[str],
    power_keys: Iterable[str],
    delivery_power_keys: Iterable[str] = (),
    total_power_key: str = "E_tot [W]",
) -> dict:
    """Average a continuous minimum-speed row over the cycling period (in place).

    ``rate_keys`` -- heat and flow rates, ``delivered_key`` among them -- and
    ``delivery_power_keys`` -- power that runs only while heat is delivered,
    such as an indoor fan -- are multiplied by the capacity ratio;
    ``power_keys`` -- the refrigeration machine -- by the runtime fraction.
    ``total_power_key`` is reduced by what the powers in both groups lost.
    Adds the cycling keys: ``Q_min [W]`` is the magnitude of the continuous
    delivery the row came in with, ``E_cycling_loss [W]`` what ``power_keys``
    draw beyond the capacity ratio times their continuous value. COP columns
    are left to the caller, which knows how they are defined.
    """

    def scale(keys: Iterable[str], factor: float) -> tuple[float, float]:
        """Scale in place; return (sum before, sum after) of the finite values."""
        before = after = 0.0
        for key in keys:
            value = result.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                result[key] = float(value) * factor
                before += float(value)
                after += result[key]
        return before, after

    q_min = abs(float(result[delivered_key]))
    scale(rate_keys, state.capacity_ratio)
    with_delivery = scale(delivery_power_keys, state.capacity_ratio)
    machine = scale(power_keys, state.runtime_fraction)
    total = result.get(total_power_key)
    if isinstance(total, (int, float)) and not isinstance(total, bool) and math.isfinite(total):
        result[total_power_key] = float(total) - (with_delivery[0] - with_delivery[1]) - (machine[0] - machine[1])
    continuous_keys(result, state, q_min)
    result[CYCLING_LOSS_KEY] = machine[0] * (state.runtime_fraction - state.capacity_ratio)
    return result
