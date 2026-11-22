"""A request on the room's net sensible heat: ``request_basis="room_sensible"`` of ``AirSourceHeatPump`` (#219).

``analyze_steady`` and ``analyze_dynamic`` take the request as the
refrigerant-side heat of the indoor coil (``request_basis="coil"``, the
default). A building simulation hands over the zone's sensible load, which
differs from the coil's heat by the latent heat of a wet coil and, in both
modes, by the indoor fan's heat. With ``request_basis="room_sensible"`` the
model finds the coil duty at which ``Q_room_sensible [W]`` -- as the row
reports it, averaged over cycling and over frost and defrost -- equals the
request:

- the row is the coil-basis row of that duty, key for key; only
  ``Q_request [W]`` and ``Q_unmet [W]`` are in the requested basis, and
  ``request_basis`` says which it is;
- the match is to 1e-6 of the request where three solves reach it (a dry coil
  or heating, fans that modulate), and to 1e-4 otherwise (a row that
  condenses water, a fan held at one flow);
- above what the unit delivers flat out the row is the ceiling's and the rest
  is unmet, in the basis; ``max_capacity`` reports that ceiling as
  ``Q_room_sensible_at_Q_max [W]``;
- a row does not depend on what was solved before it;
- the row says how many coil-basis solves the search took
  (``request_solves [-]``, on a row of this basis only), twelve at most;
- a default run does not hand the option to ``analyze_steady``: a subclass
  that overrides it with the signature it had before runs as it did.

No test here looks at how the search calls the coil-basis solve: the row is
checked through the public round trip (a coil request for the ``Q_ref_iu [W]``
it reports repeats it) and the cost through the count the row carries.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import pytest

from tmhp import AirSourceHeatPump
from tmhp.operating_limits import OperatingLimitWarning

#: Match of ``Q_room_sensible [W]`` with the request, relative: a dry coil and heating.
DRY_TOL = 1e-6
#: The same on a row whose coil condenses water.
WET_TOL = 1e-4
#: Keys that are in the requested basis, or name it: the only ones a row may
#: differ in from the coil-basis row of the same coil duty.
BASIS_KEYS = frozenset({"Q_request [W]", "Q_unmet [W]", "request_basis"})
#: The most coil-basis solves one request may take (the search's budget).
MAX_SOLVES = 12
#: The count of those solves: on a row asked on the room's net sensible heat, and on no other.
SOLVES_KEY = "request_solves [-]"
#: Keys ``max_capacity`` returned before this option.
CEILING_KEYS = frozenset(
    {
        "Q_max [W]",
        "dT_ref_evap [K]",
        "dT_ref_cond [K]",
        "E_tot [W]",
        "cop_sys [-]",
        "binding",
        "mode",
        "T0 [°C]",
        "T_a_room [°C]",
        "Q_max_frost_free [W]",
        "defrost_fraction [-]",
        "SHR_iu [-]",
    }
)


@pytest.fixture(scope="module")
def hp() -> AirSourceHeatPump:
    return AirSourceHeatPump()


def _solve(model: AirSourceHeatPump, Q: float, T0: float, T_a_room: float, **kwargs) -> dict:
    """``analyze_steady`` without the operating-limit warning (the rows are the same)."""
    kwargs.setdefault("verbose", False)
    row = model.analyze_steady(Q, T0, T_a_room, **kwargs)
    assert isinstance(row, dict)
    return row


def _same_value(a: object, b: object) -> bool:
    """The same value to the last bit (NaN equal to NaN; a frame's ``np.float64`` or ``np.int64`` equal to the row's)."""
    if isinstance(a, bool | np.bool_) or isinstance(b, bool | np.bool_):
        return isinstance(a, bool | np.bool_) and isinstance(b, bool | np.bool_) and bool(a) == bool(b)
    if isinstance(a, int | np.integer) and isinstance(b, int | np.integer):
        return int(a) == int(b)
    if isinstance(a, float | np.floating) and isinstance(b, float | np.floating):
        return (math.isnan(a) and math.isnan(b)) or float(a) == float(b)
    return type(a) is type(b) and a == b


def _differing_keys(a: dict, b: dict, *, skip: frozenset[str] = BASIS_KEYS) -> list[str]:
    """Keys of both rows whose values are not the same object value, bit for bit (NaN equal to NaN).

    The rows have the same keys in the same order, but for the count of solves
    where one of them is a coil-basis row.
    """
    shared = [key for key in a if key in b]
    assert shared == [key for key in b if key in a], "the two rows do not have their keys in the same order"
    assert set(a) ^ set(b) <= {SOLVES_KEY}, "the two rows differ in more keys than the count of solves"
    return [key for key in shared if key not in skip and not _same_value(a[key], b[key])]


def _tolerance(row: dict) -> float:
    return WET_TOL if float(row["Q_iu_latent [W]"]) > 0.0 else DRY_TOL


# ---------------------------------------------------------------------------
# 1. The argument
# ---------------------------------------------------------------------------


def test_the_default_basis_is_the_coil_and_every_row_names_its_basis(hp: AirSourceHeatPump) -> None:
    default = _solve(hp, 4000.0, 35.0, 27.0, RH_room=0.5)
    named = _solve(hp, 4000.0, 35.0, 27.0, RH_room=0.5, request_basis="coil")
    assert default["request_basis"] == "coil"
    assert _differing_keys(default, named, skip=frozenset()) == []
    # The coil basis, as before: the request is the coil's total heat.
    assert default["Q_request [W]"] == 4000.0
    assert default["Q_ref_iu [W]"] == pytest.approx(4000.0, rel=1e-9)
    assert default["Q_unmet [W]"] == 0.0
    off = _solve(hp, 0.0, 35.0, 27.0)
    assert off["request_basis"] == "coil"


def test_the_number_of_solves_is_on_a_room_sensible_row_and_on_no_other(hp: AirSourceHeatPump) -> None:
    coil = _solve(hp, 4000.0, 35.0, 27.0, RH_room=0.5)
    assert SOLVES_KEY not in coil
    room = _solve(hp, 4000.0, 35.0, 27.0, RH_room=0.5, request_basis="room_sensible")
    # The coil-basis row's keys, in their order, and the count after them.
    assert list(room) == [*coil, SOLVES_KEY]
    assert type(room[SOLVES_KEY]) is int
    assert 2 <= room[SOLVES_KEY] <= MAX_SOLVES
    # One solve where the first trial is the answer: a request above the ceiling.
    assert _solve(hp, 9000.0, 35.0, 27.0, RH_room=0.5, request_basis="room_sensible")[SOLVES_KEY] == 1
    # A zero request is not searched.
    off = _solve(hp, 0.0, 35.0, 27.0, RH_room=0.5, request_basis="room_sensible")
    assert type(off[SOLVES_KEY]) is int
    assert off[SOLVES_KEY] == 0
    frame = hp.analyze_steady(4000.0, 35.0, 27.0, request_basis="room_sensible", return_dict=False, verbose=False)
    assert isinstance(frame, pd.DataFrame)
    assert frame[SOLVES_KEY].iloc[0] == 3


@pytest.mark.parametrize("basis", ["sensible", "Coil", "room", "", None, 1])
def test_an_unknown_basis_raises(hp: AirSourceHeatPump, basis: object) -> None:
    with pytest.raises(ValueError, match="request_basis"):
        hp.analyze_steady(4000.0, 35.0, 27.0, request_basis=basis)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="request_basis"):
        hp.analyze_dynamic(3600, 3600, [4000.0], [35.0], [27.0], request_basis=basis)  # type: ignore[arg-type]


def test_a_zero_request_is_the_unit_off_in_either_basis(hp: AirSourceHeatPump) -> None:
    coil = _solve(hp, 0.0, 35.0, 27.0, RH_room=0.5)
    room = _solve(hp, 0.0, 35.0, 27.0, RH_room=0.5, request_basis="room_sensible")
    assert room["request_basis"] == "room_sensible"
    assert room["hp_is_on"] is False
    assert room["Q_request [W]"] == 0.0
    assert room["Q_unmet [W]"] == 0.0
    assert room["Q_room_sensible [W]"] == 0.0
    assert _differing_keys(coil, room) == []


def test_a_frame_is_returned_on_request(hp: AirSourceHeatPump) -> None:
    frame = hp.analyze_steady(4000.0, 35.0, request_basis="room_sensible", return_dict=False, verbose=False)
    assert isinstance(frame, pd.DataFrame)
    assert len(frame) == 1
    assert frame["request_basis"].iloc[0] == "room_sensible"
    # ``T_a_room`` left out: the constructor's room (27 degC).
    assert frame["T_a_room [°C]"].iloc[0] == 27.0
    assert frame["Q_room_sensible [W]"].iloc[0] == pytest.approx(4000.0, rel=DRY_TOL)


# ---------------------------------------------------------------------------
# 2. The net sensible heat equals the request
# ---------------------------------------------------------------------------

#: (request [W], T0, T_a_room, extra arguments, what the row has to be).
MET_CASES = {
    "cooling, dry coil": (4000.0, 35.0, 27.0, {}, "continuous"),
    "cooling, humid room, coil above the dew point (RH 0.3)": (4000.0, 35.0, 27.0, {"RH_room": 0.3}, "continuous"),
    "cooling, wet coil (RH 0.5)": (4000.0, 35.0, 27.0, {"RH_room": 0.5}, "wet"),
    "cooling, wet coil (RH 0.8)": (2500.0, 35.0, 27.0, {"RH_room": 0.8}, "wet"),
    "heating, no frost": (-4000.0, 7.0, 20.0, {}, "continuous"),
    "heating, frost": (-3000.0, 0.0, 20.0, {"RH_out": 0.85}, "frost"),
    "cooling, low load (cycling)": (300.0, 30.0, 26.0, {}, "cycling"),
    "cooling, wet coil, low load (cycling)": (400.0, 30.0, 27.0, {"RH_room": 0.8}, "wet cycling"),
    "heating, low load (cycling)": (-300.0, 7.0, 20.0, {}, "cycling"),
    "heating, frost, low load (cycling)": (-300.0, 0.0, 20.0, {"RH_out": 0.85}, "frost cycling"),
}


@pytest.mark.parametrize("case", list(MET_CASES), ids=list(MET_CASES))
def test_the_net_sensible_heat_equals_the_request(hp: AirSourceHeatPump, case: str) -> None:
    request, T0, T_a_room, extra, kind = MET_CASES[case]
    row = _solve(hp, request, T0, T_a_room, request_basis="room_sensible", **extra)

    assert row["request_basis"] == "room_sensible"
    assert row["hp_is_on"] is True
    assert row["failure_reason"] == "none"
    assert row["Q_request [W]"] == request
    assert row["Q_unmet [W]"] == 0.0
    assert row["Q_room_sensible [W]"] == pytest.approx(abs(request), rel=_tolerance(row))

    # The other keys keep their meaning: the coil's heat is the coil's, and
    # the net sensible heat is the coil's sensible heat with the fan's.
    fan = row["E_iu_fan [W]"]
    if request > 0:
        assert row["Q_ref_iu [W]"] > request
        assert row["Q_room_sensible [W]"] == pytest.approx(row["Q_iu_sensible [W]"] - fan, rel=1e-12)
    else:
        assert row["Q_ref_iu [W]"] < abs(request)
        assert row["Q_room_sensible [W]"] == pytest.approx(row["Q_iu_sensible [W]"] + fan, rel=1e-12)

    # The case is the one its name says.
    assert (row["Q_iu_latent [W]"] > 0.0) == ("wet" in kind)
    assert (row["capacity_clamped"] == "min" and row["runtime_fraction [-]"] < 1.0) == ("cycling" in kind)
    assert (row["defrost_fraction [-]"] > 0.0) == ("frost" in kind)


def test_the_example_of_the_issue(hp: AirSourceHeatPump) -> None:
    """Default 4 kW unit, 35 degC outdoor, room 27 degC at 50 %: 4 kW of sensible cooling."""
    coil = _solve(hp, 4000.0, 35.0, 27.0, RH_room=0.5)
    # Asked as the coil's heat, the room is 782 W short of 4 kW of sensible cooling.
    assert coil["Q_ref_iu [W]"] == pytest.approx(4000.0, rel=1e-9)
    assert coil["Q_iu_sensible [W]"] == pytest.approx(3241.0, abs=1.0)
    assert coil["Q_room_sensible [W]"] == pytest.approx(3218.0, abs=1.0)

    room = _solve(hp, 4000.0, 35.0, 27.0, RH_room=0.5, request_basis="room_sensible")
    assert room["Q_room_sensible [W]"] == pytest.approx(4000.0, rel=WET_TOL)
    assert room["Q_request [W]"] == 4000.0
    assert room["Q_unmet [W]"] == 0.0
    assert room["capacity_clamped"] is None
    # What it takes: the coil duty, its sensible share, and the electricity
    # (the wet operating point is the optimiser's, hence the 0.1 %).
    assert room["Q_ref_iu [W]"] == pytest.approx(5937.5, rel=1e-3)
    assert room["SHR_iu [-]"] == pytest.approx(0.6776, rel=1e-3)
    assert room["Q_iu_latent [W]"] == pytest.approx(1914.3, rel=2e-3)
    assert room["E_tot [W]"] == pytest.approx(1647.1, rel=1e-3)
    assert room["E_tot [W]"] > 1.3 * coil["E_tot [W]"]

    # The dry coil: the fan's heat only.
    dry = _solve(hp, 4000.0, 35.0, 27.0, request_basis="room_sensible")
    assert dry["Q_room_sensible [W]"] == pytest.approx(4000.0, rel=DRY_TOL)
    assert dry["Q_ref_iu [W]"] == pytest.approx(4000.0 + dry["E_iu_fan [W]"], rel=1e-6)
    assert dry["Q_ref_iu [W]"] == pytest.approx(4023.13, abs=0.01)
    # Heating: the fan's heat is part of what the room receives.
    heat = _solve(hp, -4000.0, 7.0, 20.0, request_basis="room_sensible")
    assert heat["Q_ref_iu [W]"] == pytest.approx(4000.0 - heat["E_iu_fan [W]"], rel=1e-6)
    assert heat["Q_ref_iu [W]"] == pytest.approx(3976.87, abs=0.01)


# ---------------------------------------------------------------------------
# 3. The row is the coil-basis row of the duty found
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(MET_CASES), ids=list(MET_CASES))
def test_few_solves_find_the_duty(hp: AirSourceHeatPump, case: str) -> None:
    """The cost, as the row reports it: the coil-basis solves the search took."""
    request, T0, T_a_room, extra, kind = MET_CASES[case]
    row = _solve(hp, request, T0, T_a_room, request_basis="room_sensible", **extra)
    # Three solves where the duty is smooth along the request (the request, a
    # first guess from it, one secant step), a few more with a wet coil, never
    # more than the budget.
    assert 2 <= row[SOLVES_KEY] <= (MAX_SOLVES if "wet" in kind else 4)


@pytest.mark.parametrize("case", list(MET_CASES), ids=list(MET_CASES))
def test_a_coil_request_for_the_duty_found_gives_the_same_row(hp: AirSourceHeatPump, case: str) -> None:
    """The coil duty the row reports is the duty that was solved, to rounding: asking for it repeats the row.

    This is what says the row is a coil-basis row, whatever way the search
    reaches the solve. Measured on 2,646 requests of default units: every
    numeric key to 1e-12 (worst 8e-13), wet rows included.
    """
    request, T0, T_a_room, extra, _ = MET_CASES[case]
    room = _solve(hp, request, T0, T_a_room, request_basis="room_sensible", **extra)
    coil = _solve(hp, math.copysign(room["Q_ref_iu [W]"], request), T0, T_a_room, **extra)
    assert coil["request_basis"] == "coil"
    assert [*coil, SOLVES_KEY] == list(room)
    for key, value in room.items():
        if key in BASIS_KEYS or key == SOLVES_KEY:
            continue
        if isinstance(value, float) and math.isfinite(value):
            assert coil[key] == pytest.approx(value, rel=1e-9, abs=1e-9), key
        else:
            assert _same_value(coil[key], value), key


# ---------------------------------------------------------------------------
# 4. The ceiling, and the lower end
# ---------------------------------------------------------------------------

#: (request [W], T0, T_a_room, extra arguments of ``analyze_steady``, the same for ``max_capacity``).
CEILING_CASES = {
    "cooling, dry coil": (9000.0, 35.0, 27.0, {}, {}),
    "cooling, wet coil (RH 0.5)": (9000.0, 35.0, 27.0, {"RH_room": 0.5}, {"RH_room": 0.5}),
    # More sensible cooling than the wet coil has, less than its total heat.
    "cooling, wet coil, under the coil's total heat": (5500.0, 35.0, 27.0, {"RH_room": 0.5}, {"RH_room": 0.5}),
    "heating, frost": (-12000.0, -10.0, 20.0, {}, {}),
    "heating, no frost": (-12000.0, 7.0, 20.0, {}, {}),
}


@pytest.mark.parametrize("case", list(CEILING_CASES), ids=list(CEILING_CASES))
def test_above_the_ceiling_the_row_is_the_ceilings_and_the_rest_is_unmet(hp: AirSourceHeatPump, case: str) -> None:
    request, T0, T_a_room, extra, ceiling_extra = CEILING_CASES[case]
    mode = "cooling" if request > 0 else "heating"
    row = _solve(hp, request, T0, T_a_room, request_basis="room_sensible", **extra)
    # The coil-basis row of a request no unit of this size delivers: the ceiling's.
    flat_out = _solve(hp, math.copysign(50.0 * hp.hp_capacity, request), T0, T_a_room, **extra)
    ceiling = hp.max_capacity(T0, T_a_room, mode=mode, **ceiling_extra)

    assert flat_out["capacity_clamped"] == "max"
    assert _differing_keys(flat_out, row) == []
    assert row["capacity_clamped"] == "max"
    assert row["request_basis"] == "room_sensible"
    assert row["Q_request [W]"] == request
    # Unmet in the basis: what the room is short of, not what the coil is.
    assert 0.0 < row["Q_room_sensible [W]"] < abs(request)
    assert row["Q_unmet [W]"] == pytest.approx(abs(request) - row["Q_room_sensible [W]"], rel=1e-12)
    # The coil keys stay the coil's.
    assert row["Q_max [W]"] == pytest.approx(row["Q_ref_iu [W]"], rel=1e-12)
    assert row["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)

    # ``max_capacity`` reports the same ceiling in the basis, and keeps its keys.
    assert set(ceiling) == CEILING_KEYS | {"Q_room_sensible_at_Q_max [W]"}
    assert ceiling["Q_room_sensible_at_Q_max [W]"] == pytest.approx(row["Q_room_sensible [W]"], rel=1e-9)


@pytest.mark.parametrize("case", ["cooling, dry coil", "cooling, wet coil (RH 0.5)", "heating, frost"])
def test_requests_around_the_ceiling_in_the_basis(hp: AirSourceHeatPump, case: str) -> None:
    request, T0, T_a_room, extra, ceiling_extra = CEILING_CASES[case]
    sign = math.copysign(1.0, request)
    top = hp.max_capacity(T0, T_a_room, mode="cooling" if sign > 0 else "heating", **ceiling_extra)[
        "Q_room_sensible_at_Q_max [W]"
    ]
    assert top > 0.0

    under = _solve(hp, sign * 0.98 * top, T0, T_a_room, request_basis="room_sensible", **extra)
    assert under["Q_unmet [W]"] == 0.0
    assert under["Q_room_sensible [W]"] == pytest.approx(0.98 * top, rel=_tolerance(under))

    above = _solve(hp, sign * 1.02 * top, T0, T_a_room, request_basis="room_sensible", **extra)
    assert above["capacity_clamped"] == "max"
    assert above["Q_room_sensible [W]"] == pytest.approx(top, rel=1e-9)
    assert above["Q_unmet [W]"] == pytest.approx(0.02 * top, rel=1e-6)


def test_max_capacity_keeps_its_keys_where_the_unit_cannot_run() -> None:
    # Both fans held at one flow on the pressure-ratio floor: no wet duty runs
    # (docs/source/models/ashp.rst, "A fan held at one flow").
    base = AirSourceHeatPump(hp_capacity=2500.0)
    held = AirSourceHeatPump(
        hp_capacity=2500.0,
        dV_iu_fan_a_min=base.dV_iu_fan_a_max,
        dV_iu_fan_a_max=base.dV_iu_fan_a_max,
        dV_ou_fan_a_min=base.dV_ou_fan_a_max,
        dV_ou_fan_a_max=base.dV_ou_fan_a_max,
    )
    ceiling = held.max_capacity(25.0, 15.0, mode="cooling", RH_room=1.0)
    assert set(ceiling) == CEILING_KEYS | {"Q_room_sensible_at_Q_max [W]"}
    if ceiling["binding"] == "infeasible":
        assert ceiling["Q_max [W]"] == 0.0
        assert ceiling["Q_room_sensible_at_Q_max [W]"] == 0.0
    else:
        assert 0.0 < ceiling["Q_room_sensible_at_Q_max [W]"] <= ceiling["Q_max [W]"]


def test_a_unit_that_does_not_cycle_delivers_more_than_a_small_request() -> None:
    """Without the cycling model the unit runs at its smallest duty, in either basis: nothing is unmet."""
    steady = AirSourceHeatPump(Cd_cycling=None)
    coil = _solve(steady, 300.0, 30.0, 26.0)
    assert coil["capacity_clamped"] == "min"
    assert coil["Q_ref_iu [W]"] > 300.0
    room = _solve(steady, 300.0, 30.0, 26.0, request_basis="room_sensible")
    assert room["capacity_clamped"] == "min"
    assert room["Q_room_sensible [W]"] > 300.0
    assert room["Q_request [W]"] == 300.0
    assert room["Q_unmet [W]"] == 0.0
    assert room["Q_room_sensible [W]"] == pytest.approx(coil["Q_room_sensible [W]"], rel=1e-3)


def test_a_request_the_unit_is_off_for_is_the_off_row_with_all_of_it_unmet() -> None:
    """A coil duty the model answers with the unit off is reported as that: nothing delivered."""
    # Outdoor air above the largest condensing temperature the compressor allows: no cooling duty runs.
    hot = AirSourceHeatPump(T_cond_max=50.0)
    coil = _solve(hot, 4000.0, 55.0, 27.0)
    assert coil["hp_is_on"] is False
    assert coil["failure_reason"] != "none"
    room = _solve(hot, 4000.0, 55.0, 27.0, request_basis="room_sensible")
    assert _differing_keys(coil, room) == []
    assert room["request_basis"] == "room_sensible"
    assert room["Q_room_sensible [W]"] == 0.0
    assert room["Q_request [W]"] == 4000.0
    assert room["Q_unmet [W]"] == 4000.0


def test_a_request_is_met_where_the_net_sensible_heat_is_not_monotone_in_the_coil_duty() -> None:
    """A wet coil cycling on the pressure-ratio floor: more coil heat can mean less sensible heat."""
    small = AirSourceHeatPump(hp_capacity=2500.0)
    conditions = dict(T0=20.0, T_a_room=30.0, RH_room=0.5)
    # The running state of the cycling unit changes with the request, and the
    # latent share with it: between these two coil duties the room loses less.
    lower = _solve(small, 135.5, **conditions)
    higher = _solve(small, 158.2, **conditions)
    assert lower["capacity_clamped"] == higher["capacity_clamped"] == "min"
    assert higher["Q_room_sensible [W]"] < lower["Q_room_sensible [W]"] < 125.0

    row = _solve(small, 125.0, request_basis="room_sensible", **conditions)
    assert row["Q_unmet [W]"] == 0.0
    assert row["Q_room_sensible [W]"] == pytest.approx(125.0, rel=WET_TOL)
    assert row["Q_ref_iu [W]"] > 158.2
    assert row[SOLVES_KEY] <= MAX_SOLVES


def _fans_held(capacity: float, *, indoor: bool, outdoor: bool) -> AirSourceHeatPump:
    base = AirSourceHeatPump(hp_capacity=capacity)
    limits: dict[str, float] = {}
    if indoor:
        limits.update(dV_iu_fan_a_min=base.dV_iu_fan_a_max, dV_iu_fan_a_max=base.dV_iu_fan_a_max)
    if outdoor:
        limits.update(dV_ou_fan_a_min=base.dV_ou_fan_a_max, dV_ou_fan_a_max=base.dV_ou_fan_a_max)
    return AirSourceHeatPump(hp_capacity=capacity, **limits)


def test_a_fan_held_at_one_flow_is_matched_to_the_looser_tolerance() -> None:
    """A dry row of a unit with held fans scatters by about 1e-5 along the duty: met to 1e-4, nothing unmet."""
    held = _fans_held(4000.0, indoor=True, outdoor=True)
    row = _solve(held, 2000.0, 46.0, 27.0, RH_room=0.6, request_basis="room_sensible")
    assert row["Q_iu_latent [W]"] == 0.0
    assert row["Q_unmet [W]"] == 0.0
    assert row["Q_room_sensible [W]"] == pytest.approx(2000.0, rel=WET_TOL)
    assert row[SOLVES_KEY] <= 5


def test_a_request_no_duty_has_is_answered_from_below_with_the_rest_unmet() -> None:
    """Outdoor fan held: neighbouring coil duties run at different indoor airflows, with a gap in sensible heat."""
    held = _fans_held(4000.0, indoor=False, outdoor=True)
    conditions = dict(T0=35.0, T_a_room=35.0, RH_room=0.6)
    row = _solve(held, 2000.0, request_basis="room_sensible", **conditions)

    assert row[SOLVES_KEY] <= MAX_SOLVES
    assert row["hp_is_on"] is True
    sensible = row["Q_room_sensible [W]"]
    if row["Q_unmet [W]"] == 0.0:
        # The inner search no longer jumps there (issue #234): the request is met.
        assert sensible == pytest.approx(2000.0, rel=WET_TOL)
    else:
        # Never more than was asked while something is reported unmet.
        assert sensible < 2000.0
        assert row["Q_unmet [W]"] == pytest.approx(2000.0 - sensible, rel=1e-12)
        # The budget went into it, and the row is the coil-basis row of the duty it reports.
        assert row[SOLVES_KEY] == MAX_SOLVES
        again = _solve(held, row["Q_ref_iu [W]"], **conditions)
        assert again["Q_room_sensible [W]"] == pytest.approx(sensible, rel=WET_TOL)
        # No duty there has the request: one per cent more coil heat, and the
        # room loses far more than was asked (measured: 1,670 W, then 2,490 W).
        above = _solve(held, 1.01 * row["Q_ref_iu [W]"], **conditions)
        assert above["Q_room_sensible [W]"] > 2000.0 + 100.0 * (2000.0 - sensible) * WET_TOL
        assert above["Q_room_sensible [W]"] - sensible > 0.1 * 2000.0


def test_the_ceilings_net_sensible_heat_is_that_of_the_largest_coil_duty_not_a_maximum() -> None:
    """``Q_room_sensible_at_Q_max [W]`` names a row, the ceiling's; a held fan can take more sensible heat under it."""
    held = _fans_held(4000.0, indoor=False, outdoor=True)
    conditions = dict(T0=15.0, T_a_room=35.0, RH_room=1.0)
    ceiling = held.max_capacity(15.0, 35.0, mode="cooling", RH_room=1.0)
    at_ceiling = ceiling["Q_room_sensible_at_Q_max [W]"]
    # The key is the net sensible heat of the row a request above the ceiling gets.
    flat_out = _solve(held, 50.0 * held.hp_capacity, **conditions)
    assert flat_out["capacity_clamped"] == "max"
    assert flat_out["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)
    assert at_ceiling == pytest.approx(flat_out["Q_room_sensible [W]"], rel=1e-5)

    # A request a little above it: met at a smaller coil duty where one has it
    # (measured: 2,739 W at 12.8 kW, under the 13.7 kW ceiling's 2,705 W),
    # and the ceiling's row with the rest unmet otherwise.
    request = 1.0125 * at_ceiling
    row = _solve(held, request, request_basis="room_sensible", **conditions)
    if row["Q_unmet [W]"] == 0.0:
        assert row["Q_room_sensible [W]"] == pytest.approx(request, rel=WET_TOL)
        assert row["Q_room_sensible [W]"] > at_ceiling
        assert row["Q_ref_iu [W]"] < ceiling["Q_max [W]"]
    else:
        assert row["capacity_clamped"] == "max"
        assert row["Q_room_sensible [W]"] == pytest.approx(at_ceiling, rel=1e-5)
        assert row["Q_unmet [W]"] == pytest.approx(request - row["Q_room_sensible [W]"], rel=1e-12)


# ---------------------------------------------------------------------------
# 5. The warning is in the basis
# ---------------------------------------------------------------------------


def test_the_warning_of_a_short_row_is_in_the_requested_basis() -> None:
    model = AirSourceHeatPump()
    # 5.5 kW of sensible cooling: the wet coil's total heat at its ceiling is
    # 8.0 kW, its net sensible heat 4.95 kW.
    with pytest.warns(OperatingLimitWarning) as caught:
        row = model.analyze_steady(5500.0, 35.0, 27.0, RH_room=0.5, request_basis="room_sensible")
    assert isinstance(row, dict)
    assert len(caught) == 1
    text = str(caught[0].message)
    delivered = row["Q_room_sensible [W]"]
    assert f"delivers {delivered:.0f} W of the 5500 W of sensible cooling requested" in text
    assert f"({100.0 * delivered / 5500.0:.0f} %" in text
    assert "request_basis='room_sensible'" in text

    # The coil basis: worded as before.
    model = AirSourceHeatPump()
    with pytest.warns(OperatingLimitWarning) as caught:
        row = model.analyze_steady(9000.0, 35.0, 27.0, RH_room=0.5)
    assert isinstance(row, dict)
    text = str(caught[0].message)
    assert f"delivers {row['Q_ref_iu [W]']:.0f} W of the 9000 W of cooling requested" in text
    assert "request_basis" not in text

    # A request that is met warns of nothing.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        AirSourceHeatPump().analyze_steady(4000.0, 35.0, 27.0, RH_room=0.5, request_basis="room_sensible")


# ---------------------------------------------------------------------------
# 6. A dynamic run
# ---------------------------------------------------------------------------

#: A day of zone loads: (sensible load [W], T0, T_a_room, RH_room, RH_out).
STEPS = [
    (4000.0, 35.0, 27.0, 0.5, 0.5),
    (1500.0, 30.0, 26.0, 0.6, 0.5),
    (300.0, 26.0, 26.0, 0.7, 0.6),
    (7000.0, 38.0, 27.0, 0.5, 0.4),
    (0.0, 20.0, 22.0, 0.5, 0.7),
    (-3000.0, 0.0, 20.0, float("nan"), 0.85),
    (-300.0, 7.0, 20.0, None, 0.8),
    (-9000.0, -10.0, 20.0, 0.4, 0.8),
    (2500.0, 33.0, 24.0, 0.3, 0.5),
]


def _run(model: AirSourceHeatPump, steps: list[tuple], **kwargs) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OperatingLimitWarning)
        return model.analyze_dynamic(
            3600 * len(steps),
            3600,
            [s[0] for s in steps],
            [s[1] for s in steps],
            [s[2] for s in steps],
            RH_out_schedule=[s[4] for s in steps],
            RH_room_schedule=[s[3] for s in steps],
            **kwargs,
        )


