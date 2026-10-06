"""Compressor reference state solved from nominal capacity (issue #77)."""

import math

import CoolProp.CoolProp as CP
import pytest

import tmhp.reference_state as reference_state
from tmhp import (
    AirSourceHeatPump,
    AirSourceHeatPumpBoiler,
    GroundSourceHeatPump,
    GroundSourceHeatPumpBoiler,
    WaterSourceHeatPumpBoiler,
)
from tmhp.ashpb_stc_preheat import ASHPB_STC_preheat
from tmhp.compressor_efficiency import make_eta_em, make_eta_isen, make_eta_vol
from tmhp.compressor_speed import default_displacement
from tmhp.gshpb_stc_ground import GSHPB_STC_ground
from tmhp.reference_state import REFERENCE_CAPACITY_INCONSISTENT, ReferenceStateError
from tmhp.subsystems import SolarThermalCollector

GROUND = {"R_b": 0.2, "t_max_s": 3600}
FAMILIES = {
    AirSourceHeatPump: ({}, "ISO 5151", "cooling"),
    GroundSourceHeatPump: (GROUND, "ISO 13256-1", "cooling"),
    AirSourceHeatPumpBoiler: ({}, "A7/W55", "heating"),
    GroundSourceHeatPumpBoiler: ({"ref": "R32", **GROUND}, "B0/W55", "heating"),
    WaterSourceHeatPumpBoiler: ({"ref": "R32"}, "W10/W55", "heating"),
}
ALL = list(FAMILIES)
FLOW_STATE = {
    "dV_tank_w_out [m3/s]": 0.0,
    "dV_tank_w_in [m3/s]": 0.0,
    "dV_mix_sup_w_in [m3/s]": 0.0,
    "dV_mix_w_out [m3/s]": 0.0,
}


def build(cls, **overrides):
    return cls(**{**FAMILIES[cls][0], **overrides})


DERIVED = {
    ASHPB_STC_preheat: {},
    GSHPB_STC_ground: {"ref": "R32", **GROUND},
}


@pytest.mark.parametrize("cls", [*ALL, *DERIVED])
def test_capacity_only_construction_solves_reference_state(cls):
    hp = build(cls) if cls in FAMILIES else cls(stc=SolarThermalCollector(A_stc=4.0), **DERIVED[cls])
    state = hp.reference_state
    assert state is not None and state.rps_rated_solved
    assert hp.V_cmp_ref == pytest.approx(default_displacement(hp.hp_capacity))
    assert hp.rps_min < hp.rps_rated < hp.rps_max
    assert hp.rps_rated == state.rps_rated
    assert hp.m_dot_ref_rated == state.m_dot_ref_rated > 0
    rated_duty = state.Q_cond if state.condition.mode == "heating" else state.Q_evap
    assert rated_duty == pytest.approx(hp.hp_capacity, rel=1e-9)


@pytest.mark.parametrize("cls", ALL)
def test_rating_condition_is_the_family_standard_not_the_simulation_point(cls):
    hp = build(cls)
    _, standard, mode = FAMILIES[cls]
    assert standard in hp.rated_condition.standard
    assert hp.rated_condition.mode == mode
    if hasattr(hp, "T_a_room"):
        # The simulated room temperature is not used as the rating point.
        moved = build(cls, T_a_room=hp.T_a_room - 5)
        assert moved.rps_rated == pytest.approx(hp.rps_rated)


@pytest.mark.parametrize("cls", ALL)
def test_reference_state_closes_cycle_and_both_heat_exchangers(cls):
    hp = build(cls)
    state = hp.reference_state
    cond = state.condition
    # Rebuild the duty from the model's own (final) efficiencies at rps_rated:
    # n* = 1 there, so this must reproduce hp_capacity and m_dot_ref_rated.
    T_evap_K, T_cond_K = state.T_evap_sat_C + 273.15, state.T_cond_sat_C + 273.15
    P_evap = CP.PropsSI("P", "T", T_evap_K, "Q", 1, hp.ref)
    P_cond = CP.PropsSI("P", "T", T_cond_K, "Q", 0, hp.ref)
    assert state.pressure_ratio == pytest.approx(P_cond / P_evap, rel=1e-6)
    pr, rps = state.pressure_ratio, hp.rps_rated
    for name, factory in (("eta_cmp_vol", make_eta_vol), ("eta_cmp_isen", make_eta_isen), ("eta_cmp", make_eta_em)):
        assert getattr(state, name) == pytest.approx(getattr(hp, name)(pr, rps))
        assert getattr(hp, name)(pr, rps) == pytest.approx(factory(hp.rps_rated)(pr, rps))
    # Energy balance of each heat exchanger at rated UA and reference flow.
    heating = cond.mode == "heating"
    evap_side, cond_side = (cond.source, cond.load) if heating else (cond.load, cond.source)
    assert state.Q_evap == pytest.approx(evap_side.conductance * (evap_side.T_in_C - state.T_evap_sat_C))
    assert state.Q_cond == pytest.approx(cond_side.conductance * (state.T_cond_sat_C - cond_side.T_in_C))
    assert state.Q_cond - state.Q_evap == pytest.approx(state.E_cmp_ref)


