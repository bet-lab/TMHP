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


#: Row label in the residuals table of validation/index.rst -> its points.
INDEX_ROWS: dict[str, Selector] = {
    "Air-to-water, 10 units": _ashpb,
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
    """The sentence that names each known bias carries the harness value."""
    text = _flat("README.md")
    daikin = points[_daikin(points)]
    cooling = _stats(daikin[daikin["mode"] == "cooling"])[1]
    cold = _stats(daikin[(daikin["mode"] == "heating") & (daikin.t_source_C <= -7.0)])[1]
    ashpb = points[_ashpb(points)]
    frost_free = _stats(ashpb[ashpb.t_source_C >= 7.0])[1]
    plus_two = _stats(ashpb[ashpb.t_source_C == 2.0])[1]
    minus_seven = _stats(ashpb[ashpb.t_source_C == -7.0])[1]
    below = _stats(ashpb[ashpb.t_source_C < -7.0])[1]
    assert f"cooling runs low ({_signed(cooling)} %" in text
    assert f"-7 °C outdoor runs high ({_signed(cold)} %" in text
    assert f"centred at +7 °C outdoor and above ({_signed(frost_free)} %)" in text
    assert (
        f"runs low where the adjustment applies ({round(plus_two):+d} % at +2 °C, "
        f"{round(minus_seven):+d} % at -7 °C, {round(below):+d} % below;"
    ) in text


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
    assert f"inside the {min(spread):.1f} to {max(spread):.1f} % spread" in text


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
    from tmhp.compressor_efficiency import (
        COEFFICIENT_VERSION,
        PR_FIT_MAX,
        PR_FIT_MIN,
        RPS_FIT_MAX,
        RPS_FIT_MIN,
    )
    from tmhp.compressor_speed import DISPLACEMENT_PER_W, default_displacement
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
    # The ground- and water-source boilers keep the fixed constant.
    assert default_displacement(9000.0) == pytest.approx(DISPLACEMENT_PER_W * 9000.0)
    assert f"{DISPLACEMENT_PER_W * 9000.0 * 1e6:g} cm³/rev per 9 kW" in displacement

    efficiency = re.sub(
        r"\s+", " ", _list_table_row(table, "Compressor efficiencies (volumetric, isentropic, electro-mechanical)")
    )
    assert f"``{COEFFICIENT_VERSION}``" in efficiency

    guards = re.sub(r"\s+", " ", _list_table_row(table, "Efficiency guards"))
    assert f"PR {PR_FIT_MIN:.2f}–{PR_FIT_MAX:.2f}" in guards
    assert f"N {RPS_FIT_MIN:.1f}–{RPS_FIT_MAX:.1f} rev/s" in guards

    limit = re.sub(r"\s+", " ", _list_table_row(table, "Pressure-ratio limit"))
    assert f"``PR_cycle_max`` {a2a.PR_cycle_max:g} (air-to-air), {a2w.PR_cycle_max:g} (air-to-water)" in limit


def test_defaults_displacement_table_matches_the_decision_module() -> None:
    """Every cell of the default-displacement table is what the constructors use."""
    from tmhp.compressor_speed import default_displacement
    from tmhp.default_inputs import estimate_displacement

    text = _read("docs/source/validation/defaults.rst")
    start = text.index(".. list-table:: Default displacement [cm³/rev]")
    table = text[start : text.index("The earlier default, 42 cm³/rev per 9 kW", start)]
    for capacity_kW in (3.5, 8.0, 15.0):
        row = re.sub(r"\s+", " ", _list_table_row(table, f"{capacity_kW:g} kW"))
        cells = [cell.strip() for cell in row.split(" - ")[1:]]
        expected = [default_displacement(capacity_kW * 1000.0) * 1e6] + [
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


def test_scope_page_states_the_high_lift_gate_as_the_results_have_it() -> None:
    """The deficit table and the reported injection points are the committed results, row for row."""
    from validation.gate.evaluate import catalogue_point, load_gate

    from tmhp.compressor_efficiency import PR_FIT_MAX, _eval_eff, make_eta_isen

    text = _read("docs/source/concepts/scope-and-limits.rst")
    start = text.index("Known deficit: hot water in cold weather")
    section = text[start : text.index("A short checklist for any result", start)]
    flat = re.sub(r"\s+", " ", section)
    gate = load_gate()

    def stored(entry):
        catalog, point = catalogue_point(entry)
        row = pd.read_csv(RESULTS / f"{catalog.slug}.csv").set_index("point_id").loc[point.id]
        return catalog, row

    for entry in gate["gate"]["catalogue_points"]:
        catalog, row = stored(entry)
        label = f"{catalog.name} ({catalog.refrigerant}, {catalog.nominal_capacity_kW:g} kW)"
        cells = [c.strip() for c in re.sub(r"\s+", " ", _list_table_row(section, label)).split(" - ")[1:]]
        assert cells == [
            f"{row.cop_target:.2f}",
            f"{row.cop_pred:.3f}",
            f"{100 * (row.cop_pred / row.cop_target - 1):+.1f} %".replace("-", "−"),
            f"pressure ratio {row.pr_cmp:.1f}, isentropic efficiency {row.eta_cmp_isen:.3f}",
        ], (catalog.name, cells)

    injection = gate["reported"]["vapour_injection"]["catalogue_points"]
    by_unit: dict[str, list[str]] = {}
    for entry in injection:
        catalog, row = stored(entry)
        by_unit.setdefault(f"{catalog.nominal_capacity_kW:g} kW", []).append(
            f"{100 * (row.cop_pred / row.cop_target - 1):+.1f}".replace("-", "−")
        )
    for size, (w55, w65) in by_unit.items():
        assert f"{w55} and {w65} % at {size}" in flat, (size, w55, w65)

    fit = make_eta_isen(45.0)
    assert f"the fitted range ends at {PR_FIT_MAX:.2f}" in flat
    for pr, words in ((4.3, "at a pressure ratio of 4.3"), (6.0, "at 6.0"), (PR_FIT_MAX, f"at {PR_FIT_MAX:.2f}")):
        assert f"{float(_eval_eff(fit, pr, 45.0)):.3f} {words}" in flat, pr
    assert float(_eval_eff(fit, 9.0, 45.0)) == pytest.approx(float(_eval_eff(fit, PR_FIT_MAX, 45.0)))


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


def test_defaults_ablation_table_matches_the_study() -> None:
    """The four-description comparison quotes ``coefficient_ablation_parity.csv``."""
    ablation = pd.read_csv(ABLATION).set_index("variant")
    text = _read("docs/source/validation/defaults.rst")
    for variant in ablation.index:
        row = re.sub(
            r"\s+", " ", _list_table_row(text, f"``{variant}``" if variant != "defaults" else "**``defaults``**")
        )
        row = row.replace("−", "-")
        mape = ablation.at[variant, "MAPE_air_to_water_pct"]
        bias = ablation.at[variant, "bias_air_to_water_pct"]
        assert f"{mape:.1f} %" in row, f"{variant}: row {row.strip()!r}, study says {mape:.1f} %"
        assert f"{_signed(bias)} %" in row, f"{variant}: row {row.strip()!r}, study says {_signed(bias)} %"


def test_implied_conductance_table_matches_the_decomposition() -> None:
    """The per-machine back-out in index.rst quotes ``residual_decomposition.csv``."""
    decomposition = pd.read_csv(REPO_ROOT / "validation" / "data" / "residual_decomposition.csv")
    text = _read("docs/source/validation/index.rst")
    start = text.index("Where each machine sits in the measured band")
    section = text[start : text.index(".. figure::", start)]
    for _, machine in decomposition.iterrows():
        row = re.sub(r"\s+", " ", _list_table_row(section, machine.unit))
        divisor = machine.implied_ua_divisor
        expected = "none in Q/2–Q/16" if pd.isna(divisor) else f"Q/{divisor:.1f}"
        assert f"{machine.nameplate_EER:.2f}" in row, f"{machine.unit}: EER, row {row.strip()!r}"
        assert expected in row, f"{machine.unit}: row {row.strip()!r}, decomposition says {expected!r}"
