"""Bore-field layouts, pipe networks and the assembled field ground response.

:class:`BoreField` describes where the boreholes are and how deep they go
(any coordinates, per-borehole depth). :class:`BoreNetwork` adds the pipes,
the total flow and how the boreholes are connected (parallel headers or
series chains). :class:`FieldGroundResponse` puts them together into what a
host model needs: the field g-function, the field resistance ``R_b`` for
``T_f = T_wall − q'·R_b`` (``q' = Q/ΣH``), the flow in each borehole and the
split of the field load between boreholes.

All physics comes from pygfunction (Cimmino's g-functions, multipole
resistances, network fluid coupling); this module only arranges inputs and
checks them.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .borehole import BoreholePipe
from .constants import c_w, k_w, mu_w, rho_w
from .g_function import GFunction

try:
    import pygfunction as gt

    HAS_PYGFUNCTION = True
except ImportError:
    HAS_PYGFUNCTION = False


def _require_pygfunction() -> None:
    if not HAS_PYGFUNCTION:
        raise ImportError("pygfunction is not installed.")


def _readonly(values: Any, n: int | None, name: str) -> np.ndarray:
    arr = np.array(values, dtype=float)  # copy
    if n is not None:
        if arr.ndim == 0:
            arr = np.full(n, float(arr))
        elif arr.shape != (n,):
            raise ValueError(f"{name} must be a scalar or have shape ({n},), got {arr.shape}")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} must be finite")
    arr.setflags(write=False)
    return arr


@dataclass(frozen=True, eq=False)
class BoreField:
    """Vertical boreholes at arbitrary positions with per-borehole depths.

    ``x``, ``y`` are the borehole head positions [m], ``H`` the active
    lengths [m], ``D`` the buried depths of the borehole tops [m], ``r_b``
    the common borehole radius [m]. Arrays are stored as read-only copies.
    Boreholes must not touch: every centre-to-centre distance exceeds
    ``2 r_b``.
    """

    x: np.ndarray
    y: np.ndarray
    H: np.ndarray
    D: np.ndarray
    r_b: float

    def __post_init__(self) -> None:
        x = _readonly(self.x, None, "x")
        if x.ndim != 1 or x.size == 0:
            raise ValueError("x must be a non-empty 1-D array")
        n = x.size
        y = _readonly(self.y, None, "y")
        if y.shape != (n,):
            raise ValueError(f"y must have the same shape as x ({n},), got {y.shape}")
        H = _readonly(self.H, n, "H")
        D = _readonly(self.D, n, "D")
        r_b = float(self.r_b)
        if not (math.isfinite(r_b) and r_b > 0):
            raise ValueError("r_b must be positive and finite")
        if np.any(H <= 0):
            raise ValueError("borehole lengths H must be positive")
        if np.any(D < 0):
            raise ValueError("buried depths D must be nonnegative")
        if n > 1:
            dx = x[:, None] - x[None, :]
            dy = y[:, None] - y[None, :]
            dist = np.hypot(dx, dy)
            np.fill_diagonal(dist, np.inf)
            i, j = np.unravel_index(np.argmin(dist), dist.shape)
            if not dist[i, j] > 2.0 * r_b:
                raise ValueError(
                    f"boreholes {min(i, j)} and {max(i, j)} are {dist[i, j]:.4g} m apart (centre to centre); "
                    f"they must be more than 2*r_b = {2 * r_b:.4g} m apart"
                )
        object.__setattr__(self, "x", x)
        object.__setattr__(self, "y", y)
        object.__setattr__(self, "H", H)
        object.__setattr__(self, "D", D)
        object.__setattr__(self, "r_b", r_b)

    # -- constructors -------------------------------------------------------
    @classmethod
    def from_coordinates(cls, xy: Any, H: Any, D: Any = 0.0, r_b: float = 0.08) -> BoreField:
        """Boreholes at ``xy`` (shape ``(n, 2)``, metres); ``H``/``D`` scalar or per borehole."""
        xy_arr = np.asarray(xy, dtype=float)
        if xy_arr.ndim != 2 or xy_arr.shape[1] != 2 or xy_arr.shape[0] == 0:
            raise ValueError(f"xy must have shape (n, 2) with n >= 1, got {xy_arr.shape}")
        return cls(x=xy_arr[:, 0], y=xy_arr[:, 1], H=H, D=D, r_b=r_b)

    @classmethod
    def rectangle(cls, N_1: int, N_2: int, B: float, H: float, D: float = 0.0, r_b: float = 0.08) -> BoreField:
        """``N_1 × N_2`` rectangle with spacing ``B`` [m], in pygfunction's order (x fastest)."""
        _check_counts(N_1=N_1, N_2=N_2)
        x = np.tile(np.arange(N_1), N_2) * B
        y = np.repeat(np.arange(N_2), N_1) * B
        return cls(x=x, y=y, H=np.full(x.size, float(H)), D=np.full(x.size, float(D)), r_b=r_b)

    @classmethod
    def _from_pygfunction(cls, field: Any) -> BoreField:
        return cls(x=field.x, y=field.y, H=field.H, D=field.D, r_b=float(np.ravel(field.r_b)[0]))

    @classmethod
    def l_shape(
        cls, N_1: int, N_2: int, B_1: float, B_2: float, H: float, D: float = 0.0, r_b: float = 0.08
    ) -> BoreField:
        """L-shaped field (pygfunction ``Borefield.L_shaped_field``)."""
        _require_pygfunction()
        _check_counts(N_1=N_1, N_2=N_2)
        return cls._from_pygfunction(gt.borefield.Borefield.L_shaped_field(N_1, N_2, B_1, B_2, H, D, r_b))

    @classmethod
    def u_shape(
        cls, N_1: int, N_2: int, B_1: float, B_2: float, H: float, D: float = 0.0, r_b: float = 0.08
    ) -> BoreField:
        """U-shaped field (pygfunction ``Borefield.U_shaped_field``)."""
        _require_pygfunction()
        _check_counts(N_1=N_1, N_2=N_2)
        return cls._from_pygfunction(gt.borefield.Borefield.U_shaped_field(N_1, N_2, B_1, B_2, H, D, r_b))

    @classmethod
    def box(cls, N_1: int, N_2: int, B_1: float, B_2: float, H: float, D: float = 0.0, r_b: float = 0.08) -> BoreField:
        """Box-shaped field: the rectangle's perimeter (pygfunction ``Borefield.box_shaped_field``)."""
        _require_pygfunction()
        _check_counts(N_1=N_1, N_2=N_2)
        return cls._from_pygfunction(gt.borefield.Borefield.box_shaped_field(N_1, N_2, B_1, B_2, H, D, r_b))

    @classmethod
    def circle(cls, N: int, R: float, H: float, D: float = 0.0, r_b: float = 0.08) -> BoreField:
        """``N`` boreholes on a circle of radius ``R`` [m] (pygfunction ``Borefield.circle_field``)."""
        _require_pygfunction()
        _check_counts(N=N)
        return cls._from_pygfunction(gt.borefield.Borefield.circle_field(N, R, H, D, r_b))

    # -- properties ---------------------------------------------------------
    @property
    def n_boreholes(self) -> int:
        return int(self.x.size)

    @property
    def total_length(self) -> float:
        """ΣH [m]: the length the host's linear load ``q' = Q/ΣH`` refers to."""
        return float(np.sum(self.H))

    @property
    def mean_depth(self) -> float:
        """ΣH / n [m]."""
        return self.total_length / self.n_boreholes

    @property
    def uniform_depth(self) -> bool:
        """True when every borehole has the same length and buried depth."""
        return bool(np.ptp(self.H) == 0 and np.ptp(self.D) == 0)

    def to_pygfunction(self) -> Any:
        """``pygfunction.borefield.Borefield`` in this field's order."""
        _require_pygfunction()
        return gt.borefield.Borefield(self.H, self.D, self.r_b, self.x, self.y)

    def boreholes(self) -> list[Any]:
        """List of ``pygfunction.boreholes.Borehole`` in this field's order."""
        _require_pygfunction()
        return [
            gt.boreholes.Borehole(float(h), float(d), self.r_b, float(xi), float(yi))
            for h, d, xi, yi in zip(self.H, self.D, self.x, self.y, strict=True)
        ]


