"""Tests for the hybrid 1-D thermocline tank (Cruz-Loredo 2023).

The headline property is *reduced numerical diffusion*: at equal node count the
hybrid model keeps the charging thermocline sharper than the standard multi-node
model. (The hybrid trades strict per-step energy conservation for that sharpness,
so energy conservation is only checked on the standard-fallback paths.)
"""

from __future__ import annotations

import numpy as np
import pytest

from tmhp.constants import c_w, rho_w
from tmhp.hybrid_tank import HybridStratifiedTank
from tmhp.stratified_tank import StratifiedTank

_VOL = 0.2
_H = 1.2
_DT = 60.0


def _transition_width(T, t_cold, t_hot):
    """Number of nodes inside the 25–75% temperature transition band."""
    span = t_hot - t_cold
    lo, hi = t_cold + 0.25 * span, t_cold + 0.75 * span
    return int(np.sum((lo < T) & (hi > T)))


def test_construction_and_reset_validation():
    with pytest.raises(ValueError):
        HybridStratifiedTank(0, _VOL, _H)
    with pytest.raises(ValueError):
        HybridStratifiedTank(4, _VOL, 0.0)
    t = HybridStratifiedTank(5, _VOL, _H)
    t.reset(40.0)
    assert np.allclose(t.T, 40.0)
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
        t.step(_DT, charge_flow=q, T_charge=60.0)
        expected = _H - v_th * (k * _DT)
        assert t.y_th == pytest.approx(expected, rel=1e-9)


def test_long_charge_fills_hot():
    n = 10
    t = HybridStratifiedTank(n, _VOL, _H, ua=0.0)
    t.reset(15.0)
    for _ in range(400):
        t.step(_DT, charge_flow=1.0e-4, T_charge=55.0)
    assert np.allclose(t.T, 55.0, atol=1e-2)


def test_draw_fallback_matches_standard_and_conserves_energy():
    """Draw/idle reverts to the standard model: identical to StratifiedTank and
    energy-conserving."""
    n = 8
    ua = 10.0
    hyb = HybridStratifiedTank(n, _VOL, _H, ua=ua)
    std = StratifiedTank(n, _VOL, _H, ua=ua)
    prof = np.linspace(58.0, 38.0, n)
    hyb.reset(prof)
    std.reset(prof)

    e0 = hyb.stored_energy
    drw, Tm, Ta = 8.0e-5, 12.0, 16.0
    out_h = hyb.step(_DT, draw_flow=drw, T_makeup=Tm, T_amb=Ta)
    std.step(_DT, draw_flow=drw, T_makeup=Tm, T_amb=Ta)
    assert np.allclose(hyb.T, std.T, rtol=0, atol=0)  # identical to standard

    rc = rho_w * c_w
    e_in = rc * drw * Tm
    e_out = rc * drw * out_h["T_top"]
    loss = (ua / n) * np.sum(hyb.T - Ta)
    assert (hyb.stored_energy - e0) == pytest.approx(_DT * (e_in - e_out - loss), rel=1e-9, abs=1e-6)
