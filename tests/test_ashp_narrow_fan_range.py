"""``AirSourceHeatPump`` with a constant-speed fan or a narrow fan flow range.

A coil whose fan cannot modulate carries a given duty at one approach
temperature only, so the operating points form a set of zero width in the
approach plane. The grid pre-scan and the Nelder--Mead simplex never land on
it: before this search the default unit with both fans fixed returned
``hx_not_converged`` after 25 s. The band of approaches is now solved from the
coil capacity margins (``_NarrowFanSearch``), as in the ground-source and the
water-heating models.
"""

import math

import numpy as np
import pytest

from tmhp import AirSourceHeatPump
from tmhp._opt_utils import margin_band_on_valid_range
from tmhp.constants import c_a, rho_a

IU_FLOW, OU_FLOW = 0.2, 0.5
FIXED_IU = dict(dV_iu_fan_a_min=IU_FLOW, dV_iu_fan_a_max=IU_FLOW)
FIXED_OU = dict(dV_ou_fan_a_min=OU_FLOW, dV_ou_fan_a_max=OU_FLOW)

HEATING = dict(Q_r_iu=-3000.0, T0=7.0, T_a_room=20.0)
COOLING = dict(Q_r_iu=3000.0, T0=35.0, T_a_room=27.0)


def _solve(model, **point):
    return model.analyze_steady(**point, verbose=False, postprocess=False)


def _running(out, key):
    """A flow while the compressor runs.

    A row may be reported as a mean: over an on/off cycling period below the
    minimum capacity (flows scaled by ``capacity_ratio [-]``), or over frosting
    and defrost of the outdoor coil (flows scaled by the delivered heat over
    the frost-free heat).
    """
    factor = out.get("capacity_ratio [-]", 1.0)
    if "Q_ref_iu_frost_free [W]" in out:
        factor = factor * out["Q_ref_iu [W]"] / out["Q_ref_iu_frost_free [W]"]
    return out[key] / factor


def _count_states(model):
    """Count cycle evaluations: the cost of a solve, independent of the machine it runs on."""
    calls = {"n": 0}
    original = model._calc_state

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    model._calc_state = counted
    return calls


@pytest.fixture(scope="module")
def both_fixed():
    return AirSourceHeatPump(**FIXED_IU, **FIXED_OU)


@pytest.fixture(scope="module")
def variable_heating():
    return _solve(AirSourceHeatPump(), **HEATING)


# ── band helper ───────────────────────────────────────────────────────────


def _linear_margins(low_root, high_root, valid=(-math.inf, math.inf)):
    """Margins of a coil that carries the duty between two approach roots."""

    def margins(x):
        if not valid[0] <= x <= valid[1]:
            return None
        return 100.0 * (x - low_root), 100.0 * (x - high_root)

    return margins


def test_band_helper_solves_both_edges_and_a_single_point():
    assert margin_band_on_valid_range(_linear_margins(4.2, 9.7), 1.0, 20.0) == pytest.approx((4.2, 9.7), abs=1e-6)
    # Equal flow limits: the two margins are one function and the band one point.
    low, high = margin_band_on_valid_range(_linear_margins(7.123456, 7.123456), 1.0, 20.0)
    assert low == pytest.approx(7.123456, abs=1e-6) and high == pytest.approx(low, abs=1e-6)


def test_band_helper_reports_an_empty_band():
    assert margin_band_on_valid_range(_linear_margins(25.0, 30.0), 1.0, 20.0) is None  # never carries the duty
    assert margin_band_on_valid_range(_linear_margins(-5.0, 0.5), 1.0, 20.0) is None  # cannot turn down
    assert margin_band_on_valid_range(lambda x: None, 1.0, 20.0) is None  # no cycle anywhere


def test_band_helper_follows_the_margins_to_the_edge_of_the_valid_range():
    # The point lies between the last coarse sample with a cycle (10.5) and the
    # first without (15.25): the valid run is extended, not given up.
    band = margin_band_on_valid_range(_linear_margins(12.0, 12.0, valid=(1.0, 13.0)), 1.0, 20.0)
    assert band == pytest.approx((12.0, 12.0), abs=1e-6)
    # ... and the band is empty when the cycle ends before the coil carries the duty.
    assert margin_band_on_valid_range(_linear_margins(14.0, 14.0, valid=(1.0, 13.0)), 1.0, 20.0) is None


