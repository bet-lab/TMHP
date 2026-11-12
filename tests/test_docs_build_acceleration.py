"""Guards for keeping the docs data build fast in local and CI runs.

Workflows are checked as parsed YAML (steps, ``uses``, ``with``, ``if``), not as text.
"""

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_WORKFLOW = ".github/workflows/tests.yml"
PAGES_WORKFLOW = ".github/workflows/docs.yml"


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _workflow(relative: str) -> dict[str, Any]:
    loaded = yaml.safe_load(_read(relative))
    assert isinstance(loaded, dict)
    return loaded


def _all_steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # PyYAML follows YAML 1.1, where a bare ``on`` key is the boolean True.
    return workflow["on"] if "on" in workflow else workflow[True]


def test_docs_makefile_tracks_generated_json_as_file_targets() -> None:
    makefile = _read("docs/Makefile")

    assert "DATA_TARGETS" in makefile
    assert "data: $(DATA_TARGETS)" in makefile
    assert "$(CYCLE_DATA_JSON):" in makefile
    assert "$(GLOSSARY_DATA_JSON):" in makefile
    assert "$(VALIDATION_DATA_JSON):" in makefile
    assert "$(TIMESERIES_DATA_JSON):" in makefile
    assert "DOCS_DATA_PROFILE ?= full" in makefile
    assert "CYCLE_DATA_STAMP" in makefile
    assert "gen_refrigerant_data --profile" in makefile


def test_github_workflows_cache_generated_docs_data() -> None:
    for relative in (TESTS_WORKFLOW, PAGES_WORKFLOW):
        steps = _all_steps(_workflow(relative))

        (cache,) = [step for step in steps if step.get("id") == "docs-data-cache"]
        assert cache["uses"].startswith("actions/cache@"), relative
        cached_paths = cache["with"]["path"].split()
        assert "docs/source/_static/widgets/cycle_data.json" in cached_paths, relative
        assert "docs/source/_static/widgets/cycle_data.*.stamp" in cached_paths, relative
        assert "docs/source/_static/data/*.json" in cached_paths, relative
        key = cache["with"]["key"]
        assert "hashFiles(" in key and "scripts/data/**" in key, relative

        (refresh,) = [step for step in steps if step.get("name") == "Refresh cached docs data mtimes"]
        assert refresh["if"] == "steps.docs-data-cache.outputs.cache-hit == 'true'", relative


def test_tests_workflow_uses_ci_docs_profile_on_push() -> None:
    tests_runs = [step.get("run", "") for step in _all_steps(_workflow(TESTS_WORKFLOW))]
    pages_runs = [step.get("run", "") for step in _all_steps(_workflow(PAGES_WORKFLOW))]

    assert any('DOCS_DATA_PROFILE="ci"' in run for run in tests_runs)
    assert not any("DOCS_DATA_PROFILE=full" in run for run in tests_runs)
    assert any("DOCS_DATA_PROFILE=full" in run for run in pages_runs)


def test_pages_workflow_cancels_stale_deploys() -> None:
    concurrency = _workflow(PAGES_WORKFLOW)["concurrency"]

    assert concurrency["group"] == "pages"
    assert concurrency["cancel-in-progress"] is True


def test_pages_workflow_skips_ci_only_pushes() -> None:
    push = _triggers(_workflow(PAGES_WORKFLOW))["push"]

    assert push["branches"] == ["main"]
    paths = push["paths"]
    for deploy_relevant_path in (
        "docs/**",
        "src/tmhp/**",
        "scripts/data/**",
        "scripts/validation/**",
        "pyproject.toml",
        "uv.lock",
        ".github/workflows/docs.yml",
    ):
        assert deploy_relevant_path in paths

    assert ".github/workflows/tests.yml" not in paths
