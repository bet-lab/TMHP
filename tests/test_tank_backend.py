"""GSHPB tank-backend selection tests (lumped vs stratified vs hybrid).

The lumped single-node tank stays the default (its golden is pinned in
``test_ground_coupling.py``); these tests cover the *selector*, the
construction guards, and the multi-node backends: the condenser as a coil
that heats only water colder than the refrigerant, the cycle's condensing
temperature being the one that coil needs, the energy balance, and the
draw-off accounting at the water that leaves the top.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

# analyze_dynamic precomputes a g-function (needs pygfunction).
pytest.importorskip("pygfunction")

from tmhp import GroundSourceHeatPumpBoiler  # noqa: E402
from tmhp.constants import c_w, rho_w  # noqa: E402
from tmhp.hybrid_tank import HybridStratifiedTank  # noqa: E402
from tmhp.stratified_tank import StratifiedTank  # noqa: E402
from tmhp.subsystems import SolarThermalCollector, UVLamp  # noqa: E402

NODAL = ["stratified", "hybrid"]
T_INIT = 56.0


def _gshpb(
    *,
    tank_model: str = "lumped",
    n_tank_nodes: int = 10,
    condenser_node: int | None = None,
    tank_always_full: bool = True,
    stc: SolarThermalCollector | None = None,
    **kw,
) -> GroundSourceHeatPumpBoiler:
    return GroundSourceHeatPumpBoiler(
        # R134a: the default machine runs over the whole 55-65 °C band with no
        # envelope reduction, so a step costs one cycle solve (R32 costs six).
        ref="R134a",
        N_1=2,
        N_2=1,
        H_b=100.0,
        dt_s=kw.pop("dt_s", 3600.0),
        t_max_s=200 * 3600,
        tank_model=tank_model,
        n_tank_nodes=n_tank_nodes,
        condenser_node=condenser_node,
        tank_always_full=tank_always_full,
        stc=stc,
        **kw,
    )


def _run(hp: GroundSourceHeatPumpBoiler, tN: int = 12):
    dhw = np.zeros(tN)
    dhw[[i for i in (3, 4, 9, 10, 15, 16) if i < tN]] = 6.0e-5
    return hp.analyze_dynamic(
        simulation_period_sec=tN * 3600.0,
        dt_s=3600.0,
        T_tank_w_init_C=T_INIT,
        dhw_usage_schedule=dhw,
        T0_schedule=np.full(tN, 15.0),
    )


def _recorded(hp: GroundSourceHeatPumpBoiler, run):
    """Run ``run(hp)`` recording every tank step: (heat per node [W], end temperatures, result)."""
    tank = hp._tank
    assert tank is not None
    record: list[tuple[np.ndarray, np.ndarray, dict]] = []
    original = tank.step

    def step(dt, **kw):
        out = original(dt, **kw)
        q = np.asarray(out["q_hx"], dtype=float) + np.asarray(kw.get("q_source", np.zeros(tank.n)), dtype=float)
        record.append((q, tank.T.copy(), out))
        return out

    tank.step = step  # type: ignore[method-assign]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = run(hp)
    assert len(record) == len(df)
    return df, record


def _assert_heated_water_below(out: dict, T_end: np.ndarray, q: np.ndarray, T_cond: float) -> None:
    """The tank was given the cycle's condensing temperature and heated no water above it.

    ``T_hx_heated_max`` is the hottest control volume the exchanger heated (a
    zone of the hybrid tank's front node, whose node mean can lie above it).
    """
    assert out["T_hx"] == T_cond
    assert out["T_hx_heated_max"] is not None and out["T_hx_heated_max"] <= T_cond + 1e-9
    if "y_th" not in out or out["y_th"] is None:
        assert np.all(T_end[q > 0.0] <= T_cond + 1e-6), (T_cond, T_end, q)


def _balance_residual(hp: GroundSourceHeatPumpBoiler, df, dt: float, T_init: float = T_INIT) -> tuple[float, float]:
    """(condenser heat − storage change − draw-off − loss, condenser heat) [J], from the frame."""
    heat = float(df["Q_ref_tank [W]"].fillna(0.0).sum()) * dt
    stored = hp.C_tank * (float(df["T_tank_w [°C]"].iloc[-1]) - T_init)
    drawn = rho_w * c_w * df["dV_tank_w_out [m3/s]"].fillna(0.0) * (df["T_tank_w_out [°C]"] - df["T_tank_w_in [°C]"])
    loss = float(df["Q_tank_loss [W]"].sum()) * dt
    return heat - stored - float(drawn.sum()) * dt - loss, heat


@pytest.fixture(scope="module")
def day_runs():
    """12 h of the default-coil boiler on each multi-node backend, recorded."""
    return {model: (hp := _gshpb(tank_model=model), *_recorded(hp, _run)) for model in NODAL}


@pytest.fixture(scope="module")
def lumped_day():
    hp = _gshpb()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return hp, _run(hp)


# ---------------------------------------------------------------------------
# selector and construction guards
# ---------------------------------------------------------------------------


def test_default_is_lumped_single_node():
    hp = _gshpb()
    assert hp.tank_model == "lumped"
    assert hp._tank is None


def test_stratified_builds_multinode_tank():
    hp = _gshpb(tank_model="stratified", n_tank_nodes=12)
    assert isinstance(hp._tank, StratifiedTank)
    assert hp._tank.n == 12
    assert hp._tank.max_courant == 1.0  # plant calculation: numerical diffusion bounded for any dt


def test_hybrid_builds_thermocline_tank():
    hp = _gshpb(tank_model="hybrid", n_tank_nodes=12)
    assert isinstance(hp._tank, HybridStratifiedTank)
    assert hp._tank.n == 12
    assert hp._tank.volume == pytest.approx(hp.V_tank_full)
    assert hp._tank.ua_total == pytest.approx(hp.UA_tank_wall)


def test_invalid_tank_model_raises():
    with pytest.raises(ValueError, match="'lumped', 'stratified' or 'hybrid'"):
        _gshpb(tank_model="bogus")


@pytest.mark.parametrize("model", NODAL)
def test_multinode_rejects_partial_fill(model):
    with pytest.raises(NotImplementedError):
        _gshpb(tank_model=model, tank_always_full=False)


@pytest.mark.parametrize("model", NODAL)
def test_multinode_rejects_subsystems(model):
    with pytest.raises(NotImplementedError):
        _gshpb(tank_model=model, stc=SolarThermalCollector(A_stc=1.0))
    with pytest.raises(NotImplementedError):
        _gshpb(tank_model=model, uv=UVLamp(lamp_watts=40.0, exposure_sec=600.0))


@pytest.mark.parametrize("model", NODAL)
@pytest.mark.parametrize("node", [10, -11])
def test_condenser_node_outside_the_tank_raises(model, node):
    with pytest.raises(ValueError, match="condenser_node"):
        _gshpb(tank_model=model, n_tank_nodes=10, condenser_node=node)


# ---------------------------------------------------------------------------
# the coil's condensing level
# ---------------------------------------------------------------------------


def test_condensing_level_of_a_uniform_coil_is_mean_plus_approach():
    level = GroundSourceHeatPumpBoiler._condensing_level
    ua = np.full(4, 125.0)  # 500 W/K
    # Every node below the level: UA·(L − mean) = q.
    assert level(np.array([50.0, 48.0, 46.0, 44.0]), ua, 8000.0) == pytest.approx(47.0 + 16.0)
    # One node: its temperature plus q / UA.
    assert level(np.array([40.0]), np.array([500.0]), 8000.0) == pytest.approx(56.0)


def test_condensing_level_skips_nodes_hotter_than_the_refrigerant():
    """Two hot nodes above the level take no heat; the cold pair alone sets it, below mean + q/UA."""
    level = GroundSourceHeatPumpBoiler._condensing_level
    T = np.array([60.0, 60.0, 20.0, 20.0])
    ua = np.full(4, 500.0)  # 2000 W/K: approach 4 K on the whole coil
    L = level(T, ua, 8000.0)
    assert pytest.approx(20.0 + 8000.0 / 1000.0) == L  # the two cold nodes: 28 °C
    assert T.mean() + 8000.0 / 2000.0 > L  # the fully mixed tank would need 44 °C
    assert np.sum(ua * np.clip(L - T, 0.0, None)) == pytest.approx(8000.0)
    # A coil in one node sees that node only.
    one = np.array([0.0, 0.0, 0.0, 2000.0])
    assert level(T, one, 8000.0) == pytest.approx(24.0)


@pytest.mark.parametrize("model", NODAL)
def test_cycle_sink_temperature_of_a_stratified_tank_is_below_its_mean(model):
    hp = _gshpb(tank_model=model, n_tank_nodes=4, UA_tank_hx=2000.0)
    assert hp._tank is not None
    hp._tank.reset(np.array([60.0, 60.0, 20.0, 20.0]))
    # No run time: the level of the profile as it stands.
    T_sink = hp._nodal_sink_temperature(40.0, 8000.0, 0.0, 600.0, 0.0, 15.0)
    assert T_sink == pytest.approx(28.0 - 4.0)  # condenses at 28 °C, not at the lumped 44 °C
    hp._tank.reset(45.0)
    assert hp._nodal_sink_temperature(45.0, 8000.0, 0.0, 600.0, 0.0, 15.0) == pytest.approx(45.0)  # mixed tank


# ---------------------------------------------------------------------------
# dynamic runs
# ---------------------------------------------------------------------------


def test_one_node_stratified_tank_reproduces_the_lumped_tank(lumped_day):
    """N = 1 is the fully mixed tank: same cycle, same trajectory (the draw is sub-stepped).

    (A one-node hybrid tank is plug flow, not a mixed tank, and is not expected to match.)
    """
    _, base = lumped_day
    hp = _gshpb(tank_model="stratified", n_tank_nodes=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = _run(hp)
    np.testing.assert_allclose(df["T_tank_w [°C]"], base["T_tank_w [°C]"], atol=0.1)
    assert df["Q_ref_tank [W]"].sum() == pytest.approx(base["Q_ref_tank [W]"].sum(), rel=2e-3)
    assert df["E_cmp [W]"].sum() == pytest.approx(base["E_cmp [W]"].sum(), rel=2e-3)
    assert list(df["hp_is_on"]) == list(base["hp_is_on"])


@pytest.mark.parametrize("model", NODAL)
def test_multinode_run_is_finite_and_stratifies(model, day_runs):
    hp, df, _ = day_runs[model]
    for col in ("T_tank_w [°C]", "T_bhe [°C]", "T_bhe_f_in [°C]", "T_tank_w_top [°C]", "T_tank_w_out [°C]"):
        assert np.all(np.isfinite(df[col].to_numpy()))
    prof = hp._tank.T
    # The tank stratifies: top is hottest, monotone non-increasing downward.
    assert np.all(np.diff(prof) <= 1e-6)
    assert (prof[0] - prof[-1]) > 1.0  # non-trivial top-to-bottom spread
    assert np.all(df["T_tank_w_top [°C]"] >= df["T_tank_w [°C]"] - 1e-9)
    assert np.all(df["T_tank_w_bottom [°C]"] <= df["T_tank_w [°C]"] + 1e-9)


@pytest.mark.parametrize("model", NODAL)
def test_condenser_heat_goes_only_to_nodes_colder_than_the_refrigerant(model, day_runs):
    """No heated node ends a step above the cycle's condensing temperature (#96 T4).

    Before: the heat was spread evenly whatever the node temperatures and the
    top node reached 97.9 °C in this run (cycle condensing at 76.9 °C).
    """
    _, df, record = day_runs[model]
    ran = 0
    for (q, T_end, out), (_, row) in zip(record, df.iterrows(), strict=True):
        if not float(row["E_cmp [W]"]) > 0.0:
            assert not q.any(), "tank heated on a step without a running cycle"
            continue
        ran += 1
        T_cond = min(float(row["T_ref_cond_sat_v [°C]"]), float(row["T_ref_cond_sat_l [°C]"]))
        assert np.all(q >= 0.0)
        _assert_heated_water_below(out, T_end, q, T_cond)
        assert float(q.sum()) == pytest.approx(float(row["Q_ref_tank [W]"]), rel=1e-9)
    assert ran >= 4
    assert max(float(T.max()) for _, T, _ in record) < 80.0


@pytest.mark.parametrize("model", NODAL)
def test_multinode_energy_balance_closes_from_the_result_frame(model, day_runs):
    """Condenser heat = storage change + draw-off (at the temperature that left) + wall loss."""
    hp, df, _ = day_runs[model]
    residual, heat = _balance_residual(hp, df, 3600.0)
    assert heat > 15 * 3.6e6
    assert abs(residual) < 1e-6 * heat


@pytest.mark.parametrize("model", NODAL)
def test_multinode_thermostat_and_energy_follow_the_lumped_tank(model, day_runs, lumped_day):
    """The thermostat reads the mean temperature: same on/off steps and heat as the lumped tank."""
    _, df, _ = day_runs[model]
    _, base = lumped_day
    assert list(df["hp_is_on"]) == list(base["hp_is_on"])
    assert df["Q_ref_tank [W]"].sum() == pytest.approx(base["Q_ref_tank [W]"].sum(), rel=5e-3)
    assert df["T_tank_w [°C]"].max() <= hp_upper(base) + 1e-6
    # The cycle never condenses hotter than against the fully mixed tank.
    assert df["E_cmp [W]"].sum() <= base["E_cmp [W]"].sum() * (1 + 5e-3)


def hp_upper(df) -> float:
    return float(df["T_tank_w [°C]"].max())


@pytest.mark.parametrize("model", NODAL)
def test_draw_off_is_accounted_at_the_water_that_left_the_top(model, day_runs):
    hp, df, record = day_runs[model]
    draw = df["dV_mix_w_out [m3/s]"] > 0
    assert draw.sum() == 4
    # The mixing valve reaches its 40 °C target on the top water, not on the mean.
    np.testing.assert_allclose(df.loc[draw, "T_mix_w_out [°C]"], hp.T_mix_w_out, atol=0.02)
    for (_, _, out), (_, row) in zip(record, df.iterrows(), strict=True):
        assert row["T_tank_w_out [°C]"] == out["T_draw_out"]
        assert row["Q_tank_loss [W]"] == out["Q_loss"]
    assert np.all(df.loc[draw, "T_tank_w_out [°C]"] > df.loc[draw, "T_tank_w [°C]"] - 1e-9)
    # Exergy/energy of the outflow use the same temperature.
    T0_K = df["T0 [°C]"] + 273.15
    expected = c_w * rho_w * df["dV_tank_w_out [m3/s]"].fillna(0) * (df["T_tank_w_out [°C]"] + 273.15 - T0_K)
    np.testing.assert_allclose(df["Q_tank_w_out [W]"], expected, rtol=1e-12, atol=1e-9)


def test_lumped_and_stratified_both_physical(day_runs, lumped_day):
    for df in (lumped_day[1], day_runs["stratified"][1]):
        Tt = df["T_tank_w [°C]"].to_numpy()
        assert np.all(np.isfinite(Tt))
        assert Tt.min() > 5.0 and Tt.max() < 66.0  # inside the thermostat band, no divergence


def test_lumped_exergy_splits_condenser_and_storage_tank(lumped_day):
    _, df = lumped_day
    assert "Xc_ref_tank [W]" in df.columns
    assert df["Xc_tank [W]"].notna().all()
    assert "T_tank_w_out [°C]" not in df.columns  # multi-node columns stay out of the lumped frame

    expected_cond = df["X_ref_cmp_out [W]"] - df["X_ref_exp_in [W]"] - df["X_ref_tank [W]"]
    expected_tank = (
        df["X_ref_tank [W]"] + df["X_tank_w_in [W]"].fillna(0) - df["Xst_tank [W]"] - df["X_tank_w_out [W]"].fillna(0)
    )

    cond_mask = expected_cond.notna()
    assert cond_mask.any()
    np.testing.assert_allclose(
        df.loc[cond_mask, "Xc_ref_tank [W]"],
        expected_cond.loc[cond_mask],
        rtol=1e-12,
        atol=1e-9,
    )
    np.testing.assert_allclose(
        df["Xc_tank [W]"],
        expected_tank,
        rtol=1e-12,
        atol=1e-9,
    )


# ---------------------------------------------------------------------------
# coil position
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def top_coil():
    """Coil in the top node only (8 h): it can heat no water below itself."""
    hp = _gshpb(tank_model="stratified", n_tank_nodes=8, condenser_node=0)
    return (hp, *_recorded(hp, lambda m: _run(m, tN=8)))


def test_top_coil_stops_when_its_water_reaches_the_condensing_temperature(top_coil):
    """Was 364 °C in the top node under a 15 °C bottom (heat put in whatever the temperature)."""
    hp, df, record = top_coil
    cut = [i for i, (_, _, out) in enumerate(record) if out["hx_limited"]]
    assert cut, "the top node took a whole run's heat"
    for i in cut:
        row, (q, T_end, out) = df.iloc[i], record[i]
        T_cond = float(row["T_ref_cond_sat_l [°C]"])
        assert T_end[0] == pytest.approx(T_cond, abs=1e-3)  # heated up to the refrigerant, not past it
        assert np.all(q[1:] == 0.0)
        assert 0.0 < row["runtime_fraction [-]"] < 1.0
        assert row["Q_ref_tank [W]"] == pytest.approx(out["Q_hx"], rel=1e-9)  # the row is cut to what went in
        assert row["E_cmp [W]"] > 0.0 and bool(row["hp_is_on"])
    # Never past the refrigerant, never past boiling.
    assert max(float(T.max()) for _, T, _ in record) <= min(100.0, df["T_ref_cond_sat_l [°C]"].max()) + 1e-6
    # Water below the coil stays cold; once the top is hot the cycle has no operating point, and says so.
    assert float(hp._tank.T[-1]) < 20.0
    assert (df["failure_reason"] != "none").any()
    assert df.loc[df["failure_reason"] != "none", "Q_ref_tank [W]"].eq(0.0).all()


def test_top_coil_energy_balance_closes(top_coil):
    hp, df, _ = top_coil
    residual, heat = _balance_residual(hp, df, 3600.0)
    assert heat > 3.6e6
    assert abs(residual) < 1e-6 * heat


@pytest.mark.parametrize("model", NODAL)
def test_bottom_coil_mixes_instead_of_inverting(model):
    """Heat delivered at the bottom node rises by buoyancy: the profile stays monotone, the whole
    tank is heated, and the run matches the lumped tank's heat. Before the inversion mixing (#96)
    the bottom node of this run ended at 104 °C under a 54 °C top."""
    hp = _gshpb(tank_model=model, n_tank_nodes=8, condenser_node=7)
    df, record = _recorded(hp, _run)
    assert hp._tank is not None
    prof = hp._tank.T
    assert np.all(np.diff(prof) <= 1e-4)
    assert prof.max() < 70.0
    assert not any(out["hx_limited"] for _, _, out in record)
    assert df["T_tank_w [°C]"].min() > 50.0  # the thermostat band is held
    residual, heat = _balance_residual(hp, df, 3600.0)
    assert abs(residual) < 1e-6 * heat


def test_condenser_node_concentrates_vs_spreads(day_runs, top_coil):
    """condenser_node=None spreads the coil over the tank; a fixed node concentrates it."""
    spread, concen = day_runs["stratified"][0], top_coil[0]
    assert spread._condenser_ua().tolist() == [50.0] * 10
    assert concen._condenser_ua().tolist() == [500.0] + [0.0] * 7
    assert spread._tank.T[-1] > 40.0 > concen._tank.T[-1]


@pytest.mark.parametrize("model", NODAL)
def test_tight_coil_under_a_heavy_draw_leaves_the_hot_top_unheated(model):
    """A coil with a 2.7 K approach under a 6 L/min draw: the cycle condenses near the cold bottom
    water, below the 60 °C top, which then receives nothing; no step is cut short."""
    dt, n = 300.0, 7
    hp = _gshpb(tank_model=model, UA_tank_hx=3000.0, dt_s=dt)
    draw = np.zeros(n)
    draw[:4] = 6.0 / 60000.0

    def run(m):
        return m.analyze_dynamic(n * dt, dt, 60.0, draw, np.full(n, 10.0))

    df, record = _recorded(hp, run)
    unheated_hot = 0
    for (q, T_end, out), (_, row) in zip(record, df.iterrows(), strict=True):
        if not q.any():
            continue
        T_cond = float(row["T_ref_cond_sat_l [°C]"])
        _assert_heated_water_below(out, T_end, q, T_cond)
        assert not out["hx_limited"]
        assert row["runtime_fraction [-]"] > 0.0
        unheated_hot += int(np.sum((q == 0.0) & (T_end > T_cond)))
    assert unheated_hot >= 5
    residual, heat = _balance_residual(hp, df, dt, T_init=60.0)
    assert heat > 3.6e6 and abs(residual) < 1e-6 * heat
    # Condensing against the cold bottom: below what the mean temperature would need.
    on = df["E_cmp [W]"] > 0
    lumped_level = df.loc[on, "T_tank_w [°C]"] + 8000.0 / 3000.0
    assert (df.loc[on, "T_ref_cond_sat_l [°C]"] < lumped_level - 1.0).sum() >= 2
