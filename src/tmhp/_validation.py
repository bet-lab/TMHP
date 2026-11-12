"""Input validation shared by every public entry point of the model families.

Two kinds of problem are kept apart on purpose (see also
``docs/source/concepts/failure-reason-semantics.rst``):

* **An input outside its physical range is a caller error.** The model cannot
  give it a meaning, so it raises :class:`ValueError` *before* any numerics run.
  The message always has the form ``"<name> must be <expectation>, got <value>"``.
* **An operating point that cannot be solved is a result.** Valid inputs for
  which no cycle closes are reported through ``failure_reason``.

Policy -- block what is physically impossible or what the model cannot give a
meaning, do not block what is merely rare:

* Not finite (NaN, +-inf), not a number, or a sign that no physical quantity can
  have (negative capacity, UA, flow, volume, area; fan, pump, PV and storage
  efficiency outside (0, 1]) are errors. Compressor efficiencies are only
  required to be > 0: a scalar above 1 is clipped to 1 by
  :mod:`tmhp.compressor_efficiency` on purpose, so that bound lives there.
* Temperatures are given in degC. They are errors when outside a range that no
  heat-pump scenario can reach, and the range is chosen so that **any ordinary
  kelvin value falls outside it**, which is the commonest unit slip:

  - environment (outdoor air, room, surroundings, ground/source fluid):
    -90 .. 100 degC. -89.2 degC is the coldest recorded air temperature and a
    source liquid above 100 degC is not liquid at the model's pressure; a kelvin
    value for any climate (>= 223 K) is above 100.
  - water in the tank, mains and mixed supply: 0 .. 100 degC (liquid water at
    atmospheric pressure; the tank model has no pressurisation).
  - kelvin-valued parameters (``T_inv_K`` ...): 200 .. 500 K, so a degC number
    typed into a kelvin slot is rejected too.

* A pair that is valid on its own but suspicious together is only a
  :class:`UserWarning` (``T_sup_w >= T_mix_w_out``: the mains water is already at
  or above the delivery set point, so the mixing valve has nothing to mix;
  ``T_mix_w_out`` above the tank's upper set point). The model still computes a
  consistent answer for them, so they must not be blocked. Warnings are only
  emitted from constructors: the steady/dynamic entry points run per time step
  and a per-step warning would flood the log.
* Not validated here: the refrigerant name (CoolProp raises), compressor speed
  bounds and fan-flow limits (the reference-state code already checks them with
  specific messages), ground-flow ratios (``resolve_ground_flow_rates``), and
  callables passed as efficiencies (checked where they are evaluated).
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

# --------------------------------------------------------------------------
# Physical ranges (see module docstring for the reasoning)
# --------------------------------------------------------------------------
T_ENV_MIN_C, T_ENV_MAX_C = -90.0, 100.0
T_WATER_MIN_C, T_WATER_MAX_C = 0.0, 100.0
T_KELVIN_MIN, T_KELVIN_MAX = 200.0, 500.0
#: A degC temperature above this is more likely a kelvin value than a real one.
_KELVIN_HINT_C = 150.0


def _fmt(value: Any) -> str:
    """Compact, stable representation of the offending value for messages."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, np.ndarray):
        return f"array(shape={value.shape})"
    return repr(value)


def _fail(name: str, expectation: str, value: Any, *, extra: str = "") -> ValueError:
    return ValueError(f"{name} must be {expectation}, got {_fmt(value)}{extra}")


def _number(name: str, value: Any, expectation: str) -> float:
    """Convert to float or raise the standard message (rejects strings/None/arrays)."""
    if isinstance(value, (str, bytes)) or value is None:
        raise _fail(name, expectation, value)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise _fail(name, expectation, value) from None


# --------------------------------------------------------------------------
# Scalar checks. Each returns the validated float.
# --------------------------------------------------------------------------
def check_finite(name: str, value: Any) -> float:
    x = _number(name, value, "a finite number")
    if not math.isfinite(x):
        raise _fail(name, "a finite number", value)
    return x


