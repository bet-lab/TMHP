"""Tank-side condenser closure at the compressor speed floor (ASHPB).

Below the speed floor the machine no longer meets the request: it runs at
``rps_min`` and over-delivers. The condenser approach must then follow the heat
the cycle actually produces, not the heat that was asked for -- otherwise a
request that keeps falling keeps pulling the condensing temperature down, and
the on-cycle COP rises for a machine whose operating point has not moved.

These tests pin the closure, the invariance it implies, and the untouched
continuous-modulation band.
"""

from __future__ import annotations

import math

import pytest

from tmhp import AirSourceHeatPumpBoiler
from tmhp.constants import c_w

# 9 kW R32 air-to-water, the machine used throughout the v2026-09-24
# validation report. Where its speed floor falls depends on the displacement
# default, so the tests locate it instead of hard-coding a part-load ratio.
CAP = 9000.0
TANK_C = 42.5
T0_C = 7.0


@pytest.fixture
def hp() -> AirSourceHeatPumpBoiler:
    return AirSourceHeatPumpBoiler(ref="R32", hp_capacity=CAP)


def _floor_plr(hp: AirSourceHeatPumpBoiler) -> float:
    """Delivered part-load ratio with the compressor at ``rps_min``."""
    r = _steady(hp, 0.02)
    assert r["capacity_clamped"] == "min"
    return r["Q_ref_tank [W]"] / CAP


def _pr(r: dict) -> float:
    return r["P_ref_cmp_out [Pa]"] / r["P_ref_cmp_in [Pa]"]


def _steady(hp: AirSourceHeatPumpBoiler, plr: float, **kw) -> dict:
    r = hp.analyze_steady(T_tank_w=TANK_C, T0=T0_C, Q_ref_tank=CAP * plr, **kw)
    assert isinstance(r, dict)
    return r


def test_fixed_ua_speed_floor_closes_on_delivered_heat(hp):
    """Test 1 -- ``Q_delivered = UA_tank_hx * dT`` at the floor."""
    r = _steady(hp, 0.12)

    assert r["capacity_clamped"] == "min"
    assert r["converged"] is True
    assert r["cmp_rpm [rpm]"] / 60.0 == pytest.approx(hp.rps_min)
    # The machine delivers more than the request; that is what being clamped means.
    assert r["Q_ref_tank [W]"] > r["Q_ref_tank_request [W]"]

    residual = r["Q_ref_tank [W]"] - hp.UA_tank_hx * r["dT_ref_tank [K]"]
    assert abs(residual) < 1.0e-3 * r["Q_ref_tank [W]"]
    assert r["tank_hx_residual [W]"] == pytest.approx(residual, abs=1.0e-6)
    # No water-side capacity rate exists in the fixed-UA fallback.
    assert math.isnan(r["epsilon_tank_hx [-]"])
    assert math.isnan(r["C_w_tank [W/K]"])


def test_water_flow_speed_floor_closes_on_delivered_heat(hp):
    """Test 2 -- ``Q_delivered = C_w * epsilon * dT`` at the floor."""
    m_dot_w = 0.2
    r = _steady(hp, 0.12, m_dot_w=m_dot_w)

    assert r["capacity_clamped"] == "min"
    C_w = m_dot_w * c_w
    eps = 1.0 - math.exp(-hp.UA_tank_hx / C_w)
    assert r["epsilon_tank_hx [-]"] == pytest.approx(eps)
    assert r["C_w_tank [W/K]"] == pytest.approx(C_w)

    residual = r["Q_ref_tank [W]"] - C_w * eps * r["dT_ref_tank [K]"]
    assert abs(residual) < 1.0e-3 * r["Q_ref_tank [W]"]


def test_below_floor_requests_share_one_on_cycle_state(hp):
    """Test 3 -- the operating point stops moving once the floor is reached."""
    floor = _floor_plr(hp)
    states = [_steady(hp, floor * f) for f in (0.8, 0.6, 0.4, 0.3)]
    assert all(s["capacity_clamped"] == "min" for s in states)

    ref = states[0]
    for s in states[1:]:
        for key in (
            "cmp_rpm [rpm]",
            "Q_ref_tank [W]",
            "T_ref_cond_sat_v [°C]",
            "E_cmp [W]",
            "cop_sys [-]",
        ):
            assert s[key] == pytest.approx(ref[key], rel=1.0e-4)
        assert _pr(s) == pytest.approx(_pr(ref), rel=1.0e-4)

    # The requested part-load ratio still falls; only the delivered one is pinned.
    assert states[-1]["PLR_request [-]"] < states[0]["PLR_request [-]"]
    assert states[-1]["PLR_delivered [-]"] == pytest.approx(states[0]["PLR_delivered [-]"], rel=1.0e-4)


def test_continuous_modulation_is_untouched(hp):
    """Test 4 -- above the floor the request is met and nothing moved.

    The closure only runs when the speed solver reports a clamp, so every
    modulating point must still satisfy the request exactly and sit at the
    approach the request implies.
    """
    floor = _floor_plr(hp)
    for plr in (1.0, 0.8, 0.6, floor * 1.1):
        r = _steady(hp, plr)
        assert r["capacity_clamped"] is None
        assert r["Q_ref_tank [W]"] == pytest.approx(CAP * plr, rel=1.0e-6)
        assert r["dT_ref_tank [K]"] == pytest.approx(CAP * plr / hp.UA_tank_hx, rel=1.0e-6)
        assert hp.rps_min < r["cmp_rpm [rpm]"] / 60.0 < hp.rps_max


def test_condensing_temperature_no_longer_falls_below_the_floor(hp):
    """The artefact the closure removes: T_cond tracking a request it cannot meet."""
    floor = _floor_plr(hp)
    floor_states = [_steady(hp, floor * f) for f in (0.8, 0.3)]
    assert floor_states[0]["T_ref_cond_sat_v [°C]"] == pytest.approx(
        floor_states[1]["T_ref_cond_sat_v [°C]"], abs=1.0e-3
    )
    # And it stays above the modulating point that last met its request.
    met = _steady(hp, floor * 1.1)
    assert floor_states[0]["T_ref_cond_sat_v [°C]"] < met["T_ref_cond_sat_v [°C]"]


def test_heat_exchanger_diagnostics_are_reported(hp):
    """Test 5 -- the diagnostics the part-load sweeps record."""
    r = _steady(hp, 0.6)
    for key in (
        "PLR_request [-]",
        "PLR_delivered [-]",
        "dT_ref_tank [K]",
        "UA_tank_hx [W/K]",
        "epsilon_tank_hx [-]",
        "tank_hx_residual [W]",
        "UA_ou [W/K]",
        "NTU_ou [-]",
        "epsilon_ou [-]",
    ):
        assert key in r
    assert 0.0 < r["epsilon_ou [-]"] < 1.0
    assert r["NTU_ou [-]"] > 0.0
    assert r["UA_ou [W/K]"] > 0.0
