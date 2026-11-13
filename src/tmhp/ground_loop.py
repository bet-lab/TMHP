"""Borefield bookkeeping; heat extraction from the ground is positive.

Heat rates are field totals [W], flow rates are field totals [kg/s], and
borehole resistance is a per-length quantity [m K/W], never a field resistance.
"""

import math
import warnings


def resolve_ground_flow_rates(
    *,
    default_ref_lpm: float,
    legacy_ref_lpm: float | None,
    min_ratio: float | None,
    max_ratio: float | None,
    ref_lpm: float | None,
    constant_lpm: float | None,
    min_lpm: float | None,
    max_lpm: float | None,
) -> dict[str, float]:
    """Resolve independent reference, constant setpoint and control limits [L/min]."""
    if legacy_ref_lpm is not None or min_ratio is not None or max_ratio is not None:
        warnings.warn(
            "dV_b_f_lpm/ground_flow_min_ratio/ground_flow_max_ratio are deprecated; "
            "use ground_flow_ref_lpm/constant_lpm/min_lpm/max_lpm. Explicit absolute inputs take precedence.",
            DeprecationWarning,
            stacklevel=3,
        )
    ref = ref_lpm if ref_lpm is not None else (legacy_ref_lpm if legacy_ref_lpm is not None else default_ref_lpm)
    constant = ref if constant_lpm is None else constant_lpm
    low = ref * (0.2 if min_ratio is None else min_ratio) if min_lpm is None else min_lpm
    high = ref * (1.2 if max_ratio is None else max_ratio) if max_lpm is None else max_lpm
    if not all(math.isfinite(v) and v > 0 for v in (ref, constant, low, high)) or low >= high:
        raise ValueError("Ground reference/setpoint/limits must be finite and positive, with min < max")
    if not low <= constant <= high:
        raise ValueError("Constant ground flow is outside the configured bounds")
    return dict(ref_lpm=ref, constant_lpm=constant, min_lpm=low, max_lpm=high)


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