def check_positive(name: str, value: Any) -> float:
    x = _number(name, value, "a finite number > 0")
    if not (math.isfinite(x) and x > 0.0):
        raise _fail(name, "a finite number > 0", value)
    return x


def check_nonnegative(name: str, value: Any) -> float:
    x = _number(name, value, "a finite number >= 0")
    if not (math.isfinite(x) and x >= 0.0):
        raise _fail(name, "a finite number >= 0", value)
    return x


def check_load(name: str, value: Any) -> float:
    """Heat request that is a magnitude (boiler ``Q_ref_tank``): finite and >= 0."""
    x = _number(name, value, "a finite number >= 0 (heat delivered to the tank)")
    if not (math.isfinite(x) and x >= 0.0):
        raise _fail(name, "a finite number >= 0 (heat delivered to the tank)", value)
    return x


def check_range(
    name: str,
    value: Any,
    lo: float,
    hi: float,
    *,
    lo_open: bool = False,
    hi_open: bool = False,
    unit: str = "",
) -> float:
    lb = "(" if lo_open else "["
    rb = ")" if hi_open else "]"
    expectation = f"a finite number in {lb}{lo:g}, {hi:g}{rb}{unit}"
    x = _number(name, value, expectation)
    below = x <= lo if lo_open else x < lo
    above = x >= hi if hi_open else x > hi
    if not math.isfinite(x) or below or above:
        raise _fail(name, expectation, value)
    return x


def check_fraction(name: str, value: Any) -> float:
    return check_range(name, value, 0.0, 1.0)


def check_efficiency(name: str, value: Any) -> float:
    return check_range(name, value, 0.0, 1.0, lo_open=True)


def check_integer_positive(name: str, value: Any) -> int:
    x = _number(name, value, "an integer >= 1")
    if not (math.isfinite(x) and x >= 1.0 and x == int(x)):
        raise _fail(name, "an integer >= 1", value)
    return int(x)


def check_temperature(name: str, value: Any, kind: str = "env") -> float:
    """Temperature in degC within the physical range of ``kind`` ("env" | "water")."""
    lo, hi = (T_ENV_MIN_C, T_ENV_MAX_C) if kind == "env" else (T_WATER_MIN_C, T_WATER_MAX_C)
    expectation = f"a finite temperature in degC within [{lo:g}, {hi:g}]"
    x = _number(name, value, expectation)
    if not (math.isfinite(x) and lo <= x <= hi):
        hint = " (a kelvin value?)" if math.isfinite(x) and x > _KELVIN_HINT_C else ""
        raise _fail(name, expectation, value, extra=hint)
    return x


def check_kelvin(name: str, value: Any) -> float:
    expectation = f"a finite temperature in K within [{T_KELVIN_MIN:g}, {T_KELVIN_MAX:g}]"
    x = _number(name, value, expectation)
    if not (math.isfinite(x) and T_KELVIN_MIN <= x <= T_KELVIN_MAX):
        hint = " (a degC value?)" if math.isfinite(x) and x < T_KELVIN_MIN else ""
        raise _fail(name, expectation, value, extra=hint)
    return x


def check_less(low_name: str, low: Any, high_name: str, high: Any) -> None:
    """Strict ordering ``low < high`` of two already-validated numbers."""
    if float(low) >= float(high):
        raise ValueError(f"{low_name} must be < {high_name}, got {low_name}={_fmt(low)}, {high_name}={_fmt(high)}")


def check_time_grid(simulation_period_sec: Any, dt_s: Any) -> int:
    """Validate the time grid and return the number of steps ``period / dt``.

    ``np.arange(0, period, dt)`` silently makes a short last step when ``dt`` does
    not divide the period; the models step with the full ``dt`` every time, so
    the period would be mis-represented. Require an integer multiple.
    """
    period = check_positive("simulation_period_sec", simulation_period_sec)
    dt = check_positive("dt_s", dt_s)
    ratio = period / dt
    n = round(ratio)
    if n < 1 or abs(ratio - n) > 1e-9 * max(1.0, n):
        raise ValueError(
            f"simulation_period_sec must be an integer multiple of dt_s, "
            f"got simulation_period_sec={_fmt(simulation_period_sec)}, dt_s={_fmt(dt_s)}"
        )
    return int(n)


