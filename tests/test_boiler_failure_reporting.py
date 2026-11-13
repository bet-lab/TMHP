"""Boilers report unsolved operating points as failures, not as results (#96).

Each test pins one way the domestic-hot-water boilers (ASHPB, GSHPB, WSHPB)
used to hand back a state they had not solved:

* internal solver keys leaking into ``analyze_steady`` results;
* a solved cycle that is not an operating point (collapsed isentropic
  efficiency: discharge state NaN, duty many times the nameplate) reported
  as ``failure_reason="none"`` -- tested with a collapsing user efficiency
  callable, since the default fit is guarded since #101;
* ASHPB raising CoolProp's ValueError when the condensing temperature
  passes the critical point;
* GSHPB putting the requested capacity into the tank on steps whose cycle
  failed (``E_cmp == 0``), which broke the tank energy balance;
* ASHPB dynamic discarding the optimiser outcome, leaving called-but-unsolved
  steps as ``hp_is_on=True`` rows with NaN heat, power and COP;
* no per-step ``failure_reason`` column in the dynamic frames although the
  docs branch on it;
* GSHPB/WSHPB forward-filling the dynamic frame, which copied the last
  running step's COP and cycle state into every off row.

The dynamic scenarios start each machine just below its hysteresis floor for
a few 300 s steps: enough to reach the tank temperature at which condensing
at the tank plus the condenser approach would pass the refrigerant's critical
point, so every run contains both solved and called-but-unsolved steps. That
failure is physical and does not depend on the compressor-efficiency fit.
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


def _collapse_above(default, pr_limit: float, value: float = 0.003):
    """A user isentropic-efficiency callable that falls to ``value`` above ``pr_limit``.

    It stays inside (0, 1], so the efficiency guard of ``_eval_eff`` accepts
    it: what such a fit produces has to be caught by the boiler's own
    judgement of the solved cycle, whatever efficiency model is plugged in.
    Set after construction so the rating point keeps the default fit.
    """

    def eta_isen(pressure_ratio: float, rps: float) -> float:
        return value if pressure_ratio > pr_limit else default(pressure_ratio, rps)

    return eta_isen


# (family, refrigerant, analyze_steady inputs, PR above which eta_is collapses)
_COLLAPSE_CASES = [
    # The #96 reproduction points: before the efficiency guard (#101) the raw
    # fit itself fell to eta_is 0.003-0.08 here and the boilers returned
    # 9-91 kW with a NaN discharge state as failure_reason='none'.
    pytest.param(
        "WSHPB",
        "R134a",
        {"T_tank_w": 65.0, "T_source": 5.0, "Q_ref_tank": 6400.0, "T0": 10.0},
        7.15,
        id="WSHPB-R134a-65-5",
    ),
    pytest.param(
        "WSHPB",
        "R410A",
        {"T_tank_w": 55.0, "T_source": -5.0, "Q_ref_tank": 6400.0, "T0": 10.0},
        7.15,
        id="WSHPB-R410A-55-m5",
    ),
    pytest.param(
        "GSHPB",
        "R410A",
        {"T_tank_w": 55.0, "T_source": -5.0, "Q_ref_tank": 6400.0, "T0": 10.0},
        7.15,
        id="GSHPB-R410A-55-m5",
    ),
    # ASHPB's optimiser steers around a partial collapse, so collapse everywhere.
    pytest.param("ASHPB", "R32", {"T_tank_w": 45.0, "T0": 7.0, "Q_ref_tank": 6000.0}, 0.0, id="ASHPB-R32-45-7"),
]
# The #96 points were found with the former capacity-independent heat
# exchangers of the liquid-source boilers (500 W/K, 24 L/min, 200 W pump);
# with the capacity-scaled defaults the same inputs stay below PR 7.15.
_LEGACY_HX = {
    "GSHPB": dict(UA_tank_hx=500.0, UA_ground=500.0, ground_flow_ref_lpm=24.0, E_pmp=200.0),
    "WSHPB": dict(UA_tank_hx=500.0, UA_water=500.0, dV_b_f_lpm=24.0, E_pmp=200.0),
}
_FAMILY_CLASS = {
    "ASHPB": AirSourceHeatPumpBoiler,
    "GSHPB": GroundSourceHeatPumpBoiler,
    "WSHPB": WaterSourceHeatPumpBoiler,
}


@pytest.mark.parametrize(("family", "ref", "point", "pr_limit"), _COLLAPSE_CASES)
def test_collapsed_user_efficiency_is_reported_as_failure(family: str, ref: str, point: dict, pr_limit: float) -> None:
    model = _FAMILY_CLASS[family](ref=ref, **_LEGACY_HX.get(family, {}))
    model.eta_cmp_isen = _collapse_above(model.eta_cmp_isen, pr_limit)
    result = _steady(model, **point)
    _assert_off_mode_failure(result, "cycle_invalid")
    assert result["failure_detail"] in {"discharge_state_undefined", "min_speed_duty_above_capacity"}


def test_ashpb_supercritical_condensing_is_a_failure_not_an_exception() -> None:
    # R410A condenses at the tank temperature plus a 5 K approach; at a 65 °C
    # tank and 1.5x the nameplate that is above the 71.3 °C critical point.
    # CoolProp has no saturation state there and its ValueError used to
    # escape analyze_steady (on main before #102 as well).
    model = AirSourceHeatPumpBoiler(ref="R410A")
    result = _steady(model, T_tank_w=65.0, T0=7.0, Q_ref_tank=1.5 * model.hp_capacity)
    _assert_off_mode_failure(result, "cycle_invalid")


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
    model = GroundSourceHeatPumpBoiler(dt_s=dt, t_max_s=86400, UA_tank_hx=500.0)
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
    """R410A ASHPB with a 75 °C upper bound, from just below its floor, four 300 s steps.

    Two steps heat the 151 L tank from 54.9 to ~69 °C; the tank is still
    below its upper bound, so the next steps are called, but condensing at
    the tank plus a 5 K approach would be above the R410A critical point
    (71.3 °C): there is no cycle. Before #96 such steps were
    ``hp_is_on=True`` rows with NaN heat, power and COP (or, here, a
    CoolProp ValueError).
    """
    dt, n = 300.0, 4
    model = AirSourceHeatPumpBoiler(ref="R410A", T_tank_w_upper_bound=75.0)
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
    """WSHPB with an undersized tank heat exchanger (500 W/K), same scenario as
    GSHPB: its 16 K condenser approach also crosses the R410A critical point
    once the tank passes ~55.4 °C."""
    dt, n = 300.0, 3
    model = WaterSourceHeatPumpBoiler(UA_tank_hx=500.0)
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
    """The values tabled in docs/source/concepts/failure-reason-semantics.rst.

    Every ``list-table`` whose first header cell is ``Value`` lists
    ``failure_reason`` values (the ``failure_detail`` table is headed
    differently and is not included).
    """
    doc = Path(__file__).resolve().parents[1] / "docs/source/concepts/failure-reason-semantics.rst"
    reasons: set[str] = set()
    for table in doc.read_text(encoding="utf-8").split(".. list-table::")[1:]:
        rows = re.findall(r"^    \* - (.+)$", table, flags=re.MULTILINE)
        if rows and rows[0].strip() == "Value":
            reasons |= {m.group(1) for row in rows[1:] if (m := re.fullmatch(r"``([a-z_]+)``", row.strip()))}
    assert {"none", "hx_not_converged", "optimizer_failed", "cycle_invalid"} <= reasons
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


# --------------------------------------------------------------------------
# Item 3: no forward fill into off rows
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(name, marks=pytest.mark.xdist_group(name.replace("_failing_run", "-failing-run")))
        for name in _FAILING_RUNS
    ],
)
def test_off_rows_carry_no_performance_figures(run: str, request: pytest.FixtureRequest) -> None:
    """No COP or compressor state is carried into rows where nothing ran.

    GSHPB and WSHPB used to forward-fill the whole frame, copying the last
    running step's COP, efficiencies and refrigerant states into every off
    row (daily mean cop_sys 2.00 instead of 2.12 on the default GSHPB).
    """
    _model, df, _dt = request.getfixturevalue(run)
    off = ~df["hp_is_on"].astype(bool)
    assert off.any() and (~off).any()
    for key in ("cop_ref [-]", "cop_sys [-]"):
        assert df.loc[off, key].isna().all(), key
        assert df.loc[~off, key].notna().all(), key
    # Compressor descriptors of a stopped machine: absent (NaN) or zero.
    for key in ("eta_cmp_isen [-]", "cmp_rps [rev/s]", "cmp_rpm [rpm]", "pr_cmp [-]"):
        if key in df.columns:
            values = df.loc[off, key].to_numpy(dtype=float)
            assert np.all(np.isnan(values) | (values == 0.0)), key
