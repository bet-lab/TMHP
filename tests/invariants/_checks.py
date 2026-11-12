"""Physics invariant checks. Each check returns a list of violation strings.

A violation string starts with a stable tag (``"energy_balance"``,
``"eta_em"``, …) followed by the observed numbers, so a failing assertion
prints what broke and by how much. An empty list means "no violation".
"""

from __future__ import annotations

import math

import numpy as np

from tmhp.compressor_envelope import DISCHARGE_TOL_K

from ._families import (
    Family,
    Result,
    SteadyCase,
    e_cmp,
    eta_em,
    eta_is,
    eta_v,
    failure_reason,
    is_reported_running,
    num,
    q_cond,
    q_evap,
    t_cond_sat,
    t_evap_sat,
)


class KnownDefectObserved(Exception):  # noqa: N818 - reads as an event, not an error kind
    """The specific #96 defect a test is about was observed.

    Deliberately *not* an ``AssertionError`` subclass: ``xfail`` marks use
    ``raises=KnownDefectObserved`` so that an unrelated ``assert`` (in the
    test or in model code) still fails the test instead of being absorbed.
    """


def expect(condition: object, message: object) -> None:
    """Assert the behaviour a #96 defect breaks; raise :class:`KnownDefectObserved` if absent."""
    if not condition:
        raise KnownDefectObserved(str(message))


ENERGY_BALANCE_RTOL = 0.005
CARNOT_RTOL = 1e-3
TEMPERATURE_TOL_K = 0.05
ZERO_HEAT_TOL_W = 1.0
MIN_CLAMP_RTOL = 0.01


def _fmt(x: float) -> str:
    return f"{x:.6g}"


def check_running_cycle(family: Family, r: Result, case: SteadyCase) -> list[str]:
    """Invariants for a result that the model reports as a normal running solution."""
    v: list[str] = []
    Qc, Qe, E = q_cond(family, r), q_evap(family, r), e_cmp(r)
    m_dot = num(r, "m_dot_ref [kg/s]")
    h1, h2 = num(r, "h_ref_cmp_in [J/kg]"), num(r, "h_ref_cmp_out [J/kg]")
    W_ref = m_dot * (h2 - h1)

    # 1. cycle energy balance: condenser heat = evaporator heat + work on refrigerant
    if not all(math.isfinite(x) for x in (Qc, Qe, W_ref)) or Qc <= 0 or Qe <= 0:
        v.append(f"duty_nonpositive_or_nan: Q_cond={_fmt(Qc)} Q_evap={_fmt(Qe)} W_ref={_fmt(W_ref)}")
    else:
        err = abs(Qc - Qe - W_ref) / Qc
        if err > ENERGY_BALANCE_RTOL:
            v.append(f"energy_balance: |Qc-Qe-W|/Qc={_fmt(err)} (Qc={_fmt(Qc)} Qe={_fmt(Qe)} W={_fmt(W_ref)})")

    # 2. COP_ref <= Carnot COP at the saturation temperatures
    Te, Tc = t_evap_sat(r) + 273.15, t_cond_sat(r) + 273.15
    cop = num(r, "cop_ref [-]")
    if not (math.isfinite(Te) and math.isfinite(Tc) and Tc > Te):
        v.append(f"saturation_order: T_evap_sat={_fmt(Te - 273.15)} T_cond_sat={_fmt(Tc - 273.15)}")
    elif not math.isfinite(cop) or cop <= 0:
        v.append(f"cop_ref_nonpositive_or_nan: {_fmt(cop)}")
    else:
        carnot = Tc / (Tc - Te) if family.mode == "heating" else Te / (Tc - Te)
        if cop > carnot * (1.0 + CARNOT_RTOL):
            v.append(f"carnot: cop_ref={_fmt(cop)} > carnot_sat={_fmt(carnot)}")

    # 3. 0 < eta <= 1 for the three compressor efficiencies
    for tag, eta in (("eta_is", eta_is(r)), ("eta_v", eta_v(r)), ("eta_em", eta_em(r))):
        if not (math.isfinite(eta) and 0.0 < eta <= 1.0):
            v.append(f"{tag}: {_fmt(eta)} not in (0, 1]")

    # 4. heat flows down the temperature gradient in both exchangers
    T_ev_in = family.t_evap_side_in(r, case)
    T_cd_in = family.t_cond_side_in(r, case)
    if not t_evap_sat(r) < T_ev_in:
        v.append(f"evap_vs_source: T_evap_sat={_fmt(t_evap_sat(r))} >= T_source_in={_fmt(T_ev_in)}")
    if not t_cond_sat(r) > T_cd_in:
        v.append(f"cond_vs_demand: T_cond_sat={_fmt(t_cond_sat(r))} <= T_sink_in={_fmt(T_cd_in)}")

    # 5. suction no hotter than the source stream; superheat / subcooling >= 0
    T_suc = num(r, "T_ref_cmp_in [°C]")
    if not T_suc <= T_ev_in + TEMPERATURE_TOL_K:
        v.append(f"suction_vs_source: T_cmp_in={_fmt(T_suc)} > T_source_in={_fmt(T_ev_in)}")
    superheat = T_suc - t_evap_sat(r)
    subcool = num(r, "T_ref_cond_sat_l [°C]") - num(r, "T_ref_exp_in [°C]")
    if not superheat >= -0.01:
        v.append(f"superheat: {_fmt(superheat)} K")
    if not subcool >= -0.01:
        v.append(f"subcool: {_fmt(subcool)} K")

    # 6. discharge temperature is finite
    T_dis = num(r, "T_ref_cmp_out [°C]")
    if not math.isfinite(T_dis):
        v.append(f"discharge_nonfinite: T_cmp_out={_fmt(T_dis)}")

    if not (math.isfinite(E) and E > 0):
        v.append(f"power_nonpositive_or_nan: E_cmp={_fmt(E)}")
    return v


