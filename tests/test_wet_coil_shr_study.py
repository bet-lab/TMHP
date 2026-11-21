"""The wet-coil study of the catalogue cooling points (``validation/wet_coil_shr``, #205).

The study runs the Daikin cooling rows with the dry and with the wet indoor
coil and compares the model's sensible heat ratio with the published one
(``validation/results/wet_coil_shr_once/``). Checked here:

- its inputs: the indoor wet bulb and the ratio read from a row's note, the
  room humidity they give, and the coil temperature a published ratio implies;
- a sample of its rows, rerun, against the committed CSV (``rel=1e-6``, as the
  other studies in ``tests/test_study_results_current.py``), with the dry pass
  equal to the shipped parity result of the same row;
- the band and unit tables and the headline, recomputed from the committed rows;
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
    assert study.parse_note("SHR 0.67, wet coil") is None  # a Fujitsu row: no indoor wet bulb
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


def test_every_studied_row_has_its_wet_bulb_and_the_others_do_not():
    from validation.parity.spec import load_all

    for slug in study.UNITS:
        cooling = [p for p in load_all(slug)[0].points if p.mode == "cooling"]
        assert len(cooling) == 36 and all(study.parse_note(p.note) is not None for p in cooling), slug
    for slug in study.UNITS_WITHOUT_WET_BULB:
        cooling = [p for p in load_all(slug)[0].points if p.mode == "cooling"]
        assert len(cooling) == 72 and all(study.parse_note(p.note) is None for p in cooling), slug
    # No other air-to-air catalogue has cooling rows.
    others = [c for c in load_all() if c.slug not in (*study.UNITS, *study.UNITS_WITHOUT_WET_BULB)]
    assert not [c.slug for c in others if any(p.mode == "cooling" for p in c.points)]


# ---------------------------------------------------------------------------
# A sample, rerun
# ---------------------------------------------------------------------------


def test_sample_rows_reproduce_and_the_dry_pass_is_the_shipped_one(committed):
    # A dry row (SHR 0.98), the rating point and the wettest row of the smallest and the largest unit.
    sample = {"daikin_rxm20a": (1, 23, 36), "daikin_rxm50a": (1, 23, 36)}
    mismatches = []
    for slug, ids in sample.items():
        fresh = study.run_unit(slug, point_ids=ids)
        old = committed[(committed.slug == slug) & committed.point_id.isin(ids)].reset_index(drop=True)
        mismatches += [f"{slug} {m}" for m in _mismatches(fresh, old)]
        shipped = pd.read_csv(REPO_ROOT / "validation" / "results" / f"{slug}.csv").set_index("point_id")
        for row in fresh.itertuples():
            assert row.cop_dry == pytest.approx(shipped.at[row.point_id, "cop_pred"], rel=1e-6), (slug, row.point_id)
    assert not mismatches, f"validation/results/wet_coil_shr_once: {HINT}.\n  " + "\n  ".join(mismatches[:15])


def test_tables_and_headline_are_those_of_the_committed_rows(committed):
    assert len(committed) == 180 and set(committed.slug) == set(study.UNITS)
    assert study.shipped_dry_gate(committed) <= 1e-6
    for name, fresh in ((study.BANDS_NAME, study.by_band(committed)), (study.UNITS_NAME, study.by_unit(committed))):
        old = pd.read_csv(OUT / name, float_precision="round_trip")
        assert not _mismatches(fresh, old), name
    manifest = json.loads((OUT / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["units"] == list(study.UNITS) and manifest["partial_run"] is False
    assert manifest["points_run"] == {slug: study.cooling_point_ids(slug) for slug in study.UNITS}
    assert manifest["points_run"] == study.points_run(committed)
    record = json.loads((OUT / "provenance.json").read_text(encoding="utf-8"))
    assert record["model_settings"]["points_run"] == manifest["points_run"]
    for key, value in study.headline(committed).items():
        assert value == pytest.approx(manifest["headline"][key], rel=1e-6, abs=1e-9), key


def test_record_vouches_for_the_script_that_made_the_files():
    """The record names a commit that holds the script as it is now, run with nothing uncommitted."""
    from validation.parity.run import _git

    record = json.loads((OUT / "provenance.json").read_text(encoding="utf-8"))
    assert record["dirty"] is False and record["script"] == study.SCRIPT
    assert record["command"] == study.COMMAND  # the whole study, into its own folder
    assert record["study_script_uncommitted"] is False, f"made with an uncommitted script. {HINT}."
    blob = _git("hash-object", study.SCRIPT)
    if not blob:
        pytest.skip("git is not available")
    assert record["study_script_blob"] == blob, f"the study script changed after the files were made. {HINT}."
    # Where the recorded commit is in this clone (not in a shallow one), it holds that same script.
    at_commit = _git("rev-parse", f"{record['git_head']}:{study.SCRIPT}")
    assert at_commit in ("", blob), "the record's commit holds another version of the study script"


def test_what_the_study_found(committed):
    """The direction of the findings the documentation states."""
    numbers = study.headline(committed)
    # Every row runs in both passes, and all but one coil is wet.
    assert numbers["rows_scored_dry"] == numbers["rows_scored_wet"] == 180
    assert numbers["rows_model_coil_dry_in_wet_pass"] == 1
    # No row is wet enough for the coil line to reach the saturation curve.
    assert numbers["rows_leaving_air_saturated"] == 0
    # Eight rows publish a ratio of 1.00 (no coil temperature to compare); none is below what a coil line can give.
    assert numbers["rows_published_ratio_one"] == 8 and numbers["rows_with_catalogue_adp"] == 172
    assert numbers["rows_published_ratio_below_coil_line_minimum"] == 0
    assert 0.0 < numbers["model_coil_above_catalogue_adp_K_mean"] < 2.0
    # The ratio follows the catalogue's, a little high on average ...
    assert numbers["shr_correlation"] > 0.9 and 0.0 < numbers["shr_error_mean"] < 0.05
    assert numbers["shr_abs_error_mean"] < 0.06
    # ... and the wet coil is the more optimistic one: a warmer evaporator, less input, a higher COP.
    assert numbers["T_evap_rise_wet_minus_dry_K_mean"] > 2.0 and numbers["power_change_wet_vs_dry_pct_mean"] < -8.0
    assert abs(numbers["cop_bias_pct_dry"]) < 3.0 < 10.0 < numbers["cop_bias_pct_wet"]
    assert numbers["bias_shr_correlation_wet"] < numbers["bias_shr_correlation_dry"] < 0.0
    bands = study.by_band(committed).set_index("shr_band")
    assert bands.cop_bias_pct_wet["below 0.75"] > bands.cop_bias_pct_wet["0.75 to 0.95"] > 0.0
    assert bands.cop_bias_pct_wet["0.95 and above"] < 0.0
    # No single indoor conductance fits both the ratio and the COP.
    assert numbers["cop_bias_pct_wet_ua50"] < numbers["cop_bias_pct_wet"] < numbers["cop_bias_pct_wet_ua200"]
    assert bands.shr_error_wet_ua50["all"] < -0.05 and bands.shr_error_wet_ua200["all"] > 0.05


def _blob(data: bytes) -> str:
    import hashlib

    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 -- git's blob id


ONE_ROW = ["--unit", "daikin_rxm20a", "--point-id", "1"]


def test_main_records_the_blob_of_the_bytes_it_ran(tmp_path, capsys):
    """A real one-row run through ``main``: the record names this file's blob, read once and run."""
    from validation.parity.run import _git

    study.main([*ONE_ROW, "--out-dir", str(tmp_path)])
    capsys.readouterr()
    record = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    source = (REPO_ROOT / study.SCRIPT).read_bytes()
    assert record["study_script_blob"] == _blob(source)
    blob = _git("hash-object", study.SCRIPT)
    if blob:
        assert record["study_script_blob"] == blob
    committed = _git("rev-parse", f"{record['git_head']}:{study.SCRIPT}")
    assert record["study_script_uncommitted"] is (record["study_script_blob"] != committed)
    assert len(pd.read_csv(tmp_path / study.CSV_NAME)) == 1
    manifest = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["partial_run"] is True and manifest["units"] == ["daikin_rxm20a"]
    # ... and which rows: read off the rows computed, not off the request.
    assert manifest["points_run"] == record["model_settings"]["points_run"] == {"daikin_rxm20a": [1]}
    # The record says what was run: one row of one unit, into another folder -- not the whole study.
    assert record["command"] == f"{study.COMMAND} --unit daikin_rxm20a --point-id 1 --out-dir {tmp_path.resolve()}"
    assert record["model_settings"]["units"] == ["daikin_rxm20a"]


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
        study.run([*ONE_ROW, "--out-dir", str(out)])
    assert not out.exists()


