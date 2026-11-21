"""Charging a tank past the nameplate cut-out at reduced duty (#204).

``condensing_limit_control="reduce_duty"`` lets a boiler keep charging above the tank
temperature at which its nameplate duty condenses on ``T_cond_max``: the compressor runs at the
largest duty inside the limit, ``K_tank_hx * (T_reach - T)``, down to its smallest continuous
duty. The tank then approaches ``T_reach`` as a first-order lag, which the in-step thermostat
(:func:`tmhp.dynamic_context.simulate_thermostat_step`, ``duty_limit``) follows in closed form,
so the end temperature, the heat and the electricity of a charge on that limit do not depend on
the step. The last part of this file is the review of that first version: another limit governing
above the cut-out, the valid tank range of the search for the smallest duty, the stop that acted,
the rest after a stop, and how far the step-size statement holds in a tank that loses heat.

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
    BINDING_CONDENSING,
    condensing_tank_cutout,
    condensing_tank_reach,
)
from tmhp.dynamic_context import (
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


def _resolve(T_start=56.0, T_upper=63.0, dt=3600.0, floor=2_000.0, on_limit_runs=True, calls=None, q_cycle=Q_ON, **kw):
    """``resolve_step_operation`` on the tank of the event tests, with a cycle of COP 3 (2.5 on the limit)."""
    calls = {} if calls is None else calls

    def residual_free(T_K):
        return (C * T_K - C * (T_start + 273.15)) / C

    def solve_cycle(T_C, fraction):
        calls.setdefault("full", []).append((T_C, fraction))
        return {
            "hp_is_on": True,
            "Q_ref_tank [W]": q_cycle,
            "E_tot [W]": q_cycle / 3.0,
            "T_ref_cond_sat_l [°C]": T_C + 5.0,
        }

    def solve_cycle_at_duty(T_C, q, fraction):
        calls.setdefault("limit", []).append((T_C, q, fraction))
        if not on_limit_runs:
            return {"hp_is_on": False, "failure_reason": "cycle_invalid"}
        row = {"hp_is_on": True, "Q_ref_tank [W]": q, "E_tot [W]": q / 2.5, "T_ref_cond_sat_l [°C]": 65.0}
        row.update({"capacity_clamped": "max", "capacity_binding": BINDING_CONDENSING, "Q_request [W]": Q_ON})
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
        duty_line=(LIMIT.T_zero_C, LIMIT.K),
        min_duty=min_duty,
        solve_cycle_at_duty=solve_cycle_at_duty,
        rate_keys=("Q_ref_tank [W]", "E_tot [W]"),
    )
    args.update(kw)
    return (*resolve_step_operation(**args), calls)


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


def test_without_a_floor_or_a_cycle_on_the_limit_the_step_keeps_the_stop():
    """No continuous duty below the request, or a cycle the limit itself refuses on its line: the stop at the knee.

    (The follow-up review changed the third case on purpose. The first version kept the stop for
    any cycle without an operating point on the limit. A cycle that fails there for another
    reason than the condensing limit is not a condensing stop: the run still ends at the knee,
    with the same run time and the same cycle, but the step ends off and without a cut-out, and
    the step after it reports the cycle's failure.)
    """
    stop, _, _ = _resolve(duty_line=None, T_limit_C=LIMIT.knee_C(Q_ON))
    assert stop.limit_stop and stop.runtime_fraction * 3600.0 == pytest.approx(240.0)
    refused = dict(on_limit_runs=False, duty_limit_failure="cycle_invalid")  # the stub's failure is the limit's own
    for kw in (dict(floor=None), dict(floor=Q_ON), refused):
        run, row, _ = _resolve(**kw)
        assert run == stop, kw
        assert row["Q_ref_tank [W]"] == Q_ON and "capacity_clamped" not in row
    # Another failure than the limit's, above a stretch that ran at the full duty.
    run, row, _ = _resolve(on_limit_runs=False)
    assert run.runtime_fraction == stop.runtime_fraction and run.T_on_mean_C == stop.T_on_mean_C
    assert row["Q_ref_tank [W]"] == Q_ON and "capacity_clamped" not in row
    assert not run.limit_stop and in_step_stop_reason(run, T_WATER_BOIL_C) is None
    assert not run.running_end and run.call_end and run.starts == stop.starts
    after, row_after, _ = _resolve(T_start=LIMIT.knee_C(Q_ON) + 1e-9, dt=600.0, on_limit_runs=False)
    assert after.heat_unavailable and row_after["failure_reason"] == "cycle_invalid"


def test_a_cycle_already_below_the_floor_keeps_the_stop():
    """Another limit holds the full-duty cycle below this limit's smallest duty: nothing to run on between them.

    The step is then the stop's (cut-out at the knee of the request, 60 degC, below this limit's
    stop at 64 degC), so no step resolved on the limit runs above the stop at less than the floor.
    """
    stop, stop_row, _ = _resolve(duty_line=None, T_limit_C=LIMIT.knee_C(Q_ON), T_upper=64.5, q_cycle=1_500.0)
    run, row, calls = _resolve(T_upper=64.5, q_cycle=1_500.0)
    assert run == stop and row == stop_row
    assert run.reduced_fraction == 0.0 and "limit" not in calls
    # ... and a cycle above the floor is resolved on the limit, never above its stop.
    run, row, calls = _resolve(T_upper=64.5, q_cycle=2_500.0)
    assert run.reduced_fraction > 0.0 and run.T_reduced_mean_C <= LIMIT.stop_C
    assert calls["limit"][0][1] >= LIMIT.Q_min


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
    floor = m._charge_floor_cache[1]
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
        floor = m._charge_floor_cache[1]
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


# ---------------------------------------------------------------------------
# review of 719b880a: another limit on the way, the stop that acted, the rest after a stop
# ---------------------------------------------------------------------------

OTHER = "source_fluid_freezing"
REFUSED = "condensing_temp_above_max"


def _resolve_two_limits(other, T_start=61.0, T_upper=64.5, dt=300.0, failure=OTHER, calls=None, **kw):
    """``resolve_step_operation`` on the tank of the event tests with a second limit on the cycle.

    ``other(T_C)`` is the largest duty that limit leaves at tank temperature ``T_C`` [W]; zero or
    less means no operating point, and the cycle fails with ``failure``. The cycle delivers the
    smallest of what it is asked for, the condensing limit's duty and the other limit's, at COP 3.
    """
    calls = {} if calls is None else calls

    def residual_free(T_K):
        return (C * T_K - C * (T_start + 273.15)) / C

    def cycle(T_C, asked):
        allowed = other(T_C)
        if allowed <= 0.0:
            return {"hp_is_on": False, "failure_reason": failure}
        on_line = LIMIT.duty(T_C)
        q = min(asked, on_line, allowed)
        row = {"hp_is_on": True, "Q_ref_tank [W]": q, "E_tot [W]": q / 3.0}
        if q < Q_ON:
            binding = OTHER if allowed < min(asked, on_line) else BINDING_CONDENSING
            row.update({"capacity_clamped": "max", "capacity_binding": binding, "Q_request [W]": Q_ON})
        return row

    def solve_cycle(T_C, fraction):
        calls.setdefault("full", []).append(T_C)
        return cycle(T_C, Q_ON)

    def solve_cycle_at_duty(T_C, q, fraction):
        calls.setdefault("limit", []).append((T_C, q))
        return cycle(T_C, q)

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
        duty_line=(LIMIT.T_zero_C, LIMIT.K),
        min_duty=lambda: LIMIT.Q_min,
        solve_cycle_at_duty=solve_cycle_at_duty,
        rate_keys=("Q_ref_tank [W]", "E_tot [W]"),
        duty_limit_failure=REFUSED,
    )
    args.update(kw)
    return (*resolve_step_operation(**args), calls)


def _charge_two_limits(other, parts, total, T_start=61.0, **kw):
    """Tank temperature, heat [J], time on the condensing limit [s] and rows of ``total`` seconds in ``parts`` steps."""
    T, call, running, age, heat, on_limit, rows = T_start, True, False, math.inf, 0.0, 0.0, []
    dt = total / parts
    for _ in range(parts):
        run, row, _ = _resolve_two_limits(
            other, T_start=T, dt=dt, call_start=call, running_prev=running, phase_age_s=age, **kw
        )
        if row is not None and row.get("hp_is_on") and run.runtime_fraction > 0.0:
            q = row["Q_ref_tank [W]"] * run.runtime_fraction * dt
            heat, T = heat + q, T + q / C
            # The compressor is never above the tank temperature its step ends at (the tank only warms here).
            assert run.T_on_max_C <= T + 1e-9
        on_limit += run.reduced_fraction * dt
        call, running, age = run.call_end, run.running_end, run.phase_age_end_s
        rows.append((run, row))
    return T, heat, on_limit, rows


def test_another_limit_below_the_condensing_one_governs_the_run():
    """Above the nameplate cut-out a second limit leaves 3 kW where the condensing limit would leave 8 kW.

    Review, blocker 1: the events took the condensing limit's duty for the run (7.3 kW in the
    reported case) while the cycle delivered what the other limit left (3.1 kW), reported the step
    as run on the condensing limit, and did not solve the run again. Now the events are those of
    the duty the cycle delivers; the run is on the condensing limit only above the tank temperature
    at which *that* duty meets it (63.5 degC for 3 kW).
    """

    def other(T_C):
        return 3_000.0

    run, row, calls = _resolve_two_limits(other, dt=300.0)
    assert run.runtime_fraction == 1.0 and run.reduced_fraction == 0.0 and not run.limit_stop
    assert row["Q_ref_tank [W]"] == 3_000.0 and row["capacity_binding"] == OTHER
    # The cycle of the run is solved at the run's own mean (61 -> 62.5 degC), not at the mean the
    # condensing limit's duty would have given.
    assert any(solved == pytest.approx(61.75, abs=1e-6) for solved in calls["full"])
    # A longer step gets to 63.5 degC after 500 s and runs on along the condensing limit to its stop.
    run, row, _ = _resolve_two_limits(other, dt=1200.0)
    t_limit = TAU * math.log(1.5 / 1.0)
    assert run.runtime_fraction * 1200.0 == pytest.approx(500.0 + t_limit, rel=1e-9)
    assert run.reduced_fraction * 1200.0 == pytest.approx(t_limit, rel=1e-9)
    assert run.limit_stop and run.limit_stop_C == pytest.approx(64.0)
    assert row["Q_ref_tank [W]"] * run.runtime_fraction * 1200.0 == pytest.approx(3.0 * C, rel=1e-9)
    assert row["capacity_binding"] == OTHER  # the limit that governed most of the run
    # The same 1200 s in one step and in 4, 12 and 120: the same tank, heat and time on the limit.
    one = _charge_two_limits(other, 1, 1200.0)
    assert one[0] == pytest.approx(64.0, abs=1e-9)
    for parts in (4, 12, 120):
        T_end, heat, on_limit, _ = _charge_two_limits(other, parts, 1200.0)
        assert T_end == pytest.approx(one[0], abs=1e-6), parts
        assert heat == pytest.approx(one[1], rel=1e-9), parts
        assert on_limit == pytest.approx(one[2], abs=1e-6), parts


def test_no_operating_point_above_the_cut_out_is_the_cycles_own_failure():
    """A cycle that cannot run for another reason than the condensing limit keeps its failure reason.

    Review, blocker 1: with the source 0.9 K colder the cycle's ``source_fluid_freezing`` failure
    was reported as a stop at the condensing limit with ``failure_reason="none"``.
    """
    run, row, _ = _resolve_two_limits(lambda T_C: 0.0, dt=600.0)
    assert run.runtime_fraction == 0.0 and run.heat_unavailable and not run.limit_stop
    assert run.call_end and not run.running_end
    assert row["failure_reason"] == OTHER and not row["hp_is_on"]
    # A cycle the condensing limit itself refuses on its own line is the stop it always was.
    run, row, _ = _resolve_two_limits(lambda T_C: 0.0, dt=600.0, failure=REFUSED)
    assert run.runtime_fraction == 0.0 and run.limit_stop and not run.heat_unavailable and row is None
    assert in_step_stop_reason(run, T_WATER_BOIL_C) == STOP_CONDENSING_LIMIT


def test_an_envelope_that_closes_inside_the_run_is_not_overrun():
    """A second limit whose duty falls with the tank temperature and ends at 62.4 degC.

    One cycle stands for a run at one duty. Before a run is taken above every tank temperature at
    which a cycle of the step ran, the cycle is solved at the run's warmest point; where it has no
    operating point the run ends at the warmest temperature a cycle did run at. The charge then
    closes on the temperature at which the envelope closes, from below, at any step.
    """

    def other(T_C):
        return 6_000.0 - 2_000.0 * (T_C - 61.0) if T_C <= 62.4 else 0.0

    ends = {}
    for parts in (60, 12, 6, 1):
        T_end, heat, on_limit, rows = _charge_two_limits(other, parts, 3600.0)
        ends[parts] = T_end
        assert 61.0 < T_end <= 62.4 + 1e-9, parts
        assert on_limit == 0.0, parts  # the condensing limit never governed
        ran = [row for run, row in rows if row is not None and row.get("hp_is_on") and run.runtime_fraction > 0.0]
        assert ran and all(row["capacity_binding"] == OTHER for row in ran), parts
        assert all(run.limit_stop_C is None for run, _ in rows), parts  # none of the two cut-outs acted
        if parts > 1:
            # Once the envelope has closed the steps report the cycle's failure, not a stop.
            run, row = rows[-1]
            assert run.heat_unavailable and row["failure_reason"] == OTHER, parts
    assert ends[60] > 62.2 and ends[12] > 61.9 and ends[6] > 61.9


def test_the_stop_that_acted_is_the_one_reported():
    """Review, should-fix 3: a stop at the boiling point was reported as a condensing stop.

    The stop a step is resolved with is the lower of the boiling point and the limit's stop; the
    caller knows the nameplate cut-out only. The run carries the stop that acted.
    """
    hot = DutyLimit(T_zero_C=102.0, K=2_000.0, Q_min=2_000.0)  # knee 97 degC, stop 101 degC: above boiling
    run = _sim(T_start_C=96.0, T_lower=90.0, T_upper=99.0, min_on_time_s=5_000.0, duty_limit=hot)
    assert run.limit_stop and run.limit_stop_C == T_WATER_BOIL_C and run.T_on_max_C == pytest.approx(100.0)
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
    not what the compressor can do, and reduced-duty charging resolves it as it happens: stop at
    the stop, rest, restart (a counted start), run back along the limit.
    """
    loss = ((50.0, -500.0), (70.0, -500.0))
    kw = dict(T_start_C=63.9, T_upper=64.5, free_heat=loss, dt=3600.0)
    # On the limit against the loss: C dT/dt = 2000 (64.75 - T), time constant 300 s.
    t_first = TAU * math.log((64.75 - 63.9) / (64.75 - 64.0))
    rest = 600.0  # the tank cools 0.5 K
    t_back = TAU * math.log((64.75 - 63.5) / (64.75 - 64.0))
    cycles = int((3600.0 - t_first) // (rest + t_back))
    run = _sim(min_off_time_s=rest, rest_before_restart=True, **kw)
    assert run.starts == 1 + cycles == 5
    assert run.runtime_fraction * 3600.0 == pytest.approx(t_first + cycles * t_back, rel=1e-9)
    assert run.reduced_fraction == run.runtime_fraction
    assert not run.running_end and run.call_end and run.limit_stop
    assert run.phase_age_end_s == pytest.approx(3600.0 - t_first - cycles * (rest + t_back), rel=1e-9)
    assert run.phase_age_end_s < rest  # resting, and for less than the minimum rest time
    # Without a minimum rest time the hold stands (what the cycling tends to as the rest goes to zero) ...
    hold = _sim(min_off_time_s=0.0, rest_before_restart=True, **kw)
    assert hold == _sim(**kw)
    assert hold.starts == 1 and hold.running_end and hold.phase_age_end_s == 3600.0
    assert hold.runtime_fraction * 3600.0 == pytest.approx(t_first + (3600.0 - t_first) * 500.0 / 2_000.0, rel=1e-9)
    # ... and a shorter rest gives more starts and a run time that tends to the hold's.
    short = _sim(min_off_time_s=60.0, rest_before_restart=True, **kw)
    assert short.starts > run.starts and run.runtime_fraction < short.runtime_fraction < hold.runtime_fraction
    # The default leaves the hold alone whatever the rest time: the multi-node tank's hold, as on main.
    assert _sim(min_off_time_s=rest, **kw) == _sim(**kw)
    plain = dict(kw, duty_limit=None, T_limit_C=64.0)
    assert _sim(min_off_time_s=rest, **plain) == _sim(**plain)
    rested = _sim(min_off_time_s=rest, rest_before_restart=True, **plain)
    assert rested.starts > 1 and rested.runtime_fraction < _sim(**plain).runtime_fraction


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


def test_merged_row_names_the_limit_that_governed_most_of_the_run():
    full = {"Q_ref_tank [W]": 3_000.0, "E_tot [W]": 1_000.0, "hp_is_on": True}
    full.update({"capacity_clamped": "max", "capacity_binding": OTHER, "Q_request [W]": 10_000.0})
    reduced = {"Q_ref_tank [W]": 2_500.0, "E_tot [W]": 900.0, "hp_is_on": True}
    reduced.update({"capacity_clamped": "max", "capacity_binding": BINDING_CONDENSING, "Q_request [W]": 10_000.0})
    keys = ("Q_ref_tank [W]", "E_tot [W]")
    assert merge_running_rows(full, 0.6, reduced, 0.2, keys)["capacity_binding"] == OTHER
    assert merge_running_rows(full, 0.2, reduced, 0.6, keys)["capacity_binding"] == BINDING_CONDENSING


# ---- the same on the models ----


def test_source_freezing_above_the_cut_out_is_followed_at_its_own_duty():
    """Review, blocker 1, water-source: source water at 4 degC, tank at 68.1 degC, set points above.

    The source water's freezing limit leaves 3.1 kW where the condensing limit would leave 7.3 kW.
    The rows that limit governs are not on the condensing limit, and ten minutes come out the same
    in 60 s steps and in one step (before: 72.480 degC / 0.519 kWh against 72.146 degC / 0.480 kWh).
    """
    kw = dict(T_dis_max=None, T_tank_w_lower_bound=75.0, T_tank_w_upper_bound=76.0)
    totals = {}
    for dt in (60.0, 600.0):
        m = _quiet(tmhp.WaterSourceHeatPumpBoiler, condensing_limit_control="reduce_duty", **kw)
        n = int(600.0 / dt)
        df = _quiet(
            m.analyze_dynamic, n * dt, dt, 68.1, np.zeros(n), np.full(n, 7.0), T_source_w_schedule=np.full(n, 4.0)
        )
        assert (df["failure_reason"] == "none").all() and df["hp_stop_reason"].isna().all(), dt
        totals[dt] = (
            float(df["T_tank_w [°C]"].iloc[-1]),
            float(df["Q_ref_tank [W]"].sum() * dt),
            float(df["E_tot [W]"].sum() * dt),
        )
        if dt == 60.0:
            first = df.iloc[0]
            assert first["capacity_binding"] == "source_fluid_freezing" and first["reduced_duty_fraction [-]"] == 0.0
            assert first["Q_ref_tank [W]"] < 0.5 * m.K_tank_hx * (condensing_tank_reach(m) - 68.1)
            # Only a row that condenses on the limit counts as run on it.
            on_limit = df["reduced_duty_fraction [-]"] > 0.0
            freezing = df["capacity_binding"] == "source_fluid_freezing"
            assert (df.loc[freezing, "reduced_duty_fraction [-]"] < 1.0).all()
            assert (df.loc[on_limit, "T_ref_cond_sat_v [°C]"] > m.T_cond_max - 0.5).all()
    fine, coarse = totals[60.0], totals[600.0]
    assert coarse[0] == pytest.approx(fine[0], abs=0.05)
    assert coarse[1] == pytest.approx(fine[1], rel=0.01)
    assert coarse[2] == pytest.approx(fine[2], rel=0.01)


def test_a_source_too_cold_to_run_at_all_keeps_its_failure_reason():
    """Review, blocker 1: at 3.1 degC the freezing limit leaves no duty, and the row has to say so."""
    m = _quiet(
        tmhp.WaterSourceHeatPumpBoiler,
        condensing_limit_control="reduce_duty",
        T_dis_max=None,
        T_tank_w_lower_bound=75.0,
        T_tank_w_upper_bound=76.0,
    )
    df = _quiet(
        m.analyze_dynamic, 600.0, 300.0, 68.1, np.zeros(2), np.full(2, 7.0), T_source_w_schedule=np.full(2, 3.1)
    )
    assert (df["failure_reason"] == "source_fluid_freezing").all()
    assert df["hp_stop_reason"].isna().all() and not df["hp_is_on"].any()
    assert (df["reduced_duty_fraction [-]"] == 0.0).all()


def test_discharge_limit_above_the_cut_out_is_followed_and_not_overrun():
    """Review, blocker 1, air-source: R32 at -7 degC with ``T_dis_max=105``.

    The discharge-temperature limit leaves less than the condensing limit from 57 degC and no
    operating point above about 59 degC. The charge follows that limit (its rows are not on the
    condensing limit), ends where the cycle stops existing with the cycle's own failure reason,
    and comes out the same in 60 s steps, in 300 s steps and in one 600 s step. (A step that gets to
    where its full-duty cycle meets the condensing limit does not stop there while the cycle still
    runs at less: the rest of the step is resolved from that tank temperature.)
    """
    kw = dict(ISSUE, T_dis_max=105.0)
    out = {}
    for dt in (60.0, 300.0, 600.0):
        m = _quiet(tmhp.AirSourceHeatPumpBoiler, condensing_limit_control="reduce_duty", **kw)
        n = int(600.0 / dt)
        df = _quiet(m.analyze_dynamic, n * dt, dt, 56.0, np.zeros(n), np.full(n, -7.0))
        ran = df["runtime_fraction [-]"] > 0.0
        assert ran.any(), dt
        assert (df.loc[ran, "capacity_binding"] == "discharge_temperature").all(), dt
        assert (df["reduced_duty_fraction [-]"] == 0.0).all(), dt
        assert (df.loc[ran, "T_ref_cmp_out [°C]"] <= 105.0 + 0.011).all(), dt
        assert (df.loc[ran, "T_ref_cond_sat_v [°C]"] <= 65.0 + 0.011).all(), dt
        out[dt] = df
    fine = out[60.0]
    # Where the cycle stops existing the rows carry its failure, not a condensing stop.
    off = fine["runtime_fraction [-]"] == 0.0
    assert off.any() and (fine.loc[off, "failure_reason"] == "discharge_temp_above_max").all()
    assert fine.loc[off, "hp_stop_reason"].isna().all()

    def totals(df, dt):
        return (
            float(df["T_tank_w [°C]"].iloc[-1]),
            float(df["Q_ref_tank [W]"].sum() * dt),
            float(df["E_tot [W]"].sum() * dt),
        )

    end_fine, heat_fine, power_fine = totals(fine, 60.0)
    T_top = float(fine["T_tank_w [°C]"].max())
    for dt in (300.0, 600.0):
        end, heat, power = totals(out[dt], dt)
        assert end == pytest.approx(end_fine, abs=0.1), dt
        assert heat == pytest.approx(heat_fine, rel=0.03) and power == pytest.approx(power_fine, rel=0.03), dt
        # No step size takes the tank above where the fine steps end the charge.
        assert out[dt]["T_tank_w [°C]"].max() <= T_top + 0.1, dt
    # The one 600 s step went on past 57.99 degC, where its full-duty cycle meets the condensing limit.
    assert totals(out[600.0], 600.0)[0] > 58.5


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
    """Review, should-fix 3: the stop the step applied was 100 degC, the row said ``condensing_limit``."""
    m = _quiet(
        tmhp.WaterSourceHeatPumpBoiler,
        ref="R600a",
        T_cond_max=101.0,
        PR_cycle_max=15.0,
        T_dis_max=None,
        min_on_time_s=5000.0,
        T_tank_w_lower_bound=97.0,
        T_tank_w_upper_bound=100.0,
        condensing_limit_control="reduce_duty",
    )
    df = _quiet(
        m.analyze_dynamic, 2400.0, 2400.0, 93.891079, np.zeros(1), np.full(1, 15.0), T_source_w_schedule=np.full(1, 8.0)
    )
    row = df.iloc[0]
    # With the source at 8 degC the smallest duty meets the limit above 100 degC ...
    floor = m._charge_floor_cache[1]
    assert condensing_tank_reach(m) - floor / m.K_tank_hx > T_WATER_BOIL_C
    # ... so the minimum run time carried the compressor to the boiling point, which stopped it.
    assert row["hp_stop_reason"] == STOP_BOILING_LIMIT and row["failure_reason"] == "none"
    assert row["reduced_duty_fraction [-]"] > 0.0 and row["T_tank_w [°C]"] <= T_WATER_BOIL_C


def test_run_time_and_compressor_state_agree_at_the_stop():
    """Review, should-fix 4: three 1200 s steps at the stop with minimum run and rest times of 1800 s.

    Before: run-time fractions 0.196 / 0.175 / 0.175 with one start, and a final state "running for
    3600 s" although the compressor had run 654 s. With a minimum rest time the stop is a real one.
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
    state, ran_s, since_start, starts, rows = m.make_initial_state(61.8), 0.0, 0.0, 0, []
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
    assert rows[0]["runtime_fraction [-]"] < 0.05 and rows[0]["T_tank_w [°C]"] < 62.07
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
    state = hold.make_initial_state(61.8)
    fractions = []
    for i in range(3):
        inputs = dict(n=i, current_time_s=i * 1200.0, T0=35.0, dV_mix_w_out=1e-5, T_sup_w=15.0, T_sur=20.0)
        state, row = _quiet(hold.step, state, inputs, 1200.0)
        fractions.append(row["runtime_fraction [-]"])
        assert row["hp_stop_reason"] == STOP_CONDENSING_LIMIT and row["hp_starts [-]"] == (1 if i == 0 else 0)
    assert fractions == pytest.approx([0.19594, 0.17453, 0.17453], abs=1e-4)
    assert state.hp_running_prev and state.hp_phase_age_s == 3600.0


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


def test_a_run_goes_on_past_the_knee_of_its_first_duty_where_the_cycle_still_runs():
    """A second limit whose duty falls with the tank temperature, below the condensing limit's all the way.

    Stop-time review of the fix above: a step that got to where its full-duty cycle meets the
    condensing limit stopped there, although the cycle still ran at the lower duty the other limit
    left, and the next step went on from it; one long step so ended lower than short ones (57.99
    against 58.99 degC in the air-source case). The rest of the step is now resolved from that
    tank temperature, with the duty the other limit leaves there, and joined to the run.
    """

    def other(T_C):
        return 9_000.0 - 1_500.0 * (T_C - 58.0)  # 9 kW at 58 degC, 3 kW at 62 degC

    # From 56 degC one hour in one step: the cycle delivers 9 kW at the mean of the first run, whose
    # knee lies at 60.5 degC. The other limit leaves 5.25 kW there, and 2 kW (the smallest duty) at 62.67 degC.
    fine = _charge_two_limits(other, 360, 3600.0, T_start=56.0)
    one = _charge_two_limits(other, 1, 3600.0, T_start=56.0)
    run, row = one[3][0]
    first_knee = LIMIT.knee_C(min(Q_ON, other(57.0)))
    assert run.T_on_max_C > first_knee + 1.0
    # The charge ends where the other limit leaves no more than the smallest continuous duty (62.67 degC).
    assert fine[0] == pytest.approx(62.67, abs=0.05)
    assert one[0] == pytest.approx(fine[0], abs=0.15)
    assert one[1] == pytest.approx(fine[1], rel=0.02)
    # The row is the run-time-weighted mean of the stages' cycles: its heat is the step's.
    assert row["Q_ref_tank [W]"] * run.runtime_fraction * 3600.0 == pytest.approx(one[1], rel=1e-12)
    assert row["capacity_binding"] == OTHER and run.reduced_fraction == 0.0 and one[2] == 0.0
    # The joined run is one run: one start, and no cut-out is reported for the knees it passed.
    assert run.starts == 1 and run.running_start
    for parts in (36, 6):
        T_end, heat, _, _ = _charge_two_limits(other, parts, 3600.0, T_start=56.0)
        assert T_end == pytest.approx(fine[0], abs=0.15), parts
        assert heat == pytest.approx(fine[1], rel=0.02), parts


def test_a_row_of_several_stretches_names_the_limit_with_the_longest_run_time_in_total():
    """Stop-time review of the stages: merging two cycles at a time named the limit of the last pair.

    Three stretches, 40 s under one limit, 35 s under another, 25 s under the first again: the first
    governed 65 s. Merged two at a time, the last two give the second limit (35 against 25 s) and
    that pair (60 s) then outweighs the first stretch (40 s). A merged row remembers its cycles, so
    merging it again weighs every cycle with its own run time, whatever the grouping.
    """
    keys = ("Q_ref_tank [W]", "E_tot [W]")

    def cycle(q, binding, speed):
        row = {"hp_is_on": True, "Q_ref_tank [W]": q, "E_tot [W]": q / 3.0, "cmp_rpm [rpm]": speed}
        row.update({"capacity_clamped": "max", "capacity_binding": binding, "Q_request [W]": 10_000.0})
        return row

    a1, b, a2 = cycle(9_000.0, "first", 6000.0), cycle(6_000.0, "second", 4000.0), cycle(4_000.0, "first", 3000.0)
    left = merge_running_rows(merge_running_rows(a1, 40.0, b, 35.0, keys), 75.0, a2, 25.0, keys)
    right = merge_running_rows(a1, 40.0, merge_running_rows(b, 35.0, a2, 25.0, keys), 60.0, keys)
    assert merge_running_rows(b, 35.0, a2, 25.0, keys)["capacity_binding"] == "second"  # the pair alone
    for row in (left, right):
        assert row["capacity_binding"] == "first"
        assert row["Q_ref_tank [W]"] == pytest.approx((40 * 9_000.0 + 35 * 6_000.0 + 25 * 4_000.0) / 100.0)
        assert row["Q_max [W]"] == pytest.approx(row["Q_ref_tank [W]"])
        assert row["cmp_rpm [rpm]"] == 6000.0  # the state of the one cycle that ran longest
    assert dict(left) == pytest.approx(dict(right))
    # The weights of a merged row are rescaled to the weight it is given (fractions here, seconds above).
    scaled = merge_running_rows(a1, 0.4, merge_running_rows(b, 35.0, a2, 25.0, keys), 0.6, keys)
    assert scaled["capacity_binding"] == "first" and scaled["Q_ref_tank [W]"] == pytest.approx(left["Q_ref_tank [W]"])


def test_a_cycle_at_the_speed_ceiling_counts_as_under_a_limit():
    """A cycle at the compressor's speed ceiling has ``capacity_clamped == "max"`` and names no binding.

    The boiler completes such a row with ``capacity_binding = "compressor"``. Merged with a shorter
    stretch under another limit, the row has to stay one the boiler completes that way (before: it
    took the other stretch's binding, whatever the run times).
    """
    from tmhp._boiler_results import complete_capacity_keys

    keys = ("Q_ref_tank [W]", "E_tot [W]")
    ceiling = {"hp_is_on": True, "Q_ref_tank [W]": 13_000.0, "E_tot [W]": 5_000.0, "capacity_clamped": "max"}
    other = {"hp_is_on": True, "Q_ref_tank [W]": 9_500.0, "E_tot [W]": 4_000.0, "capacity_clamped": "max"}
    other.update({"capacity_binding": "discharge_temperature", "Q_max [W]": 9_500.0, "Q_request [W]": 15_000.0})
    row = merge_running_rows(ceiling, 363.0, other, 103.0, keys)
    assert row["capacity_clamped"] == "max" and "capacity_binding" not in row
    assert complete_capacity_keys(dict(row), 15_000.0)["capacity_binding"] == "compressor"
    assert row["Q_max [W]"] == pytest.approx((363 * 13_000.0 + 103 * 9_500.0) / 466.0)
    # The shorter of the two governs when it is the one at the ceiling's side ...
    row = merge_running_rows(ceiling, 100.0, other, 300.0, keys)
    assert row["capacity_binding"] == "discharge_temperature" and row["Q_request [W]"] == 15_000.0
    # ... and a cycle that is not held at all leaves the keys to the one that is.
    free = {"hp_is_on": True, "Q_ref_tank [W]": 15_000.0, "E_tot [W]": 5_500.0, "capacity_clamped": None}
    assert merge_running_rows(free, 400.0, other, 50.0, keys)["capacity_binding"] == "discharge_temperature"
    assert "capacity_binding" not in merge_running_rows(free, 400.0, ceiling, 50.0, keys)


def test_one_long_step_names_the_limit_that_governed_most_of_its_run():
    """R32 at -7 degC with ``T_dis_max=105``, half an hour from 50 degC in one step and in 60 s steps.

    Up to about 55.5 degC the compressor is at its speed ceiling, above it the discharge limit holds
    it. The one step is resolved in several stages; its row has to name the limit the run spent
    most of its time under, as the rows of the short steps do in total.
    """
    kw = dict(ISSUE, T_dis_max=105.0)
    under = {}
    for dt in (60.0, 1800.0):
        m = _quiet(tmhp.AirSourceHeatPumpBoiler, condensing_limit_control="reduce_duty", **kw)
        n = int(1800.0 / dt)
        df = _quiet(m.analyze_dynamic, n * dt, dt, 50.0, np.zeros(n), np.full(n, -7.0))
        ran = df["runtime_fraction [-]"] > 0.0
        seconds = (df.loc[ran, "runtime_fraction [-]"] * dt).groupby(df.loc[ran, "capacity_binding"]).sum()
        under[dt] = seconds.to_dict()
    fine, one = under[60.0], under[1800.0]
    assert set(fine) == {"compressor", "discharge_temperature"} and fine["compressor"] > fine["discharge_temperature"]
    assert list(one) == ["compressor"]
    assert sum(one.values()) == pytest.approx(sum(fine.values()), rel=0.03)


def test_a_limit_just_below_the_condensing_one_is_followed_to_where_it_ends():
    """Stop-time review of the stages: a second limit 100 W below the condensing limit all the way.

    A run at one duty ends where that duty meets the condensing limit, and with the two limits this
    close each run gets a few hundredths of a kelvin further: one 3600 s step used up its stages
    and reported a cut-out at 63.51 degC with 2.9 kW still to be had, where 360 steps reach
    63.95 degC (the limit leaves the smallest duty, 2 kW, there). The other limit is now followed as
    what it is, a duty that falls with the tank temperature: a straight line through the cycle at
    the start of the run and the one at its mean, integrated in closed form like the condensing limit.
    """

    def other(T_C):
        return LIMIT.duty(T_C) - 100.0

    floor_C = LIMIT.stop_C - 100.0 / LIMIT.K  # 63.95 degC
    ends = {}
    for parts in (360, 36, 6, 1):
        tank, call, running, age, heat, dt = 61.0, True, False, math.inf, 0.0, 3600.0 / parts
        for _ in range(parts):
            run, row, _ = _resolve_two_limits(
                other, T_start=tank, dt=dt, call_start=call, running_prev=running, phase_age_s=age
            )
            assert run.reduced_fraction == 0.0, parts  # never on the condensing limit: 100 W below it
            # ... and never stopped by it: the other limit ends the charge 0.05 K below the condensing
            # cut-out (64 degC), and neither that end nor the steps after it are a condensing stop.
            assert in_step_stop_reason(run, T_WATER_BOIL_C) is None, (parts, tank, run)
            assert not run.heat_unavailable, parts  # nor a cycle that failed: it runs, at too little
            if row is not None and row.get("hp_is_on") and run.runtime_fraction > 0.0:
                assert row["capacity_binding"] == OTHER, parts
                step_heat = row["Q_ref_tank [W]"] * run.runtime_fraction * dt
                heat, tank = heat + step_heat, tank + step_heat / C
            call, running, age = run.call_end, run.running_end, run.phase_age_end_s
        ends[parts] = (tank, heat)
        assert tank == pytest.approx(floor_C, abs=0.005), parts
    for parts in (36, 6, 1):
        assert ends[parts][1] == pytest.approx(ends[360][1], rel=0.005), parts
    # The one step: the run follows the limit's line from 61 degC, C dT/dt = 2000 (64.95 - T).
    run, row = _charge_two_limits(other, 1, 3600.0)[3][0]
    t_floor = TAU * math.log((64.95 - 61.0) / (64.95 - floor_C))
    assert run.runtime_fraction * 3600.0 == pytest.approx(t_floor, rel=1e-3)
    assert row["Q_ref_tank [W]"] * run.runtime_fraction * 3600.0 == pytest.approx((floor_C - 61.0) * C, rel=1e-3)


def test_running_out_of_stages_is_not_reported_as_a_cut_out(monkeypatch):
    """A step whose resolution runs out of stages has not met a cut-out: the next step takes the charge on."""
    import tmhp.dynamic_context as dc

    def other(T_C):
        return 9_000.0 - 1_500.0 * (T_C - 58.0)

    monkeypatch.setattr(dc, "_DUTY_LIMIT_STAGES", 0)
    run, row, _ = _resolve_two_limits(other, T_start=56.0, dt=3600.0)
    # The first run ends at 60.5 degC, where its 9 kW meet the condensing limit and 5.25 kW could still be had.
    assert run.T_on_max_C == pytest.approx(60.5) and row["Q_ref_tank [W]"] == pytest.approx(9_000.0)
    assert not run.limit_stop and run.limit_stop_C is None
    assert in_step_stop_reason(run, T_WATER_BOIL_C) is None
    assert run.call_end  # the call stands: the next step goes on from there


#: Second limits of several shapes on the tank of the event tests: name -> (duty left [W] by tank temperature
#: [degC], start [degC], tolerances on the end temperature [K], the heat [-] and the time on the condensing limit [s]).
OTHER_LIMITS = {
    "just below the condensing limit": (lambda T_C: LIMIT.duty(T_C) - 100.0, 61.0, 0.005, 0.005, 0.0),
    "falling, met below the cut-out": (lambda T_C: 9_000.0 - 1_500.0 * (T_C - 58.0), 56.0, 0.005, 0.005, 0.0),
    "taking over above the start": (lambda T_C: 6_000.0 - 4_000.0 * (T_C - 62.0), 61.0, 0.005, 0.005, 15.0),
    "shallow, meeting the condensing limit": (lambda T_C: 5_000.0 - 500.0 * (T_C - 61.0), 61.0, 0.005, 0.005, 1.0),
    "curved": (lambda T_C: 7_000.0 - 900.0 * (T_C - 61.0) ** 2, 61.0, 0.06, 0.025, 40.0),
    "rising": (lambda T_C: 3_000.0 + 50.0 * (T_C - 61.0), 61.0, 0.005, 0.005, 6.0),
    "ending abruptly": (lambda T_C: 6_000.0 - 2_000.0 * (T_C - 61.0) if T_C <= 62.4 else 0.0, 61.0, 0.03, 0.02, 0.0),
}


@pytest.mark.parametrize("shape", list(OTHER_LIMITS))
def test_a_second_limit_of_any_shape_comes_out_the_same_at_any_step(shape):
    """One hour under a second limit in 360, 36 and 6 steps and in one: the same tank, heat and time on the condensing limit.

    On every step: the heat of the row is the heat the events integrated (nothing but the
    compressor moves this tank, so the warmest tank the events saw it run at is the tank its heat
    gives: to 0.005 K), and a cut-out is reported only where nothing above the smallest continuous
    duty is left to be had. (Stop-time review: a run cut short where the other limit closes kept
    the cycle of the longer run, whose mean duty was another.)
    """
    other, T_init, T_tol, Q_tol, t_tol = OTHER_LIMITS[shape]
    out = {}
    for parts in (360, 36, 6, 1):
        tank, call, running, age, heat, on_limit, dt = T_init, True, False, math.inf, 0.0, 0.0, 3600.0 / parts
        for _ in range(parts):
            run, row, _ = _resolve_two_limits(
                other, T_start=tank, dt=dt, call_start=call, running_prev=running, phase_age_s=age
            )
            if row is not None and row.get("hp_is_on") and run.runtime_fraction > 0.0:
                step_heat = row["Q_ref_tank [W]"] * run.runtime_fraction * dt
                heat, tank = heat + step_heat, tank + step_heat / C
                assert run.T_on_max_C <= tank + 0.005, (shape, parts, run.T_on_max_C - tank)
            if run.limit_stop:
                top = tank if run.T_on_max_C is None else max(tank, run.T_on_max_C)
                left = min(other(top), LIMIT.duty(top))
                assert left <= LIMIT.Q_min * 1.002, (shape, parts, top, left)
                # A condensing stop is the condensing limit's: reported with the tank at that limit's
                # stop (64 degC) only, never where the second limit ended the run below it.
                assert in_step_stop_reason(run, T_WATER_BOIL_C) == STOP_CONDENSING_LIMIT
                assert top == pytest.approx(LIMIT.stop_C, abs=0.005), (shape, parts, top)
            else:
                assert in_step_stop_reason(run, T_WATER_BOIL_C) is None
            on_limit += run.reduced_fraction * dt
            call, running, age = run.call_end, run.running_end, run.phase_age_end_s
        out[parts] = (tank, heat, on_limit)
    fine = out[360]
    for parts in (36, 6, 1):
        assert out[parts][0] == pytest.approx(fine[0], abs=T_tol), (shape, parts)
        assert out[parts][1] == pytest.approx(fine[1], rel=Q_tol), (shape, parts)
        assert out[parts][2] == pytest.approx(fine[2], abs=t_tol), (shape, parts)
    # Only the shallow and the rising limit let the tank get to the condensing limit's stop.
    assert (fine[0] == pytest.approx(LIMIT.stop_C, abs=0.005)) == (
        shape in ("shallow, meeting the condensing limit", "rising")
    )


@pytest.mark.parametrize("above", [1_500.0, 0.0], ids=["too little above", "no operating point above"])
def test_a_hand_over_with_nothing_to_run_on_is_not_a_condensing_stop(above):
    """Stop-time review: the run gets to where its duty meets the condensing limit, and nothing runs on from there.

    A second limit leaves 9 kW up to 60.5 degC -- exactly where 9 kW meet the condensing limit --
    and above it less than the smallest continuous duty, or no operating point. The run ends at
    60.5 degC. That is 3.5 K below the condensing limit's stop (64 degC), so it is not a
    condensing stop: the step ends the way the rest of it was resolved (the compressor off, the
    call standing), and the step after it says why if the cycle has no operating point.
    """

    def other(T_C):
        return 9_000.0 if T_C <= 60.5 else above

    run, row, _ = _resolve_two_limits(other, T_start=56.0, dt=3600.0)
    assert run.runtime_fraction * 3600.0 == pytest.approx(4.5 * C / 9_000.0, rel=1e-9)
    assert run.T_on_max_C == pytest.approx(60.5) and row["Q_ref_tank [W]"] == pytest.approx(9_000.0)
    assert row["capacity_binding"] == OTHER and run.reduced_fraction == 0.0
    assert in_step_stop_reason(run, T_WATER_BOIL_C) is None and not run.limit_stop
    assert not run.running_end and run.call_end and run.starts == 1
    assert run.phase_age_end_s == pytest.approx(3600.0 - 4.5 * C / 9_000.0, rel=1e-9)  # off since it got there
    # The step after it, from 60.5 degC: off, and the cycle's failure if that is why.
    after, row_after, _ = _resolve_two_limits(
        other, T_start=60.5 + 1e-9, dt=600.0, running_prev=False, phase_age_s=run.phase_age_end_s
    )
    assert after.runtime_fraction == 0.0 and in_step_stop_reason(after, T_WATER_BOIL_C) is None
    if above > 0.0:
        assert not after.heat_unavailable and row_after is None
    else:
        assert after.heat_unavailable and row_after["failure_reason"] == OTHER
