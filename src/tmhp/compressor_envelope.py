"""Compressor operating envelope shared by every cycle-resolved heat-pump model.

A real compressor runs inside an envelope that its manufacturer publishes and
its controller enforces. Four limits matter for the single-stage vapour
compression cycle TMHP solves, and this module holds the rule for each so the
five families (ASHP, GSHP, ASHPB, GSHPB, WSHPB) apply them identically.

Pressure ratio (``PR_cycle_min`` / ``PR_cycle_max``)
    The physically primary lift limit is the **pressure ratio**
    ``PR = P_cond / P_evap``, not a fixed temperature lift: the lift needed to
    reach a given ``PR`` maps non-linearly through the saturation curve and
    differs by refrigerant and level, so a fixed minimum lift (e.g. 20 K) over-
    or under-states the true limit; a ``PR`` floor is transferable. Scroll axial
    loading / built-in volume ratio, the oil-feed pressure difference and the
    collapse of isentropic efficiency place the floor near ``PR ~ 1.5``
    (1.3--2.0); below it the cycle is **clamped** onto the floor. The ceiling is
    the single-stage scroll's hardware limit, about ``11:1`` (self-unloading of
    AC scrolls; Cuevas & Lebrun 2009; Bertsch & Groll 2008; Gayeski et al.
    2011); above it the point is **rejected** (:data:`PR_ABOVE_MAX`). Every
    family defaults to :data:`PR_CYCLE_MAX_SINGLE_STAGE`. Higher ratios belong
    to two-stage or vapour-injection machines, which the single-stage cycle
    does not describe. Since the discharge-temperature limit below binds first
    almost everywhere, the ratio ceiling acts as a hardware sanity bound.

Discharge temperature (``T_dis_max``)
    Lubricant breakdown and valve / motor temperature limit the gas leaving the
    compressor. Compressor application guides give the protection setting:
    Danfoss SH scrolls (R410A) "discharge gas temperature must not exceed
    135 degC"; Danfoss PSH (R410A/R454B) start wet injection at 121 degC with a
    135 degC maximum safety value; the external discharge-gas protection of
    Danfoss DSF scrolls (R32, which "leads to higher discharge temperatures
    than R410A") opens at 150 degC at most. Unit controllers act earlier on
    the discharge *pipe* temperature (Daikin room units reduce frequency from
    about 100 degC and stop at 110--120 degC). A point whose computed
    discharge temperature exceeds the limit is **not an operating point**
    (:data:`DISCHARGE_TEMP_ABOVE_MAX`). The defaults
    (:data:`DISCHARGE_TEMP_MAX_C`, :func:`default_discharge_temp_max`) are the
    *protection* values -- R32 150 degC, every other fluid 135 degC -- not the
    controllers' earlier set points, because the cycle model has no shell heat
    loss and puts every compressor loss into the gas (the default
    electro-mechanical efficiency is about 1), so its discharge temperature is
    an upper estimate of the machine's.

    Discharge-temperature control comes first (:func:`discharge_controlled`,
    ``x_suction_min``). A unit whose discharge runs hot does not stop: its
    controller opens the expansion valve, so the suction superheat falls, and
    then lets the suction go wet or injects liquid. The cycle at a candidate
    operating point is therefore re-solved, compressor speed included, first
    with the suction superheat reduced from its value towards 0 K until the
    discharge temperature equals ``T_dis_max``, then -- if 0 K is not enough --
    with a two-phase suction of quality ``x`` lowered towards
    ``x_suction_min`` (default :data:`X_SUCTION_MIN_DEFAULT` = 0.9). Wet
    suction is modelled as a homogeneous two-phase state at the evaporating
    pressure (see :func:`tmhp.refrigerant.calc_ref_state`); the evaporator
    outlet is then two-phase, the swept mass flow follows the homogeneous
    density, and the isentropic work is that of the two-phase state, so the
    cycle energy balance stays closed. The efficiency maps were fitted to dry
    suction, so applying them to wet suction is an approximation. Only a point
    that is still above the limit at ``x_suction_min`` is outside the envelope.
    ``x_suction_min=1`` keeps the superheat stage only; ``None`` switches the
    control off.

    How a family answers a point the control cannot bring inside the limit: the space-conditioning models
    (ASHP, GSHP) reject the candidate inside the operating-point search, so the
    search moves to a smaller lift if one carries the duty, and otherwise the
    existing capacity search returns the largest duty the envelope allows with
    ``capacity_clamped='max'`` and ``capacity_binding='discharge_temperature'``.
    The boilers (fixed requested duty) check the solved operating point;
    ``analyze_steady`` then tries smaller duties (:func:`steady_envelope`) and
    returns the largest one inside the limit the same way, or the off state
    with ``failure_reason='discharge_temp_above_max'`` when even a small duty
    exceeds it. A dynamic boiler step that exceeds the limit is an off step
    with that reason.

Speed (``rps_min`` / ``rps_max``)
    ``rps_min`` is the inverter's floor (15 rev/s). ``rps_max`` defaults to
    :func:`default_rps_max` of the rated speed the model solves at its rating
    point: twice the rated speed, at most :data:`RPS_MAX_ABSOLUTE`, at least
    1.5 times the rated speed. The rated
    speed itself is solved over ``[rps_min, RATED_SPEED_SEARCH_MAX]``.

Efficiency domain
    The default efficiency fits are guarded outside their (PR, N) data
    (:mod:`tmhp.compressor_efficiency`). Steady results carry
    ``efficiency_extrapolated`` so a caller can see a point evaluated outside
    the fitted data. At low speed and high pressure ratio the guard's
    first-order continuation reaches eta_is 0.14; no separate speed floor is
    imposed there because every such point already exceeds the discharge
    limit (85 of 85 running points of the 2026-11-13 sweep, at 191--348 degC).
"""

