"""Condenser ε-NTU and reverse-heat-transfer regression tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tmhp import AirSourceHeatPumpBoiler
from tmhp import air_source_heat_pump_boiler as ashpb_module
from tmhp import calc_util as cu


@pytest.fixture
def hp() -> AirSourceHeatPumpBoiler:
    return AirSourceHeatPumpBoiler(ref="R32", hp_capacity=15000.0)


def test_analyze_steady_without_loop_flow_preserves_fixed_ua_baseline(hp):
    """Omitting m_dot_w retains the pre-ε-NTU standalone result."""
    omitted = hp.analyze_steady(T_tank_w=54.0, T0=7.0, Q_ref_tank=10000.0)
    explicit_none = hp.analyze_steady(T_tank_w=54.0, T0=7.0, Q_ref_tank=10000.0, m_dot_w=None)

    baseline = {
        "Q_ref_tank [W]": 10000.0,
        "E_cmp [W]": 4138.21215536153,
        "cop_ref [-]": 2.4165024954179426,
    }
    assert omitted["converged"] is True
    assert omitted["failure_reason"] == "none"
    for key, expected in baseline.items():
        assert omitted[key] == pytest.approx(expected, rel=1e-9)
        assert explicit_none[key] == pytest.approx(omitted[key], rel=1e-12)


def test_lower_loop_flow_raises_condensing_temperature_and_reduces_cop(hp):
    low_flow = hp.analyze_steady(T_tank_w=54.0, T0=7.0, Q_ref_tank=10000.0, m_dot_w=0.5)
    high_flow = hp.analyze_steady(T_tank_w=54.0, T0=7.0, Q_ref_tank=10000.0, m_dot_w=3.0)

    assert low_flow["converged"] is True
    assert high_flow["converged"] is True
    assert low_flow["T_ref_cond_sat_l [°C]"] > high_flow["T_ref_cond_sat_l [°C]"]
    assert low_flow["cop_ref [-]"] < high_flow["cop_ref [-]"]


def test_high_loop_flow_approaches_fixed_ua_limit(hp):
    fallback = hp.analyze_steady(T_tank_w=54.0, T0=7.0, Q_ref_tank=10000.0)
    high_flow = hp.analyze_steady(T_tank_w=54.0, T0=7.0, Q_ref_tank=10000.0, m_dot_w=1.0e6)

    assert high_flow["converged"] is True
    for key in ("T_ref_cond_sat_l [°C]", "Q_ref_tank [W]", "E_cmp [W]", "cop_ref [-]"):
        assert high_flow[key] == pytest.approx(fallback[key], rel=1e-3)


def test_final_pr_clamped_condensing_temperature_cannot_be_at_loop_inlet(hp, monkeypatch):
    """The active guard uses the final saturation temperature after PR clamping."""

    def fake_ref_state(**kwargs):
        return {
            "P_ref_cmp_in [Pa]": 100.0,
            "P_ref_cmp_out [Pa]": 101.0,
        }

    monkeypatch.setattr(hp, "_optimize_operation", lambda *args, **kwargs: SimpleNamespace(x=5.0, success=True))
    monkeypatch.setattr(ashpb_module, "calc_ref_state", fake_ref_state)
    monkeypatch.setattr(ashpb_module, "check_pr_envelope", lambda *args: "pr_below_min")

    import CoolProp.CoolProp as CP

    monkeypatch.setattr(CP, "PropsSI", lambda *args: cu.C2K(50.0))

    with pytest.warns(RuntimeWarning, match="t_cond_below_t_in"):
        result = hp.analyze_steady(T_tank_w=50.0, T0=45.0, Q_ref_tank=1000.0, m_dot_w=1.0)

    assert result["converged"] is False
    assert result["failure_reason"] == "t_cond_below_t_in"
    assert result["Q_ref_tank [W]"] == 0.0
    assert result["E_cmp [W]"] == 0.0
