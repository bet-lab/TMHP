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


def test_call_at_the_cut_out_holds_or_waits():
    """The call stands (the tank below the set point) while the water is at the cut-out."""
    waits = _sim(T_start_C=54.0, T_limit_C=50.0, Q_hold=0.0)
    assert waits.runtime_fraction == 0.0 and waits.starts == 0 and waits.limit_stop and waits.call_end
    holds = _sim(T_start_C=54.0, T_limit_C=50.0, Q_hold=2_000.0)
    assert holds.runtime_fraction == pytest.approx(0.2)
    assert holds.starts == 1 and holds.running_end and holds.limit_stop
    # Held from the previous step: still running, no new start.
    again = _sim(T_start_C=54.0, T_limit_C=50.0, Q_hold=2_000.0, running_prev=True, phase_age_s=600.0)
    assert again.starts == 0 and again.runtime_fraction == pytest.approx(0.2)


def test_lumped_hold_balances_the_loss():
    """``Q_hold=None``: the tank is the coil's water, held where its loss is made up."""
    run = _sim(T_start_C=60.0, T_limit_C=60.0, free_heat=LOSS, running_prev=True, phase_age_s=600.0)
    assert run.limit_stop and run.running_end and run.starts == 0
    assert run.runtime_fraction * 10_000.0 == pytest.approx(250.0, rel=1e-9)  # the loss at 60 degC


def test_stop_reason_names_the_cut_out():
    run = _sim(T_start_C=64.0, running_prev=True, phase_age_s=0.0, min_on_time_s=1800.0, T_limit_C=67.0)
    assert in_step_stop_reason(run, 67.0) == STOP_CONDENSING_LIMIT
    assert in_step_stop_reason(run, 100.0) == STOP_BOILING_LIMIT
    assert in_step_stop_reason(_sim(), 67.0) is None


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


def test_lumped_default_run_has_no_stop_reason():
    m = _quiet(tmhp.GroundSourceHeatPumpBoiler, dt_s=600.0, t_max_s=86400.0)
    df = _quiet(m.analyze_dynamic, 6 * 600.0, 600.0, 50.0, np.zeros(6), np.full(6, 7.0))
    assert df["hp_is_on"].any() and df["hp_stop_reason"].isna().all()


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
