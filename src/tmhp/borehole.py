"""Borehole wall-to-fluid resistance; ground response lives in g_function."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.interpolate import PchipInterpolator

from .constants import c_w, k_w, mu_w, rho_w

try:
    import pygfunction as gt

    HAS_PYGFUNCTION = True
except ImportError:
    HAS_PYGFUNCTION = False

#: Pipe configurations understood by :class:`BoreholePipe`.
PIPE_KINDS = ("single_u", "double_u_parallel", "double_u_series", "coaxial")
#: Which channel of a coaxial pipe carries the fluid down the borehole.
COAXIAL_FLOW_DOWN = ("annulus", "inner")


def _require_pygfunction() -> None:
    if not HAS_PYGFUNCTION:
        raise ImportError("pygfunction is not installed.")


def _positive_finite(**values: float) -> None:
    for name, value in values.items():
        if not (isinstance(value, (int, float, np.floating, np.integer)) and math.isfinite(value) and value > 0):
            raise ValueError(f"{name} must be positive and finite, got {value!r}")


@dataclass(frozen=True)
class BoreholePipe:
    """Pipe configuration inside one borehole: single/double U-tube or coaxial.

    All lengths in metres, conductivities in W/(m·K). The defaults are those of
    :class:`~tmhp.ground_source_heat_pump_boiler.GroundSourceHeatPumpBoiler`, so
    ``BoreholePipe()`` reproduces the resistance the boiler computes today.

    U-tubes (``"single_u"``, ``"double_u_parallel"``, ``"double_u_series"``):
    every leg is a pipe of radii ``r_in``/``r_out`` whose centre is ``D_s``
    from the borehole axis. A single U-tube has its two legs at
    ``(-D_s, 0)`` and ``(D_s, 0)``. A double U-tube puts its four legs 90°
    apart on the same circle, each U-tube's legs facing each other
    (pygfunction order: down legs ``(-D_s, 0)``, ``(0, -D_s)``; up legs
    ``(D_s, 0)``, ``(0, D_s)``). In ``"double_u_parallel"`` each U-tube carries
    half the borehole flow; in ``"double_u_series"`` the fluid runs through
    one U-tube and then the other, so each leg carries the whole flow.

    Coaxial (``"coaxial"``): an inner pipe ``(r_in_inner, r_out_inner)``
    centred inside an outer pipe ``(r_in_outer, r_out_outer)``; the annulus
    between them is the second channel. ``flow_down`` says which channel takes
    the fluid down (``"annulus"``: down the annulus, up the inner pipe).
    ``k_p`` is the outer pipe's conductivity and ``k_p_inner`` the inner
    pipe's (``None``: same as ``k_p``). Build one with :meth:`coaxial`.

    ``k_g`` is the grout conductivity, ``epsilon`` the pipe roughness [m] and
    ``J`` the multipole order (pygfunction default 2).
    """

    kind: str = "single_u"
    r_in: float = 0.013
    r_out: float = 0.016
    D_s: float = 0.025
    k_p: float = 0.4
    k_g: float = 1.5
    r_in_inner: float | None = None
    r_out_inner: float | None = None
    r_in_outer: float | None = None
    r_out_outer: float | None = None
    k_p_inner: float | None = None
    flow_down: str = "annulus"
    epsilon: float = 1e-6
    J: int = 2

    def __post_init__(self) -> None:
        if self.kind not in PIPE_KINDS:
            raise ValueError(f"kind must be one of {PIPE_KINDS}, got {self.kind!r}")
        _positive_finite(k_p=self.k_p, k_g=self.k_g)
        if not (math.isfinite(self.epsilon) and self.epsilon >= 0):
            raise ValueError("epsilon must be finite and nonnegative")
        if isinstance(self.J, bool) or not isinstance(self.J, int) or self.J < 0:
            raise ValueError("J must be a nonnegative integer")
        if self.kind == "coaxial":
            radii = (self.r_in_inner, self.r_out_inner, self.r_in_outer, self.r_out_outer)
            if any(r is None for r in radii):
                raise ValueError("a coaxial pipe needs r_in_inner, r_out_inner, r_in_outer and r_out_outer")
            _positive_finite(
                r_in_inner=self.r_in_inner,  # type: ignore[arg-type]
                r_out_inner=self.r_out_inner,  # type: ignore[arg-type]
                r_in_outer=self.r_in_outer,  # type: ignore[arg-type]
                r_out_outer=self.r_out_outer,  # type: ignore[arg-type]
            )
            if not (self.r_in_inner < self.r_out_inner < self.r_in_outer < self.r_out_outer):  # type: ignore[operator]
                raise ValueError(
                    "coaxial radii must satisfy r_in_inner < r_out_inner < r_in_outer < r_out_outer "
                    "(the inner pipe must fit inside the outer pipe's bore)"
                )
            if self.k_p_inner is not None:
                _positive_finite(k_p_inner=self.k_p_inner)
            if self.flow_down not in COAXIAL_FLOW_DOWN:
                raise ValueError(f"flow_down must be one of {COAXIAL_FLOW_DOWN}, got {self.flow_down!r}")
            return
        _positive_finite(r_in=self.r_in, r_out=self.r_out, D_s=self.D_s)
        if not self.r_in < self.r_out:
            raise ValueError("r_out must be greater than r_in")
        # Closest pair of legs: opposite legs (single U) are 2 D_s apart,
        # neighbouring legs of a double U-tube sqrt(2) D_s apart.
        gap = 2.0 * self.D_s if self.kind == "single_u" else math.sqrt(2.0) * self.D_s
        if not gap > 2.0 * self.r_out:
            raise ValueError(
                f"pipes overlap: legs {gap:.4g} m apart (centre to centre) but 2*r_out = {2 * self.r_out:.4g} m"
            )

    @classmethod
    def coaxial(
        cls,
        r_in_inner: float = 0.0221,
        r_out_inner: float = 0.025,
        r_in_outer: float = 0.0487,
        r_out_outer: float = 0.055,
        k_p: float = 0.4,
        k_g: float = 1.5,
        *,
        k_p_inner: float | None = None,
        flow_down: str = "annulus",
        epsilon: float = 1e-6,
        J: int = 2,
    ) -> "BoreholePipe":
        """Coaxial pipe; default radii are pygfunction's coaxial example (outer pipe 110 mm OD)."""
        return cls(
            kind="coaxial",
            k_p=k_p,
            k_g=k_g,
            r_in_inner=r_in_inner,
            r_out_inner=r_out_inner,
            r_in_outer=r_in_outer,
            r_out_outer=r_out_outer,
            k_p_inner=k_p_inner,
            flow_down=flow_down,
            epsilon=epsilon,
            J=J,
        )

    # -- geometry ----------------------------------------------------------
    @property
    def n_pipes(self) -> int:
        """Number of U-tubes (pygfunction ``nPipes``): 1 single U, 2 double U, 1 coaxial."""
        return 2 if self.kind.startswith("double_u") else 1

    @property
    def outer_radius(self) -> float:
        """Distance from the borehole axis to the outermost pipe surface [m]."""
        if self.kind == "coaxial":
            return float(self.r_out_outer)  # type: ignore[arg-type]
        return self.D_s + self.r_out

    def positions(self) -> list[tuple[float, float]]:
        """Pipe centre positions in pygfunction order (down legs first)."""
        if self.kind == "coaxial":
            return [(0.0, 0.0)]
        d = self.D_s
        if self.kind == "single_u":
            return [(-d, 0.0), (d, 0.0)]
        return [(-d, 0.0), (0.0, -d), (d, 0.0), (0.0, d)]

    def check_fits(self, r_b: float) -> None:
        """Raise ``ValueError`` if the pipes do not fit inside a borehole of radius ``r_b`` [m]."""
        _positive_finite(r_b=r_b)
        if self.outer_radius > r_b:
            raise ValueError(
                f"pipes cross the borehole wall: outermost pipe surface at {self.outer_radius:.4g} m "
                f"from the axis, borehole radius {r_b:.4g} m"
            )

    # -- resistances ---------------------------------------------------------
    def _coaxial_radii(self) -> tuple[np.ndarray, np.ndarray]:
        """(r_in, r_out) arrays in pygfunction order: first element is the down (inlet) channel."""
        inner = (float(self.r_in_inner), float(self.r_out_inner))  # type: ignore[arg-type]
        outer = (float(self.r_in_outer), float(self.r_out_outer))  # type: ignore[arg-type]
        first, second = (outer, inner) if self.flow_down == "annulus" else (inner, outer)
        return np.array([first[0], second[0]]), np.array([first[1], second[1]])

    @staticmethod
    def _check_flow(m_flow_borehole: float) -> float:
        m = float(m_flow_borehole)
        if not (math.isfinite(m) and m > 0):
            raise ValueError(f"m_flow_borehole must be positive and finite, got {m_flow_borehole!r}")
        return m

    def fluid_to_pipe_resistance(
        self,
        m_flow_borehole: float,
        rho_f: float = rho_w,
        mu_f: float = mu_w,
        cp_f: float = c_w,
        k_f: float = k_w,
    ) -> float:
        """Fluid-to-outer-pipe-wall resistance ``R_fp`` [m·K/W] of one pipe.

        U-tubes: convection inside one leg (Gnielinski, pygfunction) plus the
        pipe wall. In ``"double_u_parallel"`` each leg carries half the
        borehole flow. Coaxial: convection on the annulus's outer surface plus
        the outer pipe wall (the path from the annulus fluid to the grout).
        """
        _require_pygfunction()
        m = self._check_flow(m_flow_borehole)
        _positive_finite(rho_f=rho_f, mu_f=mu_f, cp_f=cp_f, k_f=k_f)
        if self.kind == "coaxial":
            r_a_in, r_a_out = float(self.r_out_inner), float(self.r_in_outer)  # type: ignore[arg-type]
            _, h_out = gt.pipes.convective_heat_transfer_coefficient_concentric_annulus(
                m, r_a_in, r_a_out, mu_f, rho_f, k_f, cp_f, self.epsilon
            )
            R_wall = gt.pipes.conduction_thermal_resistance_circular_pipe(r_a_out, float(self.r_out_outer), self.k_p)  # type: ignore[arg-type]
            return float(R_wall + 1.0 / (2.0 * np.pi * r_a_out * h_out))
        m_pipe = m / 2.0 if self.kind == "double_u_parallel" else m
        h_f = gt.pipes.convective_heat_transfer_coefficient_circular_pipe(
            m_pipe, self.r_in, mu_f, rho_f, k_f, cp_f, epsilon=self.epsilon
        )
        R_conv = 1.0 / (2.0 * np.pi * self.r_in * h_f)
        R_cond = gt.pipes.conduction_thermal_resistance_circular_pipe(self.r_in, self.r_out, self.k_p)
        return float(R_conv + R_cond)

    def fluid_to_fluid_resistance(
        self,
        m_flow_borehole: float,
        rho_f: float = rho_w,
        mu_f: float = mu_w,
        cp_f: float = c_w,
        k_f: float = k_w,
    ) -> float:
        """Coaxial only: inner-pipe fluid to annulus fluid resistance ``R_ff`` [m·K/W]."""
        _require_pygfunction()
        if self.kind != "coaxial":
            raise ValueError("fluid_to_fluid_resistance applies to coaxial pipes only")
        m = self._check_flow(m_flow_borehole)
        _positive_finite(rho_f=rho_f, mu_f=mu_f, cp_f=cp_f, k_f=k_f)
        r_i_in, r_i_out = float(self.r_in_inner), float(self.r_out_inner)  # type: ignore[arg-type]
        r_a_out = float(self.r_in_outer)  # type: ignore[arg-type]
        k_p_inner = self.k_p if self.k_p_inner is None else self.k_p_inner
        h_in = gt.pipes.convective_heat_transfer_coefficient_circular_pipe(
            m, r_i_in, mu_f, rho_f, k_f, cp_f, self.epsilon
        )
        h_a_in, _ = gt.pipes.convective_heat_transfer_coefficient_concentric_annulus(
            m, r_i_out, r_a_out, mu_f, rho_f, k_f, cp_f, self.epsilon
        )
        R_wall = gt.pipes.conduction_thermal_resistance_circular_pipe(r_i_in, r_i_out, k_p_inner)
        return float(1.0 / (2.0 * np.pi * r_i_in * h_in) + R_wall + 1.0 / (2.0 * np.pi * r_i_out * h_a_in))

    def pygfunction_pipe(
        self,
        borehole: Any,
        k_s: float,
        m_flow_borehole: float,
        rho_f: float = rho_w,
        mu_f: float = mu_w,
        cp_f: float = c_w,
        k_f: float = k_w,
    ) -> Any:
        """pygfunction pipe object for ``borehole`` (a ``pygfunction.boreholes.Borehole``)."""
        _require_pygfunction()
        _positive_finite(k_s=k_s)
        self.check_fits(float(borehole.r_b))
        R_fp = self.fluid_to_pipe_resistance(m_flow_borehole, rho_f, mu_f, cp_f, k_f)
        if self.kind == "single_u":
            return gt.pipes.SingleUTube(
                self.positions(), self.r_in, self.r_out, borehole, k_s, self.k_g, R_fp, J=self.J
            )
        if self.kind.startswith("double_u"):
            config = "parallel" if self.kind == "double_u_parallel" else "series"
            return gt.pipes.MultipleUTube(
                self.positions(),
                self.r_in,
                self.r_out,
                borehole,
                k_s,
                self.k_g,
                R_fp,
                nPipes=2,
                config=config,
                J=self.J,
            )
        R_ff = self.fluid_to_fluid_resistance(m_flow_borehole, rho_f, mu_f, cp_f, k_f)
        r_in, r_out = self._coaxial_radii()
        return gt.pipes.Coaxial((0.0, 0.0), r_in, r_out, borehole, k_s, self.k_g, R_ff, R_fp, J=self.J)

    def _pipe(self, H: float, r_b: float, k_s: float, m_flow_borehole: float, fluid: tuple) -> Any:
        _positive_finite(H=H)
        borehole = gt.boreholes.Borehole(H=float(H), D=0.0, r_b=float(r_b), x=0.0, y=0.0)
        return self.pygfunction_pipe(borehole, k_s, m_flow_borehole, *fluid)

    def local_resistance(
        self,
        *,
        r_b: float,
        k_s: float,
        m_flow_borehole: float,
        rho_f: float = rho_w,
        mu_f: float = mu_w,
        cp_f: float = c_w,
        k_f: float = k_w,
    ) -> float:
        """Local (cross-section) borehole resistance ``R_b`` [m·K/W], multipole method."""
        pipe = self._pipe(100.0, r_b, k_s, m_flow_borehole, (rho_f, mu_f, cp_f, k_f))
        return float(pipe.local_borehole_thermal_resistance())

    def effective_resistance(
        self,
        H: float,
        *,
        r_b: float,
        k_s: float,
        m_flow_borehole: float,
        rho_f: float = rho_w,
        mu_f: float = mu_w,
        cp_f: float = c_w,
        k_f: float = k_w,
    ) -> float:
        """Effective borehole resistance ``R_b*`` [m·K/W] for a uniform wall temperature.

        Defined by ``Q/H = (T_b - (T_in + T_out)/2) / R_b*`` over a borehole of
        length ``H`` [m] (pygfunction ``effective_borehole_thermal_resistance``);
        it includes the thermal short-circuit between down and up channels.
        """
        pipe = self._pipe(H, r_b, k_s, m_flow_borehole, (rho_f, mu_f, cp_f, k_f))
        return float(pipe.effective_borehole_thermal_resistance(self._check_flow(m_flow_borehole), cp_f))


