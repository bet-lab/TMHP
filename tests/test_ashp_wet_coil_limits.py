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

import math
import warnings
from decimal import Decimal
from fractions import Fraction

import numpy as np
import pandas as pd
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


def _fake_trial(limit: float, lost: tuple[float, float], found_from_a_start: bool = False):
    """A least-lift solve for the search: runs below ``limit``, a coil beyond it, and not found inside ``lost``.

    Inside ``lost`` the point exists but the solve does not find it ("cycle_invalid", no approaches) --
    from any start, or only without one when ``found_from_a_start``.
    """
    calls: list[tuple[float, object]] = []

    def trial(q, start=None):
        calls.append((q, start))
        if q > limit:
            return (15.0, 30.0), None, "air_side"
        if lost[0] <= q <= lost[1] and not (found_from_a_start and start is not None):
            return None, None, "cycle_invalid"
        return (q / 2000.0, q / 1000.0), {"Q_ref_iu [W]": q}, None

    return trial, calls


def test_wet_ceiling_search_looks_above_a_duty_it_could_not_solve(large):
    # The ladder's rung at 45 % falls in the band that is not found; the bisection closes under it.
    trial, _ = _fake_trial(limit=29_700.0, lost=(22_000.0, 23_500.0))
    x, state, reason = large._largest_deliverable_wet_duty(trial, 50_000.0, 1e-3)
    assert reason == "air_side" and x is not None
    assert state["Q_ref_iu [W]"] == pytest.approx(29_700.0, rel=1.5e-3) and state["Q_ref_iu [W]"] <= 29_700.0


def test_wet_ceiling_search_retries_a_lost_duty_from_other_starts(large):
    trial, calls = _fake_trial(limit=29_700.0, lost=(22_000.0, 23_500.0), found_from_a_start=True)
    x, state, reason = large._largest_deliverable_wet_duty(trial, 50_000.0, 1e-3)
    assert reason == "air_side"
    assert state["Q_ref_iu [W]"] == pytest.approx(29_700.0, rel=1.5e-3)
    # The rung at 45 % (22.5 kW) ran once it was started from the approaches of the refused rung above.
    assert (22_500.0, (15.0, 30.0)) in calls


def test_wet_ceiling_search_reports_what_it_cannot_explain(large):
    # Nothing is found from 22 kW up to the top: no limit is named, and none is made up.
    trial, _ = _fake_trial(limit=60_000.0, lost=(22_000.0, 60_000.0))
    x, state, reason = large._largest_deliverable_wet_duty(trial, 50_000.0, 1e-3)
    assert reason == "cycle_invalid"
    assert state["Q_ref_iu [W]"] == pytest.approx(22_000.0, rel=1.5e-3) and state["Q_ref_iu [W]"] < 22_000.0
    assert large._binding_of(reason) == "cycle_invalid"


def test_wet_ceiling_search_takes_the_top_when_it_runs(large):
    trial, calls = _fake_trial(limit=60_000.0, lost=(0.0, -1.0))
    x, state, reason = large._largest_deliverable_wet_duty(trial, 50_000.0, 1e-3)
    assert reason is None and state["Q_ref_iu [W]"] == 50_000.0 and len(calls) == 1


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


def test_fans_that_leave_no_continuous_duty_are_named_as_a_coil_limit():
    # Both fans held within a few percent of their largest flow: in cold weather the coils carry
    # more than any duty the compressor can be turned down to, at every approach, so no larger
    # duty runs either. The row says a coil cannot carry the duty inside its flow range --
    # not that the cycle or the search failed.
    stiff = _quiet(
        AirSourceHeatPump,
        dV_ou_fan_a_min=0.55,
        dV_ou_fan_a_max=0.6,
        dV_ou_fan_a_rated=0.6,
        dV_iu_fan_a_min=0.19,
        dV_iu_fan_a_max=0.2,
        dV_iu_fan_a_rated=0.2,
    )
    row = stiff.analyze_steady(200.0, 5.0, 27.0, RH_room=0.9, verbose=False)
    assert row["failure_reason"] == "hx_not_converged"
    assert row["Q_ref_iu [W]"] == 0.0 and row["Q_unmet [W]"] == 200.0 and row["runtime_fraction [-]"] == 0.0
    # The dry coil does not run there either.
    assert stiff.analyze_steady(200.0, 5.0, 27.0, verbose=False)["failure_reason"] != "none"