# --------------------------------------------------------------------------
# Rule registry: a rule name maps to a scalar check
# --------------------------------------------------------------------------
_RULES: dict[str, Callable[[str, Any], Any]] = {
    "finite": check_finite,
    "pos": check_positive,
    "nonneg": check_nonnegative,
    "load": check_load,
    "frac": check_fraction,
    "eff": check_efficiency,
    "int_pos": check_integer_positive,
    "ge1": lambda n, v: check_range(n, v, 1.0, math.inf),
    "hour": lambda n, v: check_range(n, v, 0.0, 24.0),
    "tilt": lambda n, v: check_range(n, v, 0.0, 180.0, unit=" degrees"),
    "T_env": lambda n, v: check_temperature(n, v, "env"),
    "T_water": lambda n, v: check_temperature(n, v, "water"),
    "T_K": check_kelvin,
}


def _apply(rules: Mapping[str, str], args: Mapping[str, Any]) -> None:
    for name, rule in rules.items():
        value = args.get(name)
        if value is None or callable(value):  # omitted optional / user callable
            continue
        _RULES[rule](name, value)


# --------------------------------------------------------------------------
# Constructor specifications (name -> rule). Deprecated aliases are included so
# that an old keyword is checked like its replacement.
# --------------------------------------------------------------------------
_TANK_GEOMETRY = {
    "r0": "pos",
    "H": "pos",
    "x_shell": "nonneg",
    "x_ins": "nonneg",
    "k_shell": "pos",
    "k_ins": "pos",
    "h_o": "pos",
}
_TANK_CONTROL = {
    "T_tank_w_upper_bound": "T_water",
    "T_tank_w_lower_bound": "T_water",
    "T_mix_w_out": "T_water",
    "T_sup_w": "T_water",
    "T_tank_w_in": "T_water",
    "T_sur": "T_env",
    "T0": "T_env",
    "hp_capacity": "pos",
    "dV_mix_w_out_max": "pos",
    "tank_level_lower_bound": "frac",
    "tank_level_upper_bound": "frac",
    "dV_tank_w_in_refill": "pos",
}
_CYCLE = {
    "V_cmp_ref": "pos",
    "V_disp_cmp": "pos",
    # Compressor efficiencies are only required to be positive here: a scalar
    # above 1 is deliberately clipped to 1 by tmhp.compressor_efficiency
    # (the fitted volumetric efficiency reaches 1.02 inside its own data), so
    # the (0, 1] bound is enforced there, not at construction.
    "eta_cmp_isen": "pos",
    "eta_cmp_vol": "pos",
    "eta_cmp": "pos",
    "eta_cmp_mech": "pos",
    "eta_cmp_electro_mech": "pos",
    "eta_v": "pos",
    "eta_em": "pos",
    "dT_superheat": "nonneg",
    "dT_subcool": "nonneg",
    "dT_hx_min": "nonneg",
    "PR_cycle_min": "ge1",
    "PR_cycle_max": "pos",
    "rps_rated": "pos",
    "m_dot_ref_rated": "pos",
}
_BOREHOLE = {
    "N_1": "int_pos",
    "N_2": "int_pos",
    "B": "pos",
    "D_b": "nonneg",
    "H_b": "pos",
    "r_b": "pos",
    "R_b": "pos",
    "k_g": "pos",
    "k_p": "pos",
    "r_out": "pos",
    "r_in": "pos",
    "D_s": "pos",
    "dV_b_f_lpm": "pos",
    "k_s": "pos",
    "c_s": "pos",
    "rho_s": "pos",
    "E_pmp": "nonneg",
    "Ts": "T_env",
    "t_max_s": "pos",
    "dt_s": "pos",
}
_GROUND_HYDRAULICS = {
    "pump_efficiency": "eff",
    "pipe_inner_diameter": "pos",
    "pipe_roughness": "nonneg",
    "dp_common": "nonneg",
    "dp_aux_ref": "nonneg",
    "dp_aux_exponent": "pos",
    "ground_hx_fluid_fraction": "frac",
    "ground_hx_refrigerant_fraction": "frac",
    "ground_hx_constant_fraction": "frac",
    "ground_hx_fluid_exponent": "pos",
    "ground_hx_refrigerant_exponent": "pos",
}
_AIR_FAN = {
    "dV_fan_a_rated": "pos",
    "dV_fan_a_ref": "pos",
    "dV_ou_fan_a_design": "pos",
    "dP_fan_rated": "pos",
    "dP_ou_fan_design": "pos",
    "A_cross": "pos",
    "A_cross_ou": "pos",
    "eta_fan_rated": "eff",
    "eta_ou_fan_design": "eff",
}
_INDOOR_FAN = {
    "dV_iu_fan_a_rated": "pos",
    "dV_iu_fan_a_ref": "pos",
    "dV_iu_fan_a_design": "pos",
    "dP_iu_fan_rated": "pos",
    "dP_iu_fan_design": "pos",
    "A_cross_iu": "pos",
    "eta_iu_fan_rated": "eff",
    "eta_iu_fan_design": "eff",
}
_OUTDOOR_FAN_SPACE = {
    "dV_ou_fan_a_rated": "pos",
    "dV_ou_fan_a_ref": "pos",
    "dP_ou_fan_rated": "pos",
    "dP_ou_fan_design": "pos",
    "A_cross_ou": "pos",
    "eta_ou_fan_rated": "eff",
    "eta_ou_fan_design": "eff",
}

