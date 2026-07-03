import pytest

from tmhp import ASHPB_STC_tank, GSHPB_STC_tank
from tmhp.calc_util import C2K
from tmhp.dynamic_context import ControlState, StepContext
from tmhp.subsystems import SolarThermalCollector


def _ctx() -> StepContext:
    return StepContext(
        n=0,
        current_time_s=12 * 3600.0,
        current_hour=12.0,
        hour_of_day=12.0,
        T0=5.0,
        T0_K=C2K(5.0),
        activation_flags={"stc": True},
        T_tank_w_K=C2K(60.0),
        tank_level=1.0,
        dV_mix_w_out=0.0,
        I_DN=400.0,
        I_dH=0.0,
        T_sup_w_K=C2K(10.0),
    )


def _ctrl(e_tot_w: float = 0.0) -> ControlState:
    return ControlState(
        is_on=False,
        Q_heat_source=0.0,
        dV_tank_w_in_ctrl=None,
        result={"E_tot [W]": e_tot_w},
    )


def _stc(e_pump_w: float) -> SolarThermalCollector:
    return SolarThermalCollector(
        A_stc=4.0,
        A_stc_pipe=4.0,
        dV_stc_w=1.0e-4,
        E_stc_pump=e_pump_w,
    )


def test_ashpb_stc_tank_preserves_collector_pump_power():
    stc = _stc(37.0)
    model = ASHPB_STC_tank(stc=stc)

    assert model._stc.E_stc_pump == pytest.approx(37.0)


def test_ashpb_stc_tank_does_not_circulate_when_gain_does_not_beat_pump():
    stc = _stc(1000.0)
    model = ASHPB_STC_tank(stc=stc)
    ctx = _ctx()

    probe = stc.calc_performance(ctx.I_DN, ctx.I_dH, ctx.T_tank_w_K, ctx.T0_K, is_active=True)
    assert probe["T_stc_w_out_K"] > ctx.T_tank_w_K
    assert (probe["Q_stc_w_out"] - probe["Q_stc_w_in"]) < stc.E_stc_pump

    state = model._run_subsystems(ctx, _ctrl(), dt=60.0, T_tank_w_in_K=ctx.T_sup_w_K)

    assert state["stc"]["stc_active"] is False
    assert state["stc"]["E_subsystem"] == pytest.approx(0.0)


def test_ashpb_stc_tank_adds_active_pump_power_to_total_electricity():
    stc = _stc(40.0)
    model = ASHPB_STC_tank(stc=stc)
    ctx = _ctx()
    ctrl = _ctrl(e_tot_w=123.0)

    state = model._run_subsystems(ctx, ctrl, dt=60.0, T_tank_w_in_K=ctx.T_sup_w_K)
    assert state["stc"]["stc_active"] is True

    row = model._augment_results(dict(ctrl.result), ctx, ctrl, state, T_solved_K=ctx.T_tank_w_K)

    assert row["E_stc_pump [W]"] == pytest.approx(40.0)
    assert row["E_tot [W]"] == pytest.approx(163.0)


def test_gshpb_stc_tank_uses_same_pump_threshold_for_circulation():
    stc = _stc(1000.0)
    model = GSHPB_STC_tank(stc=stc)
    ctx = _ctx()

    state = model._run_subsystems(ctx, _ctrl(), dt=60.0, T_tank_w_in_K=ctx.T_sup_w_K)

    assert state["stc"]["stc_active"] is False
    assert state["stc"]["E_subsystem"] == pytest.approx(0.0)