from __future__ import annotations

import contextlib
import inspect
import io
import math
import warnings
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any, Literal, TypeVar, cast

from scipy.optimize import brentq

from .compressor_efficiency import efficiency_extrapolated

__all__ = [
    "check_pr_envelope",
    "PR_BELOW_MIN",
    "PR_ABOVE_MAX",
    "PR_CYCLE_MAX_SINGLE_STAGE",
    "DISCHARGE_TEMP_ABOVE_MAX",
    "DISCHARGE_TEMP_MAX_C",
    "DISCHARGE_TEMP_MAX_DEFAULT_C",
    "DISCHARGE_TOL_K",
    "BINDING_DISCHARGE",
    "default_discharge_temp_max",
    "resolve_discharge_temp_max",
    "discharge_temp_exceeded",
    "discharge_violation",
    "ETA_ISEN_BELOW_MIN",
    "ETA_ISEN_MIN",
    "envelope_violation",
    "envelope_excess",
    "efficiency_below_floor",
    "RPS_MAX_PER_RATED",
    "RPS_MAX_ABSOLUTE",
    "RPS_MAX_MIN_PER_RATED",
    "RATED_SPEED_SEARCH_MAX",
    "default_rps_max",
    "efficiency_extrapolated_at",
    "steady_envelope",
    "X_SUCTION_MIN_DEFAULT",
    "resolve_x_suction_min",
    "discharge_controlled",
]

#: Reason code: pressure ratio is below ``PR_cycle_min`` (floor -> clamp).
PR_BELOW_MIN = "pr_below_min"
#: Reason code: pressure ratio is above ``PR_cycle_max`` (ceiling -> reject).
PR_ABOVE_MAX = "pr_above_max"
#: Default ``PR_cycle_max`` of every family: the single-stage scroll hardware limit.
PR_CYCLE_MAX_SINGLE_STAGE = 11.0