CTOR_SPECS: dict[str, dict[str, str]] = {
    "ASHPB": {
        **_CYCLE,
        **_TANK_GEOMETRY,
        **_TANK_CONTROL,
        **_AIR_FAN,
        "UA_tank_hx": "pos",
        "UA_ou_rated": "pos",
        "UA_tank": "pos",
        "UA_cond_design": "pos",
        "UA_evap_design": "pos",
        "n_ou": "pos",
    },
    "GSHPB": {
        **_CYCLE,
        **_TANK_GEOMETRY,
        **_TANK_CONTROL,
        **_BOREHOLE,
        **_GROUND_HYDRAULICS,
        "UA_tank_hx": "pos",
        "UA_ground": "pos",
        "UA_tank": "pos",
        "UA_cond_design": "pos",
        "UA_evap_design": "pos",
        "n_tank_nodes": "int_pos",
    },
    "WSHPB": {
        **_CYCLE,
        **_TANK_GEOMETRY,
        **_TANK_CONTROL,
        **_BOREHOLE,
        "UA_tank_hx": "pos",
        "UA_water": "pos",
        "UA_tank": "pos",
        "UA_cond_design": "pos",
        "UA_evap_design": "pos",
        "v_river": "pos",
    },
    "ASHP": {
        **_CYCLE,
        **_OUTDOOR_FAN_SPACE,
        **_INDOOR_FAN,
        "UA_ou_rated": "pos",
        "UA_iu_rated": "pos",
        "UA_cond_rated": "pos",
        "UA_evap_rated": "pos",
        "UA_cond_design": "pos",
        "UA_evap_design": "pos",
        "n_ou": "pos",
        "n_iu": "pos",
        "n_cond": "pos",
        "n_evap": "pos",
        "hp_capacity": "pos",
        "T_a_room": "T_env",
    },
    "GSHP": {
        **_CYCLE,
        **_BOREHOLE,
        **_GROUND_HYDRAULICS,
        **_INDOOR_FAN,
        "UA_cond": "pos",
        "UA_evap": "pos",
        "UA_cond_design": "pos",
        "UA_evap_design": "pos",
        "UA_ground_rated": "pos",
        "UA_iu_rated": "pos",
        "ground_hx_ua_per_capacity": "pos",
        "indoor_approach_min_K": "nonneg",
        "indoor_approach_max_K": "pos",
        "hp_capacity": "pos",
        "T_a_room": "T_env",
    },
    "GSHP_EMPIRICAL": {
        "H_b": "pos",
        "D_b": "nonneg",
        "r_b": "pos",
        "k_p": "pos",
        "k_grout": "pos",
        "r_out": "pos",
        "r_in": "pos",
        "D_s": "pos",
        "k_g": "pos",
        "c_g": "pos",
        "rho_g": "pos",
        "T_g": "T_env",
        "dV_f": "pos",
        "Q_rated_cooling": "pos",
        "Q_rated_heating": "pos",
        "E_pmp": "nonneg",
        "dP_iu_fan_design": "pos",
        "eta_iu_fan_design": "eff",
        "dt_hours": "int_pos",
        "sim_hours": "int_pos",
    },
    "STC": {
        "A_stc": "pos",
        "stc_tilt": "tilt",
        "stc_azimuth": "finite",
        "A_stc_pipe": "pos",
        "alpha_stc": "frac",
        "h_o_stc": "pos",
        "h_r_stc": "nonneg",
        "k_ins_stc": "pos",
        "x_air_stc": "nonneg",
        "x_ins_stc": "nonneg",
        "preheat_start_hour": "hour",
        "preheat_end_hour": "hour",
        "dV_stc_w": "pos",
        "E_stc_pump": "nonneg",
    },
    "PV": {
        "A_pv": "pos",
        "alp_pv": "frac",
        "pv_tilt": "tilt",
        "pv_azimuth": "finite",
        "h_o": "pos",
        "eta_pv": "eff",
        "beta_pv": "nonneg",
        "T_ref_pv_K": "T_K",
        "eta_ctrl": "eff",
        "T_ctrl_K": "T_K",
    },
    "ESS": {
        "C_ess_max": "pos",
        "SOC_init": "frac",
        "SOC_min": "frac",
        "SOC_max": "frac",
        "eta_ess_chg": "eff",
        "eta_ess_dis": "eff",
        "T_ess_K": "T_K",
    },
    "UV": {
        "lamp_watts": "nonneg",
        "exposure_sec": "nonneg",
        "num_switching": "int_pos",
        "period_sec": "pos",
    },
    "PV_ESS": {"eta_inv": "eff", "T_inv_K": "T_K"},
    "TANK": {
        "n_nodes": "int_pos",
        "volume": "pos",
        "height": "pos",
        "k_eff": "pos",
        "ua": "nonneg",
        "rho": "pos",
        "cp": "pos",
    },
}

