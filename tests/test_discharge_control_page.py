"""What the docs say about the discharge-temperature control against the catalogues.

``docs/source/developer/reference-operating-limits.rst`` ("Against the
catalogues ...") and the scope table quote how many catalogue points the
control acts on, how wet their suction is and how their COP compares. The
figures are those of the committed ``validation/results`` (which
``tests/test_validation_results_current.py`` keeps the model's).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"
#: Upper end of the pressure ratios the rotary efficiency data cover.
PR_DATA_MAX = 6.84


def _pct(value: float) -> str:
    """A signed percentage as the pages print it (a true minus sign)."""
    return f"{value:+.1f}".replace("-", "−")


def _flat(path: str) -> str:
    return re.sub(r"\s+", " ", (ROOT / "docs" / "source" / path).read_text())


@pytest.fixture(scope="module")
def points() -> pd.DataFrame:
    files = [path for path in sorted(RESULTS.glob("*.csv")) if path.name != "summary.csv"]
    frame = pd.concat([pd.read_csv(path) for path in files], ignore_index=True)
    frame = frame[(frame.model_class == "ASHPB") & (frame.status == "adopted")].reset_index(drop=True)
    frame["bias"] = (frame.cop_pred / frame.cop_target - 1.0) * 100.0
    frame["controlled"] = frame.discharge_control.astype(str) == "True"
    return frame


def test_the_counts_and_the_suction_qualities(points: pd.DataFrame) -> None:
    page = _flat("developer/reference-operating-limits.rst")
    controlled = points[points.controlled]
    wet = controlled[controlled.x_suction < 1.0]
    assert set(controlled.refrigerant) == {"R32"}
    assert (
        f"The control acts on {len(controlled)} of the {len(points)} air-to-water points, all of them R32 at "
        f"pressure ratios of {controlled.pr_cmp.min():.1f} to {controlled.pr_cmp.max():.1f}: "
        f"{len(controlled) - len(wet)} by superheat alone, {len(wet)} with a wet suction."
    ) in page
    plain, injected = wet[wet.injection == "none"], wet[wet.injection != "none"]
    assert len(injected) == 1 and set(injected.injection) == {"flash"}
    assert (
        f"The suction quality is {plain.x_suction.min():.3f} to {plain.x_suction.max():.3f} on the "
        f"four points of the units without injection and {injected.x_suction.iloc[0]:.3f} on the one "
        "flash-injection unit, so the floor of 0.9 is not reached."
    ) in page
    assert len(plain) == 4 and wet.x_suction.min() > 0.9


def test_the_cop_against_the_catalogues(points: pd.DataFrame) -> None:
    page = _flat("developer/reference-operating-limits.rst")
    controlled = points[points.controlled]
    scored = controlled[controlled.usable]
    free = points[points.usable & ~points.controlled]
    high = free[free.pr_cmp > PR_DATA_MAX]
    high_r32 = high[high.refrigerant == "R32"]
    assert (
        f"Of the {len(controlled)}, {len(scored)} are scored (the other two deliver less than the published duty), "
        f"and their COP is {_pct(scored.bias.mean())} % against the catalogue."
    ) in page
    assert len(controlled) - len(scored) == 2
    assert set(controlled[~controlled.usable].failure_reason) == {"capacity_short"}
    assert (
        f"The {len(high)} points above a pressure ratio of {PR_DATA_MAX} where the control does not act run "
        f"{_pct(high.bias.mean())} %, like the {len(free)} uncontrolled points as a whole ({_pct(free.bias.mean())} %); "
        f"the {len(high_r32)} of them that are R32 run {_pct(high_r32.bias.mean())} %."
    ) in page
    dry = scored[scored.x_suction >= 1.0]
    gap = scored.bias.mean() - high.bias.mean()
    assert (
        f"does not make up a difference of {gap:.0f} points, and {len(dry)} of the {len(scored)} run with a dry suction"
        in page
    )
    # The scope table carries the same figures.
    scope = _flat("concepts/scope-and-limits.rst")
    assert (
        f"the {len(scored)} scored air-to-water points (all R32, pressure ratio {controlled.pr_cmp.min():.1f} to "
        f"{controlled.pr_cmp.max():.1f}, suction quality {scored.x_suction.min():.3f} to 1) run "
        f"{_pct(scored.bias.mean())} % in COP, the {len(high)} uncontrolled points above pressure ratio "
        f"{PR_DATA_MAX} run {_pct(high.bias.mean())} %"
    ) in scope


def test_the_derated_catalogue_duty_quoted(points: pd.DataFrame) -> None:
    """'(12 kW nameplate, 8.7 kW at -15 degC outdoor and 60 degC leaving water)'"""
    row = points[(points.slug == "panasonic_wh_mxc12j9e8") & (points.t_source_C == -15) & (points.t_sink_C == 60)]
    assert len(row) == 1 and row.nominal_kW.iloc[0] == 12.0 and row.q_kW.iloc[0] == 8.7
    assert "(12 kW nameplate, 8.7 kW at −15 °C outdoor and 60 °C leaving water)" in _flat(
        "developer/reference-operating-limits.rst"
    )
