"""GSHP standard solver: converged rated points, explicit failures, wall coupling.

Regression for the default ``GroundSourceHeatPump`` returning
``converged=False`` points with heat and power (issue #96, §1.3/§2): a fixed
start Nelder-Mead stuck on a flat penalty, a raw-power objective that picked the
starved coil at minimum speed, and ``T_bhe_wall`` ignored outside the coupled
ground-flow solver.
"""

import math

import pytest

from tmhp import GroundSourceHeatPump
from tmhp.constants import c_w, rho_w
from tmhp.ground_loop import calc_bhe_fluid_temperatures


@pytest.fixture(scope="module")
def hp():
    return GroundSourceHeatPump(ref="R32")


def steady(model, load, room, wall=None):
    T0 = 0.0 if load < 0 else 30.0
    return model.analyze_steady(Q_r_iu=load, T0=T0, T_a_room=room, T_bhe_wall=wall)


def air_side_heat(row):
    return row["Q_a_iu_out [W]"] - row["Q_a_iu_in [W]"]


# Near-rating points of the default 4 kW unit: heating from a 0/10/16 degC
# ground, cooling into a 16/25 degC ground. ``None`` keeps the constructor Ts.
RATED = [
    (-4000.0, 20.0, 0.0),
    (-4000.0, 20.0, None),
    (-2400.0, 20.0, 10.0),
    (4000.0, 27.0, 25.0),
    (2400.0, 27.0, 16.0),
]


@pytest.mark.parametrize("load,room,wall", RATED)
def test_default_unit_converges_near_rating(hp, load, room, wall):
    row = steady(hp, load, room, wall)
    assert row["converged"] and row["failure_reason"] == "none"
    assert row["hp_is_on"] and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(abs(load), rel=1e-9)
    # Both heat balances close: the indoor coil delivers the refrigerant duty
    # (plus fan heat in heating, minus it in cooling) and the ground HX carries
    # the ground-side duty at the solved approach.
    sign = 1.0 if load < 0 else -1.0
    expected_air = row["Q_ref_iu [W]"] + sign * row["E_iu_fan [W]"]
    assert sign * air_side_heat(row) == pytest.approx(expected_air, rel=1e-2)
    available, required = row["Q_HX_available [W]"], row["Q_ref_ground [W]"]
    if row["ground_balance"] == "equality":
        assert available == pytest.approx(required, rel=1e-4)
    else:
        # Spare ground-HX capacity only at the cooling pressure-ratio floor
        # (head-pressure control) or at the smallest searched approach.
        assert available > required
        assert row["pr_floor_active"] or row["ground_approach [K]"] == pytest.approx(1.0)
    assert 1.0 < row["cop_sys [-]"] < row["cop_ref [-]"]
    assert row["T_ref_evap_sat [°C]"] < row["T_ref_cond_sat_l [°C]"]


def test_reported_failure_point_now_converges(hp):
    # The diagnosis input: formerly hx_not_converged with Q 3,000 W on the
    # refrigerant side but only 1,592 W on the air side.
    row = hp.analyze_steady(Q_r_iu=-3000.0, T0=0.0, T_a_room=20.0)
    assert row["converged"] and row["failure_reason"] == "none"
    assert air_side_heat(row) == pytest.approx(row["Q_ref_iu [W]"] + row["E_iu_fan [W]"], rel=1e-6)


def test_low_cooling_requests_are_met_or_flagged_at_minimum_speed(hp):
    rows = [steady(hp, load, 27.0, 10.0) for load in (1000.0, 2000.0)]
    for load, row in zip((1000.0, 2000.0), rows, strict=True):
        assert row["converged"]
        # Met exactly either way: in continuous operation, or as the mean of
        # on/off cycling at the minimum speed (tmhp.cycling), flagged "min".
        assert row["Q_ref_iu [W]"] == pytest.approx(load, rel=1e-9)
        if row["capacity_clamped"] == "min":
            assert row["Q_min [W]"] > load
            assert 0.0 < row["runtime_fraction [-]"] < 1.0
        else:
            assert row["capacity_clamped"] is None
            assert row["runtime_fraction [-]"] == 1.0
    assert rows[0]["capacity_clamped"] == "min"


def test_wall_temperature_is_used_and_closes_through_borehole_resistance(hp):
    cold, warm = (steady(hp, -3000.0, 20.0, wall) for wall in (5.0, 25.0))
    for wall, row in ((5.0, cold), (25.0, warm)):
        assert row["converged"]
        assert row["T_bhe [°C]"] == pytest.approx(wall, abs=1e-4)
        _, inlet, outlet = calc_bhe_fluid_temperatures(
            wall,
            row["Q_bhe [W]"],
            hp.n_boreholes,
            hp.H_b,
            row["R_b_eff [mK/W]"],
            row["dV_bhe_f [m3/s]"] * rho_w,
            c_w,
        )
        assert row["T_bhe_f_out [°C]"] == pytest.approx(outlet, abs=1e-4)
        assert row["T_bhe_f_in [°C]"] == pytest.approx(inlet, abs=1e-4)
        # Heat extraction cools the fluid below the wall.
        assert row["T_bhe_f_out [°C]"] < wall
    assert warm["T_ref_evap_sat [°C]"] > cold["T_ref_evap_sat [°C]"]
    assert warm["cop_ref [-]"] > cold["cop_ref [-]"]
    assert warm["cop_sys [-]"] > cold["cop_sys [-]"]


