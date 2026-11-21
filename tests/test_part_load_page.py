"""``docs/source/validation/part-load.rst`` quotes the numbers the model produces.

The page's figures come from ``scripts/validation/part_load_figure.py``, which
writes what it plots to ``validation/data/part_load_figure/`` with a provenance
record. Two links are checked here, the same chain the parity pages have:

* prose -> CSV: every figure the page quotes is recomputed from the committed
  CSVs and looked for in the sentence or table row that states it;
* CSV -> code: the committed CSVs are rerun in full (the fixed-temperature
  sweep, 144 points from the speed floor to the top speed, and the four EN 14825
  points) and must match at ``rel=1e-6``.

The page's horizontal axis in panel (a) is the heat delivered over the maximum
capacity at the same temperatures, and its tables quote the capacity range, the
peak on both the maximum and the nameplate axis, and the fall from the peak at
both ends. The air-to-air section quotes ``validation/data/ashp_part_load_sweep.csv``
the same way; its rerun against the model is a sample, in
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


def _curve(sweep: pd.DataFrame, t_outdoor: float, lwt: float) -> dict[str, float]:
    """One sweep curve: its capacity range, the peak on both axes, and the fall from the peak at both ends."""
    rows = sweep[(sweep.t_outdoor_C == t_outdoor) & (sweep.lwt_C == lwt)]
    assert (rows.failure_reason == "none").all(), f"{t_outdoor}/{lwt}: a sweep point did not converge"
    q_max = float(rows.q_max_W.iloc[0])
    assert (rows.q_max_W == q_max).all()
    nameplate = rows[rows.is_nameplate]
    assert len(nameplate) == 1 and nameplate.plr.iloc[0] == 1.0 and nameplate.q_request_W.iloc[0] == NAMEPLATE_W
    nameplate = nameplate.iloc[0]
    # Every request below the speed floor resolves to one operating point.
    floor = rows[rows.at_floor]
    assert len(floor) >= 2 and floor.q_delivered_W.max() == pytest.approx(floor.q_delivered_W.min(), rel=1e-5)
    low = floor.loc[floor.delivered_fraction_of_max.idxmax()]
    top = rows.loc[rows.delivered_fraction_of_max.idxmax()]
    assert top.delivered_fraction_of_max == pytest.approx(1.0, rel=1e-3) and top.rps == pytest.approx(120.0)
    peak = rows.loc[rows.cop.idxmax()]
    near_75 = rows.loc[(rows.plr - 0.75).abs().idxmin()]
    return {
        "q_max": q_max,
        "q_min": float(low.q_delivered_W),
        "nameplate_of_max_pct": NAMEPLATE_W / q_max * 100.0,
        "floor_of_nameplate_pct": float(low.q_delivered_W) / NAMEPLATE_W * 100.0,
        "at_nameplate": float(nameplate.cop),
        "at_75": float(near_75.cop),
        "plr_75": float(near_75.plr),
        "floor": float(low.cop),
        "top": float(top.cop),
        "peak": float(peak.cop),
        "peak_of_max_pct": float(peak.delivered_fraction_of_max) * 100.0,
        "peak_of_nameplate_pct": float(peak.q_delivered_W) / NAMEPLATE_W * 100.0,
        "peak_rps": float(peak.rps),
        "peak_at_floor": bool(peak.at_floor),
        "gain_pct": (float(low.cop) / float(nameplate.cop) - 1.0) * 100.0,  # nameplate duty -> speed floor
        "to_top_pct": (float(top.cop) / float(peak.cop) - 1.0) * 100.0,
        "to_floor_pct": (float(low.cop) / float(peak.cop) - 1.0) * 100.0,
        "nameplate_vs_peak_pct": (float(nameplate.cop) / float(peak.cop) - 1.0) * 100.0,
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
    """The capacity-range table and the peak table, row by row."""
    ranges = _section(page, "these ranges:", "Frost and defrost are on")
    peaks = _section(page, "Frost and defrost are on", "Each curve rises from the speed floor")
    for t_outdoor, lwt in CONDITIONS:
        c = _curve(sweep, t_outdoor, lwt)
        label = _condition(t_outdoor, lwt)
        cells = _list_table_row(ranges, label)
        for expected in (
            f"{c['q_min'] / 1000:.2f} kW",
            f"{c['q_max'] / 1000:.2f} kW",
            f"{c['nameplate_of_max_pct']:.0f} %",
        ):
            assert expected in cells, f"capacity range {label}: {cells!r} lacks {expected!r}. {HINT}."
        cells = _list_table_row(peaks, label)
        for expected in (
            f"{c['peak']:.2f}",
            f"{c['peak_of_max_pct']:.0f} % of maximum, {c['peak_of_nameplate_pct']:.0f} % of nameplate, "
            f"{c['peak_rps']:.0f} rev/s",
            f"{c['top']:.2f} ({c['to_top_pct']:+.1f} %)",
            f"{c['floor']:.2f} ({c['to_floor_pct']:+.1f} %)",
            f"{c['at_nameplate']:.2f}",
        ):
            assert expected in _flat(cells), f"peak table {label}: {cells!r} lacks {expected!r}. {HINT}."
    text = _flat(page)
    w45, cold = _curve(sweep, 7.0, 45.0), _curve(sweep, -7.0, 45.0)
    assert (
        f"At -7 °C the maximum is {cold['q_max'] / 1000:.2f} kW against {w45['q_max'] / 1000:.2f} kW at +7 °C "
        "and the same water temperature"
    ) in text, HINT


def test_panel_a_peak_prose_quotes_the_sweep(page: str, sweep: pd.DataFrame) -> None:
    w35, w45, cold = (_curve(sweep, t, lwt) for t, lwt in CONDITIONS)
    text = _flat(page)
    # "Each curve rises from the speed floor to a peak and falls again": an interior peak on every curve.
    for curve in (w35, w45, cold):
        assert not curve["peak_at_floor"] and curve["peak"] > curve["floor"] and curve["peak"] > curve["top"]
    # "a little under half of the maximum at 7/35 and -7/45, a little over half at 7/45".
    assert 40 < w35["peak_of_max_pct"] < 50 and 40 < cold["peak_of_max_pct"] < 50 and 50 < w45["peak_of_max_pct"] < 60
    assert "a little under half of the maximum at 7/35 °C and -7/45 °C and a little over half at 7/45 °C" in text
    expected = (
        f"{w35['peak_of_nameplate_pct']:.0f} %, {w45['peak_of_nameplate_pct']:.0f} % and "
        f"{cold['peak_of_nameplate_pct']:.0f} % — because the nameplate"
    )
    assert expected in text, f"{expected!r}. {HINT}."
    expected = (
        f"The COP at the 9 kW duty is {-w35['nameplate_vs_peak_pct']:.1f} %, {-w45['nameplate_vs_peak_pct']:.1f} % "
        f"and {-cold['nameplate_vs_peak_pct']:.1f} % below the peak"
    )
    assert expected in text, f"{expected!r}. {HINT}."
    expected = (
        f"from the peak to the top speed COP falls by {-w35['to_top_pct']:.1f} %, {-w45['to_top_pct']:.1f} % and "
        f"{-cold['to_top_pct']:.1f} %, and from the peak to the speed floor by {-w35['to_floor_pct']:.1f} %, "
        f"{-w45['to_floor_pct']:.1f} % and {-cold['to_floor_pct']:.1f} %"
    )
    assert expected in text, f"{expected!r}. {HINT}."


def test_panel_a_nameplate_axis_sentence(page: str, sweep: pd.DataFrame) -> None:
    """The nameplate-axis numbers the page used to give, recomputed from the nameplate and floor rows."""
    w35, w45, cold = (_curve(sweep, t, lwt) for t, lwt in CONDITIONS)
    assert w35["gain_pct"] < 0 and w45["gain_pct"] < 0 and cold["gain_pct"] < 0
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


def test_denominator_paragraph_quotes_the_nameplate_share(page: str, sweep: pd.DataFrame) -> None:
    w35, w45, cold = (_curve(sweep, t, lwt) for t, lwt in CONDITIONS)
    expected = (
        f"the nameplate is {w35['nameplate_of_max_pct']:.0f} %, {w45['nameplate_of_max_pct']:.0f} % and "
        f"{cold['nameplate_of_max_pct']:.0f} % of it at the three conditions of panel (a)"
    )
    assert expected in _flat(page), f"{expected!r}. {HINT}."


def test_entering_water_paragraph_quotes_its_sweep(page: str, sweep: pd.DataFrame) -> None:
    """The same machine with the entering water held: its peak and floor against the leaving-water curve."""
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
    assert not peak.at_floor and floor.cop < nameplate.cop
    expected = (
        f"The peak is then COP {peak.cop:.2f} at {peak.q_delivered_W / NAMEPLATE_W * 100:.0f} % of the nameplate "
        f"({peak.delivered_fraction_of_max * 100:.0f} % of the maximum, {peak.rps:.0f} rev/s), "
        f"{(peak.cop / nameplate.cop - 1) * 100:.1f} % above the 9 kW duty against "
        f"{(held['peak'] / held['at_nameplate'] - 1) * 100:.1f} % with the leaving water held, and the speed floor is "
        f"{(1 - floor.cop / nameplate.cop) * 100:.1f} % below the 9 kW duty against {_bare(held['gain_pct'])} below"
    )
    assert expected in _flat(page), f"part-load.rst should read {expected!r}. {HINT}."


def test_panel_a_decomposition_quotes_the_study(page: str) -> None:
    """'with the three efficiencies frozen ... gains 22.0 % ... with the shipped efficiencies loses 18.3 %; the
    air-to-air model, at pressure ratios of 2.4 to 2.8 at full load, keeps +33 to +40 %'."""
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
    assert frozen > 0 > shipped
    assert f"rated values the same model gains {frozen:.1f} % between full load and the speed floor" in text
    assert f"with the shipped efficiencies loses {-shipped:.1f} %" in text
    a2a = gain("current", "ASHP")
    gains, prs = [g for g, _ in a2a], [p for _, p in a2a]
    assert (
        f"at pressure ratios of {min(prs):.1f} to {max(prs):.1f} at full load, keeps "
        f"+{min(gains):.0f} to +{max(gains):.0f} %"
    ) in text


