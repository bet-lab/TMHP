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
    from .pump import calc_parallel_borefield_pressure_drop, calc_pump_power, resolve_aux_pressure_drop

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
    min_ratio, max_ratio = volume_flow_min / volume_flow_ref, volume_flow_max / volume_flow_ref
    if not math.isfinite(pump_power) or pump_power < 0:
        raise ValueError("E_pmp must be finite and nonnegative")
    calc_total_borehole_length(n_boreholes, H_b)
    if not math.isclose(pipe_inner_diameter, 2 * geometry["r_in"], rel_tol=1e-10):
        raise ValueError("pipe_inner_diameter must match 2*r_in for the same U-tube")
    calc_pump_power(0, 0, pump_efficiency)
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
        1, 1, 1, 1, m_dot_ref_rated if m_dot_ref_rated is not None else 1, *ua_fractions, *ua_exponents
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
            anchor_flows=(volume_flow_ref * rho_w / n_boreholes, volume_flow_constant * rho_w / n_boreholes),
            **geometry,
        )
    return dict(
        active=control == "optimal_power"
        or variable_UA
        or variable_Rb
        or hydraulic_pump
        or dp_aux_ref > 0
        or volume_flow_constant != volume_flow_ref,
        control=control,
        variable_UA=variable_UA,
        variable_Rb=variable_Rb,
        hydraulic_pump=hydraulic_pump or control == "optimal_power" or dp_aux_ref > 0,
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
    from .pump import calc_aux_pressure_drop, calc_parallel_borefield_pressure_drop, calc_pump_power

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
            volume, settings["volume_flow_ref"], settings["dp_aux_ref"], settings["dp_aux_exponent"]
        )
        pressure = pressure_bhe + pressure_aux
        pump = calc_pump_power(pressure, volume, settings["pump_efficiency"])
    return {
        "dV": volume,
        "m_dot": mass,
        "m_dot_borehole": branch,
        "R_b": rb,
        "E_pmp": pump,
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
    result: dict, settings: dict, loop: dict, ratio: float, UA: float, available: float, required: float
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
