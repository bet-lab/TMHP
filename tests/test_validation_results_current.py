"""The committed parity results must be what the current code produces.

``validation/results/`` is what the documentation quotes and plots. It is only
regenerated when someone runs the harness, so a change to the model can leave
it describing code that no longer exists -- which is how the published COP
MAPE came to be a number no version of the library reproduced (#96).

Two checks keep it honest without rerunning all 757 points in CI:

1. ``manifest.json`` names the coefficient set the library ships.
2. A fixed sample of two catalogues -- one air-to-water unit in full, and a
   fixed subset of one air-to-air unit -- is rerun through the harness's own
   :func:`validation.parity.run.run_catalog` and compared with the committed
   CSV column by column (numbers to ``rel=1e-6`` for platform differences,
   everything else exactly).

Any change to model behaviour moves at least one of those points. When this
fails, regenerate with ``scripts/regen-validation.sh`` and update the prose the
docs-alignment tests point to.
"""

from __future__ import annotations

import dataclasses
import io
import json
import math
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS = REPO_ROOT / "validation" / "results"
MANIFEST = RESULTS / "manifest.json"

REGEN_HINT = "모델 동작이 바뀌었다. `scripts/regen-validation.sh` 로 결과를 재생성해라"

#: Catalogue slug -> point ids to rerun (``None``: every point). Chosen for run
#: time: the air-to-water unit is small enough to run whole; the air-to-air
#: subset keeps both modes, mild and cold heating and the top of the cooling
#: grid. Each point is solved independently by the harness, so a subset gives
#: the same rows as the full run.
SAMPLE: dict[str, tuple[int, ...] | None] = {
    "panasonic_wh_uq09he8": None,
    # cooling 20/20, 35/22, 35/27 (rating), 40/32; heating -15/15, -10/20, -5/22, 6/22, 10/24, -15/27, 10/27
    "daikin_rxm20a": (1, 11, 23, 36, 37, 44, 51, 53, 60, 67, 72),
}

pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="parity harness has not been run in this checkout")


def test_manifest_names_the_shipped_coefficients() -> None:
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["coefficient_version"] == COEFFICIENT_VERSION, (
        f"validation/results/manifest.json says {manifest['coefficient_version']!r}, "
        f"the library ships {COEFFICIENT_VERSION!r}. {REGEN_HINT}."
    )
    # The manifest has to say what produced the numbers, not just which
    # coefficients: a dirty tree means git_head alone does not reproduce them.
    for key in ("git_head", "dirty", "src_tmhp_tree", "run_date", "python", "coolprop"):
        assert key in manifest, f"manifest.json lacks {key!r} -- regenerate with scripts/regen-validation.sh"
    assert manifest["dirty"] is False, "committed results were produced from a dirty tree; regenerate from a commit"


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        return a == pytest.approx(b, rel=1e-6, abs=1e-9)
    if pd.isna(a) and pd.isna(b):
        return True
    return a == b


@pytest.mark.parametrize("slug", sorted(SAMPLE))
def test_committed_results_reproduce(slug: str) -> None:
    from validation.parity.run import run_catalog
    from validation.parity.spec import load_all

    (catalog,) = load_all(slug)
    wanted = SAMPLE[slug]
    if wanted is not None:
        points = tuple(p for p in catalog.points if p.id in wanted)
        assert {p.id for p in points} == set(wanted), f"{slug}: sample point ids no longer in the catalogue"
        catalog = dataclasses.replace(catalog, points=points)

    # Through a CSV round trip, so both sides carry the same dtypes.
    fresh = pd.read_csv(io.StringIO(run_catalog(catalog).to_csv(index=False)), float_precision="round_trip")
    committed = pd.read_csv(RESULTS / f"{slug}.csv", float_precision="round_trip")
    committed = committed[committed.point_id.isin(fresh.point_id)].reset_index(drop=True)

    assert list(fresh.columns) == list(committed.columns), f"{slug}: result columns changed. {REGEN_HINT}."
    assert len(fresh) == len(committed), f"{slug}: committed CSV lacks sample points. {REGEN_HINT}."

    mismatches = []
    for i in range(len(fresh)):
        for column in fresh.columns:
            new, old = fresh.at[i, column], committed.at[i, column]
            if not _same(new, old):
                mismatches.append(f"point {fresh.at[i, 'point_id']} {column}: committed {old!r}, now {new!r}")
    assert not mismatches, (
        f"{slug}: {len(mismatches)} value(s) differ from validation/results/{slug}.csv. {REGEN_HINT}.\n  "
        + "\n  ".join(mismatches[:15])
    )
