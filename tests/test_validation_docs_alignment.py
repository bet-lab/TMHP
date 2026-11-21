"""Guard the validation numbers quoted in prose against the harness output.

The whole point of this subsystem is that the published error figures are the
ones a user of the library would get. That guarantee is only as good as the
weakest copy of the numbers -- and a hand-typed figure in a documentation page
is exactly the kind of copy that drifts, which is how the previously published
MAPE came to describe a parameter set nobody was shipping.

So every number quoted in prose is recomputed here from the per-point CSVs
under ``validation/results/`` and looked for *in the place it is quoted* -- the
table row or the sentence that names the group -- never anywhere on the page,
so a figure cannot pass by matching an unrelated sentence. If the harness output
moves and the prose does not, this fails. That ``validation/results/`` itself
matches the code is ``test_validation_results_current.py``'s job.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS = REPO_ROOT / "validation" / "results"
SUMMARY = RESULTS / "summary.csv"
ABLATION = REPO_ROOT / "validation" / "data" / "coefficient_ablation_parity.csv"

Selector = Callable[[pd.DataFrame], pd.Series]


def _ashpb(df: pd.DataFrame) -> pd.Series:
    return (df.model_class == "ASHPB") & (df.status == "adopted")


def _daikin(df: pd.DataFrame) -> pd.Series:
    return (df.model_class == "ASHP") & (df.manufacturer == "Daikin") & (df.status == "adopted")


def _fujitsu(df: pd.DataFrame) -> pd.Series:
    return (df.model_class == "ASHP") & (df.manufacturer == "Fujitsu")


def _injected(df: pd.DataFrame) -> pd.Series:
    """The published injection circuit (``injection`` column of the results): anything but ``none``."""
    return df.injection.fillna("none") != "none"


#: Row label in the residuals table of validation/index.rst -> its points.
INDEX_ROWS: dict[str, Selector] = {
    "Air-to-water, 10 units": _ashpb,
    "Air-to-water, no injection, 6 units": lambda df: _ashpb(df) & ~_injected(df),
    "Air-to-water, injection, 4 units": lambda df: _ashpb(df) & _injected(df),
    "Air-to-air, Daikin, 5 units": _daikin,
    "Daikin cooling": lambda df: _daikin(df) & (df["mode"] == "cooling"),
    "Daikin heating": lambda df: _daikin(df) & (df["mode"] == "heating"),
    "Daikin heating at or below −7 °C": lambda df: _daikin(df) & (df["mode"] == "heating") & (df.t_source_C <= -7.0),
    "Air-to-air, Fujitsu, 2 units": _fujitsu,
}

#: Lead-in of each family sentence in README.md -> its points.
README_SENTENCES: dict[str, Selector] = {
    "air-to-water (`AirSourceHeatPumpBoiler`, 10 units)": _ashpb,
    "air-to-air (`AirSourceHeatPump`, 5 Daikin units)": _daikin,
}


@pytest.fixture(scope="module")
def summary() -> pd.DataFrame:
    """Read the committed summary that backs the published coverage claims."""
    assert SUMMARY.is_file(), f"committed parity summary missing: {SUMMARY}"
    return pd.read_csv(SUMMARY)


@pytest.fixture(scope="module")
def points() -> pd.DataFrame:
    frames = [pd.read_csv(p) for p in sorted(RESULTS.glob("*.csv")) if p.name != SUMMARY.name]
    return pd.concat(frames, ignore_index=True)


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _flat(relative: str) -> str:
    """Whitespace-collapsed text with ASCII minus, so line wraps and U+2212 do not matter."""
    return re.sub(r"\s+", " ", _read(relative)).replace("−", "-")


def _stats(rows: pd.DataFrame) -> tuple[float, float, int, int]:
    """Point-weighted COP MAPE, mean signed error [%], evaluated and attempted points."""
    good = rows[rows.usable]
    mape = float(good.abs_pct_error.mean())
    bias = float(((good.cop_pred - good.cop_target) / good.cop_target * 100.0).mean())
    return mape, bias, int(len(good)), int(len(rows))


def _signed(value: float) -> str:
    text = f"{value:+.1f}"
    return "-0.0" if text == "+0.0" and value < 0 else text


def _list_table_row(text: str, label: str) -> str:
    """The cells of the list-table row that starts with ``* - <label>``."""
    match = re.search(rf"^(\s*)\* - {re.escape(label)}\s*$", text, flags=re.MULTILINE)
    assert match, f"row {label!r} missing"
    indent = match.group(1)
    rest = text[match.end() :]
    end = re.search(rf"^{re.escape(indent)}\* - |^\S", rest, flags=re.MULTILINE)
    return rest[: end.start() if end else len(rest)]


def _cooling_shr(rows: pd.DataFrame) -> pd.Series:
    """Sensible heat ratio of each cooling point, as its catalogue records it in the point's ``note``."""
    from validation.parity.spec import load_all

    notes = {(catalog.slug, point.id): point.note for catalog in load_all() for point in catalog.points}
    values = [
        float(re.search(r"SHR\s*([0-9.]+)", notes[(slug, pid)]).group(1))
        for slug, pid in zip(rows.slug, rows.point_id, strict=True)
    ]
    return pd.Series(values, index=rows.index)


def test_coverage_counts_match_the_harness(summary: pd.DataFrame) -> None:
    text = _flat("docs/source/validation/index.rst")
    attempted = int(summary.points.sum())
    evaluated = int(summary.points_usable.sum())
    assert f"{attempted} attempted, {evaluated} evaluated" in text


def _celsius(value: float, signed: bool) -> str:
    text = f"{value:g}"
    return f"+{text}" if signed and value > 0 else text


def test_coverage_temperature_spans_match_the_points(points: pd.DataFrame) -> None:
    """The coverage table's source and sink spans are the extremes of the attempted points.

    The table counts all attempted points, held catalogues included, so the span
    is over the same set; the adopted units' own span is stated beside it.
    """
    raw = _read("docs/source/validation/index.rst")
    source = re.sub(r"\s+", " ", _list_table_row(raw, "Source temperature")).replace("−", "-")
    adopted = points[points.status == "adopted"]
    expected = (
        f"{_celsius(points.t_source_C.min(), True)} to {_celsius(points.t_source_C.max(), True)} °C "
        f"(adopted units {_celsius(adopted.t_source_C.min(), True)} to "
        f"{_celsius(adopted.t_source_C.max(), True)} °C)"
    )
    assert expected in source, f"coverage table says {source.strip()!r}, the points span {expected!r}"
    sink = re.sub(r"\s+", " ", _list_table_row(raw, "Sink temperature"))
    expected = f"{_celsius(points.t_sink_C.min(), False)} to {_celsius(points.t_sink_C.max(), False)} °C"
    assert expected in sink, f"coverage table says {sink.strip()!r}, the points span {expected!r}"


@pytest.mark.parametrize("label", sorted(INDEX_ROWS))
def test_residuals_table_matches_the_harness(points: pd.DataFrame, label: str) -> None:
    """Each group row quotes its own MAPE, bias and evaluated/attempted count."""
    mape, bias, evaluated, attempted = _stats(points[INDEX_ROWS[label](points)])
    raw = _read("docs/source/validation/index.rst")
    row = re.sub(r"\s+", " ", _list_table_row(raw, label)).replace("−", "-")
    count = re.search(r"(\d+) units", label)
    if count:
        rows = points[INDEX_ROWS[label](points)]
        assert int(count.group(1)) == rows.slug.nunique(), f"unit count in {label!r} disagrees with the harness"
    for expected in (f"{mape:.1f} %", f"{_signed(bias)} %", f"{evaluated} / {attempted}"):
        assert expected in row, f"{label}: row says {row.strip()!r}, harness says {expected!r}"


@pytest.mark.parametrize("lead", sorted(README_SENTENCES))
def test_readme_family_figures_match_the_harness(points: pd.DataFrame, lead: str) -> None:
    mape, bias, evaluated, attempted = _stats(points[README_SENTENCES[lead](points)])
    expected = f"{lead}: COP MAPE {mape:.1f} %, bias {_signed(bias)} %, {evaluated}/{attempted} points"
    assert expected in _flat("README.md"), f"README.md lacks {expected!r}"


def test_readme_quotes_the_stated_biases(points: pd.DataFrame) -> None:
    """The sentence that names each known bias carries the harness value, and the direction it states holds."""
    text = _flat("README.md")
    daikin = points[_daikin(points)]
    cooling = daikin[daikin["mode"] == "cooling"]
    heating = daikin[daikin["mode"] == "heating"]
    by_t = {t: _stats(heating[heating.t_source_C == t])[1] for t in sorted(heating.t_source_C.unique())}
    cool_bias = _stats(cooling)[1]
    heat_bias, heat_n = _stats(heating)[1], _stats(heating)[2]
    lo, hi = cooling.t_source_C.min(), cooling.t_source_C.max()
    cool_lo = _stats(cooling[cooling.t_source_C == lo])[1]
    cool_hi = _stats(cooling[cooling.t_source_C == hi])[1]
    assert (
        f"Air-to-air cooling runs low ({_signed(cool_bias)} %; from {_signed(cool_lo)} % at {lo:g} °C "
        f"to {_signed(cool_hi)} % at {hi:g} °C outdoor"
    ) in text
    by_t_cool = [_stats(cooling[cooling.t_source_C == t])[1] for t in sorted(cooling.t_source_C.unique())]
    assert max(by_t_cool) < 0.0, "cooling no longer runs low at every outdoor temperature"
    # "lowest where the catalogue carries no latent load": the rows of sensible heat ratio 0.95 or above.
    shr = _cooling_shr(cooling)
    errors = (cooling.cop_pred - cooling.cop_target) / cooling.cop_target * 100.0
    assert errors[shr >= 0.95].mean() < errors[shr < 0.95].mean()
    assert (
        f"runs low ({_signed(heat_bias)} % over its {heat_n} points; {_signed(by_t[-10.0])} % at -10 °C, "
        f"{_signed(by_t[0.0])} % at 0 °C, and {_signed(by_t[6.0])} % at +6 °C, where the coil does not frost), "
        f"except at -15 °C ({_signed(by_t[-15.0])} %)"
    ) in text
    assert all(by_t[t] < 0 for t in by_t if t != -15.0) and by_t[-15.0] > 0, by_t
    ashpb = points[_ashpb(points)]
    frost_free = _stats(ashpb[ashpb.t_source_C >= 7.0])[1]
    plus_two = _stats(ashpb[ashpb.t_source_C == 2.0])[1]
    minus_seven = _stats(ashpb[ashpb.t_source_C == -7.0])[1]
    below = _stats(ashpb[ashpb.t_source_C < -7.0])[1]
    assert f"it runs low at +7 °C outdoor and above ({_signed(frost_free)} %)" in text
    assert (
        f"lower where the adjustment applies ({round(plus_two):+d} % at +2 °C, "
        f"{round(minus_seven):+d} % at -7 °C, {round(below):+d} % below;"
    ) in text
    assert max(plus_two, minus_seven, below) < frost_free < 0.0
    for injected, words in ((False, "six rotaries without injection"), (True, "four injection scrolls")):
        rows = ashpb[_injected(ashpb) == injected]
        mape, bias, evaluated, attempted = _stats(rows)
        assert f"{words}" in text and f"{mape:.1f} % / {_signed(bias)} % ({evaluated}/{attempted}" in text, words
    bad = ashpb[~ashpb.usable]
    assert f"The {len(bad)} air-to-water points not evaluated all run below the published duty" in text
    assert set(bad.failure_reason) == {"capacity_short"}
    at_ceiling = bad[bad.rps >= 120.0 - 1e-6]
    words = {3: "three"}
    assert f"{words[len(at_ceiling)]} at the 120 rev/s speed ceiling" in text
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION

    assert f"(`{COEFFICIENT_VERSION}`)" in text


