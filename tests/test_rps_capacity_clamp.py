"""Compressor speed-envelope clamping.

A duty request outside the band the compressor can deliver between ``rps_min``
and ``rps_max`` is an ordinary operating situation, not a solver failure. These
tests pin the split: out-of-band requests clamp onto the nearest speed bound and
report ``converged=True`` with a ``capacity_clamped`` tag; only a sign change
that the root find fails to resolve counts as non-convergence.
"""

from __future__ import annotations

import pytest

from tmhp import AirSourceHeatPumpBoiler
from tmhp.compressor_speed import (
    CAPACITY_CLAMPED_MAX,
    CAPACITY_CLAMPED_MIN,
    solve_compressor_speed,
)

RPS_MIN = 15.0
RPS_MAX = 150.0


# --------------------------------------------------------------------------
# Shared helper — every heat-pump model routes its speed search through this.
# --------------------------------------------------------------------------


def test_request_below_floor_clamps_to_rps_min():
    """Residual positive across the interval -> the machine's minimum is the answer."""
    rps, converged, clamped = solve_compressor_speed(lambda r: 0.1 * r + 5.0, RPS_MIN, RPS_MAX)
    assert rps == RPS_MIN
    assert converged is True
    assert clamped == CAPACITY_CLAMPED_MIN


def test_request_above_ceiling_clamps_to_rps_max():
    """Residual negative across the interval -> the machine's maximum is the answer."""
    rps, converged, clamped = solve_compressor_speed(lambda r: 0.1 * r - 500.0, RPS_MIN, RPS_MAX)
    assert rps == RPS_MAX
    assert converged is True
    assert clamped == CAPACITY_CLAMPED_MAX


def test_request_inside_band_is_solved_exactly():
    rps, converged, clamped = solve_compressor_speed(lambda r: r - 42.0, RPS_MIN, RPS_MAX)
    assert rps == pytest.approx(42.0)
    assert converged is True
    assert clamped is None


@pytest.mark.parametrize("bound", [RPS_MIN, RPS_MAX])
def test_root_exactly_on_a_bound_is_not_reported_as_clamped(bound):
    """A request met exactly at a bound is met, not clamped."""
    rps, converged, clamped = solve_compressor_speed(lambda r: r - bound, RPS_MIN, RPS_MAX)
    assert rps == pytest.approx(bound)
    assert converged is True
    assert clamped is None


def test_solver_breakdown_inside_a_valid_bracket_reports_non_convergence():
    """The residual changes sign, so a root exists; failing to find it is real."""

    def residual(r: float) -> float:
        if r <= RPS_MIN:
            return -1.0
        if r >= RPS_MAX:
            return 1.0
        raise ValueError("solver probe failed")

    rps, converged, clamped = solve_compressor_speed(residual, RPS_MIN, RPS_MAX)
    assert converged is False
    assert clamped is None
    assert rps in (RPS_MIN, RPS_MAX)


# --------------------------------------------------------------------------
# End-to-end through the model that motivated the change.
# --------------------------------------------------------------------------


@pytest.fixture
def hp() -> AirSourceHeatPumpBoiler:
    return AirSourceHeatPumpBoiler(ref="R32", hp_capacity=15000.0)


def test_default_speed_floor_is_15_rps(hp):
    assert hp.rps_min == 15.0
    # Derived from the rated speed: min(2 x 34.5, 120) rev/s for this R32 15 kW unit.
    assert hp.rps_max == pytest.approx(2.0 * hp.rps_rated)


def test_duty_below_minimum_capacity_returns_that_minimum(hp):
    """Previously this reported hx_not_converged and callers discarded the row."""
    tiny = hp.analyze_steady(T_tank_w=54.0, T0=7.0, Q_ref_tank=50.0, m_dot_w=0.5)

    assert tiny["converged"] is True
    assert tiny["failure_reason"] == "none"
    assert tiny["capacity_clamped"] == CAPACITY_CLAMPED_MIN
    assert tiny["Q_request [W]"] == pytest.approx(50.0)
    # The machine's floor is well above the request: it cycles at that floor,
    # and the row is the mean over the cycling period (tmhp.cycling).
    assert tiny["Q_min [W]"] > 1000.0
    assert tiny["Q_ref_tank [W]"] == pytest.approx(50.0, rel=1e-12)
    assert 0.0 < tiny["runtime_fraction [-]"] < 0.05
    assert tiny["E_cmp [W]"] > 0.0


def test_duty_inside_the_band_is_met_and_not_flagged(hp):
    """A duty the machine can modulate to is delivered exactly and unflagged."""
    served = hp.analyze_steady(T_tank_w=54.0, T0=7.0, Q_ref_tank=14000.0, m_dot_w=0.5)

    assert served["converged"] is True
    assert served["capacity_clamped"] is None
    assert served["Q_ref_tank [W]"] == pytest.approx(14000.0, rel=1e-6)


def test_any_request_below_the_floor_runs_at_the_floor_speed(hp):
    """Requests under the floor all land on rps_min, whatever the shortfall.

    The floor duty (``Q_min [W]``) still shifts marginally between them
    because the condensing approach is set from the *requested* duty before
    the speed search; the operating speed is the invariant. The delivered
    heat is the request in both: the rows are means over the cycling period.
    """
    a = hp.analyze_steady(T_tank_w=54.0, T0=7.0, Q_ref_tank=50.0, m_dot_w=0.5)
    b = hp.analyze_steady(T_tank_w=54.0, T0=7.0, Q_ref_tank=200.0, m_dot_w=0.5)

    assert a["capacity_clamped"] == b["capacity_clamped"] == CAPACITY_CLAMPED_MIN
    assert a["cmp_rpm [rpm]"] == pytest.approx(60.0 * hp.rps_min, rel=1e-9)
    assert b["cmp_rpm [rpm]"] == pytest.approx(60.0 * hp.rps_min, rel=1e-9)
    assert a["Q_min [W]"] == pytest.approx(b["Q_min [W]"], rel=1e-3)
    assert (a["Q_ref_tank [W]"], b["Q_ref_tank [W]"]) == pytest.approx((50.0, 200.0), rel=1e-12)
