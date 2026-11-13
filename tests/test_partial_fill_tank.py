"""A tank whose level varies conserves its water and its energy (#96 T1).

``tank_always_full=False`` (draw-off lowers the level, a make-up valve refills
it between ``tank_level_lower_bound`` and ``tank_level_upper_bound``) and
``prevent_simultaneous_flow`` (no make-up while drawing) used to break both
balances of the boilers:

* the refill took ``dV_tank_w_in_refill`` only when a whole step of it fitted
  the room left, else nothing: with the default 1 L/s and any step above
  151 s the 151 L tank never refilled and drained to the 0.001 floor;
* the level came from the draw at the start temperature, the energy balance
  from the draw at the end temperature, and ``max(0.001, level)`` kept a heat
  capacity the water no longer had: the default ASHPB lost 1.33 tank volumes
  of water and +1.6 kWh in 3 h (+11 %); a tank drained below its make-up
  rate reached 97 °C with the heat pump off (+80 %);
* the in-step thermostat read the absolute enthalpy of the water that left
  as heat (about 100 kW for 8 L/min) and started the compressor on a tank
  that was only draining.

These tests pin the conservative balance ``C·(L1·T1 - L0·T0) = dt·(Q + ρc·
(V̇in·T_in - V̇out·T1) - loss)`` with ``L1 = L0 + (V̇in - V̇out)·dt/V``, the flow
limits (no draw below ``TANK_LEVEL_MIN``, no make-up above full) and the
unmet hot water they leave. The always-full tank is pinned bit for bit by
the step goldens (``tests/test_step_kernel.py``).
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

from tmhp import AirSourceHeatPumpBoiler, GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler
from tmhp.constants import c_w, rho_w
from tmhp.dynamic_context import (
    TANK_LEVEL_MIN,
    ControlState,
    StepContext,
    determine_tank_refill_flow,
    limit_tank_flows,
    resolve_step_operation,
    tank_mass_energy_residual,
)

from .invariants._checks import tank_energy_residual

T_SUP = 15.0
OFF = [(0.0, 0.0)]  # heat pump never scheduled


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        yield


# ---------------------------------------------------------------------------
# shared functions
# ---------------------------------------------------------------------------

V_FULL = 0.15  # m3


def _refill(level: float, dt: float, refill: float = 0.001, draw: float = 0.0, refilling: bool = True, **kw):
    args = dict(
        dt=dt,
        tank_level=level,
        dV_tank_w_out=draw,
        V_tank_full=V_FULL,
        tank_always_full=False,
        prevent_simultaneous_flow=False,
        tank_level_lower_bound=0.5,
        tank_level_upper_bound=1.0,
        dV_tank_w_in_refill=refill,
        is_refilling=refilling,
    )
    args.update(kw)
    return determine_tank_refill_flow(**args)


def test_refill_takes_the_part_of_a_step_that_reaches_the_target():
    # 0.6 m3 of make-up in a 600 s step does not fit the 0.105 m3 of room: it
    # used to refill nothing; it now fills to the upper bound and stops.
    dV_in, refilling = _refill(0.3, 600.0)
    assert dV_in == pytest.approx(0.7 * V_FULL / 600.0, rel=1e-12)
    assert not refilling
    # A step that does fit takes the full rate and keeps refilling.
    dV_in, refilling = _refill(0.3, 60.0)
    assert dV_in == 0.001 and refilling
    # prevent_simultaneous_flow: same cut, and no make-up while drawing.
    dV_in, _ = _refill(0.3, 600.0, tank_always_full=True, prevent_simultaneous_flow=True)
    assert dV_in == pytest.approx(0.7 * V_FULL / 600.0, rel=1e-12)
    dV_in, _ = _refill(0.3, 600.0, draw=1e-4, tank_always_full=True, prevent_simultaneous_flow=True)
    assert dV_in == 0.0


def test_refill_valve_is_followed_inside_the_step():
    """The valve opens at the lower bound and closes at the upper one mid-step.

    One 600 s step and ten 60 s steps admit the same water and end at the
    same level (the valve used to open on the level expected at the step
    end, so long steps refilled earlier).
    """
    draw, refill = 1e-4, 1e-3  # 6 and 60 L/min
    # From 0.6: the level reaches 0.5 after 150 s, the valve fills to 1.0 in
    # 0.5·V/(refill - draw) = 83.3 s and the tank drains for the rest.
    dV_in, refilling = _refill(0.6, 600.0, refill=refill, draw=draw, refilling=False)
    t_fill = 0.5 * V_FULL / (refill - draw)
    assert dV_in * 600.0 == pytest.approx(refill * t_fill, rel=1e-12)
    assert not refilling
    level, vol, state = 0.6, 0.0, False
    for _ in range(10):
        rate, state = _refill(level, 60.0, refill=refill, draw=draw, refilling=state)
        vol += rate * 60.0
        level += (rate - draw) * 60.0 / V_FULL
    assert vol == pytest.approx(dV_in * 600.0, rel=1e-9)
    assert level == pytest.approx(0.6 + (dV_in - draw) * 600.0 / V_FULL, rel=1e-9)
    assert state is refilling


@pytest.mark.parametrize("draw", [0.0, 1e-5])
def test_open_valve_carried_in_above_the_upper_bound_closes_without_negative_make_up(draw):
    """A valve carried in open with the level already past the upper bound just closes.

    The step latches the valve on its start-temperature draw and solves a
    smaller one, so the level can end above the bound with the valve still
    open; the closing time from there is negative and used to give a
    negative make-up (-2.5e-6 m3/s for 0.91 against 0.9).
    """
    dV_in, refilling = _refill(0.91, 600.0, draw=draw, refilling=True, tank_level_upper_bound=0.9)
    assert dV_in == 0.0
    assert not refilling
    # The limiter does not pass a negative flow on either port.
    dV_in, dV_out, level = limit_tank_flows(0.91, -1e-5, -2e-5, 600.0, V_FULL)
    assert (dV_in, dV_out) == (0.0, 0.0)
    assert level == pytest.approx(0.91)


def test_flow_limits_keep_the_level_between_the_floor_and_full():
    dt = 600.0
    # Draw 1e-4 m3/s for 600 s = 0.06 m3 from 0.03 m3 above the floor, with
    # 2e-5 m3/s of make-up: the draw stops at the floor.
    level0 = TANK_LEVEL_MIN + 0.03 / V_FULL
    dV_in, dV_out, level = limit_tank_flows(level0, 2e-5, 1e-4, dt, V_FULL)
    assert dV_in == 2e-5
    assert dV_out == pytest.approx(0.03 / dt + 2e-5, rel=1e-12)
    assert level == pytest.approx(TANK_LEVEL_MIN, abs=1e-12)
    # Make-up into a nearly full tank stops at full; the draw passes through.
    dV_in, dV_out, level = limit_tank_flows(0.99, 1e-3, 1e-5, dt, V_FULL)
    assert dV_out == 1e-5
    assert dV_in == pytest.approx(0.01 * V_FULL / dt + 1e-5, rel=1e-12)
    assert level == pytest.approx(1.0, abs=1e-12)
    # Within the limits nothing changes and the level follows the flows.
    dV_in, dV_out, level = limit_tank_flows(0.5, 1e-5, 4e-5, dt, V_FULL)
    assert (dV_in, dV_out) == (1e-5, 4e-5)
    assert level == pytest.approx(0.5 - 3e-5 * dt / V_FULL, rel=1e-12)


def _draining_step(T_start_C: float, dt: float = 600.0, Q: float = 0.0):
    """A full 151 L tank drawn at 8 L/min (mixed 40 °C), no loss, no make-up."""
    C_tank = c_w * rho_w * V_FULL
    ctx = StepContext(
        n=0,
        current_time_s=0.0,
        current_hour=0.0,
        hour_of_day=0.0,
        T0=5.0,
        T0_K=278.15,
        activation_flags={},
        T_tank_w_K=T_start_C + 273.15,
        tank_level=1.0,
        dV_mix_w_out=8.0 / 60000.0,
        T_sup_w_K=T_SUP + 273.15,
    )

    def residual(T_K, level=1.0):
        ctrl = ControlState(is_on=Q > 0, Q_heat_source=Q, dV_tank_w_in_ctrl=0.0)
        return tank_mass_energy_residual(
            [T_K, level], ctx, ctrl, dt, T_SUP + 273.15, T_SUP + 273.15, 313.15, C_tank, 0.0, V_FULL, {}, {}
        )

    return ctx, C_tank, residual


def test_draining_tank_keeps_its_temperature():
    """Water leaving at the tank temperature removes energy and mass, not temperature."""
    from scipy.optimize import brentq

    dt = 600.0
    _, _, residual = _draining_step(61.0, dt)
    T1_K = brentq(lambda T: residual(T)[0], 273.15, 373.15, xtol=1e-12)
    assert abs(T1_K - (61.0 + 273.15)) < 1e-6
    # Its mass residual is zero at the level the draw leaves (x[1] is not
    # used by the energy residual of a level-varying tank).
    out = 8.0 / 60000.0 * (40.0 - T_SUP) / (61.0 - T_SUP)
    assert residual(T1_K, level=1.0 - out * dt / V_FULL)[1] == pytest.approx(0.0, abs=1e-12)
    assert residual(T1_K, level=0.3)[0] == residual(T1_K)[0]


def test_in_step_thermostat_does_not_start_on_a_draining_tank():
    """1 K above the lower bound, a tank that only drains stays there: no start.

    The thermostat used to read ``-ρc·V̇_out·T`` (about -100 kW here, the
    absolute enthalpy of the water that left) as heat loss, cool the tank
    through its lower bound in seconds and start the compressor.
    """
    dt = 600.0
    ctx, C_tank, residual = _draining_step(61.0, dt)
    calls = []

    def solve_cycle(T_C, _fraction):
        calls.append(T_C)
        return {"hp_is_on": True, "Q_ref_tank [W]": 15000.0}

    run, cycle = resolve_step_operation(
        T_start_K=ctx.T_tank_w_K,
        call_start=False,
        running_prev=False,
        phase_age_s=math.inf,
        scheduled=True,
        T_lower=60.0,
        T_upper=65.0,
        dt=dt,
        C_curr=C_tank,
        C_next=C_tank * 0.5,
        C_tank=C_tank,
        residual_free=lambda T: residual(T)[0],
        solve_cycle=solve_cycle,
        Q_request=15000.0,
    )
    assert run.runtime_fraction == 0.0 and run.starts == 0
    assert cycle is None and not calls


# ---------------------------------------------------------------------------
# ASHPB dynamic runs
# ---------------------------------------------------------------------------


def _run(hours: float, dt: int, T_init: float, lpm, level_init: float = 1.0, **kw):
    model = AirSourceHeatPumpBoiler(**kw)
    n = int(round(hours * 3600 / dt))
    h = np.arange(n) * dt / 3600.0
    demand = np.asarray(lpm(h), dtype=float) / 60000.0
    df = model.analyze_dynamic(
        n * dt,
        dt,
        T_init,
        demand,
        np.full(n, 5.0),
        T_sup_w_schedule=np.full(n, T_SUP),
        T_sur_schedule=np.full(n, 20.0),
        tank_level_init=level_init,
    )
    return model, df, demand


def _check_balances(model, df, T_init: float, dt: int, level_init: float = 1.0):
    T = df["T_tank_w [°C]"].to_numpy(float)
    assert np.all((T > 0.0) & (T < 100.0)), (T.min(), T.max())
    level = df["tank_level [-]"].to_numpy(float)
    assert np.all((level >= TANK_LEVEL_MIN - 1e-12) & (level <= 1.0 + 1e-12)), (level.min(), level.max())
    bal = tank_energy_residual(df, model.C_tank, T_init, dt, T_ref=T_SUP, level_init=level_init, always_full=False)
    assert abs(bal["mass_residual"]) < 0.01, bal
    # Heat input plus water throughput, and the energy stored at the start
    # (a refill of mains water carries none relative to T_SUP).
    stored = model.C_tank * level_init * abs(T_init - T_SUP)
    assert abs(bal["residual_J"]) < 0.01 * (bal["heat_J"] + bal["throughput_J"] + stored), bal
    return bal


def _const(value: float):
    return lambda h: np.full(h.shape, value)


def test_draw_only_lowers_the_level_by_the_water_drawn():
    """(a) 20 min of 8 L/min from a full 60 °C tank, refill below 0.2, heat pump off."""
    dt = 300
    model, df, _ = _run(
        1 / 3, dt, 60.0, _const(8.0), tank_always_full=False, tank_level_lower_bound=0.2, hp_on_schedule=OFF
    )
    bal = _check_balances(model, df, 60.0, dt)
    drawn = float(np.nansum(df["dV_tank_w_out [m3/s]"].to_numpy(float)) * dt)
    assert df["tank_level [-]"].iloc[-1] == pytest.approx(1.0 - drawn / model.V_tank_full, rel=1e-9)
    assert np.nansum(df["dV_tank_w_in [m3/s]"].to_numpy(float)) == 0.0
    # Only the envelope loss cools the water (was 18.9 °C: -6.6 kWh, -541 %).
    assert df["T_tank_w [°C]"].min() > 59.5
    assert bal["heat_J"] == 0.0


def test_refill_only_fills_the_tank_with_mains_water():
    """(b) From level 0.3 at 60 °C, no draw, heat pump off: the 600 s step refills to full.

    The make-up used to need a whole step of 1 L/s to fit (0.3 m3 > 0.106 m3),
    so the level stayed at 0.3.
    """
    dt = 300
    model, df, _ = _run(1 / 3, dt, 60.0, _const(0.0), level_init=0.3, tank_always_full=False, hp_on_schedule=OFF)
    _check_balances(model, df, 60.0, dt, level_init=0.3)
    assert df["tank_level [-]"].iloc[0] == pytest.approx(1.0, abs=1e-12)
    # Adiabatic mixing of 0.3 at 60 °C with 0.7 at 15 °C, less one step's loss.
    T_mix = 0.3 * 60.0 + 0.7 * T_SUP
    assert df["T_tank_w [°C]"].iloc[0] == pytest.approx(T_mix, abs=0.05)


def test_draw_refill_and_heating_close_both_balances():
    """(c) 1 h of 8 L/min with the refill band 0.8-1.0 and the heat pump running."""
    dt = 300
    model, df, _ = _run(
        1.5, dt, 58.0, lambda h: np.where(h < 1.0, 8.0, 0.0), tank_always_full=False, tank_level_lower_bound=0.8
    )
    bal = _check_balances(model, df, 58.0, dt)
    assert bal["heat_J"] > 0.0
    assert np.nansum(df["dV_tank_w_in [m3/s]"].to_numpy(float)) > 0.0
    assert df["T_tank_w [°C]"].max() <= model.T_tank_w_upper_bound + 1e-6


@pytest.mark.parametrize("hp", ["off", "on"])
def test_draw_beyond_the_make_up_stops_at_the_floor_and_reports_unmet_water(hp):
    """(d) 15 L/min against 6 L/min of make-up for 1 h: the tank drains to the floor.

    It used to reach 97 °C with the heat pump off (+20.9 kWh, +80 %).
    """
    dt = 300
    kw = dict(tank_always_full=False, tank_level_lower_bound=0.2, dV_tank_w_in_refill=0.0001)
    if hp == "off":
        kw["hp_on_schedule"] = OFF
    model, df, demand = _run(1.0, dt, 60.0, _const(15.0), **kw)
    _check_balances(model, df, 60.0, dt)
    level = df["tank_level [-]"].to_numpy(float)
    assert level.min() == pytest.approx(TANK_LEVEL_MIN, abs=1e-9)
    unmet = df["dV_mix_w_out_unmet [m3/s]"].to_numpy(float)
    delivered = np.nan_to_num(df["dV_mix_w_out [m3/s]"].to_numpy(float))
    assert unmet.sum() > 0.0
    np.testing.assert_allclose(delivered + unmet, demand, rtol=1e-12, atol=1e-15)
    # At the floor the tank passes its make-up: draw = make-up.
    floor = level <= TANK_LEVEL_MIN + 1e-9
    out = np.nan_to_num(df["dV_tank_w_out [m3/s]"].to_numpy(float))
    inflow = np.nan_to_num(df["dV_tank_w_in [m3/s]"].to_numpy(float))
    assert floor.sum() >= 2
    np.testing.assert_allclose(out[floor][1:], inflow[floor][1:], rtol=1e-9)


def test_prevent_simultaneous_flow_conserves_and_refills_after_the_draw():
    """No make-up while drawing: 1 h at 8 L/min drains the tank, it refills afterwards."""
    dt = 600
    model, df, _ = _run(
        3.0, dt, 60.0, lambda h: np.where(h < 1.0, 8.0, 0.0), tank_always_full=True, prevent_simultaneous_flow=True
    )
    _check_balances(model, df, 60.0, dt)
    drawing = np.nan_to_num(df["dV_mix_w_out [m3/s]"].to_numpy(float)) > 0.0
    assert np.nansum(df["dV_tank_w_in [m3/s]"].to_numpy(float)[drawing]) == 0.0
    assert df["tank_level [-]"].iloc[-1] == pytest.approx(1.0, abs=1e-9)
    assert df["dV_mix_w_out_unmet [m3/s]"].sum() > 0.0


def test_partial_fill_heat_input_does_not_depend_on_time_step():
    """Draw + refill + heat-up over 1 h: dt 60 s and 600 s agree (5.91 kWh both, 0.06 %).

    The run ends full and at the set point, so the stored energy at the end
    is the same; over a day that ends mid-way through a refill cycle the
    heat input differs by the stored energy (``heat - ΔU`` still agrees).
    Before the fix the 600 s run never refilled (0.001 floor).
    """
    out = {}
    for dt in (60, 600):
        model, df, _ = _run(
            1.0, dt, 58.0, lambda h: np.where((h >= 1 / 6) & (h < 0.5), 8.0, 0.0), tank_always_full=False
        )
        _check_balances(model, df, 58.0, dt)
        T, level = df["T_tank_w [°C]"].to_numpy(float), df["tank_level [-]"].to_numpy(float)
        heat = float(np.nansum(df["Q_ref_tank [W]"].to_numpy(float)) * dt)
        dU = model.C_tank * (level[-1] * (T[-1] - T_SUP) - (58.0 - T_SUP))
        out[dt] = (heat, heat - dU)
    (q60, net60), (q600, net600) = out[60], out[600]
    assert q60 > 0.0
    assert abs(q600 - q60) < 0.05 * q60, (q60 / 3.6e6, q600 / 3.6e6)
    assert abs(net600 - net60) < 0.01 * net60, (net60 / 3.6e6, net600 / 3.6e6)


def test_level_varying_tank_cannot_start_empty():
    model = AirSourceHeatPumpBoiler(tank_always_full=False)
    with pytest.raises(ValueError, match="TANK_LEVEL_MIN"):
        model.make_initial_state(60.0, tank_level_init=0.0)
    model.make_initial_state(60.0, tank_level_init=TANK_LEVEL_MIN)
    # An always-full tank keeps accepting any level in [0, 1].
    AirSourceHeatPumpBoiler().make_initial_state(60.0, tank_level_init=0.0)


# ---------------------------------------------------------------------------
# GSHPB / WSHPB share the residual and the refill rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler])
def test_ground_and_water_boilers_close_partial_fill_balance(cls):
    """Draw + refill + heating at dt 600 s: was 1,637 °C and +10.5 kWh (+75 %)."""
    dt, n = 600, 18
    model = cls(dt_s=dt, t_max_s=86400.0, UA_tank_hx=2000.0, tank_always_full=False)
    h = (np.arange(n) + 0.5) * dt / 3600.0
    extra = {"T_source_w_schedule": np.full(n, 10.0)} if cls is WaterSourceHeatPumpBoiler else {}
    df = model.analyze_dynamic(
        n * dt,
        dt,
        54.0,
        np.where(h < 1.0, 8.0, 0.0) / 60000.0,
        np.full(n, 5.0),
        T_sup_w_schedule=np.full(n, T_SUP),
        T_sur_schedule=np.full(n, 20.0),
        **extra,
    )
    assert np.nansum(df["dV_tank_w_in [m3/s]"].to_numpy(float)) > 0.0
    _check_balances(model, df, 54.0, dt)