#: Strict orderings checked when both names are present in a constructor.
_ORDERINGS: tuple[tuple[str, str], ...] = (
    ("T_tank_w_lower_bound", "T_tank_w_upper_bound"),
    ("tank_level_lower_bound", "tank_level_upper_bound"),
    ("PR_cycle_min", "PR_cycle_max"),
    ("r_in", "r_out"),
    ("r_out", "r_b"),
    ("indoor_approach_min_K", "indoor_approach_max_K"),
    ("SOC_min", "SOC_max"),
)


def _check_hp_on_schedule(value: Any) -> None:
    """Operating windows ``[(start_h, end_h), ...]`` with ``0 <= start <= end <= 24``.

    The window test is ``start <= hour < end``. ``start == end`` is an empty
    window (the way to say "never on") and stays valid; ``start > end`` -- in
    particular a window that wraps midnight -- would be silently never on, so it
    is rejected instead of quietly disabling the heat pump.
    """
    try:
        windows = list(value)
    except TypeError:
        raise _fail("hp_on_schedule", "a list of (start_hour, end_hour) windows", value) from None
    for i, window in enumerate(windows):
        try:
            start, end = window
        except (TypeError, ValueError):
            raise _fail(f"hp_on_schedule[{i}]", "a (start_hour, end_hour) pair", window) from None
        start = check_range(f"hp_on_schedule[{i}] start", start, 0.0, 24.0)
        end = check_range(f"hp_on_schedule[{i}] end", end, 0.0, 24.0)
        if start > end:
            raise _fail(
                f"hp_on_schedule[{i}]",
                "a window with start_hour <= end_hour (a window crossing midnight must be split in two)",
                window,
            )


