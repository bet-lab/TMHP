"""Energy accounting of the eight scenario (combination) classes.

For ``ASHPB_STC_preheat``, ``ASHPB_STC_tank``, ``ASHPB_PV_ESS``,
``GSHPB_STC_preheat``, ``GSHPB_STC_tank``, ``GSHPB_STC_ground``,
``GSHPB_STC_routed`` and ``GSHPB_PV_ESS`` the electricity and heat that the
result table reports must add up, step by step:

- the PV/ESS power balances close (generation and consumption side, with every
  loss a column);
- ``E_tot`` is the sum of every electric draw the class simulates (compressor,
  outdoor fan or source-loop pump, collector-loop pump, UV lamp);
- the collector's heat is fully accounted for (absorbed = carried away + lost,
  carried away = delivered to the tank + delivered to the ground);
- running the same model twice with the same inputs gives the same table.

The collector, UV lamp and storage-limit physics themselves are tested in
``tests/test_solar_uv_mains.py``.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pygfunction")

import tmhp  # noqa: E402
from tmhp import calc_util as cu  # noqa: E402
from tmhp.subsystems import (  # noqa: E402
    GRID_IMPORT_FLOOR_W,
    EnergyStorageSystem,
    PhotovoltaicSystem,
    SolarThermalCollector,
    UVLamp,
    pv_ess_postprocess,
    route_pv_ess,
)

DT = 3600
N = 24
CLASS_NAMES = [
    "ASHPB_STC_preheat",
    "ASHPB_STC_tank",
    "ASHPB_PV_ESS",
    "GSHPB_STC_preheat",
    "GSHPB_STC_tank",
    "GSHPB_STC_ground",
    "GSHPB_STC_routed",
    "GSHPB_PV_ESS",
]
PV_ESS_NAMES = ["ASHPB_PV_ESS", "GSHPB_PV_ESS"]
STC_NAMES = [n for n in CLASS_NAMES if "STC" in n]

_GROUND = dict(N_1=2, N_2=1, H_b=100.0, dt_s=float(DT), t_max_s=200 * 3600.0)


def _uv() -> UVLamp:
    return UVLamp(lamp_watts=40.0, exposure_sec=1800.0, num_switching=1, period_sec=6 * 3600)


def _stc() -> SolarThermalCollector:
    return SolarThermalCollector(A_stc=6.0, E_stc_pump=60.0)


def _pv_ess_parts() -> dict:
    # A small battery next to a large array: the day shows PV surplus (ESS full,
    # dump), a deficit (ESS discharge) and grid import.
    return dict(
        pv=PhotovoltaicSystem(A_pv=20.0),
        ess=EnergyStorageSystem(C_ess_max=3.6e6, SOC_init=0.1),
        hp_capacity=5000.0,
    )


def build(name: str, *, uv: bool = False):
    """A fresh model of class *name* (UV lamp only for the classes that simulate it)."""
    kw: dict = {"uv": _uv()} if uv else {}
    cls = getattr(tmhp, name)
    if name == "ASHPB_PV_ESS":
        return cls(**_pv_ess_parts(), **kw)
    if name == "GSHPB_PV_ESS":
        parts = _pv_ess_parts()
        parts.pop("hp_capacity")
        return cls(**parts, **_GROUND, **kw)
    stc = _stc()
    if name.startswith("ASHPB"):
        return cls(stc=stc, **kw)
    return cls(stc=stc, **_GROUND, **kw)


def schedules() -> dict:
    hod = np.arange(N) * DT / 3600.0
    sun = np.clip(800.0 * np.sin(np.pi * (hod - 6.0) / 12.0), 0.0, None)
    draw = np.where((hod >= 19) & (hod < 20), 8.0 / 60000.0, 0.0) + np.where((hod >= 7) & (hod < 8), 6.0 / 60000.0, 0.0)
    return dict(
        dhw_usage_schedule=draw,
        T0_schedule=np.full(N, 10.0),
        I_DN_schedule=sun,
        I_dH_schedule=0.15 * sun,
        T_sup_w_schedule=np.full(N, 15.0),
        T_sur_schedule=np.full(N, 20.0),
    )


def run(model) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return model.analyze_dynamic(N * DT, DT, T_tank_w_init_C=55.0, **schedules())


def col(df: pd.DataFrame, name: str) -> np.ndarray:
    """Column as float array; a column the class does not report counts as 0."""
    if name not in df.columns:
        return np.zeros(len(df))
    return np.nan_to_num(df[name].to_numpy(dtype=float))


@pytest.fixture(scope="module")
def runs() -> dict[str, tuple]:
    """``name -> (model, first run, second run)`` — each model is run twice."""
    out = {}
    for name in CLASS_NAMES:
        model = build(name, uv=name in PV_ESS_NAMES)
        out[name] = (model, run(model), run(model))
    return out


# ---------------------------------------------------------------------------
# PV + ESS power balances
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PV_ESS_NAMES)
def test_pv_ess_scenario_exercises_every_branch(runs, name):
    """Guard for the balance tests below: the day must reach all routing branches."""
    _, df, _ = runs[name]
    assert (col(df, "E_dump [W]") > 0).any(), "no dumped PV"
    assert (col(df, "E_ess_chg [W]") > 0).any(), "battery never charged"
    assert (col(df, "E_ess_dis [W]") > 0).any(), "battery never discharged"
    assert (col(df, "E_grid_import [W]") > 0).any(), "no grid import"
    assert (col(df, "E_uv [W]") > 0).any(), "UV lamp never on"


@pytest.mark.parametrize("name", PV_ESS_NAMES)
def test_pv_ess_generation_side_balance_closes_every_step(runs, name):
    """PV array output = controller loss + PV to inverter + battery charging + dump (no export)."""
    _, df, _ = runs[name]
    pv_out = col(df, "E_pv_out [W]")
    gen_uses = col(df, "Q_l_ctrl [W]") + col(df, "E_pv_to_inv [W]") + col(df, "E_ess_chg [W]") + col(df, "E_dump [W]")
    np.testing.assert_allclose(gen_uses, pv_out, rtol=1e-12, atol=1e-6)
    # the controller loss is the (1 - eta_ctrl) share of the array output
    eta_ctrl = build(name)._pv.eta_ctrl
    np.testing.assert_allclose(col(df, "Q_l_ctrl [W]"), (1.0 - eta_ctrl) * pv_out, rtol=1e-12, atol=1e-9)
    np.testing.assert_allclose(col(df, "E_ctrl_out [W]"), eta_ctrl * pv_out, rtol=1e-12, atol=1e-9)


@pytest.mark.parametrize("name", PV_ESS_NAMES)
def test_pv_ess_consumption_side_balance_closes_every_step(runs, name):
    """Electric draw = inverter output + grid import; inverter output = eta_inv x DC in."""
    model, df, _ = runs[name]
    eta = model._eta_inv
    e_tot = col(df, "E_tot [W]")
    np.testing.assert_allclose(col(df, "E_inv_out [W]") + col(df, "E_grid_import [W]"), e_tot, rtol=1e-12, atol=1e-6)
    dc_in = col(df, "E_pv_to_inv [W]") + col(df, "E_ess_dis [W]")
    np.testing.assert_allclose(col(df, "E_inv_out [W]"), eta * dc_in, rtol=1e-12, atol=1e-6)
    np.testing.assert_allclose(col(df, "Q_l_inv [W]"), (1.0 - eta) * dc_in, rtol=1e-12, atol=1e-6)


@pytest.mark.parametrize("name", PV_ESS_NAMES)
def test_pv_ess_battery_balance_closes_every_step(runs, name):
    """Charge - discharge = stored energy rate + battery loss, from the SOC column."""
    model, df, _ = runs[name]
    ess = model._ess
    soc = df["SOC_ess [-]"].to_numpy(dtype=float)
    stored_W = np.diff(np.r_[ess.SOC_init, soc]) * ess.C_ess_max / DT
    net = col(df, "E_ess_chg [W]") - col(df, "E_ess_dis [W]")
    np.testing.assert_allclose(net, stored_W + col(df, "Q_l_ess [W]"), rtol=1e-9, atol=1e-6)
    assert soc.min() >= min(ess.SOC_init, ess.SOC_min) - 1e-12
    assert soc.max() <= ess.SOC_max + 1e-12


@pytest.mark.parametrize("name", PV_ESS_NAMES)
def test_pv_ess_whole_system_balance_closes_every_step(runs, name):
    """PV + grid = load + controller, inverter and battery losses + stored + dump."""
    model, df, _ = runs[name]
    ess = model._ess
    soc = df["SOC_ess [-]"].to_numpy(dtype=float)
    stored_W = np.diff(np.r_[ess.SOC_init, soc]) * ess.C_ess_max / DT
    supply = col(df, "E_pv_out [W]") + col(df, "E_grid_import [W]")
    uses = (
        col(df, "E_tot [W]")
        + col(df, "Q_l_ctrl [W]")
        + col(df, "Q_l_inv [W]")
        + col(df, "Q_l_ess [W]")
        + stored_W
        + col(df, "E_dump [W]")
    )
    np.testing.assert_allclose(uses, supply, rtol=1e-9, atol=1e-6)


@pytest.mark.parametrize("name", PV_ESS_NAMES)
def test_cop_grid_is_undefined_without_a_real_grid_import(runs, name):
    """No grid import (or only round-off) gives NaN, never a huge ratio."""
    _, df, _ = runs[name]
    grid = col(df, "E_grid_import [W]")
    cop = df["cop_grid [-]"].to_numpy(dtype=float)
    assert np.isnan(cop[grid <= GRID_IMPORT_FLOOR_W]).all()
    defined = ~np.isnan(cop)
    assert (grid[defined] > GRID_IMPORT_FLOOR_W).all()
    # useful heat <= a few kW against >= 1 mW would be > 1e6: nothing like that
    assert np.nanmax(np.r_[cop, 0.0]) < 1e5


def test_cop_grid_postprocess_thresholds():
    n = 4
    df = pd.DataFrame(
        {
            "E_grid_import [W]": [0.0, 1e-12, 0.5 * GRID_IMPORT_FLOOR_W, 2.0],
            "Q_tank_w_out [W]": np.full(n, 3000.0),
            "X_sol [W]": np.zeros(n),
            "Xst_tank [W]": np.zeros(n),
            "X_tank_w_out [W]": np.zeros(n),
        }
    )
    out = pv_ess_postprocess(df)
    cop = out["cop_grid [-]"].to_numpy()
    assert np.isnan(cop[:3]).all()
    assert cop[3] == pytest.approx(1500.0)


# ---------------------------------------------------------------------------
# routing / battery unit behaviour
# ---------------------------------------------------------------------------


def _pv_result(E_ctrl_out: float, E_pv_out: float | None = None) -> dict:
    pv = PhotovoltaicSystem(eta_ctrl=0.95)
    E_pv_out = E_ctrl_out / pv.eta_ctrl if E_pv_out is None else E_pv_out
    return {
        "E_pv_out": E_pv_out,
        "E_ctrl_out": E_ctrl_out,
        "Q_l_ctrl": E_pv_out - E_ctrl_out,
    }


def test_battery_that_covers_the_load_leaves_exactly_zero_grid_import():
    """Round-off must not appear as grid import (it used to feed cop_grid ~ 4e11)."""
    rng = np.random.default_rng(0)
    for _ in range(2000):
        eta = float(rng.uniform(0.8, 1.0))
        E_load = float(rng.uniform(100.0, 5000.0))
        E_ctrl = float(rng.uniform(0.0, 1.0)) * E_load / eta
        ess = EnergyStorageSystem(C_ess_max=10 * 3.6e6, SOC_init=0.9)  # can always cover
        r = route_pv_ess(_pv_result(E_ctrl), ess, E_load, eta, DT, 283.15)
        assert r["E_grid_import"] == 0.0
        assert r["E_inv_out"] == pytest.approx(E_load, rel=1e-12)


def test_empty_battery_sends_the_shortfall_to_the_grid():
    ess = EnergyStorageSystem(C_ess_max=3.6e6, SOC_init=0.1)  # at SOC_min: nothing to give
    r = route_pv_ess(_pv_result(500.0), ess, 2000.0, 0.95, DT, 283.15)
    assert r["E_ess_dis"] == 0.0
    assert r["E_inv_out"] == pytest.approx(0.95 * 500.0)
    assert r["E_grid_import"] == pytest.approx(2000.0 - 0.95 * 500.0)
    assert r["E_dump"] == 0.0


def test_pv_surplus_charges_the_battery_then_dumps():
    ess = EnergyStorageSystem(C_ess_max=3.6e6, SOC_init=0.99)  # ~1 % of 1 kWh free
    r = route_pv_ess(_pv_result(4000.0), ess, 1000.0, 0.95, DT, 283.15)
    E_dc_req = 1000.0 / 0.95
    assert r["E_pv_to_inv"] == pytest.approx(E_dc_req)
    assert r["E_ess_chg"] + r["E_dump"] == pytest.approx(4000.0 - E_dc_req)
    assert r["E_dump"] > 0.0
    assert r["E_grid_import"] == 0.0
    assert ess.SOC_ess == pytest.approx(1.0)


def test_load_off_routes_all_pv_to_battery_and_dump():
    ess = EnergyStorageSystem(C_ess_max=3.6e6, SOC_init=0.5)
    r = route_pv_ess(_pv_result(3000.0), ess, 0.0, 0.95, DT, 283.15)
    assert r["E_pv_to_inv"] == 0.0 and r["E_inv_out"] == 0.0 and r["E_grid_import"] == 0.0
    assert r["E_ess_chg"] + r["E_dump"] == pytest.approx(3000.0)


@pytest.mark.parametrize("eta", [0.0, -0.1, 1.2, float("nan")])
def test_inverter_efficiency_must_be_in_unit_interval(eta):
    with pytest.raises(ValueError, match="eta_inv"):
        route_pv_ess(_pv_result(100.0), EnergyStorageSystem(), 50.0, eta, DT, 283.15)
    with pytest.raises(ValueError, match="eta_inv"):
        tmhp.ASHPB_PV_ESS(pv=PhotovoltaicSystem(), eta_inv=eta)


def test_battery_below_soc_min_is_not_topped_up_for_free():
    """A battery that starts empty stays empty until it is charged (it used to jump to SOC_min)."""
    ess = EnergyStorageSystem(C_ess_max=3.6e6, SOC_init=0.0)
    r = ess.discharge(500.0, DT, 283.15)
    assert r["E_ess_dis"] == 0.0
    assert ess.SOC_ess == 0.0


def test_battery_above_soc_max_is_not_pulled_down_by_charging():
    ess = EnergyStorageSystem(C_ess_max=3.6e6, SOC_init=1.0, SOC_max=0.9)
    ess.charge(500.0, DT, 283.15)
    assert ess.SOC_ess == 1.0


def test_battery_reset_restores_initial_state():
    ess = EnergyStorageSystem(C_ess_max=3.6e6, SOC_init=0.3)
    ess.charge(300.0, DT, 283.15)
    assert ess.SOC_ess != 0.3
    ess.reset()
    assert ess.SOC_ess == 0.3


def test_battery_balance_per_call():
    ess = EnergyStorageSystem(C_ess_max=3.6e6, SOC_init=0.5)
    c = ess.charge(400.0, DT, 283.15)
    d = ess.discharge(250.0, DT, 283.15)
    assert c["Q_l_ess"] == pytest.approx((1 - ess.eta_ess_chg) * 400.0)
    assert d["Q_l_ess"] == pytest.approx((1 / ess.eta_ess_dis - 1) * 250.0)
    assert ess.SOC_ess == pytest.approx(0.5 + (0.9 * 400.0 - 250.0 / 0.9) * DT / 3.6e6)


# ---------------------------------------------------------------------------
# E_tot = every electric draw
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CLASS_NAMES)
def test_e_tot_is_the_sum_of_every_electric_draw(runs, name):
    """compressor + outdoor fan (air) or source-loop pump (ground) + collector pump + UV lamp."""
    _, df, _ = runs[name]
    aux = "E_ou_fan [W]" if name.startswith("ASHPB") else "E_pmp [W]"
    expected = col(df, "E_cmp [W]") + col(df, aux) + col(df, "E_stc_pump [W]") + col(df, "E_uv [W]")
    np.testing.assert_allclose(col(df, "E_tot [W]"), expected, rtol=1e-12, atol=1e-9)


@pytest.mark.parametrize("name", [n for n in STC_NAMES if not n.endswith("_preheat")])
def test_collector_pump_power_is_reported_and_counted(runs, name):
    """Precondition for the E_tot test above: the pump runs and its power is in E_tot."""
    _, df, _ = runs[name]
    pump = col(df, "E_stc_pump [W]")
    assert (pump > 0).any(), "collector pump never ran"
    aux = "E_ou_fan [W]" if name.startswith("ASHPB") else "E_pmp [W]"
    without_pump = col(df, "E_cmp [W]") + col(df, aux)
    np.testing.assert_allclose(col(df, "E_tot [W]") - without_pump, pump, rtol=1e-12, atol=1e-9)


@pytest.mark.parametrize("name", ["ASHPB_STC_preheat", "GSHPB_STC_preheat"])
def test_stc_preheat_pump_is_excluded_by_assumption(name):
    """Mains pressure drives the preheat loop: no pump power, and the caller's collector is untouched."""
    stc = _stc()
    model = getattr(tmhp, name)(stc=stc, **({} if name.startswith("ASHPB") else _GROUND))
    assert model._stc.E_stc_pump == 0.0
    assert stc.E_stc_pump == 60.0
    assert model.stc is model._stc


