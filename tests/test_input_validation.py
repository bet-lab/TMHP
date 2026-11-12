"""Input validation at every public entry point (issue #104 follow-up).

Contract under test (see ``tmhp._validation`` and
``docs/source/concepts/failure-reason-semantics.rst``):

* an input outside its physical range raises ``ValueError`` whose message names
  the argument ("<name> must be ..., got <value>") before any numerics run;
* an unsolvable operating point built from *valid* inputs is still reported via
  ``failure_reason``, not an exception;
* valid inputs -- including documented edge cases -- are not rejected.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

import tmhp
from tmhp import _validation as v
from tmhp.gshp_empirical import GroundSourceHeatPumpEmpirical
from tmhp.hybrid_tank import HybridStratifiedTank
from tmhp.stratified_tank import StratifiedTank
from tmhp.subsystems import EnergyStorageSystem, PhotovoltaicSystem, SolarThermalCollector, UVLamp

NAN = float("nan")
INF = float("inf")

ASHPB = tmhp.AirSourceHeatPumpBoiler
GSHPB = tmhp.GroundSourceHeatPumpBoiler
WSHPB = tmhp.WaterSourceHeatPumpBoiler
ASHP = tmhp.AirSourceHeatPump
GSHP = tmhp.GroundSourceHeatPump

N_STEPS = 4
PERIOD = N_STEPS * 3600


@pytest.fixture(scope="module")
def models():
    """One default instance per family; also proves the default constructors are accepted."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return {"ASHPB": ASHPB(ref="R32"), "GSHPB": GSHPB(), "WSHPB": WSHPB(), "ASHP": ASHP(), "GSHP": GSHP()}


