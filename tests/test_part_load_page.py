"""``docs/source/validation/part-load.rst`` quotes the numbers the model produces.

The page's figures come from ``scripts/validation/part_load_figure.py``, which
writes what it plots to ``validation/data/part_load_figure/`` with a provenance
record. Two links are checked here, the same chain the parity pages have:

* prose -> CSV: every figure the page quotes is recomputed from the committed
  CSVs and looked for in the sentence or table row that states it;
* CSV -> code: the committed CSVs are rerun in full (the fixed-temperature
  sweep, 144 points from the speed floor to the top speed, its 48-point
  entering-water twin and the four EN 14825 points, a few seconds) and must
  match at ``rel=1e-6``.

The page's horizontal axis in panel (a) is the heat delivered over the maximum
capacity at the same temperatures; its tables quote the capacity range and the
COP at the top speed, the nameplate and the floor, and its prose where the
curves leave the compressor maps' data (``efficiency_extrapolated``). The
air-to-air section quotes ``validation/data/ashp_part_load_sweep.csv`` the same
way; its rerun against the model is a sample, in
``tests/test_study_results_current.py::test_ashp_trend_sample_reproduces``.

Before this the page had neither link, and its −7/45 °C sentence kept describing
the frost-free model ("flat", COP 2.73) after frost and defrost became the
default and the curve rose by 17 % (#154).
"""

from __future__ import annotations

import io
import math
import re
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGE = REPO_ROOT / "docs" / "source" / "validation" / "part-load.rst"
DATA = REPO_ROOT / "validation" / "data" / "part_load_figure"
KEYMARK = REPO_ROOT / "validation" / "data" / "keymark_en14825_summary.csv"
ASHP_SWEEP = REPO_ROOT / "validation" / "data" / "ashp_part_load_sweep.csv"
NAMEPLATE_W = 9000.0

HINT = "모델 동작이 바뀌었다. python -m scripts.validation.part_load_figure 로 다시 만들고 part-load.rst 를 고쳐라"


def _flat(text: str) -> str:
    """Whitespace-collapsed text with ASCII minus, so line wraps and U+2212 do not matter."""
    return re.sub(r"\s+", " ", text).replace("−", "-")


def _section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i : text.index(end, i)]


def _list_table_row(text: str, label: str) -> str:
    """The whitespace-collapsed cells of the list-table row that starts with ``* - <label>``."""
    match = re.search(rf"^(\s*)\* - {re.escape(label)}\s*$", text, flags=re.MULTILINE)
    assert match, f"row {label!r} missing"
    rest = text[match.end() :]
    end = re.search(rf"^{re.escape(match.group(1))}\* - |^\S", rest, flags=re.MULTILINE)
    return _flat(rest[: end.start() if end else len(rest)])


def _cells(text: str, label: str) -> list[str]:
    """The cells after the label of a list-table row, in order."""
    return [cell.strip() for cell in _list_table_row(text, label).split(" - ") if cell.strip()]


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sweep() -> pd.DataFrame:
    return pd.read_csv(DATA / "sweep.csv", float_precision="round_trip")


@pytest.fixture(scope="module")
def trajectory() -> pd.DataFrame:
    return pd.read_csv(DATA / "trajectory.csv", float_precision="round_trip").set_index("point")


@pytest.fixture(scope="module")
def band() -> pd.DataFrame:
    keymark = pd.read_csv(KEYMARK)
    return keymark[keymark.application == "low"].set_index("point")


def _curve(sweep: pd.DataFrame, t_outdoor: float, lwt: float) -> dict:
    """One sweep curve: its capacity range, the COP at the top speed, the nameplate and the floor, and its shape.

    ``running`` is the COP of the rows above the floor in drawing order (top speed first); ``below`` that of the
    floor rows, which all resolve to one operating point.
    """
    rows = sweep[(sweep.t_outdoor_C == t_outdoor) & (sweep.lwt_C == lwt)]
    assert (rows.failure_reason == "none").all(), f"{t_outdoor}/{lwt}: a sweep point did not converge"
    q_max = float(rows.q_max_W.iloc[0])
    assert (rows.q_max_W == q_max).all()
    nameplate = rows[rows.is_nameplate]
    assert len(nameplate) == 1 and nameplate.plr.iloc[0] == 1.0 and nameplate.q_request_W.iloc[0] == NAMEPLATE_W
    nameplate = nameplate.iloc[0]
    floor = rows[rows.at_floor]
    assert len(floor) >= 2 and floor.q_delivered_W.max() == pytest.approx(floor.q_delivered_W.min(), rel=1e-5)
    low = floor.loc[floor.delivered_fraction_of_max.idxmax()]
    top = rows.loc[rows.delivered_fraction_of_max.idxmax()]
    assert top.delivered_fraction_of_max == pytest.approx(1.0, rel=1e-3) and top.rps == pytest.approx(120.0, rel=1e-3)
    near_75 = rows.loc[(rows.plr - 0.75).abs().idxmin()]
    running = rows[~rows.at_floor].sort_values("delivered_fraction_of_max", ascending=False)
    inside = rows[~rows.efficiency_extrapolated]
    return {
        "q_max": q_max,
        "q_min": float(low.q_delivered_W),
        "nameplate_of_max_pct": NAMEPLATE_W / q_max * 100.0,
        "floor_of_nameplate_pct": float(low.q_delivered_W) / NAMEPLATE_W * 100.0,
        "top": float(top.cop),
        "at_nameplate": float(nameplate.cop),
        "nameplate_rps": float(nameplate.rps),
        "at_75": float(near_75.cop),
        "plr_75": float(near_75.plr),
        "floor": float(low.cop),
        "top_to_floor_pct": (float(low.cop) / float(top.cop) - 1.0) * 100.0,
        "top_to_nameplate_pct": (float(nameplate.cop) / float(top.cop) - 1.0) * 100.0,
        "gain_pct": (float(low.cop) / float(nameplate.cop) - 1.0) * 100.0,  # nameplate duty -> speed floor
        "running": running.cop.to_numpy(),
        "below": floor.cop.to_numpy(),
        "inside_min_pct": float(inside.delivered_fraction_of_max.min()) * 100.0,
        "inside_max_pct": float(inside.delivered_fraction_of_max.max()) * 100.0,
        "top_extrapolated": bool(top.efficiency_extrapolated),
        "floor_extrapolated": bool(low.efficiency_extrapolated),
    }


