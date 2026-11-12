"""Ground response: g-function horizon, step-mean time convention, R_a, empirical GSHP guards (#96 G3/G4/G6/G8)."""

from __future__ import annotations

import itertools
import math
import warnings

import numpy as np
import pytest

gt = pytest.importorskip("pygfunction")

from tmhp.borehole import (  # noqa: E402
    calc_effective_borehole_thermal_resistance,
    calc_local_borehole_thermal_resistance,
)
from tmhp.cop import GSHP_EQFIT_DOMAIN, EquationFitRangeError, calc_GSHP_COP  # noqa: E402
from tmhp.g_function import GFunction, g_step_mean, precompute_gfunction  # noqa: E402
from tmhp.ground_coupling import AggregateGFunctionCoupler  # noqa: E402
from tmhp.gshp_empirical import GroundSourceHeatPumpEmpirical  # noqa: E402

from ._ground_oracle import step_mean_quad, step_mean_superposition  # noqa: E402

K_S, ALPHA, H, R_B = 2.0, 2.0 / (2000.0 * 800.0), 100.0, 0.08
YEAR = 8760 * 3600.0


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _pyg(times, n_side: int = 1) -> np.ndarray:
    """pygfunction evaluated directly at ``times``, embedded in a dense log grid.

    For a multi-borehole UBWT field pygfunction's answer depends on the time
    vector it is given (a 4-point vector puts g(1 yr) of a 3x3 field 0.8 %
    low), so the targets are solved together with 120 log-spaced points.
    """
    times = np.atleast_1d(np.asarray(times, dtype=float))
    grid = np.unique(np.r_[np.geomspace(3600.0, times.max(), 120), times])
    field = gt.borefield.Borefield.rectangle_field(N_1=n_side, N_2=n_side, B_1=6.0, B_2=6.0, H=H, D=0.0, r_b=R_B)
    g = np.asarray(gt.gfunction.gFunction(field, ALPHA, time=grid, boundary_condition="UBWT").gFunc)
    return g[np.searchsorted(grid, times)] / (2 * np.pi * K_S)


def _single(t_max: float, dt: float = 3600.0) -> GFunction:
    return precompute_gfunction(1, 1, 6.0, H, 0.0, R_B, ALPHA, K_S, t_max, dt)


# ---------------------------------------------------------------------------
# G3: no extrapolation past the precomputed horizon
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_side", [1, 3])
def test_gfunction_recomputes_instead_of_extrapolating(n_side):
    g = precompute_gfunction(n_side, n_side, 6.0, H, 0.0, R_B, ALPHA, K_S, YEAR, 3600.0)
    assert g.n_builds == 1 and g.t_end == pytest.approx(1.5 * YEAR)
    times = np.array([2.0, 10.0, 25.0]) * YEAR
    np.testing.assert_allclose(g(times), _pyg(times, n_side), rtol=2e-3)
    assert g.n_builds == 2 and g.t_end >= 1.5 * times[-1]
    # Values inside the old horizon are unchanged by the rebuild (grid density kept).
    inside = np.array([3600.0, 86400.0, 30 * 86400.0, YEAR])
    np.testing.assert_allclose(g(inside), _pyg(inside, n_side), rtol=1e-3)


def test_gfunction_rejects_non_finite_time():
    g = _single(86400.0)
    with pytest.raises(ValueError):
        g(np.array([np.inf]))


# ---------------------------------------------------------------------------
# G4: one time convention — the mean over [t_n, t_n + dt]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("tau", "dt"), [(0.0, 60.0), (0.0, 3600.0), (3000.0, 1200.0), (7 * 86400.0, 600.0)])
def test_step_mean_is_exact_for_the_interpolated_g(tau, dt):
    g = _single(30 * 86400.0)
    assert float(g.step_mean(tau, dt)) == pytest.approx(step_mean_quad(g, tau, dt), rel=1e-11)


def test_step_mean_past_horizon_extends_grid():
    g = _single(86400.0)
    value = float(g.step_mean(20 * YEAR, 3600.0))
    assert g.t_end >= 20 * YEAR + 3600.0
    assert value == pytest.approx(float(_pyg(20 * YEAR + 1800.0)[0]), rel=2e-3)


def test_generic_callable_step_mean_uses_quadrature():
    def g(t):
        return 0.01 * np.log1p(np.asarray(t) / 600.0)

    got = g_step_mean(g, np.array([0.0, 500.0]), 600.0)
    ref = [step_mean_quad(g, 0.0, 600.0), step_mean_quad(g, 500.0, 600.0)]
    np.testing.assert_allclose(got, ref, rtol=1e-8)


