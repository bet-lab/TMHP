"""Borehole wall-to-fluid resistance; ground response lives in g_function."""

from collections.abc import Callable

import numpy as np
from scipy.interpolate import PchipInterpolator

try:
    import pygfunction as gt

    HAS_PYGFUNCTION = True
except ImportError:
    HAS_PYGFUNCTION = False


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
    flows = ratios * m_flow_rated
    resistances = []
    for flow in flows:
        local, internal = calc_local_borehole_thermal_resistance(m_flow_pipe=float(flow), cp_f=cp_f, **geometry)
        resistances.append(
            calc_effective_borehole_thermal_resistance(local, internal, H, float(flow), cp_f, boundary_condition)
        )
    if not np.all(np.isfinite(resistances)) or np.any(np.asarray(resistances) <= 0):
        raise ValueError("Borehole resistance grid contains invalid values")
    interp = PchipInterpolator(flows, resistances, extrapolate=False)

    def evaluate(m_flow_borehole: float) -> float:
        if not np.isfinite(m_flow_borehole) or not flows[0] <= m_flow_borehole <= flows[-1]:
            raise ValueError("Borehole flow is outside the precomputed resistance grid")
        return float(interp(m_flow_borehole))

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
        R_a: Internal thermal resistance between the two pipes [mK/W].
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
    # In pygfunction, _Rd is the delta-circuit thermal resistance matrix
    # The resistance between the two pipes (node 0 and node 1) is _Rd[0, 1]
    R_a = pipe._Rd[0, 1]

    return R_b, R_a


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
        Internal thermal resistance between the two pipes [mK/W]
    H : float
        Borehole depth [m]
    m_flow_pipe : float
        Mass flow rate per pipe [kg/s]
    cp_f : float
        Fluid specific heat capacity [J/kgK]
    boundary_condition : str
        Boundary condition for the calculation.
        Options: 'uniform_temperature' or 'uniform_heat_flux'.

    Returns
    -------
    float
        Effective borehole thermal resistance [mK/W].

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
    if m_flow_pipe <= 0:
        return R_b

    if boundary_condition == "uniform_temperature":
        # Hellström (1991) analytical solution for uniform borehole wall temperature
        eta = (H / (m_flow_pipe * cp_f)) * (1.0 / (2.0 * R_b)) * np.sqrt(1.0 + (4.0 * R_b) / R_a)
        if eta < 1e-6:
            return R_b
        return float(R_b * eta / np.tanh(eta))

    elif boundary_condition == "uniform_heat_flux":
        # Hellström approximation for uniform heat flux
        # R_b* = R_b + H^2 / (3 * R_a * (2 * m_flow * cp)^2)
        return R_b + (H**2) / (3.0 * R_a * (2.0 * m_flow_pipe * cp_f) ** 2)

    else:
        raise ValueError("boundary_condition must be 'uniform_temperature' or 'uniform_heat_flux'")


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
        cross-section using Hellström's multipole expansion (default order
        J=10); see Javed and Spitler (2016) for an accuracy benchmark of this
        method.
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