def _change(gain_pct: float) -> str:
    """'gains 4.4 %' or 'loses 16.7 %', as the page words a change."""
    return f"gains {gain_pct:.1f} %" if gain_pct >= 0 else f"loses {-gain_pct:.1f} %"


def _bare(gain_pct: float) -> str:
    return f"{abs(gain_pct):.1f} %"


def _condition(t_outdoor: float, lwt: float) -> str:
    """'+7 °C / 35 °C' as the page's tables label a sweep condition (a true minus sign)."""
    return f"{t_outdoor:+.0f} °C / {lwt:.0f} °C".replace("-", "−")


CONDITIONS = ((7.0, 35.0), (7.0, 45.0), (-7.0, 45.0))


# ---------------------------------------------------------------------------
# prose -> CSV
# ---------------------------------------------------------------------------
def test_sweep_columns_are_the_two_axes(sweep: pd.DataFrame) -> None:
    """``plr`` stays the request over the nameplate; the new columns are the request and the delivery over the maximum."""
    assert sweep.plr.to_numpy() == pytest.approx((sweep.q_request_W / NAMEPLATE_W).to_numpy())
    assert sweep.plr_max.to_numpy() == pytest.approx((sweep.q_request_W / sweep.q_max_W).to_numpy())
    assert sweep.delivered_fraction_of_max.to_numpy() == pytest.approx((sweep.q_delivered_W / sweep.q_max_W).to_numpy())
    for t_outdoor, lwt in CONDITIONS:
        rows = sweep[(sweep.t_outdoor_C == t_outdoor) & (sweep.lwt_C == lwt)]
        # Above the floor the machine delivers what is asked, so the two maximum-based columns agree there.
        above = rows[~rows.at_floor & (rows.capacity_clamped != "max")]
        assert above.q_delivered_W.to_numpy() == pytest.approx(above.q_request_W.to_numpy(), rel=1e-6)
        assert (rows.q_request_W.max() <= rows.q_max_W.iloc[0] * (1 + 1e-9)) and rows.plr.max() > 1.0
        # Below it the delivery is the floor's, above every such request.
        assert (rows[rows.at_floor].q_delivered_W >= rows[rows.at_floor].q_request_W * (1 - 1e-9)).all()


def test_panel_a_tables_quote_the_sweep(page: str, sweep: pd.DataFrame) -> None:
    """The capacity-range table and the COP table, row by row."""
    ranges = _section(page, "these ranges:", "Frost and defrost are on")
    cops = _section(page, "Frost and defrost are on", "No curve peaks before the floor")
    for t_outdoor, lwt in CONDITIONS:
        c = _curve(sweep, t_outdoor, lwt)
        label = _condition(t_outdoor, lwt)
        expected = [f"{c['q_min'] / 1000:.2f} kW", f"{c['q_max'] / 1000:.2f} kW", f"{c['nameplate_of_max_pct']:.0f} %"]
        cells = _cells(ranges, label)
        assert cells == expected, f"capacity range {label}: {cells!r}, expected {expected!r}. {HINT}."
        expected = [
            f"{c['top']:.2f}",
            f"{c['at_nameplate']:.2f} ({c['nameplate_rps']:.0f} rev/s)",
            f"{c['floor']:.2f}",
            f"{c['top_to_floor_pct']:+.1f} %",
        ]
        cells = _cells(cops, label)
        assert cells == expected, f"COP table {label}: {cells!r}, expected {expected!r}. {HINT}."
    text = _flat(page)
    w45, cold = _curve(sweep, 7.0, 45.0), _curve(sweep, -7.0, 45.0)
    assert (
        f"At -7 °C the maximum is {cold['q_max'] / 1000:.2f} kW against {w45['q_max'] / 1000:.2f} kW at +7 °C "
        "and the same water temperature"
    ) in text, HINT


def test_panel_a_shape_prose_quotes_the_sweep(page: str, sweep: pd.DataFrame) -> None:
    """'No curve peaks before the floor ... rises at every step ... from the top speed down to the speed floor and
    stays flat below it', and the split of the rise at the nameplate duty."""
    w35, w45, cold = (_curve(sweep, t, lwt) for t, lwt in CONDITIONS)
    text = _flat(page)
    for curve in (w35, w45, cold):
        steps = curve["running"][1:] - curve["running"][:-1]
        assert steps.min() > 0 and curve["floor"] > curve["running"][-1], curve["running"]
        # Flat below the floor: one operating point, its COP agreeing to a few in 1e4.
        assert curve["below"] == pytest.approx(curve["floor"], rel=2e-4), curve["below"]
    assert (
        "No curve peaks before the floor: at each condition the COP rises at every step of the sweep from the top "
        "speed down to the speed floor and stays flat below it"
    ) in text
    # "Most of the rise at +7 °C lies above the nameplate".
    assert w35["top_to_nameplate_pct"] > w35["gain_pct"] and w45["top_to_nameplate_pct"] > w45["gain_pct"]
    expected = (
        f"from the maximum to the 9 kW duty COP rises by {w35['top_to_nameplate_pct']:.1f} %, "
        f"{w45['top_to_nameplate_pct']:.1f} % and {cold['top_to_nameplate_pct']:.1f} % at the three conditions, and "
        f"from the 9 kW duty to the speed floor by {w35['gain_pct']:.1f} %, {w45['gain_pct']:.1f} % and "
        f"{cold['gain_pct']:.1f} %"
    )
    assert expected in text, f"{expected!r}. {HINT}."