def _check_counts(**counts: int) -> None:
    for name, n in counts.items():
        if isinstance(n, bool) or not isinstance(n, (int, np.integer)) or n < 1:
            raise ValueError(f"{name} must be a positive integer, got {n!r}")


def _check_connectivity(connectivity: Sequence[int] | None, n: int) -> tuple[int, ...]:
    """Validate pygfunction's ``bore_connectivity``: −1 = fed by the header, j = downstream of borehole j."""
    if connectivity is None:
        return (-1,) * n
    c = tuple(connectivity)
    if len(c) != n:
        raise ValueError(f"connectivity must have one entry per borehole ({n}), got {len(c)}")
    for i, j in enumerate(c):
        if isinstance(j, bool) or not isinstance(j, (int, np.integer)) or not (j == -1 or 0 <= j < n):
            raise ValueError(f"connectivity[{i}] must be -1 or a borehole index in [0, {n}), got {j!r}")
        if j == i:
            raise ValueError(f"borehole {i} cannot be connected to itself")
    c = tuple(int(j) for j in c)
    feeds = [j for j in c if j != -1]
    if len(feeds) != len(set(feeds)):
        dup = next(j for j in feeds if feeds.count(j) > 1)
        raise ValueError(f"borehole {dup} feeds more than one borehole; branches inside a circuit are not supported")
    for i in range(n):
        j, steps = i, 0
        while c[j] != -1:
            j = c[j]
            steps += 1
            if steps > n:
                raise ValueError(
                    f"connectivity has a loop through borehole {i}; every circuit must start at the header"
                )
    return c


