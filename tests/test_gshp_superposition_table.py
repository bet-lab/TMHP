"""GroundSourceHeatPump.analyze_dynamic tabulates the step-mean response once per run.

The table must not change a number: every run is compared with the same
model evaluating the g-function pulse by pulse (the path used when
``_bhe_history_rise`` is called outside a run).
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

pytest.importorskip("pygfunction")

from tmhp import GroundSourceHeatPump  # noqa: E402


class _PulseByPulse(GroundSourceHeatPump):
    """Same model with the per-run table switched off."""

    def _bhe_history_rise(self, n, time_arr, pulses, upto):
        saved, self._bhe_step_mean = self._bhe_step_mean, None
        try:
            return super()._bhe_history_rise(n, time_arr, pulses, upto)
        finally:
            self._bhe_step_mean = saved


_COLS = ["T_bhe [°C]", "T_bhe_f [°C]", "T_bhe_f_in [°C]", "T_bhe_f_out [°C]", "Q_bhe [W]", "E_cmp [W]"]

# The coupled-flow machine of tests/test_ground_flow_control.py: the flow search asks the wall
# response for every candidate, which is where the history used to be re-evaluated.
_COUPLED = dict(
    ref="R410A", V_cmp_ref=1.2e-5, N_1=2, N_2=2, H_b=100, dV_b_f_lpm=80, hydraulic_pump=True, variable_Rb=True,
    variable_ground_hx_UA=True, m_dot_ref_rated=0.04, hp_capacity=8000, rps_rated=60, UA_cond=2000, UA_evap=2000,
    eta_v=1, eta_em=1, PR_cycle_max=8, ground_flow_control="optimal_power",
)  # fmt: skip


def _run(cls, dt: float, load: np.ndarray, **kw) -> np.ndarray:
    n = len(load)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = cls(**{"t_max_s": 30 * 86400.0, **kw})
        frame = model.analyze_dynamic(n * dt, dt, load, np.full(n, 20.0))
    assert model._bhe_step_mean is None  # released at the end of the run
    return frame[_COLS].to_numpy(dtype=float)


@pytest.mark.parametrize("dt", [3600.0, 600.0])
def test_tabulated_superposition_gives_the_pulse_by_pulse_numbers(dt):
    load = np.where((np.arange(18) // 3) % 2 == 0, 4000.0, 0.0)
    load[9:] *= -1.0  # cooling, then heating
    # A water loop: the 1e-9 K agreement was established for it (1.1e-9 K with the default glycol).
    fast = _run(GroundSourceHeatPump, dt, load, source_fluid="water")
    slow = _run(_PulseByPulse, dt, load, source_fluid="water")
    assert np.any(fast[:, 4] > 0) and np.any(fast[:, 4] < 0)  # the ground was loaded both ways
    np.testing.assert_allclose(fast, slow, rtol=0.0, atol=1e-9)


def test_coupled_flow_search_reads_the_wall_of_the_recorded_history():
    """Every wall temperature the flow search asks for equals the pulse sum of the loads the run recorded."""
    dt, load = 600.0, np.array([-4000.0, -4000.0])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = GroundSourceHeatPump(**{"t_max_s": 30 * 86400.0, **_COUPLED})
    solve, asked, step = model._solve_ground_flow, [], [-1]

    def spy(*args, wall_response, **kw):
        step[0] += 1

        def recorded(q_total):
            wall = wall_response(q_total)
            asked.append((step[0], float(q_total), float(wall)))
            return wall

        return solve(*args, wall_response=recorded, **kw)

    model._solve_ground_flow = spy
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        frame = model.analyze_dynamic(len(load) * dt, dt, load, np.full(len(load), 20.0))
    q_unit = frame["Q_bhe [W]"].to_numpy(dtype=float) / model.total_borehole_length
    assert q_unit[0] > 0 and any(n == 1 for n, _, _ in asked)  # step 1 was searched with step 0 in the ground
    g = model._gfunc_interp
    for n, q_total, wall in asked:
        previous = q_unit[n - 1] if n else 0.0
        pulses = np.diff(np.r_[0.0, q_unit[:n]])
        rise = sum(p * float(g.step_mean((n - i) * dt, dt)) for i, p in enumerate(pulses))
        rise += (q_total / model.total_borehole_length - previous) * float(g.step_mean(0.0, dt))
        assert wall == pytest.approx(model.Ts_K - rise, abs=1e-10)


def test_history_outside_a_run_still_evaluates_the_g_function():
    model = GroundSourceHeatPump(t_max_s=30 * 86400.0)
    model.dt = 3600.0
    time = np.arange(6) * 3600.0
    pulses = np.array([10.0, 0.0, -4.0, 0.0, 0.0, 2.0])
    g = model._gfunc_interp
    expected = sum(p * float(g.step_mean(time[5] - time[i], 3600.0)) for i, p in enumerate(pulses) if p)
    assert model._bhe_step_mean is None
    assert model._bhe_history_rise(5, time, pulses, 6) == pytest.approx(expected, abs=1e-12)
    assert model._bhe_history_rise(5, time, pulses, 0) == 0.0
