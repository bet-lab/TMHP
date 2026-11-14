"""Boiler results have the same keys whatever the row or the run did (#96, round 5).

Before: the capacity keys (``capacity_binding``, ``Q_max [W]``, ``Q_request [W]``)
appeared only on a row whose duty was reduced, ``capacity_clamped`` was dropped
from the dynamic frame, the efficiencies only on running rows; a dynamic frame
is the union of its rows, so its columns depended on what the run happened to
do (ASHPB 108-120 columns for the same machine). The boiler frames had no time
column, ASHPB reported a flow that does not run as NaN (GSHPB and WSHPB: 0),
and ``T_ref_cond_sat_v [°C]`` of a zeotropic blend repeated the bubble point.
"""

from __future__ import annotations

import contextlib
import io
import math
import warnings

import CoolProp.CoolProp as CP
import numpy as np
import pandas as pd
import pytest

import tmhp

FAMILIES = {
    "ASHPB": (tmhp.AirSourceHeatPumpBoiler, {"T0": 7.0}),
    "GSHPB": (tmhp.GroundSourceHeatPumpBoiler, {"T_source": 10.0}),
    "WSHPB": (tmhp.WaterSourceHeatPumpBoiler, {"T_source": 10.0}),
}
CAPACITY_KEYS = ("capacity_clamped", "capacity_binding", "Q_max [W]", "Q_request [W]")
FLOW_KEYS = ("dV_mix_w_out [m3/s]", "dV_tank_w_out [m3/s]", "dV_tank_w_in [m3/s]", "dV_mix_sup_w_in [m3/s]")


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


@pytest.fixture(scope="module")
def models():
    return {family: _quiet(cls) for family, (cls, _) in FAMILIES.items()}


def _steady(model, family, T_tank_w, Q, **source):
    kwargs = dict(FAMILIES[family][1])
    kwargs.update(source)
    return _quiet(model.analyze_steady, T_tank_w=T_tank_w, Q_ref_tank=Q, **kwargs)


def _steady_cases(model, family):
    cap = model.hp_capacity
    cold = {"T0": -25.0} if family == "ASHPB" else {"T_source": -8.0}
    return {
        "off": _steady(model, family, 45.0, 0.0),
        "running": _steady(model, family, 45.0, 0.6 * cap),
        "speed floor": _steady(model, family, 45.0, 0.05 * cap),
        "speed ceiling": _steady(model, family, 45.0, 3.0 * cap),
        "tank at the condensing limit": _steady(model, family, 80.0, 0.6 * cap),
        "cold source": _steady(model, family, 55.0, cap, **cold),
    }


@pytest.mark.parametrize("family", list(FAMILIES))
def test_every_steady_row_has_the_same_keys(models, family):
    rows = _steady_cases(models[family], family)
    assert rows["off"]["failure_reason"] == "none" and rows["running"]["hp_is_on"]
    assert rows["speed floor"]["capacity_clamped"] == "min" and rows["speed ceiling"]["capacity_clamped"] == "max"
    assert not rows["tank at the condensing limit"]["hp_is_on"] and not rows["cold source"]["hp_is_on"]
    keys = {name: set(row) for name, row in rows.items()}
    reference = keys["running"]
    for name, ks in keys.items():
        assert ks == reference, (name, sorted(ks ^ reference))
    assert set(CAPACITY_KEYS) <= reference


@pytest.mark.parametrize("family", list(FAMILIES))
def test_steady_capacity_keys_on_every_row(models, family):
    model = models[family]
    rows = _steady_cases(model, family)
    for name, row in rows.items():
        # The request as asked, on every row (boilers: positive).
        q = {"off": 0.0, "running": 0.6, "speed floor": 0.05, "speed ceiling": 3.0}.get(name)
        if q is not None:
            assert row["Q_request [W]"] == pytest.approx(q * model.hp_capacity), name
    for name in ("off", "running", "speed floor", "tank at the condensing limit"):
        assert rows[name]["capacity_binding"] is None and math.isnan(rows[name]["Q_max [W]"]), name
    ceiling = rows["speed ceiling"]
    # At the speed ceiling the delivered heat is the ceiling, as in ASHP.
    assert ceiling["capacity_binding"] == "compressor"
    assert ceiling["Q_max [W]"] == pytest.approx(ceiling["Q_ref_tank [W]"], rel=1e-12)
    assert ceiling["Q_max [W]"] < ceiling["Q_request [W]"]
    off = rows["off"]
    # Off is 0, undefined is NaN.
    assert math.isnan(off["eta_cmp_isen [-]"]) and math.isnan(off["pr_cmp [-]"]) and off["failure_detail"] is None


