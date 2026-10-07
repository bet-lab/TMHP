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


def calc_aux_loss_from_ashrae_grade(
    grade: str,
    *,
    volume_flow_ref: float,
    n_boreholes: int,
    H_b: float,
    pipe_inner_diameter: float,
    rho: float = 1000.0,
    mu: float = 0.001,
    roughness: float = 1e-6,
    auxiliary_pipe_inner_diameter: float | None = None,
) -> dict:
    """Calibrate equivalent auxiliary resistance at an explicit total flow.

    Single source: Kavanaugh & Rafferty (2014), ASHRAE, *Geothermal
    Heating and Cooling: Design of Ground-Source Heat Pump Systems*,
    Table 6.2, p. 185. The SI pressure budgets are A <140, B 140-210,
    C 210-280, D 280-420 and F >420 kPa, at 3 L/min/kW and 70%
    hydraulic pump efficiency. This helper uses the finite upper boundary
    of A-D, not certification of a pumping grade. F has no finite upper bound.

    ``volume_flow_ref`` [m³/s] is supplied by the caller, never derived from
    capacity. Applying the table pressure at another flow is a modeling
    assumption. The table's 70% does not replace the electrical pump map.
    Subtract one parallel branch's straight-pipe loss (length 2H) from the
    pressure budget; the remainder represents all unmodeled loop losses.
    ``K_aux`` uses total flow through the auxiliary pipe; ``K_aux_branch``
    uses one borehole's flow and diameter. Both are equivalent coefficients,
    not sums of measured fitting coefficients. Use returned ``dp_aux_ref``
    with the same reference flow and quadratic auxiliary-loss scaling.
    """
    budgets = {"A": 140000.0, "B": 210000.0, "C": 280000.0, "D": 420000.0}
    if not isinstance(grade, str) or grade.upper() not in budgets:
        raise ValueError("Require ASHRAE grade A, B, C or D; F has no finite upper pressure bound")
    grade = grade.upper()
    if not math.isfinite(volume_flow_ref) or volume_flow_ref <= 0:
        raise ValueError("volume_flow_ref must be finite and positive [m³/s]")
    calc_total_borehole_length(n_boreholes, H_b)
    # Validate rho before dividing by it in downstream hydraulic calculations.
    if not math.isfinite(rho) or rho <= 0:
        raise ValueError("rho must be finite and positive")
    diameter = pipe_inner_diameter if auxiliary_pipe_inner_diameter is None else auxiliary_pipe_inner_diameter
    if not math.isfinite(diameter) or diameter <= 0:
        raise ValueError("Auxiliary pipe diameter must be finite and positive")
    branch_mass = calc_borehole_mass_flow(rho * volume_flow_ref, n_boreholes)
    pressure_bhe = calc_pipe_pressure_drop(branch_mass, 2 * H_b, pipe_inner_diameter, rho, mu, roughness)
    pressure_target = budgets[grade]
    pressure_aux = pressure_target - pressure_bhe
    if pressure_aux < 0:
        raise ValueError(f"Borehole loss {pressure_bhe:g} Pa exceeds grade {grade} budget {pressure_target:g} Pa")
    velocity_aux = volume_flow_ref / (math.pi * diameter**2 / 4)
    velocity_branch = volume_flow_ref / n_boreholes / (math.pi * pipe_inner_diameter**2 / 4)
    return {
        "grade": grade,
        "volume_flow_ref": volume_flow_ref,
        "target_pressure_Pa": pressure_target,
        "pressure_bhe_Pa": pressure_bhe,
        "dp_aux_ref": pressure_aux,
        "K_aux": pressure_aux / (rho * velocity_aux**2 / 2),
        "K_aux_branch": pressure_aux / (rho * velocity_branch**2 / 2),
    }


def calc_pump_power(pressure_drop: float, volume_flow: float, efficiency: float) -> float:
    """Electric pump power [W] from pressure [Pa], flow [m³/s] and efficiency."""
    if not all(math.isfinite(x) for x in (pressure_drop, volume_flow, efficiency)):
        raise ValueError("Pump inputs must be finite")
    if pressure_drop < 0 or volume_flow < 0 or not 0 < efficiency <= 1:
        raise ValueError("Pump requires nonnegative pressure/flow and 0 < efficiency <= 1")
    return pressure_drop * volume_flow / efficiency


