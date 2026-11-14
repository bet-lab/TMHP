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

import numpy as np
import pandas as pd
import pytest

import tmhp
from tmhp.compressor_envelope import (
    BINDING_DISCHARGE,
    BINDING_EFFICIENCY,
    BINDING_HEAT_EXCHANGER,
    BINDING_PRESSURE_RATIO,
    DISCHARGE_TEMP_ABOVE_MAX,
    DISCHARGE_TOL_K,
    ETA_ISEN_BELOW_MIN,
    ETA_ISEN_MIN,
    PR_CYCLE_MAX_SINGLE_STAGE,
    RATED_SPEED_SEARCH_MAX,
    RPS_MAX_ABSOLUTE,
    default_discharge_temp_max,
    default_rps_max,
    discharge_temp_exceeded,
    efficiency_below_floor,
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
    assert discharge_temp_exceeded(150.02, 150.0)  # tolerance DISCHARGE_TOL_K = 0.01 K
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


# The discharge-limit reproductions below were found on a GSHPB with the former
# capacity-independent heat exchangers (500 W/K, 24 L/min, 200 W pump: a 16 K
# condensing approach at 8 kW). The defaults now scale with capacity, so that
# machine is spelled out. Its rating point (W55) condenses at 71 degC, above
# the R410A / R32 condensing limit: the limit is off for it.
_GSHPB_UNDERSIZED_HX = dict(UA_tank_hx=500.0, UA_ground=500.0, ground_flow_ref_lpm=24.0, E_pmp=200.0, T_cond_max=None)
# The R32 15 kW water heater of the discharge-control tests charges a tank to
# 65 degC at 70 degC condensing, above the R32 condensing limit (65 degC): the
# limit is off where the discharge control is what is tested.
_R32_15KW = dict(ref="R32", hp_capacity=15000.0, T_cond_max=None)


def test_gshpb_r32_b0_w55_discharge_357C_is_not_an_operating_point():
    case = dict(T_tank_w=55.0, T_source=0.0, Q_ref_tank=8000.0)
    hp = _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R32", hp_capacity=8000.0, **_GSHPB_UNDERSIZED_HX)
    r = _quiet(hp.analyze_steady, **case)
    # Wet suction holds 150 degC only at a smaller duty: the request is the ceiling.
    assert _running(r) and r["discharge_control"] is True
    assert r["capacity_clamped"] == "max" and r["capacity_binding"] == BINDING_DISCHARGE
    assert r["Q_ref_tank [W]"] < 0.6 * 8000.0
    assert 0.9 - 1e-9 <= r["x_ref_cmp_in [-]"] < 1.0
    assert r["T_ref_cmp_out [°C]"] <= 150.0 + DISCHARGE_TOL_K
    # Without the control, even the minimum-speed duty discharges at 173 degC.
    uncontrolled = _quiet(
        tmhp.GroundSourceHeatPumpBoiler, ref="R32", hp_capacity=8000.0, x_suction_min=None, **_GSHPB_UNDERSIZED_HX
    )
    _off(_quiet(uncontrolled.analyze_steady, **case), DISCHARGE_TEMP_ABOVE_MAX)
    # With the discharge limit off, the 357 degC point (eta_is 0.23 at 25 rev/s,
    # the guard's low-speed continuation) is still refused, by the efficiency
    # floor: the largest duty that keeps the fit above it is returned instead.
    legacy = _quiet(
        tmhp.GroundSourceHeatPumpBoiler, ref="R32", hp_capacity=8000.0, T_dis_max=None, **_GSHPB_UNDERSIZED_HX
    )
    old = _quiet(legacy.analyze_steady, **case)
    assert _running(old) and old["capacity_clamped"] == "max"
    assert old["capacity_binding"] == BINDING_EFFICIENCY
    assert old["Q_ref_tank [W]"] < 8000.0 and old["eta_cmp_isen [-]"] >= ETA_ISEN_MIN


def test_ashpb_r32_a_minus7_w55_is_refused_even_with_control():
    """271 degC at eta_is 0.37: wet suction at x 0.9 brings it only to ~197 degC."""
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0)
    r = _quiet(hp.analyze_steady, T_tank_w=55.0, T0=-7.0, Q_ref_tank=15000.0)
    _off(r, DISCHARGE_TEMP_ABOVE_MAX)