#: ``failure_reason``: even the smallest duty tried exceeds ``T_dis_max``.
DISCHARGE_TEMP_ABOVE_MAX = "discharge_temp_above_max"
#: ``failure_reason``: the default isentropic-efficiency fit, extrapolated, is below :data:`ETA_ISEN_MIN`.
ETA_ISEN_BELOW_MIN = "eta_isen_below_min"
#: Lowest isentropic efficiency of the default fit accepted outside its data [-].
#:
#: Inside the convex hull of its 3899 data points the default scroll fit never
#: goes below 0.336 (at PR 7.13, 38.6 rev/s), and its lowest value over the
#: whole (PR, N) box is 0.297 (corner PR 7.15, 33.8 rev/s). Below that the
#: value comes only from the guard's first-order continuation towards 15 rev/s
#: (down to 0.14) -- a compressor wasting more than 70 % of its work with no
#: datum behind it. Such a point is outside the envelope. The rule applies only
#: to the shipped fits (which carry a validity domain) and only where they are
#: extrapolated; a scalar or a user model is the caller's statement.
ETA_ISEN_MIN = 0.30
#: ``capacity_binding``: the duty was reduced to stay within ``T_dis_max``.
BINDING_DISCHARGE = "discharge_temperature"

#: Default discharge-temperature limit by refrigerant [degC]; see the module docstring.
DISCHARGE_TEMP_MAX_C: Mapping[str, float] = {"R32": 150.0}
#: Default for every refrigerant not in :data:`DISCHARGE_TEMP_MAX_C` [degC].
DISCHARGE_TEMP_MAX_DEFAULT_C = 135.0

#: Default lowest suction vapour quality the discharge-temperature control may use.
#:
#: Two-phase suction (wet compression) is one of the methods experimentally used
#: to hold the discharge temperature of R32 scroll compressors (Wang et al.,
#: International Compressor Engineering Conference 2014, paper 1595; Yang et
#: al., Int. J. Refrig. 2015), alongside liquid and two-phase injection; lower
#: suction quality lowers the discharge temperature but raises the risk of
#: over-wet compression and liquid slugging. No published lower bound on the
#: suction quality was found: 0.9 is a conservative default without a
#: quantitative basis (at most 10 % liquid by mass, of the order of the
#: liquid-injection ratios used for discharge-temperature control). Override it
#: with the constructor argument ``x_suction_min``.
X_SUCTION_MIN_DEFAULT = 0.9

#: ``rps_max`` default: this multiple of the rated speed ...
RPS_MAX_PER_RATED = 2.0
#: ... but no more than this [rev/s] (7200 rpm, the top of variable-speed scroll and rotary ranges) ...
RPS_MAX_ABSOLUTE = 120.0
#: ... and never less than this multiple of the rated speed (catalogue maximum / rated capacity <= 1.52).
RPS_MAX_MIN_PER_RATED = 1.5
#: Upper speed bound for *solving* the rated speed when ``rps_max`` is not given [rev/s].
RATED_SPEED_SEARCH_MAX = 150.0

#: Tolerance of every discharge-temperature comparison [K]. The control solves
#: T_dis = T_dis_max by root finding, so the check that follows has to accept
#: the root's own residual (brentq: ~1e-3 K); 0.01 K is far below any meaning
#: the limit has.
DISCHARGE_TOL_K = 0.01
_DISCHARGE_TOL_K = DISCHARGE_TOL_K


def check_pr_envelope(pr: float, pr_min: float, pr_max: float) -> str | None:
    """Classify a compressor pressure ratio against the operating envelope.

    Returns ``None`` when ``pr_min <= pr <= pr_max`` (feasible); otherwise
    :data:`PR_BELOW_MIN` or :data:`PR_ABOVE_MAX`.
    """
    if pr < pr_min:
        return PR_BELOW_MIN
    if pr > pr_max:
        return PR_ABOVE_MAX
    return None


def _fluid_name(ref: str) -> str:
    # "HEOS::R32" and "R32" name the same fluid; mixtures keep their spelling.
    return ref.split("::", 1)[1] if ref.upper().startswith("HEOS::") and "[" not in ref else ref


