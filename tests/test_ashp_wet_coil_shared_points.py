"""The searches of the wet indoor coil use each other's operating points (#205).

With ``RH_room`` a cooling condition of ``AirSourceHeatPump`` is searched three ways: the operating
point of a request, the smallest continuous duty above a request the fans' smallest airflows
refuse, and the largest deliverable duty (``max_capacity``). On a unit with a fan held at one flow
each samples the duty its own way and could miss a point another had found:

- a low-load request was answered with the unit off where the ceiling search had found the state
  the unit runs flat out at, or where the duties that run are a window narrower than the
  tolerance its bisection stopped at;
- the ceiling was 0 W where the low-load search had found a duty that runs, and a request above it
  was answered with the unit off;
- the flat-out ceiling was under a duty the ordinary search delivers in full;
- a dynamic step was answered from the step before it: a request refused when solved alone ran
  after a smaller one of the same condition.

A point one search has verified is now a candidate of the others, and a row is that of its step
solved alone, whatever came before.

The ceiling is such a point for the request that asks for it. A request equal to what
``max_capacity`` reported -- or under it by less than the search of the ceiling tells apart -- was
answered with the unit off, or a little short, where the search of the request did not arrive at
the point the ceiling search had verified (section 6).
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
    """Every value of ``row`` is that of ``reference``, to the last bit (NaN where it is NaN).

    The two are matched by column name: the order of a frame's columns is that of the first row
    that carried each, which is not what a row is.
    """
    assert set(row.index) == set(reference.index)
    for key in reference.index:
        got, expected = row[key], reference[key]
        missing = [value is None or (isinstance(value, float) and math.isnan(value)) for value in (got, expected)]
        assert missing[0] == missing[1], key
        if not missing[0]:
            assert got == expected, key


def _noting_calls(monkeypatch, unit: AirSourceHeatPump, *names: str) -> list[str]:
    """The names of those methods of ``unit`` that get called from here on, in order.

    Each is replaced by one that notes the call and goes on as before. A test that forbids a
    search asserts on the list after the solve: the solve makes some of its searches where an
    exception is caught, so a replacement that raises says nothing.
    """
    calls: list[str] = []
    for name in names:
        original = getattr(unit, name, None)
        if original is None:
            continue  # a search the model does not have is not made

        def noting(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(unit, name, noting)
    return calls


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
    searched = _noting_calls(monkeypatch, both_held, "_low_load_point", "_solve_max_capacity")
    row = _steady(both_held, 12000.0, -10.0, 21.0, RH_room=0.5)
    assert row["failure_reason"] == "hx_not_converged" and row["Q_ref_iu [W]"] == 0.0
    assert row["Q_unmet [W]"] == 12000.0
    assert searched == []


def test_a_forbidden_search_is_noticed_where_the_solve_catches_exceptions(both_held, monkeypatch):
    # The check of the test above, checked: 12 kW at 0 degC is answered from the flat-out ceiling
    # (`_solve_max_capacity`), asked for where the solve catches whatever is raised.
    searched = _noting_calls(monkeypatch, both_held, "_low_load_point", "_solve_max_capacity")
    row = _steady(both_held, 12000.0, 0.0, 35.0, RH_room=0.5)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "max"
    assert searched == ["_solve_max_capacity"]


def test_low_load_search_closes_on_a_window_narrower_than_its_first_tolerance(both_held):
    # 8 degC outdoor, saturated 35 degC room: the unit runs continuously between 14.31 and 14.35 kW
    # only (0.23 %). The bisection from 13.7 kW closed around the window at 0.5 % (14,304.7 to
    # 14,371.9 W) with no trial inside it, and the request was answered with the unit off.
    room = dict(T0=8.0, T_a_room=35.0, RH_room=1.0)
    for request in (13700.0, 14050.0):
        row = _steady(both_held, request, **room)
        assert row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
        assert row["Q_ref_iu [W]"] == pytest.approx(request, rel=1e-9) and row["Q_unmet [W]"] == 0.0
        assert 14312.0 < row["Q_min [W]"] < 14346.0
        continuous = _steady(both_held, row["Q_min [W]"], **room)
        assert continuous["failure_reason"] == "none" and continuous["capacity_clamped"] is None


@pytest.mark.parametrize(("humidity", "final_width"), [(None, 5e-3), (0.0, 5e-3), (0.5, 1e-3)])
def test_only_a_wet_search_that_found_nothing_is_bisected_on(monkeypatch, humidity, final_width):
    # Nothing runs: duties under 1 kW are refused from below, larger ones otherwise. The dry search
    # (no room humidity, or room air with no water vapour) stops at its tolerance as before.
    # The searches of a duty are replaced; the request is solved as any other (`analyze_steady`),
    # and the duties it tried say where its bisection stopped.
    unit = _build()
    reason = "discharge_temp_above_max" if not humidity else unit._MIN_AIRFLOW
    tried = []

    def refused(Q_r_iu, T0, T_a_room):
        tried.append(abs(Q_r_iu))
        return {"success": False, "fun": 1e12, "least_lift_reason": reason if abs(Q_r_iu) < 1000.0 else None}

    class Refusal(dict):
        __getattr__ = dict.get

    monkeypatch.setattr(unit, "_optimize_operation", lambda **kwargs: Refusal(refused(**kwargs)))
    _steady(unit, 300.0, 10.0, 27.0, RH_room=humidity)
    under_the_coil_limit = [q for q in tried if q < 50000.0]  # not the flat-out request of a ceiling
    below = max(q for q in under_the_coil_limit if q < 1000.0)
    above = min(q for q in under_the_coil_limit if q >= 1000.0)
    assert (above - below) <= final_width * above < 2.0 * (above - below)


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
    asked = _noting_calls(monkeypatch, both_held, "_low_load_point")
    # A ceiling the search finds on its own (15 degC outdoor: the band 12.2 to 13.7 kW).
    found = both_held.max_capacity(15.0, 35.0, mode="cooling", RH_room=1.0)
    assert found["Q_max [W]"] > 0.0 and asked == []
    # One it does not.
    seeded = both_held.max_capacity(8.0, 35.0, mode="cooling", RH_room=1.0)
    assert seeded["Q_max [W]"] > 0.0 and asked == ["_low_load_point"]


def test_ceiling_from_the_low_load_point_is_the_same_whatever_was_solved_before(both_held):
    # The ceiling of this condition is searched upwards from its low-load point (the test above).
    first = _held(4000.0, "iu", "ou").max_capacity(8.0, 35.0, mode="cooling", RH_room=1.0)
    _steady(both_held, 13700.0, 8.0, 35.0, RH_room=1.0)
    both_held.max_capacity(10.0, 35.0, mode="cooling", RH_room=1.0)
    again = both_held.max_capacity(8.0, 35.0, mode="cooling", RH_room=1.0)
    assert again == first
    assert first["binding"] == "cycle" and first["Q_max [W]"] == pytest.approx(14343.75, rel=5e-3)
    # That duty runs as a request of its own, in continuous operation.
    row = _steady(both_held, first["Q_max [W]"], 8.0, 35.0, RH_room=1.0)
    assert row["failure_reason"] == "none" and row["Q_ref_iu [W]"] == pytest.approx(first["Q_max [W]"], rel=1e-9)
    # Nothing is kept between calls.
    assert getattr(both_held, "_wet_points", None) is None


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
    searched = _noting_calls(monkeypatch, large_indoor_held, "_searched_point")
    ceiling = large_indoor_held.max_capacity(35.0, 27.0, mode="cooling", RH_room=0.5)
    assert ceiling["binding"] == "compressor" and ceiling["Q_max [W]"] == pytest.approx(18078.55, rel=1e-4)
    assert searched == []


def test_dry_ceiling_of_a_held_indoor_fan_is_unchanged(large_indoor_held, monkeypatch):
    # Without a room humidity, and with dry room air, the ceiling is the dry search's: the indoor
    # coil at its widest approach (14.09 kW), with none of the wet coil's searches.
    searched = _noting_calls(
        monkeypatch, large_indoor_held, "_searched_point", "_low_load_point", "_flat_out_point_on_the_floor"
    )
    for humidity in (None, 0.0):
        ceiling = large_indoor_held.max_capacity(-10.0, 35.0, mode="cooling", RH_room=humidity)
        assert ceiling["binding"] == "cycle" and ceiling["Q_max [W]"] == pytest.approx(14088.92, rel=1e-6)
        assert getattr(large_indoor_held, "_wet_points", None) is None
    assert searched == []


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
        # Each request closes on the window from its own side (14,343.75 / 14,313.09 / 14,320.02 W).
        assert 14312.0 < alone["Q_min [W]"] < 14346.0
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
    searches = _noting_calls(monkeypatch, unit, "_optimize_operation")
    state = {"Q_ref_iu [W]": 900.0, "capacity_clamped": "min"}
    with unit._remembering_floors():
        unit._floor_memo[(1.0, 10.0, 27.0, None)] = dict(state)
        assert unit._smallest_continuous_operating_point(500.0, 10.0, 27.0) == state
        with unit._solving_with_room_humidity(0.0, 27.0, cooling=True):
            unit._floor_memo[(1.0, 10.0, 27.0, 0.0)] = dict(state)
            assert unit._smallest_continuous_operating_point(500.0, 10.0, 27.0) == state
    assert searches == []


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


# ---------------------------------------------------------------------------
# 6. A request at the ceiling of its condition runs at it
# ---------------------------------------------------------------------------

#: Relative width a searched ceiling is known to (the default ``rel_tol`` of ``max_capacity``).
CEILING_REL_TOL = 1e-3


def _assert_ceiling_row(row: dict, request: float, ceiling: dict) -> None:
    """``row`` is the unit at the ceiling ``max_capacity`` reported, to the last bit, with nothing unmet under it."""
    assert row["failure_reason"] == "none" and row["converged"] and row["hp_is_on"]
    assert row["capacity_clamped"] == "max" and row["capacity_binding"] == ceiling["binding"]
    assert row["Q_ref_iu [W]"] == ceiling["Q_max [W]"] and row["Q_max [W]"] == ceiling["Q_max [W]"]
    assert row["E_tot [W]"] == ceiling["E_tot [W]"]
    assert row["Q_request [W]"] == request
    unmet = request - ceiling["Q_max [W]"]
    assert row["Q_unmet [W]"] == (pytest.approx(unmet, rel=1e-9) if unmet > 1e-9 * request else 0.0)


@pytest.fixture(scope="module")
def hot_day():
    """Default 10 kW unit at 46 degC outdoor, room 27 degC at 30 %: the ceiling is the condensing-temperature limit."""
    unit = _build(hp_capacity=10000.0)
    ceiling = unit.max_capacity(46.0, 27.0, mode="cooling", RH_room=0.3)
    assert ceiling["binding"] == "condensing_temperature"
    assert ceiling["Q_max [W]"] == pytest.approx(13771.58, rel=1e-6)
    return unit, ceiling


@pytest.mark.parametrize("under", [1e-3, 1e-4])
def test_request_the_search_delivers_under_the_ceiling_is_delivered_as_before(hot_day, under):
    unit, ceiling = hot_day
    request = ceiling["Q_max [W]"] * (1.0 - under)
    row = _steady(unit, request, 46.0, 27.0, RH_room=0.3)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(request, rel=1e-12) and row["Q_unmet [W]"] == 0.0


@pytest.mark.parametrize("under", [1e-5, 1e-6, 1e-7, "one float step", 0.0])
def test_request_at_the_ceiling_or_a_hair_under_it_runs_at_the_ceiling(hot_day, under):
    # 0 W and "condensing_temp_above_max" were the answer to all five: the search of the request
    # starts from its least-lift point solved loosely, which a request this close to the limit
    # reads as beyond it, and the ceiling was taken only for a request strictly above it.
    unit, ceiling = hot_day
    top = ceiling["Q_max [W]"]
    request = math.nextafter(top, 0.0) if under == "one float step" else top * (1.0 - under)
    row = _steady(unit, request, 46.0, 27.0, RH_room=0.3)
    _assert_ceiling_row(row, request, ceiling)
    assert row["Q_unmet [W]"] == 0.0
    # Over the request by no more than the search of the ceiling tells apart.
    assert 0.0 <= row["Q_ref_iu [W]"] - request <= CEILING_REL_TOL * top


@pytest.mark.parametrize("over", ["one float step", 1.05])
def test_request_above_the_ceiling_runs_at_it_as_before(hot_day, over):
    unit, ceiling = hot_day
    top = ceiling["Q_max [W]"]
    request = math.nextafter(top, math.inf) if over == "one float step" else top * over
    _assert_ceiling_row(_steady(unit, request, 46.0, 27.0, RH_room=0.3), request, ceiling)


def test_ceiling_row_is_the_same_from_under_at_and_above_the_ceiling(hot_day):
    unit, ceiling = hot_day
    top = ceiling["Q_max [W]"]
    above = _steady(unit, 1.05 * top, 46.0, 27.0, RH_room=0.3)
    for request in (top * (1.0 - 1e-5), top):
        row = _steady(unit, request, 46.0, 27.0, RH_room=0.3)
        assert set(row) == set(above)
        differing = {key for key in row if not (row[key] == above[key] or (row[key] != row[key]))}
        assert differing == {"Q_request [W]", "Q_unmet [W]"}


def test_request_at_a_flat_out_ceiling_is_delivered_the_ceiling():
    # Default 2 kW unit at 20/20 degC, 30 %: the ceiling is the unit flat out. A request for it was
    # delivered 0.32 W short (3,743.17 of 3,743.49 W, with that reported unmet) -- the flat-out
    # state of the search's loosely solved starting point -- while one float step more got all of it.
    unit = _build(hp_capacity=2000.0)
    ceiling = unit.max_capacity(20.0, 20.0, mode="cooling", RH_room=0.3)
    top = ceiling["Q_max [W]"]
    assert ceiling["binding"] == "compressor" and top == pytest.approx(3743.486, rel=1e-6)
    for request in (top * (1.0 - 1e-5), math.nextafter(top, 0.0), top, math.nextafter(top, math.inf)):
        row = _steady(unit, request, 20.0, 20.0, RH_room=0.3)
        _assert_ceiling_row(row, request, ceiling)
        assert row["Q_unmet [W]"] == 0.0
        assert row["cmp_rpm [rpm]"] / 60.0 == pytest.approx(unit.rps_max, rel=1e-9)


@pytest.mark.parametrize(
    ("capacity", "outdoor", "humidity", "top"), [(2500.0, 8.0, 0.9, 9612.94), (4000.0, 0.0, 0.5, 11869.94)]
)
def test_request_at_the_flat_out_ceiling_of_both_fans_held_runs_at_it(capacity, outdoor, humidity, top):
    # Both fans at one flow, on the pressure-ratio floor: the request is refused from below and no
    # larger duty runs, so the flat-out state the ceiling search starts with is taken. It was taken
    # for a request under it (met by cycling at it) and for one above it (the unit runs there) --
    # and the request equal to it was answered with the unit off, "hx_not_converged".
    unit = _held(capacity, "iu", "ou")
    room = dict(T0=outdoor, T_a_room=35.0, RH_room=humidity)
    ceiling = unit.max_capacity(outdoor, 35.0, mode="cooling", RH_room=humidity)
    exact = ceiling["Q_max [W]"]
    assert ceiling["binding"] == "compressor" and exact == pytest.approx(top, rel=1e-6)
    for request in (exact, math.nextafter(exact, math.inf)):
        row = _steady(unit, request, **room)
        _assert_ceiling_row(row, request, ceiling)
        assert row["Q_unmet [W]"] == 0.0
    # Under it, by one float step or by 1e-5: met in full by cycling at that state, as before.
    for request in (math.nextafter(exact, 0.0), exact * (1.0 - 1e-5)):
        row = _steady(unit, request, **room)
        assert row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
        assert row["Q_ref_iu [W]"] == pytest.approx(request, rel=1e-12) and row["Q_unmet [W]"] == 0.0
        assert row["Q_min [W]"] == exact


@pytest.mark.parametrize("humidity", [None, 0.0])
def test_dry_request_at_its_ceiling_does_not_take_the_wet_rule(humidity, monkeypatch):
    # Without a room humidity, and with dry room air, a request at the ceiling is answered by the
    # dry model's own searches (issue #205 leaves them as they are): the ceiling is taken for a
    # request strictly above it only, and one equal to it is searched for a duty under it.
    unit = _build(hp_capacity=2000.0)
    ceiling = unit.max_capacity(20.0, 20.0, mode="cooling", RH_room=humidity)
    top = ceiling["Q_max [W]"]
    assert ceiling["binding"] == "compressor"
    searched = []
    original = unit._largest_deliverable_duty

    def noting(sign, q_top, *args):
        searched.append(q_top)
        return original(sign, q_top, *args)

    monkeypatch.setattr(unit, "_largest_deliverable_duty", noting)
    row = _steady(unit, top, 20.0, 20.0, RH_room=humidity)
    assert searched == [top]
    assert row["failure_reason"] == "none"
    reference = _steady(_build(hp_capacity=2000.0), top, 20.0, 20.0)
    humidity_keys = {"RH_room [-]", "w_iu_a_in [kg/kg]", "w_iu_a_out [kg/kg]"}
    for key in reference:
        if key not in humidity_keys and reference[key] == reference[key]:
            assert row[key] == reference[key], key


def test_dynamic_rows_at_and_around_the_ceiling_do_not_depend_on_the_steps_before(hot_day):
    # The ceiling, a hair under it, a request the search delivers and one above the ceiling, in
    # both orders and each alone: every row is that of its step solved alone, and the steady row.
    unit, ceiling = hot_day
    top = ceiling["Q_max [W]"]
    room = (46.0, 27.0, 0.3)
    loads = (5000.0, top * (1.0 - 1e-5), top, top * (1.0 - 1e-3), 1.05 * top)
    as_given = _rows_by_load(_dynamic(unit, loads, *room), loads)
    reverse = loads[::-1]
    reversed_rows = _rows_by_load(_dynamic(unit, reverse, *room), reverse)
    for load in loads:
        alone = _rows_by_load(_dynamic(unit, [load], *room), [load])[load]
        _assert_same_row(as_given[load], alone)
        _assert_same_row(reversed_rows[load], alone)
        steady = pd.Series(_steady(unit, load, room[0], room[1], RH_room=room[2]), dtype=object)
        _assert_same_row(alone, steady)
    for load in loads[1:3]:
        assert as_given[load]["capacity_clamped"] == "max" and as_given[load]["Q_ref_iu [W]"] == top
        assert as_given[load]["Q_unmet [W]"] == 0.0
