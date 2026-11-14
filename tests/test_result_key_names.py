"""No renamed result key is left in the repository (#96, round 5).

Nine result keys were renamed or removed without an alias
(:data:`tests._renamed_result_keys.RENAMED_RESULT_KEYS`). The families'
results are checked in ``tests/test_docs_alignment.py`` (one default machine
per family, steady and dynamic). This file checks the rest: no tracked file
reads or writes an old key -- code, tests, documentation, validation scripts
and the column names of every committed CSV (validation results, the step
goldens) -- and the result-key dictionary names the old keys only in its
"Renamed keys" table, which lists every one of them with its new key.

Where an old string has another meaning it is not a result key and stays:

- ``"pressure_ratio"`` is also a *value* of ``capacity_binding``
  (``tmhp.compressor_envelope.BINDING_PRESSURE_RATIO``) and a column of the
  compressor-map data (``validation/bitzer_efficiency_pilot``); only its use as
  a result key is checked (a subscript, a ``.get``, a dict key in ``src`` and
  ``tests``; ````pressure_ratio```` unquoted in prose).
- ``fan_flow_min_limit`` / ``fan_flow_max_limit`` are also the flags of the fan
  helper :func:`tmhp.hx_fan.calc_fan_operating_point` (:data:`_FAN_HELPER`).
"""

from __future__ import annotations

import csv
import fnmatch
import gzip
import re
import subprocess
from pathlib import Path

import pytest

from tests._renamed_result_keys import RENAMED_RESULT_KEYS

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULT_KEYS_PAGE = "docs/source/concepts/result-keys.rst"
TEXT_SUFFIXES = (".py", ".rst", ".md", ".json", ".toml", ".yml", ".yaml", ".txt", ".sh", ".ipynb", ".js", ".html")
#: Files that name the old keys on purpose.
_OWN_FILES = {RESULT_KEYS_PAGE, "tests/_renamed_result_keys.py", "tests/test_result_key_names.py"}
#: Co-author areas are not edited from here; a hit there is reported to them instead.
_NOT_SCANNED = (
    "src/tmhp/calibration.py",
    "src/tmhp/compressor_baseline.py",
    "src/tmhp/compressor_efficiency.py",
    "validation/compressor_fitting/*",
    "validation/registry/*",
)
#: The fan helper's own flags: (file, line pattern; ``None`` = the whole file).
_FAN_HELPER: tuple[tuple[str, str | None], ...] = (
    ("src/tmhp/hx_fan.py", None),
    ("tests/test_fan_part_load_bounds.py", None),
    ("tests/test_fan_single_zone_defaults.py", None),
    ("tests/test_fixed_speed_fan.py", r"\bout\["),
    (
        "docs/source/developer/reference-operating-limits.rst",
        r"^``fan_flow_min_limit`` / ``fan_flow_max_limit``\. A nondefault",
    ),
)


def _quoted(key: str) -> re.Pattern[str]:
    return re.compile(r"([\"'`])" + re.escape(key) + r"\1")


def _line_patterns(key: str, path: str) -> list[re.Pattern[str]]:
    if key != "pressure_ratio":
        return [_quoted(key)]
    if path.endswith(".py") and path.startswith(("src/", "tests/")):
        k = re.escape(key)
        return [
            re.compile(rf"\[\s*[\"']{k}[\"']\s*\]"),
            re.compile(rf"\.get\(\s*[\"']{k}[\"']\s*[,)]"),
            re.compile(rf"[\"']{k}[\"']\s*:"),
        ]
    if path.endswith((".rst", ".md")):
        return [re.compile(r"``pressure_ratio``")]
    return []


def _fan_helper(path: str, line: str) -> bool:
    return any(fnmatch.fnmatch(path, f) and (rx is None or re.search(rx, line)) for f, rx in _FAN_HELPER)


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files"], check=True, capture_output=True, text=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [p for p in out.splitlines() if p and (REPO_ROOT / p).is_file()]


def _scanned(path: str) -> bool:
    return path not in _OWN_FILES and not any(fnmatch.fnmatch(path, g) for g in _NOT_SCANNED)


def _hits_in_text(path: str, text: str) -> list[str]:
    hits = []
    for no, line in enumerate(text.splitlines(), 1):
        for key in RENAMED_RESULT_KEYS:
            if key.startswith("fan_flow_") and _fan_helper(path, line):
                continue
            if any(rx.search(line) for rx in _line_patterns(key, path)):
                hits.append(f"{path}:{no}: {key!r}: {line.strip()[:120]}")
    return hits


def _csv_header(path: Path) -> list[str]:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", newline="") as fh:
        first = fh.readline()
    return next(csv.reader([first]), [])


def test_no_tracked_file_uses_a_renamed_result_key() -> None:
    hits: list[str] = []
    for path in _tracked_files():
        if not _scanned(path):
            continue
        full = REPO_ROOT / path
        if path.endswith((".csv", ".csv.gz")):
            try:
                header = _csv_header(full)
            except (UnicodeDecodeError, OSError):
                continue
            # A column called ``pressure_ratio`` is the compressor-map data's, not a result key.
            old = [c for c in header if c in RENAMED_RESULT_KEYS and c != "pressure_ratio"]
            hits += [f"{path}: column {c!r}" for c in old]
        elif path.endswith(TEXT_SUFFIXES):
            try:
                text = full.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            hits += _hits_in_text(path, text)
    assert not hits, (
        f"renamed result keys are still used (see the 'Renamed keys' table of {RESULT_KEYS_PAGE}):\n" + "\n".join(hits)
    )


def _renamed_section(page: str) -> tuple[str, str]:
    """The page split into its "Renamed keys" section and everything else."""
    m = re.search(r"^Renamed keys\n=+\n(?P<body>.*?)(?=^[^\n]+\n=+\n)", page, flags=re.M | re.S)
    assert m, f"{RESULT_KEYS_PAGE} has no 'Renamed keys' section"
    return m.group("body"), page[: m.start()] + page[m.end() :]


def test_result_key_dictionary_names_old_keys_only_in_its_renamed_table() -> None:
    page = (REPO_ROOT / RESULT_KEYS_PAGE).read_text(encoding="utf-8")
    section, rest = _renamed_section(page)
    rows = re.split(r"^    \* - ", section, flags=re.M)[1:]
    named = {row: set(re.findall(r"``([^`]+)``", row)) for row in rows}
    for old, new in RENAMED_RESULT_KEYS.items():
        row = [r for r, keys in named.items() if old in keys]
        assert row, f"the 'Renamed keys' table does not list {old!r}"
        if new is not None:
            assert new in named[row[0]], f"the 'Renamed keys' row of {old!r} does not name {new!r}"
    leftovers = _hits_in_text(RESULT_KEYS_PAGE, rest)
    assert not leftovers, "old keys outside the 'Renamed keys' table:\n" + "\n".join(leftovers)
