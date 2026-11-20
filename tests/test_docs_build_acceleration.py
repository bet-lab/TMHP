"""Guards for keeping the docs data build fast in local and CI runs.

Workflows are checked as parsed YAML (steps, ``uses``, ``with``, ``if``), not as text.
"""

import subprocess
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
    """Each workflow can reuse its own grid across changes to unrelated validation inputs."""
    for relative, profile in ((TESTS_WORKFLOW, "ci"), (PAGES_WORKFLOW, "full")):
        steps = _all_steps(_workflow(relative))

        (cache,) = [step for step in steps if step.get("id") == "docs-data-cache"]
        assert cache["uses"].startswith("actions/cache@"), relative
        assert cache["with"]["path"] == "docs/source/_static/widgets/cycle_data.json", relative
        key = cache["with"]["key"]
        assert f"-{profile}-" in key and "hashFiles(" in key
        for source in (
            "uv.lock",
            "pyproject.toml",
            "docs/Makefile",
            "scripts/data/gen_refrigerant_data.py",
            "src/tmhp/calc_util.py",
            "src/tmhp/mollier_diagram.py",
            "src/tmhp/refrigerant.py",
        ):
            assert source in key, (relative, source)
        assert "validation/" not in key and "src/tmhp/**" not in key

        (refresh,) = [step for step in steps if step.get("name") == "Use restored cycle grid"]
        assert refresh["if"] == "steps.docs-data-cache.outputs.cache-hit == 'true'", relative
        assert f"cycle_data.{profile}.stamp" in refresh["run"]
        assert any("DOCS_FRESH_DATA=1" in step.get("run", "") for step in steps)


def test_fresh_checkout_regenerates_tracked_data_and_reuses_matching_grid(tmp_path: Path) -> None:
    """Even tracked files newer than all sources must be refreshed for a fresh site build."""
    for name in (
        "glossary.json",
        "validation-points.json",
        "validation-summary.json",
        "timeseries-24h.json",
        "cycle_data.ci.stamp",
        "cycle_data.json",
    ):
        (tmp_path / name).touch()
    plan = subprocess.check_output(
        [
            "make",
            "-ns",
            "-C",
            str(REPO_ROOT / "docs"),
            "data",
            f"DATA_DIR={tmp_path}",
            f"WIDGET_DIR={tmp_path}",
            "DOCS_DATA_PROFILE=ci",
            "DOCS_FRESH_DATA=1",
        ],
        text=True,
    )
    for generator in ("gen_glossary", "gen_validation_data", "gen_timeseries_data"):
        assert plan.count(f"-m scripts.data.{generator}") == 1
    assert "gen_refrigerant_data" not in plan


def test_conflicting_profile_stamps_cannot_reuse_a_different_grid(tmp_path: Path) -> None:
    """Older builds left both stamps beside one JSON when a checkout switched profiles."""
    for name in ("cycle_data.ci.stamp", "cycle_data.full.stamp", "cycle_data.json"):
        (tmp_path / name).touch()
    plan = subprocess.check_output(
        [
            "make",
            "-ns",
            "-C",
            str(REPO_ROOT / "docs"),
            str(tmp_path / "cycle_data.json"),
            f"WIDGET_DIR={tmp_path}",
            "DOCS_DATA_PROFILE=full",
        ],
        text=True,
    )
    assert 'scripts.data.gen_refrigerant_data --profile "full"' in plan


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
    """A change to any published validation input schedules Pages on main."""
    push = _triggers(_workflow(PAGES_WORKFLOW))["push"]

    assert push["branches"] == ["main"]
    paths = push["paths"]
    for deploy_relevant_path in (
        "docs/**",
        "src/tmhp/**",
        "scripts/data/**",
        "scripts/validation/**",
        "validation/parity/*.py",
        "validation/catalogs/*.yaml",
        "validation/results/*.csv",
        "pyproject.toml",
        "uv.lock",
        ".github/workflows/docs.yml",
    ):
        assert deploy_relevant_path in paths

    assert ".github/workflows/tests.yml" not in paths
