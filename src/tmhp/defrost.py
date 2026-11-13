"""Frost on the outdoor coil of an air-source heat pump, and its defrost.

In heating the outdoor coil runs below the outdoor air. When its surface is
below 0 degC and colder than the air's dew point, water vapour deposits on it
as frost: the air passages narrow, the evaporating pressure falls, and both
capacity and power drop until the unit stops heating to melt the frost. A
catalogue or a certified rating at 2 degC or -7 degC is the mean over those
frosting and defrosting periods, not the frost-free steady state a cycle model
computes.

The correction here is the one EnergyPlus applies to its DX heating coils
(``Coil:Heating:DX:SingleSpeed``; Engineering Reference, "Frost Adjustment
Factors" and "Defrost Operation"), which it takes from DOE-2.1E (Miller and
Jaster 1985). For on-demand defrost:

.. math::

    T_{coil} &= 0.82\\,T_{out} - 8.589 \\qquad [^\\circ C]

    \\Delta\\omega &= \\max\\left(10^{-6},\\;
        \\omega_{out} - \\omega_{sat}(T_{coil})\\right)

    t_{frac} &= \\frac{1}{1 + 0.01446 / \\Delta\\omega}

    \\text{heating capacity multiplier} &= 0.875\\,(1 - t_{frac})

    \\text{input power multiplier} &= 0.954\\,(1 - t_{frac})

with no correction when the outdoor dry bulb is above the maximum temperature
for defrost operation (5 degC). ``t_frac`` is the fraction of the compressor's
runtime spent defrosting. Reverse-cycle defrost also cools the room while it
runs, an extra heating load of

.. math::

    Q_{defrost} = 0.01\\,t_{frac}\\,(7.222 - T_{out})\\,Q_{rated} / 1.01667 .

Three things differ from EnergyPlus. The first is the onset of frosting:

* **Onset.** In EnergyPlus the correction is a switch at the maximum outdoor
  temperature for defrost operation: at 5.0 degC and the default humidity the
  capacity multiplier is 0.771, at 5.1 degC it is 1, and the system COP of a
  unit steps by a quarter across a tenth of a kelvin. That limit is a setting
  of the defrost controller, not a temperature at which a coil frosts over:
  the coil-temperature correlation is at -4.5 degC there, and it puts a
  0 degC coil at 10.5 degC outdoor. A coil does not frost all at once
  either. Near the onset its surface is close to freezing, so frost forms on
  part of it and for part of the time, and what a rating measures falls off
  gradually. Here the limit keeps its EnergyPlus meaning -- no frost above
  ``T_out_max`` (5 degC) -- and the correction grows from nothing there to
  its full size ``transition_K`` (3 K) below, at 2 degC: the frosting rating
  condition of EN 14511 (A2: 2 degC dry bulb, 1 degC wet bulb), which is
  also where the default humidity comes from. In between it applies to the
  share ``w`` of the runtime that rises linearly from zero to one:

  .. math::

      t_{frac} &\\leftarrow w\\,t_{frac}

      \\text{capacity multiplier} &= 1 - w\\,\\left(1 - 0.875\\,(1 - t_{frac})\\right)

      \\text{power multiplier} &= 1 - w\\,\\left(1 - 0.954\\,(1 - t_{frac})\\right)

  -- the mean of a unit that frosts for the share ``w`` of its runtime and
  runs frost-free for the rest. At and below 2 degC and above 5 degC nothing
  differs from EnergyPlus; ``transition_K=0`` restores the switch.

The other two are there so that no input is added:

* **Defrost power.** EnergyPlus asks for a curve of defrost power per unit of
  rated capacity. Here the compressor is taken to draw, while it defrosts,
  the power it draws while it heats: ``E_defrost = t_frac * E_cmp``.
* **Outdoor humidity.** EnergyPlus reads it from the weather file. Here it is
  an optional input; without it the relative humidity is 84 %, the value of
  the EN 14511 rating condition A2 (dry bulb 2 degC, wet bulb 1 degC).

The coil temperature is the correlation above, not the evaporating temperature
a cycle model solves: the constants 0.01446, 0.875 and 0.954 were fitted with
that estimate, and the solved saturation temperature is a different quantity
(colder than the fin surface, and dependent on load). A caller that wants its
own coil temperature passes ``T_coil_C``; the physical condition that the coil
be below 0 degC is checked either way.

How a result row is averaged
----------------------------
A request ``Q`` has to be met by the mean over frosting and defrost, so the
frost-free cycle is solved at the larger duty
``(Q + Q_defrost) / capacity multiplier``. :func:`apply_defrost` then turns
that frost-free row into the mean:

* heat and flow rates are multiplied by ``heat_factor`` (delivered heat over
  frost-free heat), so the delivered heat is the request and the refrigerant
  cycle stays in balance with it. Power that only moves the delivered heat (an
  indoor fan) is averaged with it;
* compressor power is multiplied by ``power multiplier + t_frac`` (heating,
  then defrosting) and outdoor-fan power by the power multiplier;
* temperatures, pressures, speed and efficiencies keep the frost-free values.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "T_OUT_MAX_DEFROST_C",
    "FROST_TRANSITION_K",
    "RH_OUT_DEFAULT",
    "DEFROST_FRACTION_KEY",
    "FROST_CAPACITY_FACTOR_KEY",
    "FROST_POWER_FACTOR_KEY",
    "DEFROST_POWER_KEY",
    "DEFROST_LOAD_KEY",
    "RH_OUT_KEY",
    "DefrostModel",
    "FrostState",
    "NO_FROST",
    "resolve_defrost",
    "frost_free_keys",
    "apply_defrost",
    "saturation_humidity_ratio",
    "humidity_ratio",
]

#: Maximum outdoor dry-bulb temperature for defrost operation [degC] (EnergyPlus default).
T_OUT_MAX_DEFROST_C = 5.0
#: Width of the outdoor-temperature band below ``T_out_max`` over which the frost
#: correction grows to its full size [K]: from the EnergyPlus limit (5 degC, no
#: frost above) to the frosting rating condition of EN 14511 (A2, 2 degC dry bulb).
FROST_TRANSITION_K = 3.0
#: Outdoor relative humidity used when none is given [-]: EN 14511 A2 (2 degC dry bulb, 1 degC wet bulb).
RH_OUT_DEFAULT = 0.84
#: Standard atmospheric pressure [Pa].
P_ATM = 101325.0

#: Fraction of the compressor's runtime spent defrosting [-].
DEFROST_FRACTION_KEY = "defrost_fraction [-]"
#: Heating capacity multiplier of the frosted coil, defrost time included [-].
FROST_CAPACITY_FACTOR_KEY = "frost_capacity_factor [-]"
#: Input power multiplier of the frosted coil, defrost time included [-].
FROST_POWER_FACTOR_KEY = "frost_power_factor [-]"
#: Mean compressor power spent defrosting [W].
DEFROST_POWER_KEY = "E_defrost [W]"
#: Mean heat a reverse-cycle defrost takes from the room [W].
DEFROST_LOAD_KEY = "Q_defrost [W]"
#: Outdoor relative humidity the frost correction used [-].
RH_OUT_KEY = "RH_out [-]"

# EnergyPlus Engineering Reference, Coil:Heating:DX:SingleSpeed, on-demand defrost.
_COIL_SLOPE = 0.82
_COIL_OFFSET_K = -8.589
_DW_MIN = 1.0e-6
_DW_SCALE = 0.01446
_CAPACITY_FROSTED = 0.875
_POWER_FROSTED = 0.954
# ... reverse-cycle defrost load.
_LOAD_COEFFICIENT = 0.01
_LOAD_T_REF_C = 7.222
_LOAD_CAPACITY_DIVISOR = 1.01667


def saturation_humidity_ratio(T_C: float, P_Pa: float = P_ATM) -> float:
    """Humidity ratio of saturated air at ``T_C`` [kg water / kg dry air] (over ice below 0 degC)."""
    return humidity_ratio(T_C, 1.0, P_Pa)


def humidity_ratio(T_C: float, RH: float, P_Pa: float = P_ATM) -> float:
    """Humidity ratio of moist air at dry bulb ``T_C`` and relative humidity ``RH`` (0-1)."""
    from CoolProp.HumidAirProp import HAPropsSI

    if RH <= 0.0:
        return 0.0
    return float(HAPropsSI("W", "T", T_C + 273.15, "P", P_Pa, "R", RH))


@dataclass(frozen=True)
class FrostState:
    """Frost correction at one outdoor condition."""

    #: Whether the outdoor coil frosts here.
    active: bool
    #: Fraction of the compressor's runtime spent defrosting [-].
    defrost_fraction: float
    #: Heating capacity multiplier, ``0.875 (1 - t_frac)`` when frosting all the time [-].
    capacity_multiplier: float
    #: Input power multiplier, ``0.954 (1 - t_frac)`` when frosting all the time [-].
    power_multiplier: float
    #: Outdoor coil temperature the correction used [degC].
    T_coil_C: float = math.nan
    #: Humidity ratio of the outdoor air above saturation at the coil [kg/kg].
    humidity_ratio_difference: float = math.nan
    #: Outdoor relative humidity the correction used [-].
    RH_out: float = math.nan
    #: Share of the runtime the coil frosts [-]: 0 above ``T_out_max``, rising to
    #: 1 across the transition below it. The three figures above are the mean of
    #: frosting for this share and frost-free operation for the rest.
    frost_share: float = 1.0


#: No frost: nothing is corrected.
NO_FROST = FrostState(
    active=False, defrost_fraction=0.0, capacity_multiplier=1.0, power_multiplier=1.0, frost_share=0.0
)


@dataclass(frozen=True)
class DefrostModel:
    """EnergyPlus / DOE-2.1E frost adjustment with on-demand reverse-cycle defrost.

    Parameters
    ----------
    T_out_max : float
        Outdoor dry-bulb temperature above which the coil does not frost [degC].
    RH_out : float
        Outdoor relative humidity (0-1) used when a call does not give one.
    transition_K : float
        Width of the band below ``T_out_max`` over which the correction grows
        linearly from nothing to its full size [K]; in full at and below
        ``T_out_max - transition_K``. ``0`` is the EnergyPlus switch at
        ``T_out_max``.
    """

    T_out_max: float = T_OUT_MAX_DEFROST_C
    RH_out: float = RH_OUT_DEFAULT
    transition_K: float = FROST_TRANSITION_K

    def __post_init__(self) -> None:
        if isinstance(self.T_out_max, bool) or not math.isfinite(self.T_out_max):
            raise ValueError(f"T_out_max must be a finite temperature [degC], got {self.T_out_max!r}")
        if (
            isinstance(self.transition_K, bool)
            or not isinstance(self.transition_K, (int, float))
            or not math.isfinite(self.transition_K)
            or self.transition_K < 0.0
        ):
            raise ValueError(f"transition_K must be a finite width >= 0 [K], got {self.transition_K!r}")
        _check_humidity(self.RH_out)

    def frost_share(self, T_out_C: float) -> float:
        """Share of the runtime the coil frosts at outdoor dry bulb ``T_out_C`` [-].

        0 above ``T_out_max``, 1 at and below ``T_out_max - transition_K``,
        linear in between. With ``transition_K = 0`` it is 1 at and below
        ``T_out_max`` (the EnergyPlus switch).
        """
        if T_out_C > self.T_out_max:
            return 0.0
        if self.transition_K <= 0.0:
            return 1.0
        return min(1.0, (self.T_out_max - T_out_C) / self.transition_K)

    def frost_state(
        self,
        T_out_C: float,
        RH_out: float | None = None,
        *,
        T_coil_C: float | None = None,
        P_Pa: float = P_ATM,
    ) -> FrostState:
        """Frost correction at outdoor dry bulb ``T_out_C`` and relative humidity ``RH_out`` (0-1).

        ``T_coil_C`` replaces the coil-temperature correlation
        ``0.82 T_out - 8.589``. No frost above ``T_out_max``; below it the
        correction grows to its full size across ``transition_K``
        (:meth:`frost_share`). No frost on a coil at or above 0 degC either.
        """
        rh = self.RH_out if RH_out is None else _check_humidity(RH_out)
        if not math.isfinite(T_out_C):
            raise ValueError(f"T_out_C must be finite, got {T_out_C!r}")
        coil = _COIL_SLOPE * T_out_C + _COIL_OFFSET_K if T_coil_C is None else float(T_coil_C)
        share = self.frost_share(T_out_C)
        if not share > 0.0 or not coil < 0.0:
            return FrostState(False, 0.0, 1.0, 1.0, T_coil_C=coil, RH_out=rh, frost_share=0.0)
        dw = max(_DW_MIN, humidity_ratio(T_out_C, rh, P_Pa) - saturation_humidity_ratio(coil, P_Pa))
        t_frac = 1.0 / (1.0 + _DW_SCALE / dw)
        # Frosting for the share of the runtime, frost-free for the rest. With
        # a share of one these are the EnergyPlus expressions unchanged.
        return FrostState(
            active=True,
            defrost_fraction=share * t_frac,
            capacity_multiplier=1.0 - share * (1.0 - _CAPACITY_FROSTED * (1.0 - t_frac)),
            power_multiplier=1.0 - share * (1.0 - _POWER_FROSTED * (1.0 - t_frac)),
            T_coil_C=coil,
            humidity_ratio_difference=dw,
            RH_out=rh,
            frost_share=share,
        )

    @staticmethod
    def defrost_load(frost: FrostState, T_out_C: float, Q_rated: float) -> float:
        """Mean heat a reverse-cycle defrost takes from the room [W] (>= 0)."""
        if not frost.active:
            return 0.0
        load = _LOAD_COEFFICIENT * frost.defrost_fraction * (_LOAD_T_REF_C - T_out_C)
        return max(0.0, load * abs(Q_rated) / _LOAD_CAPACITY_DIVISOR)

    @staticmethod
    def gross_duty(frost: FrostState, Q_request: float, Q_defrost: float) -> float:
        """Frost-free duty [W] whose mean over frosting and defrost is ``Q_request``."""
        return (abs(Q_request) + Q_defrost) / frost.capacity_multiplier


def _check_humidity(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise ValueError(f"Outdoor relative humidity must be a fraction in [0, 1], got {value!r}")
    return float(value)


def resolve_defrost(value: DefrostModel | bool | None) -> DefrostModel | None:
    """Constructor argument -> frost model: ``None`` / ``False`` off, ``True`` the default model."""
    if value is None or value is False:
        return None
    if value is True:
        return DefrostModel()
    if isinstance(value, DefrostModel):
        return value
    raise ValueError(f"defrost must be a DefrostModel, True, False or None, got {value!r}")


def frost_free_keys(result: dict, frost: FrostState = NO_FROST) -> dict:
    """Write the frost keys of a row that was not corrected (in place)."""
    result[DEFROST_FRACTION_KEY] = 0.0
    result[FROST_CAPACITY_FACTOR_KEY] = 1.0
    result[FROST_POWER_FACTOR_KEY] = 1.0
    result[DEFROST_POWER_KEY] = 0.0
    result[DEFROST_LOAD_KEY] = 0.0
    result[RH_OUT_KEY] = frost.RH_out
    return result


def apply_defrost(
    result: dict,
    frost: FrostState,
    *,
    heat_factor: float,
    Q_defrost: float,
    rate_keys: Iterable[str],
    compressor_power_key: str = "E_cmp [W]",
    outdoor_power_keys: Iterable[str] = (),
    delivery_power_keys: Iterable[str] = (),
    total_power_key: str = "E_tot [W]",
) -> dict:
    """Average a frost-free row over frosting and defrost (in place).

    ``rate_keys`` and ``delivery_power_keys`` (power that runs only while heat
    is delivered, such as an indoor fan) are multiplied by ``heat_factor``; the
    compressor power by ``power multiplier + t_frac``; ``outdoor_power_keys``
    by the power multiplier. ``total_power_key`` is reduced by what those
    powers lost. Adds the frost keys. COP columns are left to the caller.
    """

    def scale(keys: Iterable[str], factor: float) -> float:
        """Scale in place; return how much the finite values lost in total."""
        lost = 0.0
        for key in keys:
            value = result.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                result[key] = float(value) * factor
                lost += float(value) - result[key]
        return lost

    t_frac = frost.defrost_fraction
    compressor = result.get(compressor_power_key)
    running = isinstance(compressor, (int, float)) and not isinstance(compressor, bool) and math.isfinite(compressor)
    defrost_power = t_frac * float(compressor) if running else 0.0  # type: ignore[arg-type]

    scale(rate_keys, heat_factor)
    lost = scale(delivery_power_keys, heat_factor)
    lost += scale((compressor_power_key,), frost.power_multiplier + t_frac)
    lost += scale(outdoor_power_keys, frost.power_multiplier)
    total = result.get(total_power_key)
    if isinstance(total, (int, float)) and not isinstance(total, bool) and math.isfinite(total):
        result[total_power_key] = float(total) - lost

    result[DEFROST_FRACTION_KEY] = t_frac
    result[FROST_CAPACITY_FACTOR_KEY] = frost.capacity_multiplier
    result[FROST_POWER_FACTOR_KEY] = frost.power_multiplier
    result[DEFROST_POWER_KEY] = defrost_power
    result[DEFROST_LOAD_KEY] = float(Q_defrost)
    result[RH_OUT_KEY] = frost.RH_out
    return result
