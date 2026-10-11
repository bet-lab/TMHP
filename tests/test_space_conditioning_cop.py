"""Final-air COP boundary, independently checked against coil and fan balances."""

import importlib
import math

import numpy as np
import pytest

from tmhp import AirSourceHeatPump, GroundSourceHeatPump
from tmhp.constants import c_a, rho_a


@pytest.fixture(params=[AirSourceHeatPump, GroundSourceHeatPump])
def model(request):
    common = dict(UA_iu_rated=6000, dV_iu_fan_a_rated=1.2)
    if request.param is AirSourceHeatPump:
        return request.param(UA_ou_rated=6000, dV_ou_fan_a_rated=1.2, **common)
    return request.param(UA_ground_rated=6000, **common)


def check_delivery(row):
    difference = row["Q_a_iu_out [W]"] - row["Q_a_iu_in [W]"]
    sensible = c_a * rho_a * row["dV_iu_a [m3/s]"] * (row["T_iu_a_out [°C]"] - row["T_iu_a_in [°C]"])
    assert difference == pytest.approx(sensible, abs=1e-8)
    if not row["hp_is_on"]:
        assert row["Q_a_iu_out [W]"] == row["Q_a_iu_in [W]"] == 0
        assert row["E_tot [W]"] == 0
        assert math.isnan(row["cop_sys [-]"])
        return
    sign = 1 if row["mode"] == "heating" else -1
    assert difference * sign == pytest.approx(row["Q_ref_iu [W]"] + sign * row["E_iu_fan [W]"], abs=1e-6)
    assert row["cop_sys [-]"] == pytest.approx(abs(difference) / row["E_tot [W]"])
    assert row["cop_ref [-]"] == pytest.approx(row["Q_ref_iu [W]"] / row["E_cmp [W]"])
    auxiliaries = ["E_iu_fan [W]", "E_ou_fan [W]" if "E_ou_fan [W]" in row else "E_pmp [W]"]
    assert row["E_tot [W]"] == pytest.approx(row["E_cmp [W]"] + sum(row[k] for k in auxiliaries))


@pytest.mark.parametrize("load,outdoor,room", [(3000, 30, 26), (-3000, 5, 20), (0, 5, 20)])
def test_steady_and_direct_delivery(model, load, outdoor, room):
    check_delivery(model._calc_state(10, 10, load, outdoor, room))
    check_delivery(model.analyze_steady(Q_r_iu=load, T0=outdoor, T_a_room=room))


def test_dynamic_delivery(model):
    frame = model.analyze_dynamic(3 * 3600, 3600, [3000, -3000, 0], [30, 5, 5], [26, 20, 20])
    for _, row in frame.iterrows():
        check_delivery(row)


@pytest.mark.parametrize("load,outdoor,room", [(3000, 30, 26), (-3000, 5, 20)])
def test_fan_heat_zero_and_positive_at_same_cycle(model, monkeypatch, load, outdoor, room):
    module = importlib.import_module(type(model).__module__)
    baseline = model._calc_state(10, 10, load, outdoor, room)
    original = module.calc_fan_power_from_dV_fan

    def no_indoor_fan_heat(**kwargs):
        return 0.0 if kwargs["fan_params"] is model.fan_params_iu else original(**kwargs)

    monkeypatch.setattr(module, "calc_fan_power_from_dV_fan", no_indoor_fan_heat)
    zero = model._calc_state(10, 10, load, outdoor, room)
    check_delivery(zero)
    assert zero["cop_sys [-]"] == pytest.approx(zero["Q_ref_iu [W]"] / zero["E_tot [W]"])
    for key in ("cmp_rpm [rpm]", "dV_iu_a [m3/s]", "Q_ref_iu [W]", "E_cmp [W]"):
        assert baseline[key] == zero[key]
    # Isolate the numerator effect by holding the denominator fixed.
    fan_effect = (abs(baseline["Q_a_iu_out [W]"] - baseline["Q_a_iu_in [W]"]) - zero["Q_ref_iu [W]"]) / baseline[
        "E_tot [W]"
    ]
    assert fan_effect * (1 if load < 0 else -1) > 0


def test_opposite_mode_heat_is_not_hidden_by_absolute_value(model, monkeypatch):
    module = importlib.import_module(type(model).__module__)
    monkeypatch.setattr(module, "calc_fan_power_from_dV_fan", lambda **kwargs: 10000.0)
    row = model._calc_state(10, 10, 3000, 30, 26)
    assert row["Q_a_iu_out [W]"] > row["Q_a_iu_in [W]"]
    assert not row["indoor_heat_direction_valid"]
    assert math.isnan(row["cop_sys [-]"])


def test_active_zero_airflow_has_no_reportable_cop(model, monkeypatch):
    module = importlib.import_module(type(model).__module__)
    monkeypatch.setattr(
        module,
        "calc_HX_perf_for_target_heat",
        lambda **kwargs: dict(dV_fan=0.0, T_a_mid_C=kwargs["T_a_in_C"], converged=True),
    )
    row = model._calc_state(10, 10, 3000, 30, 26)
    assert row["Q_a_iu_in [W]"] == row["Q_a_iu_out [W]"] == 0
    assert not row["indoor_heat_direction_valid"]
    assert math.isnan(row["cop_sys [-]"])


@pytest.mark.parametrize("boundary", ["min", "max"])
def test_speed_boundary_uses_actual_delivery(model, boundary):
    if boundary == "min":
        model.rps_min = 30
    else:
        model.rps_max = 16
    row = model._calc_state(10, 10, 3000, 30, 26)
    assert row["capacity_clamped"] == boundary
    check_delivery(row)
    assert not np.isclose(row["Q_ref_iu [W]"], 3000)


def test_failed_cycle_returns_off_delivery(model, monkeypatch):
    original = model._calc_state

    def invalid_active(dT_ref_evap, dT_ref_cond, Q_r_iu, T0, T_a_room, **kwargs):
        return None if Q_r_iu else original(dT_ref_evap, dT_ref_cond, Q_r_iu, T0, T_a_room, **kwargs)

    monkeypatch.setattr(model, "_calc_state", invalid_active)
    with pytest.warns(RuntimeWarning):
        row = model.analyze_steady(Q_r_iu=3000, T0=30, T_a_room=26)
    assert not row["hp_is_on"]
    assert row["failure_reason"] != "none"
    check_delivery(row)