def test_panel_a_nameplate_axis_sentence(page: str, sweep: pd.DataFrame) -> None:
    """The nameplate-axis numbers the page used to give, recomputed from the nameplate and floor rows."""
    w35, w45, cold = (_curve(sweep, t, lwt) for t, lwt in CONDITIONS)
    assert w35["gain_pct"] > 0 and w45["gain_pct"] > 0 and cold["gain_pct"] > 0
    assert abs(cold["plr_75"] - 0.75) < 0.011, "the sweep grid no longer has a request near 75 % of the nameplate"
    expected = (
        f"the model {_change(w35['gain_pct'])} between the 9 kW duty and the speed floor "
        f"(which delivers {w35['q_min'] / 1000:.2f} kW, {w35['floor_of_nameplate_pct']:.0f} % of the nameplate) at "
        f"7 °C air / 35 °C water, and {_bare(w45['gain_pct'])} at 7/45 °C. At -7/45 °C it {_change(cold['gain_pct'])} "
        f"(COP {cold['at_nameplate']:.2f} at the nameplate duty, {cold['at_75']:.2f} at 75 % of it, "
        f"{cold['floor']:.2f} at the floor, which delivers {cold['q_min'] / 1000:.2f} kW, "
        f"{cold['floor_of_nameplate_pct']:.0f} % of the nameplate)"
    )
    assert expected in _flat(page), f"part-load.rst panel (a) should read {expected!r}. {HINT}."


def test_panel_a_extrapolation_paragraph(page: str, sweep: pd.DataFrame) -> None:
    """'on these three curves the flag is clear from 30–32 % to 92 % of the maximum; both ends of every curve ...'."""
    from tmhp.compressor_efficiency import DOMAIN

    curves = [_curve(sweep, t, lwt) for t, lwt in CONDITIONS]
    for curve in curves:
        assert curve["top_extrapolated"] and curve["floor_extrapolated"]
    lows = sorted(round(c["inside_min_pct"]) for c in curves)
    highs = sorted(round(c["inside_max_pct"]) for c in curves)
    low_text = f"{lows[0]}–{lows[-1]} %" if lows[0] != lows[-1] else f"{lows[0]} %"
    high_text = f"{highs[0]}–{highs[-1]} %" if highs[0] != highs[-1] else f"{highs[-1]} %"
    text = _flat(page)
    lo, hi = DOMAIN.rps_range
    assert f"runs from {lo:.0f} to {hi:.0f} rev/s, and on these three curves the flag is clear from" in text
    expected = f"the flag is clear from {low_text} to {high_text} of the maximum; both ends of every curve"
    assert expected in text, f"{expected!r}. {HINT}."


def test_denominator_paragraph_quotes_the_nameplate_share(page: str, sweep: pd.DataFrame) -> None:
    w35, w45, cold = (_curve(sweep, t, lwt) for t, lwt in CONDITIONS)
    expected = (
        f"the nameplate is {w35['nameplate_of_max_pct']:.0f} %, {w45['nameplate_of_max_pct']:.0f} % and "
        f"{cold['nameplate_of_max_pct']:.0f} % of it at the three conditions of panel (a)"
    )
    assert expected in _flat(page), f"{expected!r}. {HINT}."


def test_entering_water_paragraph_quotes_its_sweep(page: str, sweep: pd.DataFrame) -> None:
    """The same machine with the entering water held: its floor against the leaving-water curve."""
    rows = pd.read_csv(DATA / "entering_water.csv", float_precision="round_trip")
    assert (rows.failure_reason == "none").all(), "an entering-water sweep point did not converge"
    nameplate = rows[rows.is_nameplate].iloc[0]
    held = _curve(sweep, 7.0, 35.0)
    # At the 9 kW duty the two sweeps are the same operating point (30 °C in, 35 °C out).
    assert nameplate.lwt_C == pytest.approx(35.0, abs=0.01) and nameplate.cop == pytest.approx(
        held["at_nameplate"], rel=1e-3
    )
    peak = rows.loc[rows.cop.idxmax()]
    floor = rows[rows.at_floor].iloc[0]
    # "That curve rises to the speed floor as well".
    assert peak.at_floor and floor.cop == pytest.approx(peak.cop, rel=2e-4)
    assert (rows.t_water_in_C == 30.0).all()
    assert (
        f"water entering at {rows.t_water_in_C.iloc[0]:.0f} °C at the same flow, which is the 7/35 °C point at the "
        "9 kW duty"
    ) in _flat(page)
    expected = (
        f"rises to the speed floor as well, and there reaches COP {floor.cop:.2f}, "
        f"{(floor.cop / nameplate.cop - 1) * 100:.1f} % above the 9 kW duty against {_bare(held['gain_pct'])} with "
        "the leaving water held"
    )
    assert expected in _flat(page), f"part-load.rst should read {expected!r}. {HINT}."