@pytest.mark.parametrize("x_min", [None, 1.0])
def test_ashpb_r32_a0_w60_needs_wet_suction(x_min):
    """201.6 degC dry; superheat alone (x_suction_min=1) is not enough, and the
    load reduction does not help (eta_is falls at low speed)."""
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0, x_suction_min=x_min, defrost=False)
    _off(_quiet(hp.analyze_steady, T_tank_w=60.0, T0=0.0, Q_ref_tank=15000.0), DISCHARGE_TEMP_ABOVE_MAX)


def test_ashpb_r32_rating_point_a7_w55_still_runs():
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0)
    r = _quiet(hp.analyze_steady, T_tank_w=55.0, T0=7.0, Q_ref_tank=15000.0)
    assert _running(r)
    assert r["capacity_clamped"] is None
    assert r["T_ref_cmp_out [°C]"] <= hp.T_dis_max + DISCHARGE_TOL_K


def test_switching_the_check_off_restores_the_unbounded_answer():
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0, T_dis_max=None)
    r = _quiet(hp.analyze_steady, T_tank_w=60.0, T0=0.0, Q_ref_tank=15000.0)
    assert _running(r)
    assert r["T_ref_cmp_out [°C]"] > 190.0


# ---------------------------------------------------------------------------
# discharge temperature: a smaller duty inside the limit is the capacity ceiling
# ---------------------------------------------------------------------------


def test_boiler_request_above_the_limit_returns_the_largest_duty_inside_it():
    hp = _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R410A", hp_capacity=8000.0, **_GSHPB_UNDERSIZED_HX)
    r = _quiet(hp.analyze_steady, T_tank_w=55.0, T_source=0.0, Q_ref_tank=8000.0)
    assert _running(r)
    assert r["capacity_clamped"] == "max"
    assert r["capacity_binding"] == BINDING_DISCHARGE
    assert r["Q_request [W]"] == 8000.0 and r["Q_tank_load [W]"] == 8000.0
    assert r["Q_ref_tank [W]"] < 8000.0
    assert r["Q_max [W]"] == pytest.approx(r["Q_ref_tank [W]"])
    assert r["T_ref_cmp_out [°C]"] <= hp.T_dis_max + DISCHARGE_TOL_K
    # A slightly larger duty is outside the limit (bisection to 1 % of the request).
    above = _quiet(hp.analyze_steady, T_tank_w=55.0, T_source=0.0, Q_ref_tank=r["Q_ref_tank [W]"] + 0.02 * 8000.0)
    assert above["capacity_binding"] == BINDING_DISCHARGE


def test_boiler_dataframe_return_keeps_the_envelope_keys():
    hp = _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R410A", hp_capacity=8000.0, **_GSHPB_UNDERSIZED_HX)
    df = _quiet(hp.analyze_steady, T_tank_w=55.0, T_source=0.0, Q_ref_tank=8000.0, return_dict=False)
    assert isinstance(df, pd.DataFrame) and len(df) == 1
    assert df["capacity_binding"].iat[0] == BINDING_DISCHARGE
    assert "efficiency_extrapolated" in df.columns


def test_air_to_air_ceiling_names_the_discharge_limit():
    # The indoor airflow is pinned: with the 400 cfm/ton default the condensing
    # temperature is higher and no duty at all stays below this 80 degC limit.
    hp = _quiet(
        tmhp.AirSourceHeatPump,
        ref="R32",
        hp_capacity=4000.0,
        T_dis_max=80.0,
        x_suction_min=None,
        dV_iu_fan_a_rated=0.8,
    )
    r = _quiet(hp.analyze_steady, Q_r_iu=-3000.0, T0=-7.0, T_a_room=20.0, verbose=False)
    assert _running(r)
    assert r["capacity_clamped"] == "max"
    assert r["capacity_binding"] == BINDING_DISCHARGE
    assert abs(r["Q_ref_iu [W]"]) < 3000.0
    # The ceiling is closed onto the limit, which every check reads with DISCHARGE_TOL_K.
    assert r["T_ref_cmp_out [°C]"] <= 80.0 + DISCHARGE_TOL_K


def test_air_to_air_without_any_duty_inside_the_limit_is_off():
    # Frost-free: with frost the cycle is solved at a larger duty, which this
    # indoor coil cannot carry at the probe the failure reason is read from
    # (``hx_not_converged`` instead of the discharge limit).
    hp = _quiet(tmhp.AirSourceHeatPump, ref="R32", hp_capacity=4000.0, T_dis_max=40.0, defrost=None)
    r = _quiet(hp.analyze_steady, Q_r_iu=-3000.0, T0=-7.0, T_a_room=20.0, verbose=False)
    assert r["failure_reason"] == DISCHARGE_TEMP_ABOVE_MAX
    assert r["E_cmp [W]"] == 0.0


