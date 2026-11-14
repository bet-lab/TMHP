"""The envelope stop inside a dynamic step, and the GSHPB / WSHPB result rows (#96, round 5, R5C).

(가) The in-step thermostat (:func:`tmhp.dynamic_context.simulate_thermostat_step`) takes a
cut-out ``T_limit_C`` that stops a running compressor whatever its minimum run time, and a
holding heat ``Q_hold`` for a call that stands at it. GSHPB and WSHPB pass their envelope stop
(the tank temperature at which the nameplate duty condenses on ``T_cond_max``); a multi-node
tank passes the mean temperature at which the water around the coil gets there. The
step-size invariants are in ``tests/invariants/test_known_defects.py`` (T6).

(나) ``T_cond [°C]`` is NaN on an off row (was the tank temperature), and an empty
``hp_on_schedule`` means "never" for GSHPB and WSHPB as for ASHPB (was "always").
"""

from __future__ import annotations

import contextlib
import io
import math
import warnings

import numpy as np
import pytest

import tmhp
from tmhp.dynamic_context import (
    STOP_BOILING_LIMIT,
    STOP_CONDENSING_LIMIT,
    in_step_stop_reason,
    simulate_thermostat_step,
)

C = 600_000.0  # J/K
FREE_ZERO = ((50.0, 0.0), (70.0, 0.0))  # no loss, no draw
LOSS = ((50.0, -200.0), (70.0, -300.0))  # 200 W at 50 degC, 300 W at 70 degC


def _sim(**kw):
    args = dict(
        T_start_C=56.0,
        call_start=True,
        running_prev=False,
        phase_age_s=math.inf,
        scheduled=True,
        T_lower=55.0,
        T_upper=65.0,
        dt=600.0,
        C=C,
        free_heat=FREE_ZERO,
        Q_on=10_000.0,
    )
    args.update(kw)
    return simulate_thermostat_step(**args)


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# the cut-out in the event model
# ---------------------------------------------------------------------------


def test_default_cut_out_changes_nothing():
    """Without a cut-out below the set point the run stops at the upper bound, as before."""
    run = _sim()
    assert run.runtime_fraction == pytest.approx(9.0 * C / 10_000.0 / 600.0)
    assert not run.running_end and not run.call_end and not run.limit_stop


def test_cut_out_outranks_the_minimum_run_time():
    """A minimum run past the upper bound stops at the cut-out (was: up to 100 degC)."""
    held = _sim(T_start_C=64.0, running_prev=True, phase_age_s=0.0, min_on_time_s=1800.0)
    assert held.runtime_fraction == 1.0 and not held.limit_stop  # 64 -> 74 degC in the step
    run = _sim(T_start_C=64.0, running_prev=True, phase_age_s=0.0, min_on_time_s=1800.0, T_limit_C=67.0)
    assert run.runtime_fraction == pytest.approx(3.0 * C / 10_000.0 / 600.0)
    assert not run.running_end and run.limit_stop
    # A stop at the upper bound that the cut-out coincides with is the thermostat's.
    at_bound = _sim(T_limit_C=65.0)
    assert not at_bound.limit_stop and at_bound.runtime_fraction == pytest.approx(9.0 * C / 10_000.0 / 600.0)


def test_coil_cut_out_stops_the_run_then_holds_or_waits():
    """Multi-node coil cut-out (``run_limit_s``): full duty until then, then the holding heat."""
    run = _sim(T_start_C=54.0, run_limit_s=60.0, Q_hold=2_000.0)
    # 60 s at 10 kW, then 540 s at 2 kW: 60 + 108 s of run time.
    assert run.runtime_fraction == pytest.approx((60.0 + 0.2 * 540.0) / 600.0)
    assert run.starts == 1 and run.running_end and run.limit_stop
    waits = _sim(T_start_C=54.0, run_limit_s=60.0, Q_hold=0.0)
    assert waits.runtime_fraction == pytest.approx(0.1) and not waits.running_end and waits.call_end
    # Already there (0 s) and held from the previous step: still running, no new start.
    again = _sim(T_start_C=54.0, run_limit_s=0.0, Q_hold=2_000.0, running_prev=True, phase_age_s=600.0)
    assert again.starts == 0 and again.runtime_fraction == pytest.approx(0.2)


