"""``docs/source/validation/part-load.rst`` quotes the numbers the model produces.

The page's figures come from ``scripts/validation/part_load_figure.py``, which
writes what it plots to ``validation/data/part_load_figure/`` with a provenance
record. Two links are checked here, the same chain the parity pages have:

* prose -> CSV: every figure the page quotes is recomputed from the committed
  CSVs and looked for in the sentence or table row that states it;
* CSV -> code: the committed CSVs are rerun in full (78 sweep points and the
  four EN 14825 points, a few seconds) and must match at ``rel=1e-6``.

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
    """Full-load COP, COP nearest 75 % load, the speed-floor edge and its COP, the gain to it [%], the peak."""
    rows = sweep[(sweep.t_outdoor_C == t_outdoor) & (sweep.lwt_C == lwt)].sort_values("plr", ascending=False)
    assert (rows.failure_reason == "none").all(), f"{t_outdoor}/{lwt}: a sweep point did not converge"
    full = rows.iloc[0]
    assert full.plr == 1.0
    floor = rows[rows.at_floor]
    edge = floor.loc[floor.plr.idxmax()]
    near_75 = rows.loc[(rows.plr - 0.75).abs().idxmin()]
    peak = rows.loc[rows.cop.idxmax()]
    return {
        "full": float(full.cop),
        "full_rps": float(full.rps),
        "at_75": float(near_75.cop),
        "edge_pct": float(edge.plr) * 100.0,
        "floor": float(edge.cop),
        "gain_pct": (float(edge.cop) / float(full.cop) - 1.0) * 100.0,
        "peak": float(peak.cop),
        "peak_pct": float(peak.plr) * 100.0,
        "peak_rps": float(peak.rps),
        "peak_at_floor": bool(peak.at_floor),
    }


def _change(gain_pct: float) -> str:
    """'gains 4.4 %' or 'loses 16.7 %', as the page words a change."""
    return f"gains {gain_pct:.1f} %" if gain_pct >= 0 else f"loses {-gain_pct:.1f} %"


def _bare(gain_pct: float) -> str:
    return f"{abs(gain_pct):.1f} %"


# ---------------------------------------------------------------------------
# prose -> CSV
# ---------------------------------------------------------------------------
def test_panel_a_sentence_quotes_the_sweep(page: str, sweep: pd.DataFrame) -> None:
    w35 = _curve(sweep, 7.0, 35.0)
    w45 = _curve(sweep, 7.0, 45.0)
    cold = _curve(sweep, -7.0, 45.0)
    text = _flat(page)
    # The three curves fall from full load to the floor; the page words them as losses, so a sign change fails here.
    assert w35["gain_pct"] < 0 and w45["gain_pct"] < 0 and cold["gain_pct"] < 0
    expected = (
        f"{_change(w35['gain_pct'])} between full load and the speed floor "
        f"(reached at {w35['edge_pct']:.0f} % load) at 7 °C air / 35 °C water, and "
        f"{_bare(w45['gain_pct'])} at 7/45 °C. At -7/45 °C it {_change(cold['gain_pct'])} "
        f"(COP {cold['full']:.2f} at full load, {cold['at_75']:.2f} at 75 % load, "
        f"{cold['floor']:.2f} at the floor, reached at {cold['edge_pct']:.0f} % load)"
    )
    assert expected in text, f"part-load.rst panel (a) should read {expected!r}. {HINT}."
    # "Each curve peaks above the floor and falls before the compressor reaches it".
    for curve in (w35, w45, cold):
        assert not curve["peak_at_floor"] and curve["peak"] > curve["floor"]
    expected = (
        f"at 7/35 °C the peak is COP {w35['peak']:.2f} at {w35['peak_pct']:.0f} % load ({w35['peak_rps']:.0f} rev/s), "
        f"at -7/45 °C {cold['peak']:.2f} at {cold['peak_pct']:.0f} % load ({cold['peak_rps']:.0f} rev/s)"
    )
    assert expected in text, f"part-load.rst should read {expected!r}. {HINT}."


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
    expected = f"(COP {cold['floor']:.2f} at the floor, reached at {cold['edge_pct']:.0f} % load)"
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
    assert not mismatches, f"validation/data/part_load_figure: {HINT}.\n  " + "\n  ".join(mismatches[:15])


def test_the_cold_rise_is_the_defrost_adjustment(
    page: str, figure, sweep: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'that easing is the rise to 75 % load. Without it (defrost=False) the same curve reads COP 2.75 at full
    load (90 rev/s), 2.76 at 75 % load and 1.81 at the speed floor (16 % load): flat to 75 % load and falling
    below it.' and the floor point it names (15 rev/s, its pressure ratio and isentropic efficiency)."""
    frosted = _curve(sweep, -7.0, 45.0)
    monkeypatch.setattr(figure, "MODEL_SETTINGS", {**figure.MODEL_SETTINGS, "defrost": False})
    floor = frosted["edge_pct"] / 100.0
    rows = pd.DataFrame(figure.sweep_fixed_temperatures(conditions=((-7.0, 45.0),), fractions=(1.0, 0.75, floor)))
    assert (rows.failure_reason == "none").all()
    assert rows.at_floor.iloc[-1]
    cop = rows.cop.to_numpy()
    text = _flat(page)
    assert (
        f"the same curve reads COP {cop[0]:.2f} at full load ({rows.rps.iloc[0]:.0f} rev/s), {cop[1]:.2f} at 75 % "
        f"load and {cop[2]:.2f} at the speed floor ({frosted['edge_pct']:.0f} % load)"
    ) in text, f"frost-free -7/45 °C: {cop}. {HINT}."
    # "flat to 75 % load and falling below it"; the frosted curve's rise to 75 % is the adjustment easing.
    assert abs(cop[1] / cop[0] - 1.0) < 0.01, cop
    assert cop[2] < 0.75 * cop[1], cop
    assert frosted["at_75"] / frosted["full"] - 1.0 > 0.02 + (cop[1] / cop[0] - 1.0)
    # The floor point: below the rotary data in speed, its efficiency as the page states it.
    monkeypatch.setattr(figure, "MODEL_SETTINGS", {**figure.MODEL_SETTINGS, "defrost": True})
    model = figure._model()
    from tmhp.compressor_efficiency import COMPRESSOR_TYPES, SEMI_EMPIRICAL_TYPES

    results = [
        figure.solve_at_leaving_water(model, -7.0, 45.0, figure.NOMINAL_W * f, figure.WATER_FLOW_KG_S)[0]
        for f in (1.0, floor)
    ]
    full, low = results
    speed_floor = min(n for _, n in SEMI_EMPIRICAL_TYPES[COMPRESSOR_TYPES[0]]["hull_PR_N"])
    assert low["cmp_rpm [rpm]"] / 60.0 == pytest.approx(model.rps_min) and model.rps_min < speed_floor
    assert low["efficiency_extrapolated"] and not full["efficiency_extrapolated"]
    assert (
        f"runs at {model.rps_min:.0f} rev/s and a pressure ratio of {low['pr_cmp [-]']:.1f}, with isentropic "
        f"efficiency {low['eta_cmp_isen [-]']:.3f} against {full['eta_cmp_isen [-]']:.3f} at full load"
    ) in text
    assert f"the rotary data end at {speed_floor:.0f} rev/s" in text
