"""A request the discharge-temperature limit refuses only because it is small.

At a large lift the isentropic efficiency falls towards low speed. Before the
compressor reaches its minimum speed the discharge then runs above
``T_dis_max`` even with the discharge-temperature control used up. Requests
below the duty at which the envelope lets go came back with the unit switched
off (``failure_reason == "discharge_temp_above_max"``) although a larger duty
ran: 73 points of the boilers' steady sweep, and the air-to-air unit with
R407C at -25 degC.

They are met like a request below the minimum speed: by on/off cycling at the
smallest duty the envelope admits, the loss charged with the EN 14825
degradation coefficient. A condition at which no duty runs stays the off state
with its reason.

The scenarios are pinned on the machines a bare constructor built before the
semi-empirical compressor models (``tests/conftest.py``, marker
``earlier_defaults``): where the discharge limit is met depends on the
efficiency map. The mechanism is the library's and does not.
"""

import contextlib
import io
import math
import warnings

import numpy as np
import pytest

from tmhp import (
    AirSourceHeatPump,
    AirSourceHeatPumpBoiler,
    GroundSourceHeatPumpBoiler,
    WaterSourceHeatPumpBoiler,
)
from tmhp.compressor_envelope import (
    DISCHARGE_TEMP_ABOVE_MAX,
    DISCHARGE_TOL_K,
    raise_duty_within_envelope,
    reduce_duty_within_envelope,
    remembering_smallest_duties,
)
from tmhp.cycling import (
    CAPACITY_RATIO_KEY,
    CYCLING_LOSS_KEY,
    MIN_CAPACITY_KEY,
    PART_LOAD_FACTOR_KEY,
    RUNTIME_FRACTION_KEY,
)

pytestmark = pytest.mark.earlier_defaults  # scenarios pinned on the earlier default machine: tests/conftest.py

#: R134a air-source boiler: the discharge limit refuses the small duties, and
#: continuous operation begins at a few kilowatts.
COLD = dict(T_tank_w=55.0, T0=-7.0)


def solve(model, Q_ref_tank, **point):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return model.analyze_steady(Q_ref_tank=Q_ref_tank, **point)


def solve_request_only(model, Q_ref_tank, **point):
    """The request's own solve: no duty reduction, no on/off mean, no frost average."""
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return model.analyze_steady.__wrapped__(model, Q_ref_tank=Q_ref_tank, **point)


@pytest.fixture(scope="module")
def unit():
    """Frost-free, so that a row's heat is the cycle's."""
    return AirSourceHeatPumpBoiler(ref="R134a", defrost=False)


@pytest.fixture(scope="module")
def row(unit):
    return solve(unit, 750.0, **COLD)


def test_the_request_itself_is_refused_for_the_discharge_temperature(unit):
    refused = solve_request_only(unit, 750.0, **COLD)
    assert not refused["hp_is_on"] and refused["failure_reason"] == DISCHARGE_TEMP_ABOVE_MAX


def test_small_request_is_met_by_cycling_at_the_smallest_duty_inside_the_envelope(unit, row):
    assert row["converged"] and row["hp_is_on"] and row["failure_reason"] == "none"
    # The same keys and meanings as a minimum-speed row.
    assert row["capacity_clamped"] == "min" and row["capacity_binding"] is None
    assert row["Q_ref_tank [W]"] == pytest.approx(750.0, rel=1e-12)
    assert row["Q_request [W]"] == 750.0
    assert row["PLR_request [-]"] == pytest.approx(750.0 / unit.hp_capacity, rel=1e-12)
    floor = row[MIN_CAPACITY_KEY]
    assert 750.0 < floor < unit.hp_capacity
    cr, plf = row[CAPACITY_RATIO_KEY], row[PART_LOAD_FACTOR_KEY]
    assert cr == pytest.approx(750.0 / floor, rel=1e-12)
    # EN 14825, a unit delivering to water (Cc = 0.9): the compressor runs CR of the time.
    assert plf == pytest.approx(cr / (0.9 * cr + 0.1), rel=1e-12)
    assert row[RUNTIME_FRACTION_KEY] == pytest.approx(cr, rel=1e-12)
    assert row["cop_sys [-]"] == pytest.approx(750.0 / row["E_tot [W]"], rel=1e-12)
    assert row[CYCLING_LOSS_KEY] > 0.0
    # The running machine is inside the envelope and above the speed floor.
    assert row["T_ref_cmp_out [°C]"] <= unit.T_dis_max + DISCHARGE_TOL_K
    assert row["cmp_rpm [rpm]"] / 60.0 > unit.rps_min + 1.0


