"""Frost on the outdoor coil and its defrost (``tmhp.defrost``).

The correction is EnergyPlus' ``Coil:Heating:DX:SingleSpeed`` frost adjustment
with on-demand reverse-cycle defrost (Engineering Reference, "Frost Adjustment
Factors" and "Defrost Operation"; from DOE-2.1E). The constants asserted here
were read from that text, not recalled.

Without it the air-to-air model ran 15 to 28 % high against the Daikin heating
grids below 5 degC outdoor and within 2 % above.
"""

import math

import numpy as np
import pytest

from tmhp import AirSourceHeatPump, DefrostModel
from tmhp.air_properties import air_density
from tmhp.constants import c_a
from tmhp.defrost import (
    DEFROST_FRACTION_KEY,
    DEFROST_LOAD_KEY,
    DEFROST_POWER_KEY,
    FROST_CAPACITY_FACTOR_KEY,
    FROST_POWER_FACTOR_KEY,
    FROST_TRANSITION_K,
    NO_FROST,
    RH_OUT_DEFAULT,
    RH_OUT_KEY,
    T_OUT_MAX_DEFROST_C,
    FrostState,
    apply_defrost,
    frost_free_keys,
    humidity_ratio,
    resolve_defrost,
    saturation_humidity_ratio,
)

MODEL = DefrostModel()


# ── EnergyPlus frost adjustment ───────────────────────────────────────────


def test_defaults_are_the_energyplus_limit_and_the_en14511_a2_humidity():
    assert T_OUT_MAX_DEFROST_C == 5.0 and MODEL.T_out_max == 5.0
    assert RH_OUT_DEFAULT == 0.84 and MODEL.RH_out == 0.84
    # A2: dry bulb 2 degC, wet bulb 1 degC.
    from CoolProp.HumidAirProp import HAPropsSI

    assert HAPropsSI("R", "T", 275.15, "P", 101325.0, "B", 274.15) == pytest.approx(0.84, abs=0.005)


@pytest.mark.parametrize("T_out,RH", [(2.0, 0.84), (-7.0, 0.75), (-15.0, 0.9), (5.0, 0.6)])
def test_on_demand_defrost_follows_the_published_equations(T_out, RH):
    # At and below 2 degC the default model is EnergyPlus'; up to its 5 degC
    # limit the switch (``transition_K=0``) is.
    frost = (MODEL if T_out <= 2.0 else DefrostModel(transition_K=0.0)).frost_state(T_out, RH)
    assert frost.active
    coil = 0.82 * T_out - 8.589
    assert frost.T_coil_C == pytest.approx(coil, rel=1e-12)
    dw = max(1.0e-6, humidity_ratio(T_out, RH) - saturation_humidity_ratio(coil))
    assert frost.humidity_ratio_difference == pytest.approx(dw, rel=1e-12)
    t_frac = 1.0 / (1.0 + 0.01446 / dw)
    assert frost.defrost_fraction == pytest.approx(t_frac, rel=1e-12)
    assert frost.capacity_multiplier == pytest.approx(0.875 * (1.0 - t_frac), rel=1e-12)
    assert frost.power_multiplier == pytest.approx(0.954 * (1.0 - t_frac), rel=1e-12)
    assert frost.RH_out == RH


def test_values_at_the_a2_rating_condition():
    frost = MODEL.frost_state(2.0)  # default humidity
    assert frost.T_coil_C == pytest.approx(-6.949, abs=1e-3)
    assert frost.defrost_fraction == pytest.approx(0.098, abs=2e-3)
    assert frost.capacity_multiplier == pytest.approx(0.789, abs=2e-3)
    assert frost.power_multiplier == pytest.approx(0.860, abs=2e-3)


def test_no_frost_above_the_outdoor_limit():
    assert FROST_TRANSITION_K == 3.0 and MODEL.transition_K == 3.0
    for T_out in (5.0, 5.001, 7.0):
        warm = MODEL.frost_state(T_out)
        assert not warm.active and warm.frost_share == 0.0
        assert (warm.defrost_fraction, warm.capacity_multiplier, warm.power_multiplier) == (0.0, 1.0, 1.0)
    assert MODEL.frost_state(4.999).active
    assert DefrostModel(T_out_max=2.0).frost_state(3.0).active is False
    assert DefrostModel(T_out_max=7.0).frost_state(6.0).active


