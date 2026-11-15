"""The operating-point refinement of ``AirSourceHeatPump`` at a corner of the feasible set.

Where the least-power point has both fans at their largest airflow, the
Nelder-Mead simplex contracts against the infeasible side and takes several
hundred iterations to meet its tolerance. With an iteration cap of 200 the
refinement stopped 5e-5 of the input away from the answer, reported
``success=False``, and the unit was answered as switched off
(``optimizer_failed``). The cap is a guard, not a tolerance.
"""

import pytest
from scipy.optimize import minimize

import tmhp.air_source_heat_pump as module
from tmhp import AirSourceHeatPump

#: Daikin RXM42A with the catalogue displacement and outdoor airflow of a 4.2 kW R32 unit
#: (issue #86) and its published indoor airflow, at 40 degC outdoors and a 32 degC room.
UNIT = dict(
    ref="R32",
    hp_capacity=4200.0,
    V_cmp_ref=1.2654764368270026e-05,
    dV_ou_fan_a_rated=0.6329143316922459,
    dV_iu_fan_a_rated=0.2233,
    dT_approach_bounds=(1.0, 30.0),
)
POINT = dict(Q_r_iu=4490.0, T0=40.0, T_a_room=32.0, verbose=False)


def test_the_corner_point_is_solved():
    row = AirSourceHeatPump(**UNIT).analyze_steady(**POINT)
    assert row["failure_reason"] == "none" and row["hp_is_on"] and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(4490.0, rel=1e-9)
    assert row["cop_sys [-]"] == pytest.approx(3.812, abs=0.005)
    # Both fans at their largest airflow: the least-lift corner.
    assert row["dV_ou_a [m3/s]"] == pytest.approx(UNIT["dV_ou_fan_a_rated"], rel=1e-3)
    assert row["dV_iu_a [m3/s]"] == pytest.approx(UNIT["dV_iu_fan_a_rated"], rel=1e-3)


def test_the_refinement_needs_more_than_two_hundred_iterations_there(monkeypatch):
    """The point this guards: with the earlier cap the same request is refused."""
    iterations = []

    def counted(fun, x0, **kwargs):
        result = minimize(fun, x0, **kwargs)
        iterations.append(int(result.nit))
        return result

    monkeypatch.setattr(module, "minimize", counted)
    unit = AirSourceHeatPump(**UNIT)
    assert unit.analyze_steady(**POINT)["failure_reason"] == "none"
    assert 200 < max(iterations) < AirSourceHeatPump._REFINE_MAXITER

    monkeypatch.setattr(AirSourceHeatPump, "_REFINE_MAXITER", 200)
    assert unit.analyze_steady(**POINT)["failure_reason"] == "optimizer_failed"