@pytest.fixture(scope="module")
def run(hp: AirSourceHeatPump) -> pd.DataFrame:
    return _run(hp, STEPS, request_basis="room_sensible")


def test_a_dynamic_run_meets_the_zone_loads_with_a_humidity_schedule(run: pd.DataFrame) -> None:
    assert list(run["request_basis"]) == ["room_sensible"] * len(STEPS)
    assert list(run["Q_request [W]"]) == [s[0] for s in STEPS]
    short = {3, 7}
    for n, (load, *_rest) in enumerate(STEPS):
        row = run.iloc[n]
        if load == 0.0:
            assert not row["hp_is_on"]
            assert row["Q_unmet [W]"] == 0.0
        elif n in short:
            assert row["capacity_clamped"] == "max"
            assert row["Q_unmet [W]"] == pytest.approx(abs(load) - row["Q_room_sensible [W]"], rel=1e-12)
            assert row["Q_unmet [W]"] > 0.0
        else:
            assert row["Q_unmet [W]"] == 0.0
            assert row["Q_room_sensible [W]"] == pytest.approx(abs(load), rel=_tolerance(row.to_dict()))
    # The humidity schedule is read on the cooling steps: the first is a wet coil.
    assert run["Q_iu_latent [W]"].iloc[0] > 0.0
    assert run["RH_room [-]"].iloc[0] == 0.5
    assert math.isnan(run["RH_room [-]"].iloc[5])
    # Cycling and frost are met in the mean.
    assert run["capacity_clamped"].iloc[2] == "min"
    assert run["defrost_fraction [-]"].iloc[5] > 0.0
    # The run's unmet energy is the rows', in the basis.
    summary = run.attrs["operating_limits"]
    assert summary["Q_unmet [kWh]"] == pytest.approx(run["Q_unmet [W]"].sum() / 1000.0, rel=1e-12)
    assert summary["Q_request [kWh]"] == pytest.approx(sum(abs(s[0]) for s in STEPS) / 1000.0, rel=1e-12)
    assert summary["limited_steps [-]"] == len(short)


