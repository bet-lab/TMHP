"""Guards on the shared compressor-efficiency defaults.

Two of these are correctness gates rather than characterisation:

* the speed search assumes the delivered duty rises monotonically with speed,
  and putting a speed term into the volumetric efficiency is exactly the change
  that could break that assumption, because the volumetric efficiency is
  evaluated inside the residual;
* the part-load evidence says certified COP rises toward light load, so a
  default set that manufactured a low-load roll-over at fixed temperatures
  would be contradicting the data it is supposed to encode.
"""

from __future__ import annotations

import math

import pytest

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp.compressor_efficiency import (
    RPS_FIT_MAX,
    RPS_FIT_MIN,
    RPS_REF,
    eta_em_default,
    eta_isen_default,
    eta_vol_default,
    make_eta_em,
    relative_speed_shape_em,
    speed_shape_em,
)

SPEEDS = [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 70.0, 90.0, 110.0, 130.0, 150.0]
PRESSURE_RATIOS = [1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0]


@pytest.mark.parametrize("pressure_ratio", PRESSURE_RATIOS)
def test_delivered_duty_is_monotonic_in_speed(pressure_ratio: float) -> None:
    """`solve_compressor_speed` brackets a root, so duty must rise with speed.

    Delivered mass flow is proportional to ``rps * eta_vol(pr, rps)``. The
    leakage term subtracts a quantity proportional to ``1/rps``, so the product
    keeps a positive derivative -- but that is an argument, and this is the
    check.
    """
    previous = -math.inf
    for rps in SPEEDS:
        duty = rps * eta_vol_default(pressure_ratio, rps)
        assert duty > previous, f"duty fell between speeds at rps={rps}"
        previous = duty


def test_volumetric_speed_term_penalises_low_speed_only() -> None:
    """No bonus above the reference speed: that direction is unmeasured."""
    at_ref = eta_vol_default(3.0, RPS_REF)
    assert eta_vol_default(3.0, 30.0) < at_ref
    assert eta_vol_default(3.0, 90.0) == pytest.approx(at_ref)
    assert eta_vol_default(3.0) == pytest.approx(at_ref)


def test_electro_mechanical_shape_peaks_inside_the_measured_range() -> None:
    """Guth's compressor efficiency peaks near 70 rev/s and falls both ways."""
    shape = {rps: speed_shape_em(rps) for rps in (20.0, 40.0, 60.0, 70.0, 80.0, 100.0)}
    peak = max(shape, key=lambda k: shape[k])
    assert 60.0 <= peak <= 80.0, shape
    assert shape[20.0] < shape[peak]
    assert shape[100.0] < shape[peak]
    assert speed_shape_em(RPS_REF) == pytest.approx(1.0)


def test_speed_shape_peak_follows_the_machines_rated_speed() -> None:
    """The drive is sized for its own machine, so the peak must move with it.

    Anchoring the peak at an absolute speed would put every air-to-water unit,
    which is rated near 40 rev/s, permanently on the falling side of a curve
    measured on a machine rated near 70.
    """
    for rated in (35.0, 40.0, 60.0, 80.0):
        eta = make_eta_em(rated)
        assert eta(2.5, rated) == pytest.approx(0.80)
        assert eta(2.5, rated * 0.5) < eta(2.5, rated)
        assert eta(2.5, rated * 1.6) < eta(2.5, rated)
    assert relative_speed_shape_em(1.0) == pytest.approx(1.0)


def test_speed_shape_is_held_outside_the_fitted_range() -> None:
    """A polynomial fitted over 15-110 Hz must not be trusted past its ends."""
    assert speed_shape_em(2.0) == pytest.approx(speed_shape_em(RPS_FIT_MIN))
    assert speed_shape_em(400.0) == pytest.approx(speed_shape_em(RPS_FIT_MAX))


def test_low_speed_penalty_matches_the_published_magnitude() -> None:
    """Roughly 8 % of electro-mechanical efficiency lost from 50 to 15 rev/s.

    Ossorio & Navarro-Peris measure 6-11 % for the drive alone across three
    inverters; the combined figure must sit in that neighbourhood and on the
    larger side, since it also carries motor and bearing losses.
    """
    drop = 1.0 - eta_em_default(2.5, 15.0) / eta_em_default(2.5, RPS_REF)
    assert 0.05 < drop < 0.25, drop


def test_efficiencies_stay_physical_across_the_envelope() -> None:
    for pressure_ratio in PRESSURE_RATIOS:
        assert 0.0 < eta_isen_default(pressure_ratio) <= 1.0
        for rps in SPEEDS:
            assert 0.0 < eta_vol_default(pressure_ratio, rps) <= 1.0
            assert 0.0 < eta_em_default(pressure_ratio, rps) <= 1.0


def test_ashp_no_longer_defaults_to_an_ideal_compressor() -> None:
    model = AirSourceHeatPump(hp_capacity=3500.0, ref="R32")
    assert callable(model.eta_cmp_isen)
    assert callable(model.eta_cmp_vol)
    assert model.eta_cmp_isen(3.0) < 1.0
    assert model.eta_cmp_vol(3.0, 40.0) < 1.0


