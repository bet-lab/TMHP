"""Default rated indoor airflow of ``AirSourceHeatPump``: 400 cfm per ton.

The earlier default (``hp_capacity * 0.0002``, 720 m3/h per kW) moved about
four times the air a split or ducted unit moves. The indoor coil then ran a
9 K approach at nameplate capacity: supply air at 25 degC in heating, a
condensing temperature near 30 degC, and a rated COP above what such equipment
declares. ``5.37e-5 m3/s per W`` is the airflow unitary equipment is rated at.
"""

import math

import pytest

from tmhp import AirSourceHeatPump
from tmhp.constants import c_a, rho_a
from tmhp.hx_fan import INDOOR_AIRFLOW_PER_W


def _solve(model, Q_r_iu, T0, T_a_room):
    return model.analyze_steady(Q_r_iu=Q_r_iu, T0=T0, T_a_room=T_a_room, verbose=False, postprocess=False)


@pytest.fixture(scope="module")
def hp():
    return AirSourceHeatPump()


def test_the_default_is_400_cfm_per_ton():
    cfm, ton = 0.3048**3 / 60.0, 3516.8528
    flow = INDOOR_AIRFLOW_PER_W
    assert flow == pytest.approx(400.0 * cfm / ton, rel=2e-3)
    assert flow * 3600.0 * 1000.0 == pytest.approx(193.3, abs=0.1)  # m3/h per kW


@pytest.mark.parametrize("capacity", [3000.0, 14000.0])
def test_default_airflow_scales_with_the_nameplate(capacity):
    model = AirSourceHeatPump(hp_capacity=capacity)
    assert model.dV_iu_fan_a_rated == pytest.approx(capacity * INDOOR_AIRFLOW_PER_W, rel=1e-12)
    assert model.dV_iu_fan_a_max == model.dV_iu_fan_a_ref == model.dV_iu_fan_a_rated
    assert model.A_cross_iu == pytest.approx(model.dV_iu_fan_a_rated / 2.0)
    # The outdoor airflow default is unchanged.
    assert model.dV_ou_fan_a_rated == pytest.approx(capacity * 0.0002)


def test_a_published_airflow_overrides_the_default():
    model = AirSourceHeatPump(hp_capacity=3500.0, dV_iu_fan_a_rated=0.2067)
    assert model.dV_iu_fan_a_rated == 0.2067


def test_supply_air_and_fan_power_at_the_rating_points(hp):
    heating = _solve(hp, -4000.0, 7.0, 20.0)
    cooling = _solve(hp, 4000.0, 35.0, 27.0)
    for out in (heating, cooling):
        assert out["converged"] and out["capacity_clamped"] is None
    # Supply air about 15 K from the room, as a split unit delivers it.
    assert 33.0 < heating["T_iu_a_out [°C]"] < 40.0
    assert 10.0 < cooling["T_iu_a_out [°C]"] < 15.0
    # Condensing above 35 degC in heating, evaporating near 10 degC in cooling.
    assert 35.0 < heating["T_ref_cond_sat_v [°C]"] < 45.0
    assert 7.0 < cooling["T_ref_evap_sat [°C]"] < 13.0
    # Indoor fan well under 1 % of the nameplate, both fans together 1 to 4 %.
    for out in (heating, cooling):
        assert out["E_iu_fan [W]"] < 0.01 * 4000.0
        assert 0.01 * 4000.0 < out["E_iu_fan [W]"] + out["E_ou_fan [W]"] < 0.04 * 4000.0
    # Rated COP inside what such equipment declares (A7/20: 3.8-4.8; A35/27: 3.2-4.5).
    assert 3.8 < heating["cop_sys [-]"] < 4.9
    assert 3.2 < cooling["cop_sys [-]"] < 4.5


def test_the_earlier_default_gave_a_supply_temperature_near_the_room(hp):
    old = _solve(AirSourceHeatPump(dV_iu_fan_a_rated=4000.0 * 0.0002), -4000.0, 7.0, 20.0)
    new = _solve(hp, -4000.0, 7.0, 20.0)
    assert old["T_iu_a_out [°C]"] < 26.0 < new["T_iu_a_out [°C]"]
    assert old["cop_sys [-]"] > new["cop_sys [-]"]


def test_the_mild_weather_ceiling_is_the_indoor_coil_and_is_solved_not_swept(hp, monkeypatch):
    """The ceiling is one least-lift solve at the indoor coil's limit, not a ladder of grid sweeps."""
    calls = {"n": 0}
    original = hp._calc_state

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(hp, "_calc_state", counted)
    ceiling = hp.max_capacity(T0=7.0, T_a_room=20.0, mode="heating")
    # The ladder of duties on a 13 x 13 grid took several hundred cycle evaluations.
    assert calls["n"] < 80

    # The indoor coil at its largest airflow and the widest approach is the ceiling here.
    assert ceiling["binding"] == "cycle"
    assert ceiling["dT_ref_cond [K]"] == pytest.approx(hp.dT_approach_bounds[1], rel=1e-6)
    c_air = c_a * rho_a * hp.dV_iu_fan_a_max
    ua = hp.UA_iu_rated * (hp.dV_iu_fan_a_max / hp.dV_iu_fan_a_ref) ** hp.n_iu
    coil_limit = c_air * (1.0 - math.exp(-ua / c_air)) * hp.dT_approach_bounds[1]
    assert ceiling["Q_max [W]"] == pytest.approx(coil_limit, rel=1e-6)
    # The resolution of the old sweep is accepted and changes nothing.
    assert hp.max_capacity(T0=7.0, T_a_room=20.0, mode="heating", grid_points=7, rel_tol=5e-2) == ceiling
