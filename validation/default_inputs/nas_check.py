"""Check that every archived source original is on the lab NAS with the recorded SHA-256.

Each ``sources.json`` entry carries ``nas_path`` (relative to the NAS dataset root) and
``archive87_dataset_id`` (the #87 ledger dataset, or null when the file was added only for #86).
A single-file source is compared with its ``sha256``; a folder source with its ``files`` list.

    uv run python -m validation.default_inputs.nas_check [--root /mnt/datasets]

Read-only. The NAS is mounted read-only over NFS; uploads go through the lab SMB remote
(see ``nas-transfer-20261113.json``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from validation.default_inputs.archive import sha

HERE = Path(__file__).parent
ARCHIVES = ("compressor_displacement", "fan_airflow")


def check(root: Path) -> dict:
    report: dict = {"root": str(root), "sources": 0, "files": 0, "problems": []}
    for archive in ARCHIVES:
        for source in json.loads((HERE / archive / "sources.json").read_text()):
            report["sources"] += 1
            sid, rel = source["source_id"], source.get("nas_path")
            if not rel:
                report["problems"].append(f"{archive}/{sid}: no nas_path")
                continue
            if source.get("files"):
                expected = {Path(f["path"]).name: f["sha256"] for f in source["files"]}
            else:
                expected = {Path(rel).name: source["sha256"]}
                rel = str(Path(rel).parent)
            folder = root / rel
            for name, digest in expected.items():
                report["files"] += 1
                path = folder / name
                if not path.is_file():
                    report["problems"].append(f"{archive}/{sid}: missing {path}")
                elif sha(path) != digest:
                    report["problems"].append(f"{archive}/{sid}: SHA-256 differs for {path}")
    report["all_checks_passed"] = not report["problems"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("/mnt/datasets"))
    report = check(parser.parse_args().root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["all_checks_passed"] else 1)


if __name__ == "__main__":
    main()
