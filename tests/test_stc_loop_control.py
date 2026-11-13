"""Collector-loop controller: freeze protection, pump latch, storage-limit restart
band, stagnation lock-out, and the subsystem state carried between steps.

The collector physics and the energy accounting are covered in
``tests/test_solar_uv_mains.py``; here the heat pump is switched off so the
runs are light and the collector loop is the only thing acting on the tank.
"""

from __future__ import annotations

import copy
import dataclasses
import pickle
import warnings

import numpy as np
import pandas as pd
import pytest

import tmhp
from tmhp import calc_util as cu
from tmhp._stc_scenarios import (
    SINK_LIMIT_TOL_K,
    STAGNATION_RELEASE_K,
    STC_CONTROLLER_STATE0,
    TANK_LIMIT_RAMP_K,
    TANK_LIMIT_RESTART_K,
)
from tmhp.dynamic_context import (
    SUBSYSTEM_STATE_KEY,
    TANK_BOILING_WARNING,
    ControlState,
    DynamicState,
    StepContext,
    collect_subsystem_state,
)
from tmhp.subsystems import SolarThermalCollector

DT = 600
T_SUP = 12.0
ALL = [
    "ASHPB_STC_preheat",
    "ASHPB_STC_tank",
    "GSHPB_STC_preheat",
    "GSHPB_STC_tank",
    "GSHPB_STC_ground",
    "GSHPB_STC_routed",
]
PUMPED = ALL[1:2] + ALL[3:]
# Temperature comparisons against a controller limit carry this tolerance [K].
TOL_K = 1e-6


def _model(name: str, stc: SolarThermalCollector, *, hp_off: bool = True, hours: float = 12.0, **kw):
    cls = getattr(tmhp, name)
    if not name.startswith("ASHPB"):
        kw = dict(N_1=2, N_2=1, H_b=100.0, dt_s=float(DT), t_max_s=hours * 3600.0) | kw
    if hp_off:
        kw["hp_on_schedule"] = [(0.0, 0.0)]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return cls(stc=stc, **kw)


def _run(m, n: int, T_init: float, *, T0=20.0, I_DN=0.0, I_dH=0.0, draw=0.0):
    def arr(v):
        return np.full(n, float(v)) if np.isscalar(v) else np.asarray(v, dtype=float)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return m.analyze_dynamic(
            n * DT,
            DT,
            T_init,
            arr(draw),
            arr(T0),
            I_DN_schedule=arr(I_DN),
            I_dH_schedule=arr(I_dH),
            T_sup_w_schedule=np.full(n, T_SUP),
            T_sur_schedule=np.full(n, 20.0),
        )


def _ctx(*, T0: float, T_tank: float, I_DN: float, I_dH: float = 0.0, hour: float = 12.0, draw: float = 0.0):
    return StepContext(
        n=0,
        current_time_s=hour * 3600.0,
        current_hour=hour,
        hour_of_day=hour,
        T0=T0,
        T0_K=cu.C2K(T0),
        activation_flags={"stc": True},
        T_tank_w_K=cu.C2K(T_tank),
        tank_level=1.0,
        dV_mix_w_out=draw,
        I_DN=I_DN,
        I_dH=I_dH,
        T_sup_w_K=cu.C2K(T_SUP),
    )


def _starts(on: np.ndarray) -> np.ndarray:
    return on & ~np.r_[False, on[:-1]]


# ---------------------------------------------------------------------------
# freeze protection
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", params=ALL)
def winter_morning(request):
    """Six hours from 06:00 on a clear 15 January at -10 °C: night, sunrise, a sunny morning."""
    import pvlib

    n = 36
    t = pd.date_range("2026-01-15 06:00", periods=n, freq=f"{DT}s", tz="Asia/Seoul") + pd.Timedelta(seconds=DT / 2)
    cs = pvlib.location.Location(37.57, 126.98, "Asia/Seoul").get_clearsky(t, model="ineichen")
    # The boilers count the clock window from second 0 of the run, so open it from there.
    stc = SolarThermalCollector(A_stc=4.0, start_time="2026-01-15 06:00", preheat_start_hour=0.0)
    m = _model(request.param, stc)
    hod = 6.0 + np.arange(n) * DT / 3600.0
    draw = np.where((hod >= 7) & (hod < 12), 4.0 / 60000.0, 0.0)
    df = _run(m, n, 40.0, T0=-10.0, I_DN=cs.dni.to_numpy(), I_dH=cs.dhi.to_numpy(), draw=draw)
    return request.param, m, df


