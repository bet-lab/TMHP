"""Independent oracle for the step-mean borehole-wall response (#96 G4).

Step ``n`` of a ground model covers ``[t_n, t_n + dt]``; its wall temperature
is the interval mean ``T_s - Σ_i Δq_i · (1/dt) ∫_{t_n - t_i}^{t_n - t_i + dt} g``.
The integral here is ``scipy.integrate.quad`` on the g-function callable with
its grid nodes as break points, so it does not share code with
:meth:`tmhp.g_function.GFunction.step_mean`.
"""

from __future__ import annotations

import numpy as np
from scipy.integrate import quad


def step_mean_quad(g, tau: float, dt: float) -> float:
    """``(1/dt) ∫_tau^{tau+dt} g(s) ds`` by adaptive quadrature."""
    nodes = getattr(g, "x", None)
    points = None if nodes is None else [float(x) for x in nodes if tau < x < tau + dt]
    value, _ = quad(lambda s: float(g(s)), tau, tau + dt, points=points or None, limit=400, epsabs=0.0, epsrel=1e-13)
    return value / dt


def step_mean_superposition(g, time_arr: np.ndarray, pulses: np.ndarray, dt: float) -> np.ndarray:
    """Mean wall-temperature rise [K] at every step for per-metre load pulses [W/m]."""
    time_arr = np.asarray(time_arr, dtype=float)
    pulses = np.asarray(pulses, dtype=float)
    cache: dict[float, float] = {}
    out = np.zeros(len(time_arr))
    for n in range(len(time_arr)):
        total = 0.0
        for i in np.flatnonzero(pulses[: n + 1]):
            tau = float(time_arr[n] - time_arr[i])
            if tau not in cache:
                cache[tau] = step_mean_quad(g, tau, dt)
            total += pulses[i] * cache[tau]
        out[n] = total
    return out
