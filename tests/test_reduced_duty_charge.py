"""Charging a tank past the nameplate cut-out at reduced duty (#204).

``condensing_limit_control="reduce_duty"`` lets a boiler keep charging above the tank
temperature at which its nameplate duty condenses on ``T_cond_max``: the compressor runs at the
largest duty inside the limit, ``K_tank_hx * (T_reach - T)``, down to its smallest continuous
duty. The tank then approaches ``T_reach`` as a first-order lag, which the in-step thermostat
(:func:`tmhp.dynamic_context.simulate_thermostat_step`, ``duty_limit``) follows in closed form,
so the end temperature, the heat and the electricity of a charge on that limit do not depend on
the step.

That holds where the condensing limit is the limit that governs the stretch from the nameplate
cut-out to the stop. Each source state is checked for it once, with three cycles
(:func:`tmhp.compressor_envelope.condensing_stretch`); where another limit governs somewhere on
the stretch, reduced-duty charging is withheld and the step is the one ``"stop"`` solves. The
later parts of this file cover that rule, the valid tank range of the search for the smallest
duty, the stop that acted, the rest after a stop, and how far the step-size statement holds in a
tank that loses heat.

The default (``"stop"``) is the behaviour of ``tests/test_step_envelope_stop.py`` and its
neighbours, which run unchanged.
"""

from __future__ import annotations

import contextlib
import io
import math
import warnings

import numpy as np
import pytest

import tmhp
from tmhp.compressor_envelope import (
    BINDING_COMPRESSOR,
    BINDING_CONDENSING,
    BINDING_CYCLE,
    BINDING_PRESSURE_RATIO,
    WITHHELD_NO_SMALLER_DUTY,
    condensing_stretch,
    condensing_tank_cutout,
    condensing_tank_reach,
)
from tmhp.dynamic_context import (
    DUTY_MATCH_RTOL,
    STOP_BOILING_LIMIT,
    STOP_CONDENSING_LIMIT,
    T_WATER_BOIL_C,
    DutyLimit,
    in_step_stop_reason,
    merge_running_rows,
    resolve_step_operation,
    simulate_thermostat_step,
)
from tmhp.operating_limits import OperatingLimitWarning

# ---------------------------------------------------------------------------
# the event model: a compressor on the duty limit
# ---------------------------------------------------------------------------

C = 600_000.0  # J/K
Q_ON = 10_000.0  # W
#: 2 kW per kelvin below 65 degC: the nameplate duty fits up to 60 degC (the knee), the
#: smallest continuous duty (2 kW) up to 64 degC (the stop). Time constant C / K = 300 s.
LIMIT = DutyLimit(T_zero_C=65.0, K=2_000.0, Q_min=2_000.0)
TAU = C / LIMIT.K
FREE_ZERO = ((50.0, 0.0), (70.0, 0.0))


def _sim(**kw):
    args = dict(
        T_start_C=56.0,
        call_start=True,
        running_prev=False,
        phase_age_s=math.inf,
        scheduled=True,
        T_lower=55.0,
        T_upper=63.0,
        dt=3600.0,
        C=C,
        free_heat=FREE_ZERO,
        Q_on=Q_ON,
        duty_limit=LIMIT,
    )
    args.update(kw)
    return simulate_thermostat_step(**args)


def _heat(run, dt, limit=LIMIT, q_on=Q_ON):
    """Heat [J] of a step: the full-duty stretch at ``q_on``, the reduced one at the limit's mean duty."""
    full = run.runtime_fraction - run.reduced_fraction
    reduced = limit.duty(run.T_reduced_mean_C) if run.reduced_fraction > 0.0 else 0.0
    return dt * (full * q_on + run.reduced_fraction * reduced)


def test_duty_limit_geometry():
    assert LIMIT.duty(62.0) == pytest.approx(6_000.0)
    assert LIMIT.knee_C(Q_ON) == pytest.approx(60.0)
    assert LIMIT.stop_C == pytest.approx(64.0)


def test_reduced_stretch_is_the_first_order_approach():
    """56 -> 60 degC at 10 kW (240 s), then 65 - 5 exp(-t / 300 s) up to the 63 degC set point."""
    run = _sim()
    t_reduced = TAU * math.log(5.0 / 2.0)
    assert run.runtime_fraction * 3600.0 == pytest.approx(240.0 + t_reduced, rel=1e-12)
    assert run.reduced_fraction * 3600.0 == pytest.approx(t_reduced, rel=1e-12)
    assert run.T_full_mean_C == pytest.approx(58.0, abs=1e-9)
    # The stretch's heat, K (T_zero - mean T) t, is what warmed the tank from 60 to 63 degC.
    assert LIMIT.duty(run.T_reduced_mean_C) * t_reduced == pytest.approx(3.0 * C, rel=1e-12)
    assert _heat(run, 3600.0) == pytest.approx(7.0 * C, rel=1e-12)
    assert not run.running_end and not run.call_end and not run.limit_stop and run.starts == 1


def test_without_a_duty_limit_nothing_changes():
    """``duty_limit=None`` is the model as it was: no reduced stretch, the same run."""
    before = _sim(duty_limit=None, T_limit_C=60.0)
    assert before.reduced_fraction == 0.0 and before.T_reduced_mean_C is None
    assert before.runtime_fraction * 3600.0 == pytest.approx(240.0) and before.limit_stop


def test_a_set_point_below_the_knee_never_reaches_the_limit():
    run = _sim(T_upper=59.0)
    assert run.reduced_fraction == 0.0 and run.runtime_fraction * 3600.0 == pytest.approx(180.0)
    assert not run.limit_stop


def _split(parts: int, total: float = 3600.0, **kw):
    """End temperature, heat [J] and starts of ``total`` seconds in ``parts`` equal steps."""
    T, call, running, age, heat, starts = kw.pop("T_start_C", 56.0), True, False, math.inf, 0.0, 0
    free = kw.get("free_heat", FREE_ZERO)
    (T_a, P_a), (T_b, P_b) = free
    assert P_a == P_b  # a constant free heat: the tank update below is exact
    dt = total / parts
    for _ in range(parts):
        r = _sim(T_start_C=T, call_start=call, running_prev=running, phase_age_s=age, dt=dt, **kw)
        q = _heat(r, dt)
        T += (q + P_a * dt) / C
        call, running, age = r.call_end, r.running_end, r.phase_age_end_s
        heat, starts = heat + q, starts + r.starts
    return T, heat, starts


@pytest.mark.parametrize("T_upper", [63.0, 64.5], ids=["set point below the stop", "set point above the stop"])
def test_the_charge_does_not_depend_on_the_step(T_upper):
    """One 3600 s step against 6, 60 and 360 steps: the same end temperature and heat."""
    one = _split(1, T_upper=T_upper)
    assert one[0] == pytest.approx(min(T_upper, LIMIT.stop_C), abs=1e-9)
    for parts in (6, 60, 360):
        T_end, heat, starts = _split(parts, T_upper=T_upper)
        assert T_end == pytest.approx(one[0], abs=1e-6), parts
        assert heat == pytest.approx(one[1], rel=1e-9), parts
        assert starts == one[2] == 1


def test_the_stop_is_where_the_limit_leaves_the_smallest_duty():
    """A set point above the stop: the run ends at 64 degC as a protection stop, the call stands."""
    run = _sim(T_upper=64.5)
    t_reduced = TAU * math.log(5.0 / 1.0)
    assert run.runtime_fraction * 3600.0 == pytest.approx(240.0 + t_reduced, rel=1e-12)
    assert run.limit_stop and run.call_end and not run.running_end
    # With a loss the compressor holds the tank at the stop, cycling at its smallest duty.
    loss = ((50.0, -500.0), (70.0, -500.0))
    held = _sim(T_start_C=64.0, T_upper=64.5, free_heat=loss, running_prev=True, phase_age_s=600.0)
    assert held.limit_stop and held.running_end and held.starts == 0
    assert held.runtime_fraction == pytest.approx(500.0 / LIMIT.Q_min) == held.reduced_fraction
    assert held.T_reduced_mean_C == pytest.approx(64.0)


def test_a_draw_settles_the_tank_on_the_limit():
    """A 4 kW draw: the tank settles where the limit's duty equals it (63 degC), the compressor running."""
    draw = ((50.0, -4_000.0), (70.0, -4_000.0))
    run = _sim(T_upper=63.9, free_heat=draw, dt=36_000.0)
    assert run.running_end and run.call_end and not run.limit_stop
    assert run.runtime_fraction == 1.0
    T_end = 56.0 + (_heat(run, 36_000.0) - 4_000.0 * 36_000.0) / C
    assert T_end == pytest.approx(63.0, abs=1e-6)
    # From above the knee with a draw the limit cannot cover, the tank falls back onto the full duty.
    heavy = ((50.0, -12_000.0), (70.0, -12_000.0))
    back = _sim(T_start_C=62.0, T_upper=63.9, free_heat=heavy, dt=600.0, running_prev=True, phase_age_s=60.0)
    assert 0.0 < back.reduced_fraction < back.runtime_fraction == 1.0
    assert back.T_full_mean_C < 60.0 < back.T_reduced_mean_C


@pytest.mark.parametrize("T_start", [64.0, 64.3], ids=["at the stop", "above it"])
def test_a_demand_above_the_duty_at_the_stop_runs_on_along_the_limit(T_start):
    """At the stop the limit leaves 2 kW; a 3 kW draw is more than that.

    The compressor cannot hold the tank there, and it does not stay on the 2 kW of the stop
    either: as the tank cools the limit leaves more, so it runs on, continuously, and the tank
    settles where the limit's duty is the draw (63.5 degC). (Review of the first version: the
    hold kept the duty of the stop while the tank cooled, 1 kW short, and the step's heat was
    then taken from a mean temperature that duty had not produced.)
    """
    draw = ((50.0, -3_000.0), (70.0, -3_000.0))
    kw = dict(T_upper=64.5, free_heat=draw, running_prev=True, phase_age_s=600.0)
    run = _sim(T_start_C=T_start, dt=36_000.0, **kw)
    wait = max(0.0, T_start - 64.0) * C / 3_000.0  # off above the stop until the tank has cooled to it
    assert run.runtime_fraction == pytest.approx(1.0 - wait / 36_000.0, rel=1e-12) == run.reduced_fraction
    assert run.running_end and run.call_end
    assert run.limit_stop == (T_start > 64.0)  # the stop acted only while the tank was above it
    T_end = T_start + (_heat(run, 36_000.0) - 3_000.0 * 36_000.0) / C
    assert T_end == pytest.approx(63.5, abs=1e-6)
    # The same hour in one step and in 6, 60 and 360: the same tank and the same heat.
    split = dict(T_upper=64.5, free_heat=draw)
    one = _split(1, T_start_C=T_start, **split)
    for parts in (6, 60, 360):
        T_n, heat, _ = _split(parts, T_start_C=T_start, **split)
        assert T_n == pytest.approx(one[0], abs=1e-6), parts
        assert heat == pytest.approx(one[1], rel=1e-9), parts
    # A draw the stop's duty just covers is held by cycling; one it just does not is run through.
    for demand, cycles in ((1_999.0, True), (2_001.0, False)):
        r = _sim(T_start_C=64.0, dt=600.0, **{**kw, "free_heat": ((50.0, -demand), (70.0, -demand))})
        assert (r.runtime_fraction < 1.0) == cycles and r.limit_stop == cycles
        assert _heat(r, 600.0) == pytest.approx(demand * 600.0, rel=2e-3)  # the tank stays (almost) where it is


@pytest.mark.parametrize("above", [0.0, 2e-7, 9e-7], ids=["at the stop", "0.2 uK above", "0.9 uK above"])
def test_no_run_above_the_stop_or_below_the_smallest_duty(above):
    """A tank within the bound width above the stop is at it: the run starts from the stop, not above.

    Review of the fix above: a tank a fraction of a microkelvin above the stop (the last digits of
    the previous step's root solve) with a draw larger than the stop's duty ran on from where it
    was, so for a moment above the stop, at less than the smallest continuous duty.
    """
    draw = ((50.0, -2_001.0), (70.0, -2_001.0))  # 1 W more than the stop's 2 kW: the tank barely moves
    run = _sim(T_start_C=64.0 + above, T_upper=64.5, free_heat=draw, dt=1.0, running_prev=True, phase_age_s=600.0)
    assert run.runtime_fraction == 1.0 == run.reduced_fraction and run.starts == 0
    assert run.T_reduced_mean_C <= LIMIT.stop_C
    assert LIMIT.duty(run.T_reduced_mean_C) >= LIMIT.Q_min
    # Clearly above the stop the compressor is off until the draw has cooled the tank to it
    # (1 mK at 2001 W into 600 kJ/K: 0.3 s), and runs from the stop on.
    off = _sim(T_start_C=64.0 + 1e-3, T_upper=64.5, free_heat=draw, dt=0.2, running_prev=True, phase_age_s=600.0)
    assert off.runtime_fraction == 0.0 and off.limit_stop and not off.running_end
    later = _sim(T_start_C=64.0 + 1e-3, T_upper=64.5, free_heat=draw, dt=1.0, running_prev=True, phase_age_s=600.0)
    assert later.runtime_fraction == pytest.approx(1.0 - 1e-3 * C / 2_001.0, rel=1e-9)
    assert later.T_reduced_mean_C <= LIMIT.stop_C


#: A tank that loses heat in proportion to its temperature: 10 W/K to 20 degC and a 500 W draw
#: (940 W at the stop, less than the 2 kW the limit leaves there, so the compressor holds it).
LOSS_UA, LOSS_T_SUR, LOSS_DRAW = 10.0, 20.0, 500.0


def _loss(T_C):
    return -LOSS_UA * (T_C - LOSS_T_SUR) - LOSS_DRAW


def _closing_end(T0, heat_J, dt):
    """End temperature of a step by the balance the boilers close it with.

    ``C (T1 - T0) = heat + dt P(T1)``: the free heat of the whole step taken at the end
    temperature (implicit), whatever the tank did inside the step.
    """
    return (C * T0 + heat_J + dt * (LOSS_UA * LOSS_T_SUR - LOSS_DRAW)) / (C + dt * LOSS_UA)


def _cooling_time_to_the_stop(T_start):
    """Time [s] for the tank without heat to cool from ``T_start`` to the stop: ``C dT/dt = P(T)``, exactly."""
    return -C / LOSS_UA * math.log1p(LOSS_UA * (T_start - LIMIT.stop_C) / _loss(T_start))


def _steps_from_above_the_stop(dt, steps, T_start=64.5):
    """Steps of a called compressor from above the stop: (run, tank at the step's end) per step."""
    free = ((50.0, _loss(50.0)), (70.0, _loss(70.0)))
    T, call, running, age, out = T_start, True, False, math.inf, []
    for _ in range(steps):
        run = _sim(
            T_start_C=T,
            call_start=call,
            running_prev=running,
            phase_age_s=age,
            T_lower=64.2,
            T_upper=66.0,
            free_heat=free,
            dt=dt,
        )
        T = _closing_end(T, _heat(run, dt), dt)
        call, running, age = run.call_end, run.running_end, run.phase_age_end_s
        out.append((run, T))
    return out


