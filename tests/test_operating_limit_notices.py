"""The caller is told when the machine does not do what it was asked (``tmhp.operating_limits``).

One warning category, :class:`tmhp.OperatingLimitWarning`:

- ``analyze_steady`` warns once about its *final* result -- a compressor that
  was asked to run and does not, or runs below the request -- and never about
  the trial solves on the way;
- ``analyze_dynamic`` says nothing per step and ends with one summary, whose
  totals are in ``df.attrs["operating_limits"]``;
- a sweep prints a handful of lines, not one per point, unless every point is
  asked for with the ``"always"`` filter.

Every test here reads results the models already return: the notices do not
change a number.
"""

from __future__ import annotations

import contextlib
import io
import math
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import tmhp
from tmhp import OperatingLimitWarning
from tmhp.dhw import DHWUnmetWarning
from tmhp.operating_limits import (
    LIMITS,
    dynamic_message,
    limit_for,
    reset_notices,
    silenced,
    summarize_operating_limits,
    warn_if_operating_limited,
)
from tmhp.simulation_summary import print_operating_limit_summary

REPO_ROOT = Path(__file__).resolve().parents[1]


def _quiet(fn, *args, **kwargs):
    """Call ``fn`` with warnings and prints swallowed."""
    with (
        warnings.catch_warnings(),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


def _notices(fn, action: str = "always"):
    """``(result, operating-limit warnings)`` of ``fn()`` under the filter ``action``."""
    with (
        warnings.catch_warnings(record=True) as caught,
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        warnings.simplefilter(action)
        result = fn()
    return result, [w for w in caught if issubclass(w.category, OperatingLimitWarning)]


@pytest.fixture(scope="module")
def machines() -> dict:
    """One machine per family and limit, built once."""
    return {
        "ASHPB": _quiet(tmhp.AirSourceHeatPumpBoiler, ref="R32"),
        "GSHPB": _quiet(tmhp.GroundSourceHeatPumpBoiler, source_fluid="water"),
        "WSHPB": _quiet(tmhp.WaterSourceHeatPumpBoiler),
        # A pressure-ratio ceiling just above the floor: no cycle for the lift asked.
        "WSHPB-pr": _quiet(
            tmhp.WaterSourceHeatPumpBoiler,
            ref="R410A",
            UA_tank=2000.0,
            UA_water=2000.0,
            PR_cycle_min=1.0,
            PR_cycle_max=2.0,
        ),
        "ASHP": _quiet(tmhp.AirSourceHeatPump),
        "GSHP": _quiet(tmhp.GroundSourceHeatPump),
        "GSHP-pr": _quiet(tmhp.GroundSourceHeatPump, ref="R32", Ts=0.0, PR_cycle_max=1.6, R_b=0.12, t_max_s=3600),
    }


# ---------------------------------------------------------------------------
# 1. the category
# ---------------------------------------------------------------------------


def test_one_public_category_covers_every_notice():
    assert issubclass(OperatingLimitWarning, UserWarning)
    assert tmhp.OperatingLimitWarning is OperatingLimitWarning and "OperatingLimitWarning" in tmhp.__all__
    # The hot-water warning keeps its name and joins the family.
    assert issubclass(DHWUnmetWarning, OperatingLimitWarning) and tmhp.DHWUnmetWarning is DHWUnmetWarning


def test_set_point_above_the_envelope_is_an_operating_limit_warning():
    # R32 condenses at most at 65 °C: a 65 °C upper set point is capped at construction.
    with pytest.warns(OperatingLimitWarning, match="T_tank_w_upper_bound=65"):
        tmhp.WaterSourceHeatPumpBoiler(ref="R32")


# ---------------------------------------------------------------------------
# 2. analyze_steady: a point that does not run
# ---------------------------------------------------------------------------

# family, call, failure_reason, what the text must say about the limit
OFF_CASES = [
    (
        "ASHPB",
        dict(T_tank_w=55.0, T0=-10.0, Q_ref_tank=8000.0),
        "discharge_temp_above_max",
        ["T_dis_max = 150 °C", "0 W of the 8000 W requested", "efficiency map of the actual compressor"],
    ),
    (
        "GSHPB",
        dict(T_tank_w=50.0, T_source=-2.0, Q_ref_tank=6000.0),
        "source_fluid_freezing",
        ["3.0 °C for water", "-4.0 °C at this point", "antifreeze mixture"],
    ),
    (
        "WSHPB",
        dict(T_tank_w=45.0, T_source=1.0, Q_ref_tank=6000.0),
        "source_fluid_freezing",
        ["3.0 °C for water", "antifreeze mixture"],
    ),
    (
        "WSHPB-pr",
        dict(T_tank_w=50.0, T_source=12.0, Q_ref_tank=8000.0, T0=15.0),
        "pr_above_max",
        ["PR_cycle_max = 2", "Reduce the temperature lift"],
    ),
    (
        "ASHP",
        dict(Q_r_iu=-6000.0, T0=-25.0),
        "discharge_temp_above_max",
        ["T_dis_max = 150 °C", "0 W of the 6000 W of heating requested"],
    ),
    (
        "GSHP-pr",
        dict(Q_r_iu=-3000.0, T0=0.0, T_a_room=20.0),
        "pr_above_max",
        ["PR_cycle_max = 1.6", "0 W of the 3000 W of heating requested"],
    ),
]


@pytest.mark.parametrize(("family", "call", "reason", "says"), OFF_CASES, ids=[f"{c[0]}-{c[2]}" for c in OFF_CASES])
def test_steady_point_that_does_not_run_is_reported_once_in_words(machines, family, call, reason, says):
    model = machines[family]
    row, notices = _notices(lambda: model.analyze_steady(**call))
    assert row["failure_reason"] == reason and not row["hp_is_on"]
    (notice,) = notices
    text = str(notice.message)
    # Who and what was asked: the call, with the numbers the caller gave.
    assert text.startswith(f"{type(model).__name__}.analyze_steady(")
    for name, value in call.items():
        assert f"{name}={value:g}" in text
    assert "the compressor does not run at this point" in text and f"failure_reason={reason!r}" in text
    # The limit in words and what removes it, from the one table.
    limit = limit_for(reason=reason)
    assert limit.meaning in text and limit.remedy in text
    for phrase in says:
        assert phrase in text
    # Nothing of the solver's internals, and no advice the reason does not support.
    for internal in ("opt_x", "opt_fun", "opt_success", "fell back", "UA_rated"):
        assert internal not in text
    # The warning points at the caller's line, not into the package.
    assert Path(notice.filename).name == Path(__file__).name


def test_frosted_request_reports_the_callers_duty_not_the_frost_free_one(machines):
    # A-10/W55: the frost mean solves the frost-free cycle at 9353 W to deliver 8000 W.
    _, (notice,) = _notices(lambda: machines["ASHPB"].analyze_steady(55.0, -10.0, 8000.0))
    text = str(notice.message)
    assert "Q_ref_tank=8000" in text and "8000 W requested" in text and "9353" not in text
    # The discharge temperature the refused cycle reached is named with its limit.
    reached = float(re.search(r"; (\d+) °C at this point", text).group(1))
    assert reached > 150.0


# ---------------------------------------------------------------------------
# 3. analyze_steady: a point that runs below the request
# ---------------------------------------------------------------------------

# family, call, capacity_binding, delivered key
LIMITED_CASES = [
    ("ASHPB", dict(T_tank_w=55.0, T0=-5.0, Q_ref_tank=15000.0), "discharge_temperature", "Q_ref_tank [W]"),
    ("GSHPB", dict(T_tank_w=35.0, T_source=5.0, Q_ref_tank=8000.0), "source_fluid_freezing", "Q_ref_tank [W]"),
    ("WSHPB", dict(T_tank_w=35.0, T_source=5.0, Q_ref_tank=8000.0), "source_fluid_freezing", "Q_ref_tank [W]"),
    ("ASHP", dict(Q_r_iu=20000.0, T0=48.0), "cycle", "Q_ref_iu [W]"),
    ("GSHP", dict(Q_r_iu=-30000.0, T0=0.0), "cycle", "Q_ref_iu [W]"),
]


@pytest.mark.parametrize(
    ("family", "call", "binding", "delivered_key"), LIMITED_CASES, ids=[f"{c[0]}-{c[2]}" for c in LIMITED_CASES]
)
def test_steady_point_that_runs_reduced_is_reported_with_request_and_delivery(
    machines, family, call, binding, delivered_key
):
    model = machines[family]
    row, notices = _notices(lambda: model.analyze_steady(**call))
    assert row["hp_is_on"] and row["failure_reason"] == "none"
    assert row["capacity_clamped"] == "max" and row["capacity_binding"] == binding
    # The trial solves at larger duties failed on the way; none of that leaks:
    # one warning, about the row returned.
    (notice,) = notices
    text = str(notice.message)
    assert "does not run" not in text and "off state" not in text
    request, delivered = abs(row["Q_request [W]"]), abs(row[delivered_key])
    assert delivered < request
    assert f"delivers {delivered:.0f} W of the {request:.0f} W" in text
    assert f"capacity_binding={binding!r}" in text
    limit = limit_for(binding=binding)
    assert limit.meaning in text and limit.remedy in text


# ---------------------------------------------------------------------------
# 4. analyze_steady: normal operation is silent
# ---------------------------------------------------------------------------

SILENT_CASES = [
    ("ASHPB", dict(T_tank_w=45.0, T0=7.0, Q_ref_tank=8000.0), None),
    ("ASHPB", dict(T_tank_w=45.0, T0=7.0, Q_ref_tank=500.0), "min"),  # on/off mean
    ("ASHPB", dict(T_tank_w=45.0, T0=7.0, Q_ref_tank=0.0), None),  # not called
    ("GSHPB", dict(T_tank_w=35.0, T_source=8.0, Q_ref_tank=8000.0), None),
    ("WSHPB", dict(T_tank_w=45.0, T_source=12.0, Q_ref_tank=8000.0), None),
    ("ASHP", dict(Q_r_iu=3000.0, T0=35.0), None),
    ("ASHP", dict(Q_r_iu=0.0, T0=35.0), None),
    ("GSHP", dict(Q_r_iu=-3000.0, T0=0.0), None),
]


@pytest.mark.parametrize(("family", "call", "clamped"), SILENT_CASES)
def test_steady_point_that_meets_its_request_does_not_warn(machines, family, call, clamped):
    row, notices = _notices(lambda: machines[family].analyze_steady(**call))
    assert row["failure_reason"] == "none" and row["capacity_clamped"] == clamped
    assert notices == []


def test_steady_filters_ignore_and_error(machines):
    model, call = machines["GSHPB"], dict(T_tank_w=50.0, T_source=-2.0, Q_ref_tank=6000.0)
    row, notices = _notices(lambda: model.analyze_steady(**call), "ignore")
    assert notices == [] and row["failure_reason"] == "source_fluid_freezing"
    with warnings.catch_warnings():
        warnings.simplefilter("error", OperatingLimitWarning)
        with pytest.raises(OperatingLimitWarning, match="source_fluid_freezing"):
            model.analyze_steady(**call)


def test_verbose_false_and_silenced_keep_the_row_and_drop_the_warning(machines):
    ashp = machines["ASHP"]
    row, notices = _notices(lambda: ashp.analyze_steady(Q_r_iu=-6000.0, T0=-25.0, verbose=False))
    assert notices == [] and row["failure_reason"] == "discharge_temp_above_max"

    def inside():
        with silenced():
            return machines["GSHPB"].analyze_steady(T_tank_w=50.0, T_source=-2.0, Q_ref_tank=6000.0)

    row, notices = _notices(inside)
    assert notices == [] and row["failure_reason"] == "source_fluid_freezing"


def test_the_undecorated_solve_does_not_warn(machines):
    # What the duty reduction and the frost mean call: the refusal is in the row only.
    solve = tmhp.AirSourceHeatPumpBoiler.analyze_steady.__wrapped__
    row, notices = _notices(lambda: solve(machines["ASHPB"], 55.0, -10.0, 8000.0))
    assert notices == [] and row["failure_reason"] == "discharge_temp_above_max"


def test_solve_paths_print_nothing_to_standard_output(machines, capsys):
    """A library does not print: what a point ran into is in its row and in the warning.

    The pressure-ratio guard used to print a ``[PR guard]`` line per call
    (with the duty of an inner solve, not the request), which no warning
    filter could switch off.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for family, call, *_ in OFF_CASES + LIMITED_CASES:
            machines[family].analyze_steady(**call)
        # A ratio below the floor is held on it: a running row, flagged in its own key.
        floor = machines["ASHPB"].analyze_steady(T_tank_w=20.0, T0=30.0, Q_ref_tank=8000.0)
        dt, n = 300.0, 2
        machines["WSHPB"].analyze_dynamic(
            n * dt, dt, 30.0, np.zeros(n), np.full(n, 5.0), T_source_w_schedule=np.full(n, 1.0)
        )
        machines["ASHP"].analyze_dynamic(2 * 600.0, 600.0, np.array([-2000.0, -6000.0]), np.array([7.0, -25.0]))
    assert floor["hp_is_on"] and floor["pr_floor_active"]
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# 5. a sweep does not flood
# ---------------------------------------------------------------------------


def _sweep(model) -> list[dict]:
    """200 points across the freezing limit of a water loop: off, reduced and running rows."""
    rows = []
    for T_source in np.linspace(-4.0, 15.0, 20):
        for duty in np.linspace(2000.0, 8000.0, 10):
            rows.append(model.analyze_steady(T_tank_w=35.0, T_source=float(T_source), Q_ref_tank=float(duty)))
    return rows


def test_sweep_of_200_points_prints_a_line_per_limit_not_per_point():
    model = _quiet(tmhp.WaterSourceHeatPumpBoiler)
    rows, notices = _notices(lambda: _sweep(model), "default")
    off = sum(1 for r in rows if r["failure_reason"] != "none")
    reduced = sum(1 for r in rows if r["failure_reason"] == "none" and r["capacity_clamped"] == "max")
    assert len(rows) == 200 and off >= 30 and reduced >= 10
    # One line for the points the limit stops and one for those it reduces, and each says so.
    assert len(notices) == 2
    assert all("Reported once per model and limit" in str(w.message) for w in notices)
    # Nothing is lost: the rows carry every occurrence.
    assert {r["failure_reason"] for r in rows} == {"none", "source_fluid_freezing"}

    # A second sweep of the same model stays quiet; "always" reports every point; starting over reports again.
    _, again = _notices(lambda: _sweep(model), "default")
    assert again == []
    _, every = _notices(lambda: _sweep(model), "always")
    assert len(every) == off + reduced
    assert not any("Reported once" in str(w.message) for w in every)
    reset_notices(model)
    _, fresh = _notices(lambda: _sweep(model), "default")
    assert len(fresh) == 2


def test_the_once_memory_survives_catch_warnings_inside_the_loop():
    # Python's own once-per-location registry is cleared by every catch_warnings(); the model's memory is not.
    model = _quiet(tmhp.WaterSourceHeatPumpBoiler)

    def loop():
        for _ in range(5):
            with warnings.catch_warnings():
                pass
            model.analyze_steady(T_tank_w=45.0, T_source=1.0, Q_ref_tank=6000.0)

    _, notices = _notices(loop, "default")
    assert len(notices) == 1


# ---------------------------------------------------------------------------
# 6. analyze_dynamic: one summary, the same totals in attrs
# ---------------------------------------------------------------------------


def _recount(df: pd.DataFrame, dt_s: float) -> dict:
    """Off and limited steps recounted from the frame's own columns."""
    refused = df["failure_reason"].astype(object).ne("none").to_numpy()
    on = df["hp_is_on"].astype(bool).to_numpy()
    off = refused & ~on
    request = df["Q_request [W]"].abs().to_numpy(dtype=float)
    clamped = df["capacity_clamped"].astype(object).eq("max").to_numpy()
    if "Q_unmet [W]" in df.columns:
        short = df["Q_unmet [W]"].to_numpy(dtype=float) > 1e-3 * request
    else:
        short = df["Q_max [W]"].to_numpy(dtype=float) < request * (1.0 - 1e-3)
    limited = clamped & on & ~refused & short
    return {"off": off, "limited": limited, "request": request, "hours": dt_s / 3600.0}


def test_boiler_dynamic_run_ends_with_one_summary_that_matches_the_frame(machines):
    dt, T_source = 300.0, [12.0, 12.0, 5.0, 5.0, 1.0, 1.0]
    n = len(T_source)
    model = machines["WSHPB"]
    df, notices = _notices(
        lambda: model.analyze_dynamic(
            n * dt, dt, 30.0, np.zeros(n), np.full(n, 5.0), T_source_w_schedule=np.asarray(T_source)
        )
    )
    (notice,) = notices
    text = str(notice.message)
    count = _recount(df, dt)
    summary = df.attrs["operating_limits"]
    assert summary == summarize_operating_limits(df, dt, T_tank_w_lower_bound=model.T_tank_w_lower_bound)
    assert summary["kind"] == "boiler" and summary["steps [-]"] == n
    assert summary["off_steps [-]"] == count["off"].sum() == 2
    assert summary["limited_steps [-]"] == count["limited"].sum() == 2
    assert summary["off_time [h]"] == pytest.approx(2 * count["hours"])
    assert summary["off_reasons"] == {"source_fluid_freezing": {"steps [-]": 2, "first_time [s]": 4 * dt}}
    assert summary["limited_bindings"] == {"source_fluid_freezing": {"steps [-]": 2, "first_time [s]": 2 * dt}}
    # Heat by which the reduced steps ran below the nameplate duty, over their run time.
    below = ((count["request"] - df["Q_max [W]"]) * df["runtime_fraction [-]"])[count["limited"]].sum() * dt / 3.6e6
    assert summary["Q_below_request [kWh]"] == pytest.approx(below, rel=1e-9) and below > 0
    assert summary["T_tank_w_min [°C]"] == pytest.approx(df["T_tank_w [°C]"].min())
    lower = model.T_tank_w_lower_bound
    assert summary["time_below_lower_setpoint [h]"] == pytest.approx(
        (df["T_tank_w [°C]"] < lower - 0.05).sum() * count["hours"]
    )
    assert summary["Q_dhw_unmet [kWh]"] == pytest.approx(df["Q_dhw_unmet [W]"].sum() * dt / 3.6e6)
    # The text says the same, with the first time of each limit and what to do.
    assert text.startswith("WaterSourceHeatPumpBoiler.analyze_dynamic: ")
    assert "did not run on 2 of 6 steps (0.2 h): source_fluid_freezing on 2 (first at 0.33 h)" in text
    assert "below the requested duty on 2 of 6 steps" in text and f"{below:.2f} kWh short" in text
    assert f"lowest {df['T_tank_w [°C]'].min():.1f} °C" in text and f"{lower:.1f} °C lower set point" in text
    assert "3.0 °C for water" in text and limit_for(reason="source_fluid_freezing").remedy in text
    assert "df.attrs['operating_limits']" in text
    assert Path(notice.filename).name == Path(__file__).name


def test_space_dynamic_run_reports_the_unmet_load(machines):
    dt = 600.0
    load = np.array([-2000.0, -6000.0, -6000.0, 0.0])  # met, refused twice, not called
    outdoor = np.array([7.0, -25.0, -25.0, 7.0])
    df, notices = _notices(lambda: machines["ASHP"].analyze_dynamic(len(load) * dt, dt, load, outdoor))
    (notice,) = notices
    summary = df.attrs["operating_limits"]
    count = _recount(df, dt)
    assert summary["kind"] == "space"
    assert summary["off_steps [-]"] == count["off"].sum() == 2 and summary["limited_steps [-]"] == 0
    assert summary["off_reasons"] == {"discharge_temp_above_max": {"steps [-]": 2, "first_time [s]": dt}}
    unmet = df["Q_unmet [W]"].sum() * dt / 3.6e6
    request = df["Q_request [W]"].abs().sum() * dt / 3.6e6
    assert summary["Q_unmet [kWh]"] == pytest.approx(unmet) == pytest.approx(2.0)
    assert summary["Q_request [kWh]"] == pytest.approx(request)
    assert summary["unmet_fraction [-]"] == pytest.approx(unmet / request)
    text = str(notice.message)
    assert "did not run on 2 of 4 steps" in text and "discharge_temp_above_max on 2 (first at 0.17 h)" in text
    assert f"Unmet load: {unmet:.2f} kWh of {request:.2f} kWh requested" in text
    assert "T_dis_max = 150 °C" in text


def test_ground_source_dynamic_run_reports_a_load_above_the_ceiling(machines):
    dt, n = 3600.0, 2
    df, notices = _notices(lambda: machines["GSHP"].analyze_dynamic(n * dt, dt, np.full(n, -30000.0), np.zeros(n)))
    (notice,) = notices
    summary = df.attrs["operating_limits"]
    assert summary["off_steps [-]"] == 0 and summary["limited_steps [-]"] == n
    assert list(summary["limited_bindings"]) == ["cycle"]
    assert summary["Q_unmet [kWh]"] == pytest.approx(df["Q_unmet [W]"].sum() * dt / 3.6e6)
    assert "below the requested duty on 2 of 2 steps" in str(notice.message)


def test_dynamic_run_without_a_limited_step_does_not_warn(machines):
    dt, n = 300.0, 4
    df, notices = _notices(
        lambda: machines["WSHPB"].analyze_dynamic(
            n * dt, dt, 30.0, np.zeros(n), np.full(n, 5.0), T_source_w_schedule=np.full(n, 12.0)
        )
    )
    assert notices == []
    summary = df.attrs["operating_limits"]
    assert summary["off_steps [-]"] == 0 and summary["limited_steps [-]"] == 0
    assert summary["off_reasons"] == {} and summary["limited_bindings"] == {}
    assert dynamic_message(summary, "x") is None


def test_dynamic_filter_ignore(machines):
    dt, n = 300.0, 2
    df, notices = _notices(
        lambda: machines["WSHPB"].analyze_dynamic(
            n * dt, dt, 30.0, np.zeros(n), np.full(n, 5.0), T_source_w_schedule=np.full(n, 1.0)
        ),
        "ignore",
    )
    assert notices == [] and df.attrs["operating_limits"]["off_steps [-]"] == n


def test_rows_collected_from_step_get_the_same_report(machines):
    """``step()`` does not warn; its rows carry what the summary needs."""
    model, dt, n = machines["ASHPB"], 600.0, 3

    def stepped():
        state = model.make_initial_state(50.0, 1.0)
        rows = []
        for i in range(n):
            inputs = {
                "n": i,
                "current_time_s": i * dt,
                "T0": -10.0,
                "dV_mix_w_out": 0.0,
                "T_sup_w": 15.0,
                "T_sur": 20.0,
            }
            state, row = model.step(state, inputs, dt)
            rows.append(row)
        return pd.DataFrame(rows)

    rows, notices = _notices(stepped)
    assert notices == []
    for key in ("failure_reason", "hp_is_on", "capacity_clamped", "capacity_binding", "Q_request [W]", "Q_max [W]"):
        assert key in rows.columns
    run, _ = _notices(lambda: model.analyze_dynamic(n * dt, dt, 50.0, np.zeros(n), np.full(n, -10.0)))
    with pytest.warns(OperatingLimitWarning, match="did not run on"):
        summary = warn_if_operating_limited(rows, dt, "my co-simulation", model)
    assert summary == rows.attrs["operating_limits"] == run.attrs["operating_limits"]
    assert summary["off_steps [-]"] + summary["limited_steps [-]"] == n


def test_summary_table_prints_the_limits(machines, capsys):
    dt, n = 300.0, 2
    df = _quiet(
        machines["WSHPB"].analyze_dynamic,
        n * dt,
        dt,
        30.0,
        np.zeros(n),
        np.full(n, 5.0),
        T_source_w_schedule=np.full(n, 1.0),
    )
    print_operating_limit_summary(df, dt, T_tank_w_lower_bound=machines["WSHPB"].T_tank_w_lower_bound)
    out = capsys.readouterr().out
    assert "[Operating Limits]" in out and "Called but did not run: 2 / 2 steps" in out
    assert "source_fluid_freezing: 2 steps, first at 0.00 h" in out
    assert limit_for(reason="source_fluid_freezing").remedy in out


# ---------------------------------------------------------------------------
# 7. one table for the warnings and the documentation
# ---------------------------------------------------------------------------


def _plain(text: str) -> str:
    return " ".join(text.replace("``", "").split())


def test_documentation_prints_the_meaning_and_remedy_of_every_limit():
    page = _plain((REPO_ROOT / "docs/source/concepts/failure-reason-semantics.rst").read_text(encoding="utf-8"))
    for limit in LIMITS:
        for name in (limit.reason, limit.binding):
            assert name is None or name in page, name
        meaning = limit.meaning[0].upper() + limit.meaning[1:] + "."
        assert _plain(meaning) in page, limit
        assert _plain(limit.remedy) in page, limit


def test_every_reason_and_binding_the_models_name_is_in_the_table():
    from tmhp import compressor_envelope as envelope
    from tmhp.source_fluid import SOURCE_FLUID_FREEZING

    reasons = {limit.reason for limit in LIMITS}
    bindings = {limit.binding for limit in LIMITS}
    for reason in (
        envelope.DISCHARGE_TEMP_ABOVE_MAX,
        envelope.CONDENSING_TEMP_ABOVE_MAX,
        envelope.ETA_ISEN_BELOW_MIN,
        envelope.PR_ABOVE_MAX,
        SOURCE_FLUID_FREEZING,
        "cycle_invalid",
        "hx_not_converged",
        "optimizer_failed",
        "t_cond_below_t_in",
    ):
        assert reason in reasons, reason
    for binding in (
        envelope.BINDING_DISCHARGE,
        envelope.BINDING_CONDENSING,
        envelope.BINDING_PRESSURE_RATIO,
        envelope.BINDING_HEAT_EXCHANGER,
        envelope.BINDING_EFFICIENCY,
        envelope.BINDING_CYCLE,
        "compressor",
        "outdoor_coil",
        SOURCE_FLUID_FREEZING,
    ):
        assert binding in bindings, binding
    # A value the table does not know is still named, with a pointer to the documentation.
    unknown = limit_for(reason="my_own_reason")
    assert "my_own_reason" in unknown.meaning and "documentation" in unknown.remedy
    assert math.isfinite(len(unknown.remedy))
