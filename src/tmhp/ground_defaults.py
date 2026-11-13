"""Capacity-proportional ground-side defaults and sizing checks (#96).

A model that is given only its capacity sizes the ground side from the heat
the ground loop carries at the rating point. The rules here are shared by the
ground- and water-source families; a value the user supplies always replaces
the default it would have produced.

- :func:`default_ground_flow_lpm` -- loop flow from the rated source-side heat
- :func:`default_borefield_layout` -- borehole count from the specific load
- :func:`warn_if_laminar_ground_flow`, :func:`warn_if_borefield_undersized` --
  sizing slips reported instead of being solved silently
- the default pump power is :func:`tmhp.pump.default_ground_pump_power`
"""

import math
import warnings

from .ground_loop import calc_borehole_mass_flow, calc_total_borehole_length

#: Source-side heat at the rating point as a share of the rated capacity: a
#: heating machine at COP 4 takes 0.75 of its capacity from the ground.
RATED_SOURCE_HEAT_FRACTION = 0.75
#: Ground-loop temperature change [K] at that heat, which fixes the default
#: flow (about 3.6 L/min per kW of capacity for water).
GROUND_LOOP_DESIGN_DT_K = 3.0
#: Specific borehole load [W/m] at the rated source-side heat that the default
#: borefield stays below, and the load above which a user-given field is
#: reported as undersized (VDI 4640-2 specific extraction rates span about
#: 20-80 W/m between dry sediment and water-bearing rock).
BOREHOLE_DESIGN_LINEAR_LOAD = 50.0
BOREHOLE_MAX_LINEAR_LOAD = 80.0
#: Default active depth of one borehole [m].
BOREHOLE_DEFAULT_DEPTH = 100.0
#: Pipe Reynolds number below which the U-tube flow is laminar.
LAMINAR_REYNOLDS_LIMIT = 2300.0


def calc_rated_source_heat(hp_capacity: float, source_heat_fraction: float = RATED_SOURCE_HEAT_FRACTION) -> float:
    """Source-side heat [W] at the rating point, ``source_heat_fraction * hp_capacity``."""
    if not all(math.isfinite(v) and v > 0 for v in (hp_capacity, source_heat_fraction)):
        raise ValueError("hp_capacity and source_heat_fraction must be finite and positive")
    return source_heat_fraction * hp_capacity


def default_ground_flow_lpm(
    hp_capacity: float,
    *,
    source_heat_fraction: float = RATED_SOURCE_HEAT_FRACTION,
    delta_T_K: float = GROUND_LOOP_DESIGN_DT_K,
    rho: float = 1000.0,
    cp: float = 4186.0,
) -> float:
    """Default field-total source-side flow [L/min] for a rated capacity [W].

    ``0.75 * hp_capacity / (rho * cp * 3 K)``: the flow at which the rated
    source-side heat changes the loop temperature by 3 K (14.3 L/min of water
    for 4 kW, 3.58 L/min per kW). In cooling, where the loop carries about
    1.2 times the capacity, the same flow gives about 4.8 K. Parallel
    boreholes share this flow equally.
    """
    heat = calc_rated_source_heat(hp_capacity, source_heat_fraction)
    if not all(math.isfinite(v) and v > 0 for v in (delta_T_K, rho, cp)):
        raise ValueError("delta_T_K, rho and cp must be finite and positive")
    return heat / (rho * cp * delta_T_K) * 60000.0


def calc_rated_linear_load(
    hp_capacity: float,
    n_boreholes: int,
    H_b: float,
    *,
    source_heat_fraction: float = RATED_SOURCE_HEAT_FRACTION,
) -> float:
    """Specific borehole load [W/m] at the rated source-side heat."""
    return calc_rated_source_heat(hp_capacity, source_heat_fraction) / calc_total_borehole_length(n_boreholes, H_b)


