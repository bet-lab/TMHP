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

pytestmark = pytest.mark.skipif(not SUMMARY.exists(), reason="parity harness has not been run in this checkout")

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
        f"Air-to-air cooling is centred ({_signed(cool_bias)} %; from {_signed(cool_lo)} % at {lo:g} °C "
        f"to {_signed(cool_hi)} % at {hi:g} °C outdoor"
    ) in text
    assert abs(cool_bias) < 3.0, "cooling is no longer centred"
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
        f"where the adjustment applies ({round(plus_two):+d} % at +2 °C, "
        f"{round(minus_seven):+d} % at -7 °C, {round(below):+d} % below;"
    ) in text
    for injected, words in ((False, "six rotaries without injection"), (True, "four injection scrolls")):
        rows = ashpb[_injected(ashpb) == injected]
        mape, bias, evaluated, attempted = _stats(rows)
        assert f"{words}" in text and f"{mape:.1f} % / {_signed(bias)} % ({evaluated}/{attempted}" in text, words
    bad = ashpb[~ashpb.usable]
    assert f"The {len(bad)} air-to-water points not evaluated all run below the published duty" in text
    assert set(bad.failure_reason) == {"capacity_short"}


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

    per_unit = {slug: _stats(g) for slug, g in ashpb.groupby("slug")}
    outlier = per_unit["panasonic_wh_mxc16j9e8"][1]
    assert f"unit runs {_signed(outlier)} % optimistic where the set runs {_signed(_stats(ashpb)[1])} %" in text

    r410a = [per_unit[s][0] for s in per_unit if ashpb[ashpb.slug == s].refrigerant.iat[0] == "R410A"]
    assert f"R-410A air-to-water units sit at {min(r410a):.1f} to {max(r410a):.1f} % MAPE" in text
    spread = [m for m, *_ in per_unit.values()]
    assert f"three of the four lowest in the {min(spread):.1f} to {max(spread):.1f} % spread" in text
    # "three of the four lowest": one other air-to-water unit (the 16 kW R32 one, with the default rotary speed
    # shape) is tighter than the widest R-410A unit, and no second one is. Until the default speed shape of
    # November 2026 the three were the lowest outright.
    others = [per_unit[s][0] for s in per_unit if ashpb[ashpb.slug == s].refrigerant.iat[0] != "R410A"]
    assert len(r410a) == 3 and sum(m < max(r410a) for m in others) == 1, (r410a, others)
    assert min(r410a) == min(spread)


def test_index_air_to_water_unit_table_matches_the_harness(points: pd.DataFrame) -> None:
    """One row per adopted air-to-water unit: its published compressor and injection, the library type, its figures."""
    from validation.parity.spec import MODEL_COMPRESSOR_TYPE

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
            f"{first.compressor_type} (``{MODEL_COMPRESSOR_TYPE[first.compressor_type]}``)",
            first.injection,
            f"{mape:.1f} %",
            f"{_signed(bias)} %",
            f"{evaluated} / {attempted}",
        ], (first.unit, cells)


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
    assert f"{_NUMBER_WORDS[len(at_ceiling)].capitalize()} run at the 120 rev/s speed ceiling" in text
    cold = at_ceiling[at_ceiling.t_source_C == -15.0]
    assert f"{_NUMBER_WORDS[len(cold)]} at -15 °C outdoor" in text
    assert len(at_ceiling) - len(cold) == 1 and "and WH-UQ12 at -7 °C / 55 °C." in text
    rest = at_ceiling[at_ceiling.t_source_C != -15.0].iloc[0]
    assert (rest.slug, rest.t_source_C, rest.t_sink_C) == ("panasonic_wh_uq12he8", -7.0, 55.0)
    assert f"The other {_NUMBER_WORDS[len(samsung)]} are the Samsung unit at 65 °C leaving water" in text
    assert "**Every air-to-air point is evaluated**" in text
    assert points[points.model_class == "ASHP"].usable.all()

    # The point the default rotary speed shape brought below the ceiling: evaluated, at the speed the page quotes;
    # with the other shape it is re-solved here, at the ceiling and with the ceiling raised.
    import warnings

    from validation.parity.run import _build_model, _run_point
    from validation.parity.spec import load_all

    from tmhp.compressor_efficiency import CompressorSpec

    mxc12 = ashpb[(ashpb.slug == "panasonic_wh_mxc12j9e8") & (ashpb.t_source_C == -15.0) & (ashpb.t_sink_C == 55.0)]
    (row,) = mxc12.itertuples()
    assert row.usable and row.rps < 120.0
    (catalog,) = [c for c in load_all() if c.slug == row.slug]
    (point,) = [p for p in catalog.points if p.id == row.point_id]
    other = {"compressor_type": CompressorSpec(catalog.model_compressor_type, rotary_speed_variant="shao_informed")}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ceiling = _run_point(_build_model(catalog, other), catalog, point)
        raised = _run_point(_build_model(catalog, {**other, "rps_max": 140.0}), catalog, point)
    assert ceiling["capacity_binding"] == "compressor" and raised["capacity_clamped"] is None
    share = ceiling["Q_ref_tank [W]"] / (point.q_kW * 1000.0)
    assert (
        f"WH-MXC12 at -15 °C / 55 °C delivered {100 * share:.1f} % of the duty at 120 rev/s with "
        f"``shao_informed`` (it needed {raised['cmp_rpm [rpm]'] / 60.0:.1f} rev/s) and meets it at "
        f"{row.rps:.1f} rev/s with the default shape"
    ) in text
    assert (
        f"The isentropic efficiency there is lower ({row.eta_cmp_isen:.2f} against {ceiling['eta_cmp_isen [-]']:.2f})"
    ) in text

    injected, single = ashpb[_injected(ashpb)], ashpb[~_injected(ashpb)]

    def by_outdoor(rows: pd.DataFrame) -> list[str]:
        sel = (rows.t_source_C >= 7.0, rows.t_source_C == 2.0, rows.t_source_C == -7.0, rows.t_source_C < -7.0)
        return [f"{_signed(_stats(rows[s])[1])} %" for s in sel]

    a, b, c, d = by_outdoor(single)
    assert f"the machines without it run {a} frost-free, {b} at +2 °C, {c} at -7 °C and {d} below" in text
    a, b, c, d = by_outdoor(injected)
    assert f"the injection machines {a}, {b}, {c} and {d}".replace("+", "") in text.replace("+", "")

    fujitsu = _stats(points[_fujitsu(points)])
    assert f"to {_signed(fujitsu[1])} % (all {fujitsu[3]})" in text and fujitsu[2] == fujitsu[3]

    daikin = points[_daikin(points)]
    cooling = daikin[daikin["mode"] == "cooling"]
    lo, hi = cooling.t_source_C.min(), cooling.t_source_C.max()
    assert (
        f"from {_signed(_stats(cooling[cooling.t_source_C == lo])[1])} % at {lo:g} °C to "
        f"{_signed(_stats(cooling[cooling.t_source_C == hi])[1])} % at {hi:g} °C outdoor"
    ) in text
    heating = daikin[daikin["mode"] == "heating"]
    temps = {t: _signed(_stats(heating[heating.t_source_C == t])[1]) for t in sorted(heating.t_source_C.unique())}
    assert (
        f"the bias is {temps[0.0]} % at 0 °C, {temps[-5.0]} % at -5 °C, {temps[-10.0]} % at -10 °C and "
        f"{temps[-15.0]} % at -15 °C outdoor, against {temps[6.0]} % and {temps[10.0]} % at 6 and 10 °C"
    ).replace("+", "") in text.replace("+", "")


def test_pages_state_the_cop_boundary_and_how_the_rotary_speed_shape_was_chosen() -> None:
    """The fixed control power in the compared COP and the choice of the default speed shape, as the code has them.

    The figures of the non-default shape are those the coefficient archive records
    (``README.md``, "Rotary speed-shape variants"); each one quoted on a page must be in that record.
    """
    from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
    from tmhp.compressor_efficiency import ROTARY_SPEED_VARIANTS
    from tmhp.control_power import CONTROL_POWER_SINK_AIR_W, CONTROL_POWER_SINK_WATER_W

    default, other = ROTARY_SPEED_VARIANTS
    assert (AirSourceHeatPumpBoiler().E_ctrl, AirSourceHeatPump().E_ctrl) == (
        CONTROL_POWER_SINK_WATER_W,
        CONTROL_POWER_SINK_AIR_W,
    )
    powers = f"{CONTROL_POWER_SINK_WATER_W:g} W air-to-water, {CONTROL_POWER_SINK_AIR_W:g} W air-to-air"
    index = _flat("docs/source/validation/index.rst")
    readme = _flat("README.md")
    scope = _flat("docs/source/concepts/scope-and-limits.rst")
    for name, text in (("index.rst", index), ("README.md", readme)):
        assert powers in text, name
        assert "EN 14511-3" in text and "all control and safety devices" in text, name
    for name, text in (("index.rst", index), ("scope-and-limits.rst", scope)):
        assert ":ref:`fixed-control-power`" in text, name
    for name, text in (("index.rst", index), ("README.md", readme), ("scope-and-limits.rst", scope)):
        assert f"`{default}`" in text and f"`{other}`" in text, name
        assert "EN 14825 part-load comparison" in text, name
        assert "not independent validation of the rotary types" in text, name
        assert "shape chosen by the trajectory" not in text and "C2 9" not in text, name

    archive = re.sub(
        r"\s+", " ", _read("validation/coefficients/semi-empirical-2026-11-15/README.md").replace("−", "-")
    )
    record = archive[archive.index("**What this costs.**") : archive.index("**How the third pass had chosen")]
    for quoted in (
        "8.49 / -6.30 % under `b` against 8.51 / -5.36 % under `a`",
        "heating 12.51 / -11.52 % against 11.48 / -9.71 %",
        "cooling 6.91 / -1.12 % against 6.82 / -3.73 %",
    ):
        assert quoted in record, quoted
    assert (
        "air-to-water set reads 8.51 % MAPE and -5.36 % bias against 8.49 % and -6.30 %, air-to-air heating "
        "11.48 % and -9.71 % against 12.51 % and -11.52 %, and air-to-air cooling 6.82 % and -3.73 % against "
        "6.91 % and -1.12 %"
    ) in index
    assert (
        "air-to-water reads 8.51 % / -5.36 %, air-to-air heating 11.48 % / -9.71 % against 12.51 % / -11.52 %, "
        "and cooling 6.82 % / -3.73 % against 6.91 % / -1.12 %"
    ) in readme


