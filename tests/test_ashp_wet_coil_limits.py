"""Limits of the wet indoor coil of ``AirSourceHeatPump`` (#205, review of the first round).

What the capacity ceiling, the smallest continuous duty, a dynamic run and the
exergy columns do when the room humidity is given:

- the ceiling of a weak indoor coil is found, not raised on: the wet coil's
  approach is solved inside the range the search admits, and a room with no
  water vapour is the dry coil as it is;
- the ceiling search does not take a duty whose point it failed to find for
  the ceiling: the region above it that runs is delivered, by
  ``max_capacity`` and by an overloaded ``analyze_steady``;
- a request below the smallest duty the fans' minimum flows leave on the
  pressure-ratio floor is met by cycling at that duty, as one below the
  minimum compressor speed is;
- a humidity schedule is read on cooling rows only;
- a row whose coil removes water has no indoor-coil exergy destruction and no
  efficiency that presupposes a complete exergy balance.
"""

from __future__ import annotations

import warnings

import pytest

from tmhp import AirSourceHeatPump


def _quiet(function, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return function(*args, **kwargs)


@pytest.fixture(scope="module")
def hp():
    return _quiet(AirSourceHeatPump)


# ---------------------------------------------------------------------------
# 1. Ceiling of a weak indoor coil
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def weak():
    return _quiet(AirSourceHeatPump, UA_iu_rated=150.0)


def test_weak_coil_without_water_vapour_has_the_dry_ceiling(weak):
    dry = weak.max_capacity(35.0, 27.0, mode="cooling")
    assert dry["Q_max [W]"] == pytest.approx(3464.751330243249, rel=1e-12) and dry["binding"] == "cycle"
    none = weak.max_capacity(35.0, 27.0, mode="cooling", RH_room=0.0)
    for key in ("Q_max [W]", "dT_ref_evap [K]", "dT_ref_cond [K]", "E_tot [W]", "cop_sys [-]", "binding"):
        assert none[key] == dry[key], key
    assert none["SHR_iu [-]"] == 1.0
    row = weak.analyze_steady(20000.0, 35.0, 27.0, RH_room=0.0, verbose=False)
    reference = weak.analyze_steady(20000.0, 35.0, 27.0, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "max"
    for key in ("Q_ref_iu [W]", "Q_unmet [W]", "E_tot [W]", "T_ref_evap_sat [°C]", "capacity_binding"):
        assert row[key] == reference[key], key
    assert row["Q_iu_latent [W]"] == 0.0


@pytest.mark.parametrize("humidity", [0.3, 0.5, 0.8, 1.0])
def test_weak_coil_ceiling_is_found_at_any_humidity(weak, humidity):
    ceiling = weak.max_capacity(35.0, 27.0, mode="cooling", RH_room=humidity)
    # The latent heat lets the weak coil carry more, up to what the compressor delivers flat out.
    assert ceiling["binding"] == ("cycle" if humidity < 0.6 else "compressor")
    assert ceiling["Q_max [W]"] >= 3464.75
    if ceiling["binding"] == "cycle":
        # The widest approach the search admits: the indoor coil sets the ceiling.
        assert ceiling["dT_ref_evap [K]"] == pytest.approx(weak.dT_approach_bounds[1], rel=1e-6)
    row = weak.analyze_steady(20000.0, 35.0, 27.0, RH_room=humidity, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "max"
    assert row["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)
    assert row["Q_unmet [W]"] == pytest.approx(20000.0 - ceiling["Q_max [W]"], rel=1e-9)


def test_weak_coil_ceiling_at_high_humidity_is_the_reviewed_figure(weak):
    ceiling = weak.max_capacity(35.0, 27.0, mode="cooling", RH_room=0.8)
    assert ceiling["Q_max [W]"] == pytest.approx(7322.25, abs=0.05)
    assert 0.0 < ceiling["SHR_iu [-]"] < 1.0


def test_wet_coil_approach_stays_inside_the_admitted_range(weak):
    lo, hi = weak.dT_approach_bounds
    with weak._solving_with_room_humidity(0.8, 27.0, cooling=True):
        # A duty four times the capacity: the scaled approach (122 K) is far outside any air state.
        assert weak._wet_coil_approach(hi, 4.0, 1.0, 27.0) == hi
        assert weak._wet_coil_approach(10.0, 1e-3, 1.0, 27.0) == lo
        inside = weak._wet_coil_approach(10.0, 1.2, 1.0, 27.0)
        assert lo < inside < hi
        assert weak._wet_coil_drive(inside, 27.0) == pytest.approx(1.2 * weak._wet_coil_drive(10.0, 27.0), rel=1e-12)
    with weak._solving_with_room_humidity(0.0, 27.0, cooling=True):
        assert weak._wet_coil_approach(hi, 4.0, 1.0, 27.0) == 4.0 * hi  # the dry expression, cut by the caller


# ---------------------------------------------------------------------------
# 2. The ceiling search and the region above a duty it could not solve
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def large():
    return _quiet(AirSourceHeatPump, hp_capacity=10000.0)


SATURATED_ROOM = dict(T0=20.0, T_a_room=35.0, RH_room=1.0)


@pytest.fixture(scope="module")
def large_ceiling(large):
    return large.max_capacity(20.0, 35.0, mode="cooling", RH_room=1.0)


def test_ceiling_is_above_every_duty_that_runs(large, large_ceiling):
    # 22.6 kW was reported with binding "cycle_invalid": the least-lift point of one
    # duty in the middle of the ladder was not found, and was taken for the ceiling.
    assert large_ceiling["binding"] == "cycle"
    assert large_ceiling["Q_max [W]"] == pytest.approx(29689.70, rel=2e-3)
    for duty in (22741.05, 26000.0, 0.999 * large_ceiling["Q_max [W]"]):
        row = large.analyze_steady(duty, **SATURATED_ROOM, verbose=False)
        assert row["failure_reason"] == "none" and row["capacity_clamped"] is None, duty
        assert row["Q_ref_iu [W]"] == pytest.approx(duty, rel=1e-9) and row["Q_unmet [W]"] == 0.0


def test_request_above_the_ceiling_runs_at_it(large, large_ceiling):
    row = large.analyze_steady(30000.0, **SATURATED_ROOM, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "max"
    assert row["capacity_binding"] == "cycle"
    assert row["Q_ref_iu [W]"] == pytest.approx(large_ceiling["Q_max [W]"], rel=1e-9)
    assert row["Q_unmet [W]"] == pytest.approx(30000.0 - row["Q_ref_iu [W]"], rel=1e-9)
    assert row["Q_iu_sensible [W]"] + row["Q_iu_latent [W]"] == pytest.approx(row["Q_ref_iu [W]"], rel=2e-5)


def test_least_lift_point_on_the_pressure_ratio_floor_is_found(large):
    # The outdoor coil at its largest airflow is 0.8 % short of this duty at the condensing
    # temperature the pressure-ratio floor holds (41.4 degC, 21.4 K above the outdoor air),
    # whatever smaller approach is asked for: the approach that closes the coil is 21.6 K.
    with large._solving_with_room_humidity(1.0, 35.0, cooling=True):
        x, state, reason = large._largest_airflow_point(22741.05, 20.0, 35.0)
    assert reason is None and state is not None and x is not None
    assert state["Q_ref_iu [W]"] == pytest.approx(22741.05, rel=1e-9)
    assert x[1] == pytest.approx(21.6, abs=0.1)
    assert state["pr_cmp [-]"] > large.PR_cycle_min


# ---------------------------------------------------------------------------
# 4. Below the smallest duty the fans' minimum flows leave on the pressure-ratio floor
# ---------------------------------------------------------------------------

MILD = dict(T0=15.0, T_a_room=27.0)


def test_request_below_the_smallest_continuous_wet_duty_is_met_by_cycling(hp):
    dry = hp.analyze_steady(1200.0, **MILD, verbose=False)
    assert dry["failure_reason"] == "none" and dry["capacity_clamped"] is None
    assert dry["pr_cmp [-]"] == pytest.approx(hp.PR_cycle_min, rel=1e-9)

    row = hp.analyze_steady(1200.0, **MILD, RH_room=0.8, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
    assert row["Q_ref_iu [W]"] == pytest.approx(1200.0, rel=1e-9) and row["Q_unmet [W]"] == 0.0
    # The smallest duty that runs continuously: both fans near their smallest flows, the ratio on its floor.
    assert row["Q_min [W]"] == pytest.approx(1350.0, rel=5e-3)
    assert row["pr_cmp [-]"] == pytest.approx(hp.PR_cycle_min, rel=1e-9)
    ratio = 1200.0 / row["Q_min [W]"]
    assert row["capacity_ratio [-]"] == pytest.approx(ratio, rel=1e-9)
    assert row["runtime_fraction [-]"] == pytest.approx(ratio / hp.cycling.part_load_factor(ratio), rel=1e-9)
    assert row["runtime_fraction [-]"] == pytest.approx(0.9143, abs=5e-3)
    assert row["Q_iu_latent [W]"] > 0.0
    assert row["Q_iu_sensible [W]"] + row["Q_iu_latent [W]"] == pytest.approx(1200.0, rel=2e-5)


def test_the_smallest_continuous_wet_duty_itself_runs_continuously(hp):
    floor = hp.analyze_steady(1200.0, **MILD, RH_room=0.8, verbose=False)["Q_min [W]"]
    above = hp.analyze_steady(1.01 * floor, **MILD, RH_room=0.8, verbose=False)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] is None
    assert above["Q_ref_iu [W]"] == pytest.approx(1.01 * floor, rel=1e-9)
    assert above["runtime_fraction [-]"] == 1.0


@pytest.mark.parametrize("load", [200.0, 600.0, 1200.0])
def test_small_wet_requests_are_delivered_in_mild_weather(hp, load):
    row = hp.analyze_steady(load, **MILD, RH_room=0.9, verbose=False)
    assert row["failure_reason"] == "none", row["failure_reason"]
    assert row["Q_ref_iu [W]"] == pytest.approx(load, rel=1e-9) and row["Q_unmet [W]"] == 0.0


def test_without_cycling_the_unit_runs_at_its_smallest_continuous_wet_duty():
    steady = _quiet(AirSourceHeatPump, Cd_cycling=None)
    row = steady.analyze_steady(1200.0, **MILD, RH_room=0.8, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
    assert row["Q_ref_iu [W]"] == pytest.approx(1350.0, rel=5e-3) and row["Q_unmet [W]"] == 0.0
