"""Guards on the shared compressor-efficiency defaults.

Two of these are correctness gates rather than characterisation:

* the speed search assumes the delivered duty rises monotonically with speed,
  and the speed term in the volumetric efficiency is evaluated inside the
  residual, so it must never bend the duty back down;
* at fixed temperatures the assembled model's COP is the outcome of heat
  exchangers unloading against the compressor's measured low-speed losses; the
  correlations prescribe no COP shape, so the guard is that both effects are
  present and of the size the compressor data show, not that COP rises.

The rest pin the frozen coefficient version to its archive and keep the
fitted shapes where the standalone-compressor data put them.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp.compressor_efficiency import (
    COEFFICIENT_VERSION,
    ETA_EM_N0,
    ETA_EM_REF,
    ETA_ISEN_FLOOR,
    ETA_LEAK_C,
    RPS_REF,
    coefficients,
    eta_em_default,
    eta_isen_default,
    eta_oi_product,
    eta_vol_default,
    leakage_factor,
    make_eta_em,
    make_eta_isen,
    make_eta_vol,
    speed_factor_em,
)

REPO = Path(__file__).resolve().parents[1]
SPEEDS = [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 70.0, 90.0, 110.0, 130.0, 150.0]
PRESSURE_RATIOS = [1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0]


def test_coefficient_block_matches_the_archive() -> None:
    """The numbers in the module are the numbers in validation/coefficients/<version>/."""
    archive = json.loads((REPO / "validation" / "coefficients" / COEFFICIENT_VERSION / "coefficients.json").read_text())
    c = coefficients()
    assert archive["version"] == COEFFICIENT_VERSION
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
    eta_vol = make_eta_vol(rated)
    previous = -math.inf
    for rps in SPEEDS:
        duty = rps * eta_vol(pressure_ratio, rps)
        assert duty > previous, f"duty fell between speeds at rps={rps}"
        previous = duty


def test_volumetric_speed_term_penalises_low_speed_only() -> None:
    """No bonus above rated speed: that direction is thin in the data."""
    eta_vol = make_eta_vol(60.0)
    at_rated = eta_vol(3.0, 60.0)
    assert eta_vol(3.0, 20.0) < at_rated
    assert eta_vol(3.0, 90.0) == pytest.approx(at_rated)
    # a quarter of rated speed costs a few points, as the Copeland set shows (~5-7 pp at n* 0.27)
    assert 0.02 < at_rated - eta_vol(3.0, 15.0) < 0.12


def test_speed_factor_is_one_at_rated_and_saturating() -> None:
    assert speed_factor_em(1.0) == pytest.approx(1.0)
    assert speed_factor_em(0.5) < speed_factor_em(0.8) < 1.0
    # drive-only floor of the electro-mechanical loss (Ossorio & Navarro-Peris 2023):
    # 4-15 % at a quarter of rated speed, and no less than the drive alone shows
    drop = 1.0 - speed_factor_em(0.25)
    assert 0.04 < drop < 0.15, drop


def test_speed_factor_is_held_past_twice_rated_speed() -> None:
    """Above n* = 2 the data end; the factor is held, not followed."""
    from tmhp.compressor_efficiency import N_STAR_MAX

    assert N_STAR_MAX == 2.0
    assert 1.0 < speed_factor_em(1.3) < speed_factor_em(2.0) < 1.0 + ETA_EM_N0
    assert speed_factor_em(2.5) == pytest.approx(speed_factor_em(2.0))


def test_leakage_term_scales_with_lift_and_is_one_sided() -> None:
    """The low-speed loss of the isentropic efficiency grows with (PR-1)/n* and awards no bonus above rated."""
    assert ETA_LEAK_C > 0.0
    assert leakage_factor(2.0, 1.0) == pytest.approx(1.0)
    assert leakage_factor(2.0, 1.5) == pytest.approx(1.0)
    assert leakage_factor(4.5, 0.375) < leakage_factor(3.0, 0.375) < leakage_factor(2.0, 0.375) < 1.0
    # the Copeland median machine: about 8 % of the product at PR 3 and 0.375 n*, 14 % at PR 4.5
    assert 0.04 < 1.0 - leakage_factor(3.0, 0.375) < 0.12
    assert 0.08 < 1.0 - leakage_factor(4.5, 0.375) < 0.20


def test_electro_mechanical_level_follows_the_machines_rated_speed() -> None:
    for rated in (35.0, 40.0, 60.0, 80.0):
        eta = make_eta_em(rated)
        assert eta(3.0, rated) == pytest.approx(ETA_EM_REF)
        assert eta(3.0, rated * 0.5) < eta(3.0, rated)
        # motor load: lower lift, lower torque, lower motor efficiency
        assert eta(1.5, rated) < eta(3.0, rated) <= eta(5.0, rated) + 1e-9
    assert eta_em_default(3.0, RPS_REF) == pytest.approx(ETA_EM_REF)


def test_isentropic_efficiency_peaks_near_the_built_in_volume_ratio() -> None:
    """g(PR) = A - B PR - C/PR peaks at sqrt(C/B); for the fitted set that is near PR 2.9."""
    grid = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0]
    values = {pr: eta_isen_default(pr) for pr in grid}
    peak = max(values, key=lambda k: values[k])
    assert 2.0 <= peak <= 4.0, values
    assert values[8.0] < values[peak]
    assert values[1.5] < values[peak]
    # the split reproduces the fitted product at every (PR, n*)
    for rated in (40.0, 50.0, 60.0):
        eta_isen, eta_em = make_eta_isen(rated), make_eta_em(rated)
        for pr in grid:
            for n_star in (0.3, 0.5, 1.0, 1.5):
                isen = eta_isen(pr, rated * n_star)
                if isen <= ETA_ISEN_FLOOR + 1e-12:  # held, not followed: the product is not reproduced there
                    continue
                assert isen * eta_em(pr, rated * n_star) == pytest.approx(eta_oi_product(pr, n_star), rel=1e-9)
    # with rps omitted the rated speed is assumed
    assert eta_isen_default(3.0) == pytest.approx(eta_isen_default(3.0, RPS_REF))


def test_isentropic_efficiency_carries_the_low_speed_leakage() -> None:
    """All three efficiencies depend on speed (plan v3); the lift x speed term sits in eta_isen."""
    eta_isen = make_eta_isen(60.0)
    assert eta_isen(4.5, 15.0) < eta_isen(4.5, 30.0) < eta_isen(4.5, 60.0)
    # and the penalty is larger at high lift than at low lift
    low_lift = eta_isen(2.0, 60.0) - eta_isen(2.0, 15.0)
    high_lift = eta_isen(4.5, 60.0) - eta_isen(4.5, 15.0)
    assert high_lift > low_lift > -0.03


def test_efficiencies_stay_physical_across_the_envelope() -> None:
    for pressure_ratio in PRESSURE_RATIOS:
        assert 0.0 < eta_isen_default(pressure_ratio) <= 1.0
        for rps in SPEEDS:
            assert 0.0 < eta_vol_default(pressure_ratio, rps) <= 1.0
            assert 0.0 < eta_em_default(pressure_ratio, rps) <= 1.0
            assert 0.0 < eta_isen_default(pressure_ratio, rps) <= 1.0


@pytest.mark.parametrize("pressure_ratio", PRESSURE_RATIOS)
@pytest.mark.parametrize("kappa", [0.1, 0.3, 0.6])
def test_heating_duty_is_monotonic_in_speed_with_the_speed_dependent_isentropic_efficiency(
    pressure_ratio: float, kappa: float
) -> None:
    """The boiler residual is rps * eta_vol * (dh_liq + dh_is / eta_isen); with eta_isen falling at low speed
    it must still rise with speed, or the bracketing search loses its root."""
    rated = 40.0
    eta_vol, eta_isen = make_eta_vol(rated), make_eta_isen(rated)
    previous = -math.inf
    for rps in SPEEDS:
        duty = rps * eta_vol(pressure_ratio, rps) * (1.0 + kappa / eta_isen(pressure_ratio, rps))
        assert duty > previous, f"heating duty fell between speeds at rps={rps}"
        previous = duty


def test_ashp_no_longer_defaults_to_an_ideal_compressor() -> None:
    model = AirSourceHeatPump(hp_capacity=3500.0, ref="R32")
    assert callable(model.eta_cmp_isen)
    assert callable(model.eta_cmp_vol)
    assert model.eta_cmp_isen(3.0, 40.0) < 1.0
    assert model.eta_cmp_vol(3.0, 40.0) < 1.0
    assert model.rps_rated == pytest.approx(60.0)


def test_models_report_the_compressor_internal_state() -> None:
    """The chain N -> m_dot -> lift -> PR -> W is what part-load validation reads."""
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")
    result = model.analyze_steady(T_tank_w=42.5, T0=7.0, Q_ref_tank=9000.0, return_dict=True)
    assert isinstance(result, dict)
    for key in ("n_star [-]", "pr_cmp [-]", "eta_cmp_vol [-]", "eta_cmp_isen [-]", "eta_cmp [-]"):
        assert key in result, key
    assert result["n_star [-]"] == pytest.approx(result["cmp_rpm [rpm]"] / 60.0 / model.rps_rated)
    assert result["eta_cmp_isen [-]"] * result["eta_cmp [-]"] == pytest.approx(
        eta_oi_product(result["pr_cmp [-]"], result["n_star [-]"]), rel=1e-6
    )


def test_part_load_cop_is_heat_exchanger_gain_against_compressor_loss() -> None:
    """At fixed temperatures the model neither prescribes a rising COP nor a roll-over.

    Two things must hold while the compressor can still modulate: with the three
    efficiencies frozen at their rated values COP rises as load falls (heat
    exchangers unload), and with the shipped correlations COP sits below that
    curve by the compressor's measured low-speed loss -- a few per cent at half
    load, more at the speed floor -- without collapsing.
    """
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")
    rated = model.analyze_steady(T_tank_w=42.5, T0=7.0, Q_ref_tank=9000.0, return_dict=True)
    assert isinstance(rated, dict)
    frozen = AirSourceHeatPumpBoiler(
        hp_capacity=9000.0,
        ref="R32",
        eta_cmp_vol=float(rated["eta_cmp_vol [-]"]),
        eta_cmp_isen=float(rated["eta_cmp_isen [-]"]),
        eta_cmp=float(rated["eta_cmp [-]"]),
    )
    hx_only, shipped = [], []
    for fraction in (1.0, 0.8, 0.6, 0.5, 0.4):
        for m, store in ((frozen, hx_only), (model, shipped)):
            result = m.analyze_steady(T_tank_w=42.5, T0=7.0, Q_ref_tank=9000.0 * fraction, return_dict=True)
            assert isinstance(result, dict)
            assert result.get("failure_reason", "none") == "none"
            assert result.get("capacity_clamped") is None, "floor reached earlier than expected"
            store.append(float(result["cop_sys [-]"]))
    assert all(b > a for a, b in zip(hx_only, hx_only[1:], strict=False)), hx_only
    assert shipped[0] == pytest.approx(hx_only[0], rel=1e-6)
    gap = [(h - s) / h for h, s in zip(hx_only, shipped, strict=True)]
    assert all(g >= -1e-9 for g in gap), gap
    assert 0.01 < gap[-1] < 0.20, gap  # the compressor loss at 40 % load is real but not a cliff
    assert shipped[-1] > 0.8 * shipped[0], shipped  # no roll-over below 80 % of the rated COP


def test_low_load_operation_meets_the_request_and_keeps_the_coil_fed() -> None:
    """Below the speed floor the model must not starve the outdoor coil to shave power.

    The optimiser picks the operating point by specific energy among candidates
    that meet the request; the outdoor fan must stay well above its bound and
    the air-side temperature drop within what real coils show (7.7 K max across
    1,414 catalogue coils).
    """
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")
    for fraction in (0.5, 0.4, 0.3, 0.25, 0.2):
        result = model.analyze_steady(T_tank_w=42.5, T0=7.0, Q_ref_tank=9000.0 * fraction, return_dict=True)
        assert isinstance(result, dict)
        assert result.get("failure_reason", "none") == "none", (fraction, result.get("failure_reason"))
        request = 9000.0 * fraction
        assert result["Q_ref_tank [W]"] >= request * (1 - 0.02), (fraction, result["Q_ref_tank [W]"])
        assert result["dV_ou_a [m3/s]"] / model.dV_fan_a_rated >= 0.30, (
            fraction,
            result["dV_ou_a [m3/s]"] / model.dV_fan_a_rated,
        )
        assert result["T_ou_a_in [°C]"] - result["T_ou_a_out [°C]"] <= 8.0, (
            fraction,
            result["T_ou_a_in [°C]"] - result["T_ou_a_out [°C]"],
        )


def test_air_to_air_low_load_keeps_the_coil_fed_too() -> None:
    """Same guard for the air-to-air model: no starved coil at the speed floor."""
    model = AirSourceHeatPump(hp_capacity=3500.0, ref="R32")
    for fraction in (0.4, 0.3, 0.2):
        result = model.analyze_steady(Q_r_iu=-3500.0 * fraction, T0=7.0, T_a_room=20.0, return_dict=True, verbose=False)
        assert isinstance(result, dict)
        assert result.get("failure_reason", "none") == "none", (fraction, result.get("failure_reason"))
        assert abs(result["Q_ref_iu [W]"]) >= 3500.0 * fraction * (1 - 0.02)
        assert result["dV_ou_a [m3/s]"] / model.dV_ou_fan_a_rated >= 0.25, fraction
        assert abs(result["T_ou_a_in [°C]"] - result["T_ou_a_out [°C]"]) <= 8.0, fraction


def test_en14825_trajectory_cop_rises_monotonically() -> None:
    """Along the certified test points, load and lift fall together."""
    points = [("A", -7.0, 34.0, 0.88), ("B", 2.0, 30.0, 0.54), ("C", 7.0, 27.0, 0.35), ("D", 12.0, 24.0, 0.15)]
    design_load = 6000.0
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")

    cops = []
    for label, t_outdoor, lwt, load_ratio in points:
        result = model.analyze_steady(
            T_tank_w=lwt - 2.5, T0=t_outdoor, Q_ref_tank=max(design_load * load_ratio, 0.0), return_dict=True
        )
        assert isinstance(result, dict)
        assert result.get("failure_reason", "none") == "none", (label, result.get("failure_reason"))
        cops.append((label, float(result["cop_sys [-]"])))

    for (lo_label, lo_cop), (hi_label, hi_cop) in zip(cops, cops[1:], strict=False):
        assert hi_cop > lo_cop, f"COP fell from point {lo_label} ({lo_cop:.2f}) to {hi_label} ({hi_cop:.2f})"


def _certified_gradient_band() -> tuple[float, float]:
    """p10-p90 of COP(D)/COP(A) over the Keymark low-temperature records, read from the data file."""
    import pandas as pd

    df = pd.read_csv(REPO / "validation" / "data" / "keymark_en14825_declared.csv")
    low = df[df.application == "low"].dropna(subset=["cop_A", "cop_D"])
    ratio = low.cop_D / low.cop_A
    return float(ratio.quantile(0.10)), float(ratio.quantile(0.90))


def test_en14825_gradient_matches_the_certified_population() -> None:
    """A→D gain must land inside what certified machines declare (p10-p90, read from the data)."""
    points = [("A", -7.0, 34.0, 0.88), ("D", 12.0, 24.0, 0.15)]
    design_load = 6000.0
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")

    cop = {}
    for label, t_outdoor, lwt, load_ratio in points:
        result = model.analyze_steady(
            T_tank_w=lwt - 2.5, T0=t_outdoor, Q_ref_tank=design_load * load_ratio, return_dict=True
        )
        assert isinstance(result, dict)
        assert result.get("failure_reason", "none") == "none", label
        cop[label] = float(result["cop_sys [-]"])

    lo, hi = _certified_gradient_band()
    gradient = cop["D"] / cop["A"]
    assert lo <= gradient <= hi, f"COP(D)/COP(A) = {gradient:.2f}, outside the certified p10-p90 of {lo:.2f}-{hi:.2f}"


def test_outdoor_fan_turndown_bound_is_documented() -> None:
    """``calc_HX_perf_for_target_heat`` lets the outdoor fan turn down to 5 % of rated flow.

    The bound carries no source. Since the operating-point optimiser now scores
    candidates by specific energy, the bound is no longer the branch the model
    selects at low load (see the test above), but it is still the search bracket;
    changing it should be a deliberate act with the documentation updated.
    """
    import inspect

    from tmhp.enex_functions import calc_HX_perf_for_target_heat

    source = inspect.getsource(calc_HX_perf_for_target_heat)
    assert "dV_fan_rated * 0.05" in source
