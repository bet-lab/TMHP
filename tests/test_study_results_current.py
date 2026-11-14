"""Committed one-off studies and docs figures must still show what the model does.

``tests/test_validation_results_current.py`` keeps the adopted parity results
honest. The other committed outputs of a model run -- the study CSVs under
``validation/data/`` and ``validation/results/<study>/`` and the figures under
``docs/source/_static/`` -- were each made by running a script once, and went
stale silently when a default moved: below the minimum capacity the library
now reports the mean over an on-off period, the condensing-temperature cap is
on, frost and defrost are on (#134, #136, #154), and studies of the running
cycle drawn with the defaults turned into something else.

Each study now chooses its model settings explicitly and writes a provenance
record (:mod:`validation.study_provenance`). Here a few points of each cheap
study are recomputed and compared with the committed numbers (``rel=1e-6``,
as the parity check), so a change to the model that moves them fails until
the study is rerun. Studies that are expensive, or dated on purpose, carry a
record that says so; the last test checks that every one has a record.
"""

from __future__ import annotations

import importlib
import io
import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA = REPO_ROOT / "validation" / "data"
RESULTS = REPO_ROOT / "validation" / "results"
FIGURE_RECORDS = REPO_ROOT / "docs" / "source" / "_static" / "provenance"

HINT = "모델 동작이 바뀌었다. 기록에 적힌 명령으로 이 산출물을 다시 만들어라"


def _same(a, b) -> bool:
    if a is None or b is None or (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b)):
        return (a is None or (isinstance(a, float) and math.isnan(a))) and (
            b is None or (isinstance(b, float) and math.isnan(b))
        )
    if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, str) or isinstance(b, str):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == pytest.approx(b, rel=1e-6, abs=1e-9)
    return a == b


def _frame_mismatches(fresh: pd.DataFrame, committed: pd.DataFrame, label: str) -> list[str]:
    """Column-by-column comparison of two frames with the same rows, through a CSV round trip."""
    fresh = pd.read_csv(io.StringIO(fresh.to_csv(index=False)), float_precision="round_trip")
    committed = pd.read_csv(io.StringIO(committed.to_csv(index=False)), float_precision="round_trip")
    assert list(fresh.columns) == list(committed.columns), f"{label}: columns changed. {HINT}."
    assert len(fresh) == len(committed), f"{label}: {len(fresh)} rows now, {len(committed)} committed. {HINT}."
    out = []
    for i in range(len(fresh)):
        for column in fresh.columns:
            new, old = fresh.at[i, column], committed.at[i, column]
            if pd.isna(new) and pd.isna(old):
                continue
            if not _same(new, old):
                out.append(f"{label} row {i} {column}: committed {old!r}, now {new!r}")
    return out


# ---------------------------------------------------------------------------
# Gate A: computed from the committed unit CSVs alone
# ---------------------------------------------------------------------------
def test_gate_a_is_computed_from_the_committed_results() -> None:
    from validation.parity import rated_point

    points = rated_point.load_points()
    mismatches = []
    for name, fresh in (
        ("rated_points.csv", rated_point.rated_rows(points)),
        ("residual_bins.csv", rated_point.residual_bins(points)),
    ):
        committed = pd.read_csv(RESULTS / "gate_a" / name, float_precision="round_trip")
        mismatches += _frame_mismatches(fresh, committed, f"gate_a/{name}")
    summary = json.loads((RESULTS / "gate_a" / "gate_a_summary.json").read_text(encoding="utf-8"))
    headline = rated_point.headline(points).to_dict(orient="records")
    assert summary["coefficient_version"] == points.coefficient_version.iat[0]
    for got, want in zip(headline, summary["headline"], strict=True):
        mismatches += [
            f"headline {k}: committed {want[k]!r}, now {v!r}" for k, v in got.items() if not _same(v, want[k])
        ]
    assert not mismatches, "validation/results/gate_a is not the Gate A of the committed results; run " + (
        "`python -m validation.parity.rated_point` (scripts/regen-validation.sh does).\n  "
        + "\n  ".join(mismatches[:15])
    )