def test_the_correction_grows_from_the_limit_to_the_frosting_rating_condition():
    """Frosting for a share of the runtime: the mean of the frosted and the frost-free unit."""
    full = MODEL.frost_state(2.0)  # EN 14511 A2
    assert full.frost_share == 1.0 and MODEL.frost_state(-7.0).frost_share == 1.0
    for T_out, share in [(4.25, 0.25), (3.5, 0.5), (2.75, 0.75)]:
        frost = MODEL.frost_state(T_out)
        assert frost.active and frost.frost_share == pytest.approx(share, rel=1e-12)
        dw = humidity_ratio(T_out, 0.84) - saturation_humidity_ratio(0.82 * T_out - 8.589)
        t_frac = 1.0 / (1.0 + 0.01446 / dw)
        assert frost.defrost_fraction == pytest.approx(share * t_frac, rel=1e-12)
        assert frost.capacity_multiplier == pytest.approx(1.0 - share * (1.0 - 0.875 * (1.0 - t_frac)), rel=1e-12)
        assert frost.power_multiplier == pytest.approx(1.0 - share * (1.0 - 0.954 * (1.0 - t_frac)), rel=1e-12)
        assert full.capacity_multiplier < frost.capacity_multiplier < 1.0
    # Continuous at both ends (EnergyPlus switches from 0.771 to 1 at the limit).
    assert MODEL.frost_state(5.0 - 1e-9).capacity_multiplier == pytest.approx(1.0, abs=1e-8)
    assert MODEL.frost_state(2.0 + 1e-9).capacity_multiplier == pytest.approx(full.capacity_multiplier, abs=1e-8)


def test_a_zero_transition_is_the_energyplus_switch():
    switch = DefrostModel(transition_K=0.0)
    at_limit = switch.frost_state(5.0)
    assert at_limit.active and at_limit.frost_share == 1.0
    assert at_limit.capacity_multiplier == pytest.approx(0.771, abs=1e-3)
    assert not switch.frost_state(5.001).active
    # At and below the frosting rating condition the two are the same model.
    assert switch.frost_state(2.0) == MODEL.frost_state(2.0)
    assert switch.frost_state(-7.0, 0.75) == MODEL.frost_state(-7.0, 0.75)
    for bad in (-1.0, math.nan, math.inf, True, "2"):
        with pytest.raises(ValueError):
            DefrostModel(transition_K=bad)  # type: ignore[arg-type]


def test_dry_air_leaves_only_the_frosted_coil_multipliers():
    frost = MODEL.frost_state(-10.0, 0.0)
    assert frost.humidity_ratio_difference == 1.0e-6
    assert frost.defrost_fraction == pytest.approx(1.0 / (1.0 + 0.01446 / 1.0e-6), rel=1e-12)
    assert frost.capacity_multiplier == pytest.approx(0.875, abs=1e-4)
    assert frost.power_multiplier == pytest.approx(0.954, abs=1e-4)


def test_more_humid_air_defrosts_more():
    fractions = [MODEL.frost_state(0.0, rh).defrost_fraction for rh in (0.3, 0.5, 0.7, 0.84, 1.0)]
    assert all(b > a for a, b in zip(fractions, fractions[1:], strict=False))
    assert fractions[-1] < 0.2


def test_a_given_coil_temperature_replaces_the_correlation():
    assert not MODEL.frost_state(2.0, T_coil_C=0.5).active  # coil above freezing: no frost
    cold = MODEL.frost_state(2.0, T_coil_C=-12.0)
    assert cold.T_coil_C == -12.0
    assert cold.defrost_fraction > MODEL.frost_state(2.0).defrost_fraction


def test_reverse_cycle_defrost_load():
    frost = MODEL.frost_state(-7.0)
    load = MODEL.defrost_load(frost, -7.0, 4000.0)
    assert load == pytest.approx(0.01 * frost.defrost_fraction * (7.222 + 7.0) * 4000.0 / 1.01667, rel=1e-12)
    assert MODEL.defrost_load(NO_FROST, 7.0, 4000.0) == 0.0
    # The duty whose mean over frosting and defrost is the request.
    assert MODEL.gross_duty(frost, -3000.0, load) == pytest.approx((3000.0 + load) / frost.capacity_multiplier)


