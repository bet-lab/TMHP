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

The ratio ``COP_cyc / COP_min`` is the *part-load factor* ``PLF``. The unit
delivers ``CR`` of the minimum capacity while drawing ``CR / PLF`` of the
power it draws there (the *power fraction*).

What the two forms describe
---------------------------
* **Air.** The loss is in the start: the compressor runs longer than the heat
  it delivers accounts for. The *runtime fraction* -- the share of the time
  the compressor runs -- is ``CR / PLF``, the same number as the power
  fraction.
* **Water.** The standard takes the start-up loss of a hydronic unit as
  negligible; what it charges is the power the unit keeps drawing while the
  compressor is off (controls, crankcase heater, circulation). ``Cc`` is one
  minus that off-state power over the power drawn while running, so the mean
  input is ``P_min * (CR + (1 - Cc) * (1 - CR))``, which is ``P_min * CR /
  PLF``. The compressor runs for ``CR`` of the time: the runtime fraction is
  ``CR``, the power fraction is larger by the off-state draw. The default 0.9
  is an off-state draw of a tenth of the running input; a unit with a measured
  standby power ``P_off`` has ``Cc = 1 - P_off / P_min``. A request of exactly
  zero is the unit switched off, not on standby: no power.

The coefficients are the defaults the standard prescribes when a unit has not
been tested for them; a tested value replaces them without any other change.

How a result row is averaged
----------------------------
:func:`apply_cycling` turns the continuous minimum-speed operating point into
the mean over the cycling period:

* heat and flow rates are multiplied by ``CR`` -- the delivered heat equals
  the request, and the refrigerant cycle and both secondary streams stay in
  balance with it. Power that only moves the delivered heat (an indoor fan)
  belongs here: it runs while heat is delivered, and its heat is part of the
  delivery;
* the electrical powers of the refrigeration machine (compressor, outdoor fan,
  source-side pump) are multiplied by the power fraction ``CR / PLF``;
* temperatures, pressures, speed and efficiencies keep the values of the
  running machine.

The electrical energy the machine draws beyond ``CR`` times its continuous
power is the cycling loss; it is reported, and it is the only place where the
mean row is not the running row scaled by one number. It is not part of any
heat flow of the row: the refrigerant duties of the mean row are the running
duties times ``CR``, on the source side as on the delivery side.

:func:`report_cycling` is the same step for a solved row of any family (it
decides whether the row cycles). The boilers use it for ``analyze_steady``
only (:func:`report_boiler_cycling`, :func:`steady_cycling`): in a dynamic run
their tank thermostat already switches the compressor, which then runs at its
nominal duty for part of a step, so nothing is left to average there.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import wraps
from typing import Any, Literal, TypeVar, cast

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
    "report_cycling",
    "report_boiler_cycling",
    "steady_cycling",
]

_F = TypeVar("_F", bound=Callable[..., Any])

#: EN 14825 default degradation coefficient of a unit delivering to air [-].
CD_AIR_DEFAULT = 0.25
#: EN 14825 default degradation coefficient of a unit delivering to water [-].
CC_WATER_DEFAULT = 0.9

#: Requested heat over the capacity at the minimum compressor speed [-].
CAPACITY_RATIO_KEY = "capacity_ratio [-]"
#: Mean COP over the continuous COP at the minimum compressor speed [-].
PART_LOAD_FACTOR_KEY = "part_load_factor [-]"
#: Fraction of the time the compressor runs [-] (the boilers' step key too).
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
        denominator = self.coefficient * cr + (1.0 - self.coefficient)
        # Cc = 1 is the loss-free limit (PLF = 1 at every load); at CR = 0 the
        # expression is 0/0 there.
        return 1.0 if denominator <= 0.0 else cr / denominator

    def power_fraction(self, capacity_ratio: float) -> float:
        """Mean electrical input over the input at the minimum speed, ``CR / PLF`` (0 at ``CR = 0``)."""
        cr = min(1.0, float(capacity_ratio))
        if cr <= 0.0:
            return 0.0
        return min(1.0, cr / self.part_load_factor(cr))

    def runtime_fraction(self, capacity_ratio: float) -> float:
        """Fraction of the time the compressor runs (0 at ``CR = 0``).

        ``CR / PLF`` for ``sink="air"``, where the loss is run time that
        delivers no heat; ``CR`` for ``sink="water"``, where the loss is the
        power drawn while the compressor is off (see the module docstring).
        """
        if self.sink == "water":
            return max(0.0, min(1.0, float(capacity_ratio)))
        return self.power_fraction(capacity_ratio)

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

    @property
    def power_fraction(self) -> float:
        """Mean electrical input of the machine over its continuous input, ``CR / PLF``."""
        if self.capacity_ratio <= 0.0:
            return 0.0
        return min(1.0, self.capacity_ratio / self.part_load_factor)


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
    ``power_keys`` -- the refrigeration machine -- by the power fraction
    ``CR / PLF`` (the runtime fraction of a unit delivering to air).
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
    power_fraction = state.power_fraction
    machine = scale(power_keys, power_fraction)
    total = result.get(total_power_key)
    if isinstance(total, (int, float)) and not isinstance(total, bool) and math.isfinite(total):
        result[total_power_key] = float(total) - (with_delivery[0] - with_delivery[1]) - (machine[0] - machine[1])
    continuous_keys(result, state, q_min)
    result[CYCLING_LOSS_KEY] = machine[0] * (power_fraction - state.capacity_ratio)
    return result


