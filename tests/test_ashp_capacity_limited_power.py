"""A capacity-limited operating point must not waste power the unit need not spend.

Capacity-limited candidates are ranked by delivered heat alone. Where the
limit is the air side or the approach-temperature search domain, not the
compressor, that ranking returned a state that reaches the speed ceiling only
because the outdoor coil is starved of air: the default 4 kW unit asked for
5.1 kW at 2 degC delivered 4,819 W on 1,460 W, while 4,794 W takes 1,173 W.
"""

import pytest

from tmhp import AirSourceHeatPump


def _solve(model, Q_r_iu, T0, T_a_room):
    return model.analyze_steady(Q_r_iu=Q_r_iu, T0=T0, T_a_room=T_a_room, verbose=False, postprocess=False)


@pytest.fixture(scope="module")
def hp():
    return AirSourceHeatPump(defrost=None)


def test_a_limit_on_the_air_side_is_served_at_least_power(hp):
    limited = _solve(hp, -5090.0, 2.0, 20.0)
    assert limited["failure_reason"] == "none" and limited["capacity_clamped"] == "max"
    assert limited["capacity_binding"] == "cycle"
    # The compressor is nowhere near its ceiling and the outdoor coil has its air.
    assert limited["cmp_rpm [rpm]"] < 0.9 * hp.rps_max * 60.0
    assert limited["dV_ou_a [m3/s]"] > 0.5 * hp.dV_ou_fan_a_max
    # Per unit of heat it costs what the same unit costs just below its ceiling.
    below = _solve(hp, -0.99 * limited["Q_ref_iu [W]"], 2.0, 20.0)
    assert below["capacity_clamped"] is None
    assert limited["E_tot [W]"] / limited["Q_ref_iu [W]"] == pytest.approx(
        below["E_tot [W]"] / below["Q_ref_iu [W]"], rel=2e-2
    )
    assert limited["Q_max [W]"] == pytest.approx(limited["Q_ref_iu [W]"], rel=1e-9)


def test_a_compressor_that_is_flat_out_stays_at_its_ceiling(hp):
    limited = _solve(hp, -6000.0, -15.0, 20.0)
    assert limited["capacity_clamped"] == "max"
    assert limited["cmp_rpm [rpm]"] == pytest.approx(hp.rps_max * 60.0, rel=1e-6)
    assert limited["Q_ref_iu [W]"] < 6000.0


def test_a_candidate_without_a_fluid_state_is_infeasible_not_an_exception():
    """A flash CoolProp cannot solve at one search candidate does not abort the solve.

    R407C cooling at 30 °C room / 20 °C outdoor used to raise ``ValueError``
    (a single-phase flash below the fluid's entropy range at one candidate of
    the approach search); the candidate is now skipped and the point solves.
    A floor pressure above the critical pressure (R744, room 18 °C, outdoor
    15 °C) is a failed point with a reason, not an exception.
    """
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = AirSourceHeatPump(ref="R407C").analyze_steady(Q_r_iu=4000.0, T0=20.0, T_a_room=30.0, verbose=False)
        assert r["failure_reason"] == "none"
        assert r["Q_ref_iu [W]"] == pytest.approx(4000.0, rel=1e-6)
        co2 = AirSourceHeatPump(ref="R744", rps_rated=40.0)
        off = co2.analyze_steady(Q_r_iu=-2000.0, T0=15.0, T_a_room=18.0, verbose=False)
        assert off["hp_is_on"] is False and off["failure_reason"] != "none"
        assert off["Q_ref_iu [W]"] == 0.0 and off["E_tot [W]"] == 0.0


def test_a_fan_that_can_stop_does_not_break_the_capacity_ceiling():
    """``dV_iu_fan_a_min=0`` is an accepted configuration; the ceiling search used to divide by it."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        hp = AirSourceHeatPump(dV_iu_fan_a_min=0.0)
        ceiling = hp.max_capacity(T0=7.0, T_a_room=20.0, mode="heating")
    assert ceiling["Q_max [W]"] > hp.hp_capacity
