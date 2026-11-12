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
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from .g_function import g_step_mean


@runtime_checkable
class GroundCoupler(Protocol):
    """Pluggable ground-response backend for BHE temporal superposition."""

    def reset(self, n_steps: int, time_arr: np.ndarray) -> None:
        """Initialise pulse-history state for a simulation of ``n_steps`` steps.

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
    non-uniform or foreign time array falls back to direct evaluation.

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
        Step length [s]. Taken from the time grid passed to :meth:`reset`
        when omitted; required only for a one-step grid without a
        ``g_interp.dt_s``.
    """

    def __init__(self, g_interp, pulse_tol: float = 1e-6, dt_s: float | None = None) -> None:
        self._g = g_interp
        self._pulse_tol = float(pulse_tol)
        self._dt_fixed = None if dt_s is None else float(dt_s)
        self._dt: float | None = self._dt_fixed
        self._pulses: np.ndarray = np.zeros(0)
        self._q_old: float = 0.0
        self._time: np.ndarray | None = None
        self._gbar_rev: np.ndarray | None = None

    def reset(self, n_steps: int, time_arr: np.ndarray) -> None:
        time_arr = np.asarray(time_arr, dtype=float)
        self._pulses = np.zeros(n_steps)
        self._q_old = 0.0
        dt = self._dt_fixed
        if dt is None:
            dt = float(time_arr[1] - time_arr[0]) if len(time_arr) > 1 else getattr(self._g, "dt_s", None)
        if dt is None or not (np.isfinite(dt) and dt > 0):
            raise ValueError("AggregateGFunctionCoupler needs a positive step length (pass dt_s for a one-step grid)")
        self._dt = float(dt)
        self._time = time_arr
        lags = time_arr - time_arr[0] if len(time_arr) else time_arr
        uniform = len(time_arr) == n_steps and np.allclose(lags, np.arange(len(time_arr)) * self._dt, rtol=1e-9, atol=0)
        if uniform and n_steps > 0:
            # ḡ for lag k·dt, stored reversed so that the response at step n is
            # one contiguous dot product: Σ_i p_i ḡ[n-i] = p[:n+1] · rev[N-1-n:].
            gbar = np.asarray(g_step_mean(self._g, lags, self._dt), dtype=float)
            self._gbar_rev = np.ascontiguousarray(gbar[::-1])
        else:
            self._gbar_rev = None

    def _history_rise(self, n: int, time_arr: np.ndarray, upto: int) -> float:
        """Σ_{i<upto} p_i ḡ(t_n - t_i): response at step ``n`` to the first ``upto`` pulses."""
        if upto <= 0:
            return 0.0
        rev = self._gbar_rev
        known = self._time
        if rev is not None and known is not None and (time_arr is known or np.array_equal(time_arr, known)):
            self._time = time_arr  # identity fast path from now on
            N = len(rev)
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

    def preview_wall_temperature_rise(self, n: int, time_arr: np.ndarray, q_unit: float) -> float:
        """Evaluate an uncommitted candidate without modifying load history.

        Optional extension for coupled flow control; the original GroundCoupler
        protocol remains unchanged for legacy external backends.
        """
        rise = self._history_rise(n, time_arr, n)
        delta = q_unit - self._q_old
        if abs(delta) > self._pulse_tol:
            rise += delta * self._current_response()
        return float(rise)

    def wall_temperature_rise(self, n: int, time_arr: np.ndarray, q_unit: float) -> float:
        if abs(q_unit - self._q_old) > self._pulse_tol:
            self._pulses[n] = q_unit - self._q_old
            self._q_old = q_unit
        return self._history_rise(n, time_arr, n + 1)