def test_a_collector_below_the_freeze_limit_is_drained_and_says_so(winter_morning):
    name, m, df = winter_morning
    stag = df["T_stc_stag [°C]"].to_numpy()
    frozen = df["stc_freeze_protected [-]"].to_numpy(dtype=bool)
    on = df["stc_active [-]"].to_numpy(dtype=bool)
    clear = np.abs(stag - m._stc.T_stc_freeze) > 0.01  # away from the limit itself
    np.testing.assert_array_equal(frozen[clear], (stag < m._stc.T_stc_freeze)[clear])
    assert frozen[:6].all(), "before sunrise the collector is at the outdoor temperature (-10 °C)"
    assert on.any() and not frozen[on].any(), "no flow through a drained collector"
    assert not (on & frozen).any()
    # the water in a running collector is never below 0 °C
    assert df.loc[on, "T_stc_w_in [°C]"].min() >= 0.0
    assert df.loc[on, "T_stc_w_out [°C]"].min() > 0.0
    assert (df.loc[frozen, "E_stc_pump [W]"] == 0.0).all()
    assert (df.loc[frozen, ["Q_stc_w_out [W]", "Q_stc_w_in [W]"]].fillna(0.0) == 0.0).all().all()


def test_freeze_protection_blocks_a_loop_that_would_otherwise_run():
    """Mains at 1.5 °C through a collector whose idle temperature is 3 °C would be a gain; it is not run."""
    stc = SolarThermalCollector(A_stc=4.0, start_time="2026-01-15")
    m = _model("ASHPB_STC_preheat", stc)
    ctrl = ControlState(is_on=False, Q_heat_source=0.0, dV_tank_w_in_ctrl=None)
    hit = None
    for I_DN in np.arange(0.0, 120.0, 2.0):
        ctx = _ctx(T0=-10.0, T_tank=55.0, I_DN=float(I_DN), I_dH=20.0, hour=10.5, draw=6.0 / 60000.0)
        ctx.T_sup_w_K = cu.C2K(1.5)
        plane = m._stc_plane(ctx, DT)
        stag_C = cu.K2C(stc.stagnation_temperature_K(plane, ctx.T0_K))
        if 2.0 < stag_C < 3.8:
            hit = (ctx, plane)
            break
    assert hit is not None
    ctx, plane = hit
    flow, _, r, _ = m._preheat(ctx, ctrl, plane, cu.C2K(55.0))
    assert flow > 0.0 and r["T_stc_w_out_K"] > ctx.T_sup_w_K, "the collector would warm the feed"
    state = m._run_subsystems(ctx, ctrl, DT, ctx.T_sup_w_K)["stc"]
    assert state["freeze"] and not state["stc_active"]
    # an antifreeze loop (protection off) takes that gain
    m2 = _model("ASHPB_STC_preheat", SolarThermalCollector(A_stc=4.0, start_time="2026-01-15", T_stc_freeze=None))
    state2 = m2._run_subsystems(ctx, ctrl, DT, ctx.T_sup_w_K)["stc"]
    assert state2["stc_active"] and not state2["freeze"]


@pytest.fixture(scope="module")
def ground_model():
    return _model("GSHPB_STC_ground", SolarThermalCollector(A_stc=4.0, start_time="2026-01-15"))