def test_model_cycle_at_reference_saturation_reproduces_rated_speed_and_mass_flow():
    # Max flows above reference (reference, hence the rating point, unchanged)
    # so the model's own coil solvers are not sitting on their upper bound.
    ashp = AirSourceHeatPump(dV_iu_fan_a_max=1.2, dV_ou_fan_a_max=1.2)
    assert ashp.reference_state is AirSourceHeatPump().reference_state
    s = ashp.reference_state
    row = ashp._calc_state(27 - s.T_evap_sat_C, s.T_cond_sat_C - 35, ashp.hp_capacity, 35, 27)
    assert row["cmp_rpm [rpm]"] / 60 == pytest.approx(ashp.rps_rated, rel=1e-6)
    assert row["m_dot_ref [kg/s]"] == pytest.approx(ashp.m_dot_ref_rated, rel=1e-6)
    # Both coils close at their reference airflow with rated UA.
    assert row["dV_iu_a [m3/s]"] == pytest.approx(ashp.dV_iu_fan_a_ref, rel=1e-6)
    assert row["dV_ou_a [m3/s]"] == pytest.approx(ashp.dV_ou_fan_a_ref, rel=1e-6)

    ashpb = AirSourceHeatPumpBoiler(dV_fan_a_max=3.0)
    s = ashpb.reference_state
    row = ashpb._calc_state(7 - s.T_evap_sat_C, 55, ashpb.hp_capacity, 7, flow_state=FLOW_STATE)
    assert row["cmp_rpm [rpm]"] / 60 == pytest.approx(ashpb.rps_rated, rel=1e-6)
    assert row["m_dot_ref [kg/s]"] == pytest.approx(ashpb.m_dot_ref_rated, rel=1e-6)
    assert row["Q_ref_tank [W]"] == pytest.approx(ashpb.hp_capacity, rel=1e-6)
    assert row["dV_ou_a [m3/s]"] == pytest.approx(ashpb.dV_fan_a_ref, rel=1e-6)


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
def test_variable_ground_hx_ua_runs_without_external_reference_prerun(cls):
    hp = build(cls, variable_ground_hx_UA=True)
    assert hp._ground_settings["variable_UA"]
    assert hp._ground_settings["m_dot_ref_rated"] == hp.m_dot_ref_rated == hp.reference_state.m_dot_ref_rated
    if cls is GroundSourceHeatPump:
        row = hp.analyze_steady(Q_r_iu=hp.hp_capacity / 2, T0=30, T_a_room=26, T_bhe_wall=20)
    else:
        row = hp.analyze_steady(T_tank_w=50, T_source=10, Q_ref_tank=hp.hp_capacity / 2, T0=5, T_bhe_wall=10)
    assert row["converged"]


@pytest.mark.parametrize("cls", ALL)
def test_explicit_inputs_override_solved_values(cls):
    hp = build(cls, V_cmp_ref=8e-5, rps_rated=55.0, m_dot_ref_rated=0.03)
    assert (hp.V_cmp_ref, hp.rps_rated, hp.m_dot_ref_rated) == (8e-5, 55.0, 0.03)
    assert hp.reference_state is None  # nothing left to solve
    assert hp.eta_cmp_vol(3.0, 40.0) == pytest.approx(make_eta_vol(55.0)(3.0, 40.0))
    assert hp.eta_cmp_isen(3.0, 40.0) == pytest.approx(make_eta_isen(55.0)(3.0, 40.0))
    assert hp.eta_cmp(3.0, 40.0) == pytest.approx(make_eta_em(55.0)(3.0, 40.0))

    only_speed = build(cls, rps_rated=55.0)
    assert only_speed.rps_rated == 55.0 and not only_speed.reference_state.rps_rated_solved
    assert only_speed.m_dot_ref_rated == pytest.approx(only_speed.reference_state.m_dot_ref_rated)


def test_both_explicit_skips_the_solver(monkeypatch):
    def fail(**_):
        raise AssertionError("reference state must not be solved")

    monkeypatch.setattr(reference_state, "solve_reference_state", fail)
    hp = GroundSourceHeatPumpBoiler(ref="R32", rps_rated=40, m_dot_ref_rated=0.04, variable_ground_hx_UA=True, **GROUND)
    assert hp._ground_settings["m_dot_ref_rated"] == 0.04


