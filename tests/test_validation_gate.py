"""The round-6 high-lift gate is defined in a file, and the file points at real catalogue rows.

``validation/gate/high_lift_gate.yaml`` names the operating points the gate is
judged at. These tests pin what the file *says* -- which points, that they are
catalogue rows and not retyped numbers, that the points with no evidence behind
them are reported rather than gated -- and that the evaluation script computes
a value for each. They do **not** assert that the gate passes: it does not
today (about -20 % on the three catalogue points), and that assertion belongs to
the stages that close it.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest
from validation.gate.evaluate import GATE_FILE, catalogue_point, evaluate_all, load_gate
from validation.parity.run import RESULTS_DIR

#: The gate, as the plan states it: Panasonic WH-WXG09ME8 A-7/W55 and A-7/W65,
#: WH-UQ09HE8 A-7/W55. Spelled out here so that editing the data file alone
#: cannot widen or narrow the gate.
EXPECTED_CATALOGUE_POINTS = {
    ("panasonic_wh_wxg09me8", -7, 55),
    ("panasonic_wh_wxg09me8", -7, 65),
    ("panasonic_wh_uq09he8", -7, 55),
}


def test_gate_is_the_three_catalogue_points_plus_the_default_r32_run() -> None:
    gate = load_gate()
    assert gate["schema_version"] == 1
    assert gate["gate"]["tolerance_rel"] == 0.10
    points = {
        (e["catalog"], e["condition"]["t_source_C"], e["condition"]["t_sink_C"])
        for e in gate["gate"]["catalogue_points"]
    }
    assert points == EXPECTED_CATALOGUE_POINTS
    (run,) = gate["gate"]["runs"]
    assert run["model"] == "AirSourceHeatPumpBoiler"
    assert run["constructor"]["ref"] == "R32"
    assert run["call"]["T0"] == -7.0


def test_catalogue_entries_point_at_rows_instead_of_retyping_them() -> None:
    for entry in load_gate()["gate"]["catalogue_points"]:
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
    assert not {i["id"] for i in gate["gate"]["catalogue_points"]} & set(reported)
    assert "estimate" in gate["reported"]["estimate_basis"]
    assert reported["gshpb-b0-w35"]["estimate_cop"] == {"min": 4.2, "max": 4.8}
    assert reported["wshpb-w10-w35"]["estimate_cop"] == {"min": 5.0, "max": 6.0}
    # No catalogue exists for either family, so nothing may be referenced as evidence.
    assert all("catalog" not in item for item in reported.values())


def test_evaluation_computes_a_value_for_every_entry_and_matches_the_committed_results() -> None:
    report = evaluate_all()
    assert len(report["gate"]) == 3 + 2  # three catalogue points, the R32 run and its leaving-water variant
    assert len(report["reported"]) == 2

    committed = pd.read_csv(RESULTS_DIR / "panasonic_wh_wxg09me8.csv")
    committed_uq = pd.read_csv(RESULTS_DIR / "panasonic_wh_uq09he8.csv")
    by_slug = {"panasonic_wh_wxg09me8": committed, "panasonic_wh_uq09he8": committed_uq}
    entries = {e["id"]: e for e in load_gate()["gate"]["catalogue_points"]}
    for row in (r for r in report["gate"] if r["kind"] == "catalogue"):
        assert math.isfinite(row["cop_model"]) and math.isfinite(row["rel_diff"]), row
        assert isinstance(row["within_tolerance"], bool)
        entry = entries[row["id"]]
        stored = by_slug[entry["catalog"]].set_index("point_id").loc[entry["point_id"]]
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
