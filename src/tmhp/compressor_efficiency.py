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
import weakref
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache, wraps
from typing import Any, TypeVar, cast

from scipy.optimize import brentq

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


# ===========================================================================
# Compressor-type-specific semi-empirical models (draft, open to review)
# ===========================================================================
# DESIGN: ~/scratch/tmhp-efficiency-model-20261115/DESIGN.md sections 5 and 11;
# fit archive: validation/coefficients/semi-empirical-2026-11-15/ (README there
# lists what is provisional). Fitted to standalone-compressor data only.
#
# Where each loss sits
# --------------------
# The archive fits the *terminal-basis* overall isentropic efficiency
#
#     eta_oi = m_dot (h_2s - h_1) / P_terminal          (inverter excluded)
#
# and splits it into the package's two compressor quantities with a constant
# electromechanical share ``eta_em`` (measured scroll and rotary discharge
# temperatures, archive ``split``):
#
#     eta_cmp_isen = eta_oi / eta_em     the gas-side loss: sets the discharge
#                                        enthalpy and so the condenser duty
#     eta_cmp      = eta_em * eta_inv    the loss that never reaches the gas:
#                                        motor and mechanical (eta_em) and the
#                                        drive (eta_inv, Ossorio inverter data)
#
# so the package's ``E_cmp = m_dot (h_2 - h_1) / eta_cmp`` is the electrical
# input at the **inverter input** (grid side). ``eta_cmp_vol`` is the archive's
# volumetric form. The three quantities share the suction state, which the
# forms need beyond PR and speed (:class:`SuctionState`).

#: Compressor types a model may be given (``compressor_type``); the first is the default.
COMPRESSOR_TYPES = ("rotary_twin_inverter", "scroll", "rotary_single_fixed")
#: Rotary speed-shape variants (measurement only until the owner decides; the first is the default).
ROTARY_SPEED_VARIANTS = ("archive", "ossorio_anchored")

# --- BEGIN GENERATED semi-empirical coefficients ---
# Emitted by ``python -m validation.compressor_maps.semi_empirical.emit`` from
# validation/coefficients/semi-empirical-2026-11-15/coefficients.json and rotary_speed_variants.json.
SEMI_EMPIRICAL_VERSION = "semi-empirical-2026-11-15"
SEMI_EMPIRICAL_TYPES: dict[str, Any] = {
    "scroll": {
        "family": "scroll",
        "level_form": "add",
        "speed": "abs",
        "parameters": {"rv": 2.975091, "q2": 1.054178, "cL": 0.058443, "zv": 0.64542},
        "level": 1.091016,
        "level_by_refrigerant": {},
        "eta_v": {"c0": 1.00841, "c_re": 0.010634, "c_Lv": 0.0174, "speed": "abs"},
        "eta_em": 0.915582,
        "hull_PR_N": (
            (6.1714, 90.0),
            (1.5646, 90.0),
            (1.5016, 72.5),
            (1.462, 58.0),
            (1.4552, 38.667),
            (1.5426, 20.0),
            (4.9446, 20.0),
            (7.6708, 38.667),
            (8.7619, 58.333),
            (7.6708, 72.5),
        ),
        "provisional": False,
    },
    "rotary_twin_inverter": {
        "family": "rotary",
        "level_form": "scaled",
        "speed": "rel",
        "parameters": {"cL": 0.03401, "cu": 0.118791},
        "level": 1.275779,
        "level_by_refrigerant": {"R32": 1.316402, "R410A": 1.172723},
        "eta_v": {"c0": 0.989702, "c_re": 0.022817, "c_Lv": 0.027815, "speed": "rel"},
        "eta_em": 0.857678,
        "hull_PR_N": ((1.9429, 30.0), (6.8421, 30.0), (6.8421, 120.0), (1.9429, 120.0)),
        "provisional": True,
    },
    "rotary_single_fixed": {
        "family": "rotary",
        "level_form": "scaled",
        "speed": "rel",
        "parameters": {"cL": 0.03401, "cu": 0.118791},
        "level": 1.729347,
        "level_by_refrigerant": {},
        "eta_v": {"c0": 0.981532, "c_re": 0.022817, "c_Lv": 0.027815, "speed": "rel"},
        "eta_em": 0.857678,
        "hull_PR_N": ((1.9429, 30.0), (6.8421, 30.0), (6.8421, 120.0), (1.9429, 120.0)),
        "provisional": True,
    },
}
SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS: dict[str, Any] = {
    "archive": {
        "terms": ("q2", "cL", "cu"),
        "parameters": {"cL": 0.03401, "cu": 0.118791},
        "level": {"rotary_twin_inverter": 1.275779, "rotary_single_fixed": 1.729347},
        "level_by_refrigerant": {"rotary_twin_inverter": {"R32": 1.316402, "R410A": 1.172723}},
    },
    "ossorio_anchored": {
        "terms": ("q2", "cu", "cH", "qf"),
        "parameters": {"cu": 0.242992, "cH": 2.408944, "qf": 0.123333},
        "level": {"rotary_twin_inverter": 0.501171, "rotary_single_fixed": 0.790302},
        "level_by_refrigerant": {"rotary_twin_inverter": {"R32": 0.447823, "R410A": 0.567186}},
    },
}
SEMI_EMPIRICAL_DRIVE: dict[str, Any] = {"lam": 0.058774, "beta": 0.617538, "N_ref_rps": 60.0}


