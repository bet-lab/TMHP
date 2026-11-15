"""Show where the round-6 high-lift gate stands today. It asserts nothing.

``validation/gate/high_lift_gate.yaml`` defines the gate and the reported
items. This module computes the current model value for each entry and prints
it next to the catalogue COP (or the estimate). Whether the gate passes is
**not** decided here: it does not pass today, the stages after this one close
it, and the assertion is theirs to add.

Run
---
``uv run python -m validation.gate.evaluate``  (about 2 s)
"""

from __future__ import annotations

import dataclasses
import math
import warnings
from pathlib import Path
from typing import Any

import yaml

import tmhp
from validation.parity.run import run_catalog
from validation.parity.spec import load_all

GATE_FILE = Path(__file__).with_name("high_lift_gate.yaml")


def load_gate() -> dict[str, Any]:
    return yaml.safe_load(GATE_FILE.read_text(encoding="utf-8"))


def catalogue_point(entry: dict[str, Any]):
    """The catalogue and the operating point an entry names (``KeyError`` if the id is gone)."""
    (catalog,) = load_all(entry["catalog"])
    point = next(p for p in catalog.points if p.id == entry["point_id"])
    return catalog, point


def evaluate_catalogue_point(entry: dict[str, Any], tolerance_rel: float) -> dict[str, Any]:
    catalog, point = catalogue_point(entry)
    row = run_catalog(dataclasses.replace(catalog, points=(point,))).iloc[0]
    cop_catalogue = float(row.cop_target)
    cop_model = float(row.cop_pred)
    scored = bool(row.usable) and math.isfinite(cop_model)
    rel = cop_model / cop_catalogue - 1.0 if scored else float("nan")
    return {
        "id": entry["id"],
        "kind": "catalogue",
        "cop_catalogue": cop_catalogue,
        "cop_model": cop_model,
        "rel_diff": rel,
        "failure_reason": str(row.failure_reason),
        "within_tolerance": scored and abs(rel) <= tolerance_rel,
    }


def _run(model_name: str, constructor: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = getattr(tmhp, model_name)(**constructor)
        result = model.analyze_steady(**call, return_dict=True)
    assert isinstance(result, dict)
    return result


def evaluate_run(entry: dict[str, Any], call: dict[str, Any], label: str) -> dict[str, Any]:
    result = _run(entry["model"], entry["constructor"], call)
    failure = str(result.get("failure_reason", "none"))
    cop = float(result.get("cop_sys [-]", float("nan")))
    return {
        "id": label,
        "kind": "runs",
        "runs": failure == "none",
        "cop_model": cop if failure == "none" else float("nan"),
        "failure_reason": failure,
        "capacity_binding": result.get("capacity_binding"),
    }


def evaluate_reported(entry: dict[str, Any]) -> dict[str, Any]:
    result = _run(entry["model"], entry["constructor"], entry["call"])
    cop = float(result.get("cop_sys [-]", float("nan")))
    lo, hi = entry["estimate_cop"]["min"], entry["estimate_cop"]["max"]
    return {
        "id": entry["id"],
        "kind": "reported",
        "cop_model": cop,
        "estimate_min": lo,
        "estimate_max": hi,
        "below_estimate_by": lo - cop if cop < lo else 0.0,
        "failure_reason": str(result.get("failure_reason", "none")),
    }


def evaluate_all() -> dict[str, list[dict[str, Any]]]:
    gate = load_gate()
    tol = gate["gate"]["tolerance_rel"]
    out: dict[str, list[dict[str, Any]]] = {"gate": [], "reported": []}
    for entry in gate["gate"]["catalogue_points"]:
        out["gate"].append(evaluate_catalogue_point(entry, tol))
    for entry in gate["gate"]["runs"]:
        out["gate"].append(evaluate_run(entry, entry["call"], entry["id"]))
        for variant in entry.get("variants", []):
            out["gate"].append(evaluate_run(entry, variant["call"], f"{entry['id']} [{variant['id']}]"))
    for entry in gate["reported"]["items"]:
        out["reported"].append(evaluate_reported(entry))
    return out


def main() -> None:
    gate = load_gate()
    tol = gate["gate"]["tolerance_rel"]
    report = evaluate_all()
    print(f"Gate (catalogue COP within +-{tol:.0%}, and the default R32 ASHPB runs at A-7/W55)")
    for r in report["gate"]:
        if r["kind"] == "catalogue":
            print(
                f"  {r['id']:<34} catalogue {r['cop_catalogue']:.2f}  model {r['cop_model']:.3f}  "
                f"{r['rel_diff']:+.1%}  {'within' if r['within_tolerance'] else 'outside'} "
                f"(failure_reason={r['failure_reason']})"
            )
        else:
            cop = f"cop {r['cop_model']:.3f}" if r["runs"] else "does not run"
            print(f"  {r['id']:<34} {cop}  failure_reason={r['failure_reason']}  binding={r['capacity_binding']}")
    print(f"Reported, not gated (estimate basis: {gate['reported']['estimate_basis']})")
    for r in report["reported"]:
        print(
            f"  {r['id']:<34} model {r['cop_model']:.2f}  estimate {r['estimate_min']:.1f}-{r['estimate_max']:.1f}  "
            f"{'below by ' + format(r['below_estimate_by'], '.2f') if r['below_estimate_by'] else 'inside or above'}"
        )
    print("Nothing is asserted here; see the module docstring.")


if __name__ == "__main__":
    main()