def test_recorded_command_carries_the_options_of_the_run(tmp_path):
    assert study.run_command(None, None, None) == study.COMMAND == "python -m validation.wet_coil_shr.simulate"
    assert study.run_command(("daikin_rxm20a", "daikin_rxm50a"), None, None) == (
        f"{study.COMMAND} --unit daikin_rxm20a --unit daikin_rxm50a"
    )
    assert study.run_command(None, (1, 23), None) == f"{study.COMMAND} --point-id 1 --point-id 23"
    spaced = tmp_path / "a folder"
    assert study.run_command(None, None, spaced) == f"{study.COMMAND} --out-dir '{spaced}'"


@pytest.mark.parametrize(
    "selection, says",
    [
        (["--unit", "daikin_rxm20a", "--point-id", "999"], "--point-id 999: not a cooling row of daikin_rxm20a"),
        # Point 37 is a heating row: it would select nothing and still be recorded.
        (["--unit", "daikin_rxm20a", "--point-id", "37"], "--point-id 37: not a cooling row of daikin_rxm20a"),
        (["--point-id", "1", "--point-id", "37"], "--point-id 37: not a cooling row of daikin_rxm20a"),
    ],
)
def test_a_point_id_that_selects_nothing_is_refused_before_anything_runs(tmp_path, capsys, selection, says):
    out = tmp_path / "out"
    with pytest.raises(SystemExit):
        study.main([*selection, "--out-dir", str(out)])
    assert says in capsys.readouterr().err
    assert not out.exists()


