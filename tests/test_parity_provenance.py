"""Provenance of the parity results: recorded per unit, checked when summarised.

``manifest.json`` says what produced the committed numbers. It must carry what
the *unit runs* recorded -- not the state of whichever process happens to build
the summary -- and the summary must refuse units that came from different code.
Everything here runs on three fake catalogues with a stubbed model, so no cycle
is solved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from validation.parity import run
from validation.parity.spec import Catalog, OperatingPoint

FAKES = tuple(
    Catalog(
        slug,
        slug.upper(),
        "Fixture",
        "ASHPB",
        "R32",
        nominal,
        tuple(OperatingPoint(i, t, 45.0, nominal, cop=3.0 + 0.1 * i) for i, t in enumerate((-7.0, 2.0, 7.0), 1)),
    )
    for slug, nominal in (("fake_a", 9.0), ("fake_b", 12.0), ("fake_c", 16.0))
)


@pytest.fixture
def harness(monkeypatch):
    """The real harness, minus the model: deterministic results per point."""

    def load_all(slug=None, *, include_hold=True):
        return [c for c in FAKES if slug is None or c.slug == slug]

    def run_point(model, catalog, point):
        cop = point.target_cop() * (1.0 + 0.01 * point.id + 0.001 * catalog.nominal_capacity_kW)
        return {"failure_reason": "none", "cop_sys [-]": cop, "E_tot [W]": point.q_kW * 1000.0 / cop}

    monkeypatch.setattr(run, "load_all", load_all)
    monkeypatch.setattr(run, "_build_model", lambda *args: object())
    monkeypatch.setattr(run, "_run_point", run_point)
    return run


def _parallel(harness, results: Path) -> None:
    for catalog in FAKES:
        harness.main(["--unit", catalog.slug, "--no-summary", "--results-dir", str(results)])


def _record(results: Path, slug: str) -> Path:
    return results / "provenance" / f"{slug}.json"


def _edit(path: Path, **changes) -> None:
    record = json.loads(path.read_text())
    record.update(changes)
    path.write_text(json.dumps(record))


def test_every_unit_run_records_its_provenance(harness, tmp_path: Path) -> None:
    _parallel(harness, tmp_path)
    for catalog in FAKES:
        record = json.loads(_record(tmp_path, catalog.slug).read_text())
        assert record["slug"] == catalog.slug
        for key in (*run.MUST_AGREE, "git_head", "dirty", "catalog_blob", "run_at"):
            assert key in record
    assert not (tmp_path / "manifest.json").exists(), "--no-summary must not write a manifest"


def test_full_run_and_parallel_runs_write_the_same_results(harness, tmp_path: Path) -> None:
    full, parallel = tmp_path / "full", tmp_path / "parallel"
    harness.main(["--results-dir", str(full)])
    _parallel(harness, parallel)
    harness.main(["--summarize-only", "--results-dir", str(parallel)])

    for name in [f"{c.slug}.csv" for c in FAKES] + ["summary.csv"]:
        assert (full / name).read_bytes() == (parallel / name).read_bytes(), name

    def comparable(directory: Path) -> dict:
        manifest = json.loads((directory / "manifest.json").read_text())
        return {k: v for k, v in manifest.items() if k not in ("run_started", "run_finished")}

    assert comparable(full) == comparable(parallel)


def test_manifest_carries_the_unit_provenance_not_the_summariser(harness, tmp_path: Path) -> None:
    """The case the review found: CSVs from older code summarised by a newer checkout."""
    _parallel(harness, tmp_path)
    for catalog in FAKES:
        _edit(_record(tmp_path, catalog.slug), git_head="0" * 40, src_tmhp_tree="1" * 40, python="3.0.0")
    harness.main(["--summarize-only", "--results-dir", str(tmp_path)])
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["git_head"] == "0" * 40
    assert manifest["src_tmhp_tree"] == "1" * 40
    assert manifest["python"] == "3.0.0"


@pytest.mark.parametrize("key", ["src_tmhp_tree", "validation_parity_tree", "python", "platform"])
def test_summary_refuses_units_from_different_code_or_environment(harness, tmp_path: Path, key: str) -> None:
    _parallel(harness, tmp_path)
    _edit(_record(tmp_path, "fake_b"), **{key: "something-else"})
    with pytest.raises(SystemExit) as excinfo:
        harness.main(["--summarize-only", "--results-dir", str(tmp_path)])
    message = str(excinfo.value)
    assert key in message and "fake_b" in message
    assert "fake_a=" not in message and "fake_c=" not in message, "only the differing unit is named"
    assert not (tmp_path / "manifest.json").exists()


def test_summary_refuses_a_csv_without_provenance(harness, tmp_path: Path) -> None:
    _parallel(harness, tmp_path)
    _record(tmp_path, "fake_c").unlink()
    with pytest.raises(SystemExit, match="no provenance record for fake_c"):
        harness.main(["--summarize-only", "--results-dir", str(tmp_path)])


def test_summary_refuses_a_record_that_disagrees_with_its_csv(harness, tmp_path: Path) -> None:
    _parallel(harness, tmp_path)
    for catalog in FAKES:
        _edit(_record(tmp_path, catalog.slug), coefficient_version="v-other")
    with pytest.raises(SystemExit, match="fake_a.csv says coefficients"):
        harness.main(["--summarize-only", "--results-dir", str(tmp_path)])


def test_a_dirty_unit_cannot_hide_among_clean_ones(harness, tmp_path: Path) -> None:
    _parallel(harness, tmp_path)
    for catalog in FAKES:
        _edit(_record(tmp_path, catalog.slug), dirty=False, dirty_digest=None)
    _edit(_record(tmp_path, "fake_a"), dirty=True, dirty_digest="ab" * 32)
    with pytest.raises(SystemExit, match="dirty_digest.*fake_a"):
        harness.main(["--summarize-only", "--results-dir", str(tmp_path)])


def test_dirty_units_make_a_dirty_manifest(harness, tmp_path: Path) -> None:
    _parallel(harness, tmp_path)
    for catalog in FAKES:
        _edit(_record(tmp_path, catalog.slug), dirty=True, dirty_digest="cd" * 32)
    harness.main(["--summarize-only", "--results-dir", str(tmp_path)])
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["dirty"] is True
    assert manifest["dirty_digest"] == "cd" * 32


def test_units_from_different_commits_with_the_same_code_are_accepted(harness, tmp_path: Path) -> None:
    """A later commit that touched none of the model or harness gives the same numbers."""
    _parallel(harness, tmp_path)
    _edit(_record(tmp_path, "fake_c"), git_head="f" * 40)
    harness.main(["--summarize-only", "--results-dir", str(tmp_path)])
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["git_head"] is None
    assert "f" * 40 in manifest["git_heads"] and len(manifest["git_heads"]) == 2


def test_summary_refuses_leftover_files_without_a_catalogue(harness, tmp_path: Path) -> None:
    _parallel(harness, tmp_path)
    _record(tmp_path, "fake_old").write_text("{}")
    with pytest.raises(SystemExit, match="provenance/fake_old.json"):
        harness.main(["--summarize-only", "--results-dir", str(tmp_path)])


def test_an_interrupted_rerun_never_leaves_an_old_record_vouching_for_a_new_csv(
    harness, tmp_path: Path, monkeypatch
) -> None:
    """A rerun that dies after writing the CSV but before its record must fail closed."""
    _parallel(harness, tmp_path)
    old_record = _record(tmp_path, "fake_b").read_text()
    old_csv = (tmp_path / "fake_b.csv").read_bytes()

    real_replace = run._replace_atomically

    def die_on_the_record(path, write):
        if path.suffix == ".json":
            raise KeyboardInterrupt("interrupted between the CSV and its record")
        real_replace(path, write)

    # A rerun under different code: the new CSV differs from the old one.
    monkeypatch.setattr(
        run,
        "_run_point",
        lambda model, catalog, point: {
            "failure_reason": "none",
            "cop_sys [-]": 9.0,
            "E_tot [W]": 100.0,
        },
    )
    monkeypatch.setattr(run, "_replace_atomically", die_on_the_record)
    with pytest.raises(KeyboardInterrupt):
        harness.main(["--unit", "fake_b", "--no-summary", "--results-dir", str(tmp_path)])
    monkeypatch.setattr(run, "_replace_atomically", real_replace)

    assert (tmp_path / "fake_b.csv").read_bytes() != old_csv, "the new CSV did land"
    assert not _record(tmp_path, "fake_b").exists(), f"the old record survived: {old_record[:60]}"
    assert not list(tmp_path.glob(".*.tmp")) and not list((tmp_path / "provenance").glob(".*.tmp"))
    with pytest.raises(SystemExit, match="no provenance record for fake_b"):
        harness.main(["--summarize-only", "--results-dir", str(tmp_path)])


def test_an_interrupted_csv_write_keeps_the_old_csv_and_drops_its_record(harness, tmp_path: Path, monkeypatch) -> None:
    _parallel(harness, tmp_path)
    old_csv = (tmp_path / "fake_a.csv").read_bytes()

    def die(path, write):
        raise KeyboardInterrupt("interrupted while writing the CSV")

    monkeypatch.setattr(run, "_replace_atomically", die)
    with pytest.raises(KeyboardInterrupt):
        harness.main(["--unit", "fake_a", "--no-summary", "--results-dir", str(tmp_path)])
    assert (tmp_path / "fake_a.csv").read_bytes() == old_csv, "no half-written CSV"
    assert not _record(tmp_path, "fake_a").exists()


def test_committed_parity_figure_states_the_adopted_statistics() -> None:
    """The numbers written on the figure are those of the adopted catalogues.

    Catalogues on hold are drawn but not scored (``scripts/validation/parity_figure.py``).
    The committed SVG keeps its text, so the statement on each panel can be checked
    against ``validation/results`` without rendering anything.
    """
    import pandas as pd

    root = Path(__file__).resolve().parents[1]
    results = root / "validation" / "results"
    points = pd.concat(
        [pd.read_csv(p) for p in sorted(results.glob("*.csv")) if p.name != "summary.csv"], ignore_index=True
    )
    points = points[points.usable]
    svg = " ".join((root / "docs" / "source" / "_static" / "validation_parity.svg").read_text(encoding="utf-8").split())

    for model_class in ("ASHPB", "ASHP"):
        panel = points[points.model_class == model_class]
        scored = panel[panel.status == "adopted"]
        assert f"{scored.slug.nunique()} units, {len(scored)} points" in svg, model_class
        assert f"MAPE {scored.abs_pct_error.mean():.1f} %" in svg, model_class
        assert f"{(scored.abs_pct_error <= 10.0).mean() * 100.0:.0f} % within" in svg, model_class
        pooled = f"{panel.slug.nunique()} units, {len(panel)} points"
        held = panel[panel.status == "hold"]
        if not held.empty:
            assert pooled not in svg, f"{model_class}: catalogues on hold are pooled into the statistics"
            refrigerants = "/".join(sorted(held.refrigerant.unique()))
            assert f"on hold, not scored ({refrigerants}, {held.slug.nunique()} units)" in svg
