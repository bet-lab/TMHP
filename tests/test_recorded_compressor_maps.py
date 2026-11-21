"""The efficiency maps of earlier releases, recorded outside the library (#164).

``validation/coefficients/recorded_maps`` keeps the two maps the library
shipped before the semi-empirical models, for the validation studies that
compare against them and for the tests whose scenarios were recorded on
machines built with them (``tests/conftest.py``, marker ``earlier_defaults``).
These tests pin the record to its archives and to the behaviour it had as the
library's default:

* the quadratic map of ``BITZER-three-C3-absolute-N-2026-10-06``: its
  coefficients and fit box against the archive files, and its guard (held
  above the box, first-order continuation below it, capped at one);
* the ``v2026-09-24`` correlations: their constants against the archive and
  the shapes the standalone-compressor data put there.

The rules every efficiency obeys, whatever its origin (``0 < eta <= 1``, call
forms), are in ``test_compressor_efficiency_guards.py``; the library's default
models in ``test_compressor_default_map.py``.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
import pytest
from validation.coefficients import recorded_maps
from validation.coefficients.recorded_maps import bitzer_quadratic_2026_10_06 as quadratic
from validation.coefficients.recorded_maps import v2026_09_24 as correlations

from tmhp.compressor_efficiency import _eval_eff, efficiency_extrapolated

REPO = Path(__file__).resolve().parents[1]
COEFFICIENTS = REPO / "validation" / "coefficients"

# --------------------------------------------------------------------------------------------------------------
# The record as a whole
# --------------------------------------------------------------------------------------------------------------


def test_every_recorded_release_has_its_archive_folder():
    assert set(recorded_maps.RELEASES) == {"BITZER-three-C3-absolute-N-2026-10-06", "v2026-09-24"}
    for release, module in recorded_maps.RELEASES.items():
        assert release == module.COEFFICIENT_VERSION
        assert (COEFFICIENTS / release).is_dir()


@pytest.mark.parametrize("release", sorted(recorded_maps.RELEASES))
def test_recorded_efficiencies_are_the_three_constructor_arguments(release):
    module = recorded_maps.RELEASES[release]
    models = recorded_maps.recorded_efficiencies(release, 47.0)
    assert sorted(models) == ["eta_cmp", "eta_cmp_isen", "eta_cmp_vol"]
    for name, factory in (
        ("eta_cmp_isen", module.make_eta_isen),
        ("eta_cmp_vol", module.make_eta_vol),
        ("eta_cmp", module.make_eta_em),
    ):
        for pr, rps in ((1.8, 20.0), (3.0, 47.0), (6.0, 90.0)):
            assert models[name](pr, rps) == factory(47.0)(pr, rps)
            # plain user-style callables: the library evaluates them as model(PR, rps), no suction state
            assert _eval_eff(models[name], pr, rps) == min(1.0, models[name](pr, rps))
    with pytest.raises(ValueError, match="no recorded map"):
        recorded_maps.recorded_efficiencies("semi-empirical-2026-11-15", 47.0)


# --------------------------------------------------------------------------------------------------------------
# BITZER-three-C3-absolute-N-2026-10-06: the quadratic map
# --------------------------------------------------------------------------------------------------------------

QUADRATIC_ARCHIVE = COEFFICIENTS / quadratic.COEFFICIENT_VERSION
QUADRATIC = [
    ("eta_v", quadratic.make_eta_vol, quadratic.COEFFICIENTS_ETA_V),
    ("eta_is", quadratic.make_eta_isen, quadratic.COEFFICIENTS_ETA_IS),
    ("eta_em", quadratic.make_eta_em, quadratic.COEFFICIENTS_ETA_EM),
]
FACTORIES = [quadratic.make_eta_vol, quadratic.make_eta_isen, quadratic.make_eta_em]
GRID_PR = np.linspace(1.2, 20.0, 48)
GRID_N = np.linspace(10.0, 160.0, 61)
PR_FIT_MIN, PR_FIT_MAX = quadratic.PR_FIT_MIN, quadratic.PR_FIT_MAX
RPS_FIT_MIN, RPS_FIT_MAX = quadratic.RPS_FIT_MIN, quadratic.RPS_FIT_MAX


def _cap(value: float) -> float:
    return min(value, 1.0)


@pytest.mark.parametrize(("key", "factory", "coefficients"), QUADRATIC)
def test_quadratic_coefficients_are_the_archive(key, factory, coefficients):
    """The recorded tuples are, digit for digit, the scroll rows of both archive files."""
    with (QUADRATIC_ARCHIVE / "coefficients.csv").open() as fh:
        (row,) = [r for r in csv.DictReader(fh) if r["type"] == "scroll" and r["efficiency"] == key]
    assert tuple(float(row[f"b{i}"]) for i in range(6)) == coefficients
    fit = json.loads((QUADRATIC_ARCHIVE / "three_efficiency_surfaces.json").read_text())["scroll"][key]
    assert tuple(fit["coef"]) == coefficients
    assert factory(50.0).coef == coefficients


@pytest.mark.parametrize(("key", "factory", "coefficients"), QUADRATIC)
def test_quadratic_evaluates_the_archive_surface_in_absolute_speed(key, factory, coefficients):
    for pr in np.linspace(1.5, 8, 13):
        for speed in np.linspace(5, 150, 21):
            expected = np.dot(coefficients, [1, pr, speed, pr * pr, speed * speed, pr * speed])
            # the rated speed does not enter: N is the physical shaft speed
            assert factory(40).raw(pr, speed) == pytest.approx(expected, abs=1e-14)
            assert factory(60).raw(pr, speed) == pytest.approx(expected, abs=1e-14)


def test_quadratic_fit_box_is_the_hull_of_the_archive():
    fits = json.loads((QUADRATIC_ARCHIVE / "three_efficiency_surfaces.json").read_text())["scroll"]
    box = quadratic.FIT_BOX
    for key in ("eta_v", "eta_is", "eta_em"):
        hull = np.array(fits[key]["hull"])
        assert hull[:, 0].min() == pytest.approx(box.pr_min, abs=1e-9)
        assert hull[:, 0].max() == pytest.approx(box.pr_max, abs=1e-9)
        assert hull[:, 1].min() == pytest.approx(box.rps_min, abs=1e-9)
        assert hull[:, 1].max() == pytest.approx(box.rps_max, abs=1e-9)


def test_quadratic_coefficients_equal_the_audit_copy():
    """``docs/audits/compressor-efficiency-baseline.json`` froze the map when it became the default."""
    frozen = json.loads((REPO / "docs" / "audits" / "compressor-efficiency-baseline.json").read_text())
    assert quadratic.coefficients() == frozen


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize("rated", [35.0, 50.0, 60.0])
def test_quadratic_stays_in_zero_one_across_the_model_envelope(factory, rated):
    model = factory(rated)
    for pr in GRID_PR:
        for n in GRID_N:
            value = model(pr, n)
            assert 0.0 < value <= 1.0, (pr, n, value)
            assert 0.0 < _eval_eff(model, pr, n) <= 1.0
        assert 0.0 < model(pr) <= 1.0  # rated speed when N is omitted


@pytest.mark.parametrize(
    "model", [quadratic.eta_vol_default, quadratic.eta_isen_default, quadratic.eta_em_default], ids=["v", "is", "em"]
)
def test_quadratic_module_defaults_stay_in_zero_one(model):
    assert model.rps_rated == quadratic.RPS_REF == 50.0
    for pr in GRID_PR:
        for n in GRID_N:
            assert 0.0 < model(pr, n) <= 1.0


@pytest.mark.parametrize("factory", FACTORIES)
def test_inside_the_box_the_fit_is_unchanged_except_the_cap_at_one(factory):
    model = factory(50.0)
    capped = 0
    for pr in np.linspace(PR_FIT_MIN, PR_FIT_MAX, 25):
        for n in np.linspace(RPS_FIT_MIN, RPS_FIT_MAX, 25):
            raw = quadratic._polynomial(model.coef, pr, n)
            assert model.raw(pr, n) == raw
            assert model(pr, n) == _cap(raw)
            assert not model.extrapolated(pr, n)
            capped += raw > 1.0
    # only eta_v reaches above one inside its data (1.02 at PR 1.5, mid speed)
    assert (capped > 0) == (factory is quadratic.make_eta_vol)


def test_values_above_one_are_set_to_one():
    raw = quadratic.make_eta_vol(60).raw(2.0, 53.0)
    assert raw > 1.0
    assert quadratic.make_eta_vol(60)(2.0, 53.0) == 1.0
    assert _eval_eff(quadratic.make_eta_vol(60), 2.0, 53.0) == 1.0


@pytest.mark.parametrize("factory", FACTORIES)
def test_high_pressure_ratio_and_high_speed_hold_the_boundary_value(factory):
    model = factory(50.0)
    for pr in (7.5, 9.0, 12.0, 20.0):
        for n in (40.0, 55.0, 70.0):
            assert model(pr, n) == _cap(model.raw(PR_FIT_MAX, n))
            assert model.extrapolated(pr, n)
    for n in (80.0, 100.0, 150.0, 160.0):
        for pr in (2.0, 4.0, 6.0):
            assert model(pr, n) == _cap(model.raw(pr, RPS_FIT_MAX))
            assert model.extrapolated(pr, n)
        assert model(12.0, n) == _cap(model.raw(PR_FIT_MAX, RPS_FIT_MAX))


def test_a_negative_raw_fit_is_held_by_the_box_and_still_rejected_raw():
    model = quadratic.make_eta_isen(60)
    assert model.raw(3, 150) < 0
    assert model(3, 150) == model.raw(3, 72.5) > 0
    with pytest.raises(ValueError, match="finite and positive"):
        _eval_eff(model.raw, 3, 150)


def _continued(model, pr: float, n: float) -> float:
    """First-order continuation from the box, applied only where it lowers eta."""
    b0, b1, b2, b3, b4, b5 = model.coef
    pr_b = min(max(pr, PR_FIT_MIN), PR_FIT_MAX)
    n_b = min(max(n, RPS_FIT_MIN), RPS_FIT_MAX)
    value = quadratic._polynomial(model.coef, pr_b, n_b)
    if pr < PR_FIT_MIN:
        value += min(0.0, (b1 + 2 * b3 * pr_b + b5 * n_b) * (max(pr, quadratic.PR_FOLLOW_MIN) - PR_FIT_MIN))
    if n < RPS_FIT_MIN:
        value += min(0.0, (b2 + 2 * b4 * n_b + b5 * pr_b) * (max(n, quadratic.RPS_FOLLOW_MIN) - RPS_FIT_MIN))
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
            assert model.extrapolated(pr, n)
    # below the lowest followed speed the value is held
    assert model(3.0, 10.0) == model(3.0, quadratic.RPS_FOLLOW_MIN)


def test_low_speed_continuation_carries_the_leakage_penalty():
    """eta_is and eta_v fall towards low speed; eta_em gets no low-speed bonus."""
    eta_isen, eta_vol, eta_em = quadratic.eta_isen_default, quadratic.eta_vol_default, quadratic.eta_em_default
    for pr in (2.0, 3.0, 5.0, 7.0):
        assert eta_isen(pr, 15.0) < eta_isen(pr, 25.0) < eta_isen(pr, RPS_FIT_MIN)
        assert eta_vol(pr, 15.0) < eta_vol(pr, 25.0) < eta_vol(pr, RPS_FIT_MIN)
        assert eta_em(pr, 15.0) == eta_em(pr, RPS_FIT_MIN)
    # over-compression: eta_is falls as PR -> 1 below the data
    assert eta_isen(1.0, 50.0) < eta_isen(1.2, 50.0) < eta_isen(PR_FIT_MIN, 50.0)


def test_nan_inputs_are_not_turned_into_a_valid_efficiency():
    for factory in FACTORIES:
        assert math.isnan(factory(50)(math.nan, 40.0))
        assert math.isnan(factory(50)(3.0, math.nan))
        with pytest.raises(ValueError):
            _eval_eff(factory(50), math.nan, 40.0)


def test_the_record_answers_for_its_box_and_the_library_flag_stays_false():
    """A recorded model is a caller's callable to the library: no ``validity_domain``, no extrapolation flag."""
    model = quadratic.make_eta_isen(50)
    assert model.extrapolated(3.0, 150.0)
    assert not model.extrapolated(3.0, 50.0)
    assert quadratic.make_eta_isen(100).extrapolated(3.0)  # rated speed stands in for an omitted N
    assert not hasattr(model, "validity_domain")
    assert not efficiency_extrapolated(model, 3.0, 150.0)