def test_hold_ends_when_the_tank_reaches_the_upper_bound():
    """A hold that warms the tank to the set point ends the call there, not at the step end."""
    run = _sim(T_start_C=64.0, run_limit_s=0.0, Q_hold=5_000.0, running_prev=True, phase_age_s=600.0)
    # 1 K at 5 kW into 600 kJ/K: 120 s at half the nameplate duty, then off.
    assert run.runtime_fraction == pytest.approx(0.5 * 120.0 / 600.0)
    assert not run.running_end and not run.call_end and run.limit_stop


def test_lumped_hold_balances_the_loss():
    """At the tank cut-out the tank is the coil's water: held where its loss is made up."""
    run = _sim(T_start_C=60.0, T_limit_C=60.0, free_heat=LOSS, running_prev=True, phase_age_s=600.0)
    assert run.limit_stop and run.running_end and run.starts == 0
    assert run.runtime_fraction * 10_000.0 == pytest.approx(250.0, rel=1e-9)  # the loss at 60 degC


def _split(dt_total: float, parts: int, **kw):
    """Run time [s], end temperature and starts of ``parts`` equal steps (1 kW loss, 600 kJ/K)."""
    loss = ((50.0, -1000.0), (70.0, -1000.0))
    T, call, running, age, on, starts = kw.pop("T_start_C"), True, False, 1e6, 0.0, 0
    dt = dt_total / parts
    for _ in range(parts):
        r = _sim(T_start_C=T, call_start=call, running_prev=running, phase_age_s=age, dt=dt, free_heat=loss, **kw)
        T += (r.runtime_fraction * 10_000.0 - 1000.0) * dt / C
        call, running, age = r.call_end, r.running_end, r.phase_age_end_s
        on, starts = on + r.runtime_fraction * dt, starts + r.starts
    return on, T, starts


@pytest.mark.parametrize("T_start", [60.0, 60.5], ids=["at the cut-out", "above it"])
def test_tank_cut_out_does_not_depend_on_the_step(T_start):
    """PR #150 review: called at the cut-out with a 1 kW loss, a 600 s step stayed off (0 s) while
    two 300 s steps restarted in the second (33 s). Now the tank is held at the cut-out (after
    cooling to it from above), one run, whatever the split."""
    # Q_hold=0.0 as the review called it: the holding heat of a coil cut-out, not of the tank's.
    one = _split(600.0, 1, T_start_C=T_start, T_limit_C=60.0, Q_hold=0.0)
    for parts in (2, 3, 6):
        on, T_end, starts = _split(600.0, parts, T_start_C=T_start, T_limit_C=60.0, Q_hold=0.0)
        assert on == pytest.approx(one[0], abs=1e-6) and T_end == pytest.approx(one[1], abs=1e-9), parts
        assert starts == one[2] == 1
    cool = (T_start - 60.0) * C / 1000.0  # s to cool to the cut-out
    assert one[0] == pytest.approx(0.1 * (600.0 - cool))
    assert one[1] == pytest.approx(60.0, abs=1e-9)


def test_stop_reason_names_the_cut_out():
    run = _sim(T_start_C=64.0, running_prev=True, phase_age_s=0.0, min_on_time_s=1800.0, T_limit_C=67.0)
    assert in_step_stop_reason(run, 67.0) == STOP_CONDENSING_LIMIT
    assert in_step_stop_reason(run, 100.0) == STOP_BOILING_LIMIT
    assert in_step_stop_reason(_sim(), 67.0) is None
    coil = _sim(T_start_C=54.0, run_limit_s=60.0)
    assert in_step_stop_reason(coil, 100.0, 60.0) == STOP_CONDENSING_LIMIT


