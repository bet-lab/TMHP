"""Borehole g-function and air property helpers.

Provides:
- Finite Line Source (FLS) g-function for borehole heat exchangers
- Air dynamic viscosity (Sutherland's formula) and Prandtl number
"""

from typing import TYPE_CHECKING, Any

import numpy as np
from scipy import integrate
from scipy.interpolate import interp1d
from scipy.special import erf

from . import calc_util as cu
from .borehole import (
    calc_borehole_thermal_resistance as calc_borehole_thermal_resistance,
)
from .borehole import (
    calc_effective_borehole_thermal_resistance as calc_effective_borehole_thermal_resistance,
)
from .borehole import (
    calc_local_borehole_thermal_resistance as calc_local_borehole_thermal_resistance,
)
from .constants import SP

try:
    import pygfunction as gt

    HAS_PYGFUNCTION = True
except ImportError:
    HAS_PYGFUNCTION = False

if TYPE_CHECKING:
    from .borefield import BoreField, BoreNetwork

_BOUNDARY_CONDITIONS = ("UBWT", "UHTR", "MIFT")


def f(x):
    """
    Helper function for G-function calculation.

    Parameters
    ----------
    x : float
        Input value

    Returns
    -------
    float
        f(x) = x*erf(x) - (1-exp(-x²))/√π
    """
    return x * erf(x) - (1 - np.exp(-(x**2))) / SP


def chi(s, rb, H, z0=0):
    """
    Helper function for G-function calculation.

    Parameters
    ----------
    s : float
        Integration variable
    rb : float
        Borehole radius [m]
    H : float
        Borehole height [m]
    z0 : float, optional
        Reference depth [m] (default: 0)

    Returns
    -------
    float
        chi function value
    """
    h = H * s
    d = z0 * s

    temp = np.exp(-((rb * s) ** 2)) / (h * s)
    Is = 2 * f(h) + 2 * f(h + 2 * d) - f(2 * h + 2 * d) - f(2 * d)

    return temp * Is


_GFuncCacheKey = tuple[float, float, float, float, float]
_g_func_cache: dict[_GFuncCacheKey, float | np.ndarray] = {}


def G_FLS(t: float | np.ndarray, ks: float, as_: float, rb: float, H: float) -> float | np.ndarray:
    """
    Calculate the g-function for finite line source (FLS) model.

    This function calculates the g-function used in ground source heat pump
    analysis. Results are cached for performance.

    Parameters
    ----------
    t : float
        Time [s]
    ks : float
        Ground thermal conductivity [W/mK]
    as_ : float
        Ground thermal diffusivity [m²/s]
    rb : float
        Borehole radius [m]
    H : float
        Borehole height [m]

    Returns
    -------
    float or array
        g-function value [mK/W]. Returns scalar for single time value,
        array for multiple time values.
    """
    t_arr = np.asarray(t, dtype=float)
    single = t_arr.ndim == 0
    key: _GFuncCacheKey | None = None
    if single:
        key = (
            float(round(float(t_arr), 0)),
            float(round(ks, 2)),
            float(round(as_, 6)),
            float(round(rb, 2)),
            float(round(H, 0)),
        )
        if key in _g_func_cache:
            return _g_func_cache[key]

    factor = 1 / (4 * np.pi * ks)

    lbs = 1 / np.sqrt(4 * as_ * t_arr)

    # Reshape to 1D array
    lbs = lbs.reshape(-1)

    # Pre-calculate integral from 0 to inf
    total = integrate.quad(chi, 0, np.inf, args=(rb, H))[0]
    # ODE initial value
    first = integrate.quad(chi, 0, lbs[0], args=(rb, H))[0]

    # Scipy ODE solver function form: dydx = f(y, x)
    def func(y, s):
        return chi(s, rb, H, z0=0)

    values = np.asarray(total - integrate.odeint(func, first, lbs)[:, 0], dtype=float)
    if single:
        result: float | np.ndarray = float(factor * values[0])
    else:
        result = factor * values
    if key is not None:
        _g_func_cache[key] = result
    return result