# --- Reproductions from issue #96 section 1.2: an R32 air-source boiler built with the quadratic map --------------
# (the machine a bare constructor built then: tests/conftest.py, marker ``earlier_defaults``)

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


@pytest.mark.earlier_defaults  # tests/conftest.py
def test_a_minus7_w55_full_load_is_solved_at_the_held_boundary():
    """Before the guard eta_is was negative at PR 9.8 and every load at A-7/W55 was cycle_invalid.

    The efficiency guard is tested with the discharge limit off: at 271 C the
    point is outside the compressor envelope (test_compressor_operating_envelope).
    """
    hp = _ashpb(T_dis_max=None)
    assert isinstance(hp.eta_cmp_isen, quadratic.FittedEfficiency)
    row = hp.analyze_steady(T_tank_w=55, T0=-7, Q_ref_tank=15000)
    assert row["failure_reason"] == "none"
    pr, speed = _pr_and_speed(row)
    assert pr > PR_FIT_MAX and RPS_FIT_MIN < speed < RPS_FIT_MAX
    assert hp.eta_cmp_isen.extrapolated(pr, speed)
    assert row["eta_cmp_isen [-]"] == pytest.approx(hp.eta_cmp_isen.raw(PR_FIT_MAX, speed), rel=1e-12)
    assert row["eta_cmp_vol [-]"] == pytest.approx(_cap(hp.eta_cmp_vol.raw(PR_FIT_MAX, speed)), rel=1e-12)
    assert row["Q_ref_tank [W]"] == pytest.approx(15000, rel=1e-6)
    # Observed 2026-11-12: PR 9.77, 45.6 rev/s, eta_is 0.374, discharge 271 C, COP_sys 1.73.
    # The discharge temperature is what the compressor envelope refuses (T_dis_max, default on).
    assert row["eta_cmp_isen [-]"] == pytest.approx(0.374, abs=0.005)
    assert row["T_ref_cmp_out [°C]"] == pytest.approx(271.4, abs=2.0)


