"""On/off cycling below the minimum capacity: the boilers' ``analyze_steady``.

A request below what the compressor delivers at its minimum speed used to
return the continuous minimum-speed point: 7.6 (GSHPB), 9.2 (ASHPB) and 10
(WSHPB) times a request of 5 % of an 8 kW machine's capacity. ``analyze_steady``
now reports the mean of on/off cycling, as ``AirSourceHeatPump`` and
``GroundSourceHeatPump`` do: the heat equals the request and the electrical
input carries the EN 14825 degradation of a unit delivering to water (``Cc``
0.9: the unit draws a tenth of its running input while the compressor is off).

A dynamic run is untouched: there the tank thermostat switches the compressor,
which runs at ``hp_capacity`` for part of a step.
"""

import contextlib
import io
import math

import numpy as np
import pytest

from tmhp import (
    AirSourceHeatPumpBoiler,
    CyclingDegradation,
    GroundSourceHeatPumpBoiler,
    WaterSourceHeatPumpBoiler,
)
from tmhp.cycling import (
    CAPACITY_RATIO_KEY,
    CYCLING_LOSS_KEY,
    MIN_CAPACITY_KEY,
    PART_LOAD_FACTOR_KEY,
    RUNTIME_FRACTION_KEY,
)

# GSHPB / WSHPB: R410A, the refrigerant these minimum-speed duties belong to.
# The default is now R290, whose higher rated speed puts the minimum-speed duty
# at 1.8-2.4 kW here, at or below the 2 kW request. The 59 °C upper set point
# is inside R410A's compressor envelope (the default 65 °C would be capped).
_R410A = dict(ref="R410A", hp_capacity=8000.0, T_tank_w_upper_bound=59.0)
BOILERS = {
    "ASHPB": (AirSourceHeatPumpBoiler, dict(ref="R32", hp_capacity=8000.0), dict(T_tank_w=35.0, T0=7.0)),
    "GSHPB": (GroundSourceHeatPumpBoiler, _R410A, dict(T_tank_w=35.0, T_source=0.0, T0=10.0)),
    "WSHPB": (WaterSourceHeatPumpBoiler, _R410A, dict(T_tank_w=35.0, T_source=10.0, T0=10.0)),
}
SOURCE_HEAT = {"ASHPB": "Q_ref_ou [W]", "GSHPB": "Q_ref_ground [W]", "WSHPB": "Q_ref_water [W]"}
AUXILIARY = {"ASHPB": "E_ou_fan [W]", "GSHPB": "E_pmp [W]", "WSHPB": "E_pmp [W]"}
REQUEST = 2000.0  # below every family's minimum-speed duty at its point (2.6-4.0 kW)


def quiet(model, **kwargs):
    """``analyze_steady`` without the pressure-ratio guard's console line."""
    with contextlib.redirect_stdout(io.StringIO()):
        return model.analyze_steady(**kwargs)


@pytest.fixture(scope="module", params=sorted(BOILERS))
def boiler(request):
    """(family, model, point, cycling row, continuous row) for one boiler family."""
    family = request.param
    cls, ctor, point = BOILERS[family]
    model = cls(**ctor)
    continuous = quiet(cls(**ctor, Cd_cycling=None), Q_ref_tank=REQUEST, **point)
    return family, model, point, quiet(model, Q_ref_tank=REQUEST, **point), continuous


def test_boiler_defaults_to_the_water_form(boiler):
    _, model, _, _, _ = boiler
    assert model.cycling == CyclingDegradation(0.9, "water")
    with pytest.raises(ValueError):
        type(model)(Cd_cycling=0.0)  # Cc = 0 is not a unit that can cycle


def test_boiler_without_the_cycling_model_over_delivers(boiler):
    _, _, _, _, continuous = boiler
    assert continuous["capacity_clamped"] == "min"
    assert continuous["Q_ref_tank [W]"] > 2500.0  # asked for 2,000 W
    assert continuous[RUNTIME_FRACTION_KEY] == 1.0 and continuous[CAPACITY_RATIO_KEY] == 1.0
    assert continuous[MIN_CAPACITY_KEY] == pytest.approx(continuous["Q_ref_tank [W]"])
    assert continuous[CYCLING_LOSS_KEY] == 0.0


