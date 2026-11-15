"""The certified A->D gradient of heat pumps, as a few committed numbers.

``tests/test_compressor_efficiency_defaults.py`` asks whether the model's
COP(D)/COP(A) -- the gain from the coldest to the lightest EN 14825 test point
-- lands inside what certified machines declare. The per-model extraction that
answers it (``validation/data/keymark_en14825_declared.csv``, 18,106 rows,
3.5 MB) is regenerable but stays out of the repository, so that test never ran
in CI or in a fresh clone. This module keeps the part the test needs: quantiles
of the per-model ratio ``cop_D / cop_A`` for each application, in a file small
enough to commit, together with a provenance record that says which corpus the
numbers came from.

The ratio quantile is not the ratio of the per-point quantiles in
``keymark_en14825_summary.csv`` (p10 of D over p10 of A would pair different
models), so it needs its own file.

Source
------
Heat Pump Keymark declared values, republished by hplib (Forschungszentrum
Juelich, MIT licence, doi:10.5281/zenodo.5521597), ``input/csv`` at commit
``125c2ef6``. The extraction rules are those of
:mod:`validation.extraction.keymark_declared` (average climate, the two
EN 14825 applications, all four test points present and plausible).

Run
---
``uv run python -m validation.extraction.keymark_gradient``
(needs the hplib ``input/csv`` under ``validation/evidence/data/hplib/csv/``, or
pass ``--csv-dir``). Writes ``validation/data/keymark_en14825_gradient.csv`` and
``validation/data/provenance/keymark_en14825_gradient.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from . import keymark_declared
from ._pdftext import EvidenceMissing

REPO_ROOT = keymark_declared.REPO_ROOT
OUT_CSV = REPO_ROOT / "validation" / "data" / "keymark_en14825_gradient.csv"
OUT_PROVENANCE = REPO_ROOT / "validation" / "data" / "provenance" / "keymark_en14825_gradient.json"

#: What the ratios were computed from: hplib at this commit, and the size of its
#: ``input/csv`` (the same counts ``validation/registry/sources.yaml`` records).
UPSTREAM = "https://github.com/FZJ-IEK3-VSA/hplib"
UPSTREAM_COMMIT = "125c2ef6c3fc38043368daac1352557fd2a90106"
UPSTREAM_ARCHIVE_SHA256 = "3a8f0cd4b6afbe681cb98a21df4f641284bdf7bfb87bf335bc041ae4d8399791"
EXPECTED_FILES = 2634
EXPECTED_BYTES = 78663865

QUANTILES = (("p10", 0.10), ("p25", 0.25), ("median", 0.50), ("p75", 0.75), ("p90", 0.90))


def csv_fingerprint(csv_dir: Path) -> dict:
    """File count, total bytes and a digest over every ``name`` + SHA-256 pair, sorted by name."""
    digest = hashlib.sha256()
    files = total = 0
    for path in sorted(csv_dir.glob("*.csv")):
        data = path.read_bytes()
        digest.update(f"{path.name}\t{hashlib.sha256(data).hexdigest()}\n".encode())
        files += 1
        total += len(data)
    return {"files": files, "bytes": total, "name_sha256_digest": digest.hexdigest()}


def gradient_quantiles(declared: pd.DataFrame) -> pd.DataFrame:
    """Per application: quantiles of the per-model ratio ``cop_D / cop_A``."""
    rows = []
    for application, group in declared.dropna(subset=["cop_A", "cop_D"]).groupby("application"):
        ratio = group.cop_D / group.cop_A
        rows.append(
            {
                "application": application,
                "n_models": len(ratio),
                **{f"ratio_{name}": float(ratio.quantile(q)) for name, q in QUANTILES},
                "ratio_min": float(ratio.min()),
                "ratio_max": float(ratio.max()),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-dir", type=Path, default=None, help="hplib input/csv (default: the evidence tree)")
    args = parser.parse_args()
    if args.csv_dir is not None:
        keymark_declared.CSV_DIR = args.csv_dir.resolve()
    csv_dir = keymark_declared.CSV_DIR

    try:
        declared = keymark_declared.build("air-to-water")
    except EvidenceMissing as exc:
        raise SystemExit(f"[skip] {exc}") from exc

    fingerprint = csv_fingerprint(csv_dir)
    if (fingerprint["files"], fingerprint["bytes"]) != (EXPECTED_FILES, EXPECTED_BYTES):
        raise SystemExit(
            f"{csv_dir}: {fingerprint['files']} files / {fingerprint['bytes']} bytes, expected the hplib "
            f"{UPSTREAM_COMMIT[:8]} corpus ({EXPECTED_FILES} / {EXPECTED_BYTES}); "
            "the committed quantiles describe that corpus."
        )

    quantiles = gradient_quantiles(declared)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    quantiles.to_csv(OUT_CSV, index=False)

    record = {
        "script": "validation/extraction/keymark_gradient.py",
        "command": "python -m validation.extraction.keymark_gradient",
        "question": "per-model COP(D)/COP(A) of the Heat Pump Keymark air-to-water population, by application",
        "source": {
            "name": "Heat Pump Keymark declared values, republished by hplib (input/csv)",
            "publisher": "Forschungszentrum Juelich, FZJ-IEK3-VSA/hplib",
            "url": UPSTREAM,
            "commit": UPSTREAM_COMMIT,
            "archive_sha256": UPSTREAM_ARCHIVE_SHA256,
            "doi": "10.5281/zenodo.5521597",
            "licence": "MIT (software and bundled documents); the CSV data are CC BY 4.0 in the hplib release",
        },
        "csv_input": fingerprint,
        "selection": "EN 14825 average climate; low (temperature 4/6) and medium (5/7) application; points A-D complete, COP in (1, 15), heat output > 0",
        "declared_records": int(len(declared)),
        "declared_models": int(declared.title.nunique()),
        # The per-model table these ratios come from; ``keymark_declared`` writes the same bytes to the untracked
        # validation/data/keymark_en14825_declared.csv, so a copy of that file can be checked against this hash.
        "declared_table_sha256": hashlib.sha256(declared.to_csv(index=False).encode()).hexdigest(),
        "outputs": ["validation/data/keymark_en14825_gradient.csv"],
        "output_sha256": hashlib.sha256(OUT_CSV.read_bytes()).hexdigest(),
        "run_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    OUT_PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    OUT_PROVENANCE.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(quantiles.to_string(index=False))
    print(f"wrote {OUT_CSV.relative_to(REPO_ROOT)}\nwrote {OUT_PROVENANCE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
