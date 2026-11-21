"""The wet-coil study of the catalogue cooling points (``validation/wet_coil_shr``, #205).

The study runs the air-to-air cooling rows with the dry and with the wet
indoor coil and compares the model's sensible heat ratio with the published
one (``validation/results/wet_coil_shr_once/``): the five adopted Daikin
units, and the two held Fujitsu units in files of their own. Checked here:

- its inputs: the indoor wet bulb and the ratio read from a row's note, the
  room humidity they give, and the coil temperature a published ratio implies;
- a sample of its rows, rerun, against the committed CSVs (``rel=1e-6``, as the
  other studies in ``tests/test_study_results_current.py``), with the dry pass
  equal to the shipped parity result of the same row and the net-matched
  passes delivering the published capacity net of the indoor fan's heat;
- the band and unit tables and the two headlines, recomputed from the
  committed rows;
- what a run writes: its files and no others in the record, ``null`` for a
  statistic with no row behind it, nothing when the code changed meanwhile;
- the figures ``docs/source/validation/index.rst`` quotes from it.
"""

from __future__ import annotations

import io
import json
import math
import re
from pathlib import Path

import pandas as pd
import pytest
from validation.wet_coil_shr import simulate as study

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "validation" / "results" / "wet_coil_shr_once"

HINT = "모델 동작이 바뀌었다. `python -m validation.wet_coil_shr.simulate` 로 이 연구를 다시 만들어라"


@pytest.fixture(scope="module")
def committed() -> pd.DataFrame:
    return pd.read_csv(OUT / study.CSV_NAME, float_precision="round_trip")


@pytest.fixture(scope="module")
def held() -> pd.DataFrame:
    return pd.read_csv(OUT / study.HELD_CSV_NAME, float_precision="round_trip")


def _round_trip(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(frame.to_csv(index=False)), float_precision="round_trip")


def _mismatches(fresh: pd.DataFrame, old: pd.DataFrame) -> list[str]:
    fresh, old = _round_trip(fresh), _round_trip(old)
    assert list(fresh.columns) == list(old.columns) and len(fresh) == len(old), f"shape changed. {HINT}."
    out = []
    for i in range(len(fresh)):
        for column in fresh.columns:
            new, was = fresh.at[i, column], old.at[i, column]
            if pd.isna(new) and pd.isna(was):
                continue
            numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (new, was))
            same = new == pytest.approx(was, rel=1e-6, abs=1e-9) if numbers else new == was
            if not same:
                out.append(f"row {i} {column}: committed {was!r}, now {new!r}")
    return out


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def test_note_gives_the_indoor_wet_bulb_and_the_ratio():
    assert study.parse_note("WB 19 C, SHR 0.74, wet coil") == (19.0, 0.74)
    assert study.parse_note("WB 14 C, SHR 0.98") == (14.0, 0.98)
    assert study.parse_note("WB 19.4 C, SHR 0.66, wet coil") == (19.4, 0.66)  # a Fujitsu row: one decimal
    assert study.parse_note("SHR 0.67, wet coil") is None  # no indoor wet bulb recorded
    assert study.parse_note("outdoor WB") is None


def test_room_humidity_of_the_rating_condition():
    # ISO 5151 T1 indoor air, 27 °C dry bulb / 19 °C wet bulb: 47 % relative humidity.
    assert study.room_humidity(27.0, 19.0) == pytest.approx(0.4697, abs=5e-4)
    assert study.room_humidity(32.0, 24.0) > study.room_humidity(32.0, 22.0)


def test_apparatus_dew_point_inverts_the_coil_line():
    from tmhp.defrost import humidity_ratio

    rh = study.room_humidity(27.0, 19.0)
    w_in = humidity_ratio(27.0, rh)
    assert study.coil_line_shr(27.0, w_in, 20.0) == 1.0  # above the 14.7 °C dew point: dry
    coil = study.apparatus_dew_point(27.0, rh, 0.74)
    assert coil == pytest.approx(11.06, abs=0.01)
    assert study.coil_line_shr(27.0, w_in, coil) == pytest.approx(0.74, abs=1e-8)
    # A lower ratio needs a colder coil, down to the lowest ratio a straight coil line has at this
    # room state (0.61, at a coil of -3 °C); below that, and for a ratio of 1, there is no such coil.
    assert study.apparatus_dew_point(27.0, rh, 0.65) == pytest.approx(6.58, abs=0.01)
    assert study.coil_line_shr(27.0, w_in, -2.75) == pytest.approx(0.6147, abs=1e-4)
    assert study.coil_line_shr(27.0, w_in, -10.0) > 0.6147 < study.coil_line_shr(27.0, w_in, 3.0)
    assert math.isnan(study.apparatus_dew_point(27.0, rh, 0.60))
    assert math.isnan(study.apparatus_dew_point(27.0, rh, 1.0))


