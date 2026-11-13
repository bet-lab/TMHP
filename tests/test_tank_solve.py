"""The lumped tank's end-of-step solve never substitutes a different energy balance.

The three boilers used to catch every exception of their tank solve and step
explicitly with a balance that left the subsystem heat out (and, for the air
source boiler, did so whenever the solution lay above 100 °C). Now a solve
that returns no root is recovered with the *same* residual, a step with no
solution in the liquid range is named in a warning, and the ground/water source
boilers no longer pin the tank at the mains temperature.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

import tmhp
from tmhp._tank_solve import (
    TANK_RESIDUAL_TOL_K,
    TankSolveWarning,
    recover_tank_temperature,
    solve_lumped_tank,
)
from tmhp.subsystems import UVLamp

C = 4.0e5  # tank capacity [J/K]
DT = 600.0
T0 = 320.0


def linear_residual(rate_W: float, ua: float = 0.0, T_amb: float = 293.15):
    """Boiler-style residual [K] of ``C·dT/dt = rate − ua·(T − T_amb)`` over one implicit step."""

    def residual(T):
        T = np.atleast_1d(np.asarray(T, dtype=float))
        return (C * T - C * T0 - DT * (rate_W - ua * (T - T_amb))) / C

    return residual


KW = dict(T_start_K=T0, dt=DT, C_curr=C, C_next=C, C_tank=C, who="TestBoiler", step=7)


def test_root_of_the_residual_is_returned_silently():
    res = linear_residual(4000.0, ua=5.0)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        T = solve_lumped_tank(res, T0, **KW)
    assert abs(float(res(T)[0])) < 1e-9
    expected = (C * T0 + DT * (4000.0 + 5.0 * 293.15)) / (C + DT * 5.0)
    assert pytest.approx(expected, abs=1e-8) == T


def test_stalled_first_solve_is_recovered_with_the_same_residual(monkeypatch):
    """fsolve handing back its start point (no progress) is not accepted as the answer."""
    import scipy.optimize

    monkeypatch.setattr(scipy.optimize, "fsolve", lambda func, x0, **kw: np.asarray(x0, dtype=float))
    res = linear_residual(4000.0, ua=5.0)
    assert abs(float(res(T0)[0])) > TANK_RESIDUAL_TOL_K
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        T = solve_lumped_tank(res, T0, **KW)
    assert abs(float(res(T)[0])) < 1e-9


def test_first_solve_raising_is_recovered_not_replaced(monkeypatch):
    import scipy.optimize

    def boom(func, x0, **kw):
        raise ValueError("solver blew up")

    monkeypatch.setattr(scipy.optimize, "fsolve", boom)
    res = linear_residual(-2500.0, ua=5.0)
    T = solve_lumped_tank(res, T0, **KW)
    assert abs(float(res(T)[0])) < 1e-9


def test_no_solution_in_the_liquid_range_is_stepped_with_the_whole_heat_rate_and_warned():
    """600 kW into 96 L for 10 min ends far above 200 °C: explicit step, every watt kept, named."""
    rate = 6.0e5
    res = linear_residual(rate)
    with pytest.warns(TankSolveWarning, match=r"TestBoiler.*step 7.*no solution between 0 and 200 °C"):
        T = recover_tank_temperature(res, **KW)
    assert pytest.approx(T0 + DT * rate / C) == T  # the residual's own heat rate, nothing dropped
    assert T - 273.15 > 200.0


def test_residual_that_is_not_a_number_raises():
    def residual(T):
        return np.atleast_1d(np.asarray(T, dtype=float)) * math.nan

    with pytest.raises(RuntimeError, match=r"TestBoiler.*step 7.*not a number"):
        solve_lumped_tank(residual, T0, **KW)


# ---------------------------------------------------------------------------
# through the boilers
# ---------------------------------------------------------------------------


def _flat(n: int, v: float) -> np.ndarray:
    return np.full(n, float(v))


def _lamp_to_water_J(df, uv: UVLamp, dt: float) -> float:
    return uv.heat_to_water_fraction * float(df["E_uv [W]"].sum()) * dt


def _tank_gain_J(model, df, T_init: float, dt: float) -> float:
    return model.C_tank * (float(df["T_tank_w [°C]"].iloc[-1]) - T_init) + float(df["Q_tank_loss [W]"].sum()) * dt


def test_ashpb_keeps_subsystem_heat_when_the_tank_passes_100_degC():
    """A 3 kW lamp in a 97 °C tank with the heat pump off.

    The 0-100 °C bracket has no root from the second step on; the fallback used
    to step without the lamp, and the tank received 1,619 of 19,440 kJ.
    """
    dt, n = 600, 6
    uv = UVLamp(lamp_watts=3000.0, exposure_sec=3 * 3600, num_switching=1, period_sec=3 * 3600)
    m = tmhp.AirSourceHeatPumpBoiler(uv=uv, hp_on_schedule=[(0.0, 0.0)])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        df = m.analyze_dynamic(
            n * dt,
            dt,
            97.0,
            np.zeros(n),
            _flat(n, 20.0),
            T_sup_w_schedule=_flat(n, 15.0),
            T_sur_schedule=_flat(n, 20.0),
        )
    lamp = _lamp_to_water_J(df, uv, dt)
    assert lamp == pytest.approx(0.9 * 3000.0 * n * dt)
    assert _tank_gain_J(m, df, 97.0, dt) == pytest.approx(lamp, rel=1e-6)
    assert df["T_tank_w [°C]"].iloc[-1] > 100.0
    # Above 100 °C the model is outside liquid water, and says so; the balance itself was solved.
    messages = [str(w.message) for w in caught]
    assert any("above 100 °C" in msg for msg in messages)
    assert not any(issubclass(w.category, TankSolveWarning) for w in caught)


@pytest.mark.parametrize("family", ["GSHPB", "WSHPB"])
def test_tank_colder_than_the_mains_is_not_pinned_at_the_mains_temperature(family):
    """Heat pump off, no draw, a 5 °C room: the tank cools through the 15 °C mains temperature.

    It used to be reset to 15 °C every step while the loss column kept counting:
    +311 kJ appeared in 24 h.
    """
    dt, n = 3600, 12
    kw = dict(hp_on_schedule=[(0.0, 0.0)], dt_s=float(dt), t_max_s=2 * 86400.0)
    schedules = dict(T_sup_w_schedule=_flat(n, 15.0), T_sur_schedule=_flat(n, 5.0))
    if family == "GSHPB":
        pytest.importorskip("pygfunction")
        m = tmhp.GroundSourceHeatPumpBoiler(**kw)
    else:
        m = tmhp.WaterSourceHeatPumpBoiler(**kw)
        schedules["T_source_w_schedule"] = _flat(n, 8.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = m.analyze_dynamic(n * dt, dt, 15.5, np.zeros(n), _flat(n, 5.0), **schedules)
    T = df["T_tank_w [°C]"].to_numpy()
    assert T[-1] < 15.0 - 0.1
    assert np.all(np.diff(T) < 0.0)
    assert abs(_tank_gain_J(m, df, 15.5, dt)) < 1e-6 * float(df["Q_tank_loss [W]"].sum()) * dt


@pytest.mark.parametrize("family", ["GSHPB", "WSHPB"])
def test_boiler_step_whose_solver_fails_keeps_its_energy_balance(family, monkeypatch):
    """With fsolve failing on every step the run is the same: the same balance, solved by bracketing."""
    import scipy.optimize

    dt, n = 600, 6
    kw = dict(dt_s=float(dt), t_max_s=86400.0)
    schedules: dict = {}
    if family == "GSHPB":
        pytest.importorskip("pygfunction")
        cls = tmhp.GroundSourceHeatPumpBoiler
    else:
        cls = tmhp.WaterSourceHeatPumpBoiler
        schedules["T_source_w_schedule"] = _flat(n, 8.0)
    draw = np.where(np.arange(n) < 3, 1.0e-4, 0.0)

    def run():
        uv = UVLamp(lamp_watts=500.0, exposure_sec=3 * 3600, num_switching=1, period_sec=3 * 3600)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return cls(uv=uv, **kw).analyze_dynamic(n * dt, dt, 50.0, draw, _flat(n, 5.0), **schedules)

    reference = run()

    def boom(func, x0, **kwargs):
        raise RuntimeError("solver blew up")

    monkeypatch.setattr(scipy.optimize, "fsolve", boom)
    recovered = run()
    np.testing.assert_allclose(recovered["T_tank_w [°C]"], reference["T_tank_w [°C]"], atol=1e-6)
    np.testing.assert_allclose(recovered["Q_ref_tank [W]"], reference["Q_ref_tank [W]"], rtol=1e-6, atol=1e-6)
    assert (recovered["E_uv [W]"] > 0).all()
