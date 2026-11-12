"""Tests for the hybrid 1-D thermocline tank (Cruz-Loredo 2023, conservative form).

Headline properties: the thermocline barrier stays sharp (no numerical
diffusion across it), sits where plug flow puts it (``V_injected / A`` below the
inlet) for any time step — also when it crosses several nodes in one step — and
every step closes the energy balance to round-off (#96 T5).
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from tmhp.constants import c_w, rho_w
from tmhp.hybrid_tank import FRONT_MERGE_TOL_K, HybridStratifiedTank
from tmhp.stratified_tank import StratifiedTank, TankOverheatWarning

_VOL = 0.2
_H = 1.2
_DT = 60.0
_RC = rho_w * c_w


def _transition_width(T, t_cold, t_hot):
    """Number of nodes inside the 25–75% temperature transition band."""
    span = t_hot - t_cold
    lo, hi = t_cold + 0.25 * span, t_cold + 0.75 * span
    return int(np.sum((lo < T) & (hi > T)))


def _balance_step(tank, dt, **kw):
    """Step and return (ΔE, Σ fluxes·dt) from the result's outflow temperatures."""
    e0 = tank.stored_energy
    out = tank.step(dt, **kw)
    cf, df = kw.get("charge_flow", 0.0), kw.get("draw_flow", 0.0)
    q = float(np.sum(kw.get("q_source", 0.0)))
    flux = (
        _RC * cf * (kw.get("T_charge", 0.0) - out["T_charge_out"])
        + _RC * df * (kw.get("T_makeup", 10.0) - out["T_draw_out"])
        + q
        - out["Q_loss"]
    )
    return tank.stored_energy - e0, dt * flux, out


def test_construction_and_reset_validation():
    with pytest.raises(ValueError):
        HybridStratifiedTank(0, _VOL, _H)
    with pytest.raises(ValueError):
        HybridStratifiedTank(4, _VOL, 0.0)
    with pytest.raises(ValueError, match="front"):
        HybridStratifiedTank(4, _VOL, _H, front="sideways")
    t = HybridStratifiedTank(5, _VOL, _H)
    t.reset(40.0)
    assert np.allclose(t.T, 40.0)
    assert t.y_th is None
    with pytest.raises(ValueError):
        t.reset(np.array([1.0, 2.0]))


def test_hybrid_sharper_than_standard_during_charge():
    """At equal node count the hybrid thermocline is sharper (less diffusion)."""
    n = 20
    t_cold, t_hot = 15.0, 60.0
    q = 1.2e-4

    hyb = HybridStratifiedTank(n, _VOL, _H, k_eff=0.606, ua=0.0)
    std = StratifiedTank(n, _VOL, _H, k_eff=0.606, ua=0.0)
    hyb.reset(t_cold)
    std.reset(t_cold)

    # Charge until the front is near mid-tank.
    nsteps = 14
    for _ in range(nsteps):
        hyb.step(_DT, charge_flow=q, T_charge=t_hot)
        std.step(_DT, charge_flow=q, T_charge=t_hot)

    w_hyb = _transition_width(hyb.T, t_cold, t_hot)
    w_std = _transition_width(std.T, t_cold, t_hot)
    assert w_hyb < w_std  # hybrid front is sharper
    assert w_hyb <= 1  # only the node holding the barrier can be in between
    # Both remain monotone non-increasing (stratified, no oscillation).
    assert np.all(np.diff(hyb.T) <= 1e-9)
    assert np.all(np.diff(std.T) <= 1e-9)


def test_thermocline_descends_at_v_th():
    n = 12
    q = 1.2e-4
    t = HybridStratifiedTank(n, _VOL, _H, ua=0.0)
    t.reset(15.0)
    v_th = q / t.area_cross
    for k in range(1, 6):
        out = t.step(_DT, charge_flow=q, T_charge=60.0)
        expected = _H - v_th * (k * _DT)
        assert t.y_th == pytest.approx(expected, rel=1e-9)
        assert out["y_th"] == t.y_th


