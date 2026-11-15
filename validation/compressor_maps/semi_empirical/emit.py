"""Emit the semi-empirical coefficient block of ``src/tmhp/compressor_efficiency.py`` from the archive.

Reads   validation/coefficients/semi-empirical-2026-11-15/coefficients.json
        validation/coefficients/semi-empirical-2026-11-15/rotary_speed_variants.json
Writes  the text between ``# --- BEGIN GENERATED semi-empirical coefficients ---`` and
        ``# --- END GENERATED semi-empirical coefficients ---`` in the library module (formatted with ruff).

The library module is hand-written around the block; only the numbers inside the markers come from here, so a
reviewer can diff the evidence and the code. ``tests/test_compressor_semi_empirical.py`` checks that the block
equals :func:`block_data` (the archive), value for value.

    uv run python -m validation.compressor_maps.semi_empirical.emit          # rewrite the block
    uv run python -m validation.compressor_maps.semi_empirical.emit --check  # exit 1 if it differs
"""

from __future__ import annotations

import argparse
import json
import pprint
import subprocess
import sys
from typing import Any

from validation.compressor_maps.schema import REPO_ROOT
from validation.compressor_maps.semi_empirical.run import ARCHIVE, VERSION

MODULE = REPO_ROOT / "src" / "tmhp" / "compressor_efficiency.py"
BEGIN = "# --- BEGIN GENERATED semi-empirical coefficients ---"
END = "# --- END GENERATED semi-empirical coefficients ---"
#: Package names of the rotary speed-shape variants (``variants.py``: ``b`` Ossorio-anchored, the default; ``a``
#: Shao-informed, the first archive's arrangement). The default is listed first.
VARIANT_NAMES = {"b": "ossorio_anchored", "a": "shao_informed"}
ROTARY_TYPES = ("rotary_twin_inverter", "rotary_single_fixed")


def _speed(candidate_or_coordinate: str) -> str:
    """``abs`` or ``rel`` from an archive speed string (``n = N/50 rev/s`` or a candidate name ``...:abs``)."""
    s = candidate_or_coordinate
    if s.endswith(":abs") or "N/50" in s:
        return "abs"
    if s.endswith(":rel") or "N_rated" in s:
        return "rel"
    raise ValueError(f"unknown speed coordinate {s!r}")


def block_data() -> dict[str, Any]:
    """The constants of the generated block, from the archive files."""
    coeffs = json.loads((ARCHIVE / "coefficients.json").read_text())
    var_file = json.loads((ARCHIVE / "rotary_speed_variants.json").read_text())
    variants = var_file["variants"]
    types: dict[str, Any] = {}
    for name, t in coeffs["types"].items():
        oi = t["eta_oi"]
        # own level only where the archive's rule uses it (enough rating points of that refrigerant)
        by_ref = {r: v for r, v in oi["level"].get("by_refrigerant", {}).items() if v.get("used_as_level")}
        split = t["split"]
        if split["selected"] != "F":
            raise SystemExit(
                f"{name}: the library implements the loss-share split F, archive selected {split['selected']}"
            )
        types[name] = {
            "family": t["family_form"],
            "level_form": oi["level_form"],
            "speed": _speed(oi["speed_coordinate"]),
            "parameters": dict(oi["parameters"]),
            "level": oi["level_a"],
            "level_by_refrigerant": {r: v["a_median"] for r, v in by_ref.items()},
            "eta_v": {
                "c0": t["eta_v"]["c0"],
                "parameters": dict(t["eta_v"]["parameters"]),
                "speed": _speed(t["eta_v"]["speed_coordinate"]),
            },
            "loss_share": split["F"]["coefficients"][0],
            "hull_PR_N": tuple(tuple(v) for v in t["hull_PR_N"]),
            "provisional": t["provisional"],
        }
    # The default variant must be the archive's rotary fit (run.py uses its fit object); refuse to emit otherwise.
    default = var_file["default"]
    if next(iter(VARIANT_NAMES)) != default:
        raise SystemExit(f"VARIANT_NAMES must list the archive's default variant {default!r} first")
    dv = variants[default]
    for name in ROTARY_TYPES:
        if dv["parameters"] != types[name]["parameters"]:
            raise SystemExit(f"rotary_speed_variants.json variant {default} differs from coefficients.json ({name})")
    if abs(dv["level_twin"] - types["rotary_twin_inverter"]["level"]) > 1e-6:
        raise SystemExit(f"rotary_speed_variants.json variant {default} twin level differs from coefficients.json")
    if abs(dv["level_single_fixed"] - types["rotary_single_fixed"]["level"]) > 1e-6:
        raise SystemExit(f"rotary_speed_variants.json variant {default} single level differs from coefficients.json")
    for name, key in (
        ("rotary_twin_inverter", "level_twin_by_refrigerant"),
        ("rotary_single_fixed", "level_single_fixed_by_refrigerant"),
    ):
        if dv.get(key, {}) != types[name]["level_by_refrigerant"]:
            raise SystemExit(f"rotary_speed_variants.json variant {default} {key} differs from coefficients.json")
    speed_variants = {}
    for key in VARIANT_NAMES:
        v = variants[key]
        speed_variants[VARIANT_NAMES[key]] = {
            "terms": tuple(v["terms"]),
            "parameters": dict(v["parameters"]),
            "level": {"rotary_twin_inverter": v["level_twin"], "rotary_single_fixed": v["level_single_fixed"]},
            "level_by_refrigerant": {
                "rotary_twin_inverter": dict(v["level_twin_by_refrigerant"]),
                "rotary_single_fixed": dict(v.get("level_single_fixed_by_refrigerant", {})),
            },
        }
    drive = coeffs["drive"]
    return {
        "SEMI_EMPIRICAL_VERSION": VERSION,
        "SEMI_EMPIRICAL_TYPES": types,
        "SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS": speed_variants,
        "SEMI_EMPIRICAL_DRIVE": {"lam": drive["lam_median"], "beta": drive["beta"], "N_ref_rps": 60.0},
    }


def render() -> str:
    data = block_data()
    lines = [
        BEGIN,
        "# Emitted by ``python -m validation.compressor_maps.semi_empirical.emit`` from",
        f"# validation/coefficients/{VERSION}/coefficients.json and rotary_speed_variants.json.",
    ]
    for name, value in data.items():
        annot = "" if isinstance(value, str) else ": dict[str, Any]"
        lines.append(f"{name}{annot} = {pprint.pformat(value, width=110, sort_dicts=False)}")
    lines.append(END)
    return "\n".join(lines) + "\n"


def _format(text: str) -> str:
    out = subprocess.run(
        ["ruff", "format", "--stdin-filename", str(MODULE), "-"],
        input=text,
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return out.stdout


def rewritten(source: str) -> str:
    head, sep, rest = source.partition(BEGIN)
    if not sep:
        raise SystemExit(f"{MODULE}: no {BEGIN!r} marker")
    _, sep, tail = rest.partition(END)
    if not sep:
        raise SystemExit(f"{MODULE}: no {END!r} marker")
    return _format(head + render() + tail.lstrip("\n"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit 1 if the module's block differs from the archive")
    args = ap.parse_args(argv)
    source = MODULE.read_text()
    new = rewritten(source)
    if args.check:
        if new != source:
            print(f"{MODULE.relative_to(REPO_ROOT)}: generated block differs from the archive", file=sys.stderr)
            return 1
        return 0
    MODULE.write_text(new)
    print(f"rewrote the generated block of {MODULE.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