def test_no_water_below_zero_is_pumped_through_the_collector(ground_model):
    """A borehole field driven below 0 °C: the ground loop would send -5 °C liquid through a warm collector."""
    m = ground_model
    ctx = _ctx(T0=-5.0, T_tank=50.0, I_DN=700.0, I_dH=80.0)
    plane = m._stc_plane(ctx, DT)
    m._subsystem_state_in = {}
    runs, probe, flags = m._loop_decision(ctx, plane, cu.C2K(-5.0))
    assert cu.K2C(probe["T_stc_stag_K"]) > 50.0 and probe["T_stc_w_out_K"] > cu.C2K(-5.0) + m._stc.dT_stc_off
    assert not runs and flags["freeze"]
    m._subsystem_state_in = {}
    runs, _, flags = m._loop_decision(ctx, plane, cu.C2K(2.0))  # liquid water: runs
    assert runs and not flags["freeze"]
    # declared antifreeze loop: the protection is off
    m._stc = dataclasses.replace(m._stc, T_stc_freeze=None)
    try:
        m._subsystem_state_in = {}
        runs, _, flags = m._loop_decision(ctx, plane, cu.C2K(-5.0))
        assert runs and not flags["freeze"]
    finally:
        m._stc = dataclasses.replace(m._stc, T_stc_freeze=4.0)
        m._subsystem_state_in = {}


@pytest.mark.parametrize("bad", [{"T_stc_freeze": 300.0}, {"T_stc_lockout": 90.0}, {"T_stc_lockout": float("nan")}])
def test_protection_limits_are_validated(bad):
    with pytest.raises(ValueError, match=next(iter(bad))):
        SolarThermalCollector(**bad)


# ---------------------------------------------------------------------------
# differential controller: pump latch
# ---------------------------------------------------------------------------