@pytest.mark.earlier_defaults  # tests/conftest.py
def test_a_minus7_w55_half_load_has_a_valid_cycle_after_the_guard():
    """The cycle closes at 7.5 kW (3 K outdoor approach) with all efficiencies in (0, 1]."""
    hp = _ashpb()
    row = hp._calc_state(dT_ref_ou=3.0, T_tank_w=55, Q_ref_tank=7500, T0=-7, flow_state=dict(_STEADY_FLOW))
    assert row is not None and row["converged"]
    assert row["Q_ref_tank [W]"] == pytest.approx(7500, rel=1e-6)
    for key in ("eta_cmp_isen [-]", "eta_cmp_vol [-]", "eta_cmp [-]"):
        assert 0.0 < row[key] <= 1.0
    pr, speed = _pr_and_speed(row)
    assert speed < RPS_FIT_MIN and hp.eta_cmp_isen.extrapolated(pr, speed)
    # analyze_steady still returns hx_not_converged here: tests/invariants C2.


@pytest.mark.earlier_defaults  # tests/conftest.py
def test_a0_w60_inside_the_box_is_untouched_by_the_guard():
    hp = _ashpb(T_dis_max=None)  # 201.6 C: refused by default (test_compressor_operating_envelope)
    row = hp.analyze_steady(T_tank_w=60, T0=0, Q_ref_tank=15000)
    pr, speed = _pr_and_speed(row)
    assert not hp.eta_cmp_isen.extrapolated(pr, speed)
    assert row["eta_cmp_isen [-]"] == pytest.approx(hp.eta_cmp_isen.raw(pr, speed), rel=1e-12)
    # Observed before and after the guard: PR 6.40, 35.4 rev/s, eta_is 0.452, discharge 200.4 C.
    # (0.449 and 201.6 C with 15 °C standard air on the outdoor coil: at 0 °C the fan moves
    # 5.5 % more air mass, the evaporating temperature is 0.07 K higher and the PR 6.40, not 6.41.)
    assert row["eta_cmp_isen [-]"] == pytest.approx(0.452, abs=0.002)
    assert row["T_ref_cmp_out [°C]"] == pytest.approx(200.4, abs=1.0)
    # With the default T_dis_max (R32 150 °C) the point is refused: tests/invariants C6.
    # The model reports no extrapolation for a caller-style callable, inside its box or not.
    assert row["efficiency_extrapolated"] is False