# --- END GENERATED semi-empirical coefficients ---
class SuctionStateRequired(TypeError):
    """A state-dependent efficiency model was evaluated without the suction state."""


@dataclass(frozen=True)
class SuctionState:
    """Compressor suction state handed to an efficiency model that declares ``needs_state``.

    ``p_suc`` suction pressure [Pa], ``rho_suc`` suction density [kg/m3] (the
    homogeneous density for wet suction), ``dh_isen`` isentropic enthalpy rise
    ``h_2s - h_1`` [J/kg] to the discharge pressure. The pressure ratio is
    passed beside it, as for every model.
    """

    p_suc: float
    rho_suc: float
    dh_isen: float

    @property
    def mep(self) -> float:
        """Isentropic mean effective pressure ``rho_1 (h_2s - h_1)`` [Pa]."""
        return self.rho_suc * self.dh_isen


def suction_state(p_suc: float, rho_suc: float, h_suc: float, h_isen: float) -> SuctionState:
    """:class:`SuctionState` from the suction pressure, density, enthalpy and the isentropic discharge enthalpy."""
    return SuctionState(float(p_suc), float(rho_suc), float(h_isen) - float(h_suc))


#: Bracket of the effective exponent. The archive solved it in [0.8, 2.0]; the
#: work ratio is monotone in k, so the wider bracket returns the same root
#: wherever that one has it and also covers wet suction near saturation.
_K_LO, _K_HI = 0.5, 3.0


def _pow_term(e: float, log_x: float) -> float:
    """(x**e - 1) / e, continuous through e = 0 (where it is ln x)."""
    if abs(e) < 1e-9:
        return log_x
    return math.expm1(e * log_x) / e


@lru_cache(maxsize=8192)
def effective_exponent(pressure_ratio: float, work_ratio: float) -> float:
    """The k with ``k/(k-1) (PR^((k-1)/k) - 1) = w_s rho_1 / p_1`` (``work_ratio``).

    Raises :class:`InvalidCompressorEfficiency` when no k in
    ``[0.5, 3.0]`` reproduces the state (the point is not one the forms
    describe): no silent substitute.
    """
    if not (math.isfinite(pressure_ratio) and pressure_ratio > 1.0 and math.isfinite(work_ratio) and work_ratio > 0):
        raise InvalidCompressorEfficiency(f"no effective exponent at PR={pressure_ratio!r}, w_s rho/p={work_ratio!r}")
    log_pr = math.log(pressure_ratio)

    def f(k: float) -> float:
        return _pow_term((k - 1.0) / k, log_pr) - work_ratio

    f_lo, f_hi = f(_K_LO), f(_K_HI)
    if f_lo * f_hi > 0.0:
        raise InvalidCompressorEfficiency(
            f"no effective exponent in [{_K_LO}, {_K_HI}] at PR={pressure_ratio:.4g}, w_s rho/p={work_ratio:.4g}"
        )
    return float(brentq(f, _K_LO, _K_HI, xtol=1e-12))


