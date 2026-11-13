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

    table = _summary_table(_read("docs/source/validation/defaults.rst"))
    a2a = AirSourceHeatPump(hp_capacity=5000.0, ref="R32")
    a2w = AirSourceHeatPumpBoiler(hp_capacity=9000.0, ref="R32")

    conductance = re.sub(r"\s+", " ", _list_table_row(table, "Outdoor coil conductance (air-to-air)"))
    assert f"UA_ou = hp_capacity / {a2a.hp_capacity / a2a.UA_ou_rated:g}" in conductance

    ratio = re.sub(r"\s+", " ", _list_table_row(table, "Indoor / outdoor conductance ratio"))
    assert f"UA_iu = {a2a.UA_iu_rated / a2a.UA_ou_rated:.1f} × UA_ou" in ratio

    tank = re.sub(r"\s+", " ", _list_table_row(table, "Tank heat-exchanger conductance (air-to-water)"))
    assert f"UA_tank_hx = hp_capacity / {a2w.hp_capacity / a2w.UA_tank_hx:g}" in tank

    displacement = re.sub(r"\s+", " ", _list_table_row(table, "Compressor displacement"))
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