def test_the_floor_is_where_continuous_operation_begins(unit, row):
    floor = row[MIN_CAPACITY_KEY]
    above = solve(unit, 1.02 * floor, **COLD)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] is None
    assert above[RUNTIME_FRACTION_KEY] == 1.0 and math.isnan(above[MIN_CAPACITY_KEY])
    assert above[CYCLING_LOSS_KEY] == 0.0
    below = solve(unit, 0.97 * floor, **COLD)
    # The same floor for every request below it: the search does not start from the request.
    assert below["capacity_clamped"] == "min" and below[MIN_CAPACITY_KEY] == pytest.approx(floor, rel=1e-9)
    # Input per unit of heat is continuous through the hand-over (the cycling loss vanishes at CR = 1).
    specific_below = below["E_tot [W]"] / below["Q_ref_tank [W]"]
    specific_above = above["E_tot [W]"] / above["Q_ref_tank [W]"]
    assert specific_below == pytest.approx(specific_above, rel=2e-2)
    # And the mean input rises with the request below it.
    assert row["E_tot [W]"] < below["E_tot [W]"] < above["E_tot [W]"]


def test_cycling_never_beats_the_continuous_point_it_cycles_at(unit, row):
    """The mean COP is the running machine's times a part-load factor of at most 1, falling with the request."""
    continuous = solve(AirSourceHeatPumpBoiler(ref="R134a", defrost=False, Cd_cycling=None), 750.0, **COLD)
    assert continuous["capacity_clamped"] == "min"
    assert continuous["Q_ref_tank [W]"] == pytest.approx(row[MIN_CAPACITY_KEY], rel=1e-9)  # more than the 750 W asked
    assert continuous["Q_request [W]"] == 750.0
    assert continuous[RUNTIME_FRACTION_KEY] == 1.0 and continuous[CAPACITY_RATIO_KEY] == 1.0
    cop_running = continuous["cop_sys [-]"]
    floor = row[MIN_CAPACITY_KEY]
    cops = [solve(unit, share * floor, **COLD)["cop_sys [-]"] for share in (0.02, 0.1, 0.3, 0.6, 0.9, 0.999)]
    assert all(cop <= cop_running * (1.0 + 1e-12) for cop in cops)
    assert cops == sorted(cops)
    assert cops[-1] == pytest.approx(cop_running, rel=1e-3)


def test_default_unit_with_frost_delivers_the_request_and_searches_the_floor_once():
    """Frost on: the frost balance solves the condition at several duties, the floor is searched once."""
    boiler = AirSourceHeatPumpBoiler(ref="R134a")
    solved = []
    original = boiler._optimize_operation

    def counted(T_tank_w, Q_ref_tank, T0, **kwargs):
        solved.append(float(Q_ref_tank))
        return original(T_tank_w, Q_ref_tank, T0, **kwargs)

    boiler._optimize_operation = counted
    try:
        frosted = solve(boiler, 750.0, **COLD)
    finally:
        del boiler._optimize_operation
    assert frosted["failure_reason"] == "none" and frosted["capacity_clamped"] == "min"
    assert frosted["Q_ref_tank [W]"] == pytest.approx(750.0, rel=1e-9)
    assert 0.0 < frosted[RUNTIME_FRACTION_KEY] < 1.0
    assert frosted["defrost_fraction [-]"] > 0.0 and frosted["Q_ref_tank_frost_free [W]"] > 750.0
    # A ladder rung (0.2 of the nameplate) is solved by the one search only; the
    # other duties of the frost balance reuse the floor it found.
    assert solved.count(0.2 * boiler.hp_capacity) == 1
    # The memory ends with the call (the model may be changed between calls).
    assert boiler._smallest_duty_memo is None