def test_every_cooling_row_has_its_indoor_wet_bulb():
    from validation.parity.spec import load_all

    for slug in study.UNITS:
        (catalog,) = load_all(slug)
        cooling = [p for p in catalog.points if p.mode == "cooling"]
        assert catalog.status == "adopted" and len(cooling) == 36, slug
        assert all(study.parse_note(p.note) is not None for p in cooling), slug
    for slug in study.HELD_UNITS:
        (catalog,) = load_all(slug)
        cooling = [p for p in catalog.points if p.mode == "cooling"]
        assert catalog.status == "hold" and len(cooling) == 72, slug
        assert all(study.parse_note(p.note) is not None for p in cooling), slug
    # No other air-to-air catalogue has cooling rows.
    others = [c for c in load_all() if c.slug not in (*study.UNITS, *study.HELD_UNITS)]
    assert not [c.slug for c in others if any(p.mode == "cooling" for p in c.points)]


#: Indoor dry bulb -> wet bulb [degC] of the Fujitsu cooling grids (design & technical manual,
#: section 4-1, SI block: one wet bulb printed under each of the six dry bulbs; 64/54, 70/60,
#: 75/63, 80/67, 85/71, 90/73 degF in the imperial block).
FUJITSU_INDOOR_WET_BULB = {17.8: 12.2, 21.1: 15.6, 23.9: 17.2, 26.7: 19.4, 29.4: 21.7, 32.2: 22.8}


@pytest.mark.parametrize("slug", study.HELD_UNITS)
def test_fujitsu_rows_carry_the_wet_bulb_the_table_prints(slug):
    from validation.parity.spec import load_all

    (catalog,) = load_all(slug)
    cooling = [p for p in catalog.points if p.mode == "cooling"]
    for point in cooling:
        wet_bulb, _ = study.parse_note(point.note)
        assert wet_bulb == FUJITSU_INDOOR_WET_BULB[point.t_sink_C], point.id
    # The rating condition, 80/67 degF indoor at 95 degF outdoor (point 58 of both files).
    rating = next(p for p in cooling if p.id == 58)
    assert (rating.t_source_C, rating.t_sink_C) == (35.0, 26.7) and study.parse_note(rating.note)[0] == 19.4
    assert study.room_humidity(26.7, 19.4) == pytest.approx(0.507, abs=2e-3)


# ---------------------------------------------------------------------------
# A sample, rerun
# ---------------------------------------------------------------------------


def _sample_mismatches(sample: dict[str, tuple[int, ...]], frame: pd.DataFrame) -> list[str]:
    mismatches = []
    for slug, ids in sample.items():
        fresh = study.run_unit(slug, point_ids=ids)
        old = frame[(frame.slug == slug) & frame.point_id.isin(ids)].reset_index(drop=True)
        mismatches += [f"{slug} {m}" for m in _mismatches(fresh, old)]
        shipped = pd.read_csv(REPO_ROOT / "validation" / "results" / f"{slug}.csv").set_index("point_id")
        for row in fresh.itertuples():
            assert row.cop_dry == pytest.approx(shipped.at[row.point_id, "cop_pred"], rel=1e-6), (slug, row.point_id)
    return mismatches


def test_sample_rows_reproduce_and_the_dry_pass_is_the_shipped_one(committed):
    # A dry row (SHR 0.98), the rating point and the wettest row of the smallest and the largest unit.
    mismatches = _sample_mismatches({"daikin_rxm20a": (1, 23, 36), "daikin_rxm50a": (1, 23, 36)}, committed)
    assert not mismatches, f"validation/results/wet_coil_shr_once: {HINT}.\n  " + "\n  ".join(mismatches[:15])


def test_sample_rows_of_the_held_units_reproduce(held):
    # The coldest outdoor row, the rating point (80/67 degF at 95 degF) and the hottest row of each.
    mismatches = _sample_mismatches({"fujitsu_asuh09lpas": (1, 58, 72), "fujitsu_asuh12lpas": (1, 58, 72)}, held)
    assert not mismatches, f"validation/results/wet_coil_shr_once: {HINT}.\n  " + "\n  ".join(mismatches[:15])


