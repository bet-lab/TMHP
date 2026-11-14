"""Telling the caller that the machine could not do what it was asked.

A heat pump model that is asked for a point outside the machine's operating
range answers correctly -- the row is the off state with a ``failure_reason``,
or a running row that delivers less than the request (``capacity_clamped ==
"max"``) -- but an answer in a result key is easy to miss. This module turns
those answers into one kind of warning, :class:`OperatingLimitWarning`, worded
for the person who made the call:

- **Steady calls** (:func:`steady_notice`, around every ``analyze_steady``):
  one warning for the *final* result of a call whose compressor was asked to
  run and either does not run or delivers less than asked. It names the
  machine and the call, what was requested and what is delivered, the limit in
  words with its value, and what removes it. The trial solves a call makes on
  the way (duty reduction, frost mean, smallest-duty search, coupled solvers)
  never warn.
- **Dynamic runs** (:func:`dynamic_notice`, around every ``analyze_dynamic``):
  nothing per step; one warning at the end of the run with the number of
  steps the compressor was called but did not run (by reason, with the first
  time), the steps it ran below the request, and what that did to the service
  (tank temperature and hot water, or unmet load). The same totals are in
  ``df.attrs["operating_limits"]`` (:func:`summarize_operating_limits`).
- **One table** (:data:`LIMITS`): what each ``failure_reason`` /
  ``capacity_binding`` means and what to do about it. The warnings and the
  documentation page *failure_reason semantics* both take their wording from
  it.

**Repetition.** A sweep of thousands of points must not print thousands of
lines, so a steady warning is issued **once per model and limit**: the first
point a model is stopped by ``discharge_temp_above_max`` is reported in full, later ones are
not (:func:`reset_notices` starts over). Nothing is lost -- every row carries
``failure_reason``, ``capacity_clamped`` and ``capacity_binding`` -- and a
caller who wants every point reported says so with the standard filter::

    warnings.simplefilter("always", tmhp.OperatingLimitWarning)   # every point
    warnings.simplefilter("error", tmhp.OperatingLimitWarning)    # stop at the first
    warnings.simplefilter("ignore", tmhp.OperatingLimitWarning)   # none

Python's own once-per-location memory is not relied on: it is cleared every
time any code enters ``warnings.catch_warnings()``, which the solvers (and
NumPy, SciPy, pandas) do all the time.
"""

from __future__ import annotations

import contextlib
import contextvars
import inspect
import math
import os
import sys
import warnings
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, TypeVar, cast

__all__ = [
    "LIMITS",
    "Limit",
    "OperatingLimitWarning",
    "dynamic_notice",
    "limit_for",
    "note_refusal",
    "reset_notices",
    "silenced",
    "steady_notice",
    "summarize_operating_limits",
    "warn_if_operating_limited",
]


class OperatingLimitWarning(UserWarning):
    """The machine was asked for something it does not deliver.

    Issued when a compressor that was called does not run (the result is the
    off state with a ``failure_reason``), runs below the requested duty
    (``capacity_clamped == "max"``), or -- at construction -- when a set point
    lies above what the compressor envelope reaches.
    :class:`tmhp.dhw.DHWUnmetWarning` (hot water not delivered at the set
    point) is a subclass, so one filter on this class covers them all.
    """


# ---------------------------------------------------------------------------
# What each limit means and what to do about it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Limit:
    """One operating limit, in words.

    ``reason`` is the ``failure_reason`` of a point the limit stops and
    ``binding`` the ``capacity_binding`` of a point it only reduces (either may
    be ``None``). ``meaning`` completes "Limit: ..." and ``remedy`` "What to
    do: ..." in a warning; the documentation prints the same two sentences.
    """

    reason: str | None
    binding: str | None
    meaning: str
    remedy: str


_LIFT = "a lower tank or supply temperature, or a warmer source"

