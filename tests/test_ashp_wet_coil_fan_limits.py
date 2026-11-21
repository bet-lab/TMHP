"""The wet indoor coil of ``AirSourceHeatPump`` on units whose fan flow limits are constrained (#205).

A fan held at one flow (equal smallest and largest flow) or with a high
smallest flow is what a building simulation that knows the indoor airflow
passes, and the same user passes the room humidity. With ``RH_room``:

- the capacity ceiling of a unit whose outdoor fan runs at one flow is the
  largest duty that runs, and a larger request runs at it;
- a request below the smallest duty a constant-speed fan leaves on the
  pressure-ratio floor is met by cycling at that duty;
- the smallest continuous duty is found above twice the nameplate as well.
"""

from __future__ import annotations

import warnings

import pytest

from tmhp import AirSourceHeatPump


def _build(**kwargs) -> AirSourceHeatPump:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return AirSourceHeatPump(**kwargs)


@pytest.fixture(scope="module")
def default():
    return _build()


# ---------------------------------------------------------------------------
# 1. Outdoor fan held at one flow: the ceiling
# ---------------------------------------------------------------------------

MILD_HOT_ROOM = dict(T0=15.0, T_a_room=35.0, RH_room=1.0)


@pytest.fixture(scope="module")
def outdoor_held(default):
    flow = default.dV_ou_fan_a_max
    return _build(dV_ou_fan_a_min=flow, dV_ou_fan_a_max=flow)


@pytest.fixture(scope="module")
def outdoor_held_ceiling(outdoor_held):
    return outdoor_held.max_capacity(15.0, 35.0, mode="cooling", RH_room=1.0)


def test_ceiling_of_a_unit_with_its_outdoor_fan_held_is_the_largest_duty_that_runs(outdoor_held, outdoor_held_ceiling):
    # 0 W and "infeasible" were reported: at most duties the outdoor coil at its one flow carries
    # more than its duty where the pressure-ratio floor holds the condensing temperature, and the
    # ceiling search took none of them for a duty that runs.
    assert outdoor_held_ceiling["binding"] == "cycle"
    assert outdoor_held_ceiling["Q_max [W]"] == pytest.approx(14181.45, rel=1.5e-3)
    for duty in (4000.0, 12000.0, 14180.0):
        row = outdoor_held.analyze_steady(duty, **MILD_HOT_ROOM, verbose=False)
        assert row["failure_reason"] == "none" and row["capacity_clamped"] is None, duty
        assert row["Q_ref_iu [W]"] == pytest.approx(duty, rel=1e-9)
    # The largest duty that runs on request is not above the ceiling (to the ceiling's tolerance).
    assert outdoor_held_ceiling["Q_max [W]"] >= 14180.0 * (1.0 - 1e-3)