def test_gshp_reports_the_discharge_limit():
    hp = _quiet(tmhp.GroundSourceHeatPump, ref="R32", T_dis_max=40.0, x_suction_min=None)
    r = _quiet(hp.analyze_steady, Q_r_iu=-3000.0, T0=0.0, T_a_room=20.0)
    assert r["failure_reason"] == DISCHARGE_TEMP_ABOVE_MAX
    assert r["E_cmp [W]"] == 0.0


# ---------------------------------------------------------------------------
# low speed, high pressure ratio
# ---------------------------------------------------------------------------


def test_low_speed_high_pressure_ratio_corner_is_outside_the_envelope():
    """PR > 7.15 at the 15 rev/s floor: the guard's continuation gives eta_is 0.14.

    With the discharge-temperature control the discharge can be held, so the
    corner is refused by the isentropic-efficiency floor (0.30, below the
    fit's 0.336 minimum inside its data) instead.
    """
    case = dict(T_tank_w=65.0, T_source=0.0, Q_ref_tank=1600.0, T0=10.0)
    hp = _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R134a", hp_capacity=8000.0)
    r = _quiet(hp.analyze_steady, **case)
    assert r["failure_reason"] in (ETA_ISEN_BELOW_MIN, DISCHARGE_TEMP_ABOVE_MAX)
    assert not r["hp_is_on"]
    # A scalar efficiency is the caller's statement: no floor.
    scalar = _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R134a", hp_capacity=8000.0, eta_cmp_isen=0.25, T_dis_max=None)
    assert _quiet(scalar.analyze_steady, **case)["failure_reason"] != ETA_ISEN_BELOW_MIN


@pytest.mark.parametrize("cls", [tmhp.AirSourceHeatPump, tmhp.GroundSourceHeatPumpBoiler])
def test_efficiency_floor_rule(cls):
    hp = _quiet(cls, ref="R32")
    low = {"eta_cmp_isen [-]": 0.25, "P_ref_cmp_in [Pa]": 3.0e5, "P_ref_cmp_out [Pa]": 2.4e6, "cmp_rpm [rpm]": 900.0}
    assert efficiency_below_floor(hp, low)  # PR 8, 15 rev/s: extrapolated
    inside = {**low, "P_ref_cmp_out [Pa]": 1.5e6, "cmp_rpm [rpm]": 3000.0}  # PR 5, 50 rev/s
    assert not efficiency_below_floor(hp, inside)
    assert not efficiency_below_floor(hp, {**low, "eta_cmp_isen [-]": 0.31})


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


# ---------------------------------------------------------------------------
# discharge-temperature control (superheat, then wet suction)
# ---------------------------------------------------------------------------


def _balance(r: dict, q_cond: str, q_evap: str) -> float:
    work = r["m_dot_ref [kg/s]"] * (r["h_ref_cmp_out [J/kg]"] - r["h_ref_cmp_in [J/kg]"])
    return abs(r[q_cond] - r[q_evap] - work) / r[q_cond]


def test_control_reduces_superheat_first():
    """A2/W55 R32 15 kW: 155.7 degC dry at 5 K superheat; ~1.3 K holds 150 degC."""
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0, defrost=False)
    r = _quiet(hp.analyze_steady, T_tank_w=55.0, T0=2.0, Q_ref_tank=15000.0)
    assert _running(r) and r["discharge_control"] is True
    assert r["x_ref_cmp_in [-]"] == 1.0
    assert 0.0 < r["dT_ref_superheat [K]"] < hp.dT_superheat
    assert r["T_ref_cmp_out [°C]"] == pytest.approx(150.0, abs=0.05)
    assert r["Q_ref_tank [W]"] == pytest.approx(15000.0, rel=1e-6)