def test_result_keys_state_the_data_hull_of_each_type() -> None:
    """``efficiency_extrapolated`` is described against the data hull the library carries, per type."""
    from tmhp.compressor_efficiency import SEMI_EMPIRICAL_TYPES

    text = _flat("docs/source/concepts/result-keys.rst")
    start = text.index("* - ``efficiency_extrapolated``")
    row = text[start : text.index("* - ``discharge_control``", start)]

    def span(compressor_type: str) -> str:
        hull = SEMI_EMPIRICAL_TYPES[compressor_type]["hull_PR_N"]
        prs, speeds = [pr for pr, _ in hull], [n for _, n in hull]
        return f"PR {min(prs):.2f}–{max(prs):.2f}", f"{min(speeds):g}–{max(speeds):g} rev/s"

    rotary = span("rotary_twin_inverter")
    assert rotary == span("rotary_single_fixed")
    assert f"``rotary_twin_inverter`` and ``rotary_single_fixed``: {rotary[0]}, {rotary[1]}" in row
    scroll = span("scroll")
    assert f"``scroll``: within {scroll[0]} and {scroll[1].replace(' rev/s', '')} rev/s" in row
    assert f"every rotary speed below {rotary[1].split('–')[0]} rev/s" in row


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
    from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION, COMPRESSOR_TYPES, SEMI_EMPIRICAL_TYPES
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
    assert f"``{COEFFICIENT_VERSION}``" in efficiency
    # The row names every type a constructor accepts, the default first, and says which are provisional.
    default_type, *other_types = COMPRESSOR_TYPES
    assert f"``{default_type}`` (the default)" in efficiency
    assert all(f"``{name}``" in efficiency for name in other_types)
    assert a2w.compressor_type.compressor_type == default_type
    provisional = [name for name in COMPRESSOR_TYPES if SEMI_EMPIRICAL_TYPES[name]["provisional"]]
    assert sorted(provisional) == ["rotary_single_fixed", "rotary_twin_inverter"]
    assert "Rotary types, both provisional" in efficiency
    # The size of the data each type rests on is the archive's (``data_sources.csv``).
    sources = _defaults_archive_sources()
    scroll = sources[(sources.family == "scroll") & (sources.role == "shape")]
    rotary = sources[sources.family == "rotary"]
    shape, level = rotary[rotary.role == "shape"], rotary[rotary.role == "level"].set_index("source_id")
    scroll_machines = int(pd.read_csv(DEFAULTS_ARCHIVE / "loco.csv").query("group == 'all'").machines.iloc[0])
    assert f"Scroll: {int(scroll.rows_used_eta_oi.sum()):,} points of {scroll_machines} machines" in efficiency
    assert int(shape.machines_used.sum()) == 5
    assert f"the shape from {int(shape.rows_used_eta_oi.sum())} points of five machines" in efficiency
    twins = int(level.machines_used[["rating_lg_sci_twin", "highly_catalogue_2024"]].sum())
    singles = int(level.machines_used["rating_fixed_single"])
    assert f"({twins} inverter twins, {singles} fixed-speed singles)" in efficiency
    assert f"``validation/coefficients/{COEFFICIENT_VERSION}/``" in efficiency

    guards = re.sub(r"\s+", " ", _list_table_row(table, "Efficiency guards"))
    # The (PR, N) data hull of the default compressor type: what ``efficiency_extrapolated`` reports against.
    hull = SEMI_EMPIRICAL_TYPES[COMPRESSOR_TYPES[0]]["hull_PR_N"]
    PR_FIT_MIN, PR_FIT_MAX = min(pr for pr, _ in hull), max(pr for pr, _ in hull)
    RPS_FIT_MIN, RPS_FIT_MAX = min(n for _, n in hull), max(n for _, n in hull)
    assert f"PR {PR_FIT_MIN:.2f}–{PR_FIT_MAX:.2f}" in guards
    assert f"N {RPS_FIT_MIN:.1f}–{RPS_FIT_MAX:.1f} rev/s" in guards
    # Outside the hull the forms are followed; the flag is the only consequence.
    assert "nothing is held or clamped" in guards and "``efficiency_extrapolated``" in guards

    from tmhp.compressor_envelope import default_rps_max

    speed = re.sub(r"\s+", " ", _list_table_row(table, "Compressor speed range"))
    assert f"``rps_min`` {a2w.rps_min:g} rev/s; ``rps_max = max(120, 1.5 × rps_rated)``" in speed
    assert a2a.rps_min == a2w.rps_min
    assert [default_rps_max(rated) for rated in (40.0, 80.0, 100.0)] == [120.0, 120.0, 150.0]
    assert f"start at {RPS_FIT_MIN:g} rev/s" in speed and a2w.rps_min < RPS_FIT_MIN

    limit = re.sub(r"\s+", " ", _list_table_row(table, "Pressure-ratio limit"))
    assert f"``PR_cycle_max`` {a2a.PR_cycle_max:g} (air-to-air), {a2w.PR_cycle_max:g} (air-to-water)" in limit

    # The page names the shipped version where it says how to regenerate it, and links nothing private.
    page = _flat("docs/source/validation/defaults.rst")
    assert f"The shipped compressor models are version ``{COEFFICIENT_VERSION}``" in page
    assert "notion" not in page.lower()
    for removed in ("compressor_efficiency_legacy", "default_displacement", "ETA_ISEN_MIN", "FittedEfficiency"):
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

    A gate point the harness does not score (``capacity_short``) is stated as not evaluated, with the
    share of the duty it delivers at the speed ceiling and the speed it needs; the two are re-solved here.
    """
    from validation.gate.evaluate import catalogue_point, load_gate
    from validation.parity.run import _build_model, _run_point

    from tmhp.compressor_efficiency import (
        COMPRESSOR_TYPES,
        ROTARY_SPEED_VARIANTS,
        SEMI_EMPIRICAL_TYPES,
        CompressorSpec,
    )

    text = _read("docs/source/concepts/scope-and-limits.rst")
    start = text.index("High lift: hot water in cold weather")
    assert text[:start].rstrip().endswith(".. _high-lift-deficit:")  # other pages link to the label
    section = text[start : text.index("A short checklist for any result", start)]
    flat = re.sub(r"\s+", " ", section)
    gate = load_gate()

    def stored(entry):
        catalog, point = catalogue_point(entry)
        row = pd.read_csv(RESULTS / f"{catalog.slug}.csv").set_index("point_id").loc[point.id]
        return catalog, point, row

    hull = SEMI_EMPIRICAL_TYPES[COMPRESSOR_TYPES[0]]["hull_PR_N"]
    pr_top = max(pr for pr, _ in hull)
    within, short = 0, 0
    # The evaluated gate points under the other machines the page compares them with (re-solved here).
    other_shape = CompressorSpec(COMPRESSOR_TYPES[0], rotary_speed_variant="shao_informed")
    assert ROTARY_SPEED_VARIANTS[0] == "ossorio_anchored"
    compared: dict[str, list[tuple[float, float]]] = {"before": [], "no_ctrl": [], "other_shape": [], "now": []}
    for entry in gate["gate"]["catalogue_points"]:
        catalog, point, row = stored(entry)
        assert catalog.model_compressor_type == COMPRESSOR_TYPES[0] and not catalog.injected
        assert row.pr_cmp > pr_top, (catalog.name, row.pr_cmp)  # solved past the rotary data
        label = f"{catalog.name} ({catalog.refrigerant}, {catalog.nominal_capacity_kW:g} kW)"
        cells = [c.strip() for c in re.sub(r"\s+", " ", _list_table_row(section, label)).split(" - ")[1:]]
        if row.usable:
            within += abs(row.cop_pred / row.cop_target - 1) <= gate["gate"]["tolerance_rel"]
            assert cells == [
                f"{row.cop_target:.2f}",
                f"{row.cop_pred:.3f}",
                f"{100 * (row.cop_pred / row.cop_target - 1):+.1f} %".replace("-", "−"),
                f"pressure ratio {row.pr_cmp:.1f}, {row.rps:.0f} rev/s, isentropic efficiency {row.eta_cmp_isen:.3f}",
            ], (catalog.name, cells)
            compared["now"].append((100 * (row.cop_pred / row.cop_target - 1), row.eta_cmp_isen))
            for name, overrides in (
                ("before", {"E_ctrl": 0.0, "compressor_type": other_shape}),
                ("no_ctrl", {"E_ctrl": 0.0}),
                ("other_shape", {"compressor_type": other_shape}),
            ):
                solved = _run_point(_build_model(catalog, overrides), catalog, point)
                assert solved["capacity_clamped"] is None and solved["failure_reason"] == "none"
                compared[name].append((100 * (solved["cop_sys [-]"] / row.cop_target - 1), solved["eta_cmp_isen [-]"]))
            continue
        short += 1
        assert row.failure_reason == "capacity_short"
        model = _build_model(catalog)
        ceiling = _run_point(model, catalog, point)
        share = ceiling["Q_ref_tank [W]"] / (point.q_kW * 1000.0)
        assert ceiling["capacity_binding"] == "compressor" and ceiling["cmp_rpm [rpm]"] / 60.0 == pytest.approx(
            model.rps_max
        )
        assert cells == [
            f"{row.cop_target:.2f}",
            "not evaluated",
            "capacity short",
            f"pressure ratio {row.pr_cmp:.1f}, at the {model.rps_max:.0f} rev/s ceiling: {100 * share:.0f} % of the duty",
        ], (catalog.name, cells)
        raised = _run_point(_build_model(catalog, {"rps_max": 1.5 * model.rps_max}), catalog, point)
        assert raised["capacity_clamped"] is None
        assert f"the model meets the duty at {raised['cmp_rpm [rpm]'] / 60.0:.0f} rev/s" in flat
        assert f"the default ceiling is {model.rps_max:.0f} rev/s" in flat
    assert (within, short) == (2, 1)
    assert "two of the three points are within it and the third is not evaluated" in flat
    assert f"data up to a pressure ratio of {pr_top:.2f}" in flat

    # What moved the two evaluated points: the fixed control power and the default rotary speed shape.
    def pct(values: list[tuple[float, float]]) -> str:
        return " and ".join(f"{v:+.1f} %".replace("-", "−") for v, _ in values)

    def eta(values: list[tuple[float, float]]) -> str:
        return " and ".join(f"{e:.3f}" for _, e in values)

    assert f"read lower than with the defaults of before ({pct(compared['before'])}," in flat
    assert all(now < before for (now, _), (before, _) in zip(compared["now"], compared["before"], strict=True))
    by_power = [no_ctrl - now for (now, _), (no_ctrl, _) in zip(compared["now"], compared["no_ctrl"], strict=True)]
    assert f"The control power takes {by_power[0]:.1f} and {by_power[1]:.1f} points of that" in flat
    assert f"is lower ({eta(compared['now'])} against {eta(compared['before'])})" in flat
    assert f"with the other shape and the control power the two points read {pct(compared['other_shape'])}" in flat
    tolerance = 100 * gate["gate"]["tolerance_rel"]
    assert all(abs(v) <= tolerance for name in ("now", "other_shape") for v, _ in compared[name])
    assert f"the gate points are inside the {tolerance:g} % under either shape" in flat
    assert "(15 W; :ref:`fixed-control-power`)" in flat
    assert _build_model(catalog).E_ctrl == 15.0
    assert "not independent validation of the rotary types" in flat

    injection = gate["reported"]["vapour_injection"]["catalogue_points"]
    by_unit: dict[str, list[str]] = {}
    for entry in injection:
        catalog, _, row = stored(entry)
        by_unit.setdefault(f"{catalog.nominal_capacity_kW:g} kW", []).append(
            f"{100 * (row.cop_pred / row.cop_target - 1):+.1f}".replace("-", "−")
        )
    for size, (w55, w65) in by_unit.items():
        assert f"{w55} and {w65} % at {size}" in flat, (size, w55, w65)

    ashpb = points[_ashpb(points)]
    single = ashpb[~_injected(ashpb)]
    cold = single[single.t_source_C == -7.0]
    mape, bias, evaluated, _ = _stats(cold)
    assert f"the {evaluated} points evaluated at −7 °C run {_signed(bias).replace('-', '−')} % on average" in flat
    assert f"run {_signed(_stats(ashpb[_injected(ashpb)])[1]).replace('-', '−')} % on average and the six" in flat
    assert f"without injection {_signed(_stats(single)[1]).replace('-', '−')} %" in flat

    archive = pd.read_csv(REPO_ROOT / "validation/coefficients/semi-empirical-2026-11-15/eta_v_high_pr.csv")
    assert f"{archive.eta_v_PR8.min():.3f} to {archive.eta_v_PR8.max():.3f} for R32 at 60 rev/s" in flat


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
    """The five-description comparison quotes ``coefficient_ablation_parity.csv``, cell for cell."""
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION

    ablation = pd.read_csv(ABLATION).set_index("variant")
    # The previous release is one of the descriptions: the "before" on the same tree.
    assert list(ablation.index) == ["ideal", "constant", "legacy-v1", "quadratic-2026-10-06", "defaults"]
    text = _read("docs/source/validation/defaults.rst")
    section = text[text.index("Does the coefficient set survive both tests?\n---") :]
    section = section[: section.index("\nProvenance\n=====")]
    flat = re.sub(r"\s+", " ", section)
    assert "Five compressor descriptions were run through both" in flat
    # The comparison swaps the efficiencies only: every description carries the default fixed control power.
    from validation.analysis.en14825_trend import variants

    from tmhp import AirSourceHeatPumpBoiler
    from tmhp.control_power import CONTROL_POWER_SINK_WATER_W

    described = {v.key: v for v in variants()}
    assert list(described) == list(ablation.index)
    for key, description in described.items():
        assert "E_ctrl" not in description.kwargs, key
        assert set(description.kwargs) <= {"eta_cmp_isen", "eta_cmp_vol", "eta_cmp"}, key
        built = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32", **description.kwargs)
        assert built.E_ctrl == CONTROL_POWER_SINK_WATER_W, key
    assert (
        "every description runs with the fixed control power of the default machine, "
        f"{CONTROL_POWER_SINK_WATER_W:g} W, in its electrical input"
    ) in flat
    # The figure does not draw the previous release, and the page says so.
    assert "The figure draws four of the five descriptions" in flat
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
    before = ablation.loc["quadratic-2026-10-06"]
    ascii_flat = flat.replace("−", "-")
    assert f"The shipped models come to {mape:.2f} % with a {bias:+.2f} % bias" in ascii_flat
    assert (
        f"the previous release, on the same tree, to {before.MAPE_air_to_water_pct:.2f} % "
        f"with {before.bias_air_to_water_pct:+.2f} %"
    ) in ascii_flat
    assert f"the bias is {abs(bias - before.bias_air_to_water_pct):.1f} points more negative" in flat
    assert bias < before.bias_air_to_water_pct
    # On the catalogues the default is not better than the map it replaced: the error is larger too.
    assert mape > before.MAPE_air_to_water_pct
    assert f"The error is {mape - before.MAPE_air_to_water_pct:.1f} points larger" in flat
    assert "on the catalogues the default is not better than the map it replaced" in flat
    assert f"of the {attempted} points of the ten units, {evaluated} are evaluated with the defaults" in flat
    kinds = points[_ashpb(points)].drop_duplicates("slug").compressor_type.value_counts()
    assert (int(kinds["rotary"]), int(kinds["scroll"])) == (6, 4)
    assert "six of the ten units are run as twin rotaries and four as scrolls" in flat
    for variant in ("ideal", "constant", "legacy-v1"):
        assert f"{ablation.at[variant, 'MAPE_air_to_water_pct']:.1f} %" in flat, variant


DEFAULTS_PAGE = "docs/source/validation/defaults.rst"
DEFAULTS_ARCHIVE = REPO_ROOT / "validation" / "coefficients" / "semi-empirical-2026-11-15"
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


def _defaults_archive_sources() -> pd.DataFrame:
    return pd.read_csv(DEFAULTS_ARCHIVE / "data_sources.csv")


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

    from tmhp.compressor_efficiency import (
        COEFFICIENT_VERSION,
        ROTARY_SPEED_VARIANTS,
        SEMI_EMPIRICAL_DRIVE,
        SEMI_EMPIRICAL_TYPES,
    )

    assert DEFAULTS_ARCHIVE.name == COEFFICIENT_VERSION
    archive = json.loads((DEFAULTS_ARCHIVE / "coefficients.json").read_text())
    assert archive["version"] == COEFFICIENT_VERSION
    for name, entry in SEMI_EMPIRICAL_TYPES.items():
        recorded = archive["types"][name]
        assert entry["parameters"] == recorded["eta_oi"]["parameters"], name
        assert entry["level"] == recorded["eta_oi"]["level_a"], name
        assert entry["eta_v"]["c0"] == recorded["eta_v"]["c0"], name
        assert entry["loss_share"] == pytest.approx(recorded["split"]["F"]["coefficients"][0], abs=5e-7), name
        assert entry["provisional"] == recorded["provisional"], name
    assert SEMI_EMPIRICAL_DRIVE["lam"] == archive["drive"]["lam_median"]
    assert SEMI_EMPIRICAL_DRIVE["beta"] == archive["drive"]["beta"]
    variants = json.loads((DEFAULTS_ARCHIVE / "rotary_speed_variants.json").read_text())
    assert {"a": "shao_informed", "b": "ossorio_anchored"}[variants["default"]] == ROTARY_SPEED_VARIANTS[0]


def test_defaults_compressor_forms_quote_the_library() -> None:
    """Parameters, levels, loss shares and the drive, as the page writes them and the library holds them."""
    import json

    from tmhp.compressor_efficiency import (
        COMPRESSOR_TYPES,
        ROTARY_SPEED_VARIANTS,
        SEMI_EMPIRICAL_DRIVE,
        SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS,
        SEMI_EMPIRICAL_TYPES,
        CompressorSpec,
        SemiEmpiricalCompressor,
    )

    archive = json.loads((DEFAULTS_ARCHIVE / "coefficients.json").read_text())["types"]
    forms = _defaults_section("The forms")
    flat = re.sub(r"\s+", " ", forms)

    scroll = SEMI_EMPIRICAL_TYPES["scroll"]
    p = scroll["parameters"]
    level = archive["scroll"]["eta_oi"]["level"]
    assert _defaults_cells(forms, "``scroll``") == [
        f":math:`r_v` = {p['rv']:.3f}, :math:`q_2` = {p['q2']:.3f} bar, :math:`c_L` = {p['cL']:.4f}, "
        f":math:`z_v` = {p['zv']:.3f}",
        "``n = N / 50 rev/s`` (absolute)",
        f"{scroll['level']:.3f} (quartiles {level['a_iqr'][0]:.3f}–{level['a_iqr'][1]:.3f}): median of "
        f"{level['machines']} machines of residential size",
    ]
    assert scroll["speed"] == "abs"
    twin = SEMI_EMPIRICAL_TYPES[COMPRESSOR_TYPES[0]]
    p = twin["parameters"]
    cells = _defaults_cells(forms, f"``{COMPRESSOR_TYPES[0]}``")
    # The default rotary shape: flow loss, copper loss and suction heating (no leakage term).
    assert sorted(p) == ["cH", "cu", "qf"]
    assert cells[0] == f":math:`q_f` = {p['qf']:.4f}, :math:`c_u` = {p['cu']:.4f}, :math:`c_H` = {p['cH']:.3f}"
    assert twin["speed"] == "rel" and cells[1] == "``n = N / N_rated`` (relative to the machine's rated speed)"
    assert SEMI_EMPIRICAL_TYPES["rotary_single_fixed"]["parameters"] == p
    # The table is the default speed shape; the other one is named with its own two coefficients.
    default_shape, other_shape = ROTARY_SPEED_VARIANTS
    assert SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS[default_shape]["parameters"] == p
    other = SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS[other_shape]["parameters"]
    assert sorted(other) == ["cL", "cu"]
    assert (
        f"This rotary form is the default of the two speed shapes the library carries, ``{default_shape}``; the "
        f"other, ``{other_shape}``, has the leakage term :math:`c_L (PR - 1)/n` in place of the flow-loss and "
        f"suction-heating terms (:math:`c_L` = {other['cL']:.4f}, :math:`c_u` = {other['cu']:.4f})"
    ) in flat
    # Bounded by construction or not: the default rotary form is not, and the page says so where it gives the form.
    assert "It is not a property of the default rotary form" in flat

    start = forms.index(".. list-table:: Level of the rotary types by refrigerant")
    table = forms[start : forms.index("A refrigerant has its own level", start)]
    for name in ("rotary_twin_inverter", "rotary_single_fixed"):
        recorded = archive[name]["eta_oi"]["level"]
        by_ref = recorded["by_refrigerant"]
        assert {r: round(e["a_median"], 6) for r, e in by_ref.items()} == SEMI_EMPIRICAL_TYPES[name][
            "level_by_refrigerant"
        ]
        for ref, entry in by_ref.items():
            cells = _defaults_cells(table, f"``{name}``, {ref}")
            assert cells[0].startswith(f"{entry['machines']} ("), (name, ref, cells)
            lo, hi = entry["a_iqr"]
            assert cells[1] == f"{entry['a_median']:.3f} ({lo:.3f}–{hi:.3f})", (name, ref, cells)
        lo, hi = recorded["a_iqr"]
        pooled = _defaults_cells(table, f"``{name}``, any other refrigerant")
        assert pooled[1] == f"{recorded['a_median']:.3f} ({lo:.3f}–{hi:.3f})", (name, pooled)
        assert f"{sum(e['machines'] for e in by_ref.values())}" in pooled[0], (name, pooled)

    # Volumetric form: parameters and levels, and the agreement with the displacement rule at its reference.
    for name, words in (("scroll", "The scroll has"), ("rotary_twin_inverter", "the rotary types")):
        v = SEMI_EMPIRICAL_TYPES[name]["eta_v"]
        second = "c_L" if name == "scroll" else "c_s"
        digits = 5 if name == "scroll" else 4
        expected = f"{words} :math:`c_{{re}}` = {v['parameters']['c_re']:.{digits}f}"
        assert expected in flat, expected
        assert f":math:`{second}` = {v['parameters'][second]:.4f}" in flat, (name, second)
    for name, words in (
        ("scroll", "and :math:`c_0` ="),
        ("rotary_twin_inverter", "for the twin"),
        ("rotary_single_fixed", "for the single"),
    ):
        v = archive[name]["eta_v"]
        quartiles = f"{v['c0_iqr'][0]:.3f}–{v['c0_iqr'][1]:.3f}"
        assert f"{v['c0']:.3f}" in flat and quartiles in flat, (name, words)
    consistency = archive["rotary_twin_inverter"]["eta_v"]["regression_consistency"]
    assert (
        f"the twin model gives {consistency['model_eta_v_at_reference']['R32']:.3f} for R32 and "
        f"{consistency['model_eta_v_at_reference']['R410A']:.3f} for R410A against the "
        f"{consistency['regression_eta_vol_rps_per_f']:.3f} the rule uses"
    ) in flat

    modelled = re.sub(r"\s+", " ", _defaults_section("What is modelled, and where each loss sits"))
    assert (
        f"{scroll['loss_share']:.3f} for the scroll ({archive['scroll']['split']['rows']} rows of four machines)"
        in modelled
    )
    assert archive["scroll"]["split"]["machines"] == 4 and archive["rotary_twin_inverter"]["split"]["rows"] == 4
    assert f"{twin['loss_share']:.3f} for the rotary types (four rows of one machine)" in modelled
    assert f":math:`\\beta` = {SEMI_EMPIRICAL_DRIVE['beta']:.3f}" in modelled
    assert f":math:`\\lambda` = {SEMI_EMPIRICAL_DRIVE['lam']:.4f}" in modelled
    drive = SemiEmpiricalCompressor(CompressorSpec(), "R32", 60.0).eta_inverter
    assert (
        f"It is {drive(60.0):.3f} at 60 rev/s, {drive(20.0):.3f} at 20 rev/s and {drive(15.0):.3f} at the default "
        "speed floor of 15 rev/s"
    ) in modelled
    assert "the power at the **inverter input**" in modelled


def test_defaults_data_sources_table_matches_the_archive() -> None:
    """Machines, points and power basis of every source row are ``data_sources.csv``'s."""
    sources = _defaults_archive_sources().set_index("source_id")
    section = _defaults_section("The data")
    start = section.index(".. list-table:: Sources of the fit")
    table = section[start : section.index("The drive model rests on", start)]
    rows = {
        "BITZER selection software, scrolls of 114 to 444 cm³/rev": ["bitzer"],
        "Copeland Online Product Information, AHRI 540 coefficient sets of": ["copeland_opi"],
        "AHRI Low-GWP AREP reports #11, #21, #39 and ORNL/TM-2013/414": [
            "arep_rpt11_ornl2013",
            "ornl_tm_2013_414",
            "arep_rpt21_ornl2013",
            "arep_rpt39_emerson2014",
        ],
        "`Cuevas & Lebrun (2009) <https://doi.org/10.1016/j.applthermaleng.2008.03.016>`_": ["cuevas_lebrun_2009"],
        "`Guth & Atakan (2023) <https://doi.org/10.1016/j.ijrefrig.2022.10.024>`_": ["guth_atakan_2023"],
        "`Shao et al. (2004) <https://doi.org/10.1016/j.ijrefrig.2004.02.008>`_": ["shao_2004"],
        "Ossorio-Santiago & Navarro-Peris (2019), congress paper": ["ossorio_2019_cnit"],
        "AHRI Low-GWP AREP report #26": ["arep_rpt26_hitachi2013"],
        "LG and Mitsubishi Electric SCI catalogues, inverter twin rotaries": ["rating_lg_sci_twin"],
        "Highly catalogue 2024, R290 inverter twin rotaries": ["highly_catalogue_2024"],
        "LG and Panasonic catalogues, fixed-speed single rotaries": ["rating_fixed_single"],
    }
    # Every source that enters a fit has a row, and no row names a source that does not.
    used = sources[sources.rows_used_eta_oi > 0]
    assert sorted(used.index) == sorted(s for ids in rows.values() for s in ids)
    # AREP #11 and ORNL/TM-2013/414 tested one machine: the cross-validation counts three AREP machines.
    loco = pd.read_csv(DEFAULTS_ARCHIVE / "loco.csv")
    loco = loco[(loco.family == "scroll") & loco.candidate.str.startswith("selected-abs ")]
    arep_machines = int(loco.set_index("group").machines["AREP"])
    for label, ids in rows.items():
        cells = _defaults_cells(table, label)[-4:]
        entry = used.loc[ids]
        machines = arep_machines if len(ids) > 1 else int(entry.machines_used.iloc[0])
        assert cells[1] == str(machines), (label, cells)
        rating = set(entry.role) == {"level"}
        if rating:
            assert cells[0] == "rating point" and cells[2].startswith("one point each"), (label, cells)
            assert int(entry.rows_used_eta_oi.sum()) == machines
        else:
            assert cells[2].startswith(f"{int(entry.rows_used_eta_oi.sum()):,} points,"), (label, cells)
            low, high = float(entry.PR_min.min()), float(entry.PR_max.max())
            span = f"PR {low:.1f}" if round(low, 1) == round(high, 1) else f"PR {low:.1f}–{high:.1f}"
            assert span in cells[2], (label, span, cells)
        for status in set(entry.basis_status):
            if not (status == "stated" and "both" in set(entry.power_basis)):
                assert status in cells[3], (label, status, cells)
        for refrigerant in ",".join(entry.refrigerants).split(","):
            assert refrigerant in cells[2], (label, refrigerant)

    flat = re.sub(r"\s+", " ", section)
    scroll = used[(used.family == "scroll")]
    scroll_machines = int(loco.set_index("group").machines["all"])
    assert (
        f"The scroll shape therefore rests on {int(scroll.rows_used_eta_oi.sum()):,} points of {scroll_machines} "
        "machines"
    ) in flat
    shape = used[(used.family == "rotary") & (used.role == "shape")]
    assert int(shape.machines_used.sum()) == 5
    assert f"The rotary shape rests on {int(shape.rows_used_eta_oi.sum())} points of five machines" in flat
    # The one input that is not redistributable, and the excluded sources.
    assert "``points_copeland_opi.csv``" in flat and "not redistributable" in flat
    excluded = sources[sources.role == "excluded"]
    assert len(excluded) == 3 and "(#58)" in flat and "two vapour-injection scrolls" in flat


