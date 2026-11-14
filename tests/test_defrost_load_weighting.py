"""Load weighting of the frost adjustment (``DefrostModel.load_weighted``).

The EnergyPlus correction is a function of the outdoor temperature and
humidity alone: a unit at a third of its load is corrected like one flat out.
Its coil-temperature correlation, ``0.82 T_out - 8.589``, is that of a unit
running flat out. With ``load_weighted`` the difference between the air and
the coil is taken in proportion to the load ratio, and the correction follows
the frost drive that is left.

* At a load ratio of one the result is EnergyPlus, bit for bit, at every
  outdoor state.
* The load ratio is the frost-free duty over what the correlation's unit
  delivers at that outdoor temperature -- the nameplate times
  ``(0.18 T + 8.589) / (0.18 * 7 + 8.589)`` -- not over the nameplate: the
  correlation's own temperature difference shrinks with the outdoor
  temperature, which is that unit's capacity decline.
* The correction shrinks monotonically and without a step as the load falls,
  and (at 84 % humidity) as the air gets colder at a given load ratio.
* It inherits one thing from EnergyPlus and does not hide it: in air too dry
  to frost even the rated-load coil, the fixed multipliers stay at every load.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import pytest

from tmhp import AirSourceHeatPumpBoiler
from tmhp.defrost import T_RATING_HEATING_C, DefrostModel, humidity_ratio, saturation_humidity_ratio

ENERGYPLUS = DefrostModel(load_weighted=False)
LOAD_WEIGHTED = DefrostModel()  # the default
LOADS = np.round(np.arange(1.0, 0.195, -0.01), 2)


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _cop_factor(frost) -> float:
    return frost.capacity_multiplier / (frost.power_multiplier + frost.defrost_fraction)


def _build(**kwargs) -> AirSourceHeatPumpBoiler:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return AirSourceHeatPumpBoiler(**kwargs)


@pytest.fixture(scope="module")
def boilers() -> dict[str, AirSourceHeatPumpBoiler]:
    return {
        "off": _build(defrost=False),
        "energyplus": _build(defrost=ENERGYPLUS),
        "load": _build(defrost=LOAD_WEIGHTED),
    }


def test_the_option_is_checked():
    assert LOAD_WEIGHTED.load_weighted is True
    assert ENERGYPLUS.load_weighted is False and DefrostModel(load_weighted=True) == LOAD_WEIGHTED
    with pytest.raises(ValueError, match="load_weighted must be True or False"):
        DefrostModel(load_weighted=1)
    with pytest.raises(ValueError, match="load_ratio"):
        LOAD_WEIGHTED.frost_state(0.0, load_ratio=-0.1)
    # Without the option a load ratio is ignored.
    assert ENERGYPLUS.frost_state(-7.0, load_ratio=0.3) == ENERGYPLUS.frost_state(-7.0)


# ---------------------------------------------------------------------------
# at a load ratio of one: EnergyPlus
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("RH", [1.0, 0.84, 0.6, 0.3, 0.05])
def test_rated_load_is_energyplus_bit_for_bit(RH):
    """At every outdoor state -- also where the deposition is on its floor."""
    for T_out in (4.9, 4.0, 3.0, 2.0, 0.0, -2.0, -7.0, -15.0, -25.0, -40.0):
        reference = ENERGYPLUS.frost_state(T_out, RH)
        for state in (
            LOAD_WEIGHTED.frost_state(T_out, RH),
            LOAD_WEIGHTED.frost_state(T_out, RH, load_ratio=1.0),
            LOAD_WEIGHTED.frost_state(T_out, RH, load_ratio=1.7),  # more than the correlation's unit: not extrapolated
        ):
            assert state.active is reference.active
            assert state.capacity_multiplier == reference.capacity_multiplier
            assert state.power_multiplier == reference.power_multiplier
            assert state.defrost_fraction == reference.defrost_fraction


# ---------------------------------------------------------------------------
# the load ratio
# ---------------------------------------------------------------------------


def test_reference_capacity_is_the_capacity_of_the_units_the_correlation_describes():
    """The nameplate at 7 degC, falling with the correlation's own temperature difference."""
    assert T_RATING_HEATING_C == 7.0
    assert DefrostModel.reference_capacity(9000.0, 7.0) == pytest.approx(9000.0, rel=1e-12)
    for T_out, difference in ((2.0, 8.949), (-7.0, 7.329), (-15.0, 5.889)):
        assert 0.18 * T_out + 8.589 == pytest.approx(difference, abs=1e-9)
        assert DefrostModel.reference_capacity(9000.0, T_out) == pytest.approx(9000.0 * difference / 9.849, rel=1e-12)
    # Monotone in the outdoor temperature, and nothing where the correlation puts the coil above the air.
    capacities = [DefrostModel.reference_capacity(1.0, float(T)) for T in range(7, -60, -1)]
    assert all(b <= a for a, b in zip(capacities, capacities[1:], strict=False))
    assert DefrostModel.reference_capacity(1.0, -50.0) == 0.0 and DefrostModel.load_ratio(0.2, 1.0, -50.0) == 1.0


