"""Frost-weighted forms of the frost adjustment (``DefrostModel.frost_loss``, ``load_weighted``).

The EnergyPlus on-demand multipliers keep their fixed part (0.875 on capacity,
0.954 on power) wherever the outdoor temperature is below the limit, however
little frost the air can deposit. The weighted forms let that part follow the
deposition. They are options: the default stays EnergyPlus, and every form
leaves the full-load point at the frosting rating condition (2 degC, 84 %)
and warmer, moister conditions where it was.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest
from scipy.optimize import brentq

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp.defrost import FROST_LOSS_FORMS, DefrostModel, humidity_ratio, saturation_humidity_ratio

CONSTANT = DefrostModel()
WEIGHTED = {form: DefrostModel(frost_loss=form) for form in FROST_LOSS_FORMS if form != "constant"}


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _cop_factor(frost) -> float:
    return frost.capacity_multiplier / (frost.power_multiplier + frost.defrost_fraction)


def test_default_is_the_energyplus_form_and_the_options_are_checked():
    assert CONSTANT.frost_loss == "constant" and CONSTANT.load_weighted is False
    assert FROST_LOSS_FORMS == ("constant", "moisture", "accumulation")
    with pytest.raises(ValueError, match="frost_loss must be one of"):
        DefrostModel(frost_loss="humidity")
    with pytest.raises(ValueError, match="load_weighted must be True or False"):
        DefrostModel(load_weighted=1)
    with pytest.raises(ValueError, match="load_ratio"):
        DefrostModel(load_weighted=True).frost_state(0.0, load_ratio=-0.1)


@pytest.mark.parametrize("form", sorted(WEIGHTED))
@pytest.mark.parametrize(("T_out", "RH"), [(2.0, 0.84), (2.0, 1.0), (3.5, 0.84), (4.9, 0.84), (5.5, 0.84)])
def test_weighted_forms_equal_energyplus_at_the_rating_condition_and_above(form, T_out, RH):
    """At A2, and wherever the air deposits at least as much, nothing changes."""
    reference, weighted = CONSTANT.frost_state(T_out, RH), WEIGHTED[form].frost_state(T_out, RH)
    assert weighted.active is reference.active
    for name in ("defrost_fraction", "capacity_multiplier", "power_multiplier"):
        assert getattr(weighted, name) == pytest.approx(getattr(reference, name), rel=1e-12), name
    if reference.active:
        assert weighted.frost_loss_weight == 1.0


@pytest.mark.parametrize("form", sorted(WEIGHTED))
def test_weighted_forms_take_less_where_less_frost_can_form(form):
    model = WEIGHTED[form]
    previous = None
    for T_out in np.arange(2.0, -30.01, -1.0):
        frost, reference = model.frost_state(float(T_out)), CONSTANT.frost_state(float(T_out))
        assert reference.capacity_multiplier - 1e-12 <= frost.capacity_multiplier <= 1.0
        assert reference.power_multiplier - 1e-12 <= frost.power_multiplier <= 1.0
        assert frost.defrost_fraction == pytest.approx(reference.defrost_fraction, rel=1e-12)
        if previous is not None:  # colder air holds less water: the correction only shrinks
            assert frost.capacity_multiplier >= previous.capacity_multiplier - 1e-12
            assert _cop_factor(frost) >= _cop_factor(previous) - 1e-12
        previous = frost
    # EnergyPlus still takes a tenth of the COP at -15 degC; the weighted forms a few percent.
    assert _cop_factor(CONSTANT.frost_state(-15.0)) < 0.91
    assert 0.95 < _cop_factor(model.frost_state(-15.0)) < 0.98


@pytest.mark.parametrize("form", sorted(WEIGHTED))
def test_correction_goes_to_nothing_continuously_as_the_air_dries(form):
    """Across the humidity at which the air stops depositing on the coil: no step, then no frost."""
    model, T_out = WEIGHTED[form], -5.0
    coil = 0.82 * T_out - 8.589
    # The humidity at which the outdoor air holds what saturated air holds at the coil temperature.
    rh_dry = brentq(lambda rh: humidity_ratio(T_out, rh) - saturation_humidity_ratio(coil), 0.2, 0.9, xtol=1e-12)
    assert 0.3 < rh_dry < 0.7
    just_above = model.frost_state(T_out, rh_dry + 1e-4)
    below = model.frost_state(T_out, rh_dry - 1e-4)
    assert just_above.active and not below.active
    assert below.capacity_multiplier == below.power_multiplier == 1.0 and below.defrost_fraction == 0.0
    assert just_above.capacity_multiplier == pytest.approx(1.0, abs=2e-4)
    assert just_above.defrost_fraction < 2e-4
    factors = [_cop_factor(model.frost_state(T_out, float(rh))) for rh in np.linspace(rh_dry - 0.05, 0.84, 200)]
    assert max(abs(a - b) for a, b in zip(factors, factors[1:], strict=False)) < 2e-3
    # The EnergyPlus form steps there: its fixed part stays while the defrost time vanishes.
    assert CONSTANT.frost_state(T_out, rh_dry - 1e-4).capacity_multiplier == pytest.approx(0.875, abs=1e-3)


def test_load_weighting_warms_the_coil_and_stops_the_frost():
    model = DefrostModel(frost_loss="moisture", load_weighted=True)
    full = model.frost_state(2.0)
    assert model.frost_state(2.0, load_ratio=1.0) == full
    assert full.capacity_multiplier == pytest.approx(CONSTANT.frost_state(2.0).capacity_multiplier, rel=1e-12)
    previous = full
    for ratio in (0.8, 0.6, 0.4, 0.3):
        frost = model.frost_state(2.0, load_ratio=ratio)
        assert frost.T_coil_C == pytest.approx(2.0 - ratio * (0.18 * 2.0 + 8.589), rel=1e-12)
        assert frost.capacity_multiplier > previous.capacity_multiplier
        assert frost.defrost_fraction < previous.defrost_fraction
        previous = frost
    idle = model.frost_state(2.0, load_ratio=0.2)  # coil at +0.2 degC
    assert not idle.active and idle.capacity_multiplier == 1.0
    # A ratio above one is the rated-load correlation, not an extrapolation of it.
    assert model.frost_state(2.0, load_ratio=1.4) == full
    # Without the option the ratio is ignored.
    assert DefrostModel(frost_loss="moisture").frost_state(2.0, load_ratio=0.3) == full


@pytest.mark.parametrize(("T_out", "duty"), [(2.0, 3242.0), (-7.0, 5283.0), (2.0, 9000.0), (-15.0, 500.0)])
def test_load_ratio_is_that_of_the_duty_the_request_needs(T_out, duty):
    """``frost_state_at_duty``: the ratio and the correction it gives agree with each other."""
    model, rated = DefrostModel(frost_loss="moisture", load_weighted=True), 9000.0
    frost = model.frost_state_at_duty(T_out, None, duty, rated)
    gross = model.gross_duty(frost, duty, model.defrost_load(frost, T_out, rated))
    assert frost.load_ratio == pytest.approx(min(1.0, gross / rated), abs=1e-9)
    assert duty / rated - 1e-12 <= frost.load_ratio <= 1.0
    assert DefrostModel(frost_loss="moisture").frost_state_at_duty(T_out, None, duty, rated) == DefrostModel(
        frost_loss="moisture"
    ).frost_state(T_out)


# ---------------------------------------------------------------------------
# through the models
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def boilers() -> dict[str, AirSourceHeatPumpBoiler]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return {
            "off": AirSourceHeatPumpBoiler(defrost=False),
            "constant": AirSourceHeatPumpBoiler(),
            "moisture": AirSourceHeatPumpBoiler(defrost=DefrostModel(frost_loss="moisture")),
            "accumulation": AirSourceHeatPumpBoiler(defrost=DefrostModel(frost_loss="accumulation")),
            "moisture+load": AirSourceHeatPumpBoiler(defrost=DefrostModel(frost_loss="moisture", load_weighted=True)),
        }


def test_boiler_at_a2_is_the_same_under_every_form(boilers):
    point = {"T_tank_w": 45.0, "T0": 2.0, "Q_ref_tank": 8000.0}
    reference = boilers["constant"].analyze_steady(**point)
    for name in ("moisture", "accumulation"):
        result = boilers[name].analyze_steady(**point)
        for key in ("cop_sys [-]", "E_tot [W]", "defrost_fraction [-]", "frost_capacity_factor [-]"):
            assert result[key] == pytest.approx(reference[key], rel=1e-12), (name, key)


def test_boiler_cold_point_lies_between_energyplus_and_frost_free(boilers):
    point = {"T_tank_w": 45.0, "T0": -15.0, "Q_ref_tank": 8000.0}
    cop = {name: model.analyze_steady(**point)["cop_sys [-]"] for name, model in boilers.items()}
    assert cop["constant"] < cop["accumulation"] < cop["moisture"] < cop["off"]
    assert cop["moisture"] > 0.95 * cop["off"]  # little frost at -15 degC
    assert cop["constant"] < 0.90 * cop["off"]


def test_boiler_part_load_frosts_less_with_load_weighting(boilers):
    """EN 14825 point B for a 15 kW unit sized at 0.667: +2 degC, 54 % of the design load."""
    point = {"T_tank_w": 27.5, "T0": 2.0, "Q_ref_tank": 0.54 * 0.667 * 15000.0}
    full_load_form = boilers["moisture"].analyze_steady(**point)
    weighted = boilers["moisture+load"].analyze_steady(**point)
    frost_free = boilers["off"].analyze_steady(**point)
    for result in (full_load_form, weighted):
        assert result["failure_reason"] == "none"
        assert result["Q_ref_tank [W]"] == pytest.approx(point["Q_ref_tank"], rel=1e-9)
    assert weighted["defrost_fraction [-]"] < 0.5 * full_load_form["defrost_fraction [-]"]
    assert full_load_form["cop_sys [-]"] < weighted["cop_sys [-]"] < frost_free["cop_sys [-]"]
    # On its speed floor the coil is loaded by what the running machine absorbs.
    if weighted["capacity_clamped"] == "min":
        model = boilers["moisture+load"].defrost
        running = model.frost_state(2.0, load_ratio=weighted["Q_min [W]"] / 15000.0)
        assert weighted["frost_capacity_factor [-]"] == pytest.approx(running.capacity_multiplier, rel=1e-12)
    # At the nameplate the load ratio is one: the weighting changes nothing.
    nameplate = {"T_tank_w": 45.0, "T0": -7.0, "Q_ref_tank": 15000.0}
    assert boilers["moisture+load"].analyze_steady(**nameplate)["cop_sys [-]"] == pytest.approx(
        boilers["moisture"].analyze_steady(**nameplate)["cop_sys [-]"], rel=1e-12
    )


def test_off_is_still_the_frost_free_boiler(boilers):
    result = boilers["off"].analyze_steady(T_tank_w=45.0, T0=-7.0, Q_ref_tank=8000.0)
    assert result["cop_sys [-]"] == pytest.approx(2.666745555466922, rel=1e-6)  # before any frost correction
    assert result["defrost_fraction [-]"] == 0.0 and math.isnan(result["RH_out [-]"])


def test_air_to_air_model_takes_the_same_forms():
    """The forms live in ``tmhp.defrost``: the air-to-air model gets them through its ``defrost`` argument."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        constant = AirSourceHeatPump()
        moisture = AirSourceHeatPump(defrost=DefrostModel(frost_loss="moisture"))
    at_a2 = [m.analyze_steady(Q_r_iu=-2000.0, T0=2.0, verbose=False)["cop_sys [-]"] for m in (constant, moisture)]
    assert at_a2[1] == pytest.approx(at_a2[0], rel=1e-12)
    cold = [m.analyze_steady(Q_r_iu=-2000.0, T0=-15.0, verbose=False)["cop_sys [-]"] for m in (constant, moisture)]
    assert cold[1] > 1.05 * cold[0]