def default_discharge_temp_max(ref: str) -> float:
    """Default discharge-temperature limit for refrigerant ``ref`` [degC]."""
    return float(DISCHARGE_TEMP_MAX_C.get(_fluid_name(ref), DISCHARGE_TEMP_MAX_DEFAULT_C))


def resolve_discharge_temp_max(value: float | Literal["auto"] | None, ref: str) -> float | None:
    """Constructor argument -> limit: ``"auto"`` the refrigerant default, ``None`` no check."""
    if value is None:
        return None
    if isinstance(value, str):
        if value != "auto":
            raise ValueError(f"T_dis_max must be a temperature in degC, 'auto' or None, got {value!r}")
        return default_discharge_temp_max(ref)
    limit = float(value)
    if not math.isfinite(limit):
        raise ValueError("T_dis_max must be finite (use None to switch the check off)")
    return limit


def resolve_x_suction_min(value: float | None) -> float | None:
    """Constructor argument -> lowest suction quality of the control (``None``: no control)."""
    if value is None:
        return None
    x = float(value)
    if not (math.isfinite(x) and 0.0 < x <= 1.0):
        raise ValueError(f"x_suction_min must be a vapour quality in (0, 1] or None, got {value!r}")
    return x


def discharge_temp_exceeded(T_dis_C: object, T_dis_max: float | None) -> bool:
    """Whether a discharge temperature [degC] violates ``T_dis_max``.

    A non-finite discharge temperature (no discharge state: the enthalpy left
    the fluid's equation of state) counts as exceeded. ``T_dis_max=None``
    switches the check off.
    """
    if T_dis_max is None:
        return False
    try:
        T = float(cast(float, T_dis_C))
    except (TypeError, ValueError):
        return True
    return not math.isfinite(T) or T_dis_max + _DISCHARGE_TOL_K < T


def discharge_violation(result: Mapping[str, Any], T_dis_max: float | None) -> str | None:
    """:data:`DISCHARGE_TEMP_ABOVE_MAX` if a solved state exceeds ``T_dis_max``, else ``None``."""
    if discharge_temp_exceeded(result.get("T_ref_cmp_out [°C]"), T_dis_max):
        return DISCHARGE_TEMP_ABOVE_MAX
    return None


def _eta_isen(result: Mapping[str, Any]) -> float:
    for key in ("eta_cmp_isen [-]", "eta_is [-]"):
        if key in result:
            try:
                return float(result[key])
            except (TypeError, ValueError):
                return math.nan
    return math.nan


def efficiency_below_floor(model: Any, result: Mapping[str, Any]) -> bool:
    """The shipped isentropic fit, evaluated outside its data, is below :data:`ETA_ISEN_MIN`."""
    eta_model = getattr(model, "eta_cmp_isen", None)
    if getattr(eta_model, "validity_domain", None) is None:
        return False
    eta = _eta_isen(result)
    if not (math.isfinite(eta) and eta < ETA_ISEN_MIN):
        return False
    return efficiency_extrapolated_at((eta_model,), result)


def envelope_violation(result: Mapping[str, Any], model: Any) -> str | None:
    """Why a solved running state is outside the compressor envelope, or ``None``.

    :data:`DISCHARGE_TEMP_ABOVE_MAX` (after the discharge-temperature control)
    or :data:`ETA_ISEN_BELOW_MIN`.
    """
    reason = discharge_violation(result, getattr(model, "T_dis_max", None))
    if reason is not None:
        return reason
    if efficiency_below_floor(model, result):
        return ETA_ISEN_BELOW_MIN
    return None