LIMITS: tuple[Limit, ...] = (
    Limit(
        "discharge_temp_above_max",
        "discharge_temperature",
        "the compressor discharge temperature is above its protection limit T_dis_max, even with the suction "
        "superheat taken to 0 K and wet suction",
        f"Reduce the temperature lift ({_LIFT}), or use a refrigerant that discharges cooler (R290, R134a) or the "
        "efficiency map of the actual compressor. T_dis_max=<°C> sets the limit of another compressor.",
    ),
    Limit(
        "condensing_temp_above_max",
        "condensing_temperature",
        "the condensing temperature is above T_cond_max, the top of the compressor envelope",
        "Lower the temperature the heat is delivered at (the tank set points of a boiler), or use a refrigerant "
        "with a higher limit (R290, R134a). T_cond_max=<°C> sets the limit of another compressor.",
    ),
    Limit(
        "pr_above_max",
        "pressure_ratio",
        "the compressor pressure ratio is above PR_cycle_max, the single-stage limit",
        f"Reduce the temperature lift ({_LIFT}). PR_cycle_max=<ratio> sets the limit of another compressor.",
    ),
    Limit(
        "eta_isen_below_min",
        "compressor_efficiency",
        "the shipped compressor efficiency map, read outside its data, is below its floor (isentropic efficiency 0.30)",
        f"Reduce the temperature lift ({_LIFT}), or give the efficiency map of the actual compressor (eta_cmp_isen).",
    ),
    Limit(
        "source_fluid_freezing",
        "source_fluid_freezing",
        "the source-loop liquid would run below its operating limit, its freezing point plus 3 K",
        "Fill the loop with an antifreeze mixture, or a stronger one (source_fluid='MEG-30%'), or take less heat "
        "from the source.",
    ),
    Limit(
        "hx_not_converged",
        None,
        "a heat exchanger cannot carry the duty inside its flow range and the approach temperatures the search admits",
        "Reduce the request, or enlarge the heat exchanger that is short (its UA or its largest flow); "
        "failure_detail names the fan limit on the air-source boiler.",
    ),
    Limit(
        None,
        "outdoor_coil",
        "the outdoor coil is short of the evaporator duty at the fan's largest airflow",
        "Reduce the request, or give the outdoor coil and fan of the actual unit (UA_ou_rated, dV_fan_a_max).",
    ),
    Limit(
        None,
        "source_heat_exchanger",
        "the source heat exchanger cannot absorb the evaporator duty at the approach temperatures the search admits",
        "Reduce the request, raise the source temperature or flow, or enlarge the source heat exchanger.",
    ),
    Limit(
        None,
        "cycle",
        "a coil is at its largest airflow and the widest approach temperature the search admits",
        "Reduce the request, or size the machine for it (hp_capacity scales the coils and the compressor).",
    ),
    Limit(
        "compressor_max_speed",
        "compressor",
        "the compressor is at its maximum speed (rps_max)",
        "Size the machine for the load (hp_capacity), or reduce the request.",
    ),
    Limit(
        "cycle_invalid",
        None,
        "no refrigerant cycle closes at this condition (for example a condensing temperature at or above the "
        "critical point), or the solved cycle is not an operating point",
        "Check the temperatures against the refrigerant's range and reduce the lift or the request; "
        "failure_detail names the check where there is one.",
    ),
    Limit(
        "t_cond_below_t_in",
        None,
        "the condensing temperature is at or below the water entering the condenser, so no heat can flow to the water",
        "Check the tank and water-loop temperatures given for this point.",
    ),
    Limit(
        "optimizer_failed",
        None,
        "the operating-point search did not settle: a numerical failure, not a limit of the machine",
        "Treat the point as unsolved (a neighbouring condition usually solves), and report a condition that fails "
        "repeatedly.",
    ),
    Limit(
        "pressure_ratio_limit",
        None,
        "the cycle is invalid and its pressure ratio is at PR_cycle_min or PR_cycle_max (ground-coupled solve)",
        f"Reduce the temperature lift ({_LIFT}).",
    ),
    Limit(
        "compressor_min_speed",
        None,
        "the request is below what the compressor delivers at its minimum speed, and on/off cycling is switched "
        "off (Cd_cycling=None)",
        "Leave on/off cycling on (the default Cd_cycling), or lower rps_min.",
    ),
    Limit(
        "compressor_capacity_limit",
        None,
        "the delivered duty differs from the request for another reason than the compressor's speed limits "
        "(ground-coupled solve)",
        "Reduce the request, or check the compressor's speed range against it.",
    ),
    Limit(
        "compressor_speed_limit",
        None,
        "the compressor-speed solve did not converge (ground-coupled solve)",
        "Treat the point as unsolved (a neighbouring condition usually solves), and report a condition that fails "
        "repeatedly.",
    ),
    Limit(
        "load_hx_capacity_insufficient",
        None,
        "the load-side heat exchanger (indoor coil or tank condenser) cannot transfer the duty",
        "Reduce the request, or enlarge the load-side heat exchanger.",
    ),
    Limit(
        "ground_hx_capacity_insufficient",
        None,
        "the ground heat exchanger cannot supply the evaporator duty at any approach temperature the search admits",
        "Reduce the request, or enlarge the borefield or its flow.",
    ),
)
"""Every operating limit a result can name, with its meaning and remedy."""