@dataclass(frozen=True)
class BoreNetwork:
    """Pipes, total flow and connections of a bore field (pygfunction ``Network`` inputs).

    ``connectivity`` follows pygfunction's ``bore_connectivity``: entry ``i``
    is ``-1`` when borehole ``i`` is fed from the supply header and ``j`` when
    it is fed by the outlet of borehole ``j`` (series). ``None`` connects every
    borehole in parallel. Each circuit (a chain starting at the header)
    receives ``m_flow_total / n_circuits`` — pygfunction's equal split; the
    circuits are not hydraulically balanced by length. The same pipe
    configuration ``pipe`` is used in every borehole.
    """

    pipe: BoreholePipe
    k_s: float
    m_flow_total: float
    connectivity: tuple[int, ...] | None = None
    rho_f: float = rho_w
    mu_f: float = mu_w
    cp_f: float = c_w
    k_f: float = k_w

    def __post_init__(self) -> None:
        for name in ("k_s", "m_flow_total", "rho_f", "mu_f", "cp_f", "k_f"):
            value = float(getattr(self, name))
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be positive and finite, got {getattr(self, name)!r}")
            object.__setattr__(self, name, value)
        if self.connectivity is not None:
            object.__setattr__(self, "connectivity", tuple(self.connectivity))

    def resolved_connectivity(self, n: int) -> tuple[int, ...]:
        return _check_connectivity(self.connectivity, n)

    def is_parallel(self, n: int) -> bool:
        return all(j == -1 for j in self.resolved_connectivity(n))

    def borehole_flows(self, n: int) -> np.ndarray:
        """Mass flow through each borehole [kg/s] (the flow of its circuit)."""
        c = self.resolved_connectivity(n)
        n_circuits = sum(1 for j in c if j == -1)
        return np.full(n, self.m_flow_total / n_circuits)

    def check_field(self, field: BoreField) -> None:
        self.resolved_connectivity(field.n_boreholes)
        self.pipe.check_fits(field.r_b)

    def to_pygfunction(self, field: BoreField, order: Sequence[int] | np.ndarray | None = None) -> Any:
        """pygfunction ``Network`` for ``field``, boreholes optionally reordered by ``order``.

        ``order[k]`` is the field index of the network's ``k``-th borehole;
        connections are renumbered accordingly.
        """
        _require_pygfunction()
        n = field.n_boreholes
        c = self.resolved_connectivity(n)
        perm = np.arange(n) if order is None else np.asarray(order, dtype=int)
        if sorted(perm.tolist()) != list(range(n)):
            raise ValueError("order must be a permutation of the borehole indices")
        inverse = np.empty(n, dtype=int)
        inverse[perm] = np.arange(n)
        flows = self.borehole_flows(n)
        boreholes = field.boreholes()
        fluid = (self.rho_f, self.mu_f, self.cp_f, self.k_f)
        pipes = []
        for k in perm:
            pipes.append(self.pipe.pygfunction_pipe(boreholes[k], self.k_s, flows[k], *fluid))
        conn = [-1 if c[k] == -1 else int(inverse[c[k]]) for k in perm]
        return gt.networks.Network(
            [boreholes[k] for k in perm],
            pipes,
            bore_connectivity=conn,
            m_flow_network=self.m_flow_total,
            cp_f=self.cp_f,
        )

    def field_resistance(self, field: BoreField) -> float:
        """Field resistance ``R_field`` [m·K/W] (pygfunction ``network_thermal_resistance``).

        ``(T_in + T_out)/2 = T_b − q'·R_field`` for a uniform wall temperature
        ``T_b`` and field load per metre ``q' = Q/ΣH`` (extraction positive).
        For identical parallel boreholes it equals one borehole's ``R_b*`` at
        its share of the flow.
        """
        network = self.to_pygfunction(field)
        return float(gt.networks.network_thermal_resistance(network, self.m_flow_total, self.cp_f))


