"""The fast coupler must give the default coupler's numbers, in every call pattern it accepts.

:class:`~tmhp.ground_superposition.FastGFunctionCoupler` is an exact
re-ordering of the default temporal superposition, so every test compares it
with :class:`~tmhp.ground_coupling.AggregateGFunctionCoupler` on the same
g-function; the only tolerance is floating-point round-off.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

pytest.importorskip("pygfunction")

from tmhp import GroundSourceHeatPump, GroundSourceHeatPumpBoiler  # noqa: E402
from tmhp.borehole_capacity import enable_borehole_dynamics, with_borehole_capacity  # noqa: E402
from tmhp.g_function import precompute_gfunction  # noqa: E402
from tmhp.ground_coupling import AggregateGFunctionCoupler, GroundCoupler, reset_ground_coupler  # noqa: E402
from tmhp.ground_superposition import FastGFunctionCoupler, enable_fast_superposition  # noqa: E402

K_S, RHOC_S, H, R_BH = 2.0, 2.0e6, 120.0, 0.075
ATOL = 1e-10  # K; observed differences are below 1e-12


@pytest.fixture(scope="module")
def g_field():
    return precompute_gfunction(2, 1, 6.0, H, 4.0, R_BH, K_S / RHOC_S, K_S, t_max_s=400 * 86400.0, dt_s=3600.0)


def _kernel(name: str, g_field):
    common = dict(r_b=R_BH, k_s=K_S, rhoc_s=RHOC_S, R_b=0.10, r_out=0.016, r_in=0.013, m_flow_borehole=0.4)
    if name == "g-function":
        return g_field
    if name == "capacity":
        return with_borehole_capacity(g_field, **common)
    if name == "capacity+transit":
        return with_borehole_capacity(g_field, H=H, **common)
    if name == "callable":  # a user g-function without step_mean: Gauss-Legendre step means
        return lambda t: 0.04 * np.log1p(np.asarray(t, dtype=float) / 900.0)
    raise KeyError(name)


def _loads(n: int, seed: int = 0) -> np.ndarray:
    """Loads with long holds, switching, sign changes and changes below the pulse tolerance."""
    rng = np.random.default_rng(seed)
    q = np.repeat(rng.normal(0.0, 25.0, size=n // 7 + 1), 7)[:n]
    q[n // 3 : n // 3 + 40] = 0.0  # a rest
    k = np.arange(n // 2, n // 2 + 60)
    q[k] = np.where(k % 2 == 0, 40.0, -40.0)  # step-by-step reversals
    q[5:9] = q[4] + 3e-7  # below the default pulse tolerance: the load is held
    return q


def _pair(g, n: int, dt: float, **kw):
    t = np.arange(n) * dt
    ref = AggregateGFunctionCoupler(g, **kw)
    fast = FastGFunctionCoupler(g, **kw)
    reset_ground_coupler(ref, n, t, dt)
    reset_ground_coupler(fast, n, t, dt)
    return t, ref, fast


@pytest.mark.parametrize("kernel", ["g-function", "capacity", "capacity+transit", "callable"])
@pytest.mark.parametrize(("dt", "n"), [(3600.0, 1537), (600.0, 4099), (60.0, 2500)])
def test_every_step_matches_the_default_coupler(g_field, kernel, dt, n):
    g = _kernel(kernel, g_field)
    t, ref, fast = _pair(g, n, dt)
    q = _loads(n)
    for i in range(n):
        candidate = q[i] + 3.0
        assert fast.preview_wall_temperature_rise(i, t, candidate) == pytest.approx(
            ref.preview_wall_temperature_rise(i, t, candidate), abs=ATOL
        )
        assert fast.wall_temperature_rise(i, t, q[i]) == pytest.approx(ref.wall_temperature_rise(i, t, q[i]), abs=ATOL)
        assert fast.borehole_wall_rise(i, t) == pytest.approx(ref.borehole_wall_rise(i, t), abs=ATOL)


def test_is_a_ground_coupler(g_field):
    assert isinstance(FastGFunctionCoupler(g_field), GroundCoupler)


def test_preview_leaves_the_history_alone(g_field):
    g = _kernel("capacity+transit", g_field)
    n, dt = 300, 600.0
    t, _, fast = _pair(g, n, dt)
    _, _, plain = _pair(g, n, dt)
    q = _loads(n, seed=3)
    for i in range(n):
        for candidate in (-50.0, 0.0, q[i], 80.0):
            fast.preview_wall_temperature_rise(i, t, candidate)
        assert fast.wall_temperature_rise(i, t, q[i]) == plain.wall_temperature_rise(i, t, q[i])


def test_steps_without_a_commit_hold_the_load(g_field):
    """The default records no pulse for a step it is not called for: the load stays."""
    n, dt = 200, 3600.0
    t, ref, fast = _pair(g_field, n, dt)
    for i, q in ((0, 20.0), (1, 20.0), (37, -5.0), (38, -5.0), (150, 12.0), (199, 0.0)):
        assert fast.wall_temperature_rise(i, t, q) == pytest.approx(ref.wall_temperature_rise(i, t, q), abs=ATOL)


def test_wall_rise_of_an_earlier_step_is_still_available(g_field):
    g = _kernel("capacity", g_field)
    n, dt = 400, 600.0
    t, ref, fast = _pair(g, n, dt)
    q = _loads(n, seed=5)
    for i in range(n):
        ref.wall_temperature_rise(i, t, q[i])
        fast.wall_temperature_rise(i, t, q[i])
    for i in (0, 1, 63, 64, 255, 399):
        assert fast.borehole_wall_rise(i, t) == pytest.approx(ref.borehole_wall_rise(i, t), abs=ATOL)


def test_pulse_tolerance_and_fixed_step_are_honoured(g_field):
    n, dt = 300, 900.0
    t = np.arange(n) * dt
    ref = AggregateGFunctionCoupler(g_field, pulse_tol=0.5, dt_s=dt)
    fast = FastGFunctionCoupler(g_field, pulse_tol=0.5, dt_s=dt)
    ref.reset(n, t)
    fast.reset(n, t)
    q = 10.0 + 0.3 * np.sin(np.arange(n))  # mostly below the tolerance
    q[100:] += 5.0
    for i in range(n):
        assert fast.wall_temperature_rise(i, t, q[i]) == pytest.approx(ref.wall_temperature_rise(i, t, q[i]), abs=ATOL)


def test_reset_starts_a_clean_run(g_field):
    n, dt = 500, 3600.0
    t, _, fast = _pair(g_field, n, dt)
    q = _loads(n, seed=9)
    first = [fast.wall_temperature_rise(i, t, q[i]) for i in range(n)]
    reset_ground_coupler(fast, n, t, dt)
    assert [fast.wall_temperature_rise(i, t, q[i]) for i in range(n)] == first


def test_calls_it_cannot_answer_exactly_raise(g_field):
    n, dt = 50, 3600.0
    t, _, fast = _pair(g_field, n, dt)
    with pytest.raises(ValueError, match="wall_temperature_rise"):
        fast.borehole_wall_rise(0, t)  # nothing committed yet
    fast.wall_temperature_rise(0, t, 10.0)
    fast.wall_temperature_rise(1, t, 12.0)
    with pytest.raises(ValueError, match="already committed"):
        fast.wall_temperature_rise(1, t, 15.0)
    with pytest.raises(ValueError, match="next step"):
        fast.preview_wall_temperature_rise(0, t, 15.0)
    with pytest.raises(ValueError, match="next step"):
        fast.preview_wall_temperature_rise(7, t, 15.0)
    with pytest.raises(ValueError, match="time grid"):
        fast.wall_temperature_rise(2, t + 1.0, 15.0)
    with pytest.raises(ValueError, match="outside"):
        fast.wall_temperature_rise(n, t, 15.0)
    with pytest.raises(RuntimeError, match="reset"):
        FastGFunctionCoupler(g_field).wall_temperature_rise(0, t, 1.0)


def test_reset_rejects_grids_the_step_mean_response_does_not_cover(g_field):
    fast = FastGFunctionCoupler(g_field)
    with pytest.raises(ValueError, match="uniform"):
        fast.reset(4, np.array([0.0, 3600.0, 7200.0, 12000.0]))
    with pytest.raises(ValueError, match="step length"):
        fast.reset(1, np.array([0.0]))
    with pytest.raises(ValueError, match="every step"):
        fast.reset(10, np.arange(4) * 3600.0, dt_s=3600.0)
    fast.reset(1, np.array([0.0]), dt_s=3600.0)  # a one-step run with its step given
    assert fast.wall_temperature_rise(0, np.array([0.0]), 10.0) > 0.0


def _run(model, n: int, dt: float):
    dhw = np.zeros(n)
    dhw[[2, 3, 9, 10, 11]] = 6.0e-5
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return model.analyze_dynamic(n * dt, dt, 50.0, dhw, np.full(n, 10.0))


def _model() -> GroundSourceHeatPumpBoiler:
    return GroundSourceHeatPumpBoiler(ref="R32", N_1=2, N_2=1, H_b=100.0, dt_s=3600.0, t_max_s=200 * 3600)


@pytest.mark.parametrize("dynamic", [False, True])
def test_model_run_is_unchanged(dynamic):
    cols = ["T_bhe [°C]", "T_bhe_f [°C]", "T_bhe_f_in [°C]", "T_bhe_f_out [°C]", "Q_bhe [W]"]
    n, dt = (14, 3600.0) if not dynamic else (18, 600.0)
    base, fast = _model(), _model()
    if dynamic:
        enable_borehole_dynamics(base)
        enable_borehole_dynamics(fast)
    coupler = enable_fast_superposition(fast)
    assert fast._ground_coupler is coupler
    assert isinstance(coupler, FastGFunctionCoupler)
    assert coupler._g is fast._gfunc_interp
    a = _run(base, n, dt)[cols].to_numpy(dtype=float)
    b = _run(fast, n, dt)[cols].to_numpy(dtype=float)
    assert np.any(a[:, 4] != 0.0)  # the ground was loaded
    np.testing.assert_allclose(b, a, rtol=0.0, atol=1e-9)


def test_enable_keeps_the_coupler_settings_and_refuses_other_models(g_field):
    model = _model()
    model._ground_coupler = AggregateGFunctionCoupler(model._gfunc_interp, pulse_tol=0.25, dt_s=600.0)
    fast = enable_fast_superposition(model)
    assert (fast._pulse_tol, fast._dt_fixed) == (0.25, 600.0)
    assert enable_fast_superposition(model) is fast  # already fast: nothing to do

    class Other:
        def reset(self, n_steps, time_arr): ...

        def wall_temperature_rise(self, n, time_arr, q_unit):
            return 0.0

    model._ground_coupler = Other()
    with pytest.raises(ValueError, match="injected"):
        enable_fast_superposition(model)
    with pytest.raises(TypeError, match="ground coupler"):
        enable_fast_superposition(GroundSourceHeatPump())
