"""Which flat-out candidate ``GroundSourceHeatPump`` reports as its capacity ceiling.

With the compressor at ``rps_max`` the delivered heat is nearly independent of
the indoor approach: across the band the fan can serve it varies by a tenth of
a percent, while the electrical input rises by 6 %. The search ranked those
candidates by delivered heat alone, so a few watts decided between the largest
airflow and a starved coil: the default R1234yf unit at a 0 °C borehole wall
and 24 °C room reported 4,105 W at COP 2.90 (airflow 0.190 m³/s, condensing at
49.0 °C) where the largest airflow delivers 4,102 W at COP 3.05 -- and a small
change of an input (the indoor air density, #146) moved it from one to the
other.

Heat within 0.5 % of the largest is the same ceiling; among those candidates
the one of least total input is the operating point.
"""

import warnings

import numpy as np
import pytest

from tmhp import GroundSourceHeatPump
from tmhp._opt_utils import OBJ_CAPACITY_LIMITED, OBJ_INFEASIBLE
from tmhp.ground_source_heat_pump import _IndoorSearch

POINT = dict(T0=0.0, T_a_room=24.0, T_bhe_wall=0.0)
#: Relative heat within which two flat-out candidates deliver the same ceiling.
TOL = 5e-3


def test_tolerance_is_the_models():
    assert GroundSourceHeatPump._CEILING_HEAT_RTOL == TOL


def _unit(ref):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return GroundSourceHeatPump(ref=ref)


@pytest.fixture(scope="module")
def r1234yf():
    return _unit("R1234yf")


def _flat_out_candidates(model, point, request=-6000.0, samples=41):
    """(approach, heat, input, airflow) of the max-speed candidates the fan can serve."""
    search = _IndoorSearch(model, request, point["T0"], point["T_a_room"], 273.15 + point["T_bhe_wall"])
    rows = []
    for x in np.linspace(model.indoor_approach_min_K, model.indoor_approach_max_K, samples):
        value, _, row = search.entry(float(x))
        if row is not None and OBJ_CAPACITY_LIMITED <= value < OBJ_INFEASIBLE and row["capacity_clamped"] == "max":
            rows.append((float(x), abs(row["Q_ref_iu [W]"]), row["E_tot [W]"], row["dV_iu_a [m3/s]"]))
    return rows


def test_flat_out_heat_is_flat_and_the_input_is_not(r1234yf):
    """The premise: the candidates differ in heat by far less than the tolerance, in input by several percent."""
    heat = [c[1] for c in _flat_out_candidates(r1234yf, POINT)]
    power = [c[2] for c in _flat_out_candidates(r1234yf, POINT)]
    assert len(heat) >= 4
    assert (max(heat) - min(heat)) / max(heat) < 0.2 * TOL
    assert (max(power) - min(power)) / min(power) > 0.03


def test_ceiling_is_the_least_input_candidate_among_the_largest_heat(r1234yf):
    row = r1234yf.analyze_steady(Q_r_iu=-6000.0, **POINT)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == "max"
    assert row["capacity_binding"] == "compressor" and row["Q_max [W]"] == row["Q_ref_iu [W]"]
    candidates = _flat_out_candidates(r1234yf, POINT)
    largest = max(c[1] for c in candidates)
    assert row["Q_ref_iu [W]"] >= (1.0 - TOL) * largest
    qualifying = [c for c in candidates if c[1] >= (1.0 - TOL) * largest]
    assert row["E_tot [W]"] <= min(c[2] for c in qualifying) + 1e-6  # was the largest input of the band
    # The fan runs at its largest airflow: the least lift at which the flat-out duty is delivered.
    assert row["dV_iu_a [m3/s]"] == pytest.approx(r1234yf.dV_iu_fan_a_max, rel=1e-4)
    assert row["cmp_rpm [rpm]"] / 60 == pytest.approx(r1234yf.rps_max, rel=1e-9)