# ---------------------------------------------------------------------------
# Studies: a sample of each, rerun
# ---------------------------------------------------------------------------
def test_fixed_boundary_sweep_reproduces() -> None:
    """Rated, modulating and sub-floor rows of one air-to-water and one air-to-air case."""
    from validation.fixed_boundary_plr import sweep

    mismatches = []
    for name, run, case, fractions in (
        ("ashpb_sweep.csv", sweep.sweep_ashpb, sweep.ASHPB_CASES[0], (1.0, 0.6, 0.2)),
        ("ashp_sweep.csv", sweep.sweep_ashp, sweep.ASHP_CASES[0], (1.0, 0.6, 0.12)),
    ):
        fresh = run(cases=(case,), fractions=fractions)
        committed = pd.read_csv(DATA / "fixed_boundary_plr" / name, float_precision="round_trip")
        keys = ["refrigerant", "t_outdoor_C", "t_sink_C", "plr_request"]
        committed = fresh[keys].merge(committed, on=keys, how="left")[committed.columns]
        mismatches += _frame_mismatches(fresh, committed, name)
    assert not mismatches, f"validation/data/fixed_boundary_plr: {HINT}.\n  " + "\n  ".join(mismatches[:15])


def test_fixed_boundary_sub_floor_rows_are_the_running_cycle() -> None:
    """The study asks for the cycle, so a request below the floor is over-delivered, not averaged."""
    committed = pd.read_csv(DATA / "fixed_boundary_plr" / "ashpb_sweep.csv")
    floor = committed[committed.capacity_clamped == "min"]
    assert len(floor) > 0
    assert (floor.q_delivered_W > floor.q_request_W * 1.01).all()


def test_ashp_trend_sample_reproduces() -> None:
    from validation.analysis import ashp_trend as at

    from tmhp import AirSourceHeatPump

    capacity, ref = at.CONFIGURATIONS[1]
    mismatches = []
    sweep = pd.read_csv(DATA / "ashp_part_load_sweep.csv")
    model = AirSourceHeatPump(hp_capacity=capacity, ref=ref, **at.SWEEP_SETTINGS)
    for fraction in (1.0, 0.4):
        fresh = at._run(model, at.HEATING, capacity * fraction, 7.0)
        row = sweep[
            (sweep.duty == "heating")
            & (sweep.capacity_kW == capacity / 1000.0)
            & (sweep.refrigerant == ref)
            & (sweep.t_outdoor_C == 7.0)
            & (sweep.plr_nameplate == fraction)
        ]
        assert fresh is not None and len(row) == 1
        mismatches += [
            f"sweep {fraction} {k}: committed {row[k].iat[0]!r}, now {v!r}"
            for k, v in fresh.items()
            if not _same(v, row[k].iat[0])
        ]
    points = pd.read_csv(DATA / "ashp_en14825_points.csv")
    oversizing = at.HEATING.oversizing[1]
    model = AirSourceHeatPump(hp_capacity=capacity, ref=ref, **at.TRAJECTORY_SETTINGS)
    for label, t_outdoor, plr in at.HEATING.points[:2]:
        fresh = at._run(model, at.HEATING, capacity * oversizing * plr, t_outdoor)
        row = points[
            (points.duty == "heating")
            & (points.capacity_kW == capacity / 1000.0)
            & (points.refrigerant == ref)
            & (points.oversizing == oversizing)
            & (points.point == label)
        ]
        assert fresh is not None and len(row) == 1
        mismatches += [
            f"EN 14825 {label} {k}: committed {row[k].iat[0]!r}, now {v!r}"
            for k, v in fresh.items()
            if not _same(v, row[k].iat[0])
        ]
    assert not mismatches, f"validation/data/ashp_*: {HINT}.\n  " + "\n  ".join(mismatches[:15])


def test_hx_ua_sensitivity_default_rows_reproduce() -> None:
    from validation.hx_ua_sensitivity import simulate as hx

    committed = pd.read_csv(RESULTS / "hx_ua_sensitivity_once" / hx.CSV_NAME, float_precision="round_trip")
    tasks = [t for t in hx._tasks("default", None, None) if t["duty"] == "heating" and t["plr_request"] in (1.0, 0.2)]
    fresh = pd.DataFrame([hx.run_point(t) for t in tasks])
    keys = ["variant", "duty", "plr_request"]
    committed = fresh[keys].merge(committed, on=keys, how="left")[committed.columns]
    mismatches = _frame_mismatches(fresh, committed, "hx_ua_sensitivity_once")
    assert not mismatches, f"validation/results/hx_ua_sensitivity_once: {HINT}.\n  " + "\n  ".join(mismatches[:15])


