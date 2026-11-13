"""On/off cycling below the minimum continuous capacity (``tmhp.cycling``).

Before this model a request below what the compressor delivers at its minimum
speed was answered with the continuous minimum-speed point: the default 4 kW
R32 unit at A7/20 delivered 1,963 W to a request of 400, 800 or 1,500 W, and a
24 h heating run delivered 12 % more heat than was asked for. A real unit
cycles; the mean row now delivers the request and carries the EN 14825
degradation.
"""

import math

import numpy as np
import pytest

from tmhp import AirSourceHeatPump, CyclingDegradation
from tmhp.constants import c_a, rho_a
from tmhp.cycling import (
    CAPACITY_RATIO_KEY,
    CC_WATER_DEFAULT,
    CD_AIR_DEFAULT,
    CYCLING_LOSS_KEY,
    MIN_CAPACITY_KEY,
    PART_LOAD_FACTOR_KEY,
    RUNTIME_FRACTION_KEY,
    CyclingState,
    apply_cycling,
    report_cycling,
    resolve_cycling,
)

A7_20 = dict(T0=7.0, T_a_room=20.0)


def _solve(model, Q_r_iu, **point):
    return model.analyze_steady(Q_r_iu=Q_r_iu, **point, verbose=False, postprocess=False)


# ── EN 14825 degradation ──────────────────────────────────────────────────


def test_defaults_are_the_en14825_values():
    assert CD_AIR_DEFAULT == 0.25 and CC_WATER_DEFAULT == 0.9
    assert CyclingDegradation() == CyclingDegradation(coefficient=0.25, sink="air")


@pytest.mark.parametrize(
    "model,cr,plf",
    [
        # air: COP_cyc / COP_min = 1 - Cd (1 - CR)
        (CyclingDegradation(0.25, "air"), 0.5, 0.875),
        (CyclingDegradation(0.25, "air"), 0.2, 0.8),
        (CyclingDegradation(0.0, "air"), 0.3, 1.0),
        # water: COP_cyc / COP_min = CR / (Cc CR + 1 - Cc)
        (CyclingDegradation(0.9, "water"), 0.5, 0.5 / 0.55),
        (CyclingDegradation(0.9, "water"), 0.2, 0.2 / 0.28),
        (CyclingDegradation(1.0, "water"), 0.3, 1.0),
    ],
)
def test_part_load_factor_and_runtime_fraction(model, cr, plf):
    assert model.part_load_factor(cr) == pytest.approx(plf, rel=1e-12)
    state = model.state(cr * 2000.0, 2000.0)
    assert state.capacity_ratio == pytest.approx(cr, rel=1e-12)
    # Mean electrical input over the continuous input: CR / PLF in both forms.
    assert state.power_fraction == pytest.approx(cr / plf, rel=1e-12)
    assert model.power_fraction(cr) == pytest.approx(cr / plf, rel=1e-12)
    # Air: the loss is run time (CR / PLF). Water: the loss is the power drawn
    # while the compressor is off, and the compressor runs CR of the time.
    expected_runtime = cr if model.sink == "water" else cr / plf
    assert state.runtime_fraction == pytest.approx(expected_runtime, rel=1e-12)
    assert state.cycling


def test_water_form_is_the_off_state_power():
    """``Cc`` is one minus the off-state power over the running power (EN 14825)."""
    model = CyclingDegradation(0.9, "water")
    for cr in (0.05, 0.3, 0.8):
        # mean input = running input * [CR + (1 - Cc)(1 - CR)]
        assert model.power_fraction(cr) == pytest.approx(cr + 0.1 * (1.0 - cr), rel=1e-12)
        assert model.runtime_fraction(cr) == cr
    # Just above zero load the unit is on standby: a tenth of the running input.
    assert model.power_fraction(1e-9) == pytest.approx(0.1, rel=1e-6)
    assert model.power_fraction(0.0) == 0.0  # switched off


