"""Capacity ceiling of an air-source heat pump.

Nameplate capacity is a label attached to one rating point, not a limit. The unit
delivers more than nameplate when the outdoor air is mild and less when it is
severe, so a load schedule clipped at nameplate is wrong in both directions. These
tests pin the three properties a caller needs in order to clip against the machine
instead: that the ceiling is found, that it moves with the weather in the right
direction, and that a request beyond it is reported as capacity-limited operation
rather than as a failure or as an arbitrary cheaper part-load point.
"""

from __future__ import annotations

import warnings

import pytest

from tmhp import AirSourceHeatPump

# The unit of the ASHP exergy analysis: LG Multi V, R410A, 18 kW nameplate heating.
UNIT = dict(
    ref="R410A",
    hp_capacity=18000.0,
    V_cmp_ref=49.4e-6,
    UA_ou_rated=2500.0,
    UA_iu_rated=2500.0,
    dV_ou_fan_a_rated=150.0 / 60.0,
    dV_iu_fan_a_rated=150.0 / 60.0,
)

T_ROOM_HEAT = 21.0
T_ROOM_COOL = 24.0


@pytest.fixture(scope="module")
def hp() -> AirSourceHeatPump:
    return AirSourceHeatPump(**UNIT)


def steady(hp: AirSourceHeatPump, Q_r_iu: float, T0: float, T_a_room: float) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return hp.analyze_steady(
            Q_r_iu=Q_r_iu,
            T0=T0,
            T_a_room=T_a_room,
            return_dict=True,
            postprocess=False,
            verbose=False,
        )


@pytest.fixture(scope="module")
def envelope_heating_mild(hp: AirSourceHeatPump) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return hp.max_capacity(T0=2.0, T_a_room=T_ROOM_HEAT, mode="heating")


def test_default_approach_bounds_are_unchanged(hp: AirSourceHeatPump):
    """The bound is a documented model setting; widening it is a deliberate act."""
    assert hp.dT_approach_bounds == (1.0, 20.0)


def test_ceiling_is_found_and_exceeds_nameplate_at_mild_conditions(envelope_heating_mild):
    """A unit sized for the design day has headroom when the weather is easy."""
    q_max = envelope_heating_mild["Q_max [W]"]
    assert q_max > UNIT["hp_capacity"], "an 18 kW unit at +2 degC delivers more than 18 kW"
    assert envelope_heating_mild["binding"] in ("compressor", "cycle")


def test_request_just_below_the_ceiling_is_met(hp, envelope_heating_mild):
    q = 0.98 * envelope_heating_mild["Q_max [W]"]
    res = steady(hp, -q, 2.0, T_ROOM_HEAT)
    assert res["failure_reason"] == "none"
    assert res["capacity_clamped"] is None
    assert res["Q_ref_iu [W]"] == pytest.approx(q, rel=0.01)


def test_request_beyond_the_ceiling_is_capacity_limited_not_a_failure(hp, envelope_heating_mild):
    """The machine runs flat out and delivers less; that is operation, not failure."""
    q_max = envelope_heating_mild["Q_max [W]"]
    res = steady(hp, -1.5 * q_max, 2.0, T_ROOM_HEAT)
    assert res["failure_reason"] == "none"
    assert res["capacity_clamped"] == "max"
    assert res["Q_ref_iu [W]"] < 1.5 * q_max


def test_capacity_limited_point_delivers_the_ceiling_not_a_cheaper_part_load(hp, envelope_heating_mild):
    """Ranking capacity-limited points by power would give away real capacity.

    Regression guard for exactly that: with a power-ranked objective this unit
    reported about 26 kW where it can deliver just over 30 kW.
    """
    q_max = envelope_heating_mild["Q_max [W]"]
    res = steady(hp, -2.0 * q_max, 2.0, T_ROOM_HEAT)
    assert res["Q_ref_iu [W]"] == pytest.approx(q_max, rel=0.02)


def test_overshoot_size_does_not_change_what_is_delivered(hp, envelope_heating_mild):
    """The ceiling belongs to the machine and the weather, not to the request."""
    q_max = envelope_heating_mild["Q_max [W]"]
    a = steady(hp, -1.5 * q_max, 2.0, T_ROOM_HEAT)["Q_ref_iu [W]"]
    b = steady(hp, -3.0 * q_max, 2.0, T_ROOM_HEAT)["Q_ref_iu [W]"]
    assert a == pytest.approx(b, rel=0.02)


def test_heating_ceiling_rises_with_outdoor_temperature(hp):
    """Less lift to overcome, so more capacity -- the opposite of a fixed cap."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cold = hp.max_capacity(T0=-15.0, T_a_room=T_ROOM_HEAT, mode="heating")
        mild = hp.max_capacity(T0=2.0, T_a_room=T_ROOM_HEAT, mode="heating")
    assert cold["Q_max [W]"] < mild["Q_max [W]"]
    # The pair straddles nameplate, which is the whole reason a fixed cap is
    # wrong in both directions: short of it in the cold, over it when mild.
    assert cold["Q_max [W]"] < UNIT["hp_capacity"] < mild["Q_max [W]"]


def test_cooling_ceiling_falls_with_outdoor_temperature(hp):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mild = hp.max_capacity(T0=25.0, T_a_room=T_ROOM_COOL, mode="cooling")
        hot = hp.max_capacity(T0=40.0, T_a_room=T_ROOM_COOL, mode="cooling")
    assert hot["Q_max [W]"] < mild["Q_max [W]"]


def test_widening_the_approach_bound_does_not_move_in_range_operation():
    """The bound only ever binds near the ceiling, so ordinary duties are untouched.

    This is what makes the bound safe to widen for an envelope study without
    invalidating a validation carried out at in-range duties.
    """
    narrow = AirSourceHeatPump(**UNIT)
    wide = AirSourceHeatPump(**UNIT, dT_approach_bounds=(1.0, 35.0))
    for q, t0, troom in [(-12000.0, 2.0, T_ROOM_HEAT), (12000.0, 35.0, T_ROOM_COOL)]:
        a = steady(narrow, q, t0, troom)
        b = steady(wide, q, t0, troom)
        assert b["cop_sys [-]"] == pytest.approx(a["cop_sys [-]"], rel=1e-6)


def test_mode_argument_is_validated(hp):
    with pytest.raises(ValueError, match="heating.*cooling"):
        hp.max_capacity(T0=2.0, mode="defrost")