def envelope_excess(result: Mapping[str, Any], model: Any) -> float:
    """How far a solved running state is outside the envelope, as a ranking weight [W-equivalent].

    0 inside. 1000 per kelvin of discharge above ``T_dis_max`` (beyond
    :data:`DISCHARGE_TOL_K`; an undefined discharge state counts as very far)
    plus 1e6 per unit of isentropic efficiency below :data:`ETA_ISEN_MIN` where
    the floor applies. Used by searches to rank candidates outside the
    envelope among themselves.
    """
    excess = 0.0
    T_max = getattr(model, "T_dis_max", None)
    if T_max is not None:
        T = _discharge(result)
        if not math.isfinite(T):
            excess += 1.0e9
        elif T_max + DISCHARGE_TOL_K < T:
            excess += 1000.0 * (T - T_max)
    if efficiency_below_floor(model, result):
        excess += 1.0e6 * (ETA_ISEN_MIN - _eta_isen(result))
    return excess


def default_rps_max(rps_rated: float) -> float:
    """Default upper speed bound from the rated speed [rev/s].

    ``max(min(2 * rps_rated, 120), 1.5 * rps_rated)``. Inverter heat pumps
    reach their published maximum capacity at 1.0--1.52 times the nameplate
    (the adopted validation catalogues); the model needs at most 1.66 times
    the rated speed for those points (Daikin RXM20A heating). Twice the rated
    speed keeps them all with margin while refusing the 2.5--5.9 times
    nameplate compressor capacity the former fixed 150 rev/s allowed, and
    120 rev/s (7200 rpm) is the top of the variable-speed scroll and rotary
    ranges. A machine whose *rated* speed is already high -- a
    low-volumetric-capacity fluid with the refrigerant-independent default
    displacement, e.g. R134a at 90 rev/s (#86) -- keeps at least
    :data:`RPS_MAX_MIN_PER_RATED` times its rated speed, so it can still reach
    the catalogue maximum-to-rated ratio; such a machine should be given its
    real ``V_cmp_ref``.
    """
    rated = float(rps_rated)
    return max(min(RPS_MAX_PER_RATED * rated, RPS_MAX_ABSOLUTE), RPS_MAX_MIN_PER_RATED * rated)


def efficiency_extrapolated_at(models: tuple[object, ...], result: Mapping[str, Any]) -> bool:
    """Whether any default efficiency fit is evaluated outside its data at this state.

    Pressure ratio and speed are read from the result row
    (``P_ref_cmp_out / P_ref_cmp_in`` and ``cmp_rpm / 60``). False for a state
    that does not run, and for scalar or user-supplied efficiencies.
    """
    try:
        p_in = float(result["P_ref_cmp_in [Pa]"])
        p_out = float(result["P_ref_cmp_out [Pa]"])
        rps = float(result["cmp_rpm [rpm]"]) / 60.0
    except (KeyError, TypeError, ValueError):
        return False
    if not (math.isfinite(p_in) and math.isfinite(p_out) and math.isfinite(rps) and p_in > 0 and rps > 0):
        return False
    pr = p_out / p_in
    return any(_outside_domain(m, pr, rps) for m in models)


#: Relative margin inside which a point on the edge of a fit's (PR, N) box
#: counts as inside. The pressure ratio and speed are read back from the
#: result row (``P_out / P_in``, ``cmp_rpm / 60``), so a point solved exactly
#: on an edge -- the pressure-ratio floor sits next to the fit's PR 1.5016, and
#: a speed bound can coincide with 33.8 or 72.5 rev/s -- comes back a few ulp
#: either side, and the flag (and the efficiency floor that uses it) would
#: differ between platforms. 1e-6 is the relative tolerance of the step-kernel
#: goldens and far below any physical meaning of the box.
_DOMAIN_RTOL = 1e-6


def _outside_domain(model: object, pressure_ratio: float, rps: float) -> bool:
    domain = getattr(model, "validity_domain", None)
    if domain is None or not efficiency_extrapolated(model, pressure_ratio, rps):  # type: ignore[arg-type]
        return False
    tol = _DOMAIN_RTOL
    return not (
        domain.pr_min * (1 - tol) <= pressure_ratio <= domain.pr_max * (1 + tol)
        and domain.rps_min * (1 - tol) <= rps <= domain.rps_max * (1 + tol)
    )


