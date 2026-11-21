"""Charging a tank past the nameplate cut-out at reduced duty (#204).

``condensing_limit_control="reduce_duty"`` lets a boiler keep charging above the tank
temperature at which its nameplate duty condenses on ``T_cond_max``: the compressor runs at the
largest duty inside the limit, ``K_tank_hx * (T_reach - T)``, down to its smallest continuous
duty. The tank then approaches ``T_reach`` as a first-order lag, which the in-step thermostat
(:func:`tmhp.dynamic_context.simulate_thermostat_step`, ``duty_limit``) follows in closed form,
so the end temperature, the heat and the electricity of a charge do not depend on the step.

The default (``"stop"``) is the behaviour of ``tests/test_step_envelope_stop.py`` and its
neighbours, which run unchanged.
"""

from __future__ import annotations

import contextlib
import io
import math
import warnings

import numpy as np
import pytest

import tmhp
from tmhp.compressor_envelope import (
    BINDING_CONDENSING,
    condensing_tank_cutout,
    condensing_tank_reach,
)
from tmhp.dynamic_context import (
    STOP_CONDENSING_LIMIT,
    DutyLimit,
    merge_running_rows,
    simulate_thermostat_step,
)
from tmhp.operating_limits import OperatingLimitWarning

# ---------------------------------------------------------------------------
# the event model: a compressor on the duty limit
# ---------------------------------------------------------------------------

C = 600_000.0  # J/K
Q_ON = 10_000.0  # W
#: 2 kW per kelvin below 65 degC: the nameplate duty fits up to 60 degC (the knee), the
#: smallest continuous duty (2 kW) up to 64 degC (the stop). Time constant C / K = 300 s.
LIMIT = DutyLimit(T_zero_C=65.0, K=2_000.0, Q_min=2_000.0)
TAU = C / LIMIT.K
FREE_ZERO = ((50.0, 0.0), (70.0, 0.0))


def _sim(**kw):
    args = dict(
        T_start_C=56.0,
        call_start=True,
        running_prev=False,
        phase_age_s=math.inf,
        scheduled=True,
        T_lower=55.0,
        T_upper=63.0,
        dt=3600.0,
        C=C,
        free_heat=FREE_ZERO,
        Q_on=Q_ON,
        duty_limit=LIMIT,
    )
    args.update(kw)
    return simulate_thermostat_step(**args)


def _heat(run, dt, limit=LIMIT, q_on=Q_ON):
    """Heat [J] of a step: the full-duty stretch at ``q_on``, the reduced one at the limit's mean duty."""
    full = run.runtime_fraction - run.reduced_fraction
    reduced = limit.duty(run.T_reduced_mean_C) if run.reduced_fraction > 0.0 else 0.0
    return dt * (full * q_on + run.reduced_fraction * reduced)


def test_duty_limit_geometry():
    assert LIMIT.duty(62.0) == pytest.approx(6_000.0)
    assert LIMIT.knee_C(Q_ON) == pytest.approx(60.0)
    assert LIMIT.stop_C == pytest.approx(64.0)


def test_reduced_stretch_is_the_first_order_approach():
    """56 -> 60 degC at 10 kW (240 s), then 65 - 5 exp(-t / 300 s) up to the 63 degC set point."""
    run = _sim()
    t_reduced = TAU * math.log(5.0 / 2.0)
    assert run.runtime_fraction * 3600.0 == pytest.approx(240.0 + t_reduced, rel=1e-12)
    assert run.reduced_fraction * 3600.0 == pytest.approx(t_reduced, rel=1e-12)
    assert run.T_full_mean_C == pytest.approx(58.0, abs=1e-9)
    # The stretch's heat, K (T_zero - mean T) t, is what warmed the tank from 60 to 63 degC.
    assert LIMIT.duty(run.T_reduced_mean_C) * t_reduced == pytest.approx(3.0 * C, rel=1e-12)
    assert _heat(run, 3600.0) == pytest.approx(7.0 * C, rel=1e-12)
    assert not run.running_end and not run.call_end and not run.limit_stop and run.starts == 1