def test_defaults_selection_table_matches_the_archive() -> None:
    """Every cell of the cross-validation table is ``model_selection.csv``'s, the unfavourable ones included."""
    from tmhp.compressor_efficiency import ROTARY_SPEED_VARIANTS, SEMI_EMPIRICAL_TYPES

    selection = pd.read_csv(DEFAULTS_ARCHIVE / "model_selection.csv")
    scroll_speed = SEMI_EMPIRICAL_TYPES["scroll"]["speed"]
    # The archive's letter of the library's default speed shape: the "Rotary, default" column is that row.
    letter = {"shao_informed": "a", "ossorio_anchored": "b"}[ROTARY_SPEED_VARIANTS[0]]
    assert letter == "b"
    rotary_speed = SEMI_EMPIRICAL_TYPES["rotary_twin_inverter"]["speed"]
    columns = [
        selection[(selection.family == "scroll") & selection.candidate.str.startswith(f"selected-{scroll_speed} ")],
        selection[(selection.family == "scroll") & (selection.role == "baseline")],
        selection[(selection.family == "rotary") & (selection.role == f"default (variant {letter})")],
        selection[(selection.family == "rotary") & (selection.role == "baseline")],
        # The other arrangement (variant a), the one the quadratic baseline was scored on.
        selection[(selection.family == "rotary") & selection.candidate.str.startswith(f"selected-{rotary_speed} ")],
    ]
    assert all(len(frame) == 1 for frame in columns)
    *columns, shared_shape = (frame.iloc[0] for frame in columns)

    def signed(value: float) -> str:
        return f"{value:+.3f}".replace("-", "−")

    rows = {
        "Leave one machine out, shape RMSE": ("loco_shape_all", lambda v: f"{v:.4f}"),
        "Leave one machine out, total RMSE": ("loco_total_all", lambda v: f"{v:.4f}"),
        "Leave one source out, mean shape RMSE": ("loso_shape_mean", lambda v: f"{v:.4f}"),
        "Leave one source out, largest level bias": ("loso_level_bias_max_abs", lambda v: f"{v:.3f}"),
        "Top 20 % of pressure ratio held out, bias": ("edge_bias_PR_top", signed),
        "Bottom 20 % of pressure ratio held out, bias": ("edge_bias_PR_bottom", signed),
        "Lowest speed record held out, bias": ("edge_bias_low_speed", signed),
        "Slope against pressure ratio above PR 5, model / data": ("step_ratio_model_data", lambda v: f"{v:.2f}"),
    }
    section = _defaults_section("Selection and cross-validation")
    start = section.index(".. list-table:: Cross-validation of the selected forms")
    table = section[start : section.index("Read by column pairs.", start)]
    for label, (column, fmt) in rows.items():
        assert _defaults_cells(table, label) == [fmt(float(c[column])) for c in columns], label

    # The sentences that read the table carry the same numbers.
    flat = re.sub(r"\s+", " ", section)
    scroll, scroll_q, rotary, rotary_q = columns
    assert (
        f"the shape error falls from {scroll_q.loso_shape_mean:.4f} to {scroll.loso_shape_mean:.4f} for the scroll "
        f"and from {rotary_q.loso_shape_mean:.4f} to {rotary.loso_shape_mean:.4f} for the rotary"
    ) in flat
    assert (
        f"the quadratic under-predicts by {-scroll_q.edge_bias_PR_top:.3f} and {-rotary_q.edge_bias_PR_top:.3f} "
        f"where the selected forms over-predict by {scroll.edge_bias_PR_top:.3f} and {rotary.edge_bias_PR_top:.3f}"
    ) in flat
    # The default arrangement and the quadratic were not scored on equal terms, and the page says so: on the
    # arrangement the quadratic was scored on, the quadratic fits the rotary shape slightly more closely.
    cv = pd.read_csv(DEFAULTS_ARCHIVE / "rotary_speed_variants_cv.csv")
    shared = cv[(cv.variant == "a") & (cv.candidate == "selected terms without qf")].iloc[0]
    assert shared.loco_shape_all == pytest.approx(shared_shape.loco_shape_all, abs=5e-7)
    assert rotary.loco_shape_all < rotary_q.loco_shape_all < shared_shape.loco_shape_all
    assert "The two rotary columns are not on equal terms" in flat
    assert (
        f"the semi-empirical rotary form has a shape error of {shared_shape.loco_shape_all:.4f} with a machine left "
        f"out against the quadratic's {rotary_q.loco_shape_all:.4f}"
    ) in flat
    edge = pd.read_csv(DEFAULTS_ARCHIVE / "edge_holdout.csv")
    low_speed = edge[
        edge.candidate.str.startswith(f"default: variant {letter}")
        & edge.holdout.str.startswith("lowest speed")
        & (edge.group == "all")
    ]
    assert int(low_speed.rows.iloc[0]) == 2 and "lowest-speed hold-out (two points" in flat
    # ... and so do the sentences that say where the quadratic form scores better.
    assert rotary_q.loso_level_bias_max_abs < rotary.loso_level_bias_max_abs
    assert (
        f"is larger than the quadratic's ({rotary.loso_level_bias_max_abs:.3f} against "
        f"{rotary_q.loso_level_bias_max_abs:.3f})"
    ) in flat
    assert rotary_q.edge_bias_PR_bottom < rotary.edge_bias_PR_bottom
    assert "and so is its bias at the bottom fifth of the pressure ratios" in flat
    assert f"{scroll.loco_total_all:.2f} for the scroll and {rotary.loco_total_all:.2f} for the rotary" in flat
    assert 0.3 < rotary.step_ratio_model_data < 0.4
    assert (
        "falls only a third as steeply with the pressure ratio as the rotary data do "
        f"({rotary.step_ratio_model_data:.2f})"
    ) in flat
    # The terms of the default arrangement: the high-speed (flow-loss) term stays by its own rule.
    with_qf = cv[(cv.variant == letter) & (cv.candidate == "selected terms with qf")].iloc[0]
    without_qf = cv[(cv.variant == letter) & (cv.candidate == "selected terms without qf")].iloc[0]
    assert with_qf.loco_shape_all == pytest.approx(rotary.loco_shape_all, abs=5e-7)
    assert with_qf.loco_shape_all < without_qf.loco_shape_all
    assert f"({with_qf.loco_shape_all:.4f} with it, {without_qf.loco_shape_all:.4f} without)" in flat
    readme = re.sub(r"\s+", " ", (DEFAULTS_ARCHIVE / "README.md").read_text())
    assert "(LOCO shape 0.0200 against 0.0209 for `b`; a tie within the archive's 0.001 tolerance)" in readme
    assert f"at a shape error of 0.0200 against {rotary.loco_shape_all:.4f}" in flat


