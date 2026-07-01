"""Tests for scripts/data/gen_glossary.py."""

from __future__ import annotations

import json

from scripts.data.gen_glossary import build_glossary

REQUIRED_TERMS = (
    "epsilon-ntu",
    "cop",
    "exv",
    "ashpb",
    "gshpb",
    "wshpb",
    "ashp",
    "gshp",
    "m-dot",
    "dt-evap",
    "eta-is",
    "eta-vol",
    "eta-mech",
)


def test_build_glossary_has_required_terms():
    glossary = build_glossary()
    missing = [t for t in REQUIRED_TERMS if t not in glossary]
    assert not missing, f"glossary missing terms: {missing}"


def test_glossary_entry_shape():
    glossary = build_glossary()
    for key, entry in glossary.items():
        assert isinstance(entry, dict), key
        assert {"name", "def", "link"} <= entry.keys(), (key, entry.keys())
        assert entry["name"] and entry["def"] and entry["link"], (key, entry)


def test_glossary_links_are_relative():
    glossary = build_glossary()
    for key, entry in glossary.items():
        link = entry["link"]
        assert not link.startswith(("http://", "https://")), (key, link)
        assert "/" in link, (key, link)


def test_glossary_writes_json(tmp_path, monkeypatch):
    """End-to-end: invoke the script's main() and verify it writes valid JSON."""
    monkeypatch.setattr("scripts.data._common.DATA_DIR", tmp_path)
    from scripts.data.gen_glossary import main

    main()
    out = tmp_path / "glossary.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert "cop" in payload
