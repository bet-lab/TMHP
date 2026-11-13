"""Constant-speed fans: ``dV_*_fan_a_min == dV_*_fan_a_max``.

With the airflow fixed, a coil can no longer modulate its flow to meet a duty.
The coil solve then clamps to that one flow and the cycle search closes the
duty through the approach temperature instead (the band of approaches the fan
can serve collapses to a point). ``AirSourceHeatPump`` solves that point too;
its tests are in ``test_ashp_narrow_fan_range.py``.
"""

import math

import numpy as np
import pytest

import tmhp.heat_exchanger as heat_exchanger
from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler, GroundSourceHeatPump
from tmhp.constants import c_a, rho_a
from tmhp.heat_exchanger import calc_HX_perf_for_target_heat, calc_UA_from_dV_fan, resolve_fan_flow_limits
from tmhp.hx_fan import ASHRAE_VSD_COEFFICIENTS, calc_fan_operating_point, calc_fan_power_from_dV_fan

CONSTANT_POWER = dict(c1=1.0, c2=0.0, c3=0.0, c4=0.0, c5=0.0, curve_type="custom")


# ── limits ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("custom_curve", [False, True])
def test_equal_limits_are_a_constant_speed_fan(custom_curve):
    assert resolve_fan_flow_limits(0.8, 0.8, 0.8, custom_curve=custom_curve) == (0.8, 0.8)


def test_generic_curve_still_intersects_equal_limits_with_its_validity_range():
    # A constant 0.5 m3/s fan on a 1.0 reference sits inside 0.15--1.0 ...
    assert resolve_fan_flow_limits(1.0, 0.5, 0.5) == (0.5, 0.5)
    # ... one above the reference or below 15 % does not.
    for flow in (1.2, 0.1):
        with pytest.raises(ValueError, match="overlap"):
            resolve_fan_flow_limits(1.0, flow, flow)


@pytest.mark.parametrize("low,high", [(0.9, 0.8), (0.0, 0.0), (-0.1, 0.8), (0.5, math.inf), (math.nan, 0.8)])
def test_inverted_zero_or_nonfinite_limits_stay_input_errors(low, high):
    with pytest.raises(ValueError):
        resolve_fan_flow_limits(1.0, low, high, custom_curve=True)


def test_issue_104_example_constructs_and_solves():
    # ``min == max`` equal to the default reference (hp_capacity * 0.0002 = 0.8).
    hp = GroundSourceHeatPump(ref="R32", dV_iu_fan_a_min=0.80, dV_iu_fan_a_max=0.80, R_b=0.2, t_max_s=3600)
    assert hp.dV_iu_fan_a_min == hp.dV_iu_fan_a_max == 0.80
    out = hp.analyze_steady(Q_r_iu=-2500, T0=7, T_a_room=20)
    assert out["converged"] and out["failure_reason"] == "none"
    assert out["dV_iu_a [m3/s]"] == pytest.approx(0.80)


# ── coil solve ────────────────────────────────────────────────────────────

COIL = dict(T_a_in_C=20.0, T_ref_sat_K=283.15, A_cross=0.5, UA_rated=1000.0, dV_fan_ref=1.0)


def _capacity(flow):
    ua = calc_UA_from_dV_fan(flow, A_cross=0.5, UA=1000.0, dV_fan_ref=1.0)
    c_air = c_a * rho_a * flow
    return c_air * (1 - math.exp(-ua / c_air)) * abs(293.15 - 283.15)


@pytest.fixture
def no_bisection(monkeypatch):
    """Fail if the coil solve ever bisects: ``[a, a]`` has no sign change."""

    def refuse(*args, **kwargs):
        raise AssertionError("bisection must not run with equal fan limits")

    monkeypatch.setattr(heat_exchanger, "root_scalar", refuse)


@pytest.mark.parametrize("custom", [False, True])
def test_coil_at_fixed_flow_matches_exact_duty_and_never_bisects(no_bisection, custom):
    flow = 0.6
    out = calc_HX_perf_for_target_heat(
        Q_ref_target=_capacity(flow), **COIL, dV_fan_min=flow, dV_fan_max=flow, custom_fan_curve=custom
    )
    assert out["converged"]
    assert out["dV_fan"] == flow
    assert out["Q_air"] == pytest.approx(_capacity(flow), rel=1e-12)
    assert out["min_limit"] and out["max_limit"]
    assert out["fan_flow_ratio_to_ref"] == pytest.approx(flow)