@pytest.mark.parametrize("model", [CyclingDegradation(0.25, "air"), CyclingDegradation(0.9, "water")])
def test_degradation_is_monotone_and_ends_at_continuous_operation(model):
    ratios = np.linspace(0.02, 1.0, 50)
    plf = np.array([model.part_load_factor(cr) for cr in ratios])
    runtime = np.array([model.runtime_fraction(cr) for cr in ratios])
    power = np.array([model.power_fraction(cr) for cr in ratios])
    assert np.all(np.diff(plf) > 0) and np.all(np.diff(runtime) > 0) and np.all(np.diff(power) > 0)
    assert np.all(power >= ratios - 1e-12) and np.all(power <= 1.0)  # never less power than loss-free
    assert np.all(runtime >= ratios - 1e-12) and np.all(runtime <= power + 1e-12)
    assert plf[-1] == 1.0 and runtime[-1] == 1.0 and power[-1] == 1.0
    # At or above the minimum capacity the unit runs continuously.
    state = model.state(2500.0, 2000.0)
    assert (state.capacity_ratio, state.part_load_factor, state.runtime_fraction) == (1.0, 1.0, 1.0)
    assert not state.cycling
    assert model.runtime_fraction(0.0) == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(coefficient=1.0, sink="air"),
        dict(coefficient=-0.1, sink="air"),
        dict(coefficient=0.0, sink="water"),
        dict(coefficient=1.1, sink="water"),
        dict(coefficient=math.nan, sink="air"),
        dict(coefficient=True, sink="air"),
        dict(coefficient=0.25, sink="brine"),
    ],
)
def test_out_of_range_coefficients_are_input_errors(kwargs):
    with pytest.raises(ValueError):
        CyclingDegradation(**kwargs)


def test_resolve_cycling():
    assert resolve_cycling(None, "air") is None
    assert resolve_cycling(0.15, "air") == CyclingDegradation(0.15, "air")
    assert resolve_cycling(0.95, "water") == CyclingDegradation(0.95, "water")
    given = CyclingDegradation(0.9, "water")
    assert resolve_cycling(given, "air") is given
    with pytest.raises(ValueError):
        AirSourceHeatPump(Cd_cycling=1.5)


def test_apply_cycling_scales_rates_with_cr_and_powers_with_the_runtime_fraction():
    row = {"Q [W]": 2000.0, "m [kg/s]": 0.01, "E [W]": 400.0, "E_fan [W]": 20.0, "E_tot [W]": 420.0}
    row.update({"T [°C]": 35.0, "flag": True})
    state = CyclingState(capacity_ratio=0.5, part_load_factor=0.875, runtime_fraction=0.5 / 0.875)
    apply_cycling(
        row,
        state,
        delivered_key="Q [W]",
        rate_keys=("Q [W]", "m [kg/s]"),
        power_keys=("E [W]",),
        delivery_power_keys=("E_fan [W]",),
    )
    assert row["Q [W]"] == 1000.0 and row["m [kg/s]"] == 0.005
    assert row["E [W]"] == pytest.approx(400.0 * 0.5 / 0.875)
    assert row["E_fan [W]"] == 10.0  # runs while heat is delivered
    assert row["E_tot [W]"] == pytest.approx(row["E [W]"] + row["E_fan [W]"])
    assert row["T [°C]"] == 35.0 and row["flag"] is True
    assert row[MIN_CAPACITY_KEY] == 2000.0
    assert row[CAPACITY_RATIO_KEY] == 0.5 and row[PART_LOAD_FACTOR_KEY] == 0.875
    assert row[RUNTIME_FRACTION_KEY] == pytest.approx(0.5 / 0.875)
    # Electricity beyond CR times the continuous power.
    assert row[CYCLING_LOSS_KEY] == pytest.approx(400.0 * (0.5 / 0.875 - 0.5))


# ── report_cycling: which rows are averaged ───────────────────────────────


