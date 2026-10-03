"""References stay fixed while explicit physical operating limits expand."""

import importlib
import math
from functools import lru_cache

import pytest

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler, GroundSourceHeatPump, GroundSourceHeatPumpBoiler
from tmhp.borehole import calc_effective_borehole_thermal_resistance, calc_local_borehole_thermal_resistance
from tmhp.constants import c_a, c_w, k_w, mu_w, rho_a, rho_w
from tmhp.ground_flow_control import select_ground_flow
from tmhp.ground_loop import ground_flow_state, ground_hx_UA
from tmhp.heat_exchanger import calc_HX_perf_for_target_heat, calc_UA_from_dV_fan, resolve_fan_flow_limits
from tmhp.hx_fan import calc_fan_power_from_dV_fan


@lru_cache(maxsize=8)
def ground_model(cls=GroundSourceHeatPump, maximum=36, constant=24, control="constant"):
    params = dict(
        ref="R410A",
        hp_capacity=8000,
        V_cmp_ref=1.2e-5,
        N_1=2,
        N_2=2,
        H_b=100,
        ground_flow_ref_lpm=24,
        ground_flow_constant_lpm=constant,
        ground_flow_min_lpm=9.6,
        ground_flow_max_lpm=maximum,
        hydraulic_pump=True,
        variable_Rb=True,
        variable_ground_hx_UA=True,
        m_dot_ref_rated=0.04,
        t_max_s=3600,
        ground_flow_control=control,
    )
    params.update(
        dict(UA_ground_rated=2000, UA_iu_rated=2000, PR_cycle_max=8)
        if cls is GroundSourceHeatPump
        else dict(UA_tank_hx=2000, UA_ground=2000)
    )
    return cls(**params)


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
def test_reference_and_components_remain_fixed_when_max_expands(cls):
    a, b = ground_model(cls, 24), ground_model(cls, 36)
    assert a._ground_settings["volume_flow_ref"] == b._ground_settings["volume_flow_ref"] == 24 / 60000
    for hp in (a, b):
        assert hp.ground_flow_constant_lpm == 24
        assert ground_hx_UA(hp._ground_settings, 2000, 1, 0.04) == pytest.approx(2000)
    rated = ground_flow_state(b._ground_settings, volume_flow=24 / 60000)
    high = ground_flow_state(b._ground_settings, volume_flow=36 / 60000)
    other = ground_flow_state(a._ground_settings, volume_flow=24 / 60000)
    for key in ("dV", "m_dot_borehole", "E_pmp", "R_b"):
        assert rated[key] == pytest.approx(other[key], rel=1e-12)
    assert high["m_dot_borehole"] == pytest.approx(36 / 60000 * rho_w / 4)
    assert high["E_pmp"] > rated["E_pmp"]
    assert ground_hx_UA(b._ground_settings, 2000, 1.5, 0.04) > 2000
    local, internal = calc_local_borehole_thermal_resistance(
        k_s=2,
        k_g=1.5,
        k_p=0.4,
        r_b=0.08,
        r_out=0.016,
        r_in=0.013,
        D_s=0.025,
        m_flow_pipe=high["m_dot_borehole"],
        rho_f=rho_w,
        mu_f=mu_w,
        cp_f=c_w,
        k_f=k_w,
    )
    physical = calc_effective_borehole_thermal_resistance(local, internal, 100, high["m_dot_borehole"], c_w)
    assert high["R_b"] == pytest.approx(physical, rel=1e-12)


def run(hp, prescribed=None):
    args = {} if prescribed is None else {"ground_flow_lpm": prescribed}
    if isinstance(hp, GroundSourceHeatPump):
        return hp.analyze_steady(Q_r_iu=-4000, T0=7, T_a_room=20, T_bhe_wall=16, **args)
    return hp.analyze_steady(T_tank_w=40, T_source=16, Q_ref_tank=4000, T0=7, T_bhe_wall=16, **args)


@pytest.mark.parametrize("cls", [GroundSourceHeatPump, GroundSourceHeatPumpBoiler])
def test_identical_actual_point_has_identical_physics_for_both_controls(cls):
    constant = run(ground_model(cls, control="constant"))
    prescribed = run(ground_model(cls, control="optimal_power"), 24)
    assert constant["converged"] and prescribed["converged"]
    assert constant["ground_flow_ref_ratio"] == prescribed["ground_flow_ref_ratio"] == 1
    for key in (
        "UA_ground [W/K]",
        "R_b_eff [mK/W]",
        "E_pmp [W]",
        "E_cmp [W]",
        "m_dot_ref [kg/s]",
        "P_ref_cmp_in [Pa]",
        "P_ref_cmp_out [Pa]",
        "E_tot [W]",
    ):
        assert prescribed[key] == pytest.approx(constant[key], rel=1e-10)
    if cls is GroundSourceHeatPump:
        assert prescribed["E_iu_fan [W]"] == pytest.approx(constant["E_iu_fan [W]"], rel=1e-10)