def precompute_borehole_resistance(
    m_flow_rated: float,
    min_ratio: float,
    max_ratio: float,
    H: float,
    cp_f: float,
    boundary_condition: str = "uniform_temperature",
    **geometry: float,
) -> Callable[[float], float]:
    """Precompute Rb* [m K/W] on a mass-flow grid, including the rated point.

    Uses the legacy local-resistance/axial model at initialization only. PCHIP
    interpolation preserves its shape; out-of-range flow is an input error,
    never silently extrapolated. The flow is per borehole, not field total.
    """
    if not all(np.isfinite(v) and v > 0 for v in (m_flow_rated, min_ratio, max_ratio, H, cp_f)):
        raise ValueError("Borehole flow, bounds, depth and heat capacity must be positive and finite")
    if min_ratio >= max_ratio:
        raise ValueError("min_ratio must be less than max_ratio")
    ratios = np.linspace(min_ratio, max_ratio, 101)
    if min_ratio <= 1 <= max_ratio:
        ratios = np.unique(np.append(ratios, 1.0))
    return precompute_borehole_resistance_from_flow(
        min_ratio * m_flow_rated,
        max_ratio * m_flow_rated,
        H,
        cp_f,
        boundary_condition,
        anchor_flows=(),
        _sample_flows=tuple(ratios * m_flow_rated),
        **geometry,
    )