def test_without_a_duty_limit_nothing_changes():
    """``duty_limit=None`` is the model as it was: no reduced stretch, the same run."""
    before = _sim(duty_limit=None, T_limit_C=60.0)
    assert before.reduced_fraction == 0.0 and before.T_reduced_mean_C is None
    assert before.runtime_fraction * 3600.0 == pytest.approx(240.0) and before.limit_stop


def test_a_set_point_below_the_knee_never_reaches_the_limit():
    run = _sim(T_upper=59.0)
    assert run.reduced_fraction == 0.0 and run.runtime_fraction * 3600.0 == pytest.approx(180.0)
    assert not run.limit_stop


def _split(parts: int, total: float = 3600.0, **kw):
    """End temperature, heat [J] and starts of ``total`` seconds in ``parts`` equal steps."""
    T, call, running, age, heat, starts = kw.pop("T_start_C", 56.0), True, False, math.inf, 0.0, 0
    free = kw.get("free_heat", FREE_ZERO)
    (T_a, P_a), (T_b, P_b) = free
    assert P_a == P_b  # a constant free heat: the tank update below is exact
    dt = total / parts
    for _ in range(parts):
        r = _sim(T_start_C=T, call_start=call, running_prev=running, phase_age_s=age, dt=dt, **kw)
        q = _heat(r, dt)
        T += (q + P_a * dt) / C
        call, running, age = r.call_end, r.running_end, r.phase_age_end_s
        heat, starts = heat + q, starts + r.starts
    return T, heat, starts


@pytest.mark.parametrize("T_upper", [63.0, 64.5], ids=["set point below the stop", "set point above the stop"])
def test_the_charge_does_not_depend_on_the_step(T_upper):
    """One 3600 s step against 6, 60 and 360 steps: the same end temperature and heat."""
    one = _split(1, T_upper=T_upper)
    assert one[0] == pytest.approx(min(T_upper, LIMIT.stop_C), abs=1e-9)
    for parts in (6, 60, 360):
        T_end, heat, starts = _split(parts, T_upper=T_upper)
        assert T_end == pytest.approx(one[0], abs=1e-6), parts
        assert heat == pytest.approx(one[1], rel=1e-9), parts
        assert starts == one[2] == 1


def test_the_stop_is_where_the_limit_leaves_the_smallest_duty():
    """A set point above the stop: the run ends at 64 degC as a protection stop, the call stands."""
    run = _sim(T_upper=64.5)
    t_reduced = TAU * math.log(5.0 / 1.0)
    assert run.runtime_fraction * 3600.0 == pytest.approx(240.0 + t_reduced, rel=1e-12)
    assert run.limit_stop and run.call_end and not run.running_end
    # With a loss the compressor holds the tank at the stop, cycling at its smallest duty.
    loss = ((50.0, -500.0), (70.0, -500.0))
    held = _sim(T_start_C=64.0, T_upper=64.5, free_heat=loss, running_prev=True, phase_age_s=600.0)
    assert held.limit_stop and held.running_end and held.starts == 0
    assert held.runtime_fraction == pytest.approx(500.0 / LIMIT.Q_min) == held.reduced_fraction
    assert held.T_reduced_mean_C == pytest.approx(64.0)


def test_a_draw_settles_the_tank_on_the_limit():
    """A 4 kW draw: the tank settles where the limit's duty equals it (63 degC), the compressor running."""
    draw = ((50.0, -4_000.0), (70.0, -4_000.0))
    run = _sim(T_upper=63.9, free_heat=draw, dt=36_000.0)
    assert run.running_end and run.call_end and not run.limit_stop
    assert run.runtime_fraction == 1.0
    T_end = 56.0 + (_heat(run, 36_000.0) - 4_000.0 * 36_000.0) / C
    assert T_end == pytest.approx(63.0, abs=1e-6)
    # From above the knee with a draw the limit cannot cover, the tank falls back onto the full duty.
    heavy = ((50.0, -12_000.0), (70.0, -12_000.0))
    back = _sim(T_start_C=62.0, T_upper=63.9, free_heat=heavy, dt=600.0, running_prev=True, phase_age_s=60.0)
    assert 0.0 < back.reduced_fraction < back.runtime_fraction == 1.0
    assert back.T_full_mean_C < 60.0 < back.T_reduced_mean_C


