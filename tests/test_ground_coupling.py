"""Characterization + contract tests for the BHE ground-coupling abstraction.

The default :class:`AggregateGFunctionCoupler` must reproduce the legacy inline
temporal superposition *byte-for-byte*. Archived imposed ground loads retain
the captured BHE response; the real GSHPB plant is checked separately against
heat-exchanger conservation and ground-temperature feedback.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable

import numpy as np
import pandas as pd
import pytest

# The default g-function precompute requires pygfunction; skip gracefully where
# it is unavailable (e.g. a minimal CI image) rather than failing.
pytest.importorskip("pygfunction")

from tmhp import GroundSourceHeatPumpBoiler  # noqa: E402
from tmhp.constants import c_w, rho_w  # noqa: E402
from tmhp.ground_coupling import AggregateGFunctionCoupler, GroundCoupler  # noqa: E402

from ._ground_oracle import step_mean_superposition  # noqa: E402


def _gshpb() -> GroundSourceHeatPumpBoiler:
    return GroundSourceHeatPumpBoiler(
        ref="R32",
        N_1=2,
        N_2=1,
        H_b=100.0,
        dt_s=3600.0,
        t_max_s=200 * 3600,
    )


def _reference_dT_sequence(
    g_interp: Callable[[np.ndarray], np.ndarray],
    time_arr: np.ndarray,
    q_seq: np.ndarray,
    tol: float = 1e-6,
) -> np.ndarray:
    """Pulse logic of the coupler with the step-mean response from quadrature.

    Independent oracle: pulses are registered when the load changes by more
    than ``tol`` and each step reports the mean rise over ``[t_n, t_n + dt]``
    (#96 G4), integrated by ``scipy.integrate.quad``.
    """
    pulses = np.zeros(len(q_seq))
    q_old = 0.0
    for n, q in enumerate(q_seq):
        if abs(q - q_old) > tol:
            pulses[n] = q - q_old
            q_old = q
    return step_mean_superposition(g_interp, time_arr, pulses, float(time_arr[1] - time_arr[0]))


@pytest.fixture(scope="module")
def gshpb() -> GroundSourceHeatPumpBoiler:
    return _gshpb()


def test_default_coupler_satisfies_protocol(gshpb):
    assert isinstance(gshpb._ground_coupler, AggregateGFunctionCoupler)
    assert isinstance(gshpb._ground_coupler, GroundCoupler)


@pytest.mark.parametrize("dt", [600.0, 3600.0])
def test_aggregate_coupler_matches_step_mean_superposition(gshpb, dt):
    """The default coupler is the step-mean pulse superposition (fast path and fallback)."""
    g = gshpb._gfunc_interp
    time_arr = np.arange(0, 100) * dt
    rng = np.random.default_rng(0)
    # A load history with many on/off transitions exercises the pulse logic.
    q_seq = np.where(rng.random(100) < 0.5, 0.0, 40.0)

    ref = _reference_dT_sequence(g, time_arr, q_seq)

    coupler = AggregateGFunctionCoupler(g)
    coupler.reset(len(q_seq), time_arr)
    got = np.array([coupler.wall_temperature_rise(n, time_arr, float(q)) for n, q in enumerate(q_seq)])
    np.testing.assert_allclose(got, ref, rtol=0.0, atol=1e-12)

    # A time array that is not the one passed to reset takes the direct path.
    coupler.reset(len(q_seq), time_arr)
    other = time_arr.copy()
    other[-1] += 1.0
    direct = np.array([coupler.wall_temperature_rise(n, other, float(q)) for n, q in enumerate(q_seq[:-1])])
    np.testing.assert_allclose(direct, ref[:-1], rtol=0.0, atol=1e-12)


def test_current_step_pulse_contributes_its_step_mean(gshpb):
    """#96 G4: a step's own load warms/cools its wall by q · ḡ(0; dt), not by g(0) ≈ 0."""
    g = gshpb._gfunc_interp
    time_arr = np.arange(4) * 3600.0
    coupler = AggregateGFunctionCoupler(g)
    coupler.reset(len(time_arr), time_arr)
    rise = coupler.wall_temperature_rise(0, time_arr, 25.0)
    # g is linear from (0, 0) to (3600 s, g(3600 s)) on this grid: the hourly mean is half.
    assert rise == pytest.approx(25.0 * 0.5 * float(g(3600.0)), rel=1e-12)
    assert rise > 0.3  # ~0.39 K at 25 W/m for this field; the old start-of-step value was ~1e-10 K


def test_injected_coupler_overrides_default():
    """A user-supplied coupler must replace the default backend."""
    seen: dict[str, int] = {}

    class _Spy:
        def reset(self, n_steps: int, time_arr: np.ndarray) -> None:
            seen["reset"] = n_steps

        def wall_temperature_rise(
            self,
            n: int,
            time_arr: np.ndarray,
            q_unit: float,
        ) -> float:
            return 0.0

    spy = _Spy()
    gshpb = GroundSourceHeatPumpBoiler(ref="R32", ground_coupler=spy)
    assert gshpb._ground_coupler is spy


# Single-borehole golden. First captured at ea02dc9 (step-start response,
# g(0) for the step's own pulse); re-captured for #96 G4, where every step
# reports the mean over [t_n, t_n + dt] including its own pulse. The values
# are also checked against an independent quadrature oracle below.
# Multi-borehole behavior is checked against analytic balances in test_ground_loop.
_GOLDEN = {
    "T_bhe [°C]": [
        16.0,
        16.0,
        16.0,
        16.0,
        15.476446983901642,
        14.625799474175674,
        14.620086020642868,
        15.091478334754209,
        15.338135500311445,
        15.478184859427497,
        15.152988443883014,
        14.90719289185651,
        15.222383462939103,
        15.39727515321736,
        15.500219394996735,
        15.569794659918099,
    ],
    "T_bhe_f [°C]": [
        16.0,
        16.0,
        16.0,
        16.0,
        9.949829086097111,
        9.757865142355627,
        14.620086020642873,
        15.091478334754186,
        15.338135500311466,
        15.478184859427472,
        10.762546689341832,
        14.907192891856539,
        15.222383462939092,
        15.397275153217379,
        15.500219394996748,
        15.569794659918102,
    ],
    "T_bhe_f_in [°C]": [
        16.0,
        16.0,
        16.0,
        16.0,
        7.942101132132791,
        7.989425923870783,
        14.620086020642873,
        15.091478334754186,
        15.338135500311466,
        15.478184859427472,
        9.167572547137013,
        14.907192891856539,
        15.222383462939092,
        15.397275153217379,
        15.500219394996748,
        15.569794659918102,
    ],
    "T_bhe_f_out [°C]": [
        16.0,
        16.0,
        16.0,
        16.0,
        11.957557040061431,
        11.52630436084047,
        14.620086020642873,
        15.091478334754186,
        15.338135500311466,
        15.478184859427472,
        12.357520831546651,
        14.907192891856539,
        15.222383462939092,
        15.397275153217379,
        15.500219394996748,
        15.569794659918102,
    ],
}


# Total field heat extraction [W] recorded from the unchanged 20ddce0260872ee12a5b6190d04c67ef33885d31
# plant, with the original efficiency inputs below, dt=3600 s, and 16 steps.
# The old plant returned a literal zero source-HX residual at a 1 K approach;
# these archived inputs characterize ground response, not closed HP states.
# Canonical provenance JSON SHA256: 1b4af99c4814948e9aa30100a03b1011a8cfb17db96fccf15e922ca24cd7798c
_ARCHIVED_BHE_LOAD_W = {4: 6723.479372235796, 5: 5922.149254862031, 10: 5341.249407415474}


def _historical_efficiency_plant():
    return GroundSourceHeatPumpBoiler(
        t_max_s=200 * 3600, eta_cmp_isen=0.80, eta_cmp_vol=lambda pr: 0.95 - 0.05 * pr, eta_cmp=0.855
    )


def test_archived_bhe_load_response_matches_golden():
    """Replay the original imposed loads through the production BHE hook."""
    gshpb = _historical_efficiency_plant()
    time_arr = np.arange(16) * 3600.0
    gshpb._ground_coupler.reset(len(time_arr), time_arr)
    rows = []
    for n in range(len(time_arr)):
        load = _ARCHIVED_BHE_LOAD_W.get(n, 0.0)
        gshpb._compute_bhe_superposition(n, time_arr, {"Q_bhe [W]": load}, load > 0)
        rows.append(
            {
                "T_bhe [°C]": gshpb.T_bhe,
                "T_bhe_f [°C]": gshpb.T_bhe_f,
                "T_bhe_f_in [°C]": gshpb.T_bhe_f_in,
                "T_bhe_f_out [°C]": gshpb.T_bhe_f_out,
            }
        )
    df = pd.DataFrame(rows)

    for col, golden in _GOLDEN.items():
        assert col in df.columns
        np.testing.assert_allclose(df[col].to_numpy(), np.array(golden), rtol=0.0, atol=1e-12)

    loads_per_m = np.array([_ARCHIVED_BHE_LOAD_W.get(n, 0.0) for n in range(len(time_arr))])
    loads_per_m /= gshpb.total_borehole_length
    oracle = gshpb.Ts - step_mean_superposition(gshpb._gfunc_interp, time_arr, np.diff(np.r_[0.0, loads_per_m]), 3600.0)
    np.testing.assert_allclose(df["T_bhe [°C]"].to_numpy(), oracle, rtol=0.0, atol=1e-12)


def test_analyze_dynamic_bhe_closes_source_cycle_and_ground_feedback(monkeypatch):
    """Real accepted plant states conserve energy and use the evolving loop."""
    tN = 16
    dhw = np.zeros(tN)
    dhw[[3, 4, 9, 10]] = 6.0e-5
    T0 = np.full(tN, 15.0)

    gshpb = _historical_efficiency_plant()
    accepted = []
    solve = gshpb._solve_cycle_at

    def record_state(ctx, T_tank_w, runtime_fraction=1.0):
        result = solve(ctx, T_tank_w, runtime_fraction)
        if result.get("hp_is_on", False):
            accepted.append((ctx.n, gshpb.T_bhe_f_out_K - 273.15, result.copy()))
        return result

    # Observe the actual solver return without replacing any physical result.
    monkeypatch.setattr(gshpb, "_solve_cycle_at", record_state)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        df = gshpb.analyze_dynamic(
            simulation_period_sec=tN * 3600.0,
            dt_s=3600.0,
            T_tank_w_init_C=56.0,
            dhw_usage_schedule=dhw,
            T0_schedule=T0,
        )

    # The thermostat acts inside each hour: the compressor starts when the
    # draw cools the tank to its lower bound (steps 3 and 9, not one hour
    # later) and stops at the upper bound. Step 4 starts above the cycle's
    # envelope (no operating point near 56-65 °C on this plant), runs nothing
    # and retries in step 5.
    assert [n for n, _, _ in accepted] == [3, 5, 9, 10]
    assert df["T_tank_w [°C]"].max() <= gshpb.T_tank_w_upper_bound
    for n, source_outlet_C, state in accepted:
        assert state["converged"] and state["converged_rps"]
        C = state["dV_bhe_f [m3/s]"] * rho_w * c_w
        source_inlet_C = source_outlet_C + state["E_pmp [W]"] / C
        # The HX sees the previous step's BHE outlet, before this step commits
        # its new ground response. Pump input warms that inlet stream.
        assert source_outlet_C == pytest.approx(df["T_bhe_f_out [°C]"].iloc[n - 1], abs=1e-12, rel=0)
        Q_hx = -np.expm1(-gshpb.UA_ground / C) * C * (source_inlet_C - state["T_ref_evap_sat [°C]"])
        Q_source = state["m_dot_ref [kg/s]"] * (state["h_ref_cmp_in [J/kg]"] - state["h_ref_exp_out [J/kg]"])
        assert abs(Q_source - Q_hx) <= max(0.01, 1e-5 * Q_source)
        assert state["Q_ref_ground [W]"] == pytest.approx(Q_source, rel=1e-9, abs=1e-9)
        assert state["Q_ref_tank [W]"] == pytest.approx(
            Q_source + state["E_cmp [W]"] * state["eta_cmp [-]"], rel=1e-9, abs=1e-9
        )
        assert state["Q_bhe [W]"] == pytest.approx(Q_source - state["E_pmp [W]"], rel=1e-9, abs=1e-9)
        # The row reports the step average: the running cycle times its run time.
        fraction = df["runtime_fraction [-]"].iloc[n]
        assert 0.0 < fraction <= 1.0
        assert df["Q_bhe [W]"].iloc[n] == pytest.approx(state["Q_bhe [W]"] * fraction, rel=1e-12, abs=0)

    # Independent Duhamel superposition as a dense causal response matrix:
    # the real cycle's total field load is converted to W/m exactly once.
    time_arr = np.arange(tN) * 3600.0
    loads_per_m = df["Q_bhe [W]"].to_numpy() / gshpb.total_borehole_length
    increments = np.diff(np.r_[0.0, loads_per_m])
    response = step_mean_superposition(gshpb._gfunc_interp, time_arr, increments, 3600.0)
    wall_C = gshpb.Ts - response
    # The wall answers the step-average load (#108); the borehole resistance and
    # the loop temperature difference act on the running load (average / run time).
    fraction = df["runtime_fraction [-]"].to_numpy()
    running_per_m = np.divide(loads_per_m, fraction, out=np.zeros(tN), where=fraction > 0)
    mean_C = wall_C - running_per_m * gshpb.R_b
    capacity_rate = df["dV_bhe_f [m3/s]"].to_numpy() * rho_w * c_w
    half_rise = np.divide(
        running_per_m * gshpb.total_borehole_length, 2 * capacity_rate, out=np.zeros(tN), where=capacity_rate > 0
    )
    for column, expected in (
        ("T_bhe [°C]", wall_C),
        ("T_bhe_f [°C]", mean_C),
        ("T_bhe_f_in [°C]", mean_C - half_rise),
        ("T_bhe_f_out [°C]", mean_C + half_rise),
    ):
        np.testing.assert_allclose(df[column], expected, rtol=0.0, atol=1e-12)