def _phi(pressure_ratio: float, k: float, rv: float) -> float:
    """Scroll work ratio: isentropic work to the built-in volume ratio ``rv`` plus isochoric work, over isentropic."""
    log_pr = math.log(pressure_ratio)
    ws = _pow_term((k - 1.0) / k, log_pr)
    wi = k * _pow_term(k - 1.0, math.log(rv)) + (pressure_ratio / rv - rv ** (k - 1.0))
    return float(wi / ws)


@dataclass(frozen=True)
class HullDomain:
    """Convex (PR, N) hull of a fit's data, vertices counter-clockwise; N in rev/s.

    Used for the ``efficiency_extrapolated`` flag only: outside it the
    semi-empirical forms are followed as they are (no hold, no clamp).
    """

    vertices: tuple[tuple[float, float], ...]

    def contains(self, pressure_ratio: float, rps: float, rtol: float = 0.0) -> bool:
        """Whether (PR, N) lies inside the hull (``rtol``: also accept a point that far outside, relatively)."""
        if self._inside(pressure_ratio, rps):
            return True
        if rtol <= 0.0:
            return False
        return any(
            self._inside(pressure_ratio * (1.0 + a * rtol), rps * (1.0 + b * rtol))
            for a, b in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
        )

    def _inside(self, x: float, y: float) -> bool:
        if not (math.isfinite(x) and math.isfinite(y)):
            return False
        v = self.vertices
        sign = 0.0
        for (x1, y1), (x2, y2) in zip(v, v[1:] + v[:1], strict=True):
            cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
            if cross == 0.0:
                continue
            if sign == 0.0:
                sign = cross
            elif (cross > 0.0) != (sign > 0.0):
                return False
        return True


def _hull(vertices: tuple[tuple[float, float], ...]) -> HullDomain:
    """Hull with counter-clockwise vertices (the archive writes scipy ConvexHull order)."""
    area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(vertices, vertices[1:] + vertices[:1], strict=True))
    return HullDomain(tuple(vertices) if area > 0 else tuple(reversed(vertices)))


@dataclass(frozen=True)
class CompressorSpec:
    """Which default compressor model a heat-pump model builds (the ``compressor_type`` argument).

    The public form of ``compressor_type`` is one of :data:`COMPRESSOR_TYPES`;
    a ``CompressorSpec`` adds the options that exist **for measurement only**
    while the owner question of DESIGN 11.4 is open:

    ``rotary_speed_variant``
        ``"archive"`` (default; the archive's fit, Shao 2004, Ossorio 2019 and
        AREP #26 together) or ``"ossorio_anchored"`` (Shao kept out of the speed
        shape; ``validation/compressor_maps/semi_empirical/variants.py``).
        Rotary types only.
    ``inverter``
        False sets the drive efficiency to one (isolates the inverter loss).
    ``eta_oi_scale``
        Multiplies ``eta_oi`` (sensitivity to the efficiency level, e.g. 0.90
        for the independently measured level of DESIGN 11.3 item 1). With the
        constant split it scales ``eta_cmp_isen``.
    """

    compressor_type: str = COMPRESSOR_TYPES[0]
    rotary_speed_variant: str = ROTARY_SPEED_VARIANTS[0]
    inverter: bool = True
    eta_oi_scale: float = 1.0

    def __post_init__(self) -> None:
        if self.compressor_type not in COMPRESSOR_TYPES:
            raise ValueError(f"compressor_type must be one of {COMPRESSOR_TYPES}, got {self.compressor_type!r}")
        if self.rotary_speed_variant not in ROTARY_SPEED_VARIANTS:
            raise ValueError(
                f"rotary_speed_variant must be one of {ROTARY_SPEED_VARIANTS}, got {self.rotary_speed_variant!r}"
            )
        if not (math.isfinite(self.eta_oi_scale) and 0.0 < self.eta_oi_scale <= 1.5):
            raise ValueError(f"eta_oi_scale must be in (0, 1.5], got {self.eta_oi_scale!r}")


