"""ASHPB below the minimum speed: the operating point is the cheapest way to meet the request (#96).

On the compressor's speed floor every outdoor approach is the same compressor
speed with a different fan flow, and each delivers a different amount of heat,
all above the request. The search used to credit the surplus only while the
tank was more than 0.5 K below its upper set point. Against a full tank it
ranked the floor candidates on total power, which there is nearly flat in the
approach and has two minima -- R290 at 45 degC air and a 65 degC tank: 8.7 K
and 16.1 K, 3 W apart, system COP 5.1 and 4.3 -- and it returned the starved
coil at 16.1 K. Half a kelvin of tank temperature, or a search range that
stopped short of the second minimum, moved the reported COP by a fifth.

The floor candidates are now ranked on their specific energy whatever the
tank temperature, so the row is the most efficient machine the floor offers --
reported as it runs with ``Cd_cycling=None``, and as its on/off mean by
default. These tests fail on the old ranking.
"""

from __future__ import annotations

import math
import warnings

import pytest

from tmhp import AirSourceHeatPumpBoiler
from tmhp.dynamic_context import unphysical_cycle_detail

_FLOW_STATE = {
    "dV_mix_w_out [m3/s]": 0.0,
    "dV_tank_w_out [m3/s]": 0.0,
    "dV_tank_w_in [m3/s]": 0.0,
    "dV_mix_sup_w_in [m3/s]": 0.0,
}
#: R290, 45 degC air, tank at its upper set point, a request below the speed floor.
_POINT = {"T_tank_w": 65.0, "T0": 45.0, "Q_ref_tank": 3000.0}


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


@pytest.fixture(scope="module")
def continuous() -> AirSourceHeatPumpBoiler:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return AirSourceHeatPumpBoiler(ref="R290", Cd_cycling=None)


@pytest.fixture(scope="module")
def cycling() -> AirSourceHeatPumpBoiler:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return AirSourceHeatPumpBoiler(ref="R290")


@pytest.fixture(scope="module")
def floor_scan(continuous) -> list[dict]:
    """Operating points on the speed floor over the outdoor approach, every 0.5 K."""
    rows = []
    for k in range(2, 41):
        perf = continuous._calc_state(
            0.5 * k, _POINT["T_tank_w"], _POINT["Q_ref_tank"], _POINT["T0"], flow_state=_FLOW_STATE
        )
        if perf is None or not perf.get("converged", False) or unphysical_cycle_detail(perf, continuous.hp_capacity):
            continue
        assert perf["capacity_clamped"] == "min"
        rows.append(perf)
    assert len(rows) >= 15  # the band is about 10 K wide here
    return rows


def _approach(result: dict) -> float:
    return result["T0 [°C]"] - result["T_ref_evap_sat [°C]"]


def test_continuous_floor_row_is_the_most_efficient_floor_candidate(continuous, floor_scan):
    result = continuous.analyze_steady(**_POINT)
    assert result["failure_reason"] == "none" and result["capacity_clamped"] == "min"
    assert result["Q_ref_tank [W]"] > _POINT["Q_ref_tank"]  # no cycling model: the floor duty
    best = max(row["cop_sys [-]"] for row in floor_scan)
    assert result["cop_sys [-]"] >= best * (1.0 - 1e-6)
    # ... which is not the starved coil: the old point sat at 16.1 K with the fan at its lowest flow.
    assert _approach(result) < 9.0
    assert result["dV_ou_a [m3/s]"] > 0.5 * continuous.dV_fan_a_rated


