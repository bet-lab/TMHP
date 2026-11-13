"""enable_borehole_dynamics on the ground-source models (end to end)."""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from tmhp import GroundSourceHeatPump, GroundSourceHeatPumpBoiler
from tmhp.borehole_capacity import DynamicGFunction, enable_borehole_dynamics
from tmhp.constants import c_w, rho_w
from tmhp.ground_coupling import AggregateGFunctionCoupler


def _boiler(**kwargs) -> GroundSourceHeatPumpBoiler:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return GroundSourceHeatPumpBoiler(**kwargs)


def _run(model: GroundSourceHeatPumpBoiler, hours: float, dt: float, draw_lpm: float = 4.0):
    n = int(hours * 3600 / dt)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return model.analyze_dynamic(n * dt, dt, 55.0, np.full(n, draw_lpm / 60000.0), np.full(n, 5.0))


def test_derives_everything_from_the_model():
    model = _boiler(H_b=150.0, r_b=0.07, k_g=2.0)
    steady_g, r_b = model._gfunc_interp, model.R_b
    dynamic = enable_borehole_dynamics(model)
    assert isinstance(dynamic, DynamicGFunction) and model._gfunc_interp is dynamic
    assert dynamic.field is steady_g
    assert dynamic.section.steady_resistance == pytest.approx(r_b, rel=1e-12)
    assert dynamic.section.r_b == 0.07
    assert dynamic.section.rhoc_s == model.rho_s * model.c_s
    assert isinstance(model._ground_coupler, AggregateGFunctionCoupler) and model._ground_coupler._g is dynamic
    # Transit through 150 m of U-tube at the model's own loop flow (a few minutes).
    m_cp = model.dV_b_f_m3s * rho_w * c_w
    assert dynamic.transit_time == pytest.approx(dynamic.section.C_f * 150.0 / m_cp, rel=1e-9)
    assert 200.0 < dynamic.transit_time < 600.0
    # Calling it again replaces the response instead of nesting it.
    again = enable_borehole_dynamics(model, transit=False)
    assert again.field is steady_g and again.transit_time is None


def test_rejects_models_it_cannot_serve():
    custom = AggregateGFunctionCoupler(lambda t: 0.0 * np.asarray(t, dtype=float))
    with pytest.raises(ValueError, match="injected ground_coupler"):
        enable_borehole_dynamics(_boiler(ground_coupler=custom))
    with pytest.raises(TypeError, match="does not expose a borehole field"):
        enable_borehole_dynamics(object())


def test_rejects_flow_dependent_models():
    """One flow, one resistance: the kernel cancels the model's steady terms only for those."""
    with pytest.raises(ValueError, match="variable_Rb"):
        enable_borehole_dynamics(_boiler(variable_Rb=True))
    variable = _boiler(ground_flow_control="optimal_power")
    with pytest.raises(ValueError, match="one loop flow"):
        enable_borehole_dynamics(variable)
    # A lumped fluid does not depend on the flow: allowed.
    assert enable_borehole_dynamics(variable, transit=False).transit_time is None


def test_space_conditioning_model_takes_it_too():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = GroundSourceHeatPump()
    dynamic = enable_borehole_dynamics(model)
    assert model._gfunc_interp is dynamic and dynamic.transit_time is not None


def test_start_up_no_longer_drops_the_source_by_q_rb():
    """10 min on / 20 min off: the steady model swings the outlet by 3 K, the dynamic one by under 1 K."""
    schedule = [(k * 0.5, k * 0.5 + 1 / 6) for k in range(48)]
    kwargs = dict(dt_s=600.0, t_max_s=4 * 86400.0, hp_on_schedule=schedule)
    steady, dynamic = _boiler(**kwargs), _boiler(**kwargs)
    enable_borehole_dynamics(dynamic)
    out_s = _run(steady, 6, 600.0)
    out_d = _run(dynamic, 6, 600.0)
    on = out_s["hp_is_on"].astype(bool).to_numpy()
    assert on.sum() >= 10 and np.array_equal(on, out_d["hp_is_on"].astype(bool).to_numpy())
    t_s = out_s["T_bhe_f_out [°C]"].to_numpy()
    t_d = out_d["T_bhe_f_out [°C]"].to_numpy()
    assert t_s[~on].mean() - t_s[on].mean() > 2.5
    assert abs(t_d[~on][3:].mean() - t_d[on][1:].mean()) < 1.0
    assert t_d[on].mean() - t_s[on].mean() > 1.5  # the source the compressor sees is 2 K warmer
    # The first running step sees the ground as it was: no instantaneous q' R_b.
    first = int(np.flatnonzero(on)[0])
    assert steady.Ts - t_s[first] > 2.5
    assert abs(dynamic.Ts - t_d[first]) < 0.5
    # Same heat taken from the ground within a few percent; only its temperature level changed.
    q_s, q_d = out_s["Q_bhe [W]"].to_numpy()[on].mean(), out_d["Q_bhe [W]"].to_numpy()[on].mean()
    assert q_d == pytest.approx(q_s, rel=0.05)


def test_reported_wall_and_fluid_follow_the_model_columns():
    """The model's own result columns reproduce the dynamic response for the loads it reports."""
    kwargs = dict(dt_s=600.0, t_max_s=4 * 86400.0)
    model = _boiler(**kwargs)
    dynamic = enable_borehole_dynamics(model)
    out = _run(model, 4, 600.0)
    q_line = out["Q_bhe [W]"].to_numpy() / model.total_borehole_length
    pulses = np.diff(np.concatenate(([0.0], q_line)))
    lags = np.arange(len(q_line)) * 600.0
    kernel = np.asarray(dynamic.step_mean(lags, 600.0))
    rise = np.array([np.dot(pulses[: n + 1], kernel[n::-1]) for n in range(len(q_line))])
    np.testing.assert_allclose(out["T_bhe [°C]"].to_numpy(), model.Ts - rise, atol=1e-9)
    np.testing.assert_allclose(out["T_bhe_f [°C]"].to_numpy(), model.Ts - rise - q_line * model.R_b, atol=1e-9)
