"""Boilers report unsolved operating points as failures, not as results (#96).

Each test pins one way the domestic-hot-water boilers (ASHPB, GSHPB, WSHPB)
used to hand back a state they had not solved:

* internal solver keys leaking into ``analyze_steady`` results;
* a compressor clamped onto its speed floor with a collapsed isentropic
  efficiency (discharge state NaN, duty many times the nameplate) reported
  as ``failure_reason="none"``;
* GSHPB putting the requested capacity into the tank on steps whose cycle
  failed (``E_cmp == 0``), which broke the tank energy balance;
* ASHPB dynamic discarding the optimiser outcome, leaving called-but-unsolved
  steps as ``hp_is_on=True`` rows with NaN heat, power and COP;
* no per-step ``failure_reason`` column in the dynamic frames although the
  docs branch on it.

The dynamic scenarios start each default machine just below its hysteresis
floor for a few 300 s steps: enough to reach the tank temperature above which
the default cycle has no solution, so every run contains both solved and
called-but-unsolved steps.
"""

from __future__ import annotations

import math
import re
import warnings
from pathlib import Path

import numpy as np
import pytest

from tmhp import AirSourceHeatPumpBoiler, GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler
from tmhp.constants import c_w, rho_w
from tmhp.dynamic_context import unphysical_cycle_detail


def _steady(model, **kwargs) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        result = model.analyze_steady(**kwargs)
    assert isinstance(result, dict)
    return result


def _assert_off_mode_failure(result: dict, reason: str) -> None:
    assert result["failure_reason"] == reason
    assert result["converged"] is False
    assert result["hp_is_on"] is False
    for key in ("Q_ref_tank [W]", "E_cmp [W]", "E_tot [W]"):
        assert result[key] == 0.0, key
    assert math.isnan(result["cop_sys [-]"])


# --------------------------------------------------------------------------
# Item 6: no solver-internal keys in steady results
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cls", [GroundSourceHeatPumpBoiler, WaterSourceHeatPumpBoiler])
def test_steady_result_has_no_internal_keys(cls) -> None:
    result = _steady(cls(), T_tank_w=50.0, T_source=10.0, Q_ref_tank=6000.0, T0=10.0)
    assert result["E_cmp [W]"] > 0.0
    assert not [key for key in result if key.startswith("_")]


# --------------------------------------------------------------------------
# Item 2: a speed-floor clamp on a collapsed cycle is a failure
# --------------------------------------------------------------------------


_GOOD_STATE = {
    "hp_is_on": True,
    "Q_ref_tank [W]": 6000.0,
    "E_cmp [W]": 1800.0,
    "T_ref_cmp_out [°C]": 85.0,
    "h_ref_cmp_out [J/kg]": 4.5e5,
    "capacity_clamped": None,
}


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({}, None),
        # A speed-floor clamp within the nameplate is an ordinary part-load point.
        ({"capacity_clamped": "min", "Q_ref_tank [W]": 7900.0}, None),
        ({"capacity_clamped": "min", "Q_ref_tank [W]": 8100.0}, "min_speed_duty_above_capacity"),
        # A speed-ceiling shortfall is not over-delivery.
        ({"capacity_clamped": "max", "Q_ref_tank [W]": 8100.0}, None),
        ({"T_ref_cmp_out [°C]": float("nan")}, "discharge_state_undefined"),
        ({"h_ref_cmp_out [J/kg]": float("inf")}, "discharge_state_undefined"),
        ({"E_cmp [W]": 0.0}, "duty_or_power_undefined"),
        ({"Q_ref_tank [W]": float("nan")}, "duty_or_power_undefined"),
    ],
)
def test_unphysical_cycle_detail(change: dict, detail: str | None) -> None:
    assert unphysical_cycle_detail({**_GOOD_STATE, **change}, hp_capacity=8000.0) == detail


