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

    How a family answers a rejected point: the space-conditioning models
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

from .compressor_efficiency import efficiency_extrapolated

__all__ = [
    "check_pr_envelope",
    "PR_BELOW_MIN",
    "PR_ABOVE_MAX",
    "PR_CYCLE_MAX_SINGLE_STAGE",
    "DISCHARGE_TEMP_ABOVE_MAX",
    "DISCHARGE_TEMP_MAX_C",
    "DISCHARGE_TEMP_MAX_DEFAULT_C",
    "BINDING_DISCHARGE",
    "default_discharge_temp_max",
    "resolve_discharge_temp_max",
    "discharge_temp_exceeded",
    "discharge_violation",
    "RPS_MAX_PER_RATED",
    "RPS_MAX_ABSOLUTE",
    "RPS_MAX_MIN_PER_RATED",
    "RATED_SPEED_SEARCH_MAX",
    "default_rps_max",
    "efficiency_extrapolated_at",
    "steady_envelope",
]

#: Reason code: pressure ratio is below ``PR_cycle_min`` (floor -> clamp).
PR_BELOW_MIN = "pr_below_min"
#: Reason code: pressure ratio is above ``PR_cycle_max`` (ceiling -> reject).
PR_ABOVE_MAX = "pr_above_max"
#: Default ``PR_cycle_max`` of every family: the single-stage scroll hardware limit.
PR_CYCLE_MAX_SINGLE_STAGE = 11.0

#: ``failure_reason``: even the smallest duty tried exceeds ``T_dis_max``.
DISCHARGE_TEMP_ABOVE_MAX = "discharge_temp_above_max"
#: ``capacity_binding``: the duty was reduced to stay within ``T_dis_max``.
BINDING_DISCHARGE = "discharge_temperature"

#: Default discharge-temperature limit by refrigerant [degC]; see the module docstring.
DISCHARGE_TEMP_MAX_C: Mapping[str, float] = {"R32": 150.0}
#: Default for every refrigerant not in :data:`DISCHARGE_TEMP_MAX_C` [degC].
DISCHARGE_TEMP_MAX_DEFAULT_C = 135.0

#: ``rps_max`` default: this multiple of the rated speed ...
RPS_MAX_PER_RATED = 2.0
#: ... but no more than this [rev/s] (7200 rpm, the top of variable-speed scroll and rotary ranges) ...
RPS_MAX_ABSOLUTE = 120.0
#: ... and never less than this multiple of the rated speed (catalogue maximum / rated capacity <= 1.52).
RPS_MAX_MIN_PER_RATED = 1.5
#: Upper speed bound for *solving* the rated speed when ``rps_max`` is not given [rev/s].
RATED_SPEED_SEARCH_MAX = 150.0

_DISCHARGE_TOL_K = 1e-9


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
    return any(efficiency_extrapolated(m, pr, rps) for m in models)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Steady-state wrapper
# ---------------------------------------------------------------------------

_F = TypeVar("_F", bound=Callable[..., Any])

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