def test_control_then_uses_wet_suction_with_a_closed_cycle_balance():
    """A0/W60 R32 15 kW (#96 C6): 201.6 degC dry; wet suction x ~0.92 holds 150 degC."""
    import CoolProp.CoolProp as CP

    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0, defrost=False)
    r = _quiet(hp.analyze_steady, T_tank_w=60.0, T0=0.0, Q_ref_tank=15000.0)
    assert _running(r) and r["discharge_control"] is True
    x = r["x_ref_cmp_in [-]"]
    assert hp.x_suction_min <= x < 1.0
    assert r["dT_ref_superheat [K]"] == 0.0
    assert r["T_ref_cmp_out [°C]"] == pytest.approx(150.0, abs=0.05)
    P1 = r["P_ref_cmp_in [Pa]"]
    assert r["h_ref_cmp_in [J/kg]"] == pytest.approx(CP.PropsSI("H", "P", P1, "Q", x, "R32"), rel=1e-9)
    assert r["T_ref_cmp_in [°C]"] == pytest.approx(r["T_ref_evap_sat [°C]"], abs=1e-9)
    assert _balance(r, "Q_ref_tank [W]", "Q_ref_ou [W]") < 1e-9
    assert r["Q_ref_tank [W]"] == pytest.approx(15000.0, rel=1e-6)


_NO_FLOW = {
    "dV_mix_w_out [m3/s]": 0.0,
    "dV_tank_w_out [m3/s]": 0.0,
    "dV_tank_w_in [m3/s]": 0.0,
    "dV_mix_sup_w_in [m3/s]": 0.0,
    "alp": 0.0,
}


def _count_cycle_evaluations(monkeypatch) -> list:
    """Count the full cycle evaluations of the ASHPB (one outdoor-coil solve each)."""
    import tmhp.air_source_heat_pump_boiler as ashpb

    calls: list = []
    coil = ashpb.calc_HX_perf_for_target_heat

    def counted(*args, **kwargs):
        calls.append(kwargs.get("T_ref_evap_sat_K"))
        return coil(*args, **kwargs)

    monkeypatch.setattr(ashpb, "calc_HX_perf_for_target_heat", counted)
    return calls


@pytest.mark.parametrize(
    ("T_tank_w", "T0", "approach", "stage"),
    [(65.0, 7.0, 6.0, "wet"), (55.0, 2.0, 6.5, "superheat")],
)
def test_control_closes_on_the_limit_in_a_few_cycle_evaluations(monkeypatch, T_tank_w, T0, approach, stage):
    """The control at one candidate: the uncontrolled state plus at most three more.

    The suction state at the limit is predicted from the fluid properties; root
    finding on full cycle evaluations took eight to nine of them per candidate
    and left 1e-3 K of noise in the objective the outdoor search minimises.
    """
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, **_R32_15KW)
    calls = _count_cycle_evaluations(monkeypatch)
    state = hp._calc_state(approach, T_tank_w, 15000.0, T0, flow_state=_NO_FLOW)
    assert state["discharge_control"] is True
    assert state["T_ref_cmp_out [°C]"] == pytest.approx(hp.T_dis_max, abs=1e-4)
    assert (state["x_ref_cmp_in [-]"] < 1.0) is (stage == "wet")
    assert len(calls) <= 4


def test_predicted_and_bracketed_control_agree(monkeypatch):
    """Both closures find the same suction state; the bracketing one is the fallback."""
    from tmhp import compressor_envelope as envelope

    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, **_R32_15KW)
    predicted = hp._calc_state(6.0, 65.0, 15000.0, 7.0, flow_state=_NO_FLOW)
    control = envelope._control
    # Without a fluid for the property estimate the bracketing closure runs.
    monkeypatch.setattr(envelope, "_control", lambda at, sh, T, x, ref, base: control(at, sh, T, x, None, base))
    bracketed = hp._calc_state(6.0, 65.0, 15000.0, 7.0, flow_state=_NO_FLOW)
    assert bracketed["discharge_control"] is True
    assert bracketed["T_ref_cmp_out [°C]"] <= hp.T_dis_max + DISCHARGE_TOL_K
    assert predicted["x_ref_cmp_in [-]"] == pytest.approx(bracketed["x_ref_cmp_in [-]"], abs=2e-5)
    assert predicted["E_tot [W]"] == pytest.approx(bracketed["E_tot [W]"], rel=1e-5)


def test_control_out_of_reach_costs_two_cycle_evaluations(monkeypatch):
    """A-7/W55 R32: still far above the limit at x_suction_min -- decided at once, not searched.

    The state returned is the control's last stage (the suction at
    ``x_suction_min``), the one closest to the envelope, not the uncontrolled one.
    """
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0)
    calls = _count_cycle_evaluations(monkeypatch)
    state = hp._calc_state(8.0, 55.0, 15000.0, -7.0, flow_state=_NO_FLOW)
    assert state["T_ref_cmp_out [°C]"] > hp.T_dis_max + 20.0
    assert state["discharge_control"] is True and state["x_ref_cmp_in [-]"] == pytest.approx(hp.x_suction_min)
    assert len(calls) == 2
    free = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0, x_suction_min=None)
    dry = free._calc_state(8.0, 55.0, 15000.0, -7.0, flow_state=_NO_FLOW)
    assert dry["T_ref_cmp_out [°C]"] > state["T_ref_cmp_out [°C]"] + 20.0


