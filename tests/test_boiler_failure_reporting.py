"""Boilers report unsolved operating points as failures, not as results (#96).

Each test pins one way the domestic-hot-water boilers (ASHPB, GSHPB, WSHPB)
used to hand back a state they had not solved:

* internal solver keys leaking into ``analyze_steady`` results;
* a compressor clamped onto its speed floor with a collapsed isentropic
  efficiency (discharge state NaN, duty many times the nameplate) reported
  as ``failure_reason="none"``;
"""

from __future__ import annotations

import math
import warnings

import pytest

from tmhp import GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler
from tmhp.dynamic_context import unphysical_cycle_detail


def _steady(model, **kwargs) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        result = model.analyze_steady(**kwargs)
    assert isinstance(result, dict)
    return result


def _assert_off_mode_failure(result: dict, reason: str) -> None:
    assert result["failure_reason"] == reason
    assert result["converged"] is False
    assert result["hp_is_on"] is False
    for key in ("Q_ref_tank [W]", "E_cmp [W]", "E_tot [W]"):
        assert result[key] == 0.0, key
    assert math.isnan(result["cop_sys [-]"])


# --------------------------------------------------------------------------
# Item 6: no solver-internal keys in steady results
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler])
def test_steady_result_has_no_internal_keys(cls) -> None:
    result = _steady(cls(), T_tank_w=50.0, T_source=10.0, Q_ref_tank=6000.0, T0=10.0)
    assert result["E_cmp [W]"] > 0.0
    assert not [key for key in result if key.startswith("_")]


# --------------------------------------------------------------------------
# Item 2: a speed-floor clamp on a collapsed cycle is a failure
# --------------------------------------------------------------------------


_GOOD_STATE = {
    "hp_is_on": True,
    "Q_ref_tank [W]": 6000.0,
    "E_cmp [W]": 1800.0,
    "T_ref_cmp_out [°C]": 85.0,
    "h_ref_cmp_out [J/kg]": 4.5e5,
    "capacity_clamped": None,
}


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({}, None),
        # A speed-floor clamp within the nameplate is an ordinary part-load point.
        ({"capacity_clamped": "min", "Q_ref_tank [W]": 7900.0}, None),
        ({"capacity_clamped": "min", "Q_ref_tank [W]": 8100.0}, "min_speed_duty_above_capacity"),
        # A speed-ceiling shortfall is not over-delivery.
        ({"capacity_clamped": "max", "Q_ref_tank [W]": 8100.0}, None),
        ({"T_ref_cmp_out [°C]": float("nan")}, "discharge_state_undefined"),
        ({"h_ref_cmp_out [J/kg]": float("inf")}, "discharge_state_undefined"),
        ({"E_cmp [W]": 0.0}, "duty_or_power_undefined"),
        ({"Q_ref_tank [W]": float("nan")}, "duty_or_power_undefined"),
    ],
)
def test_unphysical_cycle_detail(change: dict, detail: str | None) -> None:
    assert unphysical_cycle_detail({**_GOOD_STATE, **change}, hp_capacity=8000.0) == detail


@pytest.mark.parametrize(
    ("ref", "point"),
    [
        # Before #96: Q 90,957 W for a 6.4 kW request, eta_is 0.0032, NaN discharge.
        ("R134a", {"T_tank_w": 65.0, "T_source": 5.0, "Q_ref_tank": 6400.0, "T0": 10.0}),
        # Before #96: Q 9,659 W, E_cmp 8,552 W, eta_is 0.068, NaN discharge.
        ("R410A", {"T_tank_w": 55.0, "T_source": -5.0, "Q_ref_tank": 6400.0, "T0": 10.0}),
    ],
)
def test_wshpb_collapsed_speed_floor_is_reported_as_failure(ref: str, point: dict) -> None:
    result = _steady(WaterSourceHeatPumpBoiler(ref=ref), **point)
    _assert_off_mode_failure(result, "cycle_invalid")
    assert result["failure_detail"] in {"discharge_state_undefined", "min_speed_duty_above_capacity"}


def test_gshpb_collapsed_speed_floor_is_reported_as_failure() -> None:
    # Before #96: Q 8,665 W for a 6.4 kW request at 15 rev/s, eta_is 0.076,
    # NaN discharge, failure_reason "none". (R134a cannot even be built here:
    # its B0/W55 rating point does not bracket, so R410A carries the case.)
    model = GroundSourceHeatPumpBoiler(ref="R410A")
    result = _steady(model, T_tank_w=55.0, T_source=-5.0, Q_ref_tank=6400.0, T0=10.0)
    _assert_off_mode_failure(result, "cycle_invalid")
    assert result["failure_detail"] in {"discharge_state_undefined", "min_speed_duty_above_capacity"}