@pytest.mark.parametrize("bad", [-0.1, 1.5, math.nan, True, "0.8"])
def test_humidity_outside_zero_to_one_is_an_input_error(bad):
    with pytest.raises(ValueError):
        MODEL.frost_state(0.0, bad)
    with pytest.raises(ValueError):
        DefrostModel(RH_out=bad)


def test_resolve_defrost():
    assert resolve_defrost(None) is None and resolve_defrost(False) is None
    assert resolve_defrost(True) == DefrostModel()
    custom = DefrostModel(T_out_max=3.0, RH_out=0.7)
    assert resolve_defrost(custom) is custom
    with pytest.raises(ValueError):
        resolve_defrost("on")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DefrostModel(T_out_max=math.inf)


def test_apply_defrost_averages_a_frost_free_row():
    row = {"Q [W]": 4000.0, "m [kg/s]": 0.02, "E_cmp [W]": 1000.0, "E_ou [W]": 60.0, "E_iu [W]": 20.0}
    row["E_tot [W]"] = 1080.0
    row["T [°C]"] = 40.0
    frost = FrostState(True, 0.1, 0.875 * 0.9, 0.954 * 0.9, RH_out=0.84)
    apply_defrost(
        row,
        frost,
        heat_factor=0.78,
        Q_defrost=20.0,
        rate_keys=("Q [W]", "m [kg/s]"),
        outdoor_power_keys=("E_ou [W]",),
        delivery_power_keys=("E_iu [W]",),
    )
    assert row["Q [W]"] == pytest.approx(3120.0) and row["m [kg/s]"] == pytest.approx(0.0156)
    assert row["E_cmp [W]"] == pytest.approx(1000.0 * (0.954 * 0.9 + 0.1))  # heating, then defrosting
    assert row["E_ou [W]"] == pytest.approx(60.0 * 0.954 * 0.9)
    assert row["E_iu [W]"] == pytest.approx(20.0 * 0.78)  # the indoor fan moves the delivered heat
    assert row["E_tot [W]"] == pytest.approx(row["E_cmp [W]"] + row["E_ou [W]"] + row["E_iu [W]"])
    assert row["T [°C]"] == 40.0
    assert row[DEFROST_FRACTION_KEY] == 0.1 and row[DEFROST_POWER_KEY] == pytest.approx(100.0)
    assert row[DEFROST_LOAD_KEY] == 20.0 and row[RH_OUT_KEY] == 0.84
    assert row[FROST_CAPACITY_FACTOR_KEY] == frost.capacity_multiplier
    assert row[FROST_POWER_FACTOR_KEY] == frost.power_multiplier

    plain = frost_free_keys({})
    assert plain[DEFROST_FRACTION_KEY] == 0.0 and plain[FROST_CAPACITY_FACTOR_KEY] == 1.0
    assert plain[DEFROST_POWER_KEY] == 0.0 and plain[DEFROST_LOAD_KEY] == 0.0


# ── AirSourceHeatPump ─────────────────────────────────────────────────────

A2 = dict(T0=2.0, T_a_room=20.0)


def _solve(model, Q_r_iu, **kwargs):
    return model.analyze_steady(Q_r_iu=Q_r_iu, **kwargs, verbose=False, postprocess=False)


@pytest.fixture(scope="module")
def hp():
    return AirSourceHeatPump()


@pytest.fixture(scope="module")
def frost_free():
    return AirSourceHeatPump(defrost=None)


@pytest.fixture(scope="module")
def frosted(hp):
    return _solve(hp, -3000.0, **A2)


def test_the_frost_model_is_on_by_default_and_can_be_switched_off(hp, frost_free):
    assert hp.defrost == DefrostModel()
    assert frost_free.defrost is None and AirSourceHeatPump(defrost=False).defrost is None


def test_above_the_outdoor_limit_nothing_changes(hp, frost_free):
    on, off = _solve(hp, -3000.0, T0=7.0, T_a_room=20.0), _solve(frost_free, -3000.0, T0=7.0, T_a_room=20.0)
    assert on["E_tot [W]"] == off["E_tot [W]"] and on["cop_sys [-]"] == off["cop_sys [-]"]
    assert on[DEFROST_FRACTION_KEY] == 0.0 and on[FROST_CAPACITY_FACTOR_KEY] == 1.0
    assert on["Q_ref_iu_frost_free [W]"] == on["Q_ref_iu [W]"]
    assert on["E_tot_frost_free [W]"] == on["E_tot [W]"]


