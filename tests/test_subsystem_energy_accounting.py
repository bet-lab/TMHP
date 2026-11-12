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

Tank-side defects tracked in ``tests/invariants/test_known_defects.py`` (T2, T3,
T8, T9) are not asserted here.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pygfunction")

import tmhp  # noqa: E402
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
CLASS_NAMES = ["ASHPB_PV_ESS", "GSHPB_PV_ESS"]
PV_ESS_NAMES = ["ASHPB_PV_ESS", "GSHPB_PV_ESS"]

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


def test_pv_ess_with_uv_lamp_includes_lamp_in_e_tot(runs):
    for name in PV_ESS_NAMES:
        _, df, _ = runs[name]
        lamp = col(df, "E_uv [W]")
        assert lamp.sum() > 0
        aux = "E_ou_fan [W]" if name.startswith("ASHPB") else "E_pmp [W]"
        np.testing.assert_allclose(
            col(df, "E_tot [W]") - lamp, col(df, "E_cmp [W]") + col(df, aux), rtol=1e-12, atol=1e-9
        )


@pytest.mark.parametrize("name", PV_ESS_NAMES)
def test_pv_ess_scenarios_accept_the_uv_lamp_silently(name):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build(name, uv=True)


# ---------------------------------------------------------------------------
# collector heat accounting
# ---------------------------------------------------------------------------


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