def test_a_minimum_run_time_runs_on_along_the_limit():
    """Past the set point the minimum run time keeps the compressor on the limit, up to the stop."""
    run = _sim(T_upper=59.0, min_on_time_s=1800.0, dt=3600.0)
    assert run.limit_stop and not run.running_end
    assert run.runtime_fraction * 3600.0 == pytest.approx(240.0 + TAU * math.log(5.0), rel=1e-12)


def test_a_floor_at_or_above_the_running_duty_stops_at_the_knee():
    """No duty between the floor and the running duty: the stop of the default model."""
    run = _sim(T_upper=64.5, duty_limit=DutyLimit(65.0, 2_000.0, Q_min=12_000.0))
    assert run.reduced_fraction == 0.0 and run.limit_stop
    assert run.runtime_fraction * 3600.0 == pytest.approx(240.0)


def test_the_coil_cut_out_of_a_multi_node_tank_is_not_combined():
    with pytest.raises(ValueError, match="duty_limit"):
        _sim(run_limit_s=60.0)


def test_merged_row_is_the_run_time_weighted_mean():
    full = {"Q_ref_tank [W]": 10_000.0, "E_cmp [W]": 3_000.0, "E_tot [W]": 3_200.0, "cmp_rpm [rpm]": 3000.0}
    full.update({"cop_ref [-]": 10 / 3, "cop_sys [-]": 3.125, "capacity_clamped": None, "hp_is_on": True})
    reduced = {"Q_ref_tank [W]": 4_000.0, "E_cmp [W]": 1_500.0, "E_tot [W]": 1_600.0, "cmp_rpm [rpm]": 1200.0}
    reduced.update({"cop_ref [-]": 8 / 3, "cop_sys [-]": 2.5, "capacity_clamped": "max", "hp_is_on": True})
    reduced.update({"capacity_binding": BINDING_CONDENSING, "Q_max [W]": 4_000.0, "Q_request [W]": 10_000.0})
    row = merge_running_rows(full, 0.1, reduced, 0.3, ("Q_ref_tank [W]", "E_cmp [W]", "E_tot [W]"))
    assert row["Q_ref_tank [W]"] == pytest.approx(5_500.0) == row["Q_max [W]"]
    assert row["E_tot [W]"] == pytest.approx(2_000.0)
    assert row["cop_sys [-]"] == pytest.approx(2.75) and row["cop_ref [-]"] == pytest.approx(5_500.0 / 1_875.0)
    # State quantities are those of the stretch that ran longer; the capacity keys say the step was reduced.
    assert row["cmp_rpm [rpm]"] == 1200.0
    assert row["capacity_clamped"] == "max" and row["capacity_binding"] == BINDING_CONDENSING
    assert row["Q_request [W]"] == 10_000.0
    assert merge_running_rows(full, 0.3, reduced, 0.1, ("Q_ref_tank [W]",))["cmp_rpm [rpm]"] == 3000.0


# ---------------------------------------------------------------------------
# the three boiler families
# ---------------------------------------------------------------------------


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


#: The issue's machine: R32, 15 kW air-to-water, set points 60-62 degC, outdoor 15 degC.
ISSUE = dict(ref="R32", hp_capacity=15000.0, T_tank_w_lower_bound=60.0, T_tank_w_upper_bound=62.0)

