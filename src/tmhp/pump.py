"""Darcy-Weisbach pipe loss and pump power for identical parallel U-tubes.

This is a system model, not a header/manifold hydraulic design tool.
"""

import math
import warnings

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


def calc_aux_pressure_drop(
    volume_flow: float,
    volume_flow_ref: float,
    dp_aux_ref: float,
    exponent: float = 2.0,
) -> float:
    """Lumped HX/header/valve/piping loss [Pa] at actual total flow [m³/s].

    The reference flow is a normalization point, independent of control limits.
    Quadratic scaling approximates turbulent system resistance; this is not
    static elevation head or a detailed hydraulic network model.
    """
    if not all(math.isfinite(x) for x in (volume_flow, volume_flow_ref, dp_aux_ref, exponent)):
        raise ValueError("Auxiliary pressure-drop inputs must be finite")
    if volume_flow < 0 or volume_flow_ref <= 0 or dp_aux_ref < 0 or exponent <= 0:
        raise ValueError("Require flow >= 0, reference flow > 0, dp_aux_ref >= 0 and exponent > 0")
    return float(dp_aux_ref * (volume_flow / volume_flow_ref) ** exponent)


def resolve_aux_pressure_drop(dp_common: float | None, dp_aux_ref: float, exponent: float) -> float:
    """Migrate deprecated common loss to a reference loss, never a constant."""
    calc_aux_pressure_drop(0.0, 1.0, dp_aux_ref, exponent)
    if dp_common is None:
        return dp_aux_ref
    if not math.isfinite(dp_common) or dp_common < 0:
        raise ValueError("dp_common must be finite and nonnegative")
    if dp_aux_ref != 0.0 and dp_common != 0.0:
        raise ValueError("Supply dp_aux_ref or deprecated dp_common, not both nonzero")
    warnings.warn(
        "dp_common is deprecated and now denotes auxiliary loss at reference flow; "
        "use dp_aux_ref and dp_aux_exponent. Actual loss scales with flow.",
        DeprecationWarning,
        stacklevel=3,
    )
    return dp_aux_ref if dp_common == 0 else dp_common


def calc_parallel_borefield_pressure_drop(
    m_dot_total: float,
    n_boreholes: int,
    H_b: float,
    diameter: float,
    rho: float,
    mu: float,
    roughness: float = 1e-6,
    dp_common: float | None = None,
    *,
    volume_flow_ref: float | None = None,
    dp_aux_ref: float = 0.0,
    dp_aux_exponent: float = 2.0,
) -> float:
    """One U-tube branch loss (2H) plus flow-scaled auxiliary loss [Pa].

    Parallel branch losses are not summed. Nonzero auxiliary loss requires an
    explicit reference flow [m³/s], including calls using deprecated dp_common.
    Without a supplied reference, BHE-only calls retain their previous behavior.
    """
    calc_total_borehole_length(n_boreholes, H_b)
    auxiliary_ref = resolve_aux_pressure_drop(dp_common, dp_aux_ref, dp_aux_exponent)
    branch_flow = calc_borehole_mass_flow(m_dot_total, n_boreholes)
    dp_bhe = calc_pipe_pressure_drop(branch_flow, 2 * H_b, diameter, rho, mu, roughness)
    if volume_flow_ref is None:
        if auxiliary_ref > 0:
            raise ValueError("Nonzero auxiliary loss requires volume_flow_ref [m³/s]")
        return dp_bhe
    return dp_bhe + calc_aux_pressure_drop(m_dot_total / rho, volume_flow_ref, auxiliary_ref, dp_aux_exponent)


def calc_pump_power(pressure_drop: float, volume_flow: float, efficiency: float) -> float:
    """Electric pump power [W] from pressure [Pa], flow [m³/s] and efficiency."""
    if not all(math.isfinite(x) for x in (pressure_drop, volume_flow, efficiency)):
        raise ValueError("Pump inputs must be finite")
    if pressure_drop < 0 or volume_flow < 0 or not 0 < efficiency <= 1:
        raise ValueError("Pump requires nonnegative pressure/flow and 0 < efficiency <= 1")
    return pressure_drop * volume_flow / efficiency
