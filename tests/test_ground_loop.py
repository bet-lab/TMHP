"""Independent field energy/temperature balances, including signed loads."""

import numpy as np
import pytest

from tmhp import GroundSourceHeatPump, GroundSourceHeatPumpBoiler
from tmhp.constants import c_w, rho_w
from tmhp.ground_loop import (
    calc_bhe_fluid_temperatures,
    calc_borefield_linear_load,
    calc_borehole_count,
    calc_borehole_mass_flow,
    calc_total_borehole_length,
)


def test_parallel_field_units():
    assert calc_borehole_count(2, 2) == 4
    assert calc_total_borehole_length(4, 100) == 400
    assert calc_borehole_mass_flow(1.2, 4) == pytest.approx(0.3)
    assert calc_borefield_linear_load(4000, 4, 100) == 10
    for n in (1, 4, 8):
        assert calc_borefield_linear_load(4000, n, 100) * n * 100 == 4000


@pytest.mark.parametrize("q", [4000, -4000, 0])
def test_fluid_energy_balance(q):
    mean, inlet, outlet = calc_bhe_fluid_temperatures(16, q, 4, 100, 0.1, 1, 4000)
    assert (outlet - inlet) * 4000 == pytest.approx(q)
    assert mean == pytest.approx((inlet + outlet) / 2)
    assert 16 - mean == pytest.approx(q / 400 * 0.1)


@pytest.mark.parametrize("args", [(0, 1), (1, -2), (1.5, 2), (True, 2)])
def test_invalid_geometry(args):
    with pytest.raises(ValueError):
        calc_borehole_count(*args)


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
@pytest.mark.parametrize("n", [1, 4])
@pytest.mark.parametrize("q", [4000, -4000])
def test_model_superposition_uses_field_length(monkeypatch, cls, n, q):
    # A known 0.2 mK/W response isolates normalization from pygfunction.
    module = "tmhp." + ("ground_source_heat_pump" if cls is GroundSourceHeatPump else "ground_source_heat_pump_boiler")
    monkeypatch.setattr(module + ".precompute_gfunction", lambda **kw: lambda t: np.full_like(t, 0.2))
    model = cls(N_1=n, N_2=1, H_b=100, R_b=0.1, dV_b_f_lpm=60)
    time = np.array([0.0, 3600.0])
    row = {"Q_bhe [W]": q}
    if cls is GroundSourceHeatPump:
        model._compute_bhe_superposition(0, time, np.zeros(2), 0, row, True)
    else:
        model._ground_coupler.reset(2, time)
        model._compute_bhe_superposition(0, time, row, True)
    assert model.Q_bhe == pytest.approx(q)
    assert model.T_bhe == pytest.approx(16 - q / (n * 100) * 0.2)
    assert model.T_bhe - model.T_bhe_f == pytest.approx(q / (n * 100) * 0.1)
    assert (model.T_bhe_f_out - model.T_bhe_f_in) * 0.001 * rho_w * c_w == pytest.approx(q)


def test_off_flow_guard():
    assert calc_bhe_fluid_temperatures(16, 0, 1, 100, 0.1, 0, 4000) == (16, 16, 16)
    with pytest.raises(ValueError):
        calc_bhe_fluid_temperatures(16, 1, 1, 100, 0.1, 0, 4000)