#: family -> (class, constructor arguments, source temperature of the runs [degC], steady source keyword).
#: The ground-source run uses a long response table so that every step size shares one model.
FAMILIES = {
    "ASHPB": (tmhp.AirSourceHeatPumpBoiler, dict(ISSUE), 15.0, "T0"),
    "GSHPB": (
        tmhp.GroundSourceHeatPumpBoiler,
        dict(T_tank_w_lower_bound=70.0, T_tank_w_upper_bound=72.0, dt_s=60.0, t_max_s=86400.0),
        16.0,
        "T_source",
    ),
    "WSHPB": (
        tmhp.WaterSourceHeatPumpBoiler,
        dict(T_tank_w_lower_bound=70.0, T_tank_w_upper_bound=72.0),
        16.0,
        "T_source",
    ),
}
STEPS = (60.0, 600.0, 3600.0)
HORIZON = 3600.0


def _model(family, **extra):
    cls, kwargs, _, _ = FAMILIES[family]
    kwargs = {**kwargs, **extra}
    if family == "GSHPB" and "dt_s" in extra:
        kwargs["dt_s"] = extra["dt_s"]
    return _quiet(cls, **kwargs)


def _run(family, model, dt, T_init=50.0, horizon=HORIZON, draw=0.0):
    n = int(round(horizon / dt))
    source = FAMILIES[family][2]
    outdoor = source if family == "ASHPB" else 7.0
    return _quiet(model.analyze_dynamic, n * dt, dt, T_init, np.full(n, draw), np.full(n, outdoor))


@pytest.fixture(scope="module", params=list(FAMILIES))
def charge(request):
    """One hour from 50 degC without a draw, reduced-duty charging, at 60, 600 and 3600 s steps."""
    family = request.param
    runs = {}
    for dt in STEPS:
        model = _model(family, condensing_limit_control="reduce_duty", **({"dt_s": dt} if family == "GSHPB" else {}))
        runs[dt] = (model, _run(family, model, dt))
    return family, runs


def test_the_issue_band_is_kept_with_reduced_duty_charging():
    """60-62 degC, R32 15 kW: capped to 55.99-57.99 degC by default, kept when the duty is reduced."""
    with pytest.warns(OperatingLimitWarning, match="at the nameplate duty"):
        stop = tmhp.AirSourceHeatPumpBoiler(**ISSUE)
    assert stop.condensing_limit_control == "stop"
    assert (stop.T_tank_w_lower_bound, stop.T_tank_w_upper_bound) == pytest.approx((55.991, 57.991), abs=1e-3)
    with warnings.catch_warnings():
        warnings.simplefilter("error", OperatingLimitWarning)
        reduce = tmhp.AirSourceHeatPumpBoiler(**ISSUE, condensing_limit_control="reduce_duty")
    assert (reduce.T_tank_w_lower_bound, reduce.T_tank_w_upper_bound) == (60.0, 62.0)
    assert reduce.T_tank_w_setpoints_requested == (60.0, 62.0)


@pytest.mark.parametrize("family", list(FAMILIES))
def test_set_points_above_the_charging_limit_are_capped_there(family):
    """The cap follows the charging limit: where the smallest continuous duty condenses on the limit."""
    cls, kwargs, _, source_arg = FAMILIES[family]
    with pytest.warns(OperatingLimitWarning, match="smallest continuous duty"):
        m = cls(
            **{**kwargs, "T_tank_w_lower_bound": 60.0, "T_tank_w_upper_bound": 80.0},
            condensing_limit_control="reduce_duty",
        )
    cutout, reach = condensing_tank_cutout(m), condensing_tank_reach(m)
    cap = m.T_tank_w_upper_bound
    assert m.T_tank_w_setpoints_requested == (60.0, 80.0)
    assert cutout + 1.0 < cap < reach and m.T_tank_w_lower_bound == 60.0
    reference = {source_arg: m.charge_limit_reference_C}
    # The cap is where the minimum-speed duty the steady solve reports there (``Q_min [W]``) meets the limit ...
    probe = _quiet(m.analyze_steady, T_tank_w=cap, Q_ref_tank=0.1 * m.hp_capacity, **reference)
    assert probe["capacity_clamped"] == "min"
    assert cap == pytest.approx(reach - probe["Q_min [W]"] / m.K_tank_hx, abs=0.01)
    # ... and just below it a nameplate request still runs, reduced onto the limit, above that duty.
    below = _quiet(m.analyze_steady, T_tank_w=cap - 0.1, Q_ref_tank=m.hp_capacity, **reference)
    assert below["hp_is_on"] and below["capacity_binding"] == BINDING_CONDENSING
    assert below["Q_ref_tank [W]"] == pytest.approx(m.K_tank_hx * (reach - cap + 0.1), rel=0.02)
    assert below["Q_ref_tank [W]"] > probe["Q_min [W]"]
    if family != "ASHPB":
        # The source boilers' steady solve stops there too. (The air-source one runs on for a few
        # tenths of a kelvin at the speed floor by widening the outdoor approach: see the docs.)
        above = _quiet(m.analyze_steady, T_tank_w=cap + 0.1, Q_ref_tank=m.hp_capacity, **reference)
        assert not above["hp_is_on"] and above["failure_reason"] == "condensing_temp_above_max"


