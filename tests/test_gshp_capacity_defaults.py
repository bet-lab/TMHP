"""GSHP ground-side and indoor-air defaults follow the capacity (#96, #86).

With only ``hp_capacity`` given, the loop flow, pump power and borefield scale
with it, so the ground side is the same at every size. The indoor airflow is
the largest fan step water-to-air units of that capacity publish
(:mod:`tmhp.default_inputs`, #86), which is not proportional to the capacity:
large units move less air per kW, and the rating-point COP of the cycle falls
by 14 % from 3 kW to 100 kW for that reason alone -- with an airflow
proportional to the capacity it does not depend on the capacity at all. The
fixed control power (``E_ctrl``, 12 W for a unit delivering to air,
:mod:`tmhp.control_power`) does not scale with the size: it is 1.4 % of the
input at 3 kW and 0.04 % at 100 kW, so the system COP of a default machine
falls by 13 % between those sizes, and with a proportional airflow it rises by
1.4 %, by exactly that share. A value the caller
supplies is used as given, and a loop the caller sized badly is reported, not
silently solved. The dynamic run closes the ground fluid inside each step (G1).
"""

import math
import warnings

import numpy as np
import pytest

from tmhp import GroundSourceHeatPump
from tmhp.constants import c_w, rho_w
from tmhp.default_inputs import estimate_max_airflow
from tmhp.ground_defaults import (
    BOREHOLE_DESIGN_LINEAR_LOAD,
    calc_borehole_reynolds,
    calc_rated_linear_load,
    default_borefield_layout,
    default_ground_flow_lpm,
    warn_if_borefield_undersized,
    warn_if_laminar_ground_flow,
)
from tmhp.pump import calc_aux_loss_from_ashrae_grade, calc_pump_power, default_ground_pump_power

SHORT = dict(t_max_s=3600.0)
# From 3 kW: below about 2.6 kW per borehole the default flow of the default
# loop liquid (25 % ethylene glycol) is laminar in the 26 mm U-tube, which the
# constructor reports (test_small_machine_on_the_default_glycol_reports_its_laminar_loop).
CAPACITIES = (3000.0, 4000.0, 30000.0, 100000.0)
#: Indoor airflow per watt of capacity before the catalogue law (400 cfm/ton) [m3/s per W].
PROPORTIONAL_AIRFLOW_PER_W = 5.37e-5


def catalogue_airflow(capacity: float) -> float:
    return estimate_max_airflow(capacity, side="indoor", product="water_to_air", mode="cooling").value