def test_index_prose_figures_match_the_harness(points: pd.DataFrame) -> None:
    """Figures quoted in the running text, each next to the words that name it."""
    text = _flat("docs/source/validation/index.rst")
    ashpb = points[_ashpb(points)]
    frost_free = _stats(ashpb[ashpb.t_source_C >= 7.0])
    at_two = _stats(ashpb[ashpb.t_source_C == 2.0])[1]
    at_minus_seven = _stats(ashpb[ashpb.t_source_C == -7.0])[1]
    below = _stats(ashpb[ashpb.t_source_C < -7.0])[1]
    expected = (
        f"the bias is {_signed(frost_free[1])} % over the {frost_free[2]} points at +7 °C outdoor and above, "
        f"where the coil does not frost, {_signed(at_two)} % at +2 °C, {_signed(at_minus_seven)} % at -7 °C "
        f"and {_signed(below)} % below -7 °C"
    )
    assert expected in text, f"index.rst lacks {expected!r}"
    # "runs lowest where it applies": every adjusted stratum is below the frost-free one.
    assert max(at_two, at_minus_seven, below) < frost_free[1]

    per_unit = {slug: _stats(g) for slug, g in ashpb.groupby("slug")}
    outlier = per_unit["panasonic_wh_mxc16j9e8"][1]
    assert f"unit runs {_signed(outlier)} % optimistic where the set runs {_signed(_stats(ashpb)[1])} %" in text
    siblings = [per_unit[s][1] for s in ("panasonic_wh_mxc09j3e8", "panasonic_wh_mxc12j9e8")]
    assert f"(its 9 and 12 kW siblings {_signed(siblings[0])} and {_signed(siblings[1])} %)" in text

    r410a = [per_unit[s][0] for s in per_unit if ashpb[ashpb.slug == s].refrigerant.iat[0] == "R410A"]
    assert f"R-410A air-to-water units sit at {min(r410a):.1f} to {max(r410a):.1f} % MAPE" in text
    spread = [m for m, *_ in per_unit.values()]
    assert f"inside the {min(spread):.1f} to {max(spread):.1f} % spread" in text
    # "inside": the R-410A units are at neither end of the spread, so the offset is not a refrigerant effect.
    assert min(spread) < min(r410a) and max(r410a) < max(spread), (r410a, spread)


def test_index_air_to_water_unit_table_matches_the_harness(points: pd.DataFrame) -> None:
    """One row per adopted air-to-water unit: its published compressor and injection, its figures."""
    raw = _read("docs/source/validation/index.rst")
    start = raw.index("The air-to-water headline by unit")
    table = raw[start : raw.index("No validation machine has a scroll without injection", start)]
    ashpb = points[_ashpb(points)]
    assert len(re.findall(r"^\s*\* - (?!Unit$)\S", table, flags=re.MULTILINE)) == ashpb.slug.nunique()
    for _, unit in ashpb.groupby("slug"):
        first = unit.iloc[0]
        cells = [
            c.strip()
            for c in re.sub(r"\s+", " ", _list_table_row(table, first.unit)).replace("−", "-").split(" - ")[1:]
        ]
        mape, bias, evaluated, attempted = _stats(unit)
        assert cells == [
            first.refrigerant,
            first.compressor_type,
            first.injection,
            f"{mape:.1f} %",
            f"{_signed(bias)} %",
            f"{evaluated} / {attempted}",
        ], (first.unit, cells)
    # Every unit runs on the one set of default maps: the harness passes no compressor kind to the model.
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION

    assert set(ashpb.coefficient_version) == {COEFFICIENT_VERSION}
    assert "Every unit runs on the same maps" in re.sub(r"\s+", " ", table)
    kinds = ashpb.drop_duplicates("slug")
    scrolls = kinds[kinds.compressor_type == "scroll"]
    assert len(scrolls) == 4 and set(scrolls.injection) == {"vapor", "flash"}
    assert int((kinds.compressor_type == "rotary").sum()) == 6
    flat = _flat("docs/source/validation/index.rst")
    assert (
        "the four scrolls of the set have vapour or flash injection" in flat and "the six others are rotaries" in flat
    )


def test_index_prose_states_the_unevaluated_points_and_the_strata(points: pd.DataFrame) -> None:
    """The unevaluated air-to-water points, the frost split by injection and the held units, as the results have them."""
    text = _flat("docs/source/validation/index.rst")
    ashpb = points[_ashpb(points)]
    bad = ashpb[~ashpb.usable]
    assert set(bad.failure_reason) == {"capacity_short"}
    assert f"**{len(bad)} air-to-water points are not evaluated, all of them because the model delivers" in text
    at_ceiling = bad[bad.rps >= 120.0 - 1e-6]
    samsung = bad[bad.slug == "samsung_ehs_mono_ht_r32_14kw"]
    assert len(at_ceiling) + len(samsung) == len(bad)
    assert (samsung.t_sink_C == 65.0).all()
    words = {3: "Three", 5: "five"}
    assert f"{words[len(at_ceiling)]} run at the 120 rev/s speed ceiling" in text
    assert set(at_ceiling.unit) == {"WH-WXG16ME8"} and set(at_ceiling.t_source_C) == {-15.0}
    sinks = sorted(int(t) for t in at_ceiling.t_sink_C)
    assert f"WH-WXG16 at -15 °C outdoor, at {sinks[0]}, {sinks[1]} and {sinks[2]} °C leaving water" in text
    assert f"The other {words[len(samsung)]} are the Samsung unit at 65 °C leaving water" in text
    assert "**Every air-to-air point is evaluated**" in text
    assert points[points.model_class == "ASHP"].usable.all()

    injected, single = ashpb[_injected(ashpb)], ashpb[~_injected(ashpb)]

    def by_outdoor(rows: pd.DataFrame) -> list[str]:
        sel = (rows.t_source_C >= 7.0, rows.t_source_C == 2.0, rows.t_source_C == -7.0, rows.t_source_C < -7.0)
        return [f"{_signed(_stats(rows[s])[1])} %" for s in sel]

    a, b, c, d = by_outdoor(single)
    assert f"the machines without it run {a} frost-free, {b} at +2 °C, {c} at -7 °C and {d} below" in text
    a, b, c, d = by_outdoor(injected)
    assert f"the injection machines {a}, {b}, {c} and {d}".replace("+", "") in text.replace("+", "")

    fujitsu_rows = points[_fujitsu(points)]
    fujitsu = _stats(fujitsu_rows)
    assert f"to {_signed(fujitsu[1])} % (all {fujitsu[3]})" in text and fujitsu[2] == fujitsu[3]
    cool, heat = (_stats(fujitsu_rows[fujitsu_rows["mode"] == m])[1] for m in ("cooling", "heating"))
    raw = _read("docs/source/validation/index.rst")
    row = re.sub(r"\s+", " ", _list_table_row(raw, "Air-to-air, Fujitsu, 2 units"))
    assert f"{_signed(cool)} % in cooling, {_signed(heat)} % in heating" in row

    daikin = points[_daikin(points)]
    cooling = daikin[daikin["mode"] == "cooling"]
    lo, hi = cooling.t_source_C.min(), cooling.t_source_C.max()
    assert (
        f"from {_signed(_stats(cooling[cooling.t_source_C == lo])[1])} % at {lo:g} °C to "
        f"{_signed(_stats(cooling[cooling.t_source_C == hi])[1])} % at {hi:g} °C outdoor"
    ) in text
    by_t_cool = {t: _stats(cooling[cooling.t_source_C == t])[1] for t in sorted(cooling.t_source_C.unique())}
    assert max(by_t_cool.values()) < 0.0 and max(by_t_cool, key=by_t_cool.get) == hi  # "low at every ..., least at 40"
    shr = _cooling_shr(cooling)
    errors = (cooling.cop_pred - cooling.cop_target) / cooling.cop_target * 100.0
    dry, wet = errors[shr >= 0.95], errors[shr < 0.75]
    assert (
        f"(sensible heat ratio 0.95 or above, {len(dry)} rows) run {_signed(dry.mean())} %, and those with the most "
        f"latent load (below 0.75, {len(wet)} rows) {_signed(wet.mean())} %"
    ) in text
    heating = daikin[daikin["mode"] == "heating"]
    temps = {t: _signed(_stats(heating[heating.t_source_C == t])[1]) for t in sorted(heating.t_source_C.unique())}
    assert (
        f"the bias is {temps[0.0]} % at 0 °C, {temps[-5.0]} % at -5 °C, {temps[-10.0]} % at -10 °C and "
        f"{temps[-15.0]} % at -15 °C outdoor, against {temps[6.0]} % and {temps[10.0]} % at 6 and 10 °C"
    ).replace("+", "") in text.replace("+", "")


def test_index_ceiling_points_meet_the_duty_without_the_frost_adjustment(points: pd.DataFrame) -> None:
    """'All three meet the published duty with the frost and defrost adjustment switched off', re-solved."""
    import dataclasses

    from validation.parity.run import run_catalog
    from validation.parity.spec import load_all

    ashpb = points[_ashpb(points)]
    at_ceiling = ashpb[~ashpb.usable & (ashpb.rps >= 120.0 - 1e-6)]
    for slug, rows in at_ceiling.groupby("slug"):
        catalog = load_all(slug)[0]
        catalog = dataclasses.replace(catalog, points=tuple(p for p in catalog.points if p.id in set(rows.point_id)))
        frost_free = run_catalog(catalog, {"defrost": False})
        assert frost_free.usable.all(), (slug, frost_free.failure_reason.tolist())
    assert "All three meet the published duty with the frost and defrost adjustment switched off" in _flat(
        "docs/source/validation/index.rst"
    )