# --------------------------------------------------------------------------------------------------------------
# v2026-09-24: the correlations before the quadratic map
# --------------------------------------------------------------------------------------------------------------

SPEEDS = [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 70.0, 90.0, 110.0, 130.0, 150.0]
PRESSURE_RATIOS = [1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0]


def test_correlation_constants_match_their_archive() -> None:
    """The numbers in the record are the numbers in validation/coefficients/v2026-09-24/."""
    archive = json.loads((COEFFICIENTS / correlations.COEFFICIENT_VERSION / "coefficients.json").read_text())
    c = correlations.coefficients()
    assert archive["version"] == correlations.COEFFICIENT_VERSION == c["version"]
    for k in ("ETA_VOL_A", "ETA_VOL_B", "ETA_VOL_C"):
        assert c[k] == pytest.approx(archive["eta_vol"][k], abs=5e-6)
    for k in ("ETA_OI_A", "ETA_OI_B", "ETA_OI_C", "ETA_LEAK_C", "ETA_FLOW_D"):
        assert c[k] == pytest.approx(archive["eta_oi"][k], abs=5e-6)
    for k in ("ETA_LEAK_TWO_SIDED", "ETA_FLOW_TWO_SIDED", "ETA_OI_HAS_DRIVE"):
        assert c[k] == archive["eta_oi"][k]
    for k in ("ETA_EM_N0", "ETA_EM_P0"):
        assert c[k] == pytest.approx(archive["split"][k], abs=5e-6)
    assert c["ETA_EM_REF"] == pytest.approx(archive["split"]["ETA_EM_REF"], abs=5e-5)