# --------------------------------------------------------------------------
# Constructors
# --------------------------------------------------------------------------
CTOR_CASES = [
    # (family class, kwargs, argument name expected in the message)
    *[
        (cls, {"hp_capacity": bad}, "hp_capacity")
        for cls in (ASHPB, GSHPB, WSHPB, ASHP, GSHP)
        for bad in (-5000.0, 0.0, NAN, INF)
    ],
    # ASHPB
    (ASHPB, {"UA_tank_hx": -100.0}, "UA_tank_hx"),
    (ASHPB, {"UA_ou_rated": 0.0}, "UA_ou_rated"),
    (ASHPB, {"n_ou": -1.0}, "n_ou"),
    (ASHPB, {"dV_fan_a_rated": -1.0}, "dV_fan_a_rated"),
    (ASHPB, {"eta_fan_rated": 1.5}, "eta_fan_rated"),
    (ASHPB, {"T_tank_w_upper_bound": 50.0, "T_tank_w_lower_bound": 60.0}, "T_tank_w_lower_bound"),
    (ASHPB, {"T_tank_w_upper_bound": 60.0, "T_tank_w_lower_bound": 60.0}, "T_tank_w_lower_bound"),
    (ASHPB, {"tank_level_lower_bound": 0.9, "tank_level_upper_bound": 0.2}, "tank_level_lower_bound"),
    (ASHPB, {"tank_level_upper_bound": 1.5}, "tank_level_upper_bound"),
    (ASHPB, {"r0": -0.2}, "r0"),
    (ASHPB, {"H": 0.0}, "H"),
    (ASHPB, {"x_ins": -0.01}, "x_ins"),
    (ASHPB, {"T_mix_w_out": 313.15}, "T_mix_w_out"),
    (ASHPB, {"T_sup_w": -5.0}, "T_sup_w"),
    (ASHPB, {"T_sur": 293.15}, "T_sur"),
    (ASHPB, {"dT_superheat": -5.0}, "dT_superheat"),
    (ASHPB, {"dT_subcool": NAN}, "dT_subcool"),
    (ASHPB, {"PR_cycle_min": 0.5}, "PR_cycle_min"),
    (ASHPB, {"PR_cycle_min": 3.0, "PR_cycle_max": 2.0}, "PR_cycle_min"),
    (ASHPB, {"hp_on_schedule": [(22.0, 6.0)]}, "hp_on_schedule"),
    (ASHPB, {"hp_on_schedule": [(0.0, 25.0)]}, "hp_on_schedule"),
    (ASHPB, {"eta_cmp_isen": -0.1}, "eta_cmp_isen"),
    (ASHPB, {"eta_cmp_vol": NAN}, "eta_cmp_vol"),
    (ASHPB, {"V_cmp_ref": -1e-4}, "V_cmp_ref"),
    # deprecated aliases are checked like their replacements
    (ASHPB, {"UA_tank": -3.0}, "UA_tank"),
    # GSHPB
    (GSHPB, {"H_b": -100.0}, "H_b"),
    (GSHPB, {"Ts": 300.0}, "Ts"),
    (GSHPB, {"T0": 300.0}, "T0"),
    (GSHPB, {"E_pmp": -100.0}, "E_pmp"),
    (GSHPB, {"N_1": 0}, "N_1"),
    (GSHPB, {"r_in": 0.02, "r_out": 0.016}, "r_in"),
    (GSHPB, {"UA_ground": -1.0}, "UA_ground"),
    (GSHPB, {"pump_efficiency": 1.5}, "pump_efficiency"),
    (GSHPB, {"ground_hx_fluid_fraction": -0.1}, "ground_hx_fluid_fraction"),
    (GSHPB, {"D_b": -1.0}, "D_b"),
    (GSHPB, {"T_tank_w_lower_bound": 70.0}, "T_tank_w_lower_bound"),
    # WSHPB
    (WSHPB, {"v_river": 0.0}, "v_river"),
    (WSHPB, {"UA_water": -1.0}, "UA_water"),
    (WSHPB, {"Ts": 300.0}, "Ts"),
    (WSHPB, {"T_tank_w_upper_bound": 40.0}, "T_tank_w_lower_bound"),
    # ASHP
    (ASHP, {"T_a_room": 293.15}, "T_a_room"),
    (ASHP, {"UA_iu_rated": -1.0}, "UA_iu_rated"),
    (ASHP, {"n_iu": 0.0}, "n_iu"),
    (ASHP, {"dV_iu_fan_a_rated": -1.0}, "dV_iu_fan_a_rated"),
    (ASHP, {"eta_ou_fan_rated": 0.0}, "eta_ou_fan_rated"),
    (ASHP, {"dT_approach_bounds": (5.0, 1.0)}, "dT_approach_bounds"),
    (ASHP, {"dT_approach_bounds": (0.0, 5.0)}, "dT_approach_bounds"),
    # GSHP
    (GSHP, {"H_b": -1.0}, "H_b"),
    (GSHP, {"Ts": 300.0}, "Ts"),
    (GSHP, {"T_a_room": 300.0}, "T_a_room"),
    (GSHP, {"indoor_approach_min_K": -1.0}, "indoor_approach_min_K"),
    (GSHP, {"indoor_approach_min_K": 5.0, "indoor_approach_max_K": 2.0}, "indoor_approach_min_K"),
    (GSHP, {"UA_ground_rated": -1.0}, "UA_ground_rated"),
    (GSHP, {"ground_hx_ua_per_capacity": 0.0}, "ground_hx_ua_per_capacity"),
]


@pytest.mark.parametrize(
    ("cls", "kwargs", "name"), CTOR_CASES, ids=lambda x: x.__name__ if isinstance(x, type) else None
)
def test_constructor_rejects_out_of_range_input(cls, kwargs, name):
    with pytest.raises(ValueError, match=name):
        cls(**kwargs)


def test_negative_hp_capacity_names_the_argument_not_the_fan():
    """Used to surface as 'Fan reference must be positive' -- the cause was not discoverable."""
    with pytest.raises(ValueError, match=r"hp_capacity must be a finite number > 0, got -5000\.0") as exc:
        ASHPB(ref="R32", hp_capacity=-5000.0)
    assert "Fan reference" not in str(exc.value)


def test_message_format_names_argument_expectation_and_value():
    with pytest.raises(ValueError, match=r"^Ts must be .+, got 300\.0 \(a kelvin value\?\)$"):
        GSHPB(Ts=300.0)


@pytest.mark.parametrize("cls", [ASHPB, GSHPB, WSHPB, ASHP, GSHP])
def test_default_constructors_are_accepted(cls):
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)  # defaults must not even warn
        warnings.simplefilter("ignore", DeprecationWarning)
        cls()


