"""Compressor efficiency models: type-specific semi-empirical defaults and the rules every efficiency obeys.

A heat-pump model evaluates three compressor quantities at each cycle state:

``eta_cmp_isen``
    Isentropic efficiency -- the loss that reaches the gas. It sets the
    discharge enthalpy and so the condenser duty and the discharge temperature.
``eta_cmp_vol``
    Volumetric efficiency -- the delivered fraction of the swept volume. It
    sets the mass flow.
``eta_cmp``
    The loss that never reaches the gas: motor, mechanical and drive. It sets
    the electrical input ``E_cmp = m_dot (h_2 - h_1) / eta_cmp``, which is the
    power at the **inverter input** (grid side).

Default models
--------------
The defaults are semi-empirical forms fitted to standalone-compressor data,
one set per compressor type (:data:`COMPRESSOR_TYPES`; a model's
``compressor_type`` argument, :class:`CompressorSpec`). The fit, its data and
what in it is provisional are archived in
``validation/coefficients/<COEFFICIENT_VERSION>/`` (see the README there); the
constants between the ``GENERATED`` markers below are emitted from that archive
by ``validation/compressor_maps/semi_empirical/emit.py``.

The archive fits the *terminal-basis* overall isentropic efficiency ::

    eta_oi = m_dot (h_2s - h_1) / P_terminal          (inverter excluded)

and splits it into the package's two power quantities by the share ``f`` of
the loss work ``P - m_dot w_s`` that reaches the gas (measured scroll and
rotary discharge temperatures; archive ``split``, form F)::

    eta_cmp_isen = 1 / (1 + f (1/eta_oi - 1))
    eta_cmp      = (eta_oi + f (1 - eta_oi)) * eta_inv

``eta_inv`` is the drive efficiency (Ossorio inverter data) and
``eta_em = eta_oi + f (1 - eta_oi)`` the motor and mechanical part, so
``eta_cmp_isen * eta_em = eta_oi``. Both lie in (0, 1] for any ``eta_oi`` in
(0, 1] and ``f`` in [0, 1]. Outside their data the forms are followed as they
are -- nothing is held, clamped or refused there; the range rule below is the
only bound. ``eta_cmp_vol`` is the archive's volumetric form. The three
quantities share the suction state, which the forms need beyond the pressure
ratio and the speed (:class:`SuctionState`). :class:`SemiEmpiricalCompressor`
writes the forms out; :func:`default_efficiencies` returns the three models of
one machine.

Call forms
----------
An efficiency is a scalar or a callable (:func:`_eval_eff`):

* ``model(PR, rps, state)`` for a model that declares ``needs_state = True``
  -- the defaults. ``state`` is the :class:`SuctionState` of the cycle;
  calling such a model without it raises :class:`SuctionStateRequired`.
* ``model(PR, rps)`` or ``model(PR)`` -- how a caller supplies their own.

``PR`` is the pressure ratio and ``rps`` the shaft speed in rev/s.

Rules
-----
* Range: every evaluated efficiency satisfies ``0 < eta <= 1``
  (:func:`_eval_eff`). A value above one is set to one; a nonpositive or
  nonfinite value makes the operating point infeasible
  (:class:`InvalidCompressorEfficiency`).
* Data hull: each default type carries the convex (PR, N) hull of its fitting
  data (:class:`HullDomain`, attribute ``validity_domain``). It feeds the
  ``efficiency_extrapolated`` flag of a result and nothing else
  (:func:`efficiency_extrapolated`).
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

#: Compressor types a model may be given (``compressor_type``); the first is the default.
COMPRESSOR_TYPES = ("rotary_twin_inverter", "scroll", "rotary_single_fixed")

# --- BEGIN GENERATED semi-empirical coefficients ---
# Emitted by ``python -m validation.compressor_maps.semi_empirical.emit`` from
# validation/coefficients/semi-empirical-2026-11-15/coefficients.json and rotary_speed_variants.json.
COEFFICIENT_VERSION = "semi-empirical-2026-11-15"
SEMI_EMPIRICAL_TYPES: dict[str, Any] = {
    "scroll": {
        "family": "scroll",
        "level_form": "add",
        "speed": "abs",
        "parameters": {"rv": 2.975091, "q2": 1.054178, "cL": 0.058443, "zv": 0.64542},
        "level": 1.091016,
        "level_by_refrigerant": {},
        "eta_v": {"c0": 1.011463, "parameters": {"c_re": 0.009801, "c_L": 0.02071}, "speed": "abs"},
        "loss_share": 0.768674,
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
        "level": 1.27097,
        "level_by_refrigerant": {"R290": 1.145152, "R32": 1.316402, "R410A": 1.172723},
        "eta_v": {"c0": 1.129917, "parameters": {"c_re": 0.068093, "c_s": 0.123416}, "speed": "rel"},
        "loss_share": 0.575922,
        "hull_PR_N": ((1.9429, 30.0), (6.8421, 30.0), (6.8421, 120.0), (1.9429, 120.0)),
        "provisional": True,
    },
    "rotary_single_fixed": {
        "family": "rotary",
        "level_form": "scaled",
        "speed": "rel",
        "parameters": {"cL": 0.03401, "cu": 0.118791},
        "level": 1.729347,
        "level_by_refrigerant": {"R22": 1.790992, "R410A": 1.692558},
        "eta_v": {"c0": 1.101583, "parameters": {"c_re": 0.068093, "c_s": 0.123416}, "speed": "rel"},
        "loss_share": 0.575922,
        "hull_PR_N": ((1.9429, 30.0), (6.8421, 30.0), (6.8421, 120.0), (1.9429, 120.0)),
        "provisional": True,
    },
}
SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS: dict[str, Any] = {
    "shao_informed": {
        "terms": ("q2", "cL", "cu"),
        "parameters": {"cL": 0.03401, "cu": 0.118791},
        "level": {"rotary_twin_inverter": 1.27097, "rotary_single_fixed": 1.729347},
        "level_by_refrigerant": {
            "rotary_twin_inverter": {"R290": 1.145152, "R32": 1.316402, "R410A": 1.172723},
            "rotary_single_fixed": {"R22": 1.790992, "R410A": 1.692558},
        },
    },
    "ossorio_anchored": {
        "terms": ("q2", "cu", "cH", "qf"),
        "parameters": {"cu": 0.242992, "cH": 2.408944, "qf": 0.123333},
        "level": {"rotary_twin_inverter": 0.52336, "rotary_single_fixed": 0.790302},
        "level_by_refrigerant": {
            "rotary_twin_inverter": {"R290": 0.790146, "R32": 0.447823, "R410A": 0.567186},
            "rotary_single_fixed": {"R22": 0.802211, "R410A": 0.783514},
        },
    },
}
SEMI_EMPIRICAL_DRIVE: dict[str, Any] = {"lam": 0.058774, "beta": 0.617538, "N_ref_rps": 60.0}
# --- END GENERATED semi-empirical coefficients ---
#: Rotary speed-shape variants; the first (``shao_informed``, the first archive's arrangement) is the archive's and
#: the package's default, the other (``ossorio_anchored``) is kept selectable through :class:`CompressorSpec`.
ROTARY_SPEED_VARIANTS = tuple(SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS)


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

    A plain string, one of :data:`COMPRESSOR_TYPES`, names the type; a
    ``CompressorSpec`` also selects the speed shape of the rotary types:

    ``rotary_speed_variant``
        ``"shao_informed"`` (default: Shao 2004, Ossorio 2019 and AREP #26 in
        one shared speed shape) or ``"ossorio_anchored"`` (Shao 2004 kept out
        of the speed shape, which then rests on Ossorio 2019). The compressor
        data do not separate the two shapes, so both stay in the library. The
        default was chosen by the EN 14825 trajectory-shape rule recorded in
        the archive README ("Rotary speed-shape variants"), which this option
        reproduces; that shape check is therefore not an independent
        validation of the rotary types. The option has no effect on a scroll.
    """

    compressor_type: str = COMPRESSOR_TYPES[0]
    rotary_speed_variant: str = ROTARY_SPEED_VARIANTS[0]

    def __post_init__(self) -> None:
        if self.compressor_type not in COMPRESSOR_TYPES:
            raise ValueError(f"compressor_type must be one of {COMPRESSOR_TYPES}, got {self.compressor_type!r}")
        if self.rotary_speed_variant not in ROTARY_SPEED_VARIANTS:
            raise ValueError(
                f"rotary_speed_variant must be one of {ROTARY_SPEED_VARIANTS}, got {self.rotary_speed_variant!r}"
            )


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
        eta_v   = c0 exp(-c_re (PR^(1/k) - 1)) / (1 + c_L (PR-1)/n + c_s/n + c_f n^2)
        eta_is  = 1 / (1 + f (1/eta_oi - 1)),   eta_em = eta_oi + f (1 - eta_oi)
        eta_inv = 1 - lam (1 + beta (60/N - 1))                 (N in rev/s)

    ``eta_v`` is positive for every PR and speed (clearance re-expansion as an
    exponential, leakage as competing outflows ``1/(1 + x)``); its rotary
    level ``c0`` is set from the catalogue rating points the default
    displacement regression pools, so the two agree at the rating condition.
    ``f`` is the share of the loss work that reaches the gas.

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
        self.loss_share: float = float(t["loss_share"])
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
        """Terminal-basis overall isentropic efficiency (inverter excluded)."""
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
        """Volumetric efficiency: positive and at most ``c0`` for every PR >= 1 and speed > 0."""
        c = self.eta_v_coefficients
        p = c["parameters"]
        k = self._k(pressure_ratio, state)
        n = self._n(rps, c["speed"])
        if not n > 0.0:
            raise InvalidCompressorEfficiency(f"nonpositive speed coordinate {n!r}")
        clearance = math.exp(-p.get("c_re", 0.0) * (pressure_ratio ** (1.0 / k) - 1.0))
        outflow = (
            1.0 + p.get("c_L", 0.0) * (pressure_ratio - 1.0) / n + p.get("c_s", 0.0) / n + p.get("c_f", 0.0) * n * n
        )
        return float(c["c0"] * clearance / outflow)

    def split(self, eta_oi: float) -> tuple[float, float]:
        """``(eta_is, eta_em)`` of an overall ``eta_oi``: a share ``f`` of the loss work reaches the gas."""
        f = self.loss_share
        return 1.0 / (1.0 + f * (1.0 / eta_oi - 1.0)), eta_oi + f * (1.0 - eta_oi)

    def eta_inverter(self, rps: float) -> float:
        """Drive efficiency at shaft speed ``rps`` [rev/s]."""
        d = SEMI_EMPIRICAL_DRIVE
        return float(1.0 - d["lam"] * (1.0 + d["beta"] * (d["N_ref_rps"] / rps - 1.0)))