@pytest.mark.parametrize("dt", [60.0, 600.0, 3600.0])
def test_a_hold_that_follows_cooling_from_above_the_stop_is_one_start(dt):
    """Final review: a tank 0.5 K above the stop, the call standing, cools to it and is held.

    The events have the tank lose more while it is warmer than the stop; the balance that closes
    the step takes the loss of the whole step at its end temperature. Credited with the heat of
    the events' hold, the tank ended that step above the stop (1.3e-4 K on the R32 unit, 1.2e-3 K
    here at 600 s), and the next step read that as a tank above the stop: a stop, and a second
    start. The hold of such a step has the heat its closing balance needs to end at the stop.
    """
    steps = _steps_from_above_the_stop(dt, int(round(7200.0 / dt)))
    assert sum(run.starts for run, _ in steps) == 1
    started = next(i for i, (run, _) in enumerate(steps) if run.starts)
    for i, (run, T_end) in enumerate(steps):
        if i < started:
            assert run.runtime_fraction == 0.0 and not run.running_end and run.limit_stop and T_end > LIMIT.stop_C
        else:
            # Held: the step ends at the stop, by the closing balance, and the compressor is on.
            assert T_end == pytest.approx(LIMIT.stop_C, abs=1e-9), i
            assert run.running_end and run.call_end and run.limit_stop and run.runtime_fraction > 0.0
            assert run.reduced_fraction == run.runtime_fraction and run.T_reduced_mean_C == pytest.approx(LIMIT.stop_C)
    # A step that is all hold: the loss at the stop, for the whole step.
    run, _ = steps[-1]
    assert _heat(run, dt) == pytest.approx(-_loss(LIMIT.stop_C) * dt, rel=1e-9)
    # The timers of the run that started when the tank got to the stop count from there.
    run, _ = steps[started]
    t_start = dt - run.phase_age_end_s
    assert 0.0 <= t_start < dt and run.runtime_fraction * dt < dt - t_start


def test_the_heat_of_a_hold_after_cooling_is_what_the_closing_balance_needs():
    """One 600 s step from 64.5 degC: the heat is the loss at the stop for the step, less what the tank gave up."""
    ((run, T_end),) = _steps_from_above_the_stop(600.0, 1)
    assert _heat(run, 600.0) == pytest.approx(-_loss(LIMIT.stop_C) * 600.0 - C * 0.5, rel=1e-12)
    assert T_end == pytest.approx(LIMIT.stop_C, abs=1e-12)
    # The events' own cooling takes 318.2 s; the on-time is a share of the 281.8 s left.
    t_cool = _cooling_time_to_the_stop(64.5)
    assert run.phase_age_end_s == pytest.approx(600.0 - t_cool, rel=1e-12)
    assert 0.0 < run.runtime_fraction * 600.0 < -_loss(LIMIT.stop_C) / LIMIT.Q_min * (600.0 - t_cool)
    # A tank without a temperature-dependent loss: the two balances are the same one, as before.
    flat = ((50.0, -940.0), (70.0, -940.0))
    even = _sim(T_start_C=64.5, T_lower=64.2, T_upper=66.0, free_heat=flat, dt=600.0)
    assert _heat(even, 600.0) == pytest.approx(940.0 * 600.0 - C * 0.5, rel=1e-12)


def test_a_tank_the_closing_balance_does_not_bring_to_the_stop_is_not_held_in_that_step():
    """A step that ends 0.01 s after the events' tank reaches the stop.

    By the closing balance the tank of that step is still above the stop without any heat: the
    compressor does not start in it (a start with no run time), and starts in the next. One start.
    """
    t_cool = _cooling_time_to_the_stop(64.5)
    steps = _steps_from_above_the_stop(t_cool + 0.01, 3)
    first, T_first = steps[0]
    assert first.starts == 0 and first.runtime_fraction == 0.0 and not first.running_end
    assert first.call_end and first.limit_stop
    assert 0.0 < T_first - LIMIT.stop_C < 2e-3
    assert [run.starts for run, _ in steps] == [0, 1, 0]
    assert steps[-1][1] == pytest.approx(LIMIT.stop_C, abs=1e-9)


def test_the_duty_on_the_limit_is_never_below_the_smallest_duty_to_the_last_digit():
    """At or below the stop the limit's duty is at least ``Q_min``, exactly, for any numbers.

    Review of the clamp above: the stop is ``T_zero - Q_min / K`` in floating point, and
    ``K (T_zero - stop)`` computed back from it comes out a few 1e-11 W below ``Q_min`` for most
    values (the tests above use 65 degC, 2 kW/K and 2 kW, for which it is exact). Bounding the
    temperature therefore did not bound the duty: a tank held at the stop was resolved at a duty
    below the smallest continuous one in its last digits.
    """
    import random

    rng = random.Random(204)
    short = 0
    for _ in range(300):
        limit = DutyLimit(rng.uniform(60.0, 80.0), rng.uniform(700.0, 4000.0), rng.uniform(500.0, 3500.0))
        stop = limit.stop_C
        short += limit.K * (limit.T_zero_C - stop) < limit.Q_min
        assert limit.duty(stop) >= limit.Q_min
        assert limit.duty(stop - 1e-9) >= limit.Q_min and limit.duty(stop - 5.0) > limit.Q_min
        assert limit.duty(stop + 0.5) < limit.Q_min  # above the stop the line is the line
        # Held at the stop against a loss of half the smallest duty: the stretch's duty is that duty.
        loss = ((50.0, -0.5 * limit.Q_min), (70.0, -0.5 * limit.Q_min))
        held = _sim(
            T_start_C=stop,
            T_lower=stop - 3.0,
            T_upper=stop + 0.5,
            free_heat=loss,
            dt=600.0,
            Q_on=4.0 * limit.Q_min,
            duty_limit=limit,
            running_prev=True,
            phase_age_s=600.0,
        )
        assert held.reduced_fraction == pytest.approx(0.5) and held.T_reduced_mean_C <= stop
        assert limit.duty(held.T_reduced_mean_C) >= limit.Q_min
        # Approaching the stop from below with exactly the smallest duty drawn: settles on it, not past it.
        draw = ((50.0, -limit.Q_min), (70.0, -limit.Q_min))
        settle = _sim(
            T_start_C=stop - 0.3,
            T_lower=stop - 3.0,
            T_upper=stop + 0.5,
            free_heat=draw,
            dt=36_000.0,
            Q_on=4.0 * limit.Q_min,
            duty_limit=limit,
        )
        assert settle.T_reduced_mean_C <= stop and limit.duty(settle.T_reduced_mean_C) >= limit.Q_min
    assert short > 50  # the raw line does fall short for a good part of these numbers


def test_fast_cycling_limit_stays_on_the_duty_limit():
    """A band far narrower than the step, past the knee: the fast-cycling limit balances the tank at the limit's duty."""
    loss = ((50.0, -1_000.0), (70.0, -1_000.0))
    run = _sim(T_start_C=62.0, T_lower=62.0, T_upper=62.0005, free_heat=loss, dt=36_000.0)
    assert run.reduced_fraction == pytest.approx(run.runtime_fraction)
    # The compressor makes up the loss at the limit's duty for the tank where it is (6 kW at 62 degC).
    assert _heat(run, 36_000.0) == pytest.approx(1_000.0 * 36_000.0, rel=1e-3)
    assert run.runtime_fraction == pytest.approx(1_000.0 / LIMIT.duty(62.0), rel=1e-3)
    assert run.T_reduced_mean_C <= LIMIT.stop_C and LIMIT.duty(run.T_reduced_mean_C) >= LIMIT.Q_min


def test_a_minimum_run_time_runs_on_along_the_limit():
    """Past the set point the minimum run time keeps the compressor on the limit, up to the stop."""
    run = _sim(T_upper=59.0, min_on_time_s=1800.0, dt=3600.0)
    assert run.limit_stop and not run.running_end
    assert run.runtime_fraction * 3600.0 == pytest.approx(240.0 + TAU * math.log(5.0), rel=1e-12)


def test_a_floor_at_or_above_the_running_duty_stops_at_the_knee():
    """No duty between the floor and the running duty: the stop of the default model."""
    floor_above = DutyLimit(65.0, 2_000.0, Q_min=12_000.0)
    run = _sim(T_upper=64.5, duty_limit=floor_above)
    assert run.reduced_fraction == 0.0 and run.limit_stop
    assert run.runtime_fraction * 3600.0 == pytest.approx(240.0)
    # Nothing of such a compressor is ever reported as a stretch on the limit: not a hold at the
    # knee, not the fast-cycling limit of a band at it, not a floor equal to the running duty.
    loss = ((50.0, -1_000.0), (70.0, -1_000.0))
    for kw in (
        dict(T_start_C=60.0, T_upper=64.5, running_prev=True, phase_age_s=600.0),
        dict(T_start_C=60.0, T_lower=59.9995, T_upper=60.0, dt=36_000.0),
        dict(T_start_C=59.0, T_upper=64.5, duty_limit=DutyLimit(65.0, 2_000.0, Q_min=Q_ON)),
    ):
        r = _sim(**{"free_heat": loss, "duty_limit": floor_above, **kw})
        assert r.reduced_fraction == 0.0 and r.T_reduced_mean_C is None, kw
        assert r.runtime_fraction > 0.0


def test_the_coil_cut_out_of_a_multi_node_tank_is_not_combined():
    with pytest.raises(ValueError, match="duty_limit"):
        _sim(run_limit_s=60.0)


def test_merged_row_is_the_run_time_weighted_mean():
    full = {"Q_ref_tank [W]": 10_000.0, "E_cmp [W]": 3_000.0, "E_tot [W]": 3_200.0, "cmp_rpm [rpm]": 3000.0}
    full.update({"cop_ref [-]": 10 / 3, "cop_sys [-]": 3.125, "capacity_clamped": None, "hp_is_on": True})
    reduced = {"Q_ref_tank [W]": 4_000.0, "E_cmp [W]": 1_500.0, "E_tot [W]": 1_600.0, "cmp_rpm [rpm]": 1200.0}
    reduced.update({"cop_ref [-]": 8 / 3, "cop_sys [-]": 2.5, "capacity_clamped": "max", "hp_is_on": True})
    reduced.update({"capacity_binding": BINDING_CONDENSING, "Q_max [W]": 4_000.0, "Q_request [W]": 10_000.0})
    row = merge_running_rows(full, 0.1, reduced, 0.3, ("Q_ref_tank [W]", "E_cmp [W]", "E_tot [W]"))
    assert row["Q_ref_tank [W]"] == pytest.approx(5_500.0) == row["Q_max [W]"]
    assert row["E_tot [W]"] == pytest.approx(2_000.0)
    assert row["cop_sys [-]"] == pytest.approx(2.75) and row["cop_ref [-]"] == pytest.approx(5_500.0 / 1_875.0)
    # State quantities are those of the stretch that ran longer; the capacity keys say the step was reduced.
    assert row["cmp_rpm [rpm]"] == 1200.0
    assert row["capacity_clamped"] == "max" and row["capacity_binding"] == BINDING_CONDENSING
    assert row["Q_request [W]"] == 10_000.0
    assert merge_running_rows(full, 0.3, reduced, 0.1, ("Q_ref_tank [W]",))["cmp_rpm [rpm]"] == 3000.0


# ---------------------------------------------------------------------------
# one step: the cycles of its two stretches
# ---------------------------------------------------------------------------


def _resolve(
    T_start=56.0,
    T_upper=63.0,
    dt=3600.0,
    floor=2_000.0,
    on_limit_runs=True,
    calls=None,
    q_cycle=Q_ON,
    on_limit_share=1.0,
    full_binding=None,
    on_limit_binding=BINDING_CONDENSING,
    full_runs=None,
    **kw,
):
    """``resolve_step_operation`` on the tank of the event tests, with a cycle of COP 3 (2.5 on the limit).

    ``q_cycle`` is what the full-duty cycle delivers [W]; ``on_limit_share`` the part of the duty
    asked on the limit that the cycle there delivers (less than 1: another limit holds it).
    ``full_binding`` and ``on_limit_binding`` are the limits those two cycles name in
    ``capacity_binding`` (``None``: none). ``full_runs(T_C, fraction)`` says whether the full-duty
    cycle has an operating point there (default: always); a failed one is an off-mode row that
    carries what it was solved at.
    """
    calls = {} if calls is None else calls

    def residual_free(T_K):
        return (C * T_K - C * (T_start + 273.15)) / C

    def solve_cycle(T_C, fraction):
        calls.setdefault("full", []).append((T_C, fraction))
        if full_runs is not None and not full_runs(T_C, fraction):
            return {"hp_is_on": False, "failure_reason": "cycle_invalid", "solved at": (T_C, fraction)}
        row = {
            "hp_is_on": True,
            "Q_ref_tank [W]": q_cycle,
            "E_tot [W]": q_cycle / 3.0,
            "T_ref_cond_sat_l [°C]": T_C + 5.0,
        }
        if full_binding is not None:
            row.update({"capacity_clamped": "max", "capacity_binding": full_binding})
        return row

    def solve_cycle_at_duty(T_C, q, fraction):
        calls.setdefault("limit", []).append((T_C, q, fraction))
        if not on_limit_runs:
            return {"hp_is_on": False, "failure_reason": "cycle_invalid"}
        q = q * on_limit_share
        row = {"hp_is_on": True, "Q_ref_tank [W]": q, "E_tot [W]": q / 2.5, "T_ref_cond_sat_l [°C]": 65.0}
        row.update({"capacity_clamped": "max", "Q_request [W]": Q_ON})
        if on_limit_binding is not None:
            row["capacity_binding"] = on_limit_binding
        return row

    def min_duty():
        calls["floor"] = calls.get("floor", 0) + 1
        return floor

    args = dict(
        T_start_K=T_start + 273.15,
        call_start=True,
        running_prev=False,
        phase_age_s=math.inf,
        scheduled=True,
        T_lower=55.0,
        T_upper=T_upper,
        dt=dt,
        C_curr=C,
        C_next=C,
        C_tank=C,
        residual_free=residual_free,
        solve_cycle=solve_cycle,
        Q_request=Q_ON,
        # The cut-out of the compressor that stops instead: where the nameplate duty meets the limit.
        T_limit_C=LIMIT.knee_C(Q_ON),
        duty_line=(LIMIT.T_zero_C, LIMIT.K),
        min_duty=min_duty,
        solve_cycle_at_duty=solve_cycle_at_duty,
        rate_keys=("Q_ref_tank [W]", "E_tot [W]"),
    )
    args.update(kw)
    return (*resolve_step_operation(**args), calls)


def _resolve_stop(**kw):
    """The same step by a compressor that stops at the cut-out: no reduced-duty argument reaches the kernel."""
    return _resolve(duty_line=None, min_duty=None, solve_cycle_at_duty=None, rate_keys=(), **kw)


def test_a_step_below_the_knee_costs_nothing_more():
    """The floor (cycle solves) and the cycle on the limit are asked for only when the step gets there."""
    run, row, calls = _resolve(T_upper=59.0)
    assert run.reduced_fraction == 0.0 and row["Q_ref_tank [W]"] == Q_ON
    assert "floor" not in calls and "limit" not in calls and len(calls["full"]) == 1
    # The same step without the limit's arguments: the same run.
    plain, _, _ = _resolve(T_upper=59.0, duty_line=None)
    assert run == plain


def test_a_step_on_the_limit_solves_each_stretch_once_at_its_mean():
    run, row, calls = _resolve()
    t_full, t_reduced = 240.0, TAU * math.log(2.5)
    assert calls["floor"] == 1 and len(calls["full"]) == 1 and len(calls["limit"]) == 1
    T_full, _ = calls["full"][0]
    T_reduced, q_reduced, _ = calls["limit"][0]
    assert T_full == pytest.approx(58.0) and q_reduced == pytest.approx(LIMIT.duty(T_reduced))
    assert q_reduced * t_reduced == pytest.approx(3.0 * C, rel=1e-12)
    # The row is the mean running cycle: times the run time, the step's heat and electricity.
    run_s = run.runtime_fraction * 3600.0
    assert run_s == pytest.approx(t_full + t_reduced)
    assert row["Q_ref_tank [W]"] * run_s == pytest.approx(7.0 * C, rel=1e-12)
    assert row["E_tot [W]"] * run_s == pytest.approx(4.0 * C / 3.0 + 3.0 * C / 2.5, rel=1e-12)
    assert row["capacity_clamped"] == "max" and row["Q_max [W]"] == pytest.approx(row["Q_ref_tank [W]"])
    # Each stretch's source sees the run time that carries the step's mean load at its duty.
    load = 7.0 * C / 3600.0
    assert calls["full"][0][1] == pytest.approx(load / Q_ON) and calls["limit"][0][2] == pytest.approx(load / q_reduced)