def _is_zero_or_nan(x: float) -> bool:
    return (not math.isfinite(x)) or abs(x) <= ZERO_HEAT_TOL_W


def check_not_running(family: Family, r: Result) -> list[str]:
    """A result reported as off / failed / unconverged carries no heat or power."""
    v: list[str] = []
    if failure_reason(r) == "none":
        v.append(
            f"failed_without_reason: hp_is_on={r.get('hp_is_on')} converged={r.get('converged')} failure_reason='none'"
        )
    for tag, x in (("Q_cond", q_cond(family, r)), ("Q_evap", q_evap(family, r)), ("E_cmp", e_cmp(r))):
        if not _is_zero_or_nan(x):
            v.append(
                f"failed_with_heat: {tag}={_fmt(x)} while "
                f"converged={r.get('converged')} failure_reason={failure_reason(r)!r}"
            )
    return v


def check_min_clamp(family: Family, r: Result, case: SteadyCase, rated_W: float) -> list[str]:
    """Minimum-speed clamp may overshoot the request, never past the rated duty."""
    if r.get("capacity_clamped") != "min":
        return []
    Q = abs(num(r, family.q_demand_key))
    limit = max(case.load_W, rated_W)
    if math.isfinite(Q) and limit * (1.0 + MIN_CLAMP_RTOL) < Q:
        return [f"min_clamp_excess: Q={_fmt(Q)} > max(request={_fmt(case.load_W)}, rated={_fmt(rated_W)})"]
    return []


ENVELOPE_RTOL = 1e-6


def check_envelope(model: object, r: Result) -> list[str]:
    """A running result lies inside the compressor envelope the model declares.

    Discharge temperature <= ``T_dis_max`` (when the check is on), pressure
    ratio <= ``PR_cycle_max`` and speed <= ``rps_max``.
    """
    v: list[str] = []
    T_dis = num(r, "T_ref_cmp_out [°C]")
    T_max = getattr(model, "T_dis_max", None)
    if T_max is not None and not T_dis <= T_max + DISCHARGE_TOL_K:
        v.append(f"discharge_above_max: T_cmp_out={_fmt(T_dis)} > T_dis_max={_fmt(T_max)}")
    pr = num(r, "P_ref_cmp_out [Pa]") / num(r, "P_ref_cmp_in [Pa]")
    pr_max = float(getattr(model, "PR_cycle_max", math.inf))
    if not pr <= pr_max * (1.0 + ENVELOPE_RTOL):
        v.append(f"pressure_ratio_above_max: PR={_fmt(pr)} > PR_cycle_max={_fmt(pr_max)}")
    rps = num(r, "cmp_rpm [rpm]") / 60.0
    rps_max = float(getattr(model, "rps_max", math.inf))
    if not rps <= rps_max * (1.0 + ENVELOPE_RTOL):
        v.append(f"speed_above_max: rps={_fmt(rps)} > rps_max={_fmt(rps_max)}")
    v += check_suction_quality(model, r)
    return v


def check_suction_quality(model: object, r: Result) -> list[str]:
    """Suction is dry vapour, except under discharge-temperature control (x >= x_suction_min).

    The reported quality must also be the state's own: ``h_cmp_in`` equals the
    saturated enthalpy at that quality, or lies at or above the dew point.
    """
    from CoolProp.CoolProp import PropsSI

    v: list[str] = []
    x = num(r, "x_ref_cmp_in [-]")
    controlled = bool(r.get("discharge_control", False))
    x_min = getattr(model, "x_suction_min", None)
    if not math.isfinite(x):
        return [f"suction_quality_missing: x_ref_cmp_in={_fmt(x)}"]
    if controlled:
        if x_min is None or not x >= x_min - 1e-9:
            v.append(f"suction_quality_below_min: x={_fmt(x)} x_suction_min={x_min}")
    elif x != 1.0:
        v.append(f"suction_wet_without_control: x={_fmt(x)}")
    ref = getattr(model, "ref", None)
    P1, h1 = num(r, "P_ref_cmp_in [Pa]"), num(r, "h_ref_cmp_in [J/kg]")
    if ref is not None and math.isfinite(P1) and math.isfinite(h1):
        h_x = PropsSI("H", "P", P1, "Q", min(x, 1.0), ref)
        if x < 1.0 and abs(h1 - h_x) > 1e-6 * abs(h_x) + 1.0:
            v.append(f"suction_state_inconsistent: h1={_fmt(h1)} h(x={_fmt(x)})={_fmt(h_x)}")
        if x >= 1.0 and h1 < h_x - 1.0:
            v.append(f"suction_wet_unreported: h1={_fmt(h1)} < h_dew={_fmt(h_x)}")
    return v