def _check_approach_bounds(value: Any) -> None:
    try:
        lo, hi = value
    except (TypeError, ValueError):
        raise _fail("dT_approach_bounds", "a (min, max) pair of temperature differences", value) from None
    lo = check_positive("dT_approach_bounds[0]", lo)
    hi = check_positive("dT_approach_bounds[1]", hi)
    if lo >= hi:
        raise _fail("dT_approach_bounds", "a pair with min < max", value)


def _warn_suspicious_pair(args: Mapping[str, Any]) -> None:
    supply = args.get("T_sup_w", args.get("T_tank_w_in"))
    mix = args.get("T_mix_w_out")
    upper = args.get("T_tank_w_upper_bound")
    if supply is not None and mix is not None and float(supply) >= float(mix):
        warnings.warn(
            f"mains water temperature ({_fmt(supply)} degC) is not below the delivery set point "
            f"T_mix_w_out={_fmt(mix)} degC: the mixing valve has nothing to mix",
            UserWarning,
            stacklevel=4,
        )
    if mix is not None and upper is not None and float(mix) > float(upper):
        warnings.warn(
            f"delivery set point T_mix_w_out={_fmt(mix)} degC is above the tank upper set point "
            f"T_tank_w_upper_bound={_fmt(upper)} degC: the tank can never reach it",
            UserWarning,
            stacklevel=4,
        )


def validate_ctor(scope: str, args: Mapping[str, Any]) -> None:
    """Validate the constructor arguments of ``scope`` (a key of :data:`CTOR_SPECS`).

    Call as the **first** statement of ``__init__`` with ``locals()`` (dataclasses:
    ``vars(self)``) so that nothing downstream sees an invalid value.
    """
    _apply(CTOR_SPECS[scope], args)
    for low, high in _ORDERINGS:
        if args.get(low) is not None and args.get(high) is not None:
            check_less(low, args[low], high, args[high])
    if args.get("hp_on_schedule") is not None:
        _check_hp_on_schedule(args["hp_on_schedule"])
    if args.get("dT_approach_bounds") is not None:
        _check_approach_bounds(args["dT_approach_bounds"])
    if scope in ("ASHPB", "GSHPB", "WSHPB"):
        _warn_suspicious_pair(args)


# --------------------------------------------------------------------------
# Steady-state entry points
# --------------------------------------------------------------------------
_STEADY_RULES = {
    "T_tank_w": "T_water",
    "T0": "T_env",
    "T_source": "T_env",
    "T_a_room": "T_env",
    "T_bhe_wall": "T_env",
    # Boiler heat request is a magnitude (>= 0). Space conditioning Q_r_iu carries
    # the mode in its sign (<0 heating, >0 cooling, 0 off), so it only has to be finite.
    "Q_ref_tank": "load",
    "Q_r_iu": "finite",
    "m_dot_w": "nonneg",
    "ground_flow_ratio": "pos",
    "ground_flow_lpm": "pos",
}


def validate_steady(args: Mapping[str, Any]) -> None:
    """Validate ``analyze_steady`` arguments (pass ``locals()`` at the top of the method)."""
    _apply(_STEADY_RULES, args)