def test_net_matched_pass_delivers_the_published_capacity_net_of_the_fan_heat():
    """FTXM42A point 23: the request the harness passes leaves the room 22 W short of the published 4.2 kW."""
    (row,) = study.run_unit("daikin_rxm42a", point_ids=(23,)).itertuples()
    assert row.q_kW == 4.2 and row.shr_catalog == 0.74
    assert row.net_total_W_wet == pytest.approx(4177.71, abs=0.01) and row.cop_wet == pytest.approx(4.56528, abs=1e-5)
    assert row.q_request_W_wet_net == pytest.approx(4222.29, abs=0.01)
    assert row.net_total_W_wet_net == pytest.approx(4200.0, rel=study.NET_MATCH_TOL)
    assert row.cop_wet_net == pytest.approx(4.55579, abs=1e-5)
    # The COP of a matched row is the published capacity over the model's input.
    assert row.cop_wet_net == pytest.approx(row.net_total_W_wet_net / (row.power_kW_wet_net * 1000.0), rel=1e-9)
    assert row.usable_wet_net and row.failure_wet_net == "none"
    assert row.q_request_W_dry_net == pytest.approx(row.q_kW * 1000.0 + row.fan_W_dry_net, rel=1e-6)
    # The published ratio as the coil's own, and put on the coil's boundary with the fan heat.
    assert row.T_adp_catalog_C == pytest.approx(11.059, abs=1e-3)
    assert row.T_adp_catalog_fan_C == pytest.approx(11.098, abs=2e-3)
    on_coil = study.catalogue_ratio_on_the_coil(0.74, 4200.0, row.fan_W_wet_net)
    assert on_coil == pytest.approx((0.74 * 4200.0 + row.fan_W_wet_net) / (4200.0 + row.fan_W_wet_net), rel=1e-12)
    assert 0.74 < on_coil < 0.742


def test_tables_and_headlines_are_those_of_the_committed_rows(committed, held):
    assert len(committed) == 180 and list(dict.fromkeys(committed.slug)) == list(study.UNITS)
    assert len(held) == 144 and list(dict.fromkeys(held.slug)) == list(study.HELD_UNITS)
    assert set(committed.status) == {"adopted"} and set(held.status) == {"hold"}
    assert list(committed.columns) == list(held.columns)
    assert study.shipped_dry_gate(committed) <= 1e-6 and study.shipped_dry_gate(held) <= 1e-6
    for name, fresh in (
        (study.BANDS_NAME, study.by_band(committed)),
        (study.UNITS_NAME, study.by_unit(committed)),
        (study.HELD_BANDS_NAME, study.by_band(held)),
        (study.HELD_UNITS_NAME, study.by_unit(held)),
    ):
        old = pd.read_csv(OUT / name, float_precision="round_trip")
        assert not _mismatches(fresh, old), name
    text = (OUT / study.MANIFEST_NAME).read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text
    manifest = json.loads(text)
    assert manifest["units"] == list(study.UNITS) and manifest["held_units"] == list(study.HELD_UNITS)
    assert manifest["cooling_rows"] == {
        **dict.fromkeys(study.UNITS, 36),
        **dict.fromkeys(study.HELD_UNITS, 72),
    }
    for key, frame in (("headline", committed), ("headline_held", held)):
        numbers = study.headline(frame)
        assert set(numbers) == set(manifest[key]), key
        for name, value in numbers.items():
            stored = manifest[key][name]
            if isinstance(value, float) and math.isnan(value):
                assert stored is None, (key, name)  # undefined: null, not NaN
            else:
                assert value == pytest.approx(stored, rel=1e-6, abs=1e-9), (key, name)


