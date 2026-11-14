"""On/off cycling below the minimum capacity: ``GroundSourceHeatPump``.

``AirSourceHeatPump`` reports a request below its minimum-speed duty as the
mean of on/off cycling (``tests/test_cycling.py``). The ground-source heat
pump still answered with the continuous minimum-speed point: the default 4 kW
unit delivered 1,659 W to a 200 W heating request and 2,370 W to a 200 W
cooling one, and a 24 h run 18 % (heating) and 29 % (cooling) more heat than
was asked for -- with that surplus drawn from, or rejected to, the ground.

Now the mean row delivers the request and carries the EN 14825 degradation of
a unit delivering to air (``Cd`` 0.25); the loop pump runs with the
compressor, and the borefield is loaded with the mean.
"""

import math

import numpy as np
import pytest

from tmhp import CyclingDegradation, GroundSourceHeatPump, OperatingLimitWarning
from tmhp.air_properties import air_density
from tmhp.constants import c_a
from tmhp.cycling import (
    CAPACITY_RATIO_KEY,
    CYCLING_LOSS_KEY,
    MIN_CAPACITY_KEY,
    PART_LOAD_FACTOR_KEY,
    RUNTIME_FRACTION_KEY,
)
from tmhp.g_function import g_step_mean
from tmhp.ground_coupling import AggregateGFunctionCoupler, reset_ground_coupler, run_window_preview

HEATING = dict(Q_r_iu=-800.0, T0=0.0, T_a_room=20.0, T_bhe_wall=5.0)
COOLING = dict(Q_r_iu=800.0, T0=30.0, T_a_room=27.0, T_bhe_wall=20.0)


@pytest.fixture(scope="module")
def gshp():
    return GroundSourceHeatPump()


@pytest.fixture(scope="module")
def gshp_continuous():
    """The same unit without the cycling model: continuous at minimum speed."""
    return GroundSourceHeatPump(Cd_cycling=None)


@pytest.fixture(scope="module", params=[HEATING, COOLING], ids=["heating", "cooling"])
def pair(request, gshp, gshp_continuous):
    """(point, cycling row, continuous row) for one mode."""
    point = request.param
    return point, gshp.analyze_steady(**point), gshp_continuous.analyze_steady(**point)


def test_gshp_defaults_to_the_air_form(gshp, gshp_continuous):
    assert gshp.cycling == CyclingDegradation(0.25, "air")
    assert gshp_continuous.cycling is None
    with pytest.raises(ValueError):
        GroundSourceHeatPump(Cd_cycling=1.5)


def test_gshp_without_the_cycling_model_over_delivers(pair):
    _, _, continuous = pair
    assert continuous["capacity_clamped"] == "min"
    assert continuous["Q_ref_iu [W]"] > 1500.0  # asked for 800 W
    assert continuous[RUNTIME_FRACTION_KEY] == 1.0 and continuous[CAPACITY_RATIO_KEY] == 1.0
    assert continuous[MIN_CAPACITY_KEY] == pytest.approx(continuous["Q_ref_iu [W]"])
    assert continuous[CYCLING_LOSS_KEY] == 0.0 and continuous["Q_unmet [W]"] == 0.0


def test_gshp_request_below_the_minimum_capacity_is_delivered_exactly(pair):
    point, row, _ = pair
    assert row["converged"] and row["failure_reason"] == "none"
    assert row["capacity_clamped"] == "min"
    assert row["Q_ref_iu [W]"] == pytest.approx(800.0, rel=1e-12)
    assert row["Q_request [W]"] == point["Q_r_iu"] and row["Q_unmet [W]"] == 0.0
    assert 0.0 < row[CAPACITY_RATIO_KEY] < row[RUNTIME_FRACTION_KEY] < 1.0
    assert row[CAPACITY_RATIO_KEY] == pytest.approx(800.0 / row[MIN_CAPACITY_KEY], rel=1e-12)
    plf = row[PART_LOAD_FACTOR_KEY]
    assert plf == pytest.approx(1.0 - 0.25 * (1.0 - row[CAPACITY_RATIO_KEY]), rel=1e-12)
    assert row[RUNTIME_FRACTION_KEY] == pytest.approx(row[CAPACITY_RATIO_KEY] / plf, rel=1e-12)


