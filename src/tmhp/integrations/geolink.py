"""Ground response from a borehole-to-borehole response matrix (``geolink``).

``geolink`` (bet-lab/geolink) computes resolved ground responses that
pygfunction does not: groundwater advection (moving finite line source),
response at arbitrary observation points, depth-segmented responses. Its
response models share one contract::

    response.response_matrix(field, soil, times) -> h[i, j, t]   # K per W/m

the wall-temperature rise of borehole ``i`` for a unit load per metre on
borehole ``j``. :class:`ResponseMatrixGFunction` turns any object with that
method into the field g-function the tmhp models use, so the rest of the
chain (step-mean superposition, borehole resistance,
:func:`~tmhp.borehole_capacity.with_borehole_capacity`) is unchanged::

    from tmhp.ground_coupling import AggregateGFunctionCoupler
    from tmhp.integrations.geolink import geolink_gfunction

    g = geolink_gfunction(x, y, H=150.0, r_b=0.08, k_s=2.0, alpha_s=1.25e-6,
                          t_max_s=10 * 8760 * 3600, groundwater_velocity=1e-7,
                          rho_s=2000.0, c_s=800.0)
    model = GroundSourceHeatPumpBoiler(N_1=len(x), N_2=1, H_b=150.0,
                                       ground_coupler=AggregateGFunctionCoupler(g))

``geolink`` is not a dependency of tmhp and is imported only by
:func:`geolink_gfunction`; :class:`ResponseMatrixGFunction` itself needs
nothing but the method above.

Two routes from geolink to a tmhp model
---------------------------------------
**The g-function route** (this module) reduces the response matrix to one
field g-function and leaves the superposition to tmhp. It is the default
choice. Every borehole carries the same load per metre; the first hour is
tabulated as in tmhp's own g-functions (a line from ``g(0) = 0`` to
``g(1 h)``); the table extends itself when a run outlasts ``t_max_s``; and
the g-function can be wrapped by
:func:`~tmhp.borehole_capacity.with_borehole_capacity` before it is given to
the coupler.

**The coupler route** gives the model geolink's own backend as
``ground_coupler``::

    from geolink.coupling import NetworkGroundCoupler

    coupler = NetworkGroundCoupler.from_rectangular(
        N_1=2, N_2=2, B=6.0, H_b=150.0, D_b=1.0, r_b=0.08, k_s=2.0, alpha_s=1.25e-6)
    model = GroundSourceHeatPumpBoiler(N_1=2, N_2=2, H_b=150.0, ground_coupler=coupler)

Use it when the boreholes do not share the heat in proportion to their
length (its ``weights``), or when the response model itself should be
evaluated below one hour. It has no g-function to wrap, so the borehole is
the steady one (a resistance without heat capacity).

The coupler route needs geolink main ``5cff1aa1`` (2026-11-13,
bet-lab/geolink#578) or later. From that commit ``NetworkGroundCoupler``
follows the contract of :mod:`tmhp.ground_coupling`: the load is per metre
of total borehole length (``q_unit = Q / (N H)``), the returned rise is the
mean over ``[t_n, t_n + dt]``, ``reset`` takes ``dt_s`` and
``preview_wall_temperature_rise`` exists. Earlier versions divided the load
by the number of boreholes a second time and evaluated the response at the
start of each step: an ``N``-borehole field returned ``1/N`` of the rise and
the first loaded step returned zero.

On either route the total borehole length must be the same on both sides.
The model divides the field load by its own ``N_1 * N_2 * H_b``; geolink
cannot see that number. A field of another total length
(``NetworkGroundCoupler.total_length``, or the ``H`` given to
:func:`geolink_gfunction`) is not detected and scales every rise by the
ratio of the two lengths: a 4 x 4 coupler under a 2 x 2 model returns four
times the rise the same heat causes in the 4 x 4 field. For boreholes of
different lengths ``H_b`` is their mean.

Checked with geolink ``5cff1aa1`` (2026-11-14). For 1 x 1, 2 x 2 and 4 x 4
fields (200 m, 6 m spacing, 20 W/m for a year, hourly and daily steps) the
coupler route gives 1.0001 to 1.0002 times the rise of tmhp's ``'UHTR'``
g-function, and no step after the first differs by more than 0.002 K (of 7
to 11 K). A 24 h run of the boiler on a 2 x 2 field of 100 m boreholes
stays within 0.013 K of the ``'UHTR'`` run on the coupler route and within
0.0003 K on the g-function route, for a swing of 0.65 K.

What remains different is the first hour. geolink evaluates the response
model there; a tmhp table draws the line from zero. With hourly steps the
first loaded step rises by 0.218 K on the coupler route and by 0.252 K on
the g-function route (20 W/m, ``k_s`` = 2 W/(m K), ``alpha_s`` = 1e-6 m2/s,
``r_b`` = 0.08 m); with daily steps by 1.998 and 1.999 K.
"""

from __future__ import annotations

import importlib
import math
from typing import Any

import numpy as np

