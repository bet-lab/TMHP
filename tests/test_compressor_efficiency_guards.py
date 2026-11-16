"""The range rule every evaluated compressor efficiency obeys: ``0 < eta <= 1``.

Whatever its origin -- a scalar, a caller's callable in either call form, one
of the library's default models -- an efficiency reaches a cycle only through
``_eval_eff``: a value above one is set to one, and a nonpositive or nonfinite
value makes the operating point infeasible.

The default models themselves are examined in
``test_compressor_semi_empirical.py``; the maps of earlier releases and their
own guard in ``test_recorded_compressor_maps.py``.
"""

from __future__ import annotations

import math

import CoolProp.CoolProp as CP
import pytest

from tmhp.compressor_efficiency import (
    COMPRESSOR_TYPES,
    CompressorSpec,
    InvalidCompressorEfficiency,
    SemiEmpiricalCompressor,
    SuctionState,
    _eval_eff,
    default_efficiencies,
    efficiency_extrapolated,
    suction_state,
)


def _suction(ref: str, pr: float, T_evap_C: float = 0.0) -> SuctionState:
    """Suction 5 K above the dew point at ``T_evap_C``, compressed by ``pr``."""
    p_suc = CP.PropsSI("P", "T", T_evap_C + 273.15, "Q", 1, ref)
    h_suc, s_suc, rho_suc = (CP.PropsSI(k, "P", p_suc, "T", T_evap_C + 278.15, ref) for k in ("H", "S", "D"))
    return suction_state(p_suc, rho_suc, h_suc, CP.PropsSI("H", "P", p_suc * pr, "S", s_suc, ref))


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


@pytest.mark.parametrize("ctype", COMPRESSOR_TYPES)
def test_a_default_volumetric_model_above_one_is_set_to_one(ctype):
    """The volumetric forms have a level above one, so they pass one at a low pressure ratio and a high speed."""
    compressor = SemiEmpiricalCompressor(CompressorSpec(ctype), "R32", 40.0)
    state = _suction("R32", 1.2, T_evap_C=-25.0)
    raw = compressor.eta_v(1.2, 150.0, state)
    assert raw > 1.0
    assert compressor.eta_vol(1.2, 150.0, state) == raw  # the model itself is not capped
    assert _eval_eff(compressor.eta_vol, 1.2, 150.0, state) == 1.0
    # the two power quantities cannot exceed one
    assert 0.0 < _eval_eff(compressor.eta_isen, 1.2, 150.0, state) < 1.0
    assert 0.0 < _eval_eff(compressor.eta_em, 1.2, 150.0, state) < 1.0


@pytest.mark.parametrize("ctype", COMPRESSOR_TYPES)
@pytest.mark.parametrize("ref", ["R32", "R290", "R410A"])
def test_default_models_stay_in_zero_one_through_the_rule(ctype, ref):
    models = default_efficiencies(CompressorSpec(ctype), ref, 60.0)
    for pr in (1.5, 2.5, 4.0, 6.0, 9.0, 11.0):
        state = _suction(ref, pr)
        for rps in (15.0, 30.0, 60.0, 90.0, 120.0, 150.0):
            for model in models.values():
                assert 0.0 < _eval_eff(model, pr, rps, state) <= 1.0, (ctype, ref, pr, rps)


def test_extrapolation_flag_is_false_for_models_without_a_domain():
    assert not efficiency_extrapolated(0.7, 12.0, 150.0)
    assert not efficiency_extrapolated(lambda pr, n: 0.7, 12.0, 150.0)

    class WithAnotherDomain:
        validity_domain = object()  # only the library's own hull is read

        def __call__(self, pr, n):
            return 0.7

    assert not efficiency_extrapolated(WithAnotherDomain(), 12.0, 150.0)
    default = default_efficiencies(CompressorSpec(), "R32", 60.0)["eta_cmp_isen"]
    assert efficiency_extrapolated(default, 12.0, 150.0)
    assert not efficiency_extrapolated(default, 3.0, 60.0)


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