def test_gshp_cycling_cop_is_the_continuous_cop_times_the_part_load_factor(pair):
    _, row, continuous = pair
    plf, cr, runtime = row[PART_LOAD_FACTOR_KEY], row[CAPACITY_RATIO_KEY], row[RUNTIME_FRACTION_KEY]
    # The minimum-speed point is the continuous one (the search weighs the
    # degradation, so it may sit a few watts away from it).
    assert row[MIN_CAPACITY_KEY] == pytest.approx(continuous["Q_ref_iu [W]"], rel=1e-2)
    assert row["cop_ref [-]"] == pytest.approx(continuous["cop_ref [-]"] * plf, rel=5e-3)
    # The indoor fan is not degraded (it runs while heat is delivered), so the
    # system COP falls a little less than the refrigerant COP.
    assert continuous["cop_sys [-]"] * plf < row["cop_sys [-]"] < continuous["cop_sys [-]"] * (plf + 0.03)
    # Compressor and loop pump: the running power for the runtime fraction of
    # the time (the pump runs with the compressor); the indoor fan for the
    # capacity ratio.
    assert row["E_cmp [W]"] / runtime == pytest.approx(continuous["E_cmp [W]"], rel=1e-2)
    assert row["E_pmp [W]"] / runtime == pytest.approx(continuous["E_pmp [W]"], rel=1e-9)
    assert row["E_iu_fan [W]"] / cr == pytest.approx(continuous["E_iu_fan [W]"], rel=2e-2)
    machine_running = (row["E_cmp [W]"] + row["E_pmp [W]"]) / runtime
    assert row[CYCLING_LOSS_KEY] == pytest.approx(machine_running * (runtime - cr), rel=1e-9)
    assert row["E_tot [W]"] < continuous["E_tot [W]"]


def test_gshp_mean_row_keeps_its_books(pair):
    point, r, _ = pair
    heating = point["Q_r_iu"] < 0
    sign = 1.0 if heating else -1.0
    # Refrigerant circuit: both duties and the work on the refrigerant carry the capacity ratio.
    work = r["m_dot_ref [kg/s]"] * (r["h_ref_cmp_out [J/kg]"] - r["h_ref_cmp_in [J/kg]"])
    assert r["E_cmp_ref [W]"] == pytest.approx(work, rel=1e-9)
    assert sign * (r["Q_ref_iu [W]"] - r["Q_ref_ground [W]"]) == pytest.approx(work, rel=1e-9)
    # Electricity.
    assert r["E_tot [W]"] == pytest.approx(r["E_cmp [W]"] + r["E_pmp [W]"] + r["E_iu_fan [W]"], rel=1e-12)
    assert r["E_cmp_plus_pmp [W]"] == pytest.approx(r["E_cmp [W]"] + r["E_pmp [W]"], rel=1e-12)
    assert r["E_cmp [W]"] == pytest.approx(r["E_cmp_ref [W]"] + r["E_cmp_loss [W]"], rel=1e-12)
    assert r["cop_ref [-]"] == pytest.approx(r["Q_ref_iu [W]"] / r["E_cmp [W]"], rel=1e-12)
    # Room: the coil duty plus (heating) or minus (cooling) the fan heat,
    # carried by the mean airflow at the running machine's supply temperature.
    delivery = r["Q_a_iu_out [W]"] - r["Q_a_iu_in [W]"]
    assert r["cop_sys [-]"] == pytest.approx(abs(delivery) / r["E_tot [W]"], rel=1e-12)
    assert delivery == pytest.approx(sign * r["Q_ref_iu [W]"] + r["E_iu_fan [W]"], abs=1e-6)
    sensible = (
        c_a * air_density(r["T_iu_a_in [°C]"]) * r["dV_iu_a [m3/s]"] * (r["T_iu_a_out [°C]"] - r["T_iu_a_in [°C]"])
    )
    assert delivery == pytest.approx(sensible, abs=1e-6)
    # Ground: the mean ground-HX duty and the mean pump heat.
    ground = r["Q_ref_ground [W]"] - r["E_pmp [W]"] if heating else -(r["Q_ref_ground [W]"] + r["E_pmp [W]"])
    assert r["Q_bhe [W]"] == pytest.approx(ground, rel=1e-12)
    # State points are those of the running machine.
    assert r["cmp_rpm [rpm]"] / 60 == pytest.approx(15.0)
    assert r["T_bhe [°C]"] == pytest.approx(point["T_bhe_wall"], abs=1e-4)


