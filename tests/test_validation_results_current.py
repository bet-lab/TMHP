"""The committed parity results must be what the current code produces.

``validation/results/`` is what the documentation quotes and plots. It is only
regenerated when someone runs the harness, so a change to the model can leave
it describing code that no longer exists -- which is how the published COP
MAPE came to be a number no version of the library reproduced (#96).

Three checks keep it honest:

1. ``manifest.json`` names the coefficient set the library ships.
2. **Every** catalogue is rerun through the harness's own
   :func:`validation.parity.run.run_catalog` -- all 757 points, about 21 s
   serial -- and compared with the committed CSV column by column (numbers to
   ``rel=1e-6`` for platform differences, everything else exactly). A sample
   would leave most points unguarded: the earlier 26-point sample (3.4 %) did
   not see 15 of 17 catalogues.
3. ``manifest.json`` stamps the ``src/tmhp`` tree the results came from. A stamp
   that differs from the tree under test is not an error by itself -- most
   changes to ``src/tmhp`` leave every published number alone, and they must
   not fail unrelated PRs -- but it is only acceptable while the full rerun of
   (2) still reproduces the results. The stamp is then history ("this tree
   produced these numbers"), not a claim about HEAD. Regenerate with
   ``scripts/regen-validation.sh --parity-only`` to move it forward.

Any change to model behaviour moves at least one point. When this fails,
regenerate with ``scripts/regen-validation.sh`` and update the prose the
docs-alignment tests point to.
"""

from __future__ import annotations

import functools
import io
import json
import math
import re
import subprocess
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS = REPO_ROOT / "validation" / "results"
MANIFEST = RESULTS / "manifest.json"

REGEN_HINT = "모델 동작이 바뀌었다. `scripts/regen-validation.sh` 로 결과를 재생성해라"

# One xdist group: the per-catalogue reruns and the stamp test share one worker
# and one cache of fresh results, so nothing is solved twice.
pytestmark = [
    pytest.mark.skipif(not MANIFEST.exists(), reason="parity harness has not been run in this checkout"),
    pytest.mark.xdist_group("parity-rerun"),
]


def test_manifest_names_the_shipped_coefficients() -> None:
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["coefficient_version"] == COEFFICIENT_VERSION, (
        f"validation/results/manifest.json says {manifest['coefficient_version']!r}, "
        f"the library ships {COEFFICIENT_VERSION!r}. {REGEN_HINT}."
    )
    # The manifest has to say what produced the numbers, not just which
    # coefficients: a dirty tree means git_head alone does not reproduce them.
    for key in (
        "git_heads",
        "dirty",
        "src_tmhp_tree",
        "validation_parity_tree",
        "python",
        "coolprop",
        "platform",
        "run_started",
        "run_finished",
    ):
        assert key in manifest, f"manifest.json lacks {key!r} -- regenerate with scripts/regen-validation.sh"
    assert manifest["dirty"] is False, "committed results were produced from a dirty tree; regenerate from a commit"


def test_every_committed_unit_has_a_provenance_record_matching_the_manifest() -> None:
    """The manifest is the units' common provenance; each unit's record must exist and agree."""
    from validation.parity.run import MUST_AGREE
    from validation.parity.spec import load_all

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for catalog in load_all():
        path = RESULTS / "provenance" / f"{catalog.slug}.json"
        assert path.exists(), f"{catalog.slug}: no provenance record. {REGEN_HINT}."
        record = json.loads(path.read_text(encoding="utf-8"))
        for key in MUST_AGREE:
            assert record[key] == manifest[key], f"{catalog.slug}: {key} {record[key]!r} != manifest {manifest[key]!r}"
        assert record["git_head"] in manifest["git_heads"]


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        return a == pytest.approx(b, rel=1e-6, abs=1e-9)
    if pd.isna(a) and pd.isna(b):
        return True
    return a == b


def _slugs() -> list[str]:
    from validation.parity.spec import load_all

    return sorted(catalog.slug for catalog in load_all())