def test_speed_floor_cycling_is_unchanged():
    """Where the minimum speed is inside the envelope it is still the lower end."""
    mild = AirSourceHeatPumpBoiler(ref="R32", defrost=False)
    row = solve(mild, 750.0, T_tank_w=40.0, T0=-15.0)
    assert row["capacity_clamped"] == "min" and row["cmp_rpm [rpm]"] / 60.0 == pytest.approx(mild.rps_min)


@pytest.mark.parametrize(
    ("cls", "ctor", "point"),
    [
        (GroundSourceHeatPumpBoiler, dict(ref="R134a"), dict(T_tank_w=75.0, T_source=5.0, T0=10.0)),
        (WaterSourceHeatPumpBoiler, dict(ref="R134a"), dict(T_tank_w=75.0, T_source=5.0, T0=10.0)),
    ],
    ids=["GSHPB", "WSHPB"],
)
def test_source_boilers_share_the_rule(cls, ctor, point):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = cls(**ctor)
    assert solve_request_only(model, 400.0, **point)["failure_reason"] == DISCHARGE_TEMP_ABOVE_MAX
    row = solve(model, 400.0, **point)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
    assert row["Q_ref_tank [W]"] == pytest.approx(400.0, rel=1e-9)
    floor = row[MIN_CAPACITY_KEY]
    assert row[RUNTIME_FRACTION_KEY] == pytest.approx(400.0 / floor, rel=1e-9)
    assert row["T_ref_cmp_out [°C]"] <= model.T_dis_max + DISCHARGE_TOL_K
    # At the floor the unit runs continuously.
    above = solve(model, 1.02 * floor, **point)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] is None