def test_load_ratio_does_not_read_a_cold_units_capacity_as_part_load():
    """0.6 of the nameplate at -15 degC is what the correlation's unit has there: a ratio of one."""
    assert DefrostModel.load_ratio(0.598 * 9000.0, 9000.0, -15.0) == pytest.approx(1.0, abs=1e-3)
    assert DefrostModel.load_ratio(0.6 * 9000.0, 9000.0, -15.0) == 1.0
    assert DefrostModel.load_ratio(0.6 * 9000.0, 9000.0, 2.0) == pytest.approx(0.6 * 9.849 / 8.949, rel=1e-12)
    # In other words: the coil sits (duty / nameplate) x 9.849 K below the air, at most the correlation's difference.
    for T_out, share in ((2.0, 0.5), (-7.0, 0.5), (-15.0, 0.4)):
        ratio = DefrostModel.load_ratio(share * 9000.0, 9000.0, T_out)
        coil = LOAD_WEIGHTED.frost_state(T_out, load_ratio=ratio).T_coil_C
        assert T_out - coil == pytest.approx(share * 9.849, rel=1e-12)
    cold_full = LOAD_WEIGHTED.frost_state_at_duty(-15.0, None, 0.6 * 9000.0, 9000.0)
    assert cold_full.load_ratio == 1.0
    assert cold_full.capacity_multiplier == ENERGYPLUS.frost_state(-15.0).capacity_multiplier


@pytest.mark.parametrize(
    ("T_out", "duty"), [(2.0, 3242.0), (-7.0, 3600.0), (2.0, 9000.0), (-15.0, 500.0), (0.0, 1800.0)]
)
def test_load_ratio_and_correction_are_solved_together(T_out, duty):
    """``frost_state_at_duty``: the ratio is that of the frost-free duty the request needs."""
    rated = 9000.0
    frost = LOAD_WEIGHTED.frost_state_at_duty(T_out, None, duty, rated)
    gross = LOAD_WEIGHTED.gross_duty(frost, duty, LOAD_WEIGHTED.defrost_load(frost, T_out, rated))
    assert frost.load_ratio == pytest.approx(DefrostModel.load_ratio(gross, rated, T_out), abs=1e-9)
    assert DefrostModel.load_ratio(duty, rated, T_out) - 1e-12 <= frost.load_ratio <= 1.0
    assert ENERGYPLUS.frost_state_at_duty(T_out, None, duty, rated) == ENERGYPLUS.frost_state(T_out)


# ---------------------------------------------------------------------------
# the correction below the rated load
# ---------------------------------------------------------------------------


def test_fixed_loss_and_defrost_time_follow_the_frost_drive():
    """-7 degC, 84 %, half the load: the rated-load deposition times the drive that is left."""
    T_out, ratio = -7.0, 0.5
    rated, part = LOAD_WEIGHTED.frost_state(T_out), LOAD_WEIGHTED.frost_state(T_out, load_ratio=ratio)
    assert part.T_coil_C == pytest.approx(T_out - ratio * (0.18 * T_out + 8.589), rel=1e-12)
    w_out = humidity_ratio(T_out, 0.84)
    drive = (w_out - saturation_humidity_ratio(part.T_coil_C)) / (w_out - saturation_humidity_ratio(rated.T_coil_C))
    assert 0.3 < drive < 0.5  # half the temperature difference, less than half the drive
    assert part.frost_drive_ratio == pytest.approx(drive, rel=1e-12) and rated.frost_drive_ratio == 1.0
    assert part.humidity_ratio_difference == pytest.approx(drive * rated.humidity_ratio_difference, rel=1e-12)
    t_frac = 1.0 / (1.0 + 0.01446 / part.humidity_ratio_difference)
    assert part.defrost_fraction == pytest.approx(t_frac, rel=1e-12)
    assert part.capacity_multiplier == pytest.approx((1.0 - drive * 0.125) * (1.0 - t_frac), rel=1e-12)
    assert part.power_multiplier == pytest.approx((1.0 - drive * (1.0 - 0.954)) * (1.0 - t_frac), rel=1e-12)


