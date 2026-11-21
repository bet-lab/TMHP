"""A cooling request above what the compressor delivers flat out is solved, and named as the speed range.

The least-lift solve of such a request iterates on a fixed point that the full
step overshoots from side to side -- the delivered heat moves with the
approaches -- and in cooling it ran out of iterations. That was answered as
``"cycle_invalid"`` (a cycle the model cannot represent), so
:meth:`AirSourceHeatPump.max_capacity` named the wrong limit and stopped a
bisection step short of what ``analyze_steady`` delivers. It showed whenever
the approach-temperature search box was wide enough for the compressor, not the
box, to be the ceiling.

The iteration is continued with half steps, which contract. What it returns is
a state at the fixed point -- both coils at their largest airflow -- or no
state at all: a flat-out state met on the way is not the ceiling (it delivers
up to 1 % less).
"""

import pytest

from tmhp import AirSourceHeatPump

#: Outdoor / room [degC]. The hottest case was 46/32 degC until the default maps of #217; with them the
#: condensing-temperature limit binds there before the compressor, so 44/32 degC is the hot case.
COOLING = [(40.0, 24.0), (35.0, 27.0), (25.0, 27.0), (44.0, 32.0)]


@pytest.fixture(scope="module")
def unit() -> AirSourceHeatPump:
    # A box wide enough for the compressor to be the ceiling, whatever the default is.
    return AirSourceHeatPump(ref="R32", defrost=None, dT_approach_bounds=(1.0, 30.0))


def _at_largest_airflows(model: AirSourceHeatPump, state: dict) -> bool:
    return state["dV_iu_a [m3/s]"] == pytest.approx(model.dV_iu_fan_a_max, rel=1e-5) and state[
        "dV_ou_a [m3/s]"
    ] == pytest.approx(model.dV_ou_fan_a_max, rel=1e-5)


@pytest.mark.parametrize(("outdoor", "room"), COOLING)
def test_cooling_ceiling_is_the_compressor_and_is_what_the_unit_delivers(unit, outdoor, room):
    ceiling = unit.max_capacity(T0=outdoor, T_a_room=room, mode="cooling")
    assert ceiling["binding"] == "compressor"
    row = unit.analyze_steady(Q_r_iu=2.5 * unit.hp_capacity, T0=outdoor, T_a_room=room, verbose=False)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "max"
    assert row["capacity_binding"] == "compressor"
    assert row["cmp_rpm [rpm]"] == pytest.approx(unit.rps_max * 60.0, rel=1e-6)
    assert row["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)


@pytest.mark.parametrize(("outdoor", "room"), COOLING)
def test_the_flat_out_state_is_the_least_lift_one(unit, outdoor, room):
    """Flat out and at the fixed point: the compressor at its top speed, both coils at their largest airflow."""
    _, state, reason = unit._largest_airflow_point(1000.0 * unit.hp_capacity, outdoor, room)
    assert reason is None and state is not None
    assert state["capacity_clamped"] == "max" and state["converged"]
    assert _at_largest_airflows(unit, state)
    # It is the largest heat the unit moves here: no flat-out state at other approaches moves more.
    best = state["Q_ref_iu [W]"]
    de, dc = state["T_iu_a_in [°C]"] - state["T_ref_evap_sat [°C]"], state["T_ref_cond_sat_v [°C]"] - outdoor
    for scale_e, scale_c in ((1.05, 1.0), (1.0, 1.05), (1.05, 1.05), (1.2, 1.2)):
        other = unit._calc_state(
            dT_ref_evap=de * scale_e,
            dT_ref_cond=dc * scale_c,
            Q_r_iu=1000.0 * unit.hp_capacity,
            T0=outdoor,
            T_a_room=room,
        )
        if other is not None and other.get("converged", False):
            assert other["Q_ref_iu [W]"] < best


@pytest.mark.parametrize("budget", [2, 3, 4, 6, 10, 20])
def test_running_out_of_iterations_never_returns_a_state_off_the_fixed_point(monkeypatch, budget):
    """With too few iterations the solve says it found nothing; it does not hand back where it stopped."""
    model = AirSourceHeatPump(ref="R32", defrost=None, dT_approach_bounds=(1.0, 30.0))
    monkeypatch.setattr(AirSourceHeatPump, "_LEAST_LIFT_MAX_ITER", budget)
    x, state, reason = model._largest_airflow_point(1000.0 * model.hp_capacity, 40.0, 24.0)
    if state is None:
        assert reason == "cycle_invalid"
    else:
        assert x is not None and reason is None and _at_largest_airflows(model, state)