_BY_REASON: dict[str, Limit] = {limit.reason: limit for limit in LIMITS if limit.reason is not None}
_BY_BINDING: dict[str, Limit] = {limit.binding: limit for limit in LIMITS if limit.binding is not None}

_DOCS = "see 'failure_reason semantics' in the documentation"


def limit_for(reason: str | None = None, binding: str | None = None) -> Limit:
    """The :class:`Limit` behind a ``failure_reason`` or a ``capacity_binding``.

    A value the table does not know (a reason of a user's own solver, say)
    gets a generic entry that names it and points at the documentation.
    """
    if binding is not None and binding in _BY_BINDING:
        return _BY_BINDING[binding]
    if reason is not None and reason in _BY_REASON:
        return _BY_REASON[reason]
    name = f"capacity_binding={binding!r}" if binding is not None else f"failure_reason={reason!r}"
    return Limit(reason, binding, f"{name} ({_DOCS})", f"Reduce the request or the temperature lift; {_DOCS}.")


def _numbers(model: Any, limit: Limit, note: _Note | None, where: str = "at this point") -> str:
    """The limit's value on this machine, and the refused value (``where`` it was found) if the solve left one."""
    here = note.value if note is not None and note.value is not None and math.isfinite(note.value) else None
    key = limit.reason or limit.binding
    parts: list[str] = []

    def attribute(name: str) -> float | None:
        value = getattr(model, name, None)
        try:
            return float(value) if value is not None and math.isfinite(float(value)) else None
        except (TypeError, ValueError):
            return None

    if key == "discharge_temp_above_max":
        bound = attribute("T_dis_max")
        if bound is not None:
            parts.append(f"T_dis_max = {bound:g} °C")
        if here is not None:
            parts.append(f"{here:.0f} °C {where}")
    elif key == "condensing_temp_above_max":
        bound = attribute("T_cond_max")
        if bound is not None:
            parts.append(f"T_cond_max = {bound:g} °C for {getattr(model, 'ref', 'this refrigerant')}")
        if here is not None:
            parts.append(f"{here:.1f} °C {where}")
    elif key in ("pr_above_max", "pressure_ratio_limit"):
        bound = attribute("PR_cycle_max")
        if bound is not None:
            parts.append(f"PR_cycle_max = {bound:g}")
        if here is not None:
            parts.append(f"{here:.1f} {where}")
    elif key == "eta_isen_below_min":
        if here is not None:
            parts.append(f"{here:.2f} {where}")
    elif key == "source_fluid_freezing":
        fluid = getattr(model, "source_fluid", None)
        name, bound = getattr(fluid, "name", None), getattr(fluid, "limit_C", None)
        if name is not None and bound is not None:
            parts.append(f"{float(bound):.1f} °C for {name}")
        if here is not None:
            parts.append(f"{here:.1f} °C {where}")
    elif key == "compressor_max_speed":
        bound = attribute("rps_max")
        if bound is not None:
            parts.append(f"rps_max = {bound:.1f} rev/s")
    return f" ({'; '.join(parts)})" if parts else ""


# ---------------------------------------------------------------------------
# Scope: which call owns the warning
# ---------------------------------------------------------------------------


@dataclass
class _Note:
    """What a solve that refused a point knew about it."""

    reason: str
    duty: float
    value: float | None
    detail: str | None


@dataclass
class _Scope:
    notes: list[_Note] = field(default_factory=list)


_SCOPE: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar("tmhp_operating_limit_scope", default=None)


@contextlib.contextmanager
def silenced() -> Iterator[None]:
    """No operating-limit warning from the ``analyze_steady`` calls made inside the block.

    For code that calls a model's ``analyze_steady`` many times as a step of
    its own calculation and reports the outcome itself (a dynamic run, a
    search). The rows are unchanged.
    """
    if _SCOPE.get() is not None:
        yield
        return
    token = _SCOPE.set(_Scope())
    try:
        yield
    finally:
        _SCOPE.reset(token)


