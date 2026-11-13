"""GSHP ground-side and indoor-air defaults follow the capacity (#96).

With only ``hp_capacity`` given, the loop flow, pump power, borefield and
indoor airflow scale with it, so the machine is the same at every size: the
rating-point COP does not depend on the capacity. A value the caller supplies
is used as given, and a loop the caller sized badly is reported, not silently
solved.
"""

import math
import warnings

import pytest

from tmhp import GroundSourceHeatPump
from tmhp.constants import c_w, rho_w
from tmhp.ground_loop import (
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
CAPACITIES = (2000.0, 4000.0, 30000.0, 100000.0)


def build(**kwargs):
    """Model built with every warning turned into an error (defaults must be quiet)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return GroundSourceHeatPump(**SHORT, **kwargs)


@pytest.fixture(scope="module")
def by_capacity():
    """Liquid entering at 0 °C (``Ts = 0``): the ISO 13256-1 heating point."""
    return {cap: build(hp_capacity=cap, Ts=0.0) for cap in CAPACITIES}


# ── policy functions (shared ground modules) ──────────────────────────────


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
    assert model.ground_flow_ref_lpm == pytest.approx(14.33, abs=0.01)
    assert model.dV_b_f_m3s == pytest.approx(model.ground_flow_ref_lpm / 60000.0)
    assert model.E_pmp == pytest.approx(85.3, abs=0.05)
    assert model.dV_iu_fan_a_ref == pytest.approx(4000.0 * 5.37e-5)
    assert model.q_bhe_rated == pytest.approx(30.0)
    assert model.Re_bhe_ref > 10000


def test_every_default_scales_with_the_capacity(by_capacity):
    base = by_capacity[4000.0]
    for cap, model in by_capacity.items():
        ratio = cap / 4000.0
        assert model.ground_flow_ref_lpm == pytest.approx(ratio * base.ground_flow_ref_lpm)
        assert model.E_pmp == pytest.approx(ratio * base.E_pmp)
        assert model.dV_iu_fan_a_ref == pytest.approx(ratio * base.dV_iu_fan_a_ref)
        assert model.E_iu_fan_rated == pytest.approx(ratio * base.E_iu_fan_rated)
        assert model.UA_ground_rated == pytest.approx(ratio * base.UA_ground_rated)
        # The same machine at every size: one rated speed, one pressure ratio.
        assert model.rps_rated == pytest.approx(base.rps_rated, rel=1e-6)
        assert model.q_bhe_rated <= BOREHOLE_DESIGN_LINEAR_LOAD + 1e-9
        assert model.Re_bhe_ref > 2300
    assert by_capacity[30000.0].n_boreholes == 6 and by_capacity[100000.0].n_boreholes == 16


def test_rating_point_cop_does_not_depend_on_the_capacity(by_capacity):
    # Before: 30 kW ran on the 4 kW loop (20 L/min, 100 W) and lost 12 % of the
    # 4 kW system COP at this point; 100 kW could not be constructed at all.
    rows = {
        cap: model.analyze_steady(Q_r_iu=-cap, T0=0.0, T_a_room=20.0)  # liquid entering 0 °C, room 20 °C
        for cap, model in by_capacity.items()
    }
    base = rows[4000.0]
    for cap, row in rows.items():
        assert row["failure_reason"] == "none" and row["capacity_clamped"] is None, cap
        assert row["Q_ref_iu [W]"] == pytest.approx(cap, rel=1e-9)
        for key in ("cop_sys [-]", "cop_ref [-]"):
            assert row[key] == pytest.approx(base[key], rel=1e-4), (cap, key)
        loop_dT = row["T_bhe_f_out [°C]"] - row["T_bhe_f_in [°C]"]
        assert loop_dT == pytest.approx(base["T_bhe_f_out [°C]"] - base["T_bhe_f_in [°C]"], abs=1e-3)
        assert 2.5 < loop_dT < 3.5
        assert row["E_pmp [W]"] / cap == pytest.approx(75.0 / 3516.853)
    assert 3.2 < base["cop_sys [-]"] < 4.2


def test_rated_duty_is_delivered_with_a_plausible_supply_air_temperature(by_capacity):
    model = by_capacity[4000.0]
    heating = model.analyze_steady(Q_r_iu=-4000.0, T0=0.0, T_a_room=20.0)
    assert 33.0 < heating["T_iu_a_out [°C]"] < 50.0  # was 24 °C at 0.0002 m3/s per W
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
    # 3.8 at 4 kW. The default field now holds the specific load within
    # 50 W/m, which leaves a few percent between sizes.
    cops = {}
    for cap in (4000.0, 30000.0):
        row = build(hp_capacity=cap).analyze_steady(Q_r_iu=-cap, T0=0.0, T_a_room=20.0, T_bhe_wall=0.0)
        assert row["failure_reason"] == "none" and row["capacity_clamped"] is None, cap
        cops[cap] = row["cop_sys [-]"]
    assert cops[30000.0] == pytest.approx(cops[4000.0], rel=0.05)


def test_supplied_values_are_used_as_given():
    model = build(hp_capacity=16000.0, N_1=3, N_2=3)
    assert (model.N_1, model.N_2, model.n_boreholes, model.H_b) == (3, 3, 9, 100.0)
    assert model.ground_flow_ref_lpm == pytest.approx(4 * 14.33, abs=0.05)  # flow still follows the capacity
    assert model.Re_bhe_ref == pytest.approx(5200.0, rel=0.01)
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
    # The former default: 20.04 L/min shared by nine boreholes.
    with pytest.warns(UserWarning, match=r"laminar at the reference flow: Re = 1817 .*9 parallel borehole"):
        model = GroundSourceHeatPump(**SHORT, hp_capacity=16000.0, N_1=3, N_2=3, ground_flow_ref_lpm=20.04)
    assert model.Re_bhe_ref == pytest.approx(1817.0, abs=1.0)
    with pytest.warns(UserWarning, match="laminar at the constant setpoint flow"):
        GroundSourceHeatPump(**SHORT, ground_flow_constant_lpm=2.5, ground_flow_min_lpm=2.0)


def test_borefield_too_small_for_the_capacity_is_reported_at_construction():
    with pytest.warns(UserWarning, match=r"borefield is small for the capacity.*100 W/m"):
        model = GroundSourceHeatPump(**SHORT, hp_capacity=8000.0, H_b=60.0)
    assert model.q_bhe_rated == pytest.approx(100.0)
    assert build(hp_capacity=8000.0, N_1=1).q_bhe_rated == pytest.approx(60.0)  # within 80 W/m: quiet