@pytest.mark.parametrize("n", [5, 20, 60])
@pytest.mark.parametrize("dt", [10.0, 60.0, 600.0, 1500.0])
@pytest.mark.parametrize("flow", [0.5e-4, 2.0e-4])
def test_charge_conserves_energy_and_places_front(n, dt, flow):
    """#96 T5: stored = injected − loss (to round-off) and the barrier sits
    ``V_injected/A`` below the top for any dt, node count and flow — including
    steps that move the front across many nodes."""
    t = HybridStratifiedTank(n, _VOL, _H, ua=3.0)
    t.reset(15.0)
    e_start, injected = t.stored_energy, 0.0
    v_in = 0.0
    for _ in range(40):
        if v_in + flow * dt >= _VOL:  # stop before the front leaves the tank
            break
        de, flux, out = _balance_step(t, dt, charge_flow=flow, T_charge=60.0, T_amb=20.0)
        assert de == pytest.approx(flux, rel=1e-9, abs=1e-6)
        injected += flux
        v_in += flow * dt
        assert t.y_th == pytest.approx(_H - v_in / t.area_cross, abs=1e-12)
        assert np.all(np.diff(t.T) <= 1e-9)
        # Sharp: besides the node holding the barrier, only the physical conduction
        # smear (25-75 % band 1.35·sqrt(2·α·t)) may put nodes in the transition band.
        sigma = np.sqrt(2.0 * (t.k_eff / _RC) * (v_in / flow))
        assert _transition_width(t.T, 15.0, 60.0) <= 1 + int(np.ceil(1.349 * sigma / t.dz))
    assert t.stored_energy - e_start == pytest.approx(injected, rel=1e-9)


def test_front_crosses_several_nodes_in_one_step():
    n = 20
    t = HybridStratifiedTank(n, _VOL, _H, k_eff=1e-9, ua=0.0)  # no conduction: pure plug flow
    t.reset(15.0)
    vol = 7.5 * t.v_node  # 7.5 nodes in one step
    t.step(600.0, charge_flow=vol / 600.0, T_charge=60.0)
    expected = np.full(n, 15.0)
    expected[:7] = 60.0
    expected[7] = 0.5 * (60.0 + 15.0)
    np.testing.assert_allclose(t.T, expected, atol=1e-6)
    assert t.front_temperatures == pytest.approx((60.0, 15.0), abs=1e-6)


def test_charge_storage_matches_plug_flow_at_large_dt():
    """12 L/min for 1 h into a 200 L tank, dt 600 s (#96: was −92 % energy, bottom 15 °C)."""
    t = HybridStratifiedTank(20, _VOL, _H, ua=0.0)
    t.reset(15.0)
    e0, injected = t.stored_energy, 0.0
    for _ in range(6):
        out = t.step(600.0, charge_flow=0.2e-3, T_charge=60.0)
        injected += 600.0 * _RC * 0.2e-3 * (60.0 - out["T_charge_out"])
    assert t.stored_energy - e0 == pytest.approx(injected, rel=1e-12)
    assert np.allclose(t.T, 60.0, atol=0.5)  # 3.6 tank volumes: fully charged


def test_long_charge_fills_hot():
    n = 10
    t = HybridStratifiedTank(n, _VOL, _H, ua=0.0)
    t.reset(15.0)
    for _ in range(400):
        t.step(_DT, charge_flow=1.0e-4, T_charge=55.0)
    assert np.allclose(t.T, 55.0, atol=1e-2)
    assert t.y_th is None  # the barrier left through the bottom


def test_barrier_landing_on_node_boundary_is_robust():
    """One node volume per step lands the barrier exactly on node boundaries;
    a perturbation far below any physical scale must give the same state."""
    n = 10
    states = []
    for eps in (0.0, 1e-13, -1e-13):
        t = HybridStratifiedTank(n, _VOL, _H, ua=1.0)
        t.reset(15.0)
        q = t.v_node / _DT * (1.0 + eps)
        for _ in range(4):
            t.step(_DT, charge_flow=q, T_charge=60.0, T_amb=20.0)
        states.append((t.T.copy(), t.y_th))
    for T, y in states[1:]:
        np.testing.assert_allclose(T, states[0][0], atol=1e-6)
        assert y == pytest.approx(states[0][1], abs=1e-9)