def test_valid_but_suspicious_water_temperatures_warn_instead_of_raise():
    with pytest.warns(UserWarning, match="mixing valve has nothing to mix"):
        ASHPB(ref="R32", T_sup_w=45.0, T_mix_w_out=40.0)
    with pytest.warns(UserWarning, match="can never reach"):
        ASHPB(ref="R32", T_mix_w_out=70.0)


def test_empty_operating_window_and_compressor_efficiency_above_one_stay_valid():
    # start == end is the documented way to say "never on"; an efficiency above 1
    # is clipped by tmhp.compressor_efficiency, not rejected at construction.
    ASHPB(ref="R32", hp_on_schedule=[(0.0, 0.0)])
    ASHPB(ref="R32", eta_cmp_isen=1.2)


# --------------------------------------------------------------------------
# Subsystems, combination classes, tanks, empirical model
# --------------------------------------------------------------------------
SUBSYSTEM_CASES = [
    (SolarThermalCollector, {"A_stc": -1.0}, "A_stc"),
    (SolarThermalCollector, {"alpha_stc": 1.5}, "alpha_stc"),
    (SolarThermalCollector, {"stc_tilt": 200.0}, "stc_tilt"),
    (SolarThermalCollector, {"E_stc_pump": -1.0}, "E_stc_pump"),
    (SolarThermalCollector, {"preheat_end_hour": 30.0}, "preheat_end_hour"),
    (PhotovoltaicSystem, {"eta_pv": 0.0}, "eta_pv"),
    (PhotovoltaicSystem, {"A_pv": NAN}, "A_pv"),
    (PhotovoltaicSystem, {"T_ctrl_K": 40.0}, "T_ctrl_K"),
    (EnergyStorageSystem, {"SOC_min": 0.9, "SOC_max": 0.2}, "SOC_min"),
    (EnergyStorageSystem, {"eta_ess_chg": 1.2}, "eta_ess_chg"),
    (EnergyStorageSystem, {"T_ess_K": 40.0}, "T_ess_K"),
    (EnergyStorageSystem, {"C_ess_max": -1.0}, "C_ess_max"),
    (UVLamp, {"lamp_watts": -1.0}, "lamp_watts"),
    (UVLamp, {"period_sec": 0.0}, "period_sec"),
    (StratifiedTank, {"n_nodes": 4, "volume": -1.0, "height": 1.0}, "volume"),
    (StratifiedTank, {"n_nodes": 0, "volume": 1.0, "height": 1.0}, "n_nodes"),
    (StratifiedTank, {"n_nodes": 4, "volume": 1.0, "height": 1.0, "ua": -1.0}, "ua"),
    (StratifiedTank, {"n_nodes": 4, "volume": 1.0, "height": 1.0, "k_eff": NAN}, "k_eff"),
    (HybridStratifiedTank, {"n_nodes": 4, "volume": 1.0, "height": 0.0}, "height"),
    (HybridStratifiedTank, {"n_nodes": 4, "volume": 1.0, "height": 1.0, "cp": -1.0}, "cp"),
    (GroundSourceHeatPumpEmpirical, {"H_b": -1.0}, "H_b"),
    (GroundSourceHeatPumpEmpirical, {"T_g": 300.0}, "T_g"),
    (GroundSourceHeatPumpEmpirical, {"eta_iu_fan_design": 2.0}, "eta_iu_fan_design"),
    (GroundSourceHeatPumpEmpirical, {"sim_hours": 0}, "sim_hours"),
]


@pytest.mark.parametrize(
    ("cls", "kwargs", "name"), SUBSYSTEM_CASES, ids=lambda x: x.__name__ if isinstance(x, type) else None
)
def test_subsystem_tank_and_empirical_constructors_reject_out_of_range_input(cls, kwargs, name):
    with pytest.raises(ValueError, match=name):
        cls(**kwargs)


@pytest.mark.parametrize("cls", [tmhp.ASHPB_PV_ESS, tmhp.GSHPB_PV_ESS])
@pytest.mark.parametrize(("extra", "name"), [({"eta_inv": 1.5}, "eta_inv"), ({"T_inv_K": 40.0}, "T_inv_K")])
def test_pv_ess_combinations_validate_their_own_arguments(cls, extra, name):
    with pytest.raises(ValueError, match=name):
        cls(pv=PhotovoltaicSystem(), **extra)