def _dynamic(model, init_C, n, dt, draw=0.0, T0=7.0):
    return _quiet(model.analyze_dynamic, n * dt, dt, init_C, np.full(n, draw), np.full(n, T0))


@pytest.fixture(scope="module")
def dynamic_runs(models):
    runs = {}
    for family, (cls, _) in FAMILIES.items():
        model = models[family]
        # R410A against a 65 degC set point from just below 55 degC: the nameplate
        # duty, then the in-step stop at the envelope cut-out (test_condensing_limit).
        limited = _quiet(cls, ref="R410A", hp_capacity=8000.0)
        limited.T_tank_w_upper_bound = 65.0
        # A compressor whose speed ceiling is below the rated speed: steps at the ceiling.
        slow = _quiet(cls)
        slow.rps_max = 0.7 * slow.rps_rated
        runs[family] = {
            "charging": _dynamic(model, 40.0, 3, 600.0, draw=1e-4),
            "idle, no call": _dynamic(model, 62.0, 3, 600.0),
            "reduced": _dynamic(limited, 54.9, 8, 60.0),
            "speed ceiling": _dynamic(slow, 50.0, 2, 600.0),
        }
    return runs


@pytest.mark.parametrize("family", list(FAMILIES))
def test_dynamic_frames_have_the_same_columns_whatever_the_run(dynamic_runs, family):
    runs = dynamic_runs[family]
    assert runs["charging"]["hp_is_on"].all()
    assert not runs["idle, no call"]["hp_is_on"].any()
    assert (runs["reduced"]["hp_stop_reason"] == "condensing_limit").any()
    assert (runs["reduced"]["failure_reason"] == "none").all()
    assert (runs["speed ceiling"]["capacity_binding"] == "compressor").any()
    reference = set(runs["charging"].columns)
    for name, df in runs.items():
        assert set(df.columns) == reference, (name, sorted(set(df.columns) ^ reference))
    assert set(CAPACITY_KEYS) <= reference


@pytest.mark.parametrize("family", list(FAMILIES))
def test_dynamic_capacity_keys(dynamic_runs, family):
    runs = dynamic_runs[family]
    charging, idle, reduced = runs["charging"], runs["idle, no call"], runs["reduced"]
    cap = {"ASHPB": 15000.0, "GSHPB": 8000.0, "WSHPB": 8000.0}[family]
    # A step the thermostat called asked for the nameplate duty; one it did not, for nothing.
    assert (charging["Q_request [W]"] == cap).all()
    assert (idle["Q_request [W]"] == 0.0).all()
    assert idle["capacity_clamped"].isna().all() and idle["capacity_binding"].isna().all()
    assert idle["Q_max [W]"].isna().all()
    # The envelope stops the R410A run inside the step, so the duty is reduced at the speed ceiling.
    reduced, request = runs["speed ceiling"], cap
    cut = reduced["capacity_binding"] == "compressor"
    assert cut.any()
    assert (reduced.loc[cut, "capacity_clamped"] == "max").all()
    assert (reduced.loc[cut, "Q_request [W]"] == request).all()
    assert (reduced.loc[cut, "Q_max [W]"] < request).all()
    # Q_max is the running machine's ceiling, Q_ref_tank the step average.
    ran = reduced.loc[cut]
    assert (ran["runtime_fraction [-]"] < 1.0).any()  # a step-average row
    assert np.allclose(ran["Q_max [W]"] * ran["runtime_fraction [-]"], ran["Q_ref_tank [W]"], rtol=1e-9)


@pytest.mark.parametrize("family", list(FAMILIES))
def test_dynamic_frame_has_the_step_start_time(dynamic_runs, family):
    df = dynamic_runs[family]["charging"]
    assert df["time [s]"].tolist() == [0.0, 600.0, 1200.0]
    assert np.allclose(df["time [h]"], df["time [s]"] / 3600.0)
    reduced = dynamic_runs[family]["reduced"]
    assert np.allclose(reduced["time [s]"], np.arange(8) * 60.0)