def test_barrier_persists_while_idle_and_conserves_energy():
    t = HybridStratifiedTank(20, _VOL, _H, ua=2.0)
    t.reset(15.0)
    for _ in range(10):
        t.step(_DT, charge_flow=1.2e-4, T_charge=60.0)
    y = t.y_th
    for _ in range(60):
        de, flux, _ = _balance_step(t, _DT, T_amb=20.0)
        assert de == pytest.approx(flux, rel=1e-9, abs=1e-6)
    assert t.y_th == y  # idle: the barrier does not move or vanish
    assert np.all(np.diff(t.T) <= 1e-9)


def test_discharge_front_rises_from_the_bottom():
    t = HybridStratifiedTank(20, _VOL, _H, k_eff=1e-9, ua=0.0)
    t.reset(60.0)
    q = 1.0e-4
    for k in range(1, 11):
        de, flux, out = _balance_step(t, _DT, draw_flow=q, T_makeup=12.0)
        assert de == pytest.approx(flux, rel=1e-9, abs=1e-6)
        assert t.y_th == pytest.approx(q * k * _DT / t.area_cross, rel=1e-9)
        assert out["T_draw_out"] == pytest.approx(60.0, abs=1e-6)  # plug flow: hot until the front arrives


def test_charge_only_mode_dissolves_barrier_on_draw():
    """front='charge' is the paper's Eq. 7: the barrier is destroyed by discharge."""
    t = HybridStratifiedTank(20, _VOL, _H, ua=1.0, front="charge")
    t.reset(15.0)
    for _ in range(8):
        t.step(_DT, charge_flow=1.2e-4, T_charge=60.0)
    assert t.y_th is not None
    de, flux, _ = _balance_step(t, _DT, draw_flow=1.0e-4, T_makeup=12.0, T_amb=20.0)
    assert de == pytest.approx(flux, rel=1e-9, abs=1e-6)
    assert t.y_th is None
    # idle keeps whatever exists; charge forms a new barrier at the top
    t.step(_DT, charge_flow=1.2e-4, T_charge=65.0)
    assert t.y_th is not None and t.y_th > 0.9 * _H


def test_discharge_only_mode_has_no_charge_barrier():
    t = HybridStratifiedTank(20, _VOL, _H, front="discharge")
    t.reset(15.0)
    t.step(_DT, charge_flow=1.2e-4, T_charge=60.0)
    assert t.y_th is None


@pytest.mark.parametrize("dt", [60.0, 600.0])
def test_simultaneous_charge_and_draw_conserves_energy(dt):
    t = HybridStratifiedTank(12, _VOL, _H, ua=4.0)
    t.reset(np.linspace(55.0, 25.0, 12))
    for chg, drw in ((1.0e-4, 0.4e-4), (0.4e-4, 1.0e-4), (0.8e-4, 0.8e-4)):
        de, flux, out = _balance_step(t, dt, charge_flow=chg, T_charge=62.0, draw_flow=drw, T_makeup=12.0, T_amb=18.0)
        assert de == pytest.approx(flux, rel=1e-9, abs=1e-6)
        assert np.all(np.diff(t.T) <= 1e-9)


def test_cold_charge_into_hot_top_is_mixed_not_inverted():
    """Charging colder than the top is buoyantly unstable: it must mix, not layer."""
    t = HybridStratifiedTank(10, _VOL, _H, ua=0.0)
    t.reset(np.linspace(60.0, 40.0, 10))
    for _ in range(5):
        de, flux, _ = _balance_step(t, _DT, charge_flow=1.0e-4, T_charge=30.0)
        assert de == pytest.approx(flux, rel=1e-9, abs=1e-6)
        assert np.all(np.diff(t.T) <= 1e-9)


