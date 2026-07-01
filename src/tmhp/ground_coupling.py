"""Ground-coupling abstraction for borehole-heat-exchanger temporal superposition.

This module separates the *ground thermal response* (how the borehole-wall
temperature responds to a history of ground loads) from the heat-pump cycle
physics, so the response backend becomes swappable behind one small contract:

- :class:`AggregateGFunctionCoupler` (default) wraps a single field-average
  g-function interpolator ``g(tau)`` [m·K/W] and reproduces the legacy inline
  temporal superposition **byte-for-byte**.
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
    """Single field-average g-function temporal superposition (legacy default).

    Reproduces the legacy ``_compute_bhe_superposition`` inner loop exactly:
    a pulse is recorded whenever the per-length load changes by more than
    ``pulse_tol``, and the pulse train is convolved with ``g(tau)``.

    Parameters
    ----------
    g_interp : Callable[[np.ndarray], np.ndarray]
        Field-average g-function interpolator mapping lag time ``tau`` [s] to
        the dimensional response ``g`` [m·K/W].
    pulse_tol : float, optional
        Minimum load change [W/m] that registers a new pulse (default 1e-6).
    """

    def __init__(self, g_interp, pulse_tol: float = 1e-6) -> None:
        self._g = g_interp
        self._pulse_tol = float(pulse_tol)
        self._pulses: np.ndarray = np.zeros(0)
        self._q_old: float = 0.0

    def reset(self, n_steps: int, time_arr: np.ndarray) -> None:
        self._pulses = np.zeros(n_steps)
        self._q_old = 0.0

    def wall_temperature_rise(self, n: int, time_arr: np.ndarray, q_unit: float) -> float:
        if abs(q_unit - self._q_old) > self._pulse_tol:
            self._pulses[n] = q_unit - self._q_old
            self._q_old = q_unit

        idx = np.flatnonzero(self._pulses[: n + 1])
        if len(idx) == 0:
            return 0.0

        dQ = self._pulses[idx]
        tau = np.maximum(time_arr[n] - time_arr[idx], 1e-6)
        return float(np.dot(dQ, self._g(tau)))