def test_steady_solve_evaluates_each_outdoor_approach_once(monkeypatch):
    """The operating point the search found is reported from the search, not solved again."""
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, **_R32_15KW)
    approaches: list[float] = []
    calc_state = hp._calc_state

    def counted(*args, **kwargs):
        approaches.append(float(kwargs["dT_ref_ou"] if "dT_ref_ou" in kwargs else args[0]))
        return calc_state(*args, **kwargs)

    monkeypatch.setattr(hp, "_calc_state", counted)
    r = _quiet(hp.analyze_steady, T_tank_w=65.0, T0=7.0, Q_ref_tank=15000.0)
    assert _running(r) and r["discharge_control"] is True
    assert len(approaches) == len(set(approaches))


@pytest.mark.parametrize("cls", FAMILIES)
def test_control_arguments_on_every_family(cls):
    hp = _quiet(cls, ref="R32")
    assert hp.x_suction_min == 0.9
    assert _quiet(cls, ref="R32", x_suction_min=None).x_suction_min is None
    with pytest.raises(ValueError, match="x_suction_min"):
        _quiet(cls, ref="R32", x_suction_min=0.0)
    with pytest.raises(ValueError, match="T_dis_max"):
        _quiet(cls, ref="R32", T_dis_max="off")


def test_steady_results_report_the_suction_state():
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0)
    dry = _quiet(hp.analyze_steady, T_tank_w=35.0, T0=7.0, Q_ref_tank=15000.0)
    assert dry["discharge_control"] is False and dry["x_ref_cmp_in [-]"] == 1.0
    # Superheat as solved: the set value capped by the outdoor approach.
    assert 0.0 <= dry["dT_ref_superheat [K]"] <= hp.dT_superheat + 1e-9
    off = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0, T_dis_max=None)
    r = _quiet(off.analyze_steady, T_tank_w=35.0, T0=7.0, Q_ref_tank=15000.0)
    assert r["discharge_control"] is False and r["x_ref_cmp_in [-]"] == 1.0


def test_control_on_air_to_air_meets_the_duty_at_the_limit():
    hp = _quiet(tmhp.AirSourceHeatPump, ref="R32", hp_capacity=4000.0, T_dis_max=80.0)
    r = _quiet(hp.analyze_steady, Q_r_iu=-3000.0, T0=-7.0, T_a_room=20.0, verbose=False)
    assert _running(r) and r["discharge_control"] is True
    assert r["capacity_clamped"] is None
    assert r["T_ref_cmp_out [°C]"] <= 80.0 + DISCHARGE_TOL_K
    assert _balance(r, "Q_ref_iu [W]", "Q_ref_ou [W]") < 1e-9


# ---------------------------------------------------------------------------
# dynamic steps answer like analyze_steady: control, then a smaller duty, then off
# ---------------------------------------------------------------------------


def _one_call(T0: float, n: int = 1, dt: int = 600):
    """R32 15 kW water heater whose thermostat calls on the first step (tank at 58 degC).

    The tank passes 60 degC inside the step, where the nameplate duty condenses
    above the R32 condensing limit; these tests are about the discharge limit.
    """
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, **_R32_15KW)
    df = _quiet(hp.analyze_dynamic, n * dt, dt, 58.0, np.zeros(n), np.full(n, float(T0)))
    return hp, df


def test_dynamic_step_holds_the_discharge_limit_by_control():
    """A7: the full duty runs, held at the limit by wet suction."""
    hp, df = _one_call(7.0)
    row = df.iloc[0]
    assert bool(row["hp_is_on"]) and row["failure_reason"] == "none"
    assert bool(row["discharge_control"])
    assert row["T_ref_cmp_out [°C]"] <= hp.T_dis_max + DISCHARGE_TOL_K
    assert hp.x_suction_min <= row["x_ref_cmp_in [-]"] < 1.0
    assert pd.isna(row["capacity_binding"])  # the request itself fits