@pytest.mark.parametrize("pressure_ratio", PRESSURE_RATIOS)
@pytest.mark.parametrize("rated", [40.0, 50.0, 60.0])
def test_delivered_duty_is_monotonic_in_speed(pressure_ratio: float, rated: float) -> None:
    """`solve_compressor_speed` brackets a root, so duty must rise with speed."""
    eta_vol = correlations.make_eta_vol(rated)
    previous = -math.inf
    for rps in SPEEDS:
        duty = rps * eta_vol(pressure_ratio, rps)
        assert duty > previous, f"duty fell between speeds at rps={rps}"
        previous = duty


def test_volumetric_speed_term_penalises_low_speed_only() -> None:
    """No bonus above rated speed: that direction is thin in the data."""
    eta_vol = correlations.make_eta_vol(60.0)
    at_rated = eta_vol(3.0, 60.0)
    assert eta_vol(3.0, 20.0) < at_rated
    assert eta_vol(3.0, 90.0) == pytest.approx(at_rated)
    # a quarter of rated speed costs a few points, as the Copeland set shows (~5-7 pp at n* 0.27)
    assert 0.02 < at_rated - eta_vol(3.0, 15.0) < 0.12


def test_speed_factor_is_one_at_rated_and_saturating() -> None:
    speed_factor_em = correlations.speed_factor_em
    assert speed_factor_em(1.0) == pytest.approx(1.0)
    assert speed_factor_em(0.5) < speed_factor_em(0.8) < 1.0
    # drive-only floor of the electro-mechanical loss (Ossorio & Navarro-Peris 2023):
    # 4-15 % at a quarter of rated speed, and no less than the drive alone shows
    drop = 1.0 - speed_factor_em(0.25)
    assert 0.04 < drop < 0.15, drop


def test_speed_factor_is_held_past_twice_rated_speed() -> None:
    """Above n* = 2 the data end; the factor is held, not followed."""
    speed_factor_em = correlations.speed_factor_em
    assert correlations.N_STAR_MAX == 2.0
    assert 1.0 < speed_factor_em(1.3) < speed_factor_em(2.0) < 1.0 + correlations.ETA_EM_N0
    assert speed_factor_em(2.5) == pytest.approx(speed_factor_em(2.0))


def test_leakage_term_scales_with_lift_and_is_one_sided() -> None:
    """The low-speed loss of the isentropic efficiency grows with (PR-1)/n* and awards no bonus above rated."""
    leakage_factor = correlations.leakage_factor
    assert correlations.ETA_LEAK_C > 0.0
    assert leakage_factor(2.0, 1.0) == pytest.approx(1.0)
    assert leakage_factor(2.0, 1.5) == pytest.approx(1.0)
    assert leakage_factor(4.5, 0.375) < leakage_factor(3.0, 0.375) < leakage_factor(2.0, 0.375) < 1.0
    # the Copeland median machine: about 8 % of the product at PR 3 and 0.375 n*, 14 % at PR 4.5
    assert 0.04 < 1.0 - leakage_factor(3.0, 0.375) < 0.12
    assert 0.08 < 1.0 - leakage_factor(4.5, 0.375) < 0.20


