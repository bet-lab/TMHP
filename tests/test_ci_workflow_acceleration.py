"""Guards for keeping the GitHub Actions test matrix focused and fast.

The workflow is checked as parsed YAML (jobs, steps, ``if``/``needs`` values), not as
text, so re-indenting or re-wrapping the file cannot break these tests; only a change
in *which job runs under which condition* can.
"""

import argparse
import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/tests.yml"


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(_read(WORKFLOW))
    assert isinstance(loaded, dict)
    return loaded


def _job(name: str) -> dict[str, Any]:
    return _workflow()["jobs"][name]


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return job["steps"]


def _step_names(job: dict[str, Any]) -> list[str]:
    return [step["name"] for step in _steps(job) if "name" in step]


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [step for step in _steps(job) if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}, found {len(matches)}"
    return matches[0]


def _commands(run: str) -> list[list[str]]:
    """Split a (possibly multi-line, backslash-continued) ``run`` block into argv lists."""
    joined = run.replace("\\\n", " ")
    return [shlex.split(line) for line in joined.splitlines() if line.strip()]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def test_every_workflow_job_runs_on_github_hosted_runners() -> None:
    """TMHP is public: its CI/CD runs on GitHub-hosted runners only, never self-hosted.

    Hosted runners are free for public repositories and isolated per job. Main pushes
    pinned to the lab's ``ml-main`` runner queued for about four hours on 2026-11-14
    while the same checks on PRs (hosted) were green.
    """
    workflows = sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))

    assert {path.name for path in workflows} >= {"tests.yml", "docs.yml"}
    for path in workflows:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, job in loaded["jobs"].items():
            assert job["runs-on"] == "ubuntu-24.04", f"{path.name}:{name}"


def test_static_analysis_runs_once_outside_python_test_matrix() -> None:
    lint, test = _job("lint-type"), _job("test")

    assert lint["name"] == "Static analysis"
    assert "strategy" not in lint
    for name in ("Ruff lint", "Ruff format", "Mypy"):
        assert _step_names(lint).count(name) == 1, name
        assert name not in _step_names(test), name

    (ruff_check,) = _commands(_step(lint, "Ruff lint")["run"])
    (ruff_format,) = _commands(_step(lint, "Ruff format")["run"])
    assert ruff_check[:4] == ["uv", "run", "ruff", "check"]
    assert ruff_format[:5] == ["uv", "run", "ruff", "format", "--check"]
    # validation/ is linted in CI too, so its scripts cannot drift again.
    expected_paths = {"src/tmhp", "tests", "scripts", "validation"}
    assert set(ruff_check[4:]) == expected_paths
    assert set(ruff_format[5:]) == expected_paths
    assert _commands(_step(lint, "Mypy")["run"]) == [["uv", "run", "mypy", "src/tmhp"]]


def test_coverage_is_collected_once_on_canonical_python() -> None:
    test = _job("test")
    plain = _step(test, "Pytest")
    covered = _step(test, "Pytest with coverage")
    upload = _step(test, "Upload coverage XML")

    assert plain["if"] == "matrix.python-version != '3.12'"
    assert covered["if"] == "matrix.python-version == '3.12'"
    assert upload["if"] == "matrix.python-version == '3.12'"
    assert upload["with"]["name"] == "coverage-3.12"
    assert upload["with"]["path"] == "coverage.xml"


