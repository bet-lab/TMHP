"""The multi-node tank step of GSHPB does not depend on the step length (#96, round 5, R5H).

A step's cycle is solved at the mean condensing level of its run, not at the level the run ends
on; the tank step takes the heat through the coil's conductance for the time the compressor runs
and ends on the condensing limit at most; the trial tank steps draw what the step itself does;
a hold at the envelope stop never asks for more than the nameplate duty; and the thermostat
draws a multi-node tank's hot water from its top. The step-size invariants (60 s against 600 s)
are in ``tests/invariants/test_known_defects.py`` (T6) and ``tests/test_step_envelope_stop.py``.
"""

from __future__ import annotations

import contextlib
import io
import math
import warnings

import numpy as np
import pytest

import tmhp
from tmhp.compressor_envelope import condensing_tank_cutout
from tmhp.constants import c_w, rho_w
from tmhp.dynamic_context import StepContext, tank_free_heat_rate
from tmhp.ground_source_heat_pump_boiler import COIL_PEAK_KEY

T_COND = "T_ref_cond_sat_l [°C]"


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


def _boiler(**kw):
    return _quiet(tmhp.GroundSourceHeatPumpBoiler, dt_s=kw.pop("dt_s", 600.0), t_max_s=86400.0, **kw)


def _ctx(m, draw: float = 0.0, T_start: float | None = None) -> StepContext:
    """A step context at the tank's present mean temperature (15 degC make-up)."""
    T_mean = float(np.mean(m._tank.T)) if T_start is None else T_start
    return StepContext(
        n=0,
        current_time_s=0.0,
        current_hour=0.0,
        hour_of_day=0.0,
        T0=7.0,
        T0_K=280.15,
        activation_flags={},
        T_tank_w_K=T_mean + 273.15,
        tank_level=1.0,
        dV_mix_w_out=draw,
        T_sup_w_K=288.15,
    )


# ---------------------------------------------------------------------------
# the cycle's condensing temperature: the run's mean
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def warm_up():
    """Coil over the whole tank, 20 min from 55 degC without a draw: one run to the 65 degC set point.

    At 60 s, 600 s and 1200 s steps, and the lumped tank at 600 s.
    """
    out = {}
    for dt in (60.0, 600.0, 1200.0):
        m = _boiler(tank_model="stratified", dt_s=dt)
        n = int(1200 / dt)
        out[dt] = _quiet(m.analyze_dynamic, n * dt, dt, 55.0, np.zeros(n), np.full(n, 5.0))
    m = _boiler(dt_s=600.0)
    out["lumped"] = _quiet(m.analyze_dynamic, 1200.0, 600.0, 55.0, np.zeros(2), np.full(2, 5.0))
    return out


def _energy(df, dt: float, key: str) -> float:
    return float(df[key].sum() * dt)


def test_a_long_step_condenses_at_the_mean_of_its_run(warm_up):
    """Was 67.5 degC for the 600 s step of this run: the level it ended on less half the approach
    (the end-of-step floor), 2.5 K above the mean of the 60 s rows, and 4.7 % more electricity."""
    fine = warm_up[60.0]
    heat = fine["Q_ref_tank [W]"]
    mean_fine = float((fine[T_COND] * heat).sum() / heat.sum())
    assert mean_fine == pytest.approx(65.0, abs=0.1)  # 55 -> 65 degC under a 5 K approach
    for dt in (600.0, 1200.0):
        df = warm_up[dt]
        ran = df[df["runtime_fraction [-]"] > 0.0]
        assert len(ran) == 1  # the whole run in one step
        assert float(ran[T_COND].iloc[0]) == pytest.approx(mean_fine, abs=0.1)
        # ... and it ended on the level of the set point: the highest of the run.
        assert float(ran[COIL_PEAK_KEY].iloc[0]) == pytest.approx(70.0, abs=0.1)
        assert _energy(df, dt, "Q_ref_tank [W]") == pytest.approx(_energy(fine, 60.0, "Q_ref_tank [W]"), rel=2e-3)
        assert _energy(df, dt, "E_tot [W]") == pytest.approx(_energy(fine, 60.0, "E_tot [W]"), rel=5e-3)