# ---------------------------------------------------------------------------
# 7. A humidity schedule is read on cooling rows only
# ---------------------------------------------------------------------------


def test_humidity_schedule_is_not_read_on_heating_and_off_rows(hp):
    nan = float("nan")
    loads, outdoor, room = [0.0, -2000.0, 2000.0], [35.0, 7.0, 35.0], [27.0, 20.0, 27.0]
    frame = _quiet(hp.analyze_dynamic, 3 * 3600, 3600, loads, outdoor, room, RH_room_schedule=[nan, nan, 0.6])
    reference = _quiet(hp.analyze_dynamic, 3 * 3600, 3600, loads, outdoor, room)
    assert list(frame["failure_reason"]) == ["none", "none", "none"]
    for key in ("Q_ref_iu [W]", "E_tot [W]", "cop_sys [-]"):
        for n in (0, 1):
            got, want = frame[key].iloc[n], reference[key].iloc[n]
            assert (math.isnan(got) and math.isnan(want)) or got == want, (key, n)
    assert frame["RH_room [-]"].iloc[:2].isna().all() and frame["RH_room [-]"].iloc[2] == 0.6
    assert frame["Q_iu_latent [W]"].iloc[2] > 0.0
    # Values no humidity can take are not read there either.
    _quiet(hp.analyze_dynamic, 2 * 3600, 3600, loads[:2], outdoor[:2], room[:2], RH_room_schedule=[-1.0, 50.0])


@pytest.mark.parametrize("value", [float("nan"), -0.1, 1.5])
def test_humidity_schedule_is_checked_on_cooling_rows(hp, value):
    with pytest.raises(ValueError, match="Room relative humidity must be a fraction"):
        _quiet(
            hp.analyze_dynamic, 2 * 3600, 3600, [0.0, 2000.0], [35.0, 35.0], [27.0, 27.0], RH_room_schedule=[0.5, value]
        )


@pytest.mark.parametrize("missing", [None, "n/a", "", pd.NA, float("inf"), np.float32("nan"), object()])
def test_any_value_is_left_unread_on_heating_and_off_rows(hp, missing):
    """Not only NaN: a schedule read from a file holds whatever the file has where there is no cooling."""
    loads, outdoor, room = [0.0, -2000.0, 2000.0], [35.0, 7.0, 35.0], [27.0, 20.0, 27.0]
    frame = _quiet(hp.analyze_dynamic, 3 * 3600, 3600, loads, outdoor, room, RH_room_schedule=[missing, missing, 0.6])
    assert list(frame["failure_reason"]) == ["none", "none", "none"]
    assert frame["RH_room [-]"].iloc[:2].isna().all() and frame["RH_room [-]"].iloc[2] == 0.6


def test_humidity_schedule_takes_a_column_with_missing_values(hp):
    loads, outdoor, room = [0.0, -2000.0, 2000.0], [35.0, 7.0, 35.0], [27.0, 20.0, 27.0]
    column = pd.Series([pd.NA, pd.NA, 0.6], dtype="Float64")
    frame = _quiet(hp.analyze_dynamic, 3 * 3600, 3600, loads, outdoor, room, RH_room_schedule=column)
    assert frame["RH_room [-]"].iloc[2] == 0.6 and frame["Q_iu_latent [W]"].iloc[2] > 0.0
    as_numbers = _quiet(
        hp.analyze_dynamic, 3 * 3600, 3600, loads, outdoor, room, RH_room_schedule=np.array([0.1, 0.2, 0.6])
    )
    assert as_numbers["E_tot [W]"].iloc[2] == frame["E_tot [W]"].iloc[2]


@pytest.mark.parametrize("value", [None, "n/a", pd.NA, True, np.float32("nan")])
def test_a_cooling_row_needs_a_number_for_its_humidity(hp, value):
    with pytest.raises(ValueError, match=r"Room relative humidity must be a fraction in \[0, 1\], got .* at step 1"):
        _quiet(
            hp.analyze_dynamic, 2 * 3600, 3600, [0.0, 2000.0], [35.0, 35.0], [27.0, 27.0], RH_room_schedule=[0.5, value]
        )