def test_electricity_of_the_stub_charge_does_not_depend_on_the_step():
    """The whole charge in one step against 36 steps of 100 s: heat exact, electricity to the midpoint rule."""

    def charge(parts):
        T, call, running, age, heat, power = 56.0, True, False, math.inf, 0.0, 0.0
        dt = 3600.0 / parts
        for _ in range(parts):
            run, row, _ = _resolve(T_start=T, dt=dt, call_start=call, running_prev=running, phase_age_s=age)
            if row is not None and run.runtime_fraction > 0.0:
                heat += row["Q_ref_tank [W]"] * run.runtime_fraction * dt
                power += row["E_tot [W]"] * run.runtime_fraction * dt
                T += row["Q_ref_tank [W]"] * run.runtime_fraction * dt / C
            call, running, age = run.call_end, run.running_end, run.phase_age_end_s
        return T, heat, power

    one, many = charge(1), charge(36)
    assert many[0] == pytest.approx(one[0], abs=1e-6) == pytest.approx(63.0, abs=1e-6)
    assert many[1] == pytest.approx(one[1], rel=1e-9)
    assert many[2] == pytest.approx(one[2], rel=1e-9)  # this stub's power is linear in the duty: exact


#: Fields of an :class:`InStepRun` that say what the compressor did (the labels are compared separately).
RUN_FIELDS = (
    "runtime_fraction",
    "running_start",
    "running_end",
    "call_end",
    "starts",
    "phase_age_end_s",
    "T_on_mean_C",
    "heat_unavailable",
    "T_on_first_C",
    "limit_stop",
    "reduced_fraction",
    "T_reduced_mean_C",
    "T_full_mean_C",
)


def _same_step(a, b):
    """Two steps resolved the same way: every field of the run, both ways, and the same cycle row."""
    (run_a, row_a), (run_b, row_b) = a, b
    for name in RUN_FIELDS:
        assert getattr(run_a, name) == getattr(run_b, name), name
    assert row_a == row_b and row_b == row_a


@pytest.mark.parametrize(
    "withheld",
    [dict(floor=None), dict(floor=Q_ON), dict(on_limit_runs=False)],
    ids=["the caller withholds it", "no duty below the request", "no cycle on the limit"],
)
@pytest.mark.parametrize("dt", [3600.0, 600.0, 60.0])
@pytest.mark.parametrize("T_start", [56.0, 59.99, 60.0, 61.0])
def test_a_withheld_step_is_the_step_of_the_compressor_that_stops(withheld, dt, T_start):
    """Reduced-duty charging withheld: the run and the cycle of the kernel called without it.

    The reference is the same step resolved with no reduced-duty argument at all, the path of
    ``condensing_limit_control="stop"``. A step that does not get to the limit is not withheld
    (nothing was asked of the caller), and is the reference's step too.
    """
    stop, stop_row, stop_calls = _resolve_stop(T_start=T_start, dt=dt)
    run, row, calls = _resolve(T_start=T_start, dt=dt, **withheld)
    _same_step((run, row), (stop, stop_row))
    assert run.reduced_fraction == 0.0 and run.limit_stop_C is None
    gets_there = stop.limit_stop
    assert run.reduced_withheld == gets_there and not stop.reduced_withheld
    assert ("floor" in calls) == gets_there
    if "on_limit_runs" not in withheld:
        # Withheld by the caller: the cycle solves of the reference, and not one more.
        assert calls.get("full") == stop_calls.get("full") and "limit" not in calls
    if gets_there:
        assert in_step_stop_reason(run, LIMIT.knee_C(Q_ON)) == STOP_CONDENSING_LIMIT


def test_a_withheld_step_holds_at_the_cut_out_as_the_stop_does():
    """Withheld, the compressor holds the tank at the nameplate cut-out whatever its minimum rest time.

    (On the limit a stop is followed by the minimum rest time; a withheld step is not on it.)
    """
    kw = dict(T_start=59.5, dt=3600.0, min_off_time_s=600.0)

    def residual_free(T_K):  # a loss of 500 W
        return (C * T_K - C * (59.5 + 273.15) + 500.0 * 3600.0) / C

    stop, stop_row, _ = _resolve_stop(residual_free=residual_free, **kw)
    run, row, _ = _resolve(floor=None, residual_free=residual_free, **kw)
    _same_step((run, row), (stop, stop_row))
    assert run.reduced_withheld and run.running_end and run.starts == 1  # held as one run
    on_limit, _, _ = _resolve(residual_free=residual_free, T_upper=64.5, **kw)
    assert not on_limit.reduced_withheld and on_limit.starts > 1 and on_limit.reduced_fraction > 0.0


def test_the_verdict_of_a_charge_does_not_depend_on_the_step():
    """One hour in 1, 6 and 60 steps: withheld, the tank, the heat and the labels are the stop's at every step.

    The verdict is the caller's (``min_duty``), asked whenever a step gets to the limit; the
    kernel adds no condition of its own to it.
    """

    def charge(parts, resolve, **kw):
        T, call, running, age, heat, labels = 56.0, True, False, math.inf, 0.0, []
        dt = 3600.0 / parts
        for _ in range(parts):
            run, row, _ = resolve(T_start=T, dt=dt, call_start=call, running_prev=running, phase_age_s=age, **kw)
            if row is not None and run.runtime_fraction > 0.0:
                heat += row["Q_ref_tank [W]"] * run.runtime_fraction * dt
                T += row["Q_ref_tank [W]"] * run.runtime_fraction * dt / C
            call, running, age = run.call_end, run.running_end, run.phase_age_end_s
            labels.append((run.reduced_withheld, run.reduced_fraction > 0.0))
        return T, heat, labels

    for parts in (1, 6, 60):
        T_stop, heat_stop, _ = charge(parts, _resolve_stop)
        T_end, heat, labels = charge(parts, _resolve, floor=None)
        assert (T_end, heat) == (T_stop, heat_stop), parts
        assert T_end == pytest.approx(60.0, abs=1e-9) and heat == pytest.approx(4.0 * C, rel=1e-12), parts
        assert not any(reduced for _, reduced in labels), parts
        assert labels[-1][0], parts  # the step that got to the limit says so
        # ... and with the stretch qualified the same charge goes on to the set point, at every step.
        T_on, _, on_labels = charge(parts, _resolve)
        assert T_on == pytest.approx(63.0, abs=1e-6) and not any(withheld for withheld, _ in on_labels), parts


def test_a_cycle_already_below_the_floor_keeps_the_stop():
    """Another limit holds the full-duty cycle below this limit's smallest duty: nothing to run on between them.

    The step is then the stop's (cut-out at the knee of the request, 60 degC, below this limit's
    stop at 64 degC), so no step resolved on the limit runs above the stop at less than the floor.
    """
    stop, stop_row, _ = _resolve_stop(T_upper=64.5, q_cycle=1_500.0)
    run, row, calls = _resolve(T_upper=64.5, q_cycle=1_500.0)
    _same_step((run, row), (stop, stop_row))
    assert run.reduced_fraction == 0.0 and run.reduced_withheld and "limit" not in calls


@pytest.mark.parametrize(
    "held",
    [
        dict(on_limit_share=0.9),
        dict(on_limit_share=1.0 - 2.0 * DUTY_MATCH_RTOL),
        dict(on_limit_share=1.0 + 2.0 * DUTY_MATCH_RTOL),
        dict(q_cycle=9_000.0),
        dict(q_cycle=2_500.0),
        dict(q_cycle=Q_ON * (1.0 - 2.0 * DUTY_MATCH_RTOL)),
    ],
    ids=[
        "on the limit: 90 %",
        "on the limit: just short",
        "on the limit: just over",
        "below the limit: 9 of 10 kW",
        "below the limit: 2.5 of 10 kW",
        "below the limit: just short",
    ],
)
@pytest.mark.parametrize("dt", [3600.0, 600.0])
@pytest.mark.parametrize("T_upper", [63.0, 64.5])
def test_a_cycle_of_the_step_that_another_limit_holds_withholds_it(held, dt, T_upper):
    """The stretch qualified, but a cycle of the step itself does not deliver the duty its events took.

    The events of a step on the limit take the request below the knee and the limit's duty above
    it. A cycle that delivers another duty (by more than ``DUTY_MATCH_RTOL``) was held by another
    limit, and the step would credit the tank with heat the compressor did not give: the step is
    withheld, and is the step of the compressor that stops.
    """
    stop, stop_row, _ = _resolve_stop(T_upper=T_upper, dt=dt, **{k: v for k, v in held.items() if k == "q_cycle"})
    run, row, _ = _resolve(T_upper=T_upper, dt=dt, **held)
    _same_step((run, row), (stop, stop_row))
    assert run.reduced_fraction == 0.0 and run.T_reduced_mean_C is None
    assert run.reduced_withheld == stop.limit_stop
    # The row's heat is the heat of the run's own duty: nothing of the limit's line is in it.
    assert row["Q_ref_tank [W]"] == held.get("q_cycle", Q_ON) and "capacity_clamped" not in row


@pytest.mark.parametrize(
    "held",
    [dict(on_limit_binding="discharge_temperature"), dict(full_binding="discharge_temperature")],
    ids=["the cycle on the limit", "the full-duty cycle"],
)
@pytest.mark.parametrize("dt", [3600.0, 600.0])
def test_a_cycle_of_the_step_that_names_another_limit_withholds_it(held, dt):
    """A cycle of the step delivers the duty its events took, but names another limit as the one holding it.

    Such a cycle is on that limit, not on the condensing one (the qualification of the stretch
    reads its three cycles the same way), and its step is not one of reduced-duty charging.
    """
    full = {k: v for k, v in held.items() if k == "full_binding"}
    stop, stop_row, _ = _resolve_stop(dt=dt, **full)
    run, row, _ = _resolve(dt=dt, **held)
    _same_step((run, row), (stop, stop_row))
    assert run.reduced_fraction == 0.0 and run.reduced_withheld == stop.limit_stop
    assert row.get("capacity_binding") == held.get("full_binding")


@pytest.mark.parametrize(
    "full_runs",
    [lambda T_C, fraction: False, lambda T_C, fraction: fraction < 0.1, lambda T_C, fraction: T_C < 57.0],
    ids=["never", "not at the load of a run on the limit", "only where the run starts"],
)
@pytest.mark.parametrize("dt", [3600.0, 600.0])
@pytest.mark.parametrize("cut_out", [60.0, 61.0], ids=["cut-out at the knee", "cut-out above the knee"])
def test_a_full_duty_cycle_without_an_operating_point_is_the_stops_to_resolve(full_runs, dt, cut_out):
    """The step gets to the limit, and the cycle of its full-duty stretch has no operating point.

    Nothing of the limit can be resolved then, and the step is the one the compressor that stops
    takes: the same cycle solves (at the mean and with the run time *its* events give, then at the
    start of the run), the same row, the same run. A cycle that fails at the larger load of a run
    on the limit and runs at the stop's own is a step in which the compressor runs.
    (A caller's cut-out above the knee of the request is the air-source boiler's with a frosting
    coil.)
    """
    kw = dict(dt=dt, T_limit_C=cut_out, full_runs=full_runs)
    stop, stop_row, stop_calls = _resolve_stop(**kw)
    run, row, calls = _resolve(**kw)
    _same_step((run, row), (stop, stop_row))
    assert run.reduced_fraction == 0.0 and run.reduced_withheld == stop.limit_stop
    # The stop's own solves close the step's: whatever was tried on the limit, the row is from these.
    assert calls["full"][-len(stop_calls["full"]) :] == stop_calls["full"]
    assert "limit" not in calls


@pytest.mark.parametrize("floor", [None, 2_000.0], ids=["stretch withheld", "stretch qualified"])
@pytest.mark.parametrize("T_zero", [106.0, 105.0], ids=["knee above the boiling point", "knee at the boiling point"])
def test_a_stop_at_the_boiling_point_is_not_reduced_duty_charging_withheld(floor, T_zero):
    """The nameplate duty meets the limit at or above 100 degC: the boiling point ends the run, not this limit.

    The step never gets to the limit, so nothing is asked about its stretch, nothing is withheld,
    and the step is the one resolved without the reduced-duty arguments.
    """
    kw = dict(T_start=96.0, T_lower=90.0, T_upper=99.5, dt=3600.0, min_on_time_s=5_000.0, T_limit_C=T_WATER_BOIL_C)
    stop, stop_row, stop_calls = _resolve_stop(**kw)
    assert stop.limit_stop and in_step_stop_reason(stop, T_WATER_BOIL_C) == STOP_BOILING_LIMIT
    run, row, calls = _resolve(duty_line=(T_zero, LIMIT.K), floor=floor, **kw)
    _same_step((run, row), (stop, stop_row))
    assert not run.reduced_withheld and run.limit_stop_C is None
    assert in_step_stop_reason(run, T_WATER_BOIL_C) == STOP_BOILING_LIMIT
    assert "floor" not in calls and "limit" not in calls and calls["full"] == stop_calls["full"]


def test_a_withheld_step_that_the_boiling_point_stops_is_not_flagged():
    """The knee lies below 100 degC but the cut-out of the compressor that stops is the boiling point.

    (A caller's cut-out above the knee: the delivered duty of a frosting coil meets the limit at a
    colder tank than the nameplate duty does.) Withheld, the step is the stop's and runs on to
    100 degC; the stop that acted there is the boiling point, and the run is not marked.
    """
    kw = dict(T_start=96.0, T_lower=90.0, T_upper=99.5, dt=3600.0, min_on_time_s=5_000.0, T_limit_C=T_WATER_BOIL_C)
    stop, stop_row, _ = _resolve_stop(**kw)
    run, row, calls = _resolve(duty_line=(103.0, LIMIT.K), floor=None, **kw)  # knee 98 degC
    _same_step((run, row), (stop, stop_row))
    assert calls["floor"] == 1 and run.limit_stop and not run.reduced_withheld
    assert in_step_stop_reason(run, T_WATER_BOIL_C) == STOP_BOILING_LIMIT
    # The same step with the cut-out at the knee: the condensing cut-out acts, and the run says so.
    at_knee, _, _ = _resolve(duty_line=(103.0, LIMIT.K), floor=None, **{**kw, "T_limit_C": 98.0})
    assert at_knee.reduced_withheld and in_step_stop_reason(at_knee, 98.0) == STOP_CONDENSING_LIMIT


def test_cycles_that_name_the_condensing_limit_or_none_are_on_it():
    for kw in (dict(on_limit_binding=None), dict(full_binding=BINDING_CONDENSING), dict()):
        run, _, _ = _resolve(**kw)
        assert run.reduced_fraction > 0.0 and not run.reduced_withheld, kw


def test_cycles_within_the_tolerance_are_the_events_duty():
    """Within ``DUTY_MATCH_RTOL`` of the duty asked, a step is resolved on the limit as before."""
    inside = 1.0 - 0.5 * DUTY_MATCH_RTOL
    for kw in (dict(on_limit_share=inside), dict(q_cycle=Q_ON * inside)):
        run, row, _ = _resolve(**kw)
        assert run.reduced_fraction > 0.0 and not run.reduced_withheld, kw
        assert row["capacity_binding"] == BINDING_CONDENSING, kw


