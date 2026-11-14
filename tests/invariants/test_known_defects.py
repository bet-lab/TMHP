"""One test per defect found in the #96 diagnosis, asserting the *correct* behaviour.

A test that fails on the current model carries
``xfail(strict=True, reason="#96 <ID>: ...")``. The PR that fixes defect
``<ID>`` removes that mark; strict mode turns an unexpected pass into a
failure, so a fix cannot land silently and a regression cannot hide.
"""

from __future__ import annotations

import math
import warnings

import numpy as np
import pytest

import tmhp
from tests._fluids import ANTIFREEZE_WITH_WATER_PROPERTIES
from tmhp.compressor_envelope import RATED_SPEED_SEARCH_MAX
from tmhp.constants import c_w, rho_w

from ._checks import KnownDefectObserved, check_running_cycle, expect
from ._families import FAMILIES, SteadyCase, is_reported_running, num


def defect(defect_id: str, why: str) -> pytest.MarkDecorator:
    """Strict xfail that absorbs only the asserted defect.

    Only the statement that asserts the defect raises
    :class:`KnownDefectObserved` (via :func:`expect`). Every other failure —
    a plain ``assert`` elsewhere in the test, an ``AssertionError`` or other
    exception from model code, a failed scenario precondition
    (:func:`require`) — is not absorbed by the mark and fails the test.
    """
    return pytest.mark.xfail(strict=True, raises=KnownDefectObserved, reason=f"#96 {defect_id}: {why}")


def require(condition: object, message: str) -> None:
    """Scenario precondition; failing it is a broken test, never the expected defect."""
    if not condition:
        pytest.fail(f"scenario precondition failed: {message}")


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


# ---------------------------------------------------------------------------
# refrigerant cycle (steady state)
# ---------------------------------------------------------------------------


# C1 fixed by the 0 < eta <= 1 and validity-domain guard (#101).
def test_c1_default_efficiencies_stay_in_unit_interval_over_searched_range():
    """Every default efficiency evaluated where the solver searches is in (0, 1] or rejected.

    The solvers evaluate efficiencies through ``_eval_eff``; a value it
    rejects (``InvalidCompressorEfficiency``) never reaches a cycle, so a
    guard that rejects instead of clamping also satisfies this test.
    """
    from tmhp import compressor_efficiency as ce

    rps_lo = min(f.cls.__init__.__kwdefaults__.get("rps_min", 15.0) for f in FAMILIES.values())
    # rps_max defaults to None (derived from the rated speed); the rated-speed
    # search itself evaluates the efficiencies up to RATED_SPEED_SEARCH_MAX.
    rps_hi = max(f.cls.__init__.__kwdefaults__.get("rps_max") or RATED_SPEED_SEARCH_MAX for f in FAMILIES.values())
    pr_hi = max(f.cls.__init__.__kwdefaults__.get("PR_cycle_max", 20.0) for f in FAMILIES.values())
    functions = {
        "eta_vol_default": ce.eta_vol_default,
        "eta_isen_default": ce.eta_isen_default,
        "eta_em_default": ce.eta_em_default,
        "make_eta_vol(35)": ce.make_eta_vol(35.0),
        "make_eta_isen(35)": ce.make_eta_isen(35.0),
        "make_eta_em(35)": ce.make_eta_em(35.0),
    }
    bad = []
    for name, fn in functions.items():
        for pr in np.linspace(1.5, pr_hi, 25):
            for rps in np.linspace(rps_lo, rps_hi, 28):
                try:
                    value = ce._eval_eff(fn, float(pr), float(rps))
                except ce.InvalidCompressorEfficiency:
                    continue
                if not 0.0 < value <= 1.0:
                    bad.append((name, round(float(pr), 2), round(float(rps), 1), round(value, 4)))
    expect(not bad, f"{len(bad)} efficiency values outside (0, 1], e.g. {bad[:5]}")


# C2 fixed by the outdoor-approach band search: the feasible ~2.3-5 K window (the coil
# is short at the largest airflow below it and over-absorbs at the smallest above it)
# is solved from the fan-limit capacity margins instead of hoped for by a bounded Brent.
def test_c2_ashpb_r32_solves_a7_w55():
    fam = FAMILIES["ASHPB"]
    case = SteadyCase(T_source=-7.0, T_demand=55.0, load_W=7500.0)
    # The point discharges at 234 degC (eta_is 0.34): outside the compressor
    # envelope even under the discharge-temperature control. C2 is about the
    # search finding the narrow band, so look at it with the limit off.
    _, r = fam.run("R32", case, T_dis_max=None)
    expect(is_reported_running(r), f"failure_reason={r.get('failure_reason')!r} Q={r.get('Q_ref_tank [W]')}")
    expect(num(r, "Q_ref_tank [W]") > 0.5 * case.load_W, 'num(r, "Q_ref_tank [W]") > 0.5 * case.load_W')
    assert check_running_cycle(fam, r, case) == []


# C5 scenario fixed by the guard (#101): eta_is is held at the PR 7.15 boundary (0.42),
# so the minimum-speed clamp no longer over-delivers; 6.4 kW at 68.8 rev/s, discharge 141.7 °C.
def test_c5_wshpb_does_not_report_degenerate_efficiency_as_success():
    _, r = FAMILIES["WSHPB"].run("R134a", SteadyCase(T_source=5.0, T_demand=65.0, load_W=6400.0))
    eta = num(r, "eta_cmp_isen [-]")
    degenerate = is_reported_running(r) and math.isfinite(eta) and eta < 0.2
    expect(not degenerate, f"eta_is={eta:.4g} Q={num(r, 'Q_ref_tank [W]'):.0f} W reported as success")


# C6 fixed by the compressor operating envelope (T_dis_max, R32 150 °C): the point
# discharged at 201.6 °C and is now discharge_temp_above_max (no smaller duty runs).
def test_c6_ashpb_discharge_temperature_limit():
    _, r = FAMILIES["ASHPB"].run("R32", SteadyCase(T_source=0.0, T_demand=60.0, load_W=15000.0))
    T_dis = num(r, "T_ref_cmp_out [°C]")
    expect((not is_reported_running(r)) or T_dis <= 150.0, f"T_cmp_out={T_dis:.1f} °C reported as success")