@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("scale,sign", [(0.5, +1), (2.0, -1)])
def test_coil_at_fixed_flow_reports_a_mismatched_duty_as_not_converged(no_bisection, custom, scale, sign):
    flow = 0.6
    out = calc_HX_perf_for_target_heat(
        Q_ref_target=scale * _capacity(flow), **COIL, dV_fan_min=flow, dV_fan_max=flow, custom_fan_curve=custom
    )
    assert not out["converged"]
    assert out["dV_fan"] == flow  # the airflow does not move to chase the duty
    assert out["min_limit"] and out["max_limit"]  # fixed fan: at both limits
    assert math.copysign(1.0, out["capacity_margin_W"]) == sign  # direction is in the margin
    assert out["min_flow_capacity_margin_W"] == pytest.approx(out["max_flow_capacity_margin_W"], rel=1e-12)
    assert out["capacity_margin_W"] == pytest.approx(out["max_flow_capacity_margin_W"], rel=1e-9)


def test_fixed_flow_matches_a_very_narrow_variable_range_at_the_same_duty():
    flow, duty = 0.6, 3000.0
    fixed = calc_HX_perf_for_target_heat(Q_ref_target=duty, **COIL, dV_fan_min=flow, dV_fan_max=flow)
    narrow = calc_HX_perf_for_target_heat(Q_ref_target=duty, **COIL, dV_fan_min=flow * 0.9995, dV_fan_max=flow * 1.0005)
    # Duty 3000 W is outside the +/-0.05 % flow band, so both clamp to the same side.
    assert fixed["Q_air"] == pytest.approx(narrow["Q_air"], rel=1e-3)
    assert fixed["dV_fan"] == pytest.approx(narrow["dV_fan"], rel=1e-3)


def test_ordinary_ranges_keep_their_flags():
    out = calc_HX_perf_for_target_heat(Q_ref_target=3000.0, **COIL, dV_fan_min=0.15, dV_fan_max=1.0)
    assert out["converged"] and not out["min_limit"] and not out["max_limit"]


# ── fan power and flags ───────────────────────────────────────────────────


def test_fan_operating_point_reports_both_limits_at_a_fixed_flow():
    params = dict(fan_ref_flow_rate=1.4, fan_ref_power=100.0, fan_min_flow_rate=1.4, fan_max_flow_rate=1.4)
    for demand in (0.3, 1.4, 5.0):
        out = calc_fan_operating_point(demand, params, CONSTANT_POWER)
        assert out["actual_flow"] == 1.4 and out["flow_ratio_to_ref"] == 1.0
        assert out["fan_flow_max_limit"] or out["fan_flow_min_limit"]
    out = calc_fan_operating_point(1.4, params, CONSTANT_POWER)
    assert out["fan_flow_min_limit"] and out["fan_flow_max_limit"]
    assert out["power_W"] == pytest.approx(100.0)  # flow ratio 1 -> rated power


@pytest.mark.parametrize(
    "coefficients,expected",
    [
        (CONSTANT_POWER, 1.0),
        (ASHRAE_VSD_COEFFICIENTS, 0.9991),  # 0.0013 + 0.1470 + 0.9506 - 0.0998
        (None, 0.998262),  # c1+..+c4 of the default single-zone VAV surrogate
    ],
)
def test_fixed_fan_power_at_the_reference_flow_follows_the_curve_at_ratio_one(coefficients, expected):
    params = dict(fan_ref_flow_rate=1.4, fan_ref_power=100.0, fan_min_flow_rate=1.4, fan_max_flow_rate=1.4)
    assert calc_fan_power_from_dV_fan(1.4, params, coefficients) == pytest.approx(100.0 * expected, rel=1e-9)


# ── steady states ─────────────────────────────────────────────────────────

FLOW_ASHPB = 1.4
FLOW_GSHP = 0.5
EPS = 5e-4  # +/- 0.05 % around the fixed flow