@pytest.mark.parametrize("request_W", [14500.0, 20000.0])
def test_request_above_the_ceiling_of_a_unit_with_its_outdoor_fan_held_runs_at_it(
    outdoor_held, outdoor_held_ceiling, request_W
):
    # 13,773.43 W was delivered: the best capacity-limited candidate of the search, not the ceiling.
    row = outdoor_held.analyze_steady(request_W, **MILD_HOT_ROOM, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "max"
    assert row["capacity_binding"] == "cycle"
    assert row["Q_ref_iu [W]"] == pytest.approx(outdoor_held_ceiling["Q_max [W]"], rel=1e-9)
    assert row["Q_unmet [W]"] == pytest.approx(request_W - row["Q_ref_iu [W]"], rel=1e-9)


def test_dry_ceiling_of_a_unit_with_its_outdoor_fan_held_is_unchanged(outdoor_held):
    dry = outdoor_held.max_capacity(15.0, 35.0, mode="cooling")
    assert dry["Q_max [W]"] == pytest.approx(7275.037327591837, rel=1e-12) and dry["binding"] == "cycle"


# ---------------------------------------------------------------------------
# 2. Indoor fan held at one flow: below the smallest continuous duty
# ---------------------------------------------------------------------------

MILD = dict(T0=15.0, T_a_room=27.0)


@pytest.fixture(scope="module")
def indoor_held(default):
    flow = default.dV_iu_fan_a_max
    return _build(dV_iu_fan_a_min=flow, dV_iu_fan_a_max=flow)


def test_request_below_the_smallest_continuous_duty_of_a_held_indoor_fan_is_met_by_cycling(indoor_held):
    dry = indoor_held.analyze_steady(2400.0, **MILD, verbose=False)
    assert dry["failure_reason"] == "none" and dry["Q_ref_iu [W]"] == pytest.approx(2400.0, rel=1e-9)

    # 0 W and "hx_not_converged" were reported while 2,500 W ran continuously.
    row = indoor_held.analyze_steady(2400.0, **MILD, RH_room=1.0, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
    assert row["Q_ref_iu [W]"] == pytest.approx(2400.0, rel=1e-9) and row["Q_unmet [W]"] == 0.0
    assert row["Q_min [W]"] == pytest.approx(2418.75, rel=5e-3) and row["Q_min [W]"] > 2400.0
    ratio = 2400.0 / row["Q_min [W]"]
    assert row["runtime_fraction [-]"] == pytest.approx(ratio / indoor_held.cycling.part_load_factor(ratio), rel=1e-9)

    above = indoor_held.analyze_steady(2500.0, **MILD, RH_room=1.0, verbose=False)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] is None
    assert above["Q_ref_iu [W]"] == pytest.approx(2500.0, rel=1e-9) and above["runtime_fraction [-]"] == 1.0


@pytest.mark.parametrize("load", [200.0, 1200.0])
def test_small_wet_requests_of_a_held_indoor_fan_are_delivered(indoor_held, load):
    row = indoor_held.analyze_steady(load, **MILD, RH_room=1.0, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
    assert row["Q_ref_iu [W]"] == pytest.approx(load, rel=1e-9) and row["Q_unmet [W]"] == 0.0
    assert row["Q_min [W]"] == pytest.approx(2418.75, rel=5e-3)


def test_dry_failure_reason_of_a_held_fan_is_unchanged(default):
    # The dry search of a constant-speed fan names no least-lift reason, as on main.
    indoor, outdoor = default.dV_iu_fan_a_max, default.dV_ou_fan_a_max
    held = _build(dV_iu_fan_a_min=indoor, dV_iu_fan_a_max=indoor, dV_ou_fan_a_min=outdoor, dV_ou_fan_a_max=outdoor)
    row = held.analyze_steady(200.0, 25.0, 27.0, verbose=False)
    assert row["failure_reason"] == "hx_not_converged" and row["Q_ref_iu [W]"] == 0.0


# ---------------------------------------------------------------------------
# 3. High smallest flows: the smallest continuous duty above twice the nameplate
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def high_minimum(default):
    return _build(dV_iu_fan_a_min=0.6 * default.dV_iu_fan_a_max, dV_ou_fan_a_min=0.6 * default.dV_ou_fan_a_max)


def test_smallest_continuous_wet_duty_above_twice_the_nameplate_is_found(high_minimum):
    dry = high_minimum.analyze_steady(4000.0, **MILD_HOT_ROOM | {"RH_room": None}, verbose=False)
    assert dry["failure_reason"] == "none" and dry["Q_ref_iu [W]"] == pytest.approx(4000.0, rel=1e-9)

    # 0 W and "hx_not_converged" were reported: every duty up to 8 kW (twice the nameplate) is
    # refused by the fans' smallest flows, and the search ended there, under 8.03 kW that runs.
    row = high_minimum.analyze_steady(4000.0, **MILD_HOT_ROOM, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
    assert row["Q_ref_iu [W]"] == pytest.approx(4000.0, rel=1e-9) and row["Q_unmet [W]"] == 0.0
    assert row["Q_min [W]"] == pytest.approx(8031.25, rel=5e-3) and row["Q_min [W]"] > 2.0 * high_minimum.hp_capacity
    assert row["runtime_fraction [-]"] == pytest.approx(0.570, abs=5e-3)

    above = high_minimum.analyze_steady(8250.0, **MILD_HOT_ROOM, verbose=False)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] is None
    assert above["Q_ref_iu [W]"] == pytest.approx(8250.0, rel=1e-9)


def test_smallest_continuous_wet_duty_is_under_the_ceiling(high_minimum):
    floor = high_minimum.analyze_steady(4000.0, **MILD_HOT_ROOM, verbose=False)["Q_min [W]"]
    ceiling = high_minimum.max_capacity(15.0, 35.0, mode="cooling", RH_room=1.0)
    assert ceiling["binding"] == "cycle" and floor < ceiling["Q_max [W]"]


# ---------------------------------------------------------------------------
# 4. The ceiling where the duties that run are a band no rung of the search falls in
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def both_held(default):
    indoor, outdoor = default.dV_iu_fan_a_max, default.dV_ou_fan_a_max
    return _build(dV_iu_fan_a_min=indoor, dV_iu_fan_a_max=indoor, dV_ou_fan_a_min=outdoor, dV_ou_fan_a_max=outdoor)


def test_ceiling_of_a_unit_with_both_fans_held_is_found_above_its_smallest_continuous_duty(both_held):
    # With both fans at one airflow the coils balance in a band of duties only: below it they
    # carry more than the duty (the pressure ratio is on its floor), above it the outdoor coil
    # is at its widest approach. 0 W and "infeasible" were reported for the ceiling.
    ceiling = both_held.max_capacity(15.0, 35.0, mode="cooling", RH_room=1.0)
    assert ceiling["binding"] == "cycle"
    assert ceiling["Q_max [W]"] == pytest.approx(14181.45, rel=1.5e-3)
    row = both_held.analyze_steady(4000.0, **MILD_HOT_ROOM, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
    assert row["Q_ref_iu [W]"] == pytest.approx(4000.0, rel=1e-9)
    assert row["Q_min [W]"] == pytest.approx(12515.6, rel=5e-3) and row["Q_min [W]"] < ceiling["Q_max [W]"]
    above = both_held.analyze_steady(20000.0, **MILD_HOT_ROOM, verbose=False)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] == "max"
    assert above["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)


def test_ceiling_of_fans_with_a_narrow_range_is_above_the_duty_the_unit_cycles_at():
    # Fans that modulate by a few percent: the least-lift point of no duty runs here, and the
    # ceiling was 0 W while 9.9 kW ran on request.
    stiff = _build(
        dV_ou_fan_a_min=0.55,
        dV_ou_fan_a_max=0.6,
        dV_ou_fan_a_rated=0.6,
        dV_iu_fan_a_min=0.19,
        dV_iu_fan_a_max=0.2,
        dV_iu_fan_a_rated=0.2,
    )
    ceiling = stiff.max_capacity(-5.0, 21.0, mode="cooling", RH_room=1.0)
    assert ceiling["binding"] == "compressor" and ceiling["Q_max [W]"] == pytest.approx(10164.4, rel=2e-3)
    row = stiff.analyze_steady(200.0, -5.0, 21.0, RH_room=1.0, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
    assert row["Q_min [W]"] < ceiling["Q_max [W]"]
    above = stiff.analyze_steady(20000.0, -5.0, 21.0, RH_room=1.0, verbose=False)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] == "max"
    assert above["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)


# ---------------------------------------------------------------------------
# 5. The dry model's lower end quoted on the model page
# ---------------------------------------------------------------------------


def test_dry_lower_end_quoted_on_the_model_page(default):
    # docs/source/models/ashp.rst, "This is the wet coil's behaviour only".
    runs = default.analyze_steady(1100.0, 5.0, 27.0, verbose=False)
    assert runs["failure_reason"] == "none" and runs["Q_ref_iu [W]"] == pytest.approx(1100.0, rel=1e-9)
    refused = default.analyze_steady(1050.0, 5.0, 27.0, verbose=False)
    assert refused["failure_reason"] == "cycle_invalid" and refused["Q_ref_iu [W]"] == 0.0
    wet = default.analyze_steady(1050.0, 5.0, 27.0, RH_room=0.2, verbose=False)
    assert wet["failure_reason"] == "none" and wet["capacity_clamped"] == "min"
    assert wet["Q_min [W]"] == pytest.approx(1080.0, rel=5e-3)


# ---------------------------------------------------------------------------
# 6. A duty the ordinary search ends short of is not the ceiling
# ---------------------------------------------------------------------------


def _searched_trial(model, runs: tuple[tuple[float, float], ...], floor: float):
    """A trial of the wet ceiling search for a unit whose least-lift point no duty has.

    The duties inside one of the bands ``runs`` run; below ``floor`` the fans' smallest airflows
    refuse the duty; anywhere else the ordinary search ends with the compressor flat out, short of it.
    """
    calls: list[float] = []

    def trial(q, start=None):
        calls.append(q)
        if any(low <= q <= high for low, high in runs):
            return (q / 2000.0, q / 1000.0), {"Q_ref_iu [W]": q}, None
        if q < floor:
            return (1.0, 1.0), None, model._MIN_AIRFLOW
        return (20.0, 10.0), None, model._SEARCH_FLAT_OUT

    return trial, calls


def test_a_duty_the_search_ends_short_of_names_no_limit(default):
    assert default._SEARCH_FLAT_OUT not in default._LIMIT_REASONS
    # The binding of a ceiling found under such a duty is the compressor's.
    assert default._binding_of(default._SEARCH_FLAT_OUT) == "compressor"


def test_wet_ceiling_search_looks_above_a_duty_the_search_ended_short_of(default):
    # Two bands run: 14 to 18.2 kW and 22.2 to 23.4 kW. The rungs at 80 % and 60 % of the top
    # (25.3 and 19 kW) end short of the duty; taken for limits of the machine they put the
    # ceiling at 18.2 kW, under 5 kW that run.
    trial, _ = _searched_trial(default, runs=((14_000.0, 18_236.0), (22_163.0, 23_400.0)), floor=14_000.0)
    x, state, reason = default._largest_deliverable_wet_duty(trial, 31_661.0, 1e-3)
    assert state["Q_ref_iu [W]"] == pytest.approx(23_400.0, rel=1.5e-3) and state["Q_ref_iu [W]"] <= 23_400.0
    assert default._binding_of(reason) == "compressor"


def test_wet_ceiling_search_finds_a_band_between_a_rung_refused_from_below_and_one_ended_short(default):
    # One band, between the rungs at 60 % and 80 % of the top, and no rung in it: 0 W was the
    # answer when the rung at 80 % counted as a limit of the machine and nothing ran under it.
    trial, _ = _searched_trial(default, runs=((22_163.0, 23_400.0),), floor=22_163.0)
    x, state, reason = default._largest_deliverable_wet_duty(trial, 31_661.0, 1e-3)
    assert x is not None and state is not None
    assert state["Q_ref_iu [W]"] == pytest.approx(23_400.0, rel=1.5e-3) and state["Q_ref_iu [W]"] <= 23_400.0
    assert default._binding_of(reason) == "compressor"


def test_ceiling_of_a_stiff_outdoor_fan_is_above_every_duty_that_runs_on_request():
    # A 10 kW unit whose outdoor fan runs at no less than 90 % of its largest flow, outdoor -10 degC,
    # saturated 21 degC room: 18.5 to 21 kW end with the compressor flat out at the widest indoor
    # approach, and 22.5 kW runs at a narrower one. 18.24 kW was reported for the ceiling.
    base = _build(hp_capacity=10000.0)
    stiff = _build(hp_capacity=10000.0, dV_ou_fan_a_min=0.9 * base.dV_ou_fan_a_max)
    room = dict(T0=-10.0, T_a_room=21.0, RH_room=1.0)
    running = stiff.analyze_steady(22500.0, **room, verbose=False)
    assert running["failure_reason"] == "none" and running["capacity_clamped"] is None
    assert running["Q_ref_iu [W]"] == pytest.approx(22500.0, rel=1e-9)
    ceiling = stiff.max_capacity(-10.0, 21.0, mode="cooling", RH_room=1.0)
    assert ceiling["binding"] == "compressor" and ceiling["Q_max [W]"] >= 22500.0
    above = stiff.analyze_steady(30000.0, **room, verbose=False)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] == "max"
    assert above["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)