def test_record_vouches_for_the_script_that_made_the_files():
    """The record names a commit that holds the script as it is now, run with nothing uncommitted."""
    from validation.parity.run import _git

    record = json.loads((OUT / "provenance.json").read_text(encoding="utf-8"))
    assert record["dirty"] is False and record["script"] == study.SCRIPT
    assert record["command"] == study.COMMAND  # the whole study, into its own folder
    assert record["study_script_uncommitted"] is False, f"made with an uncommitted script. {HINT}."
    # The files the run wrote, and all of them: nothing else the folder may hold.
    names = (
        study.CSV_NAME,
        study.BANDS_NAME,
        study.UNITS_NAME,
        study.HELD_CSV_NAME,
        study.HELD_BANDS_NAME,
        study.HELD_UNITS_NAME,
        study.MANIFEST_NAME,
    )
    assert record["outputs"] == sorted(f"validation/results/wet_coil_shr_once/{name}" for name in names)
    assert sorted(p.name for p in OUT.iterdir()) == sorted((*names, "provenance.json"))
    blob = _git("hash-object", study.SCRIPT)
    if not blob:
        pytest.skip("git is not available")
    assert record["study_script_blob"] == blob, f"the study script changed after the files were made. {HINT}."
    # Where the recorded commit is in this clone (not in a shallow one), it holds that same script.
    at_commit = _git("rev-parse", f"{record['git_head']}:{study.SCRIPT}")
    assert at_commit in ("", blob), "the record's commit holds another version of the study script"


def test_what_the_study_found(committed):
    """The direction of the findings the documentation states (adopted units)."""
    numbers = study.headline(committed)
    # Every row runs in both passes, and all but one coil is wet.
    assert numbers["rows_scored_dry"] == numbers["rows_scored_wet"] == 180
    assert numbers["rows_model_coil_dry_in_wet_pass"] == 1
    # No row is wet enough for the coil line to reach the saturation curve.
    assert numbers["rows_leaving_air_saturated"] == 0
    # Eight rows publish a ratio of 1.00 (no coil temperature to compare); none is below what a coil line can give.
    assert numbers["rows_published_ratio_one"] == 8 and numbers["rows_with_catalogue_adp"] == 172
    assert numbers["rows_published_ratio_below_coil_line_minimum"] == 0
    # The band "0.95 and above" is not a dry band: 17 of its 25 rows publish a ratio below 1.
    assert numbers["rows_published_ratio_at_least_095"] == 25
    assert numbers["rows_published_ratio_at_least_095"] - numbers["rows_published_ratio_one"] == 17
    assert numbers["rows_published_ratio_at_least_095_with_latent_heat"] == 24
    assert 0.0 < numbers["model_coil_above_catalogue_adp_K_mean"] < 2.0
    # The ratio follows the catalogue's, a little high on average ...
    assert numbers["shr_correlation"] > 0.9 and 0.0 < numbers["shr_error_mean"] < 0.05
    assert numbers["shr_abs_error_mean"] < 0.06
    # ... and the wet coil is the more optimistic one over these rows: a warmer evaporator, less input
    # on all but one of them, a higher COP.
    assert numbers["T_evap_rise_wet_minus_dry_K_mean"] > 2.0 and numbers["power_change_wet_vs_dry_pct_mean"] < -8.0
    assert numbers["rows_wet_draws_more_than_dry"] == 1
    assert abs(numbers["cop_bias_pct_dry"]) < 3.0 < 10.0 < numbers["cop_bias_pct_wet"]
    assert numbers["bias_shr_correlation_wet"] < numbers["bias_shr_correlation_dry"] < 0.0
    bands = study.by_band(committed).set_index("shr_band")
    assert bands.cop_bias_pct_wet["below 0.75"] > bands.cop_bias_pct_wet["0.75 to 0.95"] > 0.0
    assert bands.cop_bias_pct_wet["0.95 and above"] < 0.0
    # The indoor conductance trades the ratio against the COP: neither moves without the other.
    assert numbers["cop_bias_pct_wet_ua50"] < numbers["cop_bias_pct_wet"] < numbers["cop_bias_pct_wet_ua200"]
    assert bands.shr_error_wet_ua50["all"] < -0.05 and bands.shr_error_wet_ua200["all"] > 0.05


def test_what_matching_the_net_capacity_changes(committed):
    numbers = study.headline(committed)
    # The harness's request leaves the room about 20 W (0.6 %) short of the published capacity,
    # one row by more than 1 %.
    assert numbers["net_delivery_short_of_published_W_mean_wet"] == pytest.approx(20.35, abs=0.01)
    assert numbers["rows_net_delivery_short_by_more_than_1pct_wet"] == 1
    # Matched, the request is that much larger and the COP a quarter of a percent lower: the finding stands.
    assert numbers["request_above_published_W_mean_wet_net"] == pytest.approx(20.3, abs=0.2)
    assert -0.5 < numbers["cop_change_net_matched_vs_request_pct_mean_wet"] < -0.1
    assert numbers["scored_dry_net"] == numbers["scored_wet_net"] == 180
    assert 0.3 < numbers["cop_bias_pct_dry"] - numbers["cop_bias_pct_dry_net"] < 0.8
    assert 0.2 < numbers["cop_bias_pct_wet"] - numbers["cop_bias_pct_wet_net"] < 0.8
    matched = committed[committed.usable_wet_net]
    target = matched.q_kW * 1000.0
    assert (abs(matched.net_total_W_wet_net - target) <= study.NET_MATCH_TOL * target).all()
    # The fan heat moves the coil temperature a published ratio implies by a few hundredths of a kelvin.
    assert 0.01 < numbers["catalogue_adp_fan_minus_adp_K_mean"] < 0.1