def test_index_residual_causes_quote_the_decomposition(points: pd.DataFrame) -> None:
    """The rating-point biases, the latent-load correlation and the Fujitsu speeds of the 'three things' admonition."""
    import numpy as np

    decomposition = pd.read_csv(REPO_ROOT / "validation" / "data" / "residual_decomposition.csv")
    text = _flat("docs/source/validation/index.rst")
    daikin_d = decomposition[decomposition.manufacturer == "Daikin"].sort_values("nameplate_EER", ascending=False)
    fujitsu_d = decomposition[decomposition.manufacturer == "Fujitsu"]
    heat_d, heat_f = daikin_d.bias_heating_pct, fujitsu_d.bias_heating_pct
    assert f"Daikin lands between {round(heat_d.min())} and {round(heat_d.max())} %" in text
    assert f"Fujitsu at +{round(heat_f.min())} to +{round(heat_f.max())} %" in text
    assert f"about {round(heat_f.mean() - heat_d.mean(), -1):.0f} points at the heating rating point" in text
    cool_d, cool_f = daikin_d.bias_cooling_pct, fujitsu_d.bias_cooling_pct
    assert f"Daikin spans {round(cool_d.min())} to {round(cool_d.max())} %, falling with nameplate EER" in text
    assert cool_d.is_monotonic_increasing  # sorted by falling EER, the bias rises: it falls with EER
    assert f"Fujitsu +{round(cool_f.min())} to +{round(cool_f.max())} %" in text
    smallest = daikin_d.head(3).bias_cooling_pct
    assert set(daikin_d.head(3).cooling_kW) == set(sorted(daikin_d.cooling_kW)[:3])
    assert f"sit {round(-smallest.max())} to {round(-smallest.min())} % low (①)" in text

    daikin = points[_daikin(points)]
    cooling = daikin[daikin["mode"] == "cooling"]
    errors = (cooling.cop_pred - cooling.cop_target) / cooling.cop_target * 100.0
    r = float(np.corrcoef(_cooling_shr(cooling), errors)[0, 1])
    assert f"of the {len(cooling)} cooling points correlates with their sensible heat ratio at *r* = {r:.2f}".replace(
        "-", "−"
    ) in re.sub(r"\s+", " ", _read("docs/source/validation/index.rst"))
    fujitsu_heating = points[_fujitsu(points) & (points["mode"] == "heating")]
    speeds = sorted(fujitsu_heating.groupby("unit").rps.median())
    assert f"(median {speeds[0]:.0f} and {speeds[1]:.0f} rev/s of 120)" in text
    assert (fujitsu_heating.rps < 120.0).all()


def test_result_keys_state_the_data_domain_of_the_maps() -> None:
    """``efficiency_extrapolated`` is described against the data domain the library carries."""
    from tmhp.compressor_efficiency import DOMAIN, MAP_DOMAIN

    text = _flat("docs/source/concepts/result-keys.rst")
    start = text.index("* - ``efficiency_extrapolated``")
    row = text[start : text.index("* - ``discharge_control``", start)]
    low, high = DOMAIN.rps_range
    assert f"the speeds {low:g}–{high:g} rev/s" in row
    hull = MAP_DOMAIN["hull_PR_Te"]
    prs, temps = [pr for pr, _ in hull], [t for _, t in hull]
    assert f"spans PR {min(prs):.2f}–{max(prs):.2f} and {min(temps):.1f} to {max(temps):.1f} °C" in row
    assert "``T_ref_evap_sat [°C]``" in row
    assert f"every speed below {low:g} rev/s" in row and f"every speed above {high:g} rev/s" in row


def test_coverage_claims_match_the_catalogues(summary: pd.DataFrame) -> None:
    text = _read("docs/source/validation/index.rst")
    for manufacturer in sorted(summary.manufacturer.unique()):
        assert manufacturer in text, f"{manufacturer} is in the results but not named on the page"
    for refrigerant in sorted(summary.refrigerant.unique()):
        assert refrigerant in text, f"{refrigerant} is in the results but not named on the page"
    low, high = summary.nominal_kW.min(), summary.nominal_kW.max()
    assert f"{low:.1f} – {high:.1f} kW" in text


def test_index_names_the_shipped_coefficients() -> None:
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION

    text = _flat("docs/source/validation/index.rst")
    assert f"``{COEFFICIENT_VERSION}``" in text


def _summary_table(text: str) -> str:
    start = text.index("Summary\n=======")
    return text[start : text.index("\n\n\n", start)]


#: Row label of the strata table in defaults.rst -> the archive stratum it quotes.
HX_STRATA_ROWS = {
    "Outdoor coil, condensing": "outdoor_coil|fin_tube|condensing",
    "Outdoor coil, evaporating": "outdoor_coil|fin_tube|evaporating",
    "Plate condenser of air-to-water units": "water_condenser|bphe|condensing",
    "Coaxial source exchanger, condensing": "liquid_source_hx|water_to_air|coaxial|condensing",
    "Coaxial source exchanger, evaporating": "liquid_source_hx|water_to_air|coaxial|evaporating",
    "Water-to-air indoor coil, condensing": "indoor_coil|water_to_air|fin_tube|condensing",
}


def _hx_entry(stratum: str) -> dict:
    from tmhp.default_inputs import _artifact

    entry = _artifact("hx_approach_regression.json")["strata"][stratum]
    assert entry["meets_shipping_gate"] and entry["law"]["kind"] == "leaving_approach", stratum
    return entry


def _approach(stratum: str) -> str:
    return f"{_hx_entry(stratum)['law']['dT_out_K']:.2f} K"


def _size(stratum: str) -> tuple[int, int, int]:
    entry = _hx_entry(stratum)
    return entry["n"], entry["n_holdout_groups"], len(entry["manufacturers"])


def _assert_conductance_rows_state_the_shipped_defaults(table: str, a2a, a2w) -> None:
    """The five conductance rows of the Summary state the laws the constructors use, from the packaged archive."""
    from tmhp import GroundSourceHeatPump, GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler
    from tmhp.constants import c_w

    def row(label: str) -> str:
        return re.sub(r"\s+", " ", _list_table_row(table, label))

    def uses(model, name: str, stratum: str) -> None:
        estimate = model.default_input_estimates[name]
        assert estimate.key == stratum and estimate.status != "provisional", (name, estimate.key)
        assert estimate.assumptions[0].startswith(f"leaving-end approach {_approach(stratum)}"), name

    cond, evap = "outdoor_coil|fin_tube|condensing", "outdoor_coil|fin_tube|evaporating"
    uses(a2a, "UA_ou_rated", cond)
    uses(a2a, "UA_ou_evap_rated", evap)
    uses(a2w, "UA_ou_rated", evap)
    outdoor = row("Outdoor coil conductance (per role)")
    assert f"Leaving-end approach {_approach(cond)} as a condenser" in outdoor
    assert f"{_approach(evap)} as an evaporator" in outdoor
    (n_c, g_c, m_c), (n_e, g_e, m_e) = _size(cond), _size(evap)
    assert f"{n_c} / {n_e} operating points ({g_c} / {g_e} product lines, {m_c} / {m_e} manufacturers)" in outdoor

    indoor = row("Indoor coil conductance (air-to-air)")
    assert a2a.default_input_estimates["UA_iu_rated"].status == "provisional"
    assert f"``UA_iu_rated = {a2a.UA_iu_rated / a2a.hp_capacity:.2f} × hp_capacity``, provisional" in indoor

    water_to_air = "indoor_coil|water_to_air|fin_tube|condensing"
    gshp = GroundSourceHeatPump()
    uses(gshp, "UA_iu_rated", water_to_air)
    n, g, m = _size(water_to_air)
    water_air_row = row("Indoor coil conductance (water-to-air)")
    assert f"Leaving-end approach {_approach(water_to_air)} at the rated indoor airflow" in water_air_row
    assert f"{n} operating points of water-to-air units ({g} product lines, {m} manufacturers)" in water_air_row

    liquid_c = "liquid_source_hx|water_to_air|coaxial|condensing"
    liquid_e = "liquid_source_hx|water_to_air|coaxial|evaporating"
    uses(gshp, "UA_ground_rated", liquid_c)
    uses(gshp, "UA_ground_evap_rated", liquid_e)
    uses(GroundSourceHeatPumpBoiler(), "UA_ground", liquid_e)
    uses(WaterSourceHeatPumpBoiler(), "UA_water", liquid_e)
    liquid = row("Liquid–refrigerant exchanger conductance")
    assert f"Leaving-end approach {_approach(liquid_c)} as a condenser and {_approach(liquid_e)} as an evaporator" in (
        liquid
    )
    (n_c, g_c, m_c), (n_e, g_e, m_e) = _size(liquid_c), _size(liquid_e)
    assert (g_c, m_c) == (g_e, m_e)
    assert f"{n_c} / {n_e} operating points" in liquid and f"({g_c} product lines, {m_c} manufacturers)" in liquid

    plate = "water_condenser|bphe|condensing"
    uses(a2w, "UA_tank_hx", plate)
    assert a2w.m_dot_w_rated == pytest.approx(a2w.hp_capacity / (c_w * a2w.dT_w_rated))
    tank = row("Plate condenser of the boilers (``UA_tank_hx``)")
    assert f"Leaving-end approach {_approach(plate)} with circulating water" in tank
    assert f"``dT_w_rated`` {a2w.dT_w_rated:g} K" in tank
    n, g, m = _size(plate)
    assert f"{n} operating points of the plate condensers of air-to-water units ({g} product lines, {m}" in tank


def test_defaults_conductance_strata_and_holdout_tables_match_the_archive() -> None:
    """The strata table quotes the packaged archive; the hold-out table quotes the archive's regression.json."""
    import json

    text = _read("docs/source/validation/defaults.rst")
    start = text.index(".. list-table:: The strata the models use")
    strata = text[start : text.index("The 59 sources are", start)]
    for label, stratum in HX_STRATA_ROWS.items():
        entry = _hx_entry(stratum)
        leave, itd = entry["band_p10_p50_p90"]["leave_K"], entry["band_p10_p50_p90"]["ITD_K"]
        cells = [c.strip() for c in re.sub(r"\s+", " ", _list_table_row(strata, label)).split(" - ")[1:]]
        n, g, m = _size(stratum)
        assert cells[1:] == [
            f"{n} / {g} / {m}",
            f"{leave[1]:.2f} K ({leave[0]:.1f}–{leave[2]:.1f})",
            f"{itd[1]:.1f} K",
        ], (label, cells)

    regression = json.loads((REPO_ROOT / "validation/default_inputs/hx_approach/regression.json").read_text())
    start = text.index(".. list-table:: Product-line hold-out error")
    holdout = text[start : text.index("The leaving-end approach wins", start)]
    holdout_rows = {
        "Outdoor coil, condensing": "outdoor_coil|fin_tube|condensing",
        "Outdoor coil, evaporating": "outdoor_coil|fin_tube|evaporating",
        "Plate condenser of air-to-water units": "water_condenser|bphe|condensing",
        "Coaxial source exchanger, evaporating": "liquid_source_hx|water_to_air|coaxial|evaporating",
        "Coaxial source exchanger, condensing": "liquid_source_hx|water_to_air|coaxial|condensing",
        "Water-to-air indoor coil, condensing": "indoor_coil|water_to_air|fin_tube|condensing",
    }
    for label, stratum in holdout_rows.items():
        candidates = regression["fits"][stratum]["candidates"]
        cells = [c.strip() for c in re.sub(r"\s+", " ", _list_table_row(holdout, label)).split(" - ")[1:]]
        expected = [
            f"{candidates[k]['group_holdout']['RMSE_K']:.2f} K" for k in ("leaving_approach", "ua_per_duty", "itd")
        ]
        assert cells == expected, (label, cells)
    # The packaged archive is the release regression.json was built for.
    from tmhp.default_inputs import _artifact

    assert _artifact("hx_approach_regression.json")["data_sha256"] == regression["data_sha256"]