def test_frosting_delivers_the_request_from_a_larger_frost_free_duty(hp, frost_free, frosted):
    frost = MODEL.frost_state(2.0)
    q_defrost = MODEL.defrost_load(frost, 2.0, hp.hp_capacity)
    gross = (3000.0 + q_defrost) / frost.capacity_multiplier
    assert frosted["converged"] and frosted["capacity_clamped"] is None
    assert frosted["Q_ref_iu [W]"] == pytest.approx(3000.0, rel=1e-9)
    assert frosted["Q_ref_iu_frost_free [W]"] == pytest.approx(gross, rel=1e-6)
    assert frosted[DEFROST_FRACTION_KEY] == pytest.approx(frost.defrost_fraction, rel=1e-12)
    assert frosted[DEFROST_LOAD_KEY] == pytest.approx(q_defrost, rel=1e-12)
    assert frosted[RH_OUT_KEY] == 0.84

    # The frost-free machine at that duty, averaged as the module says.
    steady = _solve(frost_free, -gross, **A2)
    t_frac, ipm = frost.defrost_fraction, frost.power_multiplier
    assert frosted["E_tot_frost_free [W]"] == pytest.approx(steady["E_tot [W]"], rel=1e-9)
    assert frosted["E_cmp [W]"] == pytest.approx(steady["E_cmp [W]"] * (ipm + t_frac), rel=1e-9)
    assert frosted["E_ou_fan [W]"] == pytest.approx(steady["E_ou_fan [W]"] * ipm, rel=1e-9)
    assert frosted["E_iu_fan [W]"] == pytest.approx(steady["E_iu_fan [W]"] * 3000.0 / gross, rel=1e-9)
    assert frosted[DEFROST_POWER_KEY] == pytest.approx(steady["E_cmp [W]"] * t_frac, rel=1e-9)
    assert frosted["T_ref_evap_sat [°C]"] == steady["T_ref_evap_sat [°C]"]  # the running machine's state


def test_frost_lowers_the_cop_by_about_a_fifth(frost_free, frosted):
    steady = _solve(frost_free, -3000.0, **A2)
    ratio = frosted["cop_sys [-]"] / steady["cop_sys [-]"]
    assert 0.70 < ratio < 0.85
    assert frosted["E_tot [W]"] > steady["E_tot [W]"]


def test_the_frosted_row_keeps_the_cycle_in_balance(frosted):
    r = frosted
    work = r["m_dot_ref [kg/s]"] * (r["h_ref_cmp_out [J/kg]"] - r["h_ref_cmp_in [J/kg]"])
    assert r["Q_ref_iu [W]"] - r["Q_ref_ou [W]"] == pytest.approx(work, rel=1e-9)
    assert r["E_tot [W]"] == pytest.approx(r["E_cmp [W]"] + r["E_iu_fan [W]"] + r["E_ou_fan [W]"], rel=1e-12)
    assert r["cop_ref [-]"] == pytest.approx(r["Q_ref_iu [W]"] / r["E_cmp [W]"], rel=1e-12)
    delivery = r["Q_a_iu_out [W]"] - r["Q_a_iu_in [W]"]
    assert r["cop_sys [-]"] == pytest.approx(delivery / r["E_tot [W]"], rel=1e-12)
    # The room receives the coil duty plus the indoor fan's heat.
    assert delivery == pytest.approx(r["Q_ref_iu [W]"] + r["E_iu_fan [W]"], abs=1e-6)
    rho_iu = air_density(r["T_iu_a_in [°C]"])  # the room air the indoor fan moves
    sensible = c_a * rho_iu * r["dV_iu_a [m3/s]"] * (r["T_iu_a_out [°C]"] - r["T_iu_a_in [°C]"])
    assert delivery == pytest.approx(sensible, abs=1e-6)