@pytest.mark.parametrize("n", [4, 10, 30])
@pytest.mark.parametrize("dt", [60.0, 600.0])
def test_bottom_heater_mixes_the_tank(n, dt):
    """A bottom heater makes the bottom buoyant: the profile stays monotone and,
    with no loss, the whole tank ends at the energy-mean temperature."""
    t = HybridStratifiedTank(n, _VOL, _H, ua=0.0)
    t.reset(40.0)
    q = np.zeros(n)
    q[-1] = 3000.0
    for _ in range(int(3600 / dt)):
        de, flux, _ = _balance_step(t, dt, q_source=q)
        assert de == pytest.approx(flux, rel=1e-9, abs=1e-6)
        assert np.all(np.diff(t.T) <= 1e-9)
    t_mean = 40.0 + 3000.0 * 3600.0 / (_RC * _VOL)
    np.testing.assert_allclose(t.T, t_mean, atol=1e-6)


def test_scalar_heat_source_goes_to_top_node():
    t = HybridStratifiedTank(6, _VOL, _H, ua=0.0)
    t.reset(40.0)
    t.step(_DT, q_source=2000.0)
    assert t.T[0] > 40.4
    assert t.T[1] == pytest.approx(40.0, abs=1e-2)  # only conduction reaches node 1


def test_overheat_is_flagged_and_warned():
    t = HybridStratifiedTank(4, _VOL, _H, ua=0.0)
    t.reset(95.0)
    with pytest.warns(TankOverheatWarning):
        out = t.step(600.0, q_source=20_000.0)
    assert out["overheat"] and out["T_max"] > 100.0
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        t.reset(50.0)
        assert not t.step(60.0, q_source=1000.0)["overheat"]


def test_barrier_dropped_once_both_sides_equalise():
    """Charging a uniformly hot tank with the same temperature leaves no barrier."""
    t = HybridStratifiedTank(10, _VOL, _H, ua=0.0)
    t.reset(60.0)
    t.step(_DT, charge_flow=1.0e-4, T_charge=60.0 + 0.5 * FRONT_MERGE_TOL_K)
    assert t.y_th is None


def test_equal_charge_and_draw_still_heat_the_inlet_node():
    """Codex review: with charge == draw the tank must still exchange heat at the
    inlets (fully mixed inlet nodes), not pass the charge straight to the draw."""
    t = HybridStratifiedTank(10, _VOL, _H, ua=0.0)
    t.reset(np.linspace(55.0, 25.0, 10))
    e0 = t.stored_energy
    q = 1.0e-4
    de, flux, out = _balance_step(t, 600.0, charge_flow=q, T_charge=60.0, draw_flow=q, T_makeup=12.0)
    assert de == pytest.approx(flux, rel=1e-9, abs=1e-6)
    assert t.T[0] > 55.0 + 1.0  # top node warmed by the hot charge
    assert t.T[-1] < 25.0 - 1.0  # bottom node cooled by the makeup
    assert 55.0 < out["T_draw_out"] < 60.0  # draw is mixed top-node water
    assert 12.0 < out["T_charge_out"] < 25.0
    assert t.stored_energy != pytest.approx(e0)


@pytest.mark.parametrize("ratio", [0.25, 0.5, 0.9])
def test_simultaneous_flow_is_time_step_independent(ratio):
    """Barrier position and stored energy with overlapping port flows do not depend on dt."""
    q = 1.2e-4
    results = []
    for dt in (10.0, 60.0, 600.0):
        t = HybridStratifiedTank(20, _VOL, _H, k_eff=1e-9, ua=0.0)
        t.reset(15.0)
        e0 = t.stored_energy
        for _ in range(int(1200.0 / dt)):
            t.step(dt, charge_flow=q, T_charge=60.0, draw_flow=ratio * q, T_makeup=15.0)
        results.append((t.y_th, t.stored_energy - e0))
    # The barrier forms below the mixed inlet node and then moves with the net flow.
    y_expected = _H - (t.v_node + (1.0 - ratio) * q * 1200.0) / t.area_cross
    for y, gain in results:
        assert y == pytest.approx(y_expected, abs=1e-9)
        assert gain == pytest.approx(results[0][1], rel=2e-3)
