"""The fixed control power ``E_ctrl``: defaults, system boundary and how it is averaged (``tmhp.control_power``).

``E_tot`` was the compressor with its fans and pumps; a real unit also feeds a
control board, sensors, communications and the standby of its drive. That
power is an input now and part of ``E_tot [W]`` and ``cop_sys [-]``:

* the defaults are the medians Heat Pump Keymark certificates declare, read
  here from the committed extraction (15 W to water, 12 W to air);
* ``E_ctrl=0`` is the machine without it: with the earlier rotary speed shape
  as well it reproduces the rated COPs recorded before the change;
* the row keeps its books: ``E_tot`` is the sum of its parts on running,
  cycling, frosting and dynamic rows, and ``X_tot`` follows it;
* the off time is charged once: an on/off mean multiplies ``E_ctrl`` by the
  power fraction (for a unit delivering to water the EN 14825 ``Cc`` form,
  which already contains the off-period draw), a defrost leaves it alone, a
  boiler's dynamic step multiplies it by the run-time fraction, an off row
  has none.
"""

from __future__ import annotations

import inspect
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tmhp import (
    ASHPB_PV_ESS,
    AirSourceHeatPump,
    AirSourceHeatPumpBoiler,
    GroundSourceHeatPump,
    GroundSourceHeatPumpBoiler,
    PhotovoltaicSystem,
    WaterSourceHeatPumpBoiler,
)
from tmhp.compressor_efficiency import CompressorSpec
from tmhp.control_power import CONTROL_POWER_KEY, CONTROL_POWER_SINK_AIR_W, CONTROL_POWER_SINK_WATER_W

REPO_ROOT = Path(__file__).resolve().parents[1]
AUXILIARY = REPO_ROOT / "validation" / "data" / "auxiliary_power"

FAMILIES = {
    "ASHP": (AirSourceHeatPump, CONTROL_POWER_SINK_AIR_W),
    "GSHP": (GroundSourceHeatPump, CONTROL_POWER_SINK_AIR_W),
    "ASHPB": (AirSourceHeatPumpBoiler, CONTROL_POWER_SINK_WATER_W),
    "GSHPB": (GroundSourceHeatPumpBoiler, CONTROL_POWER_SINK_WATER_W),
    "WSHPB": (WaterSourceHeatPumpBoiler, CONTROL_POWER_SINK_WATER_W),
}
#: A point inside the modulation range of the default machine, and the key of the heat it delivers.
POINT = {
    "ASHP": (dict(Q_r_iu=-3000.0, T0=7.0, T_a_room=20.0), "Q_ref_iu [W]"),
    "GSHP": (dict(Q_r_iu=-6000.0, T0=7.0, T_a_room=20.0), "Q_ref_iu [W]"),
    "ASHPB": (dict(T_tank_w=30.0, T0=7.0, Q_ref_tank=9000.0), "Q_ref_tank [W]"),
    "GSHPB": (dict(T_tank_w=30.0, T_source=0.0, Q_ref_tank=6000.0), "Q_ref_tank [W]"),
    "WSHPB": (dict(T_tank_w=30.0, T_source=10.0, Q_ref_tank=6000.0), "Q_ref_tank [W]"),
}
#: The same point asked for a duty far below the minimum-speed one: the row is an on/off mean.
SMALL = {
    "ASHP": dict(Q_r_iu=-200.0),
    "GSHP": dict(Q_r_iu=-300.0),
    "ASHPB": dict(Q_ref_tank=500.0),
    "GSHPB": dict(Q_ref_tank=400.0),
    "WSHPB": dict(Q_ref_tank=400.0),
}
#: Electrical inputs that add up to ``E_tot [W]`` (the other ``E_*`` keys are parts or diagnostics of these).
PARTS = ("E_cmp [W]", "E_ou_fan [W]", "E_iu_fan [W]", "E_pmp [W]", CONTROL_POWER_KEY)