def test_dynamic_step_runs_at_the_largest_duty_inside_the_limit():
    """A-3: the request does not fit even with the control; the step runs at the
    duty analyze_steady returns for the step-start state, and its step-average
    heat is that duty times the run time."""
    hp, df = _one_call(-3.0)
    row = df.iloc[0]
    steady = _quiet(hp.analyze_steady, T_tank_w=58.0, T0=-3.0, Q_ref_tank=15000.0)
    assert steady["capacity_binding"] == BINDING_DISCHARGE and 0.0 < steady["Q_ref_tank [W]"] < 15000.0
    assert bool(row["hp_is_on"]) and row["failure_reason"] == "none"
    assert row["capacity_binding"] == BINDING_DISCHARGE
    assert row["Q_request [W]"] == 15000.0
    assert row["Q_max [W]"] == pytest.approx(steady["Q_ref_tank [W]"], rel=1e-6)
    assert row["T_ref_cmp_out [°C]"] <= hp.T_dis_max + DISCHARGE_TOL_K
    assert 0.0 < row["runtime_fraction [-]"] <= 1.0
    assert row["Q_ref_tank [W]"] == pytest.approx(row["Q_max [W]"] * row["runtime_fraction [-]"], rel=1e-6)


def test_dynamic_step_is_off_with_the_reason_when_no_duty_fits():
    """A-7: no duty fits; the called step is off, names the limit and adds no heat."""
    hp, df = _one_call(-7.0)
    row = df.iloc[0]
    assert not bool(row["hp_is_on"])
    assert row["failure_reason"] == DISCHARGE_TEMP_ABOVE_MAX
    assert row["Q_ref_tank [W]"] == 0.0 and row["E_cmp [W]"] == 0.0 and row["runtime_fraction [-]"] == 0.0
    assert row["T_tank_w [°C]"] < 58.0  # standing loss only


# ---------------------------------------------------------------------------
# a request no limit lets through runs at the largest duty that fits (any limit)
# ---------------------------------------------------------------------------


def _steady_ashpb(hp, T_tank_w, T0, Q):
    return _quiet(hp.analyze_steady, T_tank_w=T_tank_w, T0=T0, Q_ref_tank=Q)


def _reduced(r: dict, request: float, binding: str) -> None:
    assert _running(r), (r.get("failure_reason"), r.get("failure_detail"))
    assert r["capacity_clamped"] == "max" and r["capacity_binding"] == binding
    assert r["Q_request [W]"] == request and r["Q_ref_tank_request [W]"] == request
    assert 0.0 < r["Q_ref_tank [W]"] < request
    assert r["Q_max [W]"] == pytest.approx(r["Q_ref_tank [W]"])


@pytest.fixture(scope="module")
def r134a_15kw():
    # The frost-free cycle: these tests pin envelope limits at the cycle's own
    # duty (frost solves it at a larger one, tests/test_ashpb_defrost.py).
    return _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R134a", hp_capacity=15000.0, defrost=False)


def test_request_the_outdoor_coil_cannot_carry_runs_at_the_duty_it_can(r134a_15kw):
    """A-12/W58 R134a 15 kW: 8 kW ran, 15 kW used to be off (``hx_not_converged``).

    The failed result had no ``hp_is_on`` key at all (#96 A-2). The coil needs
    a larger approach than the pressure-ratio ceiling leaves it: the point
    returned sits on the ceiling, and one percent of the request above it the
    coil is short at full airflow at every approach the cycle reaches.
    """
    r = _steady_ashpb(r134a_15kw, 58.0, -12.0, 15000.0)
    _reduced(r, 15000.0, BINDING_PRESSURE_RATIO)
    assert r["Q_ref_tank [W]"] > 8000.0
    assert r["T_ref_cmp_out [°C]"] <= r134a_15kw.T_dis_max + DISCHARGE_TOL_K
    assert r["pr_cmp [-]"] == pytest.approx(r134a_15kw.PR_cycle_max, rel=1e-3)
    # The undecorated solve is what the duty reduction tries: it reports the
    # refusal in its row and does not warn (tmhp.operating_limits).
    with warnings.catch_warnings():
        warnings.simplefilter("error", tmhp.OperatingLimitWarning)
        above = tmhp.AirSourceHeatPumpBoiler.analyze_steady.__wrapped__(
            r134a_15kw, T_tank_w=58.0, T0=-12.0, Q_ref_tank=r["Q_ref_tank [W]"] + 0.0101 * 15000.0
        )
    _off(above, "hx_not_converged")
    assert above["failure_detail"] == "outdoor_coil_short_at_max_airflow"


