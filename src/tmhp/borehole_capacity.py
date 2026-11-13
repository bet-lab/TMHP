"""Borehole heat capacity and short-time ground response.

The steady borehole model used so far has two short-time defects that act in
opposite directions:

- the borehole-wall response is a *line* source evaluated at ``r_b``, valid
  only after ``Fo = alpha t / r_b^2`` of about 5 (several hours), and before
  one hour :class:`~tmhp.g_function.GFunction` is a straight line from the
  origin; and
- the fluid-to-wall temperature difference ``q' R_b`` appears instantly,
  whereas the fluid, pipe and grout store heat and the fluid temperature
  moves over one to three hours.

This module replaces both by one load-step response of the borehole cross
section, the 1-D radial *equivalent-cylinder model* (ECM) of Xu and Spitler
(2006)::

    fluid node (C_f) - R_in - node at r_eq (C_p)
      - grout annulus r_eq..r_b (k_g,eq, rho c_g) - wall node (r_b, no capacity)
      - ground r_b..r_far (k_s, rho c_s) - T = 0 at r_far

``r_eq = sqrt(n) r_po`` keeps the grout cross-section (and so its heat
capacity) equal to the real one, ``R_in = R_fp / n`` is the fluid-to-pipe
resistance of the ``n`` pipes in parallel, and the equivalent grout
conductivity is chosen so that the steady resistance of the model is exactly
the target ``R_b`` (by default the multipole/Hellstrom value the host already
uses)::

    R_b = R_in + ln(r_b / r_eq) / (2 pi k_g,eq)

The model is discretised with log-spaced finite volumes (conductance between
cell centres ``2 pi k / ln(r_{i+1}/r_i)``, exact for steady radial
conduction) and backward Euler in time; energy is conserved to round-off.

Splicing onto the long-time g-function
--------------------------------------
For a unit load step the ECM gives the mean-fluid response ``S_cap(t)`` and
the wall response ``W_cap(t)`` [m.K/W]. A g-function built from finite line
sources behaves at short times like an infinite line source evaluated at
``r_b``, which already fills the borehole with soil. The dynamic response is
therefore the Yavuzturk-Spitler splice

    fluid:  S_dyn(t) = g(t) - G_ILS(r_b, t) + S_cap(t)
    wall :  W_dyn(t) = g(t) - G_ILS(r_b, t) + W_cap(t)

with ``G_ILS = E1(r_b^2 / (4 alpha t)) / (4 pi k_s)``. Both corrections vanish
at long times (``S_cap -> R_b + G_ILS``), so nothing changes after a few
days. :class:`DynamicGFunction` tabulates

    K_f(t) = S_dyn(t) - R_b      ("fluid-referenced" kernel, K_f(0) = -R_b)
    K_w(t) = W_dyn(t)            (physical wall kernel)

and is a drop-in replacement for the g-function: a host that computes
``T_f = T_s - sum(dq * K_f) - q' R_b`` (the unchanged steady formula) obtains
the dynamic mean fluid temperature, exact for piecewise-constant loads.

Limits
------
One-dimensional cross-section (the pipe layout enters only through ``R_b``):
against a 2-D finite-element reference the correction is within about 5 % of
``R_b`` between 0.5 h and 2 h and 1.6 % after 4 h for common DN32 sections
(geolink technical note, see below). The fluid is one node: the transit time
of the fluid through the U-tube (minutes) and the fluid outside the borehole
are not resolved.

Provenance
----------
Ported from ``geolink.borehole.equivalent_cylinder`` (bet-lab/geolink, MIT
License, Copyright (c) 2026 betlab), which documents the derivation and its
verification in ``docs/technote/borehole-capacity-equivalent-cylinder.tex``.
The equivalent-radius mapping follows Xu and Spitler (2006) as implemented in
GHEDesigner (BSD-3-Clause); no GHEDesigner code is included.

References
----------
Xu, X., & Spitler, J. D. (2006). Modeling of vertical ground loop heat
exchangers with variable convective resistance and thermal mass of the fluid.
Proc. Ecostock 2006.

Yavuzturk, C., & Spitler, J. D. (1999). A short time step response factor
model for vertical ground loop heat exchangers. ASHRAE Transactions, 105(2),
475-485.

Carslaw, H. S., & Jaeger, J. C. (1959). Conduction of Heat in Solids (2nd
ed.), section 13.5. Oxford University Press.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

import numpy as np
from scipy.integrate import quad_vec
from scipy.linalg import solve_banded
from scipy.special import exp1, j1, y1

from .constants import c_w, k_w, mu_w, rho_w

__all__ = [
    "RHOC_GROUT_DEFAULT",
    "RHOC_PIPE_DEFAULT",
    "CapacityStepResponse",
    "DynamicGFunction",
    "EquivalentCylinderBorehole",
    "cylinder_source_wall",
    "enable_borehole_dynamics",
    "fluid_to_pipe_resistance",
    "line_source_wall",
    "with_borehole_capacity",
]

#: Volumetric heat capacity of grout [J/(m3 K)] (bentonite-based grout; the
#: GLHEPro / GHEDesigner default). Thermally enhanced grouts are 3.0-3.5e6.
RHOC_GROUT_DEFAULT = 3.9e6
#: Volumetric heat capacity of a HDPE pipe wall [J/(m3 K)] (950 kg/m3 x 1900 J/(kg K)).
RHOC_PIPE_DEFAULT = 1.8e6

# Discretisation defaults. With these the capacity response differs from the
# converged one by less than 2e-4 m.K/W (0.1 % of a typical R_b); see
# tests/test_borehole_capacity.py.
_CELLS_PER_DECADE = 40
_MIN_GROUT_CELLS = 12
_DT_MIN = 1.0
_STEP_FRACTION = 0.01
_FAR_FIELD_FACTOR = 8.0


# ---------------------------------------------------------------------------
# Analytical ground responses at the borehole wall (unit load per metre)
# ---------------------------------------------------------------------------


def line_source_wall(times: Any, k_s: float, alpha_s: float, r_b: float) -> np.ndarray:
    """Infinite line source at ``r_b``: ``E1(r_b^2 / (4 alpha t)) / (4 pi k_s)`` [m.K/W]."""
    t = np.asarray(times, dtype=float)
    out = np.zeros_like(t)
    pos = t > 0
    out[pos] = exp1(r_b**2 / (4.0 * alpha_s * t[pos])) / (4.0 * math.pi * k_s)
    return out


def cylinder_source_wall(times: Any, k_s: float, alpha_s: float, r_b: float) -> np.ndarray:
    """Hollow cylindrical source: wall temperature of ``r > r_b`` for a unit flux at ``r_b`` [m.K/W].

    Carslaw and Jaeger (1959), constant flux on the surface of a cylindrical
    cavity in an infinite medium, evaluated at the surface::

        G = 2 / (pi^3 k) * int_0^inf (1 - exp(-Fo b^2)) / (b^3 (J1(b)^2 + Y1(b)^2)) db,
        Fo = alpha t / r_b^2

    integrated in ``u = ln b`` for all times at once. Above
    ``B = max(200, 12 / sqrt(Fo_min))`` the integrand is replaced by its
    closed-form tail ``(pi/2) (1/B - 1/(8 B^3))``.
    """
    t = np.atleast_1d(np.asarray(times, dtype=float))
    if np.any(t <= 0) or not np.all(np.isfinite(t)):
        raise ValueError("times must be positive and finite")
    fo = alpha_s * t / r_b**2
    b_hi = max(200.0, 12.0 / math.sqrt(float(fo.min())))
    u_lo = 0.5 * math.log(1e-18 / float(fo.max()))

    def integrand(u: float) -> np.ndarray:
        b = math.exp(u)
        modulus = float(j1(b)) ** 2 + float(y1(b)) ** 2
        return np.asarray(-np.expm1(-fo * b * b) / (b * b * modulus))

    body, _ = quad_vec(integrand, u_lo, math.log(b_hi), epsabs=0.0, epsrel=1e-12, norm="max", limit=4000)
    tail = 0.5 * math.pi * (1.0 / b_hi - 1.0 / (8.0 * b_hi**3))
    return np.asarray(2.0 / (math.pi**3 * k_s) * (np.asarray(body) + tail))


def fluid_to_pipe_resistance(
    m_flow_pipe: float,
    r_in: float,
    r_out: float,
    k_p: float,
    rho_f: float = rho_w,
    mu_f: float = mu_w,
    cp_f: float = c_w,
    k_f: float = k_w,
) -> float:
    """Fluid-to-outer-pipe-wall resistance of one pipe ``R_fp`` [m.K/W].

    Convection inside the pipe (the correlation pygfunction uses for the
    borehole resistance) plus conduction through the pipe wall.
    """
    import pygfunction as gt

    if not (math.isfinite(m_flow_pipe) and m_flow_pipe > 0):
        raise ValueError("m_flow_pipe must be positive and finite")
    h_f = gt.pipes.convective_heat_transfer_coefficient_circular_pipe(
        m_flow_pipe, r_in, mu_f, rho_f, k_f, cp_f, epsilon=1e-6
    )
    r_cond = gt.pipes.conduction_thermal_resistance_circular_pipe(r_in, r_out, k_p)
    return float(1.0 / (2.0 * math.pi * r_in * h_f) + r_cond)


# ---------------------------------------------------------------------------
# Equivalent-cylinder borehole
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapacityStepResponse:
    """Response of the borehole section to a unit load step [m.K/W = K per W/m].

    Attributes
    ----------
    times : np.ndarray
        Evaluation times [s], increasing.
    fluid : np.ndarray
        Mean fluid temperature rise ``S_cap(t)`` with heat capacity.
    wall : np.ndarray
        Borehole-wall temperature rise ``W_cap(t)`` with heat capacity.
    fluid_no_capacity, wall_no_capacity : np.ndarray
        The same model with ``C_f = C_p = rho c_g = 0`` on the same grid:
        ``fluid_no_capacity = R_b + wall_no_capacity`` exactly, and
        ``wall_no_capacity`` is the finite-volume cylindrical source.
    ground_cylinder, ground_line : np.ndarray
        Analytical hollow-cylinder and infinite-line-source wall responses.
    steady_resistance : float
        Steady resistance ``R_b`` of the model [m.K/W].
    stored_fluid, stored_borehole, stored_ground, far_outflow : np.ndarray
        Where the injected heat is at each time [J/m per W/m = s]: fluid,
        pipe + grout, ground, and lost through ``r_far``. Their sum is
        ``times`` to round-off.
    """

    times: np.ndarray
    fluid: np.ndarray
    wall: np.ndarray
    fluid_no_capacity: np.ndarray
    wall_no_capacity: np.ndarray
    ground_cylinder: np.ndarray
    ground_line: np.ndarray
    steady_resistance: float
    stored_fluid: np.ndarray
    stored_borehole: np.ndarray
    stored_ground: np.ndarray
    far_outflow: np.ndarray

    @property
    def fluid_deviation(self) -> np.ndarray:
        """``S_cap - R_b - G_ILS``: add to ``g + R_b`` for the dynamic fluid response.

        Computed as ``(S_cap - S_nocap) + G_ICS - G_ILS`` so that the ground
        discretisation error cancels between the two finite-volume runs.
        Tends to zero at long times; ``-R_b`` at ``t = 0``.
        """
        return np.asarray(self.fluid - self.fluid_no_capacity + self.ground_cylinder - self.ground_line)

    @property
    def wall_deviation(self) -> np.ndarray:
        """``W_cap - G_ILS``: add to ``g`` for the dynamic wall response (same cancellation)."""
        return np.asarray(self.wall - self.wall_no_capacity + self.ground_cylinder - self.ground_line)


@dataclass(frozen=True)
class _Grid:
    capacity: np.ndarray  # node heat capacity [J/(m K)]
    conductance: np.ndarray  # between neighbours [W/(m K)]
    far_conductance: float
    wall_index: int


def _log_faces(r_in: float, r_out: float, n: int) -> np.ndarray:
    faces = np.asarray(r_in * (r_out / r_in) ** np.linspace(0.0, 1.0, n + 1))
    faces[0], faces[-1] = r_in, r_out
    return faces


def _step_ends(times: np.ndarray, dt_min: float, step_fraction: float) -> np.ndarray:
    """Backward-Euler step ends: a grid growing as ``max(dt_min, f t)`` plus the evaluation times."""
    t_end = float(times[-1])
    ends: list[float] = []
    t = 0.0
    while t < t_end:
        t += max(dt_min, step_fraction * t)
        ends.append(t)
    base = np.asarray(ends[:-1], dtype=float)
    if base.size:
        idx = np.searchsorted(times, base)
        lo = times[np.clip(idx - 1, 0, times.size - 1)]
        hi = times[np.clip(idx, 0, times.size - 1)]
        dist = np.minimum(np.abs(base - lo), np.abs(hi - base))
        base = base[dist >= 0.5 * np.maximum(dt_min, step_fraction * base)]
    return np.union1d(base, times)


def _march(grid: _Grid, ends: np.ndarray, keep: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Backward Euler with a unit load on node 0; node temperatures and far outflow at kept times."""
    cap, k = grid.capacity, grid.conductance
    n = cap.size
    diag = np.zeros(n)
    diag[:-1] += k
    diag[1:] += k
    diag[-1] += grid.far_conductance
    ab = np.zeros((3, n))
    ab[0, 1:] = -k
    ab[2, :-1] = -k
    src = np.zeros(n)
    src[0] = 1.0
    temp = np.zeros(n)
    states = np.empty((int(keep.sum()), n))
    outflow = np.empty(int(keep.sum()))
    flux_far = 0.0
    t_prev = 0.0
    row = 0
    for t, take in zip(ends, keep, strict=True):
        dt = t - t_prev
        ab[1] = cap / dt + diag
        temp = solve_banded((1, 1), ab, cap / dt * temp + src, check_finite=False)
        flux_far += dt * grid.far_conductance * temp[-1]
        t_prev = t
        if take:
            states[row] = temp
            outflow[row] = flux_far
            row += 1
    return states, outflow