def test_report_cycling_averages_only_a_running_row_on_the_speed_floor():
    def row(**changes):
        base = {"hp_is_on": True, "failure_reason": "none", "capacity_clamped": "min", "Q [W]": 2000.0}
        return {**base, "E [W]": 400.0, "E_tot [W]": 400.0, **changes}

    model = CyclingDegradation(0.25, "air")
    keys = dict(delivered_key="Q [W]", rate_keys=("Q [W]",), power_keys=("E [W]",))

    cycled = row()
    assert report_cycling(cycled, model, 1000.0, **keys) is True
    assert cycled["Q [W]"] == 1000.0 and cycled["E [W]"] == pytest.approx(400.0 * 0.5 / 0.875)
    assert cycled[MIN_CAPACITY_KEY] == 2000.0 and cycled[CAPACITY_RATIO_KEY] == 0.5

    untouched = [
        (row(), None, 1000.0),  # cycling switched off
        (row(capacity_clamped=None), model, 2000.0),  # continuous operation
        (row(capacity_clamped="max"), model, 3000.0),  # at the ceiling
        (row(), model, 2000.0),  # the request is the minimum capacity
        (row(hp_is_on=False), model, 1000.0),  # off
        (row(failure_reason="cycle_invalid"), model, 1000.0),  # failed solve
    ]
    for result, cycling, request in untouched:
        before = dict(result)
        assert report_cycling(result, cycling, request, **keys) is False
        assert {k: result[k] for k in before} == before
        assert result[CYCLING_LOSS_KEY] == 0.0
        running = before["hp_is_on"] and before["failure_reason"] == "none"
        assert result[RUNTIME_FRACTION_KEY] == (1.0 if running else 0.0)


# ── AirSourceHeatPump ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def hp():
    return AirSourceHeatPump()


@pytest.fixture(scope="module")
def continuous():
    """The same unit without the cycling model: continuous at minimum speed."""
    return _solve(AirSourceHeatPump(Cd_cycling=None), -800.0, **A7_20)


@pytest.fixture(scope="module")
def cycling(hp):
    return _solve(hp, -800.0, **A7_20)


def test_without_the_cycling_model_the_unit_over_delivers(continuous):
    assert continuous["capacity_clamped"] == "min"
    assert continuous["Q_ref_iu [W]"] > 1900.0  # asked for 800 W
    assert continuous[RUNTIME_FRACTION_KEY] == 1.0 and continuous[CAPACITY_RATIO_KEY] == 1.0
    assert continuous[MIN_CAPACITY_KEY] == pytest.approx(continuous["Q_ref_iu [W]"])
    assert continuous[CYCLING_LOSS_KEY] == 0.0 and continuous["Q_unmet [W]"] == 0.0


def test_a_request_below_the_minimum_capacity_is_delivered_exactly(cycling):
    assert cycling["converged"] and cycling["failure_reason"] == "none"
    assert cycling["capacity_clamped"] == "min"
    assert cycling["Q_ref_iu [W]"] == pytest.approx(800.0, rel=1e-12)
    assert cycling["Q_request [W]"] == -800.0 and cycling["Q_unmet [W]"] == 0.0
    assert 0.0 < cycling[CAPACITY_RATIO_KEY] < cycling[RUNTIME_FRACTION_KEY] < 1.0
    assert cycling[CAPACITY_RATIO_KEY] == pytest.approx(800.0 / cycling[MIN_CAPACITY_KEY], rel=1e-12)


def test_cycling_cop_is_the_continuous_cop_times_the_part_load_factor(cycling, continuous):
    plf = cycling[PART_LOAD_FACTOR_KEY]
    assert plf == pytest.approx(1.0 - 0.25 * (1.0 - cycling[CAPACITY_RATIO_KEY]), rel=1e-12)
    # The minimum-speed point is the continuous one (the search weighs the
    # degradation, so it may sit a few watts away from it).
    assert cycling[MIN_CAPACITY_KEY] == pytest.approx(continuous["Q_ref_iu [W]"], rel=1e-2)
    assert cycling["cop_ref [-]"] == pytest.approx(continuous["cop_ref [-]"] * plf, rel=5e-3)
    # The indoor fan is not degraded (it runs while heat is delivered), so the
    # system COP falls a little less than the refrigerant COP.
    assert continuous["cop_sys [-]"] * plf < cycling["cop_sys [-]"] < continuous["cop_sys [-]"] * (plf + 0.03)
    # Compressor and outdoor fan: the running power for the runtime fraction of
    # the time; the indoor fan for the capacity ratio.
    runtime, cr = cycling[RUNTIME_FRACTION_KEY], cycling[CAPACITY_RATIO_KEY]
    machine_running = (cycling["E_cmp [W]"] + cycling["E_ou_fan [W]"]) / runtime
    assert machine_running == pytest.approx(continuous["E_cmp [W]"] + continuous["E_ou_fan [W]"], rel=1e-2)
    assert cycling["E_iu_fan [W]"] / cr == pytest.approx(continuous["E_iu_fan [W]"], rel=2e-2)
    assert cycling[CYCLING_LOSS_KEY] == pytest.approx(machine_running * (runtime - cr), rel=1e-9)
    assert cycling["E_tot [W]"] < continuous["E_tot [W]"]