def precompute_borehole_resistance_from_flow(
    m_flow_min: float,
    m_flow_max: float,
    H: float,
    cp_f: float,
    boundary_condition: str = "uniform_temperature",
    *,
    anchor_flows: tuple[float, ...] = (),
    _sample_flows: tuple[float, ...] | None = None,
    **geometry: float,
) -> Callable[[float], float]:
    """Interpolate Rb* using actual branch mass flow [kg/s], with no normalization.

    Reference/setpoint anchors reproduce their physical resistance exactly;
    changing the upper limit does not redefine any reference operating point.
    """
    if not all(np.isfinite(v) and v > 0 for v in (m_flow_min, m_flow_max, H, cp_f)) or m_flow_min >= m_flow_max:
        raise ValueError("Require positive finite mass flows with min < max, depth and heat capacity")
    if any(not np.isfinite(v) or not m_flow_min <= v <= m_flow_max for v in anchor_flows):
        raise ValueError("Borehole anchor flows must lie inside the actual flow bounds")
    samples = np.linspace(m_flow_min, m_flow_max, 101) if _sample_flows is None else _sample_flows
    flows = np.unique(np.append(samples, anchor_flows))
    resistances = []
    for flow in flows:
        local, internal = calc_local_borehole_thermal_resistance(m_flow_pipe=float(flow), cp_f=cp_f, **geometry)
        resistances.append(
            calc_effective_borehole_thermal_resistance(local, internal, H, float(flow), cp_f, boundary_condition)
        )
    if not np.all(np.isfinite(resistances)) or np.any(np.asarray(resistances) <= 0):
        raise ValueError("Borehole resistance grid contains invalid values")
    interp = PchipInterpolator(flows, resistances, extrapolate=False)
    # The caller computes rated_volume * ratio * rho / N, whereas this grid
    # computes ratio * (rated_volume * rho / N). Their endpoints can differ by
    # a few ULPs. Accept only that rounding tolerance, then evaluate the endpoint
    # itself; genuine out-of-range flow still must not extrapolate.
    endpoint_tolerance = 8 * np.finfo(float).eps * max(abs(flows[0]), abs(flows[-1]))

    def evaluate(m_flow_borehole: float) -> float:
        if (
            not np.isfinite(m_flow_borehole)
            or not flows[0] - endpoint_tolerance <= m_flow_borehole <= flows[-1] + endpoint_tolerance
        ):
            raise ValueError("Borehole flow is outside the precomputed resistance grid")
        return float(interp(np.clip(m_flow_borehole, flows[0], flows[-1])))

    return evaluate