def build(**kwargs):
    """Model built with every warning turned into an error (defaults must be quiet)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return GroundSourceHeatPump(**SHORT, **kwargs)


@pytest.fixture(scope="module")
def by_capacity():
    """Liquid entering at 0 °C (``Ts = 0``): the ISO 13256-1 heating point, on the default glycol loop."""
    return {cap: build(hp_capacity=cap, Ts=0.0) for cap in CAPACITIES}


# ── policy functions (tmhp.ground_defaults) ───────────────────────────────


def test_default_flow_is_the_rated_source_heat_over_three_kelvin():
    flow = default_ground_flow_lpm(4000.0)
    assert flow == pytest.approx(14.33, abs=0.01)
    assert flow / 60000.0 * rho_w * c_w * 3.0 == pytest.approx(0.75 * 4000.0)
    assert default_ground_flow_lpm(30000.0) == pytest.approx(7.5 * flow)
    for bad in (0.0, -1.0, math.nan):
        with pytest.raises(ValueError):
            default_ground_flow_lpm(bad)


@pytest.mark.parametrize(
    "capacity,layout",
    [
        (1000, (1, 1)),
        (4000, (1, 1)),
        (6600, (1, 1)),
        (8000, (2, 1)),
        (14000, (2, 2)),
        (30000, (3, 2)),
        (100000, (4, 4)),
    ],
)
def test_default_borefield_keeps_the_rated_load_within_50_w_per_m(capacity, layout):
    assert default_borefield_layout(capacity) == layout
    n = layout[0] * layout[1]
    assert calc_rated_linear_load(capacity, n, 100.0) <= BOREHOLE_DESIGN_LINEAR_LOAD + 1e-9
    # Near-square, and one row fewer would exceed 50 W/m.
    assert layout[0] - layout[1] in (0, 1)
    needed = math.ceil(0.75 * capacity / (BOREHOLE_DESIGN_LINEAR_LOAD * 100.0))
    assert layout[0] * (layout[1] - 1) < needed <= n


def test_default_borefield_does_not_gain_a_borehole_from_rounding():
    # 0.75 * 20000 W / 50 W/m = 300 m exactly: three boreholes, rounded to 2 x 2.
    assert default_borefield_layout(20000.0) == (2, 2)
    assert default_borefield_layout(0.75**-1 * 5000.0) == (1, 1)  # exactly 50 W/m on one borehole
    assert default_borefield_layout(4000.0, 50.0) == (2, 1)  # shallower boreholes: more of them


def test_default_pump_power_is_ashrae_grade_b_and_matches_the_hydraulic_model():
    assert default_ground_pump_power(3516.853) == pytest.approx(75.0)
    assert default_ground_pump_power(4000.0) == pytest.approx(85.3, abs=0.05)
    assert default_ground_pump_power(4000.0, "a") == pytest.approx(50.0 / 75.0 * default_ground_pump_power(4000.0))
    with pytest.raises(ValueError):
        default_ground_pump_power(4000.0, "F")
    with pytest.raises(ValueError):
        default_ground_pump_power(0.0)
    # Same grade in the hydraulic model: 210 kPa at the default flow and the
    # default 60 % pump efficiency is the same power within 3 %.
    flow = default_ground_flow_lpm(4000.0) / 60000.0
    budget = calc_aux_loss_from_ashrae_grade(
        "B", volume_flow_ref=flow, n_boreholes=1, H_b=100.0, pipe_inner_diameter=0.026
    )
    hydraulic = calc_pump_power(budget["target_pressure_Pa"], flow, 0.6)
    assert hydraulic == pytest.approx(default_ground_pump_power(4000.0), rel=0.03)


def test_reynolds_and_the_laminar_warning():
    # 2.82 L/min in a 26 mm pipe is Re 2300 for water at 1 mPa s.
    assert calc_borehole_reynolds(2.818 / 60000.0, 1, 0.026) == pytest.approx(2300.0, rel=1e-3)
    assert calc_borehole_reynolds(9 * 2.818 / 60000.0, 9, 0.026) == pytest.approx(2300.0, rel=1e-3)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert warn_if_laminar_ground_flow(14.33 / 60000.0, 1, 0.026) == pytest.approx(11700.0, rel=1e-2)
    with pytest.warns(UserWarning, match=r"laminar at the reference flow: Re = 1817 "):
        assert warn_if_laminar_ground_flow(20.04 / 60000.0, 9, 0.026) == pytest.approx(1817.0, abs=1.0)


def test_undersized_borefield_warning_names_the_load_and_a_field_that_fits():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert warn_if_borefield_undersized(8000.0, 1, 100.0) == pytest.approx(60.0)
    with pytest.warns(UserWarning, match=r"100 W/m over 1 x 60 m, above 80 W/m.*2 x 1 boreholes"):
        assert warn_if_borefield_undersized(8000.0, 1, 60.0) == pytest.approx(100.0)


# ── constructor defaults ──────────────────────────────────────────────────


def test_default_machine_keeps_one_borehole_and_gets_capacity_based_flows():
    model = build()
    assert (model.N_1, model.N_2, model.H_b) == (1, 1, 100.0)
    # 3 K loop difference with the default loop liquid, 25 % ethylene glycol (14.33 L/min for water).
    fluid = model.source_fluid
    assert fluid.name == "MEG-25%"
    assert model.ground_flow_ref_lpm == pytest.approx(default_ground_flow_lpm(4000.0, rho=fluid.rho, cp=fluid.cp))
    assert model.ground_flow_ref_lpm == pytest.approx(15.38, abs=0.01)
    assert build(source_fluid="water").ground_flow_ref_lpm == pytest.approx(14.33, abs=0.01)
    assert model.dV_b_f_m3s == pytest.approx(model.ground_flow_ref_lpm / 60000.0)
    assert model.E_pmp == pytest.approx(85.3, abs=0.05)
    # The largest airflow water-to-air units publish (0.2148 m3/s at 400 cfm/ton before #86). The
    # default 4 kW is below the smallest unit of that catalogue set (4.7 kW), and says so.
    assert model.dV_iu_fan_a_ref == pytest.approx(catalogue_airflow(4000.0)) == pytest.approx(0.3269, rel=1e-3)
    assert model.default_input_estimates["dV_iu_fan_a_rated"].extrapolated
    assert model.q_bhe_rated == pytest.approx(30.0)
    assert model.Re_bhe_ref == pytest.approx(3520.0, rel=0.01)  # turbulent; 11 700 for water


def test_every_default_scales_with_the_capacity(by_capacity):
    base = by_capacity[4000.0]
    for cap, model in by_capacity.items():
        ratio = cap / 4000.0
        assert model.ground_flow_ref_lpm == pytest.approx(ratio * base.ground_flow_ref_lpm)
        assert model.E_pmp == pytest.approx(ratio * base.E_pmp)
        assert model.UA_ground_rated == pytest.approx(ratio * base.UA_ground_rated)
        assert model.V_cmp_ref == pytest.approx(ratio * base.V_cmp_ref)
        # The indoor airflow is the catalogue's, and the fan power follows the airflow.
        assert model.dV_iu_fan_a_ref == pytest.approx(catalogue_airflow(cap))
        assert model.E_iu_fan_rated / model.dV_iu_fan_a_ref == pytest.approx(base.E_iu_fan_rated / base.dV_iu_fan_a_ref)
        assert model.q_bhe_rated <= BOREHOLE_DESIGN_LINEAR_LOAD + 1e-9
        assert model.Re_bhe_ref > 2300
    assert by_capacity[30000.0].n_boreholes == 6 and by_capacity[100000.0].n_boreholes == 16
    # Less air per kW in a larger unit (333 m3/h per kW at 3 kW, 181 at 100 kW), so the indoor coil
    # asks for a larger lift and the rated speed rises with the size (43.0 to 50.6 rev/s) -- inside
    # the fitted speed range of the efficiency maps at every size.
    per_watt = [by_capacity[cap].dV_iu_fan_a_ref / cap for cap in CAPACITIES]
    speeds = [by_capacity[cap].rps_rated for cap in CAPACITIES]
    assert per_watt == sorted(per_watt, reverse=True) and speeds == sorted(speeds)
    assert 0.5 < per_watt[-1] / per_watt[0] < 0.6
    assert speeds[0] > 33.8 and speeds[-1] < 72.5


def test_with_a_proportional_airflow_the_machine_is_the_same_at_every_size():
    """What the capacity-based ground side guarantees: one rated speed, one cycle, one COP without the fixed power.

    The machine without fixed control power (``E_ctrl=0``) has one system COP
    at every size (3.626). The default machine draws the same 12 W at every
    size on top of the same cycle, so its system COP is that machine's with
    ``E_ctrl`` added to the input -- 3.574 at 3 kW, 3.624 at 100 kW.
    """
    proportional = {
        cap: dict(hp_capacity=cap, Ts=0.0, dV_iu_fan_a_rated=cap * PROPORTIONAL_AIRFLOW_PER_W) for cap in CAPACITIES
    }
    models = {cap: build(**kw) for cap, kw in proportional.items()}
    bare = {cap: build(E_ctrl=0.0, **kw) for cap, kw in proportional.items()}
    base = models[4000.0]
    rows = {cap: model.analyze_steady(Q_r_iu=-cap, T0=0.0, T_a_room=20.0) for cap, model in models.items()}
    bare_rows = {cap: model.analyze_steady(Q_r_iu=-cap, T0=0.0, T_a_room=20.0) for cap, model in bare.items()}
    for cap, model in models.items():
        assert model.E_iu_fan_rated == pytest.approx(cap / 4000.0 * base.E_iu_fan_rated)
        assert model.rps_rated == pytest.approx(base.rps_rated, rel=1e-6)
        assert bare[cap].rps_rated == model.rps_rated
        row, bare_row = rows[cap], bare_rows[cap]
        for r in (row, bare_row):
            assert r["failure_reason"] == "none" and r["capacity_clamped"] is None, cap
        assert row["cop_ref [-]"] == pytest.approx(rows[4000.0]["cop_ref [-]"], rel=1e-4), cap
        assert bare_row["cop_sys [-]"] == pytest.approx(bare_rows[4000.0]["cop_sys [-]"], rel=1e-4), cap
        # The default machine: the same cycle, and E_ctrl on top of its input.
        assert row["E_ctrl [W]"] == 12.0 and bare_row["E_ctrl [W]"] == 0.0
        assert row["E_cmp [W]"] == pytest.approx(bare_row["E_cmp [W]"], rel=1e-12)
        assert row["E_tot [W]"] - row["E_ctrl [W]"] == pytest.approx(bare_row["E_tot [W]"], rel=1e-12)
        heat = abs(row["Q_a_iu_out [W]"] - row["Q_a_iu_in [W]"])
        assert 1.0 / row["cop_sys [-]"] == pytest.approx(1.0 / bare_row["cop_sys [-]"] + 12.0 / heat, rel=1e-12)
        loop_dT = row["T_bhe_f_out [°C]"] - row["T_bhe_f_in [°C]"]
        assert loop_dT == pytest.approx(rows[4000.0]["T_bhe_f_out [°C]"] - rows[4000.0]["T_bhe_f_in [°C]"], abs=1e-3)
    # A constant 12 W is a larger share of a small machine's input: the system COP rises with the size.
    cops = [rows[cap]["cop_sys [-]"] for cap in CAPACITIES]
    assert cops == sorted(cops)
    assert cops[0] == pytest.approx(3.574, abs=1e-3) and cops[-1] == pytest.approx(3.624, abs=1e-3)


def test_rating_point_cop_depends_on_the_capacity_through_the_indoor_airflow_and_the_fixed_power(by_capacity):
    # Before the capacity-based ground side: 30 kW ran on the 4 kW loop (20 L/min, 100 W) and
    # lost 12 % of the 4 kW system COP at this point; 100 kW could not be constructed at all.
    rows = {
        cap: model.analyze_steady(Q_r_iu=-cap, T0=0.0, T_a_room=20.0)  # liquid entering 0 °C, room 20 °C
        for cap, model in by_capacity.items()
    }
    for cap, row in rows.items():
        assert row["failure_reason"] == "none" and row["capacity_clamped"] is None, cap
        assert row["Q_ref_iu [W]"] == pytest.approx(cap, rel=1e-9)
        assert 2.5 < row["T_bhe_f_out [°C]"] - row["T_bhe_f_in [°C]"] < 3.5
        assert row["E_pmp [W]"] / cap == pytest.approx(75.0 / 3516.853)
    # What is left between the sizes is the catalogue airflow and the fixed control power (the test
    # above): 4.06 at 3 kW, 3.99 at 4 kW, 3.62 at 30 kW, 3.55 at 100 kW, a ratio of 0.874. Without
    # the 12 W (E_ctrl=0) the airflow alone gives 4.13, 4.03, 3.62 and 3.55, a ratio of 0.860: the
    # fixed power costs the small machine more and narrows the spread. With the earlier rotary speed
    # shape (shao_informed) and no fixed power: 4.13, 4.05, 3.69 and 3.62, a ratio of 0.877.
    cops = [rows[cap]["cop_sys [-]"] for cap in CAPACITIES]
    assert cops == sorted(cops, reverse=True)
    assert 0.85 < cops[-1] / cops[0] < 0.90
    without_ctrl = []
    for cap in CAPACITIES:
        row = rows[cap]
        assert row["E_ctrl [W]"] == 12.0
        without_ctrl.append(abs(row["Q_a_iu_out [W]"] - row["Q_a_iu_in [W]"]) / (row["E_tot [W]"] - row["E_ctrl [W]"]))
    assert without_ctrl == sorted(without_ctrl, reverse=True)
    assert cops[-1] / cops[0] > without_ctrl[-1] / without_ctrl[0] > 0.85
    assert rows[4000.0]["cop_sys [-]"] == pytest.approx(3.987, rel=2e-3)
    assert 3.2 < rows[4000.0]["cop_sys [-]"] < 4.2


def test_rated_duty_is_delivered_with_a_plausible_supply_air_temperature(by_capacity):
    model = by_capacity[4000.0]
    heating = model.analyze_steady(Q_r_iu=-4000.0, T0=0.0, T_a_room=20.0)
    # 24 °C at 0.0002 m3/s per W, the default before #96. At the largest airflow water-to-air
    # units publish (#86) the nameplate warms the air by 10.2 K (30.2 °C): what the energy balance
    # gives for that airflow, at the default 4 kW below the smallest catalogue unit (608 cfm/ton
    # where the law is extrapolated; 487 cfm/ton and 32.7 °C at 8 kW). The lower bound was 33 °C
    # while the default was 400 cfm/ton.
    assert 28.0 < heating["T_iu_a_out [°C]"] < 50.0
    assert heating["dV_iu_a [m3/s]"] <= model.dV_iu_fan_a_ref * (1 + 1e-9)
    cooling = build(Ts=25.0).analyze_steady(Q_r_iu=4000.0, T0=30.0, T_a_room=27.0)
    assert cooling["failure_reason"] == "none" and cooling["capacity_clamped"] is None
    assert cooling["Q_ref_iu [W]"] == pytest.approx(4000.0, rel=1e-9)
    assert 10.0 < cooling["T_iu_a_out [°C]"] < 17.0  # was 23 °C
    assert cooling["T_ref_evap_sat [°C]"] > 2.0  # the coil stays above freezing
    # Cooling rejects about 1.2 x the capacity: the loop warms by about 4.8 K.
    assert 4.3 < cooling["T_bhe_f_in [°C]"] - cooling["T_bhe_f_out [°C]"] < 5.3


def test_borehole_wall_rating_no_longer_collapses_with_the_capacity():
    # Rated heating against a 0 °C borehole wall. Before, one 100 m borehole
    # served every capacity: 30 kW delivered a system COP of 2.05 against
    # 3.8 at 4 kW (-46 %). The default field now holds the specific load within
    # 50 W/m, which leaves a few percent between sizes. The catalogue indoor
    # airflow is smaller per kW in a large unit, and the default indoor coil
    # holds the leaving approach of real water-to-air coils at that airflow
    # (#115-#118), so the air leaves warmer and the condensing temperature is
    # higher: 3.68 at 4 kW and 3.27 at 30 kW (-11 %; 3.743 and 3.446 with the
    # earlier 0.08 W/K per W coil).
    cops = {}
    for cap in (4000.0, 30000.0):
        row = build(hp_capacity=cap).analyze_steady(Q_r_iu=-cap, T0=0.0, T_a_room=20.0, T_bhe_wall=0.0)
        assert row["failure_reason"] == "none" and row["capacity_clamped"] is None, cap
        cops[cap] = row["cop_sys [-]"]
    assert 0.85 < cops[30000.0] / cops[4000.0] < 1.0


def test_supplied_values_are_used_as_given():
    # A water loop: nine boreholes on 16 kW keep it turbulent (Re 2900 with water
    # at its 0 °C design viscosity; 5200 at 20 °C). The default glycol would be
    # laminar there (Re 1564) and the constructor would say so.
    model = build(hp_capacity=16000.0, N_1=3, N_2=3, source_fluid="water")
    assert (model.N_1, model.N_2, model.n_boreholes, model.H_b) == (3, 3, 9, 100.0)
    assert model.ground_flow_ref_lpm == pytest.approx(4 * 14.33, abs=0.05)  # flow still follows the capacity
    assert model.Re_bhe_ref == pytest.approx(2900.0, rel=0.01)
    with pytest.warns(UserWarning, match=r"laminar at the reference flow: Re = 1564 "):
        GroundSourceHeatPump(**SHORT, hp_capacity=16000.0, N_1=3, N_2=3)
    # Any one of N_1, N_2, H_b fixes the field: the others take 1, 1, 100 m.
    deep = build(hp_capacity=8000.0, H_b=150.0)
    assert (deep.N_1, deep.N_2, deep.H_b) == (1, 1, 150.0)
    with pytest.warns(UserWarning, match="borefield is small"):  # 225 W/m: kept, and reported
        row = GroundSourceHeatPump(**SHORT, hp_capacity=30000.0, N_2=1)
    assert (row.N_1, row.N_2, row.H_b) == (1, 1, 100.0)
    explicit = build(E_pmp=100.0, ground_flow_ref_lpm=20.04, dV_iu_fan_a_rated=0.8)
    assert explicit.E_pmp == 100.0
    assert explicit.ground_flow_ref_lpm == 20.04 and explicit.dV_b_f_m3s == pytest.approx(0.000334)
    assert explicit.dV_iu_fan_a_ref == 0.8
    assert build(E_pmp=0.0).E_pmp == 0.0


def test_legacy_flow_alias_keeps_its_place_and_its_warning():
    with pytest.warns(DeprecationWarning, match="dV_b_f_lpm"):
        legacy = GroundSourceHeatPump(**SHORT, dV_b_f_lpm=24.0)
    assert legacy.ground_flow_ref_lpm == 24.0
    with pytest.warns(DeprecationWarning, match="dV_b_f_lpm"):
        both = GroundSourceHeatPump(**SHORT, dV_b_f_lpm=24.0, ground_flow_ref_lpm=30.0)
    assert both.ground_flow_ref_lpm == 30.0


def test_laminar_loop_is_reported_at_construction():
    # The former default: 20.04 L/min of water shared by nine boreholes (Re 1817
    # with water at 20 °C, 1014 at the 0 °C design viscosity).
    with pytest.warns(UserWarning, match=r"laminar at the reference flow: Re = 1014 .*9 parallel borehole"):
        model = GroundSourceHeatPump(
            **SHORT, hp_capacity=16000.0, N_1=3, N_2=3, ground_flow_ref_lpm=20.04, source_fluid="water"
        )
    assert model.Re_bhe_ref == pytest.approx(1014.0, abs=1.0)
    with pytest.warns(UserWarning, match="laminar at the constant setpoint flow"):
        GroundSourceHeatPump(**SHORT, ground_flow_constant_lpm=2.5, ground_flow_min_lpm=2.0)


def test_small_machine_on_the_default_glycol_reports_its_laminar_loop():
    # 25 % ethylene glycol at 0 °C is 2.1 times as viscous as water at 0 °C: the
    # default 3 K flow of a 2 kW machine (7.7 L/min in one 26 mm U-tube) is
    # laminar (Re 1760), where water at 7.2 L/min is not (Re 3260).
    with pytest.warns(UserWarning, match=r"laminar at the reference flow: Re = 17\d\d "):
        small = GroundSourceHeatPump(**SHORT, hp_capacity=2000.0)
    assert small.Re_bhe_ref < 2300 < build(hp_capacity=2000.0, source_fluid="water").Re_bhe_ref
    assert build(hp_capacity=3000.0).Re_bhe_ref > 2300  # quiet from 3 kW per borehole


def test_borefield_too_small_for_the_capacity_is_reported_at_construction():
    with pytest.warns(UserWarning, match=r"borefield is small for the capacity.*100 W/m"):
        model = GroundSourceHeatPump(**SHORT, hp_capacity=8000.0, H_b=60.0)
    assert model.q_bhe_rated == pytest.approx(100.0)
    assert build(hp_capacity=8000.0, N_1=1).q_bhe_rated == pytest.approx(60.0)  # within 80 W/m: quiet


# ── dynamic: ground fluid closed inside the step (G1) ─────────────────────


@pytest.fixture(scope="module")
def cold_ground_run():
    """Two hourly steps of 8 kW heating and of 8 kW cooling on a short borehole."""
    dt = 3600
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # 100 W/m on purpose: a large own-step response
        model = GroundSourceHeatPump(
            dt_s=dt, t_max_s=86400.0, UA_iu_rated=2000.0, Ts=10.0, H_b=60.0, N_1=1, N_2=1, hp_capacity=8000.0
        )
    heating = model.analyze_dynamic(2 * dt, dt, np.full(2, -8000.0), np.zeros(2), np.full(2, 20.0))
    cooling = model.analyze_dynamic(2 * dt, dt, np.full(2, 8000.0), np.full(2, 30.0), np.full(2, 27.0))
    return model, heating, cooling


def test_evaporator_is_colder_than_the_ground_fluid_of_the_same_row(cold_ground_run):
    _, heating, _ = cold_ground_run
    assert heating["hp_is_on"].all() and (heating["failure_reason"] == "none").all()
    # Before: the first row paired an evaporator at +3 °C (cycle solved on the
    # undisturbed 10 °C ground) with a fluid at -6 °C (after the step's load).
    margin = heating["T_bhe_f_in [°C]"] - heating["T_ref_evap_sat [°C]"]
    assert (margin > 0.5).all(), margin.tolist()
    assert (heating["T_bhe_f_out [°C]"] < 10.0 - 5.0).all()  # the first step already sees its own load


def test_condenser_is_warmer_than_the_ground_fluid_of_the_same_row(cold_ground_run):
    _, _, cooling = cold_ground_run
    assert cooling["hp_is_on"].all() and (cooling["failure_reason"] == "none").all()
    margin = cooling["T_ref_cond_sat_l [°C]"] - cooling["T_bhe_f_in [°C]"]
    assert (margin > 0.5).all(), margin.tolist()


@pytest.mark.parametrize("mode", ["heating", "cooling"])
def test_reported_fluid_temperature_is_the_one_the_cycle_was_solved_with(cold_ground_run, mode):
    model, heating, cooling = cold_ground_run
    df = heating if mode == "heating" else cooling
    sign = -1.0 if mode == "heating" else 1.0
    m_cp = df["dV_bhe_f [m3/s]"] * model.source_fluid.rho * model.source_fluid.cp
    source = df["T_bhe_f_out [°C]"] + df["E_pmp [W]"] / m_cp
    saturation = df["T_ref_evap_sat [°C]"] if mode == "heating" else df["T_ref_cond_sat_l [°C]"]
    # Ground-side saturation = fluid entering the heat pump -/+ the solved approach.
    np.testing.assert_allclose(saturation, source + sign * df["ground_approach [K]"], atol=1e-3)
    # Borehole relation of the same row: wall - q' R_b* = mean fluid. With the dynamic
    # borehole (the default) that wall is the fluid-referenced one, the model's response to
    # the reported loads; the T_bhe column is the physical wall (test_borehole_dynamics_models.py).
    q_unit = (df["Q_bhe [W]"] / (model.n_boreholes * model.H_b)).to_numpy()
    mean = 0.5 * (df["T_bhe_f_in [°C]"] + df["T_bhe_f_out [°C]"])
    assert model.borehole_dynamics == "transit"
    kernel = np.asarray(model._gfunc_interp.step_mean(np.arange(len(q_unit)) * 3600.0, 3600.0))
    pulses = np.diff(np.concatenate(([0.0], q_unit)))
    wall = model.Ts - np.array([np.dot(pulses[: n + 1], kernel[n::-1]) for n in range(len(q_unit))])
    np.testing.assert_allclose(wall - q_unit * df["R_b_eff [mK/W]"], mean, atol=1e-3)
    np.testing.assert_allclose((df["T_bhe_f_out [°C]"] - df["T_bhe_f_in [°C]"]) * m_cp, df["Q_bhe [W]"], rtol=1e-4)


def test_repeating_the_dynamic_run_gives_the_same_table(cold_ground_run):
    model, heating, _ = cold_ground_run
    again = model.analyze_dynamic(2 * 3600, 3600, np.full(2, -8000.0), np.zeros(2), np.full(2, 20.0))
    for key in ("T_bhe_f_out [°C]", "T_ref_evap_sat [°C]", "E_cmp [W]", "Q_ref_iu [W]"):
        np.testing.assert_allclose(again[key], heating[key], rtol=1e-9, atol=1e-9)


# ── ground closure at the pressure-ratio floor (cooling) ──────────────────


def test_ground_closure_at_the_pressure_ratio_floor_closes_every_candidate():
    # 2.6 kW cooling against a 16 °C wall sits where the ground HX balance
    # needs a condensing temperature just above the pressure-ratio floor. The
    # closure used to step from the trial approach although the floor held the
    # condenser above it, crept 0.07 K per iteration and gave up on three of
    # the indoor approaches it tried (139 cycle evaluations for one point).
    model = build()
    statuses, cycles = [], [0]
    close, state = model._close_ground_side, model._calc_state

    def counted_close(*args, **kwargs):
        out = close(*args, **kwargs)
        statuses.append(out[0])
        return out

    def counted_state(*args, **kwargs):
        cycles[0] += 1
        return state(*args, **kwargs)

    model._close_ground_side, model._calc_state = counted_close, counted_state
    row = model._solve_operating_point(2600.0, 30.0, 27.0, wall_K=289.15)
    assert row["failure_reason"] == "none" and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(2600.0, rel=1e-9)
    assert "not_closed" not in statuses, statuses
    assert set(statuses) <= {"equality", "slack_floor", "slack_bound"}
    assert cycles[0] < 100
    # The operating point itself is unchanged (370.9 W with a water loop; the
    # default glycol's slightly larger borehole resistance adds 0.8 W; the 27 °C
    # room air, 3.9 % lighter than standard air, another 0.9 W, #96 -- 372.6 W in all).
    # 354.6 W with the catalogue displacement and indoor airflow of a 4 kW R32 unit (#86); 303.3 W
    # with the rotary-compressor default efficiencies and approach-temperature conductances; 298.3 W
    # with the default rotary speed shape ossorio_anchored (compressor 213.9 -> 196.9 W) and the
    # 12 W fixed control power.
    assert row["E_tot [W]"] == pytest.approx(298.3, rel=2e-3)
    assert row["E_ctrl [W]"] == 12.0