def test_hourly_means_do_not_depend_on_the_time_step():
    """For an hourly load history, dt = 60 / 600 / 3600 s give the same hourly-mean wall."""
    hours = 48
    load = np.where(np.arange(hours) % 24 < 8, 25.0, np.where(np.arange(hours) % 24 < 12, 0.0, -10.0))
    hourly = {}
    for dt in (60.0, 600.0, 3600.0):
        per = int(3600 / dt)
        time = np.arange(hours * per) * dt
        coupler = AggregateGFunctionCoupler(_single(3 * 86400.0, dt))
        coupler.reset(len(time), time)
        rise = np.array([coupler.wall_temperature_rise(n, time, float(load[n // per])) for n in range(len(time))])
        hourly[dt] = rise.reshape(hours, per).mean(axis=1)
    np.testing.assert_allclose(hourly[60.0], hourly[3600.0], rtol=0, atol=1e-10)
    np.testing.assert_allclose(hourly[600.0], hourly[3600.0], rtol=0, atol=1e-10)


def test_constant_load_matches_finite_line_source_over_years():
    """25 W/m extraction for 3 years with a 30-day t_max: every reported step is the FLS interval mean."""
    dt, q = 3600.0, 25.0
    n = int(3 * YEAR / dt)
    time = np.arange(n) * dt
    coupler = AggregateGFunctionCoupler(_single(30 * 86400.0))
    coupler.reset(n, time)
    rows = {24: None, 30 * 24: None, 8760: None, n - 1: None}
    for i in range(n):
        rise = coupler.wall_temperature_rise(i, time, q)
        if i in rows:
            rows[i] = rise
    nodes, weights = np.polynomial.legendre.leggauss(8)
    for i, rise in rows.items():
        t0 = time[i]
        ref = q * 0.5 * float(np.dot(weights, _pyg(t0 + 0.5 * dt * (nodes + 1.0))))
        assert rise == pytest.approx(ref, abs=0.01), (i, rise, ref)


def test_first_step_sees_its_own_load():
    g = _single(86400.0)
    coupler = AggregateGFunctionCoupler(g)
    time = np.arange(3) * 3600.0
    coupler.reset(3, time)
    assert coupler.preview_wall_temperature_rise(0, time, 25.0) == pytest.approx(25.0 * float(g.step_mean(0, 3600)))
    assert coupler.wall_temperature_rise(0, time, 25.0) == pytest.approx(25.0 * 0.5 * float(g(3600.0)), rel=1e-12)


def test_coupler_requires_a_step_length_for_one_step_grid():
    coupler = AggregateGFunctionCoupler(lambda t: np.zeros_like(np.asarray(t, dtype=float)))
    with pytest.raises(ValueError, match="step length"):
        coupler.reset(1, np.array([0.0]))
    AggregateGFunctionCoupler(lambda t: np.asarray(t) * 0.0, dt_s=600.0).reset(1, np.array([0.0]))


def test_gshp_inline_superposition_uses_the_coupler_convention():
    from tmhp import GroundSourceHeatPump

    model = GroundSourceHeatPump(R_b=0.2, t_max_s=86400.0)
    time = np.arange(12) * 3600.0
    loads = np.array([0, 3000, 3000, 0, 0, 2500, 2500, 2500, 0, -1500, -1500, 0], dtype=float)
    model.dt = 3600.0
    pulses = np.zeros(len(time))
    q_old = 0.0
    walls = []
    for n, load in enumerate(loads):
        q_old = model._compute_bhe_superposition(
            n, time, pulses, q_old, {"Q_bhe [W]": load, "dV_bhe_f [m3/s]": model.dV_b_f_m3s}, load != 0
        )
        walls.append(model.T_bhe)
    q_unit = loads / model.total_borehole_length
    coupler = AggregateGFunctionCoupler(model._gfunc_interp)
    coupler.reset(len(time), time)
    via_coupler = [model.Ts - coupler.wall_temperature_rise(n, time, float(q)) for n, q in enumerate(q_unit)]
    np.testing.assert_allclose(walls, via_coupler, rtol=0, atol=1e-12)
    oracle = model.Ts - step_mean_superposition(model._gfunc_interp, time, np.diff(np.r_[0.0, q_unit]), 3600.0)
    np.testing.assert_allclose(walls, oracle, rtol=0, atol=1e-12)


def test_empirical_gshp_uses_the_same_convention():
    m = GroundSourceHeatPumpEmpirical(sim_hours=24, T_g=12.0)
    walls = []
    for n in range(8):
        m.Q_r_iu, m.T0 = (-6000.0 if n < 4 else 0.0), 0.0
        m.system_update()
        assert m.failure_reason == "none"
        walls.append(m.T_b)
    q = np.array(m.q_b_history[1:])  # injection-positive [W/m]
    time = np.arange(len(q)) * 3600.0
    oracle = m.T_g_K + step_mean_superposition(m.g_func_interp, time, np.diff(np.r_[0.0, q]), 3600.0)
    np.testing.assert_allclose(walls, oracle, rtol=0, atol=1e-10)


# ---------------------------------------------------------------------------
# G6: internal resistance R_a from the public multipole matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("D_s", "k_g"), [(0.025, 1.5), (0.04, 2.5), (0.055, 1.5)])
def test_internal_resistance_is_positive_and_matches_delta_circuit(D_s, k_g):
    rho, mu, cp, k_f = 1000.0, 1.0e-3, 4186.0, 0.606
    m_flow = 8.0 / 60000.0 * rho
    geometry = dict(k_s=2.0, k_g=k_g, k_p=0.4, r_b=0.08, r_out=0.016, r_in=0.013, D_s=D_s)
    R_b, R_a = calc_local_borehole_thermal_resistance(
        m_flow_pipe=m_flow, rho_f=rho, mu_f=mu, cp_f=cp, k_f=k_f, **geometry
    )
    h_f = gt.pipes.convective_heat_transfer_coefficient_circular_pipe(m_flow, 0.013, mu, rho, k_f, cp, 1e-6)
    R_fp = gt.pipes.conduction_thermal_resistance_circular_pipe(0.013, 0.016, 0.4) + 1 / (2 * np.pi * 0.013 * h_f)
    _, R_delta = gt.pipes.thermal_resistances([(-D_s, 0.0), (D_s, 0.0)], 0.016, 0.08, 2.0, k_g, R_fp, J=2)
    assert R_a > 0
    # Symmetric U-tube: 1/R_a = 1/R_12^Δ + 1/(4 R_b), whatever the sign of R_12^Δ.
    assert 1 / R_a == pytest.approx(1 / R_delta[0, 1] + 1 / (4 * R_b), rel=1e-9)
    ubwt = calc_effective_borehole_thermal_resistance(R_b, R_a, 100.0, m_flow, cp, "uniform_temperature")
    uhf = calc_effective_borehole_thermal_resistance(R_b, R_a, 100.0, m_flow, cp, "uniform_heat_flux")
    assert R_b < ubwt <= uhf


def test_effective_resistance_rejects_non_positive_internal_resistance():
    with pytest.raises(ValueError):
        calc_effective_borehole_thermal_resistance(0.15, -2.0, 100.0, 0.1, 4186.0, "uniform_heat_flux")


# ---------------------------------------------------------------------------
# G8: empirical GSHP stays inside its curve fit
# ---------------------------------------------------------------------------


def _empirical(Q: float, T_g: float, steps: int = 1, T_a_room: float | None = None, **kw):
    m = GroundSourceHeatPumpEmpirical(sim_hours=24, T_g=T_g, **kw)
    for _ in range(steps):
        m.Q_r_iu, m.T0, m.T_a_room = Q, 20.0, T_a_room
        m.system_update()
    return m


def test_calc_gshp_cop_refuses_points_outside_the_fit():
    room = 273.15 + 21.0
    with pytest.raises(EquationFitRangeError):
        calc_GSHP_COP(room, 273.15 - 5.0, 1.0, "heating")  # EWT below -1.25 °C
    with pytest.raises(EquationFitRangeError):
        calc_GSHP_COP(room, 273.15 + 10.0, 0.2, "heating")  # a part-load ratio is not an air-flow ratio
    with pytest.raises(EquationFitRangeError):
        calc_GSHP_COP(273.15 + 27.0, 273.15 + 0.0, 1.0, "cooling")  # cooling EWT below 9.85 °C
    lo, hi = GSHP_EQFIT_DOMAIN["heating"]["x"]
    assert calc_GSHP_COP(room, 0.5 * (lo + hi) * 283.0, 1.0, "heating") > 0


@pytest.mark.parametrize(
    ("Q", "T_g"),
    # (40 kW, 38 °C) starts inside the cooling range and settles at EWT 55 °C, above 48.72 °C.
    [(100.0, -5.0), (2000.0, 0.0), (40000.0, 38.0), (-16450.0, -3.0), (-8000.0, 0.0)],
)
def test_out_of_domain_steps_do_not_run(Q, T_g):
    m = _empirical(Q, T_g)
    assert m.failure_reason == "outside_curve_domain"
    assert m.failure_detail
    assert m.E_cmp == 0.0 and m.Q_iu == 0.0 and math.isnan(m.COP)
    assert m.q_b == 0.0 and m.T_f_out == pytest.approx(m.T_b)


@pytest.mark.parametrize(("Q", "T_g"), [(6000.0, 5.0), (6000.0, 8.0), (-6000.0, 33.0), (-6000.0, 34.0)])
def test_valid_point_reached_from_out_of_range_ground_is_not_rejected(Q, T_g):
    """Only the converged entering water decides the domain, not the undisturbed-ground first guess."""
    m = _empirical(Q, T_g)
    lo, hi = (v * 283.0 - 273.15 for v in GSHP_EQFIT_DOMAIN["cooling" if Q > 0 else "heating"]["x"])
    assert not lo <= T_g <= hi
    assert m.failure_reason == "none", m.failure_detail
    assert lo <= m.T_f_out - 273.15 <= hi
    assert m.Q_iu == Q and m.COP > 0.0
    # Self-consistent: the COP is the curve value at the reported entering water.
    curve_cop = calc_GSHP_COP(m.T_a_room_K, m.T_f_out, 1.0, "cooling" if Q > 0 else "heating")
    assert pytest.approx(curve_cop, rel=2e-3) == m.COP


def test_converged_entering_water_outside_the_fit_still_stops_the_unit():
    # 3 kW heating from 34 °C ground settles at EWT 32.3 °C, above the 32.06 °C fit limit.
    m = _empirical(-3000.0, 34.0)
    assert m.failure_reason == "outside_curve_domain" and "x=" in m.failure_detail
    assert m.E_cmp == 0.0 and math.isnan(m.COP)


def test_room_outside_the_fit_reports_nan_cop():
    m = _empirical(-8000.0, 10.0, T_a_room=30.0)
    assert m.failure_reason == "outside_curve_domain" and "w=" in m.failure_detail
    assert math.isnan(m.COP) and math.isnan(m.COP_sys) and m.E_cmp == 0.0


def test_request_above_capacity_is_clamped():
    m = _empirical(40000.0, 15.0)
    assert m.failure_reason == "none" and m.capacity_clamped == "max"
    assert m.Q_iu == pytest.approx(m.Q_cap) and m.Q_cap < 40000.0
    assert m.E_cmp == pytest.approx(m.Q_iu / m.COP)
    small = _empirical(5000.0, 15.0)
    assert small.capacity_clamped is None and small.Q_iu == 5000.0


def test_room_temperature_is_an_input():
    cool = _empirical(-8000.0, 10.0, T_a_room=18.0)
    warm = _empirical(-8000.0, 10.0, T_a_room=24.0)
    assert cool.failure_reason == warm.failure_reason == "none"
    assert cool.T_a_iu_in == 18.0 and warm.T_a_iu_in == 24.0
    assert cool.COP > warm.COP  # lower condensing temperature in heating
    assert _empirical(-8000.0, 10.0, T_a_room=30.0).failure_reason == "outside_curve_domain"
    assert _empirical(-8000.0, 10.0).T_a_iu_in == 21.0  # default when not given


def test_non_finite_inputs_raise():
    m = GroundSourceHeatPumpEmpirical(sim_hours=4)
    m.Q_r_iu, m.T0 = float("nan"), 0.0
    with pytest.raises(ValueError):
        m.system_update()


def test_no_negative_power_or_cop_on_a_grid():
    for T_g, Q, room in itertools.product(
        (-10.0, 0.0, 10.0, 20.0, 35.0), (-40000.0, -3000.0, 3000.0, 40000.0), (None, 12.0, 26.0)
    ):
        m = _empirical(Q, T_g, steps=2, T_a_room=room)
        assert m.E_cmp >= 0.0
        assert math.isnan(m.COP) or m.COP > 0.0
        assert math.isnan(m.COP_sys) or m.COP_sys >= 0.0
        if m.failure_reason == "none":
            assert m.Q_iu != 0.0 and m.COP > 0.0