def test_defaults_rotary_speed_variant_account_matches_the_archive_and_the_study() -> None:
    """The one place heat-pump data entered: what was chosen, on what, and that the comparison is not independent.

    The default is the Ossorio-anchored shape (variant ``b`` of the archive). The page gives the two steps of the
    choice -- the trajectory-shape rule on a model without the fixed control power, then the level with that power
    in the model -- with the archive README's numbers and the tables of the study the decision was taken on.
    """
    import json

    from tmhp.compressor_efficiency import ROTARY_SPEED_VARIANTS

    variants = json.loads((DEFAULTS_ARCHIVE / "rotary_speed_variants.json").read_text())
    cv = pd.read_csv(DEFAULTS_ARCHIVE / "rotary_speed_variants_cv.csv")
    readme = re.sub(r"\s+", " ", (DEFAULTS_ARCHIVE / "README.md").read_text())
    section = _defaults_section("Compressor data, not heat-pump data")
    flat = re.sub(r"\s+", " ", section)

    assert ROTARY_SPEED_VARIANTS == ("ossorio_anchored", "shao_informed") and variants["default"] == "b"
    assert "* ``ossorio_anchored``, the default, gives every speed record of the 2004 machines its own level" in flat
    assert '``CompressorSpec("rotary_twin_inverter", rotary_speed_variant="shao_informed")``' in flat
    # Speed ratios of the data and of the two variants at 30 rev/s.
    shao = sorted(unit["30"] for unit in variants["data_speed_ratios"]["shao_2004"].values())
    (ossorio,) = (unit["30"] for unit in variants["data_speed_ratios"]["ossorio_2019_cnit"].values())
    assert f"have {shao[0]:.2f}, {shao[1]:.2f} and {shao[2]:.2f} of their efficiency at 60 rev/s" in flat
    assert f"measured in 2019 has {ossorio:.2f}" in flat
    default = cv[(cv.variant == "b") & (cv.candidate == "selected terms with qf")].iloc[0]
    other = cv[(cv.variant == "a") & (cv.candidate == "selected terms without qf")].iloc[0]
    assert f"At the 2019 test condition it gives {default.ratio_ossorio_30:.3f} at 30 rev/s" in flat
    assert f"It gives {other.ratio_ossorio_30:.3f}." in flat

    # First step: the rule's result on the model without the control power is the README's.
    assert "variant `a` 30 / 30, variant `b` 24 / 30; secondary 0.041 against 0.115" in readme
    assert "30 of 30 cases for ``shao_informed`` against 24 of 30 for ``ossorio_anchored``" in flat
    assert "0.041 against 0.115 in logarithm" in flat
    assert "``shao_informed`` became the default" in flat
    # Second step: with the fixed control power, as the README and the study's table have it.
    assert "D/A inside the certified p10–p90 in **30 / 30 cases under both**" in readme
    assert "27 / 30 cases under `b` against 6 / 30 under `a`**" in readme
    assert "the ratio lies inside the band in 30 of 30 cases under both" in flat
    assert "in 27 of the 30 cases under ``ossorio_anchored`` against 6 under ``shao_informed``" in flat
    study = pd.read_csv(REPO_ROOT / "validation" / "data" / "rotary_speed_evidence" / "en14825_summary.csv")
    study = study[study.application == "both"].set_index(["variant", "P_fixed_W", "quantity"]).cases_inside
    levels = pd.read_csv(REPO_ROOT / "validation" / "data" / "rotary_speed_evidence" / "fixed_power_levels.csv")
    water = levels[levels.model_class == "ASHPB"].set_index("level").P_fixed_W
    assert [water[level] for level in ("none", "certified_median", "certified_upper")] == [0.0, 15.0, 25.0]
    start = section.index(".. list-table:: Cases of thirty inside the certified p10–p90")
    table = section[start : section.index("The table is the study the decision was taken on", start)]
    for variant in ("shao_informed", "ossorio_anchored"):
        for power in water:
            expected = [
                str(int(study[(variant, power, quantity)])) for quantity in ("all four points", "D/A", "B/A", "C/A")
            ]
            assert _defaults_cells(table, f"``{variant}``, {power:g} W") == expected, (variant, power)
    assert int(study[("ossorio_anchored", 15.0, "all four points")]) == 27
    assert int(study[("shao_informed", 15.0, "all four points")]) == 6
    scores = pd.read_csv(REPO_ROOT / "validation" / "data" / "rotary_speed_evidence" / "en14825_scores.csv")
    without = scores[(scores.variant == "ossorio_anchored") & (scores.level == "none")]
    assert int((~without.in_D).sum()) == 12 and (without[~without.in_D].cop_D > 8.9).all()
    assert "point D above the 90th percentile in 12 cases" in flat
    # The same counts with the power inside the library's own search: the committed verdict of the default.
    verdict = pd.read_csv(TREND_VERDICT)
    shipped = verdict[verdict.variant == "defaults"]
    assert (int(shipped.C2_level.sum()), int(shipped.C3_gradient.sum())) == (27, 30)
    assert "27 and 30 for the default (the committed ``en14825_trend_verdict.csv``), 6 and 30 for" in flat

    # The consequence is stated, in both places the default is judged on the trajectory.
    assert "**Consequence: the EN 14825 part-load comparison (trajectory shape and level) is not independent" in readme
    assert "**the EN 14825 comparison is not independent validation**" in flat
    assert "were not used and remain independent" in flat
    survive = re.sub(r"\s+", " ", _defaults_section("Does the coefficient set survive both tests?"))
    assert "The population half is not an independent test of the default" in survive

    # What does not support the choice: the independent comparisons, as the study's parity tables have them.
    parity = pd.read_csv(REPO_ROOT / "validation" / "data" / "rotary_speed_evidence" / "parity_summary.csv")
    parity = parity[parity.level == "certified_median"].set_index(["variant", "model_class", "mode"])

    def figures(variant: str, model_class: str, mode: str) -> str:
        row = parity.loc[(variant, model_class, mode)]
        return f"{row.MAPE_pct:.2f} % and {row.bias_pct:+.2f} %".replace("-", "−")

    b, a = "ossorio_anchored", "shao_informed"
    assert (
        f"are {figures(b, 'ASHPB', 'heating')} for the air-to-water units under the default against "
        f"{figures(a, 'ASHPB', 'heating')} under ``shao_informed``; for the air-to-air units "
        f"{figures(b, 'ASHP', 'heating')} against {figures(a, 'ASHP', 'heating')} in heating and "
        f"{figures(b, 'ASHP', 'cooling')} against {figures(a, 'ASHP', 'cooling')} in cooling"
    ) in flat
    assert "air-to-water 8.49 / −6.30 % under `b` against 8.51 / −5.36 % under `a`" in readme
    gate = pd.read_csv(REPO_ROOT / "validation" / "data" / "rotary_speed_evidence" / "parity_gate.csv")
    gate = gate[gate.level == "certified_median"].set_index(["variant", "slug"])
    running = [slug for slug in gate.loc[b].index if gate.loc[(b, slug), "failure_reason"] == "none"]
    assert running == ["panasonic_wh_uq09he8", "panasonic_wh_uq16he8"]
    errors = [f"{gate.loc[(v, slug), 'err_pct']:+.1f} %".replace("-", "−") for v in (b, a) for slug in running]
    assert all(abs(gate.loc[(v, slug), "err_pct"]) < 10.0 for v in (b, a) for slug in running)
    assert f"read {errors[0]} and {errors[1]} against {errors[2]} and {errors[3]}, all inside the ±10 % gate" in flat
    # ... and the dependence of the count on the power that is put in.
    upper = scores[(scores.variant == b) & (scores.level == "certified_upper")]
    assert int(study[(b, 25.0, "all four points")]) == int(upper.level_all_inside.sum())
    assert (
        f"With 25 W in place of 15 W the default passes in {int(upper.level_all_inside.sum())} cases, "
        f"{int((~upper.in_B).sum())} B points and {int((~upper.in_C).sum())} C points falling below the 10th "
        "percentile"
    ) in flat
    assert (upper[~upper.in_B].cop_B < 4.2).all() and upper.in_A.all() and upper.in_D.all()
    # The compressor-level check that does not separate the variants.
    assert "implied inverter efficiency of 0.90–0.91 under variant `a` and 0.89–0.90 under `b`" in readme.replace(
        "implies an inverter efficiency", "implied inverter efficiency"
    )
    assert "0.89–0.90 under ``ossorio_anchored`` and 0.90–0.91 under ``shao_informed``" in flat

    # The part-load curve at held temperatures, from the study's curve table (15 W, air-to-water 9 kW R32).
    curves = pd.read_csv(REPO_ROOT / "validation" / "data" / "rotary_speed_evidence" / "curve_summary.csv")
    curves = curves[(curves.model_class == "ASHPB") & (curves.level == "certified_median")]
    assert set(curves.capacity_W) == {9000.0} and set(curves.refrigerant) == {"R32"}
    conditions = [(7.0, 35.0), (7.0, 45.0), (-7.0, 45.0)]
    fall, peak = {}, {}
    for variant in (b, a):
        rows = curves[curves.variant == variant].set_index(["t_outdoor_C", "t_sink_C"])
        fall[variant] = [f"{-rows.floor_vs_nameplate_pct[c]:.1f}" for c in conditions]
        peak[variant] = (100.0 * rows.plr_peak.min(), 100.0 * rows.plr_peak.max())
    assert (
        f"loses {fall[b][0]} % of its nameplate-load COP by the time it reaches its speed floor at +7 °C air and "
        f"35 °C leaving water, {fall[b][1]} % at +7 / 45 °C and {fall[b][2]} % at −7 / 45 °C under the default, and "
        f"its curve peaks at {peak[b][0]:.0f} to {peak[b][1]:.0f} % of the nameplate; under ``shao_informed`` it "
        f"loses {fall[a][0]}, {fall[a][1]} and {fall[a][2]} % and peaks at {peak[a][0]:.0f} to {peak[a][1]:.0f} %"
    ) in flat

    # The owner's decision, as a fact: implemented as a draft, the merge held -- not adopted, not released.
    assert "**Owner decision of 2026-11-16, after the measurements for #183:**" in readme
    assert "regenerated as a draft pull request; the merge is held" in readme
    assert "regenerated as a draft pull request. The merge is held." in flat
    assert "adopted" not in flat and "released" not in flat