def test_step_driven_frame_has_the_time_too():
    model = _quiet(tmhp.AirSourceHeatPumpBoiler)
    state = model.make_initial_state(50.0)
    rows = []
    for n in range(2):
        inputs = {"n": n, "current_time_s": 600.0 * n, "T0": 7.0, "dV_mix_w_out": 0.0, "T_sup_w": 15.0, "T_sur": 20.0}
        state, row = _quiet(model.step, state, inputs, 600.0)
        rows.append(row)
    assert [r["time [s]"] for r in rows] == [0.0, 600.0]
    assert [r["time [h]"] for r in rows] == [0.0, 600.0 / 3600.0]


@pytest.mark.parametrize("family", list(FAMILIES))
def test_a_flow_that_does_not_run_is_zero(models, dynamic_runs, family):
    df = dynamic_runs[family]["idle, no call"]
    for key in FLOW_KEYS:
        assert (df[key] == 0.0).all(), key
    # Water that does not flow carries no heat and no exergy (0) and has no
    # temperature (NaN), in the three boilers alike. Before, ASHPB reported the
    # heat and exergy of the mixed water as NaN (0 * NaN), and GSHPB and WSHPB
    # the valve's set point as its temperature.
    assert df["T_mix_w_out [°C]"].isna().all()
    for key in ("X_tank_w_out [W]", "X_tank_w_in [W]", "X_mix_sup_w_in [W]", "X_mix_w_out [W]", "Xc_mix [W]"):
        assert (df[key] == 0.0).all(), key
    heat_keys = ["Q_tank_w_out [W]", "Q_dhw_request [W]", "Q_dhw_unmet [W]"]
    if family == "ASHPB":
        heat_keys += ["Q_mix_w_out [W]", "Q_mix_sup_w_in [W]", "Q_tank_w_in [W]"]
    for key in heat_keys:
        assert (df[key] == 0.0).all(), key
    steady = _steady(models[family], family, 45.0, 0.6 * models[family].hp_capacity)
    for key in FLOW_KEYS:
        assert steady[key] == 0.0, key
    if family == "ASHPB":
        assert steady["Q_mix_w_out [W]"] == 0.0 and math.isnan(steady["T_mix_w_out [°C]"])


@pytest.fixture(scope="module")
def draw_runs(models):
    """Six 10 min steps from 58 degC, a draw in the third and fourth only."""
    draw = np.zeros(6)
    draw[2:4] = 1e-4
    return draw, {
        family: _quiet(models[family].analyze_dynamic, 6 * 600.0, 600.0, 58.0, draw, np.full(6, 7.0))
        for family in FAMILIES
    }


@pytest.mark.parametrize("family", list(FAMILIES))
def test_mixed_water_of_a_step_without_a_draw(models, draw_runs, family):
    """Flow, heat and exergy 0, temperature NaN; the steps with a draw are untouched."""
    draw, runs = draw_runs
    df, drawing = runs[family], draw > 0.0
    rc = tmhp.constants.c_w * tmhp.constants.rho_w
    T_mix = df["T_mix_w_out [°C]"].to_numpy(float)
    assert np.isnan(T_mix[~drawing]).all()
    assert T_mix[drawing] == pytest.approx(models[family].T_mix_w_out, abs=0.02)
    for key in ("dV_mix_w_out [m3/s]", "X_mix_w_out [W]", "Xc_mix [W]"):
        values = df[key].to_numpy(float)
        assert (values[~drawing] == 0.0).all() and np.isfinite(values).all(), key
    assert (df["X_mix_w_out [W]"].to_numpy(float)[drawing] > 0.0).all()
    if family == "ASHPB":
        heat = df["Q_mix_w_out [W]"].to_numpy(float)
        assert (heat[~drawing] == 0.0).all()
        expected = rc * draw[drawing] * (T_mix[drawing] - df["T0 [°C]"].to_numpy(float)[drawing])
        assert heat[drawing] == pytest.approx(expected, rel=1e-12)
    # No water column is NaN on any step: the frame sums without skipping anything.
    water = [c for c in df.columns if c.startswith(("Q_tank_w", "Q_mix", "Q_dhw", "X_tank_w", "X_mix", "Xc_mix"))]
    assert not df[water].isna().any().any(), df[water].isna().any()
    # The exergy step reads the frame again without a temperature for the idle steps.
    again = models[family].postprocess_exergy(df.copy())
    assert again["X_mix_w_out [W]"].to_numpy(float) == pytest.approx(df["X_mix_w_out [W]"].to_numpy(float), rel=1e-12)