def _positive(value: float, name: str) -> float:
    out = float(value)
    if isinstance(value, bool) or not math.isfinite(out) or out <= 0.0:
        raise ValueError(f"{name} must be positive and finite, got {value!r}")
    return out


def _nonnegative(value: float, name: str) -> float:
    out = float(value)
    if isinstance(value, bool) or not math.isfinite(out) or out < 0.0:
        raise ValueError(f"{name} must be non-negative and finite, got {value!r}")
    return out


@dataclass(frozen=True)
class EquivalentCylinderBorehole:
    """Mapped equivalent-cylinder borehole section (see the module docstring).

    Build it from a real section with :meth:`from_pipes`.

    Parameters
    ----------
    r_eq : float
        Equivalent pipe radius [m] (inner radius of the grout annulus).
    r_b : float
        Borehole radius [m].
    k_g : float
        Equivalent grout conductivity [W/(m K)].
    k_s : float
        Ground conductivity [W/(m K)].
    rhoc_g, rhoc_s : float
        Volumetric heat capacity of the grout annulus and of the ground [J/(m3 K)].
    R_in : float
        Fluid node to ``r_eq`` resistance [m.K/W] (``R_fp / n_pipes``).
    C_f : float
        Fluid heat capacity per metre of borehole [J/(m K)].
    C_p : float, optional
        Heat capacity lumped at ``r_eq`` (pipe walls) [J/(m K)].
    """

    r_eq: float
    r_b: float
    k_g: float
    k_s: float
    rhoc_g: float
    rhoc_s: float
    R_in: float
    C_f: float
    C_p: float = 0.0

    def __post_init__(self) -> None:
        r_eq = _positive(self.r_eq, "r_eq")
        if _positive(self.r_b, "r_b") <= r_eq:
            raise ValueError(f"r_b ({self.r_b}) must exceed r_eq ({r_eq})")
        _positive(self.k_g, "k_g")
        _positive(self.k_s, "k_s")
        _nonnegative(self.rhoc_g, "rhoc_g")
        _positive(self.rhoc_s, "rhoc_s")
        _positive(self.R_in, "R_in")
        _nonnegative(self.C_f, "C_f")
        _nonnegative(self.C_p, "C_p")

    @classmethod
    def from_pipes(
        cls,
        *,
        n_pipes: int,
        r_out: float,
        r_in: float,
        r_b: float,
        R_b: float,
        R_fp: float,
        k_s: float,
        rhoc_s: float,
        rhoc_g: float = RHOC_GROUT_DEFAULT,
        rhoc_f: float = rho_w * c_w,
        rhoc_p: float = RHOC_PIPE_DEFAULT,
    ) -> EquivalentCylinderBorehole:
        """Map a U-tube section onto the cylinder so that its steady resistance is ``R_b``.

        Parameters
        ----------
        n_pipes : int
            Number of pipes in the borehole (2 for a single U-tube, 4 for a double).
        r_out, r_in : float
            Pipe outer and inner radius [m].
        r_b : float
            Borehole radius [m].
        R_b : float
            Steady borehole resistance to reproduce [m.K/W] (the value the
            host uses in ``T_f = T_wall - q' R_b``).
        R_fp : float
            Fluid-to-outer-pipe-wall resistance of one pipe [m.K/W]
            (:func:`fluid_to_pipe_resistance`).
        k_s, rhoc_s : float
            Ground conductivity [W/(m K)] and volumetric heat capacity [J/(m3 K)].
        rhoc_g, rhoc_f, rhoc_p : float, optional
            Volumetric heat capacity of grout, fluid and pipe wall [J/(m3 K)].

        Raises
        ------
        ValueError
            If the pipes cannot fit in the borehole or ``R_b <= R_fp / n_pipes``
            (the grout would need a negative resistance).
        """
        if isinstance(n_pipes, bool) or not isinstance(n_pipes, int | np.integer) or n_pipes < 1:
            raise ValueError(f"n_pipes must be a positive integer, got {n_pipes!r}")
        n = int(n_pipes)
        r_po, r_pi, rb = _positive(r_out, "r_out"), _positive(r_in, "r_in"), _positive(r_b, "r_b")
        if r_pi >= r_po:
            raise ValueError("r_in must be smaller than r_out")
        r_eq = math.sqrt(n) * r_po
        if r_eq >= rb:
            raise ValueError(f"{n} pipes of outer radius {r_po} m do not fit in a borehole of radius {rb} m")
        r_inner = _positive(R_fp, "R_fp") / n
        if _positive(R_b, "R_b") <= r_inner:
            raise ValueError(f"R_b ({R_b}) must exceed R_fp / n_pipes ({r_inner}): the grout resistance is negative")
        return cls(
            r_eq=r_eq,
            r_b=rb,
            k_g=math.log(rb / r_eq) / (2.0 * math.pi * (R_b - r_inner)),
            k_s=_positive(k_s, "k_s"),
            rhoc_g=_nonnegative(rhoc_g, "rhoc_g"),
            rhoc_s=_positive(rhoc_s, "rhoc_s"),
            R_in=r_inner,
            C_f=_nonnegative(rhoc_f, "rhoc_f") * n * math.pi * r_pi**2,
            C_p=_nonnegative(rhoc_p, "rhoc_p") * n * math.pi * (r_po**2 - r_pi**2),
        )

    # -- properties ---------------------------------------------------------
    @property
    def steady_resistance(self) -> float:
        """``R_b = R_in + ln(r_b / r_eq) / (2 pi k_g)`` [m.K/W]."""
        return self.R_in + math.log(self.r_b / self.r_eq) / (2.0 * math.pi * self.k_g)

    @property
    def borehole_heat_capacity(self) -> float:
        """Fluid + pipe + grout heat capacity per metre [J/(m K)]."""
        return self.C_f + self.C_p + self.rhoc_g * math.pi * (self.r_b**2 - self.r_eq**2)

    @property
    def capacity_ratio(self) -> float:
        """Borehole heat capacity over that of the soil it replaces, ``pi r_b^2 rho c_s`` [-]."""
        return self.borehole_heat_capacity / (math.pi * self.r_b**2 * self.rhoc_s)

    @property
    def ground_diffusivity(self) -> float:
        """``alpha_s = k_s / (rho c_s)`` [m2/s]."""
        return self.k_s / self.rhoc_s

    def without_capacity(self) -> EquivalentCylinderBorehole:
        """The same section with ``C_f = C_p = rho c_g = 0``."""
        return replace(self, rhoc_g=0.0, C_f=0.0, C_p=0.0)

    # -- discretisation -----------------------------------------------------
    def _grid(self, r_far: float, cells_per_decade: int, min_grout_cells: int, *, capacity: bool) -> _Grid:
        n_g = max(min_grout_cells, math.ceil(cells_per_decade * math.log10(self.r_b / self.r_eq)))
        n_s = max(4, math.ceil(cells_per_decade * math.log10(r_far / self.r_b)))
        fg = _log_faces(self.r_eq, self.r_b, n_g)
        fs = _log_faces(self.r_b, r_far, n_s)
        cg = np.sqrt(fg[:-1] * fg[1:])
        cs = np.sqrt(fs[:-1] * fs[1:])
        two_pi = 2.0 * math.pi
        scale = 1.0 if capacity else 0.0
        cap = np.concatenate(
            [
                [scale * self.C_f, scale * self.C_p],
                scale * self.rhoc_g * math.pi * np.diff(fg**2),
                [0.0],
                self.rhoc_s * math.pi * np.diff(fs**2),
            ]
        )
        cond = np.concatenate(
            [
                [1.0 / self.R_in, two_pi * self.k_g / math.log(cg[0] / self.r_eq)],
                two_pi * self.k_g / np.log(cg[1:] / cg[:-1]),
                [two_pi * self.k_g / math.log(self.r_b / cg[-1]), two_pi * self.k_s / math.log(cs[0] / self.r_b)],
                two_pi * self.k_s / np.log(cs[1:] / cs[:-1]),
            ]
        )
        far = two_pi * self.k_s / math.log(r_far / cs[-1])
        return _Grid(capacity=cap, conductance=cond, far_conductance=far, wall_index=2 + n_g)

    def load_step_response(
        self,
        times: Any,
        *,
        cells_per_decade: int = _CELLS_PER_DECADE,
        min_grout_cells: int = _MIN_GROUT_CELLS,
        dt_min: float = _DT_MIN,
        step_fraction: float = _STEP_FRACTION,
        far_field_factor: float = _FAR_FIELD_FACTOR,
    ) -> CapacityStepResponse:
        """Response to a unit load per metre applied to the fluid from ``t = 0``.

        Parameters
        ----------
        times : array_like
            Evaluation times [s], positive and increasing; each is an exact
            step end (no interpolation in time).
        cells_per_decade : int, optional
            Radial cells per decade of radius (grout and ground).
        min_grout_cells : int, optional
            Minimum number of grout cells.
        dt_min, step_fraction : float, optional
            Time step ``max(dt_min, step_fraction * t)`` [s].
        far_field_factor : float, optional
            Outer radius ``r_b + factor * sqrt(alpha_s t_max)``; 8 makes the
            line-source temperature there ``E1(16)`` (6e-9) of the wall scale.
        """
        t = np.asarray(times, dtype=float)
        if t.ndim != 1 or t.size == 0 or not np.all(np.isfinite(t)) or t[0] <= 0 or np.any(np.diff(t) <= 0):
            raise ValueError("times must be a non-empty, positive, strictly increasing 1-D array")
        r_far = self.r_b + _positive(far_field_factor, "far_field_factor") * math.sqrt(
            self.ground_diffusivity * float(t[-1])
        )
        ends = _step_ends(t, _positive(dt_min, "dt_min"), _positive(step_fraction, "step_fraction"))
        keep = np.isin(ends, t)
        g_cap = self._grid(r_far, cells_per_decade, min_grout_cells, capacity=True)
        g_no = self._grid(r_far, cells_per_decade, min_grout_cells, capacity=False)
        x_cap, out_cap = _march(g_cap, ends, keep)
        x_no, _ = _march(g_no, ends, keep)
        w = g_cap.wall_index
        stored = x_cap * g_cap.capacity[None, :]
        alpha = self.ground_diffusivity
        return CapacityStepResponse(
            times=t,
            fluid=x_cap[:, 0],
            wall=x_cap[:, w],
            fluid_no_capacity=x_no[:, 0],
            wall_no_capacity=x_no[:, w],
            ground_cylinder=cylinder_source_wall(t, self.k_s, alpha, self.r_b),
            ground_line=line_source_wall(t, self.k_s, alpha, self.r_b),
            steady_resistance=self.steady_resistance,
            stored_fluid=stored[:, 0],
            stored_borehole=stored[:, 1:w].sum(axis=1),
            stored_ground=stored[:, w + 1 :].sum(axis=1),
            far_outflow=out_cap,
        )


