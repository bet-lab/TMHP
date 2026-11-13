"""Immersed constant-temperature exchanger of the multi-node tanks (``hx_ua`` / ``T_hx`` / ``q_hx``).

A condenser coil heats only water colder than the refrigerant. Both tank
backends must: pass heat one way, split it in proportion to
``UA_i·(T_hx − T_i)⁺``, deliver exactly the requested heat when the nodes can
take it below ``T_hx`` (and say so when they cannot), and close the energy
balance. ``preview`` must give the step's result without advancing the tank.
"""

from __future__ import annotations

import numpy as np
import pytest

from tmhp.constants import c_w, rho_w
from tmhp.hybrid_tank import HybridStratifiedTank
from tmhp.stratified_tank import HX_SCALE_MAX, StratifiedTank

TANKS = [StratifiedTank, HybridStratifiedTank]
V, H = 0.2, 1.2  # 200 L, 1.2 m


def _tank(cls, n=10, profile=None, **kw):
    t = cls(n, V, H, **kw)
    t.reset(np.linspace(60.0, 20.0, n) if profile is None else profile)
    return t


def _balance(t, E0, out, dt, *, draw=0.0, T_makeup=10.0, q_source=0.0):
    """Stored-energy change minus everything the step reports [J]."""
    net = dt * (rho_w * c_w * draw * (T_makeup - out["T_draw_out"]) + out["Q_hx"] + q_source - out["Q_loss"])
    return t.stored_energy - E0 - net


@pytest.mark.parametrize("cls", TANKS)
def test_exchanger_heats_only_nodes_colder_than_it(cls):
    t = _tank(cls, ua=2.0)
    T_before = t.T.copy()
    E0 = t.stored_energy
    out = t.step(600.0, hx_ua=400.0, T_hx=45.0, T_amb=20.0)
    q = out["q_hx"]
    assert np.all(q >= 0.0)
    assert np.all(q[T_before >= 45.0] == 0.0), "a node as hot as the exchanger received heat"
    assert np.all(q[T_before < 44.0] > 0.0), "a node colder than the exchanger received none"
    assert np.all(out["T"][q > 0.0] <= 45.0 + 1e-9), "a heated node ended above the exchanger temperature"
    assert out["Q_hx"] == pytest.approx(float(q.sum()))
    assert out["hx_scale"] == 1.0 and out["hx_limited"] is False
    assert abs(_balance(t, E0, out, 600.0)) < 1e-6 * out["Q_hx"] * 600.0


@pytest.mark.parametrize("cls", TANKS)
def test_exchanger_split_is_proportional_to_ua_times_temperature_difference(cls):
    """q_i = k·UA_i·(T_hx − T_i) at the end-of-step temperatures, for per-node UA."""
    ua = np.array([0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0])
    t = _tank(cls)
    out = t.step(300.0, hx_ua=ua, T_hx=70.0, q_hx=5000.0)
    assert out["Q_hx"] == pytest.approx(5000.0, rel=1e-9)
    expected = out["hx_scale"] * ua * (70.0 - out["T"])
    np.testing.assert_allclose(out["q_hx"], expected, rtol=1e-9, atol=1e-9)
    assert out["q_hx"][0] == 0.0  # no coil in the top node


@pytest.mark.parametrize("cls", TANKS)
@pytest.mark.parametrize("dt", [60.0, 600.0, 3600.0])
def test_requested_heat_is_delivered_and_balance_closes_with_draw(cls, dt):
    t = _tank(cls, ua=2.0)
    E0 = t.stored_energy
    draw = 5.0e-5
    out = t.step(dt, draw_flow=draw, T_makeup=12.0, hx_ua=500.0, T_hx=75.0, q_hx=6000.0, T_amb=20.0)
    assert out["hx_limited"] is False
    assert out["Q_hx"] == pytest.approx(6000.0, rel=1e-9)
    assert abs(_balance(t, E0, out, dt, draw=draw, T_makeup=12.0)) < 1e-7 * 6000.0 * dt
    assert np.all(out["T"][out["q_hx"] > 0.0] <= 75.0 + 1e-9)


@pytest.mark.parametrize("cls", TANKS)
def test_heat_the_nodes_cannot_take_below_the_exchanger_is_reported(cls):
    """50 kW for 10 min would lift a 200 L tank 36 K; the coil at 40 °C can only bring it to 40 °C."""
    t = _tank(cls, profile=np.linspace(38.0, 30.0, 10))
    E0 = t.stored_energy
    out = t.step(600.0, hx_ua=500.0, T_hx=40.0, q_hx=50_000.0)
    assert out["hx_limited"] is True and out["hx_scale"] == HX_SCALE_MAX
    assert 0.0 < out["Q_hx"] < 50_000.0
    assert np.all(out["T"] <= 40.0 + 1e-9)
    assert np.max(out["T"]) == pytest.approx(40.0, abs=1e-3)  # every node brought up to the coil
    assert abs(_balance(t, E0, out, 600.0)) < 1e-6 * out["Q_hx"] * 600.0


@pytest.mark.parametrize("cls", TANKS)
def test_exchanger_colder_than_every_node_delivers_nothing(cls):
    t = _tank(cls)
    T_before = t.T.copy()
    out = t.step(600.0, hx_ua=500.0, T_hx=10.0, q_hx=3000.0)
    assert out["Q_hx"] == 0.0 and out["hx_limited"] is True
    assert np.all(out["q_hx"] == 0.0)
    assert np.all(out["T"] <= T_before.max() + 1e-9) and np.all(out["T"] >= T_before.min() - 1e-9)