def test_unknown_control_is_refused():
    with pytest.raises(ValueError, match="condensing_limit_control"):
        tmhp.AirSourceHeatPumpBoiler(condensing_limit_control="inverter")


def test_multi_node_tank_keeps_the_stop():
    """The coil of a multi-node tank has its own cut-out and hold: the option is refused there."""
    with pytest.raises(ValueError, match="tank_model"):
        _quiet(
            tmhp.GroundSourceHeatPumpBoiler,
            tank_model="stratified",
            condensing_limit_control="reduce_duty",
            dt_s=600.0,
            t_max_s=86400.0,
        )


def test_the_charge_reaches_the_set_point_at_every_step_size(charge):
    family, runs = charge
    for dt, (m, df) in runs.items():
        cutout = condensing_tank_cutout(m)
        assert m.T_tank_w_upper_bound > cutout + 1.0  # the band lies past the nameplate cut-out
        assert (df["failure_reason"] == "none").all(), (family, dt)
        # The charge ends at the set point (a long step reports the tank after it has cooled a little).
        assert df["T_tank_w [°C]"].max() <= m.T_tank_w_upper_bound + 1e-6, (family, dt)
        assert df["T_tank_w [°C]"].iloc[-1] > m.T_tank_w_upper_bound - 0.5, (family, dt)
        if dt == 60.0:
            assert df["T_tank_w [°C]"].max() == pytest.approx(m.T_tank_w_upper_bound, abs=0.05), family
        assert df["hp_starts [-]"].sum() == 1, (family, dt)
        assert (df["reduced_duty_fraction [-]"] > 0.0).any(), (family, dt)


def test_end_temperature_heat_and_electricity_do_not_depend_on_the_step(charge):
    """60 s against 600 s and 3600 s steps: 0.05 K, 1 % of heat, 1 % of electricity."""
    family, runs = charge
    totals = {
        dt: (
            float(df["T_tank_w [°C]"].iloc[-1]),
            float(df["Q_ref_tank [W]"].sum() * dt),
            float(df["E_tot [W]"].sum() * dt),
        )
        for dt, (_, df) in runs.items()
    }
    fine = totals[60.0]
    for dt in (600.0, 3600.0):
        assert totals[dt][0] == pytest.approx(fine[0], abs=0.05), (family, dt)
        assert totals[dt][1] == pytest.approx(fine[1], rel=0.01), (family, dt)
        assert totals[dt][2] == pytest.approx(fine[2], rel=0.01), (family, dt)


def test_every_step_conserves_energy(charge):
    """Tank energy change = heat delivered - envelope loss (no draw), on every step."""
    family, runs = charge
    for dt, (m, df) in runs.items():
        T = np.concatenate(([50.0], df["T_tank_w [°C]"].to_numpy()))
        stored = m.C_tank * np.diff(T)
        supplied = (df["Q_ref_tank [W]"] - df["Q_tank_loss [W]"]).to_numpy() * dt
        assert np.allclose(stored, supplied, rtol=0.0, atol=1e-6 * m.C_tank), (family, dt)