def test_a_mixed_multi_node_tank_runs_like_the_lumped_tank(warm_up):
    """No draw, coil over the whole tank: every node warms alike, so the cycle is the lumped one."""
    df, base = warm_up[600.0], warm_up["lumped"]
    assert list(df["hp_is_on"]) == list(base["hp_is_on"])
    assert _energy(df, 600.0, "Q_ref_tank [W]") == pytest.approx(_energy(base, 600.0, "Q_ref_tank [W]"), rel=1e-4)
    assert _energy(df, 600.0, "E_tot [W]") == pytest.approx(_energy(base, 600.0, "E_tot [W]"), rel=1e-3)
    assert COIL_PEAK_KEY not in base.columns  # a multi-node column


def test_run_levels_of_a_mixed_tank():
    """Mean: the thermostat's mean tank temperature plus the approach. Highest: where the run ends."""
    m = _boiler(tank_model="stratified", n_tank_nodes=4)
    m._tank.reset(50.0)
    q, approach = m.hp_capacity, m.hp_capacity / m.UA_tank_hx
    assert m._coil_run_levels(50.0, q, 0.0, 0.0, 15.0) == pytest.approx((50.0 + approach, 50.0 + approach))
    run_s = 300.0
    warmed = q * run_s / m.C_tank  # no draw; the wall loss is a few watts
    mean, peak = m._coil_run_levels(50.0 + 0.5 * warmed, q, run_s, 0.0, 15.0)
    assert mean == pytest.approx(50.0 + 0.5 * warmed + approach, abs=0.02)
    assert peak == pytest.approx(50.0 + warmed + approach, abs=0.02)
    # The cycle's tank-side temperature is the mean level less the approach of the duty tried.
    assert m._nodal_sink_temperature(50.0 + 0.5 * warmed, q, 0.5, 600.0, 0.0, 15.0) == pytest.approx(mean - approach)


def test_run_levels_past_the_envelope_stop_are_the_hold():
    """After the run time to the stop the compressor holds the coil's water at the cut-out."""
    m = _boiler(tank_model="stratified", n_tank_nodes=4, condenser_node=0)
    m._tank.reset(50.0)
    m._envelope_cutout = cutout = condensing_tank_cutout(m)
    q, approach = m.hp_capacity, m.hp_capacity / m.UA_tank_hx
    level_max = cutout + approach
    m._coil_run_limit_s = 0.0  # already at the stop: all of the run is the hold
    assert m._coil_run_levels(50.0, q, 120.0, 0.0, 15.0) == pytest.approx((level_max, level_max))
    # No level above the condensing limit, however long the trial run.
    m._coil_run_limit_s = math.inf
    mean, peak = m._coil_run_levels(50.0, q, 3600.0, 0.0, 15.0)
    assert mean <= level_max and peak == pytest.approx(level_max)


# ---------------------------------------------------------------------------
# the tank step: the coil for the time the compressor runs, on the limit at most
# ---------------------------------------------------------------------------


def test_tank_step_ends_on_the_condensing_limit_at_most():
    """A step asked for more heat than the coil's water can take below the limit is held on it.

    Was: the heat went in up to the cycle's condensing temperature whatever the coil's
    conductance (72.4 degC in the top node of a 600 s step, envelope stop 70 degC)."""
    m = _boiler(tank_model="stratified", condenser_node=0)
    tank = m._tank
    tank.reset(55.0)
    m._envelope_cutout = cutout = condensing_tank_cutout(m)
    q = float(m.hp_capacity)
    ctx = _ctx(m)
    # 600 s at the nameplate duty would take the 10 % top node far past the stop.
    args, flows, held = m._coil_step(ctx, ctx.T_sup_w_K, 600.0, q, 1.0)
    assert held and args["T_hx"] == pytest.approx(cutout)  # the coil's water is capped at the stop
    out = tank.preview(600.0, draw_flow=flows["dV_tank_w_out"], **args)
    assert out["T"][0] == pytest.approx(cutout, abs=1e-3)  # ends at the envelope stop
    assert 0.0 < out["Q_hx"] < q
    assert np.all(out["q_hx"][1:] == 0.0)
    # With a draw the tank sub-steps: still no moment of the step above the stop.
    m._coil_step_memo.clear()
    drawn = _ctx(m, draw=1e-4)
    args, flows, held = m._coil_step(drawn, drawn.T_sup_w_K, 600.0, q, 1.0)
    out = tank.preview(600.0, draw_flow=flows["dV_tank_w_out"], **args)
    assert held and out["substeps"] > 1 and out["T"][0] <= cutout + 1e-6
    # 30 s of it stays below: the step's heat goes in, at the level the run ends on.
    m._coil_step_memo.clear()
    args, flows, held = m._coil_step(ctx, ctx.T_sup_w_K, 30.0, q, 1.0)
    assert not held and "T_hx" not in args
    out = tank.preview(30.0, draw_flow=0.0, **args)
    assert out["Q_hx"] == pytest.approx(q, rel=1e-9)
    assert out["T_hx"] == pytest.approx(out["T"][0] + q / m.UA_tank_hx, abs=1e-6)


