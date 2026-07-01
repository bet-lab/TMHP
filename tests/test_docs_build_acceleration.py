"""Guards for keeping the docs data build fast in local and CI runs."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


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
    for workflow in (
        ".github/workflows/tests.yml",
        ".github/workflows/docs.yml",
    ):
        text = _read(workflow)

        assert "id: docs-data-cache" in text, workflow
        assert "actions/cache@" in text, workflow
        assert "docs/source/_static/widgets/cycle_data.json" in text, workflow
        assert "docs/source/_static/widgets/cycle_data.*.stamp" in text, workflow
        assert "docs/source/_static/data/*.json" in text, workflow
        assert "hashFiles(" in text and "scripts/data/**" in text, workflow
        assert "Refresh cached docs data mtimes" in text, workflow
        assert "steps.docs-data-cache.outputs.cache-hit == 'true'" in text, workflow


def test_tests_workflow_uses_ci_docs_profile_on_push() -> None:
    tests_workflow = _read(".github/workflows/tests.yml")
    pages_workflow = _read(".github/workflows/docs.yml")

    assert 'DOCS_DATA_PROFILE="ci"' in tests_workflow
    assert "github.event_name == 'pull_request' && 'ci' || 'full'" not in tests_workflow
    assert "DOCS_DATA_PROFILE=full" in pages_workflow


def test_pages_workflow_cancels_stale_deploys() -> None:
    pages_workflow = _read(".github/workflows/docs.yml")

    assert "group: pages" in pages_workflow
    assert "cancel-in-progress: true" in pages_workflow


def test_pages_workflow_skips_ci_only_pushes() -> None:
    pages_workflow = _read(".github/workflows/docs.yml")

    assert "paths:" in pages_workflow
    for deploy_relevant_path in (
        "docs/**",
        "src/tmhp/**",
        "scripts/data/**",
        "scripts/validation/**",
        "pyproject.toml",
        "uv.lock",
        ".github/workflows/docs.yml",
    ):
        assert f"      - {deploy_relevant_path}" in pages_workflow

    assert "      - .github/workflows/tests.yml" not in pages_workflow