def test_ceiling_row_joins_the_requests_just_below_it(r1234yf):
    """A request arriving at the ceiling from below ends at the row the ceiling reports."""
    ceiling = r1234yf.analyze_steady(Q_r_iu=-6000.0, **POINT)
    below = r1234yf.analyze_steady(Q_r_iu=-0.995 * ceiling["Q_max [W]"], **POINT)
    assert below["capacity_clamped"] is None
    assert below["cop_sys [-]"] == pytest.approx(ceiling["cop_sys [-]"], rel=1e-2)  # 6 % apart before
    assert below["dV_iu_a [m3/s]"] == pytest.approx(ceiling["dV_iu_a [m3/s]"], rel=1e-3)
    assert below["T_ref_cond_sat_l [°C]"] == pytest.approx(ceiling["T_ref_cond_sat_l [°C]"], abs=0.3)


def test_ceiling_does_not_depend_on_how_far_the_request_is_above_it(r1234yf):
    rows = [r1234yf.analyze_steady(Q_r_iu=-q, **POINT) for q in (4400.0, 6000.0, 12000.0)]
    for key in ("Q_ref_iu [W]", "E_tot [W]", "dV_iu_a [m3/s]", "indoor_approach [K]"):
        assert [r[key] for r in rows[1:]] == pytest.approx([rows[0][key]] * 2, rel=1e-9), key
    assert [r["Q_unmet [W]"] for r in rows] == pytest.approx([q - rows[0]["Q_ref_iu [W]"] for q in (4400, 6000, 12000)])


def test_a_small_change_of_an_input_does_not_move_the_ceiling_to_another_airflow(r1234yf):
    """Neighbouring room temperatures give neighbouring ceilings (the two solutions were 6 % apart in COP)."""
    rows = [r1234yf.analyze_steady(Q_r_iu=-6000.0, **{**POINT, "T_a_room": t}) for t in (23.5, 24.0, 24.5)]
    cop = [r["cop_sys [-]"] for r in rows]
    assert cop[0] > cop[1] > cop[2]  # a warmer room is a larger lift
    assert cop[0] / cop[2] - 1.0 < 0.04
    assert all(r["dV_iu_a [m3/s]"] == pytest.approx(r1234yf.dV_iu_fan_a_max, rel=1e-4) for r in rows)


def test_where_the_largest_airflow_delivers_less_the_heat_tolerance_decides():
    """R134a, 21 °C room: the lower edge of the band is 0.9 % short of the largest heat.

    The operating point is then where the heat reaches the tolerance band,
    not the edge and not the starved end -- so it moves continuously as the
    edge crosses the band.
    """
    unit = _unit("R134a")
    point = {**POINT, "T_a_room": 21.0}
    row = unit.analyze_steady(Q_r_iu=-6000.0, **point)
    candidates = _flat_out_candidates(unit, point, samples=97)
    largest = max(c[1] for c in candidates)
    edge = min(candidates)  # smallest approach: largest airflow
    assert edge[1] < (1.0 - TOL) * largest
    assert row["capacity_clamped"] == "max"
    assert row["Q_ref_iu [W]"] == pytest.approx((1.0 - TOL) * largest, rel=2e-4)
    starved = max(candidates, key=lambda c: c[1])
    assert edge[2] < row["E_tot [W]"] < starved[2]
    assert starved[3] < row["dV_iu_a [m3/s]"] <= unit.dV_iu_fan_a_max


def test_coil_limited_ceiling_is_the_least_input_point_of_its_duty():
    """A ceiling found by bisection on the duty (``capacity_binding == "cycle"``) is solved as that duty."""
    unit = _unit("R32")
    point = dict(T0=0.0, T_a_room=18.0, T_bhe_wall=10.0)
    ceiling = unit.analyze_steady(Q_r_iu=-8000.0, **point)
    assert ceiling["capacity_clamped"] == "max" and ceiling["capacity_binding"] == "cycle"
    same_duty = unit.analyze_steady(Q_r_iu=-ceiling["Q_max [W]"], **point)
    assert same_duty["capacity_clamped"] is None
    assert ceiling["E_tot [W]"] == pytest.approx(same_duty["E_tot [W]"], rel=1e-6)
    assert ceiling["cop_sys [-]"] == pytest.approx(same_duty["cop_sys [-]"], rel=1e-6)
