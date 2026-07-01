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

Bit-identity tolerance: rtol=1e-9 (a pure code extraction must not change
the math; the tolerance only absorbs %.17g CSV round-trip noise).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tmhp import GroundSourceHeatPumpBoiler

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
                rtol=1e-9,
                atol=1e-12,
                equal_nan=True,
                err_msg=f"column {col!r} diverged",
            )
        else:
            assert (d.astype(str).to_numpy() == g.astype(str).to_numpy()).all(), (
                f"column {col!r} (non-numeric) diverged"
            )


@pytest.mark.parametrize("name,dt", S.SCENARIOS)
def test_analyze_dynamic_matches_golden(name: str, dt: int) -> None:
    df = S.make_model().analyze_dynamic(**S.scenario_kwargs(name, dt))
    _assert_frames_close(df, _load_golden(name, dt))


@pytest.mark.parametrize("name,dt", S.SCENARIOS)
def test_step_public_api_matches_golden(name: str, dt: int) -> None:
    df = S.run_step_driven(S.make_model(), name, dt)
    _assert_frames_close(df, _load_golden(name, dt))


@pytest.mark.parametrize("name,dt", S.SCENARIOS)
def test_analyze_dynamic_idempotent(name: str, dt: int) -> None:
    model = S.make_model()
    df1 = model.analyze_dynamic(**S.scenario_kwargs(name, dt))
    df2 = model.analyze_dynamic(**S.scenario_kwargs(name, dt))
    _assert_frames_close(df1, df2)


def test_gshpb_step_not_implemented() -> None:
    """GSHPB reads the whole borehole load history each step, so a
    point-state step() would silently corrupt results — it must raise."""
    model = GroundSourceHeatPumpBoiler(ref="R32")
    with pytest.raises(NotImplementedError):
        model.step(None, {}, 600)