def test_cycling_row_is_the_on_off_mean_of_the_most_efficient_floor_candidate(cycling, continuous, floor_scan):
    result = cycling.analyze_steady(**_POINT)
    running = continuous.analyze_steady(**_POINT)
    assert result["failure_reason"] == "none" and result["capacity_clamped"] == "min"
    assert result["Q_ref_tank [W]"] == pytest.approx(_POINT["Q_ref_tank"], rel=1e-9)
    # The same machine as without the cycling model ...
    assert result["Q_min [W]"] == pytest.approx(running["Q_ref_tank [W]"], rel=1e-9)
    assert _approach(result) == pytest.approx(_approach(running), abs=1e-9)
    assert result["cop_sys [-]"] / result["part_load_factor [-]"] == pytest.approx(running["cop_sys [-]"], rel=1e-9)
    # ... which no floor candidate beats, as it runs or cycling to the request
    # (EN 14825, water form: the mean input is the running one times the power fraction).
    best = max(row["cop_sys [-]"] for row in floor_scan)
    assert result["cop_sys [-]"] / result["part_load_factor [-]"] >= best * (1.0 - 1e-6)
    mean_inputs = [
        row["E_tot [W]"] * cycling.cycling.power_fraction(_POINT["Q_ref_tank"] / row["Q_ref_tank [W]"])
        for row in floor_scan
    ]
    assert result["E_tot [W]"] <= min(mean_inputs) * (1.0 + 2e-3)
    # The starved coil the old ranking chose cost a seventh more for the same heat.
    assert result["E_tot [W]"] < 0.9 * mean_inputs[-1]


@pytest.mark.parametrize("model_name", ["continuous", "cycling"])
def test_floor_row_does_not_jump_where_the_tank_runs_out_of_headroom(model_name, request):
    """0.2 K of tank temperature across ``upper bound - 0.5 K`` moved the approach by 10 K."""
    model = request.getfixturevalue(model_name)
    assert model.T_tank_w_upper_bound == 65.0
    below = model.analyze_steady(T_tank_w=64.4, T0=45.0, Q_ref_tank=3000.0)
    above = model.analyze_steady(T_tank_w=64.6, T0=45.0, Q_ref_tank=3000.0)
    assert below["capacity_clamped"] == above["capacity_clamped"] == "min"
    assert abs(_approach(above) - _approach(below)) < 0.2
    assert above["cop_sys [-]"] == pytest.approx(below["cop_sys [-]"], rel=0.01)


@pytest.mark.parametrize(
    "settings",
    [
        {"_OUTDOOR_GRID_POINTS": 7},
        {"_OUTDOOR_GRID_POINTS": 9, "_BAND_SCAN_INTERVALS": 8},
        {"_BAND_SCAN_INTERVALS": 2},
        {"_BAND_XATOL_K": 1e-3},
        # A range that ends before the starved-coil minimum the old ranking chose.
        {"_OUTDOOR_APPROACH_BOUNDS": (1.0, 14.0)},
    ],
    ids=["grid7", "grid9-scan8", "scan2", "xatol", "range-to-14K"],
)
@pytest.mark.parametrize("model_name", ["continuous", "cycling"])
def test_floor_row_does_not_depend_on_where_the_search_starts(model_name, settings, request, monkeypatch):
    model = request.getfixturevalue(model_name)
    reference = model.analyze_steady(**_POINT)
    for name, value in settings.items():
        monkeypatch.setattr(AirSourceHeatPumpBoiler, name, value)
    moved = model.analyze_steady(**_POINT)
    assert moved["failure_reason"] == "none"
    # The optimum is flat: the approach is located to a few hundredths of a
    # kelvin, the cost to much better.
    assert _approach(moved) == pytest.approx(_approach(reference), abs=0.05)
    assert moved["cop_sys [-]"] == pytest.approx(reference["cop_sys [-]"], rel=1e-4)
    assert moved["E_tot [W]"] == pytest.approx(reference["E_tot [W]"], rel=1e-4)


def test_request_inside_the_modulating_range_is_ranked_on_total_power(continuous):
    """Where the compressor meets the request the cost is ``E_tot``, as before."""
    opt = continuous._optimize_operation(55.0, 9000.0, 7.0, flow_state=_FLOW_STATE)
    state = opt.solved[0]
    assert state["capacity_clamped"] is None
    assert math.isclose(opt.fun, state["E_tot [W]"], rel_tol=1e-9)