def test_gshp_loss_free_cycling_keeps_the_continuous_cop(gshp_continuous):
    ideal = GroundSourceHeatPump(Cd_cycling=0.0).analyze_steady(**HEATING)
    continuous = gshp_continuous.analyze_steady(**HEATING)
    assert ideal["Q_ref_iu [W]"] == pytest.approx(800.0, rel=1e-12)
    assert ideal[PART_LOAD_FACTOR_KEY] == 1.0 and ideal[CYCLING_LOSS_KEY] == 0.0
    assert ideal[RUNTIME_FRACTION_KEY] == pytest.approx(ideal[CAPACITY_RATIO_KEY], rel=1e-12)
    assert ideal["cop_sys [-]"] == pytest.approx(continuous["cop_sys [-]"], rel=1e-6)
    assert ideal["cop_ref [-]"] == pytest.approx(continuous["cop_ref [-]"], rel=1e-6)


def test_gshp_power_rises_with_the_request_through_the_minimum_capacity(gshp, pair):
    """No step where cycling ends: the mean power is continuous at ``CR = 1``."""
    point, row, _ = pair
    sign = -1.0 if point["Q_r_iu"] < 0 else 1.0
    floor = row[MIN_CAPACITY_KEY]
    loads = [0.1 * floor, 0.98 * floor, 1.02 * floor]
    rows = [gshp.analyze_steady(**{**point, "Q_r_iu": sign * q}) for q in loads]
    power = [r["E_tot [W]"] for r in rows]
    assert all(b > a for a, b in zip(power, power[1:], strict=False))
    assert [r["capacity_clamped"] for r in rows] == ["min", "min", None]
    assert [r["Q_ref_iu [W]"] for r in rows] == pytest.approx(loads, rel=1e-6)
    assert rows[2]["E_tot [W]"] / rows[1]["E_tot [W]"] == pytest.approx(1.02 / 0.98, rel=3e-2)
    assert rows[2][RUNTIME_FRACTION_KEY] == 1.0 and math.isnan(rows[2][MIN_CAPACITY_KEY])


def test_gshp_off_and_failed_rows_report_no_runtime(gshp):
    off = gshp.analyze_steady(Q_r_iu=0.0, T0=0.0, T_a_room=20.0)
    assert off[RUNTIME_FRACTION_KEY] == 0.0 and off[CAPACITY_RATIO_KEY] == 0.0
    assert off["Q_request [W]"] == 0.0 and off["Q_unmet [W]"] == 0.0
    with pytest.warns(OperatingLimitWarning, match="does not run"):
        failed = gshp.analyze_steady(Q_r_iu=-4000.0, T0=0.0, T_a_room=20.0, T_bhe_wall=-40.0)
    assert failed["failure_reason"] != "none" and not failed["hp_is_on"]
    assert failed[RUNTIME_FRACTION_KEY] == 0.0 and failed["Q_unmet [W]"] == 4000.0


DYNAMIC_STEP_S = 600
DYNAMIC_LOAD = -np.array([600.0, 600.0, 1200.0, 1200.0, 3000.0, 3000.0])


def _dynamic_run(model):
    """Six 10 min steps: four below the minimum capacity, two above it."""
    steps = len(DYNAMIC_LOAD)
    return model.analyze_dynamic(
        simulation_period_sec=steps * DYNAMIC_STEP_S,
        dt_s=DYNAMIC_STEP_S,
        Q_r_iu_schedule=DYNAMIC_LOAD,
        T0_schedule=np.zeros(steps),
        T_a_room_schedule=np.full(steps, 20.0),
    )


