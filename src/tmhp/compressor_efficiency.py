"""BITZER scroll three-efficiency defaults from the 2026-10-06 Notion study.

Each efficiency independently uses b0+b1*PR+b2*N+b3*PR**2+b4*N**2+b5*PR*N.
N is physical shaft speed in rev/s; no relative-speed mapping or clipping.
Manufacturer calculation data at SH/SC 10/0 K underpin the fit. Extrapolation
is allowed; heat-pump state evaluation requires 0 < eta <= 1. eta_em is the
effective refrigerant enthalpy rise / electrical input factor.
Previous correlations remain in compressor_efficiency_legacy.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Callable
from functools import wraps
from typing import TypeVar, cast

COEFFICIENT_VERSION = "BITZER-three-C3-absolute-N-2026-10-06"
SOURCE_URL = "https://app.notion.com/p/3ee6947d125d80f4830ffcb408fa647d"
RPS_REF = 50.0
COEFFICIENTS_ETA_V = (
    0.8984145282319755,
    -0.04603589234676063,
    0.006859130215452917,
    0.0017836363753654246,
    -6.902995858246164e-05,
    0.0002199529050337149,
)
COEFFICIENTS_ETA_IS = (
    0.13253565847275364,
    0.16813651653278897,
    0.011123675381951727,
    -0.028573870225468822,
    -0.0001300096925413996,
    0.0008058213178039828,
)
COEFFICIENTS_ETA_EM = (
    0.9962645635944746,
    0.0007039898269919783,
    -0.0001389095096216701,
    -1.3317636920523126e-05,
    1.3559873315458154e-06,
    4.449463172570228e-07,
)


def coefficients() -> dict:
    """Frozen scroll fit and its physical input convention."""
    return {
        "version": COEFFICIENT_VERSION,
        "source_url": SOURCE_URL,
        "speed_variable": "N [rev/s]",
        "eta_v": list(COEFFICIENTS_ETA_V),
        "eta_is": list(COEFFICIENTS_ETA_IS),
        "eta_em": list(COEFFICIENTS_ETA_EM),
    }


def _polynomial(coef: tuple[float, float, float, float, float, float], pressure_ratio: float, rps: float) -> float:
    b0, b1, b2, b3, b4, b5 = coef
    return b0 + b1 * pressure_ratio + b2 * rps + b3 * pressure_ratio**2 + b4 * rps**2 + b5 * pressure_ratio * rps


def _make_efficiency(coef: tuple[float, float, float, float, float, float], rps_rated: float) -> Callable:
    def efficiency(pressure_ratio: float, rps: float | None = None) -> float:
        return _polynomial(coef, pressure_ratio, rps_rated if rps is None else rps)

    return efficiency


def make_eta_vol(rps_rated: float) -> Callable:
    """Raw volumetric fit; rated speed is used only when N is omitted."""
    return _make_efficiency(COEFFICIENTS_ETA_V, rps_rated)


def make_eta_isen(rps_rated: float) -> Callable:
    """Independent raw isentropic fit using discharge temperature."""
    return _make_efficiency(COEFFICIENTS_ETA_IS, rps_rated)


def make_eta_em(rps_rated: float) -> Callable:
    """Independent raw effective electrical/mechanical fit."""
    return _make_efficiency(COEFFICIENTS_ETA_EM, rps_rated)


eta_vol_default = make_eta_vol(RPS_REF)
eta_isen_default = make_eta_isen(RPS_REF)
eta_em_default = make_eta_em(RPS_REF)


class InvalidCompressorEfficiency(ValueError):
    """The raw efficiency cannot describe a physical heat-pump state."""


_F = TypeVar("_F", bound=Callable)


def reject_invalid_efficiency(function: _F) -> _F:
    """Return an infeasible state for raw-fit violations, as in the study."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except InvalidCompressorEfficiency:
            return None

    return cast(_F, wrapped)


def _eval_eff(model: float | Callable, PR: float, rps: float) -> float:
    """Evaluate a scalar or PR / PR-speed callable without masking its errors.

    Constructors resolve None to the corresponding baseline factory. Inspect
    argument binding rather than catching a TypeError raised inside a model.
    """
    if callable(model):
        signature = inspect.signature(model)
        try:
            signature.bind(PR, rps)
        except TypeError:
            signature.bind(PR)
            value = float(model(PR))
        else:
            value = float(model(PR, rps))
    else:
        value = float(model)
    if not math.isfinite(value) or not 0 < value <= 1:
        raise InvalidCompressorEfficiency("Compressor efficiencies must be finite and in (0, 1]")
    return value