class GFunction:
    """Dimensional g-function ``g(t)`` [m·K/W] of a bore field.

    Built by :func:`precompute_gfunction`; callable like the
    ``scipy.interpolate.interp1d`` it replaces (``g(t)`` for a scalar or an
    array of times [s]).

    **Values.** pygfunction is evaluated on a log-spaced grid from
    ``t_min = 3600 s`` to the horizon, with ``g(0) = 0`` prepended;
    between nodes the value is interpolated linearly in ``t`` (relative error
    ≤ 7e-4 against direct pygfunction evaluation for the default grid
    density). Below ``t_min`` the value is the straight line from ``(0, 0)``
    to the first node: the model has no grout or fluid heat capacity, so the
    response in the first hour is only approximate.

    **Horizon.** The grid ends at ``1.5 * t_max_s``. A request beyond it does
    *not* extrapolate: the grid is recomputed by pygfunction to cover the
    request (at least twice the previous horizon, keeping the node density per
    decade). For a 10×10 field this costs about 0.2 s. A simulation longer than
    ``t_max_s`` therefore gets the same values it would have got with a larger
    ``t_max_s``.

    **Step mean.** :meth:`step_mean` returns ``(1/dt) ∫_τ^{τ+dt} g(s) ds``,
    integrated exactly for the piecewise-linear ``g``. This is the response a
    time-stepping model needs when its step ``n`` represents the interval
    ``[t_n, t_n + dt]`` and a load pulse starts at ``t_i``: the mean wall
    temperature over the step is ``T_s - Σ Δq_i · step_mean(t_n - t_i, dt)``.
    It includes the step's own pulse (``τ = 0``) and gives the same interval
    means for any ``dt`` that divides the load's own resolution.

    **Any bore field.** The constructor builds an ``N_1 × N_2`` rectangle.
    :meth:`from_field` takes any :class:`~tmhp.borefield.BoreField` (arbitrary
    coordinates and per-borehole depths) and, with the ``'MIFT'`` boundary
    condition, a :class:`~tmhp.borefield.BoreNetwork` (pipes, flow, series or
    parallel connections). Grid, first hour, horizon and :meth:`step_mean`
    behave the same way for every field. Such a g-function also returns the
    per-borehole split of the field load (:meth:`borehole_load_shares`).
    """

    #: Minimum node density of the log-spaced grid [nodes per decade].
    POINTS_PER_DECADE = 24.0

    # Set by from_field only: borehole order handed to pygfunction, and solver.
    _order: np.ndarray
    _method: str

    def __init__(
        self,
        N_1: int,
        N_2: int,
        B: float,
        H_b: float,
        D_b: float,
        r_b: float,
        alpha_s: float,
        k_s: float,
        t_max_s: float,
        dt_s: float,
        boundary_condition: str = "UBWT",
        num: int = 100,
    ) -> None:
        _require_pygfunction()
        bc = boundary_condition.upper()
        if bc not in ("UBWT", "UHTR"):
            raise ValueError(f"boundary_condition must be 'UBWT' or 'UHTR', got {boundary_condition!r}")
        _check_horizon(t_max_s, dt_s)
        self._field: dict[str, Any] | None = dict(N_1=N_1, N_2=N_2, B_1=B, B_2=B, H=H_b, D=D_b, r_b=r_b)
        self.bore_field: BoreField | None = None
        self.network: BoreNetwork | None = None
        self._setup(alpha_s, k_s, bc, dt_s)
        self._build(float(t_max_s) * 1.5, int(num))

    @classmethod
    def from_field(
        cls,
        field: "BoreField",
        alpha_s: float,
        k_s: float,
        t_max_s: float,
        *,
        boundary_condition: str = "UBWT",
        network: "BoreNetwork | None" = None,
        dt_s: float = 3600.0,
        num: int = 100,
        method: str | None = None,
    ) -> "GFunction":
        """g-function of an arbitrary bore field.

        Parameters
        ----------
        field : BoreField
            Borehole positions, depths, buried depths and radius.
        alpha_s, k_s : float
            Ground thermal diffusivity [m²/s] and conductivity [W/(m·K)].
        t_max_s : float
            Expected simulation length [s] (grid to ``1.5 * t_max_s``).
        boundary_condition : {'UBWT', 'UHTR', 'MIFT'}
            ``'MIFT'`` (mixed inlet fluid temperature, Cimmino 2015/2019)
            couples the boreholes through the fluid and needs ``network``;
            its g is the *effective* borehole wall temperature, defined by
            ``T_b* = (T_in + T_out)/2 + q'·R_field`` with ``R_field`` the
            network's field resistance (``q'`` field load per metre,
            extraction positive), so the host's ``T_f = T_wall − q'·R_b``
            holds with ``R_b = R_field``.
        network : BoreNetwork, optional
            Pipes, total flow and connections; required for ``'MIFT'`` only.
            Its ``k_s`` must equal ``k_s``. The g-function is valid for that
            flow only.
        dt_s, num : as in the constructor.
        method : str, optional
            pygfunction solver. Default ``'equivalent'`` (as the constructor),
            ``'similarities'`` when a MIFT network has series connections
            (``'equivalent'`` does not support them). ``'equivalent'`` load
            shares are per cluster of similar boreholes (see
            :meth:`borehole_load_shares`).

        Notes
        -----
        The boreholes are handed to pygfunction sorted by (depth, buried
        depth, y, x). The g-function is a field mean, so the order is
        physically irrelevant, but pygfunction 2.3.1's ``'equivalent'``
        solver assigns boreholes to clusters in the wrong order when
        boreholes of different depths are interleaved (depths 100/200/100/
        200/100 m on a line give a g 5 % off its ``'similarities'`` value);
        sorted input avoids it. A rectangle is already in this order.
        """
        _require_pygfunction()
        bc = str(boundary_condition).upper()
        if bc not in _BOUNDARY_CONDITIONS:
            raise ValueError(f"boundary_condition must be one of {_BOUNDARY_CONDITIONS}, got {boundary_condition!r}")
        _check_horizon(t_max_s, dt_s)
        if bc == "MIFT":
            if network is None:
                raise ValueError("boundary_condition 'MIFT' needs a network (pipes, flow and connections)")
            if float(network.k_s) != float(k_s):
                raise ValueError(f"network.k_s ({network.k_s!r}) must equal k_s ({k_s!r})")
            network.check_field(field)
        elif network is not None:
            raise ValueError(f"a network is used by the 'MIFT' boundary condition only, not {bc!r}")
        if method is None:
            series = bc == "MIFT" and network is not None and not network.is_parallel(field.n_boreholes)
            method = "similarities" if series else "equivalent"
        self = cls.__new__(cls)
        self._field = None
        self.bore_field = field
        self.network = network
        self._method = str(method)
        # Canonical order (see Notes); rounding keeps equal-within-1e-6 m
        # depths together so pygfunction's tolerance groups stay contiguous.
        self._order = np.lexsort((field.x, field.y, np.round(field.D, 6), np.round(field.H, 6)))
        self._setup(alpha_s, k_s, bc, dt_s)
        self._build(float(t_max_s) * 1.5, int(num))
        return self

    def _setup(self, alpha_s: float, k_s: float, bc: str, dt_s: float) -> None:
        self.alpha_s = float(alpha_s)
        self.k_s = float(k_s)
        self.boundary_condition = bc
        # Kept for reference only: the grid and every response are independent
        # of the step the model was constructed with (the run's own step is
        # passed to step_mean), so a model built with one dt_s and run with
        # another gives the same answer as one built with the run's dt_s.
        self.dt_s = float(dt_s)
        # Evaluate from 1 hour to bypass the short-term numerical noise (Fo < 0.1)
        # of the finite line source BEM discretization. Fixed, not max(dt_s, 1 h):
        # a dt-dependent first node made g on [0, dt_s] a straight line.
        self.t_min = 3600.0
        self.n_builds = 0
        self._shares: np.ndarray | None = None

    # -- grid -------------------------------------------------------------
    def _evaluate(self, times: np.ndarray) -> np.ndarray:
        """Dimensional g [m·K/W] at ``times`` (and, for a BoreField, the load shares)."""
        if self._field is not None:
            field = gt.borefield.Borefield.rectangle_field(**self._field)
            gfunc_obj = gt.gfunction.gFunction(
                field, self.alpha_s, time=times, boundary_condition=self.boundary_condition
            )
            return np.asarray(gfunc_obj.gFunc, dtype=float) / (2 * np.pi * self.k_s)
        assert self.bore_field is not None
        order = self._order
        kwargs: dict[str, Any] = dict(
            time=times,
            method=self._method,
            boundary_condition=self.boundary_condition,
            options={"profiles": True, "disp": False},
        )
        if self.boundary_condition == "MIFT":
            assert self.network is not None
            source = self.network.to_pygfunction(self.bore_field, order)
            kwargs.update(m_flow_network=float(self.network.m_flow_total), cp_f=float(self.network.cp_f))
        else:
            f = self.bore_field
            source = gt.borefield.Borefield(f.H[order], f.D[order], f.r_b, f.x[order], f.y[order])
        gfunc_obj = gt.gfunction.gFunction(source, self.alpha_s, **kwargs)
        shares_sorted = _borehole_shares(gfunc_obj.solver, self.bore_field.H[order], len(times))
        shares = np.empty_like(shares_sorted)
        shares[order] = shares_sorted
        # At t = 0 pygfunction's linearisation gives a uniform load per metre.
        start = (self.bore_field.H / self.bore_field.total_length)[:, None]
        self._shares = np.hstack((start, shares))
        return np.asarray(gfunc_obj.gFunc, dtype=float) / (2 * np.pi * self.k_s)

    def _build(self, t_end: float, num: int) -> None:
        t_end = max(t_end, self.t_min * 1.01)
        times = np.geomspace(self.t_min, t_end, num=num)
        g_vals = self._evaluate(times)
        # Prepend g(0) = 0: the first hour is the line from the origin.
        self.x = np.concatenate(([0.0], times))
        self.y = np.concatenate(([0.0], g_vals))
        self.t_end = float(self.x[-1])
        self._slope = np.diff(self.y) / np.diff(self.x)
        self._cum = np.concatenate(([0.0], np.cumsum(0.5 * (self.y[:-1] + self.y[1:]) * np.diff(self.x))))
        self.n_builds += 1

    def borehole_load_shares(self, t) -> np.ndarray:
        """Fraction of the field load carried by each borehole after a unit load step.

        Returns an ``(n_boreholes, n_times)`` array for times ``t`` [s] (field
        order); each column sums to 1. Solved by pygfunction together with
        the g-function (heat rate per segment, weighted by segment length)
        and interpolated linearly in time between grid nodes. At ``t = 0``
        the share is ``H_i / ΣH`` (pygfunction's linearisation of the first
        instants), and the line from there to the first node covers the
        first hour. With pygfunction's ``'equivalent'`` solver (the default)
        boreholes in one cluster share its load per metre; ``'similarities'``
        or ``'detailed'`` resolve every borehole. Available for g-functions
        built by :meth:`from_field`.
        """
        if self._shares is None:
            raise RuntimeError("load shares are only available for g-functions built with GFunction.from_field")
        t_arr = np.atleast_1d(np.asarray(t, dtype=float))
        if t_arr.ndim != 1:
            raise ValueError("t must be a scalar or a 1-D array of times")
        if t_arr.size:
            if np.any(t_arr < 0):
                raise ValueError("load shares requested at a negative time")
            self.ensure_horizon(float(np.max(t_arr)))
        shares = self._shares
        out = np.array([np.interp(t_arr, self.x, row) for row in shares])
        return np.asarray(out / out.sum(axis=0, keepdims=True), dtype=float)

    def ensure_horizon(self, t: float) -> None:
        """Recompute the grid so that it covers time ``t`` [s] (no-op if it already does)."""
        t = float(t)
        if not np.isfinite(t):
            raise ValueError("g-function requested at a non-finite time")
        if t <= self.t_end:
            return
        t_end = max(1.5 * t, 2.0 * self.t_end)
        decades = np.log10(t_end / self.t_min)
        num = max(len(self.x) - 1, int(np.ceil(self.POINTS_PER_DECADE * decades)) + 1)
        self._build(t_end, num)

    # -- evaluation ---------------------------------------------------------
    def __call__(self, t):
        t_arr = np.asarray(t, dtype=float)
        if t_arr.size:
            self.ensure_horizon(float(np.max(t_arr)))
        return np.interp(t_arr, self.x, self.y)

    def _integral(self, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """``∫_0^t g`` split as (node part, in-segment part) to limit cancellation."""
        k = np.clip(np.searchsorted(self.x, t, side="right") - 1, 0, len(self.x) - 2)
        s = t - self.x[k]
        return k, self.y[k] * s + 0.5 * self._slope[k] * s * s

    def step_mean(self, tau, dt: float):
        """Mean of ``g`` over ``[tau, tau + dt]`` [m·K/W], exact for the interpolated ``g``."""
        dt = float(dt)
        if not (np.isfinite(dt) and dt > 0):
            raise ValueError("dt must be positive and finite")
        tau_arr = np.maximum(np.asarray(tau, dtype=float), 0.0)
        if tau_arr.size:
            self.ensure_horizon(float(np.max(tau_arr)) + dt)
        k_a, loc_a = self._integral(tau_arr)
        k_b, loc_b = self._integral(tau_arr + dt)
        return ((self._cum[k_b] - self._cum[k_a]) + (loc_b - loc_a)) / dt


def _require_pygfunction() -> None:
    if not HAS_PYGFUNCTION:
        raise ImportError(
            "pygfunction is not installed. Run `uv pip install pygfunction` to use multi-borehole features."
        )


def _check_horizon(t_max_s: float, dt_s: float) -> None:
    if not (np.isfinite(t_max_s) and t_max_s > 0 and np.isfinite(dt_s) and dt_s > 0):
        raise ValueError("t_max_s and dt_s must be positive and finite")


def _borehole_shares(solver: Any, H: np.ndarray, nt: int) -> np.ndarray:
    """Per-borehole share of the field heat rate from a pygfunction solver's profiles.

    ``solver.Q_b`` is the heat rate per metre of each segment, normalised so
    that ``Σ Q_b·L_seg = ΣH``; ``H`` are the borehole lengths in the order the
    solver received them. The ``'equivalent'`` solver holds one entry per
    cluster of similar boreholes (``solver.clusters``); every member carries
    its cluster's heat rate per metre.
    """
    n = len(H)
    Q_b = np.asarray(solver.Q_b, dtype=float)
    if Q_b.ndim == 0:  # 'UHTR': uniform heat rate per metre everywhere
        return np.tile((H / H.sum())[:, None], (1, nt))
    clusters = getattr(solver, "clusters", None)
    owner = np.arange(n) if clusters is None else np.asarray([int(np.ravel(c)[0]) for c in clusters])
    if owner.shape != (n,):
        raise RuntimeError("pygfunction solver returned an unexpected borehole-to-cluster map")
    heat = np.empty((n, nt))
    for i in range(n):
        c = int(owner[i])
        seg = slice(solver._i0Segments[c], solver._i1Segments[c])
        ratios = np.asarray(solver.segment_ratios[c], dtype=float)
        heat[i] = H[i] * (ratios @ Q_b[seg])
    return np.asarray(heat / heat.sum(axis=0, keepdims=True), dtype=float)


# Gauss–Legendre nodes for the step mean of an arbitrary g(t) callable.
_GL_NODES, _GL_WEIGHTS = np.polynomial.legendre.leggauss(8)


def g_step_mean(g, tau, dt: float):
    """Step-mean response ``(1/dt) ∫_τ^{τ+dt} g(s) ds`` for any g-function callable.

    Uses :meth:`GFunction.step_mean` (exact) when available; any other
    callable ``g(t)`` (a user or test g-function) is integrated with an
    8-point Gauss–Legendre rule per step.
    """
    method = getattr(g, "step_mean", None)
    if method is not None:
        return method(tau, dt)
    tau_arr = np.maximum(np.asarray(tau, dtype=float), 0.0)
    nodes = tau_arr[..., None] + 0.5 * dt * (_GL_NODES + 1.0)
    vals = np.asarray(g(nodes.reshape(-1)), dtype=float).reshape(nodes.shape)
    return 0.5 * vals @ _GL_WEIGHTS


def precompute_gfunction(
    N_1: int,
    N_2: int,
    B: float,
    H_b: float,
    D_b: float,
    r_b: float,
    alpha_s: float,
    k_s: float,
    t_max_s: float,
    dt_s: float,
    boundary_condition: str = "UBWT",
) -> GFunction:
    """Precompute the g-function with pygfunction and return it as a callable.

    Creates a rectangular borehole field and computes the g-function
    for log-spaced time steps up to ``1.5 * t_max_s``. Returns a
    :class:`GFunction` mapping time [s] to the g-function [mK/W]; a request
    past that horizon recomputes the grid instead of extrapolating (see
    :class:`GFunction`).

    The g-function is built from the finite line source (FLS) solution
    of Claesson and Javed (2011), extended to boreholes at different
    vertical positions by Cimmino and Bernier (2014).  Because a
    borehole has finite length, the wall temperature is not uniform
    along its depth, so an axial boundary condition must be assumed:

    - ``'UHTR'`` (uniform heat transfer rate): every depth exchanges the
      same heat per unit length; the wall temperature then varies with
      depth and the g-function is its depth-average.
    - ``'UBWT'`` (uniform borehole wall temperature): the wall is at a
      single temperature over the whole depth and the heat flux varies
      instead.  This is Eskilson's original definition and is the more
      realistic of the two for a circulated borehole.

    Parameters
    ----------
    N_1 : int
        Number of boreholes in x-direction.
    N_2 : int
        Number of boreholes in y-direction.
    B : float
        Borehole spacing [m].
    H_b : float
        Borehole depth/length [m].
    D_b : float
        Buried depth [m].
    r_b : float
        Borehole radius [m].
    alpha_s : float
        Ground thermal diffusivity [m²/s].
    k_s : float
        Ground thermal conductivity [W/mK].
    t_max_s : float
        Expected simulation length [s]. The grid covers ``1.5 * t_max_s``;
        longer requests trigger a recompute, never an extrapolation.
    dt_s : float
        Construction timestep [s], kept on the result as ``dt_s``. It does not
        shape the grid (first node always 3600 s) or any response: time-stepping
        hosts pass their run's step to :meth:`GFunction.step_mean`.
    boundary_condition : str, optional
        Axial boundary condition along the borehole. Either ``'UBWT'``
        (uniform borehole wall temperature) or ``'UHTR'`` (uniform heat
        transfer rate). Default is ``'UBWT'``.

    Returns
    -------
    GFunction
        Callable mapping `time [s]` to `g-function [mK/W]`, with
        :meth:`GFunction.step_mean` for time-stepping models.

    References
    ----------
    Claesson, J., & Javed, S. (2011). An analytical method to calculate
    borehole fluid temperatures for time scales from minutes to decades.
    ASHRAE Transactions, 117(2), 279-288.

    Cimmino, M., & Bernier, M. (2014). A semi-analytical method to
    generate g-functions for geothermal bore fields. International
    Journal of Heat and Mass Transfer, 70, 641-650.
    """
    return GFunction(
        N_1=N_1,
        N_2=N_2,
        B=B,
        H_b=H_b,
        D_b=D_b,
        r_b=r_b,
        alpha_s=alpha_s,
        k_s=k_s,
        t_max_s=t_max_s,
        dt_s=dt_s,
        boundary_condition=boundary_condition,
    )


# ---------------------------------------------------------------------------
# Moving finite line source (MFLS) — UNUSED, UNVERIFIED
# ---------------------------------------------------------------------------
# ``chi_mfls``, ``G_MFLS_Field`` and ``precompute_gfunction_mls`` are not called
# anywhere in tmhp (checked 2026-11-13, #96) and their accuracy has never been
# checked against a reference MFLS solution. ``precompute_gfunction_mls`` still
# extrapolates linearly past ``1.5 * t_max_s`` (the defect fixed for
# :func:`precompute_gfunction`). Kept until the co-authors confirm whether any
# external work uses them; do not rely on them.


def chi_mfls(s, r, H, x_prime, U, alpha_s, z0=0):
    """
    Helper function for MFLS (Moving Finite Line Source) G-function calculation.

    .. warning:: Unused in tmhp and not verified against a reference MFLS
       solution (#96). Kept pending co-author confirmation.

    Ref: Molina-Giraldo et al. (2011), "A moving finite line source model
    to simulate borehole heat exchangers with groundwater advection"
    """
    if s == 0:
        return 0.0
    val = chi(s, r, H, z0)

    # Advective multiplier
    adv_mult = np.exp((U * x_prime) / (2 * alpha_s) - (U**2) / (16 * (alpha_s**2) * (s**2)))
    return val * adv_mult


def G_MFLS_Field(
    times: np.ndarray,
    boreholes: list,
    v_gw: float,
    theta_gw: float,
    rho_w: float,
    c_w: float,
    alpha_s: float,
    k_s: float,
    rho_s: float,
    c_s: float,
) -> np.ndarray:
    """Calculate the spatial superposition of the MFLS response for a bore field.

    .. warning:: Unused in tmhp and not verified against a reference MFLS
       solution (#96). Kept pending co-author confirmation.

    Parameters
    ----------
    times : np.ndarray
        Array of time values [s]
    boreholes : list
        List of pygfunction Borehole objects
    v_gw : float
        Darcy velocity of groundwater [m/s]
    theta_gw : float
        Direction of groundwater flow [rad]
    rho_w : float
        Density of groundwater [kg/m³]
    c_w : float
        Specific heat capacity of groundwater [J/kgK]
    alpha_s : float
        Ground thermal diffusivity [m²/s]
    k_s : float
        Ground thermal conductivity [W/mK]
    rho_s : float
        Density of ground [kg/m³]
    c_s : float
        Specific heat capacity of ground [J/kgK]

    Returns
    -------
    np.ndarray
        Dimensional g-values for the entire field over time [mK/W]
    """
    U = v_gw * (rho_w * c_w) / (rho_s * c_s)
    N_bh = len(boreholes)

    field_g_vals = np.zeros(len(times))
    factor = 1 / (4 * np.pi * k_s)

    # Evaluate integrals for each pair
    # To optimize, we loop through times and pairs
    for i, b_i in enumerate(boreholes):
        for j, b_j in enumerate(boreholes):
            dx = b_i.x - b_j.x
            dy = b_i.y - b_j.y
            r = np.sqrt(dx**2 + dy**2)

            # Using r_b for self-response
            if i == j:
                r = b_i.r_b
                x_prime = 0.0
            else:
                x_prime = dx * np.cos(theta_gw) + dy * np.sin(theta_gw)

            H = b_j.H
            D = b_j.D

            for t_idx, t in enumerate(times):
                if t <= 0:
                    continue
                lbs = 1 / np.sqrt(4 * alpha_s * t)

                # Single integration from lbs to infinity
                # For high limits, quad works effectively
                integral_val = integrate.quad(chi_mfls, lbs, np.inf, args=(r, H, x_prime, U, alpha_s, D), limit=100)[0]

                # Each source influences target, so we accumulate the dimensional temp rise
                field_g_vals[t_idx] += factor * integral_val

    # Average temperature response of the field
    field_g_vals /= N_bh
    return field_g_vals


def precompute_gfunction_mls(
    N_1: int,
    N_2: int,
    B: float,
    H_b: float,
    D_b: float,
    r_b: float,
    alpha_s: float,
    k_s: float,
    rho_s: float,
    c_s: float,
    v_gw: float,
    theta_gw: float,
    rho_w: float,
    c_w: float,
    t_max_s: float,
    dt_s: float,
) -> interp1d:
    """Precompute the MFLS g-function and return an interpolator.

    .. warning:: Unused in tmhp and not verified against a reference MFLS
       solution (#96). Unlike :func:`precompute_gfunction`, the returned
       interpolator extrapolates linearly in time past ``1.5 * t_max_s``.
       Kept pending co-author confirmation.
    """
    if not HAS_PYGFUNCTION:
        raise ImportError("pygfunction is not installed.")

    t_min = max(dt_s, 3600.0)
    times = np.geomspace(t_min, t_max_s * 1.5, num=50)

    boreField = gt.borefield.Borefield.rectangle_field(N_1=N_1, N_2=N_2, B_1=B, B_2=B, H=H_b, D=D_b, r_b=r_b)

    g_vals_dim = G_MFLS_Field(
        times=times,
        boreholes=boreField.to_boreholes(),
        v_gw=v_gw,
        theta_gw=theta_gw,
        rho_w=rho_w,
        c_w=c_w,
        alpha_s=alpha_s,
        k_s=k_s,
        rho_s=rho_s,
        c_s=c_s,
    )

    times = np.concatenate(([0.0], times))
    g_vals_dim = np.concatenate(([0.0], g_vals_dim))

    return interp1d(times, g_vals_dim, kind="linear", bounds_error=False, fill_value="extrapolate")


def air_dynamic_viscosity(T_K):
    """
    Calculate air dynamic viscosity using Sutherland's formula.

    Parameters
    ----------
    T_K : float
        Temperature [K]

    Returns
    -------
    float
        Dynamic viscosity [Pa·s]

    Reference: Sutherland's formula for air
    mu = mu0 * (T/T0)^1.5 * (T0 + S) / (T + S)
    where mu0 = 1.716e-5 Pa·s at T0 = 273.15 K, S = 110.4 K
    """
    T0 = cu.C2K(0)  # Reference temperature [K]
    mu0 = 1.716e-5  # Reference viscosity [Pa·s] at T0
    S = 110.4  # Sutherland constant [K] for air

    mu = mu0 * ((T_K / T0) ** 1.5) * ((T0 + S) / (T_K + S))
    return mu


def air_prandtl_number(T_K):
    """
    Calculate air Prandtl number.

    Parameters
    ----------
    T_K : float
        Temperature [K]

    Returns
    -------
    float
        Prandtl number [-]

    Note: Pr ≈ 0.71 for air at typical temperatures (20-50°C)
    Temperature dependence is weak, so using constant value.
    """
    # Pr = mu * cp / k
    # For air: Pr ≈ 0.71 (weak temperature dependence)
    return 0.71


def calc_submerged_coil_thermal_resistance(
    r_out: float,
    r_in: float,
    D_s: float,
    k_p: float,
    m_flow_pipe: float,
    rho_f: float,
    mu_f: float,
    cp_f: float,
    k_f: float,
    v_river: float = 0.5,
) -> float:
    """Calculate the local thermal resistance [mK/W] of a submerged surface water heat exchanger coil.

    Uses the Churchill-Bernstein correlation for cross-flow forced convection over a cylinder
    to estimate the external (river water) convective heat transfer coefficient.
    It tricks pygfunction's SingleUTube model into capturing this pure pipe resistance
    without any ground thermal mass by assigning exceptionally high thermal conductivities
    to the grout and ground.

    Parameters
    ----------
    r_out : float
        Pipe outer radius [m]
    r_in : float
        Pipe inner radius [m]
    D_s : float
        Shank spacing (half distance between pipes) [m]
    k_p : float
        Pipe thermal conductivity [W/mK]
    m_flow_pipe : float
        Mass flow rate per pipe [kg/s]
    rho_f : float
        Internal fluid density [kg/m³]
    mu_f : float
        Internal fluid dynamic viscosity [Pa·s]
    cp_f : float
        Internal fluid specific heat capacity [J/kgK]
    k_f : float
        Internal fluid thermal conductivity [W/mK]
    v_river : float
        Velocity of the river water cross-flow [m/s]

    Returns
    -------
    float
        Thermal resistance of the submerged coil [mK/W].
    """
    if not HAS_PYGFUNCTION:
        raise ImportError("pygfunction is not installed.")

    # 1. Convective resistance inside the pipe
    if m_flow_pipe > 0:
        h_in = gt.pipes.convective_heat_transfer_coefficient_circular_pipe(
            m_flow_pipe, r_in, mu_f, rho_f, k_f, cp_f, epsilon=1e-6
        )
        R_conv_in = 1.0 / (2.0 * np.pi * r_in * h_in)
    else:
        R_conv_in = 10.0

    # 2. Conduction resistance of the pipe wall
    R_cond = gt.pipes.conduction_thermal_resistance_circular_pipe(r_in, r_out, k_p)

    # 3. External (river water) convective resistance
    # Reference river properties at ~15 degC
    rho_w = 999.1
    mu_w = 0.001138
    cp_w = 4187.0
    k_w = 0.589

    D_out = 2.0 * r_out
    Re_D = (rho_w * v_river * D_out) / mu_w
    Pr = (cp_w * mu_w) / k_w

    if Re_D * Pr >= 0.2:
        Nu = (
            0.3
            + (0.62 * Re_D**0.5 * Pr ** (1 / 3))
            / (1 + (0.4 / Pr) ** (2 / 3)) ** 0.25
            * (1 + (Re_D / 282000.0) ** (5 / 8)) ** 0.8
        )
    else:
        Nu = 10.0

    h_ext = (Nu * k_w) / D_out
    R_conv_ext = 1.0 / (2.0 * np.pi * r_out * h_ext)

    # 4. Total fluid-to-river equivalent resistance
    R_fp_total = R_conv_in + R_cond + R_conv_ext

    # 5. The Pygfunction Trick
    # We assign a massive conductivity to nullify the grout/ground resistance
    k_g_trick = 1e6
    k_s_trick = 1e6

    # Set up a dummy borehole around the submerged U-tube structure
    r_b_trick = D_s + r_out + 0.01
    borehole = gt.boreholes.Borehole(H=100.0, D=0.0, r_b=r_b_trick, x=0.0, y=0.0)

    # The two legs of the U-tube
    pos = [(-D_s, 0.0), (D_s, 0.0)]
    pipe = gt.pipes.SingleUTube(pos, r_in, r_out, borehole, k_s_trick, k_g_trick, R_fp_total)

    return float(pipe.local_borehole_thermal_resistance())
