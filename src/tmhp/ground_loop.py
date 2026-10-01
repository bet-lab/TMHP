"""Borefield bookkeeping; heat extraction from the ground is positive.

Heat rates are field totals [W], flow rates are field totals [kg/s], and
borehole resistance is a per-length quantity [m K/W], never a field resistance.
"""

import math


def calc_borehole_count(N_1: int, N_2: int) -> int:
    """Return the number of identical parallel boreholes."""
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in (N_1, N_2)):
        raise ValueError("N_1 and N_2 must be positive integers")
    return N_1 * N_2


def calc_total_borehole_length(n_boreholes: int, H_b: float) -> float:
    """Total active borehole length [m] (not the U-tube pipe length)."""
    calc_borehole_count(n_boreholes, 1)
    if not math.isfinite(H_b) or H_b <= 0:
        raise ValueError("H_b must be finite and positive")
    return n_boreholes * H_b


def calc_borehole_mass_flow(m_dot_total: float, n_boreholes: int) -> float:
    """Mass flow in one parallel U-tube [kg/s]."""
    calc_borehole_count(n_boreholes, 1)
    if not math.isfinite(m_dot_total) or m_dot_total < 0:
        raise ValueError("m_dot_total must be finite and nonnegative")
    return m_dot_total / n_boreholes


def calc_borefield_linear_load(Q_bhe_total: float, n_boreholes: int, H_b: float) -> float:
    """Convert signed field-total heat extraction [W] to linear load [W/m]."""
    if not math.isfinite(Q_bhe_total):
        raise ValueError("Q_bhe_total must be finite")
    return Q_bhe_total / calc_total_borehole_length(n_boreholes, H_b)


def calc_bhe_fluid_temperatures(
    T_wall: float,
    Q_bhe_total: float,
    n_boreholes: int,
    H_b: float,
    R_b_eff: float,
    m_dot_total: float,
    cp: float,
) -> tuple[float, float, float]:
    """Return (mean, BHE inlet, BHE outlet) in the input temperature units.

    Zero flow is allowed only for an unloaded loop, where all temperatures
    equal the wall temperature. Positive load heats the fluid in the ground.
    """
    q = calc_borefield_linear_load(Q_bhe_total, n_boreholes, H_b)
    calc_borehole_mass_flow(m_dot_total, n_boreholes)
    if not all(math.isfinite(v) for v in (T_wall, R_b_eff, cp)) or R_b_eff < 0 or cp <= 0:
        raise ValueError("Invalid wall temperature, resistance or heat capacity")
    if m_dot_total == 0:
        if Q_bhe_total != 0:
            raise ValueError("Nonzero heat transfer requires positive mass flow")
        return T_wall, T_wall, T_wall
    T_mean = T_wall - q * R_b_eff
    dT_half = Q_bhe_total / (2 * m_dot_total * cp)
    return T_mean, T_mean - dT_half, T_mean + dT_half
