"""Finite line source g-function scalar and array agreement."""

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