def test_panel_a_decomposition_quotes_the_study(page: str) -> None:
    """'with the three efficiencies frozen ... gains 19.8 % ... with the shipped efficiencies gains 11.2 %; the
    air-to-air model, at pressure ratios of 2.4 to 2.8 at full load, keeps +26 to +35 %'."""
    decomposition = pd.read_csv(REPO_ROOT / "validation" / "data" / "fixed_boundary_plr" / "decomposition.csv")

    def gain(variant: str, model_class: str, ref: str | None = None, t: float | None = None) -> list[tuple]:
        rows = decomposition[(decomposition.variant == variant) & (decomposition.model_class == model_class)]
        if ref is not None:
            rows = rows[(rows.refrigerant == ref) & (rows.t_outdoor_C == t)]
        out = []
        for _, case in rows[rows.failure_reason == "none"].groupby(["refrigerant", "t_outdoor_C", "t_sink_C"]):
            case = case.sort_values("plr_request", ascending=False)
            running = case[case.capacity_clamped.isna()]
            out.append(((running.cop_sys.iloc[-1] / case.cop_sys.iloc[0] - 1.0) * 100.0, case.pr.iloc[0]))
        return out

    text = _flat(page)
    ((frozen, _),) = gain("hx_only", "ASHPB", "R32", 7.0)
    ((shipped, _),) = gain("current", "ASHPB", "R32", 7.0)
    # The compressor takes back part of the heat exchangers' gain, not all of it.
    assert frozen > shipped > 0
    assert f"full-load values the same model gains {frozen:.1f} % between full load and the speed floor" in text
    assert f"with the shipped efficiencies gains {shipped:.1f} %" in text
    assert "take back part of what the heat exchangers give, not all of it" in text
    a2a = gain("current", "ASHP")
    gains, prs = [g for g, _ in a2a], [p for _, p in a2a]
    assert (
        f"at pressure ratios of {min(prs):.1f} to {max(prs):.1f} at full load, keeps "
        f"+{min(gains):.0f} to +{max(gains):.0f} %"
    ) in text


def test_floor_note_quotes_the_fan_and_air_side_of_the_sweeps(page: str) -> None:
    """'the outdoor fan now stays above 58 % of rated flow ... within 5.26 K; the air-to-air sweep stays above 46 %
    and 6.6 K', from the committed fixed-boundary sweeps (9 kW R32 at 7 degC air, water entering at 42.5 degC)."""
    data = REPO_ROOT / "validation" / "data" / "fixed_boundary_plr"
    water = pd.read_csv(data / "ashpb_sweep.csv")
    water = water[
        (water.refrigerant == "R32")
        & (water.capacity_W == 9000.0)
        & (water.t_outdoor_C == 7.0)
        & (water.t_sink_C == 42.5)
    ]
    air = pd.read_csv(data / "ashp_sweep.csv")
    assert len(water) and (water.failure_reason == "none").all() and (air.failure_reason == "none").all()
    note = _flat(_section(page, "Below the speed floor the model delivers", "Panel (b)"))
    assert (
        f"the outdoor fan now stays above {math.floor(100 * water.fan_fraction.min()):.0f} % of rated flow over the "
        f"whole sweep and the air-side temperature drop stays within {water.air_dT_K.max():.2f} K"
    ) in note
    assert (
        f"the air-to-air sweep stays above {math.floor(100 * air.fan_fraction.min()):.0f} % and "
        f"{air.air_dT_K.max():.1f} K"
    ) in note


def test_floor_note_quotes_the_cold_floor(page: str, sweep: pd.DataFrame) -> None:
    cold = _curve(sweep, -7.0, 45.0)
    expected = (
        f"(COP {cold['floor']:.2f} at the floor, which delivers {cold['q_min'] / 1000:.2f} kW, "
        f"{cold['floor_of_nameplate_pct']:.0f} % of the nameplate)"
    )
    note = _flat(_section(page, "Below the speed floor the model delivers", "Panel (b)"))
    assert expected in note, f"the speed-floor note should read {expected!r}. {HINT}."


def test_panel_b_table_quotes_the_trajectory(page: str, trajectory: pd.DataFrame, band: pd.DataFrame) -> None:
    table = _section(page, "Panel (b) — the certification trajectory", "The model sits between")
    for point, row in trajectory.iterrows():
        cells = _list_table_row(table, str(point))
        k = band.loc[point]
        for expected in (
            f"{row.t_outdoor_C:+.0f} °C / {row.lwt_C:.0f} °C",
            f"{row.q_required_W / 1000:.2f} kW",
            f"{row.q_delivered_W / 1000:.2f} kW",
            f"- {row.cop:.2f} -",
            f"{k.cop_median:.2f} ({k.cop_p10:.2f}–{k.cop_p90:.2f})",
        ):
            assert expected in cells + " -", f"point {point}: row says {cells!r}, expected {expected!r}. {HINT}."


def test_keymark_table_quotes_the_certified_band(page: str, band: pd.DataFrame) -> None:
    table = _section(page, "What the certified data actually shows", "Declared COP rises")
    for point, k in band.iterrows():
        cells = _list_table_row(table, str(point))
        for value in (k.plr, k.cop_p10, k.cop_p25, k.cop_median, k.cop_p75):
            assert f"{value:.2f}" in cells, f"Keymark row {point}: {cells!r} lacks {value:.2f}"


def test_delivered_callout_quotes_the_trajectory(page: str, trajectory: pd.DataFrame) -> None:
    text = _flat(page)
    d, b = trajectory.loc["D"], trajectory.loc["B"]
    assert f"{d.q_delivered_W / 1000:.2f} kW where {d.q_required_W / 1000:.2f} kW is required" in text, HINT
    assert f"At B the {b.q_delivered_W / 1000:.2f} kW is the mean over frosting and defrost" in text, HINT


