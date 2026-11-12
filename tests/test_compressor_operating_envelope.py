"""Compressor operating envelope: discharge temperature, speed and pressure-ratio limits.

See :mod:`tmhp.compressor_envelope`. The reproduction inputs are the #96 /
#104 rediagnosis points that the models used to report as normal operation:
discharge 357 degC (GSHPB R32 B0/W55 8 kW), 271 degC (ASHPB R32 A-7/W55 15 kW)
and 201.6 degC (ASHPB R32 A0/W60 15 kW), and the low-speed / high-pressure-ratio
corner at eta_is 0.14.
"""

from __future__ import annotations

import math
import warnings

import pandas as pd
import pytest

import tmhp
from tmhp.compressor_envelope import (
    BINDING_DISCHARGE,
    DISCHARGE_TEMP_ABOVE_MAX,
    PR_CYCLE_MAX_SINGLE_STAGE,
    RATED_SPEED_SEARCH_MAX,
    RPS_MAX_ABSOLUTE,
    default_discharge_temp_max,
    default_rps_max,
    discharge_temp_exceeded,
    resolve_discharge_temp_max,
)

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

FAMILIES = (
    tmhp.AirSourceHeatPump,
    tmhp.GroundSourceHeatPump,
    tmhp.AirSourceHeatPumpBoiler,
    tmhp.GroundSourceHeatPumpBoiler,
    tmhp.WaterSourceHeatPumpBoiler,
)


def _quiet(function, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return function(*args, **kwargs)


def _running(r: dict) -> bool:
    return bool(r.get("hp_is_on")) and r.get("converged") is True and r.get("failure_reason") == "none"


def _off(r: dict, reason: str) -> None:
    assert r["failure_reason"] == reason
    assert not r["hp_is_on"]
    assert r["Q_ref_tank [W]"] == 0.0 and r["E_cmp [W]"] == 0.0


# ---------------------------------------------------------------------------
# limits and their resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "limit"),
    [("R32", 150.0), ("HEOS::R32", 150.0), ("R410A", 135.0), ("R290", 135.0), ("R134a", 135.0), ("R1234yf", 135.0)],
)
def test_default_discharge_limit_by_refrigerant(ref, limit):
    assert default_discharge_temp_max(ref) == limit
    assert resolve_discharge_temp_max("auto", ref) == limit


def test_discharge_limit_resolution():
    assert resolve_discharge_temp_max(None, "R32") is None
    assert resolve_discharge_temp_max(120, "R32") == 120.0
    with pytest.raises(ValueError, match="T_dis_max"):
        resolve_discharge_temp_max("off", "R32")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        resolve_discharge_temp_max(math.nan, "R32")


def test_discharge_exceeded_counts_undefined_discharge():
    assert not discharge_temp_exceeded(150.0, 150.0)
    assert discharge_temp_exceeded(150.01, 150.0)
    assert discharge_temp_exceeded(math.nan, 150.0)  # no discharge state at all
    assert not discharge_temp_exceeded(math.nan, None)  # check off


@pytest.mark.parametrize("cls", FAMILIES)
def test_every_family_carries_the_envelope(cls):
    model = _quiet(cls, ref="R32")
    assert model.T_dis_max == 150.0
    assert model.PR_cycle_max == PR_CYCLE_MAX_SINGLE_STAGE == 11.0
    assert model.rps_max == pytest.approx(default_rps_max(model.rps_rated))
    assert _quiet(cls, ref="R32", T_dis_max=None).T_dis_max is None


# ---------------------------------------------------------------------------
# rps_max derived from the rated speed
# ---------------------------------------------------------------------------


def test_default_rps_max_rule():
    assert default_rps_max(30.0) == 60.0
    assert default_rps_max(70.0) == RPS_MAX_ABSOLUTE
    # A machine already rated fast keeps 1.5x headroom (catalogue max/rated <= 1.52).
    assert default_rps_max(90.0) == 135.0
    assert RATED_SPEED_SEARCH_MAX > RPS_MAX_ABSOLUTE