def _running_ground_load_per_metre(row, length):
    """Borefield load while the compressor runs [W/m], recovered from a (mean) row."""
    cr = row[CAPACITY_RATIO_KEY]
    power_fraction = cr / row[PART_LOAD_FACTOR_KEY]
    return (row["Q_ref_ground [W]"] / cr - row["E_pmp [W]"] / power_fraction) / length


@pytest.mark.parametrize("borehole_dynamics", [None, False], ids=["dynamic-borehole", "steady-borehole"])
def test_gshp_dynamic_run_delivers_the_request_and_loads_the_ground_with_the_mean(borehole_dynamics):
    """The mean load enters the ground history once; the running load sets the running fluid.

    Checked for both borehole responses. With the steady borehole the wall
    under the running fluid is the step mean under the mean load. With the
    dynamic borehole (the default) the response is fluid-referenced and the
    running fluid starts from the wall over the run -- a block of ``mean load
    / running load`` of the step -- under the running load
    (:func:`tmhp.ground_coupling.run_window_preview`). Both are recomputed here
    from a coupler fed with the rows' own mean loads.
    """
    model = GroundSourceHeatPump(borehole_dynamics=borehole_dynamics)
    assert model.borehole_dynamics == ("off" if borehole_dynamics is False else "transit")
    df = _dynamic_run(model)
    dt, length, g = DYNAMIC_STEP_S, model.total_borehole_length, model._gfunc_interp
    assert (df["failure_reason"] == "none").all()
    assert list(df["capacity_clamped"].fillna("none")) == ["min"] * 4 + ["none"] * 2
    assert df["Q_ref_iu [W]"].to_numpy() == pytest.approx(-DYNAMIC_LOAD, rel=1e-9)
    assert (df[RUNTIME_FRACTION_KEY].iloc[:4] < 1.0).all() and (df[RUNTIME_FRACTION_KEY].iloc[:4] > 0.0).all()
    assert (df[RUNTIME_FRACTION_KEY].iloc[4:] == 1.0).all()

    # The row's ground load is the mean over the cycling period ...
    mean_load = df["Q_ref_ground [W]"] - df["E_pmp [W]"]
    assert df["Q_bhe [W]"].to_numpy() == pytest.approx(mean_load.to_numpy(), rel=1e-12)
    # ... and that mean is what the history holds, once: the physical wall of
    # the first two steps is the response to the mean loads and nothing else.
    wall_mean = getattr(g, "wall_step_mean", None)
    response = (lambda lag: wall_mean(lag, dt)) if wall_mean is not None else (lambda lag: g_step_mean(g, lag, dt))
    w0, w1 = (float(v) for v in response(np.array([0.0, float(dt)])))
    q = df["Q_bhe [W]"].to_numpy() / length
    assert df["T_bhe [°C]"].iloc[0] == pytest.approx(model.Ts - q[0] * w0, abs=1e-9)
    assert df["T_bhe [°C]"].iloc[1] == pytest.approx(model.Ts - (q[0] * w1 + (q[1] - q[0]) * w0), abs=1e-9)

    # The fluid of a row is that of the running machine. Recompute it from a
    # coupler that is given the mean loads only.
    coupler = AggregateGFunctionCoupler(g)
    reset_ground_coupler(coupler, len(df), model.time, dt)
    window = run_window_preview(coupler)
    assert (window is None) == (borehole_dynamics is False)
    for n, row in df.iterrows():
        q_run = _running_ground_load_per_metre(row, length)
        fraction = q[n] / q_run
        assert fraction == pytest.approx(row[CAPACITY_RATIO_KEY], rel=2e-2)  # the pump-heat term apart
        if window is not None and fraction < 1.0 - 1e-12:
            rise = window(n, model.time, q_run, fraction * dt)  # over the run, under the running load
        else:
            rise = coupler.preview_wall_temperature_rise(n, model.time, q[n])  # step mean, mean load
        fluid = model.Ts - rise - q_run * row["R_b_eff [mK/W]"]
        rho_c = model.source_fluid.rho * model.source_fluid.cp  # the loop liquid (25 % ethylene glycol)
        half = q_run * length / (2.0 * rho_c * row["dV_bhe_f [m3/s]"] / row[CAPACITY_RATIO_KEY])
        assert row["T_bhe_f_in [°C]"] == pytest.approx(fluid - half, abs=1e-6), n
        assert row["T_bhe_f_out [°C]"] == pytest.approx(fluid + half, abs=1e-6), n
        assert row["T_ref_evap_sat [°C]"] < row["T_bhe_f_in [°C]"] < row["T_bhe_f_out [°C]"]
        coupler.wall_temperature_rise(n, model.time, q[n])  # the mean load, once

    first = df.iloc[0]
    drop = first["T_bhe [°C]"] - 0.5 * (first["T_bhe_f_in [°C]"] + first["T_bhe_f_out [°C]"])
    steady_drop = _running_ground_load_per_metre(first, length) * first["R_b_eff [mK/W]"]
    if borehole_dynamics is False:
        # No heat capacity: the fluid sits the running load's q' R_b below the wall.
        assert drop == pytest.approx(steady_drop, abs=1e-6)
    else:
        # A three-minute run from rest has not pulled the fluid down that far.
        assert 0.0 < drop < 0.6 * steady_drop


