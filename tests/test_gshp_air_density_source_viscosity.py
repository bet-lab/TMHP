"""GSHP indoor-coil air density and the source-side viscosity of the liquid-source hosts (#96).

(a) The GSHP indoor coil, physical and empirical, takes the density of the room
air it draws (``tmhp.air_properties.air_density``), as the ASHP coils do.
The air-side balance of the physical model is checked per row in
``tests/invariants/test_air_side_balance.py``.

(b) The source-side Reynolds number of GSHP, GSHPB and WSHPB (laminar-flow
warning, pipe convection inside ``R_b``) takes the liquid viscosity at the
loop's design temperature (``SourceFluid.design_viscosity``), not the 20 °C
constant ``mu_w`` for water.
"""

from __future__ import annotations

import math
import warnings

import CoolProp.CoolProp as CP
import numpy as np
import pytest

import tmhp
from tmhp.air_properties import air_density, air_heat_capacity_rate
from tmhp.ground_defaults import calc_borehole_reynolds
from tmhp.constants import c_a, mu_w, water_viscosity
from tmhp.gshp_empirical import GroundSourceHeatPumpEmpirical
from tmhp.heat_exchanger import AirCoilSide
from tmhp.source_fluid import (
    GROUND_LOOP_DESIGN_INLET_C,
    WATER,
    WATER_LOOP_DESIGN_INLET_C,
    SourceFluid,
    resolve_source_fluid,
)


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


@pytest.fixture(scope="module")
def gshp():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return tmhp.GroundSourceHeatPump()


# ---------------------------------------------------------------------------
# (a) GSHP indoor coil: density of the room air drawn in
# ---------------------------------------------------------------------------


def test_gshp_rating_side_carries_the_inlet_air_density(gshp):
    load = gshp.rated_condition.load
    assert isinstance(load, AirCoilSide)
    assert load.density == pytest.approx(air_density(load.T_in_C), rel=1e-12)


def test_gshp_rating_point_reproduces_the_rated_speed(gshp):
    """Reference state and cycle use one indoor air density at the rating point (ISO 13256-1 cooling).

    With a water loop: the rating side of the loop is water (``HXSide``), so a
    glycol loop leaves a separate 0.25 % gap from its own properties.
    """
    rating = gshp.rated_condition
    hp = tmhp.GroundSourceHeatPump(Ts=rating.source.T_in_C, source_fluid="water")
    row = hp.analyze_steady(Q_r_iu=hp.hp_capacity, T0=35.0, T_a_room=rating.load.T_in_C)
    assert row["failure_reason"] == "none"
    assert row["T_bhe_f_out [°C]"] == pytest.approx(rating.source.T_in_C, abs=1e-9)
    assert row["cmp_rpm [rpm]"] / 60.0 == pytest.approx(hp.rps_rated, rel=1e-3)


@pytest.mark.parametrize("Q_r_iu", [8000.0, -6000.0])
def test_gshp_air_exergy_uses_the_coil_mass_flow(gshp, Q_r_iu):
    """The post-processed air exergy flows use ``rho(T_iu_a_in) * dV`` like the cycle solve."""
    from tmhp.enex_functions import calc_exergy_flow

    T_room = 26.0 if Q_r_iu > 0 else 20.0
    row = gshp.analyze_steady(Q_r_iu=Q_r_iu, T0=10.0, T_a_room=T_room, T_bhe_wall=15.0)
    assert row["failure_reason"] == "none"
    import pandas as pd

    df = gshp.postprocess_exergy(pd.DataFrame([row]))
    G = air_heat_capacity_rate(row["dV_iu_a [m3/s]"], T_room)
    expected = calc_exergy_flow(G, row["T_iu_a_out [°C]"] + 273.15, 10.0 + 273.15)
    assert float(df["X_a_iu_out [W]"].iloc[0]) == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize("Q_r_iu", [-6000.0, 8000.0])
def test_empirical_airflow_carries_the_served_heat_at_room_air_density(Q_r_iu):
    """``c_a * rho(T_room) * dV_a * |dT| = Q_served``; the air exergy uses the same mass flow."""
    m = GroundSourceHeatPumpEmpirical(sim_hours=4)
    m.Q_r_iu, m.T0 = Q_r_iu, 10.0
    m.system_update()
    assert m.failure_reason == "none"
    T_room = m.T_a_iu_in_K - 273.15
    C_air = c_a * air_density(T_room) * m.dV_a
    assert C_air * abs(m.T_a_iu_out - T_room) == pytest.approx(abs(m.Q_iu), rel=1e-9)
    T0_K = m.T0_K
    expected = C_air * ((m.T_a_iu_out_K - T0_K) - T0_K * math.log(m.T_a_iu_out_K / T0_K))
    assert m.X_a_iu_out == pytest.approx(expected, rel=1e-9)


def test_empirical_design_airflow_stays_on_standard_air():
    """The design volume flow (fan curve reference) is a sizing value: 10 K in standard air."""
    m = GroundSourceHeatPumpEmpirical(sim_hours=4)
    assert m.dV_iu_fan_design == pytest.approx(max(m.Q_rated_cooling, m.Q_rated_heating) / (1.225 * c_a * 10.0))