def build(name: str, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return FAMILIES[name][0](**kwargs)


def steady(model, **kwargs) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return model.analyze_steady(**kwargs)


def parts(row: dict) -> float:
    return sum(float(row[key]) for key in PARTS if key in row)


@pytest.fixture(scope="module", params=sorted(FAMILIES))
def family(request):
    name = request.param
    return name, build(name), build(name, E_ctrl=0.0)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
def test_defaults_are_the_medians_of_the_certified_declarations():
    """15 W: both off/standby declarations of the air-to-water certificates pooled; 12 W: air-to-air thermostat-off."""
    a2w = pd.read_csv(AUXILIARY / "keymark_pto_air_to_water.csv")
    a2a = pd.read_csv(AUXILIARY / "keymark_pto_air_to_air.csv")
    standby = pd.concat([a2w.P_offsb_a_W, a2w.P_offsb_b_W]).dropna()
    assert (len(a2w), len(a2a)) == (8676, 19)
    assert float(standby.median()) == CONTROL_POWER_SINK_WATER_W == 15.0
    assert float(a2a.P_TO_W.dropna().median()) == CONTROL_POWER_SINK_AIR_W == 12.0


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_constructor_default_follows_the_sink(name):
    cls, default = FAMILIES[name]
    parameter = inspect.signature(cls.__init__).parameters["E_ctrl"]
    assert parameter.default == default
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY or name in ("ASHP", "ASHPB")
    assert build(name).E_ctrl == default


def test_combination_classes_hand_the_input_to_their_boiler():
    """The PV and collector variants take the boiler's arguments through ``**kwargs``: same default, same input."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert ASHPB_PV_ESS(pv=PhotovoltaicSystem()).E_ctrl == CONTROL_POWER_SINK_WATER_W
        assert ASHPB_PV_ESS(pv=PhotovoltaicSystem(), E_ctrl=0.0).E_ctrl == 0.0


@pytest.mark.parametrize("name", sorted(FAMILIES))
@pytest.mark.parametrize("value", [-1.0, math.nan, math.inf, "15"])
def test_invalid_control_power_is_refused(name, value):
    with pytest.raises((ValueError, TypeError), match="E_ctrl"):
        build(name, E_ctrl=value)


# ---------------------------------------------------------------------------
# The running row
# ---------------------------------------------------------------------------
def test_running_row_adds_the_control_power_to_the_total(family):
    """Inside the modulation range a constant does not move the operating point: only the total and its COP change."""
    name, with_power, without = family
    kwargs, heat_key = POINT[name]
    a, b = steady(with_power, **kwargs), steady(without, **kwargs)
    assert a["capacity_clamped"] is None and b["capacity_clamped"] is None
    assert a[CONTROL_POWER_KEY] == FAMILIES[name][1] and b[CONTROL_POWER_KEY] == 0.0
    assert a["E_cmp [W]"] == pytest.approx(b["E_cmp [W]"], rel=1e-6)
    assert a["cmp_rpm [rpm]"] == pytest.approx(b["cmp_rpm [rpm]"], rel=1e-6)
    assert a["E_tot [W]"] == pytest.approx(b["E_tot [W]"] + FAMILIES[name][1], rel=1e-6)
    assert a["cop_ref [-]"] == pytest.approx(b["cop_ref [-]"], rel=1e-6)
    assert a["cop_sys [-]"] == pytest.approx(b["cop_sys [-]"] * b["E_tot [W]"] / a["E_tot [W]"], rel=1e-6)
    for row in (a, b):
        assert row["E_tot [W]"] == pytest.approx(parts(row), rel=1e-12)
        assert row[heat_key] == pytest.approx(abs(next(v for k, v in kwargs.items() if k.startswith("Q_"))), rel=1e-6)


def test_a_given_control_power_is_used_as_given(family):
    name, _, without = family
    kwargs, _ = POINT[name]
    row = steady(build(name, E_ctrl=40.0), **kwargs)
    assert row[CONTROL_POWER_KEY] == 40.0
    assert row["E_tot [W]"] == pytest.approx(steady(without, **kwargs)["E_tot [W]"] + 40.0, rel=1e-6)


def test_off_row_draws_no_control_power(family):
    name, with_power, _ = family
    kwargs, _ = POINT[name]
    request = next(k for k in kwargs if k.startswith("Q_"))
    row = steady(with_power, **{**kwargs, request: 0.0})
    assert row["hp_is_on"] is False
    assert row[CONTROL_POWER_KEY] == 0.0 and row["E_tot [W]"] == 0.0


def test_exergy_input_is_the_electrical_input(family):
    """ASHP's steady row carries the exergy columns: the control power is in ``X_tot`` and is destroyed."""
    name, with_power, _ = family
    kwargs, _ = POINT[name]
    row = steady(with_power, **kwargs)
    if "X_tot [W]" not in row:
        pytest.skip("this family's steady row has no exergy columns")
    assert row["X_tot [W]"] == pytest.approx(row["E_tot [W]"], rel=1e-12)
    assert row["Xc_ctrl [W]"] == row["X_ctrl [W]"] == row[CONTROL_POWER_KEY]


# ---------------------------------------------------------------------------
# The earlier machine
# ---------------------------------------------------------------------------
#: ``cop_sys`` of the default machines at standard conditions, recorded on 19e14ab5 (before the control power
#: and with ``shao_informed`` as the default rotary speed shape): ``model.operating_range()``.
RECORDED_COP = {
    "ASHP": {"A7/20": 4.650565, "A35/27": 4.269342},
    "GSHP": {"B0/20": 4.052672, "B25/27": 7.162773},
    "ASHPB": {"A7/W35": 4.580552, "A7/W55": 3.067860},
    "GSHPB": {"B0/W35": 3.851797, "B0/W55": 2.622774},
    "WSHPB": {"W10/W35": 5.035507, "W10/W55": 3.243059},
}


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_without_control_power_and_with_the_earlier_speed_shape_the_recorded_cops_return(name):
    earlier = CompressorSpec("rotary_twin_inverter", rotary_speed_variant="shao_informed")
    model = build(name, E_ctrl=0.0, compressor_type=earlier)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        table = model.operating_range().set_index("condition")
    for condition, cop in RECORDED_COP[name].items():
        assert table.loc[condition, "cop_sys [-]"] == pytest.approx(cop, rel=2e-6), condition


# ---------------------------------------------------------------------------
# On/off mean: the off time is charged once
# ---------------------------------------------------------------------------
def test_cycling_mean_scales_the_control_power_with_the_power_fraction(family):
    name, with_power, _ = family
    kwargs, _ = POINT[name]
    row = steady(with_power, **{**kwargs, **SMALL[name]})
    assert row["capacity_clamped"] == "min" and 0.0 < row["capacity_ratio [-]"] < 1.0
    power_fraction = row["capacity_ratio [-]"] / row["part_load_factor [-]"]
    assert row[CONTROL_POWER_KEY] == pytest.approx(FAMILIES[name][1] * power_fraction, rel=1e-9)
    assert row["E_tot [W]"] == pytest.approx(parts(row), rel=1e-12)


@pytest.mark.parametrize("name", ["ASHPB", "GSHPB", "WSHPB"])
def test_water_sink_mean_input_is_the_en14825_form_on_the_whole_running_input(name):
    """``P_mean = P_on (CR + (1 - Cc)(1 - CR))`` with ``P_on`` the running total, control power included.

    The off-period draw is the ``1 - Cc`` share of that total. Nothing is added
    for the off time beside it: the mean total is exactly the form, and the
    mean control power is its share of it.
    """
    kwargs, _ = POINT[name]
    small = {**kwargs, **SMALL[name]}
    mean = steady(build(name), **small)
    running = steady(build(name, Cd_cycling=None), **small)  # the continuous minimum-speed machine
    cr, cc = mean["capacity_ratio [-]"], 0.9
    fraction = cr + (1.0 - cc) * (1.0 - cr)
    assert mean["runtime_fraction [-]"] == pytest.approx(cr, rel=1e-12)
    assert running[CONTROL_POWER_KEY] == CONTROL_POWER_SINK_WATER_W
    assert mean["E_tot [W]"] == pytest.approx(running["E_tot [W]"] * fraction, rel=1e-6)
    assert mean[CONTROL_POWER_KEY] == pytest.approx(CONTROL_POWER_SINK_WATER_W * fraction, rel=1e-9)
    assert mean["cop_sys [-]"] == pytest.approx(running["cop_sys [-]"] * cr / fraction, rel=1e-6)


def test_measured_standby_is_stated_through_cc_not_added():
    """A unit whose off-state draw is its control power alone: ``Cc = 1 - E_ctrl / E_tot`` at the minimum speed."""
    kwargs, _ = POINT["ASHPB"]
    small = {**kwargs, **SMALL["ASHPB"]}
    running = steady(build("ASHPB", Cd_cycling=None), **small)
    cc = 1.0 - CONTROL_POWER_SINK_WATER_W / running["E_tot [W]"]
    mean = steady(build("ASHPB", Cd_cycling=cc), **small)
    cr = mean["capacity_ratio [-]"]
    # compressor and fan for the run time, the control power all the time
    expected = cr * (running["E_tot [W]"] - CONTROL_POWER_SINK_WATER_W) + CONTROL_POWER_SINK_WATER_W
    assert mean["E_tot [W]"] == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize("name", ["ASHP", "GSHP"])
def test_air_sink_control_power_runs_with_the_compressor(name):
    kwargs, _ = POINT[name]
    row = steady(build(name), **{**kwargs, **SMALL[name]})
    assert row[CONTROL_POWER_KEY] == pytest.approx(CONTROL_POWER_SINK_AIR_W * row["runtime_fraction [-]"], rel=1e-9)


def test_speed_floor_search_weighs_the_control_power_against_the_heat():
    """On the speed floor the candidates deliver different amounts: the objective is the whole input per heat."""
    kwargs = dict(T_tank_w=30.0, T0=7.0, Q_ref_tank=500.0)
    rows = {p: steady(build("ASHPB", E_ctrl=p, Cd_cycling=None), **kwargs) for p in (0.0, 15.0, 400.0)}
    assert all(row["capacity_clamped"] == "min" for row in rows.values())
    specific = {p: row["E_tot [W]"] / row["Q_ref_tank [W]"] for p, row in rows.items()}
    # each machine is the best of the floor for its own control power: no other one beats it there
    for p in rows:
        for other in rows.values():
            rival = (other["E_tot [W]"] - other[CONTROL_POWER_KEY] + p) / other["Q_ref_tank [W]"]
            assert specific[p] <= rival * (1.0 + 1e-6)


# ---------------------------------------------------------------------------
# Frost and defrost
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["ASHP", "ASHPB"])
def test_defrost_leaves_the_control_power_as_it_is(name):
    kwargs = (
        dict(Q_r_iu=-3000.0, T0=0.0, T_a_room=20.0)
        if name == "ASHP"
        else dict(T_tank_w=30.0, T0=0.0, Q_ref_tank=9000.0)
    )
    row, without = steady(build(name), **kwargs), steady(build(name, E_ctrl=0.0), **kwargs)
    assert row["defrost_fraction [-]"] > 0.0 and row["capacity_clamped"] is None
    assert row[CONTROL_POWER_KEY] == FAMILIES[name][1]
    assert row["E_tot [W]"] == pytest.approx(parts(row), rel=1e-12)
    # the frost multipliers act on the compressor and the fans: the control power is the same constant before and
    # after the mean over frosting and defrost
    for key in ("E_tot [W]", "E_tot_frost_free [W]"):
        assert row[key] - without[key] == pytest.approx(FAMILIES[name][1], rel=1e-6), key


