"""P0 regression suite for the public step() kernel (#165).

Verification strategy (TDD characterization gate):

* ``test_analyze_dynamic_matches_golden`` — GREEN before *and* after the
  refactor; guards that rewiring ``analyze_dynamic`` into a thin loop over
  ``step()`` does not perturb any numeric column (manuscript-result safety).
* ``test_step_public_api_matches_golden`` — RED before (no ``step()`` /
  ``make_initial_state``), GREEN after; proves the standalone per-step API
  (the FMU/EnergyPlus call pattern) reproduces the legacy trajectory.
* ``test_analyze_dynamic_idempotent`` — re-entrancy guard: two consecutive
  runs on one instance must agree. GREEN for the default (always-full)
  config both before and after the refactor; it pins the property so moving
  the cross-step ``dV_tank_w_out`` coupling into ``DynamicState`` cannot
  regress repeated-run independence.
* ``test_gshpb_step_not_implemented`` — RED before (no method), GREEN after;
  the history-dependent GSHPB must refuse a point-state ``step()`` loudly.

The dynamic golden and idempotence checks share only the first trajectory,
with one fresh model per scenario; the public-step run stays independent.

Golden tolerance: rtol=1e-6, atol=1e-7. The step kernel must reproduce the
recorded trajectory, but the outdoor-approach search solves the fan-limit
edge of the feasible band as a root (to 1e-8 K), and that root lands a few
1e-9 K apart on Linux and macOS: the goldens recorded on macOS / Python 3.14
differed from Linux by 5e-9 K in ``T_ref_cmp_in`` (relative 4.6e-8). The
tolerance sits two orders above that solver noise and far below any change
in the physics (a control or cycle change moves these columns by 1e-3 or more).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tmhp import AirSourceHeatPumpBoiler, GroundSourceHeatPumpBoiler

from . import _step_scenarios as S


def _load_golden(name: str, dt: int) -> pd.DataFrame:
    p = S.golden_path(name, dt)
    if not p.exists():  # pragma: no cover - fixture must be committed
        pytest.skip(f"golden fixture missing: {p}")
    return pd.read_csv(p)


def _assert_frames_close(df: pd.DataFrame, golden: pd.DataFrame) -> None:
    assert list(df.columns) == list(golden.columns), "column set/order mismatch"
    assert len(df) == len(golden), f"row count {len(df)} != {len(golden)}"
    for col in golden.columns:
        g = golden[col]
        d = df[col]
        if pd.api.types.is_numeric_dtype(g) and pd.api.types.is_numeric_dtype(d):
            np.testing.assert_allclose(
                d.to_numpy(dtype=float),
                g.to_numpy(dtype=float),
                rtol=1e-6,
                atol=1e-7,
                equal_nan=True,
                err_msg=f"column {col!r} diverged",
            )
        else:
            assert (d.astype(str).to_numpy() == g.astype(str).to_numpy()).all(), (
                f"column {col!r} (non-numeric) diverged"
            )


@pytest.fixture(
    scope="module",
    params=[
        pytest.param((name, dt), id=f"{name}-{dt}", marks=pytest.mark.xdist_group(f"dynamic-{name}-{dt}"))
        for name, dt in S.SCENARIOS
    ],
)
def dynamic_run(request: pytest.FixtureRequest) -> tuple[AirSourceHeatPumpBoiler, pd.DataFrame, str, int]:
    """Keep one fresh model and a detached first trajectory per scenario.

    Only idempotence runs the model again; golden comparison reads the saved
    first result, so either test order observes the original trajectory.
    With xdist, loadgroup keeps both consumers on the same worker.
    """
    name, dt = request.param
    model = S.make_model()
    first = model.analyze_dynamic(**S.scenario_kwargs(name, dt)).copy(deep=True)
    return model, first, name, dt


def test_analyze_dynamic_matches_golden(dynamic_run: tuple[AirSourceHeatPumpBoiler, pd.DataFrame, str, int]) -> None:
    """The first dynamic run retains every archived numeric and string column."""
    _, first, name, dt = dynamic_run
    _assert_frames_close(first, _load_golden(name, dt))


@pytest.mark.parametrize("name,dt", S.SCENARIOS)
def test_step_public_api_matches_golden(name: str, dt: int) -> None:
    """A fresh model driven only through public step reproduces the archive."""
    df = S.run_step_driven(S.make_model(), name, dt)
    _assert_frames_close(df, _load_golden(name, dt))


def test_analyze_dynamic_idempotent(dynamic_run: tuple[AirSourceHeatPumpBoiler, pd.DataFrame, str, int]) -> None:
    """A second invocation on the exact first-run instance has no state leak."""
    model, first, name, dt = dynamic_run
    second = model.analyze_dynamic(**S.scenario_kwargs(name, dt))
    _assert_frames_close(first, second)


def test_gshpb_step_not_implemented() -> None:
    """GSHPB reads the whole borehole load history each step, so a
    point-state step() would silently corrupt results — it must raise."""
    model = GroundSourceHeatPumpBoiler(ref="R32")
    with pytest.raises(NotImplementedError):
        model.step(None, {}, 600)