def default_borefield_layout(
    hp_capacity: float,
    H_b: float = BOREHOLE_DEFAULT_DEPTH,
    *,
    max_linear_load: float = BOREHOLE_DESIGN_LINEAR_LOAD,
    source_heat_fraction: float = RATED_SOURCE_HEAT_FRACTION,
) -> tuple[int, int]:
    """Smallest near-square ``(N_1, N_2)`` field that keeps the rated load within ``max_linear_load``.

    The number of boreholes of depth ``H_b`` is the smallest that carries the
    rated source-side heat at no more than ``max_linear_load`` [W/m]; it is
    then rounded up to a rectangle with ``N_1 = ceil(sqrt(n))`` columns. One
    100 m borehole serves up to 6.67 kW of capacity; 8 kW gets 2 x 1, 14 kW
    2 x 2, 30 kW 3 x 2 and 100 kW 4 x 4.
    """
    if not math.isfinite(max_linear_load) or max_linear_load <= 0:
        raise ValueError("max_linear_load must be finite and positive")
    length = calc_rated_source_heat(hp_capacity, source_heat_fraction) / max_linear_load
    calc_total_borehole_length(1, H_b)
    # A count that is an integer up to rounding must not gain a borehole.
    needed = max(1, math.ceil(length / H_b - 1e-9))
    n_1 = math.ceil(math.sqrt(needed) - 1e-9)
    n_2 = math.ceil(needed / n_1 - 1e-9)
    return n_1, n_2


def calc_borehole_reynolds(
    volume_flow_total: float,
    n_boreholes: int,
    pipe_inner_diameter: float,
    rho: float = 1000.0,
    mu: float = 0.001,
) -> float:
    """Pipe Reynolds number in one U-tube leg for a field-total flow [m³/s]."""
    if not all(math.isfinite(v) and v > 0 for v in (pipe_inner_diameter, rho, mu)):
        raise ValueError("pipe_inner_diameter, rho and mu must be finite and positive")
    branch = calc_borehole_mass_flow(volume_flow_total * rho, n_boreholes)
    return 4.0 * branch / (math.pi * pipe_inner_diameter * mu)


def warn_if_laminar_ground_flow(
    volume_flow_total: float,
    n_boreholes: int,
    pipe_inner_diameter: float,
    *,
    rho: float = 1000.0,
    mu: float = 0.001,
    label: str = "reference",
    stacklevel: int = 2,
) -> float:
    """Return the U-tube Reynolds number; warn (``UserWarning``) when it is laminar.

    A laminar U-tube is not an error: the borehole resistance and the pipe
    loss are evaluated for it. It is reported because the fluid film then
    dominates the borehole resistance (R_b* roughly doubles) and such a loop
    is normally a sizing slip -- a flow that was not scaled with the number of
    parallel boreholes.
    """
    reynolds = calc_borehole_reynolds(volume_flow_total, n_boreholes, pipe_inner_diameter, rho, mu)
    if reynolds < LAMINAR_REYNOLDS_LIMIT:
        per_borehole = volume_flow_total / n_boreholes * 60000.0
        needed = LAMINAR_REYNOLDS_LIMIT / reynolds * volume_flow_total * 60000.0
        warnings.warn(
            f"ground loop is laminar at the {label} flow: Re = {reynolds:.0f} < {LAMINAR_REYNOLDS_LIMIT:.0f} in each "
            f"U-tube ({per_borehole:.2f} L/min per borehole, {n_boreholes} parallel borehole(s)). The borehole "
            f"resistance is evaluated for laminar flow; a turbulent loop needs at least {needed:.1f} L/min in total "
            "(ground_flow_ref_lpm) or fewer parallel boreholes.",
            UserWarning,
            stacklevel=stacklevel,
        )
    return reynolds


def warn_if_borefield_undersized(
    hp_capacity: float,
    n_boreholes: int,
    H_b: float,
    *,
    max_linear_load: float = BOREHOLE_MAX_LINEAR_LOAD,
    source_heat_fraction: float = RATED_SOURCE_HEAT_FRACTION,
    stacklevel: int = 2,
) -> float:
    """Return the rated specific borehole load [W/m]; warn when it exceeds ``max_linear_load``."""
    load = calc_rated_linear_load(hp_capacity, n_boreholes, H_b, source_heat_fraction=source_heat_fraction)
    if load > max_linear_load:
        n_1, n_2 = default_borefield_layout(hp_capacity, H_b, source_heat_fraction=source_heat_fraction)
        warnings.warn(
            f"borefield is small for the capacity: the rated source-side heat "
            f"({source_heat_fraction:g} x {hp_capacity:.0f} W) is {load:.0f} W/m over {n_boreholes} x {H_b:g} m, "
            f"above {max_linear_load:.0f} W/m. The ground-loop temperature will swing far from the ground "
            f"temperature; {n_1} x {n_2} boreholes of this depth keep it within "
            f"{BOREHOLE_DESIGN_LINEAR_LOAD:.0f} W/m (the default when N_1, N_2 and H_b are all omitted).",
            UserWarning,
            stacklevel=stacklevel,
        )
    return load