def test_boiler_request_below_the_minimum_capacity_is_delivered_exactly(boiler):
    family, _, _, row, continuous = boiler
    assert row["converged"] and row["failure_reason"] == "none" and row["hp_is_on"]
    assert row["capacity_clamped"] == "min"
    assert row["Q_ref_tank [W]"] == pytest.approx(REQUEST, rel=1e-12)
    # The steady search does not weigh the degradation: the running machine
    # is the continuous minimum-speed point.
    assert row[MIN_CAPACITY_KEY] == pytest.approx(continuous["Q_ref_tank [W]"], rel=1e-9)
    cr = row[CAPACITY_RATIO_KEY]
    assert cr == pytest.approx(REQUEST / continuous["Q_ref_tank [W]"], rel=1e-9)
    # Water form: the compressor runs for the capacity ratio of the time ...
    assert row[RUNTIME_FRACTION_KEY] == cr
    # ... and the unit draws a tenth of its running input while it is off.
    assert row[PART_LOAD_FACTOR_KEY] == pytest.approx(cr / (0.9 * cr + 0.1), rel=1e-12)
    mean_share = cr + 0.1 * (1.0 - cr)
    for key in ("E_cmp [W]", AUXILIARY[family], "E_tot [W]"):
        assert row[key] == pytest.approx(continuous[key] * mean_share, rel=1e-9), key
    assert row[CYCLING_LOSS_KEY] == pytest.approx(continuous["E_tot [W]"] * 0.1 * (1.0 - cr), rel=1e-9)
    for key in ("cop_ref [-]", "cop_sys [-]"):
        assert row[key] == pytest.approx(continuous[key] * row[PART_LOAD_FACTOR_KEY], rel=1e-9), key


def test_boiler_mean_row_keeps_its_books(boiler):
    family, model, _, r, continuous = boiler
    cr = r[CAPACITY_RATIO_KEY]
    source = SOURCE_HEAT[family]
    work = r["m_dot_ref [kg/s]"] * (r["h_ref_cmp_out [J/kg]"] - r["h_ref_cmp_in [J/kg]"])
    assert r["Q_ref_tank [W]"] - r[source] == pytest.approx(work, rel=1e-6)
    assert r[source] == pytest.approx(continuous[source] * cr, rel=1e-9)
    assert r["E_tot [W]"] == pytest.approx(r["E_cmp [W]"] + r[AUXILIARY[family]], rel=1e-12)
    assert r["cop_ref [-]"] == pytest.approx(r["Q_ref_tank [W]"] / r["E_cmp [W]"], rel=1e-12)
    assert r["cop_sys [-]"] == pytest.approx(r["Q_ref_tank [W]"] / r["E_tot [W]"], rel=1e-12)
    if family == "ASHPB":
        assert r["PLR_delivered [-]"] == pytest.approx(REQUEST / model.hp_capacity, rel=1e-12)
        assert r["Q_request [W]"] == REQUEST
        assert r["dV_ou_a [m3/s]"] == pytest.approx(continuous["dV_ou_a [m3/s]"] * cr, rel=1e-9)
    else:
        assert r["Q_bhe [W]"] == pytest.approx(r[source] - r["E_pmp [W]"], rel=1e-12)
        assert r["Q_request [W]"] == REQUEST
    # State points are those of the running machine.
    for key in ("cmp_rpm [rpm]", "T_ref_cmp_out [°C]", "T_ref_evap_sat [°C]", "dT_ref_tank [K]"):
        assert r[key] == pytest.approx(continuous[key], rel=1e-9), key


def test_boiler_loss_free_and_tested_coefficients(boiler):
    family, model, point, _, continuous = boiler
    cls, ctor, _ = BOILERS[family]
    ideal = quiet(cls(**ctor, Cd_cycling=1.0), Q_ref_tank=REQUEST, **point)
    assert ideal["Q_ref_tank [W]"] == pytest.approx(REQUEST, rel=1e-12)
    assert ideal[PART_LOAD_FACTOR_KEY] == 1.0 and ideal[CYCLING_LOSS_KEY] == 0.0
    assert ideal["cop_sys [-]"] == pytest.approx(continuous["cop_sys [-]"], rel=1e-9)
    # A unit tested for its off-state power: Cc = 1 - P_off / P_on.
    standby = 15.0
    tested = cls(**ctor, Cd_cycling=1.0 - standby / continuous["E_tot [W]"])
    row = quiet(tested, Q_ref_tank=REQUEST, **point)
    cr = row[CAPACITY_RATIO_KEY]
    assert row["E_tot [W]"] == pytest.approx(cr * continuous["E_tot [W]"] + (1.0 - cr) * standby, rel=1e-9)