def test_defaults_limits_quote_the_archive_and_the_library() -> None:
    """The limits are stated with the archive's numbers, and none of the README's is left out."""
    import json

    import CoolProp.CoolProp as CP

    from tmhp.compressor_efficiency import (
        SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS,
        SEMI_EMPIRICAL_TYPES,
        CompressorSpec,
        SemiEmpiricalCompressor,
        suction_state,
    )

    archive = json.loads((DEFAULTS_ARCHIVE / "coefficients.json").read_text())
    flat = re.sub(r"\s+", " ", _defaults_section("Limits and provisional items"))
    for heading in (
        "**The rotary types are provisional.**",
        "**The level is the manufacturers' level, and independent measurements sit lower.**",
        "**The power basis of the rating points is assumed.**",
        "**Refrigerants without rating points take the pooled level.**",
        "**The split of the loss is weakly identified**",
        "**Below 30 rev/s the default is an extrapolation.**",
        "**The volumetric efficiency of the rotary types above a pressure ratio of 6.8 is an extrapolation.**",
        "**The rotary volumetric efficiency passes one at a low lift and a high speed.**",
        "**The default rotary form can exceed one.**",
        "**The speed shape of the rotary types was chosen on heat-pump data.**",
        "**The scroll type has not been checked on a machine it describes.**",
        "**Sources disagree on the scroll's shape at a high pressure ratio.**",
    ):
        assert heading in flat, heading

    def state(ref: str, t_evap: float, t_cond: float, superheat: float):
        p1 = CP.PropsSI("P", "T", t_evap + 273.15, "Q", 1, ref)
        p2 = CP.PropsSI("P", "T", t_cond + 273.15, "Q", 1, ref)
        h1 = CP.PropsSI("H", "P", p1, "T", t_evap + 273.15 + superheat, ref)
        s1 = CP.PropsSI("S", "P", p1, "H", h1, ref)
        return p2 / p1, suction_state(
            p1, CP.PropsSI("D", "P", p1, "H", h1, ref), h1, CP.PropsSI("H", "P", p2, "S", s1, ref)
        )

    # The level at the catalogues' rating condition: the default, the manufacturers' sources and the measured machines.
    reference = archive["types"]["scroll"]["eta_oi"]["rating_reference"]
    pr, st = state(reference["refrigerant"], reference["T_evap_C"], reference["T_cond_C"], reference["superheat_K"])
    scroll = SemiEmpiricalCompressor(CompressorSpec("scroll"), reference["refrigerant"], reference["N"])

    def scroll_at(level: float) -> float:
        scroll.level = level
        return scroll.eta_oi(pr, reference["N"], st)

    recorded = archive["types"]["scroll"]["eta_oi"]
    default = scroll_at(recorded["level_a"])
    assert default == pytest.approx(recorded["eta_oi_at_rating_reference"], abs=5e-4)
    levels = pd.read_csv(DEFAULTS_ARCHIVE / "levels.csv")
    arep = sorted(scroll_at(a) for a in levels[(levels.family == "scroll") & (levels.group == "AREP")].a)
    literature = levels[(levels.family == "scroll") & (levels.group == "literature")].set_index("machine").a
    (cuevas,) = (scroll_at(a) for m, a in literature.items() if m.startswith("cuevas"))
    (guth,) = (scroll_at(a) for m, a in literature.items() if m.startswith("guth"))
    assert (
        f"the level of the Copeland polynomials is an :math:`\\eta_{{oi}}` of "
        f"{scroll_at(recorded['level']['by_group_median']['Copeland']):.3f} and that of the BITZER calculation data "
        f"{scroll_at(recorded['level']['bitzer_median']):.3f}; the default, the median of the residential-size "
        f"machines, is {default:.3f}"
    ) in flat
    assert (
        f"The measured scrolls are at {arep[0]:.3f}, {arep[1]:.3f} and {arep[2]:.3f} (the three AREP machines) and "
        f"{cuevas:.3f} (Cuevas & Lebrun), the published functions of Guth & Atakan at {guth:.3f}"
    ) in flat
    measured = [*arep, cuevas]
    below = sorted(100.0 * (1.0 - value / default) for value in [*measured, guth])
    assert f"between {below[0]:.0f} % and {below[-1]:.0f} % below the default" in flat
    assert sum(12.5 <= 100.0 * (1.0 - value / default) < 16.5 for value in measured) == 3
    assert "three of the four measured machines by 13 to 16 %" in flat
    twin = archive["types"]["rotary_twin_inverter"]["eta_oi"]["level"]["by_refrigerant"]
    single = archive["types"]["rotary_single_fixed"]["eta_oi"]["level"]
    assert (
        f"medians of {twin['R32']['eta_oi_median']:.3f} (R32), {twin['R410A']['eta_oi_median']:.3f} (R410A) and "
        f"{twin['R290']['eta_oi_median']:.3f} (R290) for inverter twins and "
        f"{single['by_refrigerant']['R410A']['eta_oi_median']:.3f} (R410A) for fixed-speed singles"
    ) in flat
    measured_rotary = {}
    for unit, a in single["arep26_unit_levels"].items():
        ref = "R32" if " R32 [" in unit else "R410A"
        pr_r, st_r = state(ref, reference["T_evap_C"], reference["T_cond_C"], reference["superheat_K"])
        machine = SemiEmpiricalCompressor(CompressorSpec("rotary_single_fixed"), ref, 50.0)
        catalogue = machine.eta_oi(pr_r, 50.0, st_r)
        machine.level = a
        value = machine.eta_oi(pr_r, 50.0, st_r)
        twin_model = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter"), ref, 60.0).eta_oi(pr_r, 60.0, st_r)
        measured_rotary[ref] = (value, 100.0 * (1.0 - value / catalogue), 100.0 * (1.0 - value / twin_model))
    assert f"is at {measured_rotary['R32'][0]:.3f} (R32) and {measured_rotary['R410A'][0]:.3f} (R410A)" in flat
    # Against its own class the measured machine is below the catalogue level with R410A, which the fixed-speed
    # catalogues rate, and above the pooled level with R32, which they do not (the default speed shape; with the
    # other shape both sat about 2 % below).
    assert sorted(single["by_refrigerant"]) == ["R22", "R410A"]
    below_own, above_pooled = measured_rotary["R410A"][1], -measured_rotary["R32"][1]
    assert 2.5 <= below_own < 3.5 and 8.5 <= above_pooled < 9.5
    assert (
        f"With R410A that is {below_own:.0f} % below the catalogue level of its own class; with R32, which the "
        f"fixed-speed catalogues do not rate, it is {above_pooled:.0f} % above what the pooled level gives"
    ) in flat
    assert sorted(round(twins) for _, _, twins in measured_rotary.values()) == [11, 12]
    assert "Both are 11 to 12 % below the inverter twins" in flat
    assert "it holds no independent measurement of the efficiency level of one" in flat

    # The same condition in "The forms": what each type gives at its rated speed, by refrigerant.
    at_rating = {}
    for ref in ("R32", "R410A", "R290"):
        pr_r, st_r = state(ref, reference["T_evap_C"], reference["T_cond_C"], reference["superheat_K"])
        machine = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter"), ref, 60.0)
        at_rating[ref] = (pr_r, machine.eta_oi(pr_r, 60.0, st_r))
    ratios = [pr_r for pr_r, _ in at_rating.values()]
    single_model = SemiEmpiricalCompressor(CompressorSpec("rotary_single_fixed"), "R410A", 50.0).eta_oi(pr, 50.0, st)
    forms = re.sub(r"\s+", " ", _defaults_section("The forms"))
    assert (
        f"a pressure ratio of {min(ratios):.1f} to {max(ratios):.1f} by refrigerant) and the rated speed, the twin "
        f"model gives :math:`\\eta_{{oi}}` = {at_rating['R32'][1]:.3f} for R32, {at_rating['R410A'][1]:.3f} for R410A "
        f"and {at_rating['R290'][1]:.3f} for R290, the single one {single_model:.3f} for R410A, and the scroll "
        f"{default:.3f} for R410A at 60 rev/s"
    ) in forms
    # A refrigerant on the pooled level, at the same condition: above every refrigerant that has rating points.
    pr_p, st_p = state("R134a", reference["T_evap_C"], reference["T_cond_C"], reference["superheat_K"])
    pooled_machine = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter"), "R134a", 60.0)
    assert pooled_machine.level == SEMI_EMPIRICAL_TYPES["rotary_twin_inverter"]["level"]
    pooled_value = pooled_machine.eta_oi(pr_p, 60.0, st_p)
    assert pooled_value > max(value for _, value in at_rating.values())
    assert f"the twin model gives {pooled_value:.3f} for R134a there" in forms
    assert (
        f"For R134a it gives an :math:`\\eta_{{oi}}` of {pooled_value:.3f} at the catalogues' rating condition, above "
        "every refrigerant that has rating points"
    ) in flat

    # The GMCC check, the pooled level and the split.
    implied = sorted(entry["implied_inverter_eff"] for entry in archive["checks"]["gmcc_inverter_input"])
    assert [f"{value:.2f}" for value in implied] == ["0.89", "0.90"]
    assert "an inverter efficiency of 0.89–0.90, where the drive model has 0.94 at that speed" in flat
    drive = archive["drive"]["eta_at_60rps"]
    assert f"{drive:.2f}" == "0.94" and [round(100.0 * (drive / v - 1.0)) for v in implied] == [6, 5]
    assert "the model's electrical input is 5 to 6 % low" in flat
    levels_by_ref = SEMI_EMPIRICAL_TYPES["rotary_twin_inverter"]["level_by_refrigerant"]
    assert sorted(levels_by_ref) == ["R290", "R32", "R410A"]
    assert f"span a factor of {max(levels_by_ref.values()) / min(levels_by_ref.values()):.2f}" in flat
    efficiencies = sorted(entry["eta_oi_median"] for entry in twin.values())
    assert f"for rating-point efficiencies of {efficiencies[0]:.3f} to {efficiencies[-1]:.3f}" in flat
    other_levels = SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS["shao_informed"]["level_by_refrigerant"]["rotary_twin_inverter"]
    assert f"spans {max(other_levels.values()) / min(other_levels.values()):.2f})" in flat
    check = archive["checks"]["single_fixed_r290"]
    assert check["rows"] == 6 and f"{100.0 * (1.0 - check['ratio_data_model_median']):.0f}" == "15"
    assert "six R290 single rotaries" in flat and "sit 15 % below what the pooled level gives" in flat
    readme = re.sub(r"\s+", " ", (DEFAULTS_ARCHIVE / "README.md").read_text())
    assert "the pooled single level ~15 % optimistic for a refrigerant outside its data (`a`: 4 %)" in readme
    assert "(4 % under ``shao_informed``)" in flat
    unit_shares = [unit["f_fit"] for unit in archive["types"]["scroll"]["split"]["unit_means"]]
    assert f"run from {min(unit_shares):.2f} to {max(unit_shares):.2f}" in flat

    # Below the data in speed: the form itself, at the two states the page names.
    hull = SEMI_EMPIRICAL_TYPES["rotary_twin_inverter"]["hull_PR_N"]
    assert (min(n for _, n in hull), max(n for _, n in hull)) == (30.0, 120.0)
    assert "the rotary data cover 30 to 120 rev/s" in flat
    rated = SemiEmpiricalCompressor(CompressorSpec(), "R32", 40.0)
    steeper = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter", "shao_informed"), "R32", 40.0)
    falls, steeper_falls = [], []
    for t_evap, t_cond in ((7.0, 35.0), (-12.0, 47.0)):
        pr_s, st_s = state("R32", t_evap, t_cond, 5.0)
        falls.append((pr_s, 100.0 * (1.0 - rated.eta_oi(pr_s, 15.0, st_s) / rated.eta_oi(pr_s, 40.0, st_s))))
        steeper_falls.append(100.0 * (1.0 - steeper.eta_oi(pr_s, 15.0, st_s) / steeper.eta_oi(pr_s, 40.0, st_s)))
    assert (
        f"drops by {falls[0][1]:.1f} % between the rated speed and 15 rev/s at a pressure ratio of {falls[0][0]:.1f} "
        f"and by {falls[1][1]:.1f} % at {falls[1][0]:.1f}"
    ) in flat
    assert f"goes from {rated.eta_inverter(40.0):.3f} to {rated.eta_inverter(15.0):.3f}" in flat
    # The other shape falls about twice and three times as far over the same range: that is the disagreement.
    assert all(other > 1.5 * default_fall for other, (_, default_fall) in zip(steeper_falls, falls, strict=True))
    assert f"with ``shao_informed`` the same two drops are {steeper_falls[0]:.1f} and {steeper_falls[1]:.1f} %" in flat

    # Above the data in pressure ratio: the archive's three slopes.
    high = pd.read_csv(DEFAULTS_ARCHIVE / "eta_v_high_pr.csv").set_index("variant")
    fit, arep26, shao = (
        high.loc[next(k for k in high.index if k.startswith(s))] for s in ("selected", "PR slope", "Shao")
    )
    assert (
        f"the selected fit gives {fit.eta_v_PR8:.3f} at a pressure ratio of 8 and {fit.eta_v_PR10:.3f} at 10; the "
        f"slope of the measured machine alone gives {arep26.eta_v_PR8:.3f} and {arep26.eta_v_PR10:.3f}, that of the "
        f"2004 machine alone {shao.eta_v_PR8:.3f} and {shao.eta_v_PR10:.3f}"
    ) in flat

    # Above one: the volumetric form of the default at twice the rated speed, and the level it comes from.
    c0 = SEMI_EMPIRICAL_TYPES["rotary_twin_inverter"]["eta_v"]["c0"]
    assert f"Its level :math:`c_0` is {c0:.2f}" in flat
    twice = []
    for target in (1.5, 2.0):
        p2 = CP.PropsSI("P", "T", 303.15, "Q", 1, "R32")
        t_evap = CP.PropsSI("T", "P", p2 / target, "Q", 1, "R32") - 273.15
        pr_v, st_v = state("R32", t_evap, 30.0, 5.0)
        twice.append(SemiEmpiricalCompressor(CompressorSpec(), "R32", 60.0).eta_v(pr_v, 120.0, st_v))
    assert f"(it gives {twice[0]:.2f} and {twice[1]:.2f} at twice the rated speed)" in flat
    assert all(value > 1.0 for value in twice)

    # The validation set has no scroll without injection.
    summary = pd.read_csv(SUMMARY)
    water = summary[(summary.model_class == "ASHPB") & (summary.status == "adopted")]
    scrolls = water[water.compressor_type == "scroll"]
    assert len(scrolls) == 4 and set(scrolls.injection) == {"vapor", "flash"}
    assert int((water.compressor_type == "rotary").sum()) == 6
    air = summary[(summary.model_class == "ASHP") & (summary.status == "adopted")]
    assert len(air) == 5 and set(air.compressor_type) == {"swing"}
    assert "No unit of the validation set has a scroll without injection" in flat
    assert "the four air-to-water units with a scroll have vapour or flash injection" in flat
    assert "The five adopted air-to-air units have swing compressors" in flat


def test_defaults_rated_speed_table_matches_the_constructors() -> None:
    """Rated speeds with the catalogue displacement and with the earlier constant, as the present library gives them."""
    from tmhp import (
        AirSourceHeatPump,
        AirSourceHeatPumpBoiler,
        GroundSourceHeatPump,
        GroundSourceHeatPumpBoiler,
        WaterSourceHeatPumpBoiler,
    )
    from tmhp.compressor_efficiency import (
        COMPRESSOR_TYPES,
        ROTARY_SPEED_VARIANTS,
        SEMI_EMPIRICAL_TYPES,
        CompressorSpec,
    )
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
    hull = SEMI_EMPIRICAL_TYPES[COMPRESSOR_TYPES[0]]["hull_PR_N"]
    n_low, n_high = min(n for _, n in hull), max(n for _, n in hull)
    below = unbuildable = 0
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
        assert _defaults_cells(table, label) == [before, f"{model.rps_rated:.1f}"], label
    assert (below, unbuildable) == (1, 1)
    flat = re.sub(r"\s+", " ", section)
    assert f"speed range of the rotary efficiency data, {n_low:g} to {n_high:g} rev/s" in flat
    assert "with the constant one was below it and one could not be built" in flat
    # What the rated speed depends on: the speed shape of the rotary types, a little, for a heating-rated machine;
    # not the fixed control power.
    other_shape = CompressorSpec(COMPRESSOR_TYPES[0], ROTARY_SPEED_VARIANTS[1])
    assert other_shape.rotary_speed_variant == "shao_informed"
    moves = {}
    for label, (cls, capacity, ref) in rows.items():
        model = cls(hp_capacity=capacity, ref=ref)
        assert cls(hp_capacity=capacity, ref=ref, E_ctrl=0.0).rps_rated == model.rps_rated, label
        other = cls(hp_capacity=capacity, ref=ref, compressor_type=other_shape)
        moves[label] = (model.rps_rated - other.rps_rated, other.rps_rated)
    boilers = [move for label, (move, _) in moves.items() if label.endswith("R290")]
    assert len(boilers) == 3 and min(boilers) > 0.0
    r134a_move, r134a_other = moves["``GroundSourceHeatPumpBoiler``, 8 kW, R134a"]
    assert (
        f"the R290 boilers of this table differ by {min(boilers):.1f} to {max(boilers):.1f} rev/s and the R134a "
        f"ground-source boiler by {r134a_move:.0f} rev/s ({r134a_other:.1f} rev/s with ``shao_informed``)"
    ) in flat
    cooling_rated = [abs(move) for label, (move, _) in moves.items() if "Boiler" not in label]
    assert len(cooling_rated) == 2 and max(cooling_rated) < 0.05
    assert "the two cooling-rated units do not move" in flat
    assert "does not enter the rated speed" in flat
    # The boilers the constant was removed from last: the default machines, before and after.
    for cls in (GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler):
        assert (cls().hp_capacity, cls().ref) == (8000.0, "R290")
    ground = {ref: GroundSourceHeatPumpBoiler(hp_capacity=8000.0, ref=ref) for ref in ("R32", "R410A", "R290", "R134a")}
    assert (
        f"at 8 kW, {ground['R32'].rps_rated:.1f} rev/s with R32, {ground['R410A'].rps_rated:.1f} with R410A, "
        f"{ground['R290'].rps_rated:.1f} with R290 and {ground['R134a'].rps_rated:.1f} with R134a"
    ) in flat
    for ref in ("R290", "R134a"):
        assert ground[ref].rps_rated > 80.0 and ground[ref].rps_max == default_rps_max(ground[ref].rps_rated)
    assert (
        f"({ground['R290'].rps_max:.1f} rev/s for the R290 machine, {ground['R134a'].rps_max:.1f} for the R134a one)"
    ) in flat
    water = WaterSourceHeatPumpBoiler(hp_capacity=8000.0, ref="R290")
    water_before = WaterSourceHeatPumpBoiler(hp_capacity=8000.0, ref="R290", V_cmp_ref=earlier_per_W * 8000.0)
    ground_before = GroundSourceHeatPumpBoiler(hp_capacity=8000.0, ref="R290", V_cmp_ref=earlier_per_W * 8000.0)
    assert (
        f"went from a rated speed of {ground_before.rps_rated:.1f} to {ground['R290'].rps_rated:.1f} rev/s and the "
        f"water-source boiler from {water_before.rps_rated:.1f} to {water.rps_rated:.1f}"
    ) in flat
    assert f"is now rated at {ground['R134a'].rps_rated:.1f} rev/s" in flat


