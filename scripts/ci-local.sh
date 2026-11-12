#!/usr/bin/env bash
# Run locally the same checks the GitHub Actions `lint-type` and `test` jobs run.
#
# Use it when GitHub-hosted checks are blocked or queued (spending limit, runner
# outage) and a merge needs evidence, or simply to see a PR's result before pushing.
# Each step prints its command and exit code; a receipt (commit SHA, per-step
# pass/fail) is printed at the end and the script exits non-zero if any step failed.
#
# Usage:
#   scripts/ci-local.sh              # sync + ruff + ruff format + mypy + pytest
#   scripts/ci-local.sh --no-sync    # skip `uv sync` (environment already installed)
#   scripts/ci-local.sh --docs       # also build the Sphinx docs with -W --keep-going
#   scripts/ci-local.sh -h|--help
#
# Keep the step list in sync with .github/workflows/tests.yml.

set -euo pipefail

usage() {
    sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

do_sync=1
do_docs=0
for arg in "$@"; do
    case "$arg" in
        --no-sync) do_sync=0 ;;
        --docs) do_docs=1 ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "unknown option: $arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

lint_paths=(src/tmhp tests scripts validation)

step_names=()
step_codes=()
step_secs=()

# run_step NAME CMD... : run CMD, record its exit code, never abort the script.
run_step() {
    local name="$1"
    shift
    echo
    echo "==> [$name] $*"
    local start=$SECONDS code=0
    "$@" || code=$?
    local elapsed=$((SECONDS - start))
    echo "<== [$name] exit code ${code} (${elapsed}s)"
    step_names+=("$name")
    step_codes+=("$code")
    step_secs+=("$elapsed")
}

if [[ $do_sync -eq 1 ]]; then
    run_step "sync" uv sync --group dev --extra integrations --locked
fi
run_step "ruff-check" uv run ruff check "${lint_paths[@]}"
run_step "ruff-format" uv run ruff format --check "${lint_paths[@]}"
run_step "mypy" uv run mypy src/tmhp
run_step "pytest" uv run pytest -q -n auto --dist loadgroup
if [[ $do_docs -eq 1 ]]; then
    # Mirrors the CI `docs` job (--all-groups installs the docs group).
    if [[ $do_sync -eq 1 ]]; then
        run_step "sync-docs" uv sync --all-groups --locked
    fi
    run_step "docs" make -C docs html SPHINXOPTS="-W --keep-going" DOCS_DATA_PROFILE="ci"
fi

commit="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
dirty=""
if [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
    dirty=" (working tree has uncommitted changes)"
fi

failed=0
echo
echo "=============== ci-local receipt ==============="
echo "commit : ${commit}${dirty}"
echo "python : $(uv run python -c 'import platform; print(platform.python_version())' 2>/dev/null || echo unknown)"
echo "host   : $(uname -sm)"
echo "date   : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
for i in "${!step_names[@]}"; do
    if [[ "${step_codes[$i]}" -eq 0 ]]; then
        status="PASS"
    else
        status="FAIL"
        failed=$((failed + 1))
    fi
    printf '  %-12s %s  exit=%s  %ss\n' "${step_names[$i]}" "$status" "${step_codes[$i]}" "${step_secs[$i]}"
done
if [[ $failed -eq 0 ]]; then
    echo "result : PASS (${#step_names[@]} steps)"
else
    echo "result : FAIL (${failed} of ${#step_names[@]} steps failed)"
fi
echo "================================================"

[[ $failed -eq 0 ]]