def test_what_the_held_units_show(committed, held):
    """Reported apart: held catalogues whose power and capacity boundaries the manual does not state."""
    numbers = study.headline(held)
    units = study.by_unit(held).set_index("slug")
    assert numbers["rows_scored_dry"] == numbers["rows_scored_wet"] == 144
    # The dry bias of the shipped (held) results, and a wet coil that adds to it.
    assert units.cop_bias_pct_dry["fujitsu_asuh09lpas"] == pytest.approx(27.35, abs=0.01)
    assert units.cop_bias_pct_wet["fujitsu_asuh09lpas"] == pytest.approx(34.0, abs=0.05)
    assert units.cop_bias_pct_dry["fujitsu_asuh12lpas"] == pytest.approx(38.49, abs=0.01)
    assert units.cop_bias_pct_wet["fujitsu_asuh12lpas"] == pytest.approx(48.7, abs=0.05)
    # No row publishes a ratio of 0.95 or more: that band is empty, and its statistics undefined.
    bands = study.by_band(held).set_index("shr_band")
    assert bands.points["0.95 and above"] == 0 and math.isnan(bands.cop_bias_pct_wet["0.95 and above"])
    # The ratio is not reproduced: the table's is nearly constant where the model's follows the conditions.
    assert numbers["shr_correlation"] < 0.5 < 0.9 < study.headline(committed)["shr_correlation"]
    assert numbers["rows_published_ratio_below_coil_line_minimum"] > 0


# ---------------------------------------------------------------------------
# What a run writes
# ---------------------------------------------------------------------------


def _blob(data: bytes) -> str:
    import hashlib

    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 -- the blob id of the VCS


def test_main_runs_the_file_as_it_is_and_records_exactly_that(tmp_path):
    """The blob recorded is the blob of the source whose code did the work, whatever module was loaded before.

    A copy of the script whose ``run`` only reports which version it is and what it would record. The
    copy is loaded, then the file is replaced by a second version: ``main`` of the *loaded* module
    reads the file once, and both the code that runs and the blob it reports are the second version's.
    """
    import importlib.util

    source = (REPO_ROOT / study.SCRIPT).read_text(encoding="utf-8")
    probe = (
        "\n\ndef run(argv=None):\n"
        "    Path(argv[0]).write_text(json.dumps({'version': VERSION, **script_identity()}))\n"
        "\n\nVERSION = %r\n"
    )
    first, second = ((source + probe % version).encode() for version in ("first", "second"))
    copy = tmp_path / "simulate.py"
    copy.write_bytes(first)
    spec = importlib.util.spec_from_file_location("wet_coil_shr_copy", copy)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    assert loaded.VERSION == "first"

    out = tmp_path / "report.json"
    loaded.main([str(out)])
    assert json.loads(out.read_text()) == {"version": "first", "study_script_blob": _blob(first)}

    copy.write_bytes(second)
    loaded.main([str(out)])
    assert json.loads(out.read_text()) == {"version": "second", "study_script_blob": _blob(second)}
    assert _blob(first) != _blob(second) and loaded.VERSION == "first"


def test_a_module_loaded_by_the_import_system_records_nothing(tmp_path):
    """Its source bytes are not known, so ``run`` called on it directly refuses before computing."""
    assert study._SOURCE is None
    with pytest.raises(SystemExit, match="has to be run through main"):
        study.script_identity()
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="has to be run through main"):
        study.run(["--out-dir", str(out)])
    assert not out.exists()


@pytest.mark.parametrize("option", [["--unit", "daikin_rxm20a"], ["--point-id", "1"]])
def test_the_command_line_runs_the_whole_study_only(capsys, option):
    """Part of the study is an argument of ``run_unit``, not an option: no partial run is recorded as the study."""
    with pytest.raises(SystemExit):
        study.main(option)
    assert "unrecognized arguments" in capsys.readouterr().err


