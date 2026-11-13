"""Air properties at the state an air coil draws its air.

A fan moves a volume of air, a coil exchanges heat with a mass of it. The two
are tied by the density of the air the fan draws, and that density follows the
air temperature: at a fixed volume flow, air at -25 degC carries 16 % more mass
(and heat capacity) than the 15 degC standard air of ``constants.rho_a``, air
at 35 degC 6.5 % less.

``air_density`` is the ideal-gas density of dry air,
``rho = P / (R_a T)``, with ``R_a = 287.055 J/(kg K)`` (``constants.R_a``).
At 15 degC and 101 325 Pa it gives 1.2250 kg/m3, the value of
``constants.rho_a``, so the two agree at the standard state.

What is left out, and how much it is:

- **Humidity.** Water vapour makes moist air lighter and its specific heat
  larger; per cubic metre the two nearly cancel. At 20 degC and 50 % relative
  humidity the sensible heat capacity of a cubic metre is 0.2 % above the
  dry-air figure, at 35 degC and 40 % it is 0.3 % above. The coils of the
  cycle models are dry (sensible heat only), so dry air is used.
- **Specific heat.** ``constants.c_a`` (1005 J/(kg K)) is kept constant: the
  specific heat of dry air changes by 0.1 % between -25 and 40 degC.
- **Altitude.** The pressure argument defaults to one standard atmosphere.
  The models use that default; at 1 600 m the density is 18 % lower.
- **Fan power.** The fan curves stay functions of the volume flow at the rated
  static pressure. By the fan laws the pressure rise, and so the power, is
  proportional to the density at a fixed volume flow; the models do not apply
  that (outdoor fan at -25 degC: +16 % of its power, 0.5 to 0.7 % of the total
  electrical input).

The volume flows of the air-source models (``dV_ou_a``, ``dV_iu_a``, the fan
references and limits) are volumes at the coil inlet state, so the air mass
flow of a coil is ``air_density(T_in) * dV`` and is the same before and after
the coil and the fan.
"""

from __future__ import annotations

from typing import Any

from .constants import P0_PA, R_a, c_a

__all__ = [
    "air_density",
    "air_heat_capacity_rate",
]

_KELVIN_OFFSET = 273.15


def air_density(T_C: Any, P: float = P0_PA) -> Any:
    """Density of dry air [kg/m3] at temperature ``T_C`` [degC] and pressure ``P`` [Pa].

    Ideal gas, ``rho = P / (R_a (T_C + 273.15))``. ``T_C`` may be a scalar or
    an array-like (NumPy array, pandas Series); the result has the same shape.
    A scalar at or below absolute zero, or a non-positive pressure, raises
    ``ValueError``.

    Examples
    --------
    >>> round(air_density(15.0), 4)
    1.225
    >>> round(air_density(-25.0), 3), round(air_density(35.0), 3)
    (1.422, 1.145)
    """
    if not P > 0.0:
        raise ValueError("Air pressure must be positive")
    T_K = T_C + _KELVIN_OFFSET
    if isinstance(T_K, (int, float)) and not T_K > 0.0:
        raise ValueError("Air temperature must be above absolute zero")
    return P / (R_a * T_K)


def air_heat_capacity_rate(dV: Any, T_C: Any, P: float = P0_PA) -> Any:
    """Heat capacity rate of an air stream [W/K]: ``c_a * air_density(T_C, P) * dV``.

    ``dV`` [m3/s] is the volume flow at the state (``T_C`` [degC], ``P`` [Pa])
    the density is taken at -- for a coil, its inlet air.
    """
    return c_a * air_density(T_C, P) * dV