def test_defaults_trend_verdict_prose_matches_the_study() -> None:
    """Counts, medians, percentiles and speeds of the EN 14825 paragraphs are the committed study's."""
    from tmhp.compressor_efficiency import COMPRESSOR_TYPES, SEMI_EMPIRICAL_TYPES

    verdict = pd.read_csv(TREND_VERDICT)
    trend = pd.read_csv(TREND_POINTS)
    reference = pd.read_csv(KEYMARK_SUMMARY).set_index(["application", "point"])
    gradient = pd.read_csv(KEYMARK_GRADIENT).set_index("application")
    flat = re.sub(r"\s+", " ", _defaults_section("Does the coefficient set survive both tests?"))
    key = ["application", "capacity_kW", "refrigerant", "oversizing"]

    def outside(variant: str) -> pd.DataFrame:
        """Per run and point: -1 below the certified p10, +1 above the p90, 0 inside."""
        cop = trend[trend.variant == variant].pivot_table(index=key, columns="point", values="cop")
        flags = pd.DataFrame(index=cop.index)
        for point in "ABCD":
            p10 = cop.index.get_level_values("application").map(lambda a, p=point: reference.cop_p10[(a, p)])
            p90 = cop.index.get_level_values("application").map(lambda a, p=point: reference.cop_p90[(a, p)])
            flags[point] = (cop[point].to_numpy() > p90.to_numpy()).astype(int) - (
                cop[point].to_numpy() < p10.to_numpy()
            ).astype(int)
        return flags

    def by_application(mask: pd.Series) -> tuple[int, int]:
        counts = mask.groupby(level="application").sum()
        return int(counts.get("low", 0)), int(counts.get("medium", 0))

    by_variant = verdict.groupby("variant")
    assert (by_variant.size() == 30).all() and (by_variant.C1_shape.sum() == 30).all()
    shipped = verdict[verdict.variant == "defaults"]
    before = verdict[verdict.variant == "quadratic-2026-10-06"]

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
    before_medians = before.groupby("application").gradient_DA.median()
    assert int(before.C3_gradient.sum()) == 30 and int(by_variant.C3_gradient.sum()["constant"]) == 30
    assert f"(medians {before_medians['low']:.2f} and {before_medians['medium']:.2f})" in flat
    assert (
        f"``ideal`` passes in {int(by_variant.C3_gradient.sum()['ideal'])} and ``legacy-v1`` in "
        f"{int(by_variant.C3_gradient.sum()['legacy-v1'])}"
    ) in flat

    # Level: how many runs pass, which, and where the others are outside.
    flags = outside("defaults")
    inside = (flags == 0).all(axis=1)
    assert int(inside.sum()) == int(shipped.C2_level.sum()) == 27
    low, medium = by_application(inside)
    assert (low, medium) == (15, 12)
    assert (
        "Twenty-seven of the thirty runs have all four points inside the certified p10–p90: all "
        f"{_NUMBER_WORDS[low]} low-temperature runs and {_NUMBER_WORDS[medium]} of the medium-temperature ones"
    ) in flat
    assert (flags[["A", "D"]] == 0).all().all() and "No run is outside at point A or at point D" in flat
    # The three that fail are one machine, below the band at C and, in one run, at B.
    failing = flags[~inside].reset_index()
    assert set(zip(failing.application, failing.capacity_kW, failing.refrigerant, strict=True)) == {
        ("medium", 9.0, "R410A")
    }
    c_low, c_medium = by_application(flags.C < 0)
    b_low, b_medium = by_application(flags.B < 0)
    assert ((flags.B < 0) <= (flags.C < 0)).all() and not (flags[["B", "C"]] > 0).any().any()
    assert (c_low, c_medium, b_low, b_medium) == (0, 3, 0, 1)
    assert (
        f"The {_NUMBER_WORDS[c_medium]} that fail are the medium-temperature runs of the 9 kW R410A machine, below the "
        f"10th percentile at C (+7 °C, 35 % of the design load) in all {_NUMBER_WORDS[c_medium]} and at B (+2 °C, "
        f"54 %) in {_NUMBER_WORDS[b_medium]}"
    ) in flat
    # The criterion was the one the speed shape was chosen on, and the page says so in the paragraph's heading.
    assert (
        "**The level sits inside the certified band — which, for the default, was the criterion of a choice.**" in flat
    )

    def percentiles(frame: pd.DataFrame, application: str) -> str:
        row = frame[
            (frame.application == application)
            & (frame.refrigerant == "R32")
            & (frame.capacity_kW == 9.0)
            & (frame.oversizing == 0.667)
        ].iloc[0]
        return " / ".join(f"{row[f'pct_{point}']:.0f}" for point in "ABCD")

    assert f"sits at the {percentiles(shipped, 'low')}rd percentile at points A / B / C / D" in flat
    assert f"the medium-temperature one at {percentiles(shipped, 'medium')}:" in flat
    # Below the certified median at the three colder points and above it at D, in both applications.
    for application in ("low", "medium"):
        a, b, c, d = (float(value) for value in percentiles(shipped, application).split(" / "))
        assert max(a, b, c) < 50.0 < d, application
    assert "below the median at the three colder points and above it at D" in flat
    # The point nearest the edge of the band: C of the medium-temperature application, only just inside.
    r32 = trend[
        (trend.variant == "defaults")
        & (trend.application == "medium")
        & (trend.refrigerant == "R32")
        & (trend.capacity_kW == 9.0)
        & (trend.oversizing == 0.667)
    ].set_index("point")
    assert (
        f"(a COP of {r32.cop['C']:.2f} against a 10th percentile of {reference.cop_p10[('medium', 'C')]:.2f})" in flat
    )
    assert reference.cop_p10[("medium", "C")] < r32.cop["C"] < reference.cop_p10[("medium", "C")] + 0.05

    # Where the compressor runs at each point, against the data hull of the default type.
    n_low = min(n for _, n in SEMI_EMPIRICAL_TYPES[COMPRESSOR_TYPES[0]]["hull_PR_N"])
    speed = trend[trend.variant == "defaults"].groupby("point").rps
    assert f"At A it turns at {speed.min()['A']:.0f} to {speed.max()['A']:.0f} rev/s" in flat
    slow_b = int((trend[(trend.variant == "defaults") & (trend.point == "B")].rps < n_low).sum())
    assert (
        f"at B at {speed.min()['B']:.0f} to {speed.max()['B']:.0f}, below the {n_low:g} rev/s where the rotary data "
        f"start in {slow_b} of the 30 runs"
    ) in flat
    assert f"at C at {speed.min()['C']:.0f} to {speed.max()['C']:.0f} and at D at {speed.max()['D']:.0f}" in flat
    assert speed.min()["D"] == speed.max()["D"] == 15.0

    # The previous release on the same tree: its count, the side it fails on, and the COP ratio against it.
    flags_before = outside("quadratic-2026-10-06")
    inside_before = (flags_before == 0).all(axis=1)
    low, medium = by_application(inside_before)
    assert int(inside_before.sum()) == int(before.C2_level.sum())
    assert f"passes it in {low + medium} ({low} low- and {medium} medium-temperature)" in flat
    assert not (flags_before < 0).any().any()
    above_before = (flags_before > 0).sum()
    assert by_application(flags_before.A > 0) == (6, 0) and int(above_before.sum()) == 6
    assert "failing above the 90th percentile at point A in six low-temperature runs" in flat
    # The level criterion does not single out the default: two earlier descriptions pass it in most runs too.
    level = by_variant.C2_level.sum()
    assert (int(level["legacy-v1"]), int(level["constant"]), int(level["ideal"])) == (26, 0, 0)
    assert "Nor does the level criterion single out the default" in flat
    assert f"``legacy-v1`` passes it in {int(level['legacy-v1'])}, ``constant`` and ``ideal`` in none" in flat
    wide = {
        variant: trend[trend.variant == variant].pivot_table(index=key, columns="point", values="cop")
        for variant in ("defaults", "quadratic-2026-10-06")
    }
    ratio = (wide["defaults"] / wide["quadratic-2026-10-06"]).groupby(level="application").median()
    assert (
        f"the default's COP is {100 * (1 - ratio.A['low']):.0f} % lower at point A of the low-temperature application "
        f"and {100 * (1 - ratio.B['medium']):.0f} and {100 * (1 - ratio.C['medium']):.0f} % lower at points B and C "
        f"of the medium-temperature one, and {100 * (ratio.D['low'] - 1):.0f} % higher at point D of the "
        "low-temperature application"
    ) in flat
    assert f"and meets it in {low + medium} runs on the present tree" in flat
    assert (
        f"puts the R32 9 kW machine at the {percentiles(before, 'medium').split(' / ')[2]}th percentile there" in flat
    )

    # The history of the count: the present value is the committed verdict's, and the two parts of the last step
    # are the tables of the study the choice was taken on (fixed power added after the solve).
    assert f"and {int(inside.sum())} now, with the ``ossorio_anchored`` shape and the fixed control power" in flat
    study = pd.read_csv(REPO_ROOT / "validation" / "data" / "rotary_speed_evidence" / "en14825_summary.csv")
    study = study[(study.application == "both") & (study.quantity == "all four points")]
    count = {(row.variant, row.P_fixed_W): int(row.cases_inside) for row in study.itertuples()}
    start, shape_only = count[("shao_informed", 0.0)], count[("ossorio_anchored", 0.0)]
    power_only, both = count[("shao_informed", 15.0)], count[("ossorio_anchored", 15.0)]
    assert both == int(inside.sum())
    assert (
        f"the speed shape alone takes the count from {start} to {shape_only}, the control power alone from {start} to "
        f"{power_only}, and the two together to {both}"
    ) in flat
    assert "Nothing was fitted to this count; the choice between the two speed shapes was made on it." in flat