@pytest.mark.parametrize("RH", [0.84, 0.6])
@pytest.mark.parametrize("T_out", [4.0, 2.0, -2.0, -7.0, -15.0])
def test_correction_shrinks_continuously_with_the_load(RH, T_out):
    """From the rated load down: monotone, no step -- also where the coil passes 0 degC in humid air (4 degC)."""
    states = [LOAD_WEIGHTED.frost_state(T_out, RH, load_ratio=float(r)) for r in LOADS]
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


@pytest.mark.parametrize("ratio", [1.0, 0.75, 0.5])
def test_correction_is_continuous_and_monotone_in_the_outdoor_temperature_at_rating_humidity(ratio):
    """84 % humidity, a given load ratio: from 2 degC down to -25 degC colder air only takes less, and nothing steps.

    (Further down the EnergyPlus floor comes into view, see
    ``test_dry_cold_air_keeps_the_energyplus_fixed_loss_at_every_load``: at
    84 % it is reached at -39 degC, and a half-loaded coil starts to lose its
    advantage over the rated one from about -29 degC.)
    """
    temperatures = np.round(np.arange(5.5, -25.01, -0.1), 1)
    factor = [_cop_factor(LOAD_WEIGHTED.frost_state(float(T), 0.84, load_ratio=ratio)) for T in temperatures]
    assert max(abs(b - a) for a, b in zip(factor, factor[1:], strict=False)) < 0.01  # per 0.1 K (the 5 -> 2 degC onset)
    cold = [f for T, f in zip(temperatures, factor, strict=True) if T <= 2.0]
    assert all(b >= a - 1e-12 for a, b in zip(cold, cold[1:], strict=False))
    assert min(factor) == pytest.approx(_cop_factor(LOAD_WEIGHTED.frost_state(2.0, 0.84, load_ratio=ratio)), rel=1e-12)


def test_lightly_loaded_coil_does_not_frost_at_rating_humidity():
    """At 30 % of the load the coil is within 3 K of the air: under 2 % from +5 to -20 degC."""
    for T_out in np.arange(5.0, -20.01, -0.5):
        state = LOAD_WEIGHTED.frost_state(float(T_out), 0.84, load_ratio=0.3)
        assert _cop_factor(state) > 0.98, T_out
        assert state.defrost_fraction < 0.012


def test_correction_is_continuous_in_the_outdoor_temperature_at_a_given_duty():
    """A duty of half the nameplate from +5.5 to -30 degC at 84 %: no step where the load ratio reaches one."""
    rated, duty = 9000.0, 4500.0
    temperatures = np.round(np.arange(5.5, -30.01, -0.1), 1)
    states = [LOAD_WEIGHTED.frost_state_at_duty(float(T), 0.84, duty, rated) for T in temperatures]
    factor = [_cop_factor(s) for s in states]
    assert max(abs(b - a) for a, b in zip(factor, factor[1:], strict=False)) < 0.01
    # The same duty loads the coil of a colder unit more: at -25 degC it is the correlation's own state.
    assert states[0].load_ratio < states[-1].load_ratio == 1.0
    assert _cop_factor(states[-1]) == pytest.approx(_cop_factor(ENERGYPLUS.frost_state(-30.0, 0.84)), rel=1e-12)