def test_defaults_conductance_table_matches_the_constructors() -> None:
    """Every "previous rule → now" cell of the conductance table is what the rule gave and the constructor gives."""
    from tmhp import (
        AirSourceHeatPump,
        AirSourceHeatPumpBoiler,
        GroundSourceHeatPump,
        GroundSourceHeatPumpBoiler,
        WaterSourceHeatPumpBoiler,
    )

    text = _read("docs/source/validation/defaults.rst")
    start = text.index(".. list-table:: Default conductance [W/K], previous rule → now")
    table = text[start : text.index("For the outdoor coil as a condenser", start)]
    rows = {
        "``AirSourceHeatPump`` ``UA_ou_rated`` (condensing)": ([AirSourceHeatPump], "UA_ou_rated", 0.2),
        "``AirSourceHeatPump`` ``UA_ou_evap_rated`` (evaporating)": ([AirSourceHeatPump], "UA_ou_evap_rated", 0.2),
        "``AirSourceHeatPump`` ``UA_iu_rated``": ([AirSourceHeatPump], "UA_iu_rated", 0.16),
        "``AirSourceHeatPumpBoiler`` ``UA_ou_rated``": ([AirSourceHeatPumpBoiler], "UA_ou_rated", 0.14),
        "``UA_tank_hx`` of the three boilers": (
            [AirSourceHeatPumpBoiler, GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler],
            "UA_tank_hx",
            0.2,
        ),
        "``GroundSourceHeatPump`` ``UA_ground_rated`` (condensing)": ([GroundSourceHeatPump], "UA_ground_rated", 0.18),
        "``GroundSourceHeatPump`` ``UA_ground_evap_rated`` (evaporating)": (
            [GroundSourceHeatPump],
            "UA_ground_evap_rated",
            0.18,
        ),
        "``GroundSourceHeatPump`` ``UA_iu_rated``": ([GroundSourceHeatPump], "UA_iu_rated", 0.08),
    }
    for label, (classes, name, before_per_W) in rows.items():
        cells = [c.strip() for c in re.sub(r"\s+", " ", _list_table_row(table, label)).split(" - ")[1:]]
        for cls in classes:
            expected = [
                f"{before_per_W * q:.0f} → {getattr(cls(hp_capacity=q, ref='R32'), name):.0f}"
                for q in (3500.0, 8000.0, 15000.0)
            ]
            assert cells[1:] == expected, (label, cls.__name__, cells)
    source = [
        c.strip()
        for c in re.sub(
            r"\s+", " ", _list_table_row(table, "``UA_ground`` / ``UA_water`` of the liquid-source boilers")
        ).split(" - ")[1:]
    ]
    for cls, name in ((GroundSourceHeatPumpBoiler, "UA_ground"), (WaterSourceHeatPumpBoiler, "UA_water")):
        expected = [
            f"{0.18 * q:.0f} → {getattr(cls(hp_capacity=q, ref='R32'), name):.0f}" for q in (3500.0, 8000.0, 15000.0)
        ]
        assert source[1:] == expected, (cls.__name__, source)


def test_defaults_summary_states_the_shipped_rules() -> None:
    """The Summary table -- not some historical sentence elsewhere -- states each shipped value."""
    import json

    from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION, DOMAIN, MAP_DOMAIN, MAP_N_REF_RPS, MAP_TE_SCALE_K
    from tmhp.default_inputs import DEFAULT_DISPLACEMENT_BASIS, _artifact

    table = _summary_table(_read("docs/source/validation/defaults.rst"))
    a2a = AirSourceHeatPump(hp_capacity=5000.0, ref="R32")
    a2w = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")

    _assert_conductance_rows_state_the_shipped_defaults(table, a2a, a2w)

    displacement = re.sub(r"\s+", " ", _list_table_row(table, "Compressor displacement"))
    basis = _artifact("compressor_displacement_regression.json")["bases"][DEFAULT_DISPLACEMENT_BASIS]
    assert f"``η_vol·n`` = {basis['eta_vol_rps']:.1f} rev/s" in displacement
    assert f"{basis['n']} catalogue rows" in displacement and f"{basis['n_families']} families" in displacement
    condition = basis["reference_condition"]
    assert f"({condition['T_evap_C']:g} / {condition['T_cond_C']:g} °C)" in displacement
    # Every family takes it: no constructor is left on the refrigerant-independent constant.
    assert "every family" in displacement and "cm³/rev per 9 kW" not in displacement
    from tmhp import GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler
    from tmhp.default_inputs import estimate_displacement

    for cls in (GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler):
        assert cls(hp_capacity=9000.0, ref="R32").V_cmp_ref == estimate_displacement(9000.0, "R32").value

    efficiency = re.sub(
        r"\s+", " ", _list_table_row(table, "Compressor efficiencies (volumetric, isentropic, electro-mechanical)")
    )
    archive = json.loads((DEFAULTS_ARCHIVE / "coefficients.json").read_text())
    assert f"``{COEFFICIENT_VERSION}``" in efficiency
    assert f"``N / {MAP_N_REF_RPS:g}``" in efficiency and f"``T_e / {MAP_TE_SCALE_K:g}``" in efficiency
    assert "The same maps for every refrigerant and every compressor" in efficiency
    assert "the electrical input is the inverter input" in efficiency
    # The data and the scores are the archive's.
    assert archive["compressor"].startswith("46 cm3/rev R290 scroll")
    low, high = archive["domain"]["N_rps"]
    assert f"{archive['points']} measured points of a 46 cm³/rev R290 scroll at {low:g} to {high:g} rev/s" in (
        efficiency
    )
    cv = {name: archive["maps"][name]["cv_rmse"] for name in ("eta_v", "eta_ov", "f_loss")}
    assert (
        f"{archive['cv']['folds']}-fold cross-validated RMSE {cv['eta_v']:.4f} (:math:`\\eta_v`), "
        f"{cv['eta_ov']:.4f} (:math:`\\eta_{{ov}}`) and {cv['f_loss']:.4f} (:math:`f_{{loss}}`)"
    ) in efficiency
    assert "power of compressor and inverter together" in efficiency
    assert archive["power_basis"].startswith("compressor + inverter")
    assert f"``validation/coefficients/{COEFFICIENT_VERSION}/``" in efficiency

    guards = re.sub(r"\s+", " ", _list_table_row(table, "Efficiency guards"))
    # The data domain of the maps: what ``efficiency_extrapolated`` reports against.
    assert tuple(DOMAIN.rps_range) == (low, high)
    hull = MAP_DOMAIN["hull_PR_Te"]
    prs, temps = [pr for pr, _ in hull], [t for _, t in hull]
    assert f"data domain of the maps {low:g}–{high:g} rev/s" in guards
    expected = f"spans PR {min(prs):.2f}–{max(prs):.2f} and T_e {min(temps):.1f} to {max(temps):.1f} °C"
    assert expected in guards.replace("−", "-"), guards
    # Outside the domain the maps are followed; the flag is the only consequence.
    assert "nothing is held or clamped" in guards and "``efficiency_extrapolated``" in guards

    from tmhp.compressor_envelope import default_rps_max

    speed = re.sub(r"\s+", " ", _list_table_row(table, "Compressor speed range"))
    assert f"``rps_min`` {a2w.rps_min:g} rev/s; ``rps_max = max(120, 1.5 × rps_rated)``" in speed
    assert a2a.rps_min == a2w.rps_min
    assert [default_rps_max(rated) for rated in (40.0, 80.0, 100.0)] == [120.0, 120.0, 150.0]
    assert f"the efficiency data, {low:g} to {high:g} rev/s" in speed
    assert a2w.rps_min < low and default_rps_max(a2w.rps_rated) > high

    limit = re.sub(r"\s+", " ", _list_table_row(table, "Pressure-ratio limit"))
    assert f"``PR_cycle_max`` {a2a.PR_cycle_max:g} (air-to-air), {a2w.PR_cycle_max:g} (air-to-water)" in limit

    # The page names the shipped version where it says how to regenerate it, and links nothing private.
    page = _flat("docs/source/validation/defaults.rst")
    assert f"The shipped compressor maps are version ``{COEFFICIENT_VERSION}``" in page
    assert "notion" not in page.lower()
    for removed in (
        "compressor_efficiency_legacy",
        "default_displacement",
        "ETA_ISEN_MIN",
        "FittedEfficiency",
        "rotary_speed_variant",
        "shao_informed",
        "ossorio_anchored",
        "CompressorSpec",
        "rotary_twin_inverter",
    ):
        assert removed not in page, removed


def test_defaults_displacement_table_matches_the_decision_module() -> None:
    """Every cell of the default-displacement table is what the constructors use."""
    from tmhp.default_inputs import estimate_displacement

    earlier_per_W = 42.0e-6 / 9000.0  # the constant of before #86: 42 cm³/rev per 9 kW, whatever the refrigerant
    text = _read("docs/source/validation/defaults.rst")
    start = text.index(".. list-table:: Default displacement [cm³/rev]")
    table = text[start : text.index("The earlier default, 42 cm³/rev per 9 kW", start)]
    for capacity_kW in (3.5, 8.0, 15.0):
        row = re.sub(r"\s+", " ", _list_table_row(table, f"{capacity_kW:g} kW"))
        cells = [cell.strip() for cell in row.split(" - ")[1:]]
        expected = [capacity_kW * 1000.0 * earlier_per_W * 1e6] + [
            estimate_displacement(capacity_kW * 1000.0, ref).value * 1e6 for ref in ("R32", "R410A", "R290", "R134a")
        ]
        assert cells == [f"{value:.2f}" for value in expected], f"{capacity_kW:g} kW: {cells}"