# ---------------------------------------------------------------------------
# GSHPB multi-node tank: the stop of the coil's water
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def top_coil_runs():
    """Default R290 GSHPB, top-node coil, 1 h from 55 degC, no draw, at 60 s and 600 s."""
    out = {}
    for dt in (60.0, 600.0):
        m = _quiet(tmhp.GroundSourceHeatPumpBoiler, tank_model="stratified", condenser_node=0, dt_s=dt, t_max_s=86400.0)
        n = int(3600 / dt)
        out[dt] = (m, _quiet(m.analyze_dynamic, n * dt, dt, 55.0, np.zeros(n), np.full(n, 7.0)))
    return out


def test_top_coil_stops_at_the_envelope_stop_and_holds(top_coil_runs):
    """Was 54 of 60 steps (60 s) off with condensing_temp_above_max and a 75 degC top node."""
    from tmhp.compressor_envelope import condensing_tank_cutout

    for dt, (m, df) in top_coil_runs.items():
        cutout = condensing_tank_cutout(m)
        assert (df["failure_reason"] == "none").all(), dt
        assert df["capacity_binding"].isna().all(), dt  # never reduced onto the limit
        assert df["T_tank_w_top [°C]"].max() == pytest.approx(cutout, abs=0.05), dt
        # One start, then the coil's water held at the stop while the mean is below the set point.
        assert df["hp_starts [-]"].sum() == 1
        held = df["hp_stop_reason"] == STOP_CONDENSING_LIMIT
        assert held.iloc[1:].all() and df.loc[held, "hp_is_on"].all()
        assert (df["T_tank_w [°C]"] < m.T_tank_w_lower_bound + 2.0).all()
    q = {dt: float(df["Q_ref_tank [W]"].sum() * dt) for dt, (_, df) in top_coil_runs.items()}
    assert q[600.0] == pytest.approx(q[60.0], rel=0.01)
    # R5H: the electricity too. The first 600 s step used to take the condensing temperature its
    # run ended on (75 degC, the limit) for the whole run from 60 degC: 4.2 % more than 60 s steps.
    e = {dt: float(df["E_tot [W]"].sum() * dt) for dt, (_, df) in top_coil_runs.items()}
    assert e[600.0] == pytest.approx(e[60.0], rel=0.01)


@pytest.fixture(scope="module")
def draining_runs():
    """Top coil, 1 h from 55 degC with a 6 L/min draw: the coil's water warms while the mean falls.

    Records the envelope stop of the first step, ``(T_limit_C, run_limit_s, Q_hold)``.
    """
    out = {}
    for dt in (60.0, 600.0):
        m = _quiet(tmhp.GroundSourceHeatPumpBoiler, tank_model="stratified", condenser_node=0, dt_s=dt, t_max_s=86400.0)
        stops = []
        envelope_stop = m._envelope_stop

        def recorded(*args, envelope_stop=envelope_stop, stops=stops):
            stops.append(envelope_stop(*args))
            return stops[-1]

        m._envelope_stop = recorded
        n = int(3600 / dt)
        out[dt] = (m, _quiet(m.analyze_dynamic, n * dt, dt, 55.0, np.full(n, 1e-4), np.full(n, 7.0)), stops[0])
    return out


def test_coil_cut_out_keeps_the_full_duty_run_while_the_mean_falls(draining_runs):
    """PR #150 review: the coil cut-out was passed as the mean temperature at that moment, below
    the mean now when a draw drains the tank, so a 600 s step held from t = 0 and lost the run at
    the nameplate duty before the coil's water got there. Now it is a run time."""
    for dt, (_, df, (T_limit, tau, hold)) in draining_runs.items():
        assert df["T_tank_w [°C]"].iloc[-1] < 20.0  # the mean falls all along
        assert (df["failure_reason"] == "none").all()
        assert (df["hp_stop_reason"] == STOP_CONDENSING_LIMIT).any()
        # The tank cut-out is boiling; the coil's is a run time inside a 600 s step.
        assert T_limit == 100.0
        if dt == 60.0:
            assert math.isinf(tau) and df["runtime_fraction [-]"].iloc[0] == 1.0  # not there in 60 s
        else:
            assert 60.0 < tau < dt and hold > 0.0
            # At least the nameplate run up to the cut-out time (the tank step may cut the hold).
            assert df["runtime_fraction [-]"].iloc[0] * dt >= tau - 1e-6
    # While the envelope stop acts (the first 40 min), heat and electricity agree within 5 %.
    first = {
        dt: (
            float(df["Q_ref_tank [W]"].iloc[: int(2400 / dt)].sum() * dt),
            float(df["E_tot [W]"].iloc[: int(2400 / dt)].sum() * dt),
        )
        for dt, (_, df, _) in draining_runs.items()
    }
    assert first[600.0][0] == pytest.approx(first[60.0][0], rel=0.05)
    assert first[600.0][1] == pytest.approx(first[60.0][1], rel=0.05)