def test_mixed_water_helpers():
    from tmhp._boiler_results import mixed_water_enthalpy_flow, mixed_water_exergy

    rc = tmhp.constants.c_w * tmhp.constants.rho_w
    assert mixed_water_enthalpy_flow(0.0, math.nan, 283.15) == 0.0
    assert mixed_water_enthalpy_flow(1e-4, 313.15, 283.15) == pytest.approx(rc * 1e-4 * 30.0, rel=1e-12)
    df = pd.DataFrame({"dV_mix_w_out [m3/s]": [0.0, 1e-4, np.nan], "T_mix_w_out [°C]": [np.nan, 40.0, np.nan]})
    T0_K = pd.Series([283.15, 283.15, 283.15])
    X = mixed_water_exergy(df, T0_K).to_numpy(float)
    assert X[0] == 0.0 and X[2] == 0.0
    assert X[1] == pytest.approx(rc * 1e-4 * (30.0 - 283.15 * math.log(313.15 / 283.15)), rel=1e-12)


def test_ashpb_draw_rows_are_unchanged_by_the_zero_flow_convention(dynamic_runs):
    df = dynamic_runs["ASHPB"]["charging"]
    assert np.allclose(df["dV_mix_w_out [m3/s]"], 1e-4, rtol=1e-12)
    assert (df["dV_tank_w_out [m3/s]"] > 0.0).all()


@pytest.mark.parametrize("family", list(FAMILIES))
def test_zeotrope_reports_the_condensing_dew_point(family):
    model = _quiet(FAMILIES[family][0], ref="R407C")
    row = _steady(model, family, 45.0, 0.6 * model.hp_capacity)
    P = row["P_ref_cond_sat_v [Pa]"]
    dew = CP.PropsSI("T", "P", P, "Q", 1, "R407C") - 273.15
    bubble = CP.PropsSI("T", "P", P, "Q", 0, "R407C") - 273.15
    assert row["T_ref_cond_sat_v [°C]"] == pytest.approx(dew, abs=1e-6)
    assert row["T_ref_cond_sat_l [°C]"] == pytest.approx(bubble, abs=1e-6)
    assert row["T_ref_cond_sat_v [°C]"] - row["T_ref_cond_sat_l [°C]"] > 4.0  # the R407C glide
    # The saturated-vapour enthalpy already was the dew-point value.
    assert row["h_ref_cond_sat_v [J/kg]"] == pytest.approx(CP.PropsSI("H", "P", P, "Q", 1, "R407C"), rel=1e-9)


@pytest.mark.parametrize("ref", ["R32", "R290", "R134a"])
@pytest.mark.parametrize("family", list(FAMILIES))
def test_pure_refrigerant_condensing_temperatures_are_unchanged(family, ref):
    model = _quiet(FAMILIES[family][0], ref=ref)
    row = _steady(model, family, 45.0, 0.6 * model.hp_capacity)
    assert row["T_ref_cond_sat_v [°C]"] == row["T_ref_cond_sat_l [°C]"]


def test_tank_heat_exergy_stays_at_the_bubble_point():
    """Only the reported key moves: the condenser-heat exergy uses the bubble point, as before."""
    model = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R407C")
    df = _dynamic(model, 40.0, 2, 600.0)
    ran = df[df["hp_is_on"].astype(bool)]
    T0_K = ran["T0 [°C]"] + 273.15
    expected = ran["Q_ref_tank [W]"] * (1.0 - T0_K / (ran["T_ref_cond_sat_l [°C]"] + 273.15))
    assert np.allclose(ran["X_ref_tank [W]"], expected, rtol=1e-12)
    assert (ran["T_ref_cond_sat_v [°C]"] > ran["T_ref_cond_sat_l [°C]"] + 3.0).all()  # the glide


def test_dynamic_frame_is_still_numeric_where_it_was(dynamic_runs):
    """`capacity_clamped` joins the frame as an object column; the numeric columns stay numeric."""
    df = dynamic_runs["ASHPB"]["charging"]
    for key in ("Q_max [W]", "Q_request [W]", "time [s]", *FLOW_KEYS):
        assert pd.api.types.is_numeric_dtype(df[key]), key