def test_dry_cold_air_keeps_the_energyplus_fixed_loss_at_every_load():
    """Inherited from EnergyPlus, stated here rather than hidden.

    Below -18.3 degC at 60 % humidity the air cannot deposit even on the
    rated-load coil. EnergyPlus still applies its fixed multipliers there
    (the deposition sits on its floor of 1e-6): a loss that is not tied to a
    deposition, so there is none for the load to scale, and it stays at every
    load. Just above that limit a half-loaded coil has no frost and no
    correction. The COP multiplier at half load therefore *falls* from 0.997
    at -15 degC to 0.917 at -18.3 degC and below -- more correction in drier,
    colder air. It is the floor showing through: it cannot be removed while a
    load ratio of one is EnergyPlus and the correction is continuous in the
    load ratio.
    """
    RH = 0.6
    reference = ENERGYPLUS.frost_state(-19.0, RH)
    assert reference.humidity_ratio_difference == 1e-6
    assert reference.capacity_multiplier == pytest.approx(0.875, abs=1e-3)
    for ratio in (1.0, 0.6, 0.2):
        assert (
            LOAD_WEIGHTED.frost_state(-19.0, RH, load_ratio=ratio).capacity_multiplier == reference.capacity_multiplier
        )
    above = _cop_factor(LOAD_WEIGHTED.frost_state(-15.0, RH, load_ratio=0.5))
    below = _cop_factor(LOAD_WEIGHTED.frost_state(-18.5, RH, load_ratio=0.5))
    assert above == pytest.approx(0.997, abs=1e-3)
    assert below == pytest.approx(_cop_factor(ENERGYPLUS.frost_state(-18.5, RH)), rel=1e-12)
    assert below == pytest.approx(0.917, abs=1e-3)
    # Steep, not a jump: continuous across the limit.
    temperatures = np.round(np.arange(-15.0, -19.001, -0.01), 2)
    factor = [_cop_factor(LOAD_WEIGHTED.frost_state(float(T), RH, load_ratio=0.5)) for T in temperatures]
    assert max(abs(b - a) for a, b in zip(factor, factor[1:], strict=False)) < 0.02
    # At the rating humidity the floor is reached at -39 degC, and the half-loaded coil gives its
    # advantage back gradually from about -29 degC: 0.997 at -30 degC, the rated value at -39.5 degC.
    assert ENERGYPLUS.frost_state(-38.0, 0.84).humidity_ratio_difference > 1e-6
    assert ENERGYPLUS.frost_state(-39.5, 0.84).humidity_ratio_difference == 1e-6
    assert _cop_factor(LOAD_WEIGHTED.frost_state(-30.0, 0.84, load_ratio=0.5)) == pytest.approx(0.997, abs=1e-3)
    assert _cop_factor(LOAD_WEIGHTED.frost_state(-39.5, 0.84, load_ratio=0.5)) == pytest.approx(
        _cop_factor(ENERGYPLUS.frost_state(-39.5, 0.84)), rel=1e-12
    )


# ---------------------------------------------------------------------------
# through the boiler
# ---------------------------------------------------------------------------


def test_boiler_at_the_reference_capacity_and_in_a_dynamic_run_is_energyplus(boilers):
    load, default = boilers["load"], boilers["energyplus"]
    for T_tank_w, T0 in ((35.0, 2.0), (45.0, -7.0), (55.0, -15.0)):
        a = load.analyze_steady(T_tank_w=T_tank_w, T0=T0, Q_ref_tank=15000.0)
        b = default.analyze_steady(T_tank_w=T_tank_w, T0=T0, Q_ref_tank=15000.0)
        assert all(a[k] == b[k] or (a[k] != a[k] and b[k] != b[k]) for k in b), (T_tank_w, T0)
    # 0.6 of the nameplate at -15 degC: the capacity the correlation's unit has there, not a part load.
    point = {"T_tank_w": 45.0, "T0": -15.0, "Q_ref_tank": 0.6 * 15000.0}
    assert load.analyze_steady(**point)["cop_sys [-]"] == default.analyze_steady(**point)["cop_sys [-]"]
    # The thermostat asks for the nameplate: every step of a dynamic run is at a load ratio of one.
    n = 12
    schedule = {
        "simulation_period_sec": n * 600,
        "dt_s": 600,
        "T_tank_w_init_C": 55.0,
        "dhw_usage_schedule": np.zeros(n),
        "T0_schedule": np.linspace(-6.0, 4.0, n),
    }
    pd.testing.assert_frame_equal(
        load.analyze_dynamic(**schedule), default.analyze_dynamic(**schedule), check_exact=True
    )