def test_floor_note_quotes_the_cold_floor(page: str, sweep: pd.DataFrame) -> None:
    cold = _curve(sweep, -7.0, 45.0)
    expected = f"(COP {cold['floor']:.2f} at the floor, reached at {cold['floor_of_nameplate_pct']:.0f} % load)"
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

    'rises monotonically A -> D and sits inside the certified band at A, B and D; at C it is 0.1 % below
    the 10th percentile', and the sentence under the table. Until the catalogue default inputs (issue #86)
    all four points were inside the band; in the previous release A was above the 90th percentile. If a
    point moves to another band position, or C comes back inside or goes further out, this fails.
    """
    cop = trajectory.cop
    assert list(cop.index) == ["A", "B", "C", "D"]
    assert cop.is_monotonic_increasing, cop.to_dict()
    for point in ("A", "B", "D"):
        assert band.at[point, "cop_p10"] <= cop[point] <= band.at[point, "cop_p90"], point
    assert cop["C"] < band.at["C", "cop_p10"]
    below = 100.0 * (1.0 - cop["C"] / band.at["C", "cop_p10"])
    text = _flat(page)
    assert (
        f"sits inside the certified band at A, B and D; at C it is {below:.1f} % below the 10th percentile "
        f"({cop['C']:.2f} against {band.at['C', 'cop_p10']:.2f})"
    ) in text
    assert band.at["A", "cop_p25"] < cop["A"] < band.at["A", "cop_median"]
    assert band.at["B", "cop_p10"] < cop["B"] < band.at["B", "cop_p25"]
    assert band.at["D", "cop_median"] < cop["D"] < band.at["D", "cop_p75"]
    assert (
        "between the 25th percentile and the median at A, between the 10th and the 25th at B, "
        "just under the 10th at C and between the median and the 75th at D"
    ) in text
    # C and D deliver more than the point asks for; A and B modulate to it.
    assert not trajectory.loc[["A", "B"], "clamped"].any()
    assert trajectory.loc[["C", "D"], "clamped"].all()


def test_trajectory_check_counts_quote_the_study(page: str) -> None:
    """'the present defaults pass that shape check in 30 and the level check ... in 9'; the previous release 18."""
    verdict = pd.read_csv(REPO_ROOT / "validation" / "data" / "en14825_trend_verdict.csv")
    defaults = verdict[verdict.variant == "defaults"]
    previous = verdict[verdict.variant == "quadratic-2026-10-06"]
    text = _flat(page)
    assert "the number of cases whose COP(D)/COP(A) lies inside" in text
    assert (
        f"On the study's {len(defaults)} cases the present defaults pass that shape check in "
        f"{int(defaults.C3_gradient.sum())} and the level check (every point inside its p10–p90) in "
        f"{int(defaults.C2_level.sum())}"
    ) in text
    assert f"The previous release passed the level check in {int(previous.C2_level.sum())} of {len(previous)}" in text


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
    """'that easing is the rise from the nameplate duty to 75 % of it. Without it (defrost=False) the same curve
    reads COP 2.75 at the nameplate duty (91 rev/s), 2.76 at 75 % of it and 1.81 at the speed floor (16 % of the
    nameplate): flat to 75 % of the nameplate and falling below it.' and the floor point it names (15 rev/s, its
    pressure ratio and isentropic efficiency)."""
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
        f"75 % of it and {cop[2]:.2f} at the speed floor ({frosted['floor_of_nameplate_pct']:.0f} % of the nameplate)"
    ) in text, f"frost-free -7/45 °C: {cop}. {HINT}."
    # "flat to 75 % of the nameplate and falling below it"; the frosted curve's rise to 75 % is the adjustment easing.
    assert abs(cop[1] / cop[0] - 1.0) < 0.01, cop
    assert cop[2] < 0.75 * cop[1], cop
    assert frosted["at_75"] / frosted["at_nameplate"] - 1.0 > 0.02 + (cop[1] / cop[0] - 1.0)
    # The floor point: below the rotary data in speed, its efficiency as the page states it.
    monkeypatch.setattr(figure, "MODEL_SETTINGS", {**figure.MODEL_SETTINGS, "defrost": True})
    model = figure._model()
    from tmhp.compressor_efficiency import COMPRESSOR_TYPES, SEMI_EMPIRICAL_TYPES

    results = [
        figure.solve_at_leaving_water(model, -7.0, 45.0, q, figure.WATER_FLOW_KG_S)[0]
        for q in (figure.NOMINAL_W, 0.5 * frosted["q_min"])
    ]
    full, low = results
    speed_floor = min(n for _, n in SEMI_EMPIRICAL_TYPES[COMPRESSOR_TYPES[0]]["hull_PR_N"])
    assert low["cmp_rpm [rpm]"] / 60.0 == pytest.approx(model.rps_min) and model.rps_min < speed_floor
    assert low["efficiency_extrapolated"] and not full["efficiency_extrapolated"]
    assert (
        f"runs at {model.rps_min:.0f} rev/s and a pressure ratio of {low['pr_cmp [-]']:.1f}, with isentropic "
        f"efficiency {low['eta_cmp_isen [-]']:.3f} against {full['eta_cmp_isen [-]']:.3f} at the nameplate duty"
    ) in text
    assert f"the rotary data end at {speed_floor:.0f} rev/s" in text


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


def _a2a_curve(ashp: pd.DataFrame, duty: str, t_outdoor: float) -> dict[str, float]:
    rows = ashp[(ashp.duty == duty) & (ashp.t_outdoor_C == t_outdoor)].sort_values(
        ["delivered_fraction_of_max", "plr_nameplate"]
    )
    q_max = float(rows.q_max_W.iloc[0])
    assert (rows.q_max_W == q_max).all()
    low, top = rows.iloc[0], rows.iloc[-1]
    assert top.delivered_fraction_of_max == pytest.approx(1.0, rel=1e-3) and top.capacity_clamped == "max"
    assert low.capacity_clamped == "min" and low.rps == pytest.approx(15.0)
    nameplate = rows[rows.plr_nameplate == 1.0].iloc[0]
    peak = rows.loc[rows.cop_sys.idxmax()]
    distinct = rows.drop_duplicates("delivered_fraction_of_max")
    return {
        "q_min": float(low.q_delivered_W),
        "q_max": q_max,
        "nameplate_of_max_pct": A2A_NAMEPLATE_W / q_max * 100.0,
        "floor": float(low.cop_sys),
        "peak": float(peak.cop_sys),
        "peak_of_max_pct": float(peak.delivered_fraction_of_max) * 100.0,
        "peak_rps": float(peak.rps),
        "top": float(top.cop_sys),
        "at_nameplate": float(nameplate.cop_sys),
        "peak_over_floor_pct": (float(peak.cop_sys) / float(low.cop_sys) - 1.0) * 100.0,
        "floor_below_peak_pct": (1.0 - float(low.cop_sys) / float(peak.cop_sys)) * 100.0,
        "rises_every_step": bool((distinct.cop_sys.diff().iloc[1:] <= 1e-9).all()),  # COP up as the heat falls
    }


def test_air_to_air_table_quotes_the_sweep(page: str, ashp: pd.DataFrame) -> None:
    table = _section(page, "3.5 kW as a share of the maximum", "Three of the six curves")
    for duty, t_outdoor, label in A2A_CONDITIONS:
        c = _a2a_curve(ashp, duty, t_outdoor)
        cells = _flat(_list_table_row(table, label))
        for expected in (
            f"{c['q_min'] / 1000:.2f} kW",
            f"{c['q_max'] / 1000:.2f} kW",
            f"{c['nameplate_of_max_pct']:.0f} %",
            f"- {c['floor']:.2f} -",
            f"{c['peak']:.2f} ({c['peak_of_max_pct']:.0f} %, {c['peak_rps']:.0f} rev/s)",
            f"- {c['top']:.2f}",
        ):
            assert expected in cells + " -", f"air-to-air {label}: {cells!r} lacks {expected!r}. {HINT}."


def test_air_to_air_prose_quotes_the_sweep(page: str, ashp: pd.DataFrame) -> None:
    """'Three of the six curves have no peak above the speed floor ... the other three peak above the floor,
    shallowly', the three numbers it gives for the shallow peaks, and the comparison with air-to-water."""
    c = {(duty, t): _a2a_curve(ashp, duty, t) for duty, t, _ in A2A_CONDITIONS}
    text = _flat(page)
    # No interior peak: heating +7 (flat end, 0.02 % above the floor value) and both warm cooling curves (every step).
    assert c["cooling", 35.0]["rises_every_step"] and c["cooling", 30.0]["rises_every_step"]
    flat_end = c["heating", 7.0]
    assert 0.0 < flat_end["peak_over_floor_pct"] < 0.05 and flat_end["peak_rps"] < 16.0
    assert (
        "Three of the six curves have no peak above the speed floor: heating at +7 °C and cooling at 35 °C and 30 °C "
        "rise as the load falls all the way to the minimum capacity (at +7 °C the highest COP is "
        f"{flat_end['peak_over_floor_pct']:.2f} % above the value at the floor"
    ) in text, HINT
    # The other three do peak above the floor.
    for key in (("heating", -7.0), ("heating", 2.0), ("cooling", 25.0)):
        assert not c[key]["rises_every_step"] and c[key]["peak_over_floor_pct"] > 1.0 and c[key]["peak_rps"] > 16.0
    expected = (
        f"the floor is {c['heating', -7.0]['floor_below_peak_pct']:.1f} % below the peak for heating at -7 °C, "
        f"{c['heating', 2.0]['floor_below_peak_pct']:.1f} % below at +2 °C and "
        f"{c['cooling', 25.0]['floor_below_peak_pct']:.1f} % below for cooling at 25 °C"
    )
    assert expected in text, f"{expected!r}. {HINT}."
    h7, c35 = c["heating", 7.0], c["cooling", 35.0]
    expected = (
        f"heating at +7 °C goes from COP {h7['top']:.2f} at the maximum to {h7['at_nameplate']:.2f} at the nameplate "
        f"and {h7['floor']:.2f} at the minimum, and cooling at 35 °C from {c35['top']:.2f} to "
        f"{c35['at_nameplate']:.2f} and {c35['floor']:.2f}"
    )
    assert expected in text, f"{expected!r}. {HINT}."
    # "all three of which fall by 18 to 27 % between the peak and the floor" (the air-to-water curves).
    # (the sweep file of panel (a) is read here directly)
    water = pd.read_csv(DATA / "sweep.csv", float_precision="round_trip")
    falls = [-_curve(water, t, lwt)["to_floor_pct"] for t, lwt in CONDITIONS]
    assert f"all three of which fall by {min(falls):.0f} to {max(falls):.0f} % between the peak and the floor" in text
