"""Operating-point objective shared by the air-source models."""

import math

import pytest

from tmhp._opt_utils import DEFAULT_SHORTFALL_TOLERANCE, PENALTY, specific_energy_objective


def test_equals_total_power_when_the_request_is_met():
    assert specific_energy_objective(500.0, 2000.0, 2000.0) == pytest.approx(500.0)


def test_floor_candidates_rank_by_cost_per_delivered_heat():
    # At the speed floor both candidates over-deliver; the starved one draws
    # less power but costs more per unit of heat, and must rank worse.
    healthy = specific_energy_objective(600.0, 3000.0, 2000.0, math.inf)
    starved = specific_energy_objective(560.0, 2400.0, 2000.0, math.inf)
    assert healthy < starved


def test_surplus_is_credited_only_up_to_what_the_sink_can_store():
    assert specific_energy_objective(600.0, 3000.0, 2000.0, 0.0) == pytest.approx(600.0)
    assert specific_energy_objective(600.0, 3000.0, 2000.0, 500.0) == pytest.approx(600.0 / 2500.0 * 2000.0)


def test_shortfall_beyond_tolerance_is_infeasible_and_graded():
    small = specific_energy_objective(500.0, 2000.0 * (1 - DEFAULT_SHORTFALL_TOLERANCE / 2), 2000.0)
    assert small < PENALTY
    a = specific_energy_objective(500.0, 1800.0, 2000.0)
    b = specific_energy_objective(500.0, 1000.0, 2000.0)
    assert PENALTY <= a < b


@pytest.mark.parametrize(
    "E_tot,delivered,Q_request", [(0.0, 1.0, 1.0), (math.nan, 1.0, 1.0), (1.0, math.nan, 1.0), (1.0, 1.0, 0.0)]
)
def test_invalid_inputs_return_the_penalty(E_tot, delivered, Q_request):
    assert specific_energy_objective(E_tot, delivered, Q_request) == PENALTY
    assert specific_energy_objective(E_tot, delivered, Q_request, penalty=1e12) == 1e12
