#!/usr/bin/env bash
# Regenerate every committed validation result from the current code, in parallel.
#
# 1. parity harness, one process per catalogue, into a fresh staging directory;
#    summary.csv + manifest.json from those unit CSVs and their provenance
#    records (the summary refuses units that disagree on code or environment);
#    then the staged set replaces validation/results/<slug>.csv,
#    provenance/<slug>.json, summary.csv and manifest.json as a whole
# 2. residual decomposition (--jobs N)                  -> validation/data/residual_decomposition.csv
#    EN 14825 trend + coefficient ablation (--jobs N)   -> validation/data/en14825_trend_*.csv,
#                                                          coefficient_ablation_parity.csv,
#                                                          en14825_seasonal_trend_a2w_manifest.json
# 3. the site's static data (validation-points.json, validation-summary.json),
#    Gate A (validation/results/gate_a/: rated-point errors and residual bins,
#    computed from the unit CSVs of step 1 alone),
#    and the figures the validation pages show:
#    validation_parity, validation_residuals, validation_part_load, validation_en14825_verdict.
#
# The result files are the same as `python -m validation.parity.run` run in one
# process; only the wall time differs. Staging means no unit CSV or provenance
# record left over from an earlier run can enter the summary. Commit the tree
# the run starts from first: each unit's provenance record, and the manifest,
# record git_head, whether the model and harness were dirty, the src/tmhp and
# validation/parity tree hashes, the coefficient version, the Python, CoolProp
# and platform, and when the units ran.
#
# Usage:
#   scripts/regen-validation.sh            # 6 workers
#   scripts/regen-validation.sh -j 10
#   scripts/regen-validation.sh --parity-only   # step 1 only: unit CSVs, provenance, summary, manifest
#   scripts/regen-validation.sh -h|--help
#
# Per-job logs go to a fresh directory under $TMPDIR (printed at the start).
# Set PYTHON to override the interpreter command (default: uv run --no-sync python).

set -euo pipefail

usage() {
    sed -n '2,34p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

jobs=6
parity_only=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --parity-only)
            parity_only=1
            shift
            ;;
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
if [[ $parity_only -eq 0 && ! -f $declared ]]; then
    # shellcheck disable=SC2086
    $PYTHON -m validation.extraction.keymark_declared >"$logdir/keymark-declared.log" 2>&1 || true
fi
if [[ $parity_only -eq 0 && ! -f $declared ]]; then
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
echo "==> 1/3 parity harness (${#slugs[@]} catalogues) and summary"
phase_start=$SECONDS
staging="$logdir/results"
mkdir -p "$staging"
for slug in "${slugs[@]}"; do
    echo "parity-$slug -m validation.parity.run --unit $slug --no-summary --results-dir $staging"
done | xargs -L1 -P "$jobs" bash -c 'run_job "$@"' _
run_job summarize -m validation.parity.run --summarize-only --results-dir "$staging"
# Install the staged set as a whole. Only the top-level unit CSVs, summary,
# manifest and provenance records are replaced; study folders under results/
# are left alone.
results=validation/results
rm -f "${results:?}"/*.csv "${results:?}/manifest.json"
rm -rf "${results:?}/provenance"
cp "$staging"/*.csv "$staging/manifest.json" "$results/"
cp -R "$staging/provenance" "$results/provenance"
echo "<== 1/3 done ($((SECONDS - phase_start)) s)"
if [[ $parity_only -eq 1 ]]; then
    echo
    cat "$logdir/summarize.log"
    echo
    echo "regen-validation --parity-only: done in $((SECONDS - total_start)) s; logs in $logdir"
    echo "(studies, site data and figures were not regenerated)"
    exit 0
fi

echo
echo "==> 2/3 residual decomposition, EN 14825 trend and coefficient ablation"
phase_start=$SECONDS
run_job residual-decomposition -m validation.analysis.residual_decomposition --jobs "$jobs"
# The seasonal-trend wrapper runs validation.analysis.en14825_trend and stamps
# the coefficient version into validation/data/en14825_seasonal_trend_a2w_manifest.json.
run_job en14825-trend -m validation.en14825_seasonal_trend.air_to_water --jobs "$jobs"
echo "<== 2/3 done ($((SECONDS - phase_start)) s)"

echo
echo "==> 3/3 site data, figures"
phase_start=$SECONDS
run_job site-data -m scripts.data.gen_validation_data
run_job gate-a -m validation.parity.rated_point
run_job parity_figure -m scripts.validation.parity_figure
run_job residual_figure -m scripts.validation.residual_figure --jobs "$jobs"
run_job part_load_figure -m scripts.validation.part_load_figure
run_job en14825_verdict_figure -m scripts.validation.en14825_verdict_figure
echo "<== 3/3 done ($((SECONDS - phase_start)) s)"

echo
cat "$logdir/summarize.log"
echo
echo "regen-validation: done in $((SECONDS - total_start)) s; logs in $logdir"
