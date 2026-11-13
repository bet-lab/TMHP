"""Ground-coupling abstraction for borehole-heat-exchanger temporal superposition.

This module separates the *ground thermal response* (how the borehole-wall
temperature responds to a history of ground loads) from the heat-pump cycle
physics, so the response backend becomes swappable behind one small contract:

- :class:`AggregateGFunctionCoupler` (default) wraps a single field-average
  g-function ``g(tau)`` [m·K/W] and superposes load pulses with its
  step-mean response (the same convention as the inline superposition of
  :class:`~tmhp.GroundSourceHeatPump` and
  :class:`~tmhp.gshp_empirical.GroundSourceHeatPumpEmpirical`).
- External packages (e.g. ``geolink``) can implement the :class:`GroundCoupler`
  protocol with a *resolved multi-borehole network* response — replacing the
  single lumped g-function with full borehole-to-borehole superposition —
  *without this package depending on them* (dependency inversion: ``tmhp``
  defines the abstraction it needs; the richer implementation lives elsewhere
  and is injected).

Contract
--------
A coupler owns the ground-load *pulse history*. The host
(:class:`~tmhp.ground_source_heat_pump_boiler.GroundSourceHeatPumpBoiler`) calls
:meth:`GroundCoupler.reset` once per simulation and
:meth:`GroundCoupler.wall_temperature_rise` once per timestep with the current
per-length ground load ``q_unit`` [W/m]; the coupler returns the borehole-wall
temperature *rise* magnitude ``dT`` [K].

Time convention: step ``n`` stands for the interval ``[t_n, t_n + dt]`` and
the returned ``dT`` is the wall-temperature rise *averaged over that
interval*, including the response to the step's own load ``q_unit``. An
external backend should return the same quantity so that results do not
depend on which backend is injected.

Sign convention (matches the legacy GSHPB)
------------------------------------------
``q_unit > 0`` denotes heat *extraction* from the ground; the returned ``dT`` is
the positive magnitude such that the disturbed wall temperature is
``T_wall = T_undisturbed - dT``. (Note this is the opposite sign labelling from
``geolink``'s ``q' > 0`` = injection convention; a ``geolink`` coupler maps the
sign at its boundary so the value returned here keeps the extraction-positive
meaning.)

Borehole heat capacity (fluid-referenced wall temperature)
----------------------------------------------------------
The host turns the wall temperature into the mean fluid temperature with the
steady relation ``T_f = T_wall - q' R_b``. A backend that models the heat
capacity of the borehole (:class:`~tmhp.borehole_capacity.DynamicGFunction`)
therefore returns the *fluid-referenced* rise: the wall-temperature rise that
gives the dynamic fluid temperature through that unchanged relation. It
equals the physical wall rise once the borehole has settled (hours) and
differs from it just after a load change, when the fluid has not yet followed.
Such a backend reports the physical wall rise separately through the optional
``borehole_wall_rise(n, time_arr)`` (:func:`physical_wall_rise`), and says so
with a true ``fluid_referenced`` attribute.

Runs that cover part of a step
------------------------------
The history holds one load per step. A host whose compressor runs for the
first part of a step records the step-average load, but the fluid while it
runs carries the running load. With a fluid-referenced response the host must
therefore ask for the response over the run itself, under the running load
(``preview_wall_temperature_rise(..., window_s=...)``, see
:func:`run_window_preview`): the first-instant terms of the response then
cancel the host's ``q' R_b`` for the same load. A backend that does not take
``window_s`` is asked for the whole step with the step-average load, as
before.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

import numpy as np

from ._online_convolution import OnlineHistory
from .g_function import g_step_mean


@runtime_checkable
class GroundCoupler(Protocol):
    """Pluggable ground-response backend for BHE temporal superposition."""

    def reset(self, n_steps: int, time_arr: np.ndarray) -> None:
        """Initialise pulse-history state for a simulation of ``n_steps`` steps.

        A backend may also accept a keyword ``dt_s`` (the run's step length
        [s]); :func:`reset_ground_coupler` passes it when the signature
        allows, which is the only way to know the step length of a one-step
        run.

        Parameters
        ----------
        n_steps : int
            Number of timesteps in the upcoming simulation.
        time_arr : np.ndarray
            Absolute time of each step [s] (uniform grid). Backends that
            precompute a response matrix at fixed times use this.
        """
        ...

    def wall_temperature_rise(self, n: int, time_arr: np.ndarray, q_unit: float) -> float:
        """Borehole-wall temperature rise [K] at step ``n`` for load ``q_unit``.

        Parameters
        ----------
        n : int
            Current step index (0-based).
        time_arr : np.ndarray
            Absolute time of each step [s] (same array passed to :meth:`reset`).
        q_unit : float
            Per-length ground load at this step [W/m], extraction-positive.
        """
        ...


class AggregateGFunctionCoupler:
    """Single field-average g-function temporal superposition (default backend).

    A pulse is recorded whenever the per-length load changes by more than
    ``pulse_tol``; the pulse train is convolved with the **step-mean**
    response ``ḡ(τ; dt) = (1/dt) ∫_τ^{τ+dt} g`` (:func:`~tmhp.g_function.g_step_mean`).
    The value returned for step ``n`` is therefore the mean wall-temperature
    rise over the interval ``[t_n, t_n + dt]``, including the response to the
    step's own load. (Before #96 G4 the pulse train was evaluated at the step
    *start*, ``g(t_n - t_i)``, so the current step's pulse contributed
    ``g(0) ≈ 0`` and the wall was reported up to ~0.8 K too warm at
    25 W/m with hourly steps.)

    On a uniform time grid the step-mean responses for every lag are computed
    once in :meth:`reset` and each step is a contiguous dot product; a
    different time array passed later falls back to direct evaluation with
    the same step length. A non-uniform grid is rejected at :meth:`reset`.

    **Long runs.** The dot product grows with the step index (a run of ``N``
    steps costs ``N²/2``: about 7 s for 525 600 steps, half an hour for
    5 256 000). From :attr:`FAST_MIN_STEPS` steps on (a year at ten-minute
    steps), the same pulse sum is accumulated ahead of time by an online FFT
    convolution (:mod:`tmhp._online_convolution`), at a cost per step that
    does not depend on the run length (about 1 s and 10 s for the two runs
    above). It is not a load-aggregation scheme: nothing is averaged or
    truncated, and the values differ from the dot product by round-off only
    (``1e-11`` K over 250 000 steps). It applies while steps are recorded in
    increasing order on the grid given to :meth:`reset`; any other use (a
    step recorded again, another time array) is answered by the dot product,
    with the same values.

    Parameters
    ----------
    g_interp : Callable[[np.ndarray], np.ndarray]
        Field-average g-function mapping lag time ``tau`` [s] to the
        dimensional response ``g`` [m·K/W]. A
        :class:`~tmhp.g_function.GFunction` is integrated exactly; any other
        callable by Gauss–Legendre quadrature.
    pulse_tol : float, optional
        Minimum load change [W/m] that registers a new pulse (default 1e-6).
    dt_s : float, optional
        Fixed step length [s] for every run. Normally omitted: the host passes
        the run's own step length to :meth:`reset` (``dt_s=``). The step
        length is never taken from how the g-function was built.
    """

    #: Run length [steps] from which the pulse history is accumulated ahead of
    #: time (see "Long runs"). Measured break-even of a whole run: about
    #: 50 000 steps with one call per step, below 10 000 when the host also
    #: previews candidates and reads the wall temperature every step.
    FAST_MIN_STEPS: int = 50_000

    def __init__(self, g_interp, pulse_tol: float = 1e-6, dt_s: float | None = None) -> None:
        self._g = g_interp
        self._pulse_tol = float(pulse_tol)
        self._dt_fixed = None if dt_s is None else float(dt_s)
        self._dt: float | None = self._dt_fixed
        self._pulses: np.ndarray = np.zeros(0)
        self._q_old: float = 0.0
        self._time: np.ndarray | None = None
        self._gbar_rev: np.ndarray | None = None
        self._gwall_rev: np.ndarray | None = None
        self._online: OnlineHistory | None = None
        self._online_wall: OnlineHistory | None = None
        # (step-mean table it belongs to, g((k+1) dt) − g(k dt)); see _window_slopes.
        self._window_table: tuple[np.ndarray, np.ndarray] | None = None

    def reset(self, n_steps: int, time_arr: np.ndarray, dt_s: float | None = None) -> None:
        """Start a run of ``n_steps`` steps on the uniform grid ``time_arr`` [s].

        The step length is, in order: ``dt_s`` given here (the run's own
        step), the constructor ``dt_s``, the spacing of ``time_arr``. A
        one-step grid without either raises; a grid whose spacing is not
        uniform or differs from the step length raises, since the
        step-mean response assumes every step lasts ``dt``.
        """
        time_arr = np.asarray(time_arr, dtype=float)
        self._pulses = np.zeros(n_steps)
        self._q_old = 0.0
        dt = dt_s if dt_s is not None else self._dt_fixed
        if dt is None and len(time_arr) > 1:
            dt = float(time_arr[1] - time_arr[0])
        if dt is None or not (np.isfinite(dt) and dt > 0):
            raise ValueError(
                "AggregateGFunctionCoupler needs the run's positive step length: pass reset(..., dt_s=) "
                "(a one-step time grid does not define it)"
            )
        self._dt = float(dt)
        self._time = time_arr
        lags = time_arr - time_arr[0] if len(time_arr) else time_arr
        if not np.allclose(lags, np.arange(len(time_arr)) * self._dt, rtol=1e-9, atol=1e-9 * self._dt):
            raise ValueError(
                f"time grid must be uniform with spacing dt_s={self._dt:g} s "
                f"(got spacings {np.unique(np.round(np.diff(time_arr), 9))[:4]} s)"
            )
        if len(time_arr) == n_steps and n_steps > 0:
            # ḡ for lag k·dt, stored reversed so that the response at step n is
            # one contiguous dot product: Σ_i p_i ḡ[n-i] = p[:n+1] · rev[N-1-n:].
            gbar = np.asarray(g_step_mean(self._g, lags, self._dt), dtype=float)
            self._gbar_rev = np.ascontiguousarray(gbar[::-1])
            wall_mean = getattr(self._g, "wall_step_mean", None)
            self._gwall_rev = (
                None
                if wall_mean is None
                else np.ascontiguousarray(np.asarray(wall_mean(lags, self._dt), dtype=float)[::-1])
            )
        else:
            self._gbar_rev = None
            self._gwall_rev = None
        long_run = self._gbar_rev is not None and n_steps >= self.FAST_MIN_STEPS
        self._online = OnlineHistory(self._gbar_rev[::-1]) if long_run and self._gbar_rev is not None else None
        self._online_wall = None  # built when the physical wall temperature is first asked for

    def _pulse_recorded(self, n: int) -> None:
        """A pulse was written at step ``n``: the accumulated history holds only if that step was not folded in yet."""
        for online in (self._online, self._online_wall):
            if online is not None and n < online.folded:
                self._online = self._online_wall = None  # the dot product takes over for the rest of the run
                return

    def _history_rise(self, n: int, time_arr: np.ndarray, upto: int) -> float:
        """Σ_{i<upto} p_i ḡ(t_n - t_i): response at step ``n`` to the first ``upto`` pulses."""
        if upto <= 0:
            return 0.0
        rev = self._gbar_rev
        known = self._time
        if rev is not None and known is not None and (time_arr is known or np.array_equal(time_arr, known)):
            self._time = time_arr  # identity fast path from now on
            N = len(rev)
            online = self._online
            if online is not None and n <= upto <= n + 1 and n < N:
                own = float(self._pulses[n]) * float(rev[-1]) if upto == n + 1 else 0.0
                return online.history(self._pulses, n) + own
            # ḡ[n-i] for i = 0..upto-1  ->  rev[N-1-n+i]
            start = N - 1 - n
            return float(np.dot(self._pulses[:upto], rev[start : start + upto]))
        idx = np.flatnonzero(self._pulses[:upto])
        if len(idx) == 0:
            return 0.0
        tau = np.asarray(time_arr, dtype=float)[n] - np.asarray(time_arr, dtype=float)[idx]
        return float(np.dot(self._pulses[idx], g_step_mean(self._g, tau, self._step())))

    def _step(self) -> float:
        if self._dt is None:
            raise RuntimeError("call reset() before evaluating the ground response")
        return self._dt

    def _current_response(self) -> float:
        rev = self._gbar_rev
        if rev is not None:
            return float(rev[-1])
        return float(np.asarray(g_step_mean(self._g, np.array([0.0]), self._step()))[0])

    #: Pulses younger than this are re-integrated exactly over a run window [s] ...
    _WINDOW_EXACT_S = 6.0 * 3600.0
    #: ... as a number of steps, kept within these bounds.
    _WINDOW_EXACT_STEPS = (8, 512)
    #: Pulses up to this many steps old get the linear-in-step correction; older ones none.
    _WINDOW_LINEAR_STEPS = 4096

    @property
    def fluid_referenced(self) -> bool:
        """Whether the returned rise is fluid-referenced (a g-function with borehole heat capacity).

        See the module docstring. A host uses the run window of
        :meth:`preview_wall_temperature_rise` with such a response.
        """
        return getattr(self._g, "wall_step_mean", None) is not None

    def _window_mean(self, tau: np.ndarray, window_s: float) -> np.ndarray:
        """Mean of the response over ``[tau, tau + window_s]``; the point value for a vanishing window."""
        if window_s <= 1e-9 * self._step():
            return np.asarray(self._g(np.maximum(tau, 0.0)), dtype=float)
        return np.asarray(g_step_mean(self._g, tau, window_s), dtype=float)

    def _window_shift(self, n: int, time_arr: np.ndarray, window_s: float) -> float:
        """``Σ_{i<n} p_i [ḡ(τ_i; window) − ḡ(τ_i; dt)]`` [K], ``τ_i = t_n − t_i``.

        What the response to the recorded pulses changes by when it is
        averaged over the first ``window_s`` of step ``n`` instead of the
        whole step. Pulses younger than ``_WINDOW_EXACT_S`` are integrated
        exactly. For older ones the response is taken as linear in time
        across one step, ``ḡ(τ; w) − ḡ(τ; dt) = −(1 − w/dt)/2 · [g(τ + dt) −
        g(τ)]``, which a g-function satisfies to ``dt²/(48 π k_s τ²)`` per
        unit pulse there; beyond ``_WINDOW_LINEAR_STEPS`` steps the shift is
        dropped (at most ``q (1 − w/dt) / (8 π k_s · 4096)`` K for a load
        ``q`` [W/m] held for ever, 3e-4 K at 50 W/m and ``k_s`` = 2).
        """
        if n <= 0:
            return 0.0
        dt = self._step()
        rev = self._gbar_rev
        known = self._time
        if rev is None or known is None or not (time_arr is known or np.array_equal(time_arr, known)):
            idx = np.flatnonzero(self._pulses[:n])
            if len(idx) == 0:
                return 0.0
            tau = np.asarray(time_arr, dtype=float)[n] - np.asarray(time_arr, dtype=float)[idx]
            change = self._window_mean(tau, window_s) - np.asarray(g_step_mean(self._g, tau, dt), dtype=float)
            return float(np.dot(self._pulses[idx], change))
        lo_steps, hi_steps = self._WINDOW_EXACT_STEPS
        near = int(min(max(math.ceil(self._WINDOW_EXACT_S / dt), lo_steps), hi_steps))
        first = max(0, n - near)
        start = len(rev) - 1 - n  # ḡ[n-i] is rev[start + i]
        pulses = self._pulses
        tau = (n - np.arange(first, n)) * dt
        shift = float(np.dot(pulses[first:n], self._window_mean(tau, window_s) - rev[start + first : start + n]))
        if first > 0:
            slope = self._window_slopes()
            oldest = max(0, n - (len(slope) - 1))
            if oldest < first:
                # g((k+1) dt) − g(k dt) for the lags k = n − i of the pulses i = oldest .. first−1.
                steps = slope[n - first + 1 : n - oldest + 1][::-1]
                shift -= 0.5 * (1.0 - window_s / dt) * float(np.dot(pulses[oldest:first], steps))
        return shift

    def _window_slopes(self) -> np.ndarray:
        """``g((k+1) dt) − g(k dt)`` for the lags ``k`` up to ``_WINDOW_LINEAR_STEPS`` (one table per run)."""
        cached = self._window_table
        if cached is not None and cached[0] is self._gbar_rev:
            return cached[1]
        assert self._gbar_rev is not None
        count = min(len(self._gbar_rev), self._WINDOW_LINEAR_STEPS + 1)
        points = np.asarray(self._g(np.arange(count + 1) * self._step()), dtype=float)
        slope = np.diff(points)
        self._window_table = (self._gbar_rev, slope)
        return slope

    def preview_wall_temperature_rise(
        self, n: int, time_arr: np.ndarray, q_unit: float, window_s: float | None = None
    ) -> float:
        """Evaluate an uncommitted candidate without modifying load history.

        Optional extension for coupled flow control; the original GroundCoupler
        protocol remains unchanged for legacy external backends.

        Without ``window_s`` this is the mean rise over the whole step with
        ``q_unit`` held over it, the value :meth:`wall_temperature_rise`
        would return. With ``window_s`` it is the mean over the *first*
        ``window_s`` seconds of the step, ``[t_n, t_n + window_s]``, with
        ``q_unit`` held over that window: the rise a compressor sees that
        runs for the first part of a step. Give the load **while it runs**,
        not the step average. The value is continuous in the window: it
        tends to the rise at ``t_n`` under the previous load as the window
        vanishes, and equals the whole-step value at ``window_s = dt``.
        Nothing is recorded; the history keeps step-average loads.
        """
        rise = self._history_rise(n, time_arr, n)
        delta = q_unit - self._q_old
        dt = self._step()
        if window_s is None or window_s >= dt * (1.0 - 1e-12):
            if abs(delta) > self._pulse_tol:
                rise += delta * self._current_response()
            return float(rise)
        window = max(float(window_s), 0.0)
        rise += self._window_shift(n, time_arr, window)
        if abs(delta) > self._pulse_tol:
            rise += delta * float(self._window_mean(np.array([0.0]), window)[0])
        return float(rise)

    def wall_temperature_rise(self, n: int, time_arr: np.ndarray, q_unit: float) -> float:
        if abs(q_unit - self._q_old) > self._pulse_tol:
            self._pulses[n] = q_unit - self._q_old
            self._q_old = q_unit
            self._pulse_recorded(n)
        return self._history_rise(n, time_arr, n + 1)

    def borehole_wall_rise(self, n: int, time_arr: np.ndarray) -> float:
        """Physical borehole-wall temperature rise [K] at step ``n`` for the committed loads.

        Call it after :meth:`wall_temperature_rise` for the same step. For a
        plain g-function it is that method's value. For a g-function with
        borehole heat capacity (one that has ``wall_step_mean``) the value
        returned by :meth:`wall_temperature_rise` is fluid-referenced (see the
        module docstring) and this is the wall itself.
        """
        wall_mean = getattr(self._g, "wall_step_mean", None)
        if wall_mean is None:
            return self._history_rise(n, time_arr, n + 1)
        rev = self._gwall_rev
        known = self._time
        if rev is not None and known is not None and (time_arr is known or np.array_equal(time_arr, known)):
            if self._online is not None and 0 <= n < len(rev):
                if self._online_wall is None:
                    self._online_wall = OnlineHistory(rev[::-1])
                return self._online_wall.history(self._pulses, n) + float(self._pulses[n]) * float(rev[-1])
            start = len(rev) - 1 - n
            return float(np.dot(self._pulses[: n + 1], rev[start : start + n + 1]))
        idx = np.flatnonzero(self._pulses[: n + 1])
        if len(idx) == 0:
            return 0.0
        tau = np.asarray(time_arr, dtype=float)[n] - np.asarray(time_arr, dtype=float)[idx]
        return float(np.dot(self._pulses[idx], wall_mean(tau, self._step())))


def _accepts(method: Any, name: str) -> bool:
    try:
        params = list(inspect.signature(method).parameters.values())
    except (TypeError, ValueError):  # builtins / C callables without a signature
        return False
    return any(p.name == name or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def run_window_preview(coupler: Any) -> Callable[[int, np.ndarray, float, float], float] | None:
    """``coupler``'s run-window preview ``(n, time_arr, q_run, window_s) -> rise``, or ``None``.

    A host that lets the compressor run for part of a step asks for the
    response over that part (``window_s`` of
    :meth:`AggregateGFunctionCoupler.preview_wall_temperature_rise`) when the
    response is fluid-referenced: only then does it contain the first-instant
    terms that must act on the running load. A coupler qualifies when its
    ``fluid_referenced`` attribute is true and its
    ``preview_wall_temperature_rise`` accepts ``window_s``. Every other
    coupler (a plain g-function, an external backend that does not know the
    argument) returns ``None`` and is used with the step-average load, as
    before.
    """
    preview = getattr(coupler, "preview_wall_temperature_rise", None)
    if preview is None or not getattr(coupler, "fluid_referenced", False) or not _accepts(preview, "window_s"):
        return None

    def window(n: int, time_arr: np.ndarray, q_run: float, window_s: float) -> float:
        return float(preview(n, time_arr, q_run, window_s=window_s))

    return window


def physical_wall_rise(coupler: Any, n: int, time_arr: np.ndarray, rise: float) -> float:
    """Physical borehole-wall rise [K] of the step just committed.

    ``rise`` is what :meth:`GroundCoupler.wall_temperature_rise` returned for
    it. That is the wall itself unless the coupler's response is
    fluid-referenced and it reports the wall through ``borehole_wall_rise``.
    """
    method = getattr(coupler, "borehole_wall_rise", None)
    if method is None or not getattr(coupler, "fluid_referenced", True):
        return rise
    return float(method(n, time_arr))


def reset_ground_coupler(coupler: GroundCoupler, n_steps: int, time_arr: np.ndarray, dt_s: float) -> None:
    """Reset ``coupler`` for a run, giving it the run's step length when it accepts one.

    Hosts call this instead of ``coupler.reset(n_steps, time_arr)`` so that the
    step-mean response always uses the step of the run being simulated, not
    the step the g-function was built for (#96 G4 follow-up). An external
    coupler whose ``reset`` takes only ``(n_steps, time_arr)`` is called the
    old way.
    """
    params: list[inspect.Parameter]
    try:
        params = list(inspect.signature(coupler.reset).parameters.values())
    except (TypeError, ValueError):  # builtins / C callables without a signature
        params = []
    accepts_dt = any(p.name == "dt_s" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
    if accepts_dt:
        coupler.reset(n_steps, time_arr, dt_s=float(dt_s))  # type: ignore[call-arg]
    else:
        coupler.reset(n_steps, time_arr)