@pytest.mark.parametrize("hint", [None, 7.0, 4.3, 15.0])
def test_band_helper_hint_changes_the_cost_not_the_result(hint):
    assert margin_band_on_valid_range(_linear_margins(6.5, 7.5), 1.0, 20.0, hint=hint) == pytest.approx(
        (6.5, 7.5), abs=1e-6
    )


# ── steady state ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("point", [HEATING, COOLING], ids=["heating", "cooling"])
def test_both_fans_fixed_meets_the_duty_exactly(both_fixed, point):
    calls = _count_states(both_fixed)
    out = _solve(both_fixed, **point)
    del both_fixed._calc_state
    assert out["converged"] and out["failure_reason"] == "none"
    assert out["capacity_clamped"] is None
    assert out["Q_ref_iu [W]"] == pytest.approx(3000.0, rel=1e-5)
    assert out["dV_iu_a [m3/s]"] == IU_FLOW and out["dV_ou_a [m3/s]"] == OU_FLOW
    # First law across the cycle: the two coil duties differ by the shaft work.
    work = out["E_cmp [W]"] * out["eta_cmp [-]"]
    assert abs(out["Q_ref_iu [W]"] - out["Q_ref_ou [W]"]) == pytest.approx(work, rel=1e-6)
    # About 30 cycle evaluations (0.3 s); the grid search spent 2,700 and failed.
    assert calls["n"] < 150


def test_indoor_fan_fixed_outdoor_fan_still_modulates():
    model = AirSourceHeatPump(**FIXED_IU)
    out = _solve(model, **HEATING)
    assert out["converged"] and out["capacity_clamped"] is None
    assert out["Q_ref_iu [W]"] == pytest.approx(3000.0, rel=1e-5)
    assert out["dV_iu_a [m3/s]"] == IU_FLOW
    assert model.dV_ou_fan_a_min < out["dV_ou_a [m3/s]"] < model.dV_ou_fan_a_max


def test_outdoor_fan_fixed_indoor_fan_still_modulates():
    model = AirSourceHeatPump(**FIXED_OU)
    out = _solve(model, **COOLING)
    assert out["converged"] and out["capacity_clamped"] is None
    assert out["Q_ref_iu [W]"] == pytest.approx(3000.0, rel=1e-5)
    assert out["dV_ou_a [m3/s]"] == OU_FLOW
    assert model.dV_iu_fan_a_min < out["dV_iu_a [m3/s]"] < model.dV_iu_fan_a_max


def test_fans_fixed_at_the_variable_optimum_reproduce_it(variable_heating):
    """Fixing both fans at the airflows the free search chose must give that same point."""
    iu, ou = variable_heating["dV_iu_a [m3/s]"], variable_heating["dV_ou_a [m3/s]"]
    fixed = _solve(
        AirSourceHeatPump(dV_iu_fan_a_min=iu, dV_iu_fan_a_max=iu, dV_ou_fan_a_min=ou, dV_ou_fan_a_max=ou), **HEATING
    )
    assert fixed["converged"]
    assert fixed["E_tot [W]"] == pytest.approx(variable_heating["E_tot [W]"], rel=1e-5)
    assert fixed["T_ref_evap_sat [°C]"] == pytest.approx(variable_heating["T_ref_evap_sat [°C]"], abs=1e-3)
    assert fixed["T_ref_cond_sat_v [°C]"] == pytest.approx(variable_heating["T_ref_cond_sat_v [°C]"], abs=1e-3)


def test_a_fixed_fan_costs_power_against_the_free_optimum(both_fixed, variable_heating):
    out = _solve(both_fixed, **HEATING)
    assert out["E_tot [W]"] > variable_heating["E_tot [W]"]