def test_the_default_dynamic_run_is_on_the_coil_basis(hp: AirSourceHeatPump) -> None:
    frame = _run(hp, STEPS[:2])
    assert list(frame["request_basis"]) == ["coil", "coil"]
    assert frame["Q_ref_iu [W]"].iloc[0] == pytest.approx(4000.0, rel=1e-9)
    # The count of solves is a column of a room-sensible run only.
    assert SOLVES_KEY not in frame.columns


def test_a_dynamic_run_reports_the_solves_of_every_step(run: pd.DataFrame) -> None:
    solves = run[SOLVES_KEY]
    assert pd.api.types.is_integer_dtype(solves)
    for n, (load, *_rest) in enumerate(STEPS):
        if load == 0.0:
            assert solves.iloc[n] == 0
        else:
            assert 1 <= solves.iloc[n] <= MAX_SOLVES
    # The two steps above the ceiling end on the first or the second solve.
    assert solves.iloc[3] <= 2
    assert solves.iloc[7] <= 2


class _EarlierSignature(AirSourceHeatPump):
    """A subclass written before the option: ``analyze_steady`` overridden with the signature it had then."""

    def analyze_steady(  # type: ignore[override]
        self,
        Q_r_iu: float,
        T0: float,
        T_a_room: float | None = None,
        *,
        return_dict: bool = True,
        postprocess: bool = True,
        verbose: bool = True,
        RH_out: float | None = None,
        RH_room: float | None = None,
    ) -> dict | pd.DataFrame:
        self.overridden_method_ran = True
        return super().analyze_steady(
            Q_r_iu,
            T0,
            T_a_room,
            return_dict=return_dict,
            postprocess=postprocess,
            verbose=verbose,
            RH_out=RH_out,
            RH_room=RH_room,
        )