# --------------------------------------------------------------------------
# Dynamic entry points
# --------------------------------------------------------------------------
_DYNAMIC_SCALARS = {
    "T_tank_w_init_C": "T_water",
    "tank_level_init": "frac",
}
#: schedule name -> rule applied to every element.
_SCHEDULE_RULES = {
    "dhw_usage_schedule": "nonneg",
    "T0_schedule": "T_env",
    "T_sup_w_schedule": "T_water",
    "T_sur_schedule": "T_env",
    "T_source_w_schedule": "T_env",
    "T_a_room_schedule": "T_env",
    "I_DN_schedule": "nonneg",
    "I_dH_schedule": "nonneg",
    "Q_r_iu_schedule": "finite",
}


def check_schedule(name: str, values: Any, n_steps: int, rule: str) -> np.ndarray:
    """Schedule as a 1-D float array of exactly ``n_steps`` finite, in-range values."""
    try:
        arr = np.asarray(values, dtype=float)
    except (TypeError, ValueError):
        raise _fail(name, "a numeric 1-D array", values) from None
    if arr.ndim != 1:
        raise _fail(name, "a 1-D array", arr)
    if len(arr) != n_steps:
        raise ValueError(
            f"{name} must have length {n_steps} (= simulation_period_sec / dt_s), got length {len(arr)}",
        )
    bad_finite = ~np.isfinite(arr)
    if bad_finite.any():
        i = int(np.argmax(bad_finite))
        raise ValueError(f"{name} must be finite at every step, got {_fmt(float(arr[i]))} at index {i}")
    if rule == "nonneg":
        bad = arr < 0.0
        if bad.any():
            i = int(np.argmax(bad))
            raise ValueError(f"{name} must be >= 0 at every step, got {_fmt(float(arr[i]))} at index {i}")
    elif rule in ("T_env", "T_water"):
        lo, hi = (T_ENV_MIN_C, T_ENV_MAX_C) if rule == "T_env" else (T_WATER_MIN_C, T_WATER_MAX_C)
        bad = (arr < lo) | (arr > hi)
        if bad.any():
            i = int(np.argmax(bad))
            hint = " (a kelvin value?)" if arr[i] > _KELVIN_HINT_C else ""
            raise ValueError(
                f"{name} must be a temperature in degC within [{lo:g}, {hi:g}] at every step, "
                f"got {_fmt(float(arr[i]))} at index {i}{hint}",
            )
    return arr


def validate_dynamic(args: Mapping[str, Any]) -> None:
    """Validate ``analyze_dynamic`` arguments (pass ``locals()`` at the top of the method).

    Checks the time grid (``dt_s > 0``, period an integer multiple of it), the
    initial state, and every schedule present: length equal to the step count,
    finite, and in its physical range (flows and irradiance >= 0, temperatures in
    degC within range).
    """
    n_steps = check_time_grid(args["simulation_period_sec"], args["dt_s"])
    _apply(_DYNAMIC_SCALARS, args)
    for name, rule in _SCHEDULE_RULES.items():
        values = args.get(name)
        if values is not None:
            check_schedule(name, values, n_steps, rule)


def validate_initial_state(args: Mapping[str, Any]) -> None:
    """Validate ``make_initial_state`` arguments (pass ``locals()``)."""
    _apply(_DYNAMIC_SCALARS, args)


_STEP_RULES = {
    "T0": "T_env",
    "T_sup_w": "T_water",
    "T_sur": "T_env",
    "dV_mix_w_out": "nonneg",
    "I_DN": "nonneg",
    "I_dH": "nonneg",
}


def validate_step(inputs: Mapping[str, Any], dt_s: Any) -> None:
    """Validate the per-step drivers of ``step(state, inputs, dt_s)``."""
    check_positive("dt_s", dt_s)
    _apply(_STEP_RULES, inputs)


_TANK_STEP_RULES = {
    "dt": "pos",
    "charge_flow": "nonneg",
    "draw_flow": "nonneg",
    "T_charge": "T_water",
    "T_makeup": "T_water",
    "T_amb": "T_env",
}


def validate_tank_step(args: Mapping[str, Any]) -> None:
    """Validate the arguments of a stratified/hybrid tank ``step`` (pass ``locals()``)."""
    _apply(_TANK_STEP_RULES, args)