def test_pv_ess_with_uv_lamp_includes_lamp_in_e_tot(runs):
    """Once: the base boiler puts the lamp in E_tot and the PV + ESS routing reads that E_tot."""
    for name in PV_ESS_NAMES:
        _, df, _ = runs[name]
        lamp = col(df, "E_uv [W]")
        assert lamp.sum() > 0
        aux = "E_ou_fan [W]" if name.startswith("ASHPB") else "E_pmp [W]"
        np.testing.assert_allclose(
            col(df, "E_tot [W]") - lamp, col(df, "E_cmp [W]") + col(df, aux), rtol=1e-12, atol=1e-9
        )
        # The inverter and the grid supply exactly that load: the lamp is not counted twice.
        np.testing.assert_allclose(
            col(df, "E_inv_out [W]") + col(df, "E_grid_import [W]"),
            col(df, "E_cmp [W]") + col(df, aux) + lamp,
            rtol=1e-9,
            atol=1e-6,
        )


BASE_BOILERS = {
    "AirSourceHeatPumpBoiler": ("E_ou_fan [W]", {}, {}),
    "GroundSourceHeatPumpBoiler": ("E_pmp [W]", dict(dt_s=600.0, t_max_s=86400.0), {}),
    "WaterSourceHeatPumpBoiler": (
        "E_pmp [W]",
        dict(dt_s=600.0, t_max_s=86400.0),
        dict(T_source_w_schedule=np.full(9, 8.0)),
    ),
}


