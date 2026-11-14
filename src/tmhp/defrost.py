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

Where the fixed part of the frosted multipliers applies
-------------------------------------------------------
``t_frac`` follows the deposition: it goes to zero with
:math:`\\Delta\\omega`. The factors 0.875 and 0.954 do not. They are what a
coil loses, on average, between two defrosts, and an on-demand control
defrosts at a set frost load, so that average does not depend on how fast the
frost builds -- as long as it builds to that load. EnergyPlus therefore
applies them wherever the outdoor temperature is below the limit, also where
:math:`\\Delta\\omega` has hit its floor of 1e-6 and ``t_frac`` is 7e-5: at
-15 degC and 84 % relative humidity the capacity multiplier is 0.859 for a
defrost fraction of 0.018, and a part-load point is corrected like a full-load
one. ``DefrostModel.frost_loss`` and ``DefrostModel.load_weighted`` are three
ways of letting that fixed part follow the frost as well. None of them adds a
constant; all of them leave the full-load point at 2 degC and 84 % (EN 14511
A2) and warmer, moister conditions unchanged, and go to no correction
continuously as :math:`\\Delta\\omega \\rightarrow 0` (without the 1e-6 floor,
which only kept the EnergyPlus expression defined).

``frost_loss="constant"`` (default)
    EnergyPlus as above.
``frost_loss="moisture"``
    The fixed losses are scaled with the deposition potential relative to the
    frosting rating condition, ``s = min(1, dw / dw_A2)``:
    ``capacity = (1 - s (1 - 0.875)) (1 - t_frac)`` and
    ``power = (1 - s (1 - 0.954)) (1 - t_frac)``. A control that also defrosts
    after a longest interval carries, where frost builds slowly, the frost of
    that interval -- proportional to the deposition rate -- and not the set
    load.
``frost_loss="accumulation"``
    The same idea with the slopes EnergyPlus gives for *timed* defrost, where
    the frost a coil carries is what one interval deposits:
    ``capacity = 0.909 - 107.33 dw``, ``power = 0.9 - 36.45 dw``. The fixed
    losses are the lesser of the on-demand constant and that slope,
    ``min(1 - 0.875, 107.33 dw)`` and ``min(1 - 0.954, 36.45 dw)``: an
    on-demand control never carries more frost than its set load, nor more
    than a timed one would have accumulated. At A2 the slopes give 0.160 and
    0.054, above the constants, so the point is unchanged; the two cross at
    ``dw`` = 0.00116 and 0.00126.
``load_weighted=True`` (default)
    The coil-temperature correlation ``0.82 T_out - 8.589`` is that of a unit
    running flat out. The difference between the air and the coil surface is
    what drives the heat into the coil, so at a load ratio ``r`` it is taken
    ``r`` times as large: ``T_coil = T_out - r (0.18 T_out + 8.589)``. A
    lightly loaded coil is warmer and takes less moisture out of the air.

    *The load ratio.* The difference of the correlation itself shrinks with
    the outdoor temperature (10.1 K at 8.3 degC, 5.9 K at -15 degC): it
    carries the capacity decline of the unit it describes. ``r`` is therefore
    the frost-free duty over what that unit delivers at the same outdoor
    temperature, ``Q_rated (0.18 T_out + 8.589) / (0.18 * 7 + 8.589)`` with
    the nameplate ``Q_rated`` declared at 7 degC (EN 14511 A7, ISO 5151 H1),
    and at most 1 (:meth:`DefrostModel.reference_capacity`,
    :meth:`DefrostModel.load_ratio`). In other words the coil sits
    ``(duty / Q_rated) * 9.85 K`` below the air, never more than the
    correlation says. Against the nameplate alone, a unit delivering 0.6 of
    it at -15 degC because that is its capacity there would be read as
    part-loaded. The ratio depends on the correction it gives (the duty is
    the request over the capacity multiplier), so the two are solved together
    (:meth:`DefrostModel.frost_state_at_duty`); a unit cycling on its speed
    floor is loaded by what the running machine delivers.

    *The frost drive.* The humidity ratio the air can leave on the coil as
    frost -- what it holds, up to saturation at 0 degC, less saturation at
    the coil temperature (a coil at or above 0 degC counts as 0 degC: it
    collects water, not frost) -- is a share ``phi(r)`` of the rated-load
    drive, and the rated-load :math:`\\Delta\\omega` is multiplied by it:
    ``dw(r) = phi(r) dw(1)``, ``phi(1) = 1``. ``t_frac`` and the weighted
    forms above read that ``dw(r)``. With ``frost_loss="constant"`` the fixed
    losses are multiplied by ``phi(r)`` as well: at ``r = 1`` the result is
    EnergyPlus unchanged at every outdoor state, and it goes to no correction
    continuously as the load falls and the coil rises to the air's frost
    point.

    *What it inherits.* Where EnergyPlus applies its fixed multipliers to air
    that cannot deposit even on the rated-load coil (``dw`` on its 1e-6
    floor: at 60 % relative humidity below -18.3 degC, at 84 % below
    -39 degC), there is no drive for the load to scale, and they stay at
    every load. Just above that limit a part-loaded coil has no frost and no
    correction; just below it the full fixed loss is back. At half load and
    60 % humidity the COP multiplier falls from 0.997 at -15 degC to 0.917 at
    -18.3 degC -- more correction in drier, colder air. At 84 % the same
    return is spread from about -29 degC to -39 degC. The cliff is the
    EnergyPlus floor showing through, not a property of the load term: it
    cannot be removed while ``r = 1`` is EnergyPlus and the correction is
    continuous in ``r``.

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