def check_steady(family: Family, r: Result, case: SteadyCase, rated_W: float, model: object = None) -> list[str]:
    """All steady-state invariants for one ``analyze_steady`` result."""
    v = check_min_clamp(family, r, case, rated_W)
    if is_reported_running(r):
        v += check_running_cycle(family, r, case)
        if model is not None:
            v += check_envelope(model, r)
    else:
        v += check_not_running(family, r)
    return v


def tags(violations: list[str]) -> set[str]:
    return {s.split(":", 1)[0] for s in violations}


def relative_residual(lhs: float, rhs: float, scale: float) -> float:
    """``|lhs - rhs| / scale`` with a NaN-safe guard."""
    if not (np.isfinite(lhs) and np.isfinite(rhs) and scale > 0):
        return math.inf
    return abs(lhs - rhs) / scale


# ---------------------------------------------------------------------------
# dynamic tank bookkeeping
# ---------------------------------------------------------------------------


def _col(df, key: str, default: float = 0.0) -> np.ndarray:
    if key not in df:
        return np.full(len(df), default)
    return np.nan_to_num(df[key].to_numpy(dtype=float), nan=default)


def tank_energy_residual(
    df,
    C_tank_full: float,
    T_init: float,
    dt: float,
    *,
    heat_keys: tuple[str, ...] = ("Q_ref_tank [W]",),
    T_ref: float | None = None,
    level_init: float = 1.0,
    always_full: bool = True,
) -> dict[str, float]:
    """Tank first-law residual over a dynamic run, from the model's own columns.

    Stored energy is ``C_full * level * (T - T_ref)``; water leaves at the
    end-of-step tank temperature (implicit Euler) and enters at
    ``T_tank_w_in``. ``heat_keys`` are the heat inputs the caller considers
    real (e.g. the refrigerant condenser heat). Returns the residual
    ``ΔU - Σ(inputs)·dt`` [J], the heat-input total [J], the water-throughput
    enthalpy [J] and the mass residual [m3/m3 of full tank].

    The hot-water outflow is rebuilt from the mixing-valve demand
    (``dV_mix_w_out``, ``T_mix_w_out``, ``T_sup_w``) at the end-of-step tank
    temperature, as the implicit solver does. The reported
    ``dV_tank_w_out`` column is not used: it is evaluated with the previous
    step's tank temperature and so disagrees with the solved balance.
    Inflow equals outflow when ``always_full``; otherwise the reported
    refill ``dV_tank_w_in`` is used.
    """
    T = df["T_tank_w [°C]"].to_numpy(dtype=float)
    if T_ref is None:
        T_ref = float(np.nanmin(_col(df, "T_tank_w_in [°C]", np.nan))) if "T_tank_w_in [°C]" in df else 15.0
    level = _col(df, "tank_level [-]", 1.0) if "tank_level [-]" in df else np.ones(len(df))
    T_sup = _col(df, "T_sup_w [°C]", T_ref) if "T_sup_w [°C]" in df else np.full(len(df), T_ref)
    T_mix = _col(df, "T_mix_w_out [°C]", np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        alp = np.clip((T_mix - T_sup) / np.maximum(T - T_sup, 1e-9), 0.0, 1.0)
    dV_out = np.nan_to_num(alp) * _col(df, "dV_mix_w_out [m3/s]")
    dV_in = dV_out if always_full else _col(df, "dV_tank_w_in [m3/s]")
    T_in = _col(df, "T_tank_w_in [°C]", T_ref)
    heat = sum((_col(df, k) for k in heat_keys), np.zeros(len(df)))
    loss = _col(df, "Q_tank_loss [W]")
    rc = rho_w_c_w()
    flow = rc * (dV_in * (T_in - T_ref) - dV_out * (T - T_ref))
    dU = C_tank_full * (level[-1] * (T[-1] - T_ref) - level_init * (T_init - T_ref))
    net = float(np.sum(heat - loss + flow) * dt)
    V_full = C_tank_full / rc
    mass = (level[-1] - level_init) - float(np.sum(dV_in - dV_out) * dt) / V_full
    return {
        "residual_J": float(dU - net),
        "heat_J": float(np.sum(heat) * dt),
        "throughput_J": float(np.sum(np.abs(flow)) * dt),
        "mass_residual": float(mass),
    }


def rho_w_c_w() -> float:
    from tmhp.constants import c_w, rho_w

    return float(c_w * rho_w)