def test_constant_setpoint_is_not_the_reference_or_maximum():
    row = run(ground_model(constant=30))
    assert row["converged"]
    assert row["ground_flow [m3/s]"] == pytest.approx(30 / 60000)
    assert row["ground_flow_ref_ratio"] == pytest.approx(1.25)
    assert not row["ground_flow_at_max"]
    hp = ground_model()
    row = run(hp, 36)
    assert row["ground_flow_ref_ratio"] == pytest.approx(1.5)
    assert row["ground_flow_at_max"]
    with pytest.raises(ValueError, match="not both"):
        hp.analyze_steady(Q_r_iu=4000, T0=26, ground_flow_ratio=1, ground_flow_lpm=24)
    with pytest.raises(ValueError, match="outside"):
        run(hp, 37)


def test_optimizer_can_choose_above_reference_and_constant_uses_its_setpoint():
    settings = dict(
        control="optimal_power",
        min_ratio=0.4,
        max_ratio=1.5,
        volume_flow_ref=24 / 60000,
        volume_flow_constant=30 / 60000,
        volume_flow_min=9.6 / 60000,
        volume_flow_max=36 / 60000,
    )

    def point(ratio):
        return {
            "converged": True,
            "hx_feasible": True,
            "ground_flow_ratio": ratio,
            "E_tot [W]": 100 + (ratio - 1.3) ** 2,
        }

    result = select_ground_flow(point, settings)
    assert result["ground_flow_ratio"] == pytest.approx(1.3, abs=1e-3)
    assert not result["flow_bound_active"]
    constant = select_ground_flow(point, settings | {"control": "constant"})
    assert constant["ground_flow_ratio"] == pytest.approx(1.25)


def test_fan_flow_above_reference_scales_ua_and_power_without_clamp():
    flow = 1.2
    ua = calc_UA_from_dV_fan(flow, A_cross=0.5, UA=1000, dV_fan_ref=1)
    assert ua == pytest.approx(1000 * 1.2**0.71)
    capacity = rho_a * c_a * flow * (1 - math.exp(-ua / (rho_a * c_a * flow))) * 10
    kwargs = dict(Q_ref_target=capacity, T_a_in_C=20, T_ref_sat_K=283.15, A_cross=0.5, UA_rated=1000)
    limited = calc_HX_perf_for_target_heat(**kwargs, dV_fan_rated=1)
    expanded = calc_HX_perf_for_target_heat(
        **kwargs, dV_fan_ref=1, dV_fan_min=0.05, dV_fan_max=1.2, custom_fan_curve=True
    )
    assert not limited["converged"] and limited["max_limit"]
    assert expanded["converged"] and expanded["dV_fan"] == pytest.approx(flow)
    assert expanded["fan_flow_ratio_to_ref"] == pytest.approx(1.2)
    assert expanded["UA"] == pytest.approx(ua)
    coefficients = dict(c1=0, c2=0, c3=0, c4=1, c5=0)
    params = dict(fan_ref_flow_rate=1, fan_ref_power=100, fan_min_flow_rate=0.05, fan_max_flow_rate=1.2)
    assert calc_fan_power_from_dV_fan(flow, params, coefficients) == pytest.approx(100 * 1.2**3)
    old = dict(fan_rated_flow_rate=1, fan_rated_power=100, fan_min_flow_rate=0.05, fan_max_flow_rate=1.2)
    assert calc_fan_power_from_dV_fan(flow, old, coefficients) == pytest.approx(100 * 1.2**3)
    assert math.isnan(calc_fan_power_from_dV_fan(math.nan, params, coefficients))


@pytest.mark.parametrize(
    "cls,unit",
    [(AirSourceHeatPump, "ou"), (AirSourceHeatPump, "iu"), (AirSourceHeatPumpBoiler, ""), (GroundSourceHeatPump, "iu")],
)
def test_each_air_side_model_exposes_independent_reference_and_max(cls, unit, monkeypatch):
    prefix = f"dV_{unit}_fan_a" if unit else "dV_fan_a"
    custom_coeffs = dict(c1=0, c2=0, c3=0, c4=1, c5=0)
    coeff_name = f"vsd_coeffs_{unit}" if unit else "vsd_coeffs"
    kwargs = {prefix + "_ref": 1, prefix + "_min": 0.05, prefix + "_max": 1.2, coeff_name: custom_coeffs}
    if cls is GroundSourceHeatPump:
        kwargs["R_b"] = 0.2
        kwargs["t_max_s"] = 3600
    hp = cls(**kwargs)
    assert getattr(hp, prefix + "_ref") == getattr(hp, prefix + "_rated") == 1
    assert getattr(hp, prefix + "_max") == 1.2
    assert getattr(hp, prefix + "_min") == 0.05
    calls = []
    module = importlib.import_module(cls.__module__)
    original = module.calc_HX_perf_for_target_heat

    def inspect(**kw):
        if kw.get("dV_fan_ref") == 1:
            calls.append((kw["dV_fan_min"], kw["dV_fan_max"]))
        return original(**kw)

    monkeypatch.setattr(module, "calc_HX_perf_for_target_heat", inspect)
    if cls is AirSourceHeatPumpBoiler:
        hp.analyze_steady(T_tank_w=40, Q_ref_tank=2000, T0=7)
    elif cls is AirSourceHeatPump:
        hp.analyze_steady(Q_r_iu=-2000, T0=7, T_a_room=20, verbose=False)
    else:
        hp.analyze_steady(Q_r_iu=-2000, T0=7, T_a_room=20)
    assert calls and all(pair == (0.05, 1.2) for pair in calls)


