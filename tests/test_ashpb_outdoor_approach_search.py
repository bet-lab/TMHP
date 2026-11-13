"""ASHPB outdoor-approach search: band from the fan-limit coil margins (#96 C2).

The outdoor fan modulates between its flow limits, so only a band of outdoor
approaches carries the evaporator duty. The search solves that band's edges as
roots of the coil capacity margins and minimises inside it; a bounded Brent
over the whole range never found a band that its first probes missed.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

from tmhp import AirSourceHeatPumpBoiler
from tmhp._opt_utils import margin_feasible_interval
from tmhp.air_source_heat_pump_boiler import OUTDOOR_COIL_EXCESS, OUTDOOR_COIL_SHORT
from tmhp.dynamic_context import unphysical_cycle_detail

_FLOW_STATE = {
    "dV_mix_w_out [m3/s]": 0.0,
    "dV_tank_w_out [m3/s]": 0.0,
    "dV_tank_w_in [m3/s]": 0.0,
    "dV_mix_sup_w_in [m3/s]": 0.0,
}


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _steady(model: AirSourceHeatPumpBoiler, **point) -> dict:
    result = model.analyze_steady(**point)
    assert isinstance(result, dict)
    return result


def _assert_running(model: AirSourceHeatPumpBoiler, result: dict) -> None:
    assert result["failure_reason"] == "none"
    assert result["converged"] is True
    assert result["hp_is_on"] is True
    assert unphysical_cycle_detail(result, model.hp_capacity) is None
    assert model.dV_fan_a_min - 1e-12 <= result["dV_ou_a [m3/s]"] <= model.dV_fan_a_max + 1e-12


@pytest.mark.parametrize("request_W", [3000.0, 7500.0])
def test_cold_hot_water_point_inside_a_narrow_band_is_solved(request_W):
    # A-7/W55: below ~2.3 K the coil cannot absorb the duty at full airflow,
    # above ~7.5 K it over-absorbs at minimum airflow; both requests returned
    # hx_not_converged before. The points discharge at ~233 degC (eta_is 0.34),
    # outside the compressor envelope even under the discharge-temperature
    # control; the search is tested with the limit off.
    model = AirSourceHeatPumpBoiler(ref="R32", T_dis_max=None)
    result = _steady(model, T_tank_w=55.0, T0=-7.0, Q_ref_tank=request_W)
    _assert_running(model, result)
    approach = result["T0 [°C]"] - result["T_ref_evap_sat [°C]"]
    assert 2.0 < approach < 7.5


def test_search_is_not_beaten_by_a_dense_scan():
    """The band search returns at least as good a point as a 0.1 K scan of the range."""
    model = AirSourceHeatPumpBoiler(ref="R32", T_dis_max=None)  # 234 degC: outside the envelope otherwise
    point = {"T_tank_w": 55.0, "Q_ref_tank": 7500.0, "T0": -7.0}
    opt = model._optimize_operation(point["T_tank_w"], point["Q_ref_tank"], point["T0"], flow_state=_FLOW_STATE)
    best_scan = math.inf
    for x in np.arange(1.0, 20.0 + 1e-9, 0.1):
        perf = model._calc_state(float(x), point["T_tank_w"], point["Q_ref_tank"], point["T0"], flow_state=_FLOW_STATE)
        if perf is None or not perf.get("converged", False) or unphysical_cycle_detail(perf, model.hp_capacity):
            continue
        best_scan = min(best_scan, perf["E_tot [W]"])
    assert math.isfinite(best_scan)
    assert opt.fun <= best_scan + 1e-6


def test_request_below_minimum_speed_delivers_the_floor_capacity():
    """Same convention as ASHP and GSHP: run at rps_min, mark 'min', stay within the nameplate."""
    # The running machine (``Cd_cycling=None``: no averaging over the cycling period).
    model = AirSourceHeatPumpBoiler(ref="R32", Cd_cycling=None)
    result = _steady(model, T_tank_w=50.0, T0=-7.0, Q_ref_tank=750.0)
    _assert_running(model, result)
    assert result["capacity_clamped"] == "min"
    assert result["cmp_rpm [rpm]"] == pytest.approx(60.0 * model.rps_min)
    assert 750.0 < result["Q_ref_tank [W]"] <= model.hp_capacity
    assert result["Q_ref_tank_request [W]"] == 750.0
    # By default the row is the cycling mean of that machine: the request is delivered.
    mean = _steady(AirSourceHeatPumpBoiler(ref="R32"), T_tank_w=50.0, T0=-7.0, Q_ref_tank=750.0)
    assert mean["capacity_clamped"] == "min" and mean["Q_ref_tank [W]"] == pytest.approx(750.0, rel=1e-12)
    assert mean["Q_min [W]"] == pytest.approx(result["Q_ref_tank [W]"], rel=1e-9)
    assert mean["cmp_rpm [rpm]"] == pytest.approx(60.0 * model.rps_min)


@pytest.mark.parametrize("min_fraction", [0.99, 0.999])
def test_narrow_fan_range_still_finds_the_band(min_fraction):
    model = AirSourceHeatPumpBoiler(ref="R32", dV_fan_a_min=min_fraction * 15000.0 * 0.00015)
    result = _steady(model, T_tank_w=50.0, T0=7.0, Q_ref_tank=10000.0)
    _assert_running(model, result)
    assert result["Q_ref_tank [W]"] == pytest.approx(10000.0, rel=1e-9)


def test_coil_short_at_every_approach_is_named():
    # UA_ou 1/14 of the default: even at full airflow and the largest approach
    # the cycle reaches, the outdoor coil absorbs less than the duty.
    model = AirSourceHeatPumpBoiler(ref="R32", UA_ou_rated=150.0)
    opt = model._optimize_operation(50.0, 10000.0, 7.0, flow_state=_FLOW_STATE)
    assert opt.failure_detail == OUTDOOR_COIL_SHORT
    # The machine does not stop: it runs at a duty the coil carries at full
    # airflow. The approach that takes is so large that the discharge
    # temperature, held by wet suction, is what the next larger duty runs into.
    result = _steady(model, T_tank_w=50.0, T0=7.0, Q_ref_tank=10000.0)
    assert result["failure_reason"] == "none" and result["hp_is_on"] and result["converged"]
    assert result["capacity_clamped"] == "max" and result["capacity_binding"] == "discharge_temperature"
    assert result["discharge_control"] is True
    assert 0.0 < result["Q_ref_tank [W]"] < 10000.0
    assert result["Q_max [W]"] == result["Q_ref_tank [W]"] and result["Q_request [W]"] == 10000.0
    assert result["fan_flow_max_limit"]


def test_coil_excess_at_every_approach_is_named():
    # The fan cannot turn down below 9 m3/s, and at that flow the coil
    # absorbs more than the cycle hands it even at a 1 K approach.
    model = AirSourceHeatPumpBoiler(ref="R32", dV_fan_a_rated=10.0, dV_fan_a_min=9.0, UA_ou_rated=3.0e4)
    result = _steady(model, T_tank_w=30.0, T0=15.0, Q_ref_tank=3000.0)
    assert result["failure_reason"] == "hx_not_converged"
    assert result["failure_detail"] == OUTDOOR_COIL_EXCESS
    assert result["converged"] is False


def test_samples_narrow_the_bracket_without_moving_the_roots():
    calls: list[float] = []

    def capacity(x: float) -> float:
        calls.append(x)
        return x - 2.3

    def excess(x: float) -> float:
        calls.append(x)
        return x - 7.4

    plain = margin_feasible_interval(capacity, excess, 1.0, 20.0, xtol=1e-10)
    calls.clear()
    # Samples outside [lower, upper] are ignored.
    sampled = margin_feasible_interval(capacity, excess, 1.0, 20.0, xtol=1e-10, samples=[5.75, 10.5, 15.25, 0.5, 25])
    assert plain is not None and sampled is not None
    assert sampled == pytest.approx(plain, abs=1e-9)
    assert sampled == pytest.approx((2.3, 7.4), abs=1e-9)
    # Both roots were solved inside their sampled cells, [1, 5.75] and [5.75, 10.5].
    assert all(x <= 10.5 or x in (15.25, 20.0) for x in calls)


def test_samples_keep_an_empty_band_empty():
    assert margin_feasible_interval(lambda x: x - 30.0, lambda x: x - 40.0, 1.0, 20.0, samples=[5.0, 10.0]) is None
    assert margin_feasible_interval(lambda x: x + 5.0, lambda x: x + 1.0, 1.0, 20.0, samples=[5.0, 10.0]) is None


def test_search_moves_to_a_smaller_lift_inside_the_envelope():
    """With the envelope on, candidates outside it rank after every solved one.

    R290 wxg09 at -15/65 degC, 9 kW (adopted catalogue point): the solved
    pressure ratio is 10.4-10.5, but larger outdoor approaches cross PR 11 and
    the discharge limit. The point used to come back ``pr_above_max``.
    """
    model = AirSourceHeatPumpBoiler(ref="R290", hp_capacity=9000.0, V_cmp_ref=81.0e-6, dV_fan_a_rated=1.383)
    result = _steady(model, T_tank_w=62.5, T0=-15.0, Q_ref_tank=9000.0)
    _assert_running(model, result)
    assert result["P_ref_cmp_out [Pa]"] / result["P_ref_cmp_in [Pa]"] <= model.PR_cycle_max
    assert result["T_ref_cmp_out [°C]"] <= model.T_dis_max + 0.01