def resolve_compressor_type(value: str | CompressorSpec) -> CompressorSpec:
    """A model's ``compressor_type`` argument as a :class:`CompressorSpec` (validated)."""
    if isinstance(value, CompressorSpec):
        return value
    if isinstance(value, str):
        return CompressorSpec(value)
    raise TypeError(f"compressor_type must be a str or CompressorSpec, got {type(value).__name__}")


class SemiEmpiricalCompressor:
    """One compressor of a given type: ``eta_oi``, ``eta_v``, the split and the drive.

    Forms (archive ``coefficients.json``; ``n`` the speed coordinate)::

        scroll   1/eta_oi = a phi_z(PR; rv, k, zv) + L          ("add" level form)
        rotary   1/eta_oi = 1 + a L          (q2 = 1)           ("scaled" level form)
        L = (q0/n + q1 + q2 n) 1e5/MEP_s + cL (PR-1)/n + qf n^2 1e4 rho_1/MEP_s
            + cu (MEP_s/1e6)/n + cH (PR^((k-1)/k) - 1)          (absent terms are zero)
        eta_v   = c0 - c_re (PR^(1/k) - 1) - c_Lv (PR - 1)/n
        eta_inv = 1 - lam (1 + beta (60/N - 1))                 (N in rev/s)

    Speed coordinate: scroll ``n = N/50`` (absolute); rotary ``n = N/N_rated``
    with ``N_rated`` the **machine's** rated speed (``rps_rated``, the speed the
    model solves at its rating point, or the caller's). The archive anchored the
    rotary level on 60 Hz (twin) and 50 Hz (single) rating points at ``n = 1``;
    here ``n = 1`` is the machine's own rated point, so the level is read at the
    rated point of the machine whatever its rated speed. ``rps_rated=None``
    evaluates every speed as the rated speed (``n = 1``): that is the object
    used while the rated speed itself is being solved, where the candidate
    speed *is* the rated speed by definition.

    Out of the data the forms are followed as they are; :attr:`validity_domain`
    (the data hull, N in rev/s as the archive wrote it) only feeds the
    ``efficiency_extrapolated`` flag.
    """

    def __init__(self, spec: CompressorSpec, ref: str, rps_rated: float | None) -> None:
        self.spec = spec
        self.ref = ref
        self.rps_rated = rps_rated
        t = SEMI_EMPIRICAL_TYPES[spec.compressor_type]
        self.family: str = t["family"]
        self.level_form: str = t["level_form"]
        self.speed: str = t["speed"]
        params = dict(t["parameters"])
        level = t["level"]
        by_ref = t["level_by_refrigerant"]
        if self.family == "rotary":
            v = SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS[spec.rotary_speed_variant]
            params = dict(v["parameters"])
            level = v["level"][spec.compressor_type]
            by_ref = v["level_by_refrigerant"].get(spec.compressor_type, {})
        self.parameters: dict[str, float] = params
        self.level: float = float(by_ref.get(ref, level))
        self.eta_v_coefficients: dict[str, Any] = dict(t["eta_v"])
        self.eta_em_split: float = float(t["eta_em"])
        self.validity_domain = _hull(t["hull_PR_N"])
        self.provisional: bool = bool(t["provisional"])
        if self.family == "rotary" and self.level_form != "scaled":
            raise ValueError("rotary forms are fitted in the scaled level form")
        self.eta_vol = SemiEmpiricalEfficiency(self, "eta_cmp_vol")
        self.eta_isen = SemiEmpiricalEfficiency(self, "eta_cmp_isen")
        self.eta_em = SemiEmpiricalEfficiency(self, "eta_cmp")

    def _n(self, rps: float, coordinate: str) -> float:
        if coordinate == "abs":
            return rps / 50.0
        return 1.0 if self.rps_rated is None else rps / self.rps_rated

    def _k(self, pressure_ratio: float, state: SuctionState) -> float:
        return effective_exponent(pressure_ratio, state.dh_isen * state.rho_suc / state.p_suc)

    def eta_oi(self, pressure_ratio: float, rps: float, state: SuctionState) -> float:
        """Terminal-basis overall isentropic efficiency (inverter excluded, before ``eta_oi_scale``)."""
        p = self.parameters
        k = self._k(pressure_ratio, state)
        mep = state.mep
        n = self._n(rps, self.speed)
        if not (mep > 0.0 and n > 0.0):
            raise InvalidCompressorEfficiency(f"nonpositive MEP_s={mep!r} or speed coordinate {n!r}")
        loss = (p.get("q0", 0.0) / n + p.get("q1", 0.0) + p.get("q2", 1.0 if self.family == "rotary" else 0.0) * n) * (
            1e5 / mep
        )
        loss += p.get("cL", 0.0) * (pressure_ratio - 1.0) / n
        loss += p.get("qf", 0.0) * n * n * 1e4 * state.rho_suc / mep
        loss += p.get("cu", 0.0) * (mep / 1e6) / n
        loss += p.get("cH", 0.0) * (pressure_ratio ** ((k - 1.0) / k) - 1.0)
        if self.family == "scroll":
            rv = p["rv"]
            base = _phi(pressure_ratio, k, rv)
            if pressure_ratio < rv**k:
                base -= p.get("zv", 0.0) * (base - 1.0)
            inverse = self.level * base + loss
        else:
            inverse = 1.0 + self.level * loss
        return float(1.0 / inverse)

    def eta_v(self, pressure_ratio: float, rps: float, state: SuctionState) -> float:
        c = self.eta_v_coefficients
        k = self._k(pressure_ratio, state)
        n = self._n(rps, c["speed"])
        return float(c["c0"] - c["c_re"] * (pressure_ratio ** (1.0 / k) - 1.0) - c["c_Lv"] * (pressure_ratio - 1.0) / n)

    def eta_inverter(self, rps: float) -> float:
        """Drive efficiency at shaft speed ``rps`` [rev/s] (one with ``inverter=False``)."""
        if not self.spec.inverter:
            return 1.0
        d = SEMI_EMPIRICAL_DRIVE
        return float(1.0 - d["lam"] * (1.0 + d["beta"] * (d["N_ref_rps"] / rps - 1.0)))


