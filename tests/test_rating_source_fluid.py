"""The source side of the rating point carries the model's own loop liquid.

The reference state (``rps_rated``, ``m_dot_ref_rated``) is the cycle solved at
the family's rating point. The liquid-source models solved its source side as
water (``c_w * rho_w``) whatever the loop was filled with, while their cycles
use the ``SourceFluid``. With the default 25 % ethylene glycol -- 8 % less heat
per litre than water, at a flow sized for the glycol -- the two disagreed at
the rating point itself: the ground-source heat pump's rated speed was 0.17 %
low (32.790 against 32.847 rev/s), the boiler's 0.23 %.
"""

import warnings

import pytest

from tmhp import GroundSourceHeatPump, GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler
from tmhp import calc_util as cu
from tmhp.constants import c_w, rho_w
from tmhp.heat_exchanger import calc_phase_change_hx_effectiveness
from tmhp.reference_state import HXSide


def _quiet(cls, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return cls(**kwargs)


@pytest.fixture(scope="module")
def gshp():
    return _quiet(GroundSourceHeatPump)


def test_side_without_a_heat_capacity_is_water_as_before():
    plain = HXSide("water", 0.0, 720.0, 2.5e-4)
    assert HXSide("water", 0.0, 720.0, 2.5e-4, rho_w * c_w).conductance == plain.conductance
    glycol = HXSide("water", 0.0, 720.0, 2.5e-4, 0.92 * rho_w * c_w)
    assert glycol.conductance < plain.conductance and glycol != plain and hash(glycol) != hash(plain)
    for bad in (0.0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            HXSide("water", 0.0, 720.0, 2.5e-4, bad)
    with pytest.raises(ValueError):
        HXSide("tank", 55.0, 720.0, None, rho_w * c_w)


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler])
@pytest.mark.parametrize("fluid", [None, "water", "MPG-30%"])
def test_rating_source_side_is_the_loop_liquid(cls, fluid):
    model = _quiet(cls, **({} if fluid is None else {"source_fluid": fluid}))
    liquid, side = model.source_fluid, model.rated_condition.source
    assert side.rho_cp == pytest.approx(liquid.rho * liquid.cp, rel=1e-15)
    # eps * m c_p of the loop as the cycle computes it at the reference flow.
    m_dot = model.dV_b_f_m3s * liquid.rho
    expected = calc_phase_change_hx_effectiveness(side.UA, m_dot, liquid.cp) * m_dot * liquid.cp
    assert side.conductance == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("mode", ["cooling", "heating"])
def test_gshp_cycle_reproduces_the_rated_speed_at_the_rating_point(mode):
    """Default glycol loop, liquid entering the heat pump at the rating temperature (ISO 13256-1).

    The model's steady solve takes the liquid as it leaves the borefield; the
    loop pump warms it by ``E_pmp / (m c_p)`` (0.085 K) on the way in, so the
    borefield outlet is set that much below the rating inlet.
    """
    hp = _quiet(GroundSourceHeatPump, rated_condition={"mode": mode})
    rating, liquid = hp.rated_condition, hp.source_fluid
    assert liquid.name == "MEG-25%" and rating.mode == mode
    pump_rise = hp.E_pmp / (hp.dV_b_f_m3s * liquid.rho * liquid.cp)
    hp.T_bhe_f_out_K = cu.C2K(rating.source.T_in_C) - pump_rise
    sign = 1.0 if mode == "cooling" else -1.0
    row = hp.analyze_steady(Q_r_iu=sign * hp.hp_capacity, T0=rating.source.T_in_C, T_a_room=rating.load.T_in_C)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] is None
    assert row["dV_iu_a [m3/s]"] == pytest.approx(hp.dV_iu_fan_a_ref, rel=1e-6)  # the rating airflow
    assert row["cmp_rpm [rpm]"] / 60.0 == pytest.approx(hp.rps_rated, rel=1e-6)  # was 0.17 % apart
    assert row["m_dot_ref [kg/s]"] == pytest.approx(hp.m_dot_ref_rated, rel=1e-6)


def test_gshpb_cycle_reproduces_the_rated_speed_at_the_rating_point():
    boiler = _quiet(GroundSourceHeatPumpBoiler)
    rating, liquid = boiler.rated_condition, boiler.source_fluid
    pump_rise = boiler.E_pmp / (boiler.dV_b_f_m3s * liquid.rho * liquid.cp)
    row = boiler.analyze_steady(
        T_tank_w=rating.load.T_in_C, T_source=rating.source.T_in_C - pump_rise, Q_ref_tank=boiler.hp_capacity, T0=10.0
    )
    assert row["failure_reason"] == "none"
    assert row["cmp_rpm [rpm]"] / 60.0 == pytest.approx(boiler.rps_rated, rel=1e-6)  # was 0.23 % apart


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
def test_default_flow_gives_glycol_and_water_loops_the_same_rated_speed(cls):
    """The default flow is 3 K at the rated source heat for the liquid in the loop: one heat capacity rate."""
    glycol, water = _quiet(cls), _quiet(cls, source_fluid="water")
    assert glycol.dV_b_f_m3s > 1.05 * water.dV_b_f_m3s  # more litres of the mixture for the same W/K
    assert glycol.rps_rated == pytest.approx(water.rps_rated, rel=1e-9)
    assert glycol.m_dot_ref_rated == pytest.approx(water.m_dot_ref_rated, rel=1e-9)


def test_water_loop_rated_speed_is_unchanged(gshp):
    """Recorded on main before the change (6c09f1d): the water loop's reference state."""
    water = _quiet(GroundSourceHeatPump, source_fluid="water")
    assert water.rps_rated == pytest.approx(32.84692595605513, rel=1e-6)
    assert _quiet(WaterSourceHeatPumpBoiler).rps_rated == pytest.approx(60.36772626891989, rel=1e-6)
    # The default glycol loop moved onto it (32.790 before).
    assert gshp.rps_rated == pytest.approx(32.84692595605513, rel=1e-6)