def test_points_run_are_read_off_the_rows_computed(tmp_path, capsys):
    study.main(
        [
            "--unit",
            "daikin_rxm20a",
            "--unit",
            "daikin_rxm50a",
            "--point-id",
            "36",
            "--point-id",
            "1",
            "--out-dir",
            str(tmp_path),
        ]
    )
    capsys.readouterr()
    rows = pd.read_csv(tmp_path / study.CSV_NAME)
    assert list(zip(rows.slug, rows.point_id, strict=True)) == [
        ("daikin_rxm20a", 1),
        ("daikin_rxm20a", 36),
        ("daikin_rxm50a", 1),
        ("daikin_rxm50a", 36),
    ]
    manifest = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    record = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    expected = {"daikin_rxm20a": [1, 36], "daikin_rxm50a": [1, 36]}
    assert manifest["points_run"] == record["model_settings"]["points_run"] == expected
    assert manifest["units"] == ["daikin_rxm20a", "daikin_rxm50a"] and manifest["partial_run"] is True


def test_partial_run_cannot_replace_the_committed_study(capsys):
    with pytest.raises(SystemExit):
        study.main(ONE_ROW)  # no --out-dir: the default is the committed study's folder
    assert "does not replace the committed study" in capsys.readouterr().err


def _logged_run(monkeypatch, calls: list[str]):
    """Make ``study.run`` log when the code state is read and when the model runs (source bytes: the file's)."""
    real_run_unit, real_state = study.run_unit, study.run_state

    def run_unit(slug, point_ids=None):
        calls.append("run")
        return real_run_unit(slug, point_ids=point_ids)

    def run_state():
        calls.append("state")
        return real_state()

    monkeypatch.setattr(study, "_SOURCE", (REPO_ROOT / study.SCRIPT).read_bytes())
    monkeypatch.setattr(study, "run_unit", run_unit)
    monkeypatch.setattr(study, "run_state", run_state)
    return real_state


