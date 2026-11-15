"""Show where the round-6 high-lift gate stands today. It asserts nothing.

``validation/gate/high_lift_gate.yaml`` defines the gate and the reported
items. This module computes the current model value for each entry and prints
it next to the catalogue COP (or the estimate), and splits the committed
air-to-water parity results into the layer the gate is judged on and the
vapour-injection units reported beside it. Whether the gate passes is **not**
decided here: it does not pass today, the work on the compressor efficiency
model is what closes it, and the assertion is that work's to add.

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

import pandas as pd
import yaml

import tmhp
from tmhp.standard_conditions import en14511_condenser_water
from validation.parity.run import RESULTS_DIR, run_catalog
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


def gate_call(entry: dict[str, Any]) -> dict[str, Any]:
    """The ``analyze_steady`` call of a gate run: its ``call``, completed with the EN 14511 inlet water and flow.

    ``en14511_leaving_water_C`` names the water leaving the condenser; the
    water enters the standard's rise below it at the flow that carries
    ``Q_ref_tank`` at that rise (:func:`tmhp.standard_conditions.en14511_condenser_water`).
    """
    call = dict(entry["call"])
    leaving = entry.get("en14511_leaving_water_C")
    if leaving is not None:
        call["T_tank_w"], call["m_dot_w"] = en14511_condenser_water(float(leaving), float(call["Q_ref_tank"]))
    return call


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
    """Current value of every entry: ``gate``, ``variants`` (other readings of a gate run, not gated),
    ``vapour_injection`` (catalogue rows outside the model's scope) and ``reported`` (estimates)."""
    gate = load_gate()
    tol = gate["gate"]["tolerance_rel"]
    out: dict[str, list[dict[str, Any]]] = {"gate": [], "variants": [], "vapour_injection": [], "reported": []}
    for entry in gate["gate"]["catalogue_points"]:
        out["gate"].append(evaluate_catalogue_point(entry, tol))
    for entry in gate["gate"]["runs"]:
        out["gate"].append(evaluate_run(entry, gate_call(entry), entry["id"]))
        for variant in entry.get("variants", []):
            out["variants"].append(evaluate_run(entry, variant["call"], f"{entry['id']} [{variant['id']}]"))
    for entry in gate["reported"]["vapour_injection"]["catalogue_points"]:
        out["vapour_injection"].append(evaluate_catalogue_point(entry, tol))
    for entry in gate["reported"]["items"]:
        out["reported"].append(evaluate_reported(entry))
    return out


def parity_layers(results_dir: Path = RESULTS_DIR) -> pd.DataFrame:
    """The committed air-to-water parity results, whole and split by the vapour-injection layer.

    One row per layer: units, points attempted and evaluated, point-weighted
    COP MAPE and bias [%]. ``all`` is the headline the validation pages carry;
    the two layers below it add up to it, point for point. Adopted units only.
    """
    injected = set(load_gate()["reported"]["vapour_injection"]["catalogs"])
    frames = []
    for catalog in load_all():
        if catalog.model_class == "ASHPB" and catalog.status == "adopted":
            frames.append(pd.read_csv(results_dir / f"{catalog.slug}.csv"))
    points = pd.concat(frames, ignore_index=True)
    layers = {
        "all": points,
        "other units (gate layer)": points[~points.slug.isin(injected)],
        "T-CAP M, vapour injection (reported)": points[points.slug.isin(injected)],
    }
    rows = []
    for name, frame in layers.items():
        scored = frame[frame.usable]
        signed = (scored.cop_pred - scored.cop_target) / scored.cop_target * 100.0
        rows.append(
            {
                "layer": name,
                "units": int(frame.slug.nunique()),
                "points": int(len(frame)),
                "evaluated": int(len(scored)),
                "cop_MAPE_pct": float(scored.abs_pct_error.mean()),
                "cop_bias_pct": float(signed.mean()),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    gate = load_gate()
    tol = gate["gate"]["tolerance_rel"]
    report = evaluate_all()

    def show(r: dict[str, Any]) -> None:
        if r["kind"] == "catalogue":
            print(
                f"  {r['id']:<34} catalogue {r['cop_catalogue']:.2f}  model {r['cop_model']:.3f}  "
                f"{r['rel_diff']:+.1%}  {'within' if r['within_tolerance'] else 'outside'} "
                f"(failure_reason={r['failure_reason']})"
            )
        else:
            cop = f"cop {r['cop_model']:.3f}" if r["runs"] else "does not run"
            print(f"  {r['id']:<34} {cop}  failure_reason={r['failure_reason']}  binding={r['capacity_binding']}")

    print(f"Gate (catalogue COP within +-{tol:.0%}, and the default R32 ASHPB runs at A-7/W55, leaving water)")
    for r in report["gate"]:
        show(r)
    print("Other readings of a gate run (not gated)")
    for r in report["variants"]:
        show(r)
    injection = gate["reported"]["vapour_injection"]
    print(f"Reported, not gated: {injection['reason']}")
    print(f"  source: {injection['source']}")
    for r in report["vapour_injection"]:
        show(r)
    print("Committed air-to-water parity results by layer (adopted units)")
    for layer in parity_layers().itertuples():
        print(
            f"  {layer.layer:<37} {layer.units:>2} units  {layer.evaluated:>3}/{layer.points:<3} points  "
            f"MAPE {layer.cop_MAPE_pct:.2f} %  bias {layer.cop_bias_pct:+.2f} %"
        )
    print(f"Reported, not gated (estimate basis: {gate['reported']['estimate_basis']})")
    for r in report["reported"]:
        print(
            f"  {r['id']:<34} model {r['cop_model']:.2f}  estimate {r['estimate_min']:.1f}-{r['estimate_max']:.1f}  "
            f"{'below by ' + format(r['below_estimate_by'], '.2f') if r['below_estimate_by'] else 'inside or above'}"
        )
    print("Nothing is asserted here; see the module docstring.")


if __name__ == "__main__":
    main()
