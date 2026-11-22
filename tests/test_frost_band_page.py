"""The frost-point band study and what the model page says about it (#231).

``validation/frost_band_sensitivity/`` asks how much depends on
``DefrostModel.frost_point_band_K`` (3 K, an input no measurement fixes): the
rated-load multipliers over outdoor temperature and humidity, and a heating
season in three cold climates. ``docs/source/models/ashp.rst`` ("How much the
band matters") quotes its tables. Here:

* the committed tables are what the model gives (the multipliers in full; one
  outdoor-temperature class of one season, which is a few cycle solves);
* every figure of the page's three tables and of the sentences around them is
  the study's.

The helpers that read figures off a page are ``tests/_docs_figures.py``.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from validation.frost_band_sensitivity import study

from tests._docs_figures import quotes as _quotes
from tests._docs_figures import sentence as _sentence
from tests._docs_figures import table as _table
from tests._docs_figures import value as _value
from tmhp.defrost import DefrostModel, frost_point, humidity_ratio

DOCS = Path(__file__).resolve().parents[1] / "docs" / "source"
RESULTS = study.OUT_DIR
#: Rows and columns of the page's two multiplier tables.
T_ROWS = (2.0, -5.0, -10.0, -15.0, -20.0, -25.0, -30.0)
RH_COLUMNS = (0.60, 0.70, 0.85, 1.00)
SITE_ROWS = {
    "Denver, CO (5B, cool and dry)": "denver",
    "Helena, MT (6B, cold and dry)": "helena",
    "Fairbanks, AK (8, subarctic)": "fairbanks",
}


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


@pytest.fixture(scope="module")
def page() -> str:
    return (DOCS / "models" / "ashp.rst").read_text()


@pytest.fixture(scope="module")
def multipliers() -> pd.DataFrame:
    return pd.read_csv(RESULTS / "multipliers.csv", dtype={"band": str})


@pytest.fixture(scope="module")
def season() -> pd.DataFrame:
    return pd.read_csv(RESULTS / "seasonal.csv", dtype={"band": str})


# ---------------------------------------------------------------------------
# the committed study is what the model gives
# ---------------------------------------------------------------------------


def test_committed_multipliers_are_the_frost_models(multipliers: pd.DataFrame) -> None:
    fresh = study.multipliers()
    assert list(fresh.columns) == list(multipliers.columns) and len(fresh) == len(multipliers) == 4 * 36 * 15
    assert (fresh["band"] == multipliers["band"]).all()
    for column in fresh.columns.drop("band"):
        np.testing.assert_allclose(multipliers[column], fresh[column], rtol=1e-9, atol=1e-12, err_msg=column)


def test_committed_season_reproduces_in_its_coldest_class() -> None:
    """Helena below -20 degC with the default band: 35 hours in a handful of bins, each a cycle solve."""
    bins = pd.read_csv(study.WEATHER_BINS)
    weather = bins[(bins.site == "helena") & (bins.humidity == "dew_point") & (bins.T_out_C < -20.0)]
    assert 3 <= len(weather) <= 40 and weather.hours.sum() == 35
    fresh = study.totals(study.bin_results(weather, "helena", "3"), ["site", "band", "class"]).iloc[0]
    by_class = pd.read_csv(RESULTS / "seasonal_by_temperature.csv", dtype={"band": str})
    committed = by_class[
        (by_class.site == "helena")
        & (by_class.humidity == "dew_point")
        & (by_class.band == "3")
        & (by_class["class"] == "below -20")
    ].iloc[0]
    for key in (
        "hours",
        "load_kWh",
        "hp_heat_kWh",
        "hp_electricity_kWh",
        "backup_kWh",
        "frosting_hours",
        "scop_system",
    ):
        assert committed[key] == pytest.approx(fresh[key], rel=1e-6), key


def test_weather_bins_cover_the_same_hours_under_both_readings() -> None:
    bins = pd.read_csv(study.WEATHER_BINS)
    assert set(bins.site) == set(study.SITES) and set(bins.humidity) == set(study.HUMIDITY_READINGS)
    hours = bins.groupby(["site", "humidity"]).hours.sum().unstack()
    assert (hours["dew_point"] == hours["relative_humidity"]).all()
    assert ((bins.T_out_C < study.T_BALANCE) & (bins.RH > 0.0) & (bins.RH <= 1.0)).all()
    # Read from the dew point (over water), cold air is more humid over ice than the column says.
    cold = bins[bins.T_out_C < -10.0]
    mean = (cold.RH * cold.hours).groupby([cold.site, cold.humidity]).sum() / cold.groupby(
        ["site", "humidity"]
    ).hours.sum()
    for site in study.SITES:
        assert mean[site, "dew_point"] > mean[site, "relative_humidity"] + 0.08


# ---------------------------------------------------------------------------
# the page quotes the study
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, column",
    [
        ("COP multiplier at the rated load with a frost-point band of 0 / 3 / 6 K", "cop_multiplier"),
        ("Capacity multiplier at the rated load with a frost-point band of 0 / 3 / 6 K", "capacity_multiplier"),
    ],
)
def test_page_tables_of_the_multipliers(page: str, multipliers: pd.DataFrame, title: str, column: str) -> None:
    rows = _table(page, title)
    assert rows["Outdoor"] == [f"{100.0 * rh:.0f} %" for rh in RH_COLUMNS]
    assert [_value(label.removesuffix(" °C")) for label in list(rows)[1:]] == list(T_ROWS)
    for label, cells in list(rows.items())[1:]:
        T_out = _value(label.removesuffix(" °C"))
        for cell, humidity in zip(cells, RH_COLUMNS, strict=True):
            quoted = cell.split(" / ")
            assert len(quoted) == 3, cell
            for figure, band in zip(quoted, ("0", "3", "6"), strict=True):
                row = multipliers[
                    (multipliers.band == band)
                    & (multipliers.T_out_C == T_out)
                    & ((multipliers.RH - humidity).abs() < 1e-9)
                ]
                assert len(row) == 1
                _quotes(figure, float(row[column].iloc[0]), f"ashp.rst, {title}: {label}, {humidity:.0%}, {band} K")


def test_page_sentences_on_the_multipliers(page: str, multipliers: pd.DataFrame) -> None:
    RH_room, T_room, humid, T_low, T_high, RH_low, RH_high, largest, T_largest, RH_largest = _sentence(
        page,
        "In air as humid as a test room ({} % and more) down to {} °C a band of 0 K and the default 3 K give the "
        "same figures and 6 K differs by at most {} in the COP multiplier; on the whole grid, {} to {} °C and "
        "{} to {} %, the largest difference from the default is {} (0 K at {} °C and {} %)",
    )
    cop = multipliers.pivot_table(index=["T_out_C", "RH"], columns="band", values="cop_multiplier")
    T_out, RH = cop.index.get_level_values("T_out_C"), cop.index.get_level_values("RH")
    # The grid the page names is the grid of the study.
    assert (T_out.min(), T_out.max()) == (_value(T_low), _value(T_high))
    assert (RH.min(), RH.max()) == (_value(RH_low) / 100.0, _value(RH_high) / 100.0)
    test_room = cop[(T_out >= _value(T_room)) & (_value(RH_room) / 100.0 - 1e-9 <= RH)]
    assert len(test_room) == 26 * 4
    assert (test_room["0"] - test_room["3"]).abs().max() < 1e-12
    _quotes(humid, (test_room["6"] - test_room["3"]).abs().max(), "ashp.rst, 6 K against 3 K in humid air")
    from_default = pd.concat([(cop["0"] - cop["3"]).abs(), (cop["6"] - cop["3"]).abs()], axis=1, keys=["0", "6"])
    _quotes(largest, from_default.to_numpy().max(), "ashp.rst, largest difference of the COP multiplier")
    assert from_default["0"].max() > from_default["6"].max() + 0.01
    assert from_default["0"].idxmax() == (_value(T_largest), _value(RH_largest) / 100.0)


def test_page_table_and_sentences_of_the_season(page: str, season: pd.DataFrame) -> None:
    rows = _table(page, "Electricity of a heating season against the default band of 3 K")
    assert list(rows)[1:] == list(SITE_ROWS)
    assert rows["Site (ASHRAE 169 zone)"] == [
        "Heating hours (below −10 °C)",
        "Seasonal COP, 3 K",
        "No band",
        "0 K",
        "6 K",
    ]
    bins = pd.read_csv(study.WEATHER_BINS)
    spans: dict[str, list[float]] = {"band": [], "none": [], "reading": []}
    for label, site in SITE_ROWS.items():
        hours, cop, *against = rows[label]
        zone = label.split("(")[1].split(",")[0]
        assert zone == study.SITES[site]["zone"]
        weather = bins[(bins.site == site) & (bins.humidity == "dew_point")]
        total, cold = hours.replace(")", "").split(" (")
        assert _value(total) == weather.hours.sum() and _value(cold) == weather[weather.T_out_C < -10.0].hours.sum()
        at = {(r.humidity, r.band): r for r in season[season.site == site].itertuples()}
        for figure, reading in zip(cop.split(" / "), study.HUMIDITY_READINGS, strict=True):
            _quotes(figure, at[reading, "3"].scop_system, f"ashp.rst, seasonal COP at {site}, {reading}")
        for cell, band in zip(against, ("none", "0", "6"), strict=True):
            for figure, reading in zip(cell.split(" / "), study.HUMIDITY_READINGS, strict=True):
                value = at[reading, band].electricity_vs_default_pct
                _quotes(figure.removesuffix(" %").replace("+", ""), value, f"ashp.rst, {site}, band {band}, {reading}")
                spans["none" if band == "none" else "band"].append(value)
        both = [at[reading, "3"].electricity_kWh for reading in study.HUMIDITY_READINGS]
        spans["reading"].append(100.0 * (1.0 - both[1] / both[0]))

    low, high = _sentence(page, "a band of 0 or 6 K moves a season's electricity by {} to +{} % against the default")
    _quotes(low, min(spans["band"]), "ashp.rst, least effect of the band")
    _quotes(high, max(spans["band"]), "ashp.rst, largest effect of the band")
    least, most = _sentence(
        page,
        "and dropping the band (the fixed losses in any air below 5 °C, with this model's load term and "
        "transition) by {} to {} %",
    )
    _quotes(least, min(spans["none"]), "ashp.rst, least effect of dropping the band")
    _quotes(most, max(spans["none"]), "ashp.rst, largest effect of dropping the band")
    readings = _sentence(
        page, "moves it by as much: {} % (Denver), {} % (Helena) and {} % (Fairbanks) at the default band"
    )
    for figure, value, site in zip(readings, spans["reading"], SITE_ROWS.values(), strict=True):
        _quotes(figure, value, f"ashp.rst, effect of the humidity reading at {site}")


def test_page_sentence_on_the_two_readings_in_cold_air(page: str) -> None:
    T_cell, from_dew_point, threshold, from_column = _sentence(
        page,
        "Below {} °C in Fairbanks {} % of the hours are at {} % or more under the first reading and {} % under "
        "the second",
    )
    bins = pd.read_csv(study.WEATHER_BINS)
    cold = bins[(bins.site == "fairbanks") & (bins.T_out_C < _value(T_cell))]
    assert cold.groupby("humidity").hours.sum().nunique() == 1
    shares = {
        reading: 100.0 * part[_value(threshold) / 100.0 - 1e-9 <= part.RH].hours.sum() / part.hours.sum()
        for reading, part in cold.groupby("humidity")
    }
    _quotes(from_dew_point, shares["dew_point"], "ashp.rst, humid cold hours in Fairbanks from the dew point")
    _quotes(from_column, shares["relative_humidity"], "ashp.rst, humid cold hours in Fairbanks from the RH column")


def test_page_statements_about_humidity_over_ice_and_the_onset(page: str) -> None:
    over_water, T_cell, over_ice = _sentence(page, "{} % over water at {} °C is {} % over ice")
    T = _value(T_cell)
    vapour = _value(over_water) / 100.0 * float(study._vapour_pressure_over_water(np.array([T]))[0])
    _quotes(over_ice, 100.0 * vapour / study._model_saturation_pressure(T), "ashp.rst, relative humidity over ice")
    (onset,) = _sentence(page, "the frost point reaches the coil of the correlation at {} % at 0 °C")
    coil = 0.82 * 0.0 - 8.589
    humidity = _value(onset) / 100.0
    # The quoted humidity, to its last digit, brackets the one whose frost point is the coil temperature.
    assert (
        frost_point(0.0, humidity_ratio(0.0, humidity - 0.005))
        < coil
        < frost_point(0.0, humidity_ratio(0.0, humidity + 0.005))
    )
    assert DefrostModel().frost_state(0.0, humidity + 0.005).active
    assert not DefrostModel().frost_state(0.0, humidity - 0.005).active