@pytest.mark.parametrize(
    "cls",
    [tmhp.ASHPB_STC_tank, tmhp.ASHPB_STC_preheat, tmhp.GSHPB_STC_tank, tmhp.GSHPB_STC_preheat, tmhp.GSHPB_STC_ground],
)
def test_stc_combinations_inherit_the_boiler_validation(cls):
    with pytest.raises(ValueError, match="hp_capacity"):
        cls(stc=SolarThermalCollector(), hp_capacity=-1.0)


@pytest.mark.parametrize("tank_cls", [StratifiedTank, HybridStratifiedTank])
@pytest.mark.parametrize(
    ("kwargs", "name"),
    [
        ({"dt": 0.0}, "dt"),
        ({"dt": -60.0}, "dt"),
        ({"dt": 60.0, "charge_flow": -1e-4}, "charge_flow"),
        ({"dt": 60.0, "draw_flow": NAN}, "draw_flow"),
        ({"dt": 60.0, "T_charge": 150.0}, "T_charge"),
        ({"dt": 60.0, "T_makeup": NAN}, "T_makeup"),
    ],
)
def test_tank_step_rejects_out_of_range_input(tank_cls, kwargs, name):
    tank = tank_cls(4, 0.2, 1.0)
    tank.reset(50.0)
    with pytest.raises(ValueError, match=name):
        tank.step(**kwargs)


def test_empirical_system_update_rejects_bad_load_and_temperature():
    m = GroundSourceHeatPumpEmpirical(sim_hours=48, H_b=50.0)
    m.Q_r_iu, m.T0 = NAN, 10.0
    with pytest.raises(ValueError, match="Q_r_iu"):
        m.system_update()
    m.Q_r_iu, m.T0 = -1000.0, 283.15
    with pytest.raises(ValueError, match="T0"):
        m.system_update()


# --------------------------------------------------------------------------
# analyze_steady
# --------------------------------------------------------------------------
BOILER_STEADY = {
    "ASHPB": dict(T_tank_w=50.0, T0=7.0, Q_ref_tank=3000.0),
    "GSHPB": dict(T_tank_w=45.0, T_source=8.0, Q_ref_tank=3000.0, T0=7.0),
    "WSHPB": dict(T_tank_w=45.0, T_source=8.0, Q_ref_tank=3000.0, T0=7.0),
}
SPACE_STEADY = {
    "ASHP": dict(Q_r_iu=-2000.0, T0=7.0, T_a_room=20.0),
    "GSHP": dict(Q_r_iu=-2000.0, T0=7.0, T_a_room=20.0),
}
STEADY = {**BOILER_STEADY, **SPACE_STEADY}

_TEMP_BAD = (NAN, INF, -INF, 400.0, 293.15, -300.0)
STEADY_CASES = []
for _fam, _good in STEADY.items():
    for _arg, _val in _good.items():
        if _arg.startswith("T"):
            _bads = list(_TEMP_BAD)
            if _arg == "T_tank_w":
                _bads += [-5.0, 120.0]
        elif _arg == "Q_ref_tank":
            _bads = [NAN, INF, -1.0, -3000.0]
        else:  # Q_r_iu: sign is the mode, so only non-finite is invalid
            _bads = [NAN, INF, -INF]
        STEADY_CASES += [(_fam, _arg, bad) for bad in _bads]
STEADY_CASES += [("GSHP", "T_bhe_wall", bad) for bad in (NAN, 300.0)]
STEADY_CASES += [("GSHPB", "T_bhe_wall", bad) for bad in (NAN, 300.0)]
STEADY_CASES += [("GSHP", "ground_flow_lpm", bad) for bad in (NAN, -5.0, 0.0)]
STEADY_CASES += [("ASHPB", "m_dot_w", bad) for bad in (NAN, -0.1)]


@pytest.mark.parametrize(("family", "arg", "bad"), STEADY_CASES, ids=lambda x: str(x))
def test_analyze_steady_rejects_out_of_range_input(models, family, arg, bad):
    kwargs = {**STEADY[family], arg: bad}
    with pytest.raises(ValueError, match=rf"\b{arg}\b"):
        models[family].analyze_steady(**kwargs)


