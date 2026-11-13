"""The dynamic borehole response as the default of the ground-source models (end to end)."""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from tests._borehole_oracle import cycled_loop_outlet
from tests._ground_oracle import step_mean_quad, step_mean_superposition
from tmhp import GroundSourceHeatPump, GroundSourceHeatPumpBoiler, GSHPB_STC_ground
from tmhp.borehole_capacity import DynamicGFunction, _transit_section, enable_borehole_dynamics
from tmhp.constants import c_w, rho_w
from tmhp.g_function import GFunction
from tmhp.ground_coupling import AggregateGFunctionCoupler, reset_ground_coupler, run_window_preview
from tmhp.ground_superposition import FastGFunctionCoupler
from tmhp.subsystems import SolarThermalCollector


def _boiler(**kwargs) -> GroundSourceHeatPumpBoiler:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return GroundSourceHeatPumpBoiler(**kwargs)


def _space(**kwargs) -> GroundSourceHeatPump:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return GroundSourceHeatPump(**kwargs)


def _run(model: GroundSourceHeatPumpBoiler, hours: float, dt: float, draw_lpm: float = 4.0, T_init: float = 55.0):
    n = int(hours * 3600 / dt)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return model.analyze_dynamic(n * dt, dt, T_init, np.full(n, draw_lpm / 60000.0), np.full(n, 5.0))


class _Wall:
    """Physical wall response of a dynamic g-function, as a callable with its grid nodes (for quadrature)."""

    def __init__(self, g: DynamicGFunction) -> None:
        self._g, self.x = g, g.x

    def __call__(self, t):
        return self._g.wall(t)


# ---------------------------------------------------------------------------
# The constructor argument
# ---------------------------------------------------------------------------


def test_default_is_the_dynamic_borehole_derived_from_the_model():
    model = _boiler(H_b=150.0, r_b=0.07, k_g=2.0)
    dynamic = model._gfunc_interp
    assert model.borehole_dynamics == "transit" and model.borehole_dynamics_note == ""
    assert isinstance(dynamic, DynamicGFunction) and isinstance(dynamic.field, GFunction)
    assert dynamic.section.steady_resistance == pytest.approx(model.R_b, rel=1e-12)
    assert dynamic.section.r_b == 0.07
    assert dynamic.section.rhoc_s == model.rho_s * model.c_s
    assert isinstance(model._ground_coupler, AggregateGFunctionCoupler) and model._ground_coupler._g is dynamic
    assert model._ground_coupler.fluid_referenced
    # Transit through 150 m of U-tube at the model's own loop flow (a few minutes).
    m_cp = model.dV_b_f_m3s * rho_w * c_w
    assert dynamic.transit_time == pytest.approx(dynamic.section.C_f * 150.0 / m_cp, rel=1e-9)
    assert 200.0 < dynamic.transit_time < 600.0
    # enable_borehole_dynamics replaces the response instead of nesting it (other heat capacities, lumped fluid).
    again = enable_borehole_dynamics(model, transit=False, rhoc_g=3.0e6)
    assert again.field is dynamic.field and again.transit_time is None
    assert model.borehole_dynamics == "lumped" and model._gfunc_interp is again
    assert _space().borehole_dynamics == "transit"


def test_argument_is_keyword_only_and_moves_no_positional_argument():
    """Positional calls written before the argument existed keep their meaning."""
    import inspect

    for cls in (GroundSourceHeatPumpBoiler, GroundSourceHeatPump, GSHPB_STC_ground):
        parameter = inspect.signature(cls).parameters.get("borehole_dynamics")
        if parameter is not None:  # the solar variants pass it on through **kwargs
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY and parameter.default is None
    positional = [
        p.name
        for p in inspect.signature(GroundSourceHeatPumpBoiler).parameters.values()
        if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]
    # The tail of the positional list as it was before (main f1394dd).
    assert positional[-8:] == [
        "boundary_condition",
        "ground_coupler",
        "T_sur",
        "dT_hx_min",
        "PR_cycle_min",
        "PR_cycle_max",
        "rps_min",
        "rps_max",
    ]
    assert "borehole_dynamics" in inspect.signature(GroundSourceHeatPump).parameters
    assert GSHPB_STC_ground(stc=SolarThermalCollector(), borehole_dynamics=False).borehole_dynamics == "off"


