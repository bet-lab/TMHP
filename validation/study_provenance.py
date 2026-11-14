"""Provenance records for studies and figures made by running the model once.

The adopted parity results are regenerated together and checked against the
code on every run (``scripts/regen-validation.sh``,
``tests/test_validation_results_current.py``). Everything else under
``validation/data/``, ``validation/results/<study>/`` and the figures under
``docs/`` was produced by running a script once, and goes stale silently when
a model default moves (cycling below the minimum capacity, frost and defrost,
the condensing-temperature cap, ...).

:func:`write` puts a small JSON record next to such an output: which script
and command made it, the commit and source trees it ran on (the fields of
:func:`validation.parity.run.source_state`), and the **model settings the
study chose** -- the constructor arguments it passes explicitly so that its
question does not depend on what the library happens to default to.

A record does not make an output current; it says what it describes. Where a
study is cheap to recompute, a test reruns a sample of it against the
committed numbers instead (``tests/test_study_results_current.py``).
"""

from __future__ import annotations

import json
import shlex
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and value != value:
        return None  # NaN: not a JSON number
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "tolist"):  # numpy scalars and arrays
        return _jsonable(value.tolist())
    return repr(value)


def record(
    script: str,
    outputs: Sequence[str | Path],
    model_settings: Mapping[str, Any],
    question: str,
    command: str | None = None,
) -> dict:
    """The provenance record of one run (not written)."""
    from validation.parity.run import source_state

    rel = []
    for p in outputs:
        path = Path(p)
        try:
            rel.append(str(path.resolve().relative_to(REPO_ROOT)))
        except ValueError:
            rel.append(str(path))
    state = source_state()
    return {
        "script": script,
        "command": command or " ".join(shlex.quote(a) for a in ["python", *sys.argv]),
        "question": question,
        "model_settings": _jsonable(model_settings),
        "outputs": sorted(rel),
        **state,
        "run_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }


def write(
    path: str | Path,
    script: str,
    outputs: Sequence[str | Path],
    model_settings: Mapping[str, Any],
    question: str,
    command: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict:
    """Write the provenance record of a run to ``path`` and return it."""
    rec = record(script, outputs, model_settings, question, command)
    if extra:
        rec.update(_jsonable(extra))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return rec