def test_a_number_of_another_type_is_read_on_a_cooling_row(hp):
    arguments = (3600, 3600, [2000.0], [35.0], [27.0])
    reference = _quiet(hp.analyze_dynamic, *arguments, RH_room_schedule=[0.5])
    for value in (np.float32(0.5), np.float64(0.5), "0.5"):
        frame = _quiet(hp.analyze_dynamic, *arguments, RH_room_schedule=[value])
        assert frame["RH_room [-]"].iloc[0] == 0.5 and frame["E_tot [W]"].iloc[0] == reference["E_tot [W]"].iloc[0]
    whole = _quiet(hp.analyze_dynamic, *arguments, RH_room_schedule=[1])
    assert whole["RH_room [-]"].iloc[0] == 1.0
    # The single point takes the same numeric types (text is for a schedule read from a file).
    for value in (np.float32(0.5), np.float64(0.5)):
        row = hp.analyze_steady(2000.0, 35.0, 27.0, RH_room=value, verbose=False)
        assert row["RH_room [-]"] == 0.5 and row["E_tot [W]"] == reference["E_tot [W]"].iloc[0]
        assert hp.max_capacity(35.0, 27.0, mode="cooling", RH_room=value)["SHR_iu [-]"] < 1.0


@pytest.mark.parametrize(
    "entry",
    [
        Decimal("0.5"),
        Fraction(1, 2),
        b"0.5",
        np.bytes_(b"0.5"),
        np.str_("0.5"),
        " 0.5 ",
        np.array(0.5),
        np.float16(0.5),
        np.int64(1),
    ],
    ids=["Decimal", "Fraction", "bytes", "np.bytes_", "np.str_", "padded text", "0-d array", "float16", "int64"],
)
def test_an_entry_that_reads_as_one_real_number_is_a_humidity(hp, entry):
    """Whatever a float conversion of a single value reads: a column from a database or a file holds such things."""
    schedule = np.empty(1, dtype=object)
    schedule[0] = entry
    frame = _quiet(hp.analyze_dynamic, 3600, 3600, [2000.0], [35.0], [27.0], RH_room_schedule=schedule)
    assert frame["RH_room [-]"].iloc[0] == float(entry)
    # Given the usual way, too.
    usual = _quiet(hp.analyze_dynamic, 3600, 3600, [2000.0], [35.0], [27.0], RH_room_schedule=[entry])
    assert usual["RH_room [-]"].iloc[0] == float(entry)


def test_a_column_of_decimals_is_a_humidity_schedule(hp):
    column = pd.Series([Decimal("0.5"), Decimal("0.6")])
    frame = _quiet(hp.analyze_dynamic, *COOLING_TWICE, RH_room_schedule=column)
    assert list(frame["RH_room [-]"]) == [0.5, 0.6]


@pytest.mark.parametrize("entry", [np.array([0.5]), [0.5, 0.6], 0.5 + 0j, object(), np.array(float("nan"))])
def test_an_entry_that_is_not_one_real_number_is_refused_on_a_cooling_row(hp, entry):
    schedule = np.empty(1, dtype=object)
    schedule[0] = entry
    with pytest.raises(ValueError, match=r"Room relative humidity must be a fraction in \[0, 1\], got .* at step 0"):
        _quiet(hp.analyze_dynamic, 3600, 3600, [2000.0], [35.0], [27.0], RH_room_schedule=schedule)