def test_a_running_pump_runs_on_below_the_start_threshold():
    """Low-flow loop (0.002 kg/(s·m²)): idle collector 7-9 K above the tank, start threshold 10 K.

    The running outlet is more than ``dT_stc_off`` above the tank, so a pump that is
    running keeps running, and one that is stopped does not start.
    """
    stc = SolarThermalCollector(
        A_stc=4.0,
        dV_stc_w=4.0 * 0.002 / 1000.0,
        dT_stc_on=10.0,
        dT_stc_off=2.0,
        E_stc_pump=10.0,
        start_time="2026-03-20",
    )
    m = _model("ASHPB_STC_tank", stc)
    T_tank = 30.0
    hit = None
    for I_DN in np.arange(0.0, 300.0, 2.0):
        ctx = _ctx(T0=20.0, T_tank=T_tank, I_DN=float(I_DN), I_dH=50.0)
        plane = m._stc_plane(ctx, DT)
        m._subsystem_state_in = {}
        _, probe, _ = m._loop_decision(ctx, plane, ctx.T_tank_w_K, sink_max_C=85.0)
        idle_above = probe["T_stc_stag_K"] - ctx.T_tank_w_K
        rise = probe["T_stc_w_out_K"] - ctx.T_tank_w_K
        if 7.0 < idle_above < 9.0 and rise > stc.dT_stc_off + 0.2:
            hit = (ctx, plane)
            break
    assert hit is not None, "no irradiance in the hysteresis band"
    ctx, plane = hit
    m._subsystem_state_in = {"stc": {"pump_on": False}}
    assert not m._loop_decision(ctx, plane, ctx.T_tank_w_K, sink_max_C=85.0)[0]
    m._subsystem_state_in = {"stc": {"pump_on": True}}
    assert m._loop_decision(ctx, plane, ctx.T_tank_w_K, sink_max_C=85.0)[0]
    # the same through the public run (07:00-17:00): a fading sun keeps the loop on below the start threshold
    m = _model("ASHPB_STC_tank", dataclasses.replace(stc, start_time="2026-03-20 07:00"))
    n = 60
    up = np.linspace(0.0, 300.0, n // 2)
    I_DN = np.r_[up, up[::-1]]
    df = _run(m, n, T_tank, I_DN=I_DN, I_dH=50.0)
    on = df["stc_active [-]"].to_numpy(dtype=bool)
    assert _starts(on).sum() == 1
    first, last = np.flatnonzero(on)[[0, -1]]
    idle_above = (df["T_stc_stag [°C]"] - df["T_stc_w_in [°C]"]).to_numpy()
    assert idle_above[first] >= stc.dT_stc_on - TOL_K
    assert idle_above[last] < stc.dT_stc_on, "the pump ran on below the start threshold"
    rise = (df["T_stc_w_out [°C]"] - df["T_stc_w_in [°C]"]).to_numpy()
    assert (rise[on] > 0.0).all()


# ---------------------------------------------------------------------------
# storage limit: restart band; stagnation lock-out
# ---------------------------------------------------------------------------

N_STAG = 60  # 10 h from 08:00 on 21 June


def _stagnation_inputs():
    """Strong sun until 15:00, then a weaker one; a draw at 11:00 cools the tank."""
    hod = 8.0 + np.arange(N_STAG) * DT / 3600.0
    I_DN = np.where(hod < 15.0, 850.0, 500.0)
    draw = np.where((hod >= 11.0) & (hod < 11.5), 6.0 / 60000.0, 0.0)
    return hod, I_DN, draw


def _stagnation_day(name: str, **stc_kw):
    """The tank reaches its limit in strong sun; a draw then cools it; at 15:00 the sun weakens."""
    stc = SolarThermalCollector(A_stc=4.0, start_time="2026-06-21 08:00", **stc_kw)
    m = _model(name, stc)
    hod, I_DN, draw = _stagnation_inputs()
    df = _run(m, N_STAG, 80.0, T0=28.0, I_DN=I_DN, I_dH=110.0, draw=draw)
    return m, df, hod


@pytest.fixture(scope="module", params=["ASHPB_STC_tank", "GSHPB_STC_tank"])
def stagnation_day(request):
    return request.param, *_stagnation_day(request.param)


def test_stagnating_collector_is_locked_out_until_it_has_cooled(stagnation_day):
    name, m, df, hod = stagnation_day
    stc = m._stc
    on = df["stc_active [-]"].to_numpy(dtype=bool)
    locked = df["stc_stagnation_lockout [-]"].to_numpy(dtype=bool)
    stag = df["T_stc_stag [°C]"].to_numpy()
    T = df["T_tank_w [°C]"].to_numpy()
    T_start = np.r_[80.0, T[:-1]]
    assert T.max() > stc.T_stc_tank_max - SINK_LIMIT_TOL_K - TOL_K, "the tank did not reach its limit"
    assert T.max() <= stc.T_stc_tank_max + TANK_LIMIT_RAMP_K + 1e-9
    assert locked.any() and not (on & locked).any()
    # locked steps: the idle collector is above the release temperature
    assert (stag[locked] >= stc.T_stc_lockout - STAGNATION_RELEASE_K - TOL_K).all()
    # the draw at 11:00 cools the tank well below the limit while the sun is still strong:
    # without the lock-out the loop would restart into a collector idling near 150 °C
    cooled = (hod >= 11.5) & (hod < 15.0)
    assert (T_start[cooled] < stc.T_stc_tank_max - TANK_LIMIT_RESTART_K).all()
    assert stag[cooled].min() > 135.0
    assert locked[cooled].all() and not on[cooled].any()
    # once the sun has weakened the collector cools below the release temperature and the loop runs again
    faded = hod >= 15.0
    assert (stag[faded] < stc.T_stc_lockout - STAGNATION_RELEASE_K).all()
    assert not locked[faded].any()
    assert on[faded].any() and _starts(on)[faded].sum() == 1
    # no water above the boiling protection ever reaches the tank
    assert df.loc[on, "T_stc_pump_w_out [°C]"].max() < stc.T_stc_boil
    assert (df.loc[~on, "Q_solar_tank [W]"] == 0.0).all()


@pytest.mark.parametrize("name", ["ASHPB_STC_tank", "GSHPB_STC_tank"])
def test_without_the_lockout_the_loop_restarts_into_the_hot_collector(name):
    """``T_stc_lockout=None`` reproduces the former behaviour: restart right after the draw."""
    m, df, hod = _stagnation_day(name, T_stc_lockout=None)
    on = df["stc_active [-]"].to_numpy(dtype=bool)
    stag = df["T_stc_stag [°C]"].to_numpy()
    assert not df["stc_stagnation_lockout [-]"].any()
    again = _starts(on) & (hod >= 11.0) & (hod < 15.0)
    assert again.any() and stag[again].min() > 135.0


@pytest.mark.parametrize("name", ["ASHPB_STC_tank", "GSHPB_STC_tank"])
def test_after_the_storage_limit_the_loop_waits_for_the_restart_band(name):
    """Lock-out off, limit 62 °C: after the limit the loop starts again only 2 K below it."""
    stc = SolarThermalCollector(A_stc=4.0, start_time="2026-06-21 08:00", T_stc_tank_max=62.0, T_stc_lockout=None)
    m = _model(name, stc)
    n = 48
    hod = 8.0 + np.arange(n) * DT / 3600.0
    draw = np.where((hod >= 11.0) & (hod < 15.0), 0.25 / 60000.0, 0.0)  # a trickle: the tank cools slowly
    df = _run(m, n, 59.0, T0=28.0, I_DN=850.0, I_dH=110.0, draw=draw)
    on = df["stc_active [-]"].to_numpy(dtype=bool)
    T = df["T_tank_w [°C]"].to_numpy()
    T_start = np.r_[59.0, T[:-1]]
    share = df["stc_runtime_fraction [-]"].to_numpy()
    reached = np.flatnonzero(on & (share < 1.0))
    assert reached.size, "the loop never stopped at the limit"
    restarts = np.flatnonzero(_starts(on))
    restarts = restarts[restarts > reached[0]]
    assert restarts.size, "the tank never cooled into the restart band"
    assert (T_start[restarts] < 62.0 - TANK_LIMIT_RESTART_K + TOL_K).all()
    # between the limit and the restart the tank sits inside the band with the loop off
    waiting = ~on & (T_start > 62.0 - TANK_LIMIT_RESTART_K + 0.05) & (np.arange(n) > reached[0])
    assert waiting.any()
    assert T.max() <= 62.0 + TANK_LIMIT_RAMP_K + 1e-9


def test_preheat_placement_has_no_lockout_and_reports_its_idle_temperature():
    stc = SolarThermalCollector(A_stc=4.0, start_time="2026-06-21 08:00", preheat_start_hour=0.0)
    m = _model("ASHPB_STC_preheat", stc)
    n = 36
    hod = 8.0 + np.arange(n) * DT / 3600.0
    draw = np.where((hod >= 12.0) & (hod < 13.0), 5.0 / 60000.0, 0.0)
    df = _run(m, n, 55.0, T0=28.0, I_DN=850.0, I_dH=110.0, draw=draw)
    assert not df["stc_stagnation_lockout [-]"].any() and not df["stc_freeze_protected [-]"].any()
    assert df["T_stc_stag [°C]"].max() > 130.0
    assert df["stc_active [-]"].to_numpy(dtype=bool)[(hod >= 12.0) & (hod < 13.0)].all()


@pytest.mark.parametrize("name", ["GSHPB_STC_ground", "GSHPB_STC_routed"])
def test_loop_held_off_by_its_clock_window_locks_out_a_hot_collector(name):
    """The clock window opens at 13:00 in full sun: the collector has idled hot all morning, so the
    loop stays locked out until the sun fades (14:00) and the collector cools."""
    stc = SolarThermalCollector(A_stc=4.0, start_time="2026-06-21 08:00", preheat_start_hour=5.0, preheat_end_hour=24.0)
    m = _model(name, stc)
    n = 42
    hod = 8.0 + np.arange(n) * DT / 3600.0
    I_DN = np.where(hod < 14.0, 850.0, 300.0)
    df = _run(m, n, 55.0, T0=28.0, I_DN=I_DN, I_dH=110.0)
    on = df["stc_active [-]"].to_numpy(dtype=bool)
    locked = df["stc_stagnation_lockout [-]"].to_numpy(dtype=bool)
    stag = df["T_stc_stag [°C]"].to_numpy()
    hot = stag >= stc.T_stc_lockout
    assert hot[hod < 14.0].any()
    first_hot = int(np.flatnonzero(hot)[0])
    held = (np.arange(n) >= first_hot) & (hod < 14.0)
    assert locked[held].all() and not on[hod < 14.0].any()
    assert held[(hod >= 13.0) & (hod < 14.0)].all(), "the window is open again while the loop is still locked"
    assert (stag[hod >= 14.0] < stc.T_stc_lockout - STAGNATION_RELEASE_K).all()
    assert not locked[hod >= 14.0].any() and on[hod >= 14.0].all()


# ---------------------------------------------------------------------------
# carried subsystem state
# ---------------------------------------------------------------------------


def test_collect_subsystem_state_copies_and_keeps_silent_subsystems():
    previous = {"stc": {"pump_on": True}, "ess": {"soc": 0.4}}
    carried = {"pump_on": False, "lockout": True}
    out = collect_subsystem_state(previous, {"stc": {SUBSYSTEM_STATE_KEY: carried}, "uv": {"E_uv": 1.0}})
    assert out == {"stc": carried, "ess": {"soc": 0.4}}
    assert out["stc"] is not carried and out["ess"] is not previous["ess"]
    assert previous == {"stc": {"pump_on": True}, "ess": {"soc": 0.4}}


def test_initial_state_has_no_subsystem_state_and_plain_models_carry_none():
    m = tmhp.AirSourceHeatPumpBoiler(hp_on_schedule=[(0.0, 0.0)])
    state = m.make_initial_state(50.0)
    assert state.subsystem_state == {}
    assert DynamicState(1.0, 1.0, False, False, 0.0).subsystem_state == {}
    inputs = {"n": 0, "current_time_s": 0.0, "T0": 10.0, "dV_mix_w_out": 0.0, "T_sup_w": T_SUP, "T_sur": 20.0}
    new_state, _ = m.step(state, inputs, DT)
    assert new_state.subsystem_state == {} and new_state.subsystem_state is not state.subsystem_state


def _step_inputs(k: int, hod: np.ndarray, I_DN: np.ndarray, draw: np.ndarray) -> dict:
    return {
        "n": k,
        "current_time_s": float(k * DT),
        "T0": 28.0,
        "dV_mix_w_out": float(draw[k]),
        "T_sup_w": T_SUP,
        "T_sur": 20.0,
        "I_DN": float(I_DN[k]),
        "I_dH": 110.0,
    }


def test_step_threads_the_controller_state_through_dynamic_state():
    """``step()`` driven from outside (the FMU pattern) equals ``analyze_dynamic``; the state is
    carried in ``DynamicState.subsystem_state`` only, so a kept or pickled state steps again alike."""
    m, df, _ = _stagnation_day("ASHPB_STC_tank")
    hod, I_DN, draw = _stagnation_inputs()

    other = _model("ASHPB_STC_tank", SolarThermalCollector(A_stc=4.0, start_time="2026-06-21 08:00"))
    state = other.make_initial_state(80.0)
    rows, states = [], [state]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for k in range(N_STAG):
            before = copy.deepcopy(state)
            state, row = other.step(state, _step_inputs(k, hod, I_DN, draw), DT)
            assert states[-1] == before, "step() modified the state it was given"
            rows.append(row)
            states.append(state)
    stepped = pd.DataFrame(rows)
    for col in ("T_tank_w [°C]", "Q_solar_tank [W]", "E_stc_pump [W]", "stc_runtime_fraction [-]"):
        np.testing.assert_allclose(stepped[col], df[col], rtol=1e-9, atol=1e-9, err_msg=col)
    for col in ("stc_active [-]", "stc_stagnation_lockout [-]", "stc_freeze_protected [-]"):
        np.testing.assert_array_equal(stepped[col].to_numpy(dtype=bool), df[col].to_numpy(dtype=bool))

    locked = [s.subsystem_state["stc"]["lockout"] for s in states[1:]]
    np.testing.assert_array_equal(locked, df["stc_stagnation_lockout [-]"].to_numpy(dtype=bool))
    assert set(states[1].subsystem_state["stc"]) == set(STC_CONTROLLER_STATE0)
    pump_on = [s.subsystem_state["stc"]["pump_on"] for s in states[1:]]
    full = df["stc_active [-]"].to_numpy(dtype=bool) & (df["stc_runtime_fraction [-]"].to_numpy() >= 1.0)
    np.testing.assert_array_equal(pump_on, full)  # on at the end of a step it ran through
    assert any(pump_on)

    # Re-enter at a locked-out step (after the draw has cooled the tank) from a pickled copy,
    # on a model that has not run before.
    k = int(np.flatnonzero(np.asarray(locked) & (hod >= 12.0))[0])
    kept = pickle.loads(pickle.dumps(states[k]))
    assert kept == states[k] and kept.subsystem_state["stc"]["lockout"]
    fresh = _model("ASHPB_STC_tank", SolarThermalCollector(A_stc=4.0, start_time="2026-06-21 08:00"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        again_state, again_row = fresh.step(kept, _step_inputs(k, hod, I_DN, draw), DT)
        other_state, other_row = other.step(states[k], _step_inputs(k, hod, I_DN, draw), DT)
    for got in (again_state, other_state):
        assert got.subsystem_state == states[k + 1].subsystem_state
        assert got.T_tank_w_K == pytest.approx(states[k + 1].T_tank_w_K, abs=1e-9)
    assert again_row["stc_stagnation_lockout [-]"] and not again_row["stc_active [-]"]
    assert again_row["T_tank_w [°C]"] == pytest.approx(rows[k]["T_tank_w [°C]"], abs=1e-9)
    # the same step from an empty controller state (the lock-out forgotten) would start the pump
    forgetful = copy.deepcopy(states[k])
    forgetful.subsystem_state = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _, row = fresh.step(forgetful, _step_inputs(k, hod, I_DN, draw), DT)
    assert row["stc_active [-]"] and not row["stc_stagnation_lockout [-]"]


@pytest.mark.parametrize("name", PUMPED)
def test_a_second_run_starts_from_an_empty_controller_state(name):
    """The first run ends locked out (strong sun to the last step); the second gives the same table."""
    # tank loops: the tank reaches its limit; ground loops: the clock window closes after two hours
    stc = SolarThermalCollector(A_stc=4.0, start_time="2026-06-21 09:00", preheat_start_hour=0.0, preheat_end_hour=2.0)
    m = _model(name, stc)
    n = 30
    first = _run(m, n, 84.0, T0=28.0, I_DN=850.0, I_dH=110.0)
    assert first["stc_stagnation_lockout [-]"].iloc[-1], "the run does not end locked out"
    assert not first["stc_stagnation_lockout [-]"].iloc[0]
    second = _run(m, n, 84.0, T0=28.0, I_DN=850.0, I_dH=110.0)
    numeric = first.select_dtypes(["number", "bool"]).columns
    pd.testing.assert_frame_equal(first[numeric], second[numeric], rtol=1e-9, atol=1e-9)


def test_tank_boiling_warning_names_the_collector_limit():
    assert "T_stc_tank_max" in TANK_BOILING_WARNING
    assert "without a high limit" not in TANK_BOILING_WARNING


@pytest.mark.parametrize(
    ("module", "cls_name"),
    [("tmhp.integrations.fmu", "TmhpAshpbSlave"), ("tmhp.integrations.fmu3", "TmhpAshpbFmi3Slave")],
)
def test_fmu_slaves_keep_the_subsystem_state_across_exchanges(module, cls_name):
    """The FMI adapters hold the ``DynamicState`` that ``step()`` returns, so the subsystem
    state survives each exchange (an entry no subsystem rewrites is carried unchanged)."""
    import importlib

    pytest.importorskip("pythonfmu3" if module.endswith("fmu3") else "pythonfmu")
    slave = getattr(importlib.import_module(module), cls_name)(instance_name="tmhp_state")
    slave.T_tank_w_init = 63.0  # above the thermostat band: the compressor stays off (a light test)
    slave.exit_initialization_mode()
    assert slave._state.subsystem_state == {}
    marker = {"stc": {"pump_on": True, "limit_hold": False, "lockout": True}}
    slave._state.subsystem_state = copy.deepcopy(marker)
    for k in range(3):
        slave.T0, slave.dhw_draw, slave.T_sup_w, slave.T_sur = 10.0, 0.0, T_SUP, 20.0
        slave.do_step(float(k * DT), float(DT))
        assert slave._state.subsystem_state == marker