class SemiEmpiricalEfficiency:
    """One of the package's three quantities of a :class:`SemiEmpiricalCompressor`.

    ``eta_cmp_vol`` and ``eta_cmp_isen`` declare ``needs_state`` and are called
    ``model(PR, N, state)``; ``eta_cmp`` (motor, mechanical and drive) depends
    on the speed only and is called ``model(PR, N)``. Calling a state-dependent
    quantity without the state raises :class:`SuctionStateRequired`.
    """

    def __init__(self, compressor: SemiEmpiricalCompressor, quantity: str) -> None:
        self.compressor = compressor
        self.quantity = quantity
        self.needs_state = quantity != "eta_cmp"

    @property
    def validity_domain(self) -> HullDomain:
        return self.compressor.validity_domain

    @property
    def rps_rated(self) -> float | None:
        return self.compressor.rps_rated

    def __call__(self, pressure_ratio: float, rps: float, state: SuctionState | None = None) -> float:
        c = self.compressor
        if self.quantity == "eta_cmp":
            return c.eta_em_split * c.eta_inverter(rps)
        if state is None:
            raise SuctionStateRequired(
                f"the {c.spec.compressor_type} {self.quantity} model needs the suction state: "
                "call it as model(PR, rps, SuctionState(...)) (see tmhp.compressor_efficiency.SuctionState)"
            )
        if self.quantity == "eta_cmp_vol":
            return c.eta_v(pressure_ratio, rps, state)
        return c.eta_oi(pressure_ratio, rps, state) * c.spec.eta_oi_scale / c.eta_em_split

    def extrapolated(self, pressure_ratio: float, rps: float) -> bool:
        return not self.validity_domain.contains(pressure_ratio, rps)

    def __repr__(self) -> str:
        return f"SemiEmpiricalEfficiency({self.compressor.spec!r}, {self.quantity}, rps_rated={self.rps_rated!r})"


