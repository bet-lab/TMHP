"""What the docs say about the discharge-temperature control against the catalogues.

``docs/source/developer/reference-operating-limits.rst`` and the scope table
quote how many catalogue points the
control acts on, how wet their suction is and how their COP compares. The
figures are those of the committed ``validation/results`` (which
``tests/test_validation_results_current.py`` keeps the model's).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest
from validation.parity.spec import load_all

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "results"


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
    return frame


def test_the_counts_and_the_suction_qualities(points: pd.DataFrame) -> None:
    page = _flat("developer/reference-operating-limits.rst")
    controlled = points[points.discharge_control.eq(True)]
    wet = controlled[controlled.x_suction < 1.0]
    dry = controlled[controlled.x_suction.eq(1.0)]
    assert len(wet) + len(dry) == len(controlled)
    assert set(wet.refrigerant) == {"R32"}
    assert (f"Discharge control acts on {len(controlled)} of {len(points)} air-to-water catalogue points.") in page
    assert (
        f"It reduces superheat alone on {len(dry)} points, including {dry.refrigerant.eq('R410A').sum()} with R410A."
    ) in page
    assert (
        f"The {len(wet)} wet-suction points all use R32, with suction quality from "
        f"{wet.x_suction.min():.3f} to {wet.x_suction.max():.3f}."
    ) in page


def test_the_cop_against_the_catalogues(points: pd.DataFrame) -> None:
    page = _flat("developer/reference-operating-limits.rst")
    controlled = points[points.discharge_control.eq(True)]
    wet = controlled[controlled.x_suction < 1.0]
    excluded = wet[~wet.usable]
    assert len(excluded) == 1 and excluded.failure_reason.iat[0] == "capacity_short"
    scored_wet = wet[wet.usable]
    scored_dry = controlled[controlled.usable & controlled.x_suction.eq(1.0)]
    free = points[points.usable & points.discharge_control.eq(False)]
    assert (
        f"One wet-suction point falls short of its published duty and is excluded from the COP score. "
        f"The other {len(scored_wet)} average {_pct(scored_wet.bias.mean())} % COP bias against the catalogue. "
        f"The {len(scored_dry)} points with reduced superheat average {_pct(scored_dry.bias.mean())} %, "
        f"and the {len(free)} scored points without control average {_pct(free.bias.mean())} %."
    ) in page
    scope = _flat("concepts/scope-and-limits.rst")
    assert (
        f"The {len(scored_wet)} scored air-to-water points with wet suction average "
        f"{_pct(scored_wet.bias.mean())} % COP bias against the catalogues. The {len(free)} scored points "
        f"without discharge control average {_pct(free.bias.mean())} %"
    ) in scope


def test_the_derated_catalogue_duty_quoted(points: pd.DataFrame) -> None:
    (catalogue,) = load_all("panasonic_wh_mxc12j9e8")
    (point,) = [p for p in catalogue.points if (p.t_source_C, p.t_sink_C) == (-15, 60)]
    row = points[(points.slug == catalogue.slug) & (points.point_id == point.id)].iloc[0]
    assert row.discharge_control and row.x_suction < 1.0
    assert row.q_pred_kW == pytest.approx(point.q_kW)
    assert (
        f"For example, Panasonic lists {point.q_kW:g} kW at −15 °C outdoors and 60 °C leaving water "
        f"for its nominal {catalogue.nominal_capacity_kW:g} kW WH-MXC12J9E8."
    ) in _flat("developer/reference-operating-limits.rst")