@pytest.mark.parametrize("family", list(BOILER_STEADY))
def test_boiler_zero_load_is_a_valid_off_result_with_failure_reason_none(models, family):
    r = models[family].analyze_steady(**{**BOILER_STEADY[family], "Q_ref_tank": 0.0})
    assert not r["hp_is_on"]
    assert r["failure_reason"] == "none"
    assert r["Q_ref_tank [W]"] == 0.0
    assert r["E_tot [W]"] == 0.0


@pytest.mark.parametrize("family", list(SPACE_STEADY))
def test_space_conditioning_zero_load_matches_the_boiler_contract(models, family):
    r = models[family].analyze_steady(**{**SPACE_STEADY[family], "Q_r_iu": 0.0})
    assert not r["hp_is_on"]
    assert r["failure_reason"] == "none"
    assert r["Q_ref_iu [W]"] == 0.0
    assert r["E_tot [W]"] == 0.0


def test_valid_inputs_that_cannot_be_solved_still_report_failure_reason_not_an_exception(models):
    # Valid (if extreme) conditions: -60 degC outdoor air is above the pressure-ratio ceiling.
    r = models["ASHPB"].analyze_steady(T_tank_w=50.0, T0=-60.0, Q_ref_tank=5000.0)
    assert r["failure_reason"] == "pr_above_max"
    assert not r["hp_is_on"]


def test_dataframe_return_path_is_validated_too(models):
    with pytest.raises(ValueError, match="Q_ref_tank"):
        models["ASHPB"].analyze_steady(T_tank_w=50.0, T0=7.0, Q_ref_tank=NAN, return_dict=False)


# --------------------------------------------------------------------------
# analyze_dynamic
# --------------------------------------------------------------------------
def _boiler_dynamic(**over):
    kw = dict(
        simulation_period_sec=PERIOD,
        dt_s=3600,
        T_tank_w_init_C=55.0,
        dhw_usage_schedule=np.zeros(N_STEPS),
        T0_schedule=np.full(N_STEPS, 5.0),
    )
    kw.update(over)
    return kw


def _space_dynamic(**over):
    kw = dict(
        simulation_period_sec=PERIOD,
        dt_s=3600,
        Q_r_iu_schedule=np.zeros(N_STEPS),
        T0_schedule=np.full(N_STEPS, 7.0),
    )
    kw.update(over)
    return kw


DYNAMIC_BUILDERS = {
    "ASHPB": _boiler_dynamic,
    "GSHPB": _boiler_dynamic,
    "WSHPB": _boiler_dynamic,
    "ASHP": _space_dynamic,
    "GSHP": _space_dynamic,
}

