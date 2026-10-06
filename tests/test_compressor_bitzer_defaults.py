"""Check production defaults against the independent Notion fit archive."""

import json
from pathlib import Path

import numpy as np
import pytest

from tmhp.compressor_efficiency import COEFFICIENT_VERSION, _eval_eff, make_eta_em, make_eta_isen, make_eta_vol
from tmhp.compressor_speed import solve_compressor_speed

ARCHIVE = Path(__file__).parents[1] / "validation" / "coefficients" / COEFFICIENT_VERSION


@pytest.mark.parametrize("key,factory", [("eta_v", make_eta_vol), ("eta_is", make_eta_isen), ("eta_em", make_eta_em)])
def test_absolute_speed_matches_notion_archive(key, factory):
    fit = json.loads((ARCHIVE / "three_efficiency_surfaces.json").read_text())["scroll"][key]
    for pr in np.linspace(1.5, 8, 13):
        for speed in np.linspace(5, 150, 21):
            expected = np.dot(fit["coef"], [1, pr, speed, pr * pr, speed * speed, pr * speed])
            assert factory(40)(pr, speed) == pytest.approx(expected, abs=1e-14)
            assert factory(60)(pr, speed) == pytest.approx(expected, abs=1e-14)


def test_invalid_extrapolation_is_rejected_without_clipping():
    model = make_eta_isen(60)
    assert model(3, 150) < 0
    with pytest.raises(ValueError, match="finite and in"):
        _eval_eff(model, 3, 150)


def test_speed_solver_finds_lower_branch_with_invalid_high_endpoint():
    def residual(n):
        if n > 100:
            raise ValueError("invalid extrapolation")
        return n * (100 - n) - 1600

    n, ok, clamp = solve_compressor_speed(residual, 5, 150)
    assert n == pytest.approx(20)
    assert ok and clamp is None


def test_unattainable_polynomial_load_clamps_at_capacity_peak():
    n, ok, clamp = solve_compressor_speed(lambda n: n * (100 - n) - 3000, 5, 95)
    assert n == pytest.approx(50)
    assert ok and clamp == "max"


def test_invalid_fit_returns_infeasible_state_without_hiding_custom_errors():
    from tmhp import AirSourceHeatPump

    model = AirSourceHeatPump(eta_cmp=0.9, eta_cmp_vol=0.9, eta_cmp_isen=0.7)
    model.eta_cmp = -1.0
    assert model._calc_state(5, 5, -2000, 7, 20) is None

    def broken(pr, speed):
        raise TypeError("custom body error")

    model.eta_cmp = broken
    with pytest.raises(TypeError, match="custom body error"):
        model._calc_state(5, 5, -2000, 7, 20)