def test_boiler_rows_that_do_not_cycle(boiler):
    family, model, point, _, _ = boiler
    met = quiet(model, Q_ref_tank=6000.0, **point)
    assert met["capacity_clamped"] is None and met["Q_ref_tank [W]"] == pytest.approx(6000.0, rel=1e-9)
    assert met[RUNTIME_FRACTION_KEY] == 1.0 and met[CAPACITY_RATIO_KEY] == 1.0 and met[PART_LOAD_FACTOR_KEY] == 1.0
    assert math.isnan(met[MIN_CAPACITY_KEY]) and met[CYCLING_LOSS_KEY] == 0.0
    off = quiet(model, Q_ref_tank=0.0, **point)
    assert not off["hp_is_on"] and off[RUNTIME_FRACTION_KEY] == 0.0 and off[CAPACITY_RATIO_KEY] == 0.0
    frame = quiet(model, Q_ref_tank=REQUEST, return_dict=False, **point)
    assert frame.shape[0] == 1 and frame["Q_ref_tank [W]"].iloc[0] == pytest.approx(REQUEST, rel=1e-12)
    assert 0.0 < frame[RUNTIME_FRACTION_KEY].iloc[0] < 1.0


def test_boiler_power_is_continuous_where_cycling_ends(boiler):
    family, model, point, row, _ = boiler
    floor = row[MIN_CAPACITY_KEY]
    below, above = (quiet(model, Q_ref_tank=f * floor, **point) for f in (0.98, 1.02))
    assert (below["capacity_clamped"], above["capacity_clamped"]) == ("min", None)
    assert below["E_tot [W]"] < above["E_tot [W]"]
    assert above["E_tot [W]"] / below["E_tot [W]"] == pytest.approx(1.02 / 0.98, rel=3e-2)


def test_boiler_dynamic_run_is_not_touched_by_the_steady_cycling_report():
    """The tank thermostat makes the dynamic cycling; ``Cd_cycling`` must not add to it."""
    steps, dt = 36, 600
    draw = np.zeros(steps)
    draw[6:9] = 8.0 / 60000.0  # 8 L/min for half an hour
    kwargs = dict(
        simulation_period_sec=steps * dt,
        dt_s=dt,
        T_tank_w_init_C=52.0,
        dhw_usage_schedule=draw,
        T0_schedule=np.full(steps, 7.0),
    )
    with contextlib.redirect_stdout(io.StringIO()):
        default = AirSourceHeatPumpBoiler().analyze_dynamic(**kwargs)
        without = AirSourceHeatPumpBoiler(Cd_cycling=None).analyze_dynamic(**kwargs)
    assert default["hp_is_on"].any() and not default["hp_is_on"].all()
    assert (default[RUNTIME_FRACTION_KEY] <= 1.0).all()
    for column in ("Q_ref_tank [W]", "E_cmp [W]", "E_tot [W]", "T_tank_w [°C]", RUNTIME_FRACTION_KEY):
        assert default[column].to_numpy() == pytest.approx(without[column].to_numpy(), rel=0, abs=0, nan_ok=True)


def test_en14825_trend_harness_compares_the_running_machine():
    """The certified-population study must not pick up the cycling degradation.

    The declared COP of an EN 14825 part-load condition belongs to the capacity
    the unit runs at there; the degradation is applied afterwards, in the bin
    calculation. The harness therefore solves the running machine
    (``Cd_cycling=None``), and its points B-D -- all below the minimum-speed
    duty -- are the ones committed in ``validation/data``. With the default
    report they would read 4.77 / 5.34 / 5.25 instead of 4.84 / 5.88 / 7.55.
    """
    pd = pytest.importorskip("pandas")
    from pathlib import Path

    from validation.analysis import en14825_trend

    committed = pd.read_csv(Path(en14825_trend.__file__).resolve().parents[1] / "data" / "en14825_trend_points.csv")
    selection = (
        (committed.variant == "defaults")
        & (committed.application == "low")
        & (committed.capacity_kW == 9.0)
        & (committed.refrigerant == "R32")
        & (committed.oversizing == 0.667)
    )
    expected = committed[selection].set_index("point")
    variant = next(v for v in en14825_trend.variants() if v.key == "defaults")
    with contextlib.redirect_stdout(io.StringIO()):
        rows = en14825_trend.trajectory(variant, 9000.0, "R32", "low", 0.667)
    assert [r["point"] for r in rows] == ["A", "B", "C", "D"]
    assert [r["clamped"] for r in rows] == [False, True, True, True]
    for row in rows:
        assert row["cop"] == pytest.approx(expected.cop[row["point"]], rel=1e-6), row["point"]
    cop = [r["cop"] for r in rows]
    assert cop == sorted(cop)  # rises from A to D, as the certified population does
