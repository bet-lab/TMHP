"""Tests for scripts/data/gen_validation_data.py."""

from __future__ import annotations

import json

from scripts.data.gen_validation_data import build_validation_points


def test_15_points():
    points = build_validation_points()
    assert len(points) == 15


def test_point_schema():
    points = build_validation_points()
    for p in points:
        assert {"case_id", "refrigerant", "t_source_c", "t_sink_c",
                "q_cat_kw", "q_mod_kw", "cop_cat", "cop_mod"} <= p.keys()
        assert isinstance(p["case_id"], int)
        assert p["q_cat_kw"] > 0
        assert p["q_mod_kw"] > 0


def test_writes_validation_json(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.data._common.DATA_DIR", tmp_path)
    from scripts.data.gen_validation_data import main
    main()
    out = tmp_path / "validation-points.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert len(payload) == 15
    first = payload[0]
    assert {"case_id", "refrigerant", "q_cat_kw", "q_mod_kw", "cop_cat", "cop_mod"} <= first.keys()
