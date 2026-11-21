"""Compressor efficiency models: the default maps and the rules every efficiency obeys.

A heat-pump model evaluates three compressor quantities at each cycle state:

``eta_cmp_isen``
    Isentropic efficiency -- the loss that reaches the gas. It sets the
    discharge enthalpy and so the condenser duty and the discharge temperature.
``eta_cmp_vol``
    Volumetric efficiency -- the delivered fraction of the swept volume. It
    sets the mass flow.
``eta_cmp``
    The loss that never reaches the gas. It sets the electrical input
    ``E_cmp = m_dot (h_2 - h_1) / eta_cmp``, the power at the **inverter
    input** (grid side); the drive is not modelled apart from the compressor.

Default maps
------------
Three maps of one compressor, fitted to the 134 measured points of a 46 cm3
R290 scroll (Ossorio's thesis, Appendix 1; the same tests as Compressor A of
Ossorio & Navarro-Peris 2023) with the electrical input as compressor plus
inverter (#217, #218)::

    eta_v  = m_dot / (rho_1 V N)
    eta_ov = m_dot (h_2s - h_1) / W
    f_loss = 1 - m_dot (h_2 - h_1) / W

Each is a polynomial in ``l = ln PR``, ``n = N/70`` (N in rev/s) and, for
``eta_v`` and ``eta_ov``, ``t = T_e/10`` with ``T_e`` the evaporating
saturation temperature in degrees C::

    eta_v, eta_ov = c . [1, l, l^2, l^3, n, n^2, l n, t]
    f_loss        = c . [1, l, l^2, l^3, n, n^2, l n]

The package's quantities follow as ::

    eta_cmp_vol  = eta_v
    eta_cmp      = 1 - f_loss
    eta_cmp_isen = eta_ov / (1 - f_loss)

so ``eta_cmp_isen * eta_cmp = eta_ov``. The evaporating temperature is there
because the measured ``eta_ov`` and ``eta_v`` differ at one pressure ratio and
speed: at PR 3 and 70 rev/s the map's ``eta_ov`` rises from 0.62 to 0.69 between
T_e = -10 and 15 degrees C. ``f_loss`` does not (a T_e term leaves its error
unchanged). The same maps serve every refrigerant: a target machine is
followed by calibrating the maps, not by refrigerant-specific forms. The fit,
its data and its scores are archived in
``validation/coefficients/<COEFFICIENT_VERSION>/`` (README there); the
constants between the ``GENERATED`` markers below are emitted from that
archive by ``validation/compressor_maps/ossorio_te/emit.py``.

Outside their data the maps are followed as they are -- nothing is held,
clamped or refused there; the range rule below is the only bound.

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
* Data domain: the defaults carry the domain of their fitting data
  (:class:`MapDomain`, attribute ``validity_domain``): the speeds 30-110 rev/s
  and the convex (PR, T_e) hull of the measured points. It feeds the
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
from functools import wraps
from typing import Any, TypeVar, cast

# --- BEGIN GENERATED Ossorio map coefficients ---
# Emitted by ``python -m validation.compressor_maps.ossorio_te.emit`` from
# validation/coefficients/ossorio-r290-te-2026-11-21/coefficients.json.
COEFFICIENT_VERSION = "ossorio-r290-te-2026-11-21"
MAP_COEFFICIENTS: dict[str, tuple[float, ...]] = {
    "eta_ov": (
        0.08905261072465571,
        0.9019553289840403,
        -0.5552874938098706,
        0.09515212021536734,
        0.16549070069084285,
        -0.12964715931254125,
        0.07056921229372137,
        0.02869564011673592,
    ),
    "eta_v": (
        0.9265573041933589,
        -0.04584660981380326,
        0.009912987211872077,
        -0.006917609173381446,
        0.12513403117704344,
        -0.06054046284791077,
        0.021027419506493727,
        0.013171342755074474,
    ),
    "f_loss": (
        0.10476791730311057,
        0.08909291250321598,
        -0.02262142273171193,
        0.008620732026797001,
        -0.07061710713805798,
        0.05123637120862445,
        -0.05547620163107184,
    ),
}
MAP_N_REF_RPS = 70.0
MAP_TE_SCALE_K = 10.0
MAP_DOMAIN: dict[str, Any] = {
    "N_rps": (30.0, 110.0),
    "hull_PR_Te": (
        (8.226673213867404, -30.22),
        (12.667054686847852, -30.11),
        (2.22284856625581, 25.0),
        (1.796478696296018, 25.05),
        (1.440381993403251, 24.99),
        (1.439120643473566, 24.97),
        (1.7587340092509858, 0.01),
        (3.402611370202053, -19.91),
        (3.4247891359756553, -20.01),
    ),
}
# --- END GENERATED Ossorio map coefficients ---

#: The quantities a default model evaluates, by the package's efficiency name.
QUANTITIES = ("eta_cmp_isen", "eta_cmp_vol", "eta_cmp")


class SuctionStateRequired(TypeError):
    """A state-dependent efficiency model was evaluated without the suction state."""


@dataclass(frozen=True)
class SuctionState:
    """Cycle state handed to an efficiency model that declares ``needs_state``.

    ``T_evap_K`` is the evaporating saturation temperature [K]. The pressure
    ratio and the speed are passed beside it, as for every model.
    """

    T_evap_K: float


@dataclass(frozen=True)
class HullDomain:
    """Convex polygon in a plane, vertices counter-clockwise (used for the data domain)."""

    vertices: tuple[tuple[float, float], ...]

    def contains(self, x: float, y: float, rtol: float = 0.0) -> bool:
        """Whether (x, y) lies inside (``rtol``: also accept a point that far outside, relatively)."""
        if self._inside(x, y):
            return True
        if rtol <= 0.0:
            return False
        return any(
            self._inside(x * (1.0 + a * rtol), y * (1.0 + b * rtol))
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
class MapDomain:
    """Data domain of the default maps: a speed range and the convex (PR, T_e) hull of the measured points.

    Used for the ``efficiency_extrapolated`` flag only: outside it the maps
    are followed as they are (no hold, no clamp).
    """

    hull_PR_Te: HullDomain
    rps_range: tuple[float, float]

    def contains(self, pressure_ratio: float, rps: float, T_evap_C: float | None = None, rtol: float = 0.0) -> bool:
        """Whether (PR, N[, T_e]) lies inside; without ``T_evap_C`` only the speed range is checked."""
        lo, hi = self.rps_range
        if not (math.isfinite(rps) and lo * (1.0 - rtol) <= rps <= hi * (1.0 + rtol)):
            return False
        if T_evap_C is None:
            return True
        return self.hull_PR_Te.contains(pressure_ratio, T_evap_C, rtol=rtol)


DOMAIN = MapDomain(_hull(MAP_DOMAIN["hull_PR_Te"]), MAP_DOMAIN["N_rps"])


def _terms(pressure_ratio: float, rps: float) -> tuple[float, ...]:
    if not (math.isfinite(pressure_ratio) and pressure_ratio > 0.0 and math.isfinite(rps)):
        raise InvalidCompressorEfficiency(f"no map value at PR={pressure_ratio!r}, rps={rps!r}")
    log_pr = math.log(pressure_ratio)
    n = rps / MAP_N_REF_RPS
    return (1.0, log_pr, log_pr * log_pr, log_pr**3, n, n * n, log_pr * n)


def _dot(coefficients: tuple[float, ...], terms: tuple[float, ...]) -> float:
    return math.fsum(c * x for c, x in zip(coefficients, terms, strict=True))


def map_eta_v(pressure_ratio: float, rps: float, T_evap_C: float) -> float:
    """Volumetric efficiency of the default map (T_e evaporating saturation temperature [C])."""
    return _dot(MAP_COEFFICIENTS["eta_v"], (*_terms(pressure_ratio, rps), T_evap_C / MAP_TE_SCALE_K))


def map_eta_ov(pressure_ratio: float, rps: float, T_evap_C: float) -> float:
    """Overall efficiency ``m_dot (h_2s - h_1) / W`` of the default map, W the compressor + inverter input."""
    return _dot(MAP_COEFFICIENTS["eta_ov"], (*_terms(pressure_ratio, rps), T_evap_C / MAP_TE_SCALE_K))


def map_f_loss(pressure_ratio: float, rps: float) -> float:
    """Share of the electrical input that does not reach the gas, of the default map."""
    return _dot(MAP_COEFFICIENTS["f_loss"], _terms(pressure_ratio, rps))


class MapEfficiency:
    """One of the package's three quantities from the default maps.

    All three declare ``needs_state`` and are called ``model(PR, rps, state)``
    with the :class:`SuctionState` of the cycle; calling one without it raises
    :class:`SuctionStateRequired`. ``eta_cmp`` does not use the state (its map
    has no T_e term) but takes it for the one call form of the defaults.
    """

    def __init__(self, quantity: str) -> None:
        if quantity not in QUANTITIES:
            raise ValueError(f"quantity must be one of {QUANTITIES}, got {quantity!r}")
        self.quantity = quantity
        self.needs_state = True

    @property
    def validity_domain(self) -> MapDomain:
        return DOMAIN

    def __call__(self, pressure_ratio: float, rps: float, state: SuctionState | None = None) -> float:
        if state is None:
            raise SuctionStateRequired(
                f"the default {self.quantity} map needs the suction state: "
                "call it as model(PR, rps, SuctionState(T_evap_K)) (see tmhp.compressor_efficiency.SuctionState)"
            )
        if self.quantity == "eta_cmp":
            return 1.0 - map_f_loss(pressure_ratio, rps)
        T_evap_C = state.T_evap_K - 273.15
        if self.quantity == "eta_cmp_vol":
            return map_eta_v(pressure_ratio, rps, T_evap_C)
        eta_em = 1.0 - map_f_loss(pressure_ratio, rps)
        if not eta_em > 0.0:
            raise InvalidCompressorEfficiency(f"map loss share {1.0 - eta_em!r} leaves no input for the gas")
        return map_eta_ov(pressure_ratio, rps, T_evap_C) / eta_em

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MapEfficiency) and other.quantity == self.quantity

    def __hash__(self) -> int:
        return hash((MapEfficiency, self.quantity))

    def __repr__(self) -> str:
        return f"MapEfficiency({self.quantity!r}, {COEFFICIENT_VERSION})"


def default_efficiencies() -> dict[str, Callable]:
    """The three default efficiency models (the maps are the same for every machine)."""
    return {quantity: MapEfficiency(quantity) for quantity in QUANTITIES}


def efficiency_extrapolated(
    model: float | Callable, pressure_ratio: float, rps: float, T_evap_C: float | None = None
) -> bool:
    """Whether ``model`` is evaluated outside its fitting data at (PR, N[, T_e]).

    True only for a model that carries a :class:`MapDomain` as its
    ``validity_domain`` (the defaults here) and a point outside it; scalars and
    user callables without one give False. Without ``T_evap_C`` only the speed
    range is checked. For a result row use ``PR = P_ref_cmp_out / P_ref_cmp_in``,
    ``N = cmp_rpm / 60`` and ``T_e = T_ref_evap_sat``.
    """
    domain = getattr(model, "validity_domain", None)
    if not isinstance(domain, MapDomain):
        return False
    return not domain.contains(pressure_ratio, rps, T_evap_C)


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