def _pytest_options(job: dict[str, Any]) -> list[argparse.Namespace]:
    """Parse each matrix pytest shell command independent of option order."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-n", "--numprocesses")
    parser.add_argument("--dist", default="load")
    parser.add_argument("--cov", action="append", default=[])
    parser.add_argument("--cov-report", action="append", default=[])
    parser.add_argument("--cov-fail-under", default=None)
    commands = []
    for step in _steps(job):
        for command in _commands(step.get("run", "")):
            if command[:3] == ["uv", "run", "pytest"]:
                options, _ = parser.parse_known_args(command[3:])
                commands.append(options)
    return commands


def test_test_matrix_runs_pytest_with_xdist_workers() -> None:
    """Both matrix commands group shared trajectories and only one collects coverage."""
    commands = _pytest_options(_job("test"))
    pyproject = _read("pyproject.toml")

    assert '"pytest-xdist>=' in pyproject
    assert len(commands) == 2
    assert all(command.numprocesses == "auto" for command in commands)
    assert all(command.dist == "loadgroup" for command in commands)
    assert sorted((command.cov, command.cov_report) for command in commands) == [([], []), (["tmhp"], ["term", "xml"])]


def test_coverage_job_enforces_a_floor() -> None:
    """The canonical-Python run fails if total coverage drops below the floor."""
    covered = [command for command in _pytest_options(_job("test")) if command.cov]
    assert len(covered) == 1
    assert covered[0].cov_fail_under is not None
    assert float(covered[0].cov_fail_under) >= 88


def test_local_ci_script_enforces_the_same_coverage_floor() -> None:
    """scripts/ci-local.sh must fail on a coverage drop exactly as the 3.12 CI job does."""
    covered = [command for command in _pytest_options(_job("test")) if command.cov]
    script = _read("scripts/ci-local.sh").replace("\\\n", " ")
    local = [shlex.split(line) for line in script.splitlines() if "uv run pytest" in line and "run_step" in line]

    assert len(local) == 1
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--cov", action="append", default=[])
    parser.add_argument("--cov-fail-under", default=None)
    options, _ = parser.parse_known_args(local[0])
    assert options.cov == ["tmhp"]
    assert options.cov_fail_under == covered[0].cov_fail_under


def test_local_ci_script_never_drops_the_integrations_extra() -> None:
    """Every ``uv sync`` of scripts/ci-local.sh keeps the extra the FMU adapter tests need.

    The docs step used to sync without it, which uninstalled fmpy and
    pythonfmu: the next ``--no-sync`` run then skipped those tests silently.
    """
    script = _read("scripts/ci-local.sh").replace("\\\n", " ")
    syncs = [shlex.split(line) for line in script.splitlines() if "run_step" in line and "uv sync" in line]

    assert len(syncs) == 2
    for command in syncs:
        assert command[command.index("--extra") + 1] == "integrations", command


def test_pytest_matrix_is_gated_by_the_scope_job() -> None:
    workflow = _workflow()
    scope, test = workflow["jobs"]["test-scope"], workflow["jobs"]["test"]

    assert scope["outputs"]["run-matrix"] == "${{ steps.changed-files.outputs.run-matrix }}"
    assert _as_list(test["needs"]) == ["test-scope"]
    assert test["if"] == "needs.test-scope.outputs.run-matrix == 'true'"
    (checkout,) = [step for step in _steps(scope) if str(step.get("uses", "")).startswith("actions/checkout@")]
    assert checkout["if"] == "github.event_name == 'pull_request'"
    assert checkout["with"]["fetch-depth"] == 2


def test_matrix_covers_every_supported_python_and_the_classifiers_agree() -> None:
    versions = _job("test")["strategy"]["matrix"]["python-version"]
    pyproject = _read("pyproject.toml")

    assert _job("test")["strategy"]["fail-fast"] is False
    assert versions == sorted(versions, key=lambda v: tuple(map(int, v.split("."))))
    assert "3.12" in versions  # the canonical version that collects coverage
    assert "3.14" in versions
    for version in versions:
        assert f'"Programming Language :: Python :: {version}"' in pyproject, version


def test_matrix_pins_uv_interpreter_and_checks_the_actual_version() -> None:
    test = _job("test")
    verify = _step(test, "Verify matrix interpreter")

    assert test["env"]["UV_PYTHON"] == "${{ matrix.python-version }}"
    assert "sys.version_info[:2]" in verify["run"]
    assert "${{ matrix.python-version }}" in verify["run"]
    setup = [step for step in _steps(test) if str(step.get("uses", "")).startswith("actions/setup-python@")]
    assert [step["with"]["python-version"] for step in setup] == ["${{ matrix.python-version }}"]


def _scope_script() -> str:
    scope = _job("test-scope")
    (step,) = [step for step in _steps(scope) if step.get("id") == "changed-files"]
    return dedent(step["run"])


# (changed path, event, whether the pytest matrix must run)
SCOPE_CASES = (
    # Everything pytest reads or imports triggers the matrix on a PR.
    ("README.md", "pull_request", "true"),
    ("THIRD_PARTY_NOTICES.md", "pull_request", "true"),
    (".github/migration-notes.txt", "pull_request", "true"),
    ("examples/result_columns.csv.gz", "pull_request", "true"),
    ("notebooks/example.ipynb", "pull_request", "true"),
    ("new-area/future-input", "pull_request", "true"),
    ("LICENSE/reference.md", "pull_request", "true"),
    ("src/unusual\nfilename.py", "pull_request", "true"),
    ("src/tmhp/fixture.py", "pull_request", "true"),
    ("tests/test_fixture.py", "pull_request", "true"),
    ("scripts/fixture.py", "pull_request", "true"),
    ("pyproject.toml", "pull_request", "true"),
    ("uv.lock", "pull_request", "true"),
    (".github/workflows/tests.yml", "pull_request", "true"),
    # test_ci_workflow_acceleration.py reads every workflow (hosted-runner guard).
    (".github/workflows/docs.yml", "pull_request", "true"),
    # tests/test_docs_alignment.py, test_docs_examples.py read docs/;
    # test_validation_docs_alignment.py reads validation/results/.
    ("docs/source/index.rst", "pull_request", "true"),
    ("docs/source/guide/page.rst", "pull_request", "true"),
    ("validation/results/summary.csv", "pull_request", "true"),
    ("validation/parity/run.py", "pull_request", "true"),
    # Only the root license is exempt on a PR.
    ("LICENSE", "pull_request", "false"),
    # Trusted pushes always run it.
    ("LICENSE", "push", "true"),
    ("docs/another.rst", "push", "true"),
)


@pytest.fixture
def scope_environment() -> dict[str, str]:
    """Use a synthetic Git author without inheriting another repository's Git environment."""
    return {
        **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        "GIT_AUTHOR_NAME": "CI scope fixture",
        "GIT_AUTHOR_EMAIL": "scope@example.invalid",
        "GIT_COMMITTER_NAME": "CI scope fixture",
        "GIT_COMMITTER_EMAIL": "scope@example.invalid",
    }