#: One adopted and one held unit, two rows each: what the short runs below compute.
SHORT = {"daikin_rxm20a": (1, 23), "fujitsu_asuh09lpas": (1, 2)}


def _short_run(monkeypatch, calls: list[str]):
    """Make ``study.run`` compute :data:`SHORT` only, logging when the code state is read and when the model runs."""
    real_run_unit, real_state = study.run_unit, study.run_state

    def run_unit(slug):
        calls.append("run")
        return real_run_unit(slug, point_ids=SHORT[slug])

    def run_state():
        calls.append("state")
        return real_state()

    monkeypatch.setattr(study, "_SOURCE", (REPO_ROOT / study.SCRIPT).read_bytes())
    monkeypatch.setattr(study, "UNITS", ("daikin_rxm20a",))
    monkeypatch.setattr(study, "HELD_UNITS", ("fujitsu_asuh09lpas",))
    monkeypatch.setattr(study, "cooling_point_ids", lambda slug: list(SHORT[slug]))
    monkeypatch.setattr(study, "run_unit", run_unit)
    monkeypatch.setattr(study, "run_state", run_state)
    return real_state


OUTPUT_NAMES = (
    study.CSV_NAME,
    study.BANDS_NAME,
    study.UNITS_NAME,
    study.HELD_CSV_NAME,
    study.HELD_BANDS_NAME,
    study.HELD_UNITS_NAME,
    study.MANIFEST_NAME,
)


def test_run_writes_its_files_and_names_exactly_those(monkeypatch, tmp_path, capsys):
    calls: list[str] = []
    real_state = _short_run(monkeypatch, calls)
    (tmp_path / "left_over.csv").write_text("not,of,this,run\n")  # a file the folder already held
    study.run(["--out-dir", str(tmp_path)])
    capsys.readouterr()
    # The code state is read before the first model run and checked again before anything is written.
    assert calls == ["state", "run", "run", "state"], calls
    record = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    for key, value in real_state().items():
        assert record[key] == value, key
    assert record["study_script_blob"] == _blob((REPO_ROOT / study.SCRIPT).read_bytes())
    assert record["outputs"] == sorted(str(tmp_path.resolve() / name) for name in OUTPUT_NAMES)
    assert record["command"] == f"{study.COMMAND} --out-dir {tmp_path.resolve()}"
    assert record["model_settings"]["units"] == ["daikin_rxm20a"]
    assert record["model_settings"]["held_units"] == ["fujitsu_asuh09lpas"]
    rows = pd.read_csv(tmp_path / study.CSV_NAME)
    assert list(zip(rows.slug, rows.point_id, strict=True)) == [("daikin_rxm20a", 1), ("daikin_rxm20a", 23)]
    held_rows = pd.read_csv(tmp_path / study.HELD_CSV_NAME)
    assert list(zip(held_rows.slug, held_rows.point_id, strict=True)) == [
        ("fujitsu_asuh09lpas", 1),
        ("fujitsu_asuh09lpas", 2),
    ]

    # A statistic with no row behind it is null in the manifest, which is strict JSON.
    def strict(constant):
        raise AssertionError(f"{constant} in the manifest")

    manifest = json.loads((tmp_path / study.MANIFEST_NAME).read_text(encoding="utf-8"), parse_constant=strict)
    assert manifest["cooling_rows"] == {"daikin_rxm20a": 2, "fujitsu_asuh09lpas": 2}
    held_numbers = manifest["headline_held"]
    assert held_numbers["rows_published_ratio_at_least_095"] == 0 and held_numbers["cop_bias_pct_wet"] is not None
    assert None in held_numbers.values()  # the two held rows publish one ratio: no correlation with it
    bands = pd.read_csv(tmp_path / study.HELD_BANDS_NAME).set_index("shr_band")
    assert bands.points["0.95 and above"] == 0 and math.isnan(bands.cop_bias_pct_wet["0.95 and above"])


def test_run_writes_nothing_when_a_cooling_row_was_not_computed(monkeypatch, tmp_path, capsys):
    """A catalogue with a cooling row the run did not compute: the files would not be the study's, so no record."""
    calls: list[str] = []
    _short_run(monkeypatch, calls)
    monkeypatch.setattr(study, "cooling_point_ids", lambda slug: [*SHORT[slug], 36])
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="rows computed are not the cooling rows of the catalogues; nothing written"):
        study.run(["--out-dir", str(out)])
    capsys.readouterr()
    assert not out.exists()