def test_a_condition_at_which_no_duty_runs_stays_off_with_its_reason():
    """The discharge limit refuses every duty: there is no machine to cycle."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        hot = AirSourceHeatPumpBoiler(ref="R134a", defrost=False, T_dis_max=60.0)
    for request in (750.0, 7500.0, 15000.0):
        refused = solve(hot, request, **COLD)
        assert not refused["hp_is_on"] and refused["failure_reason"] == DISCHARGE_TEMP_ABOVE_MAX
        assert refused["Q_ref_tank [W]"] == 0.0 and refused[RUNTIME_FRACTION_KEY] == 0.0
        assert refused["capacity_clamped"] is None


# ---------------------------------------------------------------------------
# AirSourceHeatPump (its own search)
# ---------------------------------------------------------------------------

ARCTIC = dict(T0=-25.0, T_a_room=21.0, verbose=False)


@pytest.fixture(scope="module")
def room_unit():
    return AirSourceHeatPump(ref="R407C", defrost=None)


def test_air_to_air_unit_cycles_at_the_smallest_duty_inside_the_envelope(room_unit):
    refused = room_unit._optimize_operation(Q_r_iu=-200.0, T0=-25.0, T_a_room=21.0)
    assert refused.least_lift_reason == DISCHARGE_TEMP_ABOVE_MAX
    row = room_unit.analyze_steady(Q_r_iu=-200.0, **ARCTIC)
    assert row["converged"] and row["hp_is_on"] and row["failure_reason"] == "none"
    assert row["capacity_clamped"] == "min"
    assert row["Q_ref_iu [W]"] == pytest.approx(200.0, rel=1e-12)
    assert row["Q_request [W]"] == -200.0 and row["Q_unmet [W]"] == 0.0
    floor = row[MIN_CAPACITY_KEY]
    assert 200.0 < floor < room_unit.hp_capacity
    cr, plf = row[CAPACITY_RATIO_KEY], row[PART_LOAD_FACTOR_KEY]
    assert cr == pytest.approx(200.0 / floor, rel=1e-12)
    # EN 14825, a unit delivering to air (Cd = 0.25).
    assert plf == pytest.approx(1.0 - 0.25 * (1.0 - cr), rel=1e-12)
    assert row[RUNTIME_FRACTION_KEY] == pytest.approx(cr / plf, rel=1e-12)
    assert row[CYCLING_LOSS_KEY] > 0.0
    assert row["T_ref_cmp_out [°C]"] <= room_unit.T_dis_max + DISCHARGE_TOL_K
    assert row["cmp_rpm [rpm]"] / 60.0 > room_unit.rps_min + 1.0


def test_air_to_air_floor_is_where_continuous_operation_begins(room_unit):
    row = room_unit.analyze_steady(Q_r_iu=-200.0, **ARCTIC)
    floor = row[MIN_CAPACITY_KEY]
    above = room_unit.analyze_steady(Q_r_iu=-1.02 * floor, **ARCTIC)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] is None
    assert above[RUNTIME_FRACTION_KEY] == 1.0
    below = room_unit.analyze_steady(Q_r_iu=-0.97 * floor, **ARCTIC)
    assert below["capacity_clamped"] == "min" and below[MIN_CAPACITY_KEY] == pytest.approx(floor, rel=1e-2)
    specific_below = below["E_tot [W]"] / below["Q_ref_iu [W]"]
    specific_above = above["E_tot [W]"] / above["Q_ref_iu [W]"]
    assert specific_below == pytest.approx(specific_above, rel=2e-2)
    assert row["E_tot [W]"] < below["E_tot [W]"] < above["E_tot [W]"]
    # Without the cycling model the unit runs continuously at the floor: more than the 200 W asked for.
    continuous = AirSourceHeatPump(ref="R407C", defrost=None, Cd_cycling=None).analyze_steady(Q_r_iu=-200.0, **ARCTIC)
    assert continuous["failure_reason"] == "none" and continuous["capacity_clamped"] == "min"
    assert continuous["Q_ref_iu [W]"] == pytest.approx(floor, rel=1e-2)
    assert continuous[RUNTIME_FRACTION_KEY] == 1.0 and continuous[CAPACITY_RATIO_KEY] == 1.0
    # The mean COP does not exceed the COP of the machine it cycles at.
    assert row["cop_sys [-]"] < below["cop_sys [-]"] <= continuous["cop_sys [-]"] * (1.0 + 1e-9)


def test_air_to_air_default_unit_with_frost_delivers_the_request_too():
    frosted = AirSourceHeatPump(ref="R407C").analyze_steady(Q_r_iu=-200.0, **ARCTIC)
    assert frosted["failure_reason"] == "none" and frosted["capacity_clamped"] == "min"
    assert frosted["Q_ref_iu [W]"] == pytest.approx(200.0, rel=1e-9)
    assert 0.0 < frosted[RUNTIME_FRACTION_KEY] < 1.0


def test_air_to_air_request_no_larger_duty_cures_keeps_its_reason(room_unit):
    """The floor search only climbs while the discharge limit is what refuses the duty."""
    calls = []
    original = room_unit._optimize_operation

    def refused_for_another_limit(Q_r_iu, T0, T_a_room):
        calls.append(abs(Q_r_iu))
        result = original(Q_r_iu=Q_r_iu, T0=T0, T_a_room=T_a_room)
        if len(calls) > 1 and getattr(result, "least_lift_reason", None) == DISCHARGE_TEMP_ABOVE_MAX:
            result["least_lift_reason"] = "pr_above_max"
        return result

    room_unit._optimize_operation = refused_for_another_limit
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            refused = room_unit.analyze_steady(Q_r_iu=-100.0, **ARCTIC)
    finally:
        del room_unit._optimize_operation
    assert not refused["hp_is_on"] and refused["failure_reason"] == DISCHARGE_TEMP_ABOVE_MAX
    assert refused["Q_ref_iu [W]"] == 0.0 and refused[RUNTIME_FRACTION_KEY] == 0.0


def test_air_to_air_dynamic_run_searches_the_floor_once_per_condition(room_unit):
    searches = []
    original = room_unit._optimize_operation

    def counted(Q_r_iu, T0, T_a_room):
        searches.append(abs(Q_r_iu))
        return original(Q_r_iu=Q_r_iu, T0=T0, T_a_room=T_a_room)

    room_unit._optimize_operation = counted
    try:
        load = np.array([-200.0, -300.0, -400.0])
        df = room_unit.analyze_dynamic(3 * 3600, 3600, load, np.full(3, -25.0), np.full(3, 21.0))
    finally:
        del room_unit._optimize_operation
    assert (df["failure_reason"] == "none").all() and (df["capacity_clamped"] == "min").all()
    assert df["Q_ref_iu [W]"].to_numpy() == pytest.approx(-load, rel=1e-12)
    assert df[MIN_CAPACITY_KEY].nunique() == 1
    # One search of the floor (the first step); the later steps add their own request only.
    after_first = searches[searches.index(300.0) :]
    assert after_first == [300.0, 400.0]
    assert df["E_tot [W]"].is_monotonic_increasing


def test_air_to_air_speed_floor_cycling_is_unchanged():
    """Where the minimum speed is inside the envelope it is still the lower end."""
    mild = AirSourceHeatPump(defrost=None)
    row = mild.analyze_steady(Q_r_iu=-200.0, T0=7.0, T_a_room=20.0, verbose=False)
    assert row["capacity_clamped"] == "min" and row["cmp_rpm [rpm]"] / 60.0 == pytest.approx(mild.rps_min)


def test_air_to_air_dynamic_run_gives_the_steady_rows(room_unit):
    """A dynamic run solves each step like ``analyze_steady``: the same cycling rows, one floor per condition."""
    load = np.array([-200.0, -500.0, -800.0, -1500.0])
    df = room_unit.analyze_dynamic(4 * 3600, 3600, load, np.full(4, -25.0), np.full(4, 21.0))
    assert (df["failure_reason"] == "none").all()
    assert list(df["capacity_clamped"].iloc[:3]) == ["min"] * 3 and df[MIN_CAPACITY_KEY].iloc[:3].nunique() == 1
    assert df["Q_ref_iu [W]"].to_numpy() == pytest.approx(-load, rel=1e-12)
    assert df[RUNTIME_FRACTION_KEY].iloc[3] == 1.0 and df[CYCLING_LOSS_KEY].iloc[3] == 0.0
    for step, request in enumerate(load):
        steady = room_unit.analyze_steady(Q_r_iu=float(request), **ARCTIC)
        assert df["E_tot [W]"].iloc[step] == pytest.approx(steady["E_tot [W]"], rel=1e-9)
    assert df["E_tot [W]"].is_monotonic_increasing


def test_boiler_dynamic_step_runs_at_the_nameplate_duty_and_is_not_refused():
    """The thermostat runs the compressor at its nameplate duty: above the smallest duty, nothing to cycle."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        boiler = AirSourceHeatPumpBoiler(ref="R134a", T_tank_w_lower_bound=50.0, T_tank_w_upper_bound=55.0)
        with contextlib.redirect_stdout(io.StringIO()):
            df = boiler.analyze_dynamic(
                simulation_period_sec=6 * 600,
                dt_s=600,
                T_tank_w_init_C=49.0,
                dhw_usage_schedule=np.zeros(6),
                T0_schedule=np.full(6, -7.0),
            )
    assert (df["failure_reason"] == "none").all() and df["hp_is_on"].iloc[0]
    # The same condition, a small request: the steady row is the on/off mean at a duty below the step's.
    small = solve(boiler, 750.0, T_tank_w=54.0, T0=-7.0)
    assert small["capacity_clamped"] == "min"
    assert small[MIN_CAPACITY_KEY] < df["Q_ref_tank_frost_free [W]"].iloc[0] / df[RUNTIME_FRACTION_KEY].iloc[0]


