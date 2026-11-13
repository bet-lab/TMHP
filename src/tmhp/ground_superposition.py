"""Exact temporal superposition for long runs (opt-in ground coupler).

The default :class:`~tmhp.ground_coupling.AggregateGFunctionCoupler` evaluates
the load history with one dot product per step. Its cost grows with the step
index: a run of ``N`` steps does ``N²/2`` multiply-adds. That is negligible
next to the refrigerant-cycle solve up to a few hundred thousand steps and
takes over beyond (measured: 0.2 s for 87 600 steps, 6–7 s for 525 600,
about 30 min for 5 256 000 — ten years at one-minute steps).

:class:`FastGFunctionCoupler` gives **the same numbers** at a cost per step
that does not grow with the run (about 2 µs: 0.2 s, 1 s and 11 s for the
three runs above). It is not a load-aggregation scheme: nothing is averaged
or truncated. The difference from the default coupler is floating-point
round-off (``2.5e-11`` K at most over 525 600 steps of switching loads).

Method
------
Step ``n`` covers ``[t_n, t_n + dt]`` and the value returned for it is

.. math::

    \\Delta T_n = \\sum_{k=0}^{n} q_{n-k}\\, w_k, \\qquad
    w_0 = \\bar g_0,\\; w_k = \\bar g_k - \\bar g_{k-1},

with ``ḡ_k`` the step-mean response at lag ``k·dt``
(:func:`~tmhp.g_function.g_step_mean`) and ``q`` the load held over each
step. This is the default coupler's pulse sum written for loads instead of
load changes.

The sum over the past (``k ≥ 1``) is accumulated ahead of time. After step
``t − 1`` is committed, the block of the last ``s`` loads, where ``s`` is
the largest power of two dividing ``t``, is convolved with ``w`` once and
added to the next ``s`` steps. Every pair (load step, later step) is then
covered by exactly one block (the one at the highest binary digit in which
the two step indices differ), so when step ``n`` arrives its history term is
already complete. Blocks are convolved with the FFT, which makes a run
``O(N log² N)``; this is the standard "online" (relaxed) convolution of
computer algebra, applied to one fixed and one growing sequence.

Because the history term of the current step is known before its load is,
:meth:`FastGFunctionCoupler.preview_wall_temperature_rise` costs one
multiplication.

Use
---
::

    model = GroundSourceHeatPumpBoiler(...)
    enable_borehole_dynamics(model)      # optional; before the next line
    enable_fast_superposition(model)

or pass ``ground_coupler=FastGFunctionCoupler(g)`` with your own g-function.

Limits
------
- The run's time grid must be uniform and given in full to ``reset`` (the
  hosts in this package do that).
- Steps are committed in increasing order. A step that is skipped keeps the
  previous load, as with the default coupler. Committing a step twice, or
  previewing any step but the next one, raises instead of returning a value
  the default coupler would not give.
- Memory is three arrays of ``N`` floats, like the default (five once the
  physical wall temperature of a borehole heat-capacity g-function is asked
  for; the first such request replays the run so far, once).
- A single step can take long when a large block is due (once, half-way
  through the run, a block of ``N/2`` loads); only the total matters for a
  simulation.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import fft as _fft

from .g_function import g_step_mean
from .ground_coupling import AggregateGFunctionCoupler

__all__ = ["FastGFunctionCoupler", "enable_fast_superposition"]

# Blocks shorter than this are convolved directly (np.convolve beats an FFT there).
_DIRECT_BLOCK = 64


class _OnlineConvolution:
    """History sum ``Σ_{k≥1} q[n-k]·w[k]`` of a growing load sequence, by binary splitting."""

    def __init__(self, weights: np.ndarray) -> None:
        self.w = np.ascontiguousarray(weights, dtype=float)
        self.n_steps = len(self.w)
        self._acc = np.zeros(self.n_steps + 1)
        self._kernel_fft: dict[int, tuple[int, np.ndarray]] = {}

    def history(self, n: int) -> float:
        """Response at step ``n`` to the loads of steps ``< n`` (complete once step ``n - 1`` is flushed)."""
        return float(self._acc[n])

    def flush(self, q: np.ndarray, t: int) -> None:
        """Account for the loads ``q[t-s:t]`` in steps ``t .. t+s-1``; call once after step ``t - 1`` is stored."""
        n_steps = self.n_steps
        if t >= n_steps:
            return
        s = t & -t  # largest power of two dividing t
        if s == 1:
            self._acc[t] += q[t - 1] * self.w[1]
            return
        src = q[t - s : t]
        if not src.any():
            return
        count = min(s, n_steps - t)
        # Lags 1 .. 2s-1 reach from the block to its targets; index s-1+j of the
        # full convolution belongs to step t+j.
        ker = self.w[1 : min(2 * s, n_steps)]
        if s < _DIRECT_BLOCK:
            self._acc[t : t + count] += np.convolve(src, ker)[s - 1 : s - 1 + count]
            return
        # A circular convolution of length >= len(ker) wraps only into indices
        # below s-1, which are not used.
        cached = self._kernel_fft.get(s)
        if cached is None:
            size = _fft.next_fast_len(len(ker), real=True)
            ker_f = _fft.rfft(ker, size)
            if 8 * s <= n_steps and len(ker) == 2 * s - 1:  # reused at least four times
                self._kernel_fft[s] = (size, ker_f)
        else:
            size, ker_f = cached
        out = _fft.irfft(_fft.rfft(src, size) * ker_f, size)
        self._acc[t : t + count] += out[s - 1 : s - 1 + count]


class FastGFunctionCoupler:
    """Drop-in for :class:`~tmhp.ground_coupling.AggregateGFunctionCoupler` with a cost per step independent of the run length.

    Same contract, same sign and time conventions, same numbers (to
    round-off); see the module docstring for the method and its limits.

    Parameters
    ----------
    g_interp : Callable[[np.ndarray], np.ndarray]
        Field-average g-function ``g(tau)`` [m·K/W], as for the default
        coupler: a :class:`~tmhp.g_function.GFunction`, a
        :class:`~tmhp.borehole_capacity.DynamicGFunction` or any callable.
    pulse_tol : float, optional
        Load changes up to this size [W/m] keep the previous load (default 1e-6).
    dt_s : float, optional
        Fixed step length [s]; normally the host passes the run's step to
        :meth:`reset`.
    """

    def __init__(self, g_interp: Any, pulse_tol: float = 1e-6, dt_s: float | None = None) -> None:
        self._g = g_interp
        self._pulse_tol = float(pulse_tol)
        self._dt_fixed = None if dt_s is None else float(dt_s)
        self._dt: float | None = self._dt_fixed
        self._time: np.ndarray | None = None
        self._q: np.ndarray = np.zeros(0)
        self._q_old = 0.0
        self._next = 0
        self._fluid: _OnlineConvolution | None = None
        self._wall: _OnlineConvolution | None = None

    # -- run setup ------------------------------------------------------------
    def reset(self, n_steps: int, time_arr: np.ndarray, dt_s: float | None = None) -> None:
        """Start a run of ``n_steps`` steps on the uniform grid ``time_arr`` [s].

        The step length is, in order: ``dt_s`` given here, the constructor
        ``dt_s``, the spacing of ``time_arr``. ``time_arr`` must hold the
        time of every step of the run.
        """
        time_arr = np.asarray(time_arr, dtype=float)
        n_steps = int(n_steps)
        dt = dt_s if dt_s is not None else self._dt_fixed
        if dt is None and len(time_arr) > 1:
            dt = float(time_arr[1] - time_arr[0])
        if dt is None or not (np.isfinite(dt) and dt > 0):
            raise ValueError(
                "FastGFunctionCoupler needs the run's positive step length: pass reset(..., dt_s=) "
                "(a one-step time grid does not define it)"
            )
        if len(time_arr) != n_steps:
            raise ValueError(
                f"FastGFunctionCoupler needs the time of every step of the run: got {len(time_arr)} times "
                f"for n_steps={n_steps}"
            )
        lags = time_arr - time_arr[0] if n_steps else time_arr
        if not np.allclose(lags, np.arange(n_steps) * dt, rtol=1e-9, atol=1e-9 * dt):
            raise ValueError(
                f"time grid must be uniform with spacing dt_s={dt:g} s "
                f"(got spacings {np.unique(np.round(np.diff(time_arr), 9))[:4]} s)"
            )
        self._dt = float(dt)
        self._time = time_arr
        self._q = np.zeros(n_steps)
        self._q_old = 0.0
        self._next = 0
        gbar = np.asarray(g_step_mean(self._g, lags, self._dt), dtype=float)
        self._fluid = _OnlineConvolution(np.diff(gbar, prepend=0.0))
        self._wall = None  # built when the physical wall temperature is first asked for

    # -- checks ---------------------------------------------------------------
    def _run(self, n: int, time_arr: np.ndarray) -> _OnlineConvolution:
        fluid, known = self._fluid, self._time
        if fluid is None or known is None:
            raise RuntimeError("call reset() before evaluating the ground response")
        if time_arr is not known:
            other = np.asarray(time_arr, dtype=float)
            if other.shape != known.shape or not np.array_equal(other, known):
                raise ValueError("a different time grid was passed than the one given to reset(); call reset() again")
            self._time = time_arr  # identity fast path from now on
        if not 0 <= n < fluid.n_steps:
            raise ValueError(f"step {n} is outside the run of {fluid.n_steps} steps given to reset()")
        return fluid

    def _held(self, q_unit: float) -> float:
        """The load the default coupler would record: the previous one unless the change exceeds the tolerance."""
        return float(q_unit) if abs(q_unit - self._q_old) > self._pulse_tol else self._q_old

    def _store(self, n: int, q: float) -> None:
        self._q[n] = q
        assert self._fluid is not None
        self._fluid.flush(self._q, n + 1)
        if self._wall is not None:
            self._wall.flush(self._q, n + 1)
        self._next = n + 1

    # -- contract -------------------------------------------------------------
    def preview_wall_temperature_rise(self, n: int, time_arr: np.ndarray, q_unit: float) -> float:
        """Rise at the next step for a candidate load, without recording it."""
        fluid = self._run(n, time_arr)
        if n != self._next:
            raise ValueError(
                f"a candidate load can be evaluated for the next step only (step {self._next}), not step {n}"
            )
        return fluid.history(n) + self._held(q_unit) * float(fluid.w[0])

    def wall_temperature_rise(self, n: int, time_arr: np.ndarray, q_unit: float) -> float:
        fluid = self._run(n, time_arr)
        if n < self._next:
            raise ValueError(f"step {n} is already committed; steps are committed in increasing order")
        while self._next < n:  # steps not called for keep the previous load
            self._store(self._next, self._q_old)
        q = self._held(q_unit)
        self._q_old = q
        rise = fluid.history(n) + q * float(fluid.w[0])
        self._store(n, q)
        return rise

    def borehole_wall_rise(self, n: int, time_arr: np.ndarray) -> float:
        """Physical borehole-wall temperature rise [K] at a committed step ``n``.

        As for the default coupler: the value of
        :meth:`wall_temperature_rise` for a plain g-function, the wall itself
        for one with borehole heat capacity (``wall_step_mean``). The wall
        response is tracked from the first request on; that request replays
        the committed steps once.
        """
        fluid = self._run(n, time_arr)
        if n >= self._next:
            raise ValueError(f"call wall_temperature_rise for step {n} before asking for its wall temperature")
        wall_mean = getattr(self._g, "wall_step_mean", None)
        if wall_mean is None:
            conv = fluid
        else:
            if self._wall is None:
                assert self._time is not None and self._dt is not None
                lags = np.asarray(self._time, dtype=float) - float(self._time[0])
                self._wall = _OnlineConvolution(
                    np.diff(np.asarray(wall_mean(lags, self._dt), dtype=float), prepend=0.0)
                )
                for t in range(1, self._next + 1):
                    self._wall.flush(self._q, t)
            conv = self._wall
        # The history of a committed step is complete and later blocks never touch it.
        return conv.history(n) + float(self._q[n]) * float(conv.w[0])


def enable_fast_superposition(model: Any) -> FastGFunctionCoupler:
    """Switch a ground-source model to :class:`FastGFunctionCoupler`, in place.

    Works on :class:`~tmhp.GroundSourceHeatPumpBoiler` and its solar
    variants. The g-function, pulse tolerance and fixed step of the model's
    coupler are kept, so the results do not change. Call it last: after
    :func:`~tmhp.borehole_capacity.enable_borehole_dynamics` or attaching a
    :class:`~tmhp.borefield.FieldGroundResponse`, which both replace the
    default coupler.

    Returns
    -------
    FastGFunctionCoupler
        The coupler now used by ``model``.
    """
    if not hasattr(model, "_ground_coupler"):
        raise TypeError(
            f"{type(model).__name__} has no ground coupler (only the ground-source heat-pump boilers delegate "
            "their temporal superposition)"
        )
    coupler = model._ground_coupler
    if isinstance(coupler, FastGFunctionCoupler):
        return coupler
    if not isinstance(coupler, AggregateGFunctionCoupler):
        raise ValueError("the model uses an injected ground_coupler; build a FastGFunctionCoupler on its g-function")
    fast = FastGFunctionCoupler(coupler._g, pulse_tol=coupler._pulse_tol, dt_s=coupler._dt_fixed)
    model._ground_coupler = fast
    return fast
