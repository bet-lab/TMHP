"""Emit the default-map coefficient block of ``src/tmhp/compressor_efficiency.py`` from the archive.

Reads   validation/coefficients/ossorio-r290-te-2026-11-21/coefficients.json
Writes  the text between ``# --- BEGIN GENERATED Ossorio map coefficients ---`` and
        ``# --- END GENERATED Ossorio map coefficients ---`` in the library module (formatted with ruff).

Only the numbers inside the markers come from here; ``tests/test_compressor_default_map.py`` checks that the
block equals :func:`block_data` (the archive), value for value.

    uv run python -m validation.compressor_maps.ossorio_te.emit          # rewrite the block
    uv run python -m validation.compressor_maps.ossorio_te.emit --check  # exit 1 if it differs
"""

from __future__ import annotations

import argparse
import json
import pprint
import subprocess
import sys
from typing import Any

from validation.compressor_maps.ossorio_te.fit import ARCHIVE, REPO_ROOT, VERSION

MODULE = REPO_ROOT / "src" / "tmhp" / "compressor_efficiency.py"
BEGIN = "# --- BEGIN GENERATED Ossorio map coefficients ---"
END = "# --- END GENERATED Ossorio map coefficients ---"


def block_data() -> dict[str, Any]:
    """The constants of the block, from the archive."""
    archive = json.loads((ARCHIVE / "coefficients.json").read_text())
    return dict(
        COEFFICIENT_VERSION=archive["version"],
        MAP_COEFFICIENTS={q: tuple(m["coefficients"]) for q, m in archive["maps"].items()},
        MAP_N_REF_RPS=float(archive["n_ref_rps"]),
        MAP_TE_SCALE_K=float(archive["te_scale_K"]),
        MAP_DOMAIN=dict(
            hull_PR_Te=tuple(tuple(v) for v in archive["domain"]["hull_PR_Te"]),
            N_rps=tuple(archive["domain"]["N_rps"]),
        ),
    )


def render() -> str:
    data = block_data()
    lines = [
        BEGIN,
        "# Emitted by ``python -m validation.compressor_maps.ossorio_te.emit`` from",
        f"# validation/coefficients/{VERSION}/coefficients.json.",
        f"COEFFICIENT_VERSION = {data['COEFFICIENT_VERSION']!r}",
        f"MAP_COEFFICIENTS: dict[str, tuple[float, ...]] = {pprint.pformat(data['MAP_COEFFICIENTS'], width=100)}",
        f"MAP_N_REF_RPS = {data['MAP_N_REF_RPS']!r}",
        f"MAP_TE_SCALE_K = {data['MAP_TE_SCALE_K']!r}",
        f"MAP_DOMAIN: dict[str, Any] = {pprint.pformat(data['MAP_DOMAIN'], width=100)}",
        END,
    ]
    return "\n".join(lines)


def _format(text: str) -> str:
    out = subprocess.run(
        ["ruff", "format", "--stdin-filename", str(MODULE), "-"], input=text, capture_output=True, text=True, check=True
    )
    return out.stdout


def rewritten() -> str:
    source = MODULE.read_text()
    head, rest = source.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return _format(head + render() + tail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the module differs from the archive")
    args = parser.parse_args()
    new = rewritten()
    if args.check:
        return 0 if new == MODULE.read_text() else 1
    MODULE.write_text(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
