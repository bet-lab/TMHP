"""Record of the ``BITZER-three-C3-absolute-N-2026-10-06`` compressor efficiency map (an earlier release).

Three quadratic surfaces in the pressure ratio and the shaft speed, ::

    eta = b0 + b1 PR + b2 N + b3 PR^2 + b4 N^2 + b5 PR N          (N in rev/s)

fitted to BITZER scroll data (manufacturer calculation data at SH/SC 10/0 K;
``eta_em`` is the refrigerant enthalpy rise over the electrical input). They
were the library's default models from 2026-10-06 until the semi-empirical
models were adopted, and are **not** a supported default. They are kept here,
number for number and with the guard the library applied to them, so that the
validation studies that compare against them and the tests whose scenarios
were recorded on machines built with them can be reproduced (#164). The fit
archive is ``validation/coefficients/BITZER-three-C3-absolute-N-2026-10-06/``;
``tests/test_recorded_compressor_maps.py`` checks the coefficients below
against its ``coefficients.csv`` and the box against the hull of its
``three_efficiency_surfaces.json``.

:func:`make_eta_vol`, :func:`make_eta_isen` and :func:`make_eta_em` return
plain user-style callables ``model(PR, rps)`` for a heat-pump model's
``eta_cmp_vol`` / ``eta_cmp_isen`` / ``eta_cmp``.

Guard (:class:`FittedEfficiency`), exactly as released
------------------------------------------------------
* Inside the (PR, N) box of the fitting data (:data:`FIT_BOX`) the quadratic
  is evaluated as is; outside it is left as :class:`FitBox` describes: held at
  the boundary value above the box, continued to first order below it where
  that lowers the efficiency.
* The result is capped at one.

The library additionally refused a running state whose isentropic efficiency,
read outside the box, was below 0.30 (``failure_reason='eta_isen_below_min'``).
That rule left the library with this map and is not reproduced: a scenario
that reached it is not reproducible from this record alone.

``efficiency_extrapolated``
    The models carry no ``validity_domain``, so a heat-pump model built with
    them reports ``efficiency_extrapolated = False`` everywhere, like any
    user-supplied callable. ``model.extrapolated(PR, rps)`` answers the
    question for the box.
"""

from __future__ import annotations

from dataclasses import dataclass

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
# Lowest pressure ratio / speed the fitted trend is followed to (see FitBox).
PR_FOLLOW_MIN = 1.0
RPS_FOLLOW_MIN = 15.0


@dataclass(frozen=True)
class FitBox:
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

    def contains(self, pressure_ratio: float, rps: float, rtol: float = 0.0) -> bool:
        """Whether (PR, N) lies inside the fitting-data box (``rtol``: edges widened by that relative margin)."""
        return self.pr_min * (1 - rtol) <= pressure_ratio <= self.pr_max * (1 + rtol) and self.rps_min * (
            1 - rtol
        ) <= rps <= self.rps_max * (1 + rtol)


def _clamp(value: float, low: float, high: float) -> float:
    # Comparisons (not min/max) so a NaN input stays NaN and is rejected later.
    if value < low:
        return low
    if value > high:
        return high
    return value


FIT_BOX = FitBox(
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
    # A value above one is set to one (as tmhp.compressor_efficiency._eval_eff does); NaN passes through.
    return 1.0 if value > 1.0 else value


class FittedEfficiency:
    """A fitted efficiency surface guarded by the box of its fitting data.

    ``model(PR, N)`` evaluates the fit inside ``fit_box``, leaves it as
    :class:`FitBox` describes, and caps the result at one;
    ``model.raw(PR, N)`` is the unguarded polynomial. ``N`` omitted means the
    rated speed.
    """

    def __init__(
        self,
        coef: tuple[float, float, float, float, float, float],
        rps_rated: float,
        fit_box: FitBox = FIT_BOX,
    ) -> None:
        self.coef = coef
        self.rps_rated = rps_rated
        self.fit_box = fit_box

    def __call__(self, pressure_ratio: float, rps: float | None = None) -> float:
        speed = self.rps_rated if rps is None else rps
        d = self.fit_box
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
        return not self.fit_box.contains(pressure_ratio, self.rps_rated if rps is None else rps)


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
