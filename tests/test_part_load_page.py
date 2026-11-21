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

Since the fixed control power (``E_ctrl``, 15 W to water, 12 W to air) and the
default rotary speed shape ``ossorio_anchored``, several statements changed
direction, not only value: the −7/45 °C floor is above its nameplate point, the
entering-water floor is above the 9 kW duty, the EN 14825 trajectory is inside
the band at all four points, the frost-free −7/45 °C curve is no longer flat to
75 % of the nameplate, and the air-to-air +7 °C heating curve has its highest
COP one request above the floor. The page also compares with the machine of
before; those comparison machines are built here explicitly (``E_ctrl=0.0``,
``rotary_speed_variant="shao_informed"``, the recorded BITZER map) and rerun.
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
    # "at about a third of the maximum at 7/35 and -7/45, a little under half at 7/45". (With the earlier speed
    # shape the peaks sat at 44 %, 46 % and 54 % of the maximum; test_panel_a_machine_of_before rebuilds that.)
    assert 30 < w35["peak_of_max_pct"] < 36 and 30 < cold["peak_of_max_pct"] < 36 and 40 < w45["peak_of_max_pct"] < 50
    assert "at about a third of the maximum at 7/35 °C and -7/45 °C and a little under half at 7/45 °C" in text
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
    """The nameplate-axis numbers the page used to give, recomputed from the nameplate and floor rows.

    At 7/35 °C and 7/45 °C the floor is below the 9 kW duty; at -7/45 °C it is above it (the frost and defrost
    adjustment takes 17 % at the nameplate duty and nothing at the floor, and the floor's own fall is smaller
    with the present speed shape). With the earlier shape all three were losses.
    """
    w35, w45, cold = (_curve(sweep, t, lwt) for t, lwt in CONDITIONS)
    assert w35["gain_pct"] < 0 and w45["gain_pct"] < 0 and cold["gain_pct"] > 0
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


def test_denominator_paragraph_quotes_the_full_load_capacities(page: str, figure, monkeypatch) -> None:
    """'that capacity is 18.1 kW at the D point (+12 °C, water 24 °C) against 9.9 kW at the A point (-7 °C, water
    34 °C), a factor of 1.8 (1.5 without frost and defrost)': the top-speed capacity of the page's machine at the
    two ends of the EN 14825 trajectory."""
    points = {label: (t_outdoor, lwt) for label, t_outdoor, lwt, _ in figure.EN14825_LOW}
    model = figure._model()
    q_a, q_d = (figure.maximum_capacity(model, *points[label]) for label in ("A", "D"))
    monkeypatch.setattr(figure, "MODEL_SETTINGS", {**figure.MODEL_SETTINGS, "defrost": False})
    model = figure._model()
    free_a, free_d = (figure.maximum_capacity(model, *points[label]) for label in ("A", "D"))
    assert free_d == pytest.approx(q_d, rel=1e-6) and free_a > q_a  # frost at A only
    expected = (
        f"that capacity is {q_d / 1000.0:.1f} kW at the D point (+12 °C, water 24 °C) against {q_a / 1000.0:.1f} kW "
        f"at the A point (-7 °C, water 34 °C), a factor of {q_d / q_a:.1f} ({free_d / free_a:.1f} without frost and "
        "defrost)"
    )
    assert expected in _flat(page), f"{expected!r}. {HINT}."


def test_control_power_shares(page: str, sweep: pd.DataFrame, trajectory: pd.DataFrame) -> None:
    """The paragraph on the COP's denominator: the fixed control power as a share of ``E_tot`` at the heavy and the
    light end of each curve on the page. ``E_tot`` is the delivered heat over the COP for the air-to-water rows
    (the files hold both) and the ``e_tot_W`` column for the air-to-air rows."""
    from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
    from tmhp.control_power import CONTROL_POWER_SINK_AIR_W, CONTROL_POWER_SINK_WATER_W

    assert AirSourceHeatPumpBoiler(hp_capacity=NAMEPLATE_W, ref="R32").E_ctrl == CONTROL_POWER_SINK_WATER_W == 15.0
    assert AirSourceHeatPump(hp_capacity=A2A_NAMEPLATE_W, ref="R32").E_ctrl == CONTROL_POWER_SINK_AIR_W == 12.0
    text = _flat(page)
    assert "15 W for the air-to-water model and 12 W for the air-to-air model" in text
    assert ":ref:`fixed-control-power`" in page

    def share_water(q_w: float, cop: float) -> float:
        return CONTROL_POWER_SINK_WATER_W / (q_w / cop) * 100.0

    curves = [_curve(sweep, t, lwt) for t, lwt in CONDITIONS]
    at_duty = [share_water(NAMEPLATE_W, c["at_nameplate"]) for c in curves]
    at_floor = [share_water(c["q_min"], c["floor"]) for c in curves]
    assert all(low > 3.0 * high for low, high in zip(at_floor, at_duty, strict=True))
    a, d = trajectory.loc["A"], trajectory.loc["D"]
    expected = (
        f"In panel (a) it is {at_duty[0]:.1f} %, {at_duty[1]:.1f} % and {at_duty[2]:.1f} % of the input at the 9 kW "
        f"duty and {at_floor[0]:.1f} %, {at_floor[1]:.1f} % and {at_floor[2]:.1f} % at the speed floor; on the "
        f"trajectory of panel (b) {share_water(a.q_delivered_W, a.cop):.1f} % at A and "
        f"{share_water(d.q_delivered_W, d.cop):.1f} % at D"
    )
    assert expected in text, f"{expected!r}. {HINT}."
    # Air-to-air: the six fixed-temperature curves, and the EN 14825 cooling points.
    air = pd.read_csv(ASHP_SWEEP, float_precision="round_trip")
    air = air[(air.capacity_kW == 3.5) & (air.refrigerant == "R32")]
    duty_rows = air[air.plr_nameplate == 1.0]
    floor_rows = air[air.capacity_clamped == "min"]
    # Every request below the floor is one operating point: one input per curve (to the search's tolerance).
    spread = floor_rows.groupby(["duty", "t_outdoor_C"]).e_tot_W.agg(lambda s: s.max() / s.min() - 1.0)
    assert len(duty_rows) == 6 and len(spread) == 6 and spread.max() < 1e-4
    duty_share = CONTROL_POWER_SINK_AIR_W / duty_rows.e_tot_W * 100.0
    floor_share = CONTROL_POWER_SINK_AIR_W / floor_rows.e_tot_W * 100.0
    points = pd.read_csv(REPO_ROOT / "validation" / "data" / "ashp_en14825_points.csv")
    cooling = points[points.duty == "cooling"]
    share = CONTROL_POWER_SINK_AIR_W / cooling.e_tot_W * 100.0
    expected = (
        f"it is {duty_share.min():.1f} to {duty_share.max():.1f} % at the nameplate duty and "
        f"{floor_share.min():.1f} to {floor_share.max():.1f} % at the speed floor over the six conditions, and on its "
        f"EN 14825 points up to {share[cooling.point == 'A'].max():.1f} % at A and up to "
        f"{share[cooling.point == 'D'].max():.1f} % at D in cooling"
    )
    assert expected in text, f"{expected!r}. {HINT}."


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
    # With the entering water held the floor is above the 9 kW duty; with the leaving water held it is below.
    assert not peak.at_floor and floor.cop > nameplate.cop and held["gain_pct"] < 0
    expected = (
        f"The peak is then COP {peak.cop:.2f} at {peak.q_delivered_W / NAMEPLATE_W * 100:.0f} % of the nameplate "
        f"({peak.delivered_fraction_of_max * 100:.0f} % of the maximum, {peak.rps:.0f} rev/s), "
        f"{(peak.cop / nameplate.cop - 1) * 100:.1f} % above the 9 kW duty against "
        f"{(held['peak'] / held['at_nameplate'] - 1) * 100:.1f} % with the leaving water held, and the speed floor is "
        f"{(floor.cop / nameplate.cop - 1) * 100:.1f} % above the 9 kW duty against {_bare(held['gain_pct'])} below"
    )
    assert expected in _flat(page), f"part-load.rst should read {expected!r}. {HINT}."


