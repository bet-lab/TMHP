"""A cooling request above what the compressor delivers flat out is named as the speed range.

The least-lift solve of such a request has no fixed point to converge on
quickly -- the delivered heat moves with the approaches -- and in cooling it
ran out of iterations. That was answered as ``"cycle_invalid"`` (a cycle the
model cannot represent), so :meth:`AirSourceHeatPump.max_capacity` named the
wrong limit and stopped a bisection step short of what ``analyze_steady``
delivers. It showed whenever the approach-temperature search box was wide
enough for the compressor, not the box, to be the ceiling.
"""

import pytest

from tmhp import AirSourceHeatPump


@pytest.fixture(scope="module")
def unit() -> AirSourceHeatPump:
    # A box wide enough for the compressor to be the ceiling, whatever the default is.
    return AirSourceHeatPump(ref="R32", defrost=None, dT_approach_bounds=(1.0, 30.0))


@pytest.mark.parametrize(("outdoor", "room"), [(40.0, 24.0), (35.0, 27.0), (25.0, 27.0)])
def test_cooling_ceiling_is_the_compressor_and_is_what_the_unit_delivers(unit, outdoor, room):
    ceiling = unit.max_capacity(T0=outdoor, T_a_room=room, mode="cooling")
    assert ceiling["binding"] == "compressor"
    row = unit.analyze_steady(Q_r_iu=2.5 * unit.hp_capacity, T0=outdoor, T_a_room=room, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "max"
    assert row["capacity_binding"] == "compressor"
    assert row["cmp_rpm [rpm]"] == pytest.approx(unit.rps_max * 60.0, rel=1e-6)
    assert row["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)


def test_a_request_above_the_ceiling_is_a_flat_out_state_not_a_missing_cycle(unit):
    _, state, reason = unit._largest_airflow_point(1.5 * unit.hp_capacity, 40.0, 24.0)
    assert reason is None and state is not None
    assert state["capacity_clamped"] == "max" and state["converged"]