def test_a_hold_is_credited_with_the_heat_its_events_took():
    """A cycle at the stop that has a few millionths more than the smallest duty it was asked for.

    Measured on the R32 unit at 35 degC outdoor: asked for 7911.206 W at the stop, the cycle at its
    lowest speed delivers 7911.256 W (6.4e-6 more). Holding the tank at the stop, the compressor
    is on for the share of the time the events gave it, and the row takes the cycle's rates at
    the duty asked: the heat of the step is the loss, as the events took it, not that share more.
    (Credited with the cycle's duty, the tank ended each 1200 s step 1.3e-5 K above the stop, and
    the next step counted a stop and a start.)
    """
    loss, dt = 500.0, 1200.0

    def with_loss(T_K):
        return (C * T_K - C * (LIMIT.stop_C + 273.15) + dt * loss) / C

    held = dict(
        T_start=LIMIT.stop_C, T_upper=64.5, dt=dt, residual_free=with_loss, running_prev=True, phase_age_s=100.0
    )
    exact, row_exact, _ = _resolve(**held)
    assert exact.runtime_fraction == pytest.approx(loss / LIMIT.Q_min, rel=1e-12) and exact.limit_stop
    assert exact.runtime_fraction * row_exact["Q_ref_tank [W]"] == pytest.approx(loss, rel=1e-12)
    more = 1.0 + 6.4e-6
    run, row, _ = _resolve(on_limit_share=more, **held)
    # The run is the events': the same on-share, the same phase.
    assert run == exact
    assert (run.running_end, run.starts, run.phase_age_end_s, run.limit_stop) == (True, 0, 100.0 + dt, True)
    # The row: the cycle's rates at the duty asked (its COP of 2.5, its states), so the heat is the loss.
    assert row["Q_ref_tank [W]"] == pytest.approx(LIMIT.Q_min, rel=1e-12)
    assert row["E_tot [W]"] == pytest.approx(LIMIT.Q_min / 2.5, rel=1e-12)
    assert row["T_ref_cond_sat_l [°C]"] == row_exact["T_ref_cond_sat_l [°C]"]
    assert run.runtime_fraction * row["Q_ref_tank [W]"] == pytest.approx(loss, rel=1e-12)
    # A cycle that has less than it was asked for (within the tolerance) keeps its own rates and the events' run.
    less, row_less, _ = _resolve(on_limit_share=1.0 - 6.4e-6, **held)
    assert less == exact
    assert row_less["Q_ref_tank [W]"] == pytest.approx(LIMIT.Q_min * (1.0 - 6.4e-6), rel=1e-12)


def test_a_cycle_that_has_more_changes_neither_the_run_time_nor_the_heat_of_the_events():
    """The same cycle in a step that runs through on the limit, and in one that gets to the stop and holds.

    A compressor that runs through the step on the limit ran through it: its run time is the
    step, whatever its cycle has. And the tank gets what the events integrated, in that step and
    in one that runs along the limit to the stop and holds there: no more, so that it ends where
    the events put it, at or below the stop. (Two things a correction must not do: shorten a
    continuous run, 0.9999936 of the step with the first form of the fix, or leave the excess in
    the row, with the second.)
    """
    more = 1.0 + 6.4e-6
    # 100 s on the limit from 61 degC: the stop (64 degC) is not reached.
    through, row_through, _ = _resolve(T_start=61.0, dt=100.0)
    assert through.runtime_fraction == 1.0 and through.reduced_fraction == 1.0 and through.running_end
    with_more, row, _ = _resolve(T_start=61.0, dt=100.0, on_limit_share=more)
    assert with_more == through and with_more.runtime_fraction == 1.0
    assert row["Q_ref_tank [W]"] == pytest.approx(row_through["Q_ref_tank [W]"], rel=1e-12)
    assert row["Q_ref_tank [W]"] == pytest.approx(LIMIT.duty(through.T_reduced_mean_C), rel=1e-12)
    assert row["E_tot [W]"] == pytest.approx(row_through["E_tot [W]"], rel=1e-12)
    # 1200 s from 63.9 degC with a loss of 500 W: along the limit to the stop, then the hold.
    loss, dt = 500.0, 1200.0

    def with_loss(T_K):
        return (C * T_K - C * (63.9 + 273.15) + dt * loss) / C

    mixed = dict(T_start=63.9, T_upper=64.5, dt=dt, residual_free=with_loss)
    exact, row_exact, _ = _resolve(**mixed)
    assert exact.limit_stop and exact.running_end and 0.25 < exact.runtime_fraction < 1.0
    run, row, _ = _resolve(on_limit_share=more, **mixed)
    assert run == exact
    assert row["Q_ref_tank [W]"] == pytest.approx(row_exact["Q_ref_tank [W]"], rel=1e-12)
    # The heat of the step carries the tank from 63.9 degC to the stop and covers the loss: nothing beyond.
    heat = run.runtime_fraction * row["Q_ref_tank [W]"] * dt
    assert heat == pytest.approx(C * (LIMIT.stop_C - 63.9) + loss * dt, rel=1e-9)


def test_the_rates_of_a_cycle_that_has_more_are_lowered_by_no_more_than_the_tolerance():
    """How far the scaling goes: a cycle is taken at the duty asked only inside ``DUTY_MATCH_RTOL``.

    The excess is not always a few millionths (the R32 unit with ``rps_min=8`` at 45 degC outdoor:
    993 millionths, a factor of 0.99901). The factor is asked over delivered, so never below
    ``1 / (1 + DUTY_MATCH_RTOL)`` = 0.999001: a cycle with more than that is not on the duty the
    events took, and the step is withheld. Up to the bound the row keeps the cycle's ratio of heat
    to power.
    """
    loss, dt = 500.0, 1200.0

    def with_loss(T_K):
        return (C * T_K - C * (LIMIT.stop_C + 273.15) + dt * loss) / C

    held = dict(
        T_start=LIMIT.stop_C, T_upper=64.5, dt=dt, residual_free=with_loss, running_prev=True, phase_age_s=100.0
    )
    exact, _, _ = _resolve(**held)
    near = 1.0 + 0.99 * DUTY_MATCH_RTOL
    run, row, calls = _resolve(on_limit_share=near, **held)
    assert run == exact and not run.reduced_withheld
    (_, asked, _), delivered = calls["limit"][0], calls["limit"][0][1] * near
    assert asked / delivered == pytest.approx(1.0 / near) and 1.0 / (1.0 + DUTY_MATCH_RTOL) < asked / delivered < 0.9991
    assert row["Q_ref_tank [W]"] == pytest.approx(asked, rel=1e-12)
    assert row["E_tot [W]"] == pytest.approx(delivered / 2.5 * (asked / delivered), rel=1e-12)
    assert row["Q_ref_tank [W]"] / row["E_tot [W]"] == pytest.approx(2.5, rel=1e-12)  # the cycle's own COP
    assert run.runtime_fraction * row["Q_ref_tank [W]"] == pytest.approx(loss, rel=1e-12)
    # Past the bound the cycle is not the events': no scaling, the step is the one of the compressor that stops.
    beyond, _, _ = _resolve(on_limit_share=1.0 + 1.01 * DUTY_MATCH_RTOL, **held)
    assert beyond.reduced_withheld and beyond.reduced_fraction == 0.0 and beyond.runtime_fraction == 0.0


# ---------------------------------------------------------------------------
# the three boiler families
# ---------------------------------------------------------------------------


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


#: The issue's machine: R32, 15 kW air-to-water, set points 60-62 degC, outdoor 15 degC.
ISSUE = dict(ref="R32", hp_capacity=15000.0, T_tank_w_lower_bound=60.0, T_tank_w_upper_bound=62.0)

#: family -> (class, constructor arguments, source temperature of the runs [degC], steady source keyword).
#: The ground-source run uses a long response table so that every step size shares one model.
FAMILIES = {
    "ASHPB": (tmhp.AirSourceHeatPumpBoiler, dict(ISSUE), 15.0, "T0"),
    "GSHPB": (
        tmhp.GroundSourceHeatPumpBoiler,
        dict(T_tank_w_lower_bound=70.0, T_tank_w_upper_bound=72.0, dt_s=60.0, t_max_s=86400.0),
        16.0,
        "T_source",
    ),
    "WSHPB": (
        tmhp.WaterSourceHeatPumpBoiler,
        dict(T_tank_w_lower_bound=70.0, T_tank_w_upper_bound=72.0),
        16.0,
        "T_source",
    ),
}
STEPS = (60.0, 600.0, 3600.0)
HORIZON = 3600.0


def _model(family, **extra):
    cls, kwargs, _, _ = FAMILIES[family]
    kwargs = {**kwargs, **extra}
    if family == "GSHPB" and "dt_s" in extra:
        kwargs["dt_s"] = extra["dt_s"]
    return _quiet(cls, **kwargs)


def _run(family, model, dt, T_init=50.0, horizon=HORIZON, draw=0.0):
    n = int(round(horizon / dt))
    source = FAMILIES[family][2]
    outdoor = source if family == "ASHPB" else 7.0
    return _quiet(model.analyze_dynamic, n * dt, dt, T_init, np.full(n, draw), np.full(n, outdoor))


@pytest.fixture(scope="module", params=list(FAMILIES))
def charge(request):
    """One hour from 50 degC without a draw, reduced-duty charging, at 60, 600 and 3600 s steps."""
    family = request.param
    runs = {}
    for dt in STEPS:
        model = _model(family, condensing_limit_control="reduce_duty", **({"dt_s": dt} if family == "GSHPB" else {}))
        runs[dt] = (model, _run(family, model, dt))
    return family, runs


def test_the_issue_band_is_kept_with_reduced_duty_charging():
    """60-62 degC, R32 15 kW: capped to 55.99-57.99 degC by default, kept when the duty is reduced."""
    with pytest.warns(OperatingLimitWarning, match="at the nameplate duty"):
        stop = tmhp.AirSourceHeatPumpBoiler(**ISSUE)
    assert stop.condensing_limit_control == "stop"
    assert (stop.T_tank_w_lower_bound, stop.T_tank_w_upper_bound) == pytest.approx((55.991, 57.991), abs=1e-3)
    with warnings.catch_warnings():
        warnings.simplefilter("error", OperatingLimitWarning)
        reduce = tmhp.AirSourceHeatPumpBoiler(**ISSUE, condensing_limit_control="reduce_duty")
    assert (reduce.T_tank_w_lower_bound, reduce.T_tank_w_upper_bound) == (60.0, 62.0)
    assert reduce.T_tank_w_setpoints_requested == (60.0, 62.0)


@pytest.mark.parametrize("family", list(FAMILIES))
def test_set_points_above_the_charging_limit_are_capped_there(family):
    """The cap follows the charging limit: where the smallest continuous duty condenses on the limit."""
    cls, kwargs, _, source_arg = FAMILIES[family]
    with pytest.warns(OperatingLimitWarning, match="smallest continuous duty"):
        m = cls(
            **{**kwargs, "T_tank_w_lower_bound": 60.0, "T_tank_w_upper_bound": 80.0},
            condensing_limit_control="reduce_duty",
        )
    cutout, reach = condensing_tank_cutout(m), condensing_tank_reach(m)
    cap = m.T_tank_w_upper_bound
    assert m.T_tank_w_setpoints_requested == (60.0, 80.0)
    assert cutout + 1.0 < cap < reach and m.T_tank_w_lower_bound == 60.0
    reference = {source_arg: m.charge_limit_reference_C}
    # The cap is where the minimum-speed duty the steady solve reports there (``Q_min [W]``) meets the limit ...
    probe = _quiet(m.analyze_steady, T_tank_w=cap, Q_ref_tank=0.1 * m.hp_capacity, **reference)
    assert probe["capacity_clamped"] == "min"
    assert cap == pytest.approx(reach - probe["Q_min [W]"] / m.K_tank_hx, abs=0.01)
    # ... and just below it a nameplate request still runs, reduced onto the limit, above that duty.
    below = _quiet(m.analyze_steady, T_tank_w=cap - 0.1, Q_ref_tank=m.hp_capacity, **reference)
    assert below["hp_is_on"] and below["capacity_binding"] == BINDING_CONDENSING
    assert below["Q_ref_tank [W]"] == pytest.approx(m.K_tank_hx * (reach - cap + 0.1), rel=0.02)
    assert below["Q_ref_tank [W]"] > probe["Q_min [W]"]
    if family != "ASHPB":
        # The source boilers' steady solve stops there too. (The air-source one runs on for a few
        # tenths of a kelvin at the speed floor by widening the outdoor approach: see the docs.)
        above = _quiet(m.analyze_steady, T_tank_w=cap + 0.1, Q_ref_tank=m.hp_capacity, **reference)
        assert not above["hp_is_on"] and above["failure_reason"] == "condensing_temp_above_max"


def test_unknown_control_is_refused():
    with pytest.raises(ValueError, match="condensing_limit_control"):
        tmhp.AirSourceHeatPumpBoiler(condensing_limit_control="inverter")


def test_multi_node_tank_keeps_the_stop():
    """The coil of a multi-node tank has its own cut-out and hold: the option is refused there."""
    with pytest.raises(ValueError, match="tank_model"):
        _quiet(
            tmhp.GroundSourceHeatPumpBoiler,
            tank_model="stratified",
            condensing_limit_control="reduce_duty",
            dt_s=600.0,
            t_max_s=86400.0,
        )


def test_the_charge_reaches_the_set_point_at_every_step_size(charge):
    family, runs = charge
    for dt, (m, df) in runs.items():
        cutout = condensing_tank_cutout(m)
        assert m.T_tank_w_upper_bound > cutout + 1.0  # the band lies past the nameplate cut-out
        assert (df["failure_reason"] == "none").all(), (family, dt)
        # The charge ends at the set point (a long step reports the tank after it has cooled a little).
        assert df["T_tank_w [°C]"].max() <= m.T_tank_w_upper_bound + 1e-6, (family, dt)
        assert df["T_tank_w [°C]"].iloc[-1] > m.T_tank_w_upper_bound - 0.5, (family, dt)
        if dt == 60.0:
            assert df["T_tank_w [°C]"].max() == pytest.approx(m.T_tank_w_upper_bound, abs=0.05), family
        assert df["hp_starts [-]"].sum() == 1, (family, dt)
        assert (df["reduced_duty_fraction [-]"] > 0.0).any(), (family, dt)


def test_end_temperature_heat_and_electricity_do_not_depend_on_the_step(charge):
    """60 s against 600 s and 3600 s steps: 0.05 K, 1 % of heat, 1 % of electricity."""
    family, runs = charge
    totals = {
        dt: (
            float(df["T_tank_w [°C]"].iloc[-1]),
            float(df["Q_ref_tank [W]"].sum() * dt),
            float(df["E_tot [W]"].sum() * dt),
        )
        for dt, (_, df) in runs.items()
    }
    fine = totals[60.0]
    for dt in (600.0, 3600.0):
        assert totals[dt][0] == pytest.approx(fine[0], abs=0.05), (family, dt)
        assert totals[dt][1] == pytest.approx(fine[1], rel=0.01), (family, dt)
        assert totals[dt][2] == pytest.approx(fine[2], rel=0.01), (family, dt)


