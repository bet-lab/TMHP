"""HX physical limits and compatibility with the original public imports."""

import math

import pytest

from tmhp import borehole, enex_functions, g_function, heat_exchanger, hx_fan
from tmhp.heat_exchanger import calc_phase_change_hx_capacity, calc_phase_change_hx_effectiveness


def test_legacy_imports_remain_available():
    assert enex_functions.calc_HX_perf_for_target_heat is heat_exchanger.calc_HX_perf_for_target_heat
    assert hx_fan.calc_UA_from_dV_fan is heat_exchanger.calc_UA_from_dV_fan
    for name in (
        "calc_local_borehole_thermal_resistance",
        "calc_effective_borehole_thermal_resistance",
        "calc_borehole_thermal_resistance",
    ):
        assert getattr(g_function, name) is getattr(borehole, name)


def test_phase_change_hx_limits_and_energy_balance():
    assert calc_phase_change_hx_effectiveness(4000, 1, 4000) == pytest.approx(1 - math.exp(-1))
    assert calc_phase_change_hx_capacity(4000, 1, 4000, 20, 10) == pytest.approx(40000 * (1 - math.exp(-1)))
    assert calc_phase_change_hx_capacity(4000, 1, 4000, 10, 20) == pytest.approx(40000 * (1 - math.exp(-1)))
    assert calc_phase_change_hx_capacity(4000, 0, 4000, 20, 10) == 0
    assert calc_phase_change_hx_capacity(0, 1, 4000, 20, 10) == 0
    assert calc_phase_change_hx_capacity(1e9, 1, 4000, 20, 10) == 40000


@pytest.mark.parametrize("ua,m,cp", [(-1, 1, 4000), (1, -1, 4000), (1, 1, 0), (math.nan, 1, 4000)])
def test_hx_invalid_inputs(ua, m, cp):
    with pytest.raises(ValueError):
        calc_phase_change_hx_effectiveness(ua, m, cp)
