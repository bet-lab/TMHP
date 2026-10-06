"""Tests for scripts/data/gen_validation_data.py.

The generator no longer recomputes one hard-coded unit; it reads whatever the
parity harness last wrote under ``validation/results/``. So these tests pin the
contract the site's widget depends on -- the field names, the types, and the
fact that unevaluated points are carried through rather than dropped -- instead
of pinning a point count that changes whenever a catalogue is added.
"""

from __future__ import annotations

import json

import pytest
from scripts.data.gen_validation_data import (
    build_validation_points,
    build_validation_summary,
)

REQUIRED_FIELDS = {
    "unit",
    "slug",
    "manufacturer",
    "model_class",
    "case_id",
    "refrigerant",
    "nominal_kw",
    "mode",
    "t_source_c",
    "t_sink_c",
    "q_cat_kw",
    "cop_cat",
    "cop_mod",
    "delta_pct",
    "failure_reason",
    "usable",
}


@pytest.fixture(scope="module")
def points() -> list[dict]:
    return build_validation_points()


def test_covers_several_units_and_refrigerants(points):
    assert len({p["slug"] for p in points}) >= 2
    assert len({p["refrigerant"] for p in points}) >= 2


def test_point_schema(points):
    for point in points:
        assert point.keys() >= REQUIRED_FIELDS
        assert isinstance(point["case_id"], int)
        assert point["q_cat_kw"] > 0
        assert point["cop_cat"] > 0
        assert point["model_class"] in ("ASHP", "ASHPB")
        assert point["mode"] in ("heating", "cooling")


def test_unevaluated_points_are_carried_not_dropped(points):
    """A table that silently omits its hard cases is not telling the truth."""
    for point in points:
        if point["usable"]:
            assert point["cop_mod"] is not None
            assert point["delta_pct"] is not None
        else:
            # JSON has no NaN; the widget shows the reason instead of a number.
            assert point["cop_mod"] is None
            assert point["delta_pct"] is None
            assert point["failure_reason"] != "none"


def test_summary_is_consistent_with_the_points(points):
    summary = build_validation_summary()
    assert summary["points"] == len(points)
    assert summary["points_evaluated"] == sum(1 for p in points if p["usable"])
    assert summary["units"] == len({p["slug"] for p in points})
    assert 0.0 < summary["cop_mape_pct"] < 100.0


def test_writes_both_json_files(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.data._common.DATA_DIR", tmp_path)
    from scripts.data.gen_validation_data import main

    main()
    payload = json.loads((tmp_path / "validation-points.json").read_text())
    assert payload[0].keys() >= REQUIRED_FIELDS
    summary = json.loads((tmp_path / "validation-summary.json").read_text())
    assert summary["rows"]