def configure_ground_flow(
    *,
    control: str,
    variable_ground_flow: bool,
    variable_UA: bool,
    variable_Rb: bool,
    hydraulic_pump: bool,
    min_ratio: float | None = None,
    max_ratio: float | None = None,
    volume_flow_rated: float | None = None,
    volume_flow_ref: float | None = None,
    volume_flow_constant: float | None = None,
    volume_flow_min: float | None = None,
    volume_flow_max: float | None = None,
    n_boreholes: int,
    H_b: float,
    R_b: float,
    R_b_supplied: bool,
    pump_power: float,
    pump_efficiency: float,
    pump_map: dict | None = None,
    pipe_inner_diameter: float,
    pipe_roughness: float,
    dp_common: float | None = None,
    dp_aux_ref: float = 0.0,
    dp_aux_exponent: float = 2.0,
    m_dot_ref_rated: float | None,
    ua_fractions: tuple[float, float, float],
    ua_exponents: tuple[float, float],
    boundary_condition: str,
    geometry: dict,
) -> dict:
    """Validate/store shared ground-loop settings and precompute variable Rb*.

    The new coupled solver is opt-in. ``variable_ground_flow`` is a convenience
    alias for optimal_power; hydraulic power is mandatory in that control mode.
    Fixed Rb supplied by a user cannot simultaneously specify variable geometry.
    """
    from .borehole import precompute_borehole_resistance_from_flow
    from .constants import c_w, rho_w
    from .heat_exchanger import calc_UA_two_stream_scaled
    from .pump import (
        calc_parallel_borefield_pressure_drop,
        calc_pump_power,
        resolve_aux_pressure_drop,
    )

    if control not in ("constant", "optimal_power"):
        raise ValueError("ground_flow_control must be 'constant' or 'optimal_power'")
    if variable_ground_flow:
        control = "optimal_power"
    if volume_flow_ref is None:
        warnings.warn(
            "volume_flow_rated and ratio limits are deprecated; use independent absolute flow inputs.",
            DeprecationWarning,
            stacklevel=2,
        )
        volume_flow_ref = volume_flow_rated
    if volume_flow_ref is None or not math.isfinite(volume_flow_ref) or volume_flow_ref <= 0:
        raise ValueError("Ground reference flow must be finite and positive")
    volume_flow_constant = volume_flow_ref if volume_flow_constant is None else volume_flow_constant
    volume_flow_min = (
        volume_flow_ref * (0.2 if min_ratio is None else min_ratio) if volume_flow_min is None else volume_flow_min
    )
    volume_flow_max = (
        volume_flow_ref * (1.2 if max_ratio is None else max_ratio) if volume_flow_max is None else volume_flow_max
    )
    if (
        not all(math.isfinite(v) and v > 0 for v in (volume_flow_constant, volume_flow_min, volume_flow_max, R_b))
        or volume_flow_min >= volume_flow_max
    ):
        raise ValueError("Require positive finite setpoint/R_b and 0 < ground_flow_min < ground_flow_max")
    if not volume_flow_min <= volume_flow_constant <= volume_flow_max:
        raise ValueError("Constant ground flow is outside the configured bounds")
    min_ratio, max_ratio = (
        volume_flow_min / volume_flow_ref,
        volume_flow_max / volume_flow_ref,
    )
    if not math.isfinite(pump_power) or pump_power < 0:
        raise ValueError("E_pmp must be finite and nonnegative")
    calc_total_borehole_length(n_boreholes, H_b)
    if not math.isclose(pipe_inner_diameter, 2 * geometry["r_in"], rel_tol=1e-10):
        raise ValueError("pipe_inner_diameter must match 2*r_in for the same U-tube")
    calc_pump_power(0, 0, pump_efficiency)
    from .pump import PumpPerformanceMap

    performance_map = PumpPerformanceMap(pump_map) if pump_map is not None else None
    dp_aux_ref = resolve_aux_pressure_drop(dp_common, dp_aux_ref, dp_aux_exponent)
    calc_parallel_borefield_pressure_drop(
        0,
        n_boreholes,
        H_b,
        pipe_inner_diameter,
        rho_w,
        geometry["mu_f"],
        pipe_roughness,
        volume_flow_ref=volume_flow_ref,
        dp_aux_ref=dp_aux_ref,
        dp_aux_exponent=dp_aux_exponent,
    )
    if variable_UA and m_dot_ref_rated is None:
        raise ValueError(
            "variable_ground_hx_UA requires m_dot_ref_rated [kg/s]; it could not be derived at the "
            "rating condition for the given rps_rated (see the RuntimeWarning) -- pass it explicitly"
        )
    calc_UA_two_stream_scaled(
        1,
        1,
        1,
        1,
        m_dot_ref_rated if m_dot_ref_rated is not None else 1,
        *ua_fractions,
        *ua_exponents,
    )
    if variable_Rb and R_b_supplied:
        raise ValueError("variable_Rb requires borehole geometry; omit the fixed R_b override")
    interp = None
    if variable_Rb:
        interp = precompute_borehole_resistance_from_flow(
            min(volume_flow_min, volume_flow_ref, volume_flow_constant) * rho_w / n_boreholes,
            max(volume_flow_max, volume_flow_ref, volume_flow_constant) * rho_w / n_boreholes,
            H_b,
            c_w,
            boundary_condition,
            anchor_flows=(
                volume_flow_ref * rho_w / n_boreholes,
                volume_flow_constant * rho_w / n_boreholes,
            ),
            **geometry,
        )
    return dict(
        active=control == "optimal_power"
        or variable_UA
        or variable_Rb
        or hydraulic_pump
        or performance_map is not None
        or dp_aux_ref > 0
        or volume_flow_constant != volume_flow_ref,
        control=control,
        variable_UA=variable_UA,
        variable_Rb=variable_Rb,
        hydraulic_pump=hydraulic_pump or performance_map is not None or control == "optimal_power" or dp_aux_ref > 0,
        min_ratio=min_ratio,
        max_ratio=max_ratio,
        volume_flow_ref=volume_flow_ref,
        volume_flow_constant=volume_flow_constant,
        volume_flow_min=volume_flow_min,
        volume_flow_max=volume_flow_max,
        volume_flow_rated=volume_flow_ref,  # compatibility alias; never an upper limit
        n_boreholes=n_boreholes,
        H_b=H_b,
        R_b=R_b,
        pump_power=pump_power,
        pump_efficiency=pump_efficiency,
        pump_map=performance_map,
        pipe_inner_diameter=pipe_inner_diameter,
        pipe_roughness=pipe_roughness,
        dp_common=dp_aux_ref,  # compatibility alias for reference loss
        dp_aux_ref=dp_aux_ref,
        dp_aux_exponent=dp_aux_exponent,
        m_dot_ref_rated=m_dot_ref_rated,
        ua_fractions=ua_fractions,
        ua_exponents=ua_exponents,
        rb_interp=interp,
    )