def test_run_reads_the_code_state_before_it_computes_and_records_that(monkeypatch, tmp_path, capsys):
    calls: list[str] = []
    real_state = _logged_run(monkeypatch, calls)
    study.run([*ONE_ROW, "--out-dir", str(tmp_path)])
    capsys.readouterr()
    assert calls[0] == "state" and calls.index("run") > 0, calls  # read before the first model run
    assert calls[-1] == "state" and calls.count("state") == 2, calls  # and checked again before writing
    record = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    for key, value in real_state().items():
        assert record[key] == value, key
    assert (tmp_path / study.CSV_NAME).exists()


def test_run_writes_nothing_when_the_code_changed_while_it_ran(monkeypatch, tmp_path, capsys):
    calls: list[str] = []
    real_state = _logged_run(monkeypatch, calls)

    def changing_state():
        calls.append("state")
        state = real_state()
        # The model is edited between the start of the run and its end.
        return state if calls.count("state") == 1 else {**state, "dirty": True, "dirty_digest": "0" * 64}

    monkeypatch.setattr(study, "run_state", changing_state)
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="changed while the study ran; nothing written"):
        study.run([*ONE_ROW, "--out-dir", str(out)])
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


def test_docs_table_matches_the_study(committed):
    section = _section()
    cells = [c.strip() for c in re.findall(r"^\s+(?:\* -|-) (.*)$", section, flags=re.M)]
    rows = [cells[i : i + 6] for i in range(6, len(cells), 6)]
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


def test_docs_prose_quotes_the_study(committed):
    flat = " ".join(_section().split())
    numbers = study.headline(committed)
    units = study.by_unit(committed).set_index("slug")
    bands = study.by_band(committed).set_index("shr_band")
    small, large = units.loc["daikin_rxm20a"], units.loc["daikin_rxm50a"]
    gap = (committed.T_evap_C_wet - committed.T_adp_catalog_C).groupby(committed.slug).mean()
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
        f"runs its evaporator {numbers['T_evap_rise_wet_minus_dry_K_mean']:.1f} K warmer than the dry one and draws "
        f"{-numbers['power_change_wet_vs_dry_pct_mean']:.1f} % less",
        f"The bias goes from {_signed(numbers['cop_bias_pct_dry'])} % to {_signed(numbers['cop_bias_pct_wet'])} % and "
        f"the mean absolute error from {numbers['cop_MAPE_pct_dry']:.1f} % to {numbers['cop_MAPE_pct_wet']:.1f} %",
        f"{_signed(bands.cop_bias_pct_wet['below 0.75'])} % where the published ratio is below 0.75",
        f"goes from *r* = {_signed(numbers['bias_shr_correlation_dry'], 2)} to "
        f"{_signed(numbers['bias_shr_correlation_wet'], 2)}",
        f"halved the wet-coil bias is {_signed(numbers['cop_bias_pct_wet_ua50'])} % and the ratio "
        f"{bands.shr_model_wet_ua50['all']:.2f} ({abs(bands.shr_error_wet_ua50['all']):.2f} low); doubled, "
        f"{_signed(numbers['cop_bias_pct_wet_ua200'])} % and {bands.shr_model_wet_ua200['all']:.2f} "
        f"({bands.shr_error_wet_ua200['all']:.2f} high)",
        "their 144 cooling rows are not in it",
    ):
        assert sentence in flat, f"index.rst lacks {sentence!r}"
    # The same figure where the residuals are read.
    page = " ".join((REPO_ROOT / "docs" / "source" / "validation" / "index.rst").read_text(encoding="utf-8").split())
    assert f"at {_signed(numbers['cop_bias_pct_wet'])} % over the same rows" in page
