"""The operating-point refinement of ``AirSourceHeatPump`` at a corner of the feasible set.

Where the least-power point has both fans at their largest airflow, the
Nelder-Mead simplex contracts against the infeasible side and takes several
hundred iterations to meet its tolerance. With an iteration cap of 200 the
refinement stopped 5e-5 of the input away from the answer, reported
``success=False``, and the unit was answered as switched off
(``optimizer_failed``). The cap is a guard, not a tolerance.

Two things answer such a point. The cap is 1000, which most corners end
inside. And a refinement the cap still stops (no cap bounds the count: 1137
iterations at one point below) is replaced by the refinement that starts on
the least-lift corner itself, where that one ends and is no dearer.

Which point needs how many iterations depends on every detail of the cycle,
so the points below were found again when the default compressor efficiencies
became the rotary-compressor model: a search over 36 432 cooling points of the
two units (30 to 47.5 degC outdoors, 22 to 32 degC room, 0.9 to 1.35 times the
nameplate) for corners the refinement ends at after more than 200 iterations
and for those it does not end at inside 1000. Heating at -15 to 9 degC (0.8 to
1.3 nameplates) gave no refinement above 200 iterations.
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
POINT = dict(Q_r_iu=4830.0, T0=41.0, T_a_room=32.0, verbose=False)  # 526 iterations


def test_the_corner_point_is_solved():
    row = AirSourceHeatPump(**UNIT).analyze_steady(**POINT)
    assert row["failure_reason"] == "none" and row["hp_is_on"] and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(4830.0, rel=1e-9)
    assert row["cop_sys [-]"] == pytest.approx(3.446, abs=0.005)
    # Both fans at their largest airflow: the least-lift corner.
    assert row["dV_ou_a [m3/s]"] == pytest.approx(UNIT["dV_ou_fan_a_rated"], rel=1e-3)
    assert row["dV_iu_a [m3/s]"] == pytest.approx(UNIT["dV_iu_fan_a_rated"], rel=1e-3)


def test_the_refinement_needs_more_than_two_hundred_iterations_there(monkeypatch):
    """The refinement of this point ends, but not inside the earlier cap."""
    iterations = []

    def counted(fun, x0, **kwargs):
        result = minimize(fun, x0, **kwargs)
        iterations.append(int(result.nit))
        return result

    monkeypatch.setattr(module, "minimize", counted)
    unit = AirSourceHeatPump(**UNIT)
    assert unit.analyze_steady(**POINT)["failure_reason"] == "none"
    assert 200 < max(iterations) < AirSourceHeatPump._REFINE_MAXITER


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


def test_a_refinement_the_cap_stops_is_answered_from_the_least_lift_point(monkeypatch):
    """No cap is large enough for every corner: this one needs 1137 iterations.

    The refinement from the pre-scan runs into the cap 1.9e-5 of the input
    above where the least-lift refinement ends, too little for the least-lift
    refinement to replace it as the cheaper of two finished searches. It
    replaces it as the one that finished.
    """
    refinements = []

    def counted(fun, x0, **kwargs):
        result = minimize(fun, x0, **kwargs)
        refinements.append((int(result.nit), bool(result.success)))
        return result

    monkeypatch.setattr(module, "minimize", counted)
    row = AirSourceHeatPump(**CORNER_UNIT).analyze_steady(**CORNER_POINT)
    assert refinements[0] == (AirSourceHeatPump._REFINE_MAXITER, False)
    assert refinements[-1][1] and refinements[-1][0] < 100
    assert row["failure_reason"] == "none" and row["hp_is_on"] and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(4900.0, rel=1e-9)
    assert row["cop_sys [-]"] == pytest.approx(3.0596, abs=0.001)
    assert row["dV_ou_a [m3/s]"] == pytest.approx(CORNER_UNIT["dV_ou_fan_a_rated"], rel=1e-3)
    assert row["dV_iu_a [m3/s]"] == pytest.approx(CORNER_UNIT["dV_iu_fan_a_rated"], rel=1e-3)


def test_the_cap_is_what_solves_a_corner_the_least_lift_refinement_does_not(monkeypatch):
    """Cooling at 38/26 degC, 99 % of the nameplate: 407 iterations, and with a cap of 200 the unit is off.

    Here the refinement from the least-lift point does not answer the point
    with a cap of 200 either: it ends (33 iterations), but 0.005 W above where
    the capped one stopped, and the fallback takes a finished refinement only
    where it is no dearer. The larger cap is not made redundant by the fallback
    above (nor the fallback by the cap: the point above needs 1137).

    The last assertion therefore pins that rule of the fallback as well as the
    cap. It is not new: on the machine this test was first written for (the
    BITZER map and the conductances of then, 216 iterations, COP 3.0023 at
    -10/20 degC) the least-lift refinement also ends, in 32 iterations, 0.05 W
    dearer. A fallback that accepts a finished refinement within the
    refinement's own tolerance (``fatol`` 0.1 W) answers this point with a cap
    of 200 too; this test is to be revisited with such a change, not loosened.
    """
    point = dict(Q_r_iu=4950.0, T0=38.0, T_a_room=26.0, verbose=False)
    unit = AirSourceHeatPump(**CORNER_UNIT)
    row = unit.analyze_steady(**point)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(4950.0, rel=1e-9)
    assert row["cop_sys [-]"] == pytest.approx(3.4403, abs=0.001)

    monkeypatch.setattr(AirSourceHeatPump, "_REFINE_MAXITER", 200)
    assert unit.analyze_steady(**point)["failure_reason"] == "optimizer_failed"
