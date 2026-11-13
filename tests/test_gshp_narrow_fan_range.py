"""GSHP with a narrow indoor fan range: feasible duties must still be solved.

A narrow (or nearly fixed) fan range makes the band of serviceable indoor
approaches narrower than any grid spacing. The solver must find it from the
signed coil capacity margins at the fan flow limits, not from grid samples
(PR #99 review: 0.79-0.80 m3/s could not deliver 3 kW heating it can deliver).
"""

import math

import pytest

from tmhp import GroundSourceHeatPump

# The cases are built around an indoor unit with a 0.8 m3/s reference airflow
# (the default before the 400 cfm/ton rule), so the fan range is given
# explicitly: at the present default the 4 kW coil runs at its largest airflow.
GROUND = dict(ref="R32", R_b=0.12, t_max_s=3600, dV_iu_fan_a_rated=0.8)
# (load, room, wall): a heating and a cooling point whose default-range
# optimum has the fan strictly inside its range.
LOADS = {"heating": (-3000.0, 20.0, None), "cooling": (3000.0, 27.0, 16.0)}


def steady(model, load, room, wall=None):
    return model.analyze_steady(Q_r_iu=load, T0=0.0 if load < 0 else 30.0, T_a_room=room, T_bhe_wall=wall)


def air_side_heat(row):
    return row["Q_a_iu_out [W]"] - row["Q_a_iu_in [W]"]


def assert_delivers(row, load):
    assert row["converged"] and row["failure_reason"] == "none", row["failure_reason"]
    assert row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(abs(load), rel=1e-9)
    sign = 1.0 if load < 0 else -1.0
    assert sign * air_side_heat(row) == pytest.approx(row["Q_ref_iu [W]"] + sign * row["E_iu_fan [W]"], rel=1e-6)


@pytest.fixture(scope="module")
def default_rows():
    model = GroundSourceHeatPump(**GROUND)
    return {mode: steady(model, *case) for mode, case in LOADS.items()}


@pytest.mark.parametrize("load", [-3000.0, -2600.0])
def test_review_case_fan_range_0p79_to_0p80_delivers_heating(load):
    # Reference 0.8 m3/s and UA 320 W/K: 3 kW needs an indoor approach of about
    # 11.0-11.1 K at this fan range, between every coarse sample.
    model = GroundSourceHeatPump(ref="R32", dV_iu_fan_a_rated=0.8, dV_iu_fan_a_min=0.79, dV_iu_fan_a_max=0.80)
    row = model.analyze_steady(Q_r_iu=load, T0=0.0, T_a_room=20.0)
    assert_delivers(row, load)
    assert 0.79 - 1e-9 <= row["dV_iu_a [m3/s]"] <= 0.80 + 1e-9


@pytest.mark.parametrize("mode", list(LOADS))
@pytest.mark.parametrize("width,position", [(0.01, 0.5), (0.05, 0.25), (0.20, 0.75)])
def test_narrow_range_around_the_default_optimum_gives_the_same_answer(default_rows, mode, width, position):
    # A range that still contains the default optimum's airflow does not
    # change the optimum: the same point stays feasible and best.
    reference = default_rows[mode]
    assert_delivers(reference, LOADS[mode][0])
    flow = reference["dV_iu_a [m3/s]"]
    low = flow * (1.0 - width * position)
    model = GroundSourceHeatPump(**GROUND, dV_iu_fan_a_min=low, dV_iu_fan_a_max=low + flow * width)
    row = steady(model, *LOADS[mode])
    assert_delivers(row, LOADS[mode][0])
    assert row["dV_iu_a [m3/s]"] == pytest.approx(flow, rel=width)
    for key in ("E_tot [W]", "cop_sys [-]", "E_cmp [W]"):
        assert row[key] == pytest.approx(reference[key], rel=2e-3), key


def test_a_nearly_fixed_fan_is_solved_at_its_single_band_point():
    # Equal limits are rejected by the shared fan validation, so the fixed
    # speed is approximated by a range of 1e-7 relative width.
    flow = 0.75
    model = GroundSourceHeatPump(**GROUND, dV_iu_fan_a_min=flow * (1 - 1e-7), dV_iu_fan_a_max=flow)
    row = steady(model, *LOADS["heating"])
    assert_delivers(row, LOADS["heating"][0])
    assert row["dV_iu_a [m3/s]"] == pytest.approx(flow, rel=1e-6)


def test_narrow_range_ceiling_is_set_by_the_maximum_airflow():
    # Above capacity the ceiling is the indoor coil at its largest airflow and
    # largest approach. Raising only the minimum airflow must not lower it.
    wide = steady(GroundSourceHeatPump(**GROUND), -1.0e6, 20.0)
    narrow = steady(GroundSourceHeatPump(**GROUND, dV_iu_fan_a_min=0.79, dV_iu_fan_a_max=0.80), -1.0e6, 20.0)
    for row in (wide, narrow):
        assert row["converged"] and row["capacity_clamped"] == "max"
        assert math.isfinite(row["cop_sys [-]"])
    assert narrow["Q_max [W]"] == pytest.approx(wide["Q_max [W]"], rel=1e-2)


def test_fan_that_cannot_turn_down_fails_without_a_capacity_search():
    # A fan held at its reference airflow over-delivers a small heating duty
    # at every approach; that is not a capacity limit.
    model = GroundSourceHeatPump(**GROUND, dV_iu_fan_a_min=0.79, dV_iu_fan_a_max=0.80, rps_min=1.0)
    with pytest.warns(RuntimeWarning, match="no operating point"):
        row = steady(model, -300.0, 20.0, 30.0)
    assert not row["converged"] and row["failure_reason"] == "hx_not_converged"
    assert row["Q_ref_iu [W]"] == 0 and row["E_tot [W]"] == 0