def note_refusal(reason: str, duty: float = math.nan, value: float | None = None, detail: str | None = None) -> None:
    """Leave what a solve knows about the point it refuses for the warning of the call it belongs to.

    Called by a model where it turns a request into the off state: ``reason``
    is the ``failure_reason``, ``duty`` the duty that was solved [W], and
    ``value`` the quantity that broke the limit at that duty (a discharge
    temperature, a pressure ratio). It never warns: the call that owns the
    result (:func:`steady_notice`) decides whether the refusal is what the
    caller gets, and words it.
    """
    scope = _SCOPE.get()
    if scope is None:
        return
    try:
        number = None if value is None else float(value)
    except (TypeError, ValueError):
        number = None
    scope.notes.append(_Note(str(reason), abs(float(duty)), number, detail))


def _note_for(notes: list[_Note], reason: str) -> _Note | None:
    """The note of the largest duty refused for ``reason`` (the request, or the frost-free duty that averages to it)."""
    matching = [note for note in notes if note.reason == reason]
    if not matching:
        return None
    return max(matching, key=lambda note: note.duty if math.isfinite(note.duty) else -1.0)


# ---------------------------------------------------------------------------
# Issuing: where the warning points, and how often
# ---------------------------------------------------------------------------

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__)) + os.sep
_SEEN_ATTRIBUTE = "_operating_limit_notified"
_ONCE = (
    " (Reported once per model and limit: the rows carry it at every point, and "
    "warnings.simplefilter('always', tmhp.OperatingLimitWarning) reports each.)"
)


def _caller_frame() -> Any:
    """The innermost frame outside this package: the line the warning is about."""
    frame = sys._getframe(1)
    outermost = frame
    while frame is not None:
        outermost = frame
        filename = os.path.abspath(frame.f_code.co_filename)
        if not filename.startswith(_PACKAGE_DIR):
            return frame
        frame = frame.f_back
    return outermost


def _filter_action(category: type[Warning], text: str, module: str, lineno: int) -> str:
    """The action the active warning filters give this warning (``"default"`` when none matches)."""
    get_filters = getattr(warnings, "_get_filters", None)  # Python 3.14: includes the context's filters
    filters = get_filters() if callable(get_filters) else warnings.filters
    for action, message, filter_category, filter_module, filter_lineno in filters:
        if (
            (message is None or message.match(text))
            and issubclass(category, filter_category)
            and (filter_module is None or filter_module.match(module))
            and (filter_lineno == 0 or filter_lineno == lineno)
        ):
            return str(action)
    return str(getattr(warnings, "defaultaction", "default"))


def _issue(
    text: str, category: type[Warning] = OperatingLimitWarning, *, model: Any = None, key: object = None
) -> None:
    """Warn at the caller's line; with ``model`` and ``key``, once per model and key unless every one is asked for.

    The filters decide: ``"ignore"`` drops it, ``"always"`` and ``"error"``
    get every occurrence, and anything else (the default) gets the first
    occurrence of ``key`` on ``model``, with a sentence saying so.
    """
    frame = _caller_frame()
    module_globals = frame.f_globals
    module = str(module_globals.get("__name__", "<unknown>"))
    lineno = frame.f_lineno
    action = _filter_action(category, text, module, lineno)
    if action == "ignore":
        return
    if model is not None and key is not None and action not in ("always", "all", "error"):
        try:
            seen = model.__dict__.setdefault(_SEEN_ATTRIBUTE, set())
        except AttributeError:
            seen = None
        if seen is not None:
            if key in seen:
                return
            seen.add(key)
            text += _ONCE
    warnings.warn_explicit(
        text,
        category,
        frame.f_code.co_filename,
        lineno,
        module=module,
        registry=module_globals.setdefault("__warningregistry__", {}),
    )


def reset_notices(model: Any) -> None:
    """Forget which limits ``model`` has already been reported for: the next occurrence warns again."""
    with contextlib.suppress(AttributeError):
        model.__dict__.pop(_SEEN_ATTRIBUTE, None)


# ---------------------------------------------------------------------------
# Steady calls
# ---------------------------------------------------------------------------

_F = TypeVar("_F", bound=Callable[..., Any])

#: A running row delivers "less than asked" when it is short by more than this share of the request.
SHORTFALL_REL_TOL: float = 1e-3

_NOT_ECHOED = frozenset({"self", "return_dict", "postprocess", "verbose"})