def test_request_above_the_pressure_ratio_ceiling_runs_on_the_ceiling(r134a_15kw):
    """A-25/W40 R134a: 12 kW has no approach inside PR 11; the unit runs where the ratio is 11."""
    r = _steady_ashpb(r134a_15kw, 40.0, -25.0, 12000.0)
    _reduced(r, 12000.0, BINDING_PRESSURE_RATIO)
    assert r["pr_cmp [-]"] == pytest.approx(r134a_15kw.PR_cycle_max, rel=1e-3)
    assert r["pr_cmp [-]"] <= r134a_15kw.PR_cycle_max * (1 + 1e-9)


def test_pressure_ratio_above_the_ceiling_at_every_duty_is_decided_in_two_solves(r134a_15kw, monkeypatch):
    """A-25/W60: even a tenth of the request is above PR 11, so nothing in between runs."""
    solves = []
    search = r134a_15kw._optimize_operation

    def counted(*args, **kwargs):
        solves.append(kwargs.get("Q_ref_tank", args[1] if len(args) > 1 else None))
        return search(*args, **kwargs)

    monkeypatch.setattr(r134a_15kw, "_optimize_operation", counted)
    r = _steady_ashpb(r134a_15kw, 60.0, -25.0, 15000.0)
    _off(r, "pr_above_max")
    assert solves == [15000.0, pytest.approx(1500.0)]


def test_duties_that_run_between_two_limits_are_found():
    """A-15/W65 R1234yf 15 kW: above ~5.7 kW the pressure ratio refuses the duty, below
    ~4 kW the discharge temperature does (low speed). No rung of the ladder runs."""
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R1234yf", hp_capacity=15000.0)
    for rung in (0.8, 0.6, 0.4, 0.25, 0.1):
        assert not _running(tmhp.AirSourceHeatPumpBoiler.analyze_steady.__wrapped__(hp, 65.0, -15.0, rung * 15000.0))
    r = _steady_ashpb(hp, 65.0, -15.0, 15000.0)
    _reduced(r, 15000.0, BINDING_PRESSURE_RATIO)
    assert 0.25 * 15000.0 < r["Q_ref_tank [W]"] < 0.4 * 15000.0


def test_condensing_above_the_critical_point_is_not_reduced_onto_it():
    """W75 R32: 15 kW needs a 5 K condenser approach, 80 degC against a 78.1 degC critical
    point. Without a condensing limit 7.5 kW (77.5 degC) solves, but the largest such duty
    would sit on the critical point itself: the request stays a failure."""
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, **_R32_15KW)
    assert _running(_steady_ashpb(hp, 75.0, 15.0, 7500.0))
    _off(_steady_ashpb(hp, 75.0, 15.0, 15000.0), "cycle_invalid")


def test_coil_that_absorbs_too_much_at_minimum_airflow_is_not_reduced():
    """A smaller duty makes this worse: no trial solves."""
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", dV_fan_a_rated=10.0, dV_fan_a_min=9.0, UA_ou_rated=3.0e4)
    r = _steady_ashpb(hp, 30.0, 15.0, 3000.0)
    _off(r, "hx_not_converged")
    assert r["failure_detail"] == "outdoor_coil_excess_at_min_airflow"


@pytest.mark.parametrize(
    ("ref", "T_tank_w", "T0", "Q", "reason"),
    [
        ("R32", 60.0, -3.0, 15000.0, "discharge_temp_above_max"),
        ("R134a", 60.0, -25.0, 15000.0, "pr_above_max"),
        # No cycle at the request (80 degC, above the critical point), and a tenth
        # of it still condenses above the limit: that is the reason reported.
        ("R32", 75.0, 15.0, 15000.0, "condensing_temp_above_max"),
    ],
)
def test_every_failure_is_the_same_off_state_row(ref, T_tank_w, T0, Q, reason):
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref=ref, hp_capacity=15000.0, defrost=False)
    r = _steady_ashpb(hp, T_tank_w, T0, Q)
    _off(r, reason)
    assert r["converged"] is False
    for key in ("Q_ref_ou [W]", "E_ou_fan [W]", "E_tot [W]", "m_dot_ref [kg/s]", "cmp_rpm [rpm]"):
        assert r[key] == 0.0, key
    assert math.isnan(r["cop_ref [-]"]) and math.isnan(r["cop_sys [-]"])
    assert r["capacity_clamped"] is None and r["capacity_binding"] is None