def test_every_step_conserves_energy(charge):
    """Tank energy change = heat delivered - envelope loss (no draw), on every step."""
    family, runs = charge
    for dt, (m, df) in runs.items():
        T = np.concatenate(([50.0], df["T_tank_w [°C]"].to_numpy()))
        stored = m.C_tank * np.diff(T)
        supplied = (df["Q_ref_tank [W]"] - df["Q_tank_loss [W]"]).to_numpy() * dt
        assert np.allclose(stored, supplied, rtol=0.0, atol=1e-6 * m.C_tank), (family, dt)


def test_reduced_steps_run_at_the_duty_of_the_steady_solve(charge):
    """A step on the limit: the duty and the binding ``analyze_steady`` gives at its mean tank temperature."""
    family, runs = charge
    _, _, source, source_arg = FAMILIES[family]
    m, df = runs[60.0]
    T_start = np.concatenate(([50.0], df["T_tank_w [°C]"].to_numpy()[:-1]))
    whole = (df["reduced_duty_fraction [-]"] == 1.0).to_numpy()
    assert whole.sum() >= 3
    K = m.K_tank_hx
    for i in np.flatnonzero(whole)[:3]:
        row = df.iloc[i]
        assert row["capacity_clamped"] == "max" and row["capacity_binding"] == BINDING_CONDENSING
        assert row["Q_request [W]"] == m.hp_capacity and row["Q_max [W]"] == pytest.approx(row["Q_ref_tank [W]"])
        # Mean tank temperature of the step, from the mean duty on the limit.
        T_mean = condensing_tank_reach(m) - row["Q_ref_tank [W]"] / K
        assert min(T_start[i], row["T_tank_w [°C]"]) - 1e-6 <= T_mean <= max(T_start[i], row["T_tank_w [°C]"]) + 1e-6
        steady = _quiet(m.analyze_steady, T_tank_w=T_mean, Q_ref_tank=m.hp_capacity, **{source_arg: source})
        assert steady["capacity_clamped"] == "max" and steady["capacity_binding"] == BINDING_CONDENSING
        # The steady search closes within 0.05 K of condensing temperature of the limit.
        assert row["Q_ref_tank [W]"] == pytest.approx(steady["Q_ref_tank [W]"], abs=0.06 * K)
        if family != "GSHPB":  # the ground-source step solves against the ground's response, not a fixed source
            assert row["E_tot [W]"] == pytest.approx(steady["E_tot [W]"], rel=0.02)


def test_rows_before_the_cut_out_are_those_of_the_default(charge):
    """Up to the nameplate cut-out the two controls are the same machine: identical rows."""
    family, runs = charge
    m, df = runs[60.0]
    cutout = condensing_tank_cutout(m)
    stop = _model(family, T_tank_w_lower_bound=cutout - 2.0, T_tank_w_upper_bound=cutout)
    assert stop.condensing_limit_control == "stop"
    base = _run(family, stop, 60.0, horizon=240.0)
    assert (base["runtime_fraction [-]"] == 1.0).all() and "reduced_duty_fraction [-]" not in base.columns
    for key in ("T_tank_w [°C]", "Q_ref_tank [W]", "E_tot [W]", "cmp_rpm [rpm]", "T_ref_cond_sat_l [°C]"):
        assert np.array_equal(df[key].to_numpy()[:4], base[key].to_numpy()), (family, key)
    assert (df["reduced_duty_fraction [-]"].iloc[:4] == 0.0).all()


def test_issue_example_both_ways():
    """R32 15 kW, 60-62 degC, outdoor 15 degC, from 50 degC: the stop ends at 58 degC, the reduced duty at 62."""
    out = {}
    for control in ("stop", "reduce_duty"):
        m = _quiet(tmhp.AirSourceHeatPumpBoiler, **ISSUE, condensing_limit_control=control)
        df = _run("ASHPB", m, 60.0)
        out[control] = (m, df)
    stop_m, stop_df = out["stop"]
    reduce_m, reduce_df = out["reduce_duty"]
    assert stop_df["T_tank_w [°C]"].max() == pytest.approx(57.991, abs=0.01)
    assert reduce_df["T_tank_w [°C]"].max() == pytest.approx(62.0, abs=0.01)
    assert "reduced_duty_fraction [-]" not in stop_df.columns
    # The reduced stretch takes longer per kelvin and costs more electricity per unit of heat.
    assert reduce_df["runtime_fraction [-]"].sum() > stop_df["runtime_fraction [-]"].sum()
    cop = {k: df["Q_ref_tank [W]"].sum() / df["E_tot [W]"].sum() for k, (_, df) in out.items()}
    assert cop["reduce_duty"] < cop["stop"]
    assert reduce_df["hp_stop_reason"].isna().all()  # the set point, not the envelope, ended the charge


def test_a_warmer_source_than_the_reference_holds_at_its_own_stop():
    """The thermostat's cap is the limit at the reference source; a step applies the limit of its own source.

    At 35 degC outdoor the smallest continuous duty is larger than at the 7 degC reference, so the
    stop of the step lies below the capped set point: the compressor holds the tank there and the
    row says ``hp_stop_reason == "condensing_limit"``.
    """
    m = _quiet(
        tmhp.AirSourceHeatPumpBoiler,
        ref="R32",
        T_tank_w_lower_bound=60.0,
        T_tank_w_upper_bound=90.0,
        condensing_limit_control="reduce_duty",
    )
    n = 6
    df = _quiet(m.analyze_dynamic, n * 600.0, 600.0, 55.0, np.zeros(n), np.full(n, 35.0))
    assert (df["failure_reason"] == "none").all()
    assert df["T_tank_w [°C]"].max() < m.T_tank_w_upper_bound - 0.2
    held = df["hp_stop_reason"] == STOP_CONDENSING_LIMIT
    assert held.iloc[-3:].all() and df.loc[held, "hp_is_on"].all()
    # Held: the tank stays at the step's stop, the compressor making up the loss.
    assert df["T_tank_w [°C]"].iloc[-1] == pytest.approx(df["T_tank_w [°C]"].iloc[-2], abs=0.01)
    # The step's stop is where its smallest continuous duty meets the limit: the tank never
    # passes it, and no step runs on the limit at less than that duty.
    floor = m._charge_stretch_cache[1]
    stop = condensing_tank_reach(m) - floor / m.K_tank_hx
    assert df["T_tank_w [°C]"].max() <= stop + 1e-3
    on_limit = df["reduced_duty_fraction [-]"] > 0.0
    assert on_limit.any() and (df.loc[on_limit, "Q_max [W]"] >= floor * (1.0 - 1e-6)).all()


def test_a_draw_larger_than_the_duty_at_the_stop_is_met_along_the_limit():
    """At the step's stop the limit leaves the smallest duty; a draw that takes more is not held there.

    R32 at 35 degC outdoor: the stop is 62.1 degC and the duty there 6.3 kW. From 63 degC with a
    10.5 kW draw the compressor waits for the tank to cool to the stop, then runs on continuously
    while the tank cools along the limit, and settles where the limit's duty is the draw
    (60.1 degC) -- the same tank, heat and electricity at 60 s and 600 s steps.
    """
    runs = {}
    for dt in (60.0, 600.0):
        m = _quiet(
            tmhp.AirSourceHeatPumpBoiler,
            ref="R32",
            T_tank_w_lower_bound=60.0,
            T_tank_w_upper_bound=90.0,
            condensing_limit_control="reduce_duty",
        )
        n = int(1800.0 / dt)
        runs[dt] = (m, _quiet(m.analyze_dynamic, n * dt, dt, 63.0, np.full(n, 1.0e-4), np.full(n, 35.0)))
    for dt, (m, df) in runs.items():
        assert (df["failure_reason"] == "none").all(), dt
        ran = df["runtime_fraction [-]"] > 0.0
        assert (df.loc[ran, "T_ref_cond_sat_v [°C]"] <= 65.0 + 0.011).all(), dt
        last = df.iloc[-1]
        # Settled: running the whole step on the limit, at the limit's duty for the tank as it is.
        assert last["runtime_fraction [-]"] == 1.0 == last["reduced_duty_fraction [-]"]
        assert last["hp_stop_reason"] is None
        line = m.K_tank_hx * (condensing_tank_reach(m) - last["T_tank_w [°C]"])
        assert last["Q_ref_tank [W]"] == pytest.approx(line, rel=0.01), dt
        assert last["Q_ref_tank [W]"] > 1.5 * 6_300.0  # well above the duty at the stop
        # Never on the limit at less than the smallest continuous duty, never running above the stop.
        floor = m._charge_stretch_cache[1]
        stop = condensing_tank_reach(m) - floor / m.K_tank_hx
        on_limit = df["reduced_duty_fraction [-]"] > 0.0
        assert (df.loc[on_limit, "Q_max [W]"] >= floor * (1.0 - 1e-6)).all(), dt
        T_start = np.concatenate(([63.0], df["T_tank_w [°C]"].to_numpy()[:-1]))
        whole_step_above = (T_start > stop + 1e-3) & (df["T_tank_w [°C]"].to_numpy() > stop + 1e-3)
        assert (df.loc[whole_step_above, "runtime_fraction [-]"] == 0.0).all(), dt
    (_, fine), (_, coarse) = runs[60.0], runs[600.0]
    assert coarse["T_tank_w [°C]"].iloc[-1] == pytest.approx(fine["T_tank_w [°C]"].iloc[-1], abs=0.05)
    assert coarse["Q_ref_tank [W]"].sum() * 600.0 == pytest.approx(fine["Q_ref_tank [W]"].sum() * 60.0, rel=0.01)
    assert coarse["E_tot [W]"].sum() * 600.0 == pytest.approx(fine["E_tot [W]"].sum() * 60.0, rel=0.01)


def test_frosting_coil_charges_on_the_frost_free_limit():
    """Below 5 degC the delivered heat is the mean over frost and defrost; the limit binds the frost-free cycle."""
    runs = {}
    for dt in (60.0, 600.0):
        m = _quiet(tmhp.AirSourceHeatPumpBoiler, **ISSUE, condensing_limit_control="reduce_duty")
        n = int(HORIZON / dt)
        runs[dt] = _quiet(m.analyze_dynamic, n * dt, dt, 50.0, np.zeros(n), np.full(n, 0.0))
    for dt, df in runs.items():
        assert (df["failure_reason"] == "none").all(), dt
        assert df["defrost_fraction [-]"].max() > 0.0
        ran = df["runtime_fraction [-]"] > 0.0
        # No running row condenses above the limit.
        assert (df.loc[ran, "T_ref_cond_sat_v [°C]"] <= 65.0 + 0.011).all(), dt
    fine, coarse = runs[60.0], runs[600.0]
    assert coarse["T_tank_w [°C]"].iloc[-1] == pytest.approx(fine["T_tank_w [°C]"].iloc[-1], abs=0.05)
    assert coarse["Q_ref_tank [W]"].sum() * 600.0 == pytest.approx(fine["Q_ref_tank [W]"].sum() * 60.0, rel=0.01)
    assert coarse["E_tot [W]"].sum() * 600.0 == pytest.approx(fine["E_tot [W]"].sum() * 60.0, rel=0.01)


def test_scenario_classes_take_the_same_path():
    from tmhp.subsystems import PhotovoltaicSystem, SolarThermalCollector

    variants = {
        "ASHPB_PV_ESS": dict(pv=PhotovoltaicSystem()),
        "ASHPB_STC_preheat": dict(stc=SolarThermalCollector()),
        "ASHPB_STC_tank": dict(stc=SolarThermalCollector()),
    }
    for name, extra in variants.items():
        m = _quiet(getattr(tmhp, name), **ISSUE, condensing_limit_control="reduce_duty", **extra)
        assert m.T_tank_w_upper_bound == 62.0, name
        n = 6
        df = _quiet(
            m.analyze_dynamic,
            n * 600.0,
            600.0,
            55.0,
            np.zeros(n),
            np.full(n, 15.0),
            I_DN_schedule=np.zeros(n),
            I_dH_schedule=np.zeros(n),
        )
        assert (df["failure_reason"] == "none").all(), name
        assert (df["reduced_duty_fraction [-]"] > 0.0).any(), name
        assert df["T_tank_w [°C]"].max() == pytest.approx(62.0, abs=0.05), name


def test_the_stop_that_acted_is_the_one_reported():
    """Review, should-fix 3: a stop at the boiling point was reported as a condensing stop.

    The stop a step is resolved with is the lower of the boiling point and the limit's stop; the
    caller knows the nameplate cut-out only. The run carries the stop that acted.
    """
    hot = DutyLimit(T_zero_C=102.0, K=2_000.0, Q_min=2_000.0)  # knee 97 degC, stop 101 degC: above boiling
    run = _sim(T_start_C=96.0, T_lower=90.0, T_upper=99.0, min_on_time_s=5_000.0, duty_limit=hot)
    assert run.limit_stop and run.limit_stop_C == T_WATER_BOIL_C
    assert in_step_stop_reason(run, 97.0) == STOP_BOILING_LIMIT  # 97 degC: the nameplate cut-out the caller has
    # The limit's own stop, below the boiling point ...
    run = _sim(T_upper=64.5)
    assert run.limit_stop and run.limit_stop_C == pytest.approx(64.0)
    assert in_step_stop_reason(run, T_WATER_BOIL_C) == STOP_CONDENSING_LIMIT
    # ... and the model without a duty limit: the cut-out it was given, which the caller names.
    run = _sim(duty_limit=None, T_limit_C=60.0)
    assert run.limit_stop and run.limit_stop_C is None
    assert in_step_stop_reason(run, 60.0) == STOP_CONDENSING_LIMIT
    assert in_step_stop_reason(run, T_WATER_BOIL_C) == STOP_BOILING_LIMIT
    assert _sim(T_upper=59.0).limit_stop_C is None and in_step_stop_reason(_sim(T_upper=59.0), 60.0) is None


