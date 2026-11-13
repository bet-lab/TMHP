"""Liquid-source boilers (GSHPB, WSHPB): capacity-scaled defaults and cycle closures.

Covers the #96 follow-up for the two boilers:

1. defaults scale with ``hp_capacity`` (UA, source flow, pump, borehole field)
   and the rated COP does not depend on the capacity;
2. the default machine runs its own default set points;
3. a compressor on a speed bound closes the tank heat exchanger against the
   heat it actually delivers;
4. the suction is never hotter than the entering source stream (C11);
5. a thermostat-off row is ``failure_reason == "none"``; only a called step
   without a cycle carries a reason;
6. the evaporator is solved against the source fluid of the same step (G1).
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from tests._fluids import ANTIFREEZE_WITH_WATER_PROPERTIES
from tmhp import GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler
from tmhp._source_boiler import (
    PUMP_POWER_PER_CAPACITY,
    approach_bracket,
    close_source_temperature,
    default_borehole_grid,
    default_source_flow_lpm,
)

FAMILIES = {"GSHPB": GroundSourceHeatPumpBoiler, "WSHPB": WaterSourceHeatPumpBoiler}
RATING_SOURCE_C = {"GSHPB": 0.0, "WSHPB": 10.0}
SOURCE_UA = {"GSHPB": "UA_ground", "WSHPB": "UA_water"}
SOURCE_HEAT = {"GSHPB": "Q_ref_ground [W]", "WSHPB": "Q_ref_water [W]"}


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


@pytest.fixture(scope="module")
def machines() -> dict[tuple[str, float], object]:
    """Default (R290) machines, built once per capacity."""
    return {(fam, cap): _quiet(cls, hp_capacity=cap) for fam, cls in FAMILIES.items() for cap in (3e3, 8e3, 3e4)}


#: Loop liquid for the points at or below 0 °C (B0 and colder). The ground
#: loop's default, 25 % ethylene glycol, runs them as it is. The water-source
#: boiler's default is water (operating limit 3 °C): there the loop gets an
#: antifreeze with the properties of water, which keeps the machine and its
#: numbers those of the default one.
COLD_LOOP: dict[str, dict] = {"GSHPB": {}, "WSHPB": dict(source_fluid=ANTIFREEZE_WITH_WATER_PROPERTIES)}


# ---------------------------------------------------------------------------
# 1. capacity-scaled defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", list(FAMILIES))
@pytest.mark.parametrize("capacity", [3e3, 8e3, 3e4])
def test_defaults_scale_with_capacity(machines, family, capacity):
    m = machines[family, capacity]
    assert m.UA_tank_hx == pytest.approx(capacity / 5.0)
    assert getattr(m, SOURCE_UA[family]) == pytest.approx(0.18 * capacity)
    assert m.E_pmp == pytest.approx(75.0 / 3516.85 * capacity) == pytest.approx(PUMP_POWER_PER_CAPACITY * capacity)
    # 0.75 x capacity at a 3 K loop temperature difference of the loop liquid:
    # 3.58 L/min per kW of water (WSHPB), 3.84 of 25 % ethylene glycol (GSHPB).
    fluid = m.source_fluid
    assert fluid.name == {"GSHPB": "MEG-25%", "WSHPB": "water"}[family]
    assert m.dV_b_f_m3s * 60000.0 == pytest.approx(default_source_flow_lpm(capacity, fluid.rho, fluid.cp))
    assert m.dV_b_f_m3s * fluid.rho * fluid.cp * 3.0 == pytest.approx(0.75 * capacity)
    if family == "GSHPB":
        assert m.dV_b_f_m3s * 60000.0 == pytest.approx(3.844 * capacity / 1000.0, rel=2e-3)
        return
    assert m.dV_b_f_m3s * 60000.0 == pytest.approx(3.583 * capacity / 1000.0, rel=2e-3)


def test_default_8kw_machine_keeps_one_borehole_and_larger_ones_stay_below_50_w_per_m(machines):
    assert (machines["GSHPB", 8e3].N_1, machines["GSHPB", 8e3].N_2, machines["GSHPB", 8e3].H_b) == (1, 1, 200.0)
    assert default_borehole_grid(8e3) == (1, 1)
    assert default_borehole_grid(14e3) == (2, 1)
    assert default_borehole_grid(1e5) == (3, 3)
    for cap in (1e3, 3e3, 8e3, 14e3, 3e4, 1e5):
        n_1, n_2 = default_borehole_grid(cap)
        assert 0.75 * cap / (n_1 * n_2 * 200.0) <= 50.0 + 1e-9
        assert abs(n_1 - n_2) <= 1  # near-square
    big = machines["GSHPB", 3e4]
    assert big.n_boreholes == 4 and big.total_borehole_length == 800.0
    # The water-source coil lengthens instead (one coil, 50 W/m).
    assert machines["WSHPB", 8e3].H_b == 200.0
    assert machines["WSHPB", 3e4].H_b == pytest.approx(0.75 * 3e4 / 50.0)


@pytest.mark.parametrize("family", list(FAMILIES))
def test_rated_cop_does_not_depend_on_capacity(machines, family):
    """B0/W35 (GSHPB) and W10/W35 (WSHPB) at the rated duty: the same cycle at every capacity."""
    cops = []
    for cap in (3e3, 8e3, 3e4):
        r = _quiet(
            machines[family, cap].analyze_steady, T_tank_w=35.0, T_source=RATING_SOURCE_C[family], Q_ref_tank=cap
        )
        assert r["failure_reason"] == "none" and r["capacity_clamped"] is None
        assert r["T_ref_cond_sat_l [°C]"] - 35.0 == pytest.approx(5.0, abs=1e-6)
        cops.append((r["cop_ref [-]"], r["cop_sys [-]"]))
    ref, sys = np.array(cops).T
    assert np.ptp(ref) / ref.mean() < 1e-3 and np.ptp(sys) / sys.mean() < 1e-3
    # Catalogue ranges are 4.2-4.8 (B0/W35) and 5.5-6.3 (W10/W35); the default
    # efficiency map puts the model at the low end. A band, not a regression value.
    assert (4.0 < ref.mean() < 4.6) if family == "GSHPB" else (5.0 < ref.mean() < 5.6)


@pytest.mark.parametrize("family", list(FAMILIES))
@pytest.mark.parametrize("ref", ["R32", "R290", "R134a"])
def test_large_machine_builds_for_every_refrigerant(family, ref):
    """14 kW and above used to fail in the constructor (rated condensing 83 °C at 500 W/K)."""
    m = _quiet(FAMILIES[family], ref=ref, hp_capacity=1e5)
    assert m.reference_state is not None
    assert m.reference_state.T_cond_sat_C == pytest.approx(60.0, abs=1e-6)  # 55 °C + 5 K


def test_caller_values_are_used_as_given_and_aliases_keep_their_order():
    with pytest.warns(DeprecationWarning):
        g = GroundSourceHeatPumpBoiler(
            UA_cond_design=700.0, UA_evap_design=650.0, dV_b_f_lpm=24.0, E_pmp=200.0, N_1=1, t_max_s=86400.0
        )
    assert (g.UA_tank_hx, g.UA_ground, g.E_pmp) == (700.0, 650.0, 200.0)
    assert g.dV_b_f_m3s * 60000.0 == pytest.approx(24.0)
    assert (g.N_1, g.N_2, g.H_b) == (1, 1, 200.0)
    g = _quiet(GroundSourceHeatPumpBoiler, UA_tank=800.0, UA_cond_design=700.0, UA_tank_hx=900.0, t_max_s=86400.0)
    assert g.UA_tank_hx == 900.0
    g = _quiet(GroundSourceHeatPumpBoiler, UA_tank=800.0, UA_cond_design=700.0, t_max_s=86400.0)
    assert g.UA_tank_hx == 800.0
    w = _quiet(WaterSourceHeatPumpBoiler, UA_tank_hx=900.0, UA_water=650.0, dV_b_f_lpm=24.0, E_pmp=200.0, H_b=150.0)
    assert (w.UA_tank_hx, w.UA_water, w.E_pmp, w.H_b) == (900.0, 650.0, 200.0, 150.0)
    assert w.dV_b_f_m3s * 60000.0 == pytest.approx(24.0)


def test_default_constructors_do_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        GroundSourceHeatPumpBoiler(t_max_s=86400.0)
        WaterSourceHeatPumpBoiler()


def test_overloaded_field_laminar_flow_and_unreachable_set_point_warn():
    # 30 kW on the caller's single 200 m borehole: 112 W/m.
    with pytest.warns(UserWarning, match=r"112 W per metre.*above 80 W/m"):
        GroundSourceHeatPumpBoiler(hp_capacity=3e4, N_1=1, t_max_s=86400.0)
    # The default flow of 8 kW split over 16 boreholes: Re 440 with the default
    # 25 % ethylene glycol (816 with water at its 0 °C design viscosity, 1463 at 20 °C).
    with pytest.warns(UserWarning, match=r"laminar, Re = 4\d\d "):
        GroundSourceHeatPumpBoiler(N_1=4, N_2=4, H_b=50.0, t_max_s=86400.0)
    with pytest.warns(UserWarning, match=r"laminar, Re = 8\d\d "):
        GroundSourceHeatPumpBoiler(N_1=4, N_2=4, H_b=50.0, t_max_s=86400.0, source_fluid="water")
    # 500 W/K at 8 kW is a 16 K approach: 65 + 16 = 81 °C is above the R410A critical point.
    # (Without the condensing limit: with it this machine is refused at its rating point.)
    for cls in FAMILIES.values():
        with pytest.warns(UserWarning, match=r"critical temperature of R410A \(71\.3 °C\)"):
            cls(ref="R410A", UA_tank_hx=500.0, t_max_s=86400.0, T_cond_max=None)
    # A caller-given flow is theirs to judge.
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        GroundSourceHeatPumpBoiler(N_1=4, N_2=4, H_b=50.0, ground_flow_ref_lpm=28.7, t_max_s=86400.0)


# ---------------------------------------------------------------------------
# 3. speed-bound closure of the tank heat exchanger; 4. superheat cap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", list(FAMILIES))
def test_min_speed_clamp_closes_tank_hx_on_delivered_heat(machines, family):
    m = machines[family, 8e3]
    requests = (400.0, 800.0, 2000.0)
    results = [_quiet(m.analyze_steady, T_tank_w=50.0, T_source=10.0, Q_ref_tank=q, T0=10.0) for q in requests]
    for request, r in zip(requests, results, strict=True):
        assert r["failure_reason"] == "none" and r["capacity_clamped"] == "min"
        assert r["cmp_rpm [rpm]"] == pytest.approx(60.0 * m.rps_min)
        # The row is the mean over the on/off cycling period (tmhp.cycling);
        # ``Q_min [W]`` is what the running machine delivers.
        assert r["Q_ref_tank [W]"] == pytest.approx(request, rel=1e-12)
        delivered = r["Q_min [W]"]
        assert delivered > 2000.0  # more than any of the requests
        # The condenser runs at the approach of the heat it delivers, not of the request.
        assert r["T_ref_cond_sat_l [°C]"] - 50.0 == pytest.approx(delivered / m.UA_tank_hx, abs=1e-5)
        assert r["dT_ref_tank [K]"] == pytest.approx(delivered / m.UA_tank_hx, abs=1e-5)
        assert abs(r["tank_hx_residual [W]"]) < 1e-2
        assert r["Q_tank_load [W]"] < delivered
    # On the floor the machine no longer knows the request: one operating point.
    for key in ("Q_min [W]", "T_ref_cond_sat_l [°C]", "T_ref_evap_sat [°C]", "cmp_rpm [rpm]"):
        assert results[0][key] == pytest.approx(results[2][key], rel=1e-6)


@pytest.mark.parametrize("family", list(FAMILIES))
@pytest.mark.parametrize(
    ("ref", "T_source", "T_tank"),
    [("R290", 0.0, 55.0), ("R1234yf", 10.0, 55.0), ("R717", 10.0, 50.0)],
)
def test_request_below_the_speed_floor_runs_at_the_floor_capacity(family, ref, T_source, T_tank):
    """A request below the minimum-speed capacity runs at that capacity, whatever the refrigerant.

    The evaporator approach of these points is about 1 K against the
    capacity-scaled source conductance; the approach search used to stop at
    1e-4 K, which left about 0.1 W of the source balance open and failed the
    0.01 W check that follows (``hx_not_converged``).
    """
    m = _quiet(FAMILIES[family], ref=ref, **(COLD_LOOP[family] if T_source < 5.0 else {}))
    r = _quiet(m.analyze_steady, T_tank_w=T_tank, T_source=T_source, Q_ref_tank=400.0, T0=10.0)
    assert r["failure_reason"] == "none" and r["hp_is_on"] and r["capacity_clamped"] == "min"
    # Reported as the mean of cycling at that capacity: the request is delivered.
    assert r["Q_min [W]"] > 400.0 and r["Q_ref_tank [W]"] == pytest.approx(400.0, rel=1e-12)
    assert abs(r["tank_hx_residual [W]"]) < 1e-2


@pytest.mark.parametrize("family", list(FAMILIES))
def test_speed_floor_closure_lifts_a_small_lift_cycle_off_the_pressure_ratio_floor(family):
    """R32 from a 20 °C source into a 30 °C tank, far below the floor capacity.

    At the approach of the 400 W request the pressure ratio is below
    ``PR_cycle_min``; at the approach of the 5.5 kW the compressor delivers at
    its speed floor it is not. The closure is tried before the floor is taken
    as binding, so the point is one closed operating point (it used to fall
    between the two formulations and fail the source balance).
    """
    m = _quiet(FAMILIES[family], ref="R32")
    r = _quiet(m.analyze_steady, T_tank_w=30.0, T_source=20.0, Q_ref_tank=400.0, T0=10.0)
    assert r["failure_reason"] == "none" and r["capacity_clamped"] == "min"
    ratio = r["P_ref_cmp_out [Pa]"] / r["P_ref_cmp_in [Pa]"]
    assert ratio > m.PR_cycle_min + 0.05
    delivered = r["Q_min [W]"]  # the running machine; the row's heat is the cycling mean
    assert r["T_ref_cond_sat_l [°C]"] - 30.0 == pytest.approx(delivered / m.UA_tank_hx, abs=1e-4)
    assert abs(r["tank_hx_residual [W]"]) < 1e-2


@pytest.mark.parametrize("family", list(FAMILIES))
def test_modulating_point_keeps_the_requested_approach(machines, family):
    m = machines[family, 8e3]
    r = _quiet(m.analyze_steady, T_tank_w=50.0, T_source=10.0, Q_ref_tank=6000.0, T0=10.0)
    assert r["capacity_clamped"] is None and r["Q_ref_tank [W]"] == pytest.approx(6000.0, rel=1e-9)
    assert r["dT_ref_tank [K]"] == pytest.approx(6000.0 / m.UA_tank_hx, abs=1e-9)
    assert abs(r["tank_hx_residual [W]"]) < 1e-6


@pytest.mark.parametrize("family", list(FAMILIES))
@pytest.mark.parametrize("ref", ["R290", "R134a"])
def test_suction_is_not_hotter_than_the_entering_source(family, ref):
    """C11: at low load the evaporator approach is below the nominal superheat."""
    m = _quiet(FAMILIES[family], ref=ref, **COLD_LOOP[family])
    for T_source, T_tank, load in ((0.0, 50.0, 2000.0), (10.0, 55.0, 400.0)):
        r = _quiet(m.analyze_steady, T_tank_w=T_tank, T_source=T_source, Q_ref_tank=load, T0=10.0)
        assert r["failure_reason"] == "none"
        approach = T_source - r["T_ref_evap_sat [°C]"]
        assert approach < m.dT_superheat  # the cap binds here
        # 0.3 K margin: the cap leaves dT_hx_min (0.5 K) less the pump heat (0.09 K).
        assert r["T_ref_cmp_in [°C]"] <= T_source - 0.3
        assert r["T_ref_cmp_in [°C]"] >= r["T_ref_evap_sat [°C]"] - 1e-9


def test_superheat_is_untouched_where_the_approach_allows_it(machines):
    m = machines["GSHPB", 8e3]
    r = _quiet(m.analyze_steady, T_tank_w=35.0, T_source=0.0, Q_ref_tank=8000.0)
    assert r["T_ref_cmp_in [°C]"] - r["T_ref_evap_sat [°C]"] == pytest.approx(m.dT_superheat, abs=1e-6)


def test_low_speed_balance_below_1_K_approach_is_solved():
    """R134a at 25 % against the capacity-scaled evaporator balances at 0.97 K.

    The approach search started at 1 K, reported ``hx_not_converged`` and took
    its 1024-point scan to say so.
    """
    m = _quiet(GroundSourceHeatPumpBoiler, ref="R134a", t_max_s=86400.0)
    r = _quiet(m.analyze_steady, T_tank_w=55.0, T_source=-5.0, Q_ref_tank=2000.0)
    assert r["failure_reason"] == "none"
    rho_c = m.source_fluid.rho * m.source_fluid.cp
    approach = r["T_bhe_f_out [°C]"] + r["E_pmp [W]"] / (m.dV_b_f_m3s * rho_c) - r["T_ref_evap_sat [°C]"]
    assert 0.5 < approach < 1.0


def test_approach_bracket_decides_without_scanning():
    calls = []

    def short(x):  # heat exchanger short at every approach
        calls.append(x)
        return 100.0 - x

    with pytest.raises(ValueError, match="cannot carry"):
        approach_bracket(short)
    assert len(calls) == 2
    calls.clear()

    def low(x):  # balance at 0.6 K
        calls.append(x)
        return 0.6 - x

    assert approach_bracket(low) == (0.05, 1.0)
    assert len(calls) == 3
    # A residual rising through the band is still bracketed by the sampled search.
    lo, hi = approach_bracket(lambda x: x - 10.0)
    assert lo < 10.0 <= hi or lo <= 10.0 < hi


# ---------------------------------------------------------------------------
# 2. the default machine runs its default set points; 5. off rows; 6. same-step source
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def default_runs() -> dict[str, tuple[object, object]]:
    """Default machines over 3 h (dt 600 s): one morning draw, tank from 56 °C."""
    dt, n = 600.0, 18
    t_h = (np.arange(n) + 0.5) * dt / 3600.0
    draw = 10.0 / 60000.0 * np.exp(-0.5 * ((t_h - 0.75) / 0.25) ** 2)
    T0 = np.full(n, 5.0)
    out = {}
    for fam, cls in FAMILIES.items():
        m = _quiet(cls, dt_s=dt, t_max_s=86400.0)
        kwargs = dict(T_source_w_schedule=np.where(np.arange(n) % 2 == 0, 12.0, 6.0)) if fam == "WSHPB" else {}
        out[fam] = (m, _quiet(m.analyze_dynamic, n * dt, dt, 56.0, draw, T0, **kwargs))
    return out


@pytest.mark.parametrize("family", list(FAMILIES))
def test_default_machine_charges_to_its_default_upper_set_point(default_runs, family):
    m, df = default_runs[family]
    # The default refrigerant is R290: its envelope (75 °C condensing) holds the
    # default 65 °C set point, which R410A's (65 °C) does not.
    assert (m.ref, m.T_cond_max, m.T_tank_w_upper_bound) == ("R290", 75.0, 65.0)
    assert (df["failure_reason"] == "none").all()
    assert df["hp_is_on"].any() and not df["hp_is_on"].all()
    # The thermostat stops the compressor at the upper bound inside a step.
    assert df["T_tank_w [°C]"].max() == pytest.approx(m.T_tank_w_upper_bound, abs=0.1)
    ran = df["hp_is_on"].astype(bool)
    # It condenses at most 5 K above the 65 °C set point; the envelope never reduces the duty.
    assert df.loc[ran, "T_ref_cond_sat_l [°C]"].max() <= m.T_tank_w_upper_bound + 5.0 + 1e-6
    assert df.loc[ran, "T_ref_cond_sat_l [°C]"].max() < m.T_cond_max - 4.0
    bindings = set(df["capacity_binding"].dropna()) if "capacity_binding" in df.columns else set()
    # The ground loop (glycol) runs at the nameplate duty all the way. The
    # water-source loop is water: on the 6 °C source-water steps the duty is
    # turned down to keep the loop at its 3 °C limit (the source-fluid limit).
    assert bindings == (set() if family == "GSHPB" else {"source_fluid_freezing"})
    if family == "WSHPB":
        limited = df["capacity_binding"] == "source_fluid_freezing"
        assert (df.loc[limited, "Ts [°C]"] == 6.0).all()
        assert (df.loc[ran, "T_bhe_f_in_running [°C]"] >= 3.0 - 0.02).all()
    assert df.loc[ran, "T_ref_cmp_out [°C]"].max() <= m.T_dis_max + 0.02


@pytest.mark.parametrize("family", list(FAMILIES))
def test_former_default_refrigerant_stops_below_the_default_set_point(family):
    """R410A against the default 55-65 °C band: the envelope (65 °C condensing) caps the set point at 59.9 °C."""
    dt, n = 60.0, 12
    with pytest.warns(UserWarning, match=r"T_tank_w_upper_bound=65 °C is above .* R410A .* 59\.9 °C"):
        m = FAMILIES[family](ref="R410A", dt_s=dt, t_max_s=86400.0)
    assert m.T_tank_w_setpoints_requested == (55.0, 65.0)
    assert m.T_tank_w_upper_bound == pytest.approx(59.93, abs=0.01)
    df = _quiet(m.analyze_dynamic, n * dt, dt, 54.9, np.zeros(n), np.full(n, 5.0))
    assert (df["failure_reason"] == "none").all()
    assert df["hp_is_on"].any() and not df["hp_is_on"].all()
    assert df["T_tank_w [°C]"].max() == pytest.approx(m.T_tank_w_upper_bound, abs=0.05)


@pytest.mark.parametrize("family", list(FAMILIES))
def test_thermostat_off_rows_are_not_failures(default_runs, family):
    _m, df = default_runs[family]
    off = ~df["hp_is_on"].astype(bool)
    assert off.any()
    assert (df.loc[off, "failure_reason"] == "none").all()
    for key in ("Q_ref_tank [W]", "E_cmp [W]", "E_pmp [W]", "E_tot [W]", SOURCE_HEAT[family], "Q_bhe [W]"):
        assert (df.loc[off, key] == 0.0).all(), key
    for key in ("cop_ref [-]", "cop_sys [-]", "T_ref_evap_sat [°C]", "T_ref_cmp_out [°C]", "T_ref_cond_sat_l [°C]"):
        assert df.loc[off, key].isna().all(), key
    assert (df.loc[off, "runtime_fraction [-]"] == 0.0).all()


@pytest.mark.parametrize("family", list(FAMILIES))
def test_called_step_without_a_cycle_carries_the_reason(family):
    """An undersized tank heat exchanger (16 K approach) has no R410A cycle above ~55 °C.

    Without the condensing limit, which refuses this machine at its rating point.
    """
    dt, n = 300.0, 3
    m = _quiet(FAMILIES[family], ref="R410A", UA_tank_hx=500.0, dt_s=dt, t_max_s=86400.0, T_cond_max=None)
    df = _quiet(m.analyze_dynamic, n * dt, dt, 54.9, np.zeros(n), np.full(n, 5.0))
    failed = df["failure_reason"] != "none"
    assert failed.any() and (df.loc[failed, "failure_reason"] == "cycle_invalid").all()
    assert not df.loc[failed, "hp_is_on"].any() and not df.loc[failed, "converged"].astype(bool).any()
    assert (df.loc[failed, "Q_ref_tank [W]"] == 0.0).all() and (df.loc[failed, "E_cmp [W]"] == 0.0).all()
    assert df.loc[failed, "cop_ref [-]"].isna().all()
    # Called and below the upper bound: these are not thermostat-off rows.
    assert (df.loc[failed, "T_tank_w [°C]"] < m.T_tank_w_upper_bound - 1.0).all()


@pytest.mark.parametrize("family", list(FAMILIES))
def test_evaporator_is_solved_against_the_source_fluid_of_the_same_step(default_runs, family):
    m, df = default_runs[family]
    ran = df["hp_is_on"].astype(bool)
    assert ran.sum() >= 3
    rows = df[ran]
    # G1: in every running row the refrigerant evaporates below the fluid entering the heat pump ...
    assert (rows["T_ref_evap_sat [°C]"] < rows["T_bhe_f_out_running [°C]"] - 0.3).all()
    assert (rows["T_ref_evap_sat [°C]"] < rows["T_bhe_f_out [°C]"] - 0.3).all()
    assert (rows["T_ref_cmp_in [°C]"] <= rows["T_bhe_f_out_running [°C]"] - 0.3).all()
    # ... and the evaporator balance closes on that same fluid (pump heat included).
    C = m.dV_b_f_m3s * m.source_fluid.rho * m.source_fluid.cp
    eps = -np.expm1(-getattr(m, SOURCE_UA[family]) / C)
    fraction = rows["runtime_fraction [-]"].to_numpy(float)
    q_run = rows[SOURCE_HEAT[family]].to_numpy(float) / fraction
    T_in = rows["T_bhe_f_out_running [°C]"].to_numpy(float) + (rows["E_pmp [W]"].to_numpy(float) / fraction) / C
    q_hx = eps * C * (T_in - rows["T_ref_evap_sat [°C]"].to_numpy(float))
    np.testing.assert_allclose(q_hx, q_run, rtol=2e-3)


def test_wshpb_cycle_follows_the_source_water_schedule_of_its_own_step(default_runs):
    """The source alternates 12/6 °C: the running fluid, not the previous step's, feeds the cycle."""
    _m, df = default_runs["WSHPB"]
    assert df["Ts [°C]"].tolist() == [12.0, 6.0] * 9
    assert (df["T_bhe [°C]"] == df["Ts [°C]"]).all()
    ran = df["hp_is_on"].astype(bool)
    warm, cold = df[ran & (df["Ts [°C]"] == 12.0)], df[ran & (df["Ts [°C]"] == 6.0)]
    assert len(warm) and len(cold)
    # The coil never returns fluid warmer than the water it takes heat from ...
    assert (df.loc[ran, "T_bhe_f_out_running [°C]"] < df.loc[ran, "Ts [°C]"]).all()
    assert (df["T_bhe_f_out [°C]"] <= df["Ts [°C]"] + 1e-9).all()
    # ... and stays within 1.5 K of it (NTU 2.2 at the default flow).
    assert (df.loc[ran, "Ts [°C]"] - df.loc[ran, "T_bhe_f_out_running [°C]"] < 1.5).all()
    assert warm["T_ref_evap_sat [°C]"].min() > cold["T_ref_evap_sat [°C]"].max()
    # The first step uses the schedule, not the constructor's Ts (16 °C).
    assert df["T_bhe_f_out_running [°C]"].iloc[int(np.argmax(ran))] < 12.0


