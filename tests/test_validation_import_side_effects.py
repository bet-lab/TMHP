"""Importing a validation or script module must not write files.

``validation/extraction/build_registry.py`` once regenerated
``validation/registry/sources.yaml`` at module level. The evidence tree it reads
is not tracked, so a plain ``import`` on a checkout without it replaced the
36-document, checksummed registry -- the file ``docs/source/validation/index.rst``
cites as full provenance -- with an empty skeleton. Other scripts in the same
trees wrote a CSV into the working directory or overwrote a tracked JSON the
same way.

Every check here runs against a copy of the tracked ``validation/`` and
``scripts/`` trees, in a subprocess whose working directory is that copy, so a
regression is caught without touching the real checkout.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
REGISTRY = Path("validation/registry/sources.yaml")
TREES = ("validation", "scripts")


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", *TREES],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout
    return [Path(p) for p in out.decode().split("\0") if p]


def _snapshot(root: Path) -> dict[str, str]:
    """sha256 of every file under ``root`` (bytecode caches excluded)."""
    digests = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            digests[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return digests


@pytest.fixture(scope="module")
def tracked() -> list[Path]:
    try:
        return _tracked_files()
    except (OSError, subprocess.CalledProcessError):
        pytest.fail("these checks need a git checkout to know which files are tracked")


@pytest.fixture
def sandbox(tmp_path: Path, tracked: list[Path]) -> Path:
    """A copy of the tracked validation/ and scripts/ trees."""
    root = tmp_path / "checkout"
    for rel in tracked:
        src = REPO / rel
        if not src.is_file():  # deleted in the working tree but still in the index
            continue
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return root


def _run(root: Path, code: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(root), str(REPO / "src")])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["MPLBACKEND"] = "Agg"
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code), *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=600,
    )


def _registry_documents(path: Path) -> list[str]:
    return [d["path"] for d in yaml.safe_load(path.read_text())["documents"]]


def test_committed_registry_lists_every_described_document():
    """The tracked registry is the full one, not a skeleton written by an accident."""
    documents = yaml.safe_load((REPO / REGISTRY).read_text())
    assert len(documents["documents"]) >= 36
    with_checksum = [d for d in documents["documents"] + documents["unused_archive"] if "sha256" in d]
    assert len(with_checksum) >= 79


def test_importing_build_registry_leaves_the_registry_untouched(sandbox: Path):
    before = (sandbox / REGISTRY).read_bytes()
    result = _run(sandbox, "import validation.extraction.build_registry")
    assert result.returncode == 0, result.stderr
    assert (sandbox / REGISTRY).read_bytes() == before, (
        "importing validation.extraction.build_registry rewrote validation/registry/sources.yaml"
    )
    assert "wrote" not in result.stdout


def test_build_registry_refuses_to_run_without_the_evidence_tree(sandbox: Path):
    assert not (sandbox / "validation" / "evidence").exists()
    before = (sandbox / REGISTRY).read_bytes()
    result = _run(
        sandbox, "import runpy; runpy.run_module('validation.extraction.build_registry', run_name='__main__')"
    )
    assert result.returncode != 0
    assert "validation/evidence" in result.stderr
    assert (sandbox / REGISTRY).read_bytes() == before


def test_build_registry_refuses_to_drop_a_described_document(sandbox: Path):
    """A partial evidence tree must not shrink the registry to what happens to be present."""
    present = _registry_documents(sandbox / REGISTRY)[-1]
    (sandbox / "validation" / "evidence" / present).parent.mkdir(parents=True, exist_ok=True)
    (sandbox / "validation" / "evidence" / present).write_bytes(b"stand-in")
    before = (sandbox / REGISTRY).read_bytes()
    result = _run(
        sandbox, "import runpy; runpy.run_module('validation.extraction.build_registry', run_name='__main__')"
    )
    assert result.returncode != 0
    assert "missing" in result.stderr
    assert (sandbox / REGISTRY).read_bytes() == before


def test_build_registry_regenerates_from_a_complete_evidence_tree(sandbox: Path):
    """With every described document present the script still writes the full registry."""
    evidence = sandbox / "validation" / "evidence"
    expected = _registry_documents(sandbox / REGISTRY)
    for rel in expected:
        target = evidence / rel.rstrip("/")
        if rel.endswith("/"):  # a bulk dataset, recorded as one aggregate row
            target.mkdir(parents=True, exist_ok=True)
            (target / "record.json").write_bytes(b"{}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"stand-in")
    (evidence / "unlisted.txt").write_bytes(b"nobody has read this")
    result = _run(
        sandbox, "import runpy; runpy.run_module('validation.extraction.build_registry', run_name='__main__')"
    )
    assert result.returncode == 0, result.stderr
    regenerated = yaml.safe_load((sandbox / REGISTRY).read_text())
    assert sorted(d["path"] for d in regenerated["documents"]) == sorted(expected)
    assert [d["path"] for d in regenerated["unused_archive"]] == ["unlisted.txt"]


IMPORT_EVERY_MODULE = """
import importlib, importlib.util, json, sys
from pathlib import Path

root = Path.cwd()
sys.argv = ["import-only"]  # some scripts read their arguments at module level
outcome = {}
for path in sorted(p for tree in ("validation", "scripts") for p in (root / tree).rglob("*.py")):
    rel = path.relative_to(root)
    parts = rel.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    packaged = all((root.joinpath(*rel.parts[:i]) / "__init__.py").is_file() for i in range(1, len(rel.parts)))
    try:
        if packaged:
            importlib.import_module(".".join(parts))
        else:
            sys.path.insert(0, str(path.parent))
            name = "_import_only_" + "_".join(parts)
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        outcome[str(rel)] = "ok"
    except BaseException as exc:  # SystemExit included: a script that exits on import still ran
        outcome[str(rel)] = f"{type(exc).__name__}: {exc}"[:200]
print(json.dumps(outcome))
"""


def test_importing_validation_and_script_modules_writes_no_file(sandbox: Path):
    """Import every module under validation/ and scripts/ and compare the tree byte for byte.

    A module that cannot be imported here (an optional dependency such as
    playwright, or an input file that is not tracked) is not a failure of this
    check; what it guards is that nothing is *written*. A module that fails
    after writing is still caught by the comparison.
    """
    before = _snapshot(sandbox)
    result = _run(sandbox, IMPORT_EVERY_MODULE)
    assert result.returncode == 0, result.stderr[-2000:]
    outcome = yaml.safe_load(result.stdout.strip().splitlines()[-1])
    after = _snapshot(sandbox)

    changed = sorted(k for k in before if k in after and before[k] != after[k])
    removed = sorted(set(before) - set(after))
    created = sorted(set(after) - set(before))
    assert not (changed or removed or created), (
        f"importing modules changed {changed}, removed {removed}, created {created}"
    )
    # Most modules must actually have been imported, or the comparison proves nothing.
    imported = [k for k, v in outcome.items() if v == "ok"]
    assert len(imported) >= 0.9 * len(outcome), {k: v for k, v in outcome.items() if v != "ok"}
