"""The tank thermostat acts inside a step, so results do not depend on dt (#96 T6).

The boilers used to decide on/off from the tank temperature at the start of a
step and then run the compressor at full capacity for the whole step. With a
15 kW machine on a 151 L tank the hysteresis band fills in about 3.5 min, so
any step longer than that overshot the upper set point: the default ASHPB
reached 140 °C at dt 3600 s, and the daily heat input moved with dt.

The fix (``dynamic_context.resolve_step_operation``) follows the thermostat
through each step at its switching events and reports the run-time fraction;
heat, power and refrigerant flow are step averages. These tests pin:

* the event model itself (``simulate_thermostat_step``), against closed forms;
* dt independence of heat input and the set-point ceiling for all three
  boilers, and water below 100 °C at dt 3600 s;
* the tank first-law balance with fractional run time, including a step in
  which a hot-water draw and a capped run overlap;
* the step-average contract of the public ``step()`` used by FMU/EnergyPlus;
* minimum on/off times and the boiling warning for heat from other sources.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

from tmhp import AirSourceHeatPumpBoiler, GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler
from tmhp.dynamic_context import (
    T_WATER_BOIL_C,
    TANK_BOILING_WARNING,
    simulate_thermostat_step,
)

from .invariants._checks import tank_energy_residual

T_SUP = 15.0


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        yield


# ---------------------------------------------------------------------------
# event model
# ---------------------------------------------------------------------------

C = 600_000.0  # J/K
FREE_ZERO = ((50.0, 0.0), (65.0, 0.0))  # no loss, no draw


def _sim(**kw):
    base = dict(
        T_start_C=50.0,
        call_start=True,
        running_prev=False,
        phase_age_s=math.inf,
        scheduled=True,
        T_lower=60.0,
        T_upper=65.0,
        dt=3600.0,
        C=C,
        free_heat=FREE_ZERO,
        Q_on=10_000.0,
    )
    base.update(kw)
    return simulate_thermostat_step(**base)


def test_event_model_stops_at_upper_bound():
    run = _sim()
    t_reach = C * (65.0 - 50.0) / 10_000.0  # 900 s
    assert run.runtime_fraction == pytest.approx(t_reach / 3600.0, rel=1e-12)
    assert run.running_start and not run.running_end and not run.call_end
    assert run.starts == 1
    assert run.T_on_mean_C == pytest.approx(57.5, rel=1e-12)
    assert run.T_on_first_C == 50.0


def test_event_model_starts_at_lower_bound_and_cycles():
    # 2 kW constant draw: off from 64 °C, reaches 60 °C after C*4/2000 = 1200 s;
    # 10 kW on gives +8 kW net, 60 -> 65 in 375 s; off 1500 s back to 60 °C
    # (t = 3075 s); on again 375 s (t = 3450 s); off for the last 150 s.
    free = ((50.0, -2000.0), (65.0, -2000.0))
    run = _sim(T_start_C=64.0, call_start=False, free_heat=free)
    assert run.runtime_fraction == pytest.approx(750.0 / 3600.0, rel=1e-9)
    assert run.starts == 2
    assert not run.running_start and not run.running_end and not run.call_end
    assert run.phase_age_end_s == pytest.approx(150.0)


def test_event_model_with_loss_matches_exponential_solution():
    # P(T) = -UA (T - 20): T(t) = T_eq + (T0 - T_eq) exp(-UA t / C), T_eq = 20 + Q/UA
    UA = 50.0
    free = ((50.0, -UA * 30.0), (65.0, -UA * 45.0))
    run = _sim(free_heat=free, T_lower=40.0)  # no restart within the step
    T_eq = 20.0 + 10_000.0 / UA
    t_reach = -C / UA * math.log((65.0 - T_eq) / (50.0 - T_eq))
    assert run.runtime_fraction * 3600.0 == pytest.approx(t_reach, rel=1e-12)


def test_event_model_minimum_times_and_cut_out():
    # Minimum run holds the compressor past the set point.
    run = _sim(min_on_time_s=1200.0)
    assert run.runtime_fraction * 3600.0 == pytest.approx(1200.0)
    # ... but never past the boiling cut-out.
    run = _sim(T_start_C=95.0, T_lower=90.0, T_upper=99.0, min_on_time_s=3600.0, free_heat=((90.0, 0.0), (99.0, 0.0)))
    assert run.runtime_fraction * 3600.0 == pytest.approx(C * (T_WATER_BOIL_C - 95.0) / 10_000.0)
    # A set point at 100 °C (the largest the constructors accept) stops there.
    run = _sim(T_upper=100.0, T_lower=95.0, T_start_C=90.0, free_heat=((90.0, -500.0), (100.0, -500.0)))
    assert run.runtime_fraction * 3600.0 == pytest.approx(C * 10.0 / 9_500.0, rel=1e-9)
    assert not run.call_end and run.starts == 1
    # Minimum rest delays the start the call asks for.
    run = _sim(running_prev=False, phase_age_s=100.0, min_off_time_s=400.0)
    assert run.runtime_fraction * 3600.0 == pytest.approx(900.0)
    assert not run.running_start and run.starts == 1


def test_event_model_fast_cycling_limit():
    """A band far narrower than the step resolves to the heat-balance duty."""
    free = ((60.0, -3000.0), (61.0, -3000.0))
    # 1e-4 K band (above the 1e-6 K bound tolerance): ~1e5 switchings an hour.
    run = _sim(T_start_C=60.0, T_lower=60.0, T_upper=60.0 + 1e-4, free_heat=free)
    assert run.runtime_fraction == pytest.approx(0.3, rel=1e-3)


def test_bound_comparisons_ignore_root_solve_noise():
    """A tank that ends a step on a bound gives the same call on every platform (#109 lesson)."""
    from tmhp.dynamic_context import TEMPERATURE_TOL_K, determine_heat_source_on_off

    for noise in (-1e-9, 0.0, 1e-9):
        assert determine_heat_source_on_off(65.0 + noise, 60.0, 65.0, True, 12.0, [(0.0, 24.0)]) is False
        assert determine_heat_source_on_off(60.0 + noise, 60.0, 65.0, False, 12.0, [(0.0, 24.0)]) is True
    assert TEMPERATURE_TOL_K <= 1e-6
    # Starting a step on the upper bound (either side of it by root-solve noise)
    # never gives a sliver of a run or a start.
    for noise in (-1e-9, 1e-9):
        run = _sim(T_start_C=65.0 + noise, call_start=True, running_prev=True, phase_age_s=600.0)
        assert run.runtime_fraction == 0.0 and run.starts == 0 and not run.running_start


def test_event_at_the_step_end_belongs_to_the_next_step():
    """An upper-bound hit within EVENT_TIME_TOL_S of the step end is not counted in this step."""
    # 10 kW, no loss, C = 6e5 J/K: 50 -> 65 °C takes exactly 900 s.
    for dt in (900.0 - 1e-9, 900.0 + 1e-9):
        run = _sim(dt=dt)
        # Still running at the end with the call already off: the next step
        # stops it at t = 0. Both sides of the boundary give the same values.
        assert run.running_end and not run.call_end and run.starts == 1, dt
        assert run.runtime_fraction == 1.0


def test_event_model_without_cycle_keeps_the_call():
    run = _sim(Q_on=0.0)
    assert run.runtime_fraction == 0.0 and run.heat_unavailable
    assert run.call_end and not run.running_end and run.starts == 0


@pytest.mark.parametrize("cls", [AirSourceHeatPumpBoiler, GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler])
@pytest.mark.parametrize("name", ["min_on_time_s", "min_off_time_s"])
@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf])
def test_min_cycle_times_validated(cls, name, bad):
    with pytest.raises(ValueError, match=name):
        cls(**{name: bad})