#: Boundary conditions accepted by :class:`FieldGroundResponse`.
FIELD_BOUNDARY_CONDITIONS = ("auto", "UBWT", "UHTR", "MIFT")


class FieldGroundResponse:
    """Ground response of a bore field, assembled for a host model.

    Parameters
    ----------
    field : BoreField
    pipe : BoreholePipe
        Pipe configuration, the same in every borehole.
    k_s, alpha_s : float
        Ground conductivity [W/(m·K)] and diffusivity [m²/s].
    m_flow_total : float
        Total circulating mass flow [kg/s]; the resistance and, for MIFT, the
        g-function are evaluated at this flow.
    t_max_s : float
        Expected simulation length [s].
    rho_f, mu_f, cp_f, k_f : float
        Fluid properties (default: water from :mod:`tmhp.constants`).
    connectivity : sequence of int, optional
        pygfunction ``bore_connectivity`` (−1 = from the header, j = downstream
        of borehole j). Default: all parallel.
    boundary_condition : {'auto', 'UBWT', 'UHTR', 'MIFT'}
        ``'auto'``: ``'UBWT'`` when every borehole is in parallel and all have
        the same length and buried depth (identical to the rectangular
        constructor for a rectangle), otherwise ``'MIFT'``.
    dt_s, num : as :class:`~tmhp.g_function.GFunction`.
    method : str, optional
        pygfunction solver, passed to :meth:`GFunction.from_field`. The
        default ``'equivalent'`` groups boreholes of similar steady
        temperature into clusters, so every member of a cluster gets the
        cluster's load per metre in :meth:`borehole_load_shares` (an
        irregular 12-borehole field: up to 0.01 off ``'similarities'`` in a
        share of about 0.08, g within 0.15 %). Pass ``'similarities'`` for
        per-borehole shares; it costs more for large fields.

    Attributes
    ----------
    g : GFunction
        Field g-function [m·K/W] for ``T_wall = T_s − Σ Δq'·ḡ``.
    R_b : float
        Field resistance [m·K/W] for ``T_f = T_wall − q'·R_b`` with
        ``q' = Q/ΣH``. Always the network's field resistance (uniform wall
        temperature over the field); for MIFT it is the one the g-function's
        effective wall temperature is defined with.
    m_flow_boreholes : ndarray
        Mass flow in each borehole [kg/s].
    boundary_condition : str
        The boundary condition used (``'auto'`` resolved).
    """

    def __init__(
        self,
        field: BoreField,
        pipe: BoreholePipe | None = None,
        *,
        k_s: float,
        alpha_s: float,
        m_flow_total: float,
        t_max_s: float,
        rho_f: float = rho_w,
        mu_f: float = mu_w,
        cp_f: float = c_w,
        k_f: float = k_w,
        connectivity: Sequence[int] | None = None,
        boundary_condition: str = "auto",
        dt_s: float = 3600.0,
        num: int = 100,
        method: str | None = None,
    ) -> None:
        _require_pygfunction()
        if not isinstance(field, BoreField):
            raise TypeError("field must be a BoreField")
        pipe = BoreholePipe() if pipe is None else pipe
        bc = str(boundary_condition)
        bc = "auto" if bc.lower() == "auto" else bc.upper()
        if bc not in FIELD_BOUNDARY_CONDITIONS:
            raise ValueError(
                f"boundary_condition must be one of {FIELD_BOUNDARY_CONDITIONS}, got {boundary_condition!r}"
            )
        if not (math.isfinite(alpha_s) and alpha_s > 0):
            raise ValueError("alpha_s must be positive and finite")
        network = BoreNetwork(
            pipe=pipe,
            k_s=k_s,
            m_flow_total=m_flow_total,
            connectivity=None if connectivity is None else tuple(connectivity),
            rho_f=rho_f,
            mu_f=mu_f,
            cp_f=cp_f,
            k_f=k_f,
        )
        network.check_field(field)
        if bc == "auto":
            bc = "UBWT" if network.is_parallel(field.n_boreholes) and field.uniform_depth else "MIFT"
        self.field = field
        self.pipe = pipe
        self.network = network
        self.k_s = float(k_s)
        self.alpha_s = float(alpha_s)
        self.m_flow_total = network.m_flow_total
        self.connectivity = network.resolved_connectivity(field.n_boreholes)
        self.boundary_condition = bc
        self.m_flow_boreholes = network.borehole_flows(field.n_boreholes)
        self.m_flow_boreholes.setflags(write=False)
        self.R_b = network.field_resistance(field)
        self.g = GFunction.from_field(
            field,
            alpha_s,
            k_s,
            t_max_s,
            boundary_condition=bc,
            network=network if bc == "MIFT" else None,
            dt_s=dt_s,
            num=num,
            method=method,
        )

    def borehole_load_shares(self, times: Any) -> np.ndarray:
        """Per-borehole fraction of a unit field-load step, shape ``(n, n_times)``; columns sum to 1."""
        return self.g.borehole_load_shares(times)

    # -- host models ----------------------------------------------------------
    def dynamic_gfunction(
        self,
        *,
        rhoc_g: float | None = None,
        rhoc_p: float | None = None,
        transit: bool = True,
    ) -> Any:
        """The field g-function with borehole heat capacity, short-time response and fluid transit.

        See :mod:`tmhp.borehole_capacity`. The section is built from this
        field's pipe and resistance; the transit uses one circuit (its
        boreholes in series, its share of the flow). A coaxial pipe is mapped
        with the outer pipe as the equivalent cylinder.
        """
        from .borehole_capacity import RHOC_GROUT_DEFAULT, RHOC_PIPE_DEFAULT, with_borehole_capacity

        n_circuits = sum(1 for j in self.connectivity if j == -1)
        m_circuit = self.m_flow_total / n_circuits
        net = self.network
        R_fp = self.pipe.fluid_to_pipe_resistance(m_circuit, net.rho_f, net.mu_f, net.cp_f, net.k_f)
        if self.pipe.kind == "coaxial":
            n_pipes = 1
            r_out, r_in = float(self.pipe.r_out_outer), float(self.pipe.r_in_outer)  # type: ignore[arg-type]
        else:
            n_pipes = 2 * self.pipe.n_pipes
            r_out, r_in = self.pipe.r_out, self.pipe.r_in
        return with_borehole_capacity(
            self.g,
            r_b=self.field.r_b,
            k_s=self.k_s,
            rhoc_s=self.k_s / self.alpha_s,
            R_b=self.R_b,
            r_out=r_out,
            r_in=r_in,
            n_pipes=n_pipes,
            R_fp=R_fp,
            m_flow_borehole=m_circuit,
            rhoc_g=RHOC_GROUT_DEFAULT if rhoc_g is None else rhoc_g,
            rhoc_f=net.rho_f * net.cp_f,
            rhoc_p=RHOC_PIPE_DEFAULT if rhoc_p is None else rhoc_p,
            H=self.field.total_length / n_circuits,
            cp_f=net.cp_f,
            transit=transit,
        )

    def model_kwargs(self, c_s: float = 800.0) -> dict[str, Any]:
        """Constructor arguments that make a ground-source model describe this field.

        For :class:`~tmhp.GroundSourceHeatPumpBoiler` and
        :class:`~tmhp.GroundSourceHeatPump`: the borehole count, the mean
        depth (so that ``N * H_b`` is the total length), the field resistance
        as a fixed ``R_b``, the ground properties and the loop flow. The
        model's own rectangular g-function is then replaced with
        :meth:`attach`::

            response = FieldGroundResponse(field, pipe, k_s=2.0, alpha_s=1.25e-6,
                                           m_flow_total=0.8, t_max_s=10 * 8760 * 3600)
            model = GroundSourceHeatPumpBoiler(**response.model_kwargs(), hp_capacity=12000.0)
            response.attach(model)

        ``c_s`` [J/(kg K)] splits the ground heat capacity ``k_s / alpha_s``
        into the ``c_s`` and ``rho_s`` the models take.
        """
        if not (math.isfinite(c_s) and c_s > 0):
            raise ValueError("c_s must be positive and finite")
        field = self.field
        kwargs: dict[str, Any] = dict(
            N_1=field.n_boreholes,
            N_2=1,
            H_b=field.mean_depth,
            D_b=float(np.mean(field.D)),
            r_b=field.r_b,
            R_b=self.R_b,
            k_s=self.k_s,
            c_s=float(c_s),
            rho_s=self.k_s / (self.alpha_s * float(c_s)),
            ground_flow_ref_lpm=self.m_flow_total / self.network.rho_f * 60000.0,
        )
        if self.pipe.kind != "coaxial":
            kwargs.update(
                k_g=self.pipe.k_g, k_p=self.pipe.k_p, r_out=self.pipe.r_out, r_in=self.pipe.r_in, D_s=self.pipe.D_s
            )
        return kwargs

    def attach(
        self,
        model: Any,
        *,
        borehole_dynamics: bool = False,
        rhoc_g: float | None = None,
        rhoc_p: float | None = None,
        transit: bool = True,
    ) -> Any:
        """Make ``model`` use this field's ground response, in place.

        ``model`` must have been constructed with :meth:`model_kwargs` (same
        borehole count, total length, resistance, ground properties and
        flow); a mismatch raises instead of mixing two fields. With
        ``borehole_dynamics=True`` the response is :meth:`dynamic_gfunction`.
        Returns the g-function now used by the model.

        The field resistance and a MIFT g-function hold for the flow given
        here, so the model must run that constant flow (no ``variable_Rb``,
        no variable ground flow).
        """
        from .ground_coupling import AggregateGFunctionCoupler

        settings = getattr(model, "_ground_settings", None)
        if settings is None or not hasattr(model, "_gfunc_interp"):
            raise TypeError(f"{type(model).__name__} does not expose a borehole field")
        field = self.field
        expected = {
            "borehole count": (int(settings["n_boreholes"]), field.n_boreholes),
            "total borehole length [m]": (settings["n_boreholes"] * settings["H_b"], field.total_length),
            "R_b [m.K/W]": (settings["R_b"], self.R_b),
            "k_s [W/(m.K)]": (model.k_s, self.k_s),
            "alpha_s [m2/s]": (model.k_s / (model.rho_s * model.c_s), self.alpha_s),
            "loop flow [m3/s]": (settings["volume_flow_constant"], self.m_flow_total / self.network.rho_f),
        }
        for name, (got, want) in expected.items():
            if not math.isclose(float(got), float(want), rel_tol=1e-9, abs_tol=0.0):
                raise ValueError(
                    f"model and field disagree on {name}: model {got!r}, field {want!r}; "
                    "construct the model with FieldGroundResponse.model_kwargs()"
                )
        if settings["variable_Rb"] or settings["control"] != "constant":
            raise ValueError("a field response holds for one flow: use constant ground flow without variable_Rb")
        g = self.dynamic_gfunction(rhoc_g=rhoc_g, rhoc_p=rhoc_p, transit=transit) if borehole_dynamics else self.g
        coupler = getattr(model, "_ground_coupler", None)
        if coupler is not None:
            if not isinstance(coupler, AggregateGFunctionCoupler):
                raise ValueError("the model uses an injected ground_coupler; build it without one")
            model._ground_coupler = AggregateGFunctionCoupler(g, pulse_tol=coupler._pulse_tol, dt_s=coupler._dt_fixed)
        model._gfunc_interp = g
        return g
