"""Guard the validation numbers quoted in prose against the harness output.

The whole point of this subsystem is that the published error figures are the
ones a user of the library would get. That guarantee is only as good as the
weakest copy of the numbers -- and a hand-typed figure in a documentation page
is exactly the kind of copy that drifts, which is how the previously published
MAPE came to describe a parameter set nobody was shipping.

So every number quoted in prose is checked against
``validation/results/summary.csv`` here. If the harness output moves and the
prose does not, this fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SUMMARY = REPO_ROOT / "validation" / "results" / "summary.csv"

pytestmark = pytest.mark.skipif(not SUMMARY.exists(), reason="parity harness has not been run in this checkout")


@pytest.fixture(scope="module")
def summary() -> pd.DataFrame:
    return pd.read_csv(SUMMARY)


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _flat(relative: str) -> str:
    """Whitespace-collapsed text, so a claim split by an RST line wrap matches."""
    return re.sub(r"\s+", " ", _read(relative))


def _weighted_mape(rows: pd.DataFrame) -> float:
    evaluated = rows.points_usable.sum()
    return float((rows.cop_MAPE_pct * rows.points_usable).sum() / evaluated)


def _quoted_percents(text: str, marker: str) -> list[float]:
    """Percentages on the line containing ``marker``."""
    line = next(ln for ln in text.splitlines() if marker in ln)
    return [float(v) for v in re.findall(r"(\d+\.\d)\s*%", line)]


def test_headline_counts_match_the_harness(summary: pd.DataFrame) -> None:
    text = _flat("docs/source/validation/index.rst")
    attempted = int(summary.points.sum())
    evaluated = int(summary.points_usable.sum())
    assert f"{attempted} attempted, {evaluated} evaluated" in text
    assert f"{evaluated} evaluated points" in text


def test_headline_mape_matches_the_harness(summary: pd.DataFrame) -> None:
    overall = _weighted_mape(summary)
    for relative, marker in (
        ("docs/source/validation/index.rst", "The headline number across the whole set"),
        ("README.md", "Across 747 evaluated points" if overall else ""),
    ):
        text = _read(relative)
        quoted = _quoted_percents(text, marker)
        assert quoted, f"{relative}: no percentage found on the headline line"
        assert any(abs(value - overall) < 0.1 for value in quoted), (
            f"{relative} quotes {quoted}, harness says {overall:.1f} %"
        )


def test_group_breakdown_matches_the_harness(summary: pd.DataFrame) -> None:
    """The split by family and manufacturer is the point of the section."""
    groups = {
        "ashpb": summary[summary.model_class == "ASHPB"],
        "daikin": summary[(summary.model_class == "ASHP") & (summary.manufacturer == "Daikin")],
        "fujitsu": summary[(summary.model_class == "ASHP") & (summary.manufacturer == "Fujitsu")],
    }
    expected = {name: (_weighted_mape(rows), len(rows)) for name, rows in groups.items()}

    text = _read("docs/source/validation/index.rst")
    for label, key in (
        ("Air-to-water, 10 units", "ashpb"),
        ("Air-to-air, Daikin, 5 units", "daikin"),
        ("Air-to-air, Fujitsu, 2 units", "fujitsu"),
    ):
        mape, count = expected[key]
        assert f"{count} units" in label, f"unit count in {label!r} disagrees with the harness"
        assert label in text, f"{label!r} missing from the residuals table"
        # The MAPE sits on the line after the group label in the list-table.
        index = text.index(label)
        window = text[index : index + 220]
        quoted = [float(v) for v in re.findall(r"(\d+(?:\.\d)?)\s*%", window)]
        assert any(abs(value - mape) < 0.6 for value in quoted), (
            f"{label}: page quotes {quoted}, harness says {mape:.1f} %"
        )


def test_coverage_claims_match_the_catalogues(summary: pd.DataFrame) -> None:
    text = _read("docs/source/validation/index.rst")
    for manufacturer in sorted(summary.manufacturer.unique()):
        assert manufacturer in text, f"{manufacturer} is in the results but not named on the page"
    for refrigerant in sorted(summary.refrigerant.unique()):
        assert refrigerant in text, f"{refrigerant} is in the results but not named on the page"
    low, high = summary.nominal_kW.min(), summary.nominal_kW.max()
    assert f"{low:.1f}" in text and f"{high:.1f}" in text


def test_defaults_page_states_the_shipped_rules() -> None:
    """The evidence page must describe the values the code actually uses."""
    from tmhp import AirSourceHeatPump
    from tmhp.compressor_efficiency import ETA_EM_REF, ETA_VOL_CLEARANCE

    text = _read("docs/source/validation/defaults.rst")
    model = AirSourceHeatPump(hp_capacity=5000.0, ref="R32")

    divisor = model.hp_capacity / model.UA_ou_rated
    assert f"hp_capacity / {divisor:.0f}" in text
    ratio = model.UA_iu_rated / model.UA_ou_rated
    assert f"UA_iu = {ratio:.1f} × UA_ou" in text
    assert f"{ETA_EM_REF:.2f} ×" in text
    assert f"{ETA_VOL_CLEARANCE:.3f}" in text