def test_discharge_control_does_not_narrow_the_search_to_the_uncontrolled_coil_band():
    """A-6/W50 R32 15 kW runs at the request. Wet suction adds ~100 W to the evaporator
    duty, so the controlled cycle's coil band starts above the uncontrolled one; reading
    the band from the uncontrolled margins left a window the scan stepped over, and the
    request was 'reduced' to 14.7 kW (and a step earlier to 8.4 kW)."""
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", hp_capacity=15000.0)
    for T_tank_w, T0 in ((50.0, -6.0), (54.0, -3.0)):
        r = _steady_ashpb(hp, T_tank_w, T0, 15000.0)
        assert _running(r) and r["capacity_clamped"] is None
        assert r["Q_ref_tank [W]"] == pytest.approx(15000.0, rel=1e-6)
        assert r["discharge_control"] is True and hp.x_suction_min <= r["x_ref_cmp_in [-]"] < 1.0
        assert r["T_ref_cmp_out [°C]"] <= hp.T_dis_max + DISCHARGE_TOL_K


def test_dynamic_step_reduces_like_the_steady_solve_for_another_limit(r134a_15kw, monkeypatch):
    """A-12, tank at 58 degC: the step's cycle is the one analyze_steady returns for the
    tank temperature the step solves at -- the same reduced duty and the same binding."""
    solved_at: list[float] = []
    solve = r134a_15kw._solve_cycle_at

    def recorded(ctx, T_tank_w):
        solved_at.append(T_tank_w)
        return solve(ctx, T_tank_w)

    monkeypatch.setattr(r134a_15kw, "_solve_cycle_at", recorded)
    df = _quiet(r134a_15kw.analyze_dynamic, 60, 60, 58.0, np.zeros(1), np.full(1, -12.0))
    row = df.iloc[0]
    steady = _steady_ashpb(r134a_15kw, solved_at[-1], -12.0, 15000.0)
    assert bool(row["hp_is_on"]) and row["failure_reason"] == "none"
    assert row["capacity_binding"] == steady["capacity_binding"] == BINDING_PRESSURE_RATIO
    assert row["Q_request [W]"] == 15000.0
    assert row["Q_max [W]"] == pytest.approx(steady["Q_ref_tank [W]"], rel=1e-6)


def test_called_step_reads_the_failure_detail_like_the_steady_solve(monkeypatch):
    """A step whose coil absorbs too much at minimum airflow is off after one solve: the
    step's cycle carries the detail analyze_steady reports, and that detail says a smaller
    duty cannot help (without it the step would try the whole ladder)."""
    hp = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", dV_fan_a_rated=10.0, dV_fan_a_min=9.0, UA_ou_rated=3.0e4)
    hp.hp_capacity = 3000.0  # the request of a called step
    cycles: list[dict] = []
    searches: list[float] = []
    solve, search = hp._solve_cycle_at, hp._optimize_operation
    monkeypatch.setattr(hp, "_solve_cycle_at", lambda ctx, T: cycles.append(solve(ctx, T)) or cycles[-1])
    monkeypatch.setattr(hp, "_optimize_operation", lambda *a, **k: searches.append(a[1]) or search(*a, **k))
    df = _quiet(hp.analyze_dynamic, 60, 60, 30.0, np.zeros(1), np.full(1, 15.0))
    row = df.iloc[0]
    assert not bool(row["hp_is_on"]) and row["Q_ref_tank [W]"] == 0.0
    assert row["failure_reason"] == "hx_not_converged"
    assert cycles and all(c["failure_detail"] == "outdoor_coil_excess_at_min_airflow" for c in cycles)
    assert len(searches) == len(cycles)


def test_ground_source_boiler_reduces_onto_its_source_heat_exchanger():
    """The shared reduction serves the other boilers unchanged: B20/W30 R290 at 1.5 x nameplate.

    With the source heat exchanger and flow the machine used to default to at
    every capacity (the capacity-scaled defaults carry 1.5 x nameplate here).
    """
    hp = _quiet(tmhp.GroundSourceHeatPumpBoiler, ref="R290", **_GSHPB_UNDERSIZED_HX)
    r = _quiet(hp.analyze_steady, T_tank_w=30.0, T_source=20.0, Q_ref_tank=1.5 * hp.hp_capacity)
    assert _running(r) and r["capacity_clamped"] == "max"
    assert r["capacity_binding"] == BINDING_HEAT_EXCHANGER
    assert hp.hp_capacity < r["Q_ref_tank [W]"] < 1.5 * hp.hp_capacity