def test_draining_top_coil_does_not_depend_on_the_step(draining_runs):
    """R5H: the whole hour of the draining run, 60 s against 600 s steps.

    Was 1.911 / 2.117 kWh of electricity (+10.8 %) for 5.927 / 6.052 kWh of heat: a 600 s step
    took the condensing temperature its run ended on, its trial tank steps drew less than the
    step itself once the top cooled, and a hold could ask for more than the nameplate duty while
    the cold front arrived. A step the tank cut short was a new start in the next one (2 starts
    at 600 s for 1 at 60 s), and its top node ended above the envelope stop (72.4 degC for 70).
    """
    from tmhp.compressor_envelope import condensing_tank_cutout

    total = {}
    for dt, (m, df, _) in draining_runs.items():
        total[dt] = (float(df["Q_ref_tank [W]"].sum() * dt), float(df["E_tot [W]"].sum() * dt))
        assert df["hp_starts [-]"].sum() == 1, dt  # one run: stopped, held and run flat out again
        assert df["T_tank_w_top [°C]"].max() <= condensing_tank_cutout(m) + 0.05, dt
        ran = df["runtime_fraction [-]"] > 0.0
        # The row's condensing temperature is the run's mean, below the highest it reached.
        assert (df.loc[ran, "T_ref_cond_sat_l [°C]"] <= df.loc[ran, "T_ref_cond_sat_l_peak [°C]"] + 1e-9).all()
        assert df.loc[~ran, "T_ref_cond_sat_l_peak [°C]"].isna().all()
    # The heat keeps the 2 % of the tank's own time resolution (upwind advection: 0.3 % with the
    # tank sub-stepped twenty times finer); the electricity is inside the 5 % of invariant T6.
    assert total[600.0][0] == pytest.approx(total[60.0][0], rel=0.03)
    assert total[600.0][1] == pytest.approx(total[60.0][1], rel=0.05)


def test_a_held_step_is_not_a_stop_for_the_minimum_rest(draining_runs):
    """R5H: a step the coil's limit held is the same run in the next step.

    Was: the tank cut such a step short and the compressor counted as stopped, so the next step
    was a new start -- which a minimum rest then delayed. With ``min_off_time_s=600`` the draining
    run delivered 5.93 kWh at 60 s steps, 5.02 at 300 s and 4.55 at 600 s (1, 3 and 3 starts)."""
    _, base, _ = draining_runs[600.0]
    m = _quiet(
        tmhp.GroundSourceHeatPumpBoiler,
        tank_model="stratified",
        condenser_node=0,
        min_off_time_s=600.0,
        dt_s=600.0,
        t_max_s=86400.0,
    )
    df = _quiet(m.analyze_dynamic, 3600.0, 600.0, 55.0, np.full(6, 1e-4), np.full(6, 7.0))
    assert df["hp_starts [-]"].sum() == 1
    assert (df["runtime_fraction [-]"] > 0.0).all()  # no step waited
    assert df["Q_ref_tank [W]"].sum() == pytest.approx(base["Q_ref_tank [W]"].sum(), rel=1e-9)
    assert df["E_tot [W]"].sum() == pytest.approx(base["E_tot [W]"].sum(), rel=1e-9)


