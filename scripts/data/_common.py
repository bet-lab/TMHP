"""Helpers shared by the build-time data generators.

These scripts run once per ``make html`` (wired in docs/Makefile) and
write JSON files into ``docs/source/_static/data/`` for the frontend to
consume. We keep this module small — paths, atomic write — and let the
specific generators own their domain logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Repo root = three levels above this file (scripts/data/_common.py).
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "docs" / "source" / "_static" / "data"


def write_json(relative_path: str, payload: Any) -> Path:
    """Write ``payload`` as JSON under ``DATA_DIR`` and return the full path.

    Atomic via os.replace so a half-written file never lands in-tree.
    """
    target = DATA_DIR / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(target)
    return target
