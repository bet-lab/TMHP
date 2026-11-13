"""Compressor operating envelope: the condensing-pressure limit (``T_cond_max``).

See :mod:`tmhp.compressor_envelope`. Before the limit the boilers reported
normal operation at condensing pressures no compressor is built for: R410A at
70 degC (4.76 MPa, 97 % of the critical pressure) charging a tank to 65 degC,
R32 at 77.9 degC, a top-coil stratified tank with R134a at 96 degC (#96, fourth
round). The machines below are 8 kW R410A water heaters whose tank heat
exchanger carries the nameplate duty with a 5 K approach (1600 W/K), so the
duty that condenses on the 65 degC limit is ``1600 W/K * (64.93 degC - T_tank)``.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

import tmhp
from tmhp.compressor_envelope import (
    BINDING_CONDENSING,
    CONDENSING_PRESSURE_MAX_REDUCED,
    CONDENSING_TEMP_ABOVE_MAX,
    CONDENSING_TOL_K,
    condensing_pressure_max,
    condensing_tank_cutout,
    condensing_temp_exceeded,
    default_condensing_temp_max,
    envelope_excess,
    envelope_violation,
    resolve_condensing_temp_max,
)
from tmhp.reference_state import ReferenceStateError
from tmhp.refrigerant import props_si

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

BOILERS = {
    "ASHPB": (tmhp.AirSourceHeatPumpBoiler, dict(T0=7.0)),
    "GSHPB": (tmhp.GroundSourceHeatPumpBoiler, dict(T_source=10.0, T0=10.0)),
    "WSHPB": (tmhp.WaterSourceHeatPumpBoiler, dict(T_source=10.0, T0=10.0)),
}
CAPACITY = 8000.0
#: Bubble point of R410A at the limit pressure (dew point 65 degC): the hottest the cycle condenses.
T_LIMIT_R410A = props_si("T", "P", condensing_pressure_max("R410A", 65.0), "Q", 0, "R410A") - 273.15


def _quiet(function, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return function(*args, **kwargs)


def _running(r: dict) -> bool:
    return bool(r.get("hp_is_on")) and r.get("converged") is True and r.get("failure_reason") == "none"


def _inside(model, r: dict) -> bool:
    """The state's condensing pressure is within the model's limit (with its 0.01 K width)."""
    return not condensing_temp_exceeded(r["P_ref_cmp_out [Pa]"], model.T_cond_max, model.ref)


@pytest.fixture(scope="module")
def boilers() -> dict:
    """One R410A 8 kW machine per boiler family, with the default limit and without it."""
    out = {}
    for family, (cls, _) in BOILERS.items():
        extra = dict(t_max_s=86400.0) if family == "GSHPB" else {}
        out[family] = _quiet(cls, ref="R410A", hp_capacity=CAPACITY, **extra)
        out[family, "off"] = _quiet(cls, ref="R410A", hp_capacity=CAPACITY, T_cond_max=None, **extra)
    return out


def _steady(model, family: str, T_tank_w: float, Q: float = CAPACITY) -> dict:
    return _quiet(model.analyze_steady, T_tank_w=T_tank_w, Q_ref_tank=Q, **BOILERS[family][1])


# ---------------------------------------------------------------------------
# the limit and its resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "limit"),
    [
        ("R410A", 65.0),
        ("HEOS::R410A", 65.0),
        ("R32", 65.0),
        ("R407C", 65.0),
        ("R290", 75.0),
        ("R1234yf", 75.0),
        ("R134a", 80.0),
        ("R717", 70.0),
    ],
)
def test_default_condensing_limit_by_refrigerant(ref, limit):
    assert default_condensing_temp_max(ref) == limit
    assert resolve_condensing_temp_max("auto", ref) == limit


@pytest.mark.parametrize("ref", ["R744", "R22"])
def test_refrigerant_without_a_table_value_gets_the_near_critical_bound(ref):
    """0.9 of the critical pressure: 26.4 degC for R744 (its heat pumps are transcritical), 90.6 degC for R22."""
    limit = default_condensing_temp_max(ref)
    from CoolProp.CoolProp import PropsSI

    assert condensing_pressure_max(ref, limit) == pytest.approx(
        CONDENSING_PRESSURE_MAX_REDUCED * PropsSI("Pcrit", ref), rel=1e-9
    )
    assert limit == pytest.approx({"R744": 26.37, "R22": 90.58}[ref], abs=0.02)


def test_no_default_exceeds_the_near_critical_bound():
    from CoolProp.CoolProp import PropsSI

    from tmhp.compressor_envelope import CONDENSING_TEMP_MAX_C

    for ref, limit in CONDENSING_TEMP_MAX_C.items():
        assert default_condensing_temp_max(ref) == limit, ref
        assert condensing_pressure_max(ref, limit) < CONDENSING_PRESSURE_MAX_REDUCED * PropsSI("Pcrit", ref), ref


def test_condensing_limit_resolution():
    assert resolve_condensing_temp_max(None, "R32") is None
    assert resolve_condensing_temp_max(58, "R32") == 58.0
    with pytest.raises(ValueError, match="T_cond_max"):
        resolve_condensing_temp_max("off", "R32")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        resolve_condensing_temp_max(math.nan, "R32")
    # A limit at or above the critical temperature (78.1 degC) names no pressure.
    with pytest.raises(ValueError, match="critical temperature of R32"):
        resolve_condensing_temp_max(80.0, "R32")


def test_condensing_limit_is_a_pressure_with_a_width():
    def p_sat(T_C: float, quality: int = 1, ref: str = "R410A") -> float:
        return props_si("P", "T", T_C + 273.15, "Q", quality, ref)

    assert not condensing_temp_exceeded(p_sat(65.0), 65.0, "R410A")
    assert not condensing_temp_exceeded(p_sat(65.0 + 0.5 * CONDENSING_TOL_K), 65.0, "R410A")
    assert condensing_temp_exceeded(p_sat(65.0 + 2.0 * CONDENSING_TOL_K), 65.0, "R410A")
    assert not condensing_temp_exceeded(p_sat(70.0), None, "R410A")  # check off
    assert not condensing_temp_exceeded(math.nan, 65.0, "R410A")  # no state: judged elsewhere
    # A zeotropic blend: the envelope temperature is the dew point of the limit
    # pressure, and the cycle's own condensing temperature (bubble point) reaches
    # that pressure 3.8 K lower -- R407C at 61.2 degC for the 65 degC limit.
    assert not condensing_temp_exceeded(p_sat(61.0, 0, "R407C"), 65.0, "R407C")
    assert condensing_temp_exceeded(p_sat(61.5, 0, "R407C"), 65.0, "R407C")


@pytest.mark.parametrize(
    "cls",
    [
        tmhp.AirSourceHeatPump,
        tmhp.GroundSourceHeatPump,
        tmhp.AirSourceHeatPumpBoiler,
        tmhp.GroundSourceHeatPumpBoiler,
        tmhp.WaterSourceHeatPumpBoiler,
    ],
)
def test_every_family_takes_the_argument(cls):
    assert _quiet(cls, ref="R32").T_cond_max == 65.0
    assert _quiet(cls, ref="R32", T_cond_max=None).T_cond_max is None
    assert _quiet(cls, ref="R32", T_cond_max=68).T_cond_max == 68.0


def test_envelope_violation_and_excess_read_the_condensing_pressure(boilers):
    model = boilers["WSHPB"]
    state = {"hp_is_on": True, "T_ref_cmp_out [°C]": 90.0}
    inside = {**state, "P_ref_cmp_out [Pa]": props_si("P", "T", 60.0 + 273.15, "Q", 1, "R410A")}
    above = {**state, "P_ref_cmp_out [Pa]": props_si("P", "T", 67.0 + 273.15, "Q", 1, "R410A")}
    assert envelope_violation(inside, model) is None and envelope_excess(inside, model) == 0.0
    assert envelope_violation(above, model) == CONDENSING_TEMP_ABOVE_MAX
    assert envelope_excess(above, model) == pytest.approx(2000.0, rel=1e-6)  # 1000 per kelvin
    assert envelope_violation(above, boilers["WSHPB", "off"]) is None


# ---------------------------------------------------------------------------
# boilers, steady: reproduction and the reduced duty
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", list(BOILERS))
def test_tank_at_65C_used_to_run_at_70C_condensing(boilers, family):
    """The defect: R410A at 4.76 MPa (97 % of the critical pressure) reported as normal operation."""
    unlimited = _steady(boilers[family, "off"], family, 65.0)
    assert _running(unlimited) and unlimited["capacity_clamped"] is None
    assert unlimited["T_ref_cond_sat_l [°C]"] == pytest.approx(70.0, abs=1e-6)
    assert unlimited["P_ref_cmp_out [Pa]"] > 0.97 * 4.901e6
    # With the limit no duty runs against a tank at the limit: off, with the reason.
    r = _steady(boilers[family], family, 65.0)
    assert r["failure_reason"] == CONDENSING_TEMP_ABOVE_MAX
    assert not r["hp_is_on"] and r["converged"] is False
    assert r["Q_ref_tank [W]"] == 0.0 and r["E_cmp [W]"] == 0.0 and math.isnan(r["cop_sys [-]"])
    assert "capacity_binding" not in r


@pytest.mark.parametrize("family", list(BOILERS))
def test_request_inside_the_limit_is_untouched(boilers, family):
    r, free = _steady(boilers[family], family, 55.0), _steady(boilers[family, "off"], family, 55.0)
    assert _running(r) and r["capacity_clamped"] is None and "capacity_binding" not in r
    assert r["T_ref_cond_sat_l [°C]"] == pytest.approx(60.0, abs=1e-6)
    for key in ("Q_ref_tank [W]", "E_tot [W]", "cop_sys [-]", "P_ref_cmp_out [Pa]"):
        assert r[key] == pytest.approx(free[key], rel=1e-12), key


@pytest.mark.parametrize("family", list(BOILERS))
def test_request_above_the_limit_runs_at_the_duty_on_it(boilers, family):
    """Tank at 62 degC: 8 kW would condense at 67 degC; the unit runs where it condenses at the limit."""
    model = boilers[family]
    r = _steady(model, family, 62.0)
    assert _running(r)
    assert r["capacity_clamped"] == "max" and r["capacity_binding"] == BINDING_CONDENSING
    assert r["Q_request [W]"] == CAPACITY
    assert r["Q_max [W]"] == pytest.approx(r["Q_ref_tank [W]"])
    assert _inside(model, r)
    # On the limit, not merely below it: the duty of a 1600 W/K approach to the limit.
    assert r["T_ref_cond_sat_l [°C]"] == pytest.approx(T_LIMIT_R410A, abs=0.06)
    assert r["Q_ref_tank [W]"] == pytest.approx(model.UA_tank_hx * (T_LIMIT_R410A - 62.0), rel=0.02)
    # A slightly larger duty is outside the limit.
    above = _steady(model, family, 62.0, r["Q_ref_tank [W]"] + 0.02 * CAPACITY)
    assert above["capacity_binding"] == BINDING_CONDENSING
    assert above["Q_ref_tank [W]"] == pytest.approx(r["Q_ref_tank [W]"], rel=1e-3)


def _count_solves(model, monkeypatch) -> list[float]:
    solves: list[float] = []
    search = model._optimize_operation

    def counted(*args, **kwargs):
        duty = kwargs.get("Q_tank_load", kwargs.get("Q_ref_tank", args[1] if len(args) > 1 else None))
        solves.append(float(duty))
        return search(*args, **kwargs)

    monkeypatch.setattr(model, "_optimize_operation", counted)
    return solves


def test_duty_on_the_limit_costs_three_solves_and_no_duty_two(boilers, monkeypatch):
    """The request, a tenth of it, and the duty on the limit -- not the ladder and bisection."""
    model = boilers["WSHPB"]
    solves = _count_solves(model, monkeypatch)
    r = _steady(model, "WSHPB", 62.0)
    assert r["capacity_binding"] == BINDING_CONDENSING
    assert solves[:2] == [CAPACITY, pytest.approx(0.1 * CAPACITY)]
    assert len(solves) == 3 and solves[2] == pytest.approx(r["Q_ref_tank [W]"], rel=1e-6)
    solves.clear()
    # Tank at 64 degC: the minimum-speed duty already condenses above the limit.
    assert _steady(model, "WSHPB", 64.0)["failure_reason"] == CONDENSING_TEMP_ABOVE_MAX
    assert solves == [CAPACITY, pytest.approx(0.1 * CAPACITY)]


def test_request_without_any_cycle_is_reduced_onto_the_limit(boilers):
    """Twice the nameplate against a 62 degC tank asks for 72 degC, above the R410A critical
    point (71.3 degC): no cycle at all. That used to be ``cycle_invalid``; it is beyond the
    condensing limit, and the unit runs on the limit as for any other request above it."""
    model, free = boilers["GSHPB"], boilers["GSHPB", "off"]
    assert _steady(free, "GSHPB", 62.0, 2 * CAPACITY)["failure_reason"] == "cycle_invalid"
    r = _steady(model, "GSHPB", 62.0, 2 * CAPACITY)
    on_limit = _steady(model, "GSHPB", 62.0)["Q_ref_tank [W]"]
    assert _running(r) and r["capacity_binding"] == BINDING_CONDENSING and _inside(model, r)
    assert r["Q_request [W]"] == 2 * CAPACITY
    # The ladder's bisection closes to 1 % of the request below the boundary.
    assert on_limit - 0.011 * 2 * CAPACITY <= r["Q_ref_tank [W]"] <= on_limit * (1 + 1e-6)
    # Against a tank at the limit nothing runs, and the reason is the limit.
    assert _steady(model, "GSHPB", 68.0, 2 * CAPACITY)["failure_reason"] == CONDENSING_TEMP_ABOVE_MAX


def test_rating_point_above_the_limit_is_refused_at_construction():
    """A 500 W/K tank coil on 8 kW rates R410A at 55 + 16 = 71 degC condensing."""
    with pytest.raises(ReferenceStateError, match=r"condenses at 71\.0 °C.*above T_cond_max=65 °C of R410A"):
        _quiet(tmhp.WaterSourceHeatPumpBoiler, ref="R410A", UA_tank_hx=500.0)
    hp = _quiet(tmhp.WaterSourceHeatPumpBoiler, ref="R410A", UA_tank_hx=500.0, T_cond_max=None)
    assert hp.reference_state.T_cond_sat_C == pytest.approx(71.0, abs=1e-6)


# ---------------------------------------------------------------------------
# boilers, dynamic: the thermostat stops at the envelope's cut-out
# ---------------------------------------------------------------------------

#: Highest tank temperature the 8 kW R410A machines heat at the nameplate duty (5 K approach).
T_CUTOUT_R410A = T_LIMIT_R410A - 5.0


def test_set_point_above_the_envelope_is_capped_with_a_warning():
    """65 degC is out of reach of R410A: the band the thermostat works with is capped, and says so."""
    with pytest.warns(UserWarning, match=r"thermostat stops the heat pump at 59\.9 °C\. Lower"):
        hp = tmhp.WaterSourceHeatPumpBoiler(ref="R410A", hp_capacity=CAPACITY)
    assert hp.T_tank_w_setpoints_requested == (55.0, 65.0)
    assert hp.T_tank_w_lower_bound == 55.0
    assert hp.T_tank_w_upper_bound == pytest.approx(T_CUTOUT_R410A, abs=1e-9)
    assert condensing_tank_cutout(hp) == pytest.approx(T_CUTOUT_R410A, abs=1e-9)
    # A lower bound that is not below the cut-out moves down with it (ASHPB: 60-65 degC).
    with pytest.warns(UserWarning, match=r"lower bound is moved from 60 to 54\.9 °C"):
        ashpb = tmhp.AirSourceHeatPumpBoiler(ref="R410A", hp_capacity=CAPACITY)
    assert ashpb.T_tank_w_upper_bound == pytest.approx(T_CUTOUT_R410A, abs=1e-9)
    assert ashpb.T_tank_w_upper_bound - ashpb.T_tank_w_lower_bound == pytest.approx(5.0)
    # Inside the envelope, or without the limit, the set points are the caller's and nothing warns.
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        for kwargs in (
            dict(ref="R410A", T_tank_w_upper_bound=59.0),
            dict(ref="R410A", T_cond_max=None),
            dict(ref="R290"),
            dict(ref="R134a"),
        ):
            kept = tmhp.WaterSourceHeatPumpBoiler(hp_capacity=CAPACITY, **kwargs)
            assert kept.T_tank_w_upper_bound == kwargs.get("T_tank_w_upper_bound", 65.0)
            assert kept.T_tank_w_lower_bound == 55.0


@pytest.mark.parametrize("family", ["ASHPB", "WSHPB"])
def test_dynamic_run_stops_at_the_cut_out_whatever_the_step(family):
    """R410A against a 65 degC set point, 1 h from 50 degC: 60 s and 600 s steps agree.

    Without the cap a 600 s step carried the cycle of its start to the set point
    (called at 54.9 degC: 1.19 kWh and a 65.0 degC tank, against 0.84 kWh and
    62.1 degC with 60 s steps).
    """
    cls = BOILERS[family][0]
    out = {}
    for dt in (60.0, 600.0):
        n = int(3600 / dt)
        model = _quiet(cls, ref="R410A", hp_capacity=CAPACITY)
        df = _quiet(model.analyze_dynamic, n * dt, dt, 50.0, np.zeros(n), np.full(n, 10.0))
        ran = df["hp_is_on"].astype(bool)
        assert ran.any() and (df["failure_reason"] == "none").all()
        assert not any(condensing_temp_exceeded(p, 65.0, "R410A") for p in df.loc[ran, "P_ref_cmp_out [Pa]"])
        assert "capacity_binding" not in df.columns or df["capacity_binding"].isna().all()  # nameplate duty
        assert df["T_tank_w [°C]"].max() == pytest.approx(T_CUTOUT_R410A, abs=0.05)
        out[dt] = float(df["Q_ref_tank [W]"].sum() * dt)
    assert out[600.0] == pytest.approx(out[60.0], rel=0.01)


def test_dynamic_steps_past_the_cut_out_reduce_the_duty_then_stop():
    """The envelope's own guard, with the thermostat's cap lifted (as a tank model or a minimum
    run time takes a step past the cut-out): R410A, called from just below 55 degC (12 x 60 s)."""
    dt, n = 60.0, 12
    model = _quiet(tmhp.WaterSourceHeatPumpBoiler, ref="R410A", hp_capacity=CAPACITY)
    model.T_tank_w_upper_bound = 65.0
    df = _quiet(model.analyze_dynamic, n * dt, dt, 54.9, np.zeros(n), np.full(n, 10.0))
    ran = df["hp_is_on"].astype(bool)
    assert ran.any() and (~ran).any()
    # Every step that ran is inside the limit ...
    assert not any(condensing_temp_exceeded(p, model.T_cond_max, model.ref) for p in df.loc[ran, "P_ref_cmp_out [Pa]"])
    # ... at the nameplate duty while the tank is cold, then at the duty on the limit, which the row names.
    assert df.loc[ran, "Q_ref_tank [W]"].iloc[0] == pytest.approx(CAPACITY, rel=1e-6)
    reduced = ran & (df["capacity_binding"] == BINDING_CONDENSING)
    assert reduced.any()
    assert (df.loc[reduced, "Q_request [W]"] == CAPACITY).all()
    assert (df.loc[reduced, "Q_max [W]"] < CAPACITY).all()
    assert (df.loc[reduced, "T_tank_w [°C]"] > 60.0).all()
    # The steps it could not run are off rows with the reason: the tank is below the set point.
    stopped = df["failure_reason"] != "none"
    assert stopped.any() and (df.loc[stopped, "failure_reason"] == CONDENSING_TEMP_ABOVE_MAX).all()
    assert not df.loc[stopped, "hp_is_on"].any()
    assert (df.loc[stopped, "Q_ref_tank [W]"] == 0.0).all() and (df.loc[stopped, "E_cmp [W]"] == 0.0).all()
    assert (df.loc[stopped, "runtime_fraction [-]"] == 0.0).all()
    assert df["T_tank_w [°C]"].max() < T_LIMIT_R410A < model.T_tank_w_upper_bound


# ---------------------------------------------------------------------------
# space conditioning: candidates above the limit are rejected inside the search
# ---------------------------------------------------------------------------


def test_air_to_air_ceiling_names_the_condensing_limit():
    hp = _quiet(tmhp.AirSourceHeatPump, ref="R32", hp_capacity=4000.0, defrost=None)
    free = _quiet(hp.analyze_steady, Q_r_iu=-3000.0, T0=7.0, T_a_room=20.0, verbose=False)
    assert _running(free) and free["capacity_clamped"] is None
    hp.T_cond_max = 30.0  # below the rating point (43.9 degC), so set on the built machine
    assert free["T_ref_cond_sat_l [°C]"] > hp.T_cond_max
    r = _quiet(hp.analyze_steady, Q_r_iu=-3000.0, T0=7.0, T_a_room=20.0, verbose=False)
    assert _running(r) and _inside(hp, r)
    assert r["capacity_clamped"] == "max" and r["capacity_binding"] == BINDING_CONDENSING
    assert 0.0 < abs(r["Q_ref_iu [W]"]) < 3000.0
    # No duty heats a 20 degC room from a 25 degC condenser through this coil: off, with the reason.
    hp.T_cond_max = 25.0
    off = _quiet(hp.analyze_steady, Q_r_iu=-3000.0, T0=7.0, T_a_room=20.0, verbose=False)
    assert off["failure_reason"] == CONDENSING_TEMP_ABOVE_MAX and off["E_cmp [W]"] == 0.0


def test_gshp_reports_the_condensing_limit():
    hp = _quiet(tmhp.GroundSourceHeatPump, ref="R32")
    case = dict(Q_r_iu=3000.0, T0=30.0, T_a_room=26.0, T_bhe_wall=25.0)
    assert _quiet(hp.analyze_steady, **case)["capacity_clamped"] is None
    hp.T_cond_max = 35.0
    r = _quiet(hp.analyze_steady, **case)
    assert _running(r) and _inside(hp, r)
    assert r["capacity_clamped"] == "max" and r["capacity_binding"] == BINDING_CONDENSING
    hp.T_cond_max = 25.0  # at the borehole wall: nothing condenses against it
    off = _quiet(hp.analyze_steady, **case)
    assert off["failure_reason"] == CONDENSING_TEMP_ABOVE_MAX and off["E_cmp [W]"] == 0.0


def test_rating_point_above_the_limit_names_the_standard_for_air_to_air():
    with pytest.raises(ReferenceStateError, match=r"ISO 5151.*above T_cond_max=40 °C of R32"):
        _quiet(tmhp.AirSourceHeatPump, ref="R32", T_cond_max=40.0)