def ground_flow_state(settings: dict, ratio: float | None = None, *, volume_flow: float | None = None) -> dict:
    """Evaluate pump, branch flow and Rb* at the same field flow ratio."""
    from .constants import mu_w, rho_w
    from .pump import (
        calc_aux_pressure_drop,
        calc_parallel_borefield_pressure_drop,
        calc_pump_power,
    )

    if ratio is not None and volume_flow is not None:
        raise ValueError("Supply ratio or actual volume_flow, not both")
    if volume_flow is None:
        reference = settings.get("volume_flow_ref", settings.get("volume_flow_rated"))
        if reference is None:
            raise ValueError("Ground reference flow is required")
        volume_flow = reference * (1.0 if ratio is None else ratio)
    if not math.isfinite(volume_flow) or volume_flow <= 0:
        raise ValueError("Ground flow ratio must be positive and finite")
    volume = volume_flow
    mass = volume * rho_w
    branch = calc_borehole_mass_flow(mass, settings["n_boreholes"])
    rb = settings["rb_interp"](branch) if settings["variable_Rb"] else settings["R_b"]
    pressure = pressure_bhe = pressure_aux = math.nan
    pump = settings["pump_power"]
    pump_speed = math.nan
    pump_efficiency = settings.get("pump_efficiency", math.nan)
    if settings["hydraulic_pump"]:
        pressure_bhe = calc_parallel_borefield_pressure_drop(
            mass,
            settings["n_boreholes"],
            settings["H_b"],
            settings["pipe_inner_diameter"],
            rho_w,
            mu_w,
            settings["pipe_roughness"],
        )
        pressure_aux = calc_aux_pressure_drop(
            volume,
            settings["volume_flow_ref"],
            settings["dp_aux_ref"],
            settings["dp_aux_exponent"],
        )
        pressure = pressure_bhe + pressure_aux
        if settings.get("pump_map") is None:
            pump = calc_pump_power(pressure, volume, settings["pump_efficiency"])
        else:
            point = settings["pump_map"].operating_point(pressure, volume, rho_w)
            pump, pump_speed, pump_efficiency = (
                point["power_W"],
                point["speed_ratio"],
                point["efficiency"],
            )
    return {
        "dV": volume,
        "m_dot": mass,
        "m_dot_borehole": branch,
        "R_b": rb,
        "E_pmp": pump,
        "pump_speed_ratio": pump_speed,
        "pump_efficiency": pump_efficiency,
        "dp": pressure,
        "dp_bhe": pressure_bhe,
        "dp_aux": pressure_aux,
    }


def ground_hx_UA(settings: dict, UA_rated: float, ratio: float, m_dot_ref: float) -> float:
    """Evaluate the two-stream UA at the current water AND refrigerant flow."""
    from .heat_exchanger import calc_UA_two_stream_scaled

    if not settings["variable_UA"] or m_dot_ref == 0:
        return UA_rated
    return calc_UA_two_stream_scaled(
        UA_rated,
        ratio,
        1.0,
        m_dot_ref,
        settings["m_dot_ref_rated"],
        *settings["ua_fractions"],
        *settings["ua_exponents"],
    )


def ground_result_diagnostics(
    result: dict,
    settings: dict,
    loop: dict,
    ratio: float,
    UA: float,
    available: float,
    required: float,
) -> None:
    """Attach auditable component values; feasibility requires duty equality."""
    active = result.get("hp_is_on", False)
    if settings["active"] and not active:
        result["dV_bhe_f [m3/s]"] = 0.0
    margin = available - required
    result.update(
        {
            "ground_flow_control": settings["control"],
            "ground_flow_ratio": ratio if active else 0.0,
            "ground_flow_ref_ratio": ratio if active else 0.0,
            "ground_flow [m3/s]": loop["dV"] if active else 0.0,
            "ground_flow_at_min": bool(
                active and abs(loop["dV"] - settings["volume_flow_min"]) < 2e-3 * settings["volume_flow_ref"]
            ),
            "ground_flow_at_max": bool(
                active and abs(loop["dV"] - settings["volume_flow_max"]) < 2e-3 * settings["volume_flow_ref"]
            ),
            "m_dot_borehole [kg/s]": loop["m_dot_borehole"] if active else 0.0,
            "R_b_eff [mK/W]": loop["R_b"],
            "UA_ground [W/K]": UA,
            "pump_speed_ratio": loop.get("pump_speed_ratio", math.nan) if active else 0.0,
            "pump_efficiency": loop.get("pump_efficiency", math.nan) if active else math.nan,
            "ground_pressure_drop [Pa]": loop["dp"] if active else 0.0,
            "ground_pressure_drop_bhe [Pa]": loop["dp_bhe"] if active else 0.0,
            "ground_pressure_drop_aux [Pa]": loop["dp_aux"] if active else 0.0,
            "ground_pressure_drop_total [Pa]": loop["dp"] if active else 0.0,
            "E_cmp_plus_pmp [W]": result["E_cmp [W]"] + result["E_pmp [W]"],
            "Q_HX_available [W]": available,
            "Q_ref_required [W]": required,
            "hx_capacity_margin [W]": margin,
            "hx_capacity_ratio": available / required if required > 0 else math.nan,
            "hx_feasible": abs(margin) <= max(0.01, 1e-5 * required),
            "approach_solver_success": False,
            "approach_at_bound": False,
            "flow_optimizer_success": False,
            "flow_optimizer_nfev": 0,
            "flow_bound_active": False,
        }
    )


# ---------------------------------------------------------------------------
# Capacity-proportional ground-side defaults (#96)
#
# A model that is given only its capacity sizes the ground side from the heat
# the ground loop carries at the rating point. The rules below are shared by
# the ground- and water-source families; a value the user supplies always
# replaces the default it would have produced.
# ---------------------------------------------------------------------------

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
