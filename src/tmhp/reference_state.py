r"""Compressor reference state solved from nominal capacity.

BITZER compressor efficiencies use absolute shaft speed in rev/s. Models
normalise the ground heat exchanger refrigerant-side UA by a rated mass flow
(``m_dot_ref / m_dot_ref_rated``). Both denominators belong to one physical
point -- the *reference state* -- and this module computes that point from the
same inputs every model already requires:

.. code-block:: text

    hp_capacity -> V_cmp_ref -> rating condition -> rps_rated -> m_dot_ref_rated

``V_cmp_ref``
    Swept displacement. When the caller gives none, the models use
    :func:`tmhp.compressor_speed.default_displacement`, a capacity-scaled
    reduced-order default, not an identification of a real compressor.
``rating condition``
    A standard (or manufacturer/user) rating point for the model family:
    mode, source- and load-side inlet temperatures, the model's reference
    secondary flows and its rated UA values. It is independent of whatever
    operating condition is simulated afterwards.
``rps_rated``
    The speed at which the model delivers ``hp_capacity`` at the rating
    condition, with every efficiency evaluated at that actual shaft speed.
``m_dot_ref_rated``
    The refrigerant mass flow of that same state.

Capacity-only initialization constructs a representative compressor; it does
not identify the exact manufacturer compressor.

Solution method
---------------
At the rating condition the duty on the rated side (load side) is known, so
the rated-side saturation temperature follows from the rated-UA heat
exchanger directly. For a trial duty on the other side, the other saturation
temperature follows the same way; the cycle at those two temperatures then
fixes the speed through the bounded root

.. math:: Q_{model}(rps) - Q_{rated} = 0

with ``eta_vol(PR, 1)``, ``eta_isen(PR, 1)`` and ``eta_em(PR, 1)`` (or the
caller's own callables). An outer bounded root closes the energy balance of
the other heat exchanger. Variable ground-HX UA is not used here -- it needs
``m_dot_ref_rated``, which is what is being solved for -- so both heat
exchangers use their rated UA at their reference flow.

A rated capacity that needs a speed outside ``[rps_min, rps_max]`` -- or a
rating point the subcritical cycle or the rated heat exchangers cannot reach
-- is reported as :data:`REFERENCE_CAPACITY_INCONSISTENT` instead of being
clamped onto a bound: a clamped speed would deliver a different capacity and
silently redefine the machine. The pressure-ratio envelope is an operating
limit and is not imposed on the rating point; its pressure ratio is reported.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import CoolProp.CoolProp as CP
from scipy.optimize import brentq

from .compressor_efficiency import InvalidCompressorEfficiency, _eval_eff, make_eta_em, make_eta_isen, make_eta_vol
from .compressor_speed import solve_compressor_speed
from .constants import c_a, c_w, rho_a, rho_w
from .refrigerant import calc_ref_state

__all__ = [
    "REFERENCE_CAPACITY_INCONSISTENT",
    "ReferenceStateMixin",
    "RATING_STANDARDS",
    "HXSide",
    "RatingCondition",
    "ReferenceState",
    "ReferenceStateError",
    "rating_condition",
    "solve_reference_state",
]

#: Failure reason: the rated capacity is not reachable inside the speed
#: envelope (or by the subcritical cycle / rated HX) at the rating condition.
REFERENCE_CAPACITY_INCONSISTENT = "reference_capacity_inconsistent"

Mode = Literal["heating", "cooling"]
Fluid = Literal["air", "water", "tank"]


@dataclass(frozen=True)
class RatingStandard:
    """Inlet temperatures of one standard rating point [deg C]."""

    source_T_C: float
    load_T_C: float
    standard: str


#: Standard rating points per model family and mode.
#:
#: ``source_T_C`` / ``load_T_C`` are the secondary-fluid inlet temperatures.
#: TMHP's air coils are dry (sensible only), so wet-bulb conditions of the
#: standards are not used. The boiler models' condenser is immersed in a
#: lumped tank; the tank temperature is set to the standard's water outlet
#: temperature, the conservative end of the condenser water range.
RATING_STANDARDS: dict[str, dict[str, RatingStandard]] = {
    "ASHP": {
        "cooling": RatingStandard(35.0, 27.0, "ISO 5151:2017 T1 cooling (outdoor 35 °C DB, indoor 27 °C DB)"),
        "heating": RatingStandard(7.0, 20.0, "ISO 5151:2017 H1 heating (outdoor 7 °C DB, indoor 20 °C DB)"),
    },
    "GSHP": {
        "cooling": RatingStandard(
            25.0, 27.0, "ISO 13256-1:2021 ground-loop cooling (liquid entering 25 °C, indoor 27 °C DB)"
        ),
        "heating": RatingStandard(
            0.0, 20.0, "ISO 13256-1:2021 ground-loop heating (liquid entering 0 °C, indoor 20 °C DB)"
        ),
    },
    "ASHPB": {
        "heating": RatingStandard(7.0, 55.0, "EN 14511-2:2022 A7/W55 (outdoor air 7 °C DB, water outlet 55 °C)"),
    },
    "GSHPB": {
        "heating": RatingStandard(0.0, 55.0, "EN 14511-2:2022 B0/W55 (brine inlet 0 °C, water outlet 55 °C)"),
    },
    "WSHPB": {
        "heating": RatingStandard(10.0, 55.0, "EN 14511-2:2022 W10/W55 (water inlet 10 °C, water outlet 55 °C)"),
    },
}


class ReferenceStateError(ValueError):
    """Rated capacity cannot be reached at the rating condition."""

    reason = REFERENCE_CAPACITY_INCONSISTENT

    def __init__(self, message: str):
        super().__init__(f"{REFERENCE_CAPACITY_INCONSISTENT}: {message}")


@dataclass(frozen=True)
class HXSide:
    """Secondary side of one refrigerant heat exchanger at the rating point.

    ``fluid`` is ``"air"`` or ``"water"`` for a single-phase stream crossing a
    constant-saturation-temperature surface (``Q = eps C dT``,
    ``eps = 1 - exp(-UA / C)``), or ``"tank"`` for the boiler condenser
    immersed in a lumped tank (``Q = UA dT``), as in the models' own cycles.
    """

    fluid: Fluid
    T_in_C: float
    UA: float
    volume_flow: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.T_in_C):
            raise ValueError("Rating inlet temperature must be finite")
        if not (math.isfinite(self.UA) and self.UA > 0):
            raise ValueError("Rating heat-exchanger UA must be positive and finite")
        if self.fluid != "tank" and not (
            self.volume_flow is not None and math.isfinite(self.volume_flow) and self.volume_flow > 0
        ):
            raise ValueError("Rating reference flow must be positive and finite")

    @property
    def conductance(self) -> float:
        """Duty per kelvin of approach, ``eps C`` (or ``UA`` for a tank) [W/K]."""
        if self.fluid == "tank":
            return self.UA
        assert self.volume_flow is not None
        C = (c_a * rho_a if self.fluid == "air" else c_w * rho_w) * self.volume_flow
        return C * (1.0 - math.exp(-self.UA / C))


@dataclass(frozen=True)
class RatingCondition:
    """Rating point: mode, both secondary sides and the standard it follows."""

    mode: Mode
    source: HXSide
    load: HXSide
    standard: str

    def __post_init__(self) -> None:
        if self.mode not in ("heating", "cooling"):
            raise ValueError("Rating mode must be 'heating' or 'cooling'")


@dataclass(frozen=True)
class ReferenceState:
    """Self-consistent compressor reference state."""

    rps_rated: float
    m_dot_ref_rated: float
    condition: RatingCondition
    hp_capacity: float
    V_cmp_ref: float
    T_evap_sat_C: float
    T_cond_sat_C: float
    pressure_ratio: float
    Q_cond: float
    Q_evap: float
    E_cmp_ref: float
    E_cmp: float
    eta_cmp_isen: float
    eta_cmp_vol: float
    eta_cmp: float
    rps_rated_solved: bool = field(default=True)

    @property
    def cop(self) -> float:
        """Load-side duty over compressor electrical input at the reference state."""
        return self.hp_capacity / self.E_cmp


def rating_condition(
    family: str,
    *,
    source: Callable[[Mode, float], HXSide],
    load: Callable[[Mode, float], HXSide],
    default_mode: Mode,
    overrides: Mapping[str, Any] | RatingCondition | None = None,
) -> RatingCondition:
    """Resolve a family's rating condition, applying caller overrides.

    ``source`` / ``load`` build the model's own heat-exchanger side for a mode
    and an inlet temperature (rated UA, reference flow). ``overrides`` is a
    complete :class:`RatingCondition`, or a mapping with any of ``mode``,
    ``source_T_C``, ``load_T_C`` and ``standard``. Changing ``mode`` switches
    the defaults to that mode's standard point.
    """
    if isinstance(overrides, RatingCondition):
        return overrides
    table = RATING_STANDARDS[family]
    overrides = dict(overrides or {})
    unknown = set(overrides) - {"mode", "source_T_C", "load_T_C", "standard"}
    if unknown:
        raise ValueError(f"Unknown rated_condition keys: {sorted(unknown)}")
    mode = overrides.get("mode", default_mode)
    if mode not in table:
        raise ValueError(f"{family} has no {mode!r} rating condition; available: {sorted(table)}")
    base = table[mode]
    custom = {"source_T_C", "load_T_C"} & set(overrides)
    standard = overrides.get("standard", "user-specified rating condition" if custom else base.standard)
    return RatingCondition(
        mode=mode,
        source=source(mode, float(overrides.get("source_T_C", base.source_T_C))),
        load=load(mode, float(overrides.get("load_T_C", base.load_T_C))),
        standard=standard,
    )


def _rated_speed_efficiency(factory: Callable[[float], Callable]) -> Callable[[float, float], float]:
    """Evaluate absolute shaft speed at every candidate reference point."""
    correlation = factory(1.0)
    return lambda pressure_ratio, rps: correlation(pressure_ratio, rps)


#: Efficiency factories by attribute, used when the caller supplied none.
BASELINE_FACTORIES: dict[str, Callable[[float], Callable]] = {
    "eta_cmp_isen": make_eta_isen,
    "eta_cmp_vol": make_eta_vol,
    "eta_cmp": make_eta_em,
}


def reference_efficiencies(
    supplied: Mapping[str, float | Callable | None],
    resolved: Mapping[str, float | Callable] | None,
) -> dict[str, float | Callable]:
    """Efficiencies to evaluate the reference state with.

    ``resolved`` (the model's final efficiencies) is used when the rated speed
    is already known. Otherwise caller-supplied models are used as they are and
    omitted ones use BITZER correlations at each candidate shaft speed.
    """
    if resolved is not None:
        return dict(resolved)
    return {
        name: model if model is not None else _rated_speed_efficiency(BASELINE_FACTORIES[name])
        for name, model in supplied.items()
    }


_CACHE: dict[tuple, ReferenceState] = {}
_CACHE_MAX = 256


def solve_reference_state(
    *,
    ref: str,
    V_cmp_ref: float,
    hp_capacity: float,
    condition: RatingCondition,
    eta_cmp_isen: float | Callable,
    eta_cmp_vol: float | Callable,
    eta_cmp: float | Callable,
    dT_superheat: float,
    dT_subcool: float,
    dT_hx_min: float,
    rps_min: float,
    rps_max: float,
    rps_rated: float | None = None,
    cache_key: tuple | None = None,
) -> ReferenceState:
    """Solve the reference state at ``condition``.

    With ``rps_rated=None`` the speed delivering ``hp_capacity`` is solved and
    becomes the rated speed. With an explicit ``rps_rated`` the same state is
    solved with the model's efficiencies as given, so ``m_dot_ref_rated`` can
    still be derived; ``rps_rated`` itself is then left as supplied.

    ``cache_key`` (hashable, describing the efficiency models) enables a
    process-wide cache, so identical constructions solve once.
    """
    if not all(math.isfinite(v) and v > 0 for v in (V_cmp_ref, hp_capacity)):
        raise ValueError("V_cmp_ref and hp_capacity must be positive and finite")
    key = None
    if cache_key is not None:
        key = (
            ref,
            V_cmp_ref,
            hp_capacity,
            condition,
            dT_superheat,
            dT_subcool,
            dT_hx_min,
            rps_min,
            rps_max,
            rps_rated,
            cache_key,
        )
        if key in _CACHE:
            return _CACHE[key]
    try:
        state = _solve(
            ref=ref,
            V_cmp_ref=V_cmp_ref,
            Q_rated=hp_capacity,
            condition=condition,
            etas=(eta_cmp_isen, eta_cmp_vol, eta_cmp),
            dT_superheat=dT_superheat,
            dT_subcool=dT_subcool,
            dT_hx_min=dT_hx_min,
            rps_bounds=(rps_min, rps_max),
            rps_rated=rps_rated,
        )
    except InvalidCompressorEfficiency as exc:
        raise ReferenceStateError(
            f"raw BITZER efficiency outside (0, 1] at {condition.standard}; "
            "supply a supported rated_condition or custom compressor efficiencies"
        ) from exc
    if key is not None:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = state
    return state


def _solve(
    *,
    ref: str,
    V_cmp_ref: float,
    Q_rated: float,
    condition: RatingCondition,
    etas: tuple[float | Callable, float | Callable, float | Callable],
    dT_superheat: float,
    dT_subcool: float,
    dT_hx_min: float,
    rps_bounds: tuple[float, float],
    rps_rated: float | None,
) -> ReferenceState:
    eta_isen_model, eta_vol_model, eta_em_model = etas
    rps_min, rps_max = rps_bounds
    heating = condition.mode == "heating"
    T_crit_K = float(CP.PropsSI("Tcrit", ref))
    cond_side, evap_side = (condition.load, condition.source) if heating else (condition.source, condition.load)

    def saturation(side: HXSide, duty: float, condenser: bool) -> tuple[float, float]:
        approach = duty / side.conductance
        T_sat_K = side.T_in_C + 273.15 + (approach if condenser else -approach)
        return T_sat_K, approach

    def cycle(Q_other: float) -> dict[str, Any]:
        Q_cond, Q_evap = (Q_rated, Q_other) if heating else (Q_other, Q_rated)
        T_cond_K, dT_cond = saturation(cond_side, Q_cond, condenser=True)
        T_evap_K, dT_evap = saturation(evap_side, Q_evap, condenser=False)
        if T_cond_K <= T_evap_K:
            raise ReferenceStateError("condensing temperature not above evaporating temperature")
        if T_cond_K >= T_crit_K:
            raise ReferenceStateError(
                f"condensing temperature {T_cond_K - 273.15:.1f} °C at {condition.standard} is at or above "
                f"the critical temperature of {ref} ({T_crit_K - 273.15:.1f} °C); TMHP cycles are subcritical, "
                "so supply rps_rated (and m_dot_ref_rated) or a rated_condition the cycle can reach"
            )
        # Same margin rule as the models' cycles: superheat/subcool cannot
        # exceed the approach less the minimum HX temperature difference.
        states = calc_ref_state(
            T_evap_K=T_evap_K,
            T_cond_K=T_cond_K,
            refrigerant=ref,
            eta_cmp_isen=1.0,
            mode=condition.mode,
            dT_superheat=min(dT_superheat, max(0.0, dT_evap - dT_hx_min)),
            dT_subcool=min(dT_subcool, max(0.0, dT_cond - dT_hx_min)),
            is_active=True,
        )
        PR = states["P_ref_cmp_out [Pa]"] / states["P_ref_cmp_in [Pa]"]
        h1 = states["h_ref_cmp_in [J/kg]"]
        dh_isen = states["h_ref_cmp_out [J/kg]"] - h1
        h3 = states["h_ref_exp_in [J/kg]"]
        h4 = states["h_ref_exp_out [J/kg]"]
        rho = states["rho_ref_cmp_in [kg/m3]"]

        def duties(rps: float) -> tuple[float, float, float, float, float]:
            eta_vol = _eval_eff(eta_vol_model, PR, rps)
            eta_isen = _eval_eff(eta_isen_model, PR, rps)
            _eval_eff(eta_em_model, PR, rps)
            m_dot = V_cmp_ref * rho * eta_vol * rps
            h2 = h1 + dh_isen / eta_isen
            return m_dot, m_dot * (h2 - h3), m_dot * (h1 - h4), eta_vol, eta_isen

        def residual(rps: float) -> float:
            Q_cond_r, Q_evap_r = duties(rps)[1:3]
            return (Q_cond_r if heating else Q_evap_r) - Q_rated

        rps, converged, clamped = solve_compressor_speed(residual, rps_min, rps_max)
        in_range = converged and clamped is None
        bound = rps
        m_dot, Q_cond_c, Q_evap_c, eta_vol, eta_isen = duties(bound)
        if not in_range:
            # Preserve the outer-balance steering used for infeasible ratings;
            # the final reference state still rejects a capacity outside bounds.
            delivered = Q_cond_c if heating else Q_evap_c
            scale = Q_rated / delivered
            rps = bound * scale
            m_dot, Q_cond_c, Q_evap_c = m_dot * scale, Q_cond_c * scale, Q_evap_c * scale
        return {
            "rps": rps,
            "in_range": in_range,
            "m_dot": m_dot,
            "Q_cond": Q_cond_c,
            "Q_evap": Q_evap_c,
            "PR": PR,
            "eta_vol": eta_vol,
            "eta_isen": eta_isen,
            "T_cond_K": T_cond_K,
            "T_evap_K": T_evap_K,
        }

    def balance(Q_other: float) -> float:
        c = cycle(Q_other)
        return float(c["Q_evap"] if heating else c["Q_cond"]) - Q_other

    # Heating: the evaporator takes Q_rated less the compressor work.
    # Cooling: the condenser rejects Q_rated plus the compressor work.
    lo, hi = (1e-3 * Q_rated, Q_rated) if heating else (Q_rated, 4.0 * Q_rated)
    hi = _evaluable_bound(balance, lo, hi, Q_rated)
    f_lo, f_hi = balance(lo), balance(hi)
    if f_lo * f_hi > 0:
        side = "source" if heating else "source-side condenser"
        raise ReferenceStateError(
            f"{side} heat exchanger cannot close the energy balance at {condition.standard} "
            f"for hp_capacity={Q_rated:g} W (rated UA and reference flow too small)"
        )
    Q_other = brentq(balance, lo, hi, xtol=1e-9 * Q_rated, rtol=1e-12)
    c = cycle(Q_other)

    if not c["in_range"]:
        raise ReferenceStateError(
            f"hp_capacity={Q_rated:g} W at {condition.standard} needs {c['rps']:.3g} rev/s, "
            f"outside rps_min={rps_min:g} .. rps_max={rps_max:g} "
            f"(V_cmp_ref={V_cmp_ref:.4g} m3/rev); supply V_cmp_ref or rps_rated consistent with the machine"
        )
    rps_ref = c["rps"]
    eta_em = _eval_eff(eta_em_model, c["PR"], rps_ref)
    E_cmp_ref = c["Q_cond"] - c["Q_evap"]
    return ReferenceState(
        rps_rated=rps_ref if rps_rated is None else rps_rated,
        m_dot_ref_rated=c["m_dot"],
        condition=condition,
        hp_capacity=Q_rated,
        V_cmp_ref=V_cmp_ref,
        T_evap_sat_C=c["T_evap_K"] - 273.15,
        T_cond_sat_C=c["T_cond_K"] - 273.15,
        pressure_ratio=c["PR"],
        Q_cond=c["Q_cond"],
        Q_evap=c["Q_evap"],
        E_cmp_ref=E_cmp_ref,
        E_cmp=E_cmp_ref / eta_em,
        eta_cmp_isen=c["eta_isen"],
        eta_cmp_vol=c["eta_vol"],
        eta_cmp=eta_em,
        rps_rated_solved=rps_rated is None,
    )


def _evaluable_bound(f: Callable[[float], float], lo: float, hi: float, scale: float) -> float:
    """Shrink ``hi`` towards ``lo`` until ``f`` can be evaluated there.

    A large trial duty can push the other saturation temperature past the
    refrigerant's critical point or property range; that trial is not a
    solution candidate, so the bracket shrinks instead of failing. Only when
    no trial is evaluable is the last reason reported.
    """
    last: ValueError | None = None
    for _ in range(60):
        try:
            f(hi)
            return hi
        except ValueError as exc:
            last = exc
            hi = lo + 0.5 * (hi - lo)
            if hi - lo < 1e-9 * scale:
                break
    if isinstance(last, ReferenceStateError):
        raise last
    raise ReferenceStateError(f"no evaluable refrigerant cycle at the rating condition ({last})")


class ReferenceStateMixin:
    """Shared reference-state initialization for compressor-based models.

    A model sets ``_RATING_FAMILY`` / ``_RATING_DEFAULT_MODE``, implements
    :meth:`_rating_side`, and calls :meth:`_initialize_reference_state` once its
    refrigerant, displacement, rated UA values, reference flows and speed
    limits are known -- and before anything that needs ``m_dot_ref_rated``
    (variable ground-HX UA) is configured.
    """

    _RATING_FAMILY: str
    _RATING_DEFAULT_MODE: Mode

    ref: str
    V_cmp_ref: float
    hp_capacity: float
    dT_superheat: float
    dT_subcool: float
    dT_hx_min: float
    rps_min: float
    rps_max: float

    def _rating_side(self, role: str, mode: str, T_in_C: float) -> HXSide:
        """The model's own heat exchanger on ``role`` at its rated UA and reference flow."""
        raise NotImplementedError

    def _initialize_reference_state(
        self,
        *,
        rps_rated: float | None,
        m_dot_ref_rated: float | None,
        efficiencies: Mapping[str, float | Callable | None],
        rated_condition: Mapping[str, Any] | RatingCondition | None = None,
    ) -> dict[str, float | Callable]:
        """Resolve ``rps_rated``, ``m_dot_ref_rated`` and the compressor efficiencies.

        ``efficiencies`` maps ``eta_cmp_isen`` / ``eta_cmp_vol`` / ``eta_cmp``
        to the caller's input (``None`` = baseline correlation). Explicit
        ``rps_rated`` / ``m_dot_ref_rated`` take precedence over solved values.
        Returns the resolved efficiencies; also sets ``rps_rated``,
        ``m_dot_ref_rated``, ``rated_condition`` and ``reference_state``
        (``None`` when both were supplied and nothing was solved).

        Order: (1) ``V_cmp_ref`` is already resolved by the caller, (2) rating
        condition, (3) speed at ``n* = 1``, (4)-(5) reference cycle and its mass
        flow, (6) efficiencies built at the rated speed.
        """
        if rps_rated is not None and not (math.isfinite(rps_rated) and rps_rated > 0):
            raise ValueError("rps_rated must be positive and finite")
        if not 0 < self.rps_min < self.rps_max or not math.isfinite(self.rps_max):
            raise ValueError("Require finite 0 < rps_min < rps_max")
        condition = rating_condition(
            self._RATING_FAMILY,
            source=lambda mode, T: self._rating_side("source", mode, T),
            load=lambda mode, T: self._rating_side("load", mode, T),
            default_mode=self._RATING_DEFAULT_MODE,
            overrides=rated_condition,
        )

        def resolve(speed: float) -> dict[str, float | Callable]:
            return {
                name: model if model is not None else BASELINE_FACTORIES[name](speed)
                for name, model in efficiencies.items()
            }

        state: ReferenceState | None = None
        if rps_rated is None or m_dot_ref_rated is None:
            # Scalars and baseline correlations are hashable descriptions of the
            # compressor; a caller's callable is not, so it is solved uncached.
            cacheable = not any(callable(model) for model in efficiencies.values())
            cache_key = tuple(sorted(efficiencies.items())) if cacheable else None
            etas = reference_efficiencies(efficiencies, None if rps_rated is None else resolve(rps_rated))
            kwargs: dict[str, Any] = dict(
                ref=self.ref,
                V_cmp_ref=self.V_cmp_ref,
                hp_capacity=self.hp_capacity,
                condition=condition,
                eta_cmp_isen=etas["eta_cmp_isen"],
                eta_cmp_vol=etas["eta_cmp_vol"],
                eta_cmp=etas["eta_cmp"],
                dT_superheat=self.dT_superheat,
                dT_subcool=self.dT_subcool,
                dT_hx_min=self.dT_hx_min,
                rps_min=self.rps_min,
                rps_max=self.rps_max,
                rps_rated=rps_rated,
                cache_key=cache_key,
            )
            if rps_rated is None:
                state = solve_reference_state(**kwargs)
            else:
                # The caller fixed the machine; only m_dot_ref_rated is derived.
                # A rating point it cannot reach leaves m_dot_ref_rated unset
                # (and fails only if variable ground-HX UA asks for it).
                try:
                    state = solve_reference_state(**kwargs)
                except ReferenceStateError as exc:
                    warnings.warn(f"m_dot_ref_rated not derived: {exc}", RuntimeWarning, stacklevel=3)
        self.rated_condition: RatingCondition = condition
        self.reference_state: ReferenceState | None = state
        if rps_rated is None:
            assert state is not None
            rps_rated = state.rps_rated
        self.rps_rated: float = rps_rated
        self.m_dot_ref_rated: float | None = (
            m_dot_ref_rated if m_dot_ref_rated is not None else (state.m_dot_ref_rated if state else None)
        )
        return resolve(self.rps_rated)