# C11 fixed: the GSHPB/WSHPB suction superheat is capped at the evaporator approach
# less dT_hx_min (it was +1.6 K above the source fluid here).
def test_c11_wshpb_suction_not_hotter_than_source():
    # The speed-floor point discharges above the R32 limit and is refused by the
    # compressor envelope; the defect is in the suction state, so look at it with
    # the discharge check off.
    # A -5 degC loop is an antifreeze loop (water would be refused: source_fluid_freezing).
    case = SteadyCase(T_source=-5.0, T_demand=55.0, load_W=400.0)
    _, r = FAMILIES["WSHPB"].run("R32", case, T_dis_max=None, source_fluid=ANTIFREEZE_WITH_WATER_PROPERTIES)
    require(is_reported_running(r), f"expected a running result, got {r.get('failure_reason')!r}")
    expect(num(r, "T_ref_cmp_in [°C]") <= -5.0 + 0.05, 'num(r, "T_ref_cmp_in [°C]") <= -5.0 + 0.05')


def test_c4_gshp_heating_converges_with_consistent_indoor_balance():
    """#96 C4 (fixed): GSHP heating 3 kW used to return hx_not_converged with
    air side 1,592 W vs refrigerant 3,000 W."""
    # The issue's call leaves T_bhe_wall at its default, so call the model directly.
    model = FAMILIES["GSHP-heat"].build("R32")
    r = model.analyze_steady(Q_r_iu=-3000.0, T0=0.0, T_a_room=20.0)
    assert r["converged"] is True and r["failure_reason"] == "none", r["failure_reason"]
    Q_ref = num(r, "Q_ref_iu [W]")
    Q_air = num(r, "Q_a_iu_out [W]") - num(r, "Q_a_iu_in [W]")
    E_fan = num(r, "E_iu_fan [W]", 0.0)
    # Air gains the refrigerant heat; at most the fan heat on top.
    assert Q_ref * 0.98 <= Q_air <= (Q_ref + E_fan) * 1.02, f"Q_air={Q_air:.0f} Q_ref={Q_ref:.0f} E_fan={E_fan:.0f}"


def test_c4b_gshp_borehole_wall_temperature_changes_result():
    """#96 C4b (fixed): analyze_steady used to ignore T_bhe_wall (5 and 25 °C identical)."""
    cold = FAMILIES["GSHP-heat"].build("R32").analyze_steady(Q_r_iu=-3000.0, T0=0.0, T_a_room=20.0, T_bhe_wall=5.0)
    warm = FAMILIES["GSHP-heat"].build("R32").analyze_steady(Q_r_iu=-3000.0, T0=0.0, T_a_room=20.0, T_bhe_wall=25.0)
    keys = ("T_ref_evap_sat [°C]", "T_bhe_f_out [°C]", "E_cmp [W]")
    assert any(abs(num(cold, k) - num(warm, k)) > 0.1 for k in keys), {k: (cold.get(k), warm.get(k)) for k in keys}


# ---------------------------------------------------------------------------
# boilers, tanks and subsystems (dynamic)
# ---------------------------------------------------------------------------

T_SUP = 15.0


def _flat(n: int, value: float) -> np.ndarray:
    return np.full(n, float(value))


def _off_rows(df) -> np.ndarray:
    """Rows where no refrigerant cycle ran (off, or 'on' with zero compressor power)."""
    E = df["E_cmp [W]"].to_numpy(dtype=float)
    on = df["hp_is_on"].astype(bool).to_numpy()
    return (~on) | ~(np.nan_to_num(E) > 0.0)


def _gshpb_failing_run():
    """Default GSHPB from 50 °C: the cycle fails once the tank passes ~56 °C."""
    dt, n = 600, 4
    m = tmhp.GroundSourceHeatPumpBoiler(dt_s=dt, t_max_s=86400.0)
    df = m.analyze_dynamic(n * dt, dt, 50.0, np.zeros(n), _flat(n, 5.0))
    return m, df, dt


def test_b1_gshpb_failed_cycle_puts_no_heat_in_tank():
    m, df, dt = _gshpb_failing_run()
    off = _off_rows(df)
    require(off.any(), "scenario must contain a step without a running cycle")
    q_load = np.nan_to_num(df["Q_tank_load [W]"].to_numpy(dtype=float))
    expect(np.all(q_load[off] == 0.0), f"Q_tank_load on cycle-less rows: {q_load[off].tolist()}")
    bal = _tank_balance(df, m.C_tank, 50.0, dt)
    expect(abs(bal["residual_J"]) < 0.01 * max(bal["heat_J"], 1.0), bal)


@pytest.mark.parametrize("family", ["GSHPB", "WSHPB"])
def test_t14_off_rows_have_nan_cop(family):
    dt, n = 600, 4
    if family == "GSHPB":
        m = tmhp.GroundSourceHeatPumpBoiler(dt_s=dt, t_max_s=86400.0)
        df = m.analyze_dynamic(n * dt, dt, 50.0, np.zeros(n), _flat(n, 5.0))
    else:
        m = tmhp.WaterSourceHeatPumpBoiler(dt_s=dt, t_max_s=86400.0)
        df = m.analyze_dynamic(n * dt, dt, 50.0, np.zeros(n), _flat(n, 5.0), T_source_w_schedule=_flat(n, 8.0))
    off = _off_rows(df)
    require(off.any(), "scenario must contain an off row")
    for key in ("cop_ref [-]", "cop_sys [-]"):
        leaked = df.loc[off, key].to_numpy(dtype=float)
        expect(np.all(np.isnan(leaked)), f"{key} on off rows: {leaked.tolist()}")


def _tank_balance(df, C_tank, T_init, dt, **kw):
    from ._checks import tank_energy_residual

    return tank_energy_residual(df, C_tank, T_init, dt, T_ref=T_SUP, **kw)


