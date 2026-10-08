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


def _gshpb() -> GroundSourceHeatPumpBoiler:
    return GroundSourceHeatPumpBoiler(
        ref="R32",
        N_1=2,
        N_2=1,
        H_b=100.0,
        dt_s=3600.0,
        t_max_s=200 * 3600,
    )


def _legacy_dT_sequence(
    g_interp: Callable[[np.ndarray], np.ndarray],
    time_arr: np.ndarray,
    q_seq: np.ndarray,
    tol: float = 1e-6,
) -> np.ndarray:
    """Verbatim replica of the legacy ``_compute_bhe_superposition`` dT loop.

    Serves as the independent oracle: the refactored coupler must match this
    sequence exactly for any load history.
    """
    pulses = np.zeros(len(q_seq))
    q_old = 0.0
    out = []
    for n, q in enumerate(q_seq):
        if abs(q - q_old) > tol:
            pulses[n] = q - q_old
            q_old = q
        idx = np.flatnonzero(pulses[: n + 1])
        if len(idx) > 0:
            dQ = pulses[idx]
            tau = np.maximum(time_arr[n] - time_arr[idx], 1e-6)
            out.append(float(np.dot(dQ, g_interp(tau))))
        else:
            out.append(0.0)
    return np.array(out)


@pytest.fixture(scope="module")
def gshpb() -> GroundSourceHeatPumpBoiler:
    return _gshpb()


def test_default_coupler_satisfies_protocol(gshpb):
    assert isinstance(gshpb._ground_coupler, AggregateGFunctionCoupler)
    assert isinstance(gshpb._ground_coupler, GroundCoupler)


def test_aggregate_coupler_byte_identical_to_legacy(gshpb):
    """The default coupler reproduces the legacy pulse-superposition exactly."""
    g = gshpb._gfunc_interp
    time_arr = np.arange(0, 100) * 3600.0
    rng = np.random.default_rng(0)
    # A load history with many on/off transitions exercises the pulse logic.
    q_seq = np.where(rng.random(100) < 0.5, 0.0, 40.0)

    ref = _legacy_dT_sequence(g, time_arr, q_seq)

    coupler = AggregateGFunctionCoupler(g)
    coupler.reset(len(q_seq), time_arr)
    got = np.array([coupler.wall_temperature_rise(n, time_arr, float(q)) for n, q in enumerate(q_seq)])
    assert np.array_equal(got, ref)


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


# Single-borehole golden captured at ea02dc9 before the field normalization fix.
# Multi-borehole behavior is checked against analytic balances in test_ground_loop.
_GOLDEN = {
    "T_bhe [°C]": [
        16.0,
        16.0,
        16.0,
        16.0,
        15.999999999709138,
        14.952893967837952,
        14.359900026538678,
        14.899576456553033,
        15.240836024030267,
        15.419276268609517,
        15.52938085956007,
        14.772490299702099,
        15.088046467474117,
        15.327383954941867,
        15.456058944079423,
        15.539240307505013,
    ],
    "T_bhe_f [°C]": [
        16.0,
        16.0,
        16.0,
        16.0,
        10.473382101904576,
        10.084959636017913,
        14.359900026538696,
        14.899576456553007,
        15.240836024030273,
        15.419276268609508,
        11.138939105018892,
        14.772490299702099,
        15.0880464674741,
        15.327383954941865,
        15.456058944079416,
        15.539240307504997,
    ],
    "T_bhe_f_in [°C]": [
        16.0,
        16.0,
        16.0,
        16.0,
        8.465654147940256,
        8.31652041753307,
        14.359900026538696,
        14.899576456553007,
        15.240836024030273,
        15.419276268609508,
        9.543964962814073,
        14.772490299702099,
        15.0880464674741,
        15.327383954941865,
        15.456058944079416,
        15.539240307504997,
    ],
    "T_bhe_f_out [°C]": [
        16.0,
        16.0,
        16.0,
        16.0,
        12.481110055868896,
        11.853398854502757,
        14.359900026538696,
        14.899576456553007,
        15.240836024030273,
        15.419276268609508,
        12.733913247223711,
        14.772490299702099,
        15.0880464674741,
        15.327383954941865,
        15.456058944079416,
        15.539240307504997,
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


def test_analyze_dynamic_bhe_closes_source_cycle_and_ground_feedback(monkeypatch):
    """Real accepted plant states conserve energy and use the evolving loop."""
    tN = 16
    dhw = np.zeros(tN)
    dhw[[3, 4, 9, 10]] = 6.0e-5
    T0 = np.full(tN, 15.0)

    gshpb = _historical_efficiency_plant()
    accepted = []
    determine = gshpb._determine_hp_state

    def record_state(ctx, is_on_prev):
        result = determine(ctx, is_on_prev)
        if result[0]:
            accepted.append((ctx.n, gshpb.T_bhe_f_out_K - 273.15, result[1].copy()))
        return result

    # Observe the actual solver return without replacing any physical result.
    monkeypatch.setattr(gshpb, "_determine_hp_state", record_state)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        df = gshpb.analyze_dynamic(
            simulation_period_sec=tN * 3600.0,
            dt_s=3600.0,
            T_tank_w_init_C=56.0,
            dhw_usage_schedule=dhw,
            T0_schedule=T0,
        )

    assert [n for n, _, _ in accepted] == [4, 5, 10]
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
        assert df["Q_bhe [W]"].iloc[n] == state["Q_bhe [W]"]

    # Independent Duhamel superposition as a dense causal response matrix:
    # the real cycle's total field load is converted to W/m exactly once.
    time_arr = np.arange(tN) * 3600.0
    loads_per_m = df["Q_bhe [W]"].to_numpy() / gshpb.total_borehole_length
    increments = np.diff(np.r_[0.0, loads_per_m])
    lags = np.maximum(time_arr[:, None] - time_arr[None, :], 1e-6)
    response = np.tril(gshpb._gfunc_interp(lags)) @ increments
    wall_C = gshpb.Ts - response
    mean_C = wall_C - loads_per_m * gshpb.R_b
    capacity_rate = df["dV_bhe_f [m3/s]"].to_numpy() * rho_w * c_w
    half_rise = np.divide(df["Q_bhe [W]"].to_numpy(), 2 * capacity_rate, out=np.zeros(tN), where=capacity_rate > 0)
    for column, expected in (
        ("T_bhe [°C]", wall_C),
        ("T_bhe_f [°C]", mean_C),
        ("T_bhe_f_in [°C]", mean_C - half_rise),
        ("T_bhe_f_out [°C]", mean_C + half_rise),
    ):
        np.testing.assert_allclose(df[column], expected, rtol=0.0, atol=1e-12)