@pytest.mark.parametrize(
    "ref,low,high", [(0, 0.05, 1), (1, 1, 1), (1, -1, 2), (1, 0.05, math.inf), (math.nan, 0.05, 1)]
)
def test_invalid_fan_bounds_are_input_errors(ref, low, high):
    with pytest.raises(ValueError):
        resolve_fan_flow_limits(ref, low, high)


def test_legacy_fan_solver_matches_explicit_reference_and_compatibility_bounds():
    kw = dict(Q_ref_target=3000, T_a_in_C=20, T_ref_sat_K=283.15, A_cross=0.5, UA_rated=1000)
    old = calc_HX_perf_for_target_heat(**kw, dV_fan_rated=1)
    new = calc_HX_perf_for_target_heat(**kw, dV_fan_ref=1, dV_fan_min=0.05, dV_fan_max=1)
    assert old["converged"] and new["converged"]
    for key in ("dV_fan", "UA", "Q_air", "epsilon"):
        assert new[key] == pytest.approx(old[key], rel=1e-12)


def test_legacy_ground_inputs_warn_and_map_to_the_same_fixed_reference():
    with pytest.warns(DeprecationWarning, match="dV_b_f_lpm"):
        hp = GroundSourceHeatPump(
            dV_b_f_lpm=24, ground_flow_min_ratio=0.4, ground_flow_max_ratio=1.5, R_b=0.2, t_max_s=3600
        )
    assert hp.ground_flow_ref_lpm == hp.ground_flow_constant_lpm == 24
    assert hp.ground_flow_min_lpm == pytest.approx(9.6)
    assert hp.ground_flow_max_lpm == 36
    with pytest.warns(DeprecationWarning):
        physical = GroundSourceHeatPump(
            dV_b_f_lpm=48,
            ground_flow_ref_lpm=24,
            ground_flow_min_lpm=9.6,
            ground_flow_max_lpm=36,
            R_b=0.2,
            t_max_s=3600,
        )
    assert physical.ground_flow_ref_lpm == 24


def test_expanded_ground_flow_can_close_low_load_with_explicit_indoor_approach_range():
    common = dict(
        ref="R410A",
        hp_capacity=8000,
        V_cmp_ref=1.2e-5,
        eta_cmp_isen=0.70,
        eta_cmp_vol=0.9,
        eta_cmp=0.8,
        N_1=1,
        N_2=2,
        H_b=100,
        B=6,
        Ts=15,
        T_a_room=26,
        ground_flow_ref_lpm=24,
        ground_flow_constant_lpm=24,
        ground_flow_min_lpm=9.6,
        ground_flow_max_lpm=36,
        hydraulic_pump=True,
        variable_Rb=True,
        variable_ground_hx_UA=True,
        m_dot_ref_rated=0.04151110907913401,
        PR_cycle_max=8,
        # Freeze the historical 5% curve domain for this search-limit
        # regression. Generic 15% feasibility has separate coverage.
        vsd_coeffs_iu={"curve_type": "custom"},
        dV_iu_fan_a_min=0.05 * 1.6,
        t_max_s=3600,
    )
    expanded = GroundSourceHeatPump(**common, indoor_approach_max_K=25)
    row = expanded.analyze_steady(Q_r_iu=2400, T0=26, T_a_room=26, T_bhe_wall=15, ground_flow_lpm=36)
    assert row["converged"] and row["hx_feasible"]
    assert row["ground_flow_ref_ratio"] == pytest.approx(1.5)
    assert row["Q_ref_iu [W]"] == pytest.approx(2400)
    assert row["Q_HX_available [W]"] == pytest.approx(row["Q_ref_required [W]"], abs=0.1)
    assert row["T_ref_evap_sat [°C]"] > 0
    assert row["T_ref_evap_sat [°C]"] < 6  # needs more than the historical 20 K approach
    assert row["E_cmp_ref [W]"] == pytest.approx(0.8 * row["E_cmp [W]"])