@pytest.mark.parametrize("name", list(BASE_BOILERS))
def test_base_boiler_e_tot_includes_the_uv_lamp(name):
    """E_tot = compressor + fan/pump + lamp on every step, whether the compressor runs or not.

    The base boilers used to leave ``uv=`` power out of E_tot (only the PV + ESS
    variants added it).
    """
    aux, ctor, extra = BASE_BOILERS[name]
    dt, n = 600, 9
    # Lamp on for the middle 30 min of the 90 min run (steps 3-5); heat pump allowed
    # for the first 45 min (steps 0-4): lamp-on steps with and without the cycle.
    lamp = UVLamp(lamp_watts=40.0, exposure_sec=1800.0, num_switching=1, period_sec=5400.0)
    model = getattr(tmhp, name)(uv=lamp, hp_on_schedule=[(0.0, 0.75)], **ctor)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = model.analyze_dynamic(n * dt, dt, 54.0, np.full(n, 1.0e-4), np.full(n, 7.0), **extra)
    e_uv = col(df, "E_uv [W]")
    running = col(df, "E_cmp [W]") > 0
    assert e_uv.max() == pytest.approx(40.0) and e_uv.min() == 0.0
    assert (running & (e_uv > 0)).any() and (~running & (e_uv > 0)).any(), (
        "need lamp-on steps with and without the cycle"
    )
    np.testing.assert_allclose(col(df, "E_tot [W]"), col(df, "E_cmp [W]") + col(df, aux) + e_uv, rtol=1e-12, atol=1e-9)