def _mismatches(fresh: pd.DataFrame, committed: pd.DataFrame, slug: str) -> list[str]:
    """Differences between a rerun and the committed unit CSV, one line each."""
    if list(fresh.columns) != list(committed.columns):
        return [f"{slug}: result columns changed ({list(committed.columns)} -> {list(fresh.columns)})"]
    if list(fresh.point_id) != list(committed.point_id):
        return [f"{slug}: point ids differ (committed {len(committed)} rows, rerun {len(fresh)})"]
    found = []
    for i in range(len(fresh)):
        for column in fresh.columns:
            new, old = fresh.at[i, column], committed.at[i, column]
            if not _same(new, old):
                found.append(f"point {fresh.at[i, 'point_id']} {column}: committed {old!r}, now {new!r}")
    return found


@functools.cache
def _rerun(slug: str) -> pd.DataFrame:
    from validation.parity.run import run_catalog
    from validation.parity.spec import load_all

    (catalog,) = load_all(slug)
    # Through a CSV round trip, so both sides carry the same dtypes.
    return pd.read_csv(io.StringIO(run_catalog(catalog).to_csv(index=False)), float_precision="round_trip")


def _committed(slug: str) -> pd.DataFrame:
    return pd.read_csv(RESULTS / f"{slug}.csv", float_precision="round_trip")


@pytest.mark.parametrize("slug", _slugs())
def test_committed_results_reproduce(slug: str) -> None:
    """Every point of every catalogue, not a sample."""
    found = _mismatches(_rerun(slug), _committed(slug), slug)
    assert not found, (
        f"{slug}: {len(found)} value(s) differ from validation/results/{slug}.csv. {REGEN_HINT}.\n  "
        + "\n  ".join(found[:15])
    )


def test_a_changed_cell_or_point_is_detected() -> None:
    """The comparison above must fail on a single altered value, a lost point, and a changed column."""
    slug = "panasonic_wh_uq09he8"
    committed = _committed(slug)
    assert _mismatches(committed.copy(), committed, slug) == []

    numeric = committed.select_dtypes("number").columns.drop("point_id")
    column = next(c for c in numeric if committed[c].notna().all() and (committed[c] != 0).all())

    altered = committed.copy()
    altered.at[3, column] = altered.at[3, column] * 1.001
    (found,) = _mismatches(altered, committed, slug)
    assert f"point {committed.at[3, 'point_id']} {column}" in found

    assert _mismatches(committed.iloc[:-1].reset_index(drop=True), committed, slug)
    assert _mismatches(committed.drop(columns=column), committed, slug)


def _git_tree(path: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", f"HEAD:{path}"], cwd=REPO_ROOT, capture_output=True, text=True, check=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None  # no git, or not a checkout: nothing to compare with
    return out.stdout.strip()


def test_manifest_stamp_is_current_or_the_results_still_reproduce() -> None:
    """``src_tmhp_tree`` is history unless it equals the tree under test.

    Equal: the results came from exactly this code. Different: legitimate for
    a change that moves no number, and then the full rerun must reproduce
    every committed point -- otherwise the stamp would vouch for code that no
    longer produces them (the state #96 found).
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    stamp = manifest["src_tmhp_tree"]
    assert re.fullmatch(r"[0-9a-f]{40}", stamp), f"src_tmhp_tree {stamp!r} is not a git tree hash"

    tree = _git_tree("src/tmhp")
    if tree is None:
        pytest.skip("not a git checkout; the per-catalogue reruns still guard the results")
    if stamp == tree:
        return
    stale = {slug: found for slug in _slugs() if (found := _mismatches(_rerun(slug), _committed(slug), slug))}
    assert not stale, (
        f"manifest.json stamps src/tmhp tree {stamp[:8]} but the tree under test is {tree[:8]}, and the "
        f"results no longer reproduce ({sum(map(len, stale.values()))} value(s) in {len(stale)} catalogue(s): "
        f"{', '.join(sorted(stale))}). {REGEN_HINT}."
    )
