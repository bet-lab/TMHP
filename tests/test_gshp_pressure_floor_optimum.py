"""Physical pressure-floor boundary must remain a candidate in HX optimization."""

import pytest

from tmhp import GroundSourceHeatPump


@pytest.fixture
def hp():
    return GroundSourceHeatPump(
        ref="R410A",
        hp_capacity=8000,
        V_cmp_ref=1.2e-5,
        T_a_room=26,
        Ts=15,
        N_1=1,
        N_2=2,
        H_b=100,
        B=6,
        r_b=0.08,
        r_out=0.016,
        r_in=0.013,
        D_s=0.025,
        k_s=2,
        k_g=1.5,
        k_p=0.4,
        UA_ground_rated=800,
        UA_iu_rated=1600,
        m_dot_ref_rated=0.042881269675229136,
        ground_flow_ref_lpm=24,
        ground_flow_constant_lpm=24,
        ground_flow_min_lpm=9.6,
        ground_flow_max_lpm=36,
        hydraulic_pump=True,
        variable_Rb=True,
        variable_ground_hx_UA=True,
        pump_efficiency=0.6,
        dp_common=0,
        pipe_roughness=1e-6,
        indoor_approach_min_K=1,
        indoor_approach_max_K=25,
        rps_min=15,
        rps_max=150,
        rps_rated=60,
        PR_cycle_min=1.5,
        dT_superheat=5,
        dT_subcool=5,
        t_max_s=86400,
    )


def test_cooling_search_resolves_pressure_floor_with_ground_hx_duty_equality(hp):
    row = hp.analyze_steady(Q_r_iu=4000, T0=26, T_a_room=26, T_bhe_wall=15, ground_flow_lpm=24)
    assert row["converged"] and row["hx_feasible"]
    assert row["pr_floor_active"]
    assert row["pressure_ratio"] == pytest.approx(1.5, abs=1e-7)
    assert row["Q_HX_available [W]"] == pytest.approx(row["Q_ref_required [W]"], abs=0.05)
    assert row["Q_ref_iu [W]"] == pytest.approx(4000, rel=1e-9)


def test_projected_cycle_subcooling_is_independent_of_preprojection_trial(hp):
    # Both trial approaches project onto the identical PR-floor condenser state.
    # Its physically available subcooling must not depend on the rejected trial.
    a, b = [
        hp._calc_state(15, approach, 4000, 26, 26, ground_flow_ratio=1, source_temperature_K=290.45)
        for approach in (1, 2)
    ]
    assert a is not None and b is not None
    assert a["pr_floor_active"] and b["pr_floor_active"]
    for key in ("T_ref_cond_sat_l [°C]", "h_ref_exp_in [J/kg]", "m_dot_ref [kg/s]", "E_cmp [W]", "E_tot [W]"):
        assert a[key] == pytest.approx(b[key], rel=1e-10), key
