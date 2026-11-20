"""Finite line source response accuracy, query ordering, and scalar caching."""

import numpy as np
import pytest

from tmhp.g_function import G_FLS


@pytest.mark.parametrize("diffusivities", [(0.6e-6, 1.4e-6), (1.4e-6, 0.6e-6)], ids=["low-first", "high-first"])
def test_fls_scalar_matches_array_for_each_diffusivity(diffusivities: tuple[float, float]) -> None:
    """Nearby diffusivities have distinct responses in either scalar call order."""
    expected = [G_FLS(np.array([3600.0]), 2.0, diffusivity, 0.075, 100.0) for diffusivity in diffusivities]
    actual = [G_FLS(3600.0, 2.0, diffusivity, 0.075, 100.0) for diffusivity in diffusivities]

    for scalar, array in zip(actual, expected, strict=True):
        assert isinstance(scalar, float)
        assert isinstance(array, np.ndarray)
        assert array.shape == (1,)
        assert scalar == pytest.approx(array[0], rel=1e-10)

    assert abs(actual[0] - actual[1]) > 0.01


def test_fls_preserves_unordered_and_repeated_time_queries():
    """A batch may revisit an earlier time and must return results in request order."""
    args = (2.0, 1e-6, 0.075, 100.0)
    times = np.array([7200.0, 3600.0, 5400.0, 3600.0])
    expected = G_FLS(np.array([3600.0, 5400.0, 7200.0]), *args)
    np.testing.assert_array_equal(G_FLS(times, *args), expected[[2, 0, 1, 0]])


def test_fls_empty_query_returns_empty_array():
    """An empty batch has no response values."""
    result = G_FLS(np.array([]), 2.0, 1e-6, 0.075, 100.0)
    assert result.shape == (0,)


@pytest.mark.parametrize(
    "order",
    [[0, 1, 2, 3], [3, 2, 1, 0], [2, 0, 3, 1]],
    ids=["increasing", "decreasing", "mixed"],
)
def test_fls_resolves_short_time_tails_in_any_order(order):
    """An early-time query must neither cancel its own tail nor suppress later responses."""
    times = np.array([10.0, 60.0, 3600.0, 86400.0])
    # Independent adaptive integration of the real and image point sources over borehole depth.
    expected = np.array([2.3766043667745e-65, 1.0803064793055e-13, 0.028561398617896, 0.141217931650837])
    args = (2.0, 1e-6, 0.075, 100.0)
    np.testing.assert_allclose(G_FLS(times[order].reshape(2, 2), *args), expected[order], rtol=1e-8, atol=0)
    for time, response in zip(times, expected, strict=True):
        assert G_FLS(np.array(time), *args) == pytest.approx(response, rel=1e-8, abs=0)


@pytest.mark.parametrize("ks,alpha,rb,height", [(0.8, 3e-7, 0.045, 200.0), (3.5, 2e-6, 0.20, 8.0)])
def test_fls_matches_independent_borehole_response_across_diffusion_times(ks, alpha, rb, height):
    """Shuffled times span thermal diffusion over both the radius and the full borehole length."""
    import pygfunction as gt

    times = np.geomspace(0.04 * rb**2 / alpha, 20 * height**2 / alpha, 17)
    times = np.random.default_rng(724).permutation(times)
    borehole = gt.boreholes.Borehole(H=height, D=0.0, r_b=rb, x=0.0, y=0.0)
    expected = [gt.heat_transfer.finite_line_source(t, alpha, borehole, borehole) / (2 * np.pi * ks) for t in times]
    np.testing.assert_allclose(G_FLS(times, ks, alpha, rb, height), expected, rtol=1e-8, atol=1e-12)