def test_defaults_figures_measured_on_a_model_are_what_the_library_returns() -> None:
    """The figures the page quotes from a built machine: the low-speed fall, the lift of a split unit, a variant above one."""
    import CoolProp.CoolProp as CP
    from validation.analysis.en14825_trend import en14825_water_flow, solve_at_leaving_water

    from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
    from tmhp.compressor_efficiency import CompressorSpec, SemiEmpiricalCompressor, suction_state

    # Frost left out, the running machine: full load, 75 % and the speed floor at two conditions.
    limits = re.sub(r"\s+", " ", _defaults_section("Limits and provisional items")).replace("−", "-")
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
        assert bool(result["efficiency_extrapolated"])  # 15 rev/s is below the rotary data
    cold, mild = figures[-7.0], figures[7.0]
    assert (
        f"at -7 °C outdoor air and 45 °C leaving water has a COP of {cold[0]:.2f} at full load, {cold[1]:.2f} at 75 % "
        f"load and {cold[2]:.2f} at its speed floor ({cold[3]:.0f} % of the nameplate); at +7 °C and 35 °C it has "
        f"{mild[0]:.2f}, {mild[1]:.2f} and {mild[2]:.2f} ({mild[3]:.0f} %)"
    ) in limits
    # With the default speed shape the fall to the floor is moderate, and the curve peaks below full load: the
    # steep fall of the earlier default (a third of the COP at this cold point) is the other shape's.
    for full, three_quarters, floor, _ in (cold, mild):
        assert three_quarters > full > floor > 0.8 * full
    steeper = AirSourceHeatPumpBoiler(
        hp_capacity=9000.0,
        ref="R32",
        Cd_cycling=None,
        defrost=False,
        compressor_type=CompressorSpec("rotary_twin_inverter", "shao_informed"),
    )
    steeper_full, _ = solve_at_leaving_water(steeper, -7.0, 45.0, steeper.hp_capacity, flow)
    steeper_floor, _ = solve_at_leaving_water(steeper, -7.0, 45.0, 0.1 * steeper.hp_capacity, flow)
    assert float(steeper_floor["cop_sys [-]"]) < 0.7 * float(steeper_full["cop_sys [-]"])
    assert "Whether real inverter rotaries lose that little at a low speed, or as much as the other shape has it" in (
        limits
    )

    # The pressure ratio a default split unit reaches heating from -15 degC, in the Summary's pressure-ratio row.
    ratios = []
    for capacity, ref in ((3500.0, "R32"), (4000.0, "R32"), (8000.0, "R410A")):
        unit = AirSourceHeatPump(hp_capacity=capacity, ref=ref)
        result = unit.analyze_steady(Q_r_iu=-capacity, T0=-15.0, T_a_room=20.0, return_dict=True, verbose=False)
        assert result["failure_reason"] == "none" and result["capacity_clamped"] == "max"
        assert result["cmp_rpm [rpm]"] / 60.0 == pytest.approx(unit.rps_max)
        ratios.append(result["P_ref_cmp_out [Pa]"] / result["P_ref_cmp_in [Pa]"])
    summary = re.sub(r"\s+", " ", _list_table_row(_summary_table(_read(DEFAULTS_PAGE)), "Pressure-ratio limit"))
    assert f"reaches {min(ratios):.1f} to {max(ratios):.1f} at its speed ceiling" in summary

    # The default rotary form above one with wet suction; the other speed shape at the same state is not.
    ref, quality = "R290", 0.7
    p1 = CP.PropsSI("P", "T", 273.15, "Q", 1, ref)
    p2 = CP.PropsSI("P", "T", 65.0 + 273.15, "Q", 1, ref)
    h1 = CP.PropsSI("H", "P", p1, "Q", quality, ref)
    isentropic = CP.PropsSI("H", "P", p2, "S", CP.PropsSI("S", "P", p1, "Q", quality, ref), ref)
    state = suction_state(p1, CP.PropsSI("D", "P", p1, "Q", quality, ref), h1, isentropic)
    default = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter"), ref, 60.0)
    assert default.spec.rotary_speed_variant == "ossorio_anchored"
    other = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter", "shao_informed"), ref, 60.0)
    value = default.eta_oi(p2 / p1, 60.0, state)
    assert value > 1.0 > other.eta_oi(p2 / p1, 60.0, state)
    assert "**The default rotary form can exceed one.**" in limits
    assert (
        f"At a pressure ratio of {p2 / p1:.1f} with {ref} and a suction quality of {quality:g} the form gives "
        f":math:`\\eta_{{oi}}` = {value:.2f}"
    ) in limits
    assert f"it gives {other.eta_oi(p2 / p1, 60.0, state):.2f} at the state named above" in limits
    # The grid count is the archive's; the page repeats every part of it.
    readme = re.sub(r"\s+", " ", (DEFAULTS_ARCHIVE / "README.md").read_text())
    assert "eta_oi is above one in 292 of 3,500 states" in readme
    assert "and in 5 states with saturated suction (PR 10, up to 1.08)" in readme
    assert "it is above one in 292 of 3,500 states" in limits
    assert "and in 5 states with a saturated suction (at a ratio of 10, up to 1.08)" in limits

    # Does a machine get there? The default air-to-water unit whose discharge-temperature control wets the suction
    # (R32), at the coldest points it runs at: the suction stays nearly dry and the form well below one.
    qualities, raw = [], []
    hot_flow = en14825_water_flow(model.hp_capacity, "medium")  # the flow of the medium-temperature application
    for t_outdoor, leaving in ((-25.0, 55.0), (-20.0, 55.0), (-15.0, 65.0)):
        for fraction in (1.0, 0.3):
            result, _ = solve_at_leaving_water(model, t_outdoor, leaving, model.hp_capacity * fraction, hot_flow)
            assert result["failure_reason"] == "none", (t_outdoor, leaving, fraction)
            p_in, p_out = float(result["P_ref_cmp_in [Pa]"]), float(result["P_ref_cmp_out [Pa]"])
            h_in = float(result["h_ref_cmp_in [J/kg]"])
            entropy = CP.PropsSI("S", "P", p_in, "H", h_in, "R32")
            wet = suction_state(
                p_in,
                CP.PropsSI("D", "P", p_in, "H", h_in, "R32"),
                h_in,
                CP.PropsSI("H", "P", p_out, "S", entropy, "R32"),
            )
            qualities.append(CP.PropsSI("Q", "P", p_in, "H", h_in, "R32"))
            machine = SemiEmpiricalCompressor(CompressorSpec("rotary_twin_inverter"), "R32", model.rps_rated)
            raw.append(machine.eta_oi(p_out / p_in, float(result["cmp_rpm [rpm]"]) / 60.0, wet))
    assert all(0.95 < quality < 1.0 for quality in qualities)  # wet, and only just
    assert (
        "at -25 and -20 °C outdoor air with 55 °C leaving water and at -15 °C with 65 °C, runs with a suction quality "
        f"above 0.95, and the form gives {min(raw):.2f} to {max(raw):.2f} there"
    ) in limits, (qualities, raw)


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


def test_defaults_speed_shape_choice_is_what_the_library_returns() -> None:
    """Both steps of the choice of the rotary speed shape, run again on the study's thirty cases.

    Without the fixed control power the trajectory-shape rule separates the two shapes: that was the first choice
    (``shao_informed``). With the power in the model the rule no longer separates them and the level does: that is
    the present default (``ossorio_anchored``). The page's counts for both steps are what the library returns.
    """
    import math

    from tmhp.compressor_efficiency import ROTARY_SPEED_VARIANTS, CompressorSpec
    from tmhp.control_power import CONTROL_POWER_SINK_WATER_W

    default_shape, other_shape = ROTARY_SPEED_VARIANTS
    assert (default_shape, other_shape) == ("ossorio_anchored", "shao_informed")
    gradient = pd.read_csv(KEYMARK_GRADIENT).set_index("application")
    committed = pd.read_csv(TREND_POINTS).query("variant == 'defaults'")

    def spec(variant: str) -> CompressorSpec:
        return CompressorSpec("rotary_twin_inverter", variant)

    with_power = CONTROL_POWER_SINK_WATER_W
    measured = {
        # The default with the control power is the committed study itself.
        (default_shape, with_power): committed.pivot_table(index=_TREND_KEY, columns="point", values="cop"),
        (other_shape, with_power): _defaults_trend_cops(compressor_type=spec(other_shape)),
        (default_shape, 0.0): _defaults_trend_cops(compressor_type=spec(default_shape), E_ctrl=0.0),
        (other_shape, 0.0): _defaults_trend_cops(compressor_type=spec(other_shape), E_ctrl=0.0),
    }
    counts = {}
    for case, cops in measured.items():
        application = cops.index.get_level_values("application")
        ratio = cops.D / cops.A
        low, high, median = (
            application.map(gradient[c]).to_numpy() for c in ("ratio_p10", "ratio_p90", "ratio_median")
        )
        flags = _defaults_outside_band(cops)
        low_cases = cops.xs("low", level="application")
        counts[case] = {
            "shape": int(((ratio >= low) & (ratio <= high)).sum()),
            "distance": float((ratio / median).map(lambda v: abs(math.log(v))).median()),
            "level": int((flags == 0).all(axis=1).sum()),
            "B/A": float((low_cases.B / low_cases.A).median()),
            "C/A": float((low_cases.C / low_cases.A).median()),
            "part load below": int(((flags.B < 0) | (flags.C < 0)).sum()),
            "D above": _defaults_count(flags.D > 0),
        }
    choice = re.sub(r"\s+", " ", _defaults_section("Compressor data, not heat-pump data"))

    # First step, on the model without the control power: a difference of three cases or more decides.
    first, first_other = counts[(other_shape, 0.0)], counts[(default_shape, 0.0)]
    assert first["shape"] - first_other["shape"] >= 3
    assert (
        f"There the count was {first['shape']} of 30 cases for ``shao_informed`` against {first_other['shape']} of 30 "
        f"for ``ossorio_anchored``, with a median distance from the certified median ratio of "
        f"{first['distance']:.3f} against {first_other['distance']:.3f} in logarithm"
    ) in choice
    assert first_other["D above"][1] == 0
    assert f"point D above the 90th percentile in {first_other['D above'][0]} cases" in choice

    # Second step, with the control power: the rule no longer decides, the level does.
    default, other = counts[(default_shape, with_power)], counts[(other_shape, with_power)]
    assert abs(default["shape"] - other["shape"]) < 3 and default["shape"] == other["shape"] == 30
    assert f"the ratio lies inside the band in {default['shape']} of 30 cases under both" in choice
    assert default["level"] - other["level"] >= 3
    assert (
        f"in {default['level']} of the 30 cases under ``ossorio_anchored`` against {other['level']} under "
        "``shao_informed``"
    ) in choice
    assert (
        f"{default['level']} and {default['shape']} for the default (the committed ``en14825_trend_verdict.csv``), "
        f"{other['level']} and {other['shape']} for ``shao_informed``"
    ) in choice
    # The study the decision was taken on added the power after the solve; the counts are the same.
    study = pd.read_csv(REPO_ROOT / "validation" / "data" / "rotary_speed_evidence" / "en14825_summary.csv")
    study = study[study.application == "both"].set_index(["variant", "P_fixed_W", "quantity"]).cases_inside
    for (variant, power), measured_counts in counts.items():
        assert int(study[(variant, power, "all four points")]) == measured_counts["level"], (variant, power)
        assert int(study[(variant, power, "D/A")]) == measured_counts["shape"], (variant, power)

    # What does not support the choice is measured too: the rule's second measure still favours the other shape.
    assert other["distance"] < default["distance"]
    assert (
        f"is {other['distance']:.3f} in logarithm for ``shao_informed`` and {default['distance']:.3f} for the default"
    ) in choice
    # ... and what agrees with it without having decided it: the intermediate ratios.
    medians = pd.read_csv(KEYMARK_SUMMARY).query("application == 'low'").set_index("point").cop_median
    assert (
        f"are {default['B/A']:.2f} and {default['C/A']:.2f} for the default and {other['B/A']:.2f} and "
        f"{other['C/A']:.2f} for ``shao_informed``; the ratios of the certified medians are "
        f"{medians.B / medians.A:.2f} and {medians.C / medians.A:.2f}"
    ) in choice
    survive = re.sub(r"\s+", " ", _defaults_section("Does the coefficient set survive both tests?"))
    assert f"the variant that was not chosen passes this criterion in {other['level']} runs" in survive
    limits = re.sub(r"\s+", " ", _defaults_section("Limits and provisional items"))
    assert default["D above"] == other["D above"] == (0, 0)
    assert (
        f"the default sits below the certified 10th percentile at a part-load point in {default['part load below']} "
        f"of the 30 cases and the variant with the steeper fall in {other['part load below']}; neither is above the "
        "90th percentile at point D"
    ) in limits


def test_defaults_frost_paragraph_is_what_the_models_return() -> None:
    """The trajectory without the frost adjustment and with it in full at every load, on the study's thirty cases."""
    from tmhp.defrost import DefrostModel

    committed = pd.read_csv(TREND_POINTS).query("variant == 'defaults'")
    shipped = committed.pivot_table(index=_TREND_KEY, columns="point", values="cop")
    without = _defaults_trend_cops(defrost=False)
    in_full = _defaults_trend_cops(defrost=DefrostModel(load_weighted=False))
    flat = re.sub(r"\s+", " ", _defaults_section("Does the coefficient set survive both tests?"))

    # Frost acts on the two cold points only.
    for other in (without, in_full):
        assert (other[["C", "D"]] - shipped.loc[other.index, ["C", "D"]]).abs().to_numpy().max() < 1e-6
    flags = _defaults_outside_band(without)
    above_a = _defaults_count(flags.A > 0)
    passing = (flags == 0).all(axis=1)
    # Without any frost adjustment point A is too high in every low-temperature run; the runs that still pass are
    # medium-temperature ones.
    assert above_a[0] == 15 and _defaults_count(passing)[0] == 0
    assert (
        f"above the 90th percentile at point A in {above_a[0]} of the 15 low-temperature runs and in {above_a[1]} of "
        f"the medium-temperature ones, {int(passing.sum())} runs passing"
    ) in flat
    assert int(passing.sum()) < int((_defaults_outside_band(shipped) == 0).all(axis=1).sum())
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


