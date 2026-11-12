"""Boilers report unsolved operating points as failures, not as results (#96).

Each test pins one way the domestic-hot-water boilers (ASHPB, GSHPB, WSHPB)
used to hand back a state they had not solved:

* internal solver keys leaking into ``analyze_steady`` results;
"""

from __future__ import annotations

import warnings

import pytest

from tmhp import GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler


def _steady(model, **kwargs) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        result = model.analyze_steady(**kwargs)
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize("cls", [GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler])
def test_steady_result_has_no_internal_keys(cls) -> None:
    result = _steady(cls(), T_tank_w=50.0, T_source=10.0, Q_ref_tank=6000.0, T0=10.0)
    assert result["E_cmp [W]"] > 0.0
    assert not [key for key in result if key.startswith("_")]