class SemiEmpiricalEfficiency:
    """One of the package's three quantities of a :class:`SemiEmpiricalCompressor`.

    All three declare ``needs_state`` and are called ``model(PR, N, state)``:
    ``eta_cmp`` (motor, mechanical and drive) depends on ``eta_oi`` through
    the loss-share split. Calling one without the state raises
    :class:`SuctionStateRequired`.
    """

    def __init__(self, compressor: SemiEmpiricalCompressor, quantity: str) -> None:
        self.compressor = compressor
        self.quantity = quantity
        self.needs_state = True

    @property
    def validity_domain(self) -> HullDomain:
        return self.compressor.validity_domain

    @property
    def rps_rated(self) -> float | None:
        return self.compressor.rps_rated

    def __call__(self, pressure_ratio: float, rps: float, state: SuctionState | None = None) -> float:
        c = self.compressor
        if state is None:
            raise SuctionStateRequired(
                f"the {c.spec.compressor_type} {self.quantity} model needs the suction state: "
                "call it as model(PR, rps, SuctionState(...)) (see tmhp.compressor_efficiency.SuctionState)"
            )
        if self.quantity == "eta_cmp_vol":
            return c.eta_v(pressure_ratio, rps, state)
        eta_is, eta_em = c.split(c.eta_oi(pressure_ratio, rps, state))
        if self.quantity == "eta_cmp":
            return eta_em * c.eta_inverter(rps)
        return eta_is

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

    True only for a model that carries a :class:`HullDomain` as its
    ``validity_domain`` (the defaults here) and a point outside it; scalars and
    user callables without one give False.
    For a result row use ``PR = P_ref_cmp_out / P_ref_cmp_in`` and
    ``N = cmp_rpm / 60``.
    """
    domain = getattr(model, "validity_domain", None)
    if not isinstance(domain, HullDomain):
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
# not to the call, and ``inspect.signature`` costs about ten times a small
# model (a polynomial) it guards; a cycle evaluation asks for some twenty
# efficiencies.
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
    # approaches, so the nearest admissible value of an overshoot is the bound
    # itself. The rule is kept for the models a caller supplies (a fitted
    # polynomial overshoots by its fit noise where a loss vanishes), and it
    # still acts on the default volumetric forms: their level ``c0`` is above
    # one (1.01 scroll, 1.10-1.13 rotary), so they pass one at a low pressure
    # ratio well above the rated speed (1.08 at PR 1.2 and 150 rev/s for a
    # rotary rated at 40 rev/s). Rejecting instead would punch infeasible
    # holes into ordinary low-lift operation and break the monotone speed
    # bracket.
    return 1.0 if value > 1.0 else value