def calc_local_borehole_thermal_resistance(
    k_s: float,
    k_g: float,
    k_p: float,
    r_b: float,
    r_out: float,
    r_in: float,
    D_s: float,
    m_flow_pipe: float,
    rho_f: float,
    mu_f: float,
    cp_f: float,
    k_f: float,
) -> tuple[float, float]:
    """Calculate the local borehole thermal resistance and internal thermal resistance [mK/W] using pygfunction multipole method.

    Assumes a Single U-tube configuration.

    Parameters
    ----------
    k_s : float
        Ground thermal conductivity [W/mK]
    k_g : float
        Grout thermal conductivity [W/mK]
    k_p : float
        Pipe thermal conductivity [W/mK]
    r_b : float
        Borehole radius [m]
    r_out : float
        Pipe outer radius [m]
    r_in : float
        Pipe inner radius [m]
    D_s : float
        Shank spacing (half distance between pipes) [m]
    m_flow_pipe : float
        Mass flow rate per pipe [kg/s]
    rho_f : float
        Fluid density [kg/m³]
    mu_f : float
        Fluid dynamic viscosity [Pa·s]
    cp_f : float
        Fluid specific heat capacity [J/kgK]
    k_f : float
        Fluid thermal conductivity [W/mK]

    Returns
    -------
    tuple[float, float]
        (R_b, R_a)
        R_b: Local borehole thermal resistance [mK/W].
        R_a: Internal (pipe-to-pipe) thermal resistance [mK/W]: the
        temperature difference between the two legs per unit heat flow
        when one leg gives and the other takes the same heat per metre,
        ``R_a = R_11 + R_22 - 2 R_12`` of the multipole resistance matrix
        (Hellström 1991). It is always positive. It is *not* the
        delta-circuit pipe-to-pipe branch ``R_12^Δ``, which can be
        negative for pipes close to the borehole wall; for a symmetric
        U-tube the two are related by ``1/R_a = 1/R_12^Δ + 1/(4 R_b)``.

    Notes
    -----
    Before #96 G6 the second value was ``R_12^Δ`` read from pygfunction's
    private ``_Rd``. :func:`calc_effective_borehole_thermal_resistance` now
    takes ``R_a`` in both branches; pass it the pair returned here.
    """
    if not HAS_PYGFUNCTION:
        raise ImportError("pygfunction is not installed.")

    # Offset positions for a Single U-tube
    pos = [(-D_s, 0.0), (D_s, 0.0)]

    # 1. Convective resistance
    if m_flow_pipe > 0:
        h_f = gt.pipes.convective_heat_transfer_coefficient_circular_pipe(
            m_flow_pipe, r_in, mu_f, rho_f, k_f, cp_f, epsilon=1e-6
        )
        R_conv = 1.0 / (2.0 * np.pi * r_in * h_f)
    else:
        # Prevent division by zero if there's no flow
        R_conv = 10.0

    # 2. Conduction resistance of the pipe wall
    R_cond = gt.pipes.conduction_thermal_resistance_circular_pipe(r_in, r_out, k_p)

    # Total internal fluid-to-outer-pipe resistance
    R_fp = R_conv + R_cond

    # Build dummy borehole object for structural representation
    borehole = gt.boreholes.Borehole(H=100.0, D=0.0, r_b=r_b, x=0.0, y=0.0)

    # Create Single U-tube
    pipe = gt.pipes.SingleUTube(pos, r_in, r_out, borehole, k_s, k_g, R_fp)

    R_b = pipe.local_borehole_thermal_resistance()
    # Internal resistance from the (public) multipole resistance matrix R, at
    # the same multipole order as the pipe object: T_f = T_b + R q'. With
    # q'_1 = -q'_2 the wall exchanges no net heat and T_f1 - T_f2 = R_a q'.
    R, _ = gt.pipes.thermal_resistances(pos, r_out, r_b, k_s, k_g, R_fp, J=pipe.J)
    R_a = float(R[0, 0] + R[1, 1] - 2.0 * R[0, 1])

    return float(R_b), R_a