def test_defaults_airflow_table_matches_the_constructors() -> None:
    """Every "before → after" cell of the airflow table is what the constructors gave and give."""
    from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler, GroundSourceHeatPump

    text = _read("docs/source/validation/defaults.rst")
    start = text.index(".. list-table:: Default rated (= maximum) airflow [m³/s], before → after")
    table = text[start : text.index("Until November 2026 the outdoor airflow", start)]
    rows = {
        "``AirSourceHeatPump`` ``dV_ou_fan_a_rated``": (AirSourceHeatPump, "dV_ou_fan_a_rated", 0.0002),
        "``AirSourceHeatPump`` ``dV_iu_fan_a_rated``": (AirSourceHeatPump, "dV_iu_fan_a_rated", 5.37e-5),
        "``GroundSourceHeatPump`` ``dV_iu_fan_a_rated``": (GroundSourceHeatPump, "dV_iu_fan_a_rated", 5.37e-5),
        "``AirSourceHeatPumpBoiler`` ``dV_fan_a_rated``": (AirSourceHeatPumpBoiler, "dV_fan_a_rated", 0.00015),
    }
    for label, (cls, name, before_per_W) in rows.items():
        row = re.sub(r"\s+", " ", _list_table_row(table, label))
        cells = [cell.strip() for cell in row.split(" - ")[1:]]
        expected = [
            f"{before_per_W * capacity:.3f} → {getattr(cls(hp_capacity=capacity), name):.3f}"
            for capacity in (3500.0, 8000.0, 15000.0)
        ]
        assert cells == expected, f"{label}: {cells}"


def test_scope_page_states_the_high_lift_gate_as_the_results_have_it(points: pd.DataFrame) -> None:
    """The gate table and the reported injection points are the committed results, row for row.

    Every gate point is scored now; one is just outside the tolerance. The solved points are re-solved to
    check that each delivers the duty below the ceiling and lies inside the data of the maps.
    """
    from validation.gate.evaluate import catalogue_point, load_gate
    from validation.parity.run import _build_model, _run_point

    from tmhp.compressor_efficiency import MAP_DOMAIN

    text = _read("docs/source/concepts/scope-and-limits.rst")
    start = text.index("High lift: hot water in cold weather")
    assert text[:start].rstrip().endswith(".. _high-lift-deficit:")  # other pages link to the label
    section = text[start : text.index("A short checklist for any result", start)]
    flat = re.sub(r"\s+", " ", section)
    gate = load_gate()
    tolerance = gate["gate"]["tolerance_rel"]

    def stored(entry):
        catalog, point = catalogue_point(entry)
        row = pd.read_csv(RESULTS / f"{catalog.slug}.csv").set_index("point_id").loc[point.id]
        return catalog, point, row

    within, outside, differences = [], [], []
    for entry in gate["gate"]["catalogue_points"]:
        catalog, point, row = stored(entry)
        assert not catalog.injected and catalog.published_inputs["compressor_type"] == "rotary"
        assert row.usable, catalog.name  # every gate point is scored
        model = _build_model(catalog)
        solved = _run_point(model, catalog, point)
        assert solved["capacity_clamped"] is None and solved["cmp_rpm [rpm]"] / 60.0 < model.rps_max
        assert not solved["efficiency_extrapolated"], catalog.name  # inside the data of the maps
        label = f"{catalog.name} ({catalog.refrigerant}, {catalog.nominal_capacity_kW:g} kW)"
        cells = [c.strip() for c in re.sub(r"\s+", " ", _list_table_row(section, label)).split(" - ")[1:]]
        difference = row.cop_pred / row.cop_target - 1
        differences.append(difference)
        (within if abs(difference) <= tolerance else outside).append(catalog.name)
        assert cells == [
            f"{row.cop_target:.2f}",
            f"{row.cop_pred:.3f}",
            f"{100 * difference:+.1f} %".replace("-", "−"),
            f"pressure ratio {row.pr_cmp:.1f}, {row.rps:.0f} rev/s, isentropic efficiency {row.eta_cmp_isen:.3f}",
        ], (catalog.name, cells)
    assert len(within) == 2 and outside == ["WH-UQ09HE8"]
    assert "two of the three points are within it and the third is just outside it" in flat
    assert "WH-UQ09HE8 lies just past the tolerance" in flat
    assert "All three deliver the published duty below the 120 rev/s ceiling" in flat
    assert "``efficiency_extrapolated`` is false at each" in flat
    top_pr, top_t = max(MAP_DOMAIN["hull_PR_Te"])
    assert f"reach a pressure ratio of {top_pr:.2f} at an evaporating temperature of −{abs(top_t):.0f} °C" in flat
    below = sorted(-100 * d for d in differences)
    assert f"the three single-stage machines of the gate run {below[0]:.0f} to {below[-1]:.0f} % below" in flat

    injection = gate["reported"]["vapour_injection"]["catalogue_points"]
    by_unit: dict[str, list[str]] = {}
    shortfalls = []
    for entry in injection:
        catalog, _, row = stored(entry)
        by_unit.setdefault(f"{catalog.nominal_capacity_kW:g} kW", []).append(
            f"{100 * (row.cop_pred / row.cop_target - 1):+.1f}".replace("-", "−")
        )
        shortfalls.append(-100 * (row.cop_pred / row.cop_target - 1))
    for size, (w55, w65) in by_unit.items():
        assert f"{w55} and {w65} % at {size}" in flat, (size, w55, w65)
    assert min(shortfalls) > 0
    assert f"under-predicted, by {min(shortfalls):.0f} to {max(shortfalls):.0f} % at these points" in flat
    refused = re.sub(r"\s+", " ", text[: text.index("Simplified: computed, but approximately")])
    assert f"run {min(shortfalls):.0f} to {max(shortfalls):.0f} % below their catalogues at A−7/W55 and A−7/W65" in (
        refused
    )

    ashpb = points[_ashpb(points)]
    single = ashpb[~_injected(ashpb)]
    cold = single[single.t_source_C == -7.0]
    mape, bias, evaluated, _ = _stats(cold)
    assert f"the {evaluated} points evaluated at −7 °C run {_signed(bias).replace('-', '−')} % on average" in flat
    assert f"run {_signed(_stats(ashpb[_injected(ashpb)])[1]).replace('-', '−')} % on average and the six" in flat
    assert f"without injection {_signed(_stats(single)[1]).replace('-', '−')} %" in flat


def test_defaults_air_to_water_airflow_law_matches_the_packaged_stratum() -> None:
    """The law, the size of its data and its hold-out error, as the table and the Summary state them."""
    from tmhp.default_inputs import _artifact

    entry = _artifact("fan_airflow_regression.json")["strata"]["airflow|heating|outdoor|air_to_water"]
    law = entry["selected_law"]
    formula = {
        "power": f"``{law['a']:.4f}·Q^{law.get('b', 0):.3f}``",
        "linear": f"``{law['a']:.4f}·Q + {law.get('b', 0):.4f}``",
        "proportional": f"``{law['a']:.4f}·Q``",
    }[law["kind"]]
    low, high = (value / 1000.0 for value in entry["capacity_range_W"])
    text = _read("docs/source/validation/defaults.rst")
    start = text.index(".. list-table:: Catalogue airflow laws the constructors use")
    table = text[start : text.index("The duty is the one the nameplate names", start)]
    row = re.sub(r"\s+", " ", _list_table_row(table, "``AirSourceHeatPumpBoiler`` outdoor, heating-rated"))
    cells = [cell.strip() for cell in row.split(" - ")[1:]]
    assert cells == [
        formula,
        f"{entry['n']} / {entry['n_families']} / {len(entry['manufacturers'])}",
        f"{low:.1f}–{high:.1f}",
        f"{entry['nested_family_cv']['MAPE_pct']:.1f} %",
    ], cells
    summary = re.sub(r"\s+", " ", _list_table_row(_summary_table(text), "Rated outdoor air flow"))
    assert f"Air-to-water: {formula} m³/s (heating-rated)" in summary
    assert f"{entry['n']} air-to-water outdoor units" in summary
    assert f"({entry['n_families']} families, {len(entry['manufacturers'])} manufacturers" in summary
    assert entry.get("status", "shipped") == "shipped"
    # The scatter the section states is the packaged hold-out error, by family and by manufacturer.
    section = re.sub(r"\s+", " ", text[text.index("The air-to-water outdoor stratum is the weakest") :])
    by_maker = entry["nested_manufacturer_cv"]
    biases = entry["manufacturer_holdout_bias_pct"].values()
    assert (
        f"{entry['n']} rows from {entry['n_families']} families and {len(entry['manufacturers'])} manufacturers"
        in section
    )
    assert f"({', '.join(entry['manufacturers'])})" in section
    assert f"misses by {by_maker['MAPE_pct']:.1f} % on average" in section
    assert f"between −{abs(min(biases)):.0f} % and +{max(biases):.0f} % by manufacturer" in section
    assert f"a family held out by {entry['nested_family_cv']['MAPE_pct']:.1f} %" in section


def test_defaults_ablation_table_matches_the_study(points: pd.DataFrame) -> None:
    """The four-description comparison quotes ``coefficient_ablation_parity.csv``, cell for cell."""
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION

    ablation = pd.read_csv(ABLATION).set_index("variant")
    # BITZER data are not used (#222): the quadratic map of 2026-10-06 is no longer a description.
    assert list(ablation.index) == ["ideal", "constant", "legacy-v1", "defaults"]
    section = _defaults_section("Does the map survive both tests?")
    flat = re.sub(r"\s+", " ", section)
    assert "Four compressor descriptions were run through both" in flat
    for variant in ablation.index:
        # Plain cells: reStructuredText does not nest a literal inside bold, so the shipped row is not set in bold.
        row = re.sub(r"\s+", " ", _list_table_row(section, f"``{variant}``"))
        assert "**" not in row, variant
        cells = [cell.strip() for cell in row.split(" - ")[1:]]
        mape = ablation.at[variant, "MAPE_air_to_water_pct"]
        bias = ablation.at[variant, "bias_air_to_water_pct"]
        expected = [f"{mape:.1f} %", f"{_signed(bias)} %".replace("-", "−"), str(int(ablation.at[variant, "points"]))]
        assert cells[1:] == expected, f"{variant}: row {cells!r}, study says {expected!r}"
        if variant == "defaults":
            assert cells[0].startswith("what TMHP ships") and f"``{COEFFICIENT_VERSION}``" in cells[0]

    # The ``defaults`` row is the harness output the index page quotes, and the prose gives it to two decimals.
    mape, bias, evaluated, attempted = _stats(points[_ashpb(points)])
    assert ablation.at["defaults", "MAPE_air_to_water_pct"] == pytest.approx(mape)
    assert ablation.at["defaults", "bias_air_to_water_pct"] == pytest.approx(bias)
    assert int(ablation.at["defaults", "points"]) == int(points.usable.sum())
    ascii_flat = flat.replace("−", "-")
    assert f"The shipped maps come to {mape:.2f} % with a {bias:+.2f} % bias" in ascii_flat
    assert f"of the {attempted} points of the ten units, {evaluated} are evaluated with the defaults" in flat
    assert f"An ideal compressor is {ablation.at['ideal', 'MAPE_air_to_water_pct']:.1f} % out" in flat
    for variant in ("constant", "legacy-v1"):
        assert f"{ablation.at[variant, 'MAPE_air_to_water_pct']:.1f} %" in flat, variant
    # One description on all ten units in every row: the harness passes no compressor kind.
    assert set(points[_ashpb(points)].coefficient_version) == {COEFFICIENT_VERSION}
    assert "Each row puts one description on all ten units" in flat


