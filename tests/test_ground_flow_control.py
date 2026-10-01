"""Coupled control must conserve energy and reject infeasible duties."""

import math

import numpy as np
import pytest

from tmhp import GroundSourceHeatPump, GroundSourceHeatPumpBoiler
from tmhp.constants import c_w, rho_w
from tmhp.ground_flow_control import failed_ground_point, select_ground_flow


def _model(cls, **overrides):
    kw = dict(
        ref="R410A",
        V_cmp_ref=1.2e-5,
        N_1=2,
        N_2=2,
        H_b=100,
        dV_b_f_lpm=80,
        hydraulic_pump=True,
        variable_Rb=True,
        variable_ground_hx_UA=True,
        m_dot_ref_rated=0.04,
        hp_capacity=8000,
        t_max_s=86400,
    )
    kw.update(
        dict(UA_cond=2000, UA_evap=2000, eta_v=1, eta_em=1, PR_cycle_max=8)
        if cls is GroundSourceHeatPump
        else dict(UA_tank_hx=2000, UA_ground=2000)
    )
    kw.update(overrides)
    return cls(**kw)


def _run(model, load=4000, ratio=1.0):
    if isinstance(model, GroundSourceHeatPump):
        return model.analyze_steady(Q_r_iu=-load, T0=7, T_a_room=20, T_bhe_wall=16, ground_flow_ratio=ratio)
    return model.analyze_steady(T_tank_w=40, T_source=16, Q_ref_tank=load, T0=7, T_bhe_wall=16, ground_flow_ratio=ratio)


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
def test_coupled_energy_balance_and_determinism(cls):
    model = _model(cls)
    a = _run(model, ratio=0.8)
    assert a["converged"], a["failure_reason"]
    assert a["hx_feasible"] and a["approach_solver_success"]
    assert a["Q_HX_available [W]"] == pytest.approx(a["Q_ref_required [W]"], abs=0.05)
    assert a["T_bhe [°C]"] == pytest.approx(16, abs=1e-5)
    mass = a["dV_bhe_f [m3/s]"] * rho_w
    assert a["m_dot_borehole [kg/s]"] == pytest.approx(mass / 4)
    assert (a["T_bhe_f_out [°C]"] - a["T_bhe_f_in [°C]"]) * mass * c_w == pytest.approx(a["Q_bhe [W]"])
    assert a["Q_bhe [W]"] == pytest.approx(a["Q_ref_ground [W]"] - a["E_pmp [W]"])
    assert 16 - a["T_bhe_f [°C]"] == pytest.approx(a["Q_bhe [W]"] / 400 * a["R_b_eff [mK/W]"], abs=1e-5)
    _run(model, ratio=1.1)
    b = _run(model, ratio=0.8)
    for key in ("E_cmp [W]", "E_pmp [W]", "R_b_eff [mK/W]", "UA_ground [W/K]"):
        assert b[key] == pytest.approx(a[key], rel=1e-10)
    assert model.dV_b_f_m3s == pytest.approx(80 / 60000)  # no final-candidate mutation


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
def test_inadequate_hx_has_no_reportable_power_or_cop(cls):
    model = _model(cls, **({"UA_evap": 1} if cls is GroundSourceHeatPump else {"UA_ground": 1}))
    row = _run(model)
    assert not row["converged"] and not row["hx_feasible"]
    assert row["failure_reason"] == "ground_hx_capacity_insufficient"
    assert math.isnan(row["cop_sys [-]"])
    assert row["E_cmp [W]"] == row["E_pmp [W]"] == 0
    assert math.isnan(row["ground_flow_ratio"])