def test_drier_outdoor_air_costs_less(hp, frosted):
    dry = _solve(hp, -3000.0, **A2, RH_out=0.4)
    humid = _solve(hp, -3000.0, **A2, RH_out=1.0)
    assert dry[DEFROST_FRACTION_KEY] < frosted[DEFROST_FRACTION_KEY] < humid[DEFROST_FRACTION_KEY]
    assert dry["cop_sys [-]"] > frosted["cop_sys [-]"] > humid["cop_sys [-]"]
    assert dry[RH_OUT_KEY] == 0.4
    with pytest.raises(ValueError):
        _solve(hp, -3000.0, **A2, RH_out=84.0)  # a fraction, not percent


def test_frost_lowers_the_capacity_ceiling(hp, frost_free):
    cold = dict(T0=-15.0, T_a_room=20.0)
    on, off = _solve(hp, -6000.0, **cold), _solve(frost_free, -6000.0, **cold)
    assert on["capacity_clamped"] == "max" and off["capacity_clamped"] == "max"
    frost = MODEL.frost_state(-15.0)
    q_defrost = MODEL.defrost_load(frost, -15.0, hp.hp_capacity)
    assert on["Q_ref_iu_frost_free [W]"] == pytest.approx(off["Q_ref_iu [W]"], rel=1e-9)
    assert on["Q_ref_iu [W]"] == pytest.approx(frost.capacity_multiplier * off["Q_ref_iu [W]"] - q_defrost, rel=1e-9)
    assert on["Q_request [W]"] == -6000.0
    assert on["Q_unmet [W]"] == pytest.approx(6000.0 - on["Q_ref_iu [W]"], rel=1e-12)
    assert on["Q_max [W]"] == pytest.approx(on["Q_ref_iu [W]"], rel=1e-12)


def test_max_capacity_is_the_ceiling_analyze_steady_delivers_under_frost(hp, frost_free):
    mild = dict(T0=2.0, T_a_room=20.0)
    grid = dict(grid_points=7, rel_tol=5e-2)
    ceiling, steady = hp.max_capacity(**mild, **grid), frost_free.max_capacity(**mild, **grid)
    frost = MODEL.frost_state(2.0)
    q_defrost = MODEL.defrost_load(frost, 2.0, hp.hp_capacity)
    assert ceiling["Q_max_frost_free [W]"] == steady["Q_max [W]"] == steady["Q_max_frost_free [W]"]
    assert ceiling["Q_max [W]"] == pytest.approx(frost.capacity_multiplier * steady["Q_max [W]"] - q_defrost, rel=1e-9)
    assert ceiling["defrost_fraction [-]"] == frost.defrost_fraction and steady["defrost_fraction [-]"] == 0.0
    assert ceiling["cop_sys [-]"] < steady["cop_sys [-]"]
    # A request just below it is met; drier air raises it; cooling has no frost.
    met = _solve(hp, -0.98 * ceiling["Q_max [W]"], **mild)
    assert met["capacity_clamped"] is None and met["Q_unmet [W]"] == 0.0
    assert hp.max_capacity(**mild, **grid, RH_out=0.3)["Q_max [W]"] > ceiling["Q_max [W]"]
    cooling = hp.max_capacity(T0=35.0, T_a_room=27.0, mode="cooling", **grid)
    assert cooling["Q_max_frost_free [W]"] == cooling["Q_max [W]"]


def test_cooling_and_off_are_never_frosted(hp):
    assert hp._frost_state(3000.0, 2.0, None) is NO_FROST
    off = _solve(hp, 0.0, **A2)
    assert off[DEFROST_FRACTION_KEY] == 0.0 and off["E_tot [W]"] == 0.0


def test_exergy_post_processing_runs_on_a_frosted_row(hp):
    out = hp.analyze_steady(Q_r_iu=-3000.0, **A2, verbose=False)
    for key in ("Xc_cmp [W]", "Xc_exp [W]", "Xc_iu_hx [W]", "Xc_ou_hx [W]", "Xc_iu_fan [W]", "Xc_ou_fan [W]"):
        assert out[key] >= -1e-9, key
    assert 0.0 < out["X_eff_sys [-]"] < 1.0