def _ashpb_dynamic(model, hours: float, dt: int, T_init: float, draw_lpm, T0: float = 7.0, **schedules):
    n = int(hours * 3600 // dt)
    draw = draw_lpm(np.arange(n) * dt / 3600.0) / 60000.0 if callable(draw_lpm) else np.zeros(n)
    df = model.analyze_dynamic(
        n * dt,
        dt,
        T_init,
        draw,
        _flat(n, T0),
        T_sup_w_schedule=_flat(n, T_SUP),
        T_sur_schedule=_flat(n, 20.0),
        **{k: (v(n) if callable(v) else v) for k, v in schedules.items()},
    )
    return df, n


def test_tank_balance_baseline_ashpb_always_full():
    """Sanity check of the bookkeeping itself: the always-full ASHPB closes to round-off."""
    m = tmhp.AirSourceHeatPumpBoiler(ref="R32")
    df, _ = _ashpb_dynamic(m, 2.0, 600, 55.0, lambda h: np.where((h >= 0.15) & (h < 0.5), 8.0, 0.0))
    bal = _tank_balance(df, m.C_tank, 55.0, 600)
    assert bal["heat_J"] > 0 and bal["throughput_J"] > 0
    assert abs(bal["residual_J"]) < 1e-4 * bal["heat_J"], bal


# T1 fixed for ASHPB: the level of a level-varying tank comes from the flows its energy
# balance uses, the refill valve is followed inside the step (it refilled nothing when a
# whole step of make-up did not fit) and the draw stops at TANK_LEVEL_MIN; was level
# pinned at 0.001, +1.60 kWh (+11 %) and 1.33 tank volumes of water in 3 h.
def test_t1_partial_fill_conserves_mass_and_energy():
    m = tmhp.AirSourceHeatPumpBoiler(tank_always_full=False)
    df, _ = _ashpb_dynamic(m, 3.0, 600, 60.0, lambda h: np.where(h < 1.0, 8.0, 0.0), T0=5.0)
    T = df["T_tank_w [°C]"].to_numpy(dtype=float)
    expect(np.all((T > 0.0) & (T < 100.0)), f"tank temperature range {T.min():.1f}..{T.max():.1f} °C")
    bal = _tank_balance(df, m.C_tank, 60.0, 600, always_full=False)
    expect(abs(bal["mass_residual"]) < 0.01, bal)
    expect(abs(bal["residual_J"]) < 0.01 * (bal["heat_J"] + bal["throughput_J"]), bal)


# T1 fixed for GSHPB/WSHPB: the reported level and tank flows are the ones the solved
# balance carried (tank_step_flows); they were those of the unlimited draw, so a tank
# drained below its make-up reported level 0, +9.9 kWh (+32 %) in 1 h.
@pytest.mark.parametrize("family", ["GSHPB", "WSHPB"])
def test_t1_ground_and_water_boilers_stop_the_draw_at_the_floor(family):
    dt, n = 600, 6
    cls = tmhp.GroundSourceHeatPumpBoiler if family == "GSHPB" else tmhp.WaterSourceHeatPumpBoiler
    m = cls(
        dt_s=dt,
        t_max_s=86400.0,
        UA_tank_hx=2000.0,
        tank_always_full=False,
        tank_level_lower_bound=0.2,
        dV_tank_w_in_refill=0.0001,
    )
    extra = {"T_source_w_schedule": _flat(n, 10.0)} if family == "WSHPB" else {}
    df = m.analyze_dynamic(
        n * dt,
        dt,
        54.0,
        _flat(n, 15.0 / 60000.0),
        _flat(n, 5.0),
        T_sup_w_schedule=_flat(n, T_SUP),
        T_sur_schedule=_flat(n, 20.0),
        **extra,
    )
    level = df["tank_level [-]"].to_numpy(dtype=float)
    require(level.min() < 0.05, f"scenario must drain the tank (level min {level.min():.3f})")
    bal = _tank_balance(df, m.C_tank, 54.0, dt, always_full=False)
    expect(abs(bal["mass_residual"]) < 0.01, bal)
    expect(abs(bal["residual_J"]) < 0.01 * (bal["heat_J"] + bal["throughput_J"]), bal)


# T2 fixed: UVLamp.step reports the lamp power once (E_subsystem) and takes the share that
# does not reach the water off through Q_contribution; was 1,200 kJ for a 600 kJ lamp run.
def test_t2_uv_lamp_heat_enters_tank_once():
    from tmhp.subsystems import UVLamp

    P, on_s, dt = 1000.0, 600.0, 600
    uv = UVLamp(lamp_watts=P, exposure_sec=on_s, num_switching=1, period_sec=3 * 3600)
    m = tmhp.AirSourceHeatPumpBoiler(uv=uv, hp_on_schedule=[(0.0, 0.0)])
    df, _ = _ashpb_dynamic(m, 3.0, dt, 50.0, None, T0=20.0)
    lamp_J = float(df["E_uv [W]"].sum() * dt)
    require(lamp_J == pytest.approx(P * on_s, rel=1e-6), f"lamp energy {lamp_J:.0f} J != P*t")
    tank_gain_J = m.C_tank * (df["T_tank_w [°C]"].iloc[-1] - 50.0) + float(df["Q_tank_loss [W]"].sum() * dt)
    to_water = uv.heat_to_water_fraction * P * on_s
    expect(tank_gain_J == pytest.approx(to_water, rel=0.02), f"tank gained {tank_gain_J / 1e3:.0f} kJ")


# T3 fixed: the collector absorbs on A_stc the irradiance transposed to its plane
# (stc_tilt, stc_azimuth, sun position at time_s); was 1,652.5 W for every geometry.
@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"A_stc": 4.0}, id="A_stc"),
        pytest.param({"stc_tilt": 90.0}, id="stc_tilt"),
    ],
)
def test_t3_collector_geometry_changes_gain(kwargs):
    from tmhp.subsystems import SolarThermalCollector

    def gain(**kw):
        # Solar noon of the default start day (equinox) at the default site.
        r = SolarThermalCollector(**kw).calc_performance(800.0, 100.0, 300.0, 293.15, time_s=12.5 * 3600.0)
        return r["Q_stc_w_out"] - r["Q_stc_w_in"]

    base, changed = gain(), gain(**kwargs)
    expect(abs(changed - base) > 0.01 * abs(base), f"{kwargs}: {base:.1f} W -> {changed:.1f} W")