# ---------------------------------------------------------------------------
# The search itself, on a stand-in machine (no cycle solves)
# ---------------------------------------------------------------------------


class _Machine:
    """Runs between ``floor`` and ``top`` [W]: the discharge limit refuses below, ``top_reason`` above.

    ``ceiling`` is what the compressor delivers at its speed bound: a duty above it runs, delivers the ceiling
    and is tagged ``capacity_clamped='max'``.
    """

    hp_capacity = 10000.0

    def __init__(self, floor, top=math.inf, top_reason="pr_above_max", ceiling=math.inf):
        self.floor, self.top, self.top_reason, self.ceiling = floor, top, top_reason, ceiling
        self.solved = []

    def solve(self, q):
        self.solved.append(q)
        # The row's request keys hold the duty tried; the search restores the caller's request in them.
        row = {"Q_request [W]": q, "PLR_request [-]": q / self.hp_capacity, "Q_ref_tank [W]": 0.0, "hp_is_on": False}
        if q < self.floor:
            return {**row, "failure_reason": DISCHARGE_TEMP_ABOVE_MAX}
        if q > self.top:
            return {**row, "failure_reason": self.top_reason}
        return {
            **row,
            "hp_is_on": True,
            "failure_reason": "none",
            "Q_ref_tank [W]": min(q, self.ceiling),
            "capacity_clamped": "max" if q > self.ceiling else None,
        }


