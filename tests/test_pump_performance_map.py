"""Independent duties and heat-pump integration for catalogue pump maps."""

import numpy as np
import pytest

from tmhp import GroundSourceHeatPump
from tmhp.ground_loop import ground_flow_state
from tmhp.pump import PumpPerformanceMap

PARAMETERS = dict(
    head_coefficients=[100, 0, -1, 0, 0, 0],
    power_coefficients=[10, 0, 0, 1500, 0, 0, 0],
    speed_min=0.2,
    speed_max=1,
    flow_per_speed_min_m3_h=1,
    flow_per_speed_max_m3_h=5,
)


def test_known_duty_recovers_speed_and_electrical_input():
    model = PumpPerformanceMap(PARAMETERS)
    # q=2 m³/h, s=0.8 -> H=60 m, P1=778 W.
    point = model.operating_point(1000 * 9.80665 * 60, 2 / 3600, 1000)
    assert point["speed_ratio"] == pytest.approx(0.8, abs=1e-10)
    assert point["power_W"] == pytest.approx(778)
    assert point["efficiency"] == pytest.approx(1000 * 9.80665 * 60 * 2 / 3600 / 778)


@pytest.mark.parametrize("pressure,flow", [(2e6, 2 / 3600), (100, 2 / 3600), (1e5, 8 / 3600)])
def test_unattainable_duties_are_not_extrapolated(pressure, flow):
    with pytest.raises(ValueError, match="catalogue domain"):
        PumpPerformanceMap(PARAMETERS).operating_point(pressure, flow, 1000)


def test_stopped_pump():
    p = PumpPerformanceMap(PARAMETERS).operating_point(1e5, 0, 1000)
    assert p["power_W"] == p["speed_ratio"] == 0
    assert np.isnan(p["efficiency"])


def test_invalid_coefficients():
    with pytest.raises(ValueError):
        PumpPerformanceMap(PARAMETERS | {"head_coefficients": [float("nan")] * 6})


def test_heat_pump_uses_map_for_ground_loop_input():
    hp = GroundSourceHeatPump(
        hydraulic_pump=True,
        pump_map=PARAMETERS,
        ground_flow_ref_lpm=36,
        ground_flow_constant_lpm=36,
        ground_flow_min_lpm=18,
        ground_flow_max_lpm=54,
        dp_aux_ref=200000,
    )
    state = ground_flow_state(hp._ground_settings, 1)
    expected = PumpPerformanceMap(PARAMETERS).operating_point(state["dp"], state["dV"], 1000)
    assert state["E_pmp"] == pytest.approx(expected["power_W"])
    assert state["pump_speed_ratio"] == pytest.approx(expected["speed_ratio"])
    assert state["E_pmp"] != pytest.approx(state["dp"] * state["dV"] / 0.6)