# ---------------------------------------------------------------------------
# Dynamic g-function (drop-in for GFunction)
# ---------------------------------------------------------------------------

#: Kernel nodes per decade of time below the first g-function node.
_KERNEL_POINTS_PER_DECADE = 30
#: First kernel node [s]; below it the response is the line from t = 0.
_KERNEL_T_FIRST = 1.0
#: The correction is tabulated up to this multiple of ``r_b^2 / alpha_s`` and tapered to zero after it.
_KERNEL_FO_END = 600.0


@lru_cache(maxsize=64)
def _capacity_kernel(section: EquivalentCylinderBorehole) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(times, fluid deviation, wall deviation, line source) of ``section`` on the kernel grid."""
    t_end = _KERNEL_FO_END * section.r_b**2 / section.ground_diffusivity
    decades = math.log10(t_end / _KERNEL_T_FIRST)
    times = np.geomspace(_KERNEL_T_FIRST, t_end, int(math.ceil(_KERNEL_POINTS_PER_DECADE * decades)) + 1)
    resp = section.load_step_response(times)
    out = (times, resp.fluid_deviation, resp.wall_deviation, resp.ground_line)
    for arr in out:
        arr.setflags(write=False)
    return out


# ---------------------------------------------------------------------------
# Fluid transit: plug flow through the U-tube (Laplace domain)
# ---------------------------------------------------------------------------


def section_admittance(section: EquivalentCylinderBorehole, s: Any) -> np.ndarray:
    """Admittance ``Y(s)`` [W/(m K)] the fluid sees in the Laplace domain.

    Heat flow from the fluid into the section per unit fluid temperature for
    the *continuous* equivalent cylinder (pipe node, grout annulus, infinite
    ground), the fluid's own heat capacity excluded. The response of the
    lumped fluid node to a unit load step is ``1 / (s (s C_f + Y(s)))``.
    Exponentially scaled Bessel functions keep it finite for large ``|s|``.
    """
    from scipy.special import ive, kve

    s = np.asarray(s, dtype=complex)
    z_s = np.sqrt(s * section.rhoc_s / section.k_s) * section.r_b
    y_ground = 2.0 * math.pi * section.k_s * z_s * kve(1, z_s) / kve(0, z_s)
    c = 2.0 * math.pi * section.k_g
    if section.rhoc_g > 0.0:
        m = np.sqrt(s * section.rhoc_g / section.k_g)
        z1, z2 = m * section.r_eq, m * section.r_b
        rho = (c * z2 * kve(1, z2) - y_ground * kve(0, z2)) / (c * z2 * ive(1, z2) + y_ground * ive(0, z2))
        eps = np.exp(-(z2 - z1) - (z2 - z1).real)
        y_grout = -c * z1 * (rho * eps * ive(1, z1) - kve(1, z1)) / (rho * eps * ive(0, z1) + kve(0, z1))
    else:
        y_grout = 1.0 / (math.log(section.r_b / section.r_eq) / c + 1.0 / y_ground)
    return np.asarray(1.0 / (section.R_in + 1.0 / (s * section.C_p + y_grout)))


def _talbot(transform: Any, times: np.ndarray, order: int = 32) -> np.ndarray:
    """Fixed-Talbot numerical inverse Laplace transform (Abate and Valko 2004), one contour per time."""
    t = np.asarray(times, dtype=float)[:, None]
    r = 2.0 * order / (5.0 * t)
    theta = (np.arange(1, order) * math.pi / order)[None, :]
    cot = 1.0 / np.tan(theta)
    s = r * theta * (cot + 1j)
    sigma = theta + (theta * cot - 1.0) * cot
    body = (np.exp(t * s) * transform(s) * (1.0 + 1j * sigma)).real.sum(axis=1)
    s0 = r[:, 0].astype(complex)
    head = 0.5 * (transform(s0) * np.exp(s0 * t[:, 0])).real
    return np.asarray(r[:, 0] / order * (head + body))


def loop_outlet_response(section: EquivalentCylinderBorehole, times: Any, H: float, m_cp: float) -> np.ndarray:
    """Borehole outlet temperature rise for a unit load step, fluid in plug flow [m.K/W].

    The fluid travels through the U-tube (borehole depth ``H`` [m], heat
    capacity rate ``m_cp`` [W/K]) exchanging heat with the section at every
    depth; the load ``q' H`` is added between outlet and inlet. In the
    Laplace domain one pass multiplies the fluid temperature by
    ``E(s) = exp(-(s C_f + Y(s)) H / m_cp)``, so the outlet is
    ``(H / m_cp) / s * sum_{k>=1} E^k``: nothing reaches the outlet before one
    transit time ``t_tr = C_f H / m_cp``, and each later pass ``k`` arrives
    at ``k t_tr``. The legs do not exchange heat with each other here (that
    part of the steady resistance is restored by calibrating the section,
    see :class:`DynamicGFunction`).
    """
    t = np.asarray(times, dtype=float)
    t_tr = section.C_f * H / m_cp
    out = np.zeros_like(t)
    for k in range(1, int(float(t.max()) // t_tr) + 1 if t.size else 0):
        late = t - k * t_tr > 1e-6
        if not late.any():
            break
        out[late] += _talbot(
            lambda s, k=k: np.exp(-k * section_admittance(section, s) * H / m_cp) / s, t[late] - k * t_tr
        )
    return np.asarray(out * H / m_cp)


#: The plug-flow response is used alone up to this many transit times ...
_TRANSIT_FULL = 8.0
#: ... and blended into the lumped-fluid response by this many.
_TRANSIT_END = 24.0


def _transit_section(section: EquivalentCylinderBorehole, H: float, m_cp: float) -> EquivalentCylinderBorehole | None:
    """Section whose plug-flow loop has the steady effective resistance of ``section``.

    With a uniform wall temperature the loop gives ``(T_in + T_out)/2 - T_wall
    = q' R x coth(x)``, ``x = H / (2 m_cp R)``. The local resistance ``R`` is
    chosen so that this equals the target ``R_b`` (which already contains the
    axial effects); ``None`` if no such section exists (very low flow).
    """
    from scipy.optimize import brentq

    target = section.steady_resistance
    half = H / (2.0 * m_cp)

    def excess(resistance: float) -> float:
        return half / math.tanh(half / resistance) - target

    lower = section.R_in * (1.0 + 1e-6)
    if excess(lower) >= 0.0 or excess(target) <= 0.0:
        return None
    resistance = float(brentq(excess, lower, target, xtol=1e-14, rtol=1e-12))
    return replace(section, k_g=math.log(section.r_b / section.r_eq) / (2.0 * math.pi * (resistance - section.R_in)))


@lru_cache(maxsize=64)
def _transit_kernel(
    section: EquivalentCylinderBorehole, H: float, m_cp: float
) -> tuple[np.ndarray, np.ndarray, float] | None:
    """(times, outlet rise per unit load, transit time) of the plug-flow loop, or None."""
    calibrated = _transit_section(section, H, m_cp)
    if calibrated is None:
        return None
    t_tr = calibrated.C_f * H / m_cp
    t_end = _TRANSIT_END * t_tr
    edges = np.concatenate([k * t_tr * np.array([1.0 - 1e-6, 1.0 + 1e-6]) for k in range(1, 4)])
    decades = max(math.log10(t_end / _KERNEL_T_FIRST), 0.1)
    times = np.unique(
        np.concatenate(
            (
                np.linspace(t_tr / 20.0, 4.0 * t_tr, 80),
                edges,
                np.geomspace(_KERNEL_T_FIRST, t_end, int(math.ceil(_KERNEL_POINTS_PER_DECADE * decades)) + 1),
            )
        )
    )
    rise = loop_outlet_response(calibrated, times, H, m_cp)
    times.setflags(write=False)
    rise.setflags(write=False)
    return times, rise, t_tr


class _PiecewiseLinear:
    """Piecewise-linear table with exact interval means (clamped to the last value beyond the grid)."""

    def __init__(self, x: np.ndarray, y: np.ndarray) -> None:
        self.x = np.asarray(x, dtype=float)
        self.y = np.asarray(y, dtype=float)
        self._slope = np.diff(self.y) / np.diff(self.x)
        self._cum = np.concatenate(([0.0], np.cumsum(0.5 * (self.y[:-1] + self.y[1:]) * np.diff(self.x))))

    def __call__(self, t: np.ndarray) -> np.ndarray:
        return np.asarray(np.interp(t, self.x, self.y))

    def _integral(self, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        k = np.clip(np.searchsorted(self.x, t, side="right") - 1, 0, len(self.x) - 2)
        s = t - self.x[k]
        return k, self.y[k] * s + 0.5 * self._slope[k] * s * s

    def mean(self, tau: np.ndarray, dt: float) -> np.ndarray:
        k_a, loc_a = self._integral(tau)
        k_b, loc_b = self._integral(tau + dt)
        return np.asarray(((self._cum[k_b] - self._cum[k_a]) + (loc_b - loc_a)) / dt)


class DynamicGFunction:
    """g-function with borehole heat capacity and short-time response.

    Wraps a long-time field g-function ``g(t)`` [m.K/W] and the capacity
    response of one borehole section. It is callable and has ``step_mean``
    like :class:`~tmhp.g_function.GFunction`, so it can replace it in
    :class:`~tmhp.ground_coupling.AggregateGFunctionCoupler` and in the
    inline superposition of the other ground-source models.

    **Returned quantity.** ``g_dyn(t)`` is the *fluid-referenced* kernel
    ``K_f(t) = S_dyn(t) - R_b``, where ``S_dyn`` is the mean-fluid
    temperature response to a unit load step. With it the host's steady
    relation ``T_f = T_wall - q' R_b`` gives the dynamic fluid temperature,
    and ``T_wall = T_s - sum(dq * K_f)`` is the wall temperature *the fluid
    would need in steady state*. It starts at ``K_f(0) = -R_b`` (the fluid
    has not moved yet) and joins the long-time g-function within days. The
    physical wall temperature uses :meth:`wall` / :meth:`wall_step_mean`.

    In a field every borehole responds alone at these time scales, so the
    correction of one section is added to the field g-function; the load per
    metre is the field average.

    Parameters
    ----------
    g_field : callable
        Long-time g-function ``g(t)`` [m.K/W]. A
        :class:`~tmhp.g_function.GFunction` is read on its own grid and its
        horizon is extended on demand; any other callable is sampled on a
        log grid up to ``t_max_s``.
    section : EquivalentCylinderBorehole
        Borehole section; its ``steady_resistance`` must be the ``R_b`` the
        host subtracts.
    t_max_s : float, optional
        Horizon [s] for a plain callable ``g_field`` (ignored for a
        :class:`~tmhp.g_function.GFunction`).
    """

    #: Start of the long-time g-function: below it the field response is the line source at ``r_b``.
    T_JOIN = 3600.0

    def __init__(
        self,
        g_field: Any,
        section: EquivalentCylinderBorehole,
        t_max_s: float | None = None,
        *,
        H: float | None = None,
        m_cp: float | None = None,
    ) -> None:
        self.field = g_field
        self.section = section
        self.R_b = section.steady_resistance
        #: Half the loop temperature difference per unit load, ``H / (2 m_cp)`` [m.K/W] (0 without transit).
        self.half_loop = 0.0
        #: Fluid transit time through the U-tube [s]; ``None`` when the fluid is one lumped node.
        self.transit_time: float | None = None
        self._transit: tuple[np.ndarray, np.ndarray, float] | None = None
        if H is not None or m_cp is not None:
            if H is None or m_cp is None:
                raise ValueError("give both H and m_cp to resolve the fluid transit, or neither")
            self._transit = _transit_kernel(section, _positive(H, "H"), _positive(m_cp, "m_cp"))
            if self._transit is not None:
                self.half_loop = float(H) / (2.0 * float(m_cp))
                self.transit_time = self._transit[2]
        self._managed = all(hasattr(g_field, name) for name in ("x", "y", "ensure_horizon", "n_builds"))
        if not self._managed:
            if t_max_s is None or not (math.isfinite(t_max_s) and t_max_s > self.T_JOIN):
                raise ValueError("t_max_s (> 3600 s) is required when g_field is not a GFunction")
            self._t_plain = float(t_max_s)
        self._field_builds = -1
        self._rebuild()

    # -- table ----------------------------------------------------------------
    def _field_nodes(self) -> tuple[np.ndarray, np.ndarray]:
        if self._managed:
            x = np.asarray(self.field.x, dtype=float)
            y = np.asarray(self.field.y, dtype=float)
            keep = x >= self.T_JOIN
            return x[keep], y[keep]
        decades = math.log10(self._t_plain / self.T_JOIN)
        x = np.geomspace(self.T_JOIN, self._t_plain, max(2, int(math.ceil(24 * decades)) + 1))
        return x, np.asarray(self.field(x), dtype=float)

    def _rebuild(self) -> None:
        sec = self.section
        t_k, dev_f, dev_w, ils_k = _capacity_kernel(sec)
        x_g, y_g = self._field_nodes()
        if x_g[0] > self.T_JOIN * (1 + 1e-9):
            raise ValueError(f"the long-time g-function must start at or before {self.T_JOIN:g} s")
        nodes = np.union1d(t_k, x_g)
        if self._transit is not None:
            nodes = np.union1d(nodes, self._transit[0])
        ils = line_source_wall(nodes, sec.k_s, sec.ground_diffusivity, sec.r_b)
        # Field response: the g-function from T_JOIN on; below it the line
        # source at r_b plus the (tiny) offset of the two at T_JOIN.
        offset = float(y_g[0] - np.interp(self.T_JOIN, nodes, ils))
        g_true = np.where(
            nodes >= self.T_JOIN, np.interp(nodes, x_g, y_g), ils + offset * np.minimum(nodes / self.T_JOIN, 1.0)
        )
        # Capacity deviations: tabulated up to t_k[-1], tapered as 1/t after
        # it (the difference of two conduction solutions with equal steady
        # flux decays as 1/t), exactly zero beyond 30 t_k[-1].
        t_last = float(t_k[-1])
        taper = np.clip(t_last / np.maximum(nodes, t_last), 0.0, 1.0)
        taper = np.where(nodes > 30.0 * t_last, 0.0, taper)
        dev_fluid = np.interp(nodes, t_k, dev_f) * taper
        dev_wall = np.interp(nodes, t_k, dev_w) * taper
        start = -self.R_b
        if self._transit is not None:
            # Plug-flow outlet, referred to the host's symmetric split
            # T_out = T_f + q' H / (2 m_cp): alone for the first passes, then
            # blended (in log time) into the lumped-fluid response.
            t_x, rise_x, t_tr = self._transit
            dev_loop = np.interp(nodes, t_x, rise_x) + self.half_loop - self.R_b - ils
            lo, hi = _TRANSIT_FULL * t_tr, _TRANSIT_END * t_tr
            phase = np.clip(np.log(np.maximum(nodes, lo) / lo) / math.log(hi / lo), 0.0, 1.0)
            weight = np.cos(0.5 * math.pi * phase) ** 2
            dev_fluid = weight * dev_loop + (1.0 - weight) * dev_fluid
            start = self.half_loop - self.R_b
        x = np.concatenate(([0.0], nodes))
        self._fluid = _PiecewiseLinear(x, np.concatenate(([start], g_true + dev_fluid)))
        self._wall = _PiecewiseLinear(x, np.concatenate(([0.0], g_true + dev_wall)))
        self.x = self._fluid.x
        self.y = self._fluid.y
        self.y_wall = self._wall.y
        self.t_end = float(x[-1])
        self._field_builds = int(getattr(self.field, "n_builds", 0))

    @property
    def n_builds(self) -> int:
        """Number of times the underlying g-function grid was (re)computed."""
        return int(getattr(self.field, "n_builds", 1))

    def ensure_horizon(self, t: float) -> None:
        """Extend the table to cover time ``t`` [s] (recomputing the field g-function if needed)."""
        t = float(t)
        if not math.isfinite(t):
            raise ValueError("g-function requested at a non-finite time")
        if t <= self.t_end:
            return
        if self._managed:
            self.field.ensure_horizon(t)
        else:
            self._t_plain = max(1.5 * t, 2.0 * self._t_plain)
        self._rebuild()

    def _sync(self, t_max: float) -> None:
        self.ensure_horizon(t_max)
        if self._managed and int(self.field.n_builds) != self._field_builds:
            self._rebuild()

    # -- evaluation -------------------------------------------------------------
    def __call__(self, t: Any) -> np.ndarray:
        """Fluid-referenced kernel ``K_f(t) = S_dyn(t) - R_b`` [m.K/W]."""
        t_arr = np.asarray(t, dtype=float)
        if t_arr.size:
            self._sync(float(np.max(t_arr)))
        return self._fluid(t_arr)

    def wall(self, t: Any) -> np.ndarray:
        """Physical borehole-wall response ``W_dyn(t)`` [m.K/W]."""
        t_arr = np.asarray(t, dtype=float)
        if t_arr.size:
            self._sync(float(np.max(t_arr)))
        return self._wall(t_arr)

    def fluid(self, t: Any) -> np.ndarray:
        """Mean-fluid response ``S_dyn(t) = K_f(t) + R_b`` [m.K/W]."""
        return np.asarray(self(t) + self.R_b)

    def _mean(self, table_name: str, tau: Any, dt: float) -> np.ndarray:
        dt = float(dt)
        if not (math.isfinite(dt) and dt > 0):
            raise ValueError("dt must be positive and finite")
        tau_arr = np.maximum(np.asarray(tau, dtype=float), 0.0)
        if tau_arr.size:
            self._sync(float(np.max(tau_arr)) + dt)
        table: _PiecewiseLinear = getattr(self, table_name)
        return table.mean(tau_arr, dt)

    def step_mean(self, tau: Any, dt: float) -> np.ndarray:
        """Mean of the fluid-referenced kernel over ``[tau, tau + dt]`` [m.K/W]."""
        return self._mean("_fluid", tau, dt)

    def wall_step_mean(self, tau: Any, dt: float) -> np.ndarray:
        """Mean of the physical wall response over ``[tau, tau + dt]`` [m.K/W]."""
        return self._mean("_wall", tau, dt)


def with_borehole_capacity(
    g_field: Any,
    *,
    r_b: float,
    k_s: float,
    rhoc_s: float,
    R_b: float,
    r_out: float,
    r_in: float,
    n_pipes: int = 2,
    R_fp: float | None = None,
    k_p: float = 0.4,
    m_flow_borehole: float | None = None,
    rhoc_g: float = RHOC_GROUT_DEFAULT,
    rhoc_f: float = rho_w * c_w,
    rhoc_p: float = RHOC_PIPE_DEFAULT,
    t_max_s: float | None = None,
    H: float | None = None,
    cp_f: float = c_w,
    transit: bool = True,
) -> DynamicGFunction:
    """Add borehole heat capacity and the short-time response to a g-function.

    All inputs are ones the ground-source models already have; the heat
    capacities default to water, bentonite grout and HDPE. When the borehole
    depth ``H`` and flow ``m_flow_borehole`` are given the fluid transit
    through the U-tube is resolved as well (``transit=False`` keeps the fluid
    as one node).

    Parameters
    ----------
    g_field : callable
        Long-time g-function (e.g. from :func:`~tmhp.g_function.precompute_gfunction`).
    r_b : float
        Borehole radius [m].
    k_s, rhoc_s : float
        Ground conductivity [W/(m K)] and volumetric heat capacity
        ``rho_s c_s`` [J/(m3 K)].
    R_b : float
        Borehole resistance the host uses [m.K/W] (effective ``R_b*``).
    r_out, r_in : float
        Pipe outer and inner radius [m].
    n_pipes : int, optional
        Pipes per borehole: 2 (single U-tube, default) or 4 (double U-tube).
    R_fp : float, optional
        Fluid-to-outer-pipe-wall resistance of one pipe [m.K/W]. If omitted
        it is computed from ``m_flow_borehole`` and ``k_p`` for water
        (single U-tube: each pipe carries the borehole flow; double U-tube in
        parallel: half of it).
    k_p : float, optional
        Pipe conductivity [W/(m K)], used when ``R_fp`` is omitted.
    m_flow_borehole : float, optional
        Mass flow through one borehole [kg/s], used when ``R_fp`` is omitted.
    rhoc_g, rhoc_f, rhoc_p : float, optional
        Volumetric heat capacity of grout, fluid and pipe wall [J/(m3 K)].
    t_max_s : float, optional
        Horizon [s]; required only when ``g_field`` is a plain callable.
    H : float, optional
        Borehole depth [m] (the mean depth of a field); with
        ``m_flow_borehole`` it enables the transit-resolved response.
    cp_f : float, optional
        Fluid specific heat [J/(kg K)] for the heat capacity rate ``m c_p``.
    transit : bool, optional
        Resolve the fluid transit when ``H`` and ``m_flow_borehole`` are
        known (default). ``False`` treats the fluid as one node.

    Returns
    -------
    DynamicGFunction
    """
    if R_fp is None:
        if m_flow_borehole is None:
            raise ValueError("give R_fp, or m_flow_borehole (and k_p) to derive it")
        R_fp = fluid_to_pipe_resistance(m_flow_borehole / max(1, n_pipes // 2), r_in, r_out, k_p)
    section = EquivalentCylinderBorehole.from_pipes(
        n_pipes=n_pipes,
        r_out=r_out,
        r_in=r_in,
        r_b=r_b,
        R_b=R_b,
        R_fp=R_fp,
        k_s=k_s,
        rhoc_s=rhoc_s,
        rhoc_g=rhoc_g,
        rhoc_f=rhoc_f,
        rhoc_p=rhoc_p,
    )
    if transit and H is not None and m_flow_borehole is not None:
        return DynamicGFunction(g_field, section, t_max_s=t_max_s, H=H, m_cp=m_flow_borehole * cp_f)
    return DynamicGFunction(g_field, section, t_max_s=t_max_s)


def enable_borehole_dynamics(
    model: Any,
    *,
    rhoc_g: float = RHOC_GROUT_DEFAULT,
    rhoc_p: float = RHOC_PIPE_DEFAULT,
    n_pipes: int = 2,
    transit: bool = True,
) -> DynamicGFunction:
    """Switch a ground-source model to the dynamic borehole response, in place.

    Works on :class:`~tmhp.GroundSourceHeatPumpBoiler` (and its solar
    variants) and :class:`~tmhp.GroundSourceHeatPump`. Everything is derived
    from what the model already holds: borehole and pipe radii, pipe
    conductivity, ground properties, the borehole resistance it uses, the
    depth and the loop flow. Call it right after constructing the model::

        model = GroundSourceHeatPumpBoiler(H_b=150.0)
        enable_borehole_dynamics(model)

    To go back, construct the model again. A model given its own
    ``ground_coupler`` is left to that coupler and raises here.

    Parameters
    ----------
    model
        A ground-source model built with the default g-function backend.
    rhoc_g, rhoc_p : float, optional
        Volumetric heat capacity of grout and pipe wall [J/(m3 K)].
    n_pipes : int, optional
        Pipes per borehole (2 for the single U-tube the models assume).
    transit : bool, optional
        Resolve the fluid transit through the U-tube (default).

    Returns
    -------
    DynamicGFunction
        The response now used by ``model``.
    """
    from .ground_coupling import AggregateGFunctionCoupler

    settings = getattr(model, "_ground_settings", None)
    g_field = getattr(model, "_gfunc_interp", None)
    if settings is None or g_field is None or "geometry" not in settings:
        raise TypeError(f"{type(model).__name__} does not expose a borehole field (no ground settings / g-function)")
    coupler = getattr(model, "_ground_coupler", None)
    if coupler is not None and not (isinstance(coupler, AggregateGFunctionCoupler) and coupler._g is g_field):
        raise ValueError("the model uses an injected ground_coupler; wrap that coupler's g-function instead")
    if isinstance(g_field, DynamicGFunction):
        g_field = g_field.field
    geometry = settings["geometry"]
    n_boreholes = int(settings["n_boreholes"])
    dynamic = with_borehole_capacity(
        g_field,
        r_b=float(geometry["r_b"]),
        k_s=float(geometry["k_s"]),
        rhoc_s=float(model.rho_s) * float(model.c_s),
        R_b=float(settings["R_b"]),
        r_out=float(geometry["r_out"]),
        r_in=float(geometry["r_in"]),
        n_pipes=n_pipes,
        k_p=float(geometry["k_p"]),
        m_flow_borehole=float(settings["volume_flow_constant"]) * rho_w / n_boreholes,
        rhoc_g=rhoc_g,
        rhoc_p=rhoc_p,
        H=float(settings["H_b"]),
        transit=transit,
    )
    model._gfunc_interp = dynamic
    if coupler is not None:
        model._ground_coupler = AggregateGFunctionCoupler(dynamic, pulse_tol=coupler._pulse_tol, dt_s=coupler._dt_fixed)
    return dynamic