DEFAULTS_PAGE = "docs/source/validation/defaults.rst"
DEFAULTS_ARCHIVE = REPO_ROOT / "validation" / "coefficients" / "ossorio-r290-te-2026-11-21"
TREND_VERDICT = REPO_ROOT / "validation" / "data" / "en14825_trend_verdict.csv"
TREND_POINTS = REPO_ROOT / "validation" / "data" / "en14825_trend_points.csv"
KEYMARK_SUMMARY = REPO_ROOT / "validation" / "data" / "keymark_en14825_summary.csv"
KEYMARK_GRADIENT = REPO_ROOT / "validation" / "data" / "keymark_en14825_gradient.csv"

_NUMBER_WORDS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
)


def _defaults_section(title: str, underline: str = "-") -> str:
    """The text of one section of defaults.rst, from its title to the next title of the same or a higher level."""
    text = _read("docs/source/validation/defaults.rst")
    start = text.index(f"{title}\n{underline * len(title)}\n")
    levels = "=" if underline == "=" else "=-"
    following = re.search(rf"^[^\n]+\n[{levels}]{{3,}}\n", text[start + len(title) + 1 :], flags=re.MULTILINE)
    return text[start : start + len(title) + 1 + following.start()] if following else text[start:]


def _defaults_cells(table: str, label: str) -> list[str]:
    """The cells after the first of the list-table row whose first line is ``label``."""
    return [cell.strip() for cell in re.sub(r"\s+", " ", _list_table_row(table, label)).split(" - ")[1:]]


def test_defaults_page_names_the_archive_of_the_library() -> None:
    """The archive the page quotes is the one the library's constants were emitted from."""
    import json

    from tmhp.compressor_efficiency import COEFFICIENT_VERSION, MAP_COEFFICIENTS, MAP_DOMAIN

    assert DEFAULTS_ARCHIVE.name == COEFFICIENT_VERSION
    archive = json.loads((DEFAULTS_ARCHIVE / "coefficients.json").read_text())
    assert archive["version"] == COEFFICIENT_VERSION
    for name, coefficients in MAP_COEFFICIENTS.items():
        assert list(coefficients) == archive["maps"][name]["coefficients"], name
    assert [list(v) for v in MAP_DOMAIN["hull_PR_Te"]] == archive["domain"]["hull_PR_Te"]
    section = re.sub(r"\s+", " ", _defaults_section("Compressor efficiency", "="))
    assert f"version ``{COEFFICIENT_VERSION}``" in section
    assert f"``validation/coefficients/{COEFFICIENT_VERSION}/``, and its README" in section


def test_defaults_compressor_maps_quote_the_archive_and_the_library() -> None:
    """The forms, the fit table and the definitions of the three quantities, as the archive and the library hold them."""
    import json

    from tmhp.compressor_efficiency import MAP_N_REF_RPS, MAP_TE_SCALE_K, MapEfficiency, SuctionState

    archive = json.loads((DEFAULTS_ARCHIVE / "coefficients.json").read_text())
    maps = _defaults_section("The maps")
    flat = re.sub(r"\s+", " ", maps)
    assert f":math:`n = N / {MAP_N_REF_RPS:g}`" in flat and f":math:`t = T_e / {MAP_TE_SCALE_K:g}`" in flat
    assert f"{MAP_N_REF_RPS:g} rev/s whatever the rated speed of the machine" in flat
    # Seven terms for f_loss, the same seven and T_e for eta_v and eta_ov.
    assert len(archive["maps"]["f_loss"]["terms"]) == 7 and archive["maps"]["eta_v"]["terms"][-1] == "T_e/10"
    assert (
        archive["maps"]["eta_v"]["terms"]
        == archive["maps"]["eta_ov"]["terms"]
        == [
            *archive["maps"]["f_loss"]["terms"],
            "T_e/10",
        ]
    )
    assert r"f_{loss} = c \cdot [1,\ l,\ l^2,\ l^3,\ n,\ n^2,\ l\,n]" in flat
    assert r"\eta_v,\ \eta_{ov} = c \cdot [1,\ l,\ l^2,\ l^3,\ n,\ n^2,\ l\,n,\ t]" in flat
    assert f"fitted to the {archive['points']} points by least squares" in flat
    start = maps.index(".. list-table:: Fit of the maps")
    table = maps[start:]
    for label, name in ((":math:`\\eta_v`", "eta_v"), (":math:`\\eta_{ov}`", "eta_ov"), (":math:`f_{loss}`", "f_loss")):
        entry = archive["maps"][name]
        assert _defaults_cells(table, label) == [
            f"{entry['rmse']:.4f}",
            f"{entry['cv_rmse']:.4f}",
            f"{entry['max_abs_error']:.3f}",
        ], name
    assert f"{archive['cv']['folds']}-fold CV RMSE" in table

    # The three quantities of a model are the maps combined as the page says.
    modelled = re.sub(r"\s+", " ", _defaults_section("What is modelled, and where each loss sits"))
    assert (
        "``eta_cmp_vol`` is :math:`\\eta_v`, ``eta_cmp`` is :math:`1 - f_{loss}` and ``eta_cmp_isen`` is "
        ":math:`\\eta_{ov} / (1 - f_{loss})`"
    ) in modelled
    from tmhp.compressor_efficiency import map_eta_ov, map_eta_v, map_f_loss

    state = SuctionState(273.15 - 5.0)
    assert MapEfficiency("eta_cmp_vol")(4.0, 50.0, state) == map_eta_v(4.0, 50.0, -5.0)
    assert MapEfficiency("eta_cmp")(4.0, 50.0, state) == 1.0 - map_f_loss(4.0, 50.0)
    assert MapEfficiency("eta_cmp_isen")(4.0, 50.0, state) == pytest.approx(
        map_eta_ov(4.0, 50.0, -5.0) / (1.0 - map_f_loss(4.0, 50.0))
    )
    assert "the power at the **inverter input**" in modelled and "There is no separate drive model (#217)" in modelled
    assert archive["power_basis"].startswith("compressor + inverter")
    assert archive["environment"]["fluid"] == "Propane"
    assert "with CoolProp (propane)" in modelled


def test_defaults_data_section_matches_the_archive() -> None:
    """Points, speeds, evaporating range, pages and the agreement with the author's values are the archive's."""
    import json

    archive = json.loads((DEFAULTS_ARCHIVE / "coefficients.json").read_text())
    raw = pd.read_csv(DEFAULTS_ARCHIVE / "ossorio_thesis_r290_134.csv")
    derived = pd.read_csv(DEFAULTS_ARCHIVE / "points.csv")
    flat = re.sub(r"\s+", " ", _defaults_section("The data")).replace("−", "-")
    assert len(raw) == len(derived) == archive["points"]
    speeds = sorted(int(s) for s in raw.speed_rps.unique())
    listed = ", ".join(str(s) for s in speeds[:-1]) + f" and {speeds[-1]}"
    assert f"holds {len(raw)} measured points of a 46 cm³/rev R290 scroll compressor at {listed} rev/s" in flat
    low, high = archive["domain"]["T_evap_C"]
    assert (raw.Tevap_C.min(), raw.Tevap_C.max()) == (low, high)
    assert f"evaporating at {low:.1f} to {high:.1f} °C" in flat
    pages = f"PDF pp. {raw.source_pdf_page.min()}–{raw.source_pdf_page.max()}"
    assert pages in flat
    check = archive["check"]
    assert (
        f"agree with the author's tabulated values to {check['eta_v_vs_author_max_abs']:.3f} for :math:`\\eta_v` "
        f"and {check['eta_ov_vs_author_max_abs']:.3f} for :math:`\\eta_{{ov}}`"
    ) in flat
    assert "compressor_plus_inverter_power_W" in raw.columns and "compressor_power_W" in raw.columns
    readme = re.sub(r"\s+", " ", (DEFAULTS_ARCHIVE / "README.md").read_text())
    assert "which reports 133 points" in readme and "(133 points)" in flat
    assert "Every input of the fit is committed" in flat


def test_defaults_te_term_and_axis_comparison_quote_the_archive() -> None:
    """Why the T_e term, and the comparison that chose the axes, with the numbers of ``comparison/scores.json``."""
    import json

    from tmhp.compressor_efficiency import map_eta_ov

    scores = json.loads((DEFAULTS_ARCHIVE / "comparison" / "scores.json").read_text())
    measured = scores["measured_r290"]["scores"]
    readme = re.sub(r"\s+", " ", (DEFAULTS_ARCHIVE / "README.md").read_text())

    why = re.sub(r"\s+", " ", _defaults_section("Why the evaporating temperature")).replace("−", "-")
    a, b = measured["a_pr_n"], measured["b_pr_n_te"]
    assert (
        f"cross-validated RMSE falls from {a['cv_eo']:.3f} to {b['cv_eo']:.3f} for :math:`\\eta_{{ov}}` and from "
        f"{a['cv_ev']:.3f} to {b['cv_ev']:.3f} for :math:`\\eta_v`"
    ) in why
    assert a["cv_fl"] == b["cv_fl"]  # f_loss carries no T_e term in either candidate
    for phrase in ("(P_c + 6.70)/(P_e + 0.51)", "rises from 0.60 to 0.70 between T_e = -10 and 15 degrees C"):
        assert phrase in readme, phrase
    assert "(P_c + 6.70)/(P_e + 0.51)" in why and "rises from 0.60 to 0.70" in why
    low, high = map_eta_ov(3.0, 70.0, -10.0), map_eta_ov(3.0, 70.0, 15.0)
    assert f"The map gives {low:.2f} and {high:.2f} there" in why
    assert "a T_e term changes its error by 0.0001" in readme and "one changes its error by 0.0001" in why

    section = _defaults_section("The comparison that chose the axes")
    flat = re.sub(r"\s+", " ", section)
    catalogue = scores["catalogue_fit"]["results"]
    rows = {
        "PR–N quadratic of 2026-11-01": "current_quadratic",
        "(a) PR–N": "a_pr_n",
        "(b) PR–N + :math:`T_e`, adopted": "b_pr_n_te",
        "(c) terminal + inverter": "c_terminal_inverter",
    }
    finals = []
    for label, key in rows.items():
        cells = _defaults_cells(section, label)
        fit = catalogue[key]
        stages = [fit["baseline"]["rmse"], fit["lambda_only"]["rmse"], fit["shape"]["rmse"]]
        finals.append(stages[-1])
        assert cells[1] == " / ".join(f"{v:.3f}" for v in stages), (label, cells)
        if key == "current_quadratic":
            grid = measured["current_quadratic_in_grid"]["eo"]
            assert cells[0] == f"{measured[key]['eo']:.3f} (inside its grid {grid:.3f})", cells
        else:
            assert cells[0] == f"{measured[key]['eo']:.3f} ({measured[key]['cv_eo']:.3f})", (label, cells)
    # The adopted candidate is the best on the measured compressor; the catalogue does not separate them.
    assert min(measured, key=lambda k: measured[k]["eo"]) == "b_pr_n_te"
    assert max(finals) - min(finals) <= 0.04 + 5e-4
    assert "All four end within 0.04 of each other" in flat
    levels = [catalogue[k][stage]["params"][0] for k in rows.values() for stage in ("lambda_only", "shape")]
    assert f"lifts :math:`\\lambda_{{ov}}` to {min(levels):.1f}–{max(levels):.1f}" in flat
    warnings_ = sorted(catalogue[k]["shape"]["eta_is_warnings"] for k in rows.values())
    assert f"at {warnings_[0]} or {warnings_[-1]} of the 50 rows" in flat and set(warnings_) == {15, 16}
    assert "An inference, not a result of the comparison" in flat
    assert "the calibration takes up gaps that are not the compressor's" in flat