def test_trajectory_claims_hold(page: str, trajectory: pd.DataFrame, band: pd.DataFrame) -> None:
    """Where in the certified band each point of the R32 9 kW trajectory sits, as the page states it.

    'rises monotonically A -> D and sits inside the certified band at all four points', and the sentence
    under the table. With the semi-empirical model of before #217 C was just under the 10th percentile;
    if a point moves to another band position or leaves the band, this fails.
    """
    cop = trajectory.cop
    assert list(cop.index) == ["A", "B", "C", "D"]
    assert cop.is_monotonic_increasing, cop.to_dict()
    for point in "ABCD":
        assert band.at[point, "cop_p10"] <= cop[point] <= band.at[point, "cop_p90"], point
    text = _flat(page)
    assert "rises monotonically A → D and sits inside the certified band at all four points" in text
    assert band.at["A", "cop_p25"] < cop["A"] < band.at["A", "cop_median"]
    for point in "BCD":
        assert band.at[point, "cop_p10"] < cop[point] < band.at[point, "cop_p25"], point
    assert "between the 25th percentile and the median at A and between the 10th and the 25th at B, C and D" in text
    # C and D deliver more than the point asks for; A and B modulate to it.
    assert not trajectory.loc[["A", "B"], "clamped"].any()
    assert trajectory.loc[["C", "D"], "clamped"].all()


def test_trajectory_check_counts_quote_the_study(page: str) -> None:
    """'the present defaults pass that shape check in 30 and the level check ... in 30', as the committed verdict has it."""
    verdict = pd.read_csv(REPO_ROOT / "validation" / "data" / "en14825_trend_verdict.csv")
    defaults = verdict[verdict.variant == "defaults"]
    text = _flat(page)
    assert "counts the number of cases whose COP(D)/COP(A) lies inside the certified population's p10–p90" in text
    assert f"runs this trajectory for {len(defaults)} cases" in text
    assert (
        f"On the study's {len(defaults)} cases the present defaults pass that shape check in "
        f"{int(defaults.C3_gradient.sum())} and the level check (every point inside its p10–p90) in "
        f"{int(defaults.C2_level.sum())}"
    ) in text
    # The shape check of the page is the study's D/A criterion, and the level check every point inside.
    assert (defaults.C2_level == (defaults.points_inside == 4)).all()


# ---------------------------------------------------------------------------
# CSV -> code
# ---------------------------------------------------------------------------
def _mismatches(fresh: pd.DataFrame, committed: pd.DataFrame, label: str) -> list[str]:
    fresh = pd.read_csv(io.StringIO(fresh.to_csv(index=False)), float_precision="round_trip")
    assert list(fresh.columns) == list(committed.columns), f"{label}: columns changed. {HINT}."
    assert len(fresh) == len(committed), f"{label}: {len(fresh)} rows now, {len(committed)} committed. {HINT}."
    out = []
    for i in range(len(fresh)):
        for column in fresh.columns:
            new, old = fresh.at[i, column], committed.at[i, column]
            if isinstance(new, float) and isinstance(old, float):
                if (math.isnan(new) and math.isnan(old)) or new == pytest.approx(old, rel=1e-6, abs=1e-9):
                    continue
            elif new == old:
                continue
            out.append(f"{label} row {i} {column}: committed {old!r}, now {new!r}")
    return out


@pytest.fixture(scope="module")
def figure():
    import scripts.validation.part_load_figure as figure

    return figure


def test_committed_csvs_are_what_the_model_gives(figure) -> None:
    mismatches = _mismatches(
        pd.DataFrame(figure.sweep_fixed_temperatures()),
        pd.read_csv(DATA / "sweep.csv", float_precision="round_trip"),
        "sweep.csv",
    )
    mismatches += _mismatches(
        pd.DataFrame(figure.trajectory_en14825()),
        pd.read_csv(DATA / "trajectory.csv", float_precision="round_trip"),
        "trajectory.csv",
    )
    mismatches += _mismatches(
        pd.DataFrame(figure.sweep_entering_water()),
        pd.read_csv(DATA / "entering_water.csv", float_precision="round_trip"),
        "entering_water.csv",
    )
    assert not mismatches, f"validation/data/part_load_figure: {HINT}.\n  " + "\n  ".join(mismatches[:15])