@pytest.mark.parametrize("Cd_cycling", [0.9, None], ids=["cycling", "continuous"])
@pytest.mark.parametrize("T0", [2.0, -7.0])
def test_small_request_frosts_as_the_machine_that_runs_on_the_speed_floor(Cd_cycling, T0):
    """A request too small to frost by its own load ratio is met by a machine at ``Q_min`` that does.

    With a high speed floor the unit runs at 36-47 % of its nameplate for a
    request of 10 %. The load ratio of the request alone puts the coil above
    the air's frost point, and the row used to come back uncorrected.
    """
    model = DefrostModel(frost_loss="moisture", load_weighted=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        boiler = AirSourceHeatPumpBoiler(defrost=model, rps_min=45.0, Cd_cycling=Cd_cycling)
        frost_free = AirSourceHeatPumpBoiler(defrost=False, rps_min=45.0, Cd_cycling=Cd_cycling)
    request = 1500.0
    assert not model.frost_state_at_duty(T0, None, request, boiler.hp_capacity).active
    result = boiler.analyze_steady(T_tank_w=35.0, T0=T0, Q_ref_tank=request)
    reference = frost_free.analyze_steady(T_tank_w=35.0, T0=T0, Q_ref_tank=request)
    assert result["failure_reason"] == "none" and result["capacity_clamped"] == "min"
    q_min = result["Q_min [W]"]
    assert q_min == pytest.approx(reference["Q_min [W]"], rel=1e-9) and q_min > 2.0 * request
    running = model.frost_state(T0, load_ratio=q_min / boiler.hp_capacity)
    assert running.active
    # (Q_min is that of the row; the state was taken from the solve before it, equal to 1e-9.)
    assert result["defrost_fraction [-]"] == pytest.approx(running.defrost_fraction, rel=1e-6)
    assert result["frost_capacity_factor [-]"] == pytest.approx(running.capacity_multiplier, rel=1e-6)
    assert result["cop_sys [-]"] < reference["cop_sys [-]"]
    if Cd_cycling is None:  # the continuous floor machine: its frosted mean
        assert result["Q_ref_tank [W]"] == pytest.approx(
            result["frost_capacity_factor [-]"] * q_min - result["Q_defrost [W]"], rel=1e-9
        )
    else:  # the request, met by cycling; the defrost loss follows the run time
        assert result["Q_ref_tank [W]"] == pytest.approx(request, rel=1e-9)
        continuous = DefrostModel.defrost_load(running, T0, boiler.hp_capacity)
        assert result["Q_defrost [W]"] == pytest.approx(result["runtime_fraction [-]"] * continuous, rel=1e-6)
    # Where the floor machine does not frost either, the row is left alone.
    warm = boiler.analyze_steady(T_tank_w=35.0, T0=5.5, Q_ref_tank=300.0)
    assert warm["capacity_clamped"] == "min"
    assert warm["defrost_fraction [-]"] == 0.0 and warm["frost_capacity_factor [-]"] == 1.0
    assert warm["cop_sys [-]"] == frost_free.analyze_steady(T_tank_w=35.0, T0=5.5, Q_ref_tank=300.0)["cop_sys [-]"]


# ---------------------------------------------------------------------------
# load weighting alone: the rated load is EnergyPlus unchanged
# ---------------------------------------------------------------------------

LOAD_ONLY = DefrostModel(load_weighted=True)
LOADS = np.round(np.arange(1.0, 0.195, -0.01), 2)


@pytest.mark.parametrize("RH", [1.0, 0.84, 0.6, 0.3, 0.05])
def test_load_weighting_alone_is_energyplus_at_the_rated_load(RH):
    """Bit for bit, at every outdoor state -- also where the deposition is on its floor."""
    for T_out in (4.9, 4.0, 3.0, 2.0, 0.0, -2.0, -7.0, -15.0, -25.0, -40.0):
        reference = CONSTANT.frost_state(T_out, RH)
        for state in (LOAD_ONLY.frost_state(T_out, RH), LOAD_ONLY.frost_state(T_out, RH, load_ratio=1.0)):
            assert state.active is reference.active
            assert state.capacity_multiplier == reference.capacity_multiplier
            assert state.power_multiplier == reference.power_multiplier
            assert state.defrost_fraction == reference.defrost_fraction


@pytest.mark.parametrize(
    "model", [LOAD_ONLY, DefrostModel(frost_loss="moisture", load_weighted=True)], ids=["constant", "moisture"]
)
@pytest.mark.parametrize("RH", [0.84, 0.6])
@pytest.mark.parametrize("T_out", [4.0, 2.0, -2.0, -7.0, -15.0])
def test_load_weighted_correction_shrinks_continuously_with_the_load(model, RH, T_out):
    """From the rated load down: monotone, no step -- also where the coil passes 0 degC in humid air."""
    states = [model.frost_state(T_out, RH, load_ratio=float(r)) for r in LOADS]
    capacity = [s.capacity_multiplier for s in states]
    factor = [_cop_factor(s) for s in states]
    fraction = [s.defrost_fraction for s in states]
    assert all(b >= a - 1e-12 for a, b in zip(capacity, capacity[1:], strict=False))
    assert all(b >= a - 1e-12 for a, b in zip(factor, factor[1:], strict=False))
    assert all(b <= a + 1e-12 for a, b in zip(fraction, fraction[1:], strict=False))
    # One hundredth of the load never moves the COP multiplier by more than 1.5 %.
    assert max(b - a for a, b in zip(factor, factor[1:], strict=False)) < 0.015
    # At a fifth of the load the coil is within 2 K of the air: (almost) nothing is left.
    assert capacity[-1] > 0.995 and fraction[-1] < 1e-3


def test_load_weighting_alone_follows_the_frost_drive():
    """Coil temperature and frost drive at -7 degC, 84 %: the fixed loss is the rated one times the drive."""
    T_out, ratio = -7.0, 0.5
    rated, part = LOAD_ONLY.frost_state(T_out), LOAD_ONLY.frost_state(T_out, load_ratio=ratio)
    assert part.T_coil_C == pytest.approx(T_out - ratio * (0.18 * T_out + 8.589), rel=1e-12)
    w_out = humidity_ratio(T_out, 0.84)
    drive = (w_out - saturation_humidity_ratio(part.T_coil_C)) / (w_out - saturation_humidity_ratio(rated.T_coil_C))
    assert 0.3 < drive < 0.5  # half the temperature difference, less than half the drive
    assert part.frost_loss_weight == pytest.approx(drive, rel=1e-12)
    assert part.humidity_ratio_difference == pytest.approx(drive * rated.humidity_ratio_difference, rel=1e-12)
    t_frac = 1.0 / (1.0 + 0.01446 / part.humidity_ratio_difference)
    assert part.defrost_fraction == pytest.approx(t_frac, rel=1e-12)
    assert part.capacity_multiplier == pytest.approx((1.0 - drive * 0.125) * (1.0 - t_frac), rel=1e-12)
    assert part.power_multiplier == pytest.approx((1.0 - drive * (1.0 - 0.954)) * (1.0 - t_frac), rel=1e-12)


def test_load_weighting_alone_keeps_the_energyplus_floor_where_the_rated_coil_cannot_frost():
    """Dry air (-19 degC, 60 %): no deposition even at the rated load, and EnergyPlus still takes 0.875.

    That loss is not tied to a deposition, so there is none for the load to
    scale: the constant form keeps it at every load. The moisture form takes
    nothing there at any load.
    """
    T_out, RH = -19.0, 0.6
    reference = CONSTANT.frost_state(T_out, RH)
    assert reference.humidity_ratio_difference == 1e-6 and reference.capacity_multiplier == pytest.approx(
        0.875, abs=1e-3
    )
    for ratio in (1.0, 0.6, 0.2):
        assert LOAD_ONLY.frost_state(T_out, RH, load_ratio=ratio).capacity_multiplier == reference.capacity_multiplier
        assert (
            not DefrostModel(frost_loss="moisture", load_weighted=True).frost_state(T_out, RH, load_ratio=ratio).active
        )


def test_boiler_with_load_weighting_alone_is_the_default_at_the_nameplate_and_in_a_dynamic_run(boilers):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        load_only = AirSourceHeatPumpBoiler(defrost=LOAD_ONLY)
    default = boilers["constant"]
    for T_tank_w, T0 in ((35.0, 2.0), (45.0, -7.0), (55.0, -15.0)):
        a = load_only.analyze_steady(T_tank_w=T_tank_w, T0=T0, Q_ref_tank=15000.0)
        b = default.analyze_steady(T_tank_w=T_tank_w, T0=T0, Q_ref_tank=15000.0)
        assert a == b or all(a[k] == b[k] or (a[k] != a[k] and b[k] != b[k]) for k in b), (T_tank_w, T0)
    # Part load: less frost than the default, more than none.
    point = {"T_tank_w": 27.5, "T0": 2.0, "Q_ref_tank": 0.54 * 0.667 * 15000.0}
    cop = [m.analyze_steady(**point)["cop_sys [-]"] for m in (default, load_only, boilers["off"])]
    assert cop[0] < cop[1] < cop[2]
    # The thermostat asks for the nameplate: every step of a dynamic run is at a load ratio of one.
    n = 12
    schedule = {
        "simulation_period_sec": n * 600,
        "dt_s": 600,
        "T_tank_w_init_C": 55.0,
        "dhw_usage_schedule": np.zeros(n),
        "T0_schedule": np.linspace(-6.0, 4.0, n),
    }
    import pandas as pd

    pd.testing.assert_frame_equal(
        load_only.analyze_dynamic(**schedule), default.analyze_dynamic(**schedule), check_exact=True
    )
