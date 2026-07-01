"""Borehole g-function and air property helpers.

Provides:
- Finite Line Source (FLS) g-function for borehole heat exchangers
- Air dynamic viscosity (Sutherland's formula) and Prandtl number
"""

import numpy as np
from scipy import integrate
from scipy.interpolate import interp1d
from scipy.special import erf

from . import calc_util as cu
from .constants import SP

try:
    import pygfunction as gt

    HAS_PYGFUNCTION = True
except ImportError:
    HAS_PYGFUNCTION = False


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
) -> interp1d:
    """Precompute g-function using pygfunction and return an interpolator.

    Creates a rectangular borehole field and computes the g-function
    for log-spaced time steps up to t_max_s (plus an extra margin).
    Returns a callable `interp1d` object predicting the g-function [mK/W].

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
        Maximum simulation time [s].
    dt_s : float
        Simulation timestep [s].

    Returns
    -------
    scipy.interpolate.interp1d
        Interpolator function mapping `time [s]` to `g-function [mK/W]`.
    """

    if not HAS_PYGFUNCTION:
        raise ImportError(
            "pygfunction is not installed. Run `uv pip install pygfunction` to use multi-borehole features."
        )

    # Evaluate from 1 hour to bypass the short-term numerical noise (Fo < 0.1)
    # of the finite line source BEM discretization.
    # The first point is safely evaluated at 3600s where the numerical noise floor is cleared.
    t_min = max(dt_s, 3600.0)
    times = np.geomspace(t_min, t_max_s * 1.5, num=100)

    boreField = gt.borefield.Borefield.rectangle_field(
        N_1=N_1, N_2=N_2, B_1=B, B_2=B, H=H_b, D=D_b, r_b=r_b
    )

    # Use uniform_heat_flux to ensure stability and compatibility with fundamental FLS assumptions
    options = {"method": "uniform_heat_flux"}
    gfunc_obj = gt.gfunction.gFunction(boreField, alpha_s, time=times, options=options)
    g_vals_dim = gfunc_obj.gFunc / (2 * np.pi * k_s)

    # Prepend 0.0 for t=0.
    # This automatically provides a noise-free linear interpolation for any dt < 3600s !
    times = np.concatenate(([0.0], times))
    g_vals_dim = np.concatenate(([0.0], g_vals_dim))

    # Create interpolator
    return interp1d(times, g_vals_dim, kind="linear", bounds_error=False, fill_value="extrapolate")


def chi_mfls(s, r, H, x_prime, U, alpha_s, z0=0):
    """
    Helper function for MFLS (Moving Finite Line Source) G-function calculation.

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
    """Precompute the MFLS g-function and return an interpolator."""
    if not HAS_PYGFUNCTION:
        raise ImportError("pygfunction is not installed.")

    t_min = max(dt_s, 3600.0)
    times = np.geomspace(t_min, t_max_s * 1.5, num=50)

    boreField = gt.borefield.Borefield.rectangle_field(
        N_1=N_1, N_2=N_2, B_1=B, B_2=B, H=H_b, D=D_b, r_b=r_b
    )

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
        return R_b + (H**2) / (3.0 * R_a * (2.0 * m_flow_pipe * cp_f)**2)

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
        Nu = 0.3 + (0.62 * Re_D**0.5 * Pr**(1/3)) / (1 + (0.4/Pr)**(2/3))**0.25 * (1 + (Re_D/282000.0)**(5/8))**0.8
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