def test_the_cold_rise_is_the_defrost_adjustment(
    page: str, figure, sweep: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'that easing is most of the rise below the nameplate duty. Without it (defrost=False) the same curve reads
    COP 2.64 at the nameplate duty (76 rev/s), 2.68 at 75 % of it and 2.59 at the speed floor (21 % of the
    nameplate): within 2 % of its nameplate-duty value all the way down.' and the floor point it names (15 rev/s,
    its pressure ratio and efficiencies)."""
    frosted = _curve(sweep, -7.0, 45.0)
    monkeypatch.setattr(figure, "MODEL_SETTINGS", {**figure.MODEL_SETTINGS, "defrost": False})
    model = figure._model()
    # The sub-floor request is half the floor's heat: any request below the floor resolves to the floor point.
    requests = (figure.NOMINAL_W, 0.75 * figure.NOMINAL_W, 0.5 * frosted["q_min"])
    rows = pd.DataFrame([figure._sweep_row(model, -7.0, 45.0, q, frosted["q_max"], nameplate=False) for q in requests])
    assert (rows.failure_reason == "none").all()
    assert rows.at_floor.iloc[-1]
    cop = rows.cop.to_numpy()
    text = _flat(page)
    assert (
        f"the same curve reads COP {cop[0]:.2f} at the nameplate duty ({rows.rps.iloc[0]:.0f} rev/s), {cop[1]:.2f} at "
        f"75 % of it and {cop[2]:.2f} at the speed floor ({frosted['floor_of_nameplate_pct']:.0f} % of the "
        "nameplate): within 2 % of its nameplate-duty value all the way down"
    ) in text, f"frost-free -7/45 °C: {cop}. {HINT}."
    # "within 2 % ... all the way down", over the page's sweep from the nameplate duty to the floor.
    curve = pd.DataFrame(figure.sweep_fixed_temperatures(conditions=((-7.0, 45.0),)))
    assert (curve.failure_reason == "none").all()
    at_nameplate = float(curve[curve.is_nameplate].cop.iloc[0])
    below = curve[curve.q_request_W <= figure.NOMINAL_W]
    assert (below.cop / at_nameplate - 1.0).abs().max() < 0.02, below.cop.to_numpy()
    # The frosted curve's rise below the nameplate duty is the adjustment easing.
    assert frosted["at_75"] / frosted["at_nameplate"] - 1.0 > 0.02 + (cop[1] / cop[0] - 1.0)
    assert frosted["gain_pct"] > 10.0 + 100.0 * (cop[2] / cop[0] - 1.0)
    # The floor point: below the maps' data in speed, its efficiencies as the page states them.
    monkeypatch.setattr(figure, "MODEL_SETTINGS", {**figure.MODEL_SETTINGS, "defrost": True})
    model = figure._model()
    from tmhp.compressor_efficiency import DOMAIN

    results = [
        figure.solve_at_leaving_water(model, -7.0, 45.0, q, figure.WATER_FLOW_KG_S)[0]
        for q in (figure.NOMINAL_W, 0.5 * frosted["q_min"])
    ]
    full, low = results
    speed_floor = DOMAIN.rps_range[0]
    assert low["cmp_rpm [rpm]"] / 60.0 == pytest.approx(model.rps_min) and model.rps_min < speed_floor
    assert low["efficiency_extrapolated"] and not full["efficiency_extrapolated"]

    def overall(result: dict) -> float:
        return result["eta_cmp_isen [-]"] * result["eta_cmp [-]"]

    assert overall(low) < overall(full) and low["eta_cmp [-]"] < full["eta_cmp [-]"]
    assert (
        f"runs at {model.rps_min:.0f} rev/s and a pressure ratio of {low['pr_cmp [-]']:.1f}, with an overall "
        f"efficiency (``eta_cmp_isen`` times ``eta_cmp``) of {overall(low):.3f} against {overall(full):.3f} at the "
        f"nameplate duty. Its isentropic efficiency is {low['eta_cmp_isen [-]']:.3f} against "
        f"{full['eta_cmp_isen [-]']:.3f}; what falls is ``eta_cmp``, the share of the electrical input that reaches "
        f"the gas, {low['eta_cmp [-]']:.3f} against {full['eta_cmp [-]']:.3f}"
    ) in text
    assert f"The maps' data start at {speed_floor:.0f} rev/s" in text


def test_max_capacity_paragraph_describes_the_model(page: str, figure, sweep: pd.DataFrame) -> None:
    """'A request above what the machine can deliver is answered at the top speed ... capacity_clamped == "max" ...
    Q_max [W] ... Q_request [W]; a larger request gives the same point. Below the ceiling Q_max [W] is NaN ...
    three times the nameplate ... AirSourceHeatPump has the same ceiling as a method'."""
    import warnings

    from tmhp import AirSourceHeatPump, OperatingLimitWarning

    w35 = _curve(sweep, 7.0, 35.0)
    model = figure._model()
    assert figure.MAX_PROBE_W == 3.0 * figure.NOMINAL_W
    assert figure.maximum_capacity(model, 7.0, 35.0) == pytest.approx(w35["q_max"], rel=1e-9)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OperatingLimitWarning)
        for request in (2.0 * figure.NOMINAL_W, 3.0 * figure.NOMINAL_W):
            result, _ = figure.solve_at_leaving_water(model, 7.0, 35.0, request, figure.WATER_FLOW_KG_S)
            assert result["capacity_clamped"] == "max" and result["capacity_binding"] == "compressor"
            assert result["cmp_rpm [rpm]"] / 60.0 == pytest.approx(model.rps_max)
            assert result["Q_max [W]"] == pytest.approx(w35["q_max"], rel=1e-4)  # a larger request, the same point
            assert result["Q_request [W]"] == pytest.approx(request)
        below, _ = figure.solve_at_leaving_water(model, 7.0, 35.0, figure.NOMINAL_W, figure.WATER_FLOW_KG_S)
    assert below["capacity_clamped"] is None and math.isnan(below["Q_max [W]"])
    # The air-to-air method returns the ceiling the sweep's maximum column holds.
    from validation.analysis import ashp_trend as at

    ashp = pd.read_csv(ASHP_SWEEP)
    row = ashp[
        (ashp.duty == "heating") & (ashp.capacity_kW == 3.5) & (ashp.refrigerant == "R32") & (ashp.t_outdoor_C == 7.0)
    ]
    unit = AirSourceHeatPump(hp_capacity=3500.0, ref="R32", **at.SWEEP_SETTINGS)
    assert unit.max_capacity(7.0, 20.0, mode="heating")["Q_max [W]"] == pytest.approx(row.q_max_W.iloc[0], rel=1e-9)
    text = _flat(page)
    for phrase in (
        'capacity_clamped == "max"',
        'capacity_binding == "compressor"',
        "that heat as ``Q_max [W]``",
        "and the request as ``Q_request [W]``",
        "a larger request gives the same point",
        "the figure uses three times the nameplate",
        'max_capacity(T0, T_a_room, mode=...)["Q_max [W]"]',
    ):
        assert phrase in text, phrase


