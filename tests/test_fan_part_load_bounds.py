"""ASHRAE table parity and independent fan validity/control/HX constraints."""

import math

import pytest

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler, GroundSourceHeatPump
from tmhp.constants import c_a, rho_a
from tmhp.heat_exchanger import calc_HX_perf_for_target_heat, calc_UA_from_dV_fan, resolve_fan_flow_limits
from tmhp.hx_fan import calc_ashrae_fan_power_ratio, calc_fan_operating_point, calc_fan_power_from_dV_fan


@pytest.mark.parametrize("ratio,table_power", [(0.1, 0.03), (0.5, 0.30), (1.0, 1.0)])
def test_method_2_agrees_with_method_1_table(ratio, table_power):
    assert calc_ashrae_fan_power_ratio(ratio) == pytest.approx(table_power, abs=0.0051)


@pytest.mark.parametrize(
    "demand,actual,low,high", [(0.1, 0.15, True, False), (0.5, 0.5, False, False), (1.1, 1.0, False, True)]
)
def test_default_operating_flow_clamps_without_power_floor(demand, actual, low, high):
    point = calc_fan_operating_point(demand, {"fan_ref_flow_rate": 1, "fan_ref_power": 100}, None)
    assert point["actual_flow"] == actual
    assert point["fan_flow_min_limit"] == low
    assert point["fan_flow_max_limit"] == high
    assert point["power_W"] == pytest.approx(100 * calc_ashrae_fan_power_ratio(actual))


def test_custom_manufacturer_coefficients_allow_explicit_above_reference_limit():
    point = calc_fan_operating_point(
        1.1,
        {"fan_ref_flow_rate": 1, "fan_ref_power": 100, "fan_max_flow_rate": 1.2},
        dict(c1=0, c2=0, c3=0, c4=1, c5=0),
    )
    assert point["actual_flow"] == 1.1
    assert point["power_W"] == pytest.approx(133.1)
    assert not point["fan_flow_max_limit"]
    assert resolve_fan_flow_limits(1, 0.05, 1.2, custom_curve=True) == (0.05, 1.2)
    assert resolve_fan_flow_limits(1, 0.05, 1.2) == (0.15, 1.0)
    assert calc_UA_from_dV_fan(1.2, dV_fan_ref=1, A_cross=0.5, UA=1000) == pytest.approx(1000 * 1.2**0.71)


def test_minimum_airflow_does_not_hide_heat_exchanger_overdelivery():
    flow = 0.1
    ua = calc_UA_from_dV_fan(flow, dV_fan_ref=1, A_cross=0.5, UA=1000)
    capacity = rho_a * c_a * flow * (1 - math.exp(-ua / (rho_a * c_a * flow))) * 10
    point = calc_HX_perf_for_target_heat(
        Q_ref_target=capacity, T_a_in_C=20, T_ref_sat_K=283.15, A_cross=0.5, UA_rated=1000, dV_fan_ref=1
    )
    assert point["dV_fan"] == 0.15 and point["min_limit"]
    assert not point["converged"] and point["Q_air"] > capacity
    assert point["capacity_margin_W"] > 0


@pytest.mark.parametrize(
    "cls,prefix",
    [
        (AirSourceHeatPump, "dV_iu_fan_a"),
        (AirSourceHeatPump, "dV_ou_fan_a"),
        (AirSourceHeatPumpBoiler, "dV_fan_a"),
        (GroundSourceHeatPump, "dV_iu_fan_a"),
    ],
)
def test_default_model_interfaces_limit_generic_reference_range(cls, prefix):
    kw = {prefix + "_ref": 1, prefix + "_min": 0.05, prefix + "_max": 1.2}
    if cls is GroundSourceHeatPump:
        kw.update(R_b=0.2, t_max_s=3600)
    hp = cls(**kw)
    assert getattr(hp, prefix + "_min") == 0.15
    assert getattr(hp, prefix + "_max") == 1.0
    assert getattr(hp, prefix + "_ref") == 1.0


def test_off_and_nan_diagnostic_behavior_is_preserved():
    assert math.isnan(
        calc_fan_power_from_dV_fan(0.1, {"fan_ref_flow_rate": 1, "fan_ref_power": 100}, None, is_active=False)
    )
    assert math.isnan(calc_fan_power_from_dV_fan(math.nan, {"fan_ref_flow_rate": 1, "fan_ref_power": 100}, None))


def test_generic_low_load_infeasibility_is_not_zero_power_operation():
    hp = GroundSourceHeatPump(
        ref="R410A",
        hp_capacity=8000,
        V_cmp_ref=1.2e-5,
        N_1=1,
        N_2=2,
        H_b=100,
        B=6,
        Ts=15,
        T_a_room=26,
        UA_ground_rated=1600,
        UA_iu_rated=800,
        ground_flow_ref_lpm=24,
        ground_flow_constant_lpm=24,
        ground_flow_min_lpm=9.6,
        ground_flow_max_lpm=36,
        hydraulic_pump=True,
        variable_Rb=True,
        variable_ground_hx_UA=True,
        dp_aux_ref=121026.84689699873,
        m_dot_ref_rated=0.04118871753307563,
        dV_iu_fan_a_ref=1.6,
        dV_iu_fan_a_min=0.24,
        dV_iu_fan_a_max=1.6,
        indoor_approach_max_K=25,
        PR_cycle_max=8,
        t_max_s=3600,
    )
    row = hp.analyze_steady(Q_r_iu=2400, T0=26, T_a_room=26, T_bhe_wall=15)
    assert not row["converged"] and not row["hp_is_on"]
    assert row["failure_reason"] == "load_hx_capacity_insufficient"
    assert math.isnan(row["cop_sys [-]"])
    assert row["E_tot [W]"] == row["ground_flow [m3/s]"] == 0
    assert hp.ground_flow_constant_lpm == 24 and hp.dV_iu_fan_a_min == 0.24
    assert row["iu_hx_min_flow_margin [W]"] > 0
