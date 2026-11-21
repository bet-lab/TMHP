"""The searches of the wet indoor coil use each other's operating points (#205).

With ``RH_room`` a cooling condition of ``AirSourceHeatPump`` is searched three ways: the operating
point of a request, the smallest continuous duty above a request the fans' smallest airflows
refuse, and the largest deliverable duty (``max_capacity``). On a unit with a fan held at one flow
each samples the duty its own way and could miss a point another had found:

- a low-load request was answered with the unit off where the ceiling search had found the state
  the unit runs flat out at;
- the ceiling was 0 W where the low-load search had found a duty that runs, and a request above it
  was answered with the unit off;
- the flat-out ceiling was under a duty the ordinary search delivers in full;
- a dynamic step was answered from the step before it: a request refused when solved alone ran
  after a smaller one of the same condition.

A point one search has verified is now a candidate of the others, and a row is that of its step
solved alone, whatever came before.
"""

from __future__ import annotations

import math
import warnings

import pandas as pd
import pytest

from tmhp import AirSourceHeatPump


def _build(**kwargs) -> AirSourceHeatPump:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return AirSourceHeatPump(**kwargs)


def _held(capacity: float, *fans: str) -> AirSourceHeatPump:
    """A default unit of ``capacity`` with each named fan held at its largest flow."""
    base = _build(hp_capacity=capacity)
    flows = {"iu": base.dV_iu_fan_a_max, "ou": base.dV_ou_fan_a_max}
    limits = {f"dV_{fan}_fan_a_{end}": flows[fan] for fan in fans for end in ("min", "max")}
    return _build(hp_capacity=capacity, **limits)


def _steady(model: AirSourceHeatPump, load: float, T0: float, T_a_room: float, **kwargs) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return model.analyze_steady(load, T0, T_a_room, verbose=False, **kwargs)


def _dynamic(model: AirSourceHeatPump, loads, T0: float, T_a_room: float, RH_room: float) -> pd.DataFrame:
    steps = len(loads)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return model.analyze_dynamic(
            steps * 3600,
            3600,
            list(loads),
            [T0] * steps,
            [T_a_room] * steps,
            RH_room_schedule=[RH_room] * steps,
        )


def _rows_by_load(frame: pd.DataFrame, loads) -> dict[float, pd.Series]:
    """The rows of a run by their load, without the time columns."""
    rows = frame.drop(columns=["time [s]", "time [h]"])
    return {float(load): rows.iloc[step] for step, load in enumerate(loads)}


def _assert_same_row(row: pd.Series, reference: pd.Series) -> None:
    """Every value of ``row`` is that of ``reference``, to the last bit (NaN where it is NaN)."""
    assert list(row.index) == list(reference.index)
    for key in row.index:
        got, expected = row[key], reference[key]
        missing = [value is None or (isinstance(value, float) and math.isnan(value)) for value in (got, expected)]
        assert missing[0] == missing[1], key
        if not missing[0]:
            assert got == expected, key


# ---------------------------------------------------------------------------
# 1. The low-load search takes the ceiling the other search found
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def small_both_held():
    return _held(2500.0, "iu", "ou")


@pytest.fixture(scope="module")
def both_held():
    return _held(4000.0, "iu", "ou")