def test_a_very_narrow_range_matches_the_fixed_fan(both_fixed):
    """A +/-0.05 % flow band is the same machine: the band search seeds the simplex."""
    narrow = AirSourceHeatPump(
        dV_iu_fan_a_min=IU_FLOW * 0.9995,
        dV_iu_fan_a_max=IU_FLOW * 1.0005,
        dV_ou_fan_a_min=OU_FLOW * 0.9995,
        dV_ou_fan_a_max=OU_FLOW * 1.0005,
    )
    assert narrow._has_narrow_fan() and not narrow._has_constant_speed_fan()
    out, ref = _solve(narrow, **HEATING), _solve(both_fixed, **HEATING)
    assert out["converged"] and out["capacity_clamped"] is None
    assert out["E_tot [W]"] == pytest.approx(ref["E_tot [W]"], rel=2e-3)


def test_a_small_request_runs_at_the_compressor_floor(both_fixed):
    out = _solve(both_fixed, Q_r_iu=-800.0, T0=7.0, T_a_room=20.0)
    assert out["converged"] and out["capacity_clamped"] == "min"
    assert out["Q_ref_iu [W]"] >= 800.0 * (1.0 - 1e-6)
    assert _running(out, "dV_iu_a [m3/s]") == pytest.approx(IU_FLOW, rel=1e-12)
    assert _running(out, "dV_ou_a [m3/s]") == pytest.approx(OU_FLOW, rel=1e-12)


def test_a_request_above_the_compressor_ceiling_reports_the_ceiling():
    out = _solve(AirSourceHeatPump(**FIXED_OU), Q_r_iu=-6000.0, T0=-15.0, T_a_room=20.0)
    assert out["converged"] and out["capacity_clamped"] == "max"
    assert 0.0 < out["Q_ref_iu [W]"] < 6000.0
    assert _running(out, "dV_ou_a [m3/s]") == pytest.approx(OU_FLOW, rel=1e-12)


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_a_coil_that_cannot_carry_any_duty_fails_with_a_reason():
    # Within an 8 K approach this indoor coil carries no duty the compressor can deliver.
    model = AirSourceHeatPump(UA_iu_rated=300.0, dT_approach_bounds=(1.0, 8.0), **FIXED_IU, **FIXED_OU)
    out = _solve(model, **HEATING)
    assert not out["converged"] and out["failure_reason"] == "hx_not_converged"
    assert out["Q_ref_iu [W]"] == 0.0 and out["E_tot [W]"] == 0.0


def test_max_capacity_with_fixed_fans_is_bounded_by_the_coil(both_fixed):
    """The grid sweep never met a duty with fixed fans and reported ``Q_max = 0``."""
    ceiling = both_fixed.max_capacity(T0=7.0, T_a_room=20.0, mode="heating", rel_tol=5e-2)
    assert ceiling["binding"] in {"cycle", "compressor"}
    # The indoor coil at its one airflow and the widest approach the search allows.
    c_air = c_a * rho_a * IU_FLOW
    ua = both_fixed.UA_iu_rated * (IU_FLOW / both_fixed.dV_iu_fan_a_ref) ** both_fixed.n_iu
    coil_limit = c_air * (1.0 - math.exp(-ua / c_air)) * both_fixed.dT_approach_bounds[1]
    assert 0.9 * coil_limit <= ceiling["Q_max [W]"] <= coil_limit * (1.0 + 1e-6)


# ── dynamic ───────────────────────────────────────────────────────────────


def test_dynamic_run_with_fixed_fans(both_fixed):
    load = np.array([-3000.0, -2500.0, -800.0])
    df = both_fixed.analyze_dynamic(
        simulation_period_sec=3 * 600,
        dt_s=600,
        Q_r_iu_schedule=load,
        T0_schedule=[7.0, 6.0, 7.0],
        T_a_room_schedule=[20.0] * 3,
    )
    assert df["converged"].all() and (df["failure_reason"] == "none").all()
    assert np.allclose(_running(df, "dV_iu_a [m3/s]"), IU_FLOW)
    assert np.allclose(_running(df, "dV_ou_a [m3/s]"), OU_FLOW)
    assert df["Q_ref_iu [W]"].iloc[:2].to_numpy() == pytest.approx(-load[:2], rel=1e-5)
    assert df["capacity_clamped"].iloc[2] == "min"