@pytest.mark.parametrize("cls", ALL)
def test_explicit_rated_speed_keeps_former_results(cls):
    # Former behaviour: rps_rated fixed, baseline correlations built from it.
    fixed = 60.0 if cls in (AirSourceHeatPump, GroundSourceHeatPump) else 40.0
    legacy = dict(
        eta_cmp_isen=make_eta_isen(fixed),
        eta_cmp_vol=make_eta_vol(fixed),
        eta_cmp=make_eta_em(fixed),
        rps_rated=fixed,
    )
    new = build(cls, rps_rated=fixed)
    old = build(cls, **legacy)
    if cls is AirSourceHeatPump:
        args = dict(Q_r_iu=3000, T0=33, T_a_room=26, verbose=False)
    elif cls is GroundSourceHeatPump:
        args = dict(Q_r_iu=3000, T0=33, T_a_room=26)
    elif cls is AirSourceHeatPumpBoiler:
        args = dict(T_tank_w=50, Q_ref_tank=new.hp_capacity, T0=7)
    elif cls is GroundSourceHeatPumpBoiler:
        args = dict(T_tank_w=50, T_source=10, Q_ref_tank=4000, T0=5)
    else:
        args = dict(T_tank_w=50, T_source=10, Q_ref_tank=4000, T0=5)
    a, b = new.analyze_steady(**args), old.analyze_steady(**args)
    for key in ("E_cmp [W]", "m_dot_ref [kg/s]", "cmp_rpm [rpm]"):
        assert a[key] == pytest.approx(b[key], rel=1e-12, nan_ok=True)


def test_unreachable_rated_capacity_fails_instead_of_clamping():
    with pytest.raises(ReferenceStateError) as excinfo:
        AirSourceHeatPumpBoiler(V_cmp_ref=1e-5)
    assert isinstance(excinfo.value, ValueError)
    assert excinfo.value.reason == REFERENCE_CAPACITY_INCONSISTENT
    assert str(excinfo.value).startswith(REFERENCE_CAPACITY_INCONSISTENT)
    assert "rps_max" in str(excinfo.value)
    with pytest.raises(ReferenceStateError, match="rps_min"):
        AirSourceHeatPumpBoiler(V_cmp_ref=1e-3)
    with pytest.raises(ReferenceStateError, match="critical temperature"):
        AirSourceHeatPumpBoiler(ref="R744")


def test_rated_speed_is_not_forced_to_max_speed():
    hp = AirSourceHeatPump()
    assert hp.rps_rated < hp.rps_max
    # A machine rated below its top speed can deliver more than nameplate.
    assert hp.hp_capacity * hp.rps_max / hp.rps_rated > hp.hp_capacity


def test_rated_condition_override():
    cooling = AirSourceHeatPump()
    heating = AirSourceHeatPump(rated_condition={"mode": "heating"})
    assert "H1" in heating.rated_condition.standard
    assert heating.rated_condition.source.T_in_C == 7.0
    assert heating.rps_rated != pytest.approx(cooling.rps_rated)

    custom = AirSourceHeatPumpBoiler(rated_condition={"source_T_C": 2.0, "load_T_C": 50.0})
    assert custom.rated_condition.standard == "user-specified rating condition"
    assert custom.rated_condition.source.T_in_C == 2.0
    assert custom.rps_rated > AirSourceHeatPumpBoiler(rated_condition={"source_T_C": 7.0}).rps_rated

    with pytest.raises(ValueError, match="Unknown rated_condition"):
        AirSourceHeatPump(rated_condition={"outdoor": 7})
    with pytest.raises(ValueError, match="no 'cooling' rating condition"):
        AirSourceHeatPumpBoiler(rated_condition={"mode": "cooling"})


def test_reference_state_is_cached_per_identical_machine():
    a, b = AirSourceHeatPumpBoiler(), AirSourceHeatPumpBoiler()
    assert a.reference_state is b.reference_state
    c = AirSourceHeatPumpBoiler(hp_capacity=12000)
    assert c.reference_state is not a.reference_state


def test_custom_efficiency_callables_remain_supported():
    seen = []

    def eta_vol(pr, rps):
        seen.append(rps)
        return 0.9 - 0.01 * pr

    hp = AirSourceHeatPumpBoiler(eta_cmp_vol=eta_vol, eta_cmp_isen=lambda pr: 0.7, eta_cmp=0.9)
    assert hp.eta_cmp_vol is eta_vol and hp.eta_cmp == 0.9
    assert all(hp.rps_min <= r <= hp.rps_max for r in seen)  # never evaluated outside the envelope
    s = hp.reference_state
    assert (s.eta_cmp_isen, s.eta_cmp) == (0.7, 0.9)
    assert s.eta_cmp_vol == pytest.approx(0.9 - 0.01 * s.pressure_ratio)
    assert math.isfinite(hp.rps_rated)