def test_boiler_part_load_frosts_less(boilers):
    """EN 14825 point B for a 15 kW unit sized at 0.667: +2 degC, 54 % of the design load."""
    point = {"T_tank_w": 27.5, "T0": 2.0, "Q_ref_tank": 0.54 * 0.667 * 15000.0}
    full_load_form = boilers["energyplus"].analyze_steady(**point)
    weighted = boilers["load"].analyze_steady(**point)
    frost_free = boilers["off"].analyze_steady(**point)
    for result in (full_load_form, weighted):
        assert result["failure_reason"] == "none"
        assert result["Q_ref_tank [W]"] == pytest.approx(point["Q_ref_tank"], rel=1e-9)
    assert weighted["defrost_fraction [-]"] < 0.5 * full_load_form["defrost_fraction [-]"]
    assert full_load_form["cop_sys [-]"] < weighted["cop_sys [-]"] < frost_free["cop_sys [-]"]
    # The load ratio is that of the frost-free duty the row was solved at.
    ratio = DefrostModel.load_ratio(weighted["Q_ref_tank_frost_free [W]"], 15000.0, 2.0)
    assert 0.35 < ratio < 0.5
    assert weighted["frost_capacity_factor [-]"] == pytest.approx(
        LOAD_WEIGHTED.frost_state(2.0, load_ratio=ratio).capacity_multiplier, rel=1e-9
    )


@pytest.mark.parametrize("Cd_cycling", [0.9, None], ids=["cycling", "continuous"])
@pytest.mark.parametrize("T0", [2.0, -7.0])
def test_small_request_frosts_as_the_machine_that_runs_on_the_speed_floor(Cd_cycling, T0):
    """A request too small to frost by its own load ratio is met by a machine at ``Q_min`` that does.

    With a high speed floor the unit runs at 36-47 % of its nameplate for a
    request of 10 %. The load ratio of the request alone puts the coil above
    the air's frost point; the row must not come back uncorrected.
    """
    boiler = _build(defrost=LOAD_WEIGHTED, rps_min=45.0, Cd_cycling=Cd_cycling)
    frost_free = _build(defrost=False, rps_min=45.0, Cd_cycling=Cd_cycling)
    request = 1500.0
    alone = LOAD_WEIGHTED.frost_state_at_duty(T0, None, request, boiler.hp_capacity)
    assert (
        alone.capacity_multiplier > 0.999 and alone.defrost_fraction < 1e-4
    )  # by its own ratio: (as good as) no frost
    result = boiler.analyze_steady(T_tank_w=35.0, T0=T0, Q_ref_tank=request)
    reference = frost_free.analyze_steady(T_tank_w=35.0, T0=T0, Q_ref_tank=request)
    assert result["failure_reason"] == "none" and result["capacity_clamped"] == "min"
    q_min = result["Q_min [W]"]
    assert q_min == pytest.approx(reference["Q_min [W]"], rel=1e-6) and q_min > 2.0 * request
    running = LOAD_WEIGHTED.frost_state(T0, load_ratio=DefrostModel.load_ratio(q_min, boiler.hp_capacity, T0))
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


def test_off_is_the_frost_free_boiler(boilers):
    result = boilers["off"].analyze_steady(T_tank_w=45.0, T0=-7.0, Q_ref_tank=8000.0)
    assert result["cop_sys [-]"] == pytest.approx(2.666745555466922, rel=1e-6)  # before any frost correction
    assert result["defrost_fraction [-]"] == 0.0 and math.isnan(result["RH_out [-]"])


# ---------------------------------------------------------------------------
# through the air-to-air model
# ---------------------------------------------------------------------------

#: ``AirSourceHeatPump()`` (EnergyPlus form, no load weighting) heating a 20 degC room, on main 91f3fe5
#: before the load weighting was wired in: (T0, share of the nameplate) -> (cop_sys, E_tot, defrost fraction).
ASHP_ENERGYPLUS = {
    (2.0, 1.0): (3.3922343308322094, 1105.1200372307485, 0.09825873459029008),
    (2.0, 0.5): (3.9336095346190443, 512.6969706263664, 0.09825873459029008),
    (-7.0, 0.5): (3.5309779171735722, 571.4314173739375, 0.04429631693979417),
    (-15.0, 1.0): (2.4874513619548884, 1523.6402988952505, 0.01847577393553896),
}


@pytest.fixture(scope="module")
def heat_pumps():
    from tmhp import AirSourceHeatPump

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return {
            "off": AirSourceHeatPump(defrost=False),
            "energyplus": AirSourceHeatPump(defrost=ENERGYPLUS),
            "load": AirSourceHeatPump(defrost=LOAD_WEIGHTED),
        }


def _heat(model, T0: float, share: float) -> dict:
    return model.analyze_steady(Q_r_iu=-share * model.hp_capacity, T0=T0, T_a_room=20.0, verbose=False)


