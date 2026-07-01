"""Tests for scripts/data/gen_timeseries_data.py."""

from __future__ import annotations

import json

from scripts.data.gen_timeseries_data import build_timeseries


def test_144_points_for_24h_at_10min():
    payload = build_timeseries()
    assert len(payload["series"]) == 144


def test_series_row_schema():
    payload = build_timeseries()
    for row in payload["series"]:
        assert {"t_min", "t_amb_c", "q_heat_kw", "cop", "p_cmp_kw"} <= row.keys()


def test_t_min_is_monotonic():
    payload = build_timeseries()
    times = [r["t_min"] for r in payload["series"]]
    assert times == sorted(times)
    assert times[0] == 0 and times[-1] == 23 * 60 + 50


def test_writes_timeseries_json(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.data._common.DATA_DIR", tmp_path)
    from scripts.data.gen_timeseries_data import main

    main()
    out = tmp_path / "timeseries-24h.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert len(payload["series"]) == 144
    first = payload["series"][0]
    assert {"t_min", "t_amb_c", "q_heat_kw", "p_cmp_kw"} <= first.keys()
