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
the least-lift corner itself, where that one ends and is not materially
dearer (within the spread of two refinements that end in the same place).

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


def test_a_finished_least_lift_refinement_answers_although_a_hair_dearer(monkeypatch):
    """Cooling at 38/26 degC, 99 % of the nameplate: 407 iterations; with a cap of 200 the fallback answers it.

    Under a cap of 200 the refinement from the pre-scan is stopped, and the
    one from the least-lift point ends (33 iterations) 0.005 W -- 1e-5 of the
    input -- above where the capped one happened to stop. Two refinements
    that end in the same place differ by as much; the finished one is the
    answer. The fallback used to take it only where it was no dearer at all,
    and the unit was answered as switched off (``optimizer_failed``) -- with
    the cap of 1000 at six of 36 432 cooling points of the two units searched,
    with a cap of 200 at 135, every one of them a finished least-lift
    refinement about 1e-5 dearer.
    """
    point = dict(Q_r_iu=4950.0, T0=38.0, T_a_room=26.0, verbose=False)
    unit = AirSourceHeatPump(**CORNER_UNIT)
    row = unit.analyze_steady(**point)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(4950.0, rel=1e-9)
    assert row["cop_sys [-]"] == pytest.approx(3.4403, abs=0.001)

    refinements = []

    def counted(fun, x0, **kwargs):
        result = minimize(fun, x0, **kwargs)
        refinements.append((int(result.nit), bool(result.success), float(result.fun)))
        return result

    monkeypatch.setattr(AirSourceHeatPump, "_REFINE_MAXITER", 200)
    monkeypatch.setattr(module, "minimize", counted)
    capped = unit.analyze_steady(**point)
    assert refinements[0][:2] == (200, False) and refinements[-1][1]
    # The finished refinement is the dearer of the two, by less than the band of the fallback.
    assert 0.0 < refinements[-1][2] / refinements[0][2] - 1.0 < AirSourceHeatPump._SEED_GAIN
    assert capped["failure_reason"] == "none" and capped["hp_is_on"]
    assert capped["cop_sys [-]"] == pytest.approx(row["cop_sys [-]"], rel=1e-4)


def test_a_point_the_default_cap_stops_runs_when_the_least_lift_refinement_ends_a_hair_dearer():
    """Daikin RXM50A, 5.3 kW at 32/30 degC: 1000 iterations without ending, least-lift ends 0.023 W dearer.

    One of the six points at which the cap of 1000 itself left the unit
    switched off under "no dearer at all" (1141.982 W where the capped search
    stopped, 1142.005 W where the least-lift one ended, 2e-5 apart).
    """
    unit = AirSourceHeatPump(
        ref="R32",
        defrost=None,
        hp_capacity=5000.0,
        V_cmp_ref=1.5065195676511936e-05,
        dV_ou_fan_a_rated=0.7196365052574172,
        dV_iu_fan_a_rated=0.2467,
        dT_approach_bounds=(1.0, 30.0),
    )
    row = unit.analyze_steady(Q_r_iu=5300.0, T0=32.0, T_a_room=30.0, verbose=False)
    assert row["failure_reason"] == "none" and row["hp_is_on"] and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(5300.0, rel=1e-9)
    assert row["E_tot [W]"] == pytest.approx(1142.0, abs=0.5)