def test_rps_max_follows_the_rated_speed_after_it_is_solved():
    # Default R134a boiler is rated at ~90 rev/s: solved over the wide search
    # range, then given 1.5x headroom (the 120 rev/s bound would leave 1.33x).
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler)
    assert 80.0 < hp.rps_rated < RPS_MAX_ABSOLUTE
    assert hp.rps_max == pytest.approx(1.5 * hp.rps_rated)
    small = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0)
    assert small.rps_max == pytest.approx(2.0 * small.rps_rated)


def test_explicit_rps_max_and_rated_speed_are_kept():
    hp = _quiet(tmhp.WaterSourceHeatPumpBoiler, rps_max=130.0)
    assert hp.rps_max == 130.0
    fixed = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0, rps_rated=40.0)
    assert fixed.rps_max == 80.0


def test_speed_ceiling_caps_air_to_air_capacity_in_the_cold():
    """The former 150 rev/s let ASHP deliver 150 % of nameplate at -25 degC."""
    hp = _quiet(tmhp.AirSourceHeatPump, ref="R32", hp_capacity=4000.0)
    r = _quiet(hp.analyze_steady, Q_r_iu=-6000.0, T0=-25.0, T_a_room=20.0, verbose=False)
    assert _running(r)
    assert r["capacity_clamped"] == "max"
    assert r["cmp_rpm [rpm]"] / 60.0 <= hp.rps_max + 1e-6
    assert abs(r["Q_ref_iu [W]"]) < hp.hp_capacity


# ---------------------------------------------------------------------------
# discharge temperature: reproduction inputs
# ---------------------------------------------------------------------------


def test_gshpb_r32_b0_w55_discharge_357C_is_not_an_operating_point():
    hp = _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R32", hp_capacity=8000.0)
    r = _quiet(hp.analyze_steady, T_tank_w=55.0, T_source=0.0, Q_ref_tank=8000.0)
    # Even the minimum-speed duty discharges above 150 degC (173 degC).
    _off(r, DISCHARGE_TEMP_ABOVE_MAX)
    legacy = _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R32", hp_capacity=8000.0, T_dis_max=None)
    old = _quiet(legacy.analyze_steady, T_tank_w=55.0, T_source=0.0, Q_ref_tank=8000.0)
    assert _running(old)
    assert old["T_ref_cmp_out [°C]"] > 300.0  # the value #104 reported (357 degC)


@pytest.mark.parametrize(("T0", "T_tank"), [(-7.0, 55.0), (0.0, 60.0)])
def test_ashpb_r32_high_lift_points_are_refused(T0, T_tank):
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0)
    r = _quiet(hp.analyze_steady, T_tank_w=T_tank, T0=T0, Q_ref_tank=15000.0)
    # Load reduction does not help here: at lower speed eta_is falls and the
    # discharge stays near 190 degC down to the speed floor.
    _off(r, DISCHARGE_TEMP_ABOVE_MAX)


def test_ashpb_r32_rating_point_a7_w55_still_runs():
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0)
    r = _quiet(hp.analyze_steady, T_tank_w=55.0, T0=7.0, Q_ref_tank=15000.0)
    assert _running(r)
    assert r["capacity_clamped"] is None
    assert r["T_ref_cmp_out [°C]"] <= hp.T_dis_max


def test_switching_the_check_off_restores_the_unbounded_answer():
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0, T_dis_max=None)
    r = _quiet(hp.analyze_steady, T_tank_w=60.0, T0=0.0, Q_ref_tank=15000.0)
    assert _running(r)
    assert r["T_ref_cmp_out [°C]"] > 190.0


# ---------------------------------------------------------------------------
# discharge temperature: a smaller duty inside the limit is the capacity ceiling
# ---------------------------------------------------------------------------


