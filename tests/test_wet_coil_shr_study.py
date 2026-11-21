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
    for key, value in study.headline(committed).items():
        assert value == pytest.approx(manifest["headline"][key], rel=1e-6, abs=1e-9), key


def test_record_vouches_for_the_script_that_made_the_files():
    """The record names a commit that holds the script as it is now, run with nothing uncommitted."""
    from validation.parity.run import _git

    record = json.loads((OUT / "provenance.json").read_text(encoding="utf-8"))
    assert record["dirty"] is False and record["script"] == study.SCRIPT
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