def report_cycling(
    result: dict,
    cycling: CyclingDegradation | None,
    request: float,
    *,
    delivered_key: str,
    rate_keys: Iterable[str],
    power_keys: Iterable[str],
    delivery_power_keys: Iterable[str] = (),
    total_power_key: str = "E_tot [W]",
) -> bool:
    """Write the cycling keys of a solved row; average it if the unit cycles (in place).

    The row cycles when it is a running operating point on the compressor's
    speed floor, or at the smallest duty the envelope admits
    (``capacity_clamped == "min"``), that delivers more than the
    ``request`` and a degradation model is given: it is then averaged with
    :func:`apply_cycling`. Every other row -- continuous operation, the off
    state, a failed solve, cycling switched off (``cycling=None``) -- is left
    as it is and gets the keys of :func:`continuous_keys` (``Q_min [W]`` is
    the continuous delivery of a row on the speed floor, NaN elsewhere).
    Returns whether the row was averaged; COP columns and sums the caller
    derives from the averaged terms are left to the caller.
    """
    running = bool(result.get("hp_is_on", False)) and result.get("failure_reason", "none") == "none"
    delivered = abs(float(result.get(delivered_key, 0.0))) if running else 0.0
    on_floor = running and result.get("capacity_clamped") == "min" and math.isfinite(delivered)
    asked = abs(float(request))
    if on_floor and cycling is not None and 0.0 < asked < delivered:
        state = cycling.state(asked, delivered)
        apply_cycling(
            result,
            state,
            delivered_key=delivered_key,
            rate_keys=rate_keys,
            power_keys=power_keys,
            delivery_power_keys=delivery_power_keys,
            total_power_key=total_power_key,
        )
        return True
    continuous_keys(result, CONTINUOUS if running else OFF, delivered if on_floor else math.nan)
    return False


def report_boiler_cycling(
    result: dict,
    cycling: CyclingDegradation | None,
    request: float,
    *,
    rate_keys: Iterable[str],
    power_keys: Iterable[str],
    source_heat_key: str | None = None,
    hp_capacity: float = math.nan,
) -> bool:
    """:func:`report_cycling` for a boiler's steady row (heat to the tank, ``Q_ref_tank [W]``).

    An averaged row also gets back what a boiler row derives from the averaged
    terms: both COPs (the heat to the tank over the compressor input and over
    the total input), ``PLR_delivered [-]`` (with ``hp_capacity``), the source
    loop's net heat ``Q_bhe [W]`` (``source_heat_key`` less the pump power)
    and ``E_cmp_plus_pmp [W]``, each where the row has it.
    """
    averaged = report_cycling(
        result,
        cycling,
        request,
        delivered_key="Q_ref_tank [W]",
        rate_keys=rate_keys,
        power_keys=power_keys,
    )
    if averaged:
        delivered = float(result["Q_ref_tank [W]"])
        result["cop_ref [-]"] = delivered / result["E_cmp [W]"]
        result["cop_sys [-]"] = delivered / result["E_tot [W]"]
        if "PLR_delivered [-]" in result and hp_capacity > 0.0:
            result["PLR_delivered [-]"] = delivered / hp_capacity
        if source_heat_key is not None and "Q_bhe [W]" in result:
            result["Q_bhe [W]"] = result[source_heat_key] - result["E_pmp [W]"]
        if "E_cmp_plus_pmp [W]" in result:
            result["E_cmp_plus_pmp [W]"] = result["E_cmp [W]"] + result["E_pmp [W]"]
    return averaged


def steady_cycling(duty_arg: str) -> Callable[[_F], _F]:
    """Decorate a boiler's ``analyze_steady`` with the part-load report.

    The decorated method is solved as before; its result row is then handed
    to the model's ``_report_part_load(result, request)`` with the caller's
    ``duty_arg``. It sits outside the envelope's duty reduction
    (:func:`tmhp.compressor_envelope.steady_envelope`), whose trial solves
    must see the continuous rows.
    """

    def decorate(function: _F) -> _F:
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            want_dict = bool(bound.arguments.get("return_dict", True))
            if "return_dict" in bound.arguments:
                bound.arguments["return_dict"] = True
            result = function(*bound.args, **bound.kwargs)
            if not isinstance(result, dict):
                return result
            self._report_part_load(result, float(bound.arguments[duty_arg]))
            if want_dict:
                return result
            import pandas as pd

            return pd.DataFrame([result])

        # ``analyze_steady.__wrapped__`` stays the undecorated solve (one
        # request, no duty reduction, no averaging), as it is under
        # ``steady_envelope`` alone.
        wrapped.__wrapped__ = getattr(function, "__wrapped__", function)  # type: ignore[attr-defined]
        return cast(_F, wrapped)

    return decorate