def default_efficiencies(spec: CompressorSpec, ref: str, rps_rated: float | None) -> dict[str, Callable]:
    """The three default efficiency models of a machine (``rps_rated=None``: while the rated speed is solved)."""
    c = SemiEmpiricalCompressor(spec, ref, rps_rated)
    return {"eta_cmp_isen": c.eta_isen, "eta_cmp_vol": c.eta_vol, "eta_cmp": c.eta_em}


def efficiency_extrapolated(model: float | Callable, pressure_ratio: float, rps: float) -> bool:
    """Whether ``model`` is evaluated outside its fitting data at (PR, N).

    True only for models that carry a ``validity_domain`` (the defaults here)
    and a point outside it; scalars and user callables without one give False.
    For a result row use ``PR = P_ref_cmp_out / P_ref_cmp_in`` and
    ``N = cmp_rpm / 60``.
    """
    domain = getattr(model, "validity_domain", None)
    if not isinstance(domain, (ValidityDomain, HullDomain)):
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


# Whether a model is ``model(PR, rps)`` or ``model(PR)`` belongs to the model,
# not to the call, and ``inspect.signature`` costs about ten times the
# polynomial it guards; a cycle evaluation asks for some twenty efficiencies.
# The answer is decided once per model and kept in a weak-key dictionary: an
# entry goes when its model is collected, and reassigning ``eta_cmp_*`` brings
# a new key. The dictionary finds keys by equality, so each entry also records
# the identity of the object it was decided for, and an answer is used only for
# that object: two models that compare equal (a user ``__eq__``) keep their own
# call forms. The recorded identity cannot be taken over by a new object while
# its key is alive, and the entry dies with the key. A model that cannot be a
# weak key (a ``__slots__`` instance without ``__weakref__``, an unhashable
# one, one whose hash or equality raises) is inspected on every call, as before.

#: model -> (``id`` of the model the answer was decided for, whether it is
#: called as ``model(PR, rps)`` -- else ``model(PR)``).
_TAKES_SPEED: weakref.WeakKeyDictionary[Callable, tuple[int, bool]] = weakref.WeakKeyDictionary()


def _takes_speed(model: Callable) -> bool:
    """Whether ``model`` binds ``(PR, rps)``; else it must bind ``(PR)`` (TypeError otherwise)."""
    try:
        entry = _TAKES_SPEED.get(model)
    except Exception:  # not hashable, not weakly referenceable, or a hash / equality that raises
        entry = None
    else:
        if entry is not None and entry[0] == id(model):
            return entry[1]
    signature = inspect.signature(model)
    try:
        signature.bind(1.0, 1.0)
    except TypeError:
        signature.bind(1.0)
        takes_speed = False
    else:
        takes_speed = True
    if entry is None:  # an equal key of another object keeps its own entry
        with suppress(Exception):
            _TAKES_SPEED.setdefault(model, (id(model), takes_speed))
    return takes_speed


def needs_state(model: object) -> bool:
    """Whether ``model`` declares that it is called ``model(PR, rps, state)`` (attribute ``needs_state``)."""
    return callable(model) and getattr(model, "needs_state", False) is True


def _eval_eff(model: float | Callable, PR: float, rps: float, state: SuctionState | None = None) -> float:
    """Evaluate a scalar or a callable efficiency model and enforce 0 < eta <= 1.

    Call forms: a model that declares ``needs_state = True`` is called
    ``model(PR, rps, state)`` with the :class:`SuctionState` of the cycle
    (:class:`SuctionStateRequired` when the caller has none); otherwise
    ``model(PR, rps)`` or ``model(PR)`` -- the public way to supply one's own
    efficiency. Inspect argument binding rather than catching a TypeError
    raised inside a model; the answer is kept per model (:func:`_takes_speed`).
    """
    if not callable(model):
        value = float(model)
    elif needs_state(model):
        if state is None:
            raise SuctionStateRequired(f"{model!r} needs the suction state; the caller passed none")
        value = float(model(PR, rps, state))
    elif _takes_speed(model):
        value = float(model(PR, rps))
    else:
        value = float(model(PR))
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