def test_run_writes_nothing_when_the_code_changed_while_it_ran(monkeypatch, tmp_path, capsys):
    calls: list[str] = []
    real_state = _short_run(monkeypatch, calls)

    def changing_state():
        calls.append("state")
        state = real_state()
        # The model is edited between the start of the run and its end.
        return state if calls.count("state") == 1 else {**state, "dirty": True, "dirty_digest": "0" * 64}

    monkeypatch.setattr(study, "run_state", changing_state)
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="changed while the study ran; nothing written"):
        study.run(["--out-dir", str(out)])
    capsys.readouterr()
    assert not out.exists()


# ---------------------------------------------------------------------------
# docs/source/validation/index.rst, "Cooling and latent load"
# ---------------------------------------------------------------------------


def _signed(value: float, digits: int = 1) -> str:
    return f"{value:+.{digits}f}".replace("-", "−")


def _section() -> str:
    text = (REPO_ROOT / "docs" / "source" / "validation" / "index.rst").read_text(encoding="utf-8")
    match = re.search(r"^Cooling and latent load\n=+\n(.*?)(?=^[^\n]+\n=+\n)", text, flags=re.M | re.S)
    assert match, "index.rst has no 'Cooling and latent load' section"
    return match.group(1)


def _tables() -> list[list[list[str]]]:
    """The list tables of the section, each as rows of six cells (header row dropped)."""
    tables = []
    for block in _section().split(".. list-table::")[1:]:
        body = block.split("\n\n", 2)[1]  # after the options, up to the first blank line that ends the table
        cells = [c.strip() for c in re.findall(r"^\s+(?:\* -|-) (.*)$", body, flags=re.M)]
        tables.append([cells[i : i + 6] for i in range(6, len(cells), 6)])
    return tables


def test_docs_table_matches_the_study(committed):
    rows = _tables()[0]
    bands = study.by_band(committed)
    assert [row[0] for row in rows] == list(bands.shr_band)
    for row, band in zip(rows, bands.itertuples(), strict=True):
        expected = [
            band.shr_band,
            str(band.points),
            f"{band.shr_catalog:.2f}",
            f"{band.shr_model_wet:.2f}",
            f"{_signed(band.cop_bias_pct_dry)} %",
            f"{_signed(band.cop_bias_pct_wet)} %",
        ]
        assert row == expected, (row, expected)


def test_docs_table_of_the_held_units_matches_the_study(held):
    rows = _tables()[1]
    units = study.by_unit(held)
    assert [row[0] for row in rows] == list(units.unit)
    for row, unit in zip(rows, units.itertuples(), strict=True):
        expected = [
            unit.unit,
            str(unit.points),
            f"{unit.shr_catalog:.2f}",
            f"{unit.shr_model_wet:.2f}",
            f"{_signed(unit.cop_bias_pct_dry)} %",
            f"{_signed(unit.cop_bias_pct_wet)} %",
        ]
        assert row == expected, (row, expected)