def _ashpb(**fan):
    return AirSourceHeatPumpBoiler(**fan)


def _gshp(**fan):
    return GroundSourceHeatPump(R_b=0.2, t_max_s=3600, **fan)


def _assert_same_state(a, b):
    for key in ("cop_sys [-]", "E_tot [W]"):
        assert a[key] == pytest.approx(b[key], rel=1e-3), key
    for key in ("T_ref_evap_sat [°C]", "T_ref_cond_sat_l [°C]"):  # Celsius: compare as a difference
        assert a[key] == pytest.approx(b[key], abs=0.02), key


def _fixed_and_narrow(make, prefix, flow):
    fixed = make(**{f"{prefix}_rated": flow, f"{prefix}_min": flow, f"{prefix}_max": flow})
    narrow = make(**{f"{prefix}_ref": flow, f"{prefix}_min": flow * (1 - EPS), f"{prefix}_max": flow * (1 + EPS)})
    return fixed, narrow


@pytest.mark.parametrize("Q", [1000.0, 2000.0, 5000.0, 8000.0])
def test_ashpb_dhw_at_a_fixed_outdoor_fan(Q):
    fixed, narrow = _fixed_and_narrow(_ashpb, "dV_fan_a", FLOW_ASHPB)
    a = fixed.analyze_steady(T_tank_w=40.0, T0=7.0, Q_ref_tank=Q)
    b = narrow.analyze_steady(T_tank_w=40.0, T0=7.0, Q_ref_tank=Q)
    assert a["converged"] and a["failure_reason"] == "none" and b["converged"]
    assert a["dV_ou_a [m3/s]"] == pytest.approx(FLOW_ASHPB)
    assert a["fan_flow_min_limit"] and a["fan_flow_max_limit"]
    assert a["E_ou_fan [W]"] == pytest.approx(140.0 * 0.998262, rel=1e-9)  # E_fan_rated * curve(1)
    _assert_same_state(a, b)


@pytest.mark.parametrize(
    "Q,T0,T_room",
    [(-800.0, 7.0, 20.0), (-2000.0, 7.0, 20.0), (-3500.0, 7.0, 20.0), (600.0, 30.0, 26.0), (3500.0, 30.0, 26.0)],
)
def test_gshp_heating_and_cooling_at_a_fixed_indoor_fan(Q, T0, T_room):
    fixed, narrow = _fixed_and_narrow(_gshp, "dV_iu_fan_a", FLOW_GSHP)
    a = fixed.analyze_steady(Q_r_iu=Q, T0=T0, T_a_room=T_room)
    b = narrow.analyze_steady(Q_r_iu=Q, T0=T0, T_a_room=T_room)
    assert a["converged"] and a["failure_reason"] == "none" and b["converged"]
    assert a["dV_iu_a [m3/s]"] == pytest.approx(FLOW_GSHP)
    assert a["iu_fan_flow_min_limit"] and a["iu_fan_flow_max_limit"]
    assert a["capacity_clamped"] == b["capacity_clamped"]
    _assert_same_state(a, b)


def test_gshp_fixed_fan_delivers_the_requested_duty_exactly_below_the_compressor_limits():
    fixed = _gshp(dV_iu_fan_a_rated=FLOW_GSHP, dV_iu_fan_a_min=FLOW_GSHP, dV_iu_fan_a_max=FLOW_GSHP)
    out = fixed.analyze_steady(Q_r_iu=-3500.0, T0=7.0, T_a_room=20.0)
    assert out["capacity_clamped"] is None
    assert out["Q_ref_iu [W]"] == pytest.approx(3500.0, rel=1e-5)


