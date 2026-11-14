"""Result keys every boiler row carries, whatever the row is (ASHPB, GSHPB, WSHPB).

A boiler result used to gain keys only on the path that wrote them: the
capacity keys (``capacity_binding``, ``Q_max [W]``, ``Q_request [W]``) only on
a row whose duty was reduced, the efficiencies and heat-exchanger closure
terms only on a running row. A dynamic frame is the union of its rows, so its
columns then depended on what the run happened to do. The helpers here give
every row of a family the same keys: what does not apply to a row is ``None``
(a name) or NaN (a number), what is zero is ``0.0`` -- the conventions of
``docs/source/concepts/result-keys.rst``.

They only add keys a row does not have and fill in the zeotropic dew point;
no cycle, control or tank quantity changes.

A stream that does not flow follows the same rule: its flow, enthalpy flow and
exergy flow are ``0.0`` and its temperature is NaN (there is no water to have
one). :func:`mixed_water_enthalpy_flow` and :func:`mixed_water_exergy` write
the mixed hot water of a step without a draw that way.

The two family-independent helpers, :func:`fill_missing` and
:func:`report_condenser_dew_point`, also finish the rows of the
space-conditioning models (ASHP, GSHP: their ``_complete_row``).
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Mapping
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import CoolProp.CoolProp as CP

from . import calc_util as cu
from .constants import c_w, rho_w
from .refrigerant import props_si
from .thermodynamics import calc_energy_flow, calc_exergy_flow

if TYPE_CHECKING:
    import pandas as pd

#: Capacity keys on every boiler row, steady and dynamic.
CAPACITY_KEYS: tuple[str, ...] = ("capacity_clamped", "capacity_binding", "Q_max [W]", "Q_request [W]")

#: Time keys of every boiler dynamic row: the start of the step, as ASHP and GSHP.
TIME_KEYS: tuple[str, ...] = ("time [s]", "time [h]")

_HEAT_KEY = "Q_ref_tank [W]"


def _running(result: Mapping[str, Any]) -> bool:
    return bool(result.get("hp_is_on", False)) and result.get("failure_reason", "none") == "none"


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def complete_capacity_keys(result: dict, request: float, *, step_average: bool = False) -> dict:
    """Give a boiler row all of :data:`CAPACITY_KEYS` (in place).

    ``request`` is the duty asked of the compressor [W]: the ``Q_ref_tank``
    of ``analyze_steady``; for a dynamic step ``hp_capacity`` when the
    thermostat called the compressor during the step and 0 when it did not.
    A row the duty reduction already wrote keeps its values. A row at the
    compressor's speed ceiling (``capacity_clamped == "max"`` with no
    reduction) gains ``capacity_binding = "compressor"`` and ``Q_max [W]`` =
    the heat the running machine delivers; with ``step_average`` the row's
    ``Q_ref_tank [W]`` is a step average and is divided by
    ``runtime_fraction [-]`` for it, so ``Q_max [W]`` is a running value as
    on a reduced step. Otherwise ``capacity_clamped`` and
    ``capacity_binding`` are ``None`` and ``Q_max [W]`` is NaN.
    """
    result.setdefault("capacity_clamped", None)
    if _running(result) and result.get("capacity_clamped") == "max":
        result.setdefault("capacity_binding", "compressor")
        if "Q_max [W]" not in result:
            delivered = _number(result.get(_HEAT_KEY))
            if step_average:
                fraction = _number(result.get("runtime_fraction [-]", 1.0))
                delivered = delivered / fraction if fraction > 0.0 else math.nan
            result["Q_max [W]"] = delivered
    result.setdefault("capacity_binding", None)
    result.setdefault("Q_max [W]", math.nan)
    result.setdefault("Q_request [W]", float(request))
    return result


def step_request(result: Mapping[str, Any], hp_capacity: float) -> float:
    """Duty a dynamic step asked of the compressor [W]: ``hp_capacity`` if it was called, else 0.

    The thermostat calls the compressor at its nameplate duty; a step whose
    compressor ran, or was to run and could not (``failure_reason`` not
    ``"none"``), was called.
    """
    called = bool(result.get("hp_is_on", False)) or result.get("failure_reason", "none") not in (None, "none")
    return float(hp_capacity) if called else 0.0


def fill_missing(result: dict, defaults: Mapping[str, Any]) -> dict:
    """Add each key of ``defaults`` the row does not have, with its default (in place)."""
    for key, value in defaults.items():
        result.setdefault(key, value)
    return result


@lru_cache(maxsize=64)
def is_zeotropic(ref: str) -> bool:
    """Whether ``ref`` condenses with a temperature glide (dew point above bubble point).

    Pure fluids have none; CoolProp's blends do, the near-azeotropic R410A
    too (0.1 K). The glide is read at the saturation pressure of a 40 °C
    bubble point.
    """
    try:
        P = CP.PropsSI("P", "T", cu.C2K(40.0), "Q", 0, ref)
        glide = CP.PropsSI("T", "P", P, "Q", 1, ref) - CP.PropsSI("T", "P", P, "Q", 0, ref)
    except ValueError:
        return False
    return math.isfinite(glide) and glide > 1e-6


def report_condenser_dew_point(result: dict, ref: str) -> dict:
    """``T_ref_cond_sat_v [°C]`` as the dew point of the condensing pressure (in place).

    The cycle states (:func:`tmhp.refrigerant.calc_ref_state`) set the
    condenser's saturated-vapour temperature equal to its saturated-liquid
    one. For a pure refrigerant they are the same; for a zeotropic blend the
    vapour saturates at the dew point, higher than the bubble point
    ``T_ref_cond_sat_l [°C]`` by the glide (R407C at 21.2 bar: 52.5 against
    48.0 °C; R410A: 0.1 K). Only the reported key changes: the cycle, the enthalpy and
    entropy at ``*_cond_sat_v`` (already dew-point values) and every check stay
    as they are. Rows without a condensing pressure are left alone.
    """
    if not is_zeotropic(ref):
        return result
    P = _number(result.get("P_ref_cond_sat_v [Pa]", result.get("P_ref_cmp_out [Pa]")))
    if not (math.isfinite(P) and P > 0.0) or not math.isfinite(_number(result.get("T_ref_cond_sat_v [°C]"))):
        return result
    with contextlib.suppress(ValueError):
        result["T_ref_cond_sat_v [°C]"] = cu.K2C(props_si("T", "P", P, "Q", 1, ref))
    return result


def mixed_water_enthalpy_flow(dV_mix_w_out: float, T_mix_K: float, T0_K: float) -> float:
    """``Q_mix_w_out [W]``: enthalpy flow of the mixed water delivered, from ``T0``; 0 without a draw.

    The temperature of water that is not drawn is NaN, and ``0 * NaN`` is NaN:
    a step without a draw delivers no heat, so it reports 0.0.
    """
    if not dV_mix_w_out > 0.0:
        return 0.0
    return float(calc_energy_flow(G=c_w * rho_w * dV_mix_w_out, T=T_mix_K, T0=T0_K))


def mixed_water_exergy(df: pd.DataFrame, T0_K: Any) -> pd.Series:
    """``X_mix_w_out [W]`` of a dynamic frame: flow exergy of the mixed water delivered; 0 without a draw.

    Reads ``dV_mix_w_out [m3/s]`` and ``T_mix_w_out [°C]``. Steps with a draw
    are evaluated as before; on a step without one the temperature (NaN) is
    not used.
    """
    flow = df["dV_mix_w_out [m3/s]"].fillna(0)
    T_K = cu.C2K(df["T_mix_w_out [°C]"]).where(flow > 0, T0_K)
    return calc_exergy_flow(c_w * rho_w * flow, T_K, T0_K)
