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
    rps_hi = max(f.cls.__init__.__kwdefaults__.get("rps_max", 150.0) for f in FAMILIES.values())
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
    _, r = fam.run("R32", case)
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


@defect("C6", "R32 tank 60 / outdoor 0 °C / 15 kW reports 201.6 °C discharge as success")
def test_c6_ashpb_discharge_temperature_limit():
    _, r = FAMILIES["ASHPB"].run("R32", SteadyCase(T_source=0.0, T_demand=60.0, load_W=15000.0))
    T_dis = num(r, "T_ref_cmp_out [°C]")
    expect((not is_reported_running(r)) or T_dis <= 150.0, f"T_cmp_out={T_dis:.1f} °C reported as success")


@defect("C11", "WSHPB has no superheat cap: suction is +1.6 K above the source fluid")
def test_c11_wshpb_suction_not_hotter_than_source():
    _, r = FAMILIES["WSHPB"].run("R32", SteadyCase(T_source=-5.0, T_demand=55.0, load_W=400.0))
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


@defect("T1", "tank_always_full=False loses mass/energy: level pinned at 0.001, residual about -28 kWh in 3 h")
def test_t1_partial_fill_conserves_mass_and_energy():
    m = tmhp.AirSourceHeatPumpBoiler(tank_always_full=False)
    df, _ = _ashpb_dynamic(m, 3.0, 600, 60.0, lambda h: np.where(h < 1.0, 8.0, 0.0), T0=5.0)
    T = df["T_tank_w [°C]"].to_numpy(dtype=float)
    expect(np.all((T > 0.0) & (T < 100.0)), f"tank temperature range {T.min():.1f}..{T.max():.1f} °C")
    bal = _tank_balance(df, m.C_tank, 60.0, 600, always_full=False)
    expect(abs(bal["mass_residual"]) < 0.01, bal)
    expect(abs(bal["residual_J"]) < 0.01 * (bal["heat_J"] + bal["throughput_J"]), bal)


@defect("T2", "UV lamp heat enters the tank twice (Q_contribution and E_subsystem)")
def test_t2_uv_lamp_heat_enters_tank_once():
    from tmhp.subsystems import UVLamp

    P, on_s, dt = 1000.0, 600.0, 600
    uv = UVLamp(lamp_watts=P, exposure_sec=on_s, num_switching=1, period_sec=3 * 3600)
    m = tmhp.AirSourceHeatPumpBoiler(uv=uv, hp_on_schedule=[(0.0, 0.0)])
    df, _ = _ashpb_dynamic(m, 3.0, dt, 50.0, None, T0=20.0)
    lamp_J = float(df["E_uv [W]"].sum() * dt)
    require(lamp_J == pytest.approx(P * on_s, rel=1e-6), f"lamp energy {lamp_J:.0f} J != P*t")
    tank_gain_J = m.C_tank * (df["T_tank_w [°C]"].iloc[-1] - 50.0) + float(df["Q_tank_loss [W]"].sum() * dt)
    expect(tank_gain_J == pytest.approx(P * on_s, rel=0.02), f"tank gained {tank_gain_J / 1e3:.0f} kJ")


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"A_stc": 4.0}, id="A_stc", marks=defect("T3", "collector gain uses A_stc_pipe, ignores A_stc")),
        pytest.param({"stc_tilt": 90.0}, id="stc_tilt", marks=defect("T3", "stc_tilt is never used")),
    ],
)
def test_t3_collector_geometry_changes_gain(kwargs):
    from tmhp.subsystems import SolarThermalCollector

    def gain(**kw):
        r = SolarThermalCollector(**kw).calc_performance(800.0, 100.0, 300.0, 293.15)
        return r["Q_stc_w_out"] - r["Q_stc_w_in"]

    base, changed = gain(), gain(**kwargs)
    expect(abs(changed - base) > 0.01 * abs(base), f"{kwargs}: {base:.1f} W -> {changed:.1f} W")


def test_t5_reference_stratified_tank_conserves_energy():
    """Reference: StratifiedTank charge closes its energy balance (contrast for T5)."""
    from tmhp.stratified_tank import StratifiedTank

    assert _charge_storage_ratio(StratifiedTank) == pytest.approx(1.0, abs=0.01)


@defect("T4", "stratified GSHPB spreads condenser heat over all nodes, heating nodes above T_cond_sat")
def test_t4_stratified_condenser_never_heats_node_above_condensing_temperature():
    dt, n = 300, 9
    m = tmhp.GroundSourceHeatPumpBoiler(dt_s=dt, t_max_s=86400.0, tank_model="stratified")
    tank = m._tank
    record: list[tuple[np.ndarray, np.ndarray]] = []
    original_step = tank.step

    def recording_step(dt_s, **kw):
        out = original_step(dt_s, **kw)
        q = np.asarray(kw.get("q_source", np.zeros(tank.n)), dtype=float)
        record.append((q, tank.T.copy()))
        return out

    tank.step = recording_step
    draw = np.zeros(n)
    draw[:3] = 12.0 / 60000.0  # cool the bottom so the top stays hot
    df = m.analyze_dynamic(n * dt, dt, 60.0, draw, _flat(n, 5.0))
    require(len(record) == len(df), f"{len(record)} tank steps for {len(df)} rows")
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
        injected += dt * rho_w * c_w * flow * (60.0 - float(out["T"][-1]))
    return (tank.stored_energy - E0) / injected