def test_a_minimum_rest_time_makes_the_restart_at_the_stop_a_real_event():
    """Review, should-fix 4: held at the stop, the run time and the compressor state disagreed.

    The hold is a mean of on/off cycling reported as one run: the phase age grows by the whole
    step while the compressor is on for a share of it. With a minimum rest time that cycling is
    not what the compressor can do, and a step on the duty limit resolves it as it happens: stop
    at the stop, rest, restart (a counted start), run back along the limit.
    """
    loss = ((50.0, -500.0), (70.0, -500.0))
    kw = dict(T_start_C=63.9, T_upper=64.5, free_heat=loss, dt=3600.0)
    # On the limit against the loss: C dT/dt = 2000 (64.75 - T), time constant 300 s.
    t_first = TAU * math.log((64.75 - 63.9) / (64.75 - 64.0))
    rest = 600.0  # the tank cools 0.5 K
    t_back = TAU * math.log((64.75 - 63.5) / (64.75 - 64.0))
    cycles = int((3600.0 - t_first) // (rest + t_back))
    run = _sim(min_off_time_s=rest, **kw)
    assert run.starts == 1 + cycles == 5
    assert run.runtime_fraction * 3600.0 == pytest.approx(t_first + cycles * t_back, rel=1e-9)
    assert run.reduced_fraction == run.runtime_fraction
    assert not run.running_end and run.call_end and run.limit_stop
    assert run.phase_age_end_s == pytest.approx(3600.0 - t_first - cycles * (rest + t_back), rel=1e-9)
    assert run.phase_age_end_s < rest  # resting, and for less than the minimum rest time
    # Without a minimum rest time the hold stands (what the cycling tends to as the rest goes to zero) ...
    hold = _sim(min_off_time_s=0.0, **kw)
    assert hold.starts == 1 and hold.running_end and hold.phase_age_end_s == 3600.0
    assert hold.runtime_fraction * 3600.0 == pytest.approx(t_first + (3600.0 - t_first) * 500.0 / 2_000.0, rel=1e-9)
    # ... and a shorter rest gives more starts and a run time that tends to the hold's.
    short = _sim(min_off_time_s=60.0, **kw)
    assert short.starts > run.starts and run.runtime_fraction < short.runtime_fraction < hold.runtime_fraction
    # Without a duty limit the hold at the cut-out stands whatever the rest time, as on main.
    plain = dict(kw, duty_limit=None, T_limit_C=64.0)
    assert _sim(min_off_time_s=rest, **plain) == _sim(**plain)


def test_the_search_for_the_smallest_duty_stays_below_the_boiling_point():
    """Review, blocker 2: the fixed point was evaluated at a tank of 103 degC, which no model accepts."""
    from types import SimpleNamespace

    from tmhp.compressor_envelope import smallest_duty_on_condensing_limit

    model = SimpleNamespace(ref="R600a", T_cond_max=104.0, K_tank_hx=2140.0, hp_capacity=15000.0)
    seen = []

    def floor_at(T_C):
        seen.append(T_C)
        return 1_800.0

    floor = smallest_duty_on_condensing_limit(model, floor_at)
    assert floor == 1_800.0 and max(seen) <= T_WATER_BOIL_C and seen[-1] == T_WATER_BOIL_C
    # The duty at 100 degC still condenses below the limit: the stop is above the boiling point.
    assert condensing_tank_reach(model) - floor / model.K_tank_hx > T_WATER_BOIL_C
    # A limit that the smallest duty meets below the boiling point is found as before.
    seen.clear()
    model.T_cond_max = 80.0
    assert smallest_duty_on_condensing_limit(model, floor_at) == 1_800.0
    assert seen[-1] == pytest.approx(80.0 - 1_800.0 / 2140.0, abs=0.01)


@pytest.mark.parametrize("family", list(FAMILIES))
def test_a_limit_met_above_the_boiling_point_constructs_and_keeps_the_band(family):
    """Review, blocker 2: R600a with ``T_cond_max=104``, set points 97-100 degC.

    The default option caps the band at the nameplate cut-out (96.99 degC). With reduced duty the
    smallest duty meets the limit above 100 degC (the search handed ``analyze_steady`` a tank of
    103 degC and raised): the boiling point is the limit, and the band asked for is kept.
    """
    cls, kwargs, source, _ = FAMILIES[family]
    kwargs = {
        **kwargs,
        "ref": "R600a",
        "T_cond_max": 104.0,
        "T_tank_w_lower_bound": 97.0,
        "T_tank_w_upper_bound": 100.0,
    }
    stop = _quiet(cls, **kwargs)
    assert stop.T_tank_w_upper_bound == pytest.approx(96.991, abs=1e-3)
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("error", OperatingLimitWarning)
        m = cls(**kwargs, condensing_limit_control="reduce_duty")
    assert (m.T_tank_w_lower_bound, m.T_tank_w_upper_bound) == (97.0, 100.0)
    # One step from the nameplate cut-out: the compressor runs on, on the limit, and the tank stays below 100 degC.
    df = _run(family, m, 600.0, T_init=96.5, horizon=600.0)
    row = df.iloc[0]
    assert row["failure_reason"] == "none" and row["hp_is_on"] and row["reduced_duty_fraction [-]"] > 0.0
    assert 96.991 < row["T_tank_w [°C]"] <= T_WATER_BOIL_C + 1e-6
    assert row["hp_stop_reason"] in (None, STOP_BOILING_LIMIT)


def test_a_stop_at_the_boiling_point_is_reported_as_one():
    """Review, should-fix 3: the stop the step applied was 100 degC, the row said ``condensing_limit``.

    (R600a with ``T_cond_max=102`` and source water at 16 degC, where the condensing limit governs
    the stretch. The first version of this test ran at 8 degC with ``T_cond_max=101``: there the
    compressor's speed ceiling leaves 7976 W of the 8000 W nameplate at the knee, and reduced-duty
    charging is withheld.)
    """
    m = _quiet(
        tmhp.WaterSourceHeatPumpBoiler,
        ref="R600a",
        T_cond_max=102.0,
        PR_cycle_max=15.0,
        T_dis_max=None,
        min_on_time_s=5000.0,
        T_tank_w_lower_bound=97.0,
        T_tank_w_upper_bound=100.0,
        condensing_limit_control="reduce_duty",
    )
    T_init = condensing_tank_cutout(m) - 0.1
    df = _quiet(
        m.analyze_dynamic, 2400.0, 2400.0, T_init, np.zeros(1), np.full(1, 15.0), T_source_w_schedule=np.full(1, 16.0)
    )
    row = df.iloc[0]
    # The smallest duty meets the limit above 100 degC ...
    floor = m._charge_stretch_cache[1]
    assert condensing_tank_reach(m) - floor / m.K_tank_hx > T_WATER_BOIL_C
    # ... so the minimum run time carried the compressor to the boiling point, which stopped it.
    assert row["hp_stop_reason"] == STOP_BOILING_LIMIT and row["failure_reason"] == "none"
    assert row["reduced_duty_fraction [-]"] > 0.0 and row["T_tank_w [°C]"] <= T_WATER_BOIL_C
    assert row["reduced_duty_withheld"] is None


def test_run_time_and_compressor_state_agree_at_the_stop():
    """Review, should-fix 4: three 1200 s steps at the stop with minimum run and rest times of 1800 s.

    Before (the review's case, on the compressor map of that time): run-time fractions 0.196 /
    0.175 / 0.175 with one start, and a final state "running for 3600 s" although the compressor
    had run 654 s. With a minimum rest time the stop is a real one. At 35 degC outdoor the stop
    is at 61.30 degC; the runs start 0.3 K below it.
    """
    m = _quiet(
        tmhp.AirSourceHeatPumpBoiler,
        ref="R32",
        T_tank_w_lower_bound=62.0,
        T_tank_w_upper_bound=65.0,
        min_on_time_s=1800.0,
        min_off_time_s=1800.0,
        condensing_limit_control="reduce_duty",
    )
    state, ran_s, since_start, starts, rows = m.make_initial_state(61.0), 0.0, 0.0, 0, []
    for i in range(3):
        inputs = dict(n=i, current_time_s=i * 1200.0, T0=35.0, dV_mix_w_out=1e-5, T_sup_w=15.0, T_sur=20.0)
        state, row = _quiet(m.step, state, inputs, 1200.0)
        ran_s += row["runtime_fraction [-]"] * 1200.0
        starts += row["hp_starts [-]"]
        since_start = row["runtime_fraction [-]"] * 1200.0 if row["hp_starts [-]"] else since_start
        rows.append(row)
        # A compressor reported running has been so for no longer than it ran since its last start;
        # one reported off rests for less than the time since it last ran.
        if state.hp_running_prev:
            assert state.hp_phase_age_s <= since_start + 1e-6, i
        else:
            assert state.hp_phase_age_s <= (i + 1) * 1200.0 - ran_s + 1e-6, i
    assert starts == 2 and rows[0]["hp_stop_reason"] == STOP_CONDENSING_LIMIT
    # The first run reaches the step's stop and ends there; the rest that follows is the minimum one.
    assert rows[0]["runtime_fraction [-]"] < 0.05 and rows[0]["T_tank_w [°C]"] < 61.31
    assert rows[1]["hp_starts [-]"] == 1 and rows[2]["runtime_fraction [-]"] == 0.0
    assert not state.hp_running_prev and state.hp_phase_age_s < 1800.0
    # Without minimum times the hold stands, as one run (see the docs for what its timers mean).
    hold = _quiet(
        tmhp.AirSourceHeatPumpBoiler,
        ref="R32",
        T_tank_w_lower_bound=62.0,
        T_tank_w_upper_bound=65.0,
        condensing_limit_control="reduce_duty",
    )
    state = hold.make_initial_state(61.0)
    fractions = []
    for i in range(3):
        inputs = dict(n=i, current_time_s=i * 1200.0, T0=35.0, dV_mix_w_out=1e-5, T_sup_w=15.0, T_sur=20.0)
        state, row = _quiet(hold.step, state, inputs, 1200.0)
        fractions.append(row["runtime_fraction [-]"])
        assert row["hp_stop_reason"] == STOP_CONDENSING_LIMIT and row["hp_starts [-]"] == (1 if i == 0 else 0)
    assert fractions == pytest.approx([0.15753, 0.13826, 0.13826], abs=1e-4)
    assert state.hp_running_prev and state.hp_phase_age_s == 3600.0


@pytest.mark.parametrize("dt, steps", [(60.0, 12), (600.0, 3), (3600.0, 2)])
def test_a_tank_that_cools_to_the_stop_and_is_held_there_starts_once(dt, steps):
    """Final review: the same unit from 61.8 degC, 0.5 K above its stop at 35 degC outdoor (61.3034 degC).

    The tank cools to the stop and the compressor holds it there. Before, the step in which it got
    there ended at 61.303528 degC (600 s), 1.3e-4 K above the stop, and the next step counted a
    stop and a second start; the same with 60 s and 3600 s steps.
    """
    m = _quiet(
        tmhp.AirSourceHeatPumpBoiler,
        ref="R32",
        T_tank_w_lower_bound=62.0,
        T_tank_w_upper_bound=65.0,
        min_on_time_s=0.0,
        min_off_time_s=0.0,
        condensing_limit_control="reduce_duty",
    )
    state, rows = m.make_initial_state(61.8), []
    for i in range(steps):
        inputs = dict(n=i, current_time_s=i * dt, T0=35.0, dV_mix_w_out=1e-5, T_sup_w=15.0, T_sur=20.0)
        state, row = _quiet(m.step, state, inputs, dt)
        rows.append(row)
    assert sum(row["hp_starts [-]"] for row in rows) == 1
    started = next(i for i, row in enumerate(rows) if row["hp_starts [-]"])
    assert started == (4 if dt == 60.0 else 0)
    for row in rows[:started]:
        assert not row["hp_is_on"] and row["T_tank_w [°C]"] > 61.3034
    for row in rows[started:]:
        # Held at the stop from the step in which the tank got there, to the last digits of the balance.
        assert row["T_tank_w [°C]"] == pytest.approx(61.303399, abs=1e-6)
        assert row["T_tank_w [°C]"] == pytest.approx(rows[-1]["T_tank_w [°C]"], abs=1e-9)
        assert row["hp_is_on"] and row["hp_stop_reason"] == STOP_CONDENSING_LIMIT
        assert row["reduced_duty_fraction [-]"] == row["runtime_fraction [-]"] > 0.0
        assert row["reduced_duty_withheld"] is None
    # A step that is all hold runs the share of the time that covers the loss at the stop.
    assert rows[-1]["runtime_fraction [-]"] == pytest.approx(0.13826, abs=1e-5)
    assert state.hp_running_prev and state.hp_phase_age_s < steps * dt


def test_near_the_bound_of_the_scaling_the_row_is_still_one_cycle():
    """A hold at the stop whose cycle has 0.099 % more than it was asked for, against the 0.1 % allowed.

    R32 15 kW with a speed floor of 8 rev/s at 45 degC outdoor (final review): the smallest duty
    found for the stop is 5382.86 W, and the cycle at the floor delivers 5388.21 W there. The row
    takes the cycle's rates times 0.99901. Scaled together, they are still one cycle: heat over
    power is the COP the row reports (the cycle's), and the heat is the refrigerant flow times the
    enthalpy it gives up in the condenser.
    """
    m = _quiet(
        tmhp.AirSourceHeatPumpBoiler,
        ref="R32",
        rps_min=8.0,
        T_tank_w_lower_bound=63.0,
        T_tank_w_upper_bound=64.0,
        condensing_limit_control="reduce_duty",
    )
    solved = []
    inner = m._solve_cycle_on_limit
    m._solve_cycle_on_limit = lambda ctx, T_C, q: (solved.append((q, dict(inner(ctx, T_C, q)))), solved[-1][1])[1]
    # The first step charges to the stop and holds (its closing balance ends it 4e-6 K below, which
    # the second makes up); the third is all hold.
    state, rows = m.make_initial_state(62.3), []
    for i in range(3):
        inputs = dict(n=i, current_time_s=i * 1200.0, T0=45.0, dV_mix_w_out=1e-5, T_sup_w=15.0, T_sur=20.0)
        state, row = _quiet(m.step, state, inputs, 1200.0)
        rows.append(row)
    row, (asked, cycle) = rows[2], solved[-1]
    assert [r["hp_starts [-]"] for r in rows] == [1, 0, 0] and row["hp_stop_reason"] == STOP_CONDENSING_LIMIT
    assert row["reduced_duty_withheld"] is None and row["reduced_duty_fraction [-]"] == row["runtime_fraction [-]"]
    assert row["T_tank_w [°C]"] == pytest.approx(62.484797, abs=1e-5)
    assert row["T_tank_w [°C]"] == pytest.approx(rows[1]["T_tank_w [°C]"], abs=1e-9)  # held at the stop
    # The cycle at the speed floor has more than asked, close to the most the tolerance lets through.
    delivered = cycle["Q_ref_tank [W]"]
    assert cycle["cmp_rpm [rpm]"] == pytest.approx(8.0 * 60.0)
    assert asked == pytest.approx(5382.86, rel=1e-5) and delivered == pytest.approx(5388.21, rel=1e-5)
    factor = asked / delivered
    assert 1.0 / (1.0 + DUTY_MATCH_RTOL) < factor < 0.99902
    # Every rate of the row is the cycle's times that factor, averaged over the step.
    share = row["runtime_fraction [-]"]
    assert share == pytest.approx(0.20345, abs=1e-4)
    assert row["Q_ref_tank [W]"] == pytest.approx(share * asked, rel=1e-12)
    for key in ("Q_ref_tank [W]", "E_cmp [W]", "E_tot [W]", "E_ou_fan [W]", "m_dot_ref [kg/s]", "Q_ref_ou [W]"):
        assert row[key] == pytest.approx(share * factor * cycle[key], rel=1e-12), key
    assert row["Q_max [W]"] == pytest.approx(asked, rel=1e-12)
    # ... so the ratios the row reports are the ratios of its rates, and those of the cycle solved.
    assert row["Q_ref_tank [W]"] / row["E_cmp [W]"] == pytest.approx(row["cop_ref [-]"], rel=1e-12)
    assert row["Q_ref_tank [W]"] / row["E_tot [W]"] == pytest.approx(row["cop_sys [-]"], rel=1e-12)
    assert (row["cop_ref [-]"], row["cop_sys [-]"]) == (cycle["cop_ref [-]"], cycle["cop_sys [-]"])
    # ... and the heat is the refrigerant flow times its enthalpy drop from the discharge to the expansion valve.
    drop = row["h_ref_cmp_out [J/kg]"] - row["h_ref_exp_in [J/kg]"]
    assert drop == cycle["h_ref_cmp_out [J/kg]"] - cycle["h_ref_exp_in [J/kg]"]
    assert row["Q_ref_tank [W]"] == pytest.approx(row["m_dot_ref [kg/s]"] * drop, rel=1e-9)


#: Tank insulation -> (constructor arguments, tolerances on the end temperature [K] and the heat [-]).
#: Loss coefficients 1.15 W/K (the default), 4.6 W/K and 76.65 W/K: C_tank / UA of 153 h, 38 h and 2.3 h.
LOSSY_TANKS = {
    "default": ({}, 0.02, 0.001),
    "thin": (dict(x_ins=0.01), 0.05, 0.001),
    "bare": (dict(x_ins=0.001, k_ins=0.3, h_o=50.0), 0.3, 0.025),
}


def _reference_charge(m, horizon, reduce, T_sur=20.0, T_init=50.0, h=0.05):
    """End temperature [degC] and heat [J] of ``horizon`` seconds, integrated with ``h`` s steps.

    ``dT/dt = [Q(T) - UA (T - T_sur)] / C`` with the thermostat's hysteresis; the compressor
    delivers the nameplate duty, and with ``reduce`` no more than ``K_tank_hx (T_reach - T)``.
    Nothing of the event kernel is used.
    """
    C_tank, UA, K, Q_on = m.C_tank, m.UA_tank_wall, m.K_tank_hx, m.hp_capacity
    T_reach = condensing_tank_reach(m)
    lower, upper = m.T_tank_w_lower_bound, m.T_tank_w_upper_bound
    tank, heat, on = T_init, 0.0, True
    for _ in range(int(round(horizon / h))):
        if on and tank >= upper:
            on = False
        elif not on and tank <= lower:
            on = True
        q = (min(Q_on, K * (T_reach - tank)) if reduce else Q_on) if on else 0.0
        heat += q * h
        tank += h * (q - UA * (tank - T_sur)) / C_tank
    return tank, heat


@pytest.mark.parametrize("control", ["stop", "reduce_duty"])
@pytest.mark.parametrize("tank", list(LOSSY_TANKS))
def test_a_charge_against_an_independent_fine_step_integral(tank, control):
    """Review, should-fix 5: how far the result depends on the step in a tank that loses heat.

    The events integrate the tank's loss in time; the step's tank temperature then comes from one
    implicit solve with the loss at the end-of-step temperature. The two agree as far as the loss
    changes little over a step, with either option (it is the kernel's property, on main too).
    Measured against the reference at 60 / 600 / 1200 s steps, 20 minutes from 50 degC:
    1.15 W/K: 0.006 K, 0.01 %; 4.6 W/K: 0.022 K, 0.02 %; 76.65 W/K: 0.22 K, 1.8 %.
    """
    extra, T_tol, Q_tol = LOSSY_TANKS[tank]
    horizon = 1200.0
    for dt in (60.0, 600.0, 1200.0):
        m = _quiet(tmhp.AirSourceHeatPumpBoiler, **ISSUE, **extra, condensing_limit_control=control)
        if dt == 60.0:
            T_ref, Q_ref = _reference_charge(m, horizon, control == "reduce_duty")
        n = int(horizon / dt)
        df = _quiet(m.analyze_dynamic, n * dt, dt, 50.0, np.zeros(n), np.full(n, 15.0))
        assert float(df["T_tank_w [°C]"].iloc[-1]) == pytest.approx(T_ref, abs=T_tol), (tank, control, dt)
        assert float(df["Q_ref_tank [W]"].sum() * dt) == pytest.approx(Q_ref, rel=Q_tol), (tank, control, dt)


def test_the_blend_warning_names_the_bubble_point():
    """The cap of a zeotropic blend is below the bubble point of the limit's pressure, not ``T_cond_max``."""
    with (
        pytest.warns(OperatingLimitWarning, match="smallest continuous duty") as caught,
        contextlib.redirect_stdout(io.StringIO()),
    ):
        m = tmhp.AirSourceHeatPumpBoiler(ref="R407C", T_tank_w_upper_bound=70.0, condensing_limit_control="reduce_duty")
    message = next(str(w.message) for w in caught if "smallest continuous duty" in str(w.message))
    bubble = condensing_tank_reach(m)
    assert bubble < m.T_cond_max - 3.0
    assert f"bubble point at {bubble:.1f} °C" in message and f"T_cond_max={m.T_cond_max:g} °C" in message
    # A pure fluid has none to name.
    with (
        pytest.warns(OperatingLimitWarning, match="smallest continuous duty") as caught,
        contextlib.redirect_stdout(io.StringIO()),
    ):
        tmhp.AirSourceHeatPumpBoiler(ref="R32", T_tank_w_upper_bound=90.0, condensing_limit_control="reduce_duty")
    assert not any("bubble point" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# the stretch on the limit qualifies, or reduced-duty charging is withheld
# ---------------------------------------------------------------------------

OTHER = "discharge_temperature"
FAILS = "source_fluid_freezing"
#: Knee, middle and stop of the stretch of ``LIMIT`` for the nameplate duty ``Q_ON`` [degC].
KNEE, MIDDLE, STOP = 60.0, 62.0, 64.0


def _qualify(second=None, floor=LIMIT.Q_min, line=LIMIT):
    """``condensing_stretch`` on the limit of the event tests, with a second limit on the cycle.

    ``second(T_C)`` is what that limit leaves at tank temperature ``T_C``: ``(duty [W], name)``,
    or ``None`` where it leaves no operating point (the cycle then fails with ``FAILS``). The
    cycle delivers what it is asked for, or that duty if it is less; a ``name`` of ``None`` is the
    compressor's speed ceiling, which names no limit. Returns the verdict, the cycles solved as
    ``(T_C, asked)`` and how often the smallest duty was looked for.
    """
    solved, searches = [], []

    def solve(T_C, asked):
        solved.append((T_C, asked))
        row = {"hp_is_on": True, "failure_reason": "none", "Q_ref_tank [W]": asked}
        left = (math.inf, None) if second is None else second(T_C)
        if left is None:
            return {"hp_is_on": False, "failure_reason": FAILS}
        duty, name = left
        if duty < asked:
            row.update({"Q_ref_tank [W]": duty, "capacity_clamped": "max"})
        if name is not None and duty <= asked:
            row["capacity_binding"] = name
        return row

    def min_duty():
        searches.append(len(solved))
        return floor

    return condensing_stretch(solve, line.T_zero_C, line.K, Q_ON, min_duty), solved, searches


def test_a_stretch_the_condensing_limit_governs_qualifies():
    """Three cycles: the knee, the middle and the stop, each asked for the limit's duty there."""
    verdict, solved, searches = _qualify()
    assert verdict == (LIMIT.Q_min, None)
    assert [T for T, _ in solved] == pytest.approx([KNEE, MIDDLE, STOP])
    assert [q for _, q in solved] == pytest.approx([10_000.0, 6_000.0, 2_000.0])
    assert searches == [1]  # the smallest duty is looked for once, after the knee
    # A cycle on the limit names the condensing limit, or none: both qualify.
    named = _qualify(lambda T_C: (LIMIT.duty(T_C), BINDING_CONDENSING))
    assert named[0] == (LIMIT.Q_min, None)


@pytest.mark.parametrize("where, T_other", [("knee", KNEE), ("middle", MIDDLE), ("stop", STOP)])
def test_another_limit_anywhere_on_the_stretch_withholds_it(where, T_other):
    """A second limit that holds the cycle below the condensing limit's duty at one of the three points."""

    def second(T_C):
        return (0.9 * LIMIT.duty(T_C), OTHER) if abs(T_C - T_other) < 0.5 else (math.inf, None)

    verdict, solved, searches = _qualify(second)
    assert verdict == (None, OTHER)
    # The first point that fails ends the check: no solve after it, and at the knee no search either.
    n = {"knee": 1, "middle": 2, "stop": 3}[where]
    assert len(solved) == n and solved[-1][0] == pytest.approx(T_other)
    assert searches == ([] if where == "knee" else [1])


@pytest.mark.parametrize("where, T_other", [("knee", KNEE), ("middle", MIDDLE), ("stop", STOP)])
def test_a_cycle_that_does_not_run_withholds_it_with_its_failure(where, T_other):
    verdict, solved, _ = _qualify(lambda T_C: None if abs(T_C - T_other) < 0.5 else (math.inf, None))
    assert verdict == (None, FAILS)
    assert len(solved) == {"knee": 1, "middle": 2, "stop": 3}[where]


def test_what_withholds_the_stretch_is_named():
    # The compressor at its speed ceiling names no limit: short of the nameplate at the knee.
    assert _qualify(lambda T_C: (9_500.0, None))[0] == (None, BINDING_COMPRESSOR)
    # A cycle that delivers the duty but names another limit is on that limit.
    assert _qualify(lambda T_C: (LIMIT.duty(T_C), OTHER))[0] == (None, OTHER)
    # A cycle that fails without a reason of its own.
    silent, _, _ = _qualify(lambda T_C: None)
    assert silent == (None, FAILS)
    no_reason = condensing_stretch(lambda T_C, q: {"hp_is_on": False}, 65.0, 2_000.0, Q_ON, lambda: 2_000.0)
    assert no_reason == (None, BINDING_CYCLE)
    # No continuous duty below the nameplate: no stretch, after the one solve at the knee.
    for floor in (None, Q_ON, 1.5 * Q_ON, math.nan):
        verdict, solved, searches = _qualify(floor=floor)
        assert verdict == (None, WITHHELD_NO_SMALLER_DUTY) and len(solved) == 1 and searches == [1], floor


def test_the_duty_delivered_is_matched_to_the_stated_tolerance():
    """A cycle within ``DUTY_MATCH_RTOL`` of the limit's duty is on the limit; one further off is not."""

    def short(by):
        return lambda T_C: (LIMIT.duty(T_C) * (1.0 - by), None)

    assert _qualify(short(0.5 * DUTY_MATCH_RTOL))[0] == (LIMIT.Q_min, None)
    assert _qualify(short(2.0 * DUTY_MATCH_RTOL))[0] == (None, BINDING_COMPRESSOR)
    # More than asked is as far off the limit's line as less.
    over, _, _ = _qualify()
    assert over == (LIMIT.Q_min, None)
    above = condensing_stretch(
        lambda T_C, q: {"hp_is_on": True, "Q_ref_tank [W]": q * (1.0 + 2.0 * DUTY_MATCH_RTOL)},
        LIMIT.T_zero_C,
        LIMIT.K,
        Q_ON,
        lambda: LIMIT.Q_min,
    )
    assert above == (None, BINDING_COMPRESSOR)


def test_no_point_of_the_check_is_above_the_boiling_point():
    """A limit high enough that the smallest duty meets it above 100 degC: the stretch ends at the boiling point."""
    hot = DutyLimit(T_zero_C=102.0, K=2_000.0, Q_min=2_000.0)  # knee 97 degC, stop 101 degC
    verdict, solved, _ = _qualify(line=hot)
    assert verdict == (2_000.0, None)
    assert [T for T, _ in solved] == pytest.approx([97.0, 98.5, T_WATER_BOIL_C])
    assert solved[-1][1] == pytest.approx(hot.K * (hot.T_zero_C - T_WATER_BOIL_C))  # the limit's duty there, 4 kW


# ---- the same on the models ----

#: Columns only a ``reduce_duty`` model writes.
REDUCE_ONLY = {"reduced_duty_fraction [-]", "reduced_duty_withheld"}


def _same_cell(a, b):
    both_nan = isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b)
    return both_nan or (a is None and b is None) or a == b


def _assert_rows_are_those_of(df, reference):
    """Every column of ``reference`` in ``df`` with the same values, and no other column in ``df`` (no tolerance)."""
    assert set(df.columns) - REDUCE_ONLY == set(reference.columns)
    assert len(df) == len(reference)
    for column in reference.columns:
        differing = [
            i for i, (a, b) in enumerate(zip(df[column], reference[column], strict=True)) if not _same_cell(a, b)
        ]
        assert not differing, (column, differing[:3])


def _stop_model_with_the_band_of(cls, kwargs, model):
    """A ``"stop"`` model with the thermostat band ``model`` keeps.

    The constructor of a ``"stop"`` model caps the band at the nameplate cut-out; the band of the
    ``reduce_duty`` model is written over it afterwards. No reduced-duty argument reaches the
    kernel from this model: it is the reference a withheld step has to reproduce.
    """
    stop = _quiet(cls, **kwargs)
    assert stop.condensing_limit_control == "stop" and stop.T_tank_w_upper_bound <= condensing_tank_cutout(stop)
    stop.T_tank_w_lower_bound, stop.T_tank_w_upper_bound = model.T_tank_w_lower_bound, model.T_tank_w_upper_bound
    return stop


R32_COLD = dict(ref="R32", hp_capacity=15000.0)
#: Source states in which another limit governs the stretch: name -> (class, arguments, start tank, outdoor,
#: source water, horizon, what withholds). The first four are the reviewers' cases, the two with a discharge limit
#: at the outdoor temperature at which that limit holds the knee with the default compressor map (0 and -7 degC;
#: the reviewers' -7 and -12 degC leave no cycle at all near the cut-out now, with ``"stop"`` as well). The default
#: speed ceiling (120 rev/s) is not reached at the knee down to the outdoor temperature at which the pressure ratio
#: is: the last two cases are a compressor with a lower ceiling, and that pressure-ratio limit.
COMPETING = {
    "source water at 4 degC: its freezing limit": (
        tmhp.WaterSourceHeatPumpBoiler,
        dict(T_dis_max=None, T_tank_w_lower_bound=75.0, T_tank_w_upper_bound=76.0),
        67.0,
        7.0,
        4.0,
        600.0,
        "source_fluid_freezing",
    ),
    "source water at 3.1 degC: no cycle": (
        tmhp.WaterSourceHeatPumpBoiler,
        dict(T_dis_max=None, T_tank_w_lower_bound=75.0, T_tank_w_upper_bound=76.0),
        68.1,
        7.0,
        3.1,
        600.0,
        "source_fluid_freezing",
    ),
    "discharge limit of 105 degC at 0 degC": (
        tmhp.AirSourceHeatPumpBoiler,
        dict(R32_COLD, T_dis_max=105.0, T_tank_w_lower_bound=60.0, T_tank_w_upper_bound=62.0),
        57.0,
        0.0,
        None,
        600.0,
        "discharge_temperature",
    ),
    "discharge limit of 115 degC at -7 degC": (
        tmhp.AirSourceHeatPumpBoiler,
        dict(R32_COLD, T_dis_max=115.0, T_tank_w_lower_bound=61.0, T_tank_w_upper_bound=63.3),
        57.5,
        -7.0,
        None,
        600.0,
        "discharge_temperature",
    ),
    "speed ceiling of 80 rev/s at -7 degC": (
        tmhp.AirSourceHeatPumpBoiler,
        dict(R32_COLD, rps_max=80.0, T_tank_w_lower_bound=60.0, T_tank_w_upper_bound=62.0),
        57.0,
        -7.0,
        None,
        600.0,
        "compressor",
    ),
    "pressure-ratio limit at -15 degC": (
        tmhp.AirSourceHeatPumpBoiler,
        dict(R32_COLD, T_tank_w_lower_bound=60.0, T_tank_w_upper_bound=62.0),
        57.0,
        -15.0,
        None,
        600.0,
        "pressure_ratio",
    ),
}


@pytest.mark.parametrize("case", list(COMPETING))
def test_where_another_limit_governs_the_rows_are_those_of_the_stop(case):
    """The reviewers' competing-limit cases: reduced-duty charging withheld at every step size.

    Each run is compared with a ``"stop"`` model that keeps the same thermostat band: the same
    columns, and every value the same. The rows that got to the limit name what withheld the
    charging, the others (the tank still below the cut-out) name nothing; none is on the limit.
    """
    cls, kwargs, T_init, T0, T_source, horizon, reason = COMPETING[case]
    totals = {}
    for dt in (60.0, 600.0):
        m = _quiet(cls, **kwargs, condensing_limit_control="reduce_duty")
        stop = _stop_model_with_the_band_of(cls, kwargs, m)
        n = int(horizon / dt)
        schedule = {} if T_source is None else {"T_source_w_schedule": np.full(n, T_source)}
        df = _quiet(m.analyze_dynamic, n * dt, dt, T_init, np.zeros(n), np.full(n, T0), **schedule)
        reference = _quiet(stop.analyze_dynamic, n * dt, dt, T_init, np.zeros(n), np.full(n, T0), **schedule)
        assert REDUCE_ONLY.isdisjoint(reference.columns)
        _assert_rows_are_those_of(df, reference)
        assert (df["reduced_duty_fraction [-]"] == 0.0).all(), dt
        withheld = df["reduced_duty_withheld"]
        assert set(withheld.dropna()) == {reason}, dt
        # A step that got to the limit is one the stop's cut-out acted in, and says what withheld it.
        got_there = df["hp_stop_reason"] == STOP_CONDENSING_LIMIT
        assert got_there.any() and (withheld[got_there] == reason).all() and withheld[~got_there].isna().all(), dt
        totals[dt] = (
            float(df["T_tank_w [°C]"].iloc[-1]),
            float(df["Q_ref_tank [W]"].sum() * dt),
            float(df["E_tot [W]"].sum() * dt),
            float(reference["Q_ref_tank [W]"].sum() * dt),
        )
    # The call stands above the cut-out: the charge ends there, the same at both step sizes.
    (T_fine, Q_fine, E_fine, _), (T_coarse, Q_coarse, E_coarse, _) = totals[60.0], totals[600.0]
    assert T_coarse == pytest.approx(T_fine, abs=0.05)
    assert Q_coarse == pytest.approx(Q_fine, rel=0.01, abs=1.0) and E_coarse == pytest.approx(E_fine, rel=0.01, abs=1.0)


def test_a_qualified_source_state_is_checked_once_with_three_cycles():
    """One outdoor state over many steps: one search for the smallest duty and three qualification solves."""
    m = _quiet(tmhp.AirSourceHeatPumpBoiler, **ISSUE, condensing_limit_control="reduce_duty")
    asked = []
    inner = m._solve_cycle_at

    def spy(ctx, T_tank_w, Q_delivered=None):
        asked.append((ctx.n, T_tank_w, Q_delivered))
        return inner(ctx, T_tank_w, Q_delivered)

    m._solve_cycle_at = spy
    n = 20
    df = _quiet(m.analyze_dynamic, n * 60.0, 60.0, 56.0, np.zeros(n), np.full(n, 15.0))
    on_limit = df["reduced_duty_fraction [-]"] > 0.0
    assert on_limit.sum() > 3 and df["reduced_duty_withheld"].isna().all()
    # The step's own cycles on the limit: one per step that ran on it. The other solves at a duty
    # of their own are the qualification's, all in the first step that got to the limit.
    at_duty = [(step, T, q) for step, T, q in asked if q is not None]
    first = int(np.argmax(on_limit.to_numpy()))
    floor, reason = m._charge_stretch_cache[1:]
    assert reason is None
    knee, reach = condensing_tank_cutout(m), condensing_tank_reach(m)
    stop = reach - floor / m.K_tank_hx
    qualification = at_duty[:3]
    assert [step for step, _, _ in qualification] == [first] * 3
    assert [T for _, T, _ in qualification] == pytest.approx([knee, 0.5 * (knee + stop), stop])
    assert [q for _, _, q in qualification] == pytest.approx([m.hp_capacity, 0.5 * (m.hp_capacity + floor), floor])
    assert len(at_duty) == 3 + int(on_limit.sum())


def test_the_verdict_follows_the_source_state_of_each_step():
    """Outdoor air moving across the point where the pressure-ratio limit starts to hold the knee.

    R32, 15 kW: at -8 degC the condensing limit governs the stretch; at -15 degC the pressure
    ratio is at its ceiling at the knee, short of the nameplate duty (13.4 of 15 kW), and the
    charging is withheld. Every step is that of its own outdoor state: on the limit at -8 degC,
    and at -15 degC the step a ``"stop"`` model with the same band takes from the same state.
    """
    kwargs = dict(R32_COLD, T_tank_w_lower_bound=60.0, T_tank_w_upper_bound=62.0)
    m = _quiet(tmhp.AirSourceHeatPumpBoiler, **kwargs, condensing_limit_control="reduce_duty")
    stop = _stop_model_with_the_band_of(tmhp.AirSourceHeatPumpBoiler, kwargs, m)
    outdoor = [-8.0, -15.0, -15.0, -8.0, -15.0, -8.0, -8.0]
    state, rows = m.make_initial_state(57.0), []
    for i, T0 in enumerate(outdoor):
        inputs = dict(n=i, current_time_s=i * 120.0, T0=T0, dV_mix_w_out=0.0, T_sup_w=15.0, T_sur=20.0)
        state_next, row = _quiet(m.step, state, inputs, 120.0)
        if T0 < -14.0:
            _, reference = _quiet(stop.step, state, inputs, 120.0)
            assert set(row) - REDUCE_ONLY == set(reference), i
            assert all(_same_cell(row[key], reference[key]) for key in reference), i
            assert row["reduced_duty_fraction [-]"] == 0.0, i
            assert row["reduced_duty_withheld"] == (BINDING_PRESSURE_RATIO if row["hp_stop_reason"] else None), i
        else:
            assert row["reduced_duty_withheld"] is None, i
        rows.append(row)
        state = state_next
    on_limit = [row["reduced_duty_fraction [-]"] > 0.0 for row in rows]
    withheld = [row["reduced_duty_withheld"] for row in rows]
    # Both kinds of step occur, and a step after a withheld one charges on the limit again.
    assert BINDING_PRESSURE_RATIO in withheld and any(on_limit)
    last_withheld = max(i for i, reason in enumerate(withheld) if reason)
    assert any(on_limit[last_withheld + 1 :])
    assert max(row["T_tank_w [°C]"] for row in rows) > condensing_tank_cutout(m) + 1.0


def test_a_boiling_stop_of_a_model_is_not_flagged_as_withheld():
    """R600a with ``T_cond_max=110``: the nameplate cut-out is 103 degC, and the boiling point ends the run.

    The row is the one of ``"stop"`` (same band, 97-100 degC), its stop is ``boiling_limit``, and
    ``reduced_duty_withheld`` is ``None``: the stretch on the condensing limit lies above 100 degC
    and is never asked about.
    """
    kwargs = dict(
        ref="R600a",
        T_cond_max=110.0,
        PR_cycle_max=15.0,
        T_dis_max=None,
        min_on_time_s=5000.0,
        T_tank_w_lower_bound=97.0,
        T_tank_w_upper_bound=100.0,
    )
    m = _quiet(tmhp.WaterSourceHeatPumpBoiler, **kwargs, condensing_limit_control="reduce_duty")
    stop = _quiet(tmhp.WaterSourceHeatPumpBoiler, **kwargs)
    assert condensing_tank_cutout(m) > T_WATER_BOIL_C
    assert (stop.T_tank_w_lower_bound, stop.T_tank_w_upper_bound) == (m.T_tank_w_lower_bound, m.T_tank_w_upper_bound)
    asked = []
    inner = m._charge_stretch
    m._charge_stretch = lambda ctx: (asked.append(ctx.n), inner(ctx))[1]
    args = (2400.0, 1200.0, 96.0, np.zeros(2), np.full(2, 15.0))
    df = _quiet(m.analyze_dynamic, *args, T_source_w_schedule=np.full(2, 8.0))
    reference = _quiet(stop.analyze_dynamic, *args, T_source_w_schedule=np.full(2, 8.0))
    _assert_rows_are_those_of(df, reference)
    assert df["hp_stop_reason"].iloc[0] == STOP_BOILING_LIMIT
    assert df["reduced_duty_withheld"].isna().all() and (df["reduced_duty_fraction [-]"] == 0.0).all()
    assert not asked


# ---- a limit between the three points of the check ----

#: The final review's case: the default R290 water-source unit with a discharge limit of 92.408 degC and a
#: compressor whose isentropic efficiency dips around 35 rev/s. At the knee (49.5 rev/s), the middle (30.9 rev/s)
#: and the stop (15.0 rev/s) of the stretch at 16 degC source water the cycle delivers the condensing limit's duty;
#: between the knee and the middle (seen from 69.24 to 69.41 degC, about 33 rev/s) another limit holds it below that.
BETWEEN_KW = dict(
    T_dis_max=92.408,
    eta_cmp_isen=lambda pr, rps: 0.8 - 0.4 * math.exp(-(((rps - 35.0) / 3.0) ** 2)),
    T_tank_w_lower_bound=72.0,
    T_tank_w_upper_bound=73.0,
)


@pytest.fixture(scope="module")
def between_the_samples():
    """One hour from 68.95 degC, no draw, source water 16 degC, at 60, 600 and 3600 s steps.

    Per step size: the ``reduce_duty`` run, the run of a ``"stop"`` model with the same band, and
    every cycle the ``reduce_duty`` model solved at a duty of its own as ``(step, tank, asked,
    delivered, capacity_binding)``; with them the verdict the model kept for the source state.
    The 60 s run takes about a minute (each withheld step solves a cycle another limit holds, then
    the step of ``"stop"``): the tests that use it share one worker, so that it is run once.
    """
    runs = {}
    for dt in STEPS:
        m = _quiet(tmhp.WaterSourceHeatPumpBoiler, **BETWEEN_KW, condensing_limit_control="reduce_duty")
        stop = _stop_model_with_the_band_of(tmhp.WaterSourceHeatPumpBoiler, BETWEEN_KW, m)
        cycles = []
        inner = m._solve_cycle_at

        def spy(ctx, T_tank_w, Q_request=None, inner=inner, cycles=cycles):
            row = inner(ctx, T_tank_w, Q_request)
            if Q_request is not None:
                cycles.append((ctx.n, T_tank_w, Q_request, row.get("Q_ref_tank [W]"), row.get("capacity_binding")))
            return row

        m._solve_cycle_at = spy
        n = int(round(HORIZON / dt))
        args = (n * dt, dt, 68.95, np.zeros(n), np.full(n, 15.0))
        df = _quiet(m.analyze_dynamic, *args, T_source_w_schedule=np.full(n, 16.0))
        reference = _quiet(stop.analyze_dynamic, *args, T_source_w_schedule=np.full(n, 16.0))
        runs[dt] = (df, reference, cycles, m._charge_stretch_cache[1:], m)
    return runs


@pytest.mark.xdist_group("limit-between-the-samples")
def test_the_three_points_of_the_check_do_not_see_a_limit_between_them(between_the_samples):
    """The stretch qualifies: knee, middle and stop each deliver the condensing limit's duty, under no other limit."""
    for dt in STEPS:
        _, _, cycles, (floor, reason), m = between_the_samples[dt]
        assert reason is None and floor == pytest.approx(2447.3, rel=1e-4), dt
        knee, reach = condensing_tank_cutout(m), condensing_tank_reach(m)
        stop = reach - floor / m.K_tank_hx
        first = cycles[0][0]
        # The three cycles of the qualification, in the first step that got to the limit: asked == delivered.
        points = [(T, asked, got, binding) for step, T, asked, got, binding in cycles if step == first][:3]
        assert [T for T, _, _, _ in points] == pytest.approx([knee, 0.5 * (knee + stop), stop]), dt
        assert [asked for _, asked, _, _ in points] == pytest.approx([8000.0, 0.5 * (8000.0 + floor), floor]), dt
        for _, asked, got, binding in points:
            assert got == pytest.approx(asked, rel=1e-6) and binding in (None, BINDING_CONDENSING), dt


@pytest.mark.xdist_group("limit-between-the-samples")
def test_a_limit_between_the_three_points_withholds_the_steps_whose_cycle_lands_on_it(between_the_samples):
    """60 s steps: the cycle of the first steps lands where the other limit acts, and they are withheld.

    Their stretch on the limit would start at 68.95 degC; its mean (69.41 degC in the first step)
    asks for 6376 W and the cycle delivers 6217 W, held by another limit. Each such row says
    ``reduced_duty_withheld == "cycle"`` and is the row of the ``"stop"`` model with the same band
    (the compressor stays off above the nameplate cut-out), every column. The tank cools a little
    in each of them, and after 34 the cycle of the step is clear of the limit: the charge runs on
    the condensing limit from there.
    """
    df, reference, cycles, _, _ = between_the_samples[60.0]
    withheld = df.index[df["reduced_duty_withheld"].notna()].tolist()
    # The withheld rows are the first ones (34 here; the count depends on where a step's mean lands).
    assert withheld == list(range(len(withheld))) and 30 <= len(withheld) <= 38
    assert set(df["reduced_duty_withheld"].dropna()) == {BINDING_CYCLE}
    rows = df.iloc[withheld]
    assert (rows["hp_stop_reason"] == STOP_CONDENSING_LIMIT).all() and (rows["reduced_duty_fraction [-]"] == 0.0).all()
    assert (rows["runtime_fraction [-]"] == 0.0).all()

    def no_value(x):
        return x is None or (isinstance(x, float) and math.isnan(x))

    # Against the "stop" model, no tolerance. A cell without a value is None in the reference (a
    # column with nothing in it) and NaN here (the same column has text on later rows): both are empty.
    assert set(df.columns) - REDUCE_ONLY == set(reference.columns)
    for column in reference.columns:
        differing = [
            i
            for i in withheld
            if not (_same_cell(df[column].iloc[i], reference[column].iloc[i]))
            and not (no_value(df[column].iloc[i]) and no_value(reference[column].iloc[i]))
        ]
        assert not differing, (column, differing[:3])
    # The cycle that withheld the first step: at the mean of its stretch on the limit, short of the duty asked.
    step, T_mean, asked, got, binding = next(c for c in cycles[3:] if c[0] == 0)
    assert T_mean == pytest.approx(69.414, abs=2e-3) and asked == pytest.approx(6376.0, rel=1e-3)
    assert got < asked * (1.0 - DUTY_MATCH_RTOL) and got == pytest.approx(6217.0, rel=2e-3) and binding
    # After them the steps charge on the limit, and the hour ends near the set point.
    after = df.iloc[len(withheld) :]
    assert (after["reduced_duty_fraction [-]"] > 0.0).sum() >= 5 and after["reduced_duty_withheld"].isna().all()


@pytest.mark.xdist_group("limit-between-the-samples")
def test_such_a_limit_makes_the_charge_depend_on_the_step(between_the_samples):
    """600 s and 3600 s steps have the mean of their stretch elsewhere, and charge without a withheld row.

    The documented exception to the step-size agreement of a qualified charge: the hour differs by
    0.21 K, 4.8 % of the heat and 8.1 % of the electricity between 60 s steps and the longer ones
    (which agree with each other), because the 60 s run waits 34 minutes above the cut-out first.
    """
    totals = {}
    for dt in STEPS:
        df, _, _, _, _ = between_the_samples[dt]
        totals[dt] = (
            float(df["T_tank_w [°C]"].iloc[-1]),
            float(df["Q_ref_tank [W]"].sum() * dt) / 3.6e6,
            float(df["E_tot [W]"].sum() * dt) / 3.6e6,
            int(df["reduced_duty_withheld"].notna().sum()),
        )
    assert totals[600.0][3] == 0 and totals[3600.0][3] == 0 and totals[60.0][3] > 0
    assert totals[60.0][:3] == pytest.approx((72.730, 0.48352, 0.20388), rel=1e-2, abs=0.03)
    assert totals[600.0][:3] == pytest.approx((72.517, 0.46049, 0.18730), rel=2e-3, abs=5e-3)
    assert totals[3600.0][:3] == pytest.approx((72.518, 0.46049, 0.18730), rel=2e-3, abs=5e-3)
    # The two longer step sizes agree as a qualified charge does; the 60 s run does not.
    assert totals[3600.0][1] == pytest.approx(totals[600.0][1], rel=1e-3)
    assert 0.15 < totals[60.0][0] - totals[600.0][0] < 0.27
    assert 0.035 < 1.0 - totals[600.0][1] / totals[60.0][1] < 0.06
    assert 0.06 < 1.0 - totals[600.0][2] / totals[60.0][2] < 0.10