def test_flow_optimizer_matches_independent_analytic_optimum_and_rejects_invalid_region():
    # P = 1/f + f² -> f* = (1/2)^(1/3); low-flow capacity boundary at f=0.4.
    def evaluate(f):
        if f < 0.4:
            return failed_ground_point("ground_hx_capacity_insufficient")
        return {
            "converged": True,
            "hx_feasible": True,
            "ground_flow_ratio": f,
            "E_tot [W]": 1 / f + f * f,
            "E_cmp_plus_pmp [W]": 1 / f + f * f,
        }

    settings = dict(control="optimal_power", min_ratio=0.2, max_ratio=1.2)
    result = select_ground_flow(evaluate, settings)
    assert result["ground_flow_ratio"] == pytest.approx(0.5 ** (1 / 3), abs=1e-3)
    assert result["flow_optimizer_success"] and result["flow_optimizer_nfev"] > 9
    assert result["E_cmp_plus_pmp [W]"] <= evaluate(1)["E_cmp_plus_pmp [W]"]
    invalid = select_ground_flow(lambda _: failed_ground_point("ground_hx_capacity_insufficient"), settings)
    assert not invalid["converged"] and math.isnan(invalid["ground_flow_ratio"])


def test_fan_power_changes_both_search_and_final_flow_selection():
    # Compressor+pump has its minimum at 0.8; adding the fan moves it to 0.6.
    def evaluate(f):
        pair = 10 + (f - 0.8) ** 2
        fan = 1 + (f - 0.4) ** 2
        return {
            "converged": True,
            "hx_feasible": True,
            "ground_flow_ratio": f,
            "E_cmp_plus_pmp [W]": pair,
            "E_iu_fan [W]": fan,
            "E_tot [W]": pair + fan,
        }

    row = select_ground_flow(evaluate, dict(control="optimal_power", min_ratio=0.4, max_ratio=1.0))
    assert row["ground_flow_ratio"] == pytest.approx(0.6, abs=1e-3)
    assert row["E_tot [W]"] < evaluate(0.8)["E_tot [W]"]
    assert row["E_cmp_plus_pmp [W]"] > evaluate(0.8)["E_cmp_plus_pmp [W]"]


@pytest.mark.parametrize(
    "cls,expected",
    [
        (GroundSourceHeatPump, (261.6148755000496, 91.50563034769912, 387.1576112468979)),
        (GroundSourceHeatPumpBoiler, (655.3080480718354, 91.50563034769912, 746.8136784195345)),
    ],
)
def test_constant_flow_regression_before_total_power_policy(cls, expected):
    # Recorded from commit 4197de8 with this fixture's unchanged component physics.
    row = _run(_model(cls))
    assert row["converged"]
    for key, value in zip(("E_cmp [W]", "E_pmp [W]", "E_tot [W]"), expected, strict=True):
        assert row[key] == pytest.approx(value, rel=1e-8)
    assert row["cop_sys [-]"] == pytest.approx(4000 / row["E_tot [W]"])


@pytest.mark.parametrize("load,room,approach", [(4000, 26, -10), (-4000, 20, 10)])
def test_room_temperature_is_shared_by_cycle_and_output_without_overwriting_default(load, room, approach):
    model = _model(GroundSourceHeatPump, T_a_room=room)
    row = model._calc_state(10, 10, load, 7, room)
    assert row is not None
    assert model.T_a_room == row["T_a_room [°C]"] == room
    key = "T_ref_evap_sat [°C]" if load > 0 else "T_ref_cond_sat_l [°C]"
    assert row[key] == pytest.approx(room + approach)
    assert model.T_r_iu == pytest.approx(row[key])
    override = model._calc_state(10, 10, load, 7, room + 1)
    assert override is not None and override["T_a_room [°C]"] == room + 1
    assert override[key] == pytest.approx(room + 1 + approach)
    assert model.T_a_room == room


def test_real_boiler_optimum_is_no_worse_than_same_physics_constant_flow():
    model = _model(GroundSourceHeatPumpBoiler, ground_flow_control="optimal_power")
    baseline = _run(model, ratio=1)
    optimal = _run(model, ratio=None)
    assert baseline["converged"] and optimal["converged"]
    assert optimal["E_cmp_plus_pmp [W]"] <= baseline["E_cmp_plus_pmp [W]"] + 1e-6
    for f in np.linspace(0.4, 1.2, 5):
        row = _run(model, ratio=float(f))
        if row["converged"]:
            assert optimal["E_cmp_plus_pmp [W]"] <= row["E_cmp_plus_pmp [W]"] + 0.5


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(ground_flow_control="plr"),
        dict(dV_b_f_lpm=0),
        dict(pump_efficiency=0),
        dict(ground_flow_min_ratio=1.2),
        dict(m_dot_ref_rated=0),
        dict(R_b=0.1),
    ],
)
def test_invalid_ground_configuration(kwargs):
    with pytest.raises(ValueError):
        _model(GroundSourceHeatPump, **kwargs)


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
def test_off_state_uses_no_flow_or_power(cls):
    row = _run(_model(cls), load=0)
    assert row["converged"] and not row["hp_is_on"]
    for key in ("E_tot [W]", "E_cmp [W]", "E_pmp [W]", "dV_bhe_f [m3/s]", "m_dot_borehole [kg/s]"):
        assert row[key] == 0


