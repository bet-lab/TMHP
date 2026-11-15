"""The round-6 high-lift gate is defined in a file, and the file points at real catalogue rows.

``validation/gate/high_lift_gate.yaml`` names the operating points the gate is
judged at. These tests pin what the file *says* -- which points, that they are
catalogue rows and not retyped numbers, that the points with no evidence behind
them are reported rather than gated -- and that the evaluation script computes
a value for each. They do **not** assert that the gate passes: it does not
today (13 to 20 % below the catalogue on the three points), and that assertion
belongs to the work that closes it.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest
from validation.gate.evaluate import GATE_FILE, catalogue_point, evaluate_all, load_gate, parity_layers
from validation.parity.run import RESULTS_DIR

#: The gate, as the owner set it on 2026-11-15: A-7/W55 of the three Panasonic
#: WH-UQ units (R410A), machines the single-stage cycle can represent. Spelled
#: out here so that editing the data file alone cannot widen or narrow the gate.
EXPECTED_CATALOGUE_POINTS = {
    ("panasonic_wh_uq09he8", -7, 55),
    ("panasonic_wh_uq12he8", -7, 55),
    ("panasonic_wh_uq16he8", -7, 55),
}
#: Reported beside it: the T-CAP M units (vapour-injection scroll), A-7/W55 and A-7/W65.
INJECTION_CATALOGS = ("panasonic_wh_wxg09me8", "panasonic_wh_wxg12me8", "panasonic_wh_wxg16me8")
EXPECTED_INJECTION_POINTS = {(slug, -7, sink) for slug in INJECTION_CATALOGS for sink in (55, 65)}


def _conditions(entries) -> set:
    return {(e["catalog"], e["condition"]["t_source_C"], e["condition"]["t_sink_C"]) for e in entries}


def _catalogue_entries() -> list:
    gate = load_gate()
    return gate["gate"]["catalogue_points"] + gate["reported"]["vapour_injection"]["catalogue_points"]


def test_gate_is_the_three_catalogue_points_plus_the_default_r32_run() -> None:
    gate = load_gate()
    assert gate["schema_version"] == 2
    assert gate["gate"]["tolerance_rel"] == 0.10
    assert _conditions(gate["gate"]["catalogue_points"]) == EXPECTED_CATALOGUE_POINTS
    (run,) = gate["gate"]["runs"]
    assert run["model"] == "AirSourceHeatPumpBoiler"
    assert run["constructor"]["ref"] == "R32"
    # Leaving water 55 degC: the tank 2.5 K below it, as the catalogue parity runs read a catalogue.
    assert run["call"] == {"T0": -7.0, "T_tank_w": 52.5, "Q_ref_tank": 8000.0}
    assert [v["call"]["T_tank_w"] for v in run["variants"]] == [55.0]


def test_vapour_injection_units_are_reported_with_their_reason_and_stay_in_the_parity_runs() -> None:
    injection = load_gate()["reported"]["vapour_injection"]
    assert tuple(injection["catalogs"]) == INJECTION_CATALOGS
    assert _conditions(injection["catalogue_points"]) == EXPECTED_INJECTION_POINTS
    assert "injection" in injection["reason"] and "single-stage" in injection["reason"]
    assert injection["source"]  # the manufacturer document that says so
    assert not EXPECTED_INJECTION_POINTS & EXPECTED_CATALOGUE_POINTS
    # Reported is not removed: the layers add up to the headline, point for point.
    layers = parity_layers().set_index("layer")
    whole, rest, injected = (layers.loc[name] for name in layers.index)
    assert (rest.units, injected.units) == (whole.units - 3, 3)
    assert rest.points + injected.points == whole.points
    assert rest.evaluated + injected.evaluated == whole.evaluated
    summary = pd.read_csv(RESULTS_DIR / "summary.csv")
    adopted = summary[(summary.model_class == "ASHPB") & (summary.status == "adopted")]
    assert whole.points == adopted.points.sum() and whole.evaluated == adopted.points_usable.sum()
    weighted = (adopted.cop_MAPE_pct * adopted.points_usable).sum() / adopted.points_usable.sum()
    assert whole.cop_MAPE_pct == pytest.approx(weighted, rel=1e-9)


def test_catalogue_entries_point_at_rows_instead_of_retyping_them() -> None:
    for entry in _catalogue_entries():
        assert set(entry) == {"id", "catalog", "point_id", "condition"}, (
            f"{entry['id']}: a COP or duty written here would be a second copy of the catalogue row"
        )
        catalog, point = catalogue_point(entry)
        assert catalog.model_class == "ASHPB"
        assert catalog.status == "adopted"
        assert point.mode == "heating"
        assert (point.t_source_C, point.t_sink_C) == (
            entry["condition"]["t_source_C"],
            entry["condition"]["t_sink_C"],
        ), f"{entry['id']}: catalogue {entry['catalog']} point {entry['point_id']} is no longer that condition"
        assert point.cop is not None, f"{entry['id']}: the row carries no published COP"


def test_ground_and_water_source_items_are_reported_not_gated() -> None:
    gate = load_gate()
    reported = {item["id"]: item for item in gate["reported"]["items"]}
    assert set(reported) == {"gshpb-b0-w35", "wshpb-w10-w35"}
    assert not {i["id"] for i in _catalogue_entries()} & set(reported)
    assert "estimate" in gate["reported"]["estimate_basis"]
    assert reported["gshpb-b0-w35"]["estimate_cop"] == {"min": 4.2, "max": 4.8}
    assert reported["wshpb-w10-w35"]["estimate_cop"] == {"min": 5.0, "max": 6.0}
    # No catalogue exists for either family, so nothing may be referenced as evidence.
    assert all("catalog" not in item for item in reported.values())


def test_evaluation_computes_a_value_for_every_entry_and_matches_the_committed_results() -> None:
    report = evaluate_all()
    assert len(report["gate"]) == 3 + 1  # three catalogue points and the R32 run
    assert len(report["variants"]) == 1  # the same run read with the tank at 55 degC
    assert len(report["vapour_injection"]) == 6
    assert len(report["reported"]) == 2

    entries = {e["id"]: e for e in _catalogue_entries()}
    rows = [r for r in report["gate"] if r["kind"] == "catalogue"] + report["vapour_injection"]
    assert len(rows) == 3 + 6
    for row in rows:
        assert math.isfinite(row["cop_model"]) and math.isfinite(row["rel_diff"]), row
        assert isinstance(row["within_tolerance"], bool)
        entry = entries[row["id"]]
        stored = pd.read_csv(RESULTS_DIR / f"{entry['catalog']}.csv").set_index("point_id").loc[entry["point_id"]]
        # The gate reads what the parity harness reads: same point, same code.
        assert row["cop_catalogue"] == pytest.approx(stored.cop_target, rel=1e-9)
        assert row["cop_model"] == pytest.approx(stored.cop_pred, rel=1e-6)
    for row in report["reported"]:
        assert math.isfinite(row["cop_model"]), row


def test_gate_file_lives_outside_the_parity_provenance_scope() -> None:
    """Editing the gate must not make the committed parity results look dirty (``dirty_scope`` of the manifest)."""
    manifest_scope = ("src/tmhp", "validation/parity", "validation/catalogs", "pyproject.toml", "uv.lock")
    rel = GATE_FILE.relative_to(GATE_FILE.parents[2]).as_posix()
    assert not any(rel == s or rel.startswith(s + "/") for s in manifest_scope), rel
