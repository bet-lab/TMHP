"""Capacity ceiling of the air-to-air model at its envelope.

Two defects of the default 4 kW unit, each a result no machine gives:

* at -25 degC outdoor / 24 degC room a 1.2 kW request ran, and every request
  from 2 kW up switched the unit off with ``pr_above_max`` -- the feasible
  approaches there are a strip about a kelvin wide, which neither the search
  nor the ceiling's grid entered;
* a capacity-limited R290 or R134a unit ran its outdoor fan at 17-18 % of its
  airflow: capacity-limited candidates were ranked by delivered heat, and an
  adiabatic compressor turns its losses into heat, so starving the coil
  (pressure ratio up to the edge of the efficiency map, isentropic efficiency
  down) scored higher.

The ceiling is now the unit running flat out -- compressor at ``rps_max``,
both fans at their largest airflow -- or, where that is outside the envelope,
the largest duty whose least-lift point is an operating point.
"""

from __future__ import annotations

import warnings

import pytest

from tmhp import AirSourceHeatPump
from tmhp.compressor_envelope import (
    BINDING_DISCHARGE,
    BINDING_EFFICIENCY,
    BINDING_PRESSURE_RATIO,
    DISCHARGE_TOL_K,
)

COLD = dict(T0=-25.0, T_a_room=24.0)


def _solve(model: AirSourceHeatPump, Q_r_iu: float, T0: float, T_a_room: float) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return model.analyze_steady(Q_r_iu=Q_r_iu, T0=T0, T_a_room=T_a_room, verbose=False, postprocess=False)


@pytest.fixture(scope="module")
def r32() -> AirSourceHeatPump:
    """The default unit, frost-free so that airflows and duties are those of the cycle."""
    return AirSourceHeatPump(ref="R32", defrost=None)


@pytest.fixture(scope="module")
def r32_cold_ceiling(r32) -> dict:
    return r32.max_capacity(**COLD, mode="heating")


# ── a request above a duty that runs is not switched off ──────────────────


def test_a_request_above_a_duty_that_runs_is_served_at_a_ceiling(r32, r32_cold_ceiling):
    """-25 degC / 24 degC: 1.4 kW ran and 2.3 kW and more were ``pr_above_max``."""
    low = _solve(r32, -1400.0, **COLD)
    assert low["failure_reason"] == "none" and low["capacity_clamped"] is None
    for request in (4000.0, 6000.0):
        out = _solve(r32, -request, **COLD)
        assert out["hp_is_on"] and out["failure_reason"] == "none"
        assert out["capacity_clamped"] == "max" and out["capacity_binding"] == "compressor"
        # The ceiling belongs to the machine and the weather, not to the request.
        assert out["Q_ref_iu [W]"] == pytest.approx(r32_cold_ceiling["Q_max [W]"], rel=1e-9)
        assert out["Q_max [W]"] == pytest.approx(out["Q_ref_iu [W]"], rel=1e-12)
        assert out["Q_unmet [W]"] == pytest.approx(request - out["Q_ref_iu [W]"], rel=1e-9)
        assert out["pr_cmp [-]"] <= r32.PR_cycle_max
        assert out["T_ref_cmp_out [°C]"] <= r32.T_dis_max + DISCHARGE_TOL_K
    assert low["Q_ref_iu [W]"] < r32_cold_ceiling["Q_max [W]"] < 4000.0


def test_a_duty_the_envelope_refuses_below_the_ceiling_runs_at_the_largest_duty_below_it(r32, r32_cold_ceiling):
    """The envelope is not monotone in the duty: at mid speed the discharge runs hotter than flat out.

    2.3 kW has no operating point here although 3.6 kW has one. The unit runs
    at the largest duty below the request that does, as the water-heating
    models do, and names the limit.
    """
    request = 2300.0
    assert request < r32_cold_ceiling["Q_max [W]"]
    out = _solve(r32, -request, **COLD)
    assert out["hp_is_on"] and out["failure_reason"] == "none"
    assert out["capacity_clamped"] == "max" and out["capacity_binding"] == BINDING_DISCHARGE
    assert 0.9 * request < out["Q_ref_iu [W]"] < request
    assert out["T_ref_cmp_out [°C]"] <= r32.T_dis_max + DISCHARGE_TOL_K
    assert out["cmp_rpm [rpm]"] < 0.9 * r32.rps_max * 60.0


