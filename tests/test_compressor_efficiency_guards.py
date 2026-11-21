"""The range rule every evaluated compressor efficiency obeys: ``0 < eta <= 1``.

Whatever its origin -- a scalar, a caller's callable in either call form, one
of the library's default models -- an efficiency reaches a cycle only through
``_eval_eff``: a value above one is set to one, and a nonpositive or nonfinite
value makes the operating point infeasible.

The default maps themselves are examined in ``test_compressor_default_map.py``;
the maps of earlier releases and their own guard in
``test_recorded_compressor_maps.py``.
"""

from __future__ import annotations

import math

import pytest

from tmhp.compressor_efficiency import (
    InvalidCompressorEfficiency,
    SuctionState,
    _eval_eff,
    default_efficiencies,
    efficiency_extrapolated,
    map_eta_v,
)


def _suction(T_evap_C: float = 0.0) -> SuctionState:
    return SuctionState(T_evap_C + 273.15)


@pytest.mark.parametrize("value", [1.0 + 1e-12, 1.02, 1.5])
def test_values_above_one_are_set_to_one_for_scalars_and_callables(value):
    assert _eval_eff(value, 3.0, 40.0) == 1.0
    assert _eval_eff(lambda pr: value, 3.0, 40.0) == 1.0
    assert _eval_eff(lambda pr, n: value, 3.0, 40.0) == 1.0
    assert _eval_eff(0.7, 3.0, 40.0) == 0.7


@pytest.mark.parametrize("bad", [0.0, -0.1, math.nan, math.inf])
def test_nonpositive_or_nonfinite_values_remain_invalid(bad):
    with pytest.raises(ValueError, match="finite and positive"):
        _eval_eff(bad, 3.0, 40.0)
    with pytest.raises(ValueError, match="finite and positive"):
        _eval_eff(lambda pr, n: bad, 3.0, 40.0)
    with pytest.raises(InvalidCompressorEfficiency):
        _eval_eff(lambda pr: bad, 3.0, 40.0)


def test_a_default_volumetric_map_above_one_is_set_to_one():
    """The volumetric map passes one at a low pressure ratio and a high evaporating temperature."""
    models = default_efficiencies()
    state = _suction(25.0)
    raw = map_eta_v(1.5, 70.0, 25.0)
    assert raw > 1.0
    assert models["eta_cmp_vol"](1.5, 70.0, state) == raw  # the model itself is not capped
    assert _eval_eff(models["eta_cmp_vol"], 1.5, 70.0, state) == 1.0
    # the two power quantities cannot exceed one
    assert 0.0 < _eval_eff(models["eta_cmp_isen"], 1.5, 70.0, state) < 1.0
    assert 0.0 < _eval_eff(models["eta_cmp"], 1.5, 70.0, state) < 1.0


@pytest.mark.parametrize("T_evap_C", [-30.0, -10.0, 0.0, 15.0, 25.0])
def test_default_maps_stay_in_zero_one_through_the_rule(T_evap_C):
    models = default_efficiencies()
    state = _suction(T_evap_C)
    for pr in (1.5, 2.5, 4.0, 6.0, 9.0, 11.0):
        for rps in (15.0, 30.0, 60.0, 90.0, 120.0, 150.0):
            for model in models.values():
                assert 0.0 < _eval_eff(model, pr, rps, state) <= 1.0, (T_evap_C, pr, rps)


def test_extrapolation_flag_is_false_for_models_without_a_domain():
    assert not efficiency_extrapolated(0.7, 12.0, 150.0)
    assert not efficiency_extrapolated(lambda pr, n: 0.7, 12.0, 150.0)

    class WithAnotherDomain:
        validity_domain = object()  # only the library's own domain is read

        def __call__(self, pr, n):
            return 0.7

    assert not efficiency_extrapolated(WithAnotherDomain(), 12.0, 150.0)
    default = default_efficiencies()["eta_cmp_isen"]
    assert efficiency_extrapolated(default, 3.0, 150.0)  # above the measured speeds
    assert efficiency_extrapolated(default, 12.0, 60.0, 20.0)  # a lift the data reach only at low T_e
    assert not efficiency_extrapolated(default, 3.0, 60.0, 0.0)
    assert not efficiency_extrapolated(default, 3.0, 60.0)  # without T_e only the speed is checked


def test_an_invalid_efficiency_gives_an_infeasible_state_without_hiding_custom_errors():
    from tmhp import AirSourceHeatPump

    model = AirSourceHeatPump(eta_cmp=0.9, eta_cmp_vol=0.9, eta_cmp_isen=0.7)
    model.eta_cmp = -1.0
    assert model._calc_state(5, 5, -2000, 7, 20) is None

    def broken(pr, speed):
        raise TypeError("custom body error")

    model.eta_cmp = broken
    with pytest.raises(TypeError, match="custom body error"):
        model._calc_state(5, 5, -2000, 7, 20)