@pytest.mark.parametrize("unused", [float("nan"), -1.0, 50.0, "n/a", pd.NA, True])
def test_a_steady_heating_or_off_point_does_not_read_the_room_humidity(hp, unused):
    """As a dynamic run: a loop over rows passes whatever the row holds, and only a cooling row reads it."""
    for load, outdoor, room in ((-2000.0, 7.0, 20.0), (0.0, 35.0, 27.0)):
        row = hp.analyze_steady(load, outdoor, room, RH_room=unused, verbose=False)
        reference = hp.analyze_steady(load, outdoor, room, verbose=False)
        assert row["failure_reason"] == "none" and math.isnan(row["RH_room [-]"])
        for key in ("Q_ref_iu [W]", "E_tot [W]", "T_iu_a_out [°C]", "cop_sys [-]"):
            both_nan = math.isnan(row[key]) and math.isnan(reference[key])
            assert both_nan or row[key] == reference[key], key
    ceiling = hp.max_capacity(7.0, 20.0, mode="heating", RH_room=unused)
    assert ceiling["Q_max [W]"] == hp.max_capacity(7.0, 20.0, mode="heating")["Q_max [W]"]
    with pytest.raises(ValueError, match="Room relative humidity must be a fraction"):
        hp.analyze_steady(2000.0, 35.0, 27.0, RH_room=unused, verbose=False)
    with pytest.raises(ValueError, match="Room relative humidity must be a fraction"):
        hp.max_capacity(35.0, 27.0, mode="cooling", RH_room=unused)


COOLING_TWICE = (2 * 3600, 3600, [2000.0, 2000.0], [35.0, 35.0], [27.0, 27.0])


@pytest.mark.parametrize(
    "schedule",
    [
        [0.5, 0.6],
        (0.5, 0.6),
        np.array([0.5, 0.6]),
        np.array([[0.5], [0.6]]),
        np.array([[[0.5]], [[0.6]]]),
        pd.Series([0.5, 0.6], index=[7, 9]),
        # A frame is its values, never its column names -- also where a name is itself a number.
        pd.DataFrame({"RH": [0.5, 0.6]}),
        pd.DataFrame({0.9: [0.5, 0.6]}),
        (value for value in (0.5, 0.6)),
    ],
    ids=[
        "list",
        "tuple",
        "array",
        "column array",
        "column of columns",
        "series",
        "frame",
        "frame named by a number",
        "generator",
    ],
)
def test_humidity_schedule_is_read_by_value_one_entry_a_step(hp, schedule):
    frame = _quiet(hp.analyze_dynamic, *COOLING_TWICE, RH_room_schedule=schedule)
    assert list(frame["RH_room [-]"]) == [0.5, 0.6]


@pytest.mark.parametrize(
    "schedule, says",
    [
        # Keys and column names are not humidities: these were read as 0 and 1, and as 0.3 and 0.4.
        ({0: 0.5, 1: 0.6}, "one entry per step"),
        (pd.DataFrame({0.3: [0.5, 0.6], 0.4: [0.5, 0.6]}), "one entry per step"),
        (np.array([[0.5, 0.6], [0.5, 0.6]]), "one entry per step"),
        (0.5, "one entry per step"),
        ("0.5", "one entry per step"),
        ([0.5, 0.6, 0.7], r"RH_room_schedule length \(3\) != tN \(2\)"),
        (pd.DataFrame({"RH": [0.5]}), r"RH_room_schedule length \(1\) != tN \(2\)"),
    ],
    ids=["dict", "two columns", "square array", "scalar", "text", "too long", "frame too short"],
)
def test_humidity_schedule_of_another_shape_is_refused(hp, schedule, says):
    with pytest.raises(ValueError, match=says):
        _quiet(hp.analyze_dynamic, *COOLING_TWICE, RH_room_schedule=schedule)


def test_a_masked_entry_of_the_schedule_is_missing_not_the_number_under_the_mask(hp):
    """A NumPy masked array keeps a number under each masked entry (here 0.9): it is not the humidity."""
    loads, outdoor, room = [-2000.0, 2000.0], [7.0, 35.0], [20.0, 27.0]
    arguments = (2 * 3600, 3600, loads, outdoor, room)
    # Masked on the heating step: not read. The cooling step reads its own entry.
    frame = _quiet(hp.analyze_dynamic, *arguments, RH_room_schedule=np.ma.array([0.9, 0.6], mask=[True, False]))
    assert math.isnan(frame["RH_room [-]"].iloc[0]) and frame["RH_room [-]"].iloc[1] == 0.6
    # Masked on the cooling step: a missing humidity, whatever number lies under the mask.
    for masked in (
        np.ma.array([0.6, 0.9], mask=[False, True]),
        np.ma.array([[0.6], [0.9]], mask=[[False], [True]]),
        np.ma.masked_invalid([0.6, float("nan")]),
        [0.6, np.ma.masked],
    ):
        with pytest.raises(
            ValueError, match=r"Room relative humidity must be a fraction in \[0, 1\], got .* at step 1"
        ):
            _quiet(hp.analyze_dynamic, *arguments, RH_room_schedule=masked)
    # Nothing masked: the array's values.
    plain = _quiet(hp.analyze_dynamic, *arguments, RH_room_schedule=np.ma.array([0.9, 0.6]))
    assert plain["RH_room [-]"].iloc[1] == 0.6 and plain["E_tot [W]"].iloc[1] == frame["E_tot [W]"].iloc[1]
    # A single point given the masked constant on a cooling request is refused too.
    with pytest.raises(ValueError, match="Room relative humidity must be a fraction"):
        hp.analyze_steady(2000.0, 35.0, 27.0, RH_room=np.ma.masked, verbose=False)