@pytest.mark.parametrize("kwargs", [{}, {"request_basis": "coil"}], ids=["default", "coil"])
def test_a_coil_basis_run_does_not_hand_the_basis_to_analyze_steady(hp: AirSourceHeatPump, kwargs: dict) -> None:
    """A subclass that overrides ``analyze_steady`` without the option runs a coil-basis schedule as before."""
    earlier = _EarlierSignature()
    frame = _run(earlier, STEPS, **kwargs)
    assert earlier.overridden_method_ran
    plain = _run(hp, STEPS)
    assert list(frame.columns) == list(plain.columns)
    assert list(frame["request_basis"]) == ["coil"] * len(STEPS)
    for n in range(len(STEPS)):
        assert _row_differences(frame.iloc[n], plain.iloc[n]) == [], n
    assert frame.attrs["operating_limits"] == plain.attrs["operating_limits"]


def test_a_room_sensible_run_hands_the_basis_to_analyze_steady() -> None:
    """Such a subclass has to take the option to be asked on the room's sensible heat: Python's ``TypeError``."""
    with pytest.raises(TypeError, match="request_basis"):
        _run(_EarlierSignature(), STEPS[:1], request_basis="room_sensible")


def _row_differences(a: pd.Series, b: pd.Series) -> list[str]:
    skip = {"time [s]", "time [h]"}
    return [
        key
        for key in a.index
        if key not in skip and not (_same_value(a[key], b[key]) or (pd.isna(a[key]) and pd.isna(b[key])))
    ]


