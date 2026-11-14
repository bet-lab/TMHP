"""Result keys of ``AirSourceHeatPump`` and ``GroundSourceHeatPump`` rows.

Two things the boilers' rows already do (``tests/test_boiler_result_keys.py``):

- **The same keys on every row.** A row gained ``capacity_binding`` and
  ``Q_max [W]`` only at a capacity ceiling, an ASHP row its compressor keys
  only while running, a GSHP row its approach keys only when solved. A dynamic
  frame is the union of its rows, so its columns depended on what the run
  happened to do. What does not apply to a row is now ``None`` or NaN.
- **The dew point under ``T_ref_cond_sat_v [°C]``.** For a zeotropic blend the
  key repeated the bubble point (``T_ref_cond_sat_l [°C]``); it is the dew
  point of the condensing pressure now. Only the reported key changes: the
  cycle, every check and the condenser-heat exergy are as before.
"""

import math
import warnings

import CoolProp.CoolProp as CP
import numpy as np
import pandas as pd
import pytest

from tmhp import AirSourceHeatPump, GroundSourceHeatPump

STEPS, DT = 3, 3600


def _quiet(function, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return function(*args, **kwargs)


@pytest.fixture(scope="module")
def ashp():
    return _quiet(AirSourceHeatPump)


@pytest.fixture(scope="module")
def gshp():
    return _quiet(GroundSourceHeatPump, t_max_s=STEPS * DT, dt_s=DT)


@pytest.fixture(scope="module")
def ashp_rows(ashp):
    steady = dict(verbose=False, postprocess=False)
    return {
        "running": ashp.analyze_steady(Q_r_iu=-2500.0, T0=7.0, T_a_room=20.0, **steady),
        "cycling": ashp.analyze_steady(Q_r_iu=-200.0, T0=7.0, T_a_room=20.0, **steady),
        "ceiling": ashp.analyze_steady(Q_r_iu=-20000.0, T0=-7.0, T_a_room=20.0, **steady),
        "frosting": ashp.analyze_steady(Q_r_iu=-2500.0, T0=0.0, T_a_room=20.0, **steady),
        "cooling": ashp.analyze_steady(Q_r_iu=2500.0, T0=35.0, T_a_room=27.0, **steady),
        "off": ashp.analyze_steady(Q_r_iu=0.0, T0=7.0, T_a_room=20.0, **steady),
        "failed": _quiet(AirSourceHeatPump, ref="R134a").analyze_steady(
            Q_r_iu=-6000.0, T0=-45.0, T_a_room=30.0, **steady
        ),
    }


@pytest.fixture(scope="module")
def gshp_rows(gshp):
    wall = dict(T0=0.0, T_a_room=20.0, T_bhe_wall=5.0)
    water = _quiet(GroundSourceHeatPump, source_fluid="water")
    return {
        "running": gshp.analyze_steady(Q_r_iu=-2500.0, **wall),
        "no wall given": gshp.analyze_steady(Q_r_iu=-2500.0, T0=0.0, T_a_room=20.0),
        "cycling": gshp.analyze_steady(Q_r_iu=-200.0, **wall),
        "ceiling": gshp.analyze_steady(Q_r_iu=-20000.0, **wall),
        "freeze-limited": _quiet(water.analyze_steady, Q_r_iu=-4000.0, T0=0.0, T_a_room=20.0, T_bhe_wall=7.0),
        "freeze-off": _quiet(water.analyze_steady, Q_r_iu=-4000.0, T0=0.0, T_a_room=20.0, T_bhe_wall=2.0),
        "cooling": gshp.analyze_steady(Q_r_iu=2500.0, T0=30.0, T_a_room=27.0, T_bhe_wall=20.0),
        "off": gshp.analyze_steady(Q_r_iu=0.0, T0=0.0, T_a_room=20.0),
        "failed": _quiet(gshp.analyze_steady, Q_r_iu=-2500.0, T0=0.0, T_a_room=20.0, T_bhe_wall=-40.0),
    }


def _same_keys(rows):
    reference = set(next(iter(rows.values())))
    for name, row in rows.items():
        assert set(row) == reference, (name, sorted(set(row) ^ reference))


def test_ashp_steady_rows_have_the_same_keys(ashp_rows):
    assert ashp_rows["ceiling"]["capacity_clamped"] == "max" and ashp_rows["cycling"]["capacity_clamped"] == "min"
    assert ashp_rows["failed"]["failure_reason"] != "none" and not ashp_rows["off"]["hp_is_on"]
    _same_keys(ashp_rows)


def test_gshp_steady_rows_have_the_same_keys(gshp_rows):
    assert gshp_rows["ceiling"]["capacity_clamped"] == "max" and gshp_rows["cycling"]["capacity_clamped"] == "min"
    assert gshp_rows["freeze-limited"]["capacity_binding"] == "source_fluid_freezing"
    assert gshp_rows["freeze-off"]["failure_reason"] == "source_fluid_freezing"
    assert gshp_rows["failed"]["failure_reason"] not in ("none", "source_fluid_freezing")
    _same_keys(gshp_rows)


@pytest.mark.parametrize("family", ["ashp", "gshp"])
def test_keys_that_do_not_apply_are_none_or_nan(family, ashp_rows, gshp_rows):
    rows = ashp_rows if family == "ashp" else gshp_rows
    for name in ("running", "cycling", "cooling", "off", "failed"):
        assert rows[name]["capacity_binding"] is None, name
        assert math.isnan(rows[name]["Q_max [W]"]), name
    ceiling = rows["ceiling"]
    assert ceiling["capacity_binding"] is not None and ceiling["Q_max [W]"] == pytest.approx(ceiling["Q_ref_iu [W]"])


def test_ashp_off_and_failed_rows_carry_nan_compressor_keys(ashp_rows):
    for name in ("off", "failed"):
        for key in ("n_star [-]", "pr_cmp [-]", "eta_cmp_isen [-]", "eta_cmp_vol [-]", "eta_cmp [-]"):
            assert math.isnan(ashp_rows[name][key]), (name, key)
    assert 0.0 < ashp_rows["running"]["eta_cmp_isen [-]"] < 1.0 and ashp_rows["running"]["pr_cmp [-]"] > 1.0


def test_gshp_unsolved_rows_carry_nan_approach_keys(gshp_rows):
    for name in ("off", "failed", "freeze-off"):
        row = gshp_rows[name]
        assert math.isnan(row["indoor_approach [K]"]) and math.isnan(row["ground_approach [K]"]), name
        assert row["ground_balance"] is None, name
    assert gshp_rows["running"]["ground_balance"] == "equality"
    assert gshp_rows["freeze-off"]["failure_detail"] == "source_inlet_below_limit"  # a 2 °C wall: below 3 °C already
    assert all(row["failure_detail"] is None for name, row in gshp_rows.items() if name != "freeze-off")
    # The wall temperature is echoed; NaN says it was not given.
    assert gshp_rows["running"]["T_bhe_wall [°C]"] == 5.0
    assert math.isnan(gshp_rows["no wall given"]["T_bhe_wall [°C]"]) and math.isnan(gshp_rows["off"]["T_bhe_wall [°C]"])


@pytest.mark.parametrize("family", ["ashp", "gshp"])
def test_dynamic_frames_have_the_same_columns_whatever_the_run_does(family, ashp, gshp):
    model = ashp if family == "ashp" else gshp
    T0 = np.full(STEPS, 7.0 if family == "ashp" else 0.0)
    frames = {
        label: _quiet(model.analyze_dynamic, STEPS * DT, DT, np.array(load), T0, np.full(STEPS, 20.0))
        for label, load in {
            "running": [-2500.0] * STEPS,
            "ceiling, cycling and off": [-20000.0, -300.0, 0.0],
            "off": [0.0] * STEPS,
        }.items()
    }
    mixed = frames["ceiling, cycling and off"]
    assert list(mixed["capacity_clamped"].fillna("none")) == ["max", "min", "none"]
    # In a frame a name that does not apply reads as missing (pandas shows ``None`` in a text column as NaN).
    assert isinstance(mixed["capacity_binding"].iloc[0], str) and mixed["capacity_binding"].iloc[1:].isna().all()
    assert np.isnan(mixed["Q_max [W]"].iloc[1:]).all() and mixed["Q_max [W]"].iloc[0] > 0.0
    columns = list(frames["running"].columns)
    for label, frame in frames.items():
        assert set(frame.columns) == set(columns), (label, sorted(set(frame.columns) ^ set(columns)))
    for key in ("capacity_binding", "Q_max [W]", "dT_ref_superheat [K]", "x_ref_cmp_in [-]", "discharge_control"):
        assert key in columns, key
    assert frames["off"]["discharge_control"].eq(False).all() and frames["off"]["dT_ref_superheat [K]"].isna().all()


# ---------------------------------------------------------------------------
# Dew point of a zeotropic blend
# ---------------------------------------------------------------------------


def _blend_row(cls, ref, cooling=False):
    model = _quiet(cls, ref=ref)
    if cls is AirSourceHeatPump:
        point = dict(Q_r_iu=2500.0, T0=35.0, T_a_room=27.0) if cooling else dict(Q_r_iu=-2500.0, T0=7.0, T_a_room=20.0)
        return model.analyze_steady(**point, verbose=False)
    point = (
        dict(Q_r_iu=2500.0, T0=30.0, T_a_room=27.0, T_bhe_wall=20.0)
        if cooling
        else dict(Q_r_iu=-2500.0, T0=0.0, T_a_room=20.0, T_bhe_wall=5.0)
    )
    return model.postprocess_exergy(model.analyze_steady(**point, return_dict=False)).iloc[0].to_dict()


@pytest.mark.parametrize("cls", [AirSourceHeatPump, GroundSourceHeatPump])
@pytest.mark.parametrize("cooling", [False, True], ids=["heating", "cooling"])
def test_zeotrope_reports_the_dew_point_and_keeps_its_exergy(cls, cooling):
    row = _blend_row(cls, "R407C", cooling)
    assert row["failure_reason"] == "none"
    P = row["P_ref_cmp_out [Pa]"]
    dew = CP.PropsSI("T", "P", P, "Q", 1, "R407C") - 273.15
    bubble = CP.PropsSI("T", "P", P, "Q", 0, "R407C") - 273.15
    assert row["T_ref_cond_sat_l [°C]"] == pytest.approx(bubble, abs=1e-6)
    assert row["T_ref_cond_sat_v [°C]"] == pytest.approx(dew, abs=1e-6)  # was the bubble point
    assert 3.5 < row["T_ref_cond_sat_v [°C]"] - row["T_ref_cond_sat_l [°C]"] < 6.5  # the glide
    # The condenser-heat exergy keeps the temperature the cycle condenses at (the bubble point).
    T0_K = row["T0 [°C]"] + 273.15
    condenser_heat = (
        "Q_ref_iu [W]" if not cooling else ("Q_ref_ou [W]" if cls is AirSourceHeatPump else "Q_ref_ground [W]")
    )
    exergy = "X_ref_iu [W]" if not cooling else ("X_ref_ou [W]" if cls is AirSourceHeatPump else "X_ref_ground [W]")
    assert row[exergy] == pytest.approx(row[condenser_heat] * (1.0 - T0_K / (bubble + 273.15)), rel=1e-9)


@pytest.mark.parametrize("cls", [AirSourceHeatPump, GroundSourceHeatPump])
def test_near_azeotrope_and_pure_refrigerants(cls):
    r410a = _blend_row(cls, "R410A")
    assert 0.05 < r410a["T_ref_cond_sat_v [°C]"] - r410a["T_ref_cond_sat_l [°C]"] < 0.2
    for ref in ("R32", "R290"):
        pure = _blend_row(cls, ref)
        assert pure["T_ref_cond_sat_v [°C]"] == pure["T_ref_cond_sat_l [°C]"], ref


def test_evaporating_key_is_the_dew_point_of_the_evaporating_pressure():
    """``T_ref_evap_sat`` has no bubble/dew pair to confuse: the cycle fixes the pressure at saturated vapour."""
    row = _blend_row(AirSourceHeatPump, "R407C", cooling=True)
    dew = CP.PropsSI("T", "P", row["P_ref_cmp_in [Pa]"], "Q", 1, "R407C") - 273.15
    assert row["T_ref_evap_sat [°C]"] == pytest.approx(dew, abs=1e-6)
    # The refrigerant enters the evaporator colder than that, by part of the glide.
    assert row["T_ref_exp_out [°C]"] < row["T_ref_evap_sat [°C]"] - 2.0


@pytest.mark.parametrize("cooling", [False, True], ids=["heating", "cooling"])
def test_ashp_one_row_and_frame_exergy_agree_for_a_zeotrope(cooling):
    """The steady point's scalar post-processing and the frame's read the same condensing temperature."""
    model = _quiet(AirSourceHeatPump, ref="R407C")
    point = dict(Q_r_iu=2500.0, T0=35.0, T_a_room=27.0) if cooling else dict(Q_r_iu=-2500.0, T0=7.0, T_a_room=20.0)
    plain = model.analyze_steady(**point, verbose=False, postprocess=False)
    one_row = model.analyze_steady(**point, verbose=False, postprocess=True)
    frame = model.postprocess_exergy(pd.DataFrame([plain])).iloc[0]
    assert plain["T_ref_cond_sat_v [°C]"] > plain["T_ref_cond_sat_l [°C]"] + 3.5
    for key in ("X_ref_iu [W]", "X_ref_ou [W]", "X_eff_sys [-]", "Xc_iu_hx [W]", "Xc_ou_hx [W]"):
        assert one_row[key] == frame[key], key