def test_lumped_default_run_has_no_stop_reason():
    m = _quiet(tmhp.GroundSourceHeatPumpBoiler, dt_s=600.0, t_max_s=86400.0)
    df = _quiet(m.analyze_dynamic, 6 * 600.0, 600.0, 50.0, np.zeros(6), np.full(6, 7.0))
    assert df["hp_is_on"].any() and df["hp_stop_reason"].isna().all()


# ---------------------------------------------------------------------------
# ASHPB (single-node tank): the same connection (R5K)
# ---------------------------------------------------------------------------


def _ashpb_run(model, T_init, n=12, dt=600.0, **schedules):
    return _quiet(model.analyze_dynamic, n * dt, dt, T_init, np.zeros(n), np.full(n, 7.0), **schedules)


@pytest.fixture(scope="module")
def ashpb_min_run():
    """R32 ASHPB (envelope stop 60 degC), 53-58 degC band, 30 min minimum run, 2 h from 52 degC."""
    m = _quiet(
        tmhp.AirSourceHeatPumpBoiler,
        ref="R32",
        min_on_time_s=1800.0,
        T_tank_w_lower_bound=53.0,
        T_tank_w_upper_bound=58.0,
    )
    return m, _ashpb_run(m, 52.0)


def test_ashpb_minimum_run_time_stops_at_the_envelope_stop(ashpb_min_run):
    """Was: the run went on to 66.2 degC and ended in a ``condensing_temp_above_max`` step."""
    from tmhp.compressor_envelope import condensing_tank_cutout

    m, df = ashpb_min_run
    cutout = condensing_tank_cutout(m)
    assert cutout == pytest.approx(60.0, abs=1e-6) and m.T_tank_w_upper_bound == 58.0
    assert (df["failure_reason"] == "none").all()
    # The minimum run time carries the run past the 58 degC bound; the stop ends it.
    assert df["T_tank_w [°C]"].max() == pytest.approx(cutout, abs=0.05)
    stopped = df["hp_stop_reason"] == STOP_CONDENSING_LIMIT
    assert stopped.sum() == 1 and df.loc[stopped, "hp_is_on"].all()
    step = int(np.flatnonzero(stopped.to_numpy())[0])
    assert 0.0 < df["runtime_fraction [-]"].iloc[step] < 1.0  # stopped inside the step
    # A normal end of the run: the compressor rests afterwards, nothing is reported as failed.
    assert not df["hp_is_on"].iloc[step + 1 :].any()
    assert df["hp_stop_reason"].iloc[step + 1 :].isna().all()


def test_ashpb_stop_does_not_depend_on_the_step(ashpb_min_run):
    m, coarse = ashpb_min_run
    fine = _ashpb_run(m, 52.0, n=120, dt=60.0)
    heat = [float(df["Q_ref_tank [W]"].sum() * dt) for df, dt in ((fine, 60.0), (coarse, 600.0))]
    assert heat[1] == pytest.approx(heat[0], rel=0.02)
    assert fine["T_tank_w [°C]"].max() == pytest.approx(coarse["T_tank_w [°C]"].max(), abs=0.05)
    assert (fine["hp_stop_reason"] == STOP_CONDENSING_LIMIT).sum() == 1


def test_ashpb_default_run_has_no_stop_reason_and_the_column_is_on_every_row():
    """Without a minimum run time the thermostat's own bound ends the run: values as before."""
    m = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32")
    df = _ashpb_run(m, 50.0, n=6)
    assert df["hp_is_on"].any() and not df["hp_is_on"].all()
    assert "hp_stop_reason" in df.columns and df["hp_stop_reason"].isna().all()
    assert df["T_tank_w [°C]"].max() <= m.T_tank_w_upper_bound + 0.05
    # No condensing limit: no envelope stop either (the boiling point is the cut-out).
    free = _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32", min_on_time_s=1800.0, T_cond_max=None)
    df = _ashpb_run(free, 59.0, n=2)
    assert df["hp_stop_reason"].isna().all() and df["T_tank_w [°C]"].max() > 66.0