def test_a_dynamic_row_does_not_depend_on_the_steps_before(hp: AirSourceHeatPump, run: pd.DataFrame) -> None:
    """The same rows in reverse order, each step alone, and from a fresh model."""
    backwards = _run(hp, STEPS[::-1], request_basis="room_sensible")
    assert list(backwards.columns) == list(run.columns)
    for n in range(len(STEPS)):
        assert _row_differences(run.iloc[n], backwards.iloc[len(STEPS) - 1 - n]) == [], n
        alone = _run(AirSourceHeatPump(), [STEPS[n]], request_basis="room_sensible")
        assert _row_differences(run.iloc[n], alone.iloc[0]) == [], n


def test_a_dynamic_row_is_the_steady_row_of_its_step(hp: AirSourceHeatPump, run: pd.DataFrame) -> None:
    for n, (load, T0, T_a_room, RH_room, RH_out) in enumerate(STEPS):
        humid = load > 0 and RH_room is not None
        steady = _solve(
            hp,
            load,
            T0,
            T_a_room,
            RH_out=RH_out,
            RH_room=RH_room if humid else None,
            request_basis="room_sensible",
            postprocess=False,
        )
        row = run.iloc[n]
        for key, value in steady.items():
            assert _same_value(row[key], value) or (pd.isna(row[key]) and pd.isna(value)), (n, key)