_TIME_GRID_BAD = [
    ("dt_s", 0, "dt_s"),
    ("dt_s", -3600, "dt_s"),
    ("dt_s", NAN, "dt_s"),
    ("simulation_period_sec", 0, "simulation_period_sec"),
    ("simulation_period_sec", NAN, "simulation_period_sec"),
    ("simulation_period_sec", PERIOD + 1, "integer multiple"),
    ("dt_s", 7000, "integer multiple"),
]
_COMMON_SCHEDULE_BAD = [
    ("T0_schedule", np.full(N_STEPS, NAN), "T0_schedule"),
    ("T0_schedule", np.full(N_STEPS, 278.15), "T0_schedule"),
    ("T0_schedule", np.full(N_STEPS - 1, 5.0), "T0_schedule"),
    ("T0_schedule", np.full(N_STEPS + 1, 5.0), "T0_schedule"),
    ("T0_schedule", np.full((N_STEPS, 2), 5.0), "T0_schedule"),
    ("T0_schedule", ["a"] * N_STEPS, "T0_schedule"),
]
_BOILER_ONLY_BAD = [
    ("dhw_usage_schedule", np.full(N_STEPS, -1e-4), "dhw_usage_schedule"),
    ("dhw_usage_schedule", np.full(N_STEPS, NAN), "dhw_usage_schedule"),
    ("dhw_usage_schedule", np.zeros(N_STEPS - 1), "dhw_usage_schedule"),
    ("T_sup_w_schedule", np.full(N_STEPS, 150.0), "T_sup_w_schedule"),
    ("T_sup_w_schedule", np.full(N_STEPS + 1, 15.0), "T_sup_w_schedule"),
    ("T_sur_schedule", np.full(N_STEPS, 293.15), "T_sur_schedule"),
    ("I_DN_schedule", np.full(N_STEPS, -1.0), "I_DN_schedule"),
    ("I_dH_schedule", np.full(N_STEPS, NAN), "I_dH_schedule"),
    ("I_DN_schedule", np.zeros(N_STEPS + 1), "I_DN_schedule"),
    ("T_tank_w_init_C", 120.0, "T_tank_w_init_C"),
    ("T_tank_w_init_C", 328.15, "T_tank_w_init_C"),
    ("T_tank_w_init_C", -1.0, "T_tank_w_init_C"),
    ("T_tank_w_init_C", NAN, "T_tank_w_init_C"),
    ("tank_level_init", 1.5, "tank_level_init"),
    ("tank_level_init", -0.1, "tank_level_init"),
]
_SPACE_ONLY_BAD = [
    ("Q_r_iu_schedule", np.full(N_STEPS, NAN), "Q_r_iu_schedule"),
    ("Q_r_iu_schedule", np.full(N_STEPS, INF), "Q_r_iu_schedule"),
    ("Q_r_iu_schedule", np.zeros(N_STEPS - 1), "Q_r_iu_schedule"),
    ("T_a_room_schedule", np.full(N_STEPS, 293.15), "T_a_room_schedule"),
    ("T_a_room_schedule", np.full(N_STEPS + 1, 20.0), "T_a_room_schedule"),
]
DYNAMIC_CASES = []
for _fam, _builder in DYNAMIC_BUILDERS.items():
    _extra = _BOILER_ONLY_BAD if _builder is _boiler_dynamic else _SPACE_ONLY_BAD
    DYNAMIC_CASES += [(_fam, a, b, m) for (a, b, m) in _TIME_GRID_BAD + _COMMON_SCHEDULE_BAD + _extra]
# Water-source schedule only exists on the WSHPB.
DYNAMIC_CASES += [("WSHPB", "T_source_w_schedule", np.full(N_STEPS, 300.0), "T_source_w_schedule")]


@pytest.mark.parametrize(
    ("family", "arg", "bad", "match"), DYNAMIC_CASES, ids=lambda x: x if isinstance(x, str) else ""
)
def test_analyze_dynamic_rejects_out_of_range_input(models, family, arg, bad, match):
    kwargs = DYNAMIC_BUILDERS[family](**{arg: bad})
    with pytest.raises(ValueError, match=match):
        models[family].analyze_dynamic(**kwargs)


def test_dhw_negative_flow_used_to_heat_the_tank_to_1400_degC(models):
    with pytest.raises(ValueError, match=r"dhw_usage_schedule must be >= 0 at every step, got -0\.0001 at index 0"):
        models["ASHPB"].analyze_dynamic(**_boiler_dynamic(dhw_usage_schedule=np.full(N_STEPS, -1e-4)))


def test_dt_that_does_not_divide_the_period_used_to_give_a_221_degC_tank(models):
    kw = _boiler_dynamic(
        simulation_period_sec=86400, dt_s=7000, dhw_usage_schedule=np.zeros(13), T0_schedule=np.full(13, 5.0)
    )
    with pytest.raises(ValueError, match="simulation_period_sec must be an integer multiple of dt_s"):
        models["ASHPB"].analyze_dynamic(**kw)


def test_valid_dynamic_runs_are_accepted_for_lists_and_float_grids(models):
    kw = _boiler_dynamic(
        simulation_period_sec=float(PERIOD),
        dt_s=3600.0,
        dhw_usage_schedule=[0.0] * N_STEPS,  # plain lists are valid schedules
        T0_schedule=[5.0] * N_STEPS,
    )
    df = models["ASHPB"].analyze_dynamic(**kw)
    assert len(df) == N_STEPS
    assert np.isfinite(df["T_tank_w [°C]"]).all()
    df = models["ASHP"].analyze_dynamic(**_space_dynamic())
    assert len(df) == N_STEPS