def test_low_load_request_is_met_by_cycling_at_the_flat_out_state_the_ceiling_search_found(small_both_held):
    # Both fans at one flow, on the pressure-ratio floor: no duty the climb from 2.5 kW tries runs,
    # while the unit runs flat out at 9.6 kW (`max_capacity`, and a 10 kW request ends there).
    # 0 W and "hx_not_converged" were the answer.
    mild = dict(T0=8.0, T_a_room=35.0, RH_room=0.9)
    ceiling = small_both_held.max_capacity(8.0, 35.0, mode="cooling", RH_room=0.9)
    assert ceiling["binding"] == "compressor" and ceiling["Q_max [W]"] == pytest.approx(9612.94, rel=1e-4)
    above = _steady(small_both_held, 10000.0, **mild)
    assert above["capacity_clamped"] == "max" and above["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)
    row = _steady(small_both_held, 2500.0, **mild)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
    assert row["Q_ref_iu [W]"] == pytest.approx(2500.0, rel=1e-9) and row["Q_unmet [W]"] == 0.0
    assert row["Q_min [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)
    # EN 14825 with Cd = 0.25: capacity ratio 0.260, runtime fraction 0.319.
    ratio = 2500.0 / ceiling["Q_max [W]"]
    assert row["runtime_fraction [-]"] == pytest.approx(ratio / (1.0 - 0.25 * (1.0 - ratio)), rel=1e-9)
    assert row["runtime_fraction [-]"] == pytest.approx(0.319, abs=5e-4)


def test_ceiling_point_is_used_only_when_it_reproduces_as_a_request(small_both_held, monkeypatch):
    # A ceiling whose approaches do not deliver its duty is no operating point: the request is refused as before.
    ceiling = small_both_held.max_capacity(8.0, 35.0, mode="cooling", RH_room=0.9)
    wrong = dict(ceiling) | {"dT_ref_evap [K]": ceiling["dT_ref_evap [K]"] + 2.0}
    monkeypatch.setattr(small_both_held, "_solve_max_capacity", lambda *args, **kwargs: dict(wrong))
    row = _steady(small_both_held, 2500.0, 8.0, 35.0, RH_room=0.9)
    assert row["failure_reason"] == "hx_not_converged" and row["Q_ref_iu [W]"] == 0.0


def test_request_just_above_the_flat_out_ceiling_runs_at_it(both_held):
    # 12 kW is 1 % above what the unit delivers flat out (11.87 kW). The least-lift point of that
    # request ends where the fans' smallest airflows refuse it, so it was taken for refused from
    # below and answered with the unit off, while 13 kW ran at the ceiling.
    cold = dict(T0=0.0, T_a_room=35.0, RH_room=0.5)
    ceiling = both_held.max_capacity(0.0, 35.0, mode="cooling", RH_room=0.5)
    assert ceiling["binding"] == "compressor" and ceiling["Q_max [W]"] == pytest.approx(11869.94, rel=1e-4)
    for request in (12000.0, 13000.0):
        row = _steady(both_held, request, **cold)
        assert row["failure_reason"] == "none" and row["capacity_clamped"] == "max"
        assert row["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)
        assert row["Q_unmet [W]"] == pytest.approx(request - ceiling["Q_max [W]"], rel=1e-9)


def test_request_refused_from_below_up_to_the_coil_limit_asks_nothing_of_the_other_searches(both_held, monkeypatch):
    # -10 degC outdoor: every duty from the request to the indoor coil's limit is refused by the
    # fans' smallest airflows. Nothing above it was refused any other way, so neither the low-load
    # point nor a ceiling is searched for (seconds, for an answer that stays "off").
    def never(*args, **kwargs):
        raise AssertionError("not searched where every larger duty is refused from below")

    monkeypatch.setattr(both_held, "_low_load_point", never)
    monkeypatch.setattr(both_held, "_solve_max_capacity", never)
    row = _steady(both_held, 12000.0, -10.0, 21.0, RH_room=0.5)
    assert row["failure_reason"] == "hx_not_converged" and row["Q_ref_iu [W]"] == 0.0
    assert row["Q_unmet [W]"] == 12000.0


# ---------------------------------------------------------------------------
# 2. The ceiling search takes the duty the low-load search found
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outdoor", "humidity", "cycles_at", "top"),
    [(8.0, 1.0, 14343.75, 14343.75), (10.0, 1.0, 13734.38, 14048.22), (8.0, 0.97, 14109.37, 14142.44)],
)
def test_ceiling_of_both_fans_held_is_not_below_the_duty_the_unit_cycles_at(
    both_held, outdoor, humidity, cycles_at, top
):
    # 0 W and "infeasible" were reported for the ceiling while the nameplate was met by cycling at
    # 13.7 to 14.3 kW, and a 15 kW request was answered with the unit off.
    room = dict(T0=outdoor, T_a_room=35.0, RH_room=humidity)
    low = _steady(both_held, 4000.0, **room)
    assert low["failure_reason"] == "none" and low["capacity_clamped"] == "min"
    assert low["Q_min [W]"] == pytest.approx(cycles_at, rel=5e-3)
    ceiling = both_held.max_capacity(outdoor, 35.0, mode="cooling", RH_room=humidity)
    assert ceiling["binding"] == "cycle"
    assert ceiling["Q_max [W]"] == pytest.approx(top, rel=1.5e-3) and ceiling["Q_max [W]"] >= low["Q_min [W]"]
    above = _steady(both_held, 15000.0, **room)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] == "max"
    assert above["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)
    assert above["Q_unmet [W]"] == pytest.approx(15000.0 - ceiling["Q_max [W]"], rel=1e-9)


def test_ceiling_search_asks_for_the_low_load_point_only_where_it_found_no_duty(both_held, monkeypatch):
    calls = []
    original = both_held._low_load_point

    def counted(T0, T_a_room):
        calls.append((T0, T_a_room))
        return original(T0, T_a_room)

    monkeypatch.setattr(both_held, "_low_load_point", counted)
    # A ceiling the search finds on its own (15 degC outdoor: the band 12.2 to 13.7 kW).
    found = both_held.max_capacity(15.0, 35.0, mode="cooling", RH_room=1.0)
    assert found["Q_max [W]"] > 0.0 and calls == []
    # One it does not.
    seeded = both_held.max_capacity(8.0, 35.0, mode="cooling", RH_room=1.0)
    assert seeded["Q_max [W]"] > 0.0 and calls == [(8.0, 35.0)]


def test_low_load_point_is_the_same_whatever_was_solved_before(both_held):
    with both_held._solving_with_room_humidity(1.0, 35.0, cooling=True):
        first = both_held._low_load_point(8.0, 35.0)
    _steady(both_held, 13700.0, 8.0, 35.0, RH_room=1.0)
    both_held.max_capacity(10.0, 35.0, mode="cooling", RH_room=1.0)
    with both_held._solving_with_room_humidity(1.0, 35.0, cooling=True):
        again = both_held._low_load_point(8.0, 35.0)
    assert first is not None and again is not None
    assert first[0] == again[0] and first[1] == again[1]
    assert first[1]["Q_ref_iu [W]"] == pytest.approx(14343.75, rel=5e-3) and first[1]["capacity_clamped"] is None
    # Nothing is kept between calls.
    assert both_held._wet_points is None


# ---------------------------------------------------------------------------
# 3. The flat-out ceiling is not under a duty the ordinary search delivers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def large_indoor_held():
    return _held(10000.0, "iu")


def test_flat_out_ceiling_of_a_held_indoor_fan_is_what_a_request_is_delivered_up_to(large_indoor_held):
    # On the pressure-ratio floor the outdoor approach of the least-lift point ends on its lower
    # bound, where the cycle is solved with 0.5 K of subcooling; at a wider one the same speed
    # delivers 1.2 % more. 34,994.80 W was the ceiling while 35,379.74 W was delivered in full.
    cold = dict(T0=-10.0, T_a_room=35.0, RH_room=1.0)
    ceiling = large_indoor_held.max_capacity(-10.0, 35.0, mode="cooling", RH_room=1.0)
    assert ceiling["binding"] == "compressor"
    assert ceiling["Q_max [W]"] == pytest.approx(35430.67, rel=1e-4)
    inside = _steady(large_indoor_held, 35379.74, **cold)
    assert inside["failure_reason"] == "none" and inside["capacity_clamped"] is None
    assert (
        inside["Q_ref_iu [W]"] == pytest.approx(35379.74, rel=1e-9) and inside["Q_ref_iu [W]"] <= ceiling["Q_max [W]"]
    )
    above = _steady(large_indoor_held, 36000.0, **cold)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] == "max"
    assert above["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)
    assert above["cmp_rpm [rpm]"] / 60.0 == pytest.approx(large_indoor_held.rps_max, rel=1e-9)


def test_flat_out_ceiling_off_the_pressure_ratio_floor_is_the_least_lift_point(large_indoor_held, monkeypatch):
    # 35/27 degC: the condensing temperature is the approach asked for, and nothing is searched.
    def never(*args, **kwargs):
        raise AssertionError("the ordinary search is not part of this ceiling")

    monkeypatch.setattr(large_indoor_held, "_searched_point", never)
    ceiling = large_indoor_held.max_capacity(35.0, 27.0, mode="cooling", RH_room=0.5)
    assert ceiling["binding"] == "compressor" and ceiling["Q_max [W]"] == pytest.approx(18078.55, rel=1e-4)


def test_dry_ceiling_of_a_held_indoor_fan_is_unchanged(large_indoor_held, monkeypatch):
    # Without a room humidity, and with dry room air, the ceiling is the dry search's: the indoor
    # coil at its widest approach (14.09 kW), with none of the wet coil's searches.
    def never(*args, **kwargs):
        raise AssertionError("not part of the dry ceiling")

    monkeypatch.setattr(large_indoor_held, "_searched_point", never)
    monkeypatch.setattr(large_indoor_held, "_low_load_point", never)
    monkeypatch.setattr(large_indoor_held, "_flat_out_point_on_the_floor", never)
    for humidity in (None, 0.0):
        ceiling = large_indoor_held.max_capacity(-10.0, 35.0, mode="cooling", RH_room=humidity)
        assert ceiling["binding"] == "cycle" and ceiling["Q_max [W]"] == pytest.approx(14088.92, rel=1e-6)
        assert large_indoor_held._wet_points is None


# ---------------------------------------------------------------------------
# 4. A dynamic row is that of its step solved alone
# ---------------------------------------------------------------------------


def test_dynamic_rows_of_both_fans_held_do_not_depend_on_the_steps_before(both_held):
    # Alone, 13.7 and 14.05 kW were answered with the unit off; after a 4 kW step of the same
    # condition they were met by cycling at the duty that step had found.
    room = (8.0, 35.0, 1.0)
    loads = (4000.0, 13700.0, 14050.0)
    after_a_smaller_load = _rows_by_load(_dynamic(both_held, loads, *room), loads)
    reverse = loads[::-1]
    reversed_rows = _rows_by_load(_dynamic(both_held, reverse, *room), reverse)
    pair = loads[1:]
    without_the_smaller = _rows_by_load(_dynamic(both_held, pair, *room), pair)
    for load in loads:
        alone = _rows_by_load(_dynamic(both_held, [load], *room), [load])[load]
        assert alone["failure_reason"] == "none" and alone["capacity_clamped"] == "min"
        assert alone["Q_ref_iu [W]"] == pytest.approx(load, rel=1e-9) and alone["Q_unmet [W]"] == 0.0
        assert alone["Q_min [W]"] == pytest.approx(14343.75, rel=5e-3)
        _assert_same_row(after_a_smaller_load[load], alone)
        _assert_same_row(reversed_rows[load], alone)
        if load in without_the_smaller:
            _assert_same_row(without_the_smaller[load], alone)


def test_dynamic_rows_of_the_default_unit_do_not_depend_on_the_steps_before():
    # Each request finds its own smallest continuous duty (1 543.75 W from 200 W and 1.5 kW,
    # 1 547.5 W from 1 530 W: the bisection stops within 0.5 %). The duty of the first step was
    # used for the later ones, so a row moved by that much with the order of the steps.
    unit = _build()
    room = (10.0, 27.0, 0.8)
    loads = (200.0, 1500.0, 1530.0)
    as_given = _rows_by_load(_dynamic(unit, loads, *room), loads)
    reverse = loads[::-1]
    reversed_rows = _rows_by_load(_dynamic(unit, reverse, *room), reverse)
    for load in loads:
        alone = _rows_by_load(_dynamic(unit, [load], *room), [load])[load]
        assert alone["capacity_clamped"] == "min" and alone["Q_min [W]"] == pytest.approx(1543.75, rel=5e-3)
        _assert_same_row(as_given[load], alone)
        _assert_same_row(reversed_rows[load], alone)
        steady = _steady(unit, load, room[0], room[1], RH_room=room[2])
        for key in ("Q_ref_iu [W]", "Q_min [W]", "E_tot [W]", "runtime_fraction [-]", "Q_iu_latent [W]", "cop_sys [-]"):
            assert alone[key] == steady[key], key


def test_dynamic_run_searches_a_duty_of_a_wet_condition_once():
    unit = _build()
    searches = []
    original = unit._optimize_operation

    def counted(Q_r_iu, T0, T_a_room):
        searches.append(abs(Q_r_iu))
        return original(Q_r_iu=Q_r_iu, T0=T0, T_a_room=T_a_room)

    unit._optimize_operation = counted
    try:
        frame = _dynamic(unit, (200.0, 300.0, 400.0), 10.0, 27.0, 0.8)
    finally:
        del unit._optimize_operation
    assert (frame["capacity_clamped"] == "min").all() and frame["Q_min [W]"].nunique() == 1
    # The rungs and the bisection of the first step answer the later ones: each adds its own request only.
    assert searches[searches.index(300.0) :] == [300.0, 400.0]


def test_dry_dynamic_rows_keep_the_floor_of_the_first_step(monkeypatch):
    # Without a room humidity, and with dry room air, the floor of a condition is still searched once
    # per run and reused for smaller requests (the dry model's rule, left as it is).
    unit = _build()
    floors = []
    original = unit._climb_to_continuous_duty

    def counted(Q_r_iu, T0, T_a_room):
        floors.append(abs(Q_r_iu))
        return original(Q_r_iu, T0, T_a_room)

    monkeypatch.setattr(unit, "_climb_to_continuous_duty", counted)
    state = {"Q_ref_iu [W]": 900.0, "capacity_clamped": "min"}
    with unit._remembering_floors():
        unit._floor_memo[(1.0, 10.0, 27.0, None)] = dict(state)
        assert unit._smallest_continuous_operating_point(500.0, 10.0, 27.0) == state
        with unit._solving_with_room_humidity(0.0, 27.0, cooling=True):
            unit._floor_memo[(1.0, 10.0, 27.0, 0.0)] = dict(state)
            assert unit._smallest_continuous_operating_point(500.0, 10.0, 27.0) == state
    assert floors == []


# ---------------------------------------------------------------------------
# 5. What the model page says of a coil above the dew point
# ---------------------------------------------------------------------------


def test_coil_above_the_dew_point_is_the_dry_coil_equation_but_not_the_dry_searches():
    # Quoted on the model page: with a room humidity a request the fans' smallest airflows refuse
    # is met by cycling, also where no water condenses; the dry model answers it with the unit off.
    unit = _build()
    dry = _steady(unit, 1150.0, 0.0, 27.0)
    assert dry["Q_ref_iu [W]"] == 0.0 and dry["failure_reason"] == "cycle_invalid"
    humid = _steady(unit, 1150.0, 0.0, 27.0, RH_room=0.02)
    assert humid["failure_reason"] == "none" and humid["capacity_clamped"] == "min"
    assert humid["Q_ref_iu [W]"] == pytest.approx(1150.0, rel=1e-9) and humid["Q_iu_latent [W]"] == 0.0
    assert humid["Q_min [W]"] == pytest.approx(1153.1, rel=5e-3)
    assert humid["T_ref_evap_sat [°C]"] == pytest.approx(-1.4, abs=0.1)
    # Room air with no water vapour is solved as the dry coil is.
    none = _steady(unit, 1150.0, 0.0, 27.0, RH_room=0.0)
    assert none["Q_ref_iu [W]"] == 0.0 and none["failure_reason"] == "cycle_invalid"
