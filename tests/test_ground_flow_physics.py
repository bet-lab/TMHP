"""Physical scaling oracles for parallel hydraulics, UA and borehole resistance."""

import math

import numpy as np
import pytest

from tmhp.borehole import (
    calc_effective_borehole_thermal_resistance,
    calc_local_borehole_thermal_resistance,
    precompute_borehole_resistance,
)
from tmhp.constants import c_w, k_w, mu_w, rho_w
from tmhp.ground_loop import ground_flow_state
from tmhp.heat_exchanger import calc_UA_two_stream_scaled, solve_secondary_flow_for_target_heat
from tmhp.heat_transfer import darcy_friction_factor as old_friction
from tmhp.pump import (
    calc_parallel_borefield_pressure_drop,
    calc_pipe_pressure_drop,
    calc_pump_power,
    darcy_friction_factor,
)


def test_pipe_laminar_analytic_and_parallel_topology():
    # Hagen-Poiseuille: dp = 128 mu L V / (pi D^4).
    rho, mu, diameter, length, mass = 1000, 0.001, 0.026, 200, 0.01
    expected = 128 * mu * length * (mass / rho) / (math.pi * diameter**4)
    assert calc_pipe_pressure_drop(mass, length, diameter, rho, mu) == pytest.approx(expected)
    branch = calc_parallel_borefield_pressure_drop(mass, 1, 100, diameter, rho, mu)
    parallel = calc_parallel_borefield_pressure_drop(4 * mass, 4, 100, diameter, rho, mu)
    assert parallel == pytest.approx(branch)  # no factor-of-four serial pressure drop
    assert calc_pump_power(parallel, 4 * mass / rho, 0.6) == pytest.approx(4 * calc_pump_power(branch, mass / rho, 0.6))
    assert calc_parallel_borefield_pressure_drop(mass, 1, 200, diameter, rho, mu) == pytest.approx(2 * branch)
    assert calc_parallel_borefield_pressure_drop(mass, 1, 100, diameter, rho, mu, dp_common=500) == pytest.approx(
        branch + 500
    )
    assert old_friction is darcy_friction_factor


def test_pump_zero_flow_and_turbulent_trend():
    assert calc_parallel_borefield_pressure_drop(0, 4, 100, 0.026, 1000, 0.001) == 0
    assert calc_pump_power(1000, 0, 0.6) == 0
    powers = [
        calc_pump_power(calc_parallel_borefield_pressure_drop(m, 2, 100, 0.026, 1000, 0.001), m / 1000, 0.6)
        for m in (0.4, 0.8, 1.2)
    ]
    assert np.all(np.diff(powers) > 0)
    with pytest.raises(ValueError):
        calc_pump_power(1000, 0.001, 0)


def test_resistance_interpolation_accepts_only_roundoff_at_both_endpoints():
    interpolate = precompute_borehole_resistance(
        0.2,
        0.4,
        1.0,
        100,
        c_w,
        k_s=2,
        k_g=1.5,
        k_p=0.4,
        r_b=0.08,
        r_out=0.016,
        r_in=0.013,
        D_s=0.025,
        rho_f=rho_w,
        mu_f=mu_w,
        k_f=k_w,
    )
    for endpoint in (0.4 * 0.2, 0.2):
        for rounded in (np.nextafter(endpoint, -np.inf), np.nextafter(endpoint, np.inf)):
            assert interpolate(rounded) == pytest.approx(interpolate(endpoint), rel=1e-14)
    for outside in (0.08 - 1e-8, 0.2 + 1e-8):
        with pytest.raises(ValueError):
            interpolate(outside)
    settings = dict(
        volume_flow_rated=24 / 60000,
        n_boreholes=2,
        rb_interp=interpolate,
        variable_Rb=True,
        hydraulic_pump=False,
        pump_power=0,
    )
    assert ground_flow_state(settings, 0.4)["R_b"] == pytest.approx(interpolate(0.08))


def test_two_stream_resistance_network():
    assert calc_UA_two_stream_scaled(1000, 1, 1, 0.02, 0.02) == 1000
    assert calc_UA_two_stream_scaled(1000, 0.5, 1, 0.02, 0.02) < 1000
    assert calc_UA_two_stream_scaled(1000, 1, 1, 0.01, 0.02) < 1000
    # Half fluid flow with n=1 and a 50/30/20 split: R/R0=1+0.3+0.2.
    assert calc_UA_two_stream_scaled(1000, 0.5, 1, 0.02, 0.02, fluid_exponent=1) == pytest.approx(1000 / 1.5)
    with pytest.raises(ValueError, match="sum"):
        calc_UA_two_stream_scaled(1000, 1, 1, 0.02, 0.02, constant_fraction=0.5)
    for m in (0, -1, math.nan):
        with pytest.raises(ValueError):
            calc_UA_two_stream_scaled(1000, m, 1, 0.02, 0.02)


def test_secondary_solver_and_infeasible_duty():
    target = (1 - math.exp(-1)) * 4000 * 10
    result = solve_secondary_flow_for_target_heat(target, 4000, 4000, 20, 10, 0.1, 2)
    assert result["converged"]
    assert result["m_dot"] == pytest.approx(1)
    assert not solve_secondary_flow_for_target_heat(1e6, 4000, 4000, 20, 10, 0.1, 2)["converged"]


def test_borehole_interpolation_matches_direct_and_never_solves_at_runtime(monkeypatch):
    geometry = dict(
        k_s=2.0, k_g=1.5, k_p=0.4, r_b=0.08, r_out=0.016, r_in=0.013, D_s=0.025, rho_f=rho_w, mu_f=mu_w, k_f=k_w
    )
    interp = precompute_borehole_resistance(0.3, 0.2, 1.2, 100, c_w, **geometry)
    flows = 0.3 * np.array([0.213, 0.377, 0.693, 1.0, 1.137])
    direct = []
    for flow in flows:
        rb, ra = calc_local_borehole_thermal_resistance(m_flow_pipe=flow, cp_f=c_w, **geometry)
        direct.append(calc_effective_borehole_thermal_resistance(rb, ra, 100, flow, c_w))
    monkeypatch.setattr(
        "tmhp.borehole.calc_local_borehole_thermal_resistance", lambda **kw: pytest.fail("multipole invoked at runtime")
    )
    values = [interp(float(flow)) for flow in flows]
    np.testing.assert_allclose(values, direct, rtol=0.005)
    assert np.all(np.diff(values) < 0)
    assert values[3] == pytest.approx(direct[3], rel=1e-12)
    with pytest.raises(ValueError):
        interp(0)
    with pytest.raises(ValueError):
        interp(0.37)