_F = TypeVar("_F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Discharge-temperature control (wraps a family's cycle evaluation)
# ---------------------------------------------------------------------------

#: Result keys the control adds to every running cycle state (all five families).
SUPERHEAT_KEY = "dT_ref_superheat [K]"
QUALITY_KEY = "x_ref_cmp_in [-]"
CONTROL_KEY = "discharge_control"
_T_DIS_KEY = "T_ref_cmp_out [°C]"
_SUPERHEAT_XTOL_K = 1e-3
_QUALITY_XTOL = 1e-5


def _state_running(result: object) -> bool:
    return isinstance(result, dict) and bool(result.get("hp_is_on")) and _T_DIS_KEY in result


def _discharge(result: Mapping[str, Any]) -> float:
    try:
        value = float(result[_T_DIS_KEY])
    except (KeyError, TypeError, ValueError):
        return math.nan
    return value if math.isfinite(value) else math.inf


def _superheat(result: Mapping[str, Any]) -> float:
    try:
        return max(0.0, float(result["T_ref_cmp_in [°C]"]) - float(result["T_ref_evap_sat [°C]"]))
    except (KeyError, TypeError, ValueError):
        return math.nan


def discharge_controlled(reject: bool) -> Callable[[_F], _F]:
    """Wrap a family's ``_calc_state`` with the discharge-temperature control.

    The wrapped method evaluates one candidate operating point; it reads the
    suction superheat from ``self.dT_superheat`` (capped by the evaporator
    approach where the family caps it) and the suction quality from
    ``self._x_suction``. When the running state discharges above
    ``self.T_dis_max``, the same candidate is re-evaluated with the superheat
    reduced (root of ``T_dis(superheat) = T_dis_max`` on ``[0, superheat]``)
    and, if 0 K is not enough, with the quality reduced (root on
    ``[x_suction_min, 1]``). Every running state gets
    :data:`SUPERHEAT_KEY`, :data:`QUALITY_KEY` and :data:`CONTROL_KEY`.

    A state still above the limit is returned as is when ``reject`` is false
    (the boilers check the solved operating point and reduce the duty), and is
    rejected -- ``None``, with ``self._last_pr_event`` naming the discharge
    limit -- when ``reject`` is true (ASHP, GSHP: a candidate inside the search).
    """

    def decorate(function: _F) -> _F:
        @wraps(function)
        def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
            result = function(self, *args, **kwargs)
            if reject and _state_running(result) and efficiency_below_floor(self, result):
                self._last_pr_event = (ETA_ISEN_BELOW_MIN, _eta_isen(result), ETA_ISEN_MIN)
                return None
            T_max = self.T_dis_max
            if T_max is None or not _state_running(result):
                # No limit: the cycle state is returned untouched (the steady
                # wrapper still reports superheat and quality).
                return result
            superheat0 = _superheat(result)
            result.update({SUPERHEAT_KEY: superheat0, QUALITY_KEY: float(self._x_suction), CONTROL_KEY: False})
            if _discharge(result) <= T_max + _DISCHARGE_TOL_K:
                return result

            # A caller's diagnostics dict (ASHPB ``hx_diag``) must describe the
            # state returned: each re-evaluation fills a fresh one, copied back
            # only for the state the control keeps.
            diag_out = kwargs.get("hx_diag") if isinstance(kwargs.get("hx_diag"), dict) else None
            diags: dict[int, dict] = {}

            def at(superheat: float, quality: float) -> dict | None:
                saved = (self.dT_superheat, self._x_suction)
                self.dT_superheat, self._x_suction = superheat, quality
                call_kwargs = dict(kwargs)
                fresh: dict = {}
                if diag_out is not None:
                    call_kwargs["hx_diag"] = fresh
                try:
                    state = function(self, *args, **call_kwargs)
                except ValueError:
                    state = None
                finally:
                    self.dT_superheat, self._x_suction = saved
                if not isinstance(state, dict) or not _state_running(state) or not math.isfinite(_discharge(state)):
                    return None
                state.update({SUPERHEAT_KEY: _superheat(state), QUALITY_KEY: quality, CONTROL_KEY: True})
                diags[id(state)] = fresh
                return state

            controlled = _control(at, superheat0, T_max, self.x_suction_min)
            if controlled is not None and diag_out is not None:
                diag_out.clear()
                diag_out.update(diags.get(id(controlled), {}))
            if controlled is not None:
                if reject and efficiency_below_floor(self, controlled):
                    self._last_pr_event = (ETA_ISEN_BELOW_MIN, _eta_isen(controlled), ETA_ISEN_MIN)
                    return None
                return controlled
            if reject:
                self._last_pr_event = (DISCHARGE_TEMP_ABOVE_MAX, _discharge(result), float(T_max))
                return None
            return result

        return cast(_F, wrapped)

    return decorate


def _control(
    at: Callable[[float, float], dict | None], superheat0: float, T_max: float, x_min: float | None
) -> dict | None:
    """The controlled state at the discharge limit, or ``None`` when the control cannot reach it."""
    if x_min is None or not math.isfinite(superheat0):
        return None

    def excess(superheat: float, quality: float) -> float:
        state = at(superheat, quality)
        return math.nan if state is None else _discharge(state) - T_max

    dry = excess(0.0, 1.0)
    if not math.isfinite(dry):
        return None
    if dry <= 0.0:
        if superheat0 <= _SUPERHEAT_XTOL_K:
            return at(0.0, 1.0)
        try:
            root = brentq(lambda sh: excess(sh, 1.0), 0.0, superheat0, xtol=_SUPERHEAT_XTOL_K)
        except (ValueError, RuntimeError):
            return at(0.0, 1.0)
        # Take the bracket side inside the limit.
        state = at(root, 1.0)
        if state is None or _discharge(state) > T_max + DISCHARGE_TOL_K:
            state = at(max(0.0, root - _SUPERHEAT_XTOL_K), 1.0)
        return state
    if x_min >= 1.0:
        return None
    wet = excess(0.0, x_min)
    if not (math.isfinite(wet) and wet <= 0.0):
        return None
    try:
        root = brentq(lambda x: excess(0.0, x), x_min, 1.0, xtol=_QUALITY_XTOL)
    except (ValueError, RuntimeError):
        return at(0.0, x_min)
    state = at(0.0, root)
    if state is None or _discharge(state) > T_max + DISCHARGE_TOL_K:
        state = at(0.0, max(x_min, root - _QUALITY_XTOL))
    return state


# ---------------------------------------------------------------------------
# Steady-state wrapper
# ---------------------------------------------------------------------------

#: Fractions of the request tried, from the top, when the request exceeds ``T_dis_max``.
_REDUCTION_LADDER = (0.8, 0.6, 0.4, 0.25, 0.1)
#: Bisection stops once the bracket is this narrow relative to the request.
_REDUCTION_REL_TOL = 0.01
#: Request keys the boilers report, replaced by the caller's request on a reduced point.
_REQUEST_KEYS = ("Q_ref_tank_request [W]", "Q_tank_load [W]")


def _running(result: Mapping[str, Any]) -> bool:
    return bool(result.get("hp_is_on", False)) and result.get("failure_reason", "none") == "none"


def _delivered(result: Mapping[str, Any], key: str) -> float:
    try:
        return abs(float(result.get(key, math.nan)))
    except (TypeError, ValueError):
        return math.nan


def _annotate(model: Any, result: dict) -> None:
    models = tuple(getattr(model, name, None) for name in ("eta_cmp_isen", "eta_cmp_vol", "eta_cmp"))
    result["efficiency_extrapolated"] = bool(_running(result) and efficiency_extrapolated_at(models, result))
    # Suction state and control flag on every steady result of every family.
    running = _running(result) and _T_DIS_KEY in result
    result.setdefault(SUPERHEAT_KEY, _superheat(result) if running else math.nan)
    result.setdefault(QUALITY_KEY, 1.0 if running else math.nan)
    result.setdefault(CONTROL_KEY, False)
    if _running(result) and result.get("capacity_clamped") == "max" and "capacity_binding" not in result:
        # The boilers reach their ceiling only through the speed bound.
        result["capacity_binding"] = "compressor"


def _reduce_to_envelope(solve: Callable[[float], dict], request: float, delivered_key: str) -> dict | None:
    """Largest duty below ``request`` whose solved point is a running point.

    A ladder of smaller duties finds the highest one that runs; a bisection
    between it and the rung above closes on the boundary. Returns ``None``
    when no rung runs. The discharge temperature is not monotone in the duty
    (at low speed the efficiency falls), so this is the boundary nearest the
    request, not a global search.
    """
    hi = request
    lo_q, lo_r = 0.0, None
    for fraction in _REDUCTION_LADDER:
        q = fraction * request
        r = solve(q)
        if _running(r):
            lo_q, lo_r = q, r
            break
        hi = q
    if lo_r is None:
        return None
    while hi - lo_q > _REDUCTION_REL_TOL * request:
        mid = 0.5 * (lo_q + hi)
        r = solve(mid)
        if _running(r):
            lo_q, lo_r = mid, r
        else:
            hi = mid
    delivered = _delivered(lo_r, delivered_key)
    if not (math.isfinite(delivered) and delivered < request):
        return None
    return lo_r


def steady_envelope(duty_arg: str | None = None, delivered_key: str = "Q_ref_tank [W]") -> Callable[[_F], _F]:
    """Decorate an ``analyze_steady`` with the envelope's steady-state reporting.

    Every result gains ``efficiency_extrapolated``. With ``duty_arg`` (the
    boilers, whose requested duty is fixed), a request rejected with
    :data:`DISCHARGE_TEMP_ABOVE_MAX` is retried at smaller duties; the largest
    one inside the limit is returned with ``capacity_clamped='max'``,
    ``capacity_binding='discharge_temperature'``, ``Q_max [W]`` and
    ``Q_request [W]`` (the request keys of the family hold the caller's
    request). If no smaller duty runs, the original off-state result stands.
    The trial solves run silently.
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
            result = function(*bound.args, **bound.kwargs)
            if not isinstance(result, dict):
                return result
            request = float(bound.arguments[duty_arg]) if duty_arg is not None else 0.0
            if (
                duty_arg is not None
                and request > 0
                and math.isfinite(request)
                and result.get("failure_reason") == DISCHARGE_TEMP_ABOVE_MAX
            ):

                def solve(q: float) -> dict:
                    arguments = dict(bound.arguments)
                    arguments[duty_arg] = q
                    trial = inspect.BoundArguments(signature, arguments)  # type: ignore[arg-type]
                    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
                        warnings.simplefilter("ignore")
                        out = function(*trial.args, **trial.kwargs)
                    return out if isinstance(out, dict) else {}

                reduced = _reduce_to_envelope(solve, request, delivered_key)
                if reduced is not None:
                    result = reduced
                    result.update(
                        {
                            "capacity_clamped": "max",
                            "capacity_binding": BINDING_DISCHARGE,
                            "Q_max [W]": _delivered(reduced, delivered_key),
                            "Q_request [W]": request,
                        }
                    )
                    for key in _REQUEST_KEYS:
                        if key in result:
                            result[key] = request
                    if "PLR_request [-]" in result and getattr(self, "hp_capacity", 0) > 0:
                        result["PLR_request [-]"] = request / self.hp_capacity
            _annotate(self, result)
            if want_dict:
                return result
            import pandas as pd

            return pd.DataFrame([result])

        return cast(_F, wrapped)

    return decorate