def test_docs_prose_quotes_the_study(committed, held):
    flat = " ".join(_section().split())
    numbers = study.headline(committed)
    units = study.by_unit(committed).set_index("slug")
    bands = study.by_band(committed).set_index("shr_band")
    small, large = units.loc["daikin_rxm20a"], units.loc["daikin_rxm50a"]
    gap = (committed.T_evap_C_wet - committed.T_adp_catalog_C).groupby(committed.slug).mean()
    below_one = numbers["rows_published_ratio_at_least_095"] - numbers["rows_published_ratio_one"]
    fewer = numbers["rows_scored_wet"] - numbers["rows_wet_draws_more_than_dry"]
    held_numbers = study.headline(held)
    held_low = held[held.shr_catalog < 0.75]
    for sentence in (
        f"gives {numbers['shr_model_net_mean']:.2f} against the published {numbers['shr_catalog_mean']:.2f} on average, "
        f"with a mean absolute difference of {numbers['shr_abs_error_mean']:.3f} and a correlation of "
        f"*r* = {numbers['shr_correlation']:.2f} over the {numbers['rows_scored_wet']} rows",
        f"single rows differ by {_signed(numbers['shr_error_min'], 2)} to {_signed(numbers['shr_error_max'], 2)}",
        f"{abs(small.shr_error_wet):.2f} low for the 2.0 kW unit ({small.shr_model_wet:.2f} against "
        f"{small.shr_catalog:.2f}), {large.shr_error_wet:.2f} high for the 5.0 kW unit ({large.shr_model_wet:.2f} "
        f"against {large.shr_catalog:.2f})",
        f"over the {numbers['rows_with_catalogue_adp']} rows that publish a ratio below 1, the model's evaporating "
        f"temperature is on average {numbers['model_coil_above_catalogue_adp_K_mean']:.1f} K above the coil "
        f"temperature at which its own coil line gives the published ratio "
        f"({_signed(numbers['model_coil_above_catalogue_adp_K_min'])} to "
        f"{_signed(numbers['model_coil_above_catalogue_adp_K_max'])} K by row; {-gap['daikin_rxm20a']:.1f} K below it "
        f"for the 2.0 kW unit, {gap['daikin_rxm50a']:.1f} K above for the 5.0 kW unit)",
        f"{below_one} of its {numbers['rows_published_ratio_at_least_095']} rows publish a ratio below 1, and the "
        f"model's coil condenses water on {numbers['rows_published_ratio_at_least_095_with_latent_heat']} of them",
        f"runs its evaporator {numbers['T_evap_rise_wet_minus_dry_K_mean']:.1f} K warmer than the dry one for the "
        f"same total heat and draws {-numbers['power_change_wet_vs_dry_pct_mean']:.1f} % less — less on {fewer} of "
        f"the {numbers['rows_scored_wet']} rows",
        f"The bias goes from {_signed(numbers['cop_bias_pct_dry'])} % to {_signed(numbers['cop_bias_pct_wet'])} % and "
        f"the mean absolute error from {numbers['cop_MAPE_pct_dry']:.1f} % to {numbers['cop_MAPE_pct_wet']:.1f} %",
        f"{_signed(bands.cop_bias_pct_wet['below 0.75'])} % where the published ratio is below 0.75",
        f"goes from *r* = {_signed(numbers['bias_shr_correlation_dry'], 2)} to "
        f"{_signed(numbers['bias_shr_correlation_wet'], 2)}",
        f"halved the wet-coil bias is {_signed(numbers['cop_bias_pct_wet_ua50'])} % and the ratio "
        f"{bands.shr_model_wet_ua50['all']:.2f} ({abs(bands.shr_error_wet_ua50['all']):.2f} low); doubled, "
        f"{_signed(numbers['cop_bias_pct_wet_ua200'])} % and {bands.shr_model_wet_ua200['all']:.2f} "
        f"({bands.shr_error_wet_ua200['all']:.2f} high)",
        # The capacity the comparison runs at.
        f"receives {numbers['net_delivery_short_of_published_W_mean_wet']:.1f} W "
        f"({numbers['net_delivery_short_of_published_pct_mean_wet']:.2f} %) less than published on average, and more "
        f"than 1 % less on {numbers['rows_net_delivery_short_by_more_than_1pct_wet']} row",
        f"({numbers['request_above_published_W_mean_wet_net']:.1f} W more on average) the COP is "
        f"{-numbers['cop_change_net_matched_vs_request_pct_mean_wet']:.2f} % lower: the bias is "
        f"{_signed(numbers['cop_bias_pct_dry_net'])} % dry and {_signed(numbers['cop_bias_pct_wet_net'])} % wet, on "
        f"all {numbers['scored_wet_net']} rows",
        f"raises it by {numbers['catalogue_adp_fan_minus_adp_K_mean']:.2f} K on average",
        # The held units.
        f"the {len(held)} cooling rows of the two units are run wet as well",
        f"it lies between {held_low.shr_catalog.min():.2f} and {held_low.shr_catalog.max():.2f} on {len(held_low)} of "
        f"the {len(held)} rows",
        f"(*r* = {held_numbers['shr_correlation']:.2f}, mean absolute difference "
        f"{held_numbers['shr_abs_error_mean']:.3f}), and {held_numbers['rows_published_ratio_below_coil_line_minimum']} "
        f"rows publish a ratio lower than a straight coil line can give",
    ):
        assert sentence in flat, f"index.rst lacks {sentence!r}"
    assert numbers["scored_dry_net"] == numbers["scored_wet_net"] == len(committed)
    # The same figure where the residuals are read.
    page = " ".join((REPO_ROOT / "docs" / "source" / "validation" / "index.rst").read_text(encoding="utf-8").split())
    assert f"at {_signed(numbers['cop_bias_pct_wet'])} % over the same rows" in page
