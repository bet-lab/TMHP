"""A request the efficiency floor refuses only because it is small: ``AirSourceHeatPump``.

The envelope refuses a point at which the shipped isentropic fit, read below
its data, is under ``ETA_ISEN_MIN`` (0.30). At a large lift that happens above
the minimum compressor speed: the default R134a unit at -25 °C outdoors and
24 °C indoors runs continuously down to about 0.65 kW and no further. Requests
below that duty came back with the unit switched off
(``failure_reason == "eta_isen_below_min"``: 100-500 W) although 600 W ran.

They are met like a request below the minimum speed: by on/off cycling at the
smallest duty the envelope admits.
"""

import warnings

import numpy as np
import pytest

from tmhp import AirSourceHeatPump
from tmhp.compressor_envelope import ETA_ISEN_BELOW_MIN, ETA_ISEN_MIN
from tmhp.cycling import CAPACITY_RATIO_KEY, MIN_CAPACITY_KEY, PART_LOAD_FACTOR_KEY, RUNTIME_FRACTION_KEY

COLD = dict(T0=-25.0, T_a_room=24.0, verbose=False)


@pytest.fixture(scope="module")
def unit():
    """Frost-free, so that a row's heat is the cycle's (the frost average is checked separately)."""
    return AirSourceHeatPump(ref="R134a", defrost=None)


@pytest.fixture(scope="module")
def row(unit):
    return unit.analyze_steady(Q_r_iu=-200.0, **COLD)


def test_small_request_is_met_by_cycling_at_the_smallest_duty_inside_the_envelope(unit, row):
    assert row["converged"] and row["hp_is_on"] and row["failure_reason"] == "none"
    assert row["capacity_clamped"] == "min"
    assert row["Q_ref_iu [W]"] == pytest.approx(200.0, rel=1e-12)
    assert row["Q_request [W]"] == -200.0 and row["Q_unmet [W]"] == 0.0
    floor = row[MIN_CAPACITY_KEY]
    assert 500.0 < floor < 800.0
    cr, plf = row[CAPACITY_RATIO_KEY], row[PART_LOAD_FACTOR_KEY]
    assert cr == pytest.approx(200.0 / floor, rel=1e-12)
    assert plf == pytest.approx(1.0 - 0.25 * (1.0 - cr), rel=1e-12)
    assert row[RUNTIME_FRACTION_KEY] == pytest.approx(cr / plf, rel=1e-12)
    # The running machine is inside the envelope, on the efficiency floor and above the speed floor.
    assert ETA_ISEN_MIN <= row["eta_cmp_isen [-]"] < ETA_ISEN_MIN + 0.01
    assert row["cmp_rpm [rpm]"] / 60.0 > unit.rps_min + 1.0


def test_the_floor_is_where_continuous_operation_begins(unit, row):
    floor = row[MIN_CAPACITY_KEY]
    above = unit.analyze_steady(Q_r_iu=-1.02 * floor, **COLD)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] is None
    assert above[RUNTIME_FRACTION_KEY] == 1.0
    below = unit.analyze_steady(Q_r_iu=-0.97 * floor, **COLD)
    assert below["capacity_clamped"] == "min" and below[MIN_CAPACITY_KEY] == pytest.approx(floor, rel=1e-2)
    # Input per unit of heat is continuous through the hand-over (the cycling loss vanishes at CR = 1).
    specific_below = below["E_tot [W]"] / below["Q_ref_iu [W]"]
    specific_above = above["E_tot [W]"] / above["Q_ref_iu [W]"]
    assert specific_below == pytest.approx(specific_above, rel=2e-2)
    # And the mean input rises with the request below it.
    assert row["E_tot [W]"] < below["E_tot [W]"] < above["E_tot [W]"]


def test_without_the_cycling_model_the_unit_runs_continuously_at_the_floor(row):
    continuous = AirSourceHeatPump(ref="R134a", defrost=None, Cd_cycling=None).analyze_steady(Q_r_iu=-200.0, **COLD)
    assert continuous["failure_reason"] == "none" and continuous["capacity_clamped"] == "min"
    assert continuous["Q_ref_iu [W]"] == pytest.approx(row[MIN_CAPACITY_KEY], rel=1e-2)  # more than the 200 W asked for
    assert continuous[RUNTIME_FRACTION_KEY] == 1.0 and continuous[CAPACITY_RATIO_KEY] == 1.0


def test_default_unit_with_frost_delivers_the_request_too():
    frosted = AirSourceHeatPump(ref="R134a").analyze_steady(Q_r_iu=-200.0, **COLD)
    assert frosted["failure_reason"] == "none" and frosted["capacity_clamped"] == "min"
    assert frosted["Q_ref_iu [W]"] == pytest.approx(200.0, rel=1e-9)
    assert 0.0 < frosted[RUNTIME_FRACTION_KEY] < 1.0


def test_a_request_no_larger_duty_cures_keeps_its_reason(unit):
    """The floor search only climbs while the efficiency floor is what refuses the duty."""
    calls = []
    original = unit._optimize_operation

    def refused_for_another_limit(Q_r_iu, T0, T_a_room):
        calls.append(abs(Q_r_iu))
        result = original(Q_r_iu=Q_r_iu, T0=T0, T_a_room=T_a_room)
        if len(calls) > 1 and getattr(result, "least_lift_reason", None) == ETA_ISEN_BELOW_MIN:
            result["least_lift_reason"] = "pr_above_max"
        return result

    unit._optimize_operation = refused_for_another_limit
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            refused = unit.analyze_steady(Q_r_iu=-100.0, **COLD)
    finally:
        del unit._optimize_operation
    assert not refused["hp_is_on"] and refused["failure_reason"] == ETA_ISEN_BELOW_MIN
    assert refused["Q_ref_iu [W]"] == 0.0 and refused[RUNTIME_FRACTION_KEY] == 0.0


def test_dynamic_run_searches_the_floor_once_per_condition(unit):
    searches = []
    original = unit._optimize_operation

    def counted(Q_r_iu, T0, T_a_room):
        searches.append(abs(Q_r_iu))
        return original(Q_r_iu=Q_r_iu, T0=T0, T_a_room=T_a_room)

    unit._optimize_operation = counted
    try:
        load = np.array([-200.0, -300.0, -400.0])
        df = unit.analyze_dynamic(3 * 3600, 3600, load, np.full(3, -25.0), np.full(3, 24.0))
    finally:
        del unit._optimize_operation
    assert (df["failure_reason"] == "none").all() and (df["capacity_clamped"] == "min").all()
    assert df["Q_ref_iu [W]"].to_numpy() == pytest.approx(-load, rel=1e-12)
    assert df[MIN_CAPACITY_KEY].nunique() == 1
    # One search of the floor (the first step); the later steps add their own request only.
    after_first = searches[searches.index(300.0) :]
    assert after_first == [300.0, 400.0]
    assert df["E_tot [W]"].is_monotonic_increasing


def test_speed_floor_cycling_is_unchanged():
    """Where the minimum speed is inside the envelope it is still the lower end."""
    mild = AirSourceHeatPump(defrost=None)
    row = mild.analyze_steady(Q_r_iu=-200.0, T0=7.0, T_a_room=20.0, verbose=False)
    assert row["capacity_clamped"] == "min" and row["cmp_rpm [rpm]"] / 60.0 == pytest.approx(mild.rps_min)