# ---------------------------------------------------------------------------
# 3. Exergy columns of a row whose coil removes water
# ---------------------------------------------------------------------------

INCOMPLETE = ("Xc_iu_hx [W]", "X_eff_iu_hx [-]", "X_eff_sys [-]")


def test_dehumidifying_row_has_no_indoor_coil_exergy_balance():
    small = _quiet(AirSourceHeatPump, hp_capacity=2500.0)
    row = small.analyze_steady(2000.0, 15.0, 35.0, RH_room=0.9, verbose=False)
    assert row["Q_iu_latent [W]"] > 0.0
    for key in INCOMPLETE:
        assert math.isnan(row[key]), key
    # The thermal exergy of the dry air stream and the refrigerant states are reported as before.
    for key in ("X_a_iu_in [W]", "X_a_iu_mid [W]", "X_a_iu_out [W]", "X_in_iu_hx [W]", "X_out_iu_hx [W]"):
        assert math.isfinite(row[key]), key
    for key in ("Xc_cmp [W]", "Xc_exp [W]", "Xc_ou_hx [W]", "Xc_iu_fan [W]", "X_eff_cmp [-]", "X_eff_ou_hx [-]"):
        assert math.isfinite(row[key]), key
    # The frame form gives the same row.
    frame_row = small.postprocess_exergy(
        small.analyze_steady(2000.0, 15.0, 35.0, RH_room=0.9, verbose=False, postprocess=False, return_dict=False)
    ).iloc[0]
    for key, value in row.items():
        other = frame_row[key]
        both_nan = isinstance(value, float) and math.isnan(value) and isinstance(other, float) and math.isnan(other)
        assert both_nan or value == other, key


def test_rows_that_remove_no_water_keep_their_exergy_columns(hp):
    dry = hp.analyze_steady(4000.0, 35.0, 27.0, verbose=False)
    # 20 % at 27 degC: dew point 1.9 degC, below the evaporating temperature.
    above_dew_point = hp.analyze_steady(4000.0, 35.0, 27.0, RH_room=0.2, verbose=False)
    assert above_dew_point["Q_iu_latent [W]"] == 0.0
    for key in INCOMPLETE:
        assert math.isfinite(dry[key]) and above_dew_point[key] == dry[key], key
    assert dry["Xc_iu_hx [W]"] == pytest.approx(dry["X_in_iu_hx [W]"] - dry["X_out_iu_hx [W]"], rel=1e-12)
    heating = hp.analyze_steady(-3000.0, 7.0, 20.0, RH_room=0.9, verbose=False)
    for key in INCOMPLETE:
        assert math.isfinite(heating[key]), key


def test_dynamic_frame_blanks_the_balance_on_dehumidifying_rows_only(hp):
    frame = _quiet(
        hp.analyze_dynamic,
        3 * 3600,
        3600,
        [4000.0, 4000.0, -3000.0],
        [35.0, 35.0, 7.0],
        [27.0, 27.0, 20.0],
        RH_room_schedule=[0.2, 0.6, 0.6],
    )
    wet_rows = frame["Q_iu_latent [W]"] > 0.0
    assert list(wet_rows) == [False, True, False]
    for key in INCOMPLETE:
        assert frame[key].isna().tolist() == [False, True, False], key
    assert np.isfinite(frame["X_a_iu_out [W]"]).all()