# ---------------------------------------------------------------------------
# Dynamic rows
# ---------------------------------------------------------------------------
def test_boiler_dynamic_step_scales_the_control_power_with_the_run_time():
    model = build("ASHPB", hp_capacity=6000.0, T_tank_w_upper_bound=55.0, T_tank_w_lower_bound=50.0)
    steps = 18
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = model.analyze_dynamic(
            simulation_period_sec=steps * 600,
            dt_s=600,
            T_tank_w_init_C=54.0,
            dhw_usage_schedule=np.full(steps, 1.5e-5),
            T0_schedule=np.full(steps, 10.0),
        )
    runtime = df["runtime_fraction [-]"].to_numpy()
    assert (runtime == 0.0).any() and (runtime > 0.0).any()
    np.testing.assert_allclose(df[CONTROL_POWER_KEY], CONTROL_POWER_SINK_WATER_W * runtime, rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(
        df["E_tot [W]"], df["E_cmp [W]"] + df["E_ou_fan [W]"] + df[CONTROL_POWER_KEY], rtol=1e-9, atol=1e-9
    )
    np.testing.assert_allclose(df["X_tot [W]"], df["E_tot [W]"], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(df["Xc_ctrl [W]"], df[CONTROL_POWER_KEY], rtol=0, atol=0)


def test_space_conditioning_dynamic_rows_keep_their_books():
    model = build("ASHP")
    loads = np.array([-3000.0, -200.0, 0.0, 2500.0])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = model.analyze_dynamic(
            simulation_period_sec=4 * 3600,
            dt_s=3600,
            Q_r_iu_schedule=loads,
            T0_schedule=np.array([7.0, 7.0, 15.0, 32.0]),
        )
    assert df[CONTROL_POWER_KEY].iloc[0] == CONTROL_POWER_SINK_AIR_W == df[CONTROL_POWER_KEY].iloc[3]
    assert 0.0 < df[CONTROL_POWER_KEY].iloc[1] < CONTROL_POWER_SINK_AIR_W
    assert df[CONTROL_POWER_KEY].iloc[2] == 0.0 and df["E_tot [W]"].iloc[2] == 0.0
    total = df["E_cmp [W]"] + df["E_ou_fan [W]"] + df["E_iu_fan [W]"] + df[CONTROL_POWER_KEY]
    np.testing.assert_allclose(df["E_tot [W]"], total, rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(df["X_tot [W]"], df["E_tot [W]"], rtol=1e-9, atol=1e-9)