def test_no_request_is_refused_above_one_that_runs(r32):
    """Along a ladder of requests the unit never goes from running to off."""
    for T_a_room in (18.0, 24.0):
        running = [
            bool(_solve(r32, -q, T0=-25.0, T_a_room=T_a_room)["hp_is_on"])
            for q in (800.0, 1400.0, 2300.0, 3200.0, 4600.0)
        ]
        assert running[0]
        assert all(later or not earlier for earlier, later in zip(running, running[1:], strict=False)), running


# ── flat out means both fans at their largest airflow ─────────────────────


@pytest.mark.parametrize(
    "ref,T0,T_a_room",
    [("R290", -15.0, 18.0), ("R134a", -7.0, 18.0), ("R410A", -15.0, 20.0)],
)
def test_a_flat_out_unit_does_not_starve_its_outdoor_coil(ref, T0, T_a_room):
    """Outdoor airflow 17-33 % of its largest, pressure ratio at the edge of the efficiency map."""
    hp = AirSourceHeatPump(ref=ref, defrost=None)
    out = _solve(hp, -6000.0, T0=T0, T_a_room=T_a_room)
    assert out["capacity_clamped"] == "max" and out["capacity_binding"] == "compressor"
    assert out["cmp_rpm [rpm]"] == pytest.approx(hp.rps_max * 60.0, rel=1e-6)
    assert out["dV_ou_a [m3/s]"] == pytest.approx(hp.dV_ou_fan_a_max, rel=1e-5)
    assert out["dV_iu_a [m3/s]"] == pytest.approx(hp.dV_iu_fan_a_max, rel=1e-5)
    # The ceiling continues the duties just inside it: no jump in the COP.
    below = _solve(hp, -0.98 * out["Q_ref_iu [W]"], T0=T0, T_a_room=T_a_room)
    assert below["capacity_clamped"] is None
    assert below["cop_sys [-]"] == pytest.approx(out["cop_sys [-]"], rel=0.05)