__all__ = ["ResponseMatrixGFunction", "geolink_gfunction"]


class ResponseMatrixGFunction:
    """Field g-function ``g(t)`` [m.K/W] from a response-matrix provider.

    Every borehole carries the same load per metre ``q'`` (the field load
    over the total borehole length, as the tmhp models define it). The wall
    of borehole ``i`` then rises by ``q' * sum_j h[i, j]``, and the field
    value is the length-weighted mean over the boreholes::

        g(t) = sum_i H_i sum_j h[i, j, t] / sum_i H_i

    which is the uniform-heat-flux g-function (pygfunction's ``'UHTR'``) for
    a finite-line-source ``h``. Values are tabulated on a log-spaced grid
    from one hour to ``1.5 * t_max_s`` with ``g(0) = 0`` prepended, are
    interpolated linearly, and are recomputed when a later time is
    requested, like :class:`tmhp.g_function.GFunction`.

    The provider is called once per decade of time, never with the whole
    grid. geolink's groundwater model (``MFLSResponse``) sets its quadrature
    from the time span of one call (bet-lab/geolink#581): asked for the four
    to six decades of a table at once, its field response is up to 4e-5
    m.K/W off for a 2 x 2 field and 2.5e-3 m.K/W (0.7 %) for a 10 x 10 field
    at a Darcy velocity of 1e-6 m/s; with decade calls 2e-5 and 1.3e-4
    (150 m boreholes, 6 m spacing, against the same integral on a grid 500
    times finer). The table then takes five to seven calls instead of one,
    0.7 s -> 4 s for the 10 x 10 field. Conduction-only responses are the
    same either way to round-off.

    Parameters
    ----------
    response
        Object with ``response_matrix(field, soil, times) -> h[i, j, t]``
        [K per W/m], receiver index first.
    field, soil
        Passed through to ``response_matrix`` unchanged.
    depths : array_like
        Active length of each borehole [m] (a scalar for equal lengths).
    t_max_s : float
        Expected simulation length [s].
    points_per_decade : float, optional
        Node density of the grid (default 24, as ``GFunction``).
    """

    def __init__(
        self,
        response: Any,
        field: Any,
        soil: Any,
        depths: Any,
        t_max_s: float,
        *,
        points_per_decade: float = 24.0,
    ) -> None:
        if not callable(getattr(response, "response_matrix", None)):
            raise TypeError("response must provide response_matrix(field, soil, times)")
        if not (math.isfinite(t_max_s) and t_max_s > 0):
            raise ValueError("t_max_s must be positive and finite")
        if not (math.isfinite(points_per_decade) and points_per_decade >= 4):
            raise ValueError("points_per_decade must be at least 4")
        self._response = response
        self._field = field
        self._soil = soil
        self._depths = np.atleast_1d(np.asarray(depths, dtype=float))
        if self._depths.ndim != 1 or np.any(self._depths <= 0) or not np.all(np.isfinite(self._depths)):
            raise ValueError("depths must be positive and finite")
        self._ppd = float(points_per_decade)
        self.t_min = 3600.0
        self.n_builds = 0
        self._build(1.5 * float(t_max_s))

    def _build(self, t_end: float) -> None:
        t_end = max(t_end, self.t_min * 1.01)
        num = max(2, int(math.ceil(self._ppd * math.log10(t_end / self.t_min))) + 1)
        times = np.geomspace(self.t_min, t_end, num)
        # One call per decade of time. A provider may build its quadrature
        # from the time span of a call and lose accuracy when the span is wide
        # (geolink's MFLSResponse, bet-lab/geolink#581).
        parts: list[np.ndarray] = []
        for chunk in np.array_split(times, max(1, math.ceil(math.log10(t_end / self.t_min)))):
            part = np.asarray(self._response.response_matrix(self._field, self._soil, chunk), dtype=float)
            if part.ndim != 3 or part.shape[0] != part.shape[1] or part.shape[2] != len(chunk):
                raise ValueError(f"response_matrix must return h[n, n, {len(chunk)}], got shape {part.shape}")
            if parts and part.shape[0] != parts[0].shape[0]:
                raise ValueError(
                    f"response_matrix returned {part.shape[0]} boreholes after {parts[0].shape[0]} for the same field"
                )
            parts.append(part)
        h = np.concatenate(parts, axis=2)
        if len(self._depths) not in (1, h.shape[0]):
            raise ValueError(f"depths has {len(self._depths)} entries for {h.shape[0]} boreholes")
        weights = np.broadcast_to(self._depths, (h.shape[0],))
        g = np.einsum("i,ijt->t", weights, h) / float(weights.sum())
        if not np.all(np.isfinite(g)):
            raise ValueError("response_matrix returned non-finite values")
        self.x = np.concatenate(([0.0], times))
        self.y = np.concatenate(([0.0], g))
        self.t_end = float(self.x[-1])
        self._slope = np.diff(self.y) / np.diff(self.x)
        self._cum = np.concatenate(([0.0], np.cumsum(0.5 * (self.y[:-1] + self.y[1:]) * np.diff(self.x))))
        self.n_builds += 1

    def ensure_horizon(self, t: float) -> None:
        """Recompute the grid so that it covers time ``t`` [s] (no-op if it already does)."""
        t = float(t)
        if not math.isfinite(t):
            raise ValueError("g-function requested at a non-finite time")
        if t > self.t_end:
            self._build(max(1.5 * t, 2.0 * self.t_end))

    def __call__(self, t: Any) -> np.ndarray:
        t_arr = np.asarray(t, dtype=float)
        if t_arr.size:
            self.ensure_horizon(float(np.max(t_arr)))
        return np.asarray(np.interp(t_arr, self.x, self.y))

    def _integral(self, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        k = np.clip(np.searchsorted(self.x, t, side="right") - 1, 0, len(self.x) - 2)
        s = t - self.x[k]
        return k, self.y[k] * s + 0.5 * self._slope[k] * s * s

    def step_mean(self, tau: Any, dt: float) -> np.ndarray:
        """Mean of ``g`` over ``[tau, tau + dt]`` [m.K/W], exact for the interpolated ``g``."""
        dt = float(dt)
        if not (math.isfinite(dt) and dt > 0):
            raise ValueError("dt must be positive and finite")
        tau_arr = np.maximum(np.asarray(tau, dtype=float), 0.0)
        if tau_arr.size:
            self.ensure_horizon(float(np.max(tau_arr)) + dt)
        k_a, loc_a = self._integral(tau_arr)
        k_b, loc_b = self._integral(tau_arr + dt)
        return np.asarray(((self._cum[k_b] - self._cum[k_a]) + (loc_b - loc_a)) / dt)


def geolink_gfunction(
    x: Any,
    y: Any,
    H: Any,
    *,
    r_b: float,
    k_s: float,
    alpha_s: float,
    t_max_s: float,
    D: float = 0.0,
    groundwater_velocity: float = 0.0,
    groundwater_direction: float = 0.0,
    dispersivity: float = 0.0,
    rho_s: float | None = None,
    c_s: float | None = None,
    response: Any = None,
) -> ResponseMatrixGFunction:
    """Field g-function computed by ``geolink`` for boreholes at ``(x, y)``.

    Without groundwater it is geolink's native finite line source
    (boreholes may have different lengths). With ``groundwater_velocity``
    it is the moving finite line source of Molina-Giraldo et al. (2011),
    which needs equal lengths and the ground heat capacity.

    Parameters
    ----------
    x, y : array_like
        Borehole coordinates [m].
    H : float or array_like
        Active borehole length [m], one value or one per borehole.
    r_b : float
        Borehole radius [m].
    k_s, alpha_s : float
        Ground conductivity [W/(m K)] and diffusivity [m2/s].
    t_max_s : float
        Expected simulation length [s].
    D : float, optional
        Buried depth of the borehole heads [m].
    groundwater_velocity : float, optional
        Darcy velocity of the groundwater [m/s]; 0 (default) is pure conduction.
    groundwater_direction : float, optional
        Flow direction [rad], counter-clockwise from the x axis.
    dispersivity : float, optional
        Longitudinal thermal dispersivity [m] (isotropic in geolink).
    rho_s, c_s : float, optional
        Ground density [kg/m3] and specific heat [J/(kg K)]; required with
        groundwater flow (they set the thermal front velocity).
    response : optional
        A geolink response model to use instead of the two above.

    Raises
    ------
    ImportError
        If ``geolink`` is not installed.
    """
    try:
        base = importlib.import_module("geolink.response.base")
        fls = importlib.import_module("geolink.response.fls")
        mfls = importlib.import_module("geolink.response.mfls")
    except ImportError as exc:  # pragma: no cover - exercised only without geolink
        raise ImportError(
            "geolink is not installed. It is an optional, separately distributed package "
            "(bet-lab/geolink); the default ground response of tmhp does not need it."
        ) from exc
    x_arr = np.atleast_1d(np.asarray(x, dtype=float))
    y_arr = np.atleast_1d(np.asarray(y, dtype=float))
    depths = np.atleast_1d(np.asarray(H, dtype=float))
    depth: Any = float(depths[0]) if depths.size == 1 or np.all(depths == depths[0]) else depths
    field = base.BoreField(x=x_arr, y=y_arr, depth=depth, burial=float(D), radius=float(r_b))
    soil = base.Soil(conductivity=float(k_s), diffusivity=float(alpha_s))
    if response is None:
        if groundwater_velocity != 0.0:
            if rho_s is None or c_s is None:
                raise ValueError("groundwater flow needs rho_s and c_s (ground heat capacity)")
            response = mfls.MFLSResponse(
                v_gw=float(groundwater_velocity),
                theta_gw=float(groundwater_direction),
                alpha_L=float(dispersivity),
                rho_s=float(rho_s),
                c_s=float(c_s),
            )
        else:
            response = fls.FLSResponse()
    return ResponseMatrixGFunction(response, field, soil, depths, t_max_s)