@pytest.mark.parametrize("point", list(ASHP_ENERGYPLUS))
def test_air_to_air_without_load_weighting_is_what_it_was(heat_pumps, point):
    result = _heat(heat_pumps["energyplus"], *point)
    for key, expected in zip(("cop_sys [-]", "E_tot [W]", "defrost_fraction [-]"), ASHP_ENERGYPLUS[point], strict=True):
        assert result[key] == pytest.approx(expected, rel=1e-6), key


def test_air_to_air_at_the_reference_capacity_is_energyplus(heat_pumps):
    """The nameplate at -7 and -15 degC is more than the correlation's unit has there: a load ratio of one."""
    for T0 in (-7.0, -15.0):
        a, b = _heat(heat_pumps["load"], T0, 1.0), _heat(heat_pumps["energyplus"], T0, 1.0)
        assert all(a[k] == b[k] or (a[k] != a[k] and b[k] != b[k]) for k in b), T0
        # ... and so is 0.6 of it at -15 degC, the capacity of the correlation's unit.
    a, b = _heat(heat_pumps["load"], -15.0, 0.6), _heat(heat_pumps["energyplus"], -15.0, 0.6)
    assert a["cop_sys [-]"] == b["cop_sys [-]"]
    # The capacity ceiling is the rated-load state under either model.
    for T0 in (2.0, -15.0):
        assert heat_pumps["load"].max_capacity(T0, mode="heating", T_a_room=20.0) == heat_pumps[
            "energyplus"
        ].max_capacity(T0, mode="heating", T_a_room=20.0)


def test_air_to_air_part_load_frosts_less(heat_pumps):
    weighted, full_load_form, frost_free = (_heat(heat_pumps[k], 2.0, 0.5) for k in ("load", "energyplus", "off"))
    assert abs(weighted["Q_ref_iu [W]"]) == pytest.approx(2000.0, rel=1e-9)
    assert weighted["defrost_fraction [-]"] < 0.6 * full_load_form["defrost_fraction [-]"]
    assert full_load_form["cop_sys [-]"] < weighted["cop_sys [-]"] < frost_free["cop_sys [-]"]
    ratio = DefrostModel.load_ratio(weighted["Q_ref_iu_frost_free [W]"], 4000.0, 2.0)
    assert weighted["frost_capacity_factor [-]"] == pytest.approx(
        LOAD_WEIGHTED.frost_state(2.0, load_ratio=ratio).capacity_multiplier, rel=1e-9
    )
    # Cooling is not touched.
    cooling = [m.analyze_steady(Q_r_iu=2000.0, T0=35.0, T_a_room=27.0, verbose=False) for m in heat_pumps.values()]
    assert cooling[0]["cop_sys [-]"] == cooling[1]["cop_sys [-]"] == cooling[2]["cop_sys [-]"]


def test_air_to_air_small_request_frosts_as_the_machine_on_the_speed_floor(heat_pumps):
    """2 % of the nameplate at 0 degC: the unit cycles at its minimum capacity, and that machine frosts."""
    weighted, frost_free = _heat(heat_pumps["load"], 0.0, 0.02), _heat(heat_pumps["off"], 0.0, 0.02)
    assert weighted["capacity_clamped"] == "min" and abs(weighted["Q_ref_iu [W]"]) == pytest.approx(80.0, rel=1e-9)
    alone = LOAD_WEIGHTED.frost_state_at_duty(0.0, None, 80.0, 4000.0)
    assert alone.capacity_multiplier > 0.999  # by its own ratio: (as good as) no frost
    running = LOAD_WEIGHTED.frost_state(0.0, load_ratio=DefrostModel.load_ratio(weighted["Q_min [W]"], 4000.0, 0.0))
    assert running.defrost_fraction > 0.01
    # (Q_min moves a little between the solves of the frost balance.)
    assert weighted["defrost_fraction [-]"] == pytest.approx(running.defrost_fraction, rel=2e-2)
    assert weighted["frost_capacity_factor [-]"] == pytest.approx(running.capacity_multiplier, rel=2e-3)
    assert weighted["cop_sys [-]"] < frost_free["cop_sys [-]"]


def test_load_weighting_is_the_only_option():
    """One switch: on (the default) or the EnergyPlus form. No other form of the correction is offered."""
    import dataclasses

    assert [f.name for f in dataclasses.fields(DefrostModel)] == [
        "T_out_max",
        "RH_out",
        "transition_K",
        "load_weighted",
    ]
    with pytest.raises(TypeError):
        DefrostModel(frost_loss="moisture")