def _found(machine, request):
    return raise_duty_within_envelope(machine, machine.solve(request), machine.solve, request)


def test_search_climbs_over_a_discharge_refusal():
    machine = _Machine(floor=2345.0)
    found = _found(machine, 300.0)
    assert found["failure_reason"] == "none" and found["capacity_clamped"] == "min"
    assert 2345.0 <= found["Q_ref_tank [W]"] <= 2345.0 * 1.005 + 1e-9
    assert found["Q_request [W]"] == 300.0 and found["PLR_request [-]"] == pytest.approx(0.03)
    # No trial at or below the request, and every request below the floor finds the same row.
    assert min(machine.solved[1:]) > 300.0
    assert _found(machine, 2000.0)["Q_ref_tank [W]"] == found["Q_ref_tank [W]"]


@pytest.mark.parametrize("top_reason", ["pr_above_max", "hx_not_converged", "condensing_temp_above_max"])
def test_search_finds_duties_that_run_between_two_rungs(top_reason):
    """Refused from below at the 0.4 rung and from above at the 0.6 rung: the duties that run lie between."""
    machine = _Machine(floor=4300.0, top=4700.0, top_reason=top_reason)
    found = _found(machine, 300.0)
    assert found["failure_reason"] == "none" and found["capacity_clamped"] == "min"
    assert 4300.0 <= found["Q_ref_tank [W]"] <= 4300.0 * 1.005 + 1e-9
    assert _found(machine, 3000.0)["Q_ref_tank [W]"] == found["Q_ref_tank [W]"]


def test_search_returns_the_refusal_when_the_two_sides_meet():
    machine = _Machine(floor=4500.0, top=4400.0)
    refused = machine.solve(300.0)
    assert raise_duty_within_envelope(machine, refused, machine.solve, 300.0) is refused
    # Another limit refuses every duty above the request: the request keeps its reason.
    blocked = _Machine(floor=2345.0, top=900.0)
    refused = blocked.solve(300.0)
    assert raise_duty_within_envelope(blocked, refused, blocked.solve, 300.0) is refused
    # Nothing runs up to twice the nameplate.
    never = _Machine(floor=1e9)
    refused = never.solve(300.0)
    assert raise_duty_within_envelope(never, refused, never.solve, 300.0) is refused