def test_set_point_above_boiling_is_refused():
    """#106 bounds the set points to liquid water, so the band never reaches past 100 °C."""
    with pytest.raises(ValueError, match="T_tank_w_upper_bound"):
        AirSourceHeatPumpBoiler(T_tank_w_upper_bound=110.0, T_tank_w_lower_bound=100.0)


# ---------------------------------------------------------------------------
# boilers: dt independence, set-point ceiling, < 100 °C
# ---------------------------------------------------------------------------


def _model(family: str, **kw):
    if family == "ASHPB":
        return AirSourceHeatPumpBoiler(ref="R32", **kw), {}
    # UA_tank_hx = 2000 W/K keeps the condensing approach small enough for the
    # default R410A machine to run across the 60-65 °C band (the default
    # 500 W/K has no operating point above ~56 °C, a separate defect).
    cls = GroundSourceHeatPumpBoiler if family == "GSHPB" else WaterSourceHeatPumpBoiler
    return cls(dt_s=600, t_max_s=86400.0, UA_tank_hx=2000.0, **kw), (
        {"T_source_w_schedule": "flat10"} if family == "WSHPB" else {}
    )


def _run(family: str, hours: float, dt: int, T_init: float, draw_lpm=None, **model_kw):
    model, extra = _model(family, **model_kw)
    n = int(hours * 3600 // dt)
    t_h = (np.arange(n) + 0.5) * dt / 3600.0
    draw = draw_lpm(t_h) / 60000.0 if draw_lpm is not None else np.zeros(n)
    if extra.get("T_source_w_schedule") == "flat10":
        extra = {"T_source_w_schedule": np.full(n, 10.0)}
    df = model.analyze_dynamic(
        n * dt,
        dt,
        T_init,
        draw,
        np.full(n, 5.0),
        T_sup_w_schedule=np.full(n, T_SUP),
        T_sur_schedule=np.full(n, 20.0),
        **extra,
    )
    return model, df


@pytest.mark.parametrize("family", ["ASHPB", "GSHPB", "WSHPB"])
def test_heat_input_does_not_depend_on_time_step(family):
    """1 h heat-up from 50 °C plus a 10 min draw: dt 60 s and 600 s agree.

    The draw window is aligned with the 600 s steps so both runs draw the same
    water volume (step-midpoint sampling).
    """

    def draw(h):
        return np.where((h >= 0.5) & (h < 2.0 / 3.0), 8.0, 0.0)

    out = {}
    for dt in (60, 600):
        model, df = _run(family, 1.0, dt, 50.0, draw)
        q = float(np.nansum(df["Q_ref_tank [W]"].to_numpy(float)) * dt)
        out[dt] = (q, float(df["T_tank_w [°C]"].max()))
        upper = model.T_tank_w_upper_bound
        assert (df["failure_reason"] == "none").all(), df["failure_reason"].value_counts().to_dict()
    (q60, t60), (q600, t600) = out[60], out[600]
    assert q60 > 0
    assert abs(q600 - q60) < 0.05 * q60, (q60 / 3.6e6, q600 / 3.6e6)
    assert max(t60, t600) <= upper + 2.0


@pytest.mark.parametrize("family", ["ASHPB", "GSHPB", "WSHPB"])
def test_hourly_steps_keep_tank_below_set_point_and_boiling(family):
    """dt 3600 s, no draw, from 55 °C: was 140 °C (ASHPB) and 123 °C (GSHPB/WSHPB)."""
    model, df = _run(family, 6.0, 3600, 55.0)
    T = df["T_tank_w [°C]"].to_numpy(float)
    assert T.max() <= model.T_tank_w_upper_bound + 1e-6
    assert T.max() < T_WATER_BOIL_C
    first = df.iloc[0]
    assert 0.0 < first["runtime_fraction [-]"] < 1.0
    assert first["hp_is_on"]


def test_default_gshpb_hourly_steps_stay_below_boiling():
    """The issue's case: default GSHPB (dt_s 3600) reached 122.7 °C in a day."""
    model = GroundSourceHeatPumpBoiler()
    n = 24
    df = model.analyze_dynamic(n * 3600, 3600, 55.0, np.zeros(n), np.full(n, 5.0))
    T = df["T_tank_w [°C]"].to_numpy(float)
    assert T.max() <= model.T_tank_w_upper_bound + 1e-6


# ---------------------------------------------------------------------------
# energy balance and the step-average contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", ["ASHPB", "GSHPB", "WSHPB"])
@pytest.mark.parametrize("dt", [600, 3600])
def test_tank_balance_closes_with_fractional_run_time(family, dt):
    def draw(h):
        return np.where((h > 0.5) & (h < 1.5), 6.0, 0.0) + np.where((h > 3.0) & (h < 3.3), 12.0, 0.0)

    # From 54 °C: below every family's lower bound (55 °C GSHPB/WSHPB, 60 °C ASHPB).
    model, df = _run(family, 5.0, dt, 54.0, draw)
    f = df["runtime_fraction [-]"].to_numpy(float)
    assert ((f > 0) & (f < 1)).any(), "scenario must contain partial-run steps"
    bal = tank_energy_residual(df, model.C_tank, 54.0, dt, T_ref=T_SUP)
    assert abs(bal["residual_J"]) < 0.01 * (bal["heat_J"] + bal["throughput_J"]), bal


def test_draw_and_capped_run_in_the_same_step():
    """A step that both draws hot water and reaches the set point mid-step."""
    model, df = _run("ASHPB", 1.0, 600, 61.0, lambda h: np.where(h < 0.2, 10.0, 0.0))
    row = df.iloc[0]
    assert row["dV_mix_w_out [m3/s]"] > 0
    assert 0.0 < row["runtime_fraction [-]"] < 1.0
    assert row["T_tank_w [°C]"] <= model.T_tank_w_upper_bound + 1e-6
    bal = tank_energy_residual(df.iloc[:1], model.C_tank, 61.0, 600, T_ref=T_SUP)
    assert abs(bal["residual_J"]) < 1e-4 * (bal["heat_J"] + bal["throughput_J"]), bal


def test_step_reports_step_average_rates():
    """``step()`` (FMU/EnergyPlus contract): rates are averages over the exchange."""
    model = AirSourceHeatPumpBoiler(ref="R32")
    state = model.make_initial_state(59.0)
    inputs = {"n": 0, "current_time_s": 0.0, "T0": 5.0, "dV_mix_w_out": 0.0, "T_sup_w": T_SUP, "T_sur": 20.0}
    state, row = model.step(state, inputs, 900.0)
    f = row["runtime_fraction [-]"]
    assert 0.0 < f < 1.0 and row["hp_is_on"] and row["hp_starts [-]"] == 1
    assert row["Q_ref_tank [W]"] == pytest.approx(f * model.hp_capacity, rel=1e-6)
    # COP is the running value; it equals average heat over average power.
    assert row["Q_ref_tank [W]"] / row["E_tot [W]"] == pytest.approx(row["cop_sys [-]"], rel=1e-9)
    assert row["T_tank_w [°C]"] <= model.T_tank_w_upper_bound
    # The compressor stopped at the set point; the call is off for the next exchange.
    assert not state.hp_is_on_prev and not state.hp_running_prev
    assert state.hp_phase_age_s > 0.0


def test_minimum_times_change_cycling_only_when_set():
    def draw(h):
        return np.full(h.shape, 3.0)

    _, base = _run("ASHPB", 3.0, 300, 62.0, draw)
    _, held = _run("ASHPB", 3.0, 300, 62.0, draw, min_on_time_s=600.0, min_off_time_s=600.0)
    assert int(held["hp_starts [-]"].sum()) < int(base["hp_starts [-]"].sum())
    # A 10 min minimum run of a 15 kW machine on a 151 L tank overshoots the
    # set point, but the heat pump still stops at 100 °C at the latest.
    assert held["T_tank_w [°C]"].max() < T_WATER_BOIL_C


def test_heat_from_other_sources_above_boiling_is_not_silent():
    """GSHPB with a collector loop and no high limit (#96 T9) passes 100 °C: warn."""
    from tmhp import GSHPB_STC_tank
    from tmhp.subsystems import SolarThermalCollector

    dt, n = 600, 18
    m = GSHPB_STC_tank(stc=SolarThermalCollector(preheat_start_hour=0.0), dt_s=dt, t_max_s=86400.0)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        df = m.analyze_dynamic(
            n * dt,
            dt,
            80.0,
            np.zeros(n),
            np.full(n, 25.0),
            I_DN_schedule=np.full(n, 900.0),
            I_dH_schedule=np.full(n, 120.0),
            T_sup_w_schedule=np.full(n, T_SUP),
        )
    assert df["T_tank_w [°C]"].max() > T_WATER_BOIL_C  # T9 is out of scope here
    assert not df["hp_is_on"].any()
    assert any(str(w.message) == TANK_BOILING_WARNING for w in caught)