@pytest.mark.parametrize(
    ("ref", "point"),
    [
        # Before #96: Q 90,957 W for a 6.4 kW request, eta_is 0.0032, NaN discharge.
        ("R134a", {"T_tank_w": 65.0, "T_source": 5.0, "Q_ref_tank": 6400.0, "T0": 10.0}),
        # Before #96: Q 9,659 W, E_cmp 8,552 W, eta_is 0.068, NaN discharge.
        ("R410A", {"T_tank_w": 55.0, "T_source": -5.0, "Q_ref_tank": 6400.0, "T0": 10.0}),
    ],
)
def test_wshpb_collapsed_speed_floor_is_reported_as_failure(ref: str, point: dict) -> None:
    result = _steady(WaterSourceHeatPumpBoiler(ref=ref), **point)
    _assert_off_mode_failure(result, "cycle_invalid")
    assert result["failure_detail"] in {"discharge_state_undefined", "min_speed_duty_above_capacity"}


def test_gshpb_collapsed_speed_floor_is_reported_as_failure() -> None:
    # Before #96: Q 8,665 W for a 6.4 kW request at 15 rev/s, eta_is 0.076,
    # NaN discharge, failure_reason "none". (R134a cannot even be built here:
    # its B0/W55 rating point does not bracket, so R410A carries the case.)
    model = GroundSourceHeatPumpBoiler(ref="R410A")
    result = _steady(model, T_tank_w=55.0, T_source=-5.0, Q_ref_tank=6400.0, T0=10.0)
    _assert_off_mode_failure(result, "cycle_invalid")
    assert result["failure_detail"] in {"discharge_state_undefined", "min_speed_duty_above_capacity"}


# --------------------------------------------------------------------------
# Item 1: GSHPB heats the tank with the delivered heat only
# --------------------------------------------------------------------------


def _tank_balance_residual(model, df, T_init: float, dt: float, T_sup: float) -> tuple[float, float]:
    """Independent lumped-tank energy balance over a run [J].

    heat in - wall loss - hot water drawn - change in stored energy, with the
    draw and loss at the end-of-step temperature (the implicit scheme).
    Returns ``(residual, heat_in)``.
    """
    T = np.r_[T_init, df["T_tank_w [°C]"].to_numpy(dtype=float)]
    heat_in = float(df["Q_ref_tank [W]"].to_numpy(dtype=float).sum() * dt)
    loss = float(df["Q_tank_loss [W]"].to_numpy(dtype=float).sum() * dt)
    drawn_flow = np.nan_to_num(df["dV_tank_w_out [m3/s]"].to_numpy(dtype=float))
    drawn = float((c_w * rho_w * drawn_flow * (T[1:] - T_sup)).sum() * dt)
    stored = float(model.C_tank * (T[-1] - T[0]))
    return heat_in - loss - drawn - stored, heat_in


@pytest.fixture(scope="module")
def gshpb_failing_run():
    """Default GSHPB from just below its hysteresis floor, four 300 s steps.

    The default machine (R410A, UA_tank_hx 500 W/K, 8 kW) condenses 16 K
    above the tank, so above ~55.4 °C it would condense beyond the R410A
    critical point (71.3 °C): the first step heats the 100 L tank to ~60 °C
    and every later step is called but has no cycle. Before #96 each of those
    steps still put the requested 8 kW into the tank.
    """
    dt, n = 300.0, 4
    model = GroundSourceHeatPumpBoiler(dt_s=dt, t_max_s=86400)
    df = model.analyze_dynamic(
        n * dt,
        dt,
        54.9,
        np.zeros(n),
        np.full(n, 5.0),
        T_sup_w_schedule=np.full(n, 15.0),
        T_sur_schedule=np.full(n, 20.0),
    )
    return model, df, dt


@pytest.mark.xdist_group("gshpb-failing-run")
def test_gshpb_failed_steps_put_no_heat_into_tank_or_ground(gshpb_failing_run) -> None:
    model, df, dt = gshpb_failing_run
    failed = df["E_cmp [W]"].to_numpy(dtype=float) == 0.0
    assert failed.sum() >= 2, "scenario must contain called-but-unsolved steps"
    assert (df.loc[failed, "Q_ref_tank [W]"] == 0.0).all()
    assert (df.loc[failed, "Q_bhe [W]"] == 0.0).all()
    assert not df.loc[failed, "hp_is_on"].any()
    # The borehole load history the ground response integrates is the
    # reported extraction, step for step (zero on the failed steps).
    history = np.cumsum(model._ground_coupler._pulses) * model.total_borehole_length
    np.testing.assert_allclose(history, df["Q_bhe [W]"].to_numpy(dtype=float), atol=1e-6)

    residual, heat_in = _tank_balance_residual(model, df, 54.9, dt, 15.0)
    assert heat_in > 0.0
    assert abs(residual) < 0.01 * heat_in