@pytest.mark.parametrize("cls", TANKS)
def test_bottom_coil_heat_rises_and_does_not_depend_on_the_time_step(cls):
    """A coil in the bottom node heats the whole tank by buoyancy; 1 h at 3 kW in one step or sixty."""
    ua = np.zeros(8)
    ua[-1] = 600.0
    ends = []
    for dt in (60.0, 3600.0):
        t = _tank(cls, n=8, profile=np.full(8, 40.0))
        delivered = 0.0
        for _ in range(int(3600.0 / dt)):
            out = t.step(dt, hx_ua=ua, T_hx=70.0, q_hx=3000.0)
            assert out["hx_limited"] is False, f"dt={dt}: the bottom coil could not pass 3 kW"
            delivered += out["Q_hx"] * dt
        assert delivered == pytest.approx(3000.0 * 3600.0, rel=1e-9)
        assert np.all(np.diff(t.T) <= 1e-4), "profile inverted"
        ends.append(t.T.copy())
    # 3 kW·1 h into 200 L is +12.9 K: both step sizes end fully mixed at the same temperature.
    np.testing.assert_allclose(ends[0], ends[1], atol=0.05)
    assert ends[0].mean() == pytest.approx(40.0 + 3000.0 * 3600.0 / (rho_w * c_w * V), abs=1e-6)


@pytest.mark.parametrize("cls", TANKS)
def test_scalar_ua_is_spread_over_the_nodes(cls):
    a, b = _tank(cls), _tank(cls)
    out_a = a.step(300.0, hx_ua=500.0, T_hx=65.0, q_hx=4000.0)
    out_b = b.step(300.0, hx_ua=np.full(10, 50.0), T_hx=65.0, q_hx=4000.0)
    np.testing.assert_array_equal(out_a["T"], out_b["T"])


@pytest.mark.parametrize("cls", TANKS)
def test_preview_gives_the_step_result_without_advancing(cls):
    t = _tank(cls, ua=2.0)
    t.step(120.0, draw_flow=1.0e-4, T_makeup=12.0)  # leaves a barrier in the hybrid tank
    T_before = t.T.copy()
    args = dict(draw_flow=5.0e-5, T_makeup=12.0, hx_ua=500.0, T_hx=70.0, q_hx=5000.0, T_amb=20.0)
    seen = t.preview(600.0, **args)
    np.testing.assert_array_equal(t.T, T_before)
    done = t.step(600.0, **args)
    for key in ("T", "T_draw_out", "Q_loss", "Q_hx", "q_hx", "hx_scale"):
        np.testing.assert_array_equal(seen[key], done[key])
    np.testing.assert_array_equal(t.T, done["T"])


@pytest.mark.parametrize("cls", TANKS)
def test_preview_does_not_warn_about_overheat(cls):
    import warnings

    t = _tank(cls)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = t.preview(3600.0, q_source=np.full(10, 3000.0))
    assert out["overheat"] is True


@pytest.mark.parametrize("cls", TANKS)
@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"T_hx": 60.0}, "need hx_ua"),
        ({"q_hx": 1000.0}, "need hx_ua"),
        ({"hx_ua": 500.0}, "T_hx"),
        ({"hx_ua": 500.0, "T_hx": float("nan")}, "T_hx"),
        ({"hx_ua": -1.0, "T_hx": 60.0}, "hx_ua"),
        ({"hx_ua": np.ones(3), "T_hx": 60.0}, "hx_ua"),
        ({"hx_ua": 500.0, "T_hx": 60.0, "q_hx": -5.0}, "q_hx"),
    ],
)
def test_exchanger_arguments_are_validated(cls, kwargs, match):
    with pytest.raises(ValueError, match=match):
        _tank(cls).step(60.0, **kwargs)


@pytest.mark.parametrize("cls", TANKS)
def test_step_without_exchanger_reports_zero_exchanger_heat(cls):
    out = _tank(cls).step(60.0, draw_flow=1.0e-4)
    assert out["Q_hx"] == 0.0 and out["hx_limited"] is False
    assert np.all(out["q_hx"] == 0.0)


def test_hybrid_heat_input_and_flow_act_together():
    """A 6 kW heater spread over the tank under a draw of one tank volume per step.

    The heat goes in while the water passes: two 1000 s steps (a tank volume
    each) give the same mean outflow temperature and end temperature as two
    hundred 10 s steps. Applying a whole step's heat after its whole flow
    (the operator split without slicing) pushed the first tank volume out
    unheated: 18.6 °C mean outflow against 20.4 °C.
    """
    q = np.full(10, 600.0)
    draw, T_makeup = 2.0e-4, 15.0  # 200 L in 1000 s

    def run(dt):
        t = _tank(HybridStratifiedTank, profile=np.full(10, 15.0))
        vt = 0.0
        for _ in range(int(2000.0 / dt)):
            out = t.step(dt, draw_flow=draw, T_makeup=T_makeup, q_source=q)
            vt += out["T_draw_out"] * dt
        return vt / 2000.0, float(t.T.mean())

    T_out_coarse, T_end_coarse = run(1000.0)
    T_out_fine, T_end_fine = run(10.0)
    # Steady heating of the through-flow is 6 kW / (rho·c·V̇) = 7.2 K; the first
    # tank volume ramps up to it, so the 2000 s mean is about 5.4 K above the make-up.
    assert T_out_fine == pytest.approx(T_makeup + 5.4, abs=0.3)
    assert T_out_coarse == pytest.approx(T_out_fine, abs=0.5)
    assert T_end_coarse == pytest.approx(T_end_fine, abs=0.5)  # was 22.2 °C against 18.6 °C