def test_false_is_the_steady_borehole():
    """``borehole_dynamics=False`` is the model before the dynamic default (values recorded on main f1394dd)."""
    n, dt = 12, 600.0
    # R410A charging to 65 °C without a condensing limit: the default machine
    # when the values were recorded (now R290, and R410A stops at 59.9 °C).
    model = _boiler(ref="R410A", T_cond_max=None, dt_s=dt, t_max_s=4 * 86400.0, borehole_dynamics=False)
    assert model.borehole_dynamics == "off" and isinstance(model._gfunc_interp, GFunction)
    assert not model._ground_coupler.fluid_referenced
    assert run_window_preview(model._ground_coupler) is None
    out = _run(model, n * dt / 3600.0, dt, T_init=44.0)
    wall = [15.92295161211, 15.76971193568, 15.618210858072, 15.468500832582, 15.320641904123, 15.174702317561]
    outlet = [12.63615631429, 12.51947950691, 12.405582179276, 12.29467261992, 12.186978888154, 12.082751014907]
    power = [1901.74730639057, 1967.970262194276, 2036.070470196556, 2106.330147858333, 2179.0535425015]
    np.testing.assert_allclose(out["T_bhe [°C]"].to_numpy()[:6], wall, rtol=0, atol=1e-6)
    np.testing.assert_allclose(out["T_bhe_f_out [°C]"].to_numpy()[:6], outlet, rtol=0, atol=1e-6)
    np.testing.assert_allclose(out["T_bhe_f_out [°C]"].to_numpy()[11], 11.791746328219, rtol=0, atol=1e-6)
    np.testing.assert_allclose(out["E_cmp [W]"].to_numpy()[:5], power, rtol=1e-6)

    space = _space(dt_s=dt, t_max_s=4 * 86400.0, borehole_dynamics=False)
    assert space.borehole_dynamics == "off" and isinstance(space._gfunc_interp, GFunction)
    load = np.where(np.arange(n) % 3 == 0, 0.0, -3000.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = space.analyze_dynamic(n * dt, dt, load, np.zeros(n))
    wall = [16.0, 15.939691030869, 15.819112457477, 15.758842853217, 15.69861256257, 15.578191244667]
    outlet = [16.0, 13.335153975818, 13.216275435851, 15.758842853217, 13.097473360774, 12.978747693752]
    np.testing.assert_allclose(out["T_bhe [°C]"].to_numpy()[:6], wall, rtol=0, atol=1e-6)
    np.testing.assert_allclose(out["T_bhe_f_out [°C]"].to_numpy()[:6], outlet, rtol=0, atol=1e-6)
    np.testing.assert_allclose(out["E_cmp [W]"].to_numpy()[[1, 11]], [593.31909679143, 602.460813615154], rtol=1e-6)


def test_default_falls_back_where_the_model_cannot_use_it():
    """No error and no warning by default; the outcome and its reason are attributes."""
    custom = AggregateGFunctionCoupler(lambda t: 0.0 * np.asarray(t, dtype=float))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        injected = GroundSourceHeatPumpBoiler(ground_coupler=custom)
        variable_rb = GroundSourceHeatPumpBoiler(variable_Rb=True)
        variable_flow = GroundSourceHeatPumpBoiler(ground_flow_control="optimal_power")
        # A supplied resistance below the fluid-to-pipe resistance: no grout layer can give it.
        unmappable = GroundSourceHeatPumpBoiler(R_b=0.01)
        # 2 L/min through 200 m: the loop temperature difference alone exceeds R_b.
        trickle = GroundSourceHeatPumpBoiler(ground_flow_ref_lpm=2.0, R_b=0.2)
        space = GroundSourceHeatPump(variable_Rb=True)
    assert not [w for w in caught if "transit" in str(w.message) or "borehole_dynamics" in str(w.message)]
    assert injected.borehole_dynamics == "off" and "ground_coupler" in injected.borehole_dynamics_note
    assert injected._ground_coupler is custom
    assert variable_rb.borehole_dynamics == "off" and "variable_Rb" in variable_rb.borehole_dynamics_note
    assert isinstance(variable_rb._gfunc_interp, GFunction)
    assert variable_flow.borehole_dynamics == "off" and "variable ground flow" in variable_flow.borehole_dynamics_note
    assert unmappable.borehole_dynamics == "off" and "cannot be mapped" in unmappable.borehole_dynamics_note
    assert trickle.borehole_dynamics == "off" and "transit is not resolved" in trickle.borehole_dynamics_note
    assert space.borehole_dynamics == "off" and "variable_Rb" in space.borehole_dynamics_note
    for model in (variable_rb, variable_flow, unmappable, trickle, space):
        assert isinstance(model._gfunc_interp, GFunction)
    for model in (variable_rb, variable_flow, unmappable, trickle):
        assert isinstance(model._ground_coupler, AggregateGFunctionCoupler)
        assert model._ground_coupler._g is model._gfunc_interp and not model._ground_coupler.fluid_referenced


def test_lumped_fluid_is_not_a_default_for_variable_flow():
    """Why the default leaves variable flow on the steady borehole.

    Without the transit the loop temperature difference Q / (2 m c_p) appears
    at once: the fluid entering the heat pump starts above the undisturbed
    ground while heat is being extracted. The steady borehole stays below it.
    """
    dt, n = 600.0, 6
    draw = np.full(n, 4.0 / 60000.0)
    hottest = {}
    for name in ("default", "lumped"):
        model = _boiler(dt_s=dt, t_max_s=4 * 86400.0, ground_flow_control="optimal_power")
        if name == "lumped":
            enable_borehole_dynamics(model, transit=False)
            assert model.borehole_dynamics == "lumped"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = model.analyze_dynamic(n * dt, dt, 44.0, draw, np.full(n, 5.0))  # a cold tank: runs at once
        on = out["hp_is_on"].to_numpy().astype(bool)
        assert on.sum() >= 3 and (out["Q_bhe [W]"].to_numpy()[on] > 0).all()
        hottest[name] = out["T_bhe_f_out_running [°C]"].to_numpy()[on].max() - model.Ts
    assert hottest["default"] < -1.0
    assert hottest["lumped"] > 1.0


def test_true_requires_it():
    custom = AggregateGFunctionCoupler(lambda t: 0.0 * np.asarray(t, dtype=float))
    with pytest.raises(ValueError, match="borehole_dynamics=True.*ground_coupler"):
        _boiler(ground_coupler=custom, borehole_dynamics=True)
    with pytest.raises(ValueError, match="borehole_dynamics=True.*variable_Rb"):
        _boiler(variable_Rb=True, borehole_dynamics=True)
    with pytest.raises(ValueError, match="variable_Rb"):
        _space(variable_Rb=True, borehole_dynamics=True)
    with pytest.raises(ValueError, match="grout resistance is negative"):
        _boiler(R_b=0.01, borehole_dynamics=True)
    with pytest.raises(ValueError, match="borehole_dynamics=True.*variable ground flow"):
        _boiler(ground_flow_control="optimal_power", borehole_dynamics=True)
    with pytest.raises(ValueError, match="borehole_dynamics=True.*transit is not resolved"):
        _boiler(ground_flow_ref_lpm=2.0, R_b=0.2, borehole_dynamics=True)
    assert _boiler(borehole_dynamics=True).borehole_dynamics == "transit"
    with pytest.raises(ValueError, match="None, True or False"):
        _boiler(borehole_dynamics="transit")


def test_enable_rejects_models_it_cannot_serve():
    custom = AggregateGFunctionCoupler(lambda t: 0.0 * np.asarray(t, dtype=float))
    with pytest.raises(ValueError, match="injected ground_coupler"):
        enable_borehole_dynamics(_boiler(ground_coupler=custom))
    with pytest.raises(TypeError, match="does not expose a borehole field"):
        enable_borehole_dynamics(object())
    # One flow, one resistance: the kernel cancels the model's steady terms only for those.
    with pytest.raises(ValueError, match="variable_Rb"):
        enable_borehole_dynamics(_boiler(variable_Rb=True))
    variable = _boiler(ground_flow_control="optimal_power")
    with pytest.raises(ValueError, match="one loop flow"):
        enable_borehole_dynamics(variable)
    assert enable_borehole_dynamics(variable, transit=False).transit_time is None


# ---------------------------------------------------------------------------
# What it changes in a run
# ---------------------------------------------------------------------------


def test_start_up_no_longer_drops_the_source_by_q_rb():
    """10 min on / 20 min off: the steady model swings the outlet by 3 K, the dynamic one by under 1 K."""
    schedule = [(k * 0.5, k * 0.5 + 1 / 6) for k in range(48)]
    kwargs = dict(dt_s=600.0, t_max_s=4 * 86400.0, hp_on_schedule=schedule)
    steady, dynamic = _boiler(**kwargs, borehole_dynamics=False), _boiler(**kwargs)
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


def test_reported_wall_is_the_physical_wall():
    """``T_bhe`` is the borehole wall; the fluid columns are the dynamic response to the reported loads."""
    model = _boiler(dt_s=600.0, t_max_s=4 * 86400.0)
    g = model._gfunc_interp
    out = _run(model, 4, 600.0)
    q_line = out["Q_bhe [W]"].to_numpy() / model.total_borehole_length
    assert q_line.max() > 15.0 and q_line.min() < 0.5 * q_line.max()  # full and partial runs
    pulses = np.diff(np.concatenate(([0.0], q_line)))
    time = np.arange(len(q_line)) * 600.0
    # Independent quadrature of the wall and fluid responses (not their step_mean code).
    wall = model.Ts - step_mean_superposition(_Wall(g), time, pulses, 600.0)
    fluid_referenced = model.Ts - step_mean_superposition(g, time, pulses, 600.0)
    t_bhe = out["T_bhe [°C]"].to_numpy()
    np.testing.assert_allclose(t_bhe, wall, atol=1e-9)
    np.testing.assert_allclose(out["T_bhe_f [°C]"].to_numpy(), fluid_referenced - q_line * model.R_b, atol=1e-9)
    assert model.T_bhe == t_bhe[-1]
    # A wall: it does not rise above the undisturbed ground under extraction (to the 0.005 K
    # accuracy of the tabulated wall response), moves little in a step (the fluid-referenced
    # value jumps by q' R_b, 4 K, at every start and stop) and the heat flows from it to the fluid.
    assert np.all(t_bhe <= model.Ts + 0.005)
    assert np.abs(np.diff(t_bhe)).max() < 0.25
    assert np.abs(np.diff(fluid_referenced)).max() > 2.0
    assert np.all(out["T_bhe_f [°C]"].to_numpy() <= t_bhe + 1e-9)
    on = np.flatnonzero(q_line > 0)
    assert model.Ts - t_bhe[on[0]] < 0.05  # first running step: the heat still comes out of the borehole


def test_space_conditioning_wall_and_fluid():
    model = _space(dt_s=600.0, t_max_s=4 * 86400.0)
    g = model._gfunc_interp
    assert isinstance(g, DynamicGFunction)
    n = 18
    load = np.where((np.arange(n) % 6) < 2, -3000.0, 0.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = model.analyze_dynamic(n * 600.0, 600.0, load, np.zeros(n))
    q_line = out["Q_bhe [W]"].to_numpy() / model.total_borehole_length
    assert q_line.max() > 15.0
    pulses = np.diff(np.concatenate(([0.0], q_line)))
    time = np.arange(n) * 600.0
    wall = model.Ts - step_mean_superposition(_Wall(g), time, pulses, 600.0)
    fluid_referenced = model.Ts - step_mean_superposition(g, time, pulses, 600.0)
    np.testing.assert_allclose(out["T_bhe [°C]"].to_numpy(), wall, atol=1e-9)
    np.testing.assert_allclose(out["T_bhe_f [°C]"].to_numpy(), fluid_referenced - q_line * model.R_b, atol=1e-9)
    assert np.abs(np.diff(out["T_bhe [°C]"].to_numpy())).max() < 0.25
    # Outside a run (another grid) the wall is evaluated directly, with the same value.
    pulses_w = np.zeros(n)
    pulses_w[:] = pulses
    assert model.Ts - model._bhe_wall_rise(5, time.copy(), pulses_w, 0.0) == pytest.approx(wall[5], abs=1e-9)
    steady = _space(dt_s=600.0, t_max_s=4 * 86400.0, borehole_dynamics=False)
    assert steady._bhe_wall_rise(5, time, pulses_w, 1.25) == 1.25  # a plain g-function is the wall


def test_solar_ground_loop_reads_the_physical_wall():
    """The collector inlet of the ground-charging loop is the wall, which does not jump when the compressor starts."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = GSHPB_STC_ground(stc=SolarThermalCollector(A_stc=6.0), dt_s=600.0, t_max_s=4 * 86400.0)
        n = 84  # 14 h: the collector window opens in the morning
        hours = np.arange(n) / 6.0
        sun = np.where(hours >= 8.0, 800.0, 0.0)
        draw = np.where(hours >= 9.0, 6.0, 0.0) / 60000.0
        out = model.analyze_dynamic(n * 600.0, 600.0, 46.0, draw, np.full(n, 15.0), sun, 0.15 * sun)
    assert out["stc_active [-]"].to_numpy().any() and out["hp_is_on"].to_numpy().any()
    inlet = out["T_stc_w_in [°C]"].to_numpy()
    active = out["stc_active [-]"].to_numpy().astype(bool)
    wall_before = np.concatenate(([model.Ts], out["T_bhe [°C]"].to_numpy()[:-1]))
    np.testing.assert_allclose(inlet[active], wall_before[active], atol=1e-9)
    assert np.abs(np.diff(out["T_bhe [°C]"].to_numpy())).max() < 0.3


# ---------------------------------------------------------------------------
# A compressor that runs part of a step
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def boiler_1800():
    return _boiler(dt_s=1800.0, t_max_s=4 * 86400.0)


def _loaded(model: GroundSourceHeatPumpBoiler, dt: float, history_W: list[float]):
    """Put ``model`` in a run of step ``dt`` with the given field loads recorded; returns (time, m_cp)."""
    time = np.arange(len(history_W) + 2) * dt
    model.time, model.dt = time, dt
    reset_ground_coupler(model._ground_coupler, len(time), time, dt)
    model._run_window = run_window_preview(model._ground_coupler)
    for i, load in enumerate(history_W):
        model._ground_coupler.wall_temperature_rise(i, time, load / model.total_borehole_length)
    return time, c_w * rho_w * model.dV_b_f_m3s


@pytest.mark.parametrize("coupler_type", [AggregateGFunctionCoupler, FastGFunctionCoupler])
@pytest.mark.parametrize("dynamic", [True, False])
def test_run_window_preview_is_the_window_mean_of_the_response(boiler_1800, coupler_type, dynamic):
    """``preview(n, t, q, window_s=w)``: Σ p_i ḡ(τ_i; w) + (q − q_old) ḡ(0; w), by quadrature over every pulse."""
    g = boiler_1800._gfunc_interp if dynamic else boiler_1800._gfunc_interp.field
    dt = 600.0
    rng = np.random.default_rng(5)
    # 5 h of history lies inside the exact tier (6 h); 16 h also uses the linear tier beyond it,
    # whose error is a few 1e-4 K for these loads (see _window_shift).
    for n, tol in ((30, 1e-9), (96, 1e-3)):
        time = np.arange(n + 1) * dt
        loads = np.repeat(np.where(rng.random(n // 2) < 0.6, rng.uniform(10.0, 40.0, n // 2), 0.0), 2)
        coupler = coupler_type(g)
        reset_ground_coupler(coupler, n + 1, time, dt)
        for i in range(n):
            coupler.wall_temperature_rise(i, time, float(loads[i]))
        pulses = np.diff(np.concatenate(([0.0], loads)))
        q_old, q = float(loads[-1]), 33.0
        for window in (60.0, 180.0, 420.0):
            expected = sum(p * step_mean_quad(g, (n - i) * dt, window) for i, p in enumerate(pulses) if p != 0.0)
            expected += (q - q_old) * step_mean_quad(g, 0.0, window)
            got = coupler.preview_wall_temperature_rise(n, time, q, window_s=window)
            assert got == pytest.approx(expected, abs=tol), (n, window)
            # A grid that is not the run's takes the direct path: every pulse integrated exactly.
            longer = np.append(time, time[-1] + dt)
            direct = coupler.preview_wall_temperature_rise(n, longer, q, window_s=window)
            assert direct == pytest.approx(expected, abs=1e-9), (n, window)
        # The whole step is the plain preview, a vanishing window the rise at t_n under the load before.
        whole = coupler.preview_wall_temperature_rise(n, time, q)
        assert coupler.preview_wall_temperature_rise(n, time, q, window_s=dt) == whole
        almost = coupler.preview_wall_temperature_rise(n, time, q, window_s=dt * (1 - 1e-9))
        assert almost == pytest.approx(whole, abs=max(tol, 1e-6))
        at_start = sum(p * float(g((n - i) * dt)) for i, p in enumerate(pulses)) + (q - q_old) * float(g(0.0))
        assert coupler.preview_wall_temperature_rise(n, time, q, window_s=0.0) == pytest.approx(at_start, abs=tol)
        assert coupler.preview_wall_temperature_rise(n, time, q, window_s=1e-3) == pytest.approx(at_start, abs=2e-3)
        # Nothing was recorded.
        assert coupler._q_old == q_old and coupler._pulses[n] == 0.0


def test_only_a_fluid_referenced_response_gets_the_run_window(boiler_1800):
    g = boiler_1800._gfunc_interp
    assert run_window_preview(AggregateGFunctionCoupler(g)) is not None
    assert run_window_preview(AggregateGFunctionCoupler(g.field)) is None  # plain g-function

    class Legacy:  # an external backend that does not know window_s
        fluid_referenced = True

        def preview_wall_temperature_rise(self, n, time_arr, q_unit):
            return 0.0

    class NoPreview:
        fluid_referenced = True

    assert run_window_preview(Legacy()) is None and run_window_preview(NoPreview()) is None


def test_running_outlet_is_continuous_in_the_run_fraction(boiler_1800):
    """From rest, and after two full steps of extraction: f -> 0 is the fluid as it was, f = 1 the whole step."""
    model = boiler_1800
    dt, Q = 1800.0, 5000.0
    g = model._gfunc_interp
    q = Q / model.total_borehole_length
    for history in ([0.0, 0.0], [Q, Q], [Q, 0.0]):
        time, m_cp = _loaded(model, dt, history)
        n = len(history)
        steady_drop = q * (model.R_b - g.half_loop)
        assert steady_drop > 2.5
        fractions = np.array([1e-9, 0.02, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.98, 1.0])
        outlet = np.array([model._running_source_K(n, Q, f, model.R_b, m_cp) for f in fractions])
        # f -> 0: the outlet at t_n with the previous load still on (no jump at the start).
        q_prev = history[-1] / model.total_borehole_length
        pulses = np.diff(np.concatenate(([0.0], history))) / model.total_borehole_length
        at_start = model.Ts_K - sum(p * float(g((n - i) * dt)) for i, p in enumerate(pulses))
        at_start -= q_prev * (model.R_b - g.half_loop)
        assert outlet[0] == pytest.approx(at_start, abs=1e-6)
        # f = 1: the whole-step value the model used before.
        whole = model.Ts_K - model._ground_coupler.preview_wall_temperature_rise(n, time, q) - steady_drop
        assert outlet[-1] == pytest.approx(whole, abs=1e-12)
        # In between: no jump anywhere (the steady model drops by q'(R_b - H/2mc_p) at once).
        assert np.abs(np.diff(outlet)).max() < 0.12 * steady_drop
        if history[-1] == 0.0:
            assert outlet[0] - outlet[4] < 0.1 * steady_drop  # f = 0.3: not 70 % of the steady drop
        if not any(history):
            assert np.all(np.diff(outlet) <= 1e-4)  # from rest: more run, colder source


def test_running_outlet_matches_a_time_marched_loop(boiler_1800):
    """Mean outlet over a run that covers part of the first step, against the circulating-loop oracle."""
    model = boiler_1800
    dt, Q = 1800.0, 5000.0
    g = model._gfunc_interp
    q = Q / model.total_borehole_length
    time, m_cp = _loaded(model, dt, [])
    calibrated = _transit_section(g.section, model.H_b, m_cp)
    assert g.transit_time is not None and g.transit_time < 0.3 * dt
    steady_drop = q * (model.R_b - g.half_loop)
    for fraction in (0.3, 0.6, 1.0):
        times, rise, _ = cycled_loop_outlet(calibrated, model.H_b, m_cp, fraction * dt, dt, 1)
        truth = q * rise[times <= fraction * dt + 1e-9].mean()
        drop = model.Ts_K - model._running_source_K(0, Q, fraction, model.R_b, m_cp)
        assert drop == pytest.approx(truth, abs=0.012 * q * model.R_b), fraction
        assert 0.0 < drop < 0.35 * steady_drop
    # The steady borehole puts the whole drop at the first instant, for any run fraction.
    steady = _boiler(dt_s=dt, t_max_s=4 * 86400.0, borehole_dynamics=False)
    _loaded(steady, dt, [])
    assert steady._run_window is None
    assert steady.Ts_K - steady._running_source_K(0, Q, 0.3, steady.R_b, m_cp) > steady_drop


def test_partial_runs_through_the_history_stay_close_to_the_loop(boiler_1800):
    """Every step runs for its first 30 %: the history holds step-average loads, the run sees its own load.

    Before the run window the running fluid started (1 - f) of the steady drop
    too low in every step (0.39 R_b at f = 0.3). What is left is the error of
    recording a front-loaded run as its step average (under 0.1 R_b).
    """
    model = boiler_1800
    g = model._gfunc_interp
    dt, f, cycles = 600.0, 0.3, 8
    m_cp = c_w * rho_w * model.dV_b_f_m3s
    calibrated = _transit_section(g.section, model.H_b, m_cp)
    time = np.arange(cycles) * dt
    times, rise, _ = cycled_loop_outlet(calibrated, model.H_b, m_cp, f * dt, (1 - f) * dt, cycles)
    truth = np.array([rise[(times > a + 1e-9) & (times <= a + f * dt + 1e-9)].mean() for a in time])
    coupler = AggregateGFunctionCoupler(g)
    reset_ground_coupler(coupler, cycles, time, dt)
    offset = g.R_b - g.half_loop
    window, average = [], []
    for i in range(cycles):
        window.append(coupler.preview_wall_temperature_rise(i, time, 1.0, window_s=f * dt) + offset)
        average.append(coupler.preview_wall_temperature_rise(i, time, f) + offset)
        coupler.wall_temperature_rise(i, time, f)
    assert abs(window[0] - truth[0]) < 0.005 * g.R_b  # the first run has no history: exact
    assert np.abs(np.array(window) - truth).max() < 0.1 * g.R_b
    assert np.abs(np.array(average) - truth).min() > 0.3 * g.R_b


def test_thermostat_partial_steps_do_not_jump():
    """A day of DHW draws: where the compressor runs part of a step, its source does not jump."""
    dt, n = 600.0, 144
    hours = np.arange(n) * dt / 3600.0
    draw = np.where((hours >= 7) & (hours < 8), 6.0, 0.0) / 60000.0
    jumps = {}
    for name, dynamics in (("dynamic", None), ("steady", False)):
        model = _boiler(dt_s=dt, t_max_s=4 * 86400.0, borehole_dynamics=dynamics)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = model.analyze_dynamic(n * dt, dt, 55.0, draw, np.full(n, 5.0))
        fraction = out["runtime_fraction [-]"].to_numpy()
        running = out["T_bhe_f_out_running [°C]"].to_numpy()
        mean = out["T_bhe_f_out [°C]"].to_numpy()
        partial = [i for i in np.flatnonzero((fraction > 0) & (fraction < 1)) if i > 0]
        assert len(partial) >= 2
        # Against the fluid just before: the running fluid of a running step, the mean of a resting one.
        before = [running[i - 1] if fraction[i - 1] > 0 else mean[i - 1] for i in partial]
        jumps[name] = np.abs(running[partial] - np.array(before))
        # The cycle was solved against the fluid the row reports.
        on = fraction > 0
        assert np.all(out["T_ref_evap_sat [°C]"].to_numpy()[on] < running[on])
    assert jumps["dynamic"].max() < 0.3
    assert jumps["steady"].max() > 2.0