def test_panel_a_decomposition_quotes_the_study(page: str) -> None:
    """'with the three efficiencies frozen ... gains 19.7 % ... with the shipped efficiencies loses 3.7 %, after a
    peak 5.5 % above full load at 56 % of it; the air-to-air model, at pressure ratios of 2.4 to 2.7 at full load,
    keeps +34 to +44 %', and the fan and air-side figures of the speed-floor note from the same study."""
    study = REPO_ROOT / "validation" / "data" / "fixed_boundary_plr"
    decomposition = pd.read_csv(study / "decomposition.csv")

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
    # The shipped curve is not monotonic: it peaks between full load and the floor.
    rows = decomposition[
        (decomposition.variant == "current")
        & (decomposition.model_class == "ASHPB")
        & (decomposition.refrigerant == "R32")
        & (decomposition.t_outdoor_C == 7.0)
        & (decomposition.failure_reason == "none")
    ].sort_values("plr_request", ascending=False)
    peak = rows.loc[rows.cop_sys.idxmax()]
    assert peak.capacity_clamped != "min" and 0.3 < peak.plr_request < 1.0
    assert (
        f"with the shipped efficiencies loses {-shipped:.1f} %, after a peak "
        f"{(peak.cop_sys / rows.cop_sys.iloc[0] - 1.0) * 100.0:.1f} % above full load at "
        f"{peak.plr_request * 100.0:.0f} % of it"
    ) in text
    a2a = gain("current", "ASHP")
    gains, prs = [g for g, _ in a2a], [p for _, p in a2a]
    assert (
        f"at pressure ratios of {min(prs):.1f} to {max(prs):.1f} at full load, keeps "
        f"+{min(gains):.0f} to +{max(gains):.0f} %"
    ) in text
    # The speed-floor note: 'the outdoor fan now stays above 52 % of rated flow over the whole sweep and the
    # air-side temperature drop stays within 5.35 K; the air-to-air sweep stays above 47 % and 6.5 K'.
    water = pd.read_csv(study / "ashpb_sweep.csv")
    water = water[(water.refrigerant == "R32") & (water.t_outdoor_C == 7.0) & (water.failure_reason == "none")]
    air = pd.read_csv(study / "ashp_sweep.csv")
    air = air[air.failure_reason == "none"]
    assert 5.30 < water.air_dT_K.max() <= 5.35 and 6.0 < air.air_dT_K.max() <= 6.5
    assert (
        f"the outdoor fan now stays above {math.floor(water.fan_fraction.min() * 100.0)} % of rated flow over the "
        "whole sweep and the air-side temperature drop stays within 5.35 K; the air-to-air sweep stays above "
        f"{math.floor(air.fan_fraction.min() * 100.0)} % and 6.5 K"
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

    'rises monotonically A -> D and sits inside the certified band at all four points', and the sentence under
    the table. With the earlier rotary speed shape and no control power C was 0.1 % below the 10th percentile
    (test_trajectory_of_the_comparison_machines rebuilds that machine); with the present shape B and C moved up
    into the lower half of the band and D into its upper quarter. If a point moves to another band position, or
    leaves the band, this fails.
    """
    cop = trajectory.cop
    assert list(cop.index) == ["A", "B", "C", "D"]
    assert cop.is_monotonic_increasing, cop.to_dict()
    for point in ("A", "B", "C", "D"):
        assert band.at[point, "cop_p10"] <= cop[point] <= band.at[point, "cop_p90"], point
    text = _flat(page)
    assert "rises monotonically A → D and sits inside the certified band at all four points" in text
    for point in ("A", "B", "C"):
        assert band.at[point, "cop_p25"] < cop[point] < band.at[point, "cop_median"], point
    assert band.at["D", "cop_p75"] < cop["D"] < band.at["D", "cop_p90"]
    off = {point: (cop[point] / band.at[point, "cop_median"] - 1.0) * 100.0 for point in cop.index}
    assert (
        "between the 25th percentile and the median at A, B and C, and between the 75th and the 90th at D: "
        f"{-off['A']:.1f} %, {-off['B']:.1f} % and {-off['C']:.1f} % below the median at A, B and C and "
        f"{off['D']:.1f} % above it at D"
    ) in text
    # C and D deliver more than the point asks for; A and B modulate to it.
    assert not trajectory.loc[["A", "B"], "clamped"].any()
    assert trajectory.loc[["C", "D"], "clamped"].all()


def test_trajectory_check_counts_quote_the_study(page: str) -> None:
    """The admonition on the default rotary speed shape: what was read to choose it, and what the study gives now.

    'the shape check passes in 30 of 30 cases under both variants ...; the level check passes in 27 of 30 under the
    default and in 6 of 30 under the other variant', 'the present defaults pass that shape check in 30 and the
    level check ... in 27', the three cases outside, the medians by point, the counts without the control power
    and the BITZER map's count. The counts of the other variant and of the machines without the control power are
    those of ``validation/data/rotary_speed_evidence`` (the measurements the choice was made on, #183).
    """
    data = REPO_ROOT / "validation" / "data"
    verdict = pd.read_csv(data / "en14825_trend_verdict.csv")
    defaults = verdict[verdict.variant == "defaults"]
    previous = verdict[verdict.variant == "quadratic-2026-10-06"]
    evidence = pd.read_csv(data / "rotary_speed_evidence" / "en14825_summary.csv")

    def count(variant: str, power: float, quantity: str) -> tuple[int, int]:
        row = evidence[
            (evidence.variant == variant)
            & (evidence.P_fixed_W == power)
            & (evidence.application == "both")
            & (evidence.quantity == quantity)
        ]
        assert len(row) == 1, (variant, power, quantity)
        return int(row.cases_inside.iat[0]), int(row.cases.iat[0])

    text = _flat(page)
    n = len(defaults)
    assert "the number of cases whose COP(D)/COP(A) lies inside" in text
    # What the choice read: with the 15 W, D/A under both variants and the level under each.
    assert count("ossorio_anchored", 15.0, "D/A") == count("shao_informed", 15.0, "D/A") == (n, n)
    assert count("ossorio_anchored", 15.0, "all four points") == (int(defaults.C2_level.sum()), n)
    other_level, _ = count("shao_informed", 15.0, "all four points")
    assert (
        f"With the 15 W in the model the shape check passes in {n} of {n} cases under both variants and no longer "
        f"separates them; the level check passes in {int(defaults.C2_level.sum())} of {n} under the default and in "
        f"{other_level} of {n} under the other variant"
    ) in text
    assert (
        f"On the study's {n} cases the present defaults pass that shape check in "
        f"{int(defaults.C3_gradient.sum())} and the level check (every point inside its p10–p90) in "
        f"{int(defaults.C2_level.sum())}"
    ) in text
    # 'The three cases outside are the medium-temperature 9 kW R410A machine at its three design loads, below the
    # 10th percentile at C in all three and at B in one.'
    outside = defaults[~defaults.C2_level]
    assert len(outside) == 3 and (outside.application == "medium").all()
    assert (outside.capacity_kW == 9.0).all() and (outside.refrigerant == "R410A").all()
    assert (outside.pct_C < 10.0).all() and int((outside.pct_B < 10.0).sum()) == 1
    assert (outside[["pct_A", "pct_D"]] > 10.0).all().all() and (outside[["pct_A", "pct_D"]] < 90.0).all().all()
    assert "The three cases outside are the medium-temperature 9 kW R410A machine at its three design loads" in text
    assert "below the 10th percentile at C in all three and at B in one" in text
    # The medians by point against the population medians.
    points = pd.read_csv(data / "en14825_trend_points.csv")
    medians = points[points.variant == "defaults"].groupby(["application", "point"]).cop.median()
    keymark = pd.read_csv(KEYMARK).set_index(["application", "point"]).cop_median

    def four(series: pd.Series, application: str) -> str:
        return " / ".join(f"{series[application, point]:.2f}" for point in "ABCD")

    assert (
        f"over the 15 low-temperature cases are {four(medians, 'low')} at A to D against the population medians "
        f"{four(keymark, 'low')}, and over the 15 medium-temperature cases {four(medians, 'medium')} against "
        f"{four(keymark, 'medium')}"
    ) in text
    # Without the control power; and the earlier release's map in the present model.
    same_shape, _ = count("ossorio_anchored", 0.0, "all four points")
    d_inside, _ = count("ossorio_anchored", 0.0, "D")
    scores = pd.read_csv(data / "rotary_speed_evidence" / "en14825_scores.csv")
    d_out = scores[(scores.variant == "ossorio_anchored") & (scores.P_fixed_W == 0.0) & ~scores.in_D]
    p90 = pd.read_csv(KEYMARK).set_index(["application", "point"]).cop_p90
    assert len(d_out) == n - d_inside and all(row.cop_D > p90[row.application, "D"] for row in d_out.itertuples())
    other_shape, _ = count("shao_informed", 0.0, "all four points")
    assert (
        f"Without the control power the same shape passes the level check in {same_shape} of {n} ({n - d_inside} D "
        f"points above the 90th percentile), and the other shape in {other_shape} of {n}"
    ) in text
    assert (
        "The BITZER quadratic map of the earlier release, in the present model with the same control power, passes "
        f"the level check in {int(previous.C2_level.sum())} of {len(previous)}"
    ) in text


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


def _rotary_data_edge() -> tuple[float, float]:
    """Lowest pressure ratio and lowest speed [rev/s] of the default compressor type's data (its validity hull)."""
    from tmhp.compressor_efficiency import COMPRESSOR_TYPES, SEMI_EMPIRICAL_TYPES

    hull = SEMI_EMPIRICAL_TYPES[COMPRESSOR_TYPES[0]]["hull_PR_N"]
    return min(pr for pr, _ in hull), min(n for _, n in hull)


def test_the_cold_curve_and_the_defrost_adjustment(
    page: str, figure, sweep: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The -7/45 °C paragraph: the frost-free curve beside the frosted one, and the floor point it names.

    'Without the adjustment (defrost=False) the same curve reads COP 2.62 at the nameplate duty (89 rev/s), 2.68 at
    75 % of it and 2.19 at the speed floor (15 % of the nameplate): a peak 2.6 % above the nameplate duty at 67 % of
    it, and a floor 16.2 % below the nameplate duty. The adjustment takes 17.0 % of the COP at the nameplate duty
    and less than 0.1 % at the floor, so the frosted curve keeps rising down to 34 % of the nameplate and its floor
    is 0.9 % above its nameplate point'.

    With the earlier speed shape the frost-free curve was flat to 75 % of the nameplate (within 1 %) and its floor a
    third below; that scenario is gone on the default machine (the frost-free curve now rises 2.4 % to 75 % and
    peaks below it), so what is pinned is what the page states now: the size of the adjustment at the nameplate
    duty and at the floor, and the two floors against their nameplate points.
    """
    from tmhp.defrost import DefrostModel

    frosted = _curve(sweep, -7.0, 45.0)
    monkeypatch.setattr(figure, "MODEL_SETTINGS", {**figure.MODEL_SETTINGS, "defrost": False})
    free = pd.DataFrame(figure.sweep_fixed_temperatures(conditions=((-7.0, 45.0),)))
    assert (free.failure_reason == "none").all()
    model = figure._model()
    at_75 = figure._sweep_row(model, -7.0, 45.0, 0.75 * figure.NOMINAL_W, free.q_max_W.iloc[0], nameplate=False)
    nameplate = free[free.is_nameplate].iloc[0]
    floor = free[free.at_floor].iloc[0]
    peak = free.loc[free.cop.idxmax()]
    text = _flat(page)
    # The two floors are the same operating point: no frost correction at that load.
    assert floor.q_delivered_W == pytest.approx(frosted["q_min"], rel=1e-3)
    floor_loss = (1.0 - floor.cop / nameplate.cop) * 100.0
    assert (
        f"the same curve reads COP {nameplate.cop:.2f} at the nameplate duty ({nameplate.rps:.0f} rev/s), "
        f"{at_75['cop']:.2f} at 75 % of it and {floor.cop:.2f} at the speed floor "
        f"({frosted['floor_of_nameplate_pct']:.0f} % of the nameplate): a peak "
        f"{(peak.cop / nameplate.cop - 1.0) * 100.0:.1f} % above the nameplate duty at "
        f"{peak.q_delivered_W / NAMEPLATE_W * 100.0:.0f} % of it, and a floor {floor_loss:.1f} % below the nameplate "
        "duty"
    ) in text, f"frost-free -7/45 °C: {nameplate.cop}, {at_75['cop']}, {floor.cop}. {HINT}."
    assert not peak.at_floor and peak.q_delivered_W < nameplate.q_delivered_W
    taken_nameplate = (1.0 - frosted["at_nameplate"] / nameplate.cop) * 100.0
    taken_floor = (1.0 - frosted["floor"] / floor.cop) * 100.0
    assert 0.0 <= taken_floor < 0.1 < taken_nameplate
    assert frosted["gain_pct"] > 0.0 > -floor_loss
    assert (
        f"The adjustment takes {taken_nameplate:.1f} % of the COP at the nameplate duty and less than 0.1 % at the "
        f"floor, so the frosted curve keeps rising down to {frosted['peak_of_nameplate_pct']:.0f} % of the nameplate "
        f"and its floor is {frosted['gain_pct']:.1f} % above its nameplate point where the frost-free floor is "
        f"{floor_loss:.1f} % below"
    ) in text
    # The frosted machine, and where the adjustment reaches its full size: 'a frost-free duty of 6.7 kW at -7 °C
    # ...; the machine crosses that duty between 58 % and 60 % of the maximum, which is the bend in the drawn curve'.
    monkeypatch.setattr(figure, "MODEL_SETTINGS", {**figure.MODEL_SETTINGS, "defrost": True})
    model = figure._model()
    reference = DefrostModel.reference_capacity(figure.NOMINAL_W, -7.0)
    duty = {
        share: figure.solve_at_leaving_water(model, -7.0, 45.0, share * frosted["q_max"], figure.WATER_FLOW_KG_S)[0][
            "Q_ref_tank_frost_free [W]"
        ]
        for share in (0.58, 0.60)
    }
    assert duty[0.58] < reference <= duty[0.60]
    assert (
        f"a frost-free duty of {reference / 1000.0:.1f} kW at -7 °C, and eases below that; the machine crosses that "
        "duty between 58 % and 60 % of the maximum"
    ) in text
    # The floor point: below the rotary data in speed, its efficiency as the page states it.
    results = [
        figure.solve_at_leaving_water(model, -7.0, 45.0, q, figure.WATER_FLOW_KG_S)[0]
        for q in (figure.NOMINAL_W, 0.5 * frosted["q_min"])
    ]
    full, low = results
    _, speed_floor = _rotary_data_edge()
    assert low["cmp_rpm [rpm]"] / 60.0 == pytest.approx(model.rps_min) and model.rps_min < speed_floor
    assert low["efficiency_extrapolated"] and not full["efficiency_extrapolated"]
    assert (
        f"runs at {model.rps_min:.0f} rev/s and a pressure ratio of {low['pr_cmp [-]']:.1f}, with isentropic "
        f"efficiency {low['eta_cmp_isen [-]']:.3f} against {full['eta_cmp_isen [-]']:.3f} at the nameplate duty"
    ) in text
    assert f"the rotary data end at {speed_floor:.0f} rev/s" in text
    assert model.rps_min == pytest.approx(0.5 * speed_floor)
    assert f"The default speed floor, {model.rps_min:.0f} rev/s, is half the lowest speed in those data" in text


def test_panel_a_low_end_is_below_the_rotary_data(page: str, sweep: pd.DataFrame) -> None:
    """'On all three curves the compressor runs below 30 rev/s for every request under 30 % of the maximum (54 %,
    53 % and 32 % of the nameplate): the last eight grid points before the floor and the floor itself are that
    extrapolation, and the peaks at 7/35 °C and -7/45 °C, at 33 rev/s, sit just above its edge'."""
    _, speed_floor = _rotary_data_edge()
    shares = []
    for t_outdoor, lwt in CONDITIONS:
        rows = sweep[(sweep.t_outdoor_C == t_outdoor) & (sweep.lwt_C == lwt) & ~sweep.is_nameplate]
        inside = rows[rows.rps >= speed_floor]
        edge = inside.loc[inside.rps.idxmin()]
        assert edge.plr_max == pytest.approx(0.30), (t_outdoor, lwt, edge.plr_max)
        below = rows[(rows.rps < speed_floor) & ~rows.at_floor]
        assert len(below) == 8 and below.plr_max.max() < 0.30
        shares.append(edge.q_delivered_W / NAMEPLATE_W * 100.0)
    w35, w45, cold = (_curve(sweep, t, lwt) for t, lwt in CONDITIONS)
    assert speed_floor < w35["peak_rps"] < 1.15 * speed_floor and speed_floor < cold["peak_rps"] < 1.15 * speed_floor
    assert w45["peak_rps"] > 1.5 * speed_floor
    assert f"{w35['peak_rps']:.0f}" == f"{cold['peak_rps']:.0f}"
    expected = (
        f"On all three curves the compressor runs below {speed_floor:.0f} rev/s for every request under 30 % of the "
        f"maximum ({shares[0]:.0f} %, {shares[1]:.0f} % and {shares[2]:.0f} % of the nameplate): the last eight grid "
        "points before the floor and the floor itself are that extrapolation, and the peaks at 7/35 °C and "
        f"-7/45 °C, at {w35['peak_rps']:.0f} rev/s, sit just above its edge"
    )
    assert expected in _flat(page), f"{expected!r}. {HINT}."


def _machine_of_before() -> dict:
    """Constructor arguments of the defaults before the fixed control power and the present rotary speed shape."""
    from tmhp.compressor_efficiency import CompressorSpec

    return {
        "E_ctrl": 0.0,
        "compressor_type": CompressorSpec("rotary_twin_inverter", rotary_speed_variant="shao_informed"),
    }


def test_panel_a_machine_of_before(page: str, figure, sweep: pd.DataFrame, monkeypatch: pytest.MonkeyPatch) -> None:
    """The comparison paragraph of panel (a), with its machines built explicitly and rerun.

    'Built that way it loses 16.7 %, 27.0 % and 21.2 % between the 9 kW duty and the speed floor, its peaks are at
    44 %, 54 % and 46 % of the maximum, and its floor is 18.0 %, 27.1 % and 24.5 % below them. ... with the present
    shape and no control power the three changes from the 9 kW duty to the floor are -3.9 %, -12.7 % and +3.1 %.'
    """
    settings = dict(figure.MODEL_SETTINGS)
    text = _flat(page)
    assert 'CompressorSpec("rotary_twin_inverter", rotary_speed_variant="shao_informed")' in text
    monkeypatch.setattr(figure, "MODEL_SETTINGS", {**settings, **_machine_of_before()})
    before = pd.DataFrame(figure.sweep_fixed_temperatures())
    b = [_curve(before, t, lwt) for t, lwt in CONDITIONS]
    now = [_curve(sweep, t, lwt) for t, lwt in CONDITIONS]
    # Smaller falls and lower peaks now, at every condition.
    for was, is_ in zip(b, now, strict=True):
        assert was["gain_pct"] < is_["gain_pct"] and was["to_floor_pct"] < is_["to_floor_pct"]
        assert was["peak_of_max_pct"] > is_["peak_of_max_pct"]
    assert all(curve["gain_pct"] < 0 for curve in b)
    expected = (
        f"Built that way it loses {-b[0]['gain_pct']:.1f} %, {-b[1]['gain_pct']:.1f} % and {-b[2]['gain_pct']:.1f} % "
        f"between the 9 kW duty and the speed floor, its peaks are at {b[0]['peak_of_max_pct']:.0f} %, "
        f"{b[1]['peak_of_max_pct']:.0f} % and {b[2]['peak_of_max_pct']:.0f} % of the maximum, and its floor is "
        f"{-b[0]['to_floor_pct']:.1f} %, {-b[1]['to_floor_pct']:.1f} % and {-b[2]['to_floor_pct']:.1f} % below them"
    )
    assert expected in text, f"{expected!r}. {HINT}."
    # The present shape without the control power: the nameplate duty and the floor of each condition.
    monkeypatch.setattr(figure, "MODEL_SETTINGS", {**settings, "E_ctrl": 0.0})
    model = figure._model()
    changes = []
    for (t_outdoor, lwt), curve in zip(CONDITIONS, now, strict=True):
        rows = [
            figure._sweep_row(model, t_outdoor, lwt, q, curve["q_max"], nameplate=False)
            for q in (figure.NOMINAL_W, 0.5 * curve["q_min"])
        ]
        assert rows[1]["at_floor"] and all(row["failure_reason"] == "none" for row in rows)
        changes.append((rows[1]["cop"] / rows[0]["cop"] - 1.0) * 100.0)
    # The control power lowers the floor against the nameplate duty at every condition (a larger share there).
    assert all(without > with_["gain_pct"] for without, with_ in zip(changes, now, strict=True))
    expected = (
        "with the present shape and no control power the three changes from the 9 kW duty to the floor are "
        f"{changes[0]:+.1f} %, {changes[1]:+.1f} % and {changes[2]:+.1f} %"
    )
    assert expected in text, f"{expected!r}. {HINT}."


def test_trajectory_of_the_comparison_machines(
    page: str, figure, band: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'With the other rotary speed shape and no control power ... the trajectory reads 2.95 / 4.18 / 5.36 / 8.28,
    with C 0.1 % below the 10th percentile. With the BITZER quadratic map of the release before the semi-empirical
    models, run in the present model with the same 15 W, it reads 3.34 / 4.56 / 5.83 / 7.64, inside the band at
    all four points.'"""
    from validation.analysis import en14825_trend as study

    settings = dict(figure.MODEL_SETTINGS)
    text = _flat(page)

    def read(**kwargs) -> pd.Series:
        monkeypatch.setattr(figure, "MODEL_SETTINGS", {**settings, **kwargs})
        rows = pd.DataFrame(figure.trajectory_en14825()).set_index("point")
        assert (rows.failure_reason == "none").all()
        return rows.cop

    before = read(**_machine_of_before())
    below = (1.0 - before["C"] / band.at["C", "cop_p10"]) * 100.0
    assert 0.0 < below < 0.5
    for point in ("A", "B", "D"):
        assert band.at[point, "cop_p10"] <= before[point] <= band.at[point, "cop_p90"], point
    assert (
        f"the trajectory reads {' / '.join(f'{before[p]:.2f}' for p in 'ABCD')}, with C {below:.1f} % below the "
        "10th percentile"
    ) in text
    (quadratic,) = [v for v in study.variants() if v.key == "quadratic-2026-10-06"]
    from tmhp.control_power import CONTROL_POWER_SINK_WATER_W

    assert "E_ctrl" not in quadratic.kwargs and CONTROL_POWER_SINK_WATER_W == 15.0  # the map, with the default power
    earlier_map = read(**quadratic.kwargs)
    for point in "ABCD":
        assert band.at[point, "cop_p10"] <= earlier_map[point] <= band.at[point, "cop_p90"], point
    assert (
        "run in the present model with the same 15 W, it reads "
        f"{' / '.join(f'{earlier_map[p]:.2f}' for p in 'ABCD')}, inside the band at all four points"
    ) in text


def test_trajectory_points_below_the_rotary_data(page: str, figure, trajectory: pd.DataFrame) -> None:
    """'Three of the four points lie below the speed at which the rotary data start (30 rev/s): B runs at 26 rev/s
    and C and D at the 15 rev/s speed floor, and D at a pressure ratio of 1.6, below the lowest in the data (1.9).'"""
    pr_floor, speed_floor = _rotary_data_edge()
    model = figure._model()
    results = {
        point: figure.solve_at_leaving_water(
            model, row.t_outdoor_C, row.lwt_C, row.q_required_W, figure.WATER_FLOW_KG_S
        )[0]
        for point, row in trajectory.iterrows()
    }
    assert [point for point, r in results.items() if r["efficiency_extrapolated"]] == ["B", "C", "D"]
    assert trajectory.rps["A"] > speed_floor > trajectory.rps["B"] > model.rps_min
    assert trajectory.rps["C"] == pytest.approx(model.rps_min) and trajectory.rps["D"] == pytest.approx(model.rps_min)
    assert [point for point, r in results.items() if r["pr_cmp [-]"] < pr_floor] == ["D"]
    expected = (
        f"Three of the four points lie below the speed at which the rotary data start ({speed_floor:.0f} rev/s): B "
        f"runs at {trajectory.rps['B']:.0f} rev/s and C and D at the {model.rps_min:.0f} rev/s speed floor, and D at "
        f"a pressure ratio of {results['D']['pr_cmp [-]']:.1f}, below the lowest in the data ({pr_floor:.1f})"
    )
    assert expected in _flat(page), f"{expected!r}. {HINT}."


def test_trajectory_frost_paragraph(
    page: str, figure, trajectory: pd.DataFrame, band: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'Without the frost and defrost adjustment the same trajectory reads 3.37 at A, above the 90th percentile, and
    4.64 at B. With the EnergyPlus adjustment applied in full at every load ... it reads 2.87 at A and 3.77 at B,
    below the 10th percentile at B: the unit at B delivers 3.5 kW frost-free, 0.42 of what the adjustment's
    coil-temperature correlation is written for ... at A the unit is at 0.94 of that load and keeps nearly the whole
    correction, at B a little over a quarter of it', and the callout's '3.46 kW frost-free'."""
    from tmhp.defrost import DefrostModel

    settings = dict(figure.MODEL_SETTINGS)

    def read(defrost) -> pd.Series:
        monkeypatch.setattr(figure, "MODEL_SETTINGS", {**settings, "defrost": defrost})
        return pd.DataFrame(figure.trajectory_en14825()).set_index("point").cop

    free, full = read(False), read(DefrostModel(load_weighted=False))
    text = _flat(page)
    assert free["A"] > band.at["A", "cop_p90"] and band.at["B", "cop_p10"] < free["B"] < band.at["B", "cop_p90"]
    assert (
        f"the same trajectory reads {free['A']:.2f} at A, above the 90th percentile, and {free['B']:.2f} at B"
    ) in text
    assert band.at["A", "cop_p10"] < full["A"] and full["B"] < band.at["B", "cop_p10"]
    assert f"it reads {full['A']:.2f} at A and {full['B']:.2f} at B, below the 10th percentile at B" in text
    # The load on the coil at A and B, and how much of the full correction the default keeps there.
    monkeypatch.setattr(figure, "MODEL_SETTINGS", settings)
    model = figure._model()
    ratio = {}
    for point in ("A", "B"):
        row = trajectory.loc[point]
        result, _ = figure.solve_at_leaving_water(
            model, row.t_outdoor_C, row.lwt_C, row.q_required_W, figure.WATER_FLOW_KG_S
        )
        ratio[point] = (
            result["Q_ref_tank_frost_free [W]"],
            DefrostModel.load_ratio(result["Q_ref_tank_frost_free [W]"], figure.NOMINAL_W, row.t_outdoor_C),
        )
    assert (
        f"the unit at B delivers {ratio['B'][0] / 1000.0:.1f} kW frost-free, {ratio['B'][1]:.2f} of what the "
        "adjustment's coil-temperature correlation is written for"
    ) in text
    assert f"at A the unit is at {ratio['A'][1]:.2f} of that load and keeps nearly the whole correction" in text
    kept = {p: (free[p] - trajectory.cop[p]) / (free[p] - full[p]) for p in ("A", "B")}
    assert kept["A"] > 0.9 and 0.25 < kept["B"] < 0.30, kept
    assert "at B a little over a quarter of it" in text
    assert f"a machine that delivers {ratio['B'][0] / 1000.0:.2f} kW frost-free" in text


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
    table = _section(page, "3.5 kW as a share of the maximum", "Two of the six curves")
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
    """'Two of the six curves have no peak above the speed floor ... Heating at +7 °C is flat at its low end ... the
    other three peak above the floor, shallowly', the three numbers it gives for the shallow peaks, and the
    comparison with air-to-water.

    With the earlier speed shape the +7 °C heating curve's highest COP was within 0.02 % of the floor value and the
    page counted it with the two cooling curves ("three ... have no peak above the speed floor"). It is now 0.14 %
    above the floor value, at the first request above the floor, and the page says so instead; the bound below is
    the size of that flat end (under 0.2 %), against more than 1 % for the three curves the page calls peaked.
    """
    c = {(duty, t): _a2a_curve(ashp, duty, t) for duty, t, _ in A2A_CONDITIONS}
    text = _flat(page)
    # No interior peak: both warm cooling curves rise at every step. Heating +7 has a flat end.
    assert c["cooling", 35.0]["rises_every_step"] and c["cooling", 30.0]["rises_every_step"]
    assert c["cooling", 35.0]["peak_over_floor_pct"] == 0.0 and c["cooling", 30.0]["peak_over_floor_pct"] == 0.0
    flat_end = c["heating", 7.0]
    assert 0.0 < flat_end["peak_over_floor_pct"] < 0.2 and flat_end["peak_rps"] < 16.0
    rows = ashp[(ashp.duty == "heating") & (ashp.t_outdoor_C == 7.0) & ashp.capacity_clamped.isna()]
    assert (
        rows.loc[rows.cop_sys.idxmax()].q_delivered_W == rows.q_delivered_W.min()
    )  # the first request above the floor
    assert (
        "Two of the six curves have no peak above the speed floor: cooling at 35 °C and 30 °C rise at every step as "
        "the load falls, all the way to the minimum capacity. Heating at +7 °C is flat at its low end: its highest "
        f"COP is one request above the floor, at {flat_end['peak_rps']:.0f} rev/s, and "
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


def test_air_to_air_low_end_is_outside_the_rotary_data(page: str, ashp: pd.DataFrame) -> None:
    """The paragraph under the air-to-air prose: where each curve leaves the rotary data, and the pressure-ratio floor.

    'In heating the compressor is below 30 rev/s for every request at or below 26 %, 28 % and 29 % of the maximum
    ... In cooling the pressure ratio ... is under 1.94 for every request at or below 40 % of the maximum at 35 °C,
    51 % at 30 °C and 60 % at 25 °C, where the nameplate duty itself runs at 1.81 (2.35 and 2.07 at 35 °C and
    30 °C). At 30 °C and 25 °C the low end of the curve sits on the model's cycle pressure-ratio floor
    (PR_cycle_min = 1.5), from 28 % and 38 % of the maximum down, and the peak of the 25 °C curve lies inside that
    stretch.' The sweep file holds no pressure ratio, so every row of the six curves is solved again here.
    """
    from validation.analysis import ashp_trend as at

    from tmhp import AirSourceHeatPump

    pr_edge, speed_edge = _rotary_data_edge()
    model = AirSourceHeatPump(hp_capacity=A2A_NAMEPLATE_W, ref="R32", **at.SWEEP_SETTINGS)
    assert model.PR_cycle_min == 1.5
    curves = {}
    for duty in at.DUTIES:
        for t_outdoor in duty.sweep_outdoor_C:
            rows = ashp[(ashp.duty == duty.name) & (ashp.t_outdoor_C == t_outdoor)].sort_values(
                "plr_nameplate", ascending=False
            )
            solved = []
            for row in rows.itertuples():
                result = model.analyze_steady(
                    Q_r_iu=duty.sign * row.plr_nameplate * A2A_NAMEPLATE_W,
                    T0=t_outdoor,
                    T_a_room=duty.t_room_C,
                    return_dict=True,
                    verbose=False,
                )
                assert result["cop_sys [-]"] == pytest.approx(row.cop_sys, rel=1e-6)
                solved.append(
                    {
                        "plr": row.plr_nameplate,
                        "of_max": row.delivered_fraction_of_max * 100.0,
                        "rps": result["cmp_rpm [rpm]"] / 60.0,
                        "pr": result["pr_cmp [-]"],
                        "cop": row.cop_sys,
                        "outside": bool(result["efficiency_extrapolated"]),
                    }
                )
            curve = pd.DataFrame(solved)
            # Once a curve has left the data it stays outside down to the floor.
            first = int(curve.outside.to_numpy().argmax())
            assert curve.outside.any() and curve.outside.iloc[first:].all() and not curve.outside.iloc[:first].any()
            curves[duty.name, t_outdoor] = curve, curve.iloc[first]
    text = _flat(page)
    assert f"start at {speed_edge:.0f} rev/s and a pressure ratio of {pr_edge:.2f}" in text
    # Heating leaves the data in speed, with the pressure ratio still inside.
    heating = [curves["heating", t][1] for t in (-7.0, 2.0, 7.0)]
    assert all(edge.rps < speed_edge and edge.pr > pr_edge for edge in heating)
    assert (
        f"In heating the compressor is below {speed_edge:.0f} rev/s for every request at or below "
        f"{heating[0].of_max:.0f} %, {heating[1].of_max:.0f} % and {heating[2].of_max:.0f} % of the maximum"
    ) in text
    # Cooling leaves it in pressure ratio, at 30 and 25 °C well above 30 rev/s.
    cooling = {t: curves["cooling", t] for t in (35.0, 30.0, 25.0)}
    assert all(edge.pr < pr_edge for _, edge in cooling.values())
    assert cooling[30.0][1].rps > speed_edge and cooling[25.0][1].rps > speed_edge

    def at_nameplate(t_outdoor: float) -> float:
        curve = cooling[t_outdoor][0]
        return float(curve[curve.plr == 1.0].pr.iloc[0])

    assert at_nameplate(25.0) < pr_edge < at_nameplate(30.0) < at_nameplate(35.0)
    assert (
        f"it is under {pr_edge:.2f} for every request at or below {cooling[35.0][1].of_max:.0f} % of the maximum at "
        f"35 °C, {cooling[30.0][1].of_max:.0f} % at 30 °C and {cooling[25.0][1].of_max:.0f} % at 25 °C, where the "
        f"nameplate duty itself runs at {at_nameplate(25.0):.2f} ({at_nameplate(35.0):.2f} and "
        f"{at_nameplate(30.0):.2f} at 35 °C and 30 °C)"
    ) in text
    # The cycle pressure-ratio floor: not reached at 35 °C, the low end of the other two.
    on_floor = {t: curve[(curve.pr - model.PR_cycle_min).abs() < 1e-6] for t, (curve, _) in cooling.items()}
    assert on_floor[35.0].empty and cooling[35.0][0].pr.min() > model.PR_cycle_min
    for t_outdoor in (30.0, 25.0):
        curve = cooling[t_outdoor][0]
        assert (curve[curve.of_max <= on_floor[t_outdoor].of_max.max()].pr - model.PR_cycle_min).abs().max() < 1e-6
    peak = cooling[25.0][0].loc[cooling[25.0][0].cop.idxmax()]
    assert peak.pr == pytest.approx(model.PR_cycle_min) and peak.of_max < on_floor[25.0].of_max.max()
    assert (
        f"(``PR_cycle_min = {model.PR_cycle_min:.1f}``), from {on_floor[30.0].of_max.max():.0f} % and "
        f"{on_floor[25.0].of_max.max():.0f} % of the maximum down, and the peak of the 25 °C curve lies inside that "
        "stretch"
    ) in text


# ---------------------------------------------------------------------------
# air-to-air EN 14825 trajectory: prose -> CSV, and the comparison machines rerun
# ---------------------------------------------------------------------------
A2A_DATA = REPO_ROOT / "validation" / "data"
A2A_CASE = ["capacity_kW", "refrigerant", "oversizing"]


def _signed(pct: float) -> str:
    """'+7 %' / '-36 %' as the level columns of the air-to-air table print (ASCII minus, see ``_flat``)."""
    return f"{pct:+.0f} %"


def _cooling_steps(points: pd.DataFrame) -> pd.DataFrame:
    """Per cooling case: the COP at A, C and D, the C -> D step in percent, and the inputs and speeds there."""
    wide = points[points.duty == "cooling"].pivot_table(
        index=A2A_CASE, columns="point", values=["cop_sys", "e_tot_W", "rps"], sort=False
    )
    out = pd.DataFrame(
        {
            "cop_A": wide["cop_sys"]["A"],
            "cop_C": wide["cop_sys"]["C"],
            "cop_D": wide["cop_sys"]["D"],
            "e_A": wide["e_tot_W"]["A"],
            "e_C": wide["e_tot_W"]["C"],
            "e_D": wide["e_tot_W"]["D"],
            "rps_C": wide["rps"]["C"],
            "rps_D": wide["rps"]["D"],
        }
    )
    out["step_pct"] = (out.cop_D / out.cop_C - 1.0) * 100.0
    return out


def test_air_to_air_trajectory_table_and_prose_quote_the_study(page: str) -> None:
    """The air-to-air EN 14825 section against ``ashp_en14825_verdict.csv``, ``ashp_en14825_points.csv`` and the
    declared records: the counts and levels of the table, which cooling cases fail the shape check and by how much,
    what the declared records do at the same step, and the level statement of the 'known and not fixed' note."""
    verdict = pd.read_csv(A2A_DATA / "ashp_en14825_verdict.csv")
    points = pd.read_csv(A2A_DATA / "ashp_en14825_points.csv")
    declared = pd.read_csv(A2A_DATA / "keymark_a2a_declared.csv")
    summary = pd.read_csv(A2A_DATA / "keymark_a2a_summary.csv")
    text = _flat(page)
    table = _section(page, "Gradient inside the declared range", "The level is the median over the 15 cases")
    for duty, label in (("heating", "Heating"), ("cooling", "Cooling")):
        rows = verdict[verdict.duty == duty]
        assert len(rows) == 15
        levels = [(rows[f"level_{p}"].median() - 1.0) * 100.0 for p in "ABCD"]
        cells = "- " + _list_table_row(table, label).strip() + " -"
        for expected in (
            f"- {int(rows.C1_shape.sum())} of 15 - {int(rows.C3_gradient_modulating.sum())} of 15 -",
            "- " + " - ".join(_signed(level) for level in levels) + " -",
        ):
            assert expected in cells, f"air-to-air trajectory {label}: {cells!r} lacks {expected!r}. {HINT}."
    declared_cooling = declared[declared.application == "cooling"]
    assert (
        f"{len(declared_cooling)} declared Keymark records from {declared.manufacturer.nunique()} manufacturers" in text
    )
    # Heating: every case rises; D on the speed floor in all 15 and C in 13; above the declared median everywhere.
    heating = verdict[verdict.duty == "heating"]
    assert heating.C1_shape.all() and (heating[[f"level_{p}" for p in "ABCD"]].median() > 1.0).all()
    clamped = points[(points.duty == "heating") & (points.capacity_clamped == "min")].groupby("point").size()
    assert clamped.to_dict() == {"C": 13, "D": 15}
    assert "D is on the 15 rev/s speed floor in all 15 cases and C in 13" in text
    # Cooling: the failing step is C -> D in every failing case; A -> B -> C rises in all 15.
    cooling = _cooling_steps(points)
    wide = points[points.duty == "cooling"].pivot_table(index=A2A_CASE, columns="point", values="cop_sys", sort=False)
    assert (wide.A < wide.B).all() and (wide.B < wide.C).all()
    fails = cooling[cooling.step_pct < 0.0]
    passes = cooling[cooling.step_pct >= 0.0]
    shape = verdict[verdict.duty == "cooling"].set_index(A2A_CASE).C1_shape
    assert set(fails.index) == set(shape[~shape].index) and len(fails) == 8
    assert (cooling.rps_D == 15.0).all()
    d_over_c = (declared_cooling.cop_D / declared_cooling.cop_C - 1.0) * 100.0
    assert (d_over_c > 0.0).all()
    expected = (
        f"In cooling the shape check fails in {len(fails)} of the 15 cases, and at the same step in each: from C "
        f"(25 °C) to D (20 °C) the modelled EER falls, by {-fails.step_pct.max():.1f} to {-fails.step_pct.min():.1f} %, "
        f"where every one of the {len(d_over_c)} declared records rises (by {d_over_c.min():.0f} to "
        f"{d_over_c.max():.0f} %, median {d_over_c.median():.0f} %)"
    )
    assert expected in text, f"{expected!r}. {HINT}."
    assert set(fails.index) == {
        (2.5, "R32", 0.8),
        (2.5, "R32", 0.9),
        (2.5, "R32", 1.0),
        (3.5, "R32", 0.9),
        (3.5, "R32", 1.0),
        (2.64, "R410A", 0.9),
        (2.64, "R410A", 1.0),
        (3.52, "R410A", 1.0),
    }
    assert (
        "The eight cases are the 2.5 kW R32 unit at all three design duties, the 3.5 kW R32 and the 2.64 kW R410A "
        "units at design duties of 0.90 and 1.00 of the nameplate, and the 3.52 kW R410A unit at 1.00; in the other "
        f"seven D is {passes.step_pct.min():.1f} to {passes.step_pct.max():.1f} % above C"
    ) in text
    # The control power's share of the input at D, C and A, and C's speed.
    share = {p: 12.0 / cooling[f"e_{p}"] * 100.0 for p in "ACD"}
    assert (cooling.e_D < cooling.e_C).all()
    assert (
        f"D on the 15 rev/s speed floor and C at {cooling.rps_C.min():.1f} to {cooling.rps_C.max():.1f} rev/s" in text
    )
    assert (
        f"so the same 12 W is {share['D'].min():.0f} to {share['D'].max():.0f} % of it against "
        f"{share['C'].min():.0f} to {share['C'].max():.0f} % at C and {share['A'].min():.0f} to "
        f"{share['A'].max():.0f} % at A"
    ) in text
    # The 'known and not fixed' note.
    rows = verdict[verdict.duty == "cooling"]
    level_a, level_d = (rows.level_A - 1.0) * 100.0, (rows.level_D - 1.0) * 100.0
    medians = summary[summary.application == "cooling"].set_index("point").cop_median
    gain = rows.gradient_A_to_D
    expected = (
        f"the modelled cooling EER is {level_a.median():.0f} % above the declared median at A (35 °C, full design "
        f"load; the 15 cases range from {level_a.min():+.0f} to {level_a.max():+.0f} %) and "
        f"{-level_d.median():.0f} % below it at D (20 °C, 21 % load; {-level_d.max():.0f} to {-level_d.min():.0f} % "
        f"below). The model gains a factor of {gain.min():.1f} to {gain.max():.1f} from A to D where the declared "
        f"medians gain {medians['D'] / medians['A']:.1f}"
    )
    assert expected in text, f"{expected!r}. {HINT}."
    # 'The gradient check passes because it is scored over the points the machine modulates to (A to C)'.
    assert (rows.modulating_to == "C").all() and rows.C3_gradient_modulating.all()


def test_air_to_air_cooling_step_is_the_control_power(page: str) -> None:
    """Which of the two changes turned the C -> D step in cooling, with the comparison machines built explicitly.

    'With the earlier defaults (shao_informed, E_ctrl=0.0) two cases failed, by 0.3 and 1.0 %. ... the present shape
    without the control power passes in 14 of 15 cases, and the other shape with the 12 W in 6', 'In all 15 cases
    both run on the cycle pressure-ratio floor (PR_cycle_min = 1.5) ... without the control power their EER differs
    by -0.2 to +4.0 %'. Each machine is run at the C and D points of the study's 15 cooling cases.
    """
    from validation.analysis import ashp_trend as at

    from tmhp import AirSourceHeatPump

    before = _machine_of_before()
    machines = {
        "default": {},
        "no control power": {"E_ctrl": 0.0},
        "other shape": {"compressor_type": before["compressor_type"]},
        "before": before,
    }
    duty = at.COOLING
    (c_point,) = [p for p in duty.points if p[0] == "C"]
    (d_point,) = [p for p in duty.points if p[0] == "D"]
    steps: dict[str, list[float]] = {name: [] for name in machines}
    at_d: dict[str, list[float]] = {name: [] for name in machines}
    for capacity, refrigerant in at.CONFIGURATIONS:
        for oversizing in duty.oversizing:
            for name, kwargs in machines.items():
                model = AirSourceHeatPump(hp_capacity=capacity, ref=refrigerant, **at.TRAJECTORY_SETTINGS, **kwargs)
                cop = []
                for _, t_outdoor, plr in (c_point, d_point):
                    result = model.analyze_steady(
                        Q_r_iu=duty.sign * capacity * oversizing * plr,
                        T0=t_outdoor,
                        T_a_room=duty.t_room_C,
                        return_dict=True,
                        verbose=False,
                    )
                    assert result["failure_reason"] == "none"
                    if name == "default":
                        assert model.PR_cycle_min == 1.5
                        assert result["pr_cmp [-]"] == pytest.approx(model.PR_cycle_min, abs=1e-6)
                    cop.append(result["cop_sys [-]"])
                steps[name].append((cop[1] / cop[0] - 1.0) * 100.0)
                at_d[name].append(cop[1])
    rising = {name: sum(step >= 0.0 for step in values) for name, values in steps.items()}
    committed = _cooling_steps(pd.read_csv(A2A_DATA / "ashp_en14825_points.csv"))
    assert steps["default"] == pytest.approx(committed.step_pct.to_list(), abs=1e-4)
    assert rising["default"] == 7
    text = _flat(page)
    failed_before = sorted(-step for step in steps["before"] if step < 0.0)
    assert len(failed_before) == 2
    assert (
        "With the earlier defaults (``shao_informed``, ``E_ctrl=0.0``) two cases failed, by "
        f"{failed_before[0]:.1f} and {failed_before[1]:.1f} %"
    ) in text
    assert (
        f"the present shape without the control power passes in {rising['no control power']} of 15 cases, and the "
        f"other shape with the 12 W in {rising['other shape']}"
    ) in text
    assert "In all 15 cases both run on the cycle pressure-ratio floor (``PR_cycle_min = 1.5``)" in text
    without = steps["no control power"]
    assert f"without the control power their EER differs by {min(without):+.1f} to {max(without):+.1f} %" in text
    # The 'known and not fixed' note: how much of the deficit at D is the control power's.
    summary = pd.read_csv(A2A_DATA / "keymark_a2a_summary.csv")
    declared_d = float(summary[(summary.application == "cooling") & (summary.point == "D")].cop_median.iloc[0])
    deficit = {name: sorted((1.0 - cop / declared_d) * 100.0 for cop in values) for name, values in at_d.items()}
    assert (
        f"Without the control power the model is still {deficit['no control power'][0]:.0f} to "
        f"{deficit['no control power'][-1]:.0f} % below the declared median at D"
    ) in text
    share = 1.0 - deficit["no control power"][7] / deficit["default"][7]  # of the median case
    assert 0.2 < share < 0.3 and "so the 12 W accounts for about a quarter of the deficit" in text