def test_defaults_fixed_control_power_matches_the_declarations_and_the_library() -> None:
    """The fixed control power: declared medians and their counts, the constructor defaults, the shares, the limits."""
    from validation.analysis.en14825_trend import (
        CONFIGURATIONS,
        EN14825_POINTS,
        OVERSIZING,
        en14825_water_flow,
        solve_at_leaving_water,
    )

    from tmhp import (
        AirSourceHeatPump,
        AirSourceHeatPumpBoiler,
        GroundSourceHeatPump,
        GroundSourceHeatPumpBoiler,
        WaterSourceHeatPumpBoiler,
    )
    from tmhp.control_power import CONTROL_POWER_SINK_AIR_W, CONTROL_POWER_SINK_WATER_W

    auxiliary = REPO_ROOT / "validation" / "data" / "auxiliary_power"
    water = pd.read_csv(auxiliary / "keymark_pto_air_to_water.csv")
    air = pd.read_csv(auxiliary / "keymark_pto_air_to_air.csv")
    section = _defaults_section("Fixed control power", "=")
    flat = re.sub(r"\s+", " ", section)

    # The declarations: the statistic each default is, with its counts and quartiles, and the other declaration.
    pooled = pd.concat([water.P_offsb_a_W, water.P_offsb_b_W])
    air_standby = pd.concat([air.P_offsb_a_W, air.P_offsb_b_W])
    assert pooled.median() == CONTROL_POWER_SINK_WATER_W == 15.0
    assert air.P_TO_W.median() == CONTROL_POWER_SINK_AIR_W == 12.0

    def quartiles(series: pd.Series) -> str:
        return f"quartiles {series.quantile(0.25):g}–{series.quantile(0.75):g}"

    start = section.index(".. list-table:: Declared compressor-off powers")
    table = section[start : section.index("The two populations do not use the same declaration", start)]
    assert _defaults_cells(table, "Air-to-water") == [
        f"{len(water):,} / {water.outdoor_unit.nunique():,} / {water.manufacturer.nunique()}",
        f"Median of the two off/standby declarations pooled: {pooled.median():g} W ({quartiles(pooled)})",
        f"``P_TO``: median {water.P_TO_W.median():g} W ({quartiles(water.P_TO_W)})",
    ]
    assert _defaults_cells(table, "Air-to-air") == [
        f"{len(air)} / {air.outdoor_unit.nunique()} / {air.manufacturer.nunique()}",
        f"Median ``P_TO``: {air.P_TO_W.median():g} W ({quartiles(air.P_TO_W)})",
        f"Off/standby: median {air_standby.median():g} W",
    ]
    # The record of the levels beside the study that used them says the same.
    levels = pd.read_csv(REPO_ROOT / "validation" / "data" / "rotary_speed_evidence" / "fixed_power_levels.csv")
    levels = levels.set_index(["model_class", "level"])
    median_water, median_air = levels.loc[("ASHPB", "certified_median")], levels.loc[("ASHP", "certified_median")]
    assert median_water.statistic == "median of P_offsb_a_W and P_offsb_b_W pooled"
    assert (median_water.records, median_water.certificates, median_water.outdoor_units) == (
        len(pooled),
        len(water),
        water.outdoor_unit.nunique(),
    )
    assert median_water.P_fixed_W == CONTROL_POWER_SINK_WATER_W
    assert median_air.statistic == "median of P_TO_W" and median_air.P_fixed_W == CONTROL_POWER_SINK_AIR_W
    assert (median_air.certificates, median_air.outdoor_units) == (len(air), air.outdoor_unit.nunique())
    upper_water, upper_air = levels.loc[("ASHPB", "certified_upper")], levels.loc[("ASHP", "certified_upper")]
    assert upper_water.P_fixed_W == water.P_TO_W.quantile(0.75) and upper_air.P_fixed_W == air.P_TO_W.quantile(0.75)

    # The defaults of the constructors, and where each comes from.
    start = section.index(".. list-table:: Default ``E_ctrl``")
    table = section[start : section.index("The archive holds no declared population", start)]
    rows = {
        "``AirSourceHeatPumpBoiler``": (
            (AirSourceHeatPumpBoiler,),
            CONTROL_POWER_SINK_WATER_W,
            "The air-to-water median",
        ),
        "``AirSourceHeatPump``": ((AirSourceHeatPump,), CONTROL_POWER_SINK_AIR_W, "The air-to-air median"),
        "``GroundSourceHeatPumpBoiler``, ``WaterSourceHeatPumpBoiler``": (
            (GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler),
            CONTROL_POWER_SINK_WATER_W,
            "Carried over from the air-to-water family: a unit delivering to water",
        ),
        "``GroundSourceHeatPump``": (
            (GroundSourceHeatPump,),
            CONTROL_POWER_SINK_AIR_W,
            "Carried over from the air-to-air family: a unit delivering to air",
        ),
    }
    for label, (classes, value, basis) in rows.items():
        assert all(cls().E_ctrl == value for cls in classes), label
        assert _defaults_cells(table, label) == [f"{value:g} W", basis], label
    summary = re.sub(
        r"\s+", " ", _list_table_row(_summary_table(_read(DEFAULTS_PAGE)), "Fixed control power (``E_ctrl``)")
    )
    assert (
        f"{CONTROL_POWER_SINK_WATER_W:g} W for a unit delivering to water, {CONTROL_POWER_SINK_AIR_W:g} W for a unit "
        "delivering to air"
    ) in summary
    assert f"of {len(water):,} air-to-water certificates pooled" in summary
    assert f"of {len(air)} air-to-air certificates" in summary and "carried over" in summary

    # What it amounts to at a rating condition of each default machine.
    start = section.index(".. list-table:: Share of ``E_ctrl`` in the electrical input of a default machine")
    table = section[start : section.index("At a rating point the control power lowers", start)]
    shares = []
    for cls, conditions in (
        (AirSourceHeatPump, ("A7/20", "A35/27")),
        (AirSourceHeatPumpBoiler, ("A7/W35",)),
        (GroundSourceHeatPump, ("B0/20", "B25/27")),
        (GroundSourceHeatPumpBoiler, ("B0/W35",)),
        (WaterSourceHeatPumpBoiler, ("W10/W35",)),
    ):
        machine = cls()
        ranges = machine.operating_range().set_index("condition")
        for condition in conditions:
            row = ranges.loc[condition]
            assert bool(row["runs"]) and bool(row["meets_request"]) and row["runtime_fraction [-]"] == 1.0
            e_tot = abs(float(row["Q_delivered [W]"])) / float(row["cop_sys [-]"])
            share = 100.0 * machine.E_ctrl / e_tot
            shares.append(share)
            label = f"``{cls.__name__}`` ({machine.hp_capacity / 1000.0:g} kW, {machine.ref}), {condition}"
            assert _defaults_cells(table, label) == [f"{e_tot:.0f}", f"{share:.1f} %"], label
    assert f"between {min(shares):.1f} and {max(shares):.1f} % for the machines of the table" in flat

    # ... and on the EN 14825 trajectory, where the compressor draws little: the thirty cases at points A and D.
    share, input_d, ratio_d = {"A": [], "D": []}, [], []
    for application, test_points in EN14825_POINTS.items():
        for capacity, refrigerant in CONFIGURATIONS:
            model = AirSourceHeatPumpBoiler(hp_capacity=capacity, ref=refrigerant, Cd_cycling=None)
            flow = en14825_water_flow(capacity, application)
            for oversizing in OVERSIZING:
                for label, t_outdoor, leaving, load_ratio in test_points:
                    if label not in share:
                        continue
                    result, _ = solve_at_leaving_water(
                        model, t_outdoor, leaving, capacity * oversizing * load_ratio, flow
                    )
                    assert result["failure_reason"] == "none" and result["E_ctrl [W]"] == model.E_ctrl
                    share[label].append(100.0 * result["E_ctrl [W]"] / result["E_tot [W]"])
                    if label == "D":
                        assert result["capacity_clamped"] == "min"  # on the speed floor
                        assert result["cmp_rpm [rpm]"] / 60.0 == pytest.approx(model.rps_min)
                        input_d.append(float(result["E_tot [W]"]))
                        ratio_d.append(float(result["P_ref_cmp_out [Pa]"]) / float(result["P_ref_cmp_in [Pa]"]))
    assert len(share["A"]) == len(share["D"]) == 30
    a, d = pd.Series(share["A"]), pd.Series(share["D"])
    (point_d,) = (p for p in EN14825_POINTS["low"] if p[0] == "D")
    assert (point_d[1], point_d[3]) == (12.0, 0.15)
    assert (
        f"{CONTROL_POWER_SINK_WATER_W:g} W is {a.min():.1f} to {a.max():.1f} % of the input at point A and "
        f"{d.min():.1f} to {d.max():.1f} % at point D, the lightest (12 °C outdoor air, 15 % of the design load, the "
        f"compressor on its speed floor, an input of {min(input_d):.0f} to {max(input_d):.0f} W); the medians are "
        f"{a.median():.1f} and {d.median():.1f} %"
    ) in flat
    choice = re.sub(r"\s+", " ", _defaults_section("Compressor data, not heat-pump data"))
    assert f"15 W is about {d.median():.0f} % of the modelled input" in choice
    # Point D lies below the rotary data in speed and in pressure ratio, whatever the speed shape.
    from tmhp.compressor_efficiency import COMPRESSOR_TYPES, SEMI_EMPIRICAL_TYPES

    hull = SEMI_EMPIRICAL_TYPES[COMPRESSOR_TYPES[0]]["hull_PR_N"]
    lowest_ratio, lowest_speed = min(pr for pr, _ in hull), min(n for _, n in hull)
    assert max(ratio_d) < lowest_ratio and AirSourceHeatPumpBoiler().rps_min == 15.0 < lowest_speed == 30.0
    assert (
        "in speed (the data start at 30 rev/s and the machine runs at 15) and in pressure ratio "
        f"({min(ratio_d):.2f} to {max(ratio_d):.2f}; the data start at {lowest_ratio:.2f})"
    ) in choice

    # The limits: the spread, the sensitivity of the part-load count, the thin air-to-air population, the capacity.
    assert (pooled.quantile(0.25), pooled.quantile(0.75)) == (11.0, 21.0)
    assert (
        "A quarter of the air-to-water declarations are at or below 11 W and a quarter at or above 21 W, and the "
        f"upper quartile of ``P_TO`` is {water.P_TO_W.quantile(0.75):g} W ({air.P_TO_W.quantile(0.75):g} W for "
        "air-to-air)"
    ) in flat
    study = pd.read_csv(REPO_ROOT / "validation" / "data" / "rotary_speed_evidence" / "en14825_summary.csv")
    study = study[(study.application == "both") & (study.quantity == "all four points")]
    study = study[study.variant == "ossorio_anchored"].set_index("P_fixed_W").cases_inside
    assert (
        f"With 25 W in place of 15 W the part-load level check of `Does the coefficient set survive both tests?`_ is "
        f"passed by {int(study[25.0])} of the 30 cases, not {int(study[15.0])}"
    ) in flat
    multi = air[air.is_multi_split]
    assert set(air.refrigerant) == {"R32"} and air.manufacturer.nunique() == 3
    assert (
        f"**The air-to-air value rests on {len(air)} certificates** of three manufacturers, all R32, "
        f"{air.Q_rated_kW.min():.1f} to {air.Q_rated_kW.max():.1f} kW; {_NUMBER_WORDS[len(multi)]} of them are "
        f"multi-split outdoor units, which declare {multi.P_TO_W.min():g} to {multi.P_TO_W.max():g} W"
    ) in flat
    assert 0.895 < water.Q_rated_kW.between(3.0, 16.0).mean() < 0.905

    def declared(low: float, high: float) -> float:
        rows = water[(water.Q_rated_kW > low) & (water.Q_rated_kW <= high)]
        return float(pd.concat([rows.P_offsb_a_W, rows.P_offsb_b_W]).median())

    assert (
        f"({declared(0.0, 8.0):g} W up to 8 kW, {declared(8.0, 12.0):g} W from 8 to 12 kW, {declared(12.0, 16.0):g} W "
        f"from 12 to 16 kW). Above 25 kW it does: {declared(25.0, 50.0):g} W from 25 to 50 kW and "
        f"{declared(50.0, 1e9):g} W above 50 kW"
    ) in flat
    # Off rows draw nothing, and the page says where the off time is accounted.
    off = AirSourceHeatPumpBoiler().analyze_steady(T_tank_w=30.0, T0=7.0, Q_ref_tank=0.0)
    assert off["hp_is_on"] is False and off["E_ctrl [W]"] == 0.0 and off["E_tot [W]"] == 0.0
    assert "its row has ``E_ctrl [W]`` = 0" in flat
    # The owner's decision, as a fact.
    assert "regenerated as a draft pull request; the merge is held" in flat
    assert "adopted" not in flat and "released" not in flat


def test_defaults_approach_box_note_is_what_the_library_returns() -> None:
    """The note on the approach-temperature search box: what a default split unit delivers with a 20 K and a 30 K box."""
    from tmhp import AirSourceHeatPump
    from tmhp.default_inputs import estimate_max_airflow

    text = _read(DEFAULTS_PAGE)
    start = text.index(".. admonition:: Where the catalogue airflow meets the dry-coil model")
    note = re.sub(r"\s+", " ", text[start : text.index("The air-to-water outdoor stratum is the weakest", start)])
    note = note.replace("−", "-")

    def delivered(model: AirSourceHeatPump, load: float, t_outdoor: float, room: float) -> dict:
        result = model.analyze_steady(Q_r_iu=load, T0=t_outdoor, T_a_room=room, return_dict=True, verbose=False)
        assert result["failure_reason"] == "none"
        return result

    def fraction(capacity: float, bound: float, point: str, unit: str = "wall_mounted") -> float:
        model = AirSourceHeatPump(
            hp_capacity=capacity, ref="R32", dT_approach_bounds=(1.0, bound), indoor_unit_type=unit
        )
        load, t_outdoor, room = {"T1": (capacity, 35.0, 27.0), "cold": (-capacity, -7.0, 20.0)}[point]
        return abs(delivered(model, load, t_outdoor, room)["Q_ref_iu [W]"]) / capacity

    # The catalogue indoor airflow per kW, where the box matters.
    def per_kW(capacity_kW: float, product: str) -> float:
        law = estimate_max_airflow(capacity_kW * 1000.0, side="indoor", product=product)
        return law.value * 3600.0 / capacity_kW

    assert (
        f"no more than {per_kW(9.0, 'wall_mounted'):.0f} m³/h per kW for wall-mounted units of 9 kW and above "
        f"({per_kW(15.0, 'wall_mounted'):.0f} at 15 kW) and {per_kW(15.0, 'floor_standing'):.0f} for floor-standing "
        "units of any size"
    ) in note
    assert per_kW(50.0, "wall_mounted") < per_kW(9.0, "wall_mounted")
    assert per_kW(3.0, "floor_standing") == pytest.approx(per_kW(15.0, "floor_standing"))

    # With the 20 K box: the rating point in cooling, and heating from -7 degC.
    assert fraction(8000.0, 20.0, "T1") == pytest.approx(1.0, abs=1e-6)
    assert fraction(4000.0, 20.0, "cold") == pytest.approx(1.0, abs=1e-6)
    assert (
        f"only up to 8 kW ({100 * fraction(9000.0, 20.0, 'T1'):.0f} % at 9 kW, "
        f"{100 * fraction(15000.0, 20.0, 'T1'):.0f} % at 15 kW; a floor-standing unit "
        f"{100 * fraction(9000.0, 20.0, 'T1', 'floor_standing'):.0f} % at any capacity), and in heating at -7 °C "
        f"outdoors falls short from 5 kW ({100 * fraction(6000.0, 20.0, 'cold'):.0f} % at 6 kW, "
        f"{100 * fraction(8000.0, 20.0, 'cold'):.0f} % at 8 kW)"
    ) in note
    assert fraction(5000.0, 20.0, "cold") < 0.99
    assert fraction(3000.0, 20.0, "T1", "floor_standing") == pytest.approx(
        fraction(9000.0, 20.0, "T1", "floor_standing"), abs=1e-3
    )
    # With the default 30 K box the same units deliver their nameplate.
    assert AirSourceHeatPump().dT_approach_bounds == (1.0, 30.0)
    for capacity, point in ((15000.0, "T1"), (8000.0, "cold")):
        assert fraction(capacity, 30.0, point) == pytest.approx(1.0, abs=1e-6)

    # The ceiling of the default unit in mild weather: the compressor with the 30 K box, the box at 20 K.
    ceilings = {}
    for bound in (30.0, 20.0):
        model = AirSourceHeatPump(dT_approach_bounds=(1.0, bound))
        assert model.hp_capacity == 4000.0
        for point, load, t_outdoor, room in (("H1", -12000.0, 7.0, 20.0), ("T1", 12000.0, 35.0, 27.0)):
            result = delivered(model, load, t_outdoor, room)
            assert result["capacity_clamped"] == "max"
            assert result["capacity_binding"] == ("compressor" if bound == 30.0 else "cycle")
            ceilings[(bound, point)] = 100.0 * abs(result["Q_ref_iu [W]"]) / model.hp_capacity
    assert (
        f"its compressor ({ceilings[(30.0, 'H1')]:.0f} % of the nameplate at 7/20 °C, {ceilings[(30.0, 'T1')]:.0f} % at "
        f"35/27 °C) rather than the box ({ceilings[(20.0, 'H1')]:.0f} % and {ceilings[(20.0, 'T1')]:.0f} % at 20 K)"
    ) in note

    # The refrigerant temperatures of a 15 kW unit at its nameplate.
    large = AirSourceHeatPump(hp_capacity=15000.0, ref="R32")
    heating = delivered(large, -15000.0, 7.0, 20.0)
    cooling = delivered(large, 15000.0, 35.0, 27.0)
    condensing, evaporating = heating["T_ref_cond_sat_v [°C]"], cooling["T_ref_evap_sat [°C]"]
    assert 20.0 < condensing - 20.0 < 25.0 and 20.0 < 27.0 - evaporating < 25.0
    assert (
        f"a 15 kW unit condenses at {condensing:.0f} °C in a 20 °C room and evaporates at {evaporating:.1f} °C in a "
        "27 °C one"
    ) in note


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
