"""The operating-point refinement of ``AirSourceHeatPump`` at a corner of the feasible set.

Where the least-power point has both fans at their largest airflow, the
Nelder-Mead simplex contracts against the infeasible side and can take several
hundred iterations to meet its tolerance. With an iteration cap of 200 the
refinement stopped 5e-5 of the input away from the answer, reported
``success=False``, and the unit was answered as switched off
(``optimizer_failed``). The cap is a guard, not a tolerance.

Two things answer such a point. The cap is 1000, which most corners end
inside. And a refinement the cap still stops is replaced by the refinement
that starts on the least-lift corner itself, where that one ends and is not
materially dearer (within the spread of two refinements that end in the same
place).

Which point needs how many iterations depends on every detail of the cycle.
The corners below were found with the semi-empirical compressor models (526
and 1137 iterations); with the default maps of #217 they end in 40 to 55
iterations, and no search for new corners above 200 iterations was made. The
fallback is therefore examined with a refinement the cap is made to stop: the
first refinement is run to its end and reported as stopped by the cap.
"""

import pytest
from scipy.optimize import minimize

import tmhp.air_source_heat_pump as module
from tmhp import AirSourceHeatPump

#: Daikin RXM42A with the catalogue displacement and outdoor airflow of a 4.2 kW R32 unit
#: (issue #86) and its published indoor airflow, at 41 degC outdoors and a 32 degC room.
UNIT = dict(
    ref="R32",
    hp_capacity=4200.0,
    V_cmp_ref=1.2654764368270026e-05,
    dV_ou_fan_a_rated=0.6329143316922459,
    dV_iu_fan_a_rated=0.2233,
    dT_approach_bounds=(1.0, 30.0),
)
POINT = dict(Q_r_iu=4830.0, T0=41.0, T_a_room=32.0, verbose=False)


def test_the_corner_point_is_solved():
    row = AirSourceHeatPump(**UNIT).analyze_steady(**POINT)
    assert row["failure_reason"] == "none" and row["hp_is_on"] and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(4830.0, rel=1e-9)
    assert row["cop_sys [-]"] == pytest.approx(3.364, abs=0.005)
    # Both fans at their largest airflow: the least-lift corner.
    assert row["dV_ou_a [m3/s]"] == pytest.approx(UNIT["dV_ou_fan_a_rated"], rel=1e-3)
    assert row["dV_iu_a [m3/s]"] == pytest.approx(UNIT["dV_iu_fan_a_rated"], rel=1e-3)


def test_the_refinement_ends_inside_the_cap_there(monkeypatch):
    """The refinement of this point ends inside the cap (526 iterations with the semi-empirical models)."""
    iterations = []

    def counted(fun, x0, **kwargs):
        result = minimize(fun, x0, **kwargs)
        iterations.append(int(result.nit))
        return result

    monkeypatch.setattr(module, "minimize", counted)
    unit = AirSourceHeatPump(**UNIT)
    assert unit.analyze_steady(**POINT)["failure_reason"] == "none"
    assert max(iterations) < AirSourceHeatPump._REFINE_MAXITER


#: A 5 kW R32 unit with a published indoor airflow (Daikin RXM50A) and the catalogue displacement and
#: outdoor airflow of its capacity, at 40 degC outdoors and a 24 degC room.
CORNER_UNIT = dict(
    ref="R32",
    defrost=None,
    hp_capacity=5000.0,
    V_cmp_ref=1.5065195676511936e-05,
    dV_ou_fan_a_rated=0.7196365052574172,
    dV_iu_fan_a_rated=0.2467,
    dT_approach_bounds=(1.0, 30.0),
)
CORNER_POINT = dict(Q_r_iu=4900.0, T0=40.0, T_a_room=24.0, verbose=False)


def _first_refinement_stopped_by_the_cap(monkeypatch, shift: float) -> list:
    """Run the first refinement to its end and report it as stopped by the cap, ``shift`` off its cost."""
    refinements: list = []

    def counted(fun, x0, **kwargs):
        result = minimize(fun, x0, **kwargs)
        if not refinements:
            result.success = False
            result.nit = AirSourceHeatPump._REFINE_MAXITER
            result.fun = float(result.fun) * (1.0 + shift)
        refinements.append((int(result.nit), bool(result.success), float(result.fun)))
        return result

    monkeypatch.setattr(module, "minimize", counted)
    return refinements


def test_a_refinement_the_cap_stops_is_answered_from_the_least_lift_point(monkeypatch):
    """A refinement the cap stops above the answer is replaced by the one from the least-lift point."""
    answer = AirSourceHeatPump(**CORNER_UNIT).analyze_steady(**CORNER_POINT)
    refinements = _first_refinement_stopped_by_the_cap(monkeypatch, 2e-3)
    row = AirSourceHeatPump(**CORNER_UNIT).analyze_steady(**CORNER_POINT)
    assert refinements[0][:2] == (AirSourceHeatPump._REFINE_MAXITER, False)
    assert refinements[-1][1] and refinements[-1][0] < 100
    assert row["failure_reason"] == "none" and row["hp_is_on"] and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(4900.0, rel=1e-9)
    assert row["cop_sys [-]"] == pytest.approx(answer["cop_sys [-]"], rel=1e-4)
    assert row["cop_sys [-]"] == pytest.approx(2.991, abs=0.001)
    assert row["dV_ou_a [m3/s]"] == pytest.approx(CORNER_UNIT["dV_ou_fan_a_rated"], rel=1e-3)
    assert row["dV_iu_a [m3/s]"] == pytest.approx(CORNER_UNIT["dV_iu_fan_a_rated"], rel=1e-3)


def test_a_finished_least_lift_refinement_answers_although_a_hair_dearer(monkeypatch):
    """A capped refinement that stopped 1e-5 of the input below where the least-lift one ends.

    Two refinements that end in the same place differ by as much; the finished
    one is the answer. The fallback used to take it only where it was no
    dearer at all, and the unit was answered as switched off
    (``optimizer_failed``) -- with the cap of 1000 at six of 36 432 cooling
    points of the two units searched with the semi-empirical models, with a
    cap of 200 at 135, every one of them a finished least-lift refinement
    about 1e-5 dearer.
    """
    point = dict(Q_r_iu=4950.0, T0=38.0, T_a_room=26.0, verbose=False)
    unit = AirSourceHeatPump(**CORNER_UNIT)
    row = unit.analyze_steady(**point)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(4950.0, rel=1e-9)
    assert row["cop_sys [-]"] == pytest.approx(3.3368, abs=0.001)

    refinements = _first_refinement_stopped_by_the_cap(monkeypatch, -1e-5)
    capped = unit.analyze_steady(**point)
    assert refinements[0][:2] == (AirSourceHeatPump._REFINE_MAXITER, False) and refinements[-1][1]
    # The finished refinement is the dearer of the two, by less than the band of the fallback.
    assert 0.0 < refinements[-1][2] / refinements[0][2] - 1.0 < AirSourceHeatPump._SEED_GAIN
    assert capped["failure_reason"] == "none" and capped["hp_is_on"]
    assert capped["cop_sys [-]"] == pytest.approx(row["cop_sys [-]"], rel=1e-4)
