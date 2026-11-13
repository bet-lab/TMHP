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
        self.widest = 1.0  # largest last/first time ratio of one call

    def response_matrix(self, field, soil, times):
        self.calls += 1
        self.widest = max(self.widest, float(times[-1] / times[0]))
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
    calls = provider.calls
    g.step_mean(far, 3600.0)
    assert g.n_builds == 2 and provider.calls > calls and g.t_end >= far
    # It drives the default coupler like any g-function.
    coupler = AggregateGFunctionCoupler(g)
    time_arr = np.arange(48) * 3600.0
    coupler.reset(48, time_arr, dt_s=3600.0)
    rise = [coupler.wall_temperature_rise(i, time_arr, 20.0) for i in range(48)]
    assert rise[0] == pytest.approx(20.0 * float(g.step_mean(0.0, 3600.0)))
    assert np.all(np.diff(rise) > 0)


class _SpanSensitive(_FiniteLineSource):
    """A provider whose accuracy degrades with the time span of one call (as geolink's ``MFLSResponse``)."""

    def response_matrix(self, field, soil, times):
        exact = super().response_matrix(field, soil, times)
        return exact * (1.0 + 1e-3 * np.log10(times[-1] / times[0]))


def test_provider_is_called_one_decade_at_a_time():
    depths = [150.0] * 4
    t_max = 20 * 8760 * 3600.0
    provider = _SpanSensitive()
    g = ResponseMatrixGFunction(provider, _field(depths), (K_S, ALPHA_S), depths, t_max)
    decades = np.log10(g.t_end / g.t_min)
    assert provider.calls == int(np.ceil(decades)) and provider.widest <= 10.0
    # Every node was computed, in order, and none twice.
    assert len(g.x) == int(np.ceil(24 * decades)) + 2 and np.all(np.diff(g.x) > 0)
    exact = ResponseMatrixGFunction(_FiniteLineSource(), _field(depths), (K_S, ALPHA_S), depths, t_max)
    np.testing.assert_array_equal(g.x, exact.x)
    # One call for the whole grid would be 5.4e-3 off with this provider; a decade is at most 1e-3.
    np.testing.assert_allclose(g.y[1:], exact.y[1:], rtol=1.0e-3)
    # A horizon shorter than one decade is still one call.
    short = _SpanSensitive()
    ResponseMatrixGFunction(short, _field(depths), (K_S, ALPHA_S), depths, 4 * 3600.0)
    assert short.calls == 1


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

    class _Changing(_FiniteLineSource):
        def response_matrix(self, field, soil, times):
            return super().response_matrix(field[: 4 - min(self.calls, 1)], soil, times)

    with pytest.raises(ValueError, match="boreholes after"):
        ResponseMatrixGFunction(_Changing(), _field(depths), (K_S, ALPHA_S), depths, 1e8)
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


def test_geolink_network_coupler_follows_the_host_contract():
    """With geolink installed: its own coupler gives the rise of the ``'UHTR'`` g-function.

    The module docstring of ``tmhp.integrations.geolink`` states this for
    geolink main ``5cff1aa1`` or later; earlier couplers returned ``1/N`` of
    the rise and nothing for the first step.
    """
    coupling = pytest.importorskip("geolink.coupling")
    from tmhp.g_function import precompute_gfunction
    from tmhp.ground_coupling import GroundCoupler, reset_ground_coupler
    from tmhp.ground_loop import calc_borefield_linear_load

    depth, burial, spacing, n_steps, dt = 200.0, 4.0, 6.0, 365, 86400.0
    time_arr = np.arange(n_steps) * dt
    for n_1, n_2 in ((1, 1), (2, 2)):
        count = n_1 * n_2
        backend = coupling.NetworkGroundCoupler.from_rectangular(
            N_1=n_1, N_2=n_2, B=spacing, H_b=depth, D_b=burial, r_b=R_B, k_s=K_S, alpha_s=ALPHA_S
        )
        assert isinstance(backend, GroundCoupler)
        assert backend.total_length == pytest.approx(count * depth)  # what the model divides the load by
        reference = AggregateGFunctionCoupler(
            precompute_gfunction(
                n_1, n_2, spacing, depth, burial, R_B, ALPHA_S, K_S, n_steps * dt, dt, boundary_condition="UHTR"
            )
        )
        q_unit = calc_borefield_linear_load(20.0 * count * depth, count, depth)
        rises = []
        for coupler in (backend, reference):
            reset_ground_coupler(coupler, n_steps, time_arr, dt)
            assert coupler.preview_wall_temperature_rise(0, time_arr, q_unit) > 0.0  # the step answers its own load
            rises.append(np.array([coupler.wall_temperature_rise(k, time_arr, q_unit) for k in range(n_steps)]))
        np.testing.assert_allclose(rises[0], rises[1], rtol=1e-3)


def test_geolink_groundwater_response_does_not_depend_on_the_horizon():
    """With geolink installed: ``MFLSResponse`` is asked one decade at a time (bet-lab/geolink#581).

    Its quadrature follows the time span of a call. Asked for a whole
    20-year table at once, the 5 x 5 field below is 3.1e-4 m.K/W off calls
    for one time each (2.8e-5 with decade calls), and ``g`` at one year
    changes by 3.7e-5 m.K/W when the table is built for 100 years instead of
    2 (1e-6 with decade calls).
    """
    mfls = pytest.importorskip("geolink.response.mfls")
    base = pytest.importorskip("geolink.response.base")
    xs, ys = np.meshgrid(np.arange(5) * 6.0, np.arange(5) * 6.0, indexing="ij")
    kwargs = dict(r_b=R_B, k_s=K_S, alpha_s=ALPHA_S, D=1.0, groundwater_velocity=1e-6, rho_s=2000.0, c_s=800.0)
    year = 8760 * 3600.0
    g = geolink_gfunction(xs.ravel(), ys.ravel(), 150.0, t_max_s=20 * year, **kwargs)
    model = mfls.MFLSResponse(v_gw=1e-6, theta_gw=0.0, alpha_L=0.0, rho_s=2000.0, c_s=800.0)
    field = base.BoreField(x=xs.ravel(), y=ys.ravel(), depth=150.0, burial=1.0, radius=R_B)
    soil = base.Soil(conductivity=K_S, diffusivity=ALPHA_S)
    nodes = g.x[1::8]
    single = np.array([model.response_matrix(field, soil, np.array([t])).sum(axis=1).mean() for t in nodes])
    np.testing.assert_allclose(g.y[1::8], single, rtol=0, atol=6e-5)
    short = geolink_gfunction(xs.ravel(), ys.ravel(), 150.0, t_max_s=2 * year, **kwargs)
    long = geolink_gfunction(xs.ravel(), ys.ravel(), 150.0, t_max_s=100 * year, **kwargs)
    assert float(short(year)) == pytest.approx(float(long(year)), abs=5e-6)