def test_reduced_steps_run_at_the_duty_of_the_steady_solve(charge):
    """A step on the limit: the duty and the binding ``analyze_steady`` gives at its mean tank temperature."""
    family, runs = charge
    _, _, source, source_arg = FAMILIES[family]
    m, df = runs[60.0]
    T_start = np.concatenate(([50.0], df["T_tank_w [°C]"].to_numpy()[:-1]))
    whole = (df["reduced_duty_fraction [-]"] == 1.0).to_numpy()
    assert whole.sum() >= 3
    K = m.K_tank_hx
    for i in np.flatnonzero(whole)[:3]:
        row = df.iloc[i]
        assert row["capacity_clamped"] == "max" and row["capacity_binding"] == BINDING_CONDENSING
        assert row["Q_request [W]"] == m.hp_capacity and row["Q_max [W]"] == pytest.approx(row["Q_ref_tank [W]"])
        # Mean tank temperature of the step, from the mean duty on the limit.
        T_mean = condensing_tank_reach(m) - row["Q_ref_tank [W]"] / K
        assert min(T_start[i], row["T_tank_w [°C]"]) - 1e-6 <= T_mean <= max(T_start[i], row["T_tank_w [°C]"]) + 1e-6
        steady = _quiet(m.analyze_steady, T_tank_w=T_mean, Q_ref_tank=m.hp_capacity, **{source_arg: source})
        assert steady["capacity_clamped"] == "max" and steady["capacity_binding"] == BINDING_CONDENSING
        # The steady search closes within 0.05 K of condensing temperature of the limit.
        assert row["Q_ref_tank [W]"] == pytest.approx(steady["Q_ref_tank [W]"], abs=0.06 * K)
        if family != "GSHPB":  # the ground-source step solves against the ground's response, not a fixed source
            assert row["E_tot [W]"] == pytest.approx(steady["E_tot [W]"], rel=0.02)


def test_rows_before_the_cut_out_are_those_of_the_default(charge):
    """Up to the nameplate cut-out the two controls are the same machine: identical rows."""
    family, runs = charge
    m, df = runs[60.0]
    cutout = condensing_tank_cutout(m)
    stop = _model(family, T_tank_w_lower_bound=cutout - 2.0, T_tank_w_upper_bound=cutout)
    assert stop.condensing_limit_control == "stop"
    base = _run(family, stop, 60.0, horizon=240.0)
    assert (base["runtime_fraction [-]"] == 1.0).all() and "reduced_duty_fraction [-]" not in base.columns
    for key in ("T_tank_w [°C]", "Q_ref_tank [W]", "E_tot [W]", "cmp_rpm [rpm]", "T_ref_cond_sat_l [°C]"):
        assert np.array_equal(df[key].to_numpy()[:4], base[key].to_numpy()), (family, key)
    assert (df["reduced_duty_fraction [-]"].iloc[:4] == 0.0).all()


def test_issue_example_both_ways():
    """R32 15 kW, 60-62 degC, outdoor 15 degC, from 50 degC: the stop ends at 58 degC, the reduced duty at 62."""
    out = {}
    for control in ("stop", "reduce_duty"):
        m = _quiet(tmhp.AirSourceHeatPumpBoiler, **ISSUE, condensing_limit_control=control)
        df = _run("ASHPB", m, 60.0)
        out[control] = (m, df)
    stop_m, stop_df = out["stop"]
    reduce_m, reduce_df = out["reduce_duty"]
    assert stop_df["T_tank_w [°C]"].max() == pytest.approx(57.991, abs=0.01)
    assert reduce_df["T_tank_w [°C]"].max() == pytest.approx(62.0, abs=0.01)
    assert "reduced_duty_fraction [-]" not in stop_df.columns
    # The reduced stretch takes longer per kelvin and costs more electricity per unit of heat.
    assert reduce_df["runtime_fraction [-]"].sum() > stop_df["runtime_fraction [-]"].sum()
    cop = {k: df["Q_ref_tank [W]"].sum() / df["E_tot [W]"].sum() for k, (_, df) in out.items()}
    assert cop["reduce_duty"] < cop["stop"]
    assert reduce_df["hp_stop_reason"].isna().all()  # the set point, not the envelope, ended the charge