def calc_effective_borehole_thermal_resistance(
    R_b: float,
    R_a: float,
    H: float,
    m_flow_pipe: float,
    cp_f: float,
    boundary_condition: str = "uniform_temperature",
) -> float:
    """Calculate the effective borehole thermal resistance [mK/W].

    Parameters
    ----------
    R_b : float
        Local borehole thermal resistance [mK/W]
    R_a : float
        Internal (pipe-to-pipe) thermal resistance [mK/W], as returned by
        :func:`calc_local_borehole_thermal_resistance` (must be positive).
    H : float
        Borehole depth [m]
    m_flow_pipe : float
        Mass flow rate per pipe [kg/s]; for a single U-tube, the borehole flow.
    cp_f : float
        Fluid specific heat capacity [J/kgK]
    boundary_condition : str
        Boundary condition for the calculation.
        Options: 'uniform_temperature' or 'uniform_heat_flux'.

    Returns
    -------
    float
        Effective borehole thermal resistance [mK/W].

    Notes
    -----
    With ``η = H / (ṁ c_p √(R_b R_a))`` (Hellström 1991):

    - uniform borehole-wall temperature: ``R_b* = R_b η coth η``
      (identical to pygfunction's
      ``SingleUTube.effective_borehole_thermal_resistance``);
    - uniform heat flux: ``R_b* = R_b + H² / (3 R_a (ṁ c_p)²) = R_b (1 + η²/3)``.

    Since ``η coth η = 1 + η²/3 - η⁴/45 + …``, the uniform-heat-flux value is
    never below the uniform-temperature one; they agree to first order and
    separate at low flow (large ``η``).

    References
    ----------
    1. Hellström, G. (1991). Ground Heat Storage: Thermal Analyses of Duct Storage Systems
       (Ph.D. thesis). University of Lund, Sweden.
    2. Lamarche, L., Kajl, S., & Beauchamp, B. (2010). A review of methods to evaluate
       borehole thermal resistances in geothermal heat-pump systems. Geothermics, 39(2), 187-200.
       DOI: 10.1016/j.geothermics.2010.03.003
    3. Javed, S., & Spitler, J. D. (2016). Accuracy of borehole thermal resistance
       calculation methods for grouted single U-tube ground heat exchangers.
       Applied Energy, 182, 161-176. DOI: 10.1016/j.apenergy.2016.08.054
    """
    if boundary_condition not in ("uniform_temperature", "uniform_heat_flux"):
        raise ValueError("boundary_condition must be 'uniform_temperature' or 'uniform_heat_flux'")
    if m_flow_pipe <= 0:
        return R_b
    if not (np.isfinite(R_a) and R_a > 0 and np.isfinite(R_b) and R_b > 0):
        raise ValueError(f"R_b and R_a must be positive and finite, got R_b={R_b!r}, R_a={R_a!r}")

    m_cp = m_flow_pipe * cp_f
    if boundary_condition == "uniform_temperature":
        # Hellström (1991) analytical solution for uniform borehole wall temperature
        eta = H / (m_cp * np.sqrt(R_b * R_a))
        if eta < 1e-6:
            return R_b
        return float(R_b * eta / np.tanh(eta))

    # Hellström (1991) for uniform heat flux: R_b* = R_b + H^2 / (3 R_a (m cp)^2)
    return float(R_b + H**2 / (3.0 * R_a * m_cp**2))