def test_the_mean_row_keeps_the_cycle_and_the_air_side_in_balance(cycling):
    r = cycling
    work = r["m_dot_ref [kg/s]"] * (r["h_ref_cmp_out [J/kg]"] - r["h_ref_cmp_in [J/kg]"])
    assert r["Q_ref_iu [W]"] - r["Q_ref_ou [W]"] == pytest.approx(work, rel=1e-9)
    assert r["E_tot [W]"] == pytest.approx(r["E_cmp [W]"] + r["E_iu_fan [W]"] + r["E_ou_fan [W]"], rel=1e-12)
    assert r["cop_ref [-]"] == pytest.approx(r["Q_ref_iu [W]"] / r["E_cmp [W]"], rel=1e-12)
    delivery = r["Q_a_iu_out [W]"] - r["Q_a_iu_in [W]"]
    assert r["cop_sys [-]"] == pytest.approx(delivery / r["E_tot [W]"], rel=1e-12)
    # The room receives the coil duty plus the indoor fan's heat, carried by the
    # mean airflow at the running machine's supply temperature.
    assert delivery == pytest.approx(r["Q_ref_iu [W]"] + r["E_iu_fan [W]"], abs=1e-6)
    sensible = c_a * rho_a * r["dV_iu_a [m3/s]"] * (r["T_iu_a_out [°C]"] - r["T_iu_a_in [°C]"])
    assert delivery == pytest.approx(sensible, abs=1e-6)
    # State points are those of the running machine.
    assert r["cmp_rpm [rpm]"] == pytest.approx(15.0 * 60.0)
    assert r["T_iu_a_out [°C]"] > 20.0


def test_loss_free_cycling_keeps_the_continuous_cop(continuous):
    ideal = _solve(AirSourceHeatPump(Cd_cycling=0.0), -800.0, **A7_20)
    assert ideal["Q_ref_iu [W]"] == pytest.approx(800.0, rel=1e-12)
    assert ideal[PART_LOAD_FACTOR_KEY] == 1.0 and ideal[CYCLING_LOSS_KEY] == 0.0
    assert ideal[RUNTIME_FRACTION_KEY] == pytest.approx(ideal[CAPACITY_RATIO_KEY], rel=1e-12)
    assert ideal["cop_sys [-]"] == pytest.approx(continuous["cop_sys [-]"], rel=1e-6)
    assert ideal["cop_ref [-]"] == pytest.approx(continuous["cop_ref [-]"], rel=1e-6)


def test_power_rises_with_the_request_through_the_minimum_capacity(hp, cycling):
    """No step where cycling ends: the mean power is continuous at ``CR = 1``."""
    floor = cycling[MIN_CAPACITY_KEY]
    loads = [0.1 * floor, 0.5 * floor, 0.98 * floor, 1.02 * floor, 1.5 * floor]
    rows = [_solve(hp, -q, **A7_20) for q in loads]
    power = [r["E_tot [W]"] for r in rows]
    assert all(b > a for a, b in zip(power, power[1:], strict=False))
    assert [r["capacity_clamped"] for r in rows] == ["min", "min", "min", None, None]
    assert [r["Q_ref_iu [W]"] for r in rows] == pytest.approx(loads, rel=1e-6)
    below, above = rows[2], rows[3]
    assert above["E_tot [W]"] / below["E_tot [W]"] == pytest.approx(1.02 / 0.98, rel=2e-2)


def test_cooling_cycles_too(hp):
    out = _solve(hp, 800.0, T0=30.0, T_a_room=27.0)
    assert out["capacity_clamped"] == "min" and out["Q_ref_iu [W]"] == pytest.approx(800.0, rel=1e-12)
    assert 0.0 < out[RUNTIME_FRACTION_KEY] < 1.0
    assert out["Q_a_iu_out [W]"] < out["Q_a_iu_in [W]"]  # the room is cooled


