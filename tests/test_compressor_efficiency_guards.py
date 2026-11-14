"""Range and validity-domain guards on the active compressor-efficiency defaults.

The suite in ``test_compressor_efficiency_defaults.py`` checks the 2026-09-24
correlations kept in ``compressor_efficiency_legacy``; these tests check what
the models evaluate now (``make_eta_*`` / ``eta_*_default``).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tmhp.compressor_efficiency import (
    PR_FIT_MAX,
    PR_FIT_MIN,
    PR_FOLLOW_MIN,
    RPS_FIT_MAX,
    RPS_FIT_MIN,
    RPS_FOLLOW_MIN,
    VALIDITY_DOMAIN,
    _eval_eff,
    _polynomial,
    efficiency_extrapolated,
    eta_em_default,
    eta_isen_default,
    eta_vol_default,
    make_eta_em,
    make_eta_isen,
    make_eta_vol,
)

FACTORIES = [make_eta_vol, make_eta_isen, make_eta_em]
GRID_PR = np.linspace(1.2, 20.0, 48)
GRID_N = np.linspace(10.0, 160.0, 61)


def _cap(value: float) -> float:
    return min(value, 1.0)


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize("rated", [35.0, 50.0, 60.0])
def test_active_defaults_stay_in_zero_one_across_the_model_envelope(factory, rated):
    model = factory(rated)
    for pr in GRID_PR:
        for n in GRID_N:
            value = model(pr, n)
            assert 0.0 < value <= 1.0, (pr, n, value)
            assert 0.0 < _eval_eff(model, pr, n) <= 1.0
        assert 0.0 < model(pr) <= 1.0  # rated speed when N is omitted


@pytest.mark.parametrize("model", [eta_vol_default, eta_isen_default, eta_em_default])
def test_module_defaults_stay_in_zero_one(model):
    for pr in GRID_PR:
        for n in GRID_N:
            assert 0.0 < model(pr, n) <= 1.0


@pytest.mark.parametrize("factory", FACTORIES)
def test_inside_the_domain_the_fit_is_unchanged_except_the_cap_at_one(factory):
    model = factory(50.0)
    capped = 0
    for pr in np.linspace(PR_FIT_MIN, PR_FIT_MAX, 25):
        for n in np.linspace(RPS_FIT_MIN, RPS_FIT_MAX, 25):
            raw = _polynomial(model.coef, pr, n)
            assert model.raw(pr, n) == raw
            assert model(pr, n) == _cap(raw)
            assert not efficiency_extrapolated(model, pr, n)
            capped += raw > 1.0
    # only eta_v reaches above one inside its data (1.02 at PR 1.5, mid speed)
    assert (capped > 0) == (factory is make_eta_vol)


@pytest.mark.parametrize("factory", FACTORIES)
def test_high_pressure_ratio_and_high_speed_hold_the_boundary_value(factory):
    model = factory(50.0)
    for pr in (7.5, 9.0, 12.0, 20.0):
        for n in (40.0, 55.0, 70.0):
            assert model(pr, n) == _cap(model.raw(PR_FIT_MAX, n))
            assert efficiency_extrapolated(model, pr, n)
    for n in (80.0, 100.0, 150.0, 160.0):
        for pr in (2.0, 4.0, 6.0):
            assert model(pr, n) == _cap(model.raw(pr, RPS_FIT_MAX))
            assert efficiency_extrapolated(model, pr, n)
        assert model(12.0, n) == _cap(model.raw(PR_FIT_MAX, RPS_FIT_MAX))


def _continued(model, pr: float, n: float) -> float:
    """First-order continuation from the box, applied only where it lowers eta."""
    b0, b1, b2, b3, b4, b5 = model.coef
    pr_b = min(max(pr, PR_FIT_MIN), PR_FIT_MAX)
    n_b = min(max(n, RPS_FIT_MIN), RPS_FIT_MAX)
    value = _polynomial(model.coef, pr_b, n_b)
    if pr < PR_FIT_MIN:
        value += min(0.0, (b1 + 2 * b3 * pr_b + b5 * n_b) * (max(pr, PR_FOLLOW_MIN) - PR_FIT_MIN))
    if n < RPS_FIT_MIN:
        value += min(0.0, (b2 + 2 * b4 * n_b + b5 * pr_b) * (max(n, RPS_FOLLOW_MIN) - RPS_FIT_MIN))
    return _cap(value)


@pytest.mark.parametrize("factory", FACTORIES)
def test_low_speed_and_low_lift_follow_the_boundary_slope_only_downwards(factory):
    model = factory(50.0)
    for pr in (1.0, 1.2, 1.4, 3.0, 5.0, 7.0, 9.0):
        for n in (10.0, 15.0, 20.0, 30.0, 50.0):
            if pr >= PR_FIT_MIN and n >= RPS_FIT_MIN:
                continue
            value = model(pr, n)
            assert value == pytest.approx(_continued(model, pr, n), abs=1e-15)
            # never above the boundary value it continues from: no bonus outside the data
            pr_b = min(max(pr, PR_FIT_MIN), PR_FIT_MAX)
            n_b = min(max(n, RPS_FIT_MIN), RPS_FIT_MAX)
            assert value <= _cap(model.raw(pr_b, n_b)) + 1e-15
            assert efficiency_extrapolated(model, pr, n)
    # below the lowest followed speed the value is held
    assert model(3.0, 10.0) == model(3.0, RPS_FOLLOW_MIN)


def test_low_speed_continuation_carries_the_leakage_penalty():
    """eta_is and eta_v fall towards low speed; eta_em gets no low-speed bonus."""
    for pr in (2.0, 3.0, 5.0, 7.0):
        assert eta_isen_default(pr, 15.0) < eta_isen_default(pr, 25.0) < eta_isen_default(pr, RPS_FIT_MIN)
        assert eta_vol_default(pr, 15.0) < eta_vol_default(pr, 25.0) < eta_vol_default(pr, RPS_FIT_MIN)
        assert eta_em_default(pr, 15.0) == eta_em_default(pr, RPS_FIT_MIN)
    # over-compression: eta_is falls as PR -> 1 below the data
    assert eta_isen_default(1.0, 50.0) < eta_isen_default(1.2, 50.0) < eta_isen_default(PR_FIT_MIN, 50.0)


def test_domain_metadata_matches_the_coefficient_archive():
    import json
    from pathlib import Path

    from tmhp.compressor_efficiency import COEFFICIENT_VERSION

    archive = Path(__file__).parents[1] / "validation" / "coefficients" / COEFFICIENT_VERSION
    fits = json.loads((archive / "three_efficiency_surfaces.json").read_text())["scroll"]
    for key in ("eta_v", "eta_is", "eta_em"):
        hull = np.array(fits[key]["hull"])
        assert hull[:, 0].min() == pytest.approx(VALIDITY_DOMAIN.pr_min, abs=1e-9)
        assert hull[:, 0].max() == pytest.approx(VALIDITY_DOMAIN.pr_max, abs=1e-9)
        assert hull[:, 1].min() == pytest.approx(VALIDITY_DOMAIN.rps_min, abs=1e-9)
        assert hull[:, 1].max() == pytest.approx(VALIDITY_DOMAIN.rps_max, abs=1e-9)


def test_extrapolation_flag_is_false_for_models_without_a_domain():
    assert not efficiency_extrapolated(0.7, 12.0, 150.0)
    assert not efficiency_extrapolated(lambda pr, n: 0.7, 12.0, 150.0)
    assert make_eta_isen(50).extrapolated(3.0, 150.0)
    assert not make_eta_isen(50).extrapolated(3.0, 50.0)
    # rated speed stands in for an omitted N
    assert make_eta_isen(100).extrapolated(3.0)


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


def test_nan_inputs_are_not_turned_into_a_valid_efficiency():
    for factory in FACTORIES:
        assert math.isnan(factory(50)(math.nan, 40.0))
        assert math.isnan(factory(50)(3.0, math.nan))
        with pytest.raises(ValueError):
            _eval_eff(factory(50), math.nan, 40.0)


# --- Reproductions from issue #96 section 1.2 (R32 ASHPB, model defaults) ---

_STEADY_FLOW = {
    "dV_mix_w_out [m3/s]": 0.0,
    "dV_tank_w_out [m3/s]": 0.0,
    "dV_tank_w_in [m3/s]": 0.0,
    "dV_mix_sup_w_in [m3/s]": 0.0,
    "alp": 0.0,
}


def _ashpb(**kwargs):
    from tmhp import AirSourceHeatPumpBoiler

    # The frost-free cycle: the guards are pinned at the cycle's own duty and
    # speed (frost solves a cold point at a larger duty).
    kwargs.setdefault("defrost", False)
    return AirSourceHeatPumpBoiler(ref="R32", **kwargs)


def _pr_and_speed(row: dict) -> tuple[float, float]:
    return row["P_ref_cmp_out [Pa]"] / row["P_ref_cmp_in [Pa]"], row["cmp_rpm [rpm]"] / 60.0


def test_a_minus7_w55_full_load_is_solved_at_the_held_boundary():
    """Before the guard eta_is was negative at PR 9.8 and every load at A-7/W55 was cycle_invalid.

    The efficiency guard is tested with the discharge limit off: at 271 C the
    point is outside the compressor envelope (test_compressor_operating_envelope).
    """
    hp = _ashpb(T_dis_max=None)
    row = hp.analyze_steady(T_tank_w=55, T0=-7, Q_ref_tank=15000)
    assert row["failure_reason"] == "none"
    pr, speed = _pr_and_speed(row)
    assert pr > PR_FIT_MAX and RPS_FIT_MIN < speed < RPS_FIT_MAX
    assert efficiency_extrapolated(hp.eta_cmp_isen, pr, speed)
    assert row["eta_cmp_isen [-]"] == pytest.approx(hp.eta_cmp_isen.raw(PR_FIT_MAX, speed), rel=1e-12)
    assert row["eta_cmp_vol [-]"] == pytest.approx(_cap(hp.eta_cmp_vol.raw(PR_FIT_MAX, speed)), rel=1e-12)
    assert row["Q_ref_tank [W]"] == pytest.approx(15000, rel=1e-6)
    # Observed 2026-11-12: PR 9.77, 45.6 rev/s, eta_is 0.374, discharge 271 C, COP_sys 1.73.
    # The discharge temperature is what the compressor envelope refuses (T_dis_max, default on).
    assert row["eta_cmp_isen [-]"] == pytest.approx(0.374, abs=0.005)
    assert row["T_ref_cmp_out [°C]"] == pytest.approx(271.4, abs=2.0)


def test_a_minus7_w55_half_load_has_a_valid_cycle_after_the_guard():
    """The cycle closes at 7.5 kW (3 K outdoor approach) with all efficiencies in (0, 1]."""
    hp = _ashpb()
    row = hp._calc_state(dT_ref_ou=3.0, T_tank_w=55, Q_ref_tank=7500, T0=-7, flow_state=dict(_STEADY_FLOW))
    assert row is not None and row["converged"]
    assert row["Q_ref_tank [W]"] == pytest.approx(7500, rel=1e-6)
    for key in ("eta_cmp_isen [-]", "eta_cmp_vol [-]", "eta_cmp [-]"):
        assert 0.0 < row[key] <= 1.0
    pr, speed = _pr_and_speed(row)
    assert speed < RPS_FIT_MIN and efficiency_extrapolated(hp.eta_cmp_isen, pr, speed)
    # analyze_steady still returns hx_not_converged here: tests/invariants C2.


def test_a0_w60_inside_the_domain_is_untouched_by_the_guard():
    hp = _ashpb(T_dis_max=None)  # 201.6 C: refused by default (test_compressor_operating_envelope)
    row = hp.analyze_steady(T_tank_w=60, T0=0, Q_ref_tank=15000)
    pr, speed = _pr_and_speed(row)
    assert not efficiency_extrapolated(hp.eta_cmp_isen, pr, speed)
    assert row["eta_cmp_isen [-]"] == pytest.approx(hp.eta_cmp_isen.raw(pr, speed), rel=1e-12)
    # Observed before and after the guard: PR 6.40, 35.4 rev/s, eta_is 0.452, discharge 200.4 C.
    # (0.449 and 201.6 C with 15 °C standard air on the outdoor coil: at 0 °C the fan moves
    # 5.5 % more air mass, the evaporating temperature is 0.07 K higher and the PR 6.40, not 6.41.)
    assert row["eta_cmp_isen [-]"] == pytest.approx(0.452, abs=0.002)
    assert row["T_ref_cmp_out [°C]"] == pytest.approx(200.4, abs=1.0)
    # With the default T_dis_max (R32 150 °C) the point is refused: tests/invariants C6.