def calc_borehole_thermal_resistance(
    k_s: float,
    k_g: float,
    k_p: float,
    r_b: float,
    r_out: float,
    r_in: float,
    D_s: float,
    H_b: float,
    m_flow_borehole: float,
    rho_f: float,
    mu_f: float,
    cp_f: float,
    k_f: float,
) -> float:
    """Calculate the effective borehole thermal resistance R_b* [mK/W] for a Single U-tube.

    Implements the full three-stage calculation in a single call:

    Stage 1 — Fluid-to-pipe resistance (R_fp):
        R_fp = R_conv + R_cond
        R_conv: convective resistance inside the pipe (Gnielinski correlation).
        R_cond: conductive resistance through the pipe wall (ln(r_out/r_in)/(2πk_p)).

    Stage 2 — 2D cross-section (Local R_b via multipole method):
        SingleUTube solves the steady-state 2D Laplace equation in the grout
        cross-section using Hellström's multipole expansion at pygfunction's
        default order J=2 (for the default GSHPB geometry at 24 L/min and
        H_b = 200 m, R_b* is 5e-5 relative below its J=10 value: 0.164398 vs
        0.164405 m·K/W); see Javed and Spitler (2016) for an accuracy
        benchmark of this method.
        Boundary conditions: R_fp at each pipe surface, T=const at borehole wall.
        Outputs: Local R_b (fluid → borehole wall, cross-section only).

    Stage 3 — Axial short-circuit correction (Effective R_b*):
        pipe.effective_borehole_thermal_resistance() applies the Cimmino /
        Hellström analytical solution for axial fluid temperature variation and
        thermal short-circuiting between the two U-tube legs.

    For a Single U-tube (series flow), each pipe leg carries the full borehole
    flow rate; m_flow_borehole is passed directly without division by 2.

    Parameters
    ----------
    k_s : float
        Ground thermal conductivity [W/mK]
    k_g : float
        Grout thermal conductivity [W/mK]
    k_p : float
        Pipe thermal conductivity [W/mK]
    r_b : float
        Borehole radius [m]
    r_out : float
        Pipe outer radius [m]
    r_in : float
        Pipe inner radius [m]
    D_s : float
        Distance from borehole centre to pipe centre [m]
    H_b : float
        Borehole depth [m]
    m_flow_borehole : float
        Total fluid mass flow rate into the borehole [kg/s].
        For a Single U-tube (series), this equals the flow in each pipe leg.
    rho_f : float
        Fluid density [kg/m³]
    mu_f : float
        Fluid dynamic viscosity [Pa·s]
    cp_f : float
        Fluid specific heat capacity [J/kgK]
    k_f : float
        Fluid thermal conductivity [W/mK]

    Returns
    -------
    float
        Effective borehole thermal resistance R_b* [mK/W].

    References
    ----------
    Hellström, G. (1991). Ground Heat Storage: Thermal Analyses of Duct
    Storage Systems (Ph.D. thesis). University of Lund, Sweden.

    Claesson, J., & Hellström, G. (2011). Multipole method to calculate
    borehole thermal resistances in a borehole heat exchanger. HVAC&R
    Research, 17(6), 895-911. DOI: 10.1080/10789669.2011.609927

    Javed, S., & Spitler, J. D. (2016). Accuracy of borehole thermal
    resistance calculation methods for grouted single U-tube ground heat
    exchangers. Applied Energy, 182, 161-176. DOI:
    10.1016/j.apenergy.2016.08.054
    """
    if not HAS_PYGFUNCTION:
        raise ImportError("pygfunction is not installed.")

    # --- Stage 1: R_fp (1D analytic, fluid → pipe outer wall) ---
    if m_flow_borehole > 0:
        h_f = gt.pipes.convective_heat_transfer_coefficient_circular_pipe(
            m_flow_borehole, r_in, mu_f, rho_f, k_f, cp_f, epsilon=1e-6
        )
        R_conv = 1.0 / (2.0 * np.pi * r_in * h_f)
    else:
        R_conv = 10.0  # large fallback when there is no flow
    R_cond = gt.pipes.conduction_thermal_resistance_circular_pipe(r_in, r_out, k_p)
    R_fp = R_conv + R_cond

    # --- Stage 2 + 3: 2D multipole → Local R_b, then axial correction → R_b* ---
    pos = [(-D_s, 0.0), (D_s, 0.0)]
    borehole = gt.boreholes.Borehole(H=H_b, D=0.0, r_b=r_b, x=0.0, y=0.0)
    pipe = gt.pipes.SingleUTube(pos, r_in, r_out, borehole, k_s, k_g, R_fp)

    # pygfunction public API: internally applies Cimmino/Hellström axial correction
    R_b_eff = pipe.effective_borehole_thermal_resistance(m_flow_borehole, cp_f)

    return float(R_b_eff)