def test_electro_mechanical_level_follows_the_machines_rated_speed() -> None:
    for rated in (35.0, 40.0, 60.0, 80.0):
        eta = correlations.make_eta_em(rated)
        assert eta(3.0, rated) == pytest.approx(correlations.ETA_EM_REF)
        assert eta(3.0, rated * 0.5) < eta(3.0, rated)
        # motor load: lower lift, lower torque, lower motor efficiency
        assert eta(1.5, rated) < eta(3.0, rated) <= eta(5.0, rated) + 1e-9
    assert correlations.eta_em_default(3.0, correlations.RPS_REF) == pytest.approx(correlations.ETA_EM_REF)


def test_isentropic_efficiency_peaks_near_the_built_in_volume_ratio() -> None:
    """g(PR) = A - B PR - C/PR peaks at sqrt(C/B); for the fitted set that is near PR 2.9."""
    eta_isen_default = correlations.eta_isen_default
    grid = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0]
    values = {pr: eta_isen_default(pr) for pr in grid}
    peak = max(values, key=lambda k: values[k])
    assert 2.0 <= peak <= 4.0, values
    assert values[8.0] < values[peak]
    assert values[1.5] < values[peak]
    # the split reproduces the fitted product at every (PR, n*)
    for rated in (40.0, 50.0, 60.0):
        eta_isen, eta_em = correlations.make_eta_isen(rated), correlations.make_eta_em(rated)
        for pr in grid:
            for n_star in (0.3, 0.5, 1.0, 1.5):
                isen = eta_isen(pr, rated * n_star)
                if isen <= correlations.ETA_ISEN_FLOOR + 1e-12:  # held, not followed: the product is not reproduced
                    continue
                product = correlations.eta_oi_product(pr, n_star)
                assert isen * eta_em(pr, rated * n_star) == pytest.approx(product, rel=1e-9)
    # with rps omitted the rated speed is assumed
    assert eta_isen_default(3.0) == pytest.approx(eta_isen_default(3.0, correlations.RPS_REF))


def test_isentropic_efficiency_carries_the_low_speed_leakage() -> None:
    """All three efficiencies depend on speed (plan v3); the lift x speed term sits in eta_isen."""
    eta_isen = correlations.make_eta_isen(60.0)
    assert eta_isen(4.5, 15.0) < eta_isen(4.5, 30.0) < eta_isen(4.5, 60.0)
    # and the penalty is larger at high lift than at low lift
    low_lift = eta_isen(2.0, 60.0) - eta_isen(2.0, 15.0)
    high_lift = eta_isen(4.5, 60.0) - eta_isen(4.5, 15.0)
    assert high_lift > low_lift > -0.03


def test_correlations_stay_physical_across_the_envelope() -> None:
    for pressure_ratio in PRESSURE_RATIOS:
        assert 0.0 < correlations.eta_isen_default(pressure_ratio) <= 1.0
        for rps in SPEEDS:
            assert 0.0 < correlations.eta_vol_default(pressure_ratio, rps) <= 1.0
            assert 0.0 < correlations.eta_em_default(pressure_ratio, rps) <= 1.0
            assert 0.0 < correlations.eta_isen_default(pressure_ratio, rps) <= 1.0


@pytest.mark.parametrize("pressure_ratio", PRESSURE_RATIOS)
@pytest.mark.parametrize("kappa", [0.1, 0.3, 0.6])
def test_heating_duty_is_monotonic_in_speed_with_the_speed_dependent_isentropic_efficiency(
    pressure_ratio: float, kappa: float
) -> None:
    """The boiler residual is rps * eta_vol * (dh_liq + dh_is / eta_isen); with eta_isen falling at low speed
    it must still rise with speed, or the bracketing search loses its root."""
    rated = 40.0
    eta_vol, eta_isen = correlations.make_eta_vol(rated), correlations.make_eta_isen(rated)
    previous = -math.inf
    for rps in SPEEDS:
        duty = rps * eta_vol(pressure_ratio, rps) * (1.0 + kappa / eta_isen(pressure_ratio, rps))
        assert duty > previous, f"heating duty fell between speeds at rps={rps}"
        previous = duty