# --------------------------------------------------------------------------
# step() / make_initial_state()
# --------------------------------------------------------------------------
_STEP_GOOD = dict(n=0, current_time_s=0.0, T0=5.0, dV_mix_w_out=0.0, T_sup_w=15.0, T_sur=20.0)


@pytest.mark.parametrize(
    ("over", "dt_s", "name"),
    [
        ({"T0": NAN}, 3600, "T0"),
        ({"T0": 278.15}, 3600, "T0"),
        ({"dV_mix_w_out": -1e-4}, 3600, "dV_mix_w_out"),
        ({"dV_mix_w_out": NAN}, 3600, "dV_mix_w_out"),
        ({"T_sup_w": 120.0}, 3600, "T_sup_w"),
        ({"T_sur": 293.15}, 3600, "T_sur"),
        ({"I_DN": -5.0}, 3600, "I_DN"),
        ({"I_dH": NAN}, 3600, "I_dH"),
        ({}, 0.0, "dt_s"),
        ({}, -1.0, "dt_s"),
    ],
)
def test_ashpb_step_rejects_out_of_range_input(models, over, dt_s, name):
    m = models["ASHPB"]
    state = m.make_initial_state(50.0)
    with pytest.raises(ValueError, match=name):
        m.step(state, {**_STEP_GOOD, **over}, dt_s)


def test_ashpb_step_accepts_valid_input_and_defaults_irradiance_to_zero(models):
    m = models["ASHPB"]
    state, row = m.step(m.make_initial_state(50.0), dict(_STEP_GOOD), 3600.0)
    assert math.isfinite(row["T_tank_w [°C]"])


@pytest.mark.parametrize(
    ("args", "name"),
    [
        ((120.0,), "T_tank_w_init_C"),
        ((NAN,), "T_tank_w_init_C"),
        ((50.0, 1.5), "tank_level_init"),
    ],
)
def test_make_initial_state_rejects_out_of_range_input(models, args, name):
    with pytest.raises(ValueError, match=name):
        models["ASHPB"].make_initial_state(*args)


# --------------------------------------------------------------------------
# Helper functions
# --------------------------------------------------------------------------
def test_time_grid_accepts_integer_multiples_in_any_numeric_type():
    assert v.check_time_grid(86400, 3600) == 24
    assert v.check_time_grid(86400.0, 3600.0) == 24
    assert v.check_time_grid(np.int64(86400), np.float64(900.0)) == 96
    assert v.check_time_grid(8760 * 3600, 3600) == 8760
    assert v.check_time_grid(3600, 3600) == 1


@pytest.mark.parametrize(("period", "dt"), [(86400, 7000), (1800, 3600), (86400, 0), (86400, -1), (NAN, 3600)])
def test_time_grid_rejects_everything_else(period, dt):
    with pytest.raises(ValueError):
        v.check_time_grid(period, dt)


@pytest.mark.parametrize("bad", ["5", None, [1.0], NAN, INF])
def test_scalar_checks_reject_non_numbers_with_the_standard_message(bad):
    with pytest.raises(ValueError, match=r"^x must be a finite number > 0, got "):
        v.check_positive("x", bad)


def test_temperature_ranges():
    assert v.check_temperature("T", -60.0) == -60.0  # rare, possible: not blocked
    assert v.check_temperature("T", 0.0, "water") == 0.0
    assert v.check_temperature("T", 100.0, "water") == 100.0
    for kelvin in (273.15, 293.15, 328.15):
        with pytest.raises(ValueError, match="kelvin"):
            v.check_temperature("T", kelvin)
        with pytest.raises(ValueError, match="kelvin"):
            v.check_temperature("T", kelvin, "water")
    with pytest.raises(ValueError, match="degC value"):
        v.check_kelvin("T", 40.0)


def test_efficiency_and_fraction_bounds():
    assert v.check_efficiency("e", 1.0) == 1.0
    for bad in (0.0, 1.0001, -0.1, NAN):
        with pytest.raises(ValueError):
            v.check_efficiency("e", bad)
    assert v.check_fraction("f", 0.0) == 0.0
    with pytest.raises(ValueError):
        v.check_fraction("f", 1.1)
