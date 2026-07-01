"""Guards for keeping the GitHub Actions test matrix focused and fast."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _job_block(text: str, job_name: str, next_job_name: str) -> str:
    start = text.index(f"  {job_name}:\n")
    end = text.index(f"  {next_job_name}:\n", start)
    return text[start:end]


def test_static_analysis_runs_once_outside_python_test_matrix() -> None:
    text = _read(".github/workflows/tests.yml")
    test_job = _job_block(text, "test", "docs")

    assert "  lint-type:\n" in text
    assert "name: Static analysis" in text
    assert text.count("name: Ruff lint") == 1
    assert text.count("name: Mypy") == 1
    assert "name: Ruff lint" not in test_job
    assert "name: Mypy" not in test_job


def test_coverage_is_collected_once_on_canonical_python() -> None:
    text = _read(".github/workflows/tests.yml")

    assert "if: matrix.python-version == '3.12'" in text
    assert "if: matrix.python-version != '3.12'" in text
    assert "name: coverage-3.12" in text
    assert "coverage-${{ matrix.python-version }}" not in text


def test_test_matrix_runs_pytest_with_xdist_workers() -> None:
    workflow = _read(".github/workflows/tests.yml")
    pyproject = _read("pyproject.toml")

    assert '"pytest-xdist>=' in pyproject
    assert workflow.count("uv run pytest -q -n auto") == 2
    assert "uv run pytest -q -n auto \\\n            --cov=tmhp" in workflow


def test_pytest_matrix_skips_docs_only_pull_requests() -> None:
    workflow = _read(".github/workflows/tests.yml")
    test_job = _job_block(workflow, "test", "docs")

    assert "  test-scope:\n" in workflow
    assert "run-matrix: ${{ steps.changed-files.outputs.run-matrix }}" in workflow
    assert "github.event.pull_request.base.sha" in workflow
    assert "github.event.pull_request.head.sha" in workflow
    assert "needs: test-scope" in test_job
    assert "if: needs.test-scope.outputs.run-matrix == 'true'" in test_job
    for matrix_trigger in (
        "^src/",
        "^tests/",
        "^scripts/",
        "^pyproject\\.toml$",
        "^uv\\.lock$",
        "^\\.github/workflows/tests\\.yml$",
    ):
        assert matrix_trigger in workflow


def test_pytest_matrix_skips_all_main_pushes_after_pr_gate() -> None:
    workflow = _read(".github/workflows/tests.yml")

    assert '"${{ github.event_name }}" != "pull_request"' in workflow
    assert 'echo "run-matrix=false" >> "$GITHUB_OUTPUT"' in workflow
    assert "github.event.before" not in workflow
    assert "github.event.after" not in workflow
    assert "0000000000000000000000000000000000000000" not in workflow
