"""GSHPB tank-backend selection tests (lumped vs stratified).

The lumped single-node tank stays the default and byte-identical (its golden is
pinned in ``test_ground_coupling.py``); these tests cover the *selector*, the
construction guards, and that the stratified path runs and stratifies.
"""

from __future__ import annotations

import numpy as np
import pytest

# analyze_dynamic precomputes a g-function (needs pygfunction).
pytest.importorskip("pygfunction")

from tmhp import GroundSourceHeatPumpBoiler  # noqa: E402
from tmhp.stratified_tank import StratifiedTank  # noqa: E402
from tmhp.subsystems import SolarThermalCollector  # noqa: E402


def _gshpb(
    *,
    tank_model: str = "lumped",
    n_tank_nodes: int = 10,
    condenser_node: int | None = None,
    tank_always_full: bool = True,
    stc: SolarThermalCollector | None = None,
) -> GroundSourceHeatPumpBoiler:
    return GroundSourceHeatPumpBoiler(
        ref="R32",
        N_1=2,
        N_2=1,
        H_b=100.0,
        dt_s=3600.0,
        t_max_s=200 * 3600,
        tank_model=tank_model,
        n_tank_nodes=n_tank_nodes,
        condenser_node=condenser_node,
        tank_always_full=tank_always_full,
        stc=stc,
    )


def _run(hp: GroundSourceHeatPumpBoiler, tN: int = 24):
    dhw = np.zeros(tN)
    dhw[[i for i in (3, 4, 9, 10, 15, 16) if i < tN]] = 6.0e-5
    return hp.analyze_dynamic(
        simulation_period_sec=tN * 3600.0,
        dt_s=3600.0,
        T_tank_w_init_C=56.0,
        dhw_usage_schedule=dhw,
        T0_schedule=np.full(tN, 15.0),
    )


def test_default_is_lumped_single_node():
    hp = _gshpb()
    assert hp.tank_model == "lumped"
    assert hp._tank is None


def test_stratified_builds_multinode_tank():
    hp = _gshpb(tank_model="stratified", n_tank_nodes=12)
    assert isinstance(hp._tank, StratifiedTank)
    assert hp._tank.n == 12


def test_invalid_tank_model_raises():
    with pytest.raises(ValueError):
        _gshpb(tank_model="bogus")


def test_stratified_rejects_partial_fill():
    with pytest.raises(NotImplementedError):
        _gshpb(tank_model="stratified", tank_always_full=False)


def test_stratified_rejects_subsystems():
    with pytest.raises(NotImplementedError):
        _gshpb(tank_model="stratified", stc=SolarThermalCollector(A_stc=1.0))


def test_stratified_run_is_finite_and_stratifies():
    hp = _gshpb(tank_model="stratified", n_tank_nodes=10)
    df = _run(hp)
    for col in ("T_tank_w [°C]", "T_bhe [°C]", "T_bhe_f_in [°C]"):
        assert np.all(np.isfinite(df[col].to_numpy()))
    assert hp._tank is not None
    prof = hp._tank.T
    # The tank stratifies: top is hottest, monotone non-increasing downward.
    assert np.all(np.diff(prof) <= 1e-6)
    assert (prof[0] - prof[-1]) > 1.0  # non-trivial top-to-bottom spread


def test_lumped_and_stratified_both_physical():
    df_l = _run(_gshpb())
    df_s = _run(_gshpb(tank_model="stratified"))
    for df in (df_l, df_s):
        Tt = df["T_tank_w [°C]"].to_numpy()
        assert np.all(np.isfinite(Tt))
        assert Tt.min() > 5.0 and Tt.max() < 130.0  # bounded, no divergence


def test_condenser_node_concentrates_vs_spreads():
    """condenser_node=None spreads HP heat over the field; a fixed node
    concentrates it (so the two produce different profiles)."""
    spread = _gshpb(tank_model="stratified", n_tank_nodes=8)
    concen = _gshpb(tank_model="stratified", n_tank_nodes=8, condenser_node=0)
    _run(spread, tN=12)
    _run(concen, tN=12)
    assert spread._tank is not None
    assert concen._tank is not None
    assert not np.allclose(spread._tank.T, concen._tank.T)
