"""Response-matrix g-function (tmhp.integrations.geolink)."""

from __future__ import annotations

import numpy as np
import pygfunction as gt
import pytest

from tests._ground_oracle import step_mean_quad
from tmhp.ground_coupling import AggregateGFunctionCoupler
from tmhp.integrations.geolink import ResponseMatrixGFunction, geolink_gfunction

ALPHA_S, K_S, R_B = 1.25e-6, 2.0, 0.08


class _FiniteLineSource:
    """Stand-in response model: whole-borehole finite line sources from pygfunction."""

    def __init__(self) -> None:
        self.calls = 0

    def response_matrix(self, field, soil, times):
        self.calls += 1
        k_s, alpha_s = soil
        boreholes = [gt.boreholes.Borehole(H=h, D=d, r_b=R_B, x=x, y=y) for x, y, h, d in field]
        n = len(boreholes)
        out = np.empty((n, n, len(times)))
        for i, receiver in enumerate(boreholes):
            for j, source in enumerate(boreholes):
                out[i, j] = gt.heat_transfer.finite_line_source(times, alpha_s, source, receiver) / (2.0 * np.pi * k_s)
        return out


def _field(depths):
    xy = [(0.0, 0.0), (6.0, 0.0), (0.0, 6.0), (6.0, 6.0)]
    return [(x, y, h, 1.0) for (x, y), h in zip(xy, depths, strict=True)]


def test_equals_the_uniform_heat_flux_g_function():
    depths = [150.0] * 4
    g = ResponseMatrixGFunction(_FiniteLineSource(), _field(depths), (K_S, ALPHA_S), depths, 20 * 8760 * 3600.0)
    times = np.array([3600.0, 86400.0, 365 * 86400.0, 20 * 365 * 86400.0])
    field = gt.borefield.Borefield.rectangle_field(N_1=2, N_2=2, B_1=6.0, B_2=6.0, H=150.0, D=1.0, r_b=R_B)
    reference = gt.gfunction.gFunction(
        field, ALPHA_S, time=times, boundary_condition="UHTR", options={"disp": False}
    ).gFunc / (2.0 * np.pi * K_S)
    np.testing.assert_allclose(g(times), reference, rtol=2e-3)
    assert g(0.0) == 0.0 and g.n_builds == 1


def test_unequal_lengths_are_length_weighted():
    depths = [100.0, 150.0, 200.0, 150.0]
    provider = _FiniteLineSource()
    g = ResponseMatrixGFunction(provider, _field(depths), (K_S, ALPHA_S), depths, 8760 * 3600.0)
    h = provider.response_matrix(_field(depths), (K_S, ALPHA_S), np.array([86400.0]))[:, :, 0]
    expected = float(np.asarray(depths) @ h.sum(axis=1) / sum(depths))
    assert float(g(86400.0)) == pytest.approx(expected, rel=2e-3)


def test_horizon_extends_and_step_mean_is_exact():
    depths = [150.0] * 4
    provider = _FiniteLineSource()
    g = ResponseMatrixGFunction(provider, _field(depths), (K_S, ALPHA_S), depths, 30 * 86400.0)
    assert float(g.step_mean(7200.0, 3600.0)) == pytest.approx(step_mean_quad(g, 7200.0, 3600.0), rel=1e-10)
    far = 3 * 365 * 86400.0
    assert g.t_end < far
    g.step_mean(far, 3600.0)
    assert g.n_builds == 2 and provider.calls == 2 and g.t_end >= far
    # It drives the default coupler like any g-function.
    coupler = AggregateGFunctionCoupler(g)
    time_arr = np.arange(48) * 3600.0
    coupler.reset(48, time_arr, dt_s=3600.0)
    rise = [coupler.wall_temperature_rise(i, time_arr, 20.0) for i in range(48)]
    assert rise[0] == pytest.approx(20.0 * float(g.step_mean(0.0, 3600.0)))
    assert np.all(np.diff(rise) > 0)


def test_rejects_bad_providers_and_inputs():
    depths = [150.0] * 4
    with pytest.raises(TypeError, match="response_matrix"):
        ResponseMatrixGFunction(object(), None, None, depths, 1e6)
    with pytest.raises(ValueError, match="t_max_s"):
        ResponseMatrixGFunction(_FiniteLineSource(), _field(depths), (K_S, ALPHA_S), depths, 0.0)
    with pytest.raises(ValueError, match="depths"):
        ResponseMatrixGFunction(_FiniteLineSource(), _field(depths), (K_S, ALPHA_S), [150.0, -1.0], 1e6)
    with pytest.raises(ValueError, match="entries"):
        ResponseMatrixGFunction(_FiniteLineSource(), _field(depths), (K_S, ALPHA_S), [150.0, 150.0], 1e6)

    class _Wrong:
        def response_matrix(self, field, soil, times):
            return np.zeros((2, 3, len(times)))

    with pytest.raises(ValueError, match="shape"):
        ResponseMatrixGFunction(_Wrong(), None, None, [150.0], 1e6)
    g = ResponseMatrixGFunction(_FiniteLineSource(), _field(depths), (K_S, ALPHA_S), depths, 1e6)
    with pytest.raises(ValueError, match="non-finite"):
        g.ensure_horizon(np.inf)
    with pytest.raises(ValueError, match="dt must be positive"):
        g.step_mean(0.0, -1.0)


def test_geolink_gfunction_against_pygfunction():
    """With geolink installed: its native finite line source gives the UHTR g-function."""
    pytest.importorskip("geolink")
    x, y = np.array([0.0, 6.0, 0.0, 6.0]), np.array([0.0, 0.0, 6.0, 6.0])
    g = geolink_gfunction(x, y, 150.0, r_b=R_B, k_s=K_S, alpha_s=ALPHA_S, t_max_s=20 * 8760 * 3600.0, D=1.0)
    times = np.array([3600.0, 86400.0, 365 * 86400.0, 20 * 365 * 86400.0])
    field = gt.borefield.Borefield.rectangle_field(N_1=2, N_2=2, B_1=6.0, B_2=6.0, H=150.0, D=1.0, r_b=R_B)
    reference = gt.gfunction.gFunction(
        field, ALPHA_S, time=times, boundary_condition="UHTR", options={"disp": False}
    ).gFunc / (2.0 * np.pi * K_S)
    np.testing.assert_allclose(g(times), reference, rtol=2e-3)
    # Groundwater carries heat away: the long-time response is lower, the first hour is not.
    wet = geolink_gfunction(
        x, y, 150.0, r_b=R_B, k_s=K_S, alpha_s=ALPHA_S, t_max_s=20 * 8760 * 3600.0, D=1.0,
        groundwater_velocity=1e-6, rho_s=2000.0, c_s=800.0,
    )  # fmt: skip
    assert float(wet(times[-1])) < 0.6 * float(g(times[-1]))
    assert float(wet(times[0])) == pytest.approx(float(g(times[0])), rel=0.02)
    with pytest.raises(ValueError, match="rho_s and c_s"):
        geolink_gfunction(x, y, 150.0, r_b=R_B, k_s=K_S, alpha_s=ALPHA_S, t_max_s=1e7, groundwater_velocity=1e-6)
