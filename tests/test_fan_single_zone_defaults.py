"""Fan-only backport regression: power floors remain independent of airflow."""

import math

import pytest

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler, GroundSourceHeatPump
from tmhp.hx_fan import (
    ASHRAE_VSD_COEFFICIENTS,
    SINGLE_ZONE_VAV_COEFFICIENTS,
    calc_ashrae_fan_power_ratio,
    calc_fan_operating_point,
    calc_fan_power_from_dV_fan,
)


def test_single_zone_power_floor_does_not_impose_an_airflow_floor():
    params = {"fan_ref_flow_rate": 0.5, "fan_ref_power": 50, "fan_min_flow_rate": 0.075, "fan_max_flow_rate": 0.5}
    lower = calc_fan_operating_point(0.10, params, SINGLE_ZONE_VAV_COEFFICIENTS)
    higher = calc_fan_operating_point(0.15, params, SINGLE_ZONE_VAV_COEFFICIENTS)
    assert lower["actual_flow"] == 0.10 and higher["actual_flow"] == 0.15
    assert lower["power_W"] == higher["power_W"] == 5
    assert lower["power_floor_active"] and higher["power_floor_active"]
    assert not lower["fan_flow_min_limit"] and not higher["fan_flow_min_limit"]
    full = calc_fan_operating_point(0.5, params, SINGLE_ZONE_VAV_COEFFICIENTS)
    assert not full["power_floor_active"]
    assert full["power_W"] == pytest.approx(50 * 0.998262)


@pytest.mark.parametrize("floor", [-0.1, 1.1, math.nan, math.inf])
def test_invalid_electrical_floor_is_rejected(floor):
    with pytest.raises(ValueError, match="power_min_ratio"):
        calc_fan_operating_point(0.2, {"fan_ref_flow_rate": 0.5, "fan_ref_power": 50}, {"power_min_ratio": floor})


@pytest.mark.parametrize("ratio,table_power", [(0.1, 0.03), (0.5, 0.30), (1.0, 1.0)])
def test_method_2_agrees_with_method_1_table(ratio, table_power):
    assert calc_ashrae_fan_power_ratio(ratio) == pytest.approx(table_power, abs=0.0051)


@pytest.mark.parametrize(
    "demand,actual,low,high", [(0.1, 0.15, True, False), (0.5, 0.5, False, False), (1.1, 1.0, False, True)]
)
def test_default_operating_flow_clamps_without_power_floor(demand, actual, low, high):
    point = calc_fan_operating_point(demand, {"fan_ref_flow_rate": 1, "fan_ref_power": 100}, ASHRAE_VSD_COEFFICIENTS)
    assert point["actual_flow"] == actual
    assert point["fan_flow_min_limit"] == low
    assert point["fan_flow_max_limit"] == high
    assert point["power_W"] == pytest.approx(100 * calc_ashrae_fan_power_ratio(actual))


def test_off_and_nan_diagnostic_behavior_is_preserved():
    assert math.isnan(
        calc_fan_power_from_dV_fan(0.1, {"fan_ref_flow_rate": 1, "fan_ref_power": 100}, None, is_active=False)
    )
    assert math.isnan(calc_fan_power_from_dV_fan(math.nan, {"fan_ref_flow_rate": 1, "fan_ref_power": 100}, None))


@pytest.mark.parametrize("coefficients", [None, {}])
def test_default_fan_uses_single_zone_curve_and_independent_floor(coefficients):
    params = {"fan_ref_flow_rate": 0.5, "fan_ref_power": 50}
    points = [calc_fan_operating_point(q, params, coefficients) for q in (0.075, 0.10, 0.15)]
    assert [p["actual_flow"] for p in points] == [0.075, 0.10, 0.15]
    assert all(p["power_W"] == 5 and p["power_floor_active"] for p in points)
    assert all(p["flow_min"] == 0.075 and p["flow_max"] == 0.5 for p in points)
    full = calc_fan_operating_point(0.5, params, coefficients)
    assert full["power_W"] == pytest.approx(49.9131)


@pytest.mark.parametrize("cls", [AirSourceHeatPump, AirSourceHeatPumpBoiler, GroundSourceHeatPump])
def test_model_defaults_use_single_zone_and_do_not_share_mutable_coefficients(cls):
    kwargs = {"R_b": 0.2, "t_max_s": 3600} if cls is GroundSourceHeatPump else {}
    first, second = cls(**kwargs), cls(**kwargs)
    attrs = ["vsd_coeffs"] if cls is AirSourceHeatPumpBoiler else ["vsd_coeffs_iu"]
    if cls is AirSourceHeatPump:
        attrs += ["vsd_coeffs_ou"]
    for attr in attrs:
        assert getattr(first, attr) == SINGLE_ZONE_VAV_COEFFICIENTS
        assert getattr(first, attr) is not getattr(second, attr)


def test_explicit_custom_coefficients_do_not_acquire_default_power_floor():
    point = calc_fan_operating_point(
        0.15, {"fan_ref_flow_rate": 1, "fan_ref_power": 100}, {"c1": 0, "c2": 0, "c3": 0, "c4": 1, "c5": 0}
    )
    assert point["power_W"] == pytest.approx(0.3375)
    assert point["power_min_ratio"] == 0


def test_explicit_model_flow_limits_match_hx_and_power_backport():
    from tmhp.enex_functions import calc_HX_perf_for_target_heat

    hp = AirSourceHeatPumpBoiler(dV_fan_a_rated=1, dV_fan_a_min=0.05, dV_fan_a_max=1.2)
    assert hp.fan_params["fan_min_flow_rate"] == 0.05
    assert hp.fan_params["fan_max_flow_rate"] == 1.2
    row = calc_HX_perf_for_target_heat(
        1, T_a_in_C=20, T_ref_sat_K=283.15, A_cross=0.5, UA_rated=1000, dV_fan_rated=1, dV_fan_min=0.05, dV_fan_max=1.2
    )
    assert not row["converged"] and row["min_limit"]
    fan = calc_fan_operating_point(0.01, hp.fan_params, hp.vsd_coeffs)
    assert fan["actual_flow"] == 0.05 and fan["power_floor_active"]