def test_refrigerant_only_sample_reproduces() -> None:
    from validation.refrigerant_only_plr_cop import simulate as ro

    committed = pd.read_csv(RESULTS / "refrigerant_only_plr_cop" / ro.CSV_NAME, float_precision="round_trip")
    case, klass, cap, duty, t_out, t_sink = ro.CASES[2]
    v_cmp = ro.displacement(klass, cap, ro.REFERENCE_REFRIGERANT)
    tasks = [
        {
            "case": case,
            "model_class": klass,
            "capacity_W": cap,
            "duty": duty,
            "t_outdoor_C": t_out,
            "t_sink_C": t_sink,
            "refrigerant": "R290",
            "pass": "fixed_disp",
            "V_cmp_ref": v_cmp,
            "plr_request": f,
        }
        for f in (1.0, 0.6)
    ]
    fresh = pd.DataFrame([ro.run_point(t) for t in tasks])
    keys = ["pass", "case", "refrigerant", "PLR_requested"]
    committed = fresh[keys].merge(committed, on=keys, how="left")[committed.columns]
    mismatches = _frame_mismatches(fresh, committed, "refrigerant_only_plr_cop")
    assert not mismatches, f"validation/results/refrigerant_only_plr_cop: {HINT}.\n  " + "\n  ".join(mismatches[:15])


# ---------------------------------------------------------------------------
# Docs figures: the recorded values, recomputed
# ---------------------------------------------------------------------------
def _figure_record(name: str) -> dict:
    path = FIGURE_RECORDS / f"{name}.json"
    assert path.exists(), f"{name}: no provenance record at {path.relative_to(REPO_ROOT)}"
    return json.loads(path.read_text(encoding="utf-8"))


def _figure_script(name: str):
    folder = str(REPO_ROOT / "scripts" / "visualization")
    if folder not in sys.path:
        sys.path.insert(0, folder)
    return importlib.import_module(name)


def _compare_values(fresh: dict, recorded: dict, label: str) -> list[str]:
    return [f"{label} {k}: recorded {recorded[k]!r}, now {v!r}" for k, v in fresh.items() if not _same(v, recorded[k])]


def test_cycle_figures_reproduce() -> None:
    """The two R32 cycle diagrams (one operating point) and the refrigerant comparison."""
    from tmhp import AirSourceHeatPumpBoiler

    mollier = _figure_script("mollier_cycle_R32")
    recorded = _figure_record("mollier_cycle_R32")["data"]
    assert _figure_record("mollier_ts_R32")["data"] == recorded, "the T-s diagram shows another state than the P-h one"
    res = AirSourceHeatPumpBoiler(ref=mollier.REF, **mollier.CYCLE_SETTINGS).analyze_steady(
        T_tank_w=mollier.T_TANK_W, T0=mollier.T0, Q_ref_tank=mollier.Q_COND
    )
    mismatches = _compare_values({k: res.get(k) for k in recorded}, recorded, "mollier R32")

    compare = _figure_script("refrigerant_compare_ph")
    recorded = _figure_record("refrigerant_compare_ph")["data"]
    for ref in compare.REFRIGERANTS:
        res = AirSourceHeatPumpBoiler(ref=ref, **compare.CYCLE_SETTINGS).analyze_steady(
            T_tank_w=compare.T_TANK_W, T0=compare.T0, Q_ref_tank=compare.Q_COND
        )
        mismatches += _compare_values({k: res.get(k) for k in recorded[ref]}, recorded[ref], ref)
    assert not mismatches, f"docs/source/_static cycle figures: {HINT}.\n  " + "\n  ".join(mismatches)


@pytest.mark.parametrize("name", ["dynamic_24h_timeseries", "pv_ess_energy_balance"])
def test_dynamic_docs_figures_reproduce(name: str) -> None:
    """The two 24 h figures show the documented calls with the defaults; their daily totals must hold."""
    fig = _figure_script(name)
    out = fig.simulate()
    df = out[1] if isinstance(out, tuple) else out
    recorded = _figure_record(name)["data"]
    mismatches = _compare_values(fig.daily_totals(df), recorded, name)
    assert not mismatches, f"docs/source/_static/{name}.svg: {HINT}.\n  " + "\n  ".join(mismatches)