# ---------------------------------------------------------------------------
# (b) viscosity of the loop liquid
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("T_C", [0.01, 3.0, 10.0, 20.0, 35.0, 60.0, 90.0])
def test_water_viscosity_follows_iapws(T_C):
    reference = CP.PropsSI("V", "T", T_C + 273.15, "P", 2.0e5, "Water")
    assert water_viscosity(T_C) == pytest.approx(reference, rel=2.5e-3)


def test_water_viscosity_arrays_and_the_constant():
    T = np.array([0.0, 10.0, 20.0, 40.0])
    out = water_viscosity(T)
    assert out.shape == T.shape and np.all(np.diff(out) < 0)
    assert water_viscosity(20.0) == pytest.approx(mu_w, rel=3e-3)  # mu_w is water near 20 °C
    assert water_viscosity(0.0) / mu_w == pytest.approx(1.79, abs=0.01)


def test_source_fluid_viscosity_by_temperature():
    meg = resolve_source_fluid("MEG-25%")
    # The mixture's properties are read at 0 °C: its viscosity there is the field.
    assert meg.viscosity(0.0) == pytest.approx(meg.mu, rel=1e-12)
    assert meg.viscosity(20.0) == pytest.approx(CP.PropsSI("V", "T", 293.15, "P", 2.0e5, "INCOMP::MEG[0.25]"), rel=1e-12)
    assert meg.viscosity(20.0) < meg.mu
    # Held at the freezing point below it, and at the CoolProp range above it.
    assert meg.viscosity(-40.0) == pytest.approx(meg.viscosity(meg.T_freeze_C), rel=1e-12)
    ethanol = resolve_source_fluid("ethanol-20%")
    assert ethanol.viscosity(80.0) == pytest.approx(ethanol.viscosity(40.0), rel=1e-12)
    assert WATER.viscosity(10.0) == pytest.approx(water_viscosity(10.0), rel=1e-12)
    # A fluid the caller builds names no source: its viscosity is the given one.
    own = SourceFluid("brine", -15.0, 1100.0, 3500.0, 4e-3, 0.5)
    assert own.viscosity(-10.0) == own.viscosity(30.0) == 4e-3
    # The source is not part of the comparison: equal properties, equal fluids.
    assert WATER == SourceFluid("water", 0.0, 1000.0, 4186.0, 1e-3, 0.606)


def test_design_viscosity_is_not_colder_than_the_operating_limit():
    assert WATER.limit_C == pytest.approx(3.0)
    assert WATER.design_viscosity(GROUND_LOOP_DESIGN_INLET_C) == pytest.approx(water_viscosity(3.0), rel=1e-12)
    assert WATER.design_viscosity(WATER_LOOP_DESIGN_INLET_C) == pytest.approx(water_viscosity(10.0), rel=1e-12)
    meg = resolve_source_fluid("MEG-25%")
    assert meg.design_viscosity(GROUND_LOOP_DESIGN_INLET_C) == pytest.approx(meg.mu, rel=1e-12)


def _defaults(cls) -> dict:
    import inspect

    return {k: p.default for k, p in inspect.signature(cls).parameters.items()}


def _wshpb_resistance(model, mu):
    from tmhp.g_function import calc_submerged_coil_thermal_resistance

    d, f = _defaults(tmhp.WaterSourceHeatPumpBoiler), model.source_fluid
    return calc_submerged_coil_thermal_resistance(
        r_out=d["r_out"],
        r_in=d["r_in"],
        D_s=d["D_s"],
        k_p=d["k_p"],
        m_flow_pipe=model.dV_b_f_m3s * f.rho / max(1, model.N_1 * model.N_2),
        rho_f=f.rho,
        mu_f=mu,
        cp_f=f.cp,
        k_f=f.k,
        v_river=model.v_river,
    )


def test_wshpb_source_resistance_uses_water_at_its_rating_inlet():
    """W10: the default water loop is evaluated at 10 °C (1.31 mPa s), not 20 °C (1.00)."""
    m = tmhp.WaterSourceHeatPumpBoiler()
    assert m.mu_bhe_f_design == pytest.approx(water_viscosity(WATER_LOOP_DESIGN_INLET_C), rel=1e-12)
    assert m.R_b == pytest.approx(_wshpb_resistance(m, water_viscosity(10.0)), rel=1e-12)
    assert m.R_b != pytest.approx(_wshpb_resistance(m, mu_w), rel=1e-3)


@pytest.mark.parametrize("cls", [tmhp.GroundSourceHeatPump, tmhp.GroundSourceHeatPumpBoiler])
def test_ground_loop_hosts_take_the_design_viscosity(cls):
    """Glycol (default): 0 °C, unchanged. Water: its operating limit, 3 °C (1.62 mPa s, not 1.00)."""
    default = cls()
    assert default.mu_bhe_f_design == pytest.approx(default.source_fluid.mu, rel=1e-12)
    water = cls(source_fluid="water")
    assert water.mu_bhe_f_design == pytest.approx(water_viscosity(3.0), rel=1e-12)
    # The flow-dependent borehole resistance is tabulated with the same viscosity.
    assert water._ground_settings["geometry"]["mu_f"] == pytest.approx(water_viscosity(3.0), rel=1e-12)
    if cls is tmhp.GroundSourceHeatPump:
        d = _defaults(cls)
        expected = calc_borehole_reynolds(
            water.dV_b_f_m3s, water.n_boreholes, 2 * d["r_in"], rho=1000.0, mu=water_viscosity(3.0)
        )
        assert water.Re_bhe_ref == pytest.approx(expected, rel=1e-12)