def test_gshp_dynamic_run_is_continuous_where_cycling_ends():
    """The fluid of a cycling step joins that of a continuous one as the request reaches the floor."""
    model = GroundSourceHeatPump()

    def second_step(request):
        return model.analyze_dynamic(
            simulation_period_sec=2 * DYNAMIC_STEP_S,
            dt_s=DYNAMIC_STEP_S,
            Q_r_iu_schedule=np.array([-2000.0, -request]),
            T0_schedule=np.zeros(2),
            T_a_room_schedule=np.full(2, 20.0),
        ).iloc[1]

    floor = second_step(200.0)[MIN_CAPACITY_KEY]
    below, above = second_step(0.98 * floor), second_step(1.02 * floor)
    assert below["capacity_clamped"] == "min" and above["capacity_clamped"] != "min"
    assert below[RUNTIME_FRACTION_KEY] < 1.0 and above[RUNTIME_FRACTION_KEY] == 1.0
    for key in ("T_bhe_f_in [°C]", "T_bhe_f_out [°C]", "T_bhe [°C]", "T_ref_evap_sat [°C]"):
        assert below[key] == pytest.approx(above[key], abs=0.15), key
    assert above["E_tot [W]"] / below["E_tot [W]"] == pytest.approx(1.02 / 0.98, rel=3e-2)


def test_gshp_dynamic_run_without_the_cycling_model_over_delivers(gshp, gshp_continuous):
    df, reference = _dynamic_run(gshp), _dynamic_run(gshp_continuous)
    assert reference["Q_ref_iu [W]"].iloc[:4].min() > 1500.0
    assert reference["Q_bhe [W]"].sum() > 1.3 * df["Q_bhe [W]"].sum()
    assert reference["T_bhe [°C]"].iloc[3] < df["T_bhe [°C]"].iloc[3]
    assert df["E_tot [W]"].sum() < reference["E_tot [W]"].sum()


def test_gshp_exergy_post_processing_closes_on_a_cycling_step(gshp):
    df = gshp.analyze_dynamic(
        simulation_period_sec=1200,
        dt_s=600,
        Q_r_iu_schedule=np.array([-800.0, 800.0]),
        T0_schedule=np.array([0.0, 30.0]),
        T_a_room_schedule=np.array([20.0, 27.0]),
    )
    assert (df["capacity_clamped"] == "min").all()
    for key in ("Xc_cmp [W]", "Xc_exp [W]", "Xc_iu_hx [W]", "Xc_bhe_hx [W]", "Xc_pmp [W]", "Xc_iu_fan [W]"):
        assert (df[key] >= -1e-6).all(), key