def test_ashpb_scenario_classes_take_the_same_path():
    """The PV/ESS and collector variants inherit ``step``: the stop and its column are theirs too."""
    from tmhp.subsystems import PhotovoltaicSystem, SolarThermalCollector

    variants = {
        "ASHPB_PV_ESS": dict(pv=PhotovoltaicSystem()),
        "ASHPB_STC_preheat": dict(stc=SolarThermalCollector()),
        "ASHPB_STC_tank": dict(stc=SolarThermalCollector()),
    }
    for name, extra in variants.items():
        m = _quiet(getattr(tmhp, name), ref="R32", min_on_time_s=1800.0, **extra)
        # At night: no collector heat, so the tank's peak is the heat pump's.
        df = _ashpb_run(m, 54.0, n=6, I_DN_schedule=np.zeros(6), I_dH_schedule=np.zeros(6))
        assert (df["failure_reason"] == "none").all(), name
        assert (df["hp_stop_reason"] == STOP_CONDENSING_LIMIT).sum() == 1, name
        assert df["T_tank_w [°C]"].max() <= m.T_tank_w_upper_bound + 0.05, name


# ---------------------------------------------------------------------------
# (나) GSHPB / WSHPB result rows
# ---------------------------------------------------------------------------

SOURCE_BOILERS = {
    "GSHPB": (tmhp.GroundSourceHeatPumpBoiler, dict(dt_s=600.0, t_max_s=86400.0)),
    "WSHPB": (tmhp.WaterSourceHeatPumpBoiler, {}),
}


@pytest.mark.parametrize("family", list(SOURCE_BOILERS))
def test_condensing_temperature_is_undefined_on_an_off_row(family):
    """Was the tank temperature on an off row; NaN as every quantity of a stopped compressor."""
    cls, kw = SOURCE_BOILERS[family]
    m = _quiet(cls, **kw)
    df = _quiet(m.analyze_dynamic, 2 * 600.0, 600.0, 40.0, np.zeros(2), np.full(2, 10.0))
    off = _quiet(m.analyze_dynamic, 3 * 600.0, 600.0, 62.0, np.zeros(3), np.full(3, 10.0))
    assert df["hp_is_on"].all() and not off["hp_is_on"].any()
    assert off["T_cond [°C]"].isna().all()
    # Running rows keep the bubble point of the cycle.
    assert np.allclose(df["T_cond [°C]"], df["T_ref_cond_sat_l [°C]"])
    steady = _quiet(m.analyze_steady, T_tank_w=45.0, T_source=10.0, Q_ref_tank=0.0)
    assert not steady["hp_is_on"] and math.isnan(steady["T_cond [°C]"])


@pytest.mark.parametrize("family", list(SOURCE_BOILERS))
def test_empty_schedule_never_runs(family):
    """``hp_on_schedule=[]`` has no window: never scheduled, as ASHPB reads it (was "always")."""
    cls, kw = SOURCE_BOILERS[family]
    never = _quiet(cls, hp_on_schedule=[], **kw)
    default = _quiet(cls, hp_on_schedule=None, **kw)
    assert never.hp_on_schedule == [] and default.hp_on_schedule == [(0.0, 24.0)]
    df = _quiet(never.analyze_dynamic, 3 * 600.0, 600.0, 40.0, np.zeros(3), np.full(3, 10.0))
    assert not df["hp_is_on"].any() and (df["E_tot [W]"] == 0.0).all()
    on = _quiet(default.analyze_dynamic, 3 * 600.0, 600.0, 40.0, np.zeros(3), np.full(3, 10.0))
    assert on["hp_is_on"].all()


def test_empty_schedule_reads_the_same_for_every_boiler():
    ashpb = _quiet(tmhp.AirSourceHeatPumpBoiler, hp_on_schedule=[])
    df = _quiet(ashpb.analyze_dynamic, 2 * 600.0, 600.0, 40.0, np.zeros(2), np.full(2, 7.0))
    assert not df["hp_is_on"].any()