def test_fixed_fan_cannot_turn_down_but_the_approach_absorbs_a_small_duty():
    # A variable fan reports the minimum-capacity clamp for a tiny request; the
    # fixed fan reaches the same compressor floor through a smaller approach.
    fixed = _gshp(dV_iu_fan_a_rated=FLOW_GSHP, dV_iu_fan_a_min=FLOW_GSHP, dV_iu_fan_a_max=FLOW_GSHP)
    out = fixed.analyze_steady(Q_r_iu=-100.0, T0=7.0, T_a_room=20.0)
    assert out["converged"] and out["capacity_clamped"] == "min"
    assert out["Q_ref_iu [W]"] > 100.0


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_fixed_fan_with_an_undersized_coil_fails_like_a_variable_fan():
    kw = dict(dV_iu_fan_a_rated=FLOW_GSHP, UA_iu_rated=150.0)
    fixed = _gshp(dV_iu_fan_a_min=FLOW_GSHP, dV_iu_fan_a_max=FLOW_GSHP, **kw)
    variable = _gshp(**kw)
    for model in (fixed, variable):
        out = model.analyze_steady(Q_r_iu=-3000.0, T0=7.0, T_a_room=20.0)
        assert not out["converged"] and out["failure_reason"] == "hx_not_converged"
        assert out["Q_ref_iu [W]"] == 0.0


def test_ashpb_fixed_fan_with_an_undersized_coil_reports_hx_not_converged():
    out = _ashpb(dV_fan_a_rated=1.4, dV_fan_a_min=1.4, dV_fan_a_max=1.4, UA_ou_rated=300.0).analyze_steady(
        T_tank_w=40.0, T0=7.0, Q_ref_tank=9000.0
    )
    assert not out["converged"] and out["failure_reason"] == "hx_not_converged"


# ── dynamic ───────────────────────────────────────────────────────────────


def test_ashpb_dynamic_24h_with_a_fixed_outdoor_fan():
    dt, n = 1800, 48
    hours = np.arange(n) * dt / 3600
    dhw = np.where(((hours >= 7) & (hours < 8)) | ((hours >= 19) & (hours < 20)), 1.5e-4, 0.0)
    model = _ashpb(dV_fan_a_rated=1.4, dV_fan_a_min=1.4, dV_fan_a_max=1.4)
    df = model.analyze_dynamic(
        simulation_period_sec=24 * 3600,
        dt_s=dt,
        T_tank_w_init_C=50.0,
        dhw_usage_schedule=dhw,
        T0_schedule=7.0 + 5.0 * np.sin(2 * np.pi * hours / 24),
    )
    assert len(df) == n
    on = df[df["hp_is_on"].astype(bool)]
    assert len(on) > 0
    assert (on["failure_reason"] == "none").all()
    # Step values are time averages over the on-fraction of the step, so none
    # can exceed the fixed flow and a fully-on step equals it.
    flows = on["dV_ou_a [m3/s]"].to_numpy()
    assert flows.max() == pytest.approx(1.4) and (flows <= 1.4 + 1e-12).all()


def test_gshp_dynamic_24h_with_a_fixed_indoor_fan():
    dt, n = 3600, 24
    model = _gshp(dV_iu_fan_a_rated=0.5, dV_iu_fan_a_min=0.5, dV_iu_fan_a_max=0.5)
    model.t_max_s = 24 * 3600
    df = model.analyze_dynamic(
        simulation_period_sec=24 * 3600,
        dt_s=dt,
        Q_r_iu_schedule=-np.where(np.arange(n) < 12, 2500.0, 1200.0),
        T0_schedule=np.full(n, 7.0),
        T_a_room_schedule=np.full(n, 20.0),
    )
    assert len(df) == n
    assert df["converged"].all() and (df["failure_reason"] == "none").all()
    assert np.allclose(df["dV_iu_a [m3/s]"], 0.5)


# ── AirSourceHeatPump ─────────────────────────────────────────────────────


def test_air_source_heat_pump_accepts_constant_speed_fans_at_construction():
    hp = AirSourceHeatPump(
        dV_ou_fan_a_rated=0.5,
        dV_ou_fan_a_min=0.5,
        dV_ou_fan_a_max=0.5,
        dV_iu_fan_a_rated=0.5,
        dV_iu_fan_a_min=0.5,
        dV_iu_fan_a_max=0.5,
    )
    assert hp.fan_params_ou["fan_min_flow_rate"] == hp.fan_params_ou["fan_max_flow_rate"] == 0.5
    assert hp.fan_params_iu["fan_min_flow_rate"] == hp.fan_params_iu["fan_max_flow_rate"] == 0.5
