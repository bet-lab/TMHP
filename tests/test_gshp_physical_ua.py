"""Physical HX sizing, legacy adapter and GSHP compressor loss accounting."""

import math

import pytest

from tmhp import GroundSourceHeatPump
from tmhp.ground_flow_control import failed_ground_point
from tmhp.ground_loop import ground_hx_UA
from tmhp.heat_exchanger import calc_ground_hx_UA_from_capacity


def model(**kwargs):
    return GroundSourceHeatPump(ref="R410A", hp_capacity=8000, R_b=0.2, t_max_s=3600, **kwargs)


@pytest.mark.parametrize("capacity,expected", [(4000, 720), (8000, 1440), (10000, 1800), (12000, 2160)])
def test_ground_UA_capacity_scaling(capacity, expected):
    assert calc_ground_hx_UA_from_capacity(capacity) == pytest.approx(expected)
    hp = GroundSourceHeatPump(hp_capacity=capacity, R_b=0.2, t_max_s=3600)
    assert hp.UA_ground_rated == pytest.approx(expected)
    assert calc_ground_hx_UA_from_capacity(2 * capacity) == pytest.approx(2 * expected)


@pytest.mark.parametrize(
    "capacity,k",
    [
        (0, 0.18),
        (-1, 0.18),
        (math.nan, 0.18),
        (math.inf, 0.18),
        (8000, 0),
        (8000, -1),
        (8000, math.nan),
        (8000, math.inf),
        (1e308, 1e308),
    ],
)
def test_invalid_ground_UA_inputs(capacity, k):
    with pytest.raises(ValueError):
        calc_ground_hx_UA_from_capacity(capacity, k)


def test_explicit_ground_UA_and_air_UA_are_independent():
    automatic = model(ground_hx_ua_per_capacity=0.2)
    assert automatic.UA_ground_rated == 1600
    overridden = model(UA_ground_rated=2300, ground_hx_ua_per_capacity=0.2)
    assert overridden.UA_ground_rated == 2300
    assert automatic.UA_iu_rated == overridden.UA_iu_rated == 640
    assert model(UA_ground_rated=2300, UA_iu_rated=900).UA_iu_rated == 900


@pytest.mark.parametrize("load,mode", [(4000, "cooling"), (-4000, "heating")])
def test_physical_UA_is_identical_across_modes(load, mode):
    hp = model(UA_ground_rated=1440, UA_iu_rated=640, variable_ground_hx_UA=True, m_dot_ref_rated=0.04)
    assert hp._rated_hx_UAs(mode) == (1440, 640)
    row = hp._calc_state(10, 10, load, 26, 26)
    assert row is not None
    assert row["UA_ground_rated [W/K]"] == 1440
    assert row["UA_iu_rated [W/K]"] == 640
    assert row["UA_ground [W/K]"] == pytest.approx(ground_hx_UA(hp._ground_settings, 1440, 1, row["m_dot_ref [kg/s]"]))
    assert ground_hx_UA(hp._ground_settings, hp.UA_ground_rated, 1, 0.04) == pytest.approx(1440)


def test_deprecated_roles_preserve_explicit_legacy_mapping_and_physical_inputs_win():
    with pytest.warns(DeprecationWarning, match="cycle-role inputs"):
        legacy = model(UA_cond=2000, UA_evap=1500)
    assert legacy._rated_hx_UAs("cooling") == (2000, 1500)
    assert legacy._rated_hx_UAs("heating") == (1500, 2000)
    with pytest.warns(DeprecationWarning):
        physical = model(UA_cond=2000, UA_evap=1500, UA_ground_rated=1440, UA_iu_rated=640)
    assert physical._rated_hx_UAs("cooling") == physical._rated_hx_UAs("heating") == (1440, 640)


@pytest.mark.parametrize("load", [4000, -4000])
def test_efficiencies_correct_power_speed_and_refrigerant_energy_balance(load):
    ideal = model(eta_v=1, eta_em=1)._calc_state(10, 10, load, 26, 26)
    real = model()._calc_state(10, 10, load, 26, 26)
    assert ideal is not None and real is not None
    assert real["eta_v [-]"] == 0.9 and real["eta_em [-]"] == 0.8
    assert real["m_dot_ref [kg/s]"] == pytest.approx(ideal["m_dot_ref [kg/s]"])
    assert real["E_cmp [W]"] == pytest.approx(ideal["E_cmp [W]"] / 0.8)
    assert real["cmp_rpm [rpm]"] == pytest.approx(ideal["cmp_rpm [rpm]"] / 0.9)
    assert real["E_cmp_ref [W]"] == pytest.approx(0.8 * real["E_cmp [W]"])
    assert real["E_cmp_loss [W]"] == pytest.approx(0.2 * real["E_cmp [W]"])
    condenser = real["Q_ref_ground [W]"] if load > 0 else real["Q_ref_iu [W]"]
    evaporator = real["Q_ref_iu [W]"] if load > 0 else real["Q_ref_ground [W]"]
    assert condenser == pytest.approx(evaporator + real["E_cmp_ref [W]"])
    assert real["E_tot [W]"] == pytest.approx(real["E_cmp [W]"] + real["E_pmp [W]"] + real["E_iu_fan [W]"])
    assert real["cop_sys [-]"] == pytest.approx(abs(load) / real["E_tot [W]"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"eta_v": 0},
        {"eta_v": 1.1},
        {"eta_v": math.nan},
        {"eta_em": 0},
        {"eta_em": 1.1},
        {"eta_em": math.inf},
        {"UA_ground_rated": 0},
        {"UA_iu_rated": -1},
    ],
)
def test_invalid_efficiencies_or_physical_UA(kwargs):
    with pytest.raises(ValueError):
        model(**kwargs)


def test_off_and_failed_points_have_no_compressor_loss_or_work():
    row = model()._calc_state(5, 5, 0, 26, 26)
    assert row is not None
    assert row["E_cmp [W]"] == row["E_cmp_ref [W]"] == row["E_cmp_loss [W]"] == 0
    failed = failed_ground_point("ground_hx_capacity_insufficient", {"E_cmp_ref [W]": 80, "E_cmp_loss [W]": 20})
    assert failed["E_cmp_ref [W]"] == failed["E_cmp_loss [W]"] == 0
