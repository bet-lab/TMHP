"""Isolated physics tests for the multi-node StratifiedTank (Cadau 2019).

These pin the conservation and stratification invariants of the standalone tank
model before it is wired into the heat-pump plant as a swappable backend.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from tmhp.constants import c_w, rho_w
from tmhp.stratified_tank import INVERSION_TOL_K, StratifiedTank, TankOverheatWarning, mix_inversions

# A representative small buffer tank.
_VOL = 0.2  # m³
_H = 1.2  # m
_DT = 60.0  # s


def test_construction_validation():
    with pytest.raises(ValueError):
        StratifiedTank(0, _VOL, _H)
    with pytest.raises(ValueError):
        StratifiedTank(4, -1.0, _H)
    with pytest.raises(ValueError):
        StratifiedTank(4, _VOL, 0.0)


def test_reset_scalar_and_array():
    t = StratifiedTank(5, _VOL, _H)
    t.reset(50.0)
    assert np.allclose(t.T, 50.0)
    t.reset(np.array([60.0, 55.0, 50.0, 45.0, 40.0]))
    assert t.T[0] == 60.0 and t.T[-1] == 40.0
    with pytest.raises(ValueError):
        t.reset(np.array([1.0, 2.0]))


def test_n1_recovers_lumped_fully_mixed():
    """N=1 must equal the closed-form implicit fully-mixed tank update."""
    ua = 5.0
    t = StratifiedTank(1, _VOL, _H, ua=ua)
    t.reset(40.0)
    q, Tc, Ta = 1.0e-4, 60.0, 18.0
    out = t.step(_DT, charge_flow=q, T_charge=Tc, T_amb=Ta)

    m = rho_w * _VOL
    mc_dt = m * c_w / _DT
    mdot_c = rho_w * q * c_w
    expected = (mc_dt * 40.0 + mdot_c * Tc + ua * Ta) / (mc_dt + mdot_c + ua)
    assert out["T_top"] == pytest.approx(expected, rel=0, abs=1e-9)


def test_energy_conservation_under_charge():
    """ΔE_stored = dt·[ṁc(T_charge − T_outlet) − Σ UA_i(T_i − T_amb)] (implicit)."""
    n = 8
    ua = 12.0
    t = StratifiedTank(n, _VOL, _H, ua=ua)
    t.reset(np.linspace(55.0, 35.0, n))
    e0 = t.stored_energy
    q, Tc, Ta = 8.0e-5, 62.0, 16.0
    out = t.step(_DT, charge_flow=q, T_charge=Tc, T_amb=Ta)
    de = t.stored_energy - e0

    mdot_c = rho_w * q * c_w
    advection = mdot_c * (Tc - out["T_outlet"])
    loss = (ua / n) * np.sum(t.T - Ta)
    expected_de = _DT * (advection - loss)
    assert de == pytest.approx(expected_de, rel=1e-9, abs=1e-6)


def test_adiabatic_conduction_conserves_energy_and_smooths():
    """No flow, no loss: pseudo-conduction redistributes heat, energy conserved."""
    n = 10
    t = StratifiedTank(n, _VOL, _H, k_eff=5.0, ua=0.0)  # exaggerated k for visible mixing
    profile = np.concatenate([np.full(n // 2, 60.0), np.full(n - n // 2, 30.0)])
    t.reset(profile)
    e0 = t.stored_energy
    var0 = t.T.var()
    for _ in range(50):
        t.step(_DT, charge_flow=0.0, T_amb=20.0)
    assert t.stored_energy == pytest.approx(e0, rel=1e-12)  # adiabatic + no flow
    assert t.T.var() < var0  # profile smoothed


def test_ambient_loss_decays_toward_t_amb():
    n = 6
    ua = 20.0
    t = StratifiedTank(n, _VOL, _H, ua=ua)
    T0, Ta = 70.0, 20.0
    t.reset(T0)
    e_prev = t.stored_energy
    nsteps = 200
    for _ in range(nsteps):
        t.step(_DT, charge_flow=0.0, T_amb=Ta)
        e_now = t.stored_energy
        assert e_now <= e_prev + 1e-9  # monotone non-increasing
        e_prev = e_now
    # Uniform start + no flow ⇒ every node is an independent lumped loss; the
    # backward-Euler discrete decay is exact: T_k = Ta + (T0−Ta)·r^k with
    # r = 1/(1 + UA_node·dt/(m_node·cp)).
    m_node = rho_w * (_VOL / n)
    r = 1.0 / (1.0 + (ua / n) * _DT / (m_node * c_w))
    expected = Ta + (T0 - Ta) * r**nsteps
    assert np.allclose(t.T, expected, rtol=1e-9)
    assert np.all(t.T < T0) and np.all(Ta < t.T)


def test_charge_preserves_monotonic_stratification():
    """Charging a cold tank with hot water keeps T monotone non-increasing
    downward (no spurious oscillation) and propagates a downward thermocline."""
    n = 12
    t = StratifiedTank(n, _VOL, _H, k_eff=0.606, ua=0.0)
    t.reset(15.0)
    q, Tc = 1.2e-4, 60.0
    top_history = []
    bottom_history = []
    for _ in range(80):
        out = t.step(_DT, charge_flow=q, T_charge=Tc, T_amb=20.0)
        assert np.all(np.diff(t.T) <= 1e-9)  # T[i] >= T[i+1] (stratified)
        top_history.append(out["T_top"])
        bottom_history.append(out["T_outlet"])
    # Top heats well before the bottom (thermocline travels downward).
    assert top_history[5] > 40.0
    assert bottom_history[5] < 20.0
    assert bottom_history[-1] > bottom_history[5]


def test_long_charge_approaches_inlet_temperature():
    n = 8
    t = StratifiedTank(n, _VOL, _H, ua=0.0)
    t.reset(20.0)
    Tc = 55.0
    for _ in range(2000):
        t.step(_DT, charge_flow=1.0e-4, T_charge=Tc, T_amb=20.0)
    assert np.allclose(t.T, Tc, atol=1e-3)


def test_energy_conservation_charge_and_draw():
    """General balance with simultaneous charge + draw ports + loss."""
    n = 8
    ua = 10.0
    t = StratifiedTank(n, _VOL, _H, ua=ua)
    t.reset(np.linspace(58.0, 38.0, n))
    e0 = t.stored_energy
    chg, Tc = 6.0e-5, 62.0
    drw, Tm = 4.0e-5, 12.0
    Ta = 18.0
    out = t.step(_DT, charge_flow=chg, T_charge=Tc, draw_flow=drw, T_makeup=Tm, T_amb=Ta)
    de = t.stored_energy - e0

    rc = rho_w * c_w
    e_in = rc * chg * Tc + rc * drw * Tm  # hot charge + cold makeup
    e_out = rc * drw * out["T_top"] + rc * chg * out["T_outlet"]  # hot draw + cold HP return
    loss = (ua / n) * np.sum(t.T - Ta)
    expected_de = _DT * (e_in - e_out - loss)
    assert de == pytest.approx(expected_de, rel=1e-9, abs=1e-6)


def test_draw_cools_from_bottom_preserving_stratification():
    """Drawing hot from the top with cold makeup at the bottom cools the bottom
    first and sends a cold front upward, keeping T monotone non-increasing."""
    n = 12
    t = StratifiedTank(n, _VOL, _H, k_eff=0.606, ua=0.0)
    t.reset(60.0)  # hot tank
    drw, Tm = 1.2e-4, 12.0
    top_hist, bottom_hist = [], []
    for _ in range(80):
        out = t.step(_DT, charge_flow=0.0, draw_flow=drw, T_makeup=Tm, T_amb=20.0)
        assert np.all(np.diff(t.T) <= 1e-9)  # cold stays at the bottom
        top_hist.append(out["T_top"])
        bottom_hist.append(out["T_outlet"])
    assert bottom_hist[5] < 30.0  # bottom cooled quickly by makeup
    assert top_hist[5] > 55.0  # top still hot (front not arrived)
    assert top_hist[-1] < top_hist[5]  # cold front eventually reaches the top


def test_energy_conservation_with_heat_source():
    """Immersed heat source: ΔE = dt·[q_source − Σ UA_i(T_i − T_amb)]."""
    n = 6
    ua = 8.0
    t = StratifiedTank(n, _VOL, _H, ua=ua)
    t.reset(45.0)
    e0 = t.stored_energy
    q_hp, Ta = 3000.0, 18.0
    t.step(_DT, q_source=q_hp, T_amb=Ta)
    de = t.stored_energy - e0
    loss = (ua / n) * np.sum(t.T - Ta)
    assert de == pytest.approx(_DT * (q_hp - loss), rel=1e-9, abs=1e-6)


def test_heat_source_array_and_validation():
    n = 4
    t = StratifiedTank(n, _VOL, _H, ua=0.0)
    t.reset(40.0)
    e0 = t.stored_energy
    qarr = np.array([1000.0, 500.0, 0.0, 0.0])
    t.step(_DT, q_source=qarr, T_amb=20.0)
    assert (t.stored_energy - e0) == pytest.approx(_DT * qarr.sum(), rel=1e-9)
    with pytest.raises(ValueError):
        t.step(_DT, q_source=np.array([1.0, 2.0]))  # wrong length


# ---------------------------------------------------------------------------
# Buoyancy (inversion) mixing, overheat flag, sub-stepping (#96)
# ---------------------------------------------------------------------------
_RC = rho_w * c_w


def _step_balance(t, dt, **kw):
    e0 = t.stored_energy
    out = t.step(dt, **kw)
    cf, df = kw.get("charge_flow", 0.0), kw.get("draw_flow", 0.0)
    flux = (
        _RC * cf * (kw.get("T_charge", 0.0) - out["T_charge_out"])
        + _RC * df * (kw.get("T_makeup", 10.0) - out["T_draw_out"])
        + float(np.sum(kw.get("q_source", 0.0)))
        - out["Q_loss"]
    )
    return t.stored_energy - e0, dt * flux, out


def test_mix_inversions_conserves_energy_and_orders_profile():
    rng = np.random.default_rng(7)
    for _ in range(50):
        T = rng.uniform(10.0, 90.0, 12)
        cap = rng.uniform(0.5, 2.0, 12)
        out, mixed = mix_inversions(T, cap)
        assert mixed
        assert np.dot(cap, out) == pytest.approx(np.dot(cap, T), rel=1e-12)
        assert np.all(np.diff(out) <= INVERSION_TOL_K)
        again, mixed_again = mix_inversions(out, cap)
        assert not mixed_again and again is out  # idempotent


def test_mix_inversions_leaves_stable_or_uniform_profile_bit_identical():
    for T in (np.linspace(60.0, 20.0, 8), np.full(8, 45.0), np.full(8, 45.0) + 1e-13 * np.arange(8)):
        out, mixed = mix_inversions(T, np.ones(8))
        assert not mixed and out is T


@pytest.mark.parametrize("n", [4, 10, 30])
@pytest.mark.parametrize("dt", [60.0, 600.0])
def test_bottom_heater_mixes_instead_of_inverting(n, dt):
    """#96: 3 kW at the bottom node for 1 h used to leave 166 °C below 40 °C.

    Buoyancy mixes the warm bottom upward: energy is conserved every step, the
    profile stays monotone, and (no loss) the tank ends at the energy-mean
    temperature regardless of dt and node count."""
    t = StratifiedTank(n, _VOL, _H, ua=0.0)
    t.reset(40.0)
    q = np.zeros(n)
    q[-1] = 3000.0
    for _ in range(int(3600 / dt)):
        de, flux, out = _step_balance(t, dt, q_source=q)
        assert de == pytest.approx(flux, rel=1e-9, abs=1e-6)
        assert np.all(np.diff(t.T) <= INVERSION_TOL_K)
        assert out["mixed"]
    np.testing.assert_allclose(t.T, 40.0 + 3000.0 * 3600.0 / (_RC * _VOL), atol=1e-6)


def test_inversion_mixing_can_be_disabled():
    t = StratifiedTank(10, _VOL, _H, inversion_mixing=False)
    t.reset(40.0)
    q = np.zeros(10)
    q[-1] = 3000.0
    t.step(600.0, q_source=q)
    assert t.T[-1] > t.T[0] + 5.0


def test_mixing_with_draw_keeps_balance_on_reported_outflow():
    """Mixing after the solve: the outflow that closes the balance is the
    reported ``T_draw_out``/``T_charge_out``, not the post-mixing node value."""
    t = StratifiedTank(8, _VOL, _H, ua=5.0)
    t.reset(np.linspace(50.0, 30.0, 8))
    q = np.zeros(8)
    q[-1] = 8000.0
    de, flux, out = _step_balance(
        t, 600.0, q_source=q, draw_flow=5e-5, T_makeup=12.0, charge_flow=3e-5, T_charge=55.0, T_amb=15.0
    )
    assert out["mixed"]
    assert de == pytest.approx(flux, rel=1e-9, abs=1e-6)


def test_overheat_flag_and_warning():
    t = StratifiedTank(4, _VOL, _H, ua=0.0)
    t.reset(90.0)
    with pytest.warns(TankOverheatWarning, match="T_boil"):
        out = t.step(600.0, q_source=40_000.0)
    assert out["overheat"] and out["T_max"] > 100.0
    t.reset(90.0)
    t.T_boil = 300.0  # pressurised tank: no flag
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert not t.step(600.0, q_source=40_000.0)["overheat"]


def test_heat_source_must_be_finite():
    t = StratifiedTank(4, _VOL, _H)
    t.reset(40.0)
    with pytest.raises(ValueError, match="finite"):
        t.step(_DT, q_source=np.array([1.0, np.nan, 0.0, 0.0]))


def test_default_is_one_implicit_step_with_reported_outflows_equal_to_nodes():
    t = StratifiedTank(8, _VOL, _H, ua=3.0)
    t.reset(np.linspace(58.0, 38.0, 8))
    out = t.step(600.0, charge_flow=1e-4, T_charge=60.0, draw_flow=5e-5, T_makeup=12.0)
    assert out["substeps"] == 1
    assert out["T_draw_out"] == out["T_top"] and out["T_charge_out"] == out["T_outlet"]


def test_max_courant_substeps_conserve_energy_and_reduce_diffusion():
    n, q, dt = 20, 1.2e-4, 600.0
    widths = {}
    for mc in (None, 1.0):
        t = StratifiedTank(n, _VOL, _H, k_eff=0.606, ua=2.0, max_courant=mc)
        t.reset(15.0)
        for _ in range(2):
            de, flux, out = _step_balance(t, dt, charge_flow=q, T_charge=60.0, T_amb=20.0)
            assert de == pytest.approx(flux, rel=1e-9, abs=1e-6)
        assert out["substeps"] == (1 if mc is None else 8)  # Co = 7.2
        widths[mc] = np.sum((t.T > 26.25) & (t.T < 48.75))
    assert widths[1.0] < widths[None]


def test_substep_count_is_robust_at_integer_courant():
    t = StratifiedTank(10, _VOL, _H, max_courant=1.0)
    q = 3.0 * t.v_node / 600.0  # Co = 3 exactly (up to round-off)
    assert t.substeps(600.0, charge_flow=q * (1 + 1e-14)) == 3
    assert t.substeps(600.0, charge_flow=q * (1 - 1e-14)) == 3
    with pytest.raises(ValueError):
        StratifiedTank(4, _VOL, _H, max_courant=0.0)
