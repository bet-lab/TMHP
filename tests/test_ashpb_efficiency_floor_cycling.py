"""A request the efficiency floor refuses only because it is small: ``AirSourceHeatPumpBoiler``.

The envelope refuses a point at which the shipped isentropic fit, read below
its data, is under ``ETA_ISEN_MIN`` (0.30). At a large lift that happens above
the minimum compressor speed: the default R134a boiler at -15 °C outdoors and
a 40 °C tank runs continuously down to about 2.5 kW and no further. Requests
below that duty came back with the unit switched off
(``failure_reason == "eta_isen_below_min"``: 0.75-2.25 kW) although 3 kW ran.

They are met like a request below the minimum speed: by on/off cycling at the
smallest duty the envelope admits (:func:`tmhp.compressor_envelope.raise_duty_within_envelope`,
the counterpart of ``AirSourceHeatPump``'s own search).
"""

import contextlib
import io
import math
import warnings

import pytest

from tmhp import AirSourceHeatPumpBoiler
from tmhp.compressor_envelope import (
    _REQUEST_KEYS,
    ETA_ISEN_BELOW_MIN,
    ETA_ISEN_MIN,
    raise_duty_within_envelope,
    remembering_smallest_duties,
)
from tmhp.cycling import CAPACITY_RATIO_KEY, MIN_CAPACITY_KEY, PART_LOAD_FACTOR_KEY, RUNTIME_FRACTION_KEY

COLD = dict(T_tank_w=40.0, T0=-15.0)


def solve(model, Q_ref_tank, **point):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return model.analyze_steady(Q_ref_tank=Q_ref_tank, **point)


@pytest.fixture(scope="module")
def unit():
    """Frost-free, so that a row's heat is the cycle's (the frost average is checked separately)."""
    return AirSourceHeatPumpBoiler(ref="R134a", defrost=False)


@pytest.fixture(scope="module")
def row(unit):
    return solve(unit, 750.0, **COLD)


def test_small_request_is_met_by_cycling_at_the_smallest_duty_inside_the_envelope(unit, row):
    assert row["converged"] and row["hp_is_on"] and row["failure_reason"] == "none"
    assert row["capacity_clamped"] == "min" and row["capacity_binding"] is None
    assert row["Q_ref_tank [W]"] == pytest.approx(750.0, rel=1e-12)
    assert row["Q_request [W]"] == 750.0
    assert row["PLR_request [-]"] == pytest.approx(750.0 / unit.hp_capacity, rel=1e-12)
    floor = row[MIN_CAPACITY_KEY]
    assert 2300.0 < floor < 2600.0
    cr, plf = row[CAPACITY_RATIO_KEY], row[PART_LOAD_FACTOR_KEY]
    assert cr == pytest.approx(750.0 / floor, rel=1e-12)
    # EN 14825, a unit delivering to water (Cc = 0.9): the compressor runs CR of the time.
    assert plf == pytest.approx(cr / (0.9 * cr + 0.1), rel=1e-12)
    assert row[RUNTIME_FRACTION_KEY] == pytest.approx(cr, rel=1e-12)
    assert row["cop_sys [-]"] == pytest.approx(750.0 / row["E_tot [W]"], rel=1e-12)
    # The running machine is inside the envelope, on the efficiency floor and above the speed floor.
    assert ETA_ISEN_MIN <= row["eta_cmp_isen [-]"] < ETA_ISEN_MIN + 0.01
    assert row["cmp_rpm [rpm]"] / 60.0 > unit.rps_min + 1.0


def test_the_floor_is_where_continuous_operation_begins(unit, row):
    floor = row[MIN_CAPACITY_KEY]
    above = solve(unit, 1.02 * floor, **COLD)
    assert above["failure_reason"] == "none" and above["capacity_clamped"] is None
    assert above[RUNTIME_FRACTION_KEY] == 1.0 and math.isnan(above[MIN_CAPACITY_KEY])
    below = solve(unit, 0.97 * floor, **COLD)
    # The same floor for every request below it: the search does not start from the request.
    assert below["capacity_clamped"] == "min" and below[MIN_CAPACITY_KEY] == pytest.approx(floor, rel=1e-9)
    # Input per unit of heat is continuous through the hand-over (the cycling loss vanishes at CR = 1).
    specific_below = below["E_tot [W]"] / below["Q_ref_tank [W]"]
    specific_above = above["E_tot [W]"] / above["Q_ref_tank [W]"]
    assert specific_below == pytest.approx(specific_above, rel=2e-2)
    # And the mean input rises with the request below it.
    assert row["E_tot [W]"] < below["E_tot [W]"] < above["E_tot [W]"]


def test_without_the_cycling_model_the_unit_runs_continuously_at_the_floor(row):
    continuous = solve(AirSourceHeatPumpBoiler(ref="R134a", defrost=False, Cd_cycling=None), 750.0, **COLD)
    assert continuous["failure_reason"] == "none" and continuous["capacity_clamped"] == "min"
    assert continuous["Q_ref_tank [W]"] == pytest.approx(
        row[MIN_CAPACITY_KEY], rel=1e-9
    )  # more than the 750 W asked for
    assert continuous["Q_request [W]"] == 750.0
    assert continuous[RUNTIME_FRACTION_KEY] == 1.0 and continuous[CAPACITY_RATIO_KEY] == 1.0