@pytest.mark.parametrize("name", STC_NAMES)
def test_stc_scenarios_warn_when_the_uv_lamp_cannot_be_simulated(name):
    with pytest.warns(UserWarning, match="uv"):
        build(name, uv=True)


@pytest.mark.parametrize("name", PV_ESS_NAMES)
def test_pv_ess_scenarios_accept_the_uv_lamp_silently(name):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build(name, uv=True)


# ---------------------------------------------------------------------------
# collector heat accounting
# ---------------------------------------------------------------------------

_TANK_COLLECTOR_COLUMNS = [
    "I_sol_stc [W/m2]",
    "Q_sol_stc [W]",
    "S_sol_stc [W/K]",
    "X_sol_stc [W]",
    "Q_stc_w_out [W]",
    "Q_stc_pump_w_out [W]",
    "Q_stc_w_in [W]",
    "Q_l_stc [W]",
    "dV_stc [m3/s]",
    "T_stc_w_out [°C]",
    "T_stc_pump_w_out [°C]",
    "T_stc_w_in [°C]",
    "T_stc [°C]",
    "E_stc_pump [W]",
    "stc_active [-]",
]


def test_ground_and_routed_report_the_collector_columns_of_the_tank_variant(runs):
    tank_cols = set(runs["GSHPB_STC_tank"][1].columns)
    for name in ("GSHPB_STC_ground", "GSHPB_STC_routed"):
        cols = set(runs[name][1].columns)
        missing = [c for c in _TANK_COLLECTOR_COLUMNS if c not in cols]
        assert not missing, f"{name} lacks {missing}"
    assert set(_TANK_COLLECTOR_COLUMNS) <= tank_cols