@defect("T5", "HybridStratifiedTank front moves one node per step: 8 % (dt 600 s) of injected heat is stored")
def test_t5_hybrid_tank_stores_injected_heat():
    from tmhp.hybrid_tank import HybridStratifiedTank

    expect(_charge_storage_ratio(HybridStratifiedTank) >= 0.95, "_charge_storage_ratio(HybridStratifiedTank) >= 0.95")


@defect("T6", "dt 60 s vs 600 s: 2.75 vs 5.0 kWh and 78 °C peak (setpoint 65 °C)")
def test_t6_ashpb_time_step_independence():
    out = {}
    for dt in (60, 600):
        m = tmhp.AirSourceHeatPumpBoiler(ref="R32")
        df, _ = _ashpb_dynamic(m, 1.0, dt, 50.0, None)
        out[dt] = (float(df["Q_ref_tank [W]"].fillna(0.0).sum() * dt), float(df["T_tank_w [°C]"].max()))
        upper = m.T_tank_w_upper_bound
    (q60, tmax60), (q600, tmax600) = out[60], out[600]
    expect(abs(q600 - q60) < 0.05 * q60, f"heat input {q60 / 3.6e6:.2f} vs {q600 / 3.6e6:.2f} kWh")
    expect(max(tmax60, tmax600) <= upper + 2.0, f"peak {tmax60:.1f} / {tmax600:.1f} °C, upper bound {upper}")


@defect("T8", "STC preheat reports 1.74 kWh collected but only ~0.99 kWh reaches the tank")
def test_t8_stc_preheat_reported_gain_reaches_tank():
    from tmhp.subsystems import SolarThermalCollector

    dt = 600
    m = tmhp.ASHPB_STC_preheat(stc=SolarThermalCollector(preheat_start_hour=0.0))
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
    require(active.any(), "collector never active")
    reported_J = float(((df["Q_stc_w_out [W]"] - df["Q_stc_w_in [W]"]).to_numpy() * active).sum() * dt)
    # Heat the tank received beyond the refrigerant heat and a 15 °C inflow = preheat delivered.
    delivered_J = _tank_balance(df, m.C_tank, 60.0, dt)["residual_J"]
    expect(delivered_J == pytest.approx(reported_J, rel=0.05), (reported_J / 3.6e6, delivered_J / 3.6e6))


@pytest.mark.parametrize(
    "cls_name",
    [
        pytest.param("ASHPB_STC_tank", marks=defect("T9", "no collector high-limit: tank reaches 99.5 °C")),
        pytest.param("GSHPB_STC_tank", marks=defect("T9", "no collector high-limit: tank reaches 124 °C")),
    ],
)
def test_t9_stc_tank_respects_high_limit(cls_name):
    from tmhp.subsystems import SolarThermalCollector

    dt = 600
    cls = getattr(tmhp, cls_name)
    kw = {} if cls_name.startswith("ASHPB") else {"dt_s": dt, "t_max_s": 86400.0}
    m = cls(stc=SolarThermalCollector(preheat_start_hour=0.0), **kw)
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
    expect(df["T_tank_w [°C]"].max() <= 90.0, 'df["T_tank_w [°C]"].max() <= 90.0')


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


@defect("T11", "dhw.calc_cold_water_temp applies the °F coefficients to °C (1.8-4.9 K low)")
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


_T13_WHY = (
    "analyze_dynamic resets T_bhe_f_out (°C) but not T_bhe_f_out_K, which the first step reads: "
    "the second run's first row differs (one line in the model file; the GSHPB scenario classes "
    "restore it themselves)"
)


@pytest.mark.parametrize(
    "make_run",
    [
        pytest.param(_repeat_ashpb, id="ASHPB"),
        pytest.param(_repeat_ashp, id="ASHP"),
        pytest.param(_repeat_gshp, id="GSHP"),
        pytest.param(_repeat_gshpb, id="GSHPB", marks=defect("T13", _T13_WHY)),
        pytest.param(_repeat_wshpb, id="WSHPB", marks=defect("T13", _T13_WHY)),
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
        pytest.param(
            _gshp_cold_ground,
            id="GSHP",
            marks=defect("G1", "evaporator uses last step's fluid: T_evap 0.8 > T_fluid -6.3 °C"),
        ),
        pytest.param(
            _gshpb_cold_ground,
            id="GSHPB",
            marks=defect("G1", "evaporator uses last step's fluid: T_evap -4.1 > T_fluid -9.9 °C"),
        ),
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