def test_t5_reference_stratified_tank_conserves_energy():
    """Reference: StratifiedTank charge closes its energy balance (contrast for T5)."""
    from tmhp.stratified_tank import StratifiedTank

    assert _charge_storage_ratio(StratifiedTank) == pytest.approx(1.0, abs=0.01)


# T4 fixed: the condenser is a coil at the cycle's condensing temperature (tank.step hx_ua /
# T_hx / q_hx); node i receives k·UA_i·(T_cond − T_i)⁺, nothing once it is as hot as the
# refrigerant. Was +4.1 K above T_cond here, 92 °C top node in a 24 h run, 364 °C with
# condenser_node=0. With the default coil (16 K approach) every node of this run stays below
# the refrigerant; tests/test_tank_backend.py covers hot nodes left unheated and the hybrid tank.
def test_t4_stratified_condenser_never_heats_node_above_condensing_temperature():
    dt, n = 300, 9
    m = tmhp.GroundSourceHeatPumpBoiler(dt_s=dt, t_max_s=86400.0, tank_model="stratified")
    tank = m._tank
    record: list[tuple[np.ndarray, np.ndarray]] = []
    original_step = tank.step

    def recording_step(dt_s, **kw):
        out = original_step(dt_s, **kw)
        # Heat each node received in this step: the immersed exchanger's share
        # plus any prescribed q_source (how the condenser heat was passed before).
        q = np.asarray(out["q_hx"], dtype=float) + np.asarray(kw.get("q_source", np.zeros(tank.n)), dtype=float)
        record.append((q, tank.T.copy()))
        return out

    tank.step = recording_step
    draw = np.zeros(n)
    draw[:3] = 12.0 / 60000.0  # cool the bottom so the top stays hot
    df = m.analyze_dynamic(n * dt, dt, 60.0, draw, _flat(n, 5.0))
    require(len(record) == len(df), f"{len(record)} tank steps for {len(df)} rows")
    heat_J = sum(float(q.sum()) for q, _ in record) * dt
    require(heat_J > 3.6e6, f"the condenser put only {heat_J / 3.6e6:.2f} kWh into the tank")
    require(
        heat_J == pytest.approx(float(df["Q_ref_tank [W]"].sum()) * dt, rel=1e-6),
        "the heat the nodes received is not the cycle's condenser heat",
    )
    bad = []
    for i, (q, T_after) in enumerate(record):
        row = df.iloc[i]
        if not (float(row["E_cmp [W]"] or 0.0) > 0.0):
            continue  # no refrigerant cycle this step
        T_cond = min(float(row["T_ref_cond_sat_v [°C]"]), float(row["T_ref_cond_sat_l [°C]"]))
        heated = q > 0.0
        over = T_after[heated] - T_cond
        if over.size and over.max() > 0.5:
            bad.append((i, round(T_cond, 1), round(float(T_after[heated].max()), 1)))
    expect(not bad, f"(step, T_cond_sat, hottest heated node): {bad}")


def _charge_storage_ratio(tank_cls, dt: float = 600.0, seconds: float = 1800.0) -> float:
    """Stored-energy gain / injected enthalpy for a pure top charge of a cold tank."""
    tank = tank_cls(20, 0.2, 1.2)
    tank.reset(15.0)
    E0, injected, flow = tank.stored_energy, 0.0, 0.2e-3
    for _ in range(int(seconds / dt)):
        out = tank.step(dt, charge_flow=flow, T_charge=60.0)
        # The water that actually left; the bottom-node mean is not the outflow
        # when a sharp front sits inside the bottom node (hybrid backend).
        t_out = float(out.get("T_charge_out", out["T"][-1]))
        injected += dt * rho_w * c_w * flow * (60.0 - t_out)
    return (tank.stored_energy - E0) / injected


# T5 fixed by the conservative plug-flow barrier (hybrid_tank._advect): was 8 % (dt 600 s) /
# 46 % (dt 60 s) of the injected heat stored because the front moved at most one node per step.
def test_t5_hybrid_tank_stores_injected_heat():
    from tmhp.hybrid_tank import HybridStratifiedTank

    ratio = _charge_storage_ratio(HybridStratifiedTank)
    expect(0.95 <= ratio <= 1.01, f"_charge_storage_ratio(HybridStratifiedTank) = {ratio:.4f}, expected 0.95..1.01")


# T6 fixed by resolving the thermostat inside each step (dynamic_context.resolve_step_operation):
# was dt 60 s vs 600 s 2.75 vs 5.0 kWh and a 78 °C peak against the 65 °C set point.
def test_t6_ashpb_time_step_independence():
    out = {}
    for dt in (60, 600):
        # R32 with the default 60-65 °C band: the condensing limit (65 °C) caps the
        # band at 55-60 °C (UserWarning), and the thermostat stops there in-step.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            m = tmhp.AirSourceHeatPumpBoiler(ref="R32")
        df, _ = _ashpb_dynamic(m, 1.0, dt, 50.0, None)
        out[dt] = (float(df["Q_ref_tank [W]"].fillna(0.0).sum() * dt), float(df["T_tank_w [°C]"].max()))
        upper = m.T_tank_w_upper_bound
    (q60, tmax60), (q600, tmax600) = out[60], out[600]
    expect(abs(q600 - q60) < 0.05 * q60, f"heat input {q60 / 3.6e6:.2f} vs {q600 / 3.6e6:.2f} kWh")
    expect(max(tmax60, tmax600) <= upper + 2.0, f"peak {tmax60:.1f} / {tmax600:.1f} °C, upper bound {upper}")