def test_defaults_limits_quote_the_archive_and_the_library(summary: pd.DataFrame, points: pd.DataFrame) -> None:
    """The limits are stated with the numbers of the library and of the committed results."""
    from tmhp.compressor_efficiency import DOMAIN, map_eta_ov, map_eta_v

    flat = re.sub(r"\s+", " ", _defaults_section("Limits"))
    for heading in (
        "**One R290 scroll for every machine.**",
        "**Uncalibrated, the defaults run low.**",
        "**Over-compression at a low pressure ratio.**",
        f"**Outside {DOMAIN.rps_range[0]:g}–{DOMAIN.rps_range[1]:g} rev/s the maps are extrapolated.**",
        "**The volumetric map passes one**",
    ):
        assert heading in flat, heading

    # The validation set the maps are applied to: rotaries and swings, scrolls only with injection.
    water = summary[(summary.model_class == "ASHPB") & (summary.status == "adopted")]
    air = summary[(summary.model_class == "ASHP") & (summary.status == "adopted")]
    assert set(air.compressor_type) == {"swing"} and int((water.compressor_type == "rotary").sum()) == 6
    assert set(water[water.compressor_type == "scroll"].injection) == {"vapor", "flash"}
    assert "to the rotary and swing compressors of most of the validation set" in flat

    # The uncalibrated biases, per family.
    a2w, a2a = _stats(points[_ashpb(points)])[1], _stats(points[_daikin(points)])[1]
    w, a = (_signed(v).replace("-", "−") for v in (a2w, a2a))
    assert f"a COP bias of {w} % for the ten air-to-water units and {a} % for the five air-to-air units" in flat
    daikin = points[_daikin(points)]
    assert all(_stats(daikin[daikin["mode"] == m])[1] < 0 for m in ("cooling", "heating"))  # "cooling and heating"
    intro = re.sub(r"\s+", " ", _defaults_section("Compressor efficiency", "="))
    assert f"bias of {w} % for the air-to-water units and {a} % for the" in intro

    # Over-compression: the map at a low and at a middle pressure ratio.
    low, mid = map_eta_ov(1.5, 70.0, 10.0), map_eta_ov(4.0, 70.0, 10.0)
    assert low < 0.7 * mid
    assert f":math:`\\eta_{{ov}}` is {low:.2f} at a pressure ratio of 1.5 against {mid:.2f} at 4" in flat
    assert "an inference about other machines: the archive holds no rotary data" in flat

    # Outside the speed range of the data.
    speeds = {n: map_eta_ov(3.0, n, 0.0) for n in (70.0, 30.0, 15.0, 110.0, 120.0)}
    assert (
        f"is {speeds[70.0]:.3f} at 70 rev/s, {speeds[30.0]:.3f} at 30 and {speeds[15.0]:.3f} at 15 rev/s, and "
        f"{speeds[110.0]:.3f} at 110 and {speeds[120.0]:.3f} at 120 rev/s"
    ) in flat

    # Above one: the volumetric map at a low lift and a warm evaporator.
    value = map_eta_v(1.5, 70.0, 20.0)
    assert value > 1.0 and f"({value:.3f} at a pressure ratio of 1.5, :math:`T_e` = 20 °C and 70 rev/s)" in flat


def test_defaults_rated_speed_table_matches_the_constructors() -> None:
    """Rated speeds with the catalogue displacement and with the earlier constant, as the present library gives them."""
    from tmhp import (
        AirSourceHeatPump,
        AirSourceHeatPumpBoiler,
        GroundSourceHeatPump,
        GroundSourceHeatPumpBoiler,
        WaterSourceHeatPumpBoiler,
    )
    from tmhp.compressor_efficiency import DOMAIN
    from tmhp.compressor_envelope import default_rps_max
    from tmhp.default_inputs import estimate_displacement
    from tmhp.reference_state import ReferenceStateError

    earlier_per_W = 42.0e-6 / 9000.0
    section = _defaults_section("Compressor displacement", "=")
    start = section.index(".. list-table:: Rated speed of a default machine [rev/s]")
    table = section[start : section.index("Both columns are the present library", start)]
    rows = {
        "``AirSourceHeatPump``, 4 kW, R32": (AirSourceHeatPump, 4000.0, "R32"),
        "``AirSourceHeatPumpBoiler``, 15 kW, R290": (AirSourceHeatPumpBoiler, 15000.0, "R290"),
        "``GroundSourceHeatPump``, 4 kW, R32": (GroundSourceHeatPump, 4000.0, "R32"),
        "``GroundSourceHeatPumpBoiler``, 8 kW, R290": (GroundSourceHeatPumpBoiler, 8000.0, "R290"),
        "``GroundSourceHeatPumpBoiler``, 8 kW, R134a": (GroundSourceHeatPumpBoiler, 8000.0, "R134a"),
        "``WaterSourceHeatPumpBoiler``, 8 kW, R290": (WaterSourceHeatPumpBoiler, 8000.0, "R290"),
    }
    n_low, n_high = DOMAIN.rps_range
    below = above = unbuildable = 0
    befores = {}
    for label, (cls, capacity, ref) in rows.items():
        model = cls(hp_capacity=capacity, ref=ref)
        assert model.V_cmp_ref == estimate_displacement(capacity, ref).value, label
        assert n_low <= model.rps_rated <= n_high, label
        try:
            before = f"{cls(hp_capacity=capacity, ref=ref, V_cmp_ref=earlier_per_W * capacity).rps_rated:.1f}"
        except ReferenceStateError:
            before = "not constructible"
            unbuildable += 1
        else:
            below += float(before) < n_low
            above += float(before) > n_high
        befores[label] = before
        assert _defaults_cells(table, label) == [before, f"{model.rps_rated:.1f}"], label
    assert (below, above, unbuildable) == (1, 0, 0)
    flat = re.sub(r"\s+", " ", section)
    assert f"speed range of the efficiency data, {n_low:g} to {n_high:g} rev/s" in flat
    assert "with the constant one was below it." in flat
    # The boilers the constant was removed from last: the default machines, before and after.
    for cls in (GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler):
        assert (cls().hp_capacity, cls().ref) == (8000.0, "R290")
    ground = {ref: GroundSourceHeatPumpBoiler(hp_capacity=8000.0, ref=ref) for ref in ("R32", "R410A", "R290", "R134a")}
    assert (
        f"at 8 kW, {ground['R32'].rps_rated:.1f} rev/s with R32, {ground['R410A'].rps_rated:.1f} with R410A, "
        f"{ground['R290'].rps_rated:.1f} with R290 and {ground['R134a'].rps_rated:.1f} with R134a, above the 60 rev/s "
        "the catalogue rows are published at"
    ) in flat
    for machine in ground.values():
        assert 60.0 < machine.rps_rated <= 80.0 and machine.rps_max == default_rps_max(machine.rps_rated) == 120.0
    assert f"Its speed ceiling, 120 rev/s, lies above the {n_high:g} rev/s the efficiency data reach" in flat
    water = WaterSourceHeatPumpBoiler(hp_capacity=8000.0, ref="R290")
    label = "``GroundSourceHeatPumpBoiler``, 8 kW, R134a"
    assert (
        f"goes from a rated speed of {befores['``GroundSourceHeatPumpBoiler``, 8 kW, R290']} to "
        f"{ground['R290'].rps_rated:.1f} rev/s and the water-source boiler from "
        f"{befores['``WaterSourceHeatPumpBoiler``, 8 kW, R290']} to {water.rps_rated:.1f}, and an R134a ground-source "
        f"boiler from {befores[label]} to {ground['R134a'].rps_rated:.1f}"
    ) in flat


def test_defaults_trend_verdict_prose_matches_the_study() -> None:
    """Counts, medians, percentiles and speeds of the EN 14825 paragraphs are the committed study's."""
    from tmhp.compressor_efficiency import DOMAIN

    verdict = pd.read_csv(TREND_VERDICT)
    trend = pd.read_csv(TREND_POINTS)
    reference = pd.read_csv(KEYMARK_SUMMARY).set_index(["application", "point"])
    gradient = pd.read_csv(KEYMARK_GRADIENT).set_index("application")
    flat = re.sub(r"\s+", " ", _defaults_section("Does the map survive both tests?"))
    key = ["application", "capacity_kW", "refrigerant", "oversizing"]

    def outside(variant: str) -> pd.DataFrame:
        """Per run and point: -1 below the certified p10, +1 above the p90, 0 inside."""
        cop = trend[trend.variant == variant].pivot_table(index=key, columns="point", values="cop")
        return _defaults_outside_band(cop)

    def by_application(mask: pd.Series) -> tuple[int, int]:
        counts = mask.groupby(level="application").sum()
        return int(counts.get("low", 0)), int(counts.get("medium", 0))

    by_variant = verdict.groupby("variant")
    assert sorted(by_variant.groups) == ["constant", "defaults", "ideal", "legacy-v1"]
    assert (by_variant.size() == 30).all() and (by_variant.C1_shape.sum() == 30).all()
    shipped = verdict[verdict.variant == "defaults"]

    # Shape: the gradient criterion, for the default and for the descriptions it does not separate from it.
    assert int(shipped.C3_gradient.sum()) == 30 and "lands inside the certified p10–p90 in all thirty runs" in flat
    medians = shipped.groupby("application").gradient_DA.median()
    assert (
        f"median {medians['low']:.2f} modelled against {gradient.ratio_median['low']:.2f} certified for the "
        f"low-temperature application and {medians['medium']:.2f} against {gradient.ratio_median['medium']:.2f} for "
        "the medium-temperature one"
    ) in flat
    assert (
        f"of the {int(reference.n[('low', 'A')]):,} certified low-temperature records and 98.7 % of the "
        f"{int(reference.n[('medium', 'A')]):,} medium-temperature ones"
    ) in flat
    assert int(by_variant.C3_gradient.sum()["constant"]) == 30
    assert "the ``constant`` set, which has no speed dependence at all, passes it in all thirty runs as well" in flat
    assert (
        f"``ideal`` passes in {int(by_variant.C3_gradient.sum()['ideal'])} and ``legacy-v1`` in "
        f"{int(by_variant.C3_gradient.sum()['legacy-v1'])}"
    ) in flat

    # Level: every run passes, and how many runs the other descriptions pass.
    flags = outside("defaults")
    inside = (flags == 0).all(axis=1)
    assert int(inside.sum()) == int(shipped.C2_level.sum()) == 30
    assert "All thirty runs have all four points inside the certified p10–p90" in flat
    level = {v: int((outside(v) == 0).all(axis=1).sum()) for v in ("legacy-v1", "constant", "ideal")}
    assert level == {v: int(by_variant.C2_level.sum()[v]) for v in level}
    assert level["constant"] == level["ideal"] == 0
    assert f"``legacy-v1`` has {level['legacy-v1']} such runs, ``constant`` and ``ideal`` none" in flat

    def percentiles(frame: pd.DataFrame, application: str) -> str:
        row = frame[
            (frame.application == application)
            & (frame.refrigerant == "R32")
            & (frame.capacity_kW == 9.0)
            & (frame.oversizing == 0.667)
        ].iloc[0]
        return " / ".join(f"{row[f'pct_{point}']:.0f}" for point in "ABCD")

    assert f"sits at the {percentiles(shipped, 'low')}nd percentile at points A / B / C / D" in flat
    assert f"the medium-temperature one at {percentiles(shipped, 'medium')}." in flat

    # Where the compressor runs at each point, against the data of the maps.
    n_low = DOMAIN.rps_range[0]
    speed = trend[trend.variant == "defaults"].groupby("point").rps
    assert f"At A it turns at {speed.min()['A']:.0f} to {speed.max()['A']:.0f} rev/s" in flat
    slow_b = int((trend[(trend.variant == "defaults") & (trend.point == "B")].rps < n_low).sum())
    assert slow_b == 30
    assert (
        f"at B at {speed.min()['B']:.0f} to {speed.max()['B']:.0f}, below the {n_low:g} rev/s where the data of the "
        f"maps start, in all {slow_b} runs"
    ) in flat
    assert speed.min()["C"] == speed.max()["C"] == speed.min()["D"] == speed.max()["D"] == 15.0
    assert "at C and D at 15, the speed floor" in flat

    # The history of the count: the present value is the committed verdict's.
    assert f"and {int(inside.sum())} now, with the maps of one R290 scroll" in flat