def test_wall_closure_matches_coupled_ground_flow_solver():
    # Same unit, same wall and flow: the standard solver and the coupled
    # ground-flow solver (selected by prescribing the loop flow) solve the same
    # balances and must agree.
    model = GroundSourceHeatPump(ref="R32", R_b=0.12, t_max_s=3600)
    standard = steady(model, -3000.0, 20.0, 10.0)
    coupled = model.analyze_steady(
        Q_r_iu=-3000.0, T0=0.0, T_a_room=20.0, T_bhe_wall=10.0, ground_flow_lpm=model.ground_flow_ref_lpm
    )
    assert standard["converged"] and coupled["converged"]
    for key in ("T_bhe_f_out [°C]", "T_bhe_f_in [°C]", "T_ref_evap_sat [°C]"):
        assert standard[key] == pytest.approx(coupled[key], abs=2e-3), key
    for key in ("E_tot [W]", "cop_sys [-]", "Q_bhe [W]"):
        assert standard[key] == pytest.approx(coupled[key], rel=2e-3), key


def test_without_wall_the_constructor_ground_temperature_is_kept():
    cold = GroundSourceHeatPump(ref="R32", Ts=5.0, R_b=0.12, t_max_s=3600)
    row = steady(cold, -3000.0, 20.0)
    assert row["converged"]
    assert row["T_bhe_f_out [°C]"] == pytest.approx(5.0, abs=1e-9)


def test_request_above_capacity_reports_the_ceiling(hp):
    # The diagnosis input: formerly Q 1,000,000 W and E_cmp 986,685 W.
    row = hp.analyze_steady(Q_r_iu=-1.0e6, T0=0.0, T_a_room=20.0)
    assert row["converged"] and row["failure_reason"] == "none"
    assert row["capacity_clamped"] == "max"
    assert row["capacity_binding"] in {"compressor", "cycle"}
    assert row["Q_request [W]"] == -1.0e6
    assert 0 < row["Q_ref_iu [W]"] == pytest.approx(row["Q_max [W]"], rel=1e-9)
    assert row["Q_ref_iu [W]"] < 5 * hp.hp_capacity
    assert 1.0 < row["cop_sys [-]"] < 10.0


def test_wall_coupled_ceiling_closes_at_the_compressor_speed_limit():
    # With the efficiency guard the duty at rps_max rises steeply with the
    # source temperature, so the fluid closure against the wall oscillates
    # (loop gain near -0.5) and the PR-envelope edge moves with the fluid
    # temperature. R410A at a 10 degC wall used to end cycle_invalid here.
    model = GroundSourceHeatPump(ref="R410A")
    row = steady(model, -1.0e6, 20.0, 10.0)
    assert row["converged"] and row["failure_reason"] == "none"
    assert row["capacity_clamped"] == "max"
    assert 0 < row["Q_ref_iu [W]"] == pytest.approx(row["Q_max [W]"], rel=1e-9)
    assert row["T_bhe [°C]"] == pytest.approx(10.0, abs=1e-4)
    assert 1.0 < row["cop_sys [-]"] < 10.0


def test_unsolvable_point_returns_off_state_with_reason():
    # A pressure-ratio ceiling just above the floor leaves no valid cycle for
    # a 0 -> 20 degC lift.
    model = GroundSourceHeatPump(ref="R32", Ts=0.0, PR_cycle_max=1.6, R_b=0.12, t_max_s=3600)
    with pytest.warns(RuntimeWarning, match="no operating point"):
        row = steady(model, -3000.0, 20.0)
    assert not row["converged"] and not row["hp_is_on"]
    assert row["failure_reason"] == "pr_above_max"
    for key in ("Q_ref_iu [W]", "Q_ref_ground [W]", "E_cmp [W]", "E_tot [W]", "E_iu_fan [W]", "E_pmp [W]"):
        assert row[key] == 0, key
    assert air_side_heat(row) == 0
    assert math.isnan(row["cop_ref [-]"]) and math.isnan(row["cop_sys [-]"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"Q_r_iu": math.nan},
        {"Q_r_iu": math.inf},
        {"T0": math.nan},
        {"T_a_room": math.nan},
        {"T_bhe_wall": math.nan},
        {"T_bhe_wall": -math.inf},
    ],
)
def test_non_finite_inputs_are_rejected(hp, kwargs):
    inputs = {"Q_r_iu": -3000.0, "T0": 0.0, "T_a_room": 20.0, **kwargs}
    with pytest.raises(ValueError, match="finite"):
        hp.analyze_steady(**inputs)


def test_non_finite_dynamic_schedule_is_rejected(hp):
    with pytest.raises(ValueError, match="finite"):
        hp.analyze_dynamic(2 * 3600, 3600, [-3000.0, math.nan], [0.0, 0.0])