The air-to-air model does this inside its ``analyze_steady``; a boiler's
``analyze_steady`` is decorated with :func:`steady_defrost`, which solves the
frost-free row through the model's own part-load and envelope handling and
hands it to the model for averaging. For a boiler the heat a reverse-cycle
defrost takes comes out of the water it heats.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeVar, cast

from ._validation import validate_steady

_F = TypeVar("_F", bound=Callable[..., Any])

__all__ = [
    "T_OUT_MAX_DEFROST_C",
    "FROST_TRANSITION_K",
    "RH_OUT_DEFAULT",
    "FROST_LOSS_FORMS",
    "T_RATING_HEATING_C",
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
    "steady_defrost",
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
#: How the fixed part of the frosted multipliers follows the deposition (see the module docstring).
FROST_LOSS_FORMS = ("constant", "moisture", "accumulation")
#: Outdoor dry bulb at which a nameplate heating capacity is declared [degC]:
#: the standard rating condition of EN 14511 (A7) and ISO 5151 (H1, 7 degC dry
#: bulb / 6 degC wet bulb). The load ratio of ``load_weighted`` refers the
#: nameplate to it (:meth:`DefrostModel.reference_capacity`).
T_RATING_HEATING_C = 7.0

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
# ... timed defrost: loss of the multipliers per unit of humidity-ratio difference.
_CAPACITY_SLOPE_TIMED = 107.33
_POWER_SLOPE_TIMED = 36.45
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
    #: Share of the fixed frosted losses (1 - 0.875, 1 - 0.954) applied [-]: 1 with
    #: ``frost_loss="constant"``, less where a weighted form finds little deposition.
    frost_loss_weight: float = 1.0
    #: Load ratio the coil temperature was estimated at [-] (1: the rated-load correlation).
    load_ratio: float = 1.0


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
    frost_loss : {"constant", "moisture", "accumulation"}
        How the fixed part of the frosted multipliers (0.875, 0.954) follows
        the deposition: not at all (EnergyPlus), with the deposition potential
        relative to the A2 rating condition, or with the timed-defrost slopes
        of the same source. See the module docstring.
    load_weighted : bool
        Estimate the coil temperature at the load ratio of the request
        instead of at the rated load, and scale the frost drive with it
        (``frost_state_at_duty``). The ratio is the frost-free duty over the
        capacity the correlation's unit has at that outdoor temperature
        (``reference_capacity``). With ``frost_loss="constant"`` only the
        load dependence is added: at a ratio of one the result is EnergyPlus
        unchanged. On by default; ``False`` is EnergyPlus at every load.
    """

    T_out_max: float = T_OUT_MAX_DEFROST_C
    RH_out: float = RH_OUT_DEFAULT
    transition_K: float = FROST_TRANSITION_K
    frost_loss: str = "constant"
    load_weighted: bool = True

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
        if self.frost_loss not in FROST_LOSS_FORMS:
            raise ValueError(f"frost_loss must be one of {FROST_LOSS_FORMS}, got {self.frost_loss!r}")
        if not isinstance(self.load_weighted, bool):
            raise ValueError(f"load_weighted must be True or False, got {self.load_weighted!r}")
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
        load_ratio: float | None = None,
    ) -> FrostState:
        """Frost correction at outdoor dry bulb ``T_out_C`` and relative humidity ``RH_out`` (0-1).

        ``T_coil_C`` replaces the coil-temperature correlation
        ``0.82 T_out - 8.589``. No frost above ``T_out_max``; below it the
        correction grows to its full size across ``transition_K``
        (:meth:`frost_share`). No frost on a coil at or above 0 degC either.
        ``load_ratio`` (0-1, with ``load_weighted``) scales the difference
        between the air and the coil of that correlation, and with it the
        frost drive of the rated-load state (:meth:`_frost_drive_ratio`);
        ``None``, and a ratio of one or more, are the rated load.
        """
        rh = self.RH_out if RH_out is None else _check_humidity(RH_out)
        if not math.isfinite(T_out_C):
            raise ValueError(f"T_out_C must be finite, got {T_out_C!r}")
        ratio = 1.0
        if self.load_weighted and load_ratio is not None:
            if not math.isfinite(load_ratio) or load_ratio < 0.0:
                raise ValueError(f"load_ratio must be a finite number >= 0, got {load_ratio!r}")
            if T_coil_C is None:  # a given coil temperature is not rescaled
                ratio = min(1.0, float(load_ratio))
        # Coil at the rated load, and at this load: the difference to the air times the ratio.
        rated_coil = _COIL_SLOPE * T_out_C + _COIL_OFFSET_K if T_coil_C is None else float(T_coil_C)
        coil = rated_coil if ratio >= 1.0 else T_out_C - ratio * (T_out_C - rated_coil)
        share = self.frost_share(T_out_C)
        if not share > 0.0 or not rated_coil < 0.0:
            return FrostState(False, 0.0, 1.0, 1.0, T_coil_C=coil, RH_out=rh, frost_share=0.0, load_ratio=ratio)
        w_out = humidity_ratio(T_out_C, rh, P_Pa)
        excess = w_out - saturation_humidity_ratio(rated_coil, P_Pa)  # at the rated load
        floored = self.frost_loss == "constant"
        drive = 1.0 if ratio >= 1.0 else self._frost_drive_ratio(w_out, rated_coil, coil, P_Pa, floored=floored)
        if floored:
            # EnergyPlus: the floor keeps the expression defined, and the fixed
            # multipliers apply whatever the deposition. At the rated load
            # (drive 1) this is that model unchanged; below it both the
            # deposition and the fixed part follow the drive.
            dw = max(_DW_MIN, excess) * drive
            weight_capacity = weight_power = drive
        else:
            # No floor: air that cannot deposit on the coil leaves no frost and no correction.
            if not (excess > 0.0 and drive > 0.0):
                return FrostState(
                    False, 0.0, 1.0, 1.0, T_coil_C=coil, humidity_ratio_difference=0.0, RH_out=rh, frost_share=0.0,
                    frost_loss_weight=0.0, load_ratio=ratio,
                )  # fmt: skip
            dw = excess * drive
            if self.frost_loss == "moisture":
                weight_capacity = weight_power = min(1.0, dw / _reference_humidity_ratio_difference(P_Pa))
            else:  # "accumulation"
                weight_capacity = min(1.0, _CAPACITY_SLOPE_TIMED * dw / (1.0 - _CAPACITY_FROSTED))
                weight_power = min(1.0, _POWER_SLOPE_TIMED * dw / (1.0 - _POWER_FROSTED))
        t_frac = 1.0 / (1.0 + _DW_SCALE / dw)
        capacity = (1.0 - weight_capacity * (1.0 - _CAPACITY_FROSTED)) * (1.0 - t_frac)
        power = (1.0 - weight_power * (1.0 - _POWER_FROSTED)) * (1.0 - t_frac)
        # Frosting for the share of the runtime, frost-free for the rest. With
        # a share of one and the constant form these are the EnergyPlus
        # expressions unchanged.
        return FrostState(
            active=True,
            defrost_fraction=share * t_frac,
            capacity_multiplier=1.0 - share * (1.0 - capacity),
            power_multiplier=1.0 - share * (1.0 - power),
            T_coil_C=coil,
            humidity_ratio_difference=dw,
            RH_out=rh,
            frost_share=share,
            frost_loss_weight=weight_capacity,
            load_ratio=ratio,
        )

    @staticmethod
    def _frost_drive_ratio(w_out: float, rated_coil_C: float, coil_C: float, P_Pa: float, *, floored: bool) -> float:
        """Frost drive of a coil at ``coil_C`` over that of the rated-load coil (0-1).

        The drive is the humidity ratio the air can leave on the coil as
        frost: what it holds, up to saturation at 0 degC, less saturation at
        the coil temperature. A coil at or above 0 degC collects water, not
        frost, so its temperature counts as 0 degC and its drive is nothing --
        the ratio falls to that continuously as the load falls, also in air
        whose dew point is above freezing. ``floored`` keeps both drives at
        the EnergyPlus floor of 1e-6 (the constant form): where the
        rated-load coil has no drive either, the ratio is then one and the
        fixed multipliers of that form stay at every load -- they are not tied
        to a deposition there, so there is none for the load to scale.
        """
        frostable = min(w_out, saturation_humidity_ratio(0.0, P_Pa))
        at_rated = frostable - saturation_humidity_ratio(min(rated_coil_C, 0.0), P_Pa)
        at_load = frostable - saturation_humidity_ratio(min(coil_C, 0.0), P_Pa)
        if floored:
            return min(1.0, max(_DW_MIN, at_load) / max(_DW_MIN, at_rated))
        if not at_rated > 0.0:
            return 0.0
        return min(1.0, max(0.0, at_load) / at_rated)

    def frost_state_at_duty(
        self,
        T_out_C: float,
        RH_out: float | None,
        Q_request: float,
        Q_rated: float,
        *,
        P_Pa: float = P_ATM,
    ) -> FrostState:
        """Frost correction for a request of ``Q_request`` [W] on a unit rated ``Q_rated`` [W].

        Without ``load_weighted`` this is :meth:`frost_state`. With it the
        coil temperature is estimated at the load ratio of the frost-free duty
        the request needs (:meth:`load_ratio`: that duty over what the
        correlation's unit delivers at this outdoor temperature, at most 1).
        The duty depends on the correction, so the ratio ``r`` is the root of
        ``r = load_ratio(gross_duty(frost_state(r)))``, which lies between the
        request's own ratio and 1: more load means a colder coil, more frost
        and a larger duty still.
        """
        if not self.load_weighted:
            return self.frost_state(T_out_C, RH_out, P_Pa=P_Pa)
        request, rated = abs(float(Q_request)), abs(float(Q_rated))
        if not (math.isfinite(request) and math.isfinite(rated) and rated > 0.0):
            raise ValueError(f"Require finite Q_request and Q_rated > 0, got {Q_request!r}, {Q_rated!r}")
        low = self.load_ratio(request, rated, T_out_C)

        def state(ratio: float) -> FrostState:
            return self.frost_state(T_out_C, RH_out, P_Pa=P_Pa, load_ratio=ratio)

        def residual(ratio: float) -> float:
            frost = state(ratio)
            gross = self.gross_duty(frost, request, self.defrost_load(frost, T_out_C, rated))
            return ratio - self.load_ratio(gross, rated, T_out_C)

        if low >= 1.0 or residual(1.0) <= 0.0:
            return state(1.0)
        if residual(low) >= 0.0:
            return state(low)
        from scipy.optimize import brentq

        return state(float(brentq(residual, low, 1.0, xtol=1e-12, rtol=1e-12)))

    @staticmethod
    def reference_capacity(Q_rated: float, T_out_C: float) -> float:
        """Capacity at which the coil-temperature correlation holds at ``T_out_C`` [W].

        The correlation ``T_coil = 0.82 T_out - 8.589`` puts the coil
        ``0.18 T_out + 8.589`` below the air: 10.1 K at 8.3 degC, 7.1 K at
        -8.3 degC, 5.9 K at -15 degC. That difference is what drives the heat
        into the coil, so its fall with the outdoor temperature is the fall
        of the capacity of the unit the correlation describes -- one that runs
        flat out and delivers less as it gets colder. Relative to the
        nameplate, declared at :data:`T_RATING_HEATING_C`, that capacity is
        ``Q_rated (0.18 T_out + 8.589) / (0.18 * 7 + 8.589)``. A load ratio
        taken against the nameplate itself would read a unit that delivers
        0.6 of its nameplate at -15 degC because that is all it has as a
        part-loaded one, and count the decline twice.
        """
        difference = (1.0 - _COIL_SLOPE) * T_out_C - _COIL_OFFSET_K
        rating = (1.0 - _COIL_SLOPE) * T_RATING_HEATING_C - _COIL_OFFSET_K
        return abs(float(Q_rated)) * max(0.0, difference) / rating

    @classmethod
    def load_ratio(cls, duty: float, Q_rated: float, T_out_C: float) -> float:
        """Load ratio of a frost-free ``duty`` [W] at ``T_out_C``: over :meth:`reference_capacity`, at most 1.

        A unit that delivers the reference capacity or more has the coil of
        the correlation (ratio 1: the rated-load state, EnergyPlus unchanged
        without a weighted ``frost_loss``).
        """
        reference = cls.reference_capacity(Q_rated, T_out_C)
        if not reference > 0.0:
            return 1.0
        return min(1.0, abs(float(duty)) / reference)

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


_REFERENCE_DW: dict[float, float] = {}


def _reference_humidity_ratio_difference(P_Pa: float = P_ATM) -> float:
    """Humidity-ratio difference between the air and the coil at the frosting rating condition [kg/kg].

    EN 14511 A2 at the rated load: 2 degC dry bulb (``T_OUT_MAX_DEFROST_C -
    FROST_TRANSITION_K``, where the correction is in full), 84 % relative
    humidity, coil at ``0.82 T_out - 8.589``.
    """
    if P_Pa not in _REFERENCE_DW:
        T_ref = T_OUT_MAX_DEFROST_C - FROST_TRANSITION_K
        coil = _COIL_SLOPE * T_ref + _COIL_OFFSET_K
        _REFERENCE_DW[P_Pa] = humidity_ratio(T_ref, RH_OUT_DEFAULT, P_Pa) - saturation_humidity_ratio(coil, P_Pa)
    return _REFERENCE_DW[P_Pa]


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


def steady_defrost(duty_arg: str, outdoor_arg: str = "T0", humidity_arg: str = "RH_out") -> Callable[[_F], _F]:
    """Decorate a boiler's ``analyze_steady`` with the frost correction.

    The decorated method is the frost-free solve (with its part-load report
    and its duty reduction). A request with a frosting outdoor coil is handed
    to the model's ``_steady_over_frost(frost, solve, request, T_out)``, where
    ``solve(duty)`` is that frost-free solve at another duty; every other row
    is solved as before and gets the frost keys of a row that was not
    corrected (``_write_frost_free``). The model supplies
    ``_frost_state(request, T_out, RH_out)``, which is
    :data:`NO_FROST` when its frost model is off, and
    ``_frost_state_of_running(result, frost, T_out)``, the frost state of the
    machine a frost-free row actually runs (the same state unless a
    load-weighted model finds the running machine more loaded than the
    request). It sits outermost: the
    trial solves of the duty reduction and the cycling mean must see
    frost-free rows.
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
            # The method's own input checks come first, with their messages
            # (an outdoor temperature out of range would otherwise fail inside
            # the humid-air properties).
            validate_steady(bound.arguments)
            request, T_out = float(bound.arguments[duty_arg]), float(bound.arguments[outdoor_arg])
            frost = self._frost_state(request, T_out, bound.arguments.get(humidity_arg))

            def solve(duty: float) -> Any:
                arguments = dict(bound.arguments)
                arguments[duty_arg] = duty
                trial = inspect.BoundArguments(signature, arguments)  # type: ignore[arg-type]
                return function(*trial.args, **trial.kwargs)

            result: Any = None
            if not frost.active:
                result = function(*bound.args, **bound.kwargs)
                if isinstance(result, dict):
                    # A request the model's frost state finds too small to
                    # frost may still be met by a machine that does: on its
                    # speed floor the unit runs at more than the request
                    # (a load-weighted frost model; otherwise unchanged).
                    frost = self._frost_state_of_running(result, frost, T_out)
            if frost.active:
                result = self._steady_over_frost(frost, solve, request, T_out)
            elif isinstance(result, dict):
                self._write_frost_free(result, frost)
            if want_dict or not isinstance(result, dict):
                return result
            import pandas as pd

            return pd.DataFrame([result])

        # ``analyze_steady.__wrapped__`` stays the undecorated solve, as under
        # the cycling and envelope decorators.
        wrapped.__wrapped__ = getattr(function, "__wrapped__", function)  # type: ignore[attr-defined]
        return cast(_F, wrapped)

    return decorate