def test_defaults_figures_measured_on_a_model_are_what_the_library_returns() -> None:
    """The figures the page quotes from a built machine: COP down to the speed floor, the lift of a split unit."""
    from validation.analysis.en14825_trend import en14825_water_flow, solve_at_leaving_water

    from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
    from tmhp.compressor_efficiency import DOMAIN

    # Frost left out, the running machine: full load, 75 % and the speed floor at two conditions.
    limits = re.sub(r"\s+", " ", _defaults_section("Limits")).replace("−", "-")
    model = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32", Cd_cycling=None, defrost=False)
    flow = en14825_water_flow(model.hp_capacity, "low")
    figures = {}
    for t_outdoor, leaving in ((-7.0, 45.0), (7.0, 35.0)):
        cops = []
        for fraction in (1.0, 0.75, 0.1):
            result, _ = solve_at_leaving_water(model, t_outdoor, leaving, model.hp_capacity * fraction, flow)
            assert result["failure_reason"] == "none"
            cops.append(float(result["cop_sys [-]"]))
        # The smallest request is below what the machine delivers at its floor: it runs at ``rps_min``.
        assert result["capacity_clamped"] == "min" and result["cmp_rpm [rpm]"] / 60.0 == pytest.approx(model.rps_min)
        figures[t_outdoor] = (*cops, 100.0 * float(result["Q_min [W]"]) / model.hp_capacity)
        assert model.rps_min < DOMAIN.rps_range[0] and bool(result["efficiency_extrapolated"])
    cold, mild = figures[-7.0], figures[7.0]
    assert (
        f"at -7 °C outdoor air and 45 °C leaving water has a COP of {cold[0]:.2f} at full load, {cold[1]:.2f} at 75 % "
        f"load and {cold[2]:.2f} at its speed floor ({cold[3]:.0f} % of the nameplate); at +7 °C and 35 °C it has "
        f"{mild[0]:.2f}, {mild[1]:.2f} and {mild[2]:.2f} ({mild[3]:.0f} %)"
    ) in limits

    # The pressure ratio a default split unit reaches delivering its nameplate heating from -15 degC.
    ratios = []
    for capacity, ref in ((3500.0, "R32"), (4000.0, "R32"), (8000.0, "R410A")):
        unit = AirSourceHeatPump(hp_capacity=capacity, ref=ref)
        result = unit.analyze_steady(Q_r_iu=-capacity, T0=-15.0, T_a_room=20.0, return_dict=True, verbose=False)
        assert result["failure_reason"] == "none" and result["capacity_clamped"] is None
        ratios.append(result["P_ref_cmp_out [Pa]"] / result["P_ref_cmp_in [Pa]"])
    summary = re.sub(r"\s+", " ", _list_table_row(_summary_table(_read(DEFAULTS_PAGE)), "Pressure-ratio limit"))
    assert (
        f"delivering its nameplate heating from −15 °C outdoor air reaches {min(ratios):.1f} to {max(ratios):.1f}"
        in (summary)
    )


_TREND_KEY = ["application", "capacity_kW", "refrigerant", "oversizing"]


def _defaults_trend_cops(**model_settings) -> pd.DataFrame:
    """COP at the four EN 14825 points of the study's thirty cases, for a boiler built with ``model_settings``."""
    from validation.analysis.en14825_trend import (
        CONFIGURATIONS,
        EN14825_POINTS,
        OVERSIZING,
        en14825_water_flow,
        solve_at_leaving_water,
    )

    from tmhp import AirSourceHeatPumpBoiler

    rows = []
    for application, test_points in EN14825_POINTS.items():
        for capacity, refrigerant in CONFIGURATIONS:
            model = AirSourceHeatPumpBoiler(hp_capacity=capacity, ref=refrigerant, Cd_cycling=None, **model_settings)
            flow = en14825_water_flow(capacity, application)
            for oversizing in OVERSIZING:
                row = dict(zip(_TREND_KEY, (application, capacity / 1000.0, refrigerant, oversizing), strict=True))
                for label, t_outdoor, leaving, load_ratio in test_points:
                    result, _ = solve_at_leaving_water(
                        model, t_outdoor, leaving, capacity * oversizing * load_ratio, flow
                    )
                    assert result["failure_reason"] == "none", (application, refrigerant, label)
                    row[label] = float(result["cop_sys [-]"])
                rows.append(row)
    return pd.DataFrame(rows).set_index(_TREND_KEY)


def _defaults_outside_band(cops: pd.DataFrame) -> pd.DataFrame:
    """Per case and point: -1 below the certified p10, +1 above the p90, 0 inside (``keymark_en14825_summary.csv``)."""
    reference = pd.read_csv(KEYMARK_SUMMARY).set_index(["application", "point"])
    application = cops.index.get_level_values("application")
    flags = pd.DataFrame(index=cops.index)
    for point in "ABCD":
        p10 = application.map(lambda a, p=point: reference.cop_p10[(a, p)]).to_numpy()
        p90 = application.map(lambda a, p=point: reference.cop_p90[(a, p)]).to_numpy()
        flags[point] = (cops[point].to_numpy() > p90).astype(int) - (cops[point].to_numpy() < p10).astype(int)
    return flags


def _defaults_count(mask: pd.Series) -> tuple[int, int]:
    counts = mask.groupby(level="application").sum()
    return int(counts.get("low", 0)), int(counts.get("medium", 0))


def test_defaults_frost_paragraph_is_what_the_models_return() -> None:
    """The trajectory without the frost adjustment and with it in full at every load, on the study's thirty cases."""
    from tmhp.defrost import DefrostModel

    committed = pd.read_csv(TREND_POINTS).query("variant == 'defaults'")
    shipped = committed.pivot_table(index=_TREND_KEY, columns="point", values="cop")
    without = _defaults_trend_cops(defrost=False)
    in_full = _defaults_trend_cops(defrost=DefrostModel(load_weighted=False))
    flat = re.sub(r"\s+", " ", _defaults_section("Does the map survive both tests?"))

    # Frost acts on the two cold points only.
    for other in (without, in_full):
        assert (other[["C", "D"]] - shipped.loc[other.index, ["C", "D"]]).abs().to_numpy().max() < 1e-6
    flags = _defaults_outside_band(without)
    above_a = _defaults_count(flags.A > 0)
    passing = int((flags == 0).all(axis=1).sum())
    assert above_a[1] == 0 and passing + above_a[0] == 30
    assert (
        f"{_NUMBER_WORDS[above_a[0]]} of the 15 low-temperature runs are above the 90th percentile at point A" in flat
    )
    assert f"the other {passing} runs pass" in flat
    medians = (without.D / without.A).groupby(level="application").median()
    assert f"without it the two medians are {medians['low']:.2f} and {medians['medium']:.2f}" in flat
    flags = _defaults_outside_band(in_full)
    assert not (flags == 0).all(axis=1).any()
    assert int((flags.B < 0).sum()) == 30
    assert "point B below the 10th percentile in all 30 runs, no run passing" in flat
    # How much of the full correction the shipped, load-weighted adjustment keeps at each cold point.
    case = ("low", 9.0, "R32", 0.667)
    kept = {
        point: (without.loc[case, point] - shipped.loc[case, point])
        / (without.loc[case, point] - in_full.loc[case, point])
        for point in "AB"
    }
    assert kept["A"] > 0.9 and 0.2 < kept["B"] < 0.3
    assert "point A keeps over nine tenths of the correction and point B a quarter of it" in flat


def test_implied_conductance_table_matches_the_decomposition() -> None:
    """The per-machine back-out in index.rst quotes ``residual_decomposition.csv``."""
    decomposition = pd.read_csv(REPO_ROOT / "validation" / "data" / "residual_decomposition.csv")
    text = _read("docs/source/validation/index.rst")
    start = text.index("Where each machine sits in the measured band")
    section = text[start : text.index(".. figure::", start)]
    from validation.analysis.residual_decomposition import measured_band

    low, high = measured_band()
    assert f"The band is **Q/{low:.1f} to Q/{high:.1f}**" in re.sub(r"\s+", " ", section)
    for _, machine in decomposition.iterrows():
        row = re.sub(r"\s+", " ", _list_table_row(section, machine.unit))
        divisor = machine.implied_ua_divisor
        expected = "none in Q/2–Q/16" if pd.isna(divisor) else f"Q/{divisor:.1f}"
        assert f"{machine.nameplate_EER:.2f}" in row, f"{machine.unit}: EER, row {row.strip()!r}"
        assert expected in row, f"{machine.unit}: row {row.strip()!r}, decomposition says {expected!r}"
        # In the band or outside it, and on which side, as the back-out has it.
        if low <= divisor <= high:
            assert row.strip().endswith("inside"), (machine.unit, row)
        else:
            side = "low end" if divisor > high else "high end"
            assert f"**outside** ({side})" in row, (machine.unit, row)
