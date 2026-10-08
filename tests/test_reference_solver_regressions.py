"""Invalid numerical trials must not hide valid states or certify false closure."""

import math
from types import SimpleNamespace

import pytest

from tmhp import AirSourceHeatPump, GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler
from tmhp.air_source_heat_pump import OBJ_INFEASIBLE
from tmhp.reference_state import ReferenceStateError, _evaluable_bracket, _root_in_bracket


def test_disconnected_valid_energy_balances_do_not_share_a_bracket():
    def residual(value):
        if 0.4 <= value <= 0.6:
            raise ValueError("Unavailable thermodynamic state")
        return value - (0.2 if value < 0.4 else 0.8)

    bracket = _evaluable_bracket(residual, 0, 1, endpoints_first=True)
    solved = _root_in_bracket(residual, bracket, 1)
    assert solved == pytest.approx(0.2)
    assert residual(solved) == pytest.approx(0, abs=1e-12)


@pytest.mark.parametrize("unavailable", [math.nan, math.inf, -math.inf])
def test_nonfinite_residuals_cannot_certify_a_reference_balance(unavailable):
    with pytest.raises(ReferenceStateError, match="could not bracket a finite"):
        _evaluable_bracket(lambda _: unavailable, 15, 150, endpoints_first=True)


def test_default_raw_fit_reference_closes_despite_decreasing_low_speed_condenser_duty():
    hp = GroundSourceHeatPumpBoiler(ref="R32", R_b=0.2, t_max_s=3600)
    state = hp.reference_state
    assert state is not None
    # This reference is independently obtained from the coupled mass-flow and
    # both-HX equations, rather than the former synthetic extrapolation.
    assert state.rps_rated == pytest.approx(20.88402055816114, rel=1e-9)
    expected_evap = state.condition.source.conductance * (state.condition.source.T_in_C - state.T_evap_sat_C)
    assert state.Q_cond == pytest.approx(8000, rel=1e-9)
    assert state.Q_evap == pytest.approx(expected_evap, rel=1e-9)
    assert hp.eta_cmp_isen(state.pressure_ratio, hp.rps_max) < 0


def _capacity_probe(mode, *, binding="cycle", other=80.0, ceiling_error=False, ceiling=100.0):
    """A failed request plus an independently controlled envelope candidate."""
    hp = object.__new__(AirSourceHeatPump)
    hp._last_pr_event = None
    hp._optimize_operation = lambda **_: SimpleNamespace(x=[5.0, 5.0], fun=OBJ_INFEASIBLE, success=True)

    def max_capacity(**_):
        if ceiling_error:
            raise ValueError("Unavailable fluid property in envelope search")
        return {
            "Q_max [W]": ceiling,
            "dT_ref_evap [K]": 5.0,
            "dT_ref_cond [K]": 5.0,
            "binding": binding,
        }

    def calc_state(**inputs):
        if abs(inputs["Q_r_iu"]) == 200:
            return None
        if inputs["Q_r_iu"] == 0:
            return {"hp_is_on": False, "E_cmp [W]": 0.0}
        return {
            "converged": True,
            "Q_ref_iu [W]": 100.0,
            "Q_ref_ou [W]": other,
            "E_cmp [W]": 25.0,
            "eta_cmp [-]": 0.8,
            "hp_is_on": True,
        }

    hp.max_capacity = max_capacity
    hp._calc_state = calc_state
    request = 200.0 if mode == "cooling" else -200.0
    return hp.analyze_steady(Q_r_iu=request, T0=2, T_a_room=21, postprocess=False, verbose=False)


@pytest.mark.parametrize("mode,other,requested_duty", [("heating", 80, -200), ("cooling", 120, 200)])
def test_verified_capacity_limit_preserves_signed_request_and_cycle_work(mode, other, requested_duty):
    result = _capacity_probe(mode, other=other)
    assert result["failure_reason"] == "none"
    assert result["capacity_clamped"] == "max"
    assert result["Q_request [W]"] == requested_duty
    assert result["Q_ref_iu [W]"] == 100
    assert result["converged"] and result["hp_is_on"]


@pytest.mark.parametrize(
    "parameters",
    [
        {"binding": "unbounded"},
        {"binding": "infeasible"},
        {"other": math.nan},
        {"other": math.inf},
        {"other": 70.0},
        {"ceiling_error": True},
        {"ceiling": 0.0},
        {"ceiling": 250.0},
    ],
)
def test_incomplete_or_unneeded_ceiling_keeps_original_failure(parameters):
    result = _capacity_probe("heating", **parameters)
    assert result["failure_reason"] == "cycle_invalid"
    assert not result["converged"] and not result["hp_is_on"]
    assert result["E_cmp [W]"] == 0
    assert result.get("capacity_clamped") != "max"


@pytest.mark.parametrize("cls,side", [(GroundSourceHeatPumpBoiler, "ground"), (WaterSourceHeatPumpBoiler, "water")])
@pytest.mark.parametrize(
    "error,required,converged,accepted",
    [
        (math.nan, 1000, True, False),
        (math.inf, 1000, True, False),
        (0, math.nan, True, False),
        (0, math.inf, True, False),
        (0, -1000, True, False),
        (0, 1000, False, False),
        (1, 1000, True, False),
        (0, 1000, True, True),
    ],
)
def test_boiler_final_balance_is_checked_again_after_the_root(cls, side, error, required, converged, accepted):
    hp = object.__new__(cls)

    def calc_state(*args, **inputs):
        final = bool(args)
        return {
            "converged_rps": converged if final else True,
            f"err_Q_{side} [W]": error if final else inputs[f"dT_ref_{side}"] - 10,
            f"Q_ref_{side} [W]": required if final else 1000,
        }

    hp._calc_state = calc_state
    result = hp._optimize_operation(50, 8000, 5, flow_state={})
    assert result.success is accepted
    if accepted:
        assert result.x == pytest.approx(10)
    else:
        assert math.isnan(result.x)