class PumpPerformanceMap:
    """Catalogue fit of head and electrical input versus flow and speed.

    Flow ``q`` is in m³/h and ``s`` is the catalogue speed fraction. Head uses
    ``[s², qs, q², q³/s, s, 1]``; input power uses
    ``[1, s, s², s³, qs², q²s, q³]``. Coefficients and the supported domain are
    supplied by the caller; no manufacturer is selected as a library default.
    Electrical input includes motor/drive losses at the catalogue fluid state.
    Density changes hydraulic duty; the electrical fit has no fluid correction.
    """

    def __init__(self, parameters: dict):
        """Validate coefficients and the documented catalogue domain."""
        self.head_coefficients = tuple(float(x) for x in parameters["head_coefficients"])
        self.power_coefficients = tuple(float(x) for x in parameters["power_coefficients"])
        self.speed_min = float(parameters["speed_min"])
        self.speed_max = float(parameters["speed_max"])
        self.flow_min = float(parameters["flow_per_speed_min_m3_h"])
        self.flow_max = float(parameters["flow_per_speed_max_m3_h"])
        if len(self.head_coefficients) != 6 or len(self.power_coefficients) != 7:
            raise ValueError("Pump map requires 6 head and 7 input-power coefficients")
        values = (
            *self.head_coefficients,
            *self.power_coefficients,
            self.speed_min,
            self.speed_max,
            self.flow_min,
            self.flow_max,
        )
        if not all(math.isfinite(x) for x in values):
            raise ValueError("Pump map parameters must be finite")
        if not (0 < self.speed_min < self.speed_max and 0 < self.flow_min < self.flow_max):
            raise ValueError("Pump map requires ordered positive speed and reduced-flow bounds")

    def head(self, flow_m3_h: float, speed_ratio: float) -> float:
        """Evaluate the fitted head [m] at a positive speed fraction."""
        q, s = flow_m3_h, speed_ratio
        return sum(
            a * x
            for a, x in zip(
                self.head_coefficients,
                (s * s, q * s, q * q, q * q * q / s, s, 1),
                strict=True,
            )
        )

    def input_power(self, flow_m3_h: float, speed_ratio: float) -> float:
        """Evaluate fitted electrical input [W], including motor/drive losses."""
        q, s = flow_m3_h, speed_ratio
        return sum(
            a * x
            for a, x in zip(
                self.power_coefficients,
                (1, s, s * s, s * s * s, q * s * s, q * q * s, q * q * q),
                strict=True,
            )
        )

    def operating_point(self, pressure_drop: float, volume_flow: float, rho: float) -> dict:
        """Solve required speed within the observed domain; do not extrapolate.

        Zero flow represents a stopped pump with zero active input, excluding
        standby electronics. An unattainable duty raises ``ValueError``.
        """
        from scipy.optimize import brentq

        if not all(math.isfinite(x) for x in (pressure_drop, volume_flow, rho)):
            raise ValueError("Pump duty must be finite")
        if pressure_drop < 0 or volume_flow < 0 or rho <= 0:
            raise ValueError("Require nonnegative pump duty and positive density")
        if volume_flow == 0:
            return {"power_W": 0.0, "speed_ratio": 0.0, "efficiency": math.nan}
        q = volume_flow * 3600
        required = pressure_drop / (rho * 9.80665)
        lo = max(self.speed_min, q / self.flow_max)
        hi = min(self.speed_max, q / self.flow_min)
        if lo > hi:
            raise ValueError("Pump flow lies outside the catalogue domain")
        f_lo, f_hi = self.head(q, lo) - required, self.head(q, hi) - required
        if f_lo > 1e-8 or f_hi < -1e-8:
            raise ValueError("Required pump head lies outside the catalogue domain")
        speed = (
            lo
            if abs(f_lo) <= 1e-8
            else hi
            if abs(f_hi) <= 1e-8
            else brentq(lambda s: self.head(q, s) - required, lo, hi, xtol=1e-12)
        )
        power = self.input_power(q, speed)
        hydraulic = pressure_drop * volume_flow
        if not math.isfinite(power) or power <= 0 or power < hydraulic:
            raise ValueError("Pump fit yields nonphysical electrical input")
        return {"power_W": power, "speed_ratio": speed, "efficiency": hydraulic / power}