def test_a_request_the_unit_meets_runs_continuously(hp):
    out = _solve(hp, -3000.0, **A7_20)
    assert out["capacity_clamped"] is None
    assert out[RUNTIME_FRACTION_KEY] == 1.0 and out[CAPACITY_RATIO_KEY] == 1.0 and out[PART_LOAD_FACTOR_KEY] == 1.0
    assert math.isnan(out[MIN_CAPACITY_KEY]) and out[CYCLING_LOSS_KEY] == 0.0
    assert out["Q_request [W]"] == -3000.0 and out["Q_unmet [W]"] == 0.0


def test_a_request_above_the_ceiling_reports_the_unmet_load(hp):
    out = _solve(hp, -6000.0, T0=-15.0, T_a_room=20.0)
    assert out["capacity_clamped"] == "max" and out["failure_reason"] == "none"
    assert out["Q_request [W]"] == -6000.0
    assert out["Q_unmet [W]"] == pytest.approx(6000.0 - out["Q_ref_iu [W]"], rel=1e-12)
    assert out["Q_unmet [W]"] > 1000.0
    assert out["Q_max [W]"] == pytest.approx(out["Q_ref_iu [W]"], rel=1e-9)
    assert out["capacity_binding"] in {"compressor", "cycle", "discharge_temperature"}
    assert out[RUNTIME_FRACTION_KEY] == 1.0


def test_off_reports_no_runtime(hp):
    out = _solve(hp, 0.0, **A7_20)
    assert out[RUNTIME_FRACTION_KEY] == 0.0 and out[CAPACITY_RATIO_KEY] == 0.0
    assert out["Q_request [W]"] == 0.0 and out["Q_unmet [W]"] == 0.0


def test_exergy_post_processing_closes_on_a_cycling_row(hp):
    out = hp.analyze_steady(Q_r_iu=-800.0, **A7_20, verbose=False)
    for key in ("Xc_cmp [W]", "Xc_exp [W]", "Xc_iu_hx [W]", "Xc_ou_hx [W]", "Xc_iu_fan [W]", "Xc_ou_fan [W]"):
        assert out[key] >= -1e-9, key
    assert 0.0 < out["X_eff_sys [-]"] < 1.0


def test_dynamic_run_delivers_the_requested_heat(hp):
    """Eight 3 h steps of a mild day's heating load, 0.8-4 kW at 6-12 degC: supplied equals requested."""
    hours = np.arange(8) * 3.0
    load = -(2400.0 - 1600.0 * np.sin((hours - 9.0) / 24.0 * 2.0 * np.pi))
    df = hp.analyze_dynamic(
        simulation_period_sec=24 * 3600,
        dt_s=3 * 3600,
        Q_r_iu_schedule=load,
        T0_schedule=9.0 + 3.0 * np.sin((hours - 9.0) / 24.0 * 2.0 * np.pi),
        T_a_room_schedule=np.full(8, 20.0),
    )
    assert (df["failure_reason"] == "none").all()
    assert (df["capacity_clamped"] == "min").any()
    assert df["Q_ref_iu [W]"].sum() == pytest.approx(-load.sum(), rel=1e-9)
    assert (df["Q_unmet [W]"] == 0.0).all()
    cyc = df["capacity_clamped"] == "min"
    assert ((df.loc[cyc, RUNTIME_FRACTION_KEY] > 0) & (df.loc[cyc, RUNTIME_FRACTION_KEY] < 1)).all()
    assert (df.loc[~cyc, RUNTIME_FRACTION_KEY] == 1.0).all()


def test_loss_free_water_coefficient_at_zero_load():
    """Cc = 1 is the loss-free limit; the factor is 1 at every load, including none (was 0/0)."""
    model = CyclingDegradation(coefficient=1.0, sink="water")
    assert model.part_load_factor(0.0) == 1.0
    assert model.part_load_factor(0.3) == pytest.approx(1.0)
    state = model.state(0.0, 2000.0)
    assert state.runtime_fraction == 0.0 and state.part_load_factor == 1.0