def test_panel_b_frost_paragraph_is_what_the_model_returns(
    page: str, figure, trajectory: pd.DataFrame, band: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trajectory without the frost adjustment and with it in full at every load, the frost-free duty at B,
    the load ratios at A and B and the share of the full correction each keeps."""
    from tmhp.defrost import DefrostModel

    def run(defrost) -> pd.DataFrame:
        monkeypatch.setattr(figure, "MODEL_SETTINGS", {**figure.MODEL_SETTINGS, "defrost": defrost})
        rows = pd.DataFrame(figure.trajectory_en14825()).set_index("point")
        assert (rows.failure_reason == "none").all()
        return rows

    without, in_full = run(False).cop, run(DefrostModel(load_weighted=False)).cop
    shipped = trajectory.cop
    text = _flat(page)
    assert band.at["A", "cop_median"] < without["A"] < band.at["A", "cop_p90"]
    assert (
        f"Without the frost and defrost adjustment the same trajectory reads {without['A']:.2f} at A, just under "
        f"the 90th percentile ({band.at['A', 'cop_p90']:.2f}), and {without['B']:.2f} at B"
    ) in text
    assert in_full["B"] < band.at["B", "cop_p10"]
    assert f"it reads {in_full['A']:.2f} at A and {in_full['B']:.2f} at B, below the 10th percentile at B" in text
    # Frost acts on A and B only (C and D are above the 5 degC limit of the adjustment).
    assert (without[["C", "D"]] - shipped[["C", "D"]]).abs().max() < 1e-9
    monkeypatch.setattr(figure, "MODEL_SETTINGS", {**figure.MODEL_SETTINGS, "defrost": True})
    model = figure._model()
    ratio = {}
    for label, t_outdoor, lwt, load_ratio in figure.EN14825_LOW[:2]:
        result, _ = figure.solve_at_leaving_water(
            model, t_outdoor, lwt, figure.DESIGN_LOAD_W * load_ratio, figure.WATER_FLOW_KG_S
        )
        frost_free = result["Q_ref_tank_frost_free [W]"]
        ratio[label] = (frost_free, DefrostModel.load_ratio(frost_free, model.hp_capacity, t_outdoor))
    assert (
        f"the unit at B delivers {ratio['B'][0] / 1000:.2f} kW frost-free, {ratio['B'][1]:.2f} of what the "
        "adjustment's coil-temperature correlation is written for"
    ) in text
    assert f"at A the unit is at {ratio['A'][1]:.2f} of that load" in text
    assert f"machine that delivers {ratio['B'][0] / 1000:.2f} kW frost-free" in text
    kept = {p: (without[p] - shipped[p]) / (without[p] - in_full[p]) for p in "AB"}
    assert kept["A"] > 0.9 and 0.25 < kept["B"] < 0.3, kept
    assert "keeps nearly the whole correction, at B a little over a quarter of it" in text


# ---------------------------------------------------------------------------
# air-to-air section: prose -> CSV (the rerun against the model is a sample, in test_study_results_current)
# ---------------------------------------------------------------------------
A2A_CONDITIONS = (
    ("heating", -7.0, "Heating, −7 °C"),
    ("heating", 2.0, "Heating, +2 °C"),
    ("heating", 7.0, "Heating, +7 °C"),
    ("cooling", 35.0, "Cooling, 35 °C"),
    ("cooling", 30.0, "Cooling, 30 °C"),
    ("cooling", 25.0, "Cooling, 25 °C"),
)
A2A_NAMEPLATE_W = 3500.0


@pytest.fixture(scope="module")
def ashp() -> pd.DataFrame:
    sweep = pd.read_csv(ASHP_SWEEP, float_precision="round_trip")
    return sweep[(sweep.capacity_kW == 3.5) & (sweep.refrigerant == "R32")]


def _a2a_curve(ashp: pd.DataFrame, duty: str, t_outdoor: float) -> dict:
    rows = ashp[(ashp.duty == duty) & (ashp.t_outdoor_C == t_outdoor)].sort_values(
        ["delivered_fraction_of_max", "plr_nameplate"]
    )
    q_max = float(rows.q_max_W.iloc[0])
    assert (rows.q_max_W == q_max).all()
    low, top = rows.iloc[0], rows.iloc[-1]
    assert top.delivered_fraction_of_max == pytest.approx(1.0, rel=1e-3) and top.capacity_clamped == "max"
    assert low.capacity_clamped == "min" and low.rps == pytest.approx(15.0)
    # The ceiling is the top speed's unless another limit binds first, and then it is at a lower speed.
    assert (top.capacity_binding == "compressor") == (top.rps == pytest.approx(120.0)), (top.capacity_binding, top.rps)
    nameplate = rows[rows.plr_nameplate == 1.0].iloc[0]
    distinct = rows.drop_duplicates("delivered_fraction_of_max")
    inside = rows[~rows.efficiency_extrapolated]
    return {
        "q_min": float(low.q_delivered_W),
        "q_max": q_max,
        "nameplate_of_max_pct": A2A_NAMEPLATE_W / q_max * 100.0,
        "floor": float(low.cop_sys),
        "top": float(top.cop_sys),
        "at_nameplate": float(nameplate.cop_sys),
        "top_rps": float(top.rps),
        "top_binding": str(top.capacity_binding),
        "rise_pct": (float(low.cop_sys) / float(top.cop_sys) - 1.0) * 100.0,
        "rises_every_step": bool((distinct.cop_sys.diff().iloc[1:] < 0).all()),  # COP up as the heat falls
        "inside_min_pct": float(inside.delivered_fraction_of_max.min()) * 100.0,
        "inside_max_pct": float(inside.delivered_fraction_of_max.max()) * 100.0,
    }


def _span(values: list[float]) -> str:
    """'28–32' for a set of rounded percentages, '45' when they agree."""
    lo, hi = round(min(values)), round(max(values))
    return f"{lo}–{hi}" if lo != hi else f"{lo}"


def test_air_to_air_table_quotes_the_sweep(page: str, ashp: pd.DataFrame) -> None:
    table = _section(page, "3.5 kW as a share of the maximum", "All six curves rise")
    for duty, t_outdoor, label in A2A_CONDITIONS:
        c = _a2a_curve(ashp, duty, t_outdoor)
        expected = [
            f"{c['q_min'] / 1000:.2f} kW",
            f"{c['q_max'] / 1000:.2f} kW",
            f"{c['nameplate_of_max_pct']:.0f} %",
            f"{c['floor']:.2f}",
            f"{c['at_nameplate']:.2f}",
            f"{c['top']:.2f}",
        ]
        cells = _cells(table, label)
        assert cells == expected, f"air-to-air {label}: {cells!r}, expected {expected!r}. {HINT}."


def test_air_to_air_prose_quotes_the_sweep(page: str, ashp: pd.DataFrame, sweep: pd.DataFrame) -> None:
    """'All six curves rise as the load falls, at every step down to the minimum capacity', the rise from the
    maximum to the minimum against the air-to-water curves, two curves' three COPs and the map-data coverage."""
    c = {(duty, t): _a2a_curve(ashp, duty, t) for duty, t, _ in A2A_CONDITIONS}
    text = _flat(page)
    assert all(curve["rises_every_step"] for curve in c.values())
    assert "All six curves rise as the load falls, at every step down to the minimum capacity" in text
    heating = [v["rise_pct"] for (d, _), v in c.items() if d == "heating"]
    cooling = [v["rise_pct"] for (d, _), v in c.items() if d == "cooling"]
    water = [_curve(sweep, t, lwt)["top_to_floor_pct"] for t, lwt in CONDITIONS]
    expected = (
        f"rises by {min(heating):.0f} to {max(heating):.0f} % in heating and by {min(cooling):.0f} to "
        f"{max(cooling):.0f} % in cooling, against {min(water):.0f} to {max(water):.0f} % for the air-to-water curves"
    )
    assert expected in text, f"{expected!r}. {HINT}."
    h7, c35 = c["heating", 7.0], c["cooling", 35.0]
    expected = (
        f"heating at +7 °C goes from COP {h7['top']:.2f} at the maximum to {h7['at_nameplate']:.2f} at the nameplate "
        f"and {h7['floor']:.2f} at the minimum, and cooling at 35 °C from {c35['top']:.2f} to "
        f"{c35['at_nameplate']:.2f} and {c35['floor']:.2f}"
    )
    assert expected in text, f"{expected!r}. {HINT}."
    span = {
        duty: (
            _span([v["inside_min_pct"] for (d, _), v in c.items() if d == duty]),
            _span([v["inside_max_pct"] for (d, _), v in c.items() if d == duty]),
        )
        for duty in ("heating", "cooling")
    }
    expected = (
        f"the flag is clear from {span['heating'][0]} % to {span['heating'][1]} % of the maximum in heating and from "
        f"{span['cooling'][0]} % to {span['cooling'][1]} % in cooling"
    )
    assert expected in text, f"{expected!r}. {HINT}."
    assert min(v["inside_min_pct"] for (d, _), v in c.items() if d == "cooling") > 44.0
    # "The maximum is the top speed's, 120 rev/s, except in heating at +7 °C: ... at 115 rev/s".
    below_top = {key: v for key, v in c.items() if v["top_binding"] != "compressor"}
    assert list(below_top) == [("heating", 7.0)], below_top
    h7 = below_top["heating", 7.0]
    assert (
        "The maximum is the top speed's, 120 rev/s, except in heating at +7 °C: there a coil reaches its largest "
        f'airflow first and the ceiling is at {h7["top_rps"]:.0f} rev/s (``capacity_binding == "{h7["top_binding"]}"``)'
    ) in text
    air = ashp.q_air_delivered_W / ashp.q_delivered_W - 1.0
    assert f"by at most {air.abs().max() * 100:.1f} % over the six curves" in text


def test_extrapolation_split_draws_dashed_where_flagged() -> None:
    """The figure helper: solid on the map data, dashed where either end of a segment is flagged, one legend line."""
    import sys

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sys.path.insert(0, str(REPO_ROOT / "scripts" / "visualization"))
    from _dmpl_common import plot_extrapolation_split

    fig, ax = plt.subplots()
    plot_extrapolation_split(ax, [0, 1, 2, 3, 4], [0, 1, 2, 3, 4], [True, False, False, False, True], label="s")
    lines = ax.get_lines()
    legend, runs = lines[0], lines[1:]
    assert legend.get_label() == "s" and len(legend.get_xdata()) == 0 and legend.get_linestyle() == "-"
    assert [list(line.get_xdata()) for line in runs] == [[0, 1], [1, 2, 3], [3, 4]]
    assert [line.get_linestyle() for line in runs] == ["--", "-", "--"]
    with pytest.raises(ValueError):
        plot_extrapolation_split(ax, [0, 1], [0, 1], [True])
    n = len(ax.get_lines())
    plot_extrapolation_split(ax, [], [], [], label="empty")
    assert len(ax.get_lines()) == n
    plt.close(fig)
