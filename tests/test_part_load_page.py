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
    """Full-load COP, COP nearest 75 % load, the speed-floor edge and its COP, the gain to it [%]."""
    rows = sweep[(sweep.t_outdoor_C == t_outdoor) & (sweep.lwt_C == lwt)].sort_values("plr", ascending=False)
    assert (rows.failure_reason == "none").all(), f"{t_outdoor}/{lwt}: a sweep point did not converge"
    full = rows.iloc[0]
    assert full.plr == 1.0
    floor = rows[rows.at_floor]
    edge = floor.loc[floor.plr.idxmax()]
    near_75 = rows.loc[(rows.plr - 0.75).abs().idxmin()]
    return {
        "full": float(full.cop),
        "at_75": float(near_75.cop),
        "edge_pct": float(edge.plr) * 100.0,
        "floor": float(edge.cop),
        "gain_pct": (float(edge.cop) / float(full.cop) - 1.0) * 100.0,
    }


# ---------------------------------------------------------------------------
# prose -> CSV
# ---------------------------------------------------------------------------
def test_panel_a_sentence_quotes_the_sweep(page: str, sweep: pd.DataFrame) -> None:
    w35 = _curve(sweep, 7.0, 35.0)
    w45 = _curve(sweep, 7.0, 45.0)
    cold = _curve(sweep, -7.0, 45.0)
    text = _flat(page)
    expected = (
        f"gains {w35['gain_pct']:.1f} % between full load and the speed floor "
        f"(reached at {w35['edge_pct']:.0f} % load) at 7 °C air / 35 °C water, and "
        f"{w45['gain_pct']:.1f} % at 7/45 °C. At -7/45 °C it gains {cold['gain_pct']:.1f} % "
        f"(COP {cold['full']:.2f} at full load, {cold['at_75']:.2f} at 75 % load, "
        f"{cold['floor']:.2f} at the floor, reached at {cold['edge_pct']:.0f} % load)"
    )
    assert expected in text, f"part-load.rst panel (a) should read {expected!r}. {HINT}."


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


def test_trajectory_claims_hold(trajectory: pd.DataFrame, band: pd.DataFrame) -> None:
    """'rises monotonically A -> D and sits inside the certified band at all four points',
    and where in the band each point sits (the sentence under the table)."""
    cop = trajectory.cop
    assert list(cop.index) == ["A", "B", "C", "D"]
    assert cop.is_monotonic_increasing, cop.to_dict()
    for point in cop.index:
        assert band.at[point, "cop_p10"] <= cop[point] <= band.at[point, "cop_p90"], point
    assert band.at["A", "cop_p75"] < cop["A"] < band.at["A", "cop_p90"]
    assert band.at["B", "cop_p25"] < cop["B"] < band.at["B", "cop_p75"]
    for point in ("C", "D"):
        assert band.at[point, "cop_p25"] < cop[point] < band.at[point, "cop_median"], point
    # B, C and D deliver more than the point asks for; A modulates to it.
    assert not trajectory.at["A", "clamped"]
    assert trajectory.loc[["B", "C", "D"], "clamped"].all()


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


def test_the_cold_rise_is_the_defrost_adjustment(figure, monkeypatch: pytest.MonkeyPatch) -> None:
    """'Without it (defrost=False) the same curve is close to flat.'"""
    monkeypatch.setattr(figure, "MODEL_SETTINGS", {**figure.MODEL_SETTINGS, "defrost": False})
    rows = pd.DataFrame(figure.sweep_fixed_temperatures(conditions=((-7.0, 45.0),), fractions=(1.0, 0.75, 0.296)))
    assert (rows.failure_reason == "none").all()
    assert rows.at_floor.iloc[-1]
    cop = rows.cop.to_numpy()
    assert abs(cop[-1] / cop[0] - 1.0) < 0.03, f"frost-free -7/45 °C curve is not close to flat: {cop}"
    assert abs(cop[1] / cop[0] - 1.0) < 0.03, f"frost-free -7/45 °C curve is not close to flat: {cop}"