@pytest.mark.parametrize("name", STC_NAMES)
def test_collector_absorbed_heat_equals_carried_plus_lost(runs, name):
    """Absorbed = carried away + lost, and carried away = to the tank + to the ground."""
    _, df, _ = runs[name]
    active = df["stc_active [-]"].astype(bool).to_numpy()
    assert active.any(), "collector never active"
    carried = col(df, "Q_stc_w_out [W]") - col(df, "Q_stc_w_in [W]")
    np.testing.assert_allclose(
        (carried + col(df, "Q_l_stc [W]"))[active], col(df, "Q_sol_stc [W]")[active], rtol=1e-9, atol=1e-6
    )
    to_sinks = col(df, "Q_solar_tank [W]") + col(df, "Q_solar_ground [W]")
    np.testing.assert_allclose(to_sinks, np.where(active, carried, 0.0), rtol=1e-9, atol=1e-6)


def _forced_router(**state) -> str:
    """Tank in the morning, ground in the afternoon: both routes get used."""
    return "tank" if state["hour_of_day"] < 12.0 else "ground"


@pytest.fixture(scope="module")
def routed_run():
    model = tmhp.GSHPB_STC_routed(stc=_stc(), solar_router=_forced_router, **_GROUND)
    return model, run(model), run(model)


