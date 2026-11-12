#!/usr/bin/env bash
# Regenerate every committed validation result from the current code, in parallel.
#
# 1. parity harness, one process per catalogue        -> validation/results/<slug>.csv
# 2. residual decomposition (--jobs N)                  -> validation/data/residual_decomposition.csv
#    EN 14825 trend + coefficient ablation (--jobs N)   -> validation/data/en14825_trend_*.csv,
#                                                          coefficient_ablation_parity.csv
# 3. summary.csv + manifest.json from the unit CSVs (same _summarise as a full run),
#    the site's static data (validation-points.json, validation-summary.json),
#    and the figures the validation pages show:
#    validation_parity, validation_residuals, validation_part_load, validation_en14825_verdict.
#
# The result files are the same as `python -m validation.parity.run` run in one
# process; only the wall time differs. Commit the tree the run started from
# first: manifest.json records git_head, whether the inputs were dirty, the
# src/tmhp tree hash, the coefficient version, the date and the Python/CoolProp
# versions.
#
# Usage:
#   scripts/regen-validation.sh            # 6 workers
#   scripts/regen-validation.sh -j 10
#   scripts/regen-validation.sh -h|--help
#
# Per-job logs go to a fresh directory under $TMPDIR (printed at the start).
# Set PYTHON to override the interpreter command (default: uv run --no-sync python).

set -euo pipefail

usage() {
    sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

jobs=6
while [[ $# -gt 0 ]]; do
    case "$1" in
        -j)
            jobs="${2:?-j needs a number}"
            shift 2
            ;;
        -j*)
            jobs="${1#-j}"
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done
if ! [[ $jobs =~ ^[1-9][0-9]*$ ]]; then
    echo "-j must be a positive integer, got '$jobs'" >&2
    exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

PYTHON="${PYTHON:-uv run --no-sync python}"
logdir="$(mktemp -d "${TMPDIR:-/tmp}/tmhp-regen-validation.XXXXXX")"
export PYTHON logdir

# The EN 14825 study reads the certified population, which is extracted from
# untracked evidence (validation/registry/sources.yaml: data/hplib/csv/). Check
# before spending minutes on the parity runs.
declared=validation/data/keymark_en14825_declared.csv
if [[ ! -f $declared ]]; then
    # shellcheck disable=SC2086
    $PYTHON -m validation.extraction.keymark_declared >"$logdir/keymark-declared.log" 2>&1 || true
fi
if [[ ! -f $declared ]]; then
    echo "missing $declared (gitignored): put hplib's input/csv under" >&2
    echo "validation/evidence/data/hplib/csv/ (see validation/registry/sources.yaml) and rerun;" >&2
    echo "log: $logdir/keymark-declared.log" >&2
    exit 1
fi

if [[ -n "$(git status --porcelain -- src/tmhp validation/parity validation/catalogs pyproject.toml uv.lock)" ]]; then
    echo "warning: model or harness inputs differ from HEAD; manifest.json will say dirty=true" >&2
fi

# run_job NAME ARGS... : run "$PYTHON ARGS..." with its output in $logdir/NAME.log.
run_job() {
    local name="$1"
    shift
    local start=$SECONDS
    # shellcheck disable=SC2086  # PYTHON is a command line, split on purpose
    if $PYTHON "$@" >"$logdir/$name.log" 2>&1; then
        echo "  ok    $name ($((SECONDS - start)) s)"
    else
        echo "  FAIL  $name ($((SECONDS - start)) s) -- see $logdir/$name.log" >&2
        return 1
    fi
}
export -f run_job

total_start=$SECONDS
echo "logs: $logdir"
echo "workers: $jobs"

# Longest catalogues first so the pool drains evenly.
# (A while-read loop rather than mapfile: macOS ships bash 3.2.)
slugs=()
# shellcheck disable=SC2086
slug_list="$($PYTHON -c '
from validation.parity.spec import load_all
for c in sorted(load_all(), key=lambda c: -len(c.points)):
    print(c.slug)
')"
while IFS= read -r slug; do
    [[ -n $slug ]] && slugs+=("$slug")
done <<<"$slug_list"
if [[ ${#slugs[@]} -eq 0 ]]; then
    echo "no catalogues registered under validation/catalogs/" >&2
    exit 1
fi

echo
echo "==> 1/3 parity harness (${#slugs[@]} catalogues)"
phase_start=$SECONDS
for slug in "${slugs[@]}"; do
    echo "parity-$slug -m validation.parity.run --unit $slug --no-summary"
done | xargs -L1 -P "$jobs" bash -c 'run_job "$@"' _
echo "<== 1/3 done ($((SECONDS - phase_start)) s)"

echo
echo "==> 2/3 residual decomposition, EN 14825 trend and coefficient ablation"
phase_start=$SECONDS
run_job residual-decomposition -m validation.analysis.residual_decomposition --jobs "$jobs"
run_job en14825-trend -m validation.analysis.en14825_trend --jobs "$jobs"
echo "<== 2/3 done ($((SECONDS - phase_start)) s)"

echo
echo "==> 3/3 summary, site data, figures"
phase_start=$SECONDS
run_job summarize -m validation.parity.run --summarize-only
run_job site-data -m scripts.data.gen_validation_data
run_job parity_figure -m scripts.validation.parity_figure
run_job residual_figure -m scripts.validation.residual_figure --jobs "$jobs"
run_job part_load_figure -m scripts.validation.part_load_figure
run_job en14825_verdict_figure -m scripts.validation.en14825_verdict_figure
echo "<== 3/3 done ($((SECONDS - phase_start)) s)"

echo
cat "$logdir/summarize.log"
echo
echo "regen-validation: done in $((SECONDS - total_start)) s; logs in $logdir"