@pytest.mark.earlier_defaults  # the dry discharge floor exists on the earlier default machine: tests/conftest.py
def test_a_request_under_the_dry_discharge_floor_is_met_by_cycling_alone_and_in_a_run() -> None:
    """Where the envelope refuses small duties (R407C, -25 degC) the request is met by cycling at the floor.

    The dry model searches that floor once per condition in a run and from
    each request otherwise, to 0.5 % (``_FLOOR_REL_TOL``): on either basis a
    run's row can differ from the steady row by that search's tolerance.
    """
    arctic = AirSourceHeatPump(ref="R407C", defrost=None)
    coil = _solve(arctic, -200.0, -25.0, 21.0)
    assert coil["capacity_clamped"] == "min"
    assert coil["cmp_rpm [rpm]"] / 60.0 > arctic.rps_min + 1.0  # not the speed floor: the envelope's

    loads = [-200.0, -300.0, -400.0]
    steady = [_solve(arctic, load, -25.0, 21.0, request_basis="room_sensible") for load in loads]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OperatingLimitWarning)
        frame = arctic.analyze_dynamic(3 * 3600, 3600, loads, [-25.0] * 3, [21.0] * 3, request_basis="room_sensible")
    for n, load in enumerate(loads):
        for row in (steady[n], frame.iloc[n]):
            assert row["capacity_clamped"] == "min"
            assert row["Q_unmet [W]"] == 0.0
            assert row["Q_room_sensible [W]"] == pytest.approx(abs(load), rel=WET_TOL)
            assert row["Q_ref_iu [W]"] < abs(load)
        assert frame["E_tot [W]"].iloc[n] == pytest.approx(steady[n]["E_tot [W]"], rel=5e-3)
        assert frame["Q_min [W]"].iloc[n] == pytest.approx(steady[n]["Q_min [W]"], rel=5e-3)


