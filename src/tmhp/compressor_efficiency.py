"""BITZER scroll three-efficiency defaults from the 2026-10-06 Notion study.

Each efficiency independently uses b0+b1*PR+b2*N+b3*PR**2+b4*N**2+b5*PR*N.
N is physical shaft speed in rev/s; no relative-speed mapping.
Manufacturer calculation data at SH/SC 10/0 K underpin the fit. eta_em is the
effective refrigerant enthalpy rise / electrical input factor.
Previous correlations remain in compressor_efficiency_legacy.

Guards (apply to every efficiency the models evaluate)
------------------------------------------------------
* Range: every evaluated efficiency satisfies 0 < eta <= 1 (:func:`_eval_eff`).
  Values above one are set to one; nonpositive or nonfinite values make the
  operating point infeasible.
* Validity domain: the default polynomials carry the (PR, N) box of their
  fitting data (:data:`VALIDITY_DOMAIN`). Outside it they are not followed
  freely -- see :class:`ValidityDomain` for the rule in each direction.
  :func:`efficiency_extrapolated` tells a caller whether a point lies outside.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Callable
from dataclasses import dataclass
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

# Fitting-data box of the three scroll surfaces. Source: the "hull" vertices of
# validation/coefficients/BITZER-three-C3-absolute-N-2026-10-06/
# three_efficiency_surfaces.json ["scroll"][eta_v | eta_is | eta_em] (identical
# for all three; 3899 points, 9 machines). The hull is a hexagon inside this box:
# it cuts the corners (6.447, 33.83)-(7.149, 38.67) and (7.149, 62.83)-(6.447, 72.5).
PR_FIT_MIN = 1.501573873
PR_FIT_MAX = 7.148577233
RPS_FIT_MIN = 33.833333333
RPS_FIT_MAX = 72.5
# Lowest pressure ratio / speed the fitted trend is followed to (see ValidityDomain).
PR_FOLLOW_MIN = 1.0
RPS_FOLLOW_MIN = 15.0


@dataclass(frozen=True)
class ValidityDomain:
    """(PR, N) range of a fitted efficiency surface and how it is left.

    Inside ``[pr_min, pr_max] x [rps_min, rps_max]`` the fit is evaluated as is.
    Outside, direction by direction:

    * ``PR > pr_max`` and ``N > rps_max`` -- held at the boundary value. The
      quadratic terms (b3 < 0 for eta_is, b4 < 0 for eta_is and eta_v) bend the
      scroll surfaces to eta_is < 0 by PR 9 and at N 150. No compressor
      mechanism collapses that way: under-compression past the built-in volume
      ratio and port/flow losses at high speed grow smoothly. Holding the last
      data-supported value is the EnergyPlus performance-curve convention; the
      fit's own slope at these edges is the pathological curvature, so it is not
      continued. Operation far beyond them is for a discharge-temperature and
      pressure-ratio envelope to refuse, not for the efficiency map.
    * ``PR < pr_min`` (down to ``pr_follow_min`` = 1) and ``N < rps_min`` (down
      to ``rps_follow_min`` = 15 rev/s, held below) -- continued along the
      fit's slope at the boundary (first order: the data-free curvature is
      dropped), and only where that slope lowers the efficiency. Holding would
      be optimistic here. Below the built-in volume ratio a fixed-Vi scroll
      over-compresses and that loss grows as PR -> 1 while the isentropic work
      vanishes, so eta_is falls (0.61 -> 0.58 at N 50 from PR 1.5 to 1.2; the
      2026-09-24 correlations: -10 %). Leakage is set by the pressure
      difference, not by speed, so its share of the swept flow grows as 1/N,
      and drive fixed losses weigh more at low power; from 33.8 to 15 rev/s
      the continuation gives eta_is*eta_em -13/-19/-46 % and eta_v -6/-7/-8 %
      at PR 3/5/7, against -7..-11/-14..-23/-22..-36 % and -4..-6 % from the
      independently fitted leakage and drive terms of the 2026-09-24
      correlations (rated 40-60 rev/s); following the full quadratic instead
      gives -20/-27/-60 %. A slope that would raise the efficiency (eta_v and
      eta_em towards low N, eta_v towards low PR) is not followed: no bonus
      outside the data. Scroll compressors are not run below ~15 rev/s (oil
      supply, flank sealing), so nothing is extrapolated there.
    """

    pr_min: float
    pr_max: float
    rps_min: float
    rps_max: float
    pr_follow_min: float
    rps_follow_min: float

    def contains(self, pressure_ratio: float, rps: float) -> bool:
        """Whether (PR, N) lies inside the fitting-data box."""
        return self.pr_min <= pressure_ratio <= self.pr_max and self.rps_min <= rps <= self.rps_max


def _clamp(value: float, low: float, high: float) -> float:
    # Comparisons (not min/max) so a NaN input stays NaN and is rejected later.
    if value < low:
        return low
    if value > high:
        return high
    return value


VALIDITY_DOMAIN = ValidityDomain(
    pr_min=PR_FIT_MIN,
    pr_max=PR_FIT_MAX,
    rps_min=RPS_FIT_MIN,
    rps_max=RPS_FIT_MAX,
    pr_follow_min=PR_FOLLOW_MIN,
    rps_follow_min=RPS_FOLLOW_MIN,
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


def _cap_at_one(value: float) -> float:
    # A value above one is set to one (see _eval_eff); NaN passes through.
    return 1.0 if value > 1.0 else value


class FittedEfficiency:
    """A fitted efficiency surface guarded by its validity domain.

    ``model(PR, N)`` evaluates the fit inside ``validity_domain``, leaves it as
    :class:`ValidityDomain` describes, and caps the result at one;
    ``model.raw(PR, N)`` is the unguarded polynomial. ``N`` omitted means the
    rated speed.
    """

    def __init__(
        self,
        coef: tuple[float, float, float, float, float, float],
        rps_rated: float,
        validity_domain: ValidityDomain = VALIDITY_DOMAIN,
    ) -> None:
        self.coef = coef
        self.rps_rated = rps_rated
        self.validity_domain = validity_domain

    def __call__(self, pressure_ratio: float, rps: float | None = None) -> float:
        speed = self.rps_rated if rps is None else rps
        d = self.validity_domain
        pr_b = _clamp(pressure_ratio, d.pr_min, d.pr_max)
        rps_b = _clamp(speed, d.rps_min, d.rps_max)
        value = _polynomial(self.coef, pr_b, rps_b)
        b0, b1, b2, b3, b4, b5 = self.coef
        if pressure_ratio < d.pr_min:
            slope = b1 + 2.0 * b3 * pr_b + b5 * rps_b
            value += min(0.0, slope * (max(pressure_ratio, d.pr_follow_min) - d.pr_min))
        if speed < d.rps_min:
            slope = b2 + 2.0 * b4 * rps_b + b5 * pr_b
            value += min(0.0, slope * (max(speed, d.rps_follow_min) - d.rps_min))
        return _cap_at_one(value)

    def raw(self, pressure_ratio: float, rps: float | None = None) -> float:
        """The polynomial at (PR, N) with no domain or range guard."""
        return _polynomial(self.coef, pressure_ratio, self.rps_rated if rps is None else rps)

    def extrapolated(self, pressure_ratio: float, rps: float | None = None) -> bool:
        """Whether (PR, N) lies outside the fitting-data box."""
        return not self.validity_domain.contains(pressure_ratio, self.rps_rated if rps is None else rps)


def make_eta_vol(rps_rated: float) -> FittedEfficiency:
    """Volumetric fit; rated speed is used only when N is omitted."""
    return FittedEfficiency(COEFFICIENTS_ETA_V, rps_rated)


def make_eta_isen(rps_rated: float) -> FittedEfficiency:
    """Independent isentropic fit using discharge temperature."""
    return FittedEfficiency(COEFFICIENTS_ETA_IS, rps_rated)


def make_eta_em(rps_rated: float) -> FittedEfficiency:
    """Independent effective electrical/mechanical fit."""
    return FittedEfficiency(COEFFICIENTS_ETA_EM, rps_rated)


eta_vol_default = make_eta_vol(RPS_REF)
eta_isen_default = make_eta_isen(RPS_REF)
eta_em_default = make_eta_em(RPS_REF)


def efficiency_extrapolated(model: float | Callable, pressure_ratio: float, rps: float) -> bool:
    """Whether ``model`` is evaluated outside its fitting data at (PR, N).

    True only for models that carry a ``validity_domain`` (the defaults here)
    and a point outside it; scalars and user callables without one give False.
    For a result row use ``PR = P_ref_cmp_out / P_ref_cmp_in`` and
    ``N = cmp_rpm / 60``.
    """
    domain = getattr(model, "validity_domain", None)
    if not isinstance(domain, ValidityDomain):
        return False
    return not domain.contains(pressure_ratio, rps)


class InvalidCompressorEfficiency(ValueError):
    """The evaluated efficiency is nonpositive or nonfinite."""


_F = TypeVar("_F", bound=Callable)


def reject_invalid_efficiency(function: _F) -> _F:
    """Return an infeasible state for nonpositive or nonfinite efficiencies."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except InvalidCompressorEfficiency:
            return None

    return cast(_F, wrapped)


def _eval_eff(model: float | Callable, PR: float, rps: float) -> float:
    """Evaluate a scalar or PR / PR-speed callable and enforce 0 < eta <= 1.

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
    if not math.isfinite(value) or value <= 0:
        # No compressor delivers flow or a discharge state with eta <= 0: the
        # point is infeasible, not a lossless or reversed machine.
        raise InvalidCompressorEfficiency("Compressor efficiencies must be finite and positive")
    # Above one: set to one rather than reject. eta_is > 1 puts the discharge
    # below the isentropic enthalpy (second law), eta_em > 1 gives the gas more
    # enthalpy than the electrical input (first law), and eta_v > 1 delivers
    # more than the swept volume, which an adiabatic positive-displacement
    # machine without ram charging cannot. One is the bound each quantity
    # approaches -- the scroll fit reaches eta_v 1.02 at PR 1.5-2.4, mid speed,
    # inside its data, where clearance re-expansion vanishes -- so the overshoot
    # is fit noise at a physical limit, and the nearest admissible value is the
    # limit itself. Rejecting would punch infeasible holes into ordinary
    # low-lift operation and break the monotone speed bracket.
    return 1.0 if value > 1.0 else value