@pytest.fixture
def scope_git(tmp_path: Path, scope_environment: dict[str, str]) -> Callable[..., None]:
    """Create a checkout for exercising the workflow's real Git comparison."""

    def git(*args: str) -> None:
        """Run isolated fixture Git without invoking user hooks or commit signing."""
        subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
            cwd=tmp_path,
            env=scope_environment,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init", "-q", "-b", "base")
    return git


def _run_scope(tmp_path: Path, environment: dict[str, str], event: str) -> tuple[int, str]:
    """Run the workflow step and return its exit status and emitted matrix decision."""
    output = tmp_path / "output"
    output.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["bash", "-c", _scope_script()],
        cwd=tmp_path,
        env={**environment, "GITHUB_EVENT_NAME": event, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
    )
    return result.returncode, output.read_text(encoding="utf-8").strip()


@pytest.mark.parametrize(("relative", "event", "expected"), SCOPE_CASES, ids=[f"{e}:{r}" for r, e, _ in SCOPE_CASES])
def test_matrix_scope_selects_tests_for_changed_paths(
    tmp_path: Path,
    scope_environment: dict[str, str],
    scope_git: Callable[..., None],
    relative: str,
    event: str,
    expected: str,
) -> None:
    """Exercise the real scope shell so no input of the tests can bypass the PR matrix."""
    (tmp_path / "fixture").write_text("base\n", encoding="utf-8")
    scope_git("add", "fixture")
    scope_git("commit", "-qm", "base")
    scope_git("checkout", "-qb", "pr")

    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fixture\n", encoding="utf-8")
    scope_git("add", relative)
    scope_git("commit", "-qm", relative)
    scope_git("checkout", "-q", "base")
    # A base-only source change belongs to the merge result independently of
    # the PR. It must not send an unrelated LICENSE-only PR through the matrix.
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src/base-only.py").write_text("base change\n", encoding="utf-8")
    scope_git("add", "src/base-only.py")
    scope_git("commit", "-qm", "base advanced")
    scope_git("merge", "--no-ff", "-qm", "PR merge", "pr")

    assert _run_scope(tmp_path, scope_environment, event) == (0, f"run-matrix={expected}")


def test_matrix_scope_runs_when_a_test_input_is_renamed_to_the_license(
    tmp_path: Path, scope_environment: dict[str, str], scope_git: Callable[..., None]
) -> None:
    """The deletion still runs pytest when the destination of a rename is exempt."""
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text("fixture\n", encoding="utf-8")
    scope_git("add", "THIRD_PARTY_NOTICES.md")
    scope_git("commit", "-qm", "base")
    scope_git("checkout", "-qb", "pr")
    scope_git("mv", "THIRD_PARTY_NOTICES.md", "LICENSE")
    scope_git("commit", "-qm", "rename notice")
    scope_git("checkout", "-q", "base")
    scope_git("merge", "--no-ff", "-qm", "PR merge", "pr")

    assert _run_scope(tmp_path, scope_environment, "pull_request") == (0, "run-matrix=true")


def test_matrix_scope_fails_when_the_merge_base_parent_is_missing(
    tmp_path: Path, scope_environment: dict[str, str], scope_git: Callable[..., None]
) -> None:
    """A Git comparison error cannot emit a decision to skip pytest."""
    scope_git("commit", "--allow-empty", "-qm", "no parent")

    status, output = _run_scope(tmp_path, scope_environment, "pull_request")
    assert status != 0
    assert not output