def test_dynamic_run_takes_a_humidity_schedule(hp):
    load = np.array([-3000.0, -3000.0, -3000.0])
    kwargs = dict(
        simulation_period_sec=3 * 600,
        dt_s=600,
        Q_r_iu_schedule=load,
        T0_schedule=[7.0, 2.0, 2.0],
        T_a_room_schedule=[20.0] * 3,
    )
    df = hp.analyze_dynamic(**kwargs, RH_out_schedule=[0.84, 0.84, 0.5])
    assert (df["failure_reason"] == "none").all()
    assert df["Q_ref_iu [W]"].to_numpy() == pytest.approx(3000.0, rel=1e-9)
    assert df[DEFROST_FRACTION_KEY].iloc[0] == 0.0
    assert df[DEFROST_FRACTION_KEY].iloc[1] > df[DEFROST_FRACTION_KEY].iloc[2] > 0.0
    assert df["E_tot [W]"].iloc[1] > df["E_tot [W]"].iloc[2] > df["E_tot [W]"].iloc[0]
    with pytest.raises(ValueError, match="RH_out_schedule"):
        hp.analyze_dynamic(**kwargs, RH_out_schedule=[0.84, 0.84])


def test_a_cycling_unit_pays_the_defrost_loss_only_while_it_runs(hp):
    """Below the minimum continuous capacity the defrost heat loss follows the runtime.

    The reverse-cycle loss was the continuous-operation figure (20 W for this
    machine at 2 °C) whatever the runtime, so a 1 W request still drew 6.4 W
    (COP 0.16). It is now that figure times the runtime fraction: power falls
    to zero with the request and the COP tends to a finite limit.
    """
    full = hp.analyze_steady(Q_r_iu=-3000.0, T0=2.0, T_a_room=20.0, verbose=False)
    assert full["capacity_clamped"] is None
    q_continuous = full["Q_defrost [W]"]
    assert q_continuous > 10.0
    rows = {q: hp.analyze_steady(Q_r_iu=-q, T0=2.0, T_a_room=20.0, verbose=False) for q in (400.0, 100.0, 10.0, 1.0)}
    for q, r in rows.items():
        assert r["failure_reason"] == "none" and r["capacity_clamped"] == "min"
        assert r["Q_ref_iu [W]"] == pytest.approx(q, rel=1e-6)
        assert r["Q_defrost [W]"] == pytest.approx(q_continuous * r["runtime_fraction [-]"], rel=1e-6)
        assert r["Q_defrost [W]"] < q_continuous
    # power follows the request down ...
    assert rows[1.0]["E_tot [W]"] < 0.02 * rows[100.0]["E_tot [W]"]
    # ... and the COP settles instead of collapsing (it was 0.16 at 1 W)
    assert rows[1.0]["cop_sys [-]"] == pytest.approx(rows[10.0]["cop_sys [-]"], rel=0.01)
    assert rows[1.0]["cop_sys [-]"] > 2.5


def test_return_format_is_honoured_on_a_frosted_point(hp):
    import pandas as pd

    frame = hp.analyze_steady(Q_r_iu=-3000.0, T0=2.0, T_a_room=20.0, return_dict=False, verbose=False)
    assert isinstance(frame, pd.DataFrame) and len(frame) == 1
    assert frame["defrost_fraction [-]"].iloc[0] > 0.0


def test_the_steady_result_has_no_step_at_the_frost_limit(hp):
    """COP 3.49 at 5.0 degC and 4.64 at 5.5 degC: a switch, not a coil frosting over."""
    room = dict(T_a_room=20.0)
    temps = np.arange(1.0, 6.01, 0.25)
    cop = np.array([_solve(hp, -2000.0, T0=float(T), **room)["cop_sys [-]"] for T in temps])
    assert np.all(np.diff(cop) > 0.0)  # warmer air, less frost: never worse
    # No quarter-kelvin step carries more than a seventh of the whole frost penalty.
    assert np.max(np.diff(cop)) < 0.15 * (cop[-1] - cop[0])
    # Outside the transition nothing moved: full correction at 2 degC, none above 5 degC.
    switch = AirSourceHeatPump(defrost=DefrostModel(transition_K=0.0))
    for T in (-7.0, 2.0, 5.5, 7.0):
        a, b = _solve(hp, -2000.0, T0=T, **room), _solve(switch, -2000.0, T0=T, **room)
        assert a["cop_sys [-]"] == pytest.approx(b["cop_sys [-]"], rel=1e-12)
    stepped = [_solve(switch, -2000.0, T0=T, **room)["cop_sys [-]"] for T in (5.0, 5.25)]
    assert stepped[1] / stepped[0] > 1.2