def test_default_unit_with_frost_delivers_the_request_and_searches_the_floor_once():
    """R290 at A-7/W65 (frost on): the frost balance solves the condition at several duties."""
    frosted = AirSourceHeatPumpBoiler(ref="R290")
    solved = []
    original = frosted._optimize_operation

    def counted(T_tank_w, Q_ref_tank, T0, **kwargs):
        solved.append(float(Q_ref_tank))
        return original(T_tank_w, Q_ref_tank, T0, **kwargs)

    frosted._optimize_operation = counted
    try:
        row = solve(frosted, 750.0, T_tank_w=65.0, T0=-7.0)
    finally:
        del frosted._optimize_operation
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "min"
    assert row["Q_ref_tank [W]"] == pytest.approx(750.0, rel=1e-9)
    assert 0.0 < row[RUNTIME_FRACTION_KEY] < 1.0
    assert row["defrost_fraction [-]"] > 0.0 and row["Q_ref_tank_frost_free [W]"] > 750.0
    # A ladder rung (0.2 of the nameplate) is solved by the one search only; the
    # other duties of the frost balance reuse the floor it found.
    assert solved.count(0.2 * frosted.hp_capacity) == 1
    # The memory ends with the call (the model may be changed between calls).
    assert frosted._smallest_duty_memo is None


def test_speed_floor_cycling_is_unchanged():
    """Where the minimum speed is inside the envelope it is still the lower end."""
    mild = AirSourceHeatPumpBoiler(ref="R32", defrost=False)
    row = solve(mild, 750.0, **COLD)
    assert row["capacity_clamped"] == "min" and row["cmp_rpm [rpm]"] / 60.0 == pytest.approx(mild.rps_min)
    assert row["eta_cmp_isen [-]"] > ETA_ISEN_MIN + 0.05


# ---------------------------------------------------------------------------
# The search itself, on a stand-in machine (no cycle solves)
# ---------------------------------------------------------------------------


class _Machine:
    """Runs between ``floor`` and ``ceiling`` [W]; below the floor the efficiency floor refuses the duty."""

    hp_capacity = 10000.0

    def __init__(self, floor, ceiling=math.inf, other_limit_above=math.inf):
        self.floor, self.ceiling, self.other_limit_above = floor, ceiling, other_limit_above
        self.solved = []

    def solve(self, q):
        self.solved.append(q)
        # A family's own request key (whatever its name is): the search restores the caller's request in it.
        row = {_REQUEST_KEYS[0]: q, "PLR_request [-]": q / self.hp_capacity}
        if q < self.floor:
            return {**row, "hp_is_on": False, "failure_reason": ETA_ISEN_BELOW_MIN, "Q_ref_tank [W]": 0.0}
        if q > self.other_limit_above:
            return {**row, "hp_is_on": False, "failure_reason": "pr_above_max", "Q_ref_tank [W]": 0.0}
        clamped = "max" if q > self.ceiling else None
        return {
            **row,
            "hp_is_on": True,
            "failure_reason": "none",
            "Q_ref_tank [W]": min(q, self.ceiling),
            "capacity_clamped": clamped,
        }


def test_search_returns_the_smallest_running_duty_as_a_min_row():
    machine = _Machine(floor=2345.0)
    refused = machine.solve(300.0)
    found = raise_duty_within_envelope(machine, refused, machine.solve, 300.0)
    assert found["failure_reason"] == "none" and found["capacity_clamped"] == "min"
    assert 2345.0 <= found["Q_ref_tank [W]"] <= 2345.0 * 1.005 + 1e-9
    assert found["Q_request [W]"] == 300.0 and found[_REQUEST_KEYS[0]] == 300.0
    assert found["PLR_request [-]"] == pytest.approx(0.03)
    # No trial at or below the request, and every request below the floor finds the same row.
    assert min(machine.solved[1:]) > 300.0
    other = raise_duty_within_envelope(machine, machine.solve(2000.0), machine.solve, 2000.0)
    assert other["Q_ref_tank [W]"] == found["Q_ref_tank [W]"]


def test_search_leaves_other_refusals_and_other_limits_alone():
    machine = _Machine(floor=2345.0)
    for reason in ("discharge_temp_above_max", "pr_above_max", "none"):
        row = {"hp_is_on": False, "failure_reason": reason}
        assert raise_duty_within_envelope(machine, row, machine.solve, 300.0) is row
    assert machine.solved == []
    # Another limit refuses a duty above the request before one runs: the request keeps its reason.
    blocked = _Machine(floor=2345.0, other_limit_above=900.0)
    refused = blocked.solve(300.0)
    assert raise_duty_within_envelope(blocked, refused, blocked.solve, 300.0) is refused
    # No duty runs up to twice the nameplate.
    never = _Machine(floor=1e9)
    refused = never.solve(300.0)
    assert raise_duty_within_envelope(never, refused, never.solve, 300.0) is refused


def test_search_stops_below_the_compressor_ceiling():
    """A rung above the speed ceiling delivers less than its duty: it bounds the search, it is not the floor."""
    machine = _Machine(floor=2345.0, ceiling=2600.0)
    found = raise_duty_within_envelope(machine, machine.solve(300.0), machine.solve, 300.0)
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