def test_boiler_request_above_the_limit_returns_the_largest_duty_inside_it():
    hp = _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R410A", hp_capacity=8000.0)
    r = _quiet(hp.analyze_steady, T_tank_w=55.0, T_source=0.0, Q_ref_tank=8000.0)
    assert _running(r)
    assert r["capacity_clamped"] == "max"
    assert r["capacity_binding"] == BINDING_DISCHARGE
    assert r["Q_request [W]"] == 8000.0 and r["Q_tank_load [W]"] == 8000.0
    assert r["Q_ref_tank [W]"] < 8000.0
    assert r["Q_max [W]"] == pytest.approx(r["Q_ref_tank [W]"])
    assert r["T_ref_cmp_out [°C]"] <= hp.T_dis_max
    # A slightly larger duty is outside the limit (bisection to 1 % of the request).
    above = _quiet(hp.analyze_steady, T_tank_w=55.0, T_source=0.0, Q_ref_tank=r["Q_ref_tank [W]"] + 0.02 * 8000.0)
    assert above["capacity_binding"] == BINDING_DISCHARGE


def test_boiler_dataframe_return_keeps_the_envelope_keys():
    hp = _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R410A", hp_capacity=8000.0)
    df = _quiet(hp.analyze_steady, T_tank_w=55.0, T_source=0.0, Q_ref_tank=8000.0, return_dict=False)
    assert isinstance(df, pd.DataFrame) and len(df) == 1
    assert df["capacity_binding"].iat[0] == BINDING_DISCHARGE
    assert "efficiency_extrapolated" in df.columns


def test_air_to_air_ceiling_names_the_discharge_limit():
    hp = _quiet(tmhp.AirSourceHeatPump, ref="R32", hp_capacity=4000.0, T_dis_max=80.0)
    r = _quiet(hp.analyze_steady, Q_r_iu=-3000.0, T0=-7.0, T_a_room=20.0, verbose=False)
    assert _running(r)
    assert r["capacity_clamped"] == "max"
    assert r["capacity_binding"] == BINDING_DISCHARGE
    assert abs(r["Q_ref_iu [W]"]) < 3000.0
    assert r["T_ref_cmp_out [°C]"] <= 80.0


def test_air_to_air_without_any_duty_inside_the_limit_is_off():
    hp = _quiet(tmhp.AirSourceHeatPump, ref="R32", hp_capacity=4000.0, T_dis_max=40.0)
    r = _quiet(hp.analyze_steady, Q_r_iu=-3000.0, T0=-7.0, T_a_room=20.0, verbose=False)
    assert r["failure_reason"] == DISCHARGE_TEMP_ABOVE_MAX
    assert r["E_cmp [W]"] == 0.0


def test_gshp_reports_the_discharge_limit():
    hp = _quiet(tmhp.GroundSourceHeatPump, ref="R32", T_dis_max=40.0)
    r = _quiet(hp.analyze_steady, Q_r_iu=-3000.0, T0=0.0, T_a_room=20.0)
    assert r["failure_reason"] == DISCHARGE_TEMP_ABOVE_MAX
    assert r["E_cmp [W]"] == 0.0


# ---------------------------------------------------------------------------
# low speed, high pressure ratio
# ---------------------------------------------------------------------------