def test_routed_collector_heat_goes_to_exactly_the_routed_sink(routed_run):
    """Collected = to tank + to ground, every step; the two sinks are exclusive."""
    _, df, _ = routed_run
    route = df["solar_route [-]"].to_numpy()
    assert {"tank", "ground"} <= set(route), f"routes used: {set(route)}"
    active = df["stc_active [-]"].astype(bool).to_numpy()
    carried = col(df, "Q_stc_w_out [W]") - col(df, "Q_stc_w_in [W]")
    to_tank = col(df, "Q_solar_tank [W]")
    to_ground = col(df, "Q_solar_ground [W]")
    np.testing.assert_allclose((to_tank + to_ground)[active], carried[active], rtol=1e-9, atol=1e-6)
    assert not ((to_tank != 0) & (to_ground != 0)).any()
    assert (to_tank[route == "tank"][active[route == "tank"]] > 0).all()
    assert (to_ground[route == "ground"][active[route == "ground"]] > 0).all()
    inactive = ~active
    assert (to_tank[inactive] == 0).all() and (to_ground[inactive] == 0).all()


def test_routed_tank_route_reports_the_collector_at_the_solved_tank_temperature(routed_run):
    """The heat reported for the tank is the heat the tank balance took (evaluated at T^{n+1})."""
    model, df, _ = routed_run
    rows = df[(df["solar_route [-]"] == "tank") & df["stc_active [-]"].astype(bool)]
    assert len(rows) > 0
    for i, row in rows.iterrows():
        plane = model._stc.plane_irradiance(row["I_DN_stc [W/m2]"], row["I_dH_stc [W/m2]"], i * DT, DT)
        ref = model._stc.calc_performance(
            I_DN_stc=row["I_DN_stc [W/m2]"],
            I_dH_stc=row["I_dH_stc [W/m2]"],
            T_stc_w_in_K=cu.C2K(row["T_tank_w [°C]"]),
            T0_K=cu.C2K(row["T0 [°C]"]),
            plane=plane,
        )
        share = row["stc_runtime_fraction [-]"]
        assert row["Q_solar_tank [W]"] == pytest.approx(share * (ref["Q_stc_w_out"] - ref["Q_stc_w_in"]), rel=1e-9)
        assert row["T_stc_w_in [°C]"] == pytest.approx(row["T_tank_w [°C]"])