def _gshpb_two_hours(dt: int, draw_peak_lpm: float, **kw):
    """Default R290 GSHPB, 2 h from 55 degC, a draw peaking at 1 h; counts the cycle solves."""
    m = tmhp.GroundSourceHeatPumpBoiler(dt_s=float(dt), t_max_s=86400.0, **kw)
    n = int(2 * 3600 // dt)
    t_h = (np.arange(n) + 0.5) * dt / 3600.0
    dhw = draw_peak_lpm / 60000.0 * np.exp(-0.5 * ((t_h - 1.0) / 0.25) ** 2)
    solves = [0]
    solve = m._solve_cycle_at_duty

    def counted(*args, **kwargs):
        solves[0] += 1
        return solve(*args, **kwargs)

    m._solve_cycle_at_duty = counted
    df = m.analyze_dynamic(n * dt, dt, 55.0, dhw, np.full(n, 7.0))
    ran = int((df["runtime_fraction [-]"] > 0.0).sum())
    return m, df, solves[0], ran


# T6 extended (R5C): a minimum run time, or a multi-node tank whose coil water is hotter than the
# mean the thermostat reads, took steps past the envelope stop (the tank temperature at which the
# nameplate duty condenses on T_cond_max). Was: min on-time 30 min, 2 h: 2.13 vs 2.64 kWh and a
# 73.1 / 77.4 degC peak at 60 / 600 s; top coil, 2 h, no draw: 116 of 120 / 10 of 12 steps
# condensing_temp_above_max, 2-4 cycle solves per step. Now the thermostat stops (or holds) there
# inside the step.
@pytest.mark.parametrize(
    "case",
    [
        pytest.param(dict(min_on_time_s=1800.0, draw=0.0), id="min-on-time"),
        pytest.param(dict(tank_model="stratified", condenser_node=0, draw=0.0), id="top-coil"),
        pytest.param(dict(tank_model="stratified", condenser_node=0, draw=8.0), id="top-coil-draw"),
    ],
)
def test_t6_envelope_stop_inside_the_step(case):
    from tmhp.compressor_envelope import condensing_tank_cutout

    kw = dict(case)
    draw = kw.pop("draw")
    out = {}
    for dt in (60, 600):
        m, df, solves, ran = _gshpb_two_hours(dt, draw, **kw)
        failed = int((df["failure_reason"] != "none").sum())
        expect(failed == 0, f"dt {dt}: {failed} steps failed ({df['failure_reason'].value_counts().to_dict()})")
        expect(solves == ran, f"dt {dt}: {solves} cycle solves on {ran} running steps")
        expect((df["hp_stop_reason"] == "condensing_limit").any(), f"dt {dt}: no envelope stop reported")
        top = df.get("T_tank_w_top [°C]", df["T_tank_w [°C]"])
        out[dt] = (
            float(df["Q_ref_tank [W]"].sum() * dt),
            float(df["E_tot [W]"].sum() * dt),
            float(top.max()),
        )
        cutout = condensing_tank_cutout(m)
    (q60, e60, top60), (q600, e600, top600) = out[60], out[600]
    expect(abs(q600 - q60) < 0.02 * q60, f"heat {q60 / 3.6e6:.3f} vs {q600 / 3.6e6:.3f} kWh")
    expect(abs(e600 - e60) < 0.05 * e60, f"electricity {e60 / 3.6e6:.3f} vs {e600 / 3.6e6:.3f} kWh")
    # The coil's water at the envelope stop: the node step spreads the step's heat over the step
    # under the cycle's condensing temperature, so it may pass the stop a little (0.25 K at 60 s
    # with the draw, a few kelvin at 600 s), never the condensing limit itself
    # (cutout + hp_capacity / UA_tank_hx).
    expect(top60 <= cutout + 0.5, f"dt 60: peak {top60:.2f} degC, envelope stop {cutout:.2f}")
    expect(top600 <= cutout + m.hp_capacity / m.UA_tank_hx, f"dt 600: peak {top600:.2f} degC")


# T6 extended to ASHPB (R5K): its step did not hand the envelope stop to the in-step thermostat,
# so a minimum run time took the (single-node) tank past it. Was, R32 (stop 60 degC), min on-time
# 30 min, 2 h without a draw: 1.51 vs 1.95 kWh and a 62.6 / 65.05 degC peak at 60 / 600 s with
# the band at the stop, 1.82 vs 2.50 kWh and 62.4 / 66.2 degC with a 53-58 degC band, each with a
# condensing_temp_above_max step. Now the protection stop outranks the minimum run time.
@pytest.mark.parametrize(
    "band, T_init",
    [pytest.param(None, 54.0, id="band-at-the-stop"), pytest.param((53.0, 58.0), 52.0, id="band-below-the-stop")],
)
def test_t6_ashpb_envelope_stop_inside_the_step(band, T_init):
    from tmhp.compressor_envelope import condensing_tank_cutout

    kw = {} if band is None else dict(T_tank_w_lower_bound=band[0], T_tank_w_upper_bound=band[1])
    out = {}
    for dt in (60, 600):
        m = tmhp.AirSourceHeatPumpBoiler(ref="R32", min_on_time_s=1800.0, **kw)
        cutout = condensing_tank_cutout(m)
        require(m.T_tank_w_upper_bound <= cutout + 1e-6, "the band must not lie above the envelope stop")
        df, _ = _ashpb_dynamic(m, 2.0, dt, T_init, None)
        failed = int((df["failure_reason"] != "none").sum())
        expect(failed == 0, f"dt {dt}: {failed} steps failed ({df['failure_reason'].value_counts().to_dict()})")
        expect((df["hp_stop_reason"] == "condensing_limit").any(), f"dt {dt}: no envelope stop reported")
        out[dt] = (
            float(df["Q_ref_tank [W]"].sum() * dt),
            float(df["E_tot [W]"].sum() * dt),
            float(df["T_tank_w [°C]"].max()),
        )
    (q60, e60, top60), (q600, e600, top600) = out[60], out[600]
    expect(abs(q600 - q60) < 0.02 * q60, f"heat {q60 / 3.6e6:.3f} vs {q600 / 3.6e6:.3f} kWh")
    expect(abs(e600 - e60) < 0.05 * e60, f"electricity {e60 / 3.6e6:.3f} vs {e600 / 3.6e6:.3f} kWh")
    # One mixed node: the water around the coil is the tank, so the tank itself stops at the stop.
    expect(max(top60, top600) <= cutout + 0.05, f"peak {top60:.2f} / {top600:.2f} degC, envelope stop {cutout:.2f}")


# T8 fixed: the collector runs at the tank's make-up flow (mains -> collector -> tank; the
# mixing valve's cold side takes mains water), solved with the tank temperature, and the
# preheated inflow is the reported tank inlet; was 1.74 kWh reported vs 0.99 kWh delivered.
def test_t8_stc_preheat_reported_gain_reaches_tank():
    from tmhp.subsystems import SolarThermalCollector

    dt = 600
    # Start at 11:00 on the June solstice so the constant irradiance has the sun up.
    stc = SolarThermalCollector(preheat_start_hour=0.0, start_time="2026-06-21 11:00")
    m = tmhp.ASHPB_STC_preheat(stc=stc)
    df, n = _ashpb_dynamic(
        m,
        1.0,
        dt,
        60.0,
        lambda h: np.full(h.shape, 8.0),
        T0=20.0,
        I_DN_schedule=lambda n: _flat(n, 800.0),
        I_dH_schedule=lambda n: _flat(n, 100.0),
    )
    active = df["stc_active [-]"].astype(bool).to_numpy()
    require(active.all(), "collector must preheat every step")
    reported_J = float(((df["Q_stc_w_out [W]"] - df["Q_stc_w_in [W]"]).to_numpy() * active).sum() * dt)
    require(reported_J > 0.0, "no collector gain")
    # The tank balance closes with the reported (preheated) inlet ...
    closed = _tank_balance(df, m.C_tank, 60.0, dt)["residual_J"]
    expect(abs(closed) < 1e-3 * reported_J, f"tank residual {closed / 3.6e6:.4f} kWh with the preheated inlet")
    # ... and the heat the tank received beyond a mains-temperature inflow is the reported gain.
    delivered_J = _tank_balance(df.assign(**{"T_tank_w_in [°C]": T_SUP}), m.C_tank, 60.0, dt)["residual_J"]
    expect(delivered_J == pytest.approx(reported_J, rel=1e-3), (reported_J / 3.6e6, delivered_J / 3.6e6))


# T9 fixed: the collector loop stops at the storage high limit T_stc_tank_max (85 °C) within
# the step; was 99.5 °C (ASHPB) and 124 °C (GSHPB).
@pytest.mark.parametrize("cls_name", ["ASHPB_STC_tank", "GSHPB_STC_tank"])
def test_t9_stc_tank_respects_high_limit(cls_name):
    from tmhp.subsystems import SolarThermalCollector

    dt = 600
    cls = getattr(tmhp, cls_name)
    kw = {} if cls_name.startswith("ASHPB") else {"dt_s": dt, "t_max_s": 86400.0}
    from tmhp._stc_scenarios import TANK_LIMIT_RAMP_K

    # Start at 10:00 on the June solstice so the constant irradiance has the sun up.
    stc = SolarThermalCollector(start_time="2026-06-21 10:00")
    m = cls(stc=stc, **kw)
    df, _ = _ashpb_dynamic(
        m,
        3.0,
        dt,
        80.0,
        None,
        T0=25.0,
        I_DN_schedule=lambda n: _flat(n, 900.0),
        I_dH_schedule=lambda n: _flat(n, 120.0),
    )
    T_max = float(df["T_tank_w [°C]"].max())
    require(df["stc_active [-]"].astype(bool).any(), "collector loop never ran")
    require(T_max > stc.T_stc_tank_max - 0.5, f"tank only reached {T_max:.1f} °C: the limit is not exercised")
    expect(T_max <= 90.0, 'df["T_tank_w [°C]"].max() <= 90.0')
    expect(T_max <= stc.T_stc_tank_max + TANK_LIMIT_RAMP_K + 1e-9, f"peak {T_max:.3f} °C")


def _energyplus_mains_temperature_C(T_avg_C: float, dT_max_C: float, day_of_year: int) -> float:
    """EnergyPlus ``Site:WaterMainsTemperature`` correlation (Hendron et al. 2004).

    EnergyPlus Engineering Reference, "Water Systems > Water Mains
    Temperatures": in °F,
    ``T = (T_avg + 6) + ratio * (ΔT_max / 2) * sin(0.986 * (day - 15 - lag) - 90)``
    with ``ratio = 0.4 + 0.01 * (T_avg - 44)`` and ``lag = 35 - 1.0 * (T_avg - 44)``;
    the sine argument is in degrees. ``T_avg`` is the annual mean outdoor
    temperature and ``ΔT_max`` the spread of monthly means.
    """
    T_avg_F = T_avg_C * 1.8 + 32.0
    dT_F = dT_max_C * 1.8
    ratio = 0.4 + 0.01 * (T_avg_F - 44.0)
    lag = 35.0 - 1.0 * (T_avg_F - 44.0)
    T_F = (T_avg_F + 6.0) + ratio * (dT_F / 2.0) * math.sin(math.radians(0.986 * (day_of_year - 15.0 - lag) - 90.0))
    return (T_F - 32.0) / 1.8


# T11 fixed: the correlation is evaluated in °F as EnergyPlus does; was 1.8-4.9 K low.
def test_t11_mains_temperature_matches_energyplus():
    import pandas as pd

    from tmhp.dhw import calc_cold_water_temp

    monthly = np.array([-2.4, 0.4, 5.7, 12.5, 17.8, 22.2, 24.9, 25.7, 21.2, 14.8, 7.2, 0.4])  # Seoul-like
    df = pd.DataFrame({"T_avg": monthly})
    for date in ("2026-01-15", "2026-04-15", "2026-08-20", "2026-10-15"):
        doy = pd.Timestamp(date).dayofyear
        ref = _energyplus_mains_temperature_C(monthly.mean(), monthly.max() - monthly.min(), doy)
        expect(calc_cold_water_temp(df, date) == pytest.approx(ref, abs=0.5), date)


# T12 fixed: the battery state of charge is restored to SOC_init when a run starts.
def test_t12_pv_ess_analyze_dynamic_is_repeatable():
    import pandas as pd

    from tmhp.subsystems import EnergyStorageSystem, PhotovoltaicSystem

    dt, n = 3600, 24
    hod = np.arange(n) * dt / 3600.0
    m = tmhp.ASHPB_PV_ESS(
        pv=PhotovoltaicSystem(A_pv=20.0), ess=EnergyStorageSystem(C_ess_max=10 * 3.6e6), hp_capacity=5000.0
    )
    kw = dict(
        I_DN_schedule=np.clip(800 * np.sin(np.pi * (hod - 6) / 12), 0, None),
        I_dH_schedule=np.zeros(n),
        T_sup_w_schedule=_flat(n, T_SUP),
        T_sur_schedule=_flat(n, 20.0),
    )
    draw = np.where((hod >= 19) & (hod < 20), 8.0 / 60000.0, 0.0)
    first = m.analyze_dynamic(n * dt, dt, 55.0, draw, _flat(n, 10.0), **kw)
    second = m.analyze_dynamic(n * dt, dt, 55.0, draw, _flat(n, 10.0), **kw)
    numeric = first.select_dtypes("number").columns
    pd.testing.assert_frame_equal(first[numeric], second[numeric], rtol=1e-9, atol=1e-9)


def _repeat_ashpb():
    n = 12
    m = tmhp.AirSourceHeatPumpBoiler()
    draw = np.where(np.isin(np.arange(n), [7, 9]), 8.0 / 60000.0, 0.0)
    return lambda: m.analyze_dynamic(n * 3600, 3600, 55.0, draw, _flat(n, 8.0))


def _repeat_gshpb():
    n = 12
    m = tmhp.GroundSourceHeatPumpBoiler(N_1=2, N_2=1, H_b=100.0, dt_s=3600.0, t_max_s=200 * 3600.0)
    draw = np.where(np.isin(np.arange(n), [7, 9]), 8.0 / 60000.0, 0.0)
    return lambda: m.analyze_dynamic(n * 3600, 3600, 55.0, draw, _flat(n, 8.0))


def _repeat_wshpb():
    n = 12
    m = tmhp.WaterSourceHeatPumpBoiler(dt_s=3600.0, t_max_s=86400.0)
    draw = np.where(np.isin(np.arange(n), [7, 9]), 8.0 / 60000.0, 0.0)
    return lambda: m.analyze_dynamic(n * 3600, 3600, 55.0, draw, _flat(n, 8.0), T_source_w_schedule=_flat(n, 10.0))


def _repeat_ashp():
    n = 12
    m = tmhp.AirSourceHeatPump()
    return lambda: m.analyze_dynamic(n * 3600, 3600, _flat(n, 3000.0), _flat(n, 8.0))


def _repeat_gshp():
    n = 12
    m = tmhp.GroundSourceHeatPump(dt_s=3600.0, t_max_s=86400.0, N_1=2, N_2=1, H_b=100.0, hp_capacity=8000)
    return lambda: m.analyze_dynamic(n * 3600, 3600, _flat(n, 3000.0), _flat(n, 8.0))


@pytest.mark.parametrize(
    "make_run",
    [
        pytest.param(_repeat_ashpb, id="ASHPB"),
        pytest.param(_repeat_ashp, id="ASHP"),
        pytest.param(_repeat_gshp, id="GSHP"),
        # T13 fixed: analyze_dynamic now also resets T_bhe_f_out_K.
        pytest.param(_repeat_gshpb, id="GSHPB"),
        pytest.param(_repeat_wshpb, id="WSHPB"),
    ],
)
def test_t13_same_model_same_inputs_same_result(make_run):
    """Two ``analyze_dynamic`` calls on one model with the same inputs give the same table."""
    import pandas as pd

    do_run = make_run()
    first, second = do_run(), do_run()
    numeric = first.select_dtypes("number").columns
    try:
        pd.testing.assert_frame_equal(first[numeric], second[numeric], rtol=1e-9, atol=1e-9)
    except AssertionError as exc:  # only the comparison itself is the T13 defect
        raise KnownDefectObserved(str(exc)) from exc


# ---------------------------------------------------------------------------
# ground and water sources
# ---------------------------------------------------------------------------


def _gshp_cold_ground():
    dt = 3600
    m = tmhp.GroundSourceHeatPump(dt_s=dt, t_max_s=86400.0, UA_iu_rated=2000.0, Ts=8.0, H_b=60, hp_capacity=8000)
    return m.analyze_dynamic(2 * dt, dt, _flat(2, -8000.0), np.zeros(2))


def _gshpb_cold_ground():
    dt = 600
    m = tmhp.GroundSourceHeatPumpBoiler(dt_s=dt, t_max_s=86400.0, Ts=8.0, H_b=40.0)
    return m.analyze_dynamic(2 * dt, dt, 40.0, np.zeros(2), _flat(2, 0.0))


@pytest.mark.parametrize(
    "run",
    [
        # G1 fixed for GSHP: the ground fluid is closed inside the step (was
        # T_evap 0.8 > T_fluid -8.0 °C in the first row).
        pytest.param(_gshp_cold_ground, id="GSHP"),
        # G1 fixed for GSHPB: the cycle is closed against the ground fluid of the
        # same step (it was T_evap -4.1 > T_fluid -9.9 °C).
        pytest.param(_gshpb_cold_ground, id="GSHPB"),
    ],
)
def test_g1_evaporator_below_source_fluid_in_same_row(run):
    df = run()
    running = ~_off_rows(df)
    require(running.any(), "no running row")
    T_ev = df.loc[running, "T_ref_evap_sat [°C]"].to_numpy(dtype=float)
    T_fluid = df.loc[running, "T_bhe_f_out [°C]"].to_numpy(dtype=float)
    expect(np.all(T_ev < T_fluid), list(zip(T_ev.round(2), T_fluid.round(2), strict=True)))


def test_g3_gfunction_beyond_t_max_matches_pygfunction():
    import pygfunction as gt

    from tmhp.g_function import precompute_gfunction

    k_s, alpha = 2.0, 2.0 / (2000.0 * 800.0)
    H, r_b, t_max = 100.0, 0.08, 30 * 86400.0
    g = precompute_gfunction(1, 1, 6.0, H, 0.0, r_b, alpha, k_s, t_max, 3600.0)
    field = gt.borefield.Borefield.rectangle_field(N_1=1, N_2=1, B_1=6.0, B_2=6.0, H=H, D=0.0, r_b=r_b)
    times = np.array([4.0, 12.0]) * t_max
    ref = gt.gfunction.gFunction(field, alpha, time=times, boundary_condition="UBWT").gFunc / (2 * np.pi * k_s)
    ours = np.array([float(g(t)) for t in times])
    expect(np.allclose(ours, ref, rtol=0.05), (ours, ref))


def _pygfunction_single_u(m_flow: float, cp: float, mu: float, rho: float, k_f: float):
    """Local R_b and the pipe-to-pipe internal resistance R_a from pygfunction's multipole model."""
    import pygfunction as gt

    r_in, r_out, r_b, D_s, k_s, k_g, k_p = 0.013, 0.016, 0.08, 0.025, 2.0, 1.5, 0.4
    pos = [(-D_s, 0.0), (D_s, 0.0)]
    R_p = gt.pipes.conduction_thermal_resistance_circular_pipe(r_in, r_out, k_p)
    h_f = gt.pipes.convective_heat_transfer_coefficient_circular_pipe(m_flow, r_in, mu, rho, k_f, cp, 1e-6)
    R_fp = R_p + 1.0 / (h_f * 2 * np.pi * r_in)
    pipe = gt.pipes.SingleUTube(pos, r_in, r_out, gt.boreholes.Borehole(100.0, 0.0, r_b, 0, 0), k_s, k_g, R_fp)
    _, R_delta = gt.pipes.thermal_resistances(pos, r_out, r_b, k_s, k_g, R_fp, J=3)
    R1, R12 = R_delta[0, 0], R_delta[0, 1]
    R_a = 1.0 / (1.0 / R12 + 1.0 / (2.0 * R1))  # pipe-to-pipe with an adiabatic borehole wall
    return pipe, float(pipe.local_borehole_thermal_resistance()), float(R_a)


@pytest.mark.parametrize("lpm", [4.0, 8.0])
def test_g6_uniform_heat_flux_effective_resistance(lpm):
    from tmhp.borehole import calc_effective_borehole_thermal_resistance, calc_local_borehole_thermal_resistance

    rho, mu, cp, k_f, H = 1000.0, 1.0e-3, 4186.0, 0.606, 100.0
    m_flow = lpm / 60000.0 * rho
    R_b, R_12 = calc_local_borehole_thermal_resistance(
        k_s=2.0,
        k_g=1.5,
        k_p=0.4,
        r_b=0.08,
        r_out=0.016,
        r_in=0.013,
        D_s=0.025,
        m_flow_pipe=m_flow,
        rho_f=rho,
        mu_f=mu,
        cp_f=cp,
        k_f=k_f,
    )
    ours = calc_effective_borehole_thermal_resistance(R_b, R_12, H, m_flow, cp, "uniform_heat_flux")
    _, R_b_pyg, R_a = _pygfunction_single_u(m_flow, cp, mu, rho, k_f)
    # Hellström (1991): R_b* = R_b + H^2 / (3 R_a (m cp)^2) for uniform heat flux.
    ref = R_b_pyg + H**2 / (3.0 * R_a * (m_flow * cp) ** 2)
    expect(ours == pytest.approx(ref, rel=0.02), "ours == pytest.approx(ref, rel=0.02)")


@pytest.mark.parametrize("lpm", [4.0, 8.0])
def test_g6_reference_uniform_temperature_matches_pygfunction(lpm):
    """Contrast for G6: the uniform-temperature branch already agrees with pygfunction."""
    from tmhp.borehole import calc_effective_borehole_thermal_resistance, calc_local_borehole_thermal_resistance

    rho, mu, cp, k_f, H = 1000.0, 1.0e-3, 4186.0, 0.606, 100.0
    m_flow = lpm / 60000.0 * rho
    R_b, R_12 = calc_local_borehole_thermal_resistance(
        k_s=2.0,
        k_g=1.5,
        k_p=0.4,
        r_b=0.08,
        r_out=0.016,
        r_in=0.013,
        D_s=0.025,
        m_flow_pipe=m_flow,
        rho_f=rho,
        mu_f=mu,
        cp_f=cp,
        k_f=k_f,
    )
    pipe, R_b_pyg, _ = _pygfunction_single_u(m_flow, cp, mu, rho, k_f)
    assert R_b == pytest.approx(R_b_pyg, rel=0.02)
    ours = calc_effective_borehole_thermal_resistance(R_b, R_12, H, m_flow, cp, "uniform_temperature")
    assert ours == pytest.approx(pipe.effective_borehole_thermal_resistance(m_flow, cp), rel=0.02)


def test_g8_empirical_gshp_never_reports_negative_power_or_cop():
    from tmhp.gshp_empirical import GroundSourceHeatPumpEmpirical

    m = GroundSourceHeatPumpEmpirical(sim_hours=48, T_g=5.0, H_b=50.0)
    for _ in range(6):
        m.Q_r_iu, m.T0 = -16450.0, -10.0
        try:
            m.system_update()
        except (ValueError, RuntimeError):
            return  # refusing an out-of-range point is acceptable
        flagged = getattr(m, "failure_reason", "none") not in (None, "none") or getattr(m, "converged", True) is False
        if flagged:
            continue
        expect(
            m.E_cmp >= 0.0 and m.COP > 0.0,
            f"E_cmp={m.E_cmp:.0f} W COP={m.COP:.2f} at T_f_out={m.T_f_out - 273.15:.1f} °C",
        )