def test_a_warmer_source_than_the_reference_holds_at_its_own_stop():
    """The thermostat's cap is the limit at the reference source; a step applies the limit of its own source.

    At 35 degC outdoor the smallest continuous duty is larger than at the 7 degC reference, so the
    stop of the step lies below the capped set point: the compressor holds the tank there and the
    row says ``hp_stop_reason == "condensing_limit"``.
    """
    m = _quiet(
        tmhp.AirSourceHeatPumpBoiler,
        ref="R32",
        T_tank_w_lower_bound=60.0,
        T_tank_w_upper_bound=90.0,
        condensing_limit_control="reduce_duty",
    )
    n = 6
    df = _quiet(m.analyze_dynamic, n * 600.0, 600.0, 55.0, np.zeros(n), np.full(n, 35.0))
    assert (df["failure_reason"] == "none").all()
    assert df["T_tank_w [°C]"].max() < m.T_tank_w_upper_bound - 0.2
    held = df["hp_stop_reason"] == STOP_CONDENSING_LIMIT
    assert held.iloc[-3:].all() and df.loc[held, "hp_is_on"].all()
    # Held: the tank stays at the step's stop, the compressor making up the loss.
    assert df["T_tank_w [°C]"].iloc[-1] == pytest.approx(df["T_tank_w [°C]"].iloc[-2], abs=0.01)


def test_frosting_coil_charges_on_the_frost_free_limit():
    """Below 5 degC the delivered heat is the mean over frost and defrost; the limit binds the frost-free cycle."""
    runs = {}
    for dt in (60.0, 600.0):
        m = _quiet(tmhp.AirSourceHeatPumpBoiler, **ISSUE, condensing_limit_control="reduce_duty")
        n = int(HORIZON / dt)
        runs[dt] = _quiet(m.analyze_dynamic, n * dt, dt, 50.0, np.zeros(n), np.full(n, 0.0))
    for dt, df in runs.items():
        assert (df["failure_reason"] == "none").all(), dt
        assert df["defrost_fraction [-]"].max() > 0.0
        ran = df["runtime_fraction [-]"] > 0.0
        # No running row condenses above the limit.
        assert (df.loc[ran, "T_ref_cond_sat_v [°C]"] <= 65.0 + 0.011).all(), dt
    fine, coarse = runs[60.0], runs[600.0]
    assert coarse["T_tank_w [°C]"].iloc[-1] == pytest.approx(fine["T_tank_w [°C]"].iloc[-1], abs=0.05)
    assert coarse["Q_ref_tank [W]"].sum() * 600.0 == pytest.approx(fine["Q_ref_tank [W]"].sum() * 60.0, rel=0.01)
    assert coarse["E_tot [W]"].sum() * 600.0 == pytest.approx(fine["E_tot [W]"].sum() * 60.0, rel=0.01)


def test_scenario_classes_take_the_same_path():
    from tmhp.subsystems import PhotovoltaicSystem, SolarThermalCollector

    variants = {
        "ASHPB_PV_ESS": dict(pv=PhotovoltaicSystem()),
        "ASHPB_STC_preheat": dict(stc=SolarThermalCollector()),
        "ASHPB_STC_tank": dict(stc=SolarThermalCollector()),
    }
    for name, extra in variants.items():
        m = _quiet(getattr(tmhp, name), **ISSUE, condensing_limit_control="reduce_duty", **extra)
        assert m.T_tank_w_upper_bound == 62.0, name
        n = 6
        df = _quiet(
            m.analyze_dynamic,
            n * 600.0,
            600.0,
            55.0,
            np.zeros(n),
            np.full(n, 15.0),
            I_DN_schedule=np.zeros(n),
            I_dH_schedule=np.zeros(n),
        )
        assert (df["failure_reason"] == "none").all(), name
        assert (df["reduced_duty_fraction [-]"] > 0.0).any(), name
        assert df["T_tank_w [°C]"].max() == pytest.approx(62.0, abs=0.05), name