def test_low_speed_high_pressure_ratio_corner_is_outside_the_envelope():
    """PR > 7.15 at the 15 rev/s floor: the guarded fit gives eta_is 0.14 and
    the discharge 285 degC. The discharge limit refuses it; no separate speed
    floor is needed."""
    case = dict(T_tank_w=65.0, T_source=0.0, Q_ref_tank=1600.0, T0=10.0)
    off = _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R134a", hp_capacity=8000.0, T_dis_max=None)
    corner = _quiet(off.analyze_steady, **case)
    assert _running(corner)
    assert corner["P_ref_cmp_out [Pa]"] / corner["P_ref_cmp_in [Pa]"] > 7.15
    assert corner["cmp_rpm [rpm]"] / 60.0 < 33.8
    assert corner["eta_cmp_isen [-]"] < 0.2
    assert corner["efficiency_extrapolated"] is True
    hp = _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R134a", hp_capacity=8000.0)
    r = _quiet(hp.analyze_steady, **case)
    assert not _running(r) or r["T_ref_cmp_out [°C]"] <= hp.T_dis_max
    assert r["failure_reason"] in ("none", DISCHARGE_TEMP_ABOVE_MAX)


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def test_efficiency_extrapolated_is_reported_by_every_family():
    rows = [
        _quiet(_quiet(tmhp.AirSourceHeatPump, ref="R32").analyze_steady, Q_r_iu=-3000.0, T0=7.0, T_a_room=20.0),
        _quiet(_quiet(tmhp.GroundSourceHeatPump, ref="R32").analyze_steady, Q_r_iu=-3000.0, T0=0.0, T_a_room=20.0),
        _quiet(
            _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0).analyze_steady,
            T_tank_w=35.0,
            T0=7.0,
            Q_ref_tank=15000.0,
        ),
        _quiet(
            _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R410A").analyze_steady,
            T_tank_w=40.0,
            T_source=10.0,
            Q_ref_tank=4000.0,
        ),
        _quiet(
            _quiet(tmhp.WaterSourceHeatPumpBoiler, ref="R410A").analyze_steady,
            T_tank_w=40.0,
            T_source=10.0,
            Q_ref_tank=4000.0,
        ),
    ]
    for r in rows:
        assert _running(r), r.get("failure_reason")
        assert isinstance(r["efficiency_extrapolated"], bool)
    # Inside the fitted box (PR 1.5-7.15, 33.8-72.5 rev/s): not extrapolated.
    inside = rows[2]
    rps = inside["cmp_rpm [rpm]"] / 60.0
    pr = inside["P_ref_cmp_out [Pa]"] / inside["P_ref_cmp_in [Pa]"]
    assert inside["efficiency_extrapolated"] is not (1.5016 <= pr <= 7.1486 and 33.83 <= rps <= 72.5)


def test_scalar_efficiencies_are_never_reported_as_extrapolated():
    hp = _quiet(
        tmhp.AirSourceHeatPumpBoiler,
        ref="R32",
        hp_capacity=15000.0,
        eta_cmp_isen=0.7,
        eta_cmp_vol=0.9,
        eta_cmp=0.95,
    )
    r = _quiet(hp.analyze_steady, T_tank_w=35.0, T0=7.0, Q_ref_tank=4000.0)
    assert _running(r)
    assert r["efficiency_extrapolated"] is False


def test_air_to_air_search_finds_the_narrow_band_left_by_the_discharge_limit():
    """Daikin RXM42A at -15/27 degC, 2.39 kW (adopted catalogue point 67).

    The approaches that carry the duty lie in a 2 x 2.5 K band between the
    coil's airflow limit and T_dis_max; the first 8 x 8 lattice misses it and
    the finer fallback lattice finds the same point as without the limit.
    """
    kwargs = dict(ref="R32", hp_capacity=4200.0, dT_approach_bounds=(1.0, 30.0), dV_iu_fan_a_rated=0.2233)
    with_limit = _quiet(tmhp.AirSourceHeatPump, **kwargs)
    without = _quiet(tmhp.AirSourceHeatPump, **kwargs, T_dis_max=None)
    case = dict(Q_r_iu=-2390.0, T0=-15.0, T_a_room=27.0, verbose=False)
    r = _quiet(with_limit.analyze_steady, **case)
    free = _quiet(without.analyze_steady, **case)
    assert _running(r) and _running(free)
    assert free["T_ref_cmp_out [°C]"] < with_limit.T_dis_max
    assert r["E_tot [W]"] == pytest.approx(free["E_tot [W]"], rel=1e-3)