# --------------------------------------------------------------------------
# Item 5: ASHPB called-but-unsolved steps are off-mode rows
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ashpb_failing_run():
    """Default ASHPB from just below its hysteresis floor, three 300 s steps.

    The first step heats the tank past ~61 °C, above which the default
    R134a machine has no cycle at 5 °C outdoor. Before #96 the later steps
    were ``hp_is_on=True`` rows with NaN heat, power and COP.
    """
    dt, n = 300.0, 3
    model = AirSourceHeatPumpBoiler()
    df = model.analyze_dynamic(
        n * dt,
        dt,
        54.9,
        np.zeros(n),
        np.full(n, 5.0),
        T_sup_w_schedule=np.full(n, 15.0),
        T_sur_schedule=np.full(n, 20.0),
    )
    return model, df, dt


@pytest.mark.xdist_group("ashpb-failing-run")
def test_ashpb_unsolved_called_steps_are_off_mode_rows(ashpb_failing_run) -> None:
    model, df, dt = ashpb_failing_run
    failed = df["failure_reason"] != "none"
    assert failed.sum() >= 1, "scenario must contain called-but-unsolved steps"
    assert not df.loc[failed, "hp_is_on"].any()
    for key in ("Q_ref_tank [W]", "E_cmp [W]", "E_tot [W]"):
        assert (df.loc[failed, key] == 0.0).all(), key
    assert df.loc[failed, "cop_sys [-]"].isna().all()
    # Every row the compressor ran has finite cycle numbers.
    ran = df["hp_is_on"].astype(bool)
    assert ran.any()
    assert np.isfinite(df.loc[ran, ["Q_ref_tank [W]", "E_cmp [W]", "cop_sys [-]"]].to_numpy(dtype=float)).all()

    residual, heat_in = _tank_balance_residual(model, df, 54.9, dt, 15.0)
    assert abs(residual) < 0.01 * heat_in


# --------------------------------------------------------------------------
# Item 4: per-step failure_reason in the dynamic frames
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def wshpb_failing_run():
    """Default WSHPB, same scenario as GSHPB: its 16 K condenser approach
    also crosses the R410A critical point once the tank passes ~55.4 °C."""
    dt, n = 300.0, 3
    model = WaterSourceHeatPumpBoiler()
    df = model.analyze_dynamic(
        n * dt,
        dt,
        54.9,
        np.zeros(n),
        np.full(n, 5.0),
        T_sup_w_schedule=np.full(n, 15.0),
        T_sur_schedule=np.full(n, 20.0),
    )
    return model, df, dt


def _documented_failure_reasons() -> set[str]:
    """The values tabled in docs/source/concepts/failure-reason-semantics.rst."""
    doc = Path(__file__).resolve().parents[1] / "docs/source/concepts/failure-reason-semantics.rst"
    table = doc.read_text(encoding="utf-8").split("The four values", 1)[1].split("How to branch on it", 1)[0]
    reasons = set(re.findall(r"^    \* - ``([a-z_]+)``", table, flags=re.MULTILINE))
    assert reasons == {"none", "hx_not_converged", "optimizer_failed", "cycle_invalid"}
    return reasons


_FAILING_RUNS = ["ashpb_failing_run", "gshpb_failing_run", "wshpb_failing_run"]


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(name, marks=pytest.mark.xdist_group(name.replace("_failing_run", "-failing-run")))
        for name in _FAILING_RUNS
    ],
)
def test_dynamic_frame_reports_failure_reason_per_step(run: str, request: pytest.FixtureRequest) -> None:
    _model, df, _dt = request.getfixturevalue(run)
    assert "failure_reason" in df.columns
    reasons = df["failure_reason"]
    assert reasons.map(type).eq(str).all()
    assert set(reasons) <= _documented_failure_reasons()
    # Off because the thermostat did not call, or ran: "none". Called but
    # not solved: a reason, and the row is off-mode.
    ran = df["hp_is_on"].astype(bool)
    assert (reasons[ran] == "none").all()
    assert (reasons != "none").any(), "scenario must contain called-but-unsolved steps"
    assert not df.loc[reasons != "none", "converged"].astype(bool).any()