def test_same_inputs_give_the_same_table_twice(default_runs):
    m, first = default_runs["GSHPB"]
    n, dt = len(first), 600.0
    t_h = (np.arange(n) + 0.5) * dt / 3600.0
    draw = 10.0 / 60000.0 * np.exp(-0.5 * ((t_h - 0.75) / 0.25) ** 2)
    second = _quiet(m.analyze_dynamic, n * dt, dt, 56.0, draw, np.full(n, 5.0))
    numeric = first.select_dtypes("number").columns
    np.testing.assert_allclose(first[numeric].to_numpy(float), second[numeric].to_numpy(float), rtol=1e-9, atol=1e-9)


def test_close_source_temperature_secant_and_failure_paths():
    # outlet = 10 - 0.002 Q, Q = 5000 + 30 (T - 10): fixed point at T = 0.5660...
    def solve(T):
        return "ok", {"Q": 5000.0 + 30.0 * (T - 10.0)}

    outcome, result = close_source_temperature(solve, lambda r: 10.0 - 0.002 * r["Q"], 0.0, 10.0)
    assert outcome == "ok"
    T = (result["Q"] - 5000.0) / 30.0 + 10.0
    assert 10.0 - 0.002 * result["Q"] == pytest.approx(T, abs=1e-4)

    # No cycle at the guess, a cycle at the unloaded temperature.
    seen = []

    def solve_cold(T):
        seen.append(T)
        return ("fail", None) if T < 4.0 else ("ok", {"Q": 100.0})

    outcome, result = close_source_temperature(solve_cold, lambda r: 9.9, 0.0, 10.0)
    assert outcome == "ok" and result == {"Q": 100.0} and seen[:2] == [0.0, 10.0]
    # No cycle anywhere: the failed outcome is returned.
    assert close_source_temperature(lambda T: ("fail", None), lambda r: 0.0, 0.0, 10.0) == ("fail", None)