def test_the_coil_exchanges_for_the_time_the_compressor_runs():
    """Half the run time: half the conductance, so the same condensing level for the same water."""
    m = _boiler(tank_model="stratified", n_tank_nodes=4)
    m._tank.reset(40.0)
    ctx = _ctx(m)
    q = float(m.hp_capacity)
    args, _, held = m._coil_step(ctx, ctx.T_sup_w_K, 60.0, q, 0.5)
    assert not held
    assert args["q_hx"] == pytest.approx(0.5 * q)
    assert args["hx_ua"].sum() == pytest.approx(0.5 * m.UA_tank_hx)
    out = m._tank.preview(60.0, **args)
    assert out["T_hx"] == pytest.approx(float(np.mean(out["T"])) + q / m.UA_tank_hx, abs=1e-6)


# ---------------------------------------------------------------------------
# the hold: never more than the nameplate duty at any moment
# ---------------------------------------------------------------------------


def test_hold_never_asks_for_more_than_the_nameplate_duty():
    """A cold front that reaches the top coil inside a long step: the hold of the first part, then
    the compressor flat out -- not the step's mean of a hold that would take 2.9 times the duty in
    its last quarter."""
    m = _boiler(tank_model="hybrid", condenser_node=0)
    tank = m._tank
    m._envelope_cutout = cutout = condensing_tank_cutout(m)
    q = float(m.hp_capacity)
    profile = np.full(tank.n, 15.0)
    profile[:2] = cutout  # hot water in the top fifth only
    tank.reset(profile)
    dt = 600.0
    ctx = _ctx(m, draw=1e-4)
    T_limit, tau, hold = m._envelope_stop(ctx, cutout, dt)
    assert tau == 0.0 and T_limit == 100.0
    tail_s, tail_level = m._hold_tail
    assert 0.0 < tail_s < dt  # the hold breaks inside the step
    assert tail_level < cutout + q / m.UA_tank_hx  # ... and the coil's water cools from the cut-out
    assert hold <= q
    # Less than a hold that only its step mean limits (the coil's water capped for the whole step).
    flow = m._trial_draw_flow(ctx, dt)
    capped = tank.preview(dt, draw_flow=flow, T_makeup=15.0, T_amb=20.0, hx_ua=m._condenser_ua(), T_hx=cutout, q_hx=q)
    assert hold < capped["Q_hx"] - 100.0


def test_a_coil_stop_at_the_set_point_ends_the_call():
    """R32: the upper set point is the envelope stop itself (the 65 degC request is capped at it).

    A coil over the whole tank reaches its stop as the mean reaches the set point; the call ends
    there and the tank cools to the lower bound before the next start, as the lumped tank does.
    Was (with the hold at the stop): the mean kept 0.01 K below a set point it could not reach,
    the compressor on the condensing limit for a whole 600 s step of a draw."""
    runs = {}
    for name, kw in (("lumped", {}), ("stratified", dict(tank_model="stratified"))):
        m = _boiler(ref="R32", **kw)
        assert m.T_tank_w_upper_bound == pytest.approx(condensing_tank_cutout(m))
        n = 12
        runs[name] = (m, _quiet(m.analyze_dynamic, n * 600.0, 600.0, 59.0, np.full(n, 6e-5), np.full(n, 15.0)))
    m, df = runs["stratified"]
    base = runs["lumped"][1]
    assert df["hp_stop_reason"].isna().all()  # the thermostat's stops, none of the envelope's
    assert df["hp_starts [-]"].sum() == base["hp_starts [-]"].sum() >= 2
    assert df["T_tank_w [°C]"].min() == pytest.approx(base["T_tank_w [°C]"].min(), abs=0.3)
    assert df["T_tank_w [°C]"].max() <= m.T_tank_w_upper_bound + 1e-6
    # The cycle condenses no hotter than against the mixed tank (1 % for the node model's steps).
    assert df["E_cmp [W]"].sum() <= base["E_cmp [W]"].sum() * 1.01
    assert df["Q_ref_tank [W]"].sum() == pytest.approx(base["Q_ref_tank [W]"].sum(), rel=0.02)
    # A coil far from the set point keeps its own stop: the top coil holds.
    top = _boiler(tank_model="stratified", condenser_node=0)
    top._tank.reset(55.0)
    top._envelope_cutout = condensing_tank_cutout(top)
    T_limit, tau, hold = top._envelope_stop(_ctx(top), top._envelope_cutout, 600.0)
    assert top._setpoint_at_coil_stop(tau, hold) == (top.T_tank_w_upper_bound, tau, hold)
    assert math.isfinite(tau) and top._coil_stop_mean_C < top.T_tank_w_upper_bound - 5.0