def test_routed_and_ground_pump_runs_only_when_the_collector_is_active(routed_run):
    _, df, _ = routed_run
    pump = col(df, "E_stc_pump [W]")
    active = df["stc_active [-]"].astype(bool).to_numpy()
    # step average: the rated 60 W times the share of the step the loop ran
    share = col(df, "stc_runtime_fraction [-]")
    np.testing.assert_allclose(pump[active], 60.0 * share[active], rtol=1e-12)
    assert (share[active] > 0).all() and (pump[~active] == 0.0).all()


def test_routed_repeats_with_forced_router(routed_run):
    _, first, second = routed_run
    numeric = first.select_dtypes("number").columns
    pd.testing.assert_frame_equal(first[numeric], second[numeric], rtol=1e-9, atol=1e-9)


# ---------------------------------------------------------------------------
# the same model with the same inputs gives the same result
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CLASS_NAMES)
def test_second_run_of_the_same_model_reproduces_the_first(runs, name):
    _, first, second = runs[name]
    numeric = first.select_dtypes("number").columns
    pd.testing.assert_frame_equal(first[numeric], second[numeric], rtol=1e-9, atol=1e-9)
    non_numeric = [c for c in first.columns if c not in numeric]
    pd.testing.assert_frame_equal(first[non_numeric], second[non_numeric])


@pytest.mark.parametrize("name", CLASS_NAMES)
def test_a_fresh_model_matches_the_second_run_of_a_used_one(runs, name):
    _, _, second = runs[name]
    fresh = run(build(name, uv=name in PV_ESS_NAMES))
    numeric = fresh.select_dtypes("number").columns
    pd.testing.assert_frame_equal(fresh[numeric], second[numeric], rtol=1e-9, atol=1e-9)


def test_pv_ess_battery_starts_every_run_at_its_initial_state(runs):
    for name in PV_ESS_NAMES:
        model, first, second = runs[name]
        assert first["SOC_ess [-]"].iloc[0] == second["SOC_ess [-]"].iloc[0]
        assert model._ess.SOC_ess == second["SOC_ess [-]"].iloc[-1]
    # An unrelated state of charge left on the shared battery does not leak into the next run.
    model = build("ASHPB_PV_ESS")
    model._ess.SOC_ess = 0.95
    df = run(model)
    again = run(build("ASHPB_PV_ESS"))
    numeric = df.select_dtypes("number").columns
    pd.testing.assert_frame_equal(df[numeric], again[numeric], rtol=1e-9, atol=1e-9)


@pytest.mark.parametrize("name", PV_ESS_NAMES)
def test_pv_ess_uv_lamp_heat_enters_the_tank_once(name):
    """With the heat pump off the tank gains the lamp's heat to the water once (not twice)."""
    dt, n = 600, 18  # one full 3 h lamp period
    kw = {"uv": UVLamp(lamp_watts=1000.0, exposure_sec=600.0, num_switching=1, period_sec=3 * 3600)}
    if name == "ASHPB_PV_ESS":
        model = tmhp.ASHPB_PV_ESS(**_pv_ess_parts(), hp_on_schedule=[(0.0, 0.0)], **kw)
    else:
        parts = _pv_ess_parts()
        parts.pop("hp_capacity")
        model = tmhp.GSHPB_PV_ESS(**parts, **{**_GROUND, "dt_s": float(dt)}, hp_on_schedule=[(0.0, 0.0)], **kw)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = model.analyze_dynamic(
            n * dt,
            dt,
            50.0,
            np.zeros(n),
            np.full(n, 20.0),
            I_DN_schedule=np.zeros(n),
            I_dH_schedule=np.zeros(n),
            T_sup_w_schedule=np.full(n, 15.0),
            T_sur_schedule=np.full(n, 20.0),
        )
    lamp_J = float(col(df, "E_uv [W]").sum() * dt)
    assert lamp_J == pytest.approx(1000.0 * 600.0, rel=1e-6)
    gain_J = model.C_tank * (df["T_tank_w [°C]"].iloc[-1] - 50.0) + float(col(df, "Q_tank_loss [W]").sum() * dt)
    fraction = kw["uv"].heat_to_water_fraction
    assert gain_J == pytest.approx(fraction * lamp_J, rel=0.02)