def test_search_leaves_a_refusal_from_above_alone():
    machine = _Machine(floor=0.0, top=100.0)
    refused = machine.solve(300.0)
    assert refused["failure_reason"] == "pr_above_max"
    assert raise_duty_within_envelope(machine, refused, machine.solve, 300.0) is refused
    assert machine.solved == [300.0]
    # A refusal a larger duty does not cure, and a row that runs, are returned without a trial.
    untouched = _Machine(floor=2345.0)
    for reason in ("pr_above_max", "condensing_temp_above_max", "none"):
        row = {"hp_is_on": False, "failure_reason": reason}
        assert raise_duty_within_envelope(untouched, row, untouched.solve, 300.0) is row
    assert untouched.solved == []


def test_search_stops_below_the_compressor_ceiling():
    """A rung above the speed ceiling delivers less than its duty: it bounds the search, it is not the floor."""
    machine = _Machine(floor=2345.0, ceiling=2600.0)
    found = _found(machine, 300.0)
    assert found["capacity_clamped"] == "min" and 2345.0 <= found["Q_ref_tank [W]"] <= 2345.0 * 1.005 + 1e-9
    # A machine whose ceiling is below its floor has no continuous duty at all.
    none = _Machine(floor=2345.0, ceiling=2000.0)
    refused = none.solve(300.0)
    assert raise_duty_within_envelope(none, refused, none.solve, 300.0) is refused


def test_search_is_remembered_per_condition_inside_the_block_only():
    machine = _Machine(floor=2345.0)
    with remembering_smallest_duties(machine):
        first = raise_duty_within_envelope(machine, machine.solve(300.0), machine.solve, 300.0, condition=("A", 1))
        count = len(machine.solved)
        again = raise_duty_within_envelope(machine, machine.solve(900.0), machine.solve, 900.0, condition=("A", 1))
        assert len(machine.solved) == count + 1  # the request's own solve only
        assert again["Q_ref_tank [W]"] == first["Q_ref_tank [W]"] and again["Q_request [W]"] == 900.0
        assert first["Q_request [W]"] == 300.0  # the remembered row is not the one handed out
        raise_duty_within_envelope(machine, machine.solve(300.0), machine.solve, 300.0, condition=("B", 1))
        assert len(machine.solved) > count + 3  # another condition is searched
    assert machine._smallest_duty_memo is None
    count = len(machine.solved)
    raise_duty_within_envelope(machine, machine.solve(300.0), machine.solve, 300.0, condition=("A", 1))
    assert len(machine.solved) > count + 3


def test_reduction_from_above_finds_the_duties_between_the_two_limits():
    """A request refused from above, with the discharge limit under the duties that run: no ladder rung runs."""
    machine = _Machine(floor=2700.0, top=2800.0)
    refused = machine.solve(10000.0)
    assert refused["failure_reason"] == "pr_above_max"
    # The ladder's rungs are refused for the pressure ratio (4000 W) and the discharge (2500 W); the
    # bisection between them goes by which limit refuses the trial until one runs.
    reduced = reduce_duty_within_envelope(machine, refused, machine.solve, 10000.0)
    assert reduced["failure_reason"] == "none" and reduced["capacity_clamped"] == "max"
    assert reduced["capacity_binding"] == "pressure_ratio"
    assert 2700.0 <= reduced["Q_ref_tank [W]"] <= 2800.0 and reduced["Q_max [W]"] == reduced["Q_ref_tank [W]"]
    assert reduced["Q_request [W]"] == 10000.0
    # Both limits refused a trial on the way.
    trials = list(machine.solved)
    reasons = {machine.solve(q)["failure_reason"] for q in trials}
    assert {"pr_above_max", DISCHARGE_TEMP_ABOVE_MAX} <= reasons