def test_cooling_heat_rejection_balance():
    model = _model(GroundSourceHeatPump)
    row = model.analyze_steady(Q_r_iu=4000, T0=30, T_a_room=26, T_bhe_wall=20, ground_flow_ratio=0.8)
    assert row["converged"], row["failure_reason"]
    assert row["Q_bhe [W]"] == pytest.approx(-row["Q_ref_ground [W]"] - row["E_pmp [W]"])
    assert row["T_bhe_f [°C]"] > row["T_bhe [°C]"]


def test_preview_does_not_commit_optimizer_candidates():
    from tmhp.ground_coupling import AggregateGFunctionCoupler

    coupler = AggregateGFunctionCoupler(lambda t: np.sqrt(t + 1) * 0.01)
    time = np.array([0.0, 3600.0, 7200.0])
    coupler.reset(3, time)
    coupler.wall_temperature_rise(0, time, 10)
    before = coupler._pulses.copy()
    for q in [5.0, 50.0, -20.0, 10.0]:
        coupler.preview_wall_temperature_rise(1, time, q)
        np.testing.assert_array_equal(coupler._pulses, before)
        assert coupler._q_old == 10
    expected = coupler.preview_wall_temperature_rise(1, time, 20)
    assert coupler.wall_temperature_rise(1, time, 20) == pytest.approx(expected)


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
def test_dynamic_hx_uses_current_wall_and_selected_flow(cls):
    from tmhp.heat_exchanger import calc_phase_change_hx_capacity

    model = _model(cls, hp_capacity=4000)
    if cls is GroundSourceHeatPump:
        frame = model.analyze_dynamic(7200, 3600, [-4000, -4000], [7, 7], [20, 20])
    else:
        frame = model.analyze_dynamic(7200, 3600, 40, [0, 0], [7, 7])
    assert frame.iloc[0]["converged"]
    for _, row in frame.iterrows():
        if not row["hp_is_on"]:
            continue
        mass = row["dV_bhe_f [m3/s]"] * rho_w
        source = row["T_bhe_f_out [°C]"] + row["E_pmp [W]"] / (mass * c_w)
        available = calc_phase_change_hx_capacity(row["UA_ground [W/K]"], mass, c_w, source, row["T_ref_evap_sat [°C]"])
        assert available == pytest.approx(row["Q_ref_required [W]"], abs=0.1)


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
def test_speed_limit_is_distinct_from_hx_limit(cls):
    row = _run(_model(cls, rps_min=100, rps_max=150))
    assert not row["converged"]
    assert row["failure_reason"] == "compressor_min_speed"
    assert math.isnan(row["cop_sys [-]"])


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
def test_pressure_limit_diagnostic(cls):
    row = _run(_model(cls, PR_cycle_min=1.0, PR_cycle_max=1.01))
    assert not row["converged"]
    assert row["failure_reason"] == "pressure_ratio_limit"


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
def test_dynamic_invalid_cycle_preserves_failure_diagnostic(cls):
    model = _model(cls, hp_capacity=4000, PR_cycle_min=1.0, PR_cycle_max=1.01)
    if cls is GroundSourceHeatPump:
        frame = model.analyze_dynamic(7200, 3600, [-4000, -4000], [7, 7], [20, 20])
    else:
        frame = model.analyze_dynamic(7200, 3600, 40, [0, 0], [7, 7])
    assert not frame["converged"].any()
    assert (frame["failure_reason"] == "pressure_ratio_limit").all()
    assert (frame["E_cmp [W]"] == 0).all()
    assert frame["cop_sys [-]"].isna().all()
