"""Darcy-Weisbach pipe loss and pump power for identical parallel U-tubes.

This is a system model, not a header/manifold hydraulic design tool.
"""

import math

import numpy as np

from .ground_loop import calc_borehole_mass_flow, calc_total_borehole_length


def darcy_friction_factor(Re: float, e: float, d: float, is_active: bool = True) -> float:
    """Calculate the Darcy friction factor.

    Uses Haaland equation.

    Parameters
    ----------
    Re : float
        Reynolds number.
    e : float
        Surface roughness [m].
    d : float
        Diameter [m].
    is_active : bool, optional
        If False, returns np.nan.

    Returns
    -------
    float
        Friction factor.
    """
    if not is_active:
        return np.nan

    if Re < 2300:
        return 64.0 / max(Re, 1e-10)

    return 1.0 / (-1.8 * math.log10((e / d / 3.7) ** 1.11 + 6.9 / Re)) ** 2


def calc_pipe_pressure_drop(
    m_dot: float,
    length: float,
    diameter: float,
    rho: float,
    mu: float,
    roughness: float = 1e-6,
) -> float:
    """Straight-pipe pressure loss [Pa], with mass flow in kg/s and SI properties."""
    values = (m_dot, length, diameter, rho, mu, roughness)
    if not all(math.isfinite(x) for x in values):
        raise ValueError("Hydraulic inputs must be finite")
    if min(m_dot, length, roughness) < 0 or min(diameter, rho, mu) <= 0:
        raise ValueError("Invalid flow, pipe geometry or fluid properties")
    if m_dot == 0 or length == 0:
        return 0.0
    area = math.pi * diameter**2 / 4
    velocity = m_dot / (rho * area)
    reynolds = rho * velocity * diameter / mu
    friction = darcy_friction_factor(reynolds, roughness, diameter)
    return friction * length / diameter * rho * velocity**2 / 2


def calc_parallel_borefield_pressure_drop(
    m_dot_total: float,
    n_boreholes: int,
    H_b: float,
    diameter: float,
    rho: float,
    mu: float,
    roughness: float = 1e-6,
    dp_common: float = 0.0,
) -> float:
    """One branch loss (two legs, 2H) plus optional common loss [Pa].

    dp_common is a prescribed pressure loss at the evaluated operating point.
    Branch losses are NOT summed over parallel boreholes. Default header loss=0.
    """
    calc_total_borehole_length(n_boreholes, H_b)
    if not math.isfinite(dp_common) or dp_common < 0:
        raise ValueError("dp_common must be finite and nonnegative")
    branch_flow = calc_borehole_mass_flow(m_dot_total, n_boreholes)
    dp = calc_pipe_pressure_drop(branch_flow, 2 * H_b, diameter, rho, mu, roughness)
    return dp + dp_common if m_dot_total > 0 else 0.0


def calc_pump_power(pressure_drop: float, volume_flow: float, efficiency: float) -> float:
    """Electric pump power [W] from pressure [Pa], flow [m³/s] and efficiency."""
    if not all(math.isfinite(x) for x in (pressure_drop, volume_flow, efficiency)):
        raise ValueError("Pump inputs must be finite")
    if pressure_drop < 0 or volume_flow < 0 or not 0 < efficiency <= 1:
        raise ValueError("Pump requires nonnegative pressure/flow and 0 < efficiency <= 1")
    return pressure_drop * volume_flow / efficiency
