"""Water-heater inputs the model would otherwise answer for something else (#96, round 5).

* R744: its critical point (31.0 degC) is below the delivery set point. The
  constructor ended in a speed-bracket message, and with ``rps_rated`` given it
  built a heater whose set points were pulled to 21/16 degC and failed at
  every point.
* A hot-water draw of 0.5 m3/s (a litre value typed as m3/s) ran: the tank
  fell to the mains temperature.
* A multi-node tank with ``prevent_simultaneous_flow=True`` kept its volume
  and ran the draw and the refill together (101 L tank: 649 L delivered over
  80 min, the level never below 1), ignoring the request.
"""

from __future__ import annotations

import contextlib
import io
import warnings

import numpy as np
import pytest

import tmhp
from tmhp._validation import DHW_TANK_TURNOVER_MIN_S

BOILERS = (tmhp.AirSourceHeatPumpBoiler, tmhp.GroundSourceHeatPumpBoiler, tmhp.WaterSourceHeatPumpBoiler)


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


@pytest.mark.parametrize("cls", BOILERS, ids=lambda c: c.__name__)
@pytest.mark.parametrize("extra", [{}, {"rps_rated": 40.0}], ids=["default", "rps_rated"])
def test_r744_water_heater_is_refused_with_its_reason(cls, extra):
    with pytest.raises(ValueError, match=r"critical temperature \(31\.0 degC\).*subcritical refrigerant"):
        _quiet(cls, ref="R744", **extra)


def test_reference_state_names_the_critical_point_before_the_speed_search():
    """The rating solve itself says why (a sink below the delivery check, so the constructor lets it through)."""
    from tmhp.reference_state import ReferenceStateError

    with pytest.raises(ReferenceStateError) as excinfo:
        # R744 heating water to 25 degC: the delivery check passes (25 < 31.0),
        # the 55 degC rating condition cannot be met.
        _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R744", T_mix_w_out=25.0, T_sup_w=10.0)
    message = str(excinfo.value)
    assert "at or above the critical temperature of R744 (31.0 °C)" in message
    assert "could not bracket" not in message
    assert "choose a subcritical refrigerant" in message


@pytest.fixture(scope="module")
def ashpb():
    return _quiet(tmhp.AirSourceHeatPumpBoiler)


def test_draw_that_replaces_the_tank_in_a_blink_is_refused(ashpb):
    n = 2
    with pytest.raises(ValueError, match=r"dhw_usage_schedule must be a hot-water draw in m3/s.*at index 1"):
        ashpb.analyze_dynamic(n * 600, 600, 55.0, np.array([1e-4, 0.5]), np.full(n, 7.0))
    state = ashpb.make_initial_state(55.0)
    inputs = {"n": 0, "current_time_s": 0.0, "T0": 7.0, "dV_mix_w_out": 0.5, "T_sup_w": 15.0, "T_sur": 20.0}
    with pytest.raises(ValueError, match="dV_mix_w_out must be a hot-water draw"):
        ashpb.step(state, inputs, 600.0)


@pytest.mark.parametrize("cls", BOILERS, ids=lambda c: c.__name__)
def test_draw_limit_scales_with_the_tank(cls):
    model = _quiet(cls)
    limit = model.V_tank_full / DHW_TANK_TURNOVER_MIN_S
    ok = _quiet(model.analyze_dynamic, 600, 600, 55.0, np.array([0.99 * limit]), np.array([7.0]))
    assert len(ok) == 1  # a draw near the limit runs (the tank falls towards the mains)
    with pytest.raises(ValueError, match="hot-water draw"):
        _quiet(model.analyze_dynamic, 600, 600, 55.0, np.array([1.01 * limit]), np.array([7.0]))


def test_ordinary_draws_pass_the_limit(ashpb):
    # 15 L/min, the largest draw of the test suite and the examples, is 60x below the limit.
    assert ashpb.V_tank_full / DHW_TANK_TURNOVER_MIN_S / 50 > 2.5e-4


@pytest.mark.parametrize("tank_model", ["stratified", "hybrid"])
def test_multi_node_tank_refuses_to_keep_draw_and_refill_apart(tank_model):
    with pytest.raises(ValueError, match=r"prevent_simultaneous_flow=True needs a tank whose level varies.*lumped"):
        _quiet(tmhp.GroundSourceHeatPumpBoiler, tank_model=tank_model, prevent_simultaneous_flow=True)
    # Each on its own is fine.
    _quiet(tmhp.GroundSourceHeatPumpBoiler, tank_model=tank_model)
    _quiet(tmhp.GroundSourceHeatPumpBoiler, tank_model="lumped", prevent_simultaneous_flow=True)