def _row_of(result: Any) -> Mapping[str, Any] | None:
    """The result row of an ``analyze_steady`` return value (a dict, or a one-row frame)."""
    if isinstance(result, Mapping):
        return result if result else None
    columns = getattr(result, "columns", None)
    if columns is not None and len(result) == 1:
        return cast("Mapping[str, Any]", result.iloc[0].to_dict())
    return None


def _number(row: Mapping[str, Any], key: str) -> float:
    try:
        return float(row.get(key, math.nan))
    except (TypeError, ValueError):
        return math.nan


def _text(value: Any) -> str | None:
    """A reason or binding as a string (``None`` for a missing value, NaN included)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return str(value)


def _echo(model: Any, method: str, arguments: Mapping[str, Any]) -> str:
    """``Class.method(name=value, ...)`` with the numbers the caller gave."""
    shown = []
    for name, value in arguments.items():
        if name in _NOT_ECHOED or value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(value):
            shown.append(f"{name}={value:g}")
    return f"{type(model).__name__}.{method}({', '.join(shown)})"


def steady_message(
    model: Any,
    row: Mapping[str, Any],
    who: str,
    delivered_key: str,
    notes: list[_Note] | None = None,
) -> tuple[str, object] | None:
    """The warning text of a steady result, and the key it is reported once under; ``None`` if there is nothing to say.

    Nothing is said about a row whose compressor was not asked to run
    (``Q_request [W]`` zero or absent), a row that meets its request, or an
    on/off mean (``capacity_clamped == "min"``): those are normal operation.
    """
    request = abs(_number(row, "Q_request [W]"))
    if not (math.isfinite(request) and request > 0.0):
        return None
    reason = _text(row.get("failure_reason")) or "none"
    running = bool(row.get("hp_is_on", False))
    delivered = abs(_number(row, delivered_key))
    delivered = delivered if math.isfinite(delivered) else 0.0
    service = ""
    if delivered_key == "Q_ref_iu [W]":  # a space load is signed: heating below zero
        service = " of heating" if _number(row, "Q_request [W]") < 0 else " of cooling"

    if reason != "none":
        limit = limit_for(reason=reason)
        note = _note_for(notes or [], reason)
        detail = _text(row.get("failure_detail")) or (note.detail if note is not None else None)
        named = f"failure_reason={reason!r}" + (f", failure_detail={detail!r}" if detail else "")
        if running:
            outcome = (
                f"no operating point was found for the {request:.0f} W{service} requested; the row is the state the "
                f"search ended on (converged=False, {named}): treat its numbers as approximate"
            )
        else:
            outcome = (
                f"the compressor does not run at this point, so the result is the off state: 0 W of the "
                f"{request:.0f} W{service} requested ({named})"
            )
        text = f"{who}: {outcome}. Limit: {limit.meaning}{_numbers(model, limit, note)}. What to do: {limit.remedy}"
        return text, ("off", reason)

    if not running or row.get("capacity_clamped") != "max" or not delivered < request * (1.0 - SHORTFALL_REL_TOL):
        return None
    binding = _text(row.get("capacity_binding"))
    limit = limit_for(binding=binding) if binding is not None else limit_for(binding="compressor")
    note = _note_for(notes or [], limit.reason) if limit.reason is not None else None
    named = "capacity_clamped='max'" + (f", capacity_binding={binding!r}" if binding else "")
    text = (
        f"{who}: the heat pump delivers {delivered:.0f} W of the {request:.0f} W{service} requested "
        f"({100.0 * delivered / request:.0f} %; {named}): the largest duty that runs at this point. "
        f"Limit: {limit.meaning}{_numbers(model, limit, note, 'at the requested duty')}. What to do: {limit.remedy}"
    )
    return text, ("limited", binding or "compressor")


def steady_notice(delivered_key: str = "Q_ref_tank [W]") -> Callable[[_F], _F]:
    """Decorate an ``analyze_steady`` so that its *final* result is what the caller is warned about.

    Outermost decorator. The decorated method runs inside a scope in which
    nothing warns about operating limits: its own trial solves, and any
    ``analyze_steady`` it calls on the way. When it returns, a row whose
    compressor was asked to run and does not (``failure_reason`` other than
    ``"none"``), or runs short of the request (``capacity_clamped == "max"``),
    is reported once (:class:`OperatingLimitWarning`, :func:`steady_message`).
    A call made from inside another such scope (a dynamic run, another
    ``analyze_steady``) only returns its row. ``verbose=False``, where the
    method has that argument, switches the warning off for the call.
    """

    def decorate(function: _F) -> _F:
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
            if _SCOPE.get() is not None:
                return function(self, *args, **kwargs)
            scope = _Scope()
            token = _SCOPE.set(scope)
            try:
                result = function(self, *args, **kwargs)
            finally:
                _SCOPE.reset(token)
            row = _row_of(result)
            if row is None:
                return result
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            if bound.arguments.get("verbose", True) is False:
                return result
            message = steady_message(
                self, row, _echo(self, function.__name__, bound.arguments), delivered_key, scope.notes
            )
            if message is not None:
                _issue(message[0], model=self, key=message[1])
            return result

        # ``analyze_steady.__wrapped__`` stays the undecorated solve, as under the other decorators.
        wrapped.__wrapped__ = getattr(function, "__wrapped__", function)  # type: ignore[attr-defined]
        return cast(_F, wrapped)

    return decorate


# ---------------------------------------------------------------------------
# Dynamic runs
# ---------------------------------------------------------------------------

ATTRS_KEY: str = "operating_limits"
"""``DataFrame.attrs`` key under which a dynamic result carries :func:`summarize_operating_limits`."""

#: A tank counts as below its lower set point when it is colder by more than this [K].
SETPOINT_TOL_K: float = 0.05


def _column(df: Any, key: str) -> Any:
    import numpy as np
    import pandas as pd

    if key not in df.columns:
        return np.full(len(df), np.nan)
    return pd.to_numeric(df[key], errors="coerce").to_numpy(dtype=float)


def _labels(df: Any, key: str) -> list[str | None]:
    if key not in df.columns:
        return [None] * len(df)
    return [_text(value) for value in df[key].tolist()]


def _by_label(labels: list[str | None], mask: Any, time_s: Any) -> dict[str, dict[str, float]]:
    """``{label: {"steps [-]": n, "first_time [s]": t}}`` over the steps of ``mask``, most frequent first."""
    found: dict[str, dict[str, float]] = {}
    for index, label in enumerate(labels):
        if not mask[index]:
            continue
        name = label if label is not None else "unknown"
        entry = found.setdefault(name, {"steps [-]": 0, "first_time [s]": float(time_s[index])})
        entry["steps [-]"] += 1
    return dict(sorted(found.items(), key=lambda item: (-item[1]["steps [-]"], item[1]["first_time [s]"])))


def summarize_operating_limits(df: Any, dt_s: float, *, T_tank_w_lower_bound: float | None = None) -> dict[str, Any]:
    """What the operating limits did to a dynamic run, from its result frame.

    Works on the frame of any family's ``analyze_dynamic`` and on rows
    collected from ``step()``. A step counts as

    - **off** when the compressor was called but did not run: ``failure_reason``
      is not ``"none"`` and ``hp_is_on`` is false;
    - **limited** when it ran below the request: ``capacity_clamped == "max"``
      and the running duty is short of ``Q_request [W]`` by more than 0.1 %.

    Parameters
    ----------
    df : pd.DataFrame
        Dynamic result (``failure_reason``, ``hp_is_on``, ``capacity_clamped``,
        ``capacity_binding``, ``Q_request [W]``, ``Q_max [W]`` and, by family,
        ``Q_unmet [W]`` or the tank and hot-water columns).
    dt_s : float
        Time step of the result [s].
    T_tank_w_lower_bound : float | None
        Lower thermostat set point of a boiler [°C], for the time the tank
        spent below it. ``None``: not evaluated (NaN).

    Returns
    -------
    dict
        Common keys:

        - ``kind``: ``"boiler"`` (a tank is heated) or ``"space"`` (a load is met);
        - ``steps [-]``: steps of the run;
        - ``off_steps [-]``, ``off_time [h]``: called but did not run;
        - ``off_reasons``: ``{failure_reason: {"steps [-]", "first_time [s]"}}``,
          most frequent first;
        - ``limited_steps [-]``, ``limited_time [h]``: ran below the request;
        - ``limited_bindings``: ``{capacity_binding: {"steps [-]", "first_time [s]"}}``.

        ``"space"`` adds ``Q_request [kWh]`` (load asked for, heating and
        cooling as magnitudes), ``Q_unmet [kWh]`` and ``unmet_fraction [-]``.

        ``"boiler"`` adds ``Q_below_request [kWh]`` (heat by which the running
        duty of the limited steps fell short of the requested duty, over their
        run time), ``T_tank_w_min [°C]``, ``time_below_lower_setpoint [h]``
        and ``Q_dhw_unmet [kWh]`` (NaN without the hot-water columns).
    """
    import numpy as np

    steps = len(df)
    hours = float(dt_s) / 3600.0
    to_kwh = float(dt_s) / 3.6e6
    time_s = _column(df, "time [s]")
    if not np.isfinite(time_s).all():
        time_s = np.arange(steps, dtype=float) * float(dt_s)
    reasons = _labels(df, "failure_reason")
    bindings = _labels(df, "capacity_binding")
    clamped = _labels(df, "capacity_clamped")
    is_on = np.array([bool(v) for v in df["hp_is_on"].tolist()]) if "hp_is_on" in df.columns else np.zeros(steps, bool)
    request = np.abs(np.nan_to_num(_column(df, "Q_request [W]")))
    space = "Q_unmet [W]" in df.columns

    refused = np.array([reason not in (None, "none") for reason in reasons], dtype=bool)
    off = refused & ~is_on
    if space:
        off &= request > 0.0
        unmet = np.nan_to_num(_column(df, "Q_unmet [W]"))
        short = unmet > SHORTFALL_REL_TOL * request
    else:
        running_duty = _column(df, "Q_max [W]")
        short = np.isfinite(running_duty) & (running_duty < request * (1.0 - SHORTFALL_REL_TOL))
    limited = np.array([value == "max" for value in clamped], dtype=bool) & is_on & ~refused & short

    summary: dict[str, Any] = {
        "kind": "space" if space else "boiler",
        "steps [-]": int(steps),
        "off_steps [-]": int(off.sum()),
        "off_time [h]": float(off.sum()) * hours,
        "off_reasons": _by_label(reasons, off, time_s),
        "limited_steps [-]": int(limited.sum()),
        "limited_time [h]": float(limited.sum()) * hours,
        "limited_bindings": _by_label(bindings, limited, time_s),
    }
    if space:
        request_kwh = float(request.sum()) * to_kwh
        unmet_kwh = float(unmet.sum()) * to_kwh
        summary.update(
            {
                "Q_request [kWh]": request_kwh,
                "Q_unmet [kWh]": unmet_kwh,
                "unmet_fraction [-]": unmet_kwh / request_kwh if request_kwh > 0.0 else 0.0,
            }
        )
        return summary

    runtime = _column(df, "runtime_fraction [-]")
    runtime = np.where(np.isfinite(runtime), runtime, 1.0)
    below = np.where(limited, (request - np.nan_to_num(_column(df, "Q_max [W]"))) * runtime, 0.0)
    T_tank = _column(df, "T_tank_w [°C]")
    T_min = float(np.nanmin(T_tank)) if np.isfinite(T_tank).any() else math.nan
    time_below = math.nan
    if T_tank_w_lower_bound is not None and math.isfinite(T_tank_w_lower_bound):
        time_below = float((T_tank < T_tank_w_lower_bound - SETPOINT_TOL_K).sum()) * hours
    dhw_unmet = math.nan
    if "Q_dhw_unmet [W]" in df.columns:
        dhw_unmet = float(np.nan_to_num(_column(df, "Q_dhw_unmet [W]")).sum()) * to_kwh
    summary.update(
        {
            "Q_below_request [kWh]": float(below.sum()) * to_kwh,
            "T_tank_w_min [°C]": T_min,
            "time_below_lower_setpoint [h]": time_below,
            "Q_dhw_unmet [kWh]": dhw_unmet,
        }
    )
    return summary


def _counts(found: Mapping[str, Mapping[str, float]]) -> str:
    return ", ".join(
        f"{name} on {int(entry['steps [-]'])} (first at {entry['first_time [s]'] / 3600.0:.2f} h)"
        for name, entry in found.items()
    )


#: Remedies spelled out in a dynamic warning (the most frequent limits; the rest are named only).
_REMEDIES_SHOWN: int = 3


def dynamic_message(
    summary: Mapping[str, Any], who: str, T_tank_w_lower_bound: float | None = None, model: Any = None
) -> str | None:
    """The end-of-run warning text of a :func:`summarize_operating_limits` result; ``None`` if no step was limited.

    ``model`` adds the value of each limit on that machine (``T_dis_max``, the
    liquid's operating limit, ...).
    """
    off_steps, limited_steps = int(summary["off_steps [-]"]), int(summary["limited_steps [-]"])
    if off_steps == 0 and limited_steps == 0:
        return None
    steps = int(summary["steps [-]"])
    parts: list[str] = []
    if off_steps:
        parts.append(
            f"the compressor was called but did not run on {off_steps} of {steps} steps "
            f"({summary['off_time [h]']:.1f} h): {_counts(summary['off_reasons'])}"
        )
    if limited_steps:
        short = ""
        if summary["kind"] == "boiler":
            short = f", {summary['Q_below_request [kWh]']:.2f} kWh short of the requested duty"
        parts.append(
            f"it ran below the requested duty on {limited_steps} of {steps} steps "
            f"({summary['limited_time [h]']:.1f} h{short}): {_counts(summary['limited_bindings'])}"
        )
    text = f"{who}: " + "; ".join(parts) + ". "
    if summary["kind"] == "space":
        text += (
            f"Unmet load: {summary['Q_unmet [kWh]']:.2f} kWh of {summary['Q_request [kWh]']:.2f} kWh requested "
            f"({100.0 * summary['unmet_fraction [-]']:.1f} %). "
        )
    else:
        tank = f"Tank: lowest {summary['T_tank_w_min [°C]']:.1f} °C"
        below = summary["time_below_lower_setpoint [h]"]
        if T_tank_w_lower_bound is not None and math.isfinite(below):
            tank += f", {below:.1f} h below the {T_tank_w_lower_bound:.1f} °C lower set point"
        if math.isfinite(summary["Q_dhw_unmet [kWh]"]):
            tank += f"; hot water not delivered: {summary['Q_dhw_unmet [kWh]']:.2f} kWh"
        text += tank + ". "
    # Each limit once, under the name the counts above use (a stopped step's
    # failure_reason comes first, then a reduced step's capacity_binding).
    named: list[tuple[str, Limit]] = [(reason, limit_for(reason=reason)) for reason in summary["off_reasons"]]
    named += [(binding, limit_for(binding=binding)) for binding in summary["limited_bindings"]]
    shown: list[tuple[str, Limit]] = []
    for name, limit in named:
        if all(limit is not other for _, other in shown):
            shown.append((name, limit))
    remedies = [f"{name}{_numbers(model, limit, None)}: {limit.remedy}" for name, limit in shown[:_REMEDIES_SHOWN]]
    text += "What to do -- " + " ".join(remedies)
    text += (
        " Per step: 'failure_reason', 'capacity_clamped', 'capacity_binding'; totals: "
        f"df.attrs['{ATTRS_KEY}'] or tmhp.summarize_operating_limits(df, dt_s)."
    )
    return text


def warn_if_operating_limited(df: Any, dt_s: float, who: str, model: Any = None) -> dict[str, Any] | None:
    """Summarise a dynamic result, attach the summary, and warn once if a step was stopped or limited.

    The summary (:func:`summarize_operating_limits`) is stored in
    ``df.attrs["operating_limits"]`` and returned; ``None`` for a frame
    without ``failure_reason`` (nothing to summarise). ``model`` gives the
    lower tank set point of a boiler. Call it on the rows collected from
    ``step()`` to get the report ``analyze_dynamic`` gives.
    """
    if "failure_reason" not in getattr(df, "columns", ()):
        return None
    lower = getattr(model, "T_tank_w_lower_bound", None) if model is not None else None
    try:
        lower = None if lower is None else float(lower)
    except (TypeError, ValueError):
        lower = None
    summary = summarize_operating_limits(df, dt_s, T_tank_w_lower_bound=lower)
    df.attrs[ATTRS_KEY] = summary
    text = dynamic_message(summary, who, lower, model)
    if text is not None:
        _issue(text)
    return summary


def dynamic_notice(function: _F) -> _F:
    """Decorate an ``analyze_dynamic``: no operating-limit warning per step, one summary at the end.

    The run is made inside :func:`silenced`; the returned frame then gets
    ``attrs["operating_limits"]`` and, if a step was stopped or limited, one
    :class:`OperatingLimitWarning` (:func:`warn_if_operating_limited`).
    """
    signature = inspect.signature(function)

    @wraps(function)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        if _SCOPE.get() is not None:
            return function(self, *args, **kwargs)
        with silenced():
            df = function(self, *args, **kwargs)
        if getattr(df, "columns", None) is not None:
            bound = signature.bind(self, *args, **kwargs)
            warn_if_operating_limited(
                df, float(bound.arguments["dt_s"]), f"{type(self).__name__}.{function.__name__}", self
            )
        return df

    return cast(_F, wrapped)
