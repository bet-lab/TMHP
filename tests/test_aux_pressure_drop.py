"""Independent hydraulic scaling and coupled GSHP/GSHPB API regressions."""

import math

import numpy as np
import pytest

from tmhp import GroundSourceHeatPump, GroundSourceHeatPumpBoiler
from tmhp.constants import mu_w, rho_w
from tmhp.ground_flow_control import failed_ground_point
from tmhp.ground_loop import ground_flow_state
from tmhp.pump import calc_aux_pressure_drop, calc_parallel_borefield_pressure_drop, calc_pump_power


@pytest.mark.parametrize("ratio", [0.0, 0.4, 1.0, 1.5])
def test_quadratic_head_and_cubic_power(ratio):
    ref = 24 / 60000
    dp = calc_aux_pressure_drop(ref * ratio, ref, 50000)
    assert dp == pytest.approx(50000 * ratio**2)
    assert calc_pump_power(dp, ref * ratio, 0.6) == pytest.approx(50000 * ref / 0.6 * ratio**3)


@pytest.mark.parametrize(
    "args",
    [(-1, 1, 1, 2), (0, 0, 0, 2), (1, 1, -1, 2), (1, 1, 1, 0), (1, 1, 1, -1), (math.nan, 1, 0, 2), (1, math.inf, 0, 2)],
)
def test_aux_input_validation(args):
    with pytest.raises(ValueError):
        calc_aux_pressure_drop(*args)


def test_parallel_loss_plus_aux_uses_total_flow():
    ref = 24 / 60000
    bhe = calc_parallel_borefield_pressure_drop(ref * rho_w, 2, 100, 0.026, rho_w, mu_w)
    total = calc_parallel_borefield_pressure_drop(
        ref * rho_w, 2, 100, 0.026, rho_w, mu_w, volume_flow_ref=ref, dp_aux_ref=50000
    )
    assert total - bhe == pytest.approx(50000)
    with pytest.warns(DeprecationWarning, match="reference flow"):
        migrated = calc_parallel_borefield_pressure_drop(
            ref * rho_w, 2, 100, 0.026, rho_w, mu_w, dp_common=50000, volume_flow_ref=ref
        )
    assert migrated == total
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError, match="volume_flow_ref"):
        calc_parallel_borefield_pressure_drop(ref * rho_w, 2, 100, 0.026, rho_w, mu_w, dp_common=50000)


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
def test_shared_api_scaling_and_reference_independent_of_max(cls):
    kw = dict(
        hp_capacity=8000,
        N_1=1,
        N_2=2,
        H_b=100,
        ground_flow_ref_lpm=24,
        ground_flow_constant_lpm=24,
        ground_flow_min_lpm=9.6,
        ground_flow_max_lpm=36,
        dp_aux_ref=50000,
    )
    hp = cls(**kw)
    assert hp._ground_settings["hydraulic_pump"]  # loss input activates actual hydraulics
    state = ground_flow_state(hp._ground_settings, 1.5)
    assert state["dV"] * 60000 == pytest.approx(36)
    assert state["m_dot_borehole"] == pytest.approx(state["m_dot"] / 2)
    assert state["dp_aux"] == pytest.approx(112500)
    assert state["dp"] == state["dp_bhe"] + state["dp_aux"]
    assert state["E_pmp"] == pytest.approx(state["dp"] * state["dV"] / hp._ground_settings["pump_efficiency"])
    hp2 = cls(**(kw | {"ground_flow_max_lpm": 48}))
    assert ground_flow_state(hp2._ground_settings, 1.5)["dp"] == state["dp"]
    with pytest.warns(DeprecationWarning, match="dp_common"):
        legacy = cls(**(kw | {"dp_aux_ref": 0, "dp_common": 50000}))
    assert ground_flow_state(legacy._ground_settings, 0.4)["dp_aux"] == pytest.approx(8000)
    with pytest.raises(ValueError, match="both nonzero"):
        cls(**(kw | {"dp_common": 1000}))


def test_failed_candidate_cannot_publish_stale_pressure():
    point = failed_ground_point("ground_hx_capacity_insufficient", {"ground_pressure_drop_total [Pa]": 12345})
    assert point["ground_pressure_drop_total [Pa]"] == 0
    assert np.isnan(point["cop_sys [-]"])


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
@pytest.mark.parametrize("setpoint", [1.0, 9.5, 36.1])
def test_constant_flow_cannot_bypass_minimum_or_maximum(cls, setpoint):
    with pytest.raises(ValueError, match="outside the configured bounds"):
        cls(ground_flow_ref_lpm=24, ground_flow_constant_lpm=setpoint, ground_flow_min_lpm=9.6, ground_flow_max_lpm=36)


def test_controller_rejects_invalid_constant_setpoint_before_evaluation():
    from tmhp.ground_flow_control import select_ground_flow

    settings = dict(
        control="constant", min_ratio=0.4, max_ratio=1.5, volume_flow_ref=24 / 60000, volume_flow_constant=1 / 60000
    )
    with pytest.raises(ValueError, match="outside"):
        select_ground_flow(lambda _: pytest.fail("Invalid point must not be evaluated"), settings)