def test_source_cop_timeseries_figure_reproduces() -> None:
    """The ASHP/GSHP figure: the ground-loop run in full, the hourly COP at a sample of hours."""
    import numpy as np

    fig = _figure_script("source_cop_timeseries")
    recorded = _figure_record("source_cop_timeseries")["data"]
    t_h, t0, t_loop = fig.source_temperatures()
    sample = [0, 7, 18, 30, 42, 66]  # night, a draw dip, afternoon, the coldest night, the frost band, the warmest
    idx = fig.hour_index(np.array(sample), len(t_h))
    cop_a, cop_g = fig.hourly_cop(t0[idx], t_loop[idx])
    mismatches = []
    for j, h in enumerate(sample):
        i = recorded["hours"].index(h)
        fresh = {
            "T0_at_hour_C": float(t0[idx[j]]),
            "T_loop_at_hour_C": float(t_loop[idx[j]]),
            "cop_ashp": float(cop_a[j]),
            "cop_gshp": float(cop_g[j]),
        }
        mismatches += _compare_values(fresh, {k: recorded[k][i] for k in fresh}, f"source_cop_timeseries h={h}")
    assert not mismatches, f"docs/source/_static/source_cop_timeseries.svg: {HINT}.\n  " + "\n  ".join(mismatches)


# ---------------------------------------------------------------------------
# Every committed study says what it is
# ---------------------------------------------------------------------------
#: Study outputs made by running the model, and the record each one carries.
STUDY_RECORDS = {
    "validation/data/fixed_boundary_plr/": "validation/data/provenance/fixed_boundary_plr.json",
    "validation/data/ashp_part_load_sweep.csv": "validation/data/provenance/ashp_trend.json",
    "validation/data/refrigerant_nameplate_sweep.csv": "validation/data/provenance/refrigerant_diagnosis.json",
    "validation/data/final_report/": "validation/data/provenance/final_report.json",
    "validation/data/power_cop/": "validation/data/provenance/power_cop.json",
    "validation/data/professor_summary/": "validation/data/provenance/professor_summary.json",
    "validation/data/compressor_maps/": "validation/data/provenance/compressor_maps.json",
    "validation/results/hx_ua_sensitivity_once/": "validation/results/hx_ua_sensitivity_once/provenance.json",
    "validation/results/refrigerant_only_plr_cop/": "validation/results/refrigerant_only_plr_cop/provenance.json",
    "validation/results/refrigerant_ua_sensitivity_once/": "validation/results/refrigerant_ua_sensitivity_once/provenance.json",
    "validation/results/low_load_fixed_power_sh_sc_once/": "validation/results/low_load_fixed_power_sh_sc_once/provenance.json",
    "validation/results/auxiliary_power_pto_proxy/": "validation/results/auxiliary_power_pto_proxy/provenance.json",
    "validation/results/fan_model_sensitivity_once/": "validation/results/fan_model_sensitivity_once/provenance.json",
    "validation/results/compressor_group_fitting_once/": "validation/results/compressor_group_fitting_once/provenance.json",
}


def test_every_study_has_a_provenance_record() -> None:
    covered = set()
    for output, record in STUDY_RECORDS.items():
        assert (REPO_ROOT / output).exists(), f"{output} is gone; drop it from STUDY_RECORDS"
        path = REPO_ROOT / record
        assert path.exists(), f"{output}: no provenance record at {record}"
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert rec.get("script") and rec.get("command") and "model_settings" in rec, f"{record}: incomplete"
        if rec.get("status", "").startswith("dated"):
            assert rec.get("why_not_regenerated"), f"{record}: a dated study says why it is not rerun"
        else:
            assert rec["dirty"] is False, f"{record}: made from uncommitted model code; regenerate from a commit"
        covered.add(output.rstrip("/").split("/")[-1])
    # A new study folder under results/ has to be listed (the adopted unit CSVs sit at the top level).
    folders = {p.name for p in RESULTS.iterdir() if p.is_dir() and p.name not in ("provenance", "gate_a")}
    assert folders <= covered, f"study folders without a provenance record: {sorted(folders - covered)}"
    figures = {p.stem for p in FIGURE_RECORDS.glob("*.json")}
    assert {"mollier_cycle_R32", "mollier_ts_R32", "refrigerant_compare_ph", "source_cop_timeseries"} <= figures
