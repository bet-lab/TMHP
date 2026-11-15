"""Default rated indoor airflow of ``AirSourceHeatPump``: the catalogue maximum of its indoor-unit class.

The first default (``hp_capacity * 0.0002``, 720 m3/h per kW) moved about
four times the air a split or ducted unit moves. The indoor coil then ran a
9 K approach at nameplate capacity: supply air at 25 degC in heating, a
condensing temperature near 30 degC, and a rated COP above what such equipment
declares. The default is now the largest fan step wall-mounted indoor units of
that capacity publish (``tmhp.default_inputs``, issue #86): 209 m3/h per kW at
the default 4 kW.
"""

import math

import pytest

from tmhp import AirSourceHeatPump
from tmhp.air_properties import air_density
from tmhp.constants import c_a
from tmhp.default_inputs import estimate_max_airflow


def _solve(model, Q_r_iu, T0, T_a_room):
    return model.analyze_steady(Q_r_iu=Q_r_iu, T0=T0, T_a_room=T_a_room, verbose=False, postprocess=False)


@pytest.fixture(scope="module")
def hp():
    return AirSourceHeatPump()


def test_the_default_is_the_wall_mounted_catalogue_maximum(hp):
    estimate = estimate_max_airflow(4000.0, side="indoor", product="wall_mounted", mode="cooling")
    assert hp.dV_iu_fan_a_rated == estimate.value
    assert hp.dV_iu_fan_a_rated * 3600.0 / 4.0 == pytest.approx(208.5, abs=0.1)  # m3/h per kW


@pytest.mark.parametrize("capacity", [3000.0, 14000.0])
def test_default_airflow_follows_the_nameplate(capacity):
    model = AirSourceHeatPump(hp_capacity=capacity)
    indoor = estimate_max_airflow(capacity, side="indoor", product="wall_mounted", mode="cooling")
    outdoor = estimate_max_airflow(capacity, side="outdoor", product="single_split", mode="cooling")
    assert model.dV_iu_fan_a_rated == indoor.value
    assert model.dV_iu_fan_a_max == model.dV_iu_fan_a_ref == model.dV_iu_fan_a_rated
    assert model.A_cross_iu == pytest.approx(model.dV_iu_fan_a_rated / 2.0)
    assert model.dV_ou_fan_a_rated == outdoor.value


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
    # Rated COP inside what such equipment declares. The five Daikin FTXM-A / RXM-A units of 2.0 to
    # 5.0 kW in validation/catalogs declare 4.14 to 5.00 in heating (H1) and 3.68 to 5.41 in cooling
    # (T1) (validation/results/gate_a/rated_points.csv); the bounds are that band with 5 % either
    # side in heating. The heating bound was 4.9 until the catalogue displacement (#86), read off
    # "3.8-4.8" without a source: the default unit is at 5.03 now (4.74 before), on the upper edge of
    # the declared band. It delivers its cooling nameplate here, three quarters of what a unit of
    # this size declares in heating (5.4 kW at 6/20 degC for the 4.2 kW RXM42A, COP 4.19), so the
    # declared band is a ceiling for this row, not a like-for-like point. The like-for-like one:
    like = _solve(hp, -5400.0, 6.0, 20.0)
    assert like["capacity_clamped"] is None and like["cop_sys [-]"] == pytest.approx(4.19, rel=0.05)  # 4.09
    assert 0.95 * 4.14 < heating["cop_sys [-]"] < 1.05 * 5.00
    assert 3.2 < cooling["cop_sys [-]"] < 4.5


def test_the_earlier_default_gave_a_supply_temperature_near_the_room(hp):
    old = _solve(AirSourceHeatPump(dV_iu_fan_a_rated=4000.0 * 0.0002), -4000.0, 7.0, 20.0)
    new = _solve(hp, -4000.0, 7.0, 20.0)
    assert old["T_iu_a_out [°C]"] < 26.0 < new["T_iu_a_out [°C]"]
    assert old["cop_sys [-]"] > new["cop_sys [-]"]


def test_the_mild_weather_ceiling_is_the_indoor_coil_and_is_solved_not_swept(monkeypatch):
    """The ceiling is one least-lift solve at the indoor coil's limit, not a ladder of grid sweeps."""
    # A search box the indoor coil reaches before the compressor does (the default 30 K is past it).
    hp = AirSourceHeatPump(dT_approach_bounds=(1.0, 20.0))
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
    c_air = c_a * air_density(20.0) * hp.dV_iu_fan_a_max  # room air at 20 °C
    ua = hp.UA_iu_rated * (hp.dV_iu_fan_a_max / hp.dV_iu_fan_a_ref) ** hp.n_iu
    coil_limit = c_air * (1.0 - math.exp(-ua / c_air)) * hp.dT_approach_bounds[1]
    assert ceiling["Q_max [W]"] == pytest.approx(coil_limit, rel=1e-6)
    # The resolution of the old sweep is accepted and changes nothing.
    assert hp.max_capacity(T0=7.0, T_a_room=20.0, mode="heating", grid_points=7, rel_tol=5e-2) == ceiling
