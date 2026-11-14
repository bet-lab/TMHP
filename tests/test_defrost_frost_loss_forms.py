"""Moisture-weighted forms of the fixed frost losses (``DefrostModel.frost_loss``).

The EnergyPlus on-demand multipliers keep their fixed part (0.875 on capacity,
0.954 on power) wherever the outdoor temperature is below the limit, however
little frost the air can deposit. ``frost_loss="moisture"`` and
``"accumulation"`` let that part follow the deposition. They are options: the
default stays EnergyPlus, and both leave the full-load point at the frosting
rating condition (2 degC, 84 %) and warmer, moister conditions where it was.

Everything about these two forms is in this file (and in the ``frost_loss``
branches of ``tmhp.defrost``); the load weighting, which does not depend on
them, is tested in ``test_defrost_load_weighting.py``.
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


@pytest.mark.parametrize("form", sorted(WEIGHTED))
@pytest.mark.parametrize("RH", [0.84, 0.6])
@pytest.mark.parametrize("T_out", [4.0, 2.0, -2.0, -7.0, -15.0])
def test_weighted_forms_with_the_load_weighting_shrink_continuously_with_the_load(form, RH, T_out):
    """The two options combine: from the rated load down the correction is monotone and has no step."""
    model = DefrostModel(frost_loss=form, load_weighted=True)
    assert model.frost_state(T_out, RH, load_ratio=1.0) == WEIGHTED[form].frost_state(T_out, RH)
    states = [model.frost_state(T_out, RH, load_ratio=float(r)) for r in np.round(np.arange(1.0, 0.195, -0.01), 2)]
    factor = [_cop_factor(s) for s in states]
    assert all(b >= a - 1e-12 for a, b in zip(factor, factor[1:], strict=False))
    assert max(b - a for a, b in zip(factor, factor[1:], strict=False)) < 0.01
    assert not states[-1].active or states[-1].capacity_multiplier > 0.999