# ---------------------------------------------------------------------------
# the thermostat's draw: from the top of a multi-node tank
# ---------------------------------------------------------------------------


def test_thermostat_draws_a_multi_node_tank_from_its_top():
    """Mean and top above the mixing set point: the lumped residual. A depleted mean under a hot
    top: the scheduled heat (the lumped residual would give 35 degC water: 5 K short of 25 K)."""
    m = _boiler(tank_model="stratified", n_tank_nodes=4)
    dt, draw = 600.0, 1e-4
    demand = c_w * rho_w * draw * (m.T_mix_w_out_K - 288.15)

    def lumped(T_K, T_start_K):  # an always-full lumped tank that only gives hot water
        return (
            m.C_tank * (T_K - T_start_K) + dt * c_w * rho_w * draw * (min(T_K, m.T_mix_w_out_K) - 288.15)
        ) / m.C_tank

    def free_heat(profile, T_K):
        m._tank.reset(np.asarray(profile, dtype=float))
        ctx = _ctx(m, draw=draw)
        residual = m._nodal_free_residual(ctx, lambda T: lumped(T, ctx.T_tank_w_K), dt)
        return tank_free_heat_rate(
            residual,
            T_K,
            T_start_K=ctx.T_tank_w_K,
            C_curr=m.C_tank,
            C_next=m.C_tank,
            C_tank=m.C_tank,
            dt=dt,
            Q_heat_source=0.0,
        )

    assert free_heat([60.0, 58.0, 50.0, 45.0], 325.0) == pytest.approx(-demand)
    depleted = [65.0, 25.0, 15.0, 15.0]  # mean 30 degC, top 65 degC
    assert free_heat(depleted, 303.15) == pytest.approx(-demand)
    assert free_heat(depleted, 308.15) == pytest.approx(-demand)
    # A mixed cold tank gives what it has.
    assert free_heat([30.0, 30.0, 30.0, 30.0], 303.15) == pytest.approx(-demand * 15.0 / 25.0)
    # Without a draw, and for one mixed node, the residual is the lumped one itself.
    probe = lambda T: 0.0  # noqa: E731
    assert m._nodal_free_residual(_ctx(m, draw=0.0), probe, dt) is probe
    one = _boiler(tank_model="stratified", n_tank_nodes=1)
    assert one._nodal_free_residual(_ctx(one, draw=draw), probe, dt) is probe


def test_mixing_valve_settles_on_the_water_that_leaves_in_a_long_step():
    """An hour's draw of most of the tank: the valve's temperature and the step's outlet agree.

    A plain repetition halved the gap per trial step here and stopped 0.05 K short after six."""
    m = _boiler(tank_model="stratified", dt_s=3600.0)
    tank = m._tank
    tank.reset(np.linspace(62.0, 38.0, tank.n))
    ctx = _ctx(m, draw=6e-5)
    args = dict(T_makeup=15.0, T_amb=20.0, hx_ua=m._condenser_ua(), q_hx=0.8 * m.hp_capacity)
    flows = m._settle_draw(ctx, ctx.T_sup_w_K, 3600.0, args)
    T_out = tank.preview(3600.0, draw_flow=flows["dV_tank_w_out"], **args)["T_draw_out"]
    T_mix = flows["alp"] * T_out + (1.0 - flows["alp"]) * 15.0
    assert T_mix == pytest.approx(m.T_mix_w_out, abs=0.01)