def test_the_ceiling_is_the_least_lift_point_at_the_speed_ceiling(r32):
    ceiling = r32.max_capacity(T0=-15.0, T_a_room=20.0, mode="heating")
    assert ceiling["binding"] == "compressor"
    state = r32._calc_state(
        dT_ref_evap=ceiling["dT_ref_evap [K]"],
        dT_ref_cond=ceiling["dT_ref_cond [K]"],
        Q_r_iu=-1.0e6,
        T0=-15.0,
        T_a_room=20.0,
    )
    assert state["converged"] and state["capacity_clamped"] == "max"
    assert state["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)
    # A smaller approach on either coil needs more air than its fan moves.
    for de, dc in [(0.999, 1.0), (1.0, 0.999)]:
        tighter = r32._calc_state(
            dT_ref_evap=de * ceiling["dT_ref_evap [K]"],
            dT_ref_cond=dc * ceiling["dT_ref_cond [K]"],
            Q_r_iu=-1.0e6,
            T0=-15.0,
            T_a_room=20.0,
        )
        assert tighter is None or not tighter["converged"]


def test_a_request_just_below_the_ceiling_is_met_and_one_above_is_not(r32):
    for T0 in (-15.0, 7.0):
        ceiling = r32.max_capacity(T0=T0, T_a_room=20.0, mode="heating")["Q_max [W]"]
        below = _solve(r32, -0.999 * ceiling, T0=T0, T_a_room=20.0)
        assert below["capacity_clamped"] is None
        assert below["Q_ref_iu [W]"] == pytest.approx(0.999 * ceiling, rel=1e-9)
        above = _solve(r32, -1.001 * ceiling, T0=T0, T_a_room=20.0)
        assert above["capacity_clamped"] == "max"
        assert above["Q_ref_iu [W]"] == pytest.approx(ceiling, rel=1e-9)
        # No jump in the input across the ceiling.
        assert above["E_tot [W]"] == pytest.approx(below["E_tot [W]"], rel=5e-3)


# ── what stops the unit is named as the water-heating models name it ──────


def test_the_binding_names_the_envelope_limit():
    ammonia = AirSourceHeatPump(ref="R717", defrost=None)
    hot = _solve(ammonia, -6000.0, T0=-15.0, T_a_room=24.0)
    assert hot["capacity_clamped"] == "max" and hot["capacity_binding"] == BINDING_DISCHARGE
    assert hot["T_ref_cmp_out [°C]"] == pytest.approx(ammonia.T_dis_max, abs=2 * DISCHARGE_TOL_K)
    assert hot["x_ref_cmp_in [-]"] == pytest.approx(ammonia.x_suction_min, abs=2e-3)
    assert hot["cmp_rpm [rpm]"] < ammonia.rps_max * 60.0

    lifted = AirSourceHeatPump(ref="R32", defrost=None, PR_cycle_max=5.0, T_dis_max=None)
    out = _solve(lifted, -6000.0, T0=-15.0, T_a_room=20.0)
    assert out["capacity_clamped"] == "max" and out["capacity_binding"] == BINDING_PRESSURE_RATIO
    assert out["pr_cmp [-]"] == pytest.approx(5.0, rel=2e-3) and out["pr_cmp [-]"] <= 5.0
    assert out["cmp_rpm [rpm]"] < lifted.rps_max * 60.0

    mild = _solve(AirSourceHeatPump(defrost=None), -6000.0, T0=7.0, T_a_room=20.0)
    assert mild["capacity_clamped"] == "max" and mild["capacity_binding"] == "cycle"


def test_a_condition_without_any_operating_point_names_the_limit_at_the_least_lift_point():
    """R717 at -25 degC was ``pr_above_max``, read off a probe at a 15 K + 15 K approach."""
    ammonia = AirSourceHeatPump(ref="R717", defrost=None)
    ceiling = ammonia.max_capacity(T0=-25.0, T_a_room=20.0, mode="heating")
    assert ceiling["binding"] == "infeasible" and ceiling["Q_max [W]"] == 0.0
    out = _solve(ammonia, -2000.0, T0=-25.0, T_a_room=20.0)
    assert not out["hp_is_on"] and out["Q_ref_iu [W]"] == 0.0 and out["E_tot [W]"] == 0.0
    assert out["failure_reason"] in {"discharge_temp_above_max", "eta_isen_below_min"}
    assert BINDING_EFFICIENCY == "compressor_efficiency"  # the name such a ceiling would carry


# ── the least-power search enters a thin feasible set ─────────────────────


def test_the_search_is_not_left_on_the_flat_stretch_under_discharge_control():
    """R410A, -25 degC / 20 degC: 1,131 W was returned at 2.25 kW where 929 W delivers the same heat.

    Under the discharge-temperature control the input hardly depends on the
    outdoor approach, and the refinement stopped there with the outdoor fan
    at a quarter of its airflow -- or not, from one duty to the next: 1,149 W
    at 2,300 W and 958 W at 2,304 W.
    """
    hp = AirSourceHeatPump(ref="R410A", defrost=None)
    power = []
    for duty in (2200.0, 2250.0, 2300.0, 2304.0):
        out = _solve(hp, -duty, T0=-25.0, T_a_room=20.0)
        assert out["capacity_clamped"] is None and out["Q_ref_iu [W]"] == pytest.approx(duty, rel=1e-9)
        assert out["dV_ou_a [m3/s]"] > 0.6 * hp.dV_ou_fan_a_max
        power.append(out["E_tot [W]"])
    assert power[1] < 950.0
    # More heat costs more, and 4 W more heat costs about 2 W.
    assert power[0] < power[1] < power[2] < power[3]
    assert power[3] - power[2] < 5.0


def test_ordinary_duties_are_where_they_were(r32):
    """The least-lift start is a second start: it replaces the result only for a material gain."""
    out = _solve(r32, -2000.0, T0=7.0, T_a_room=20.0)
    assert out["capacity_clamped"] is None
    assert out["E_tot [W]"] == pytest.approx(360.9873001, rel=1e-6)
    cooling = _solve(r32, 3000.0, T0=35.0, T_a_room=27.0)
    assert cooling["capacity_clamped"] is None
    assert cooling["E_tot [W]"] == pytest.approx(669.6156, rel=1e-5)


def test_cooling_ceiling_is_flat_out_too(r32):
    ceiling = r32.max_capacity(T0=40.0, T_a_room=24.0, mode="cooling")
    out = _solve(r32, 8000.0, T0=40.0, T_a_room=24.0)
    assert out["capacity_clamped"] == "max"
    assert out["Q_ref_iu [W]"] == pytest.approx(ceiling["Q_max [W]"], rel=1e-9)
    assert ceiling["binding"] in {"compressor", "cycle"}