def test_a_steady_row_does_not_depend_on_what_was_solved_before() -> None:
    conditions = [
        (4000.0, 35.0, 27.0, {"RH_room": 0.5}),
        (300.0, 30.0, 27.0, {"RH_room": 0.8}),
        (9000.0, 35.0, 27.0, {"RH_room": 0.5}),
        (-3000.0, 0.0, 20.0, {"RH_out": 0.85}),
        (4000.0, 35.0, 27.0, {}),
    ]
    model = AirSourceHeatPump()
    first = [_solve(model, q, T0, Tr, request_basis="room_sensible", **kw) for q, T0, Tr, kw in conditions]
    again = [_solve(model, q, T0, Tr, request_basis="room_sensible", **kw) for q, T0, Tr, kw in conditions[::-1]][::-1]
    alone = [
        _solve(AirSourceHeatPump(), q, T0, Tr, request_basis="room_sensible", **kw) for q, T0, Tr, kw in conditions
    ]
    for a, b, c in zip(first, again, alone, strict=True):
        assert _differing_keys(a, b, skip=frozenset()) == []
        assert _differing_keys(a, c, skip=frozenset()) == []


def test_the_coil_basis_is_not_touched_by_a_room_sensible_solve_before_it() -> None:
    model = AirSourceHeatPump()
    before = _solve(model, 4000.0, 35.0, 27.0, RH_room=0.5)
    _solve(model, 4000.0, 35.0, 27.0, RH_room=0.5, request_basis="room_sensible")
    after = _solve(model, 4000.0, 35.0, 27.0, RH_room=0.5)
    assert _differing_keys(before, after, skip=frozenset()) == []
    assert np.isfinite(after["Q_room_sensible [W]"])