def test_part_load_cop_rises_while_the_compressor_can_still_modulate() -> None:
    """At fixed temperatures COP must rise as load falls, until the speed floor.

    Lower load means heat exchangers that are large relative to the duty and a
    smaller lift, so COP improves -- the same reason certified declared COP
    improves toward the light test points. The low-speed compressor penalty
    eats into that rise; it must not reverse it. Below the speed floor the
    machine can no longer follow the load and the curve is allowed to turn
    over, because that is the modulation limit rather than the correlations.
    """
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")
    rising = []
    for fraction in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4):
        result = model.analyze_steady(T_tank_w=42.5, T0=7.0, Q_ref_tank=9000.0 * fraction, return_dict=True)
        assert isinstance(result, dict)
        assert result.get("failure_reason", "none") == "none"
        assert result.get("capacity_clamped") is None, "floor reached earlier than expected"
        rising.append((fraction, float(result["cop_sys [-]"])))

    for (hi_frac, hi_cop), (lo_frac, lo_cop) in zip(rising, rising[1:], strict=False):
        assert lo_cop > hi_cop, (
            f"COP fell from {hi_cop:.3f} at PLR {hi_frac} to {lo_cop:.3f} at PLR {lo_frac} "
            "while the compressor was still free to modulate"
        )


def test_low_load_turnover_is_the_speed_floor_not_the_correlations() -> None:
    """Whatever happens at very low load must be attributable to the floor.

    This is the claim the documentation makes, so it is worth a gate: the
    compressor is at its minimum speed wherever the part-load curve stops
    improving.
    """
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")
    curve = []
    for fraction in (0.5, 0.4, 0.35, 0.3, 0.25, 0.2):
        result = model.analyze_steady(T_tank_w=42.5, T0=7.0, Q_ref_tank=9000.0 * fraction, return_dict=True)
        assert isinstance(result, dict)
        curve.append((fraction, float(result["cop_sys [-]"]), result["cmp_rpm [rpm]"] / 60.0))

    peak_cop = max(cop for _, cop, _ in curve)
    for fraction, cop, rps in curve:
        # A plateau is fine: the heat-exchanger benefit and the low-speed
        # compressor penalty cancel over a broad range, and a fraction of a
        # percent either way is not a turnover. A *material* loss of
        # performance has to be the modulation limit.
        if cop < peak_cop * 0.99:
            assert rps == pytest.approx(model.rps_min, rel=1e-6), (
                f"COP dropped materially at PLR {fraction} while the compressor "
                f"was at {rps:.1f} rev/s, above its {model.rps_min:.1f} rev/s floor -- "
                "that would mean the correlations, not the modulation limit, "
                "caused the turnover"
            )


def test_en14825_trajectory_cop_rises_monotonically() -> None:
    """Along the certified test points, load and lift fall together.

    Heat Pump Keymark declared data rises monotonically from A to D for 99 % of
    air-to-water machines. EN 14825 lowers the required load and the flow
    temperature together as outdoor temperature rises, so this trajectory is a
    different curve from the fixed-temperature sweep above -- and it is the one
    that has to match the certified shape.
    """
    # Average climate, low-temperature application: (outdoor degC, leaving
    # water degC, part load of the design heating demand).
    points = [("A", -7.0, 34.0, 0.88), ("B", 2.0, 30.0, 0.54), ("C", 7.0, 27.0, 0.35), ("D", 12.0, 24.0, 0.15)]
    design_load = 6000.0
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")

    cops = []
    for label, t_outdoor, lwt, load_ratio in points:
        result = model.analyze_steady(
            T_tank_w=lwt - 2.5,
            T0=t_outdoor,
            Q_ref_tank=max(design_load * load_ratio, 0.0),
            return_dict=True,
        )
        assert isinstance(result, dict)
        assert result.get("failure_reason", "none") == "none", (label, result.get("failure_reason"))
        cops.append((label, float(result["cop_sys [-]"])))

    for (lo_label, lo_cop), (hi_label, hi_cop) in zip(cops, cops[1:], strict=False):
        assert hi_cop > lo_cop, f"COP fell from point {lo_label} ({lo_cop:.2f}) to {hi_label} ({hi_cop:.2f})"


def test_en14825_gradient_matches_the_certified_population() -> None:
    """A→D gain must land inside what certified machines declare.

    Monotonicity alone is weak: a trajectory that rises by 5 % from A to D
    passes it and describes no real machine. The ratio ``COP(D)/COP(A)`` is the
    part that separates a plausible part-load trend from a flat one, and the
    Heat Pump Keymark declared rows give its distribution directly -- p10 2.02,
    p90 3.13 over 9,062 low-temperature records (see
    ``validation.analysis.en14825_trend``).
    """
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

    gradient = cop["D"] / cop["A"]
    assert 2.02 <= gradient <= 3.13, f"COP(D)/COP(A) = {gradient:.2f}, outside the certified p10-p90 of 2.02-3.13"


def test_outdoor_fan_turndown_bound_is_where_the_documentation_says() -> None:
    """The part-load pages attribute the sub-floor drop to this one number.

    ``calc_HX_perf_for_target_heat`` lets the outdoor fan turn down to 5 % of
    rated flow, and below the compressor speed floor the model uses that range
    as its capacity-modulation handle -- which is why the modelled curve below
    roughly 30 % of nominal is documented as not validated. The bound carries no
    source; this test exists so that raising it is a deliberate act with the
    documentation updated alongside, rather than a silent change to a published
    limitation.
    """
    import inspect

    from tmhp.enex_functions import calc_HX_perf_for_target_heat

    source = inspect.getsource(calc_HX_perf_for_target_heat)
    assert "dV_fan_rated * 0.05" in source, (
        "the outdoor-fan turndown bound moved; validation/part-load.rst and "
        "validation/defaults.rst both quote 5 % of rated flow and the air-side "
        "temperature drop it produces"
    )
