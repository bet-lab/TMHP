"""Dry-air density and the air heat capacity rate (``tmhp.air_properties``)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tmhp.air_properties import air_density, air_heat_capacity_rate
from tmhp.constants import P0_PA, R_a, c_a, rho_a
from tmhp.heat_exchanger import AirCoilSide
from tmhp.reference_state import HXSide


def test_standard_air_is_the_library_constant():
    """15 °C, 101 325 Pa: the ideal-gas density is ``constants.rho_a`` (1.225)."""
    assert air_density(15.0) == pytest.approx(rho_a, rel=1e-4)


@pytest.mark.parametrize(
    ("T_C", "expected"),
    # ISO 2533 standard-atmosphere / ideal-gas dry-air densities at 101 325 Pa [kg/m3]
    [(-25.0, 1.4225), (-7.0, 1.3262), (0.0, 1.2923), (7.0, 1.2600), (20.0, 1.2041), (27.0, 1.1760), (35.0, 1.1455)],
)
def test_density_follows_the_ideal_gas_law(T_C, expected):
    assert air_density(T_C) == pytest.approx(expected, rel=2e-4)
    assert air_density(T_C) == pytest.approx(P0_PA / (R_a * (T_C + 273.15)), rel=1e-12)


def test_density_scales_with_pressure_and_falls_with_temperature():
    assert air_density(20.0, 0.8 * P0_PA) == pytest.approx(0.8 * air_density(20.0), rel=1e-12)
    assert air_density(-25.0) > air_density(15.0) > air_density(35.0)
    # The range the air-source models span: +16 % / -6.5 % against standard air.
    assert air_density(-25.0) / rho_a == pytest.approx(1.161, abs=1e-3)
    assert air_density(35.0) / rho_a == pytest.approx(0.935, abs=1e-3)


def test_array_inputs_keep_their_shape():
    T = np.array([-25.0, 15.0, 35.0])
    assert air_density(T).shape == T.shape
    series = air_heat_capacity_rate(pd.Series([0.5, 0.0, 0.8]), pd.Series(T))
    assert isinstance(series, pd.Series)
    assert series.iloc[1] == 0.0
    assert series.iloc[0] == pytest.approx(c_a * air_density(-25.0) * 0.5)


def test_invalid_state_is_rejected():
    with pytest.raises(ValueError):
        air_density(-273.15)
    with pytest.raises(ValueError):
        air_density(20.0, 0.0)


def test_rating_side_of_an_air_coil_carries_the_inlet_air_density():
    """``AirCoilSide`` rates the coil with the air it draws in; ``HXSide`` stays on standard air."""
    standard = HXSide("air", -25.0, 800.0, 0.8)
    cold = AirCoilSide("air", -25.0, 800.0, 0.8)
    assert cold.density == pytest.approx(air_density(-25.0), rel=1e-12)
    assert cold.volume_flow == 0.8  # the fan's volume flow, not an equivalent one
    C = c_a * air_density(-25.0) * 0.8
    assert cold.conductance == pytest.approx(C * (1.0 - np.exp(-800.0 / C)), rel=1e-12)
    assert cold.conductance > standard.conductance
    assert AirCoilSide("air", 7.0, 800.0, 0.8, density=rho_a).conductance == HXSide("air", 7.0, 800.0, 0.8).conductance
    # Two rating points that differ only in density are different conditions (cache key).
    assert AirCoilSide("air", 7.0, 800.0, 0.8, density=1.2) != AirCoilSide("air", 7.0, 800.0, 0.8, density=1.3)
    assert hash(cold) == hash(AirCoilSide("air", -25.0, 800.0, 0.8))
    with pytest.raises(ValueError):
        AirCoilSide("air", 7.0, 800.0, 0.8, density=0.0)
    with pytest.raises(ValueError):
        AirCoilSide("water", 7.0, 800.0, 0.8)


def test_the_rating_point_reproduces_the_rated_speed():
    """The reference state and the cycle use one air density at the rating point.

    Solved with standard air instead, the rated speed of the default ASHP sat
    1.9 % below the speed its own cycle needs at the rating point.
    """
    from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler

    hp = AirSourceHeatPump()
    assert isinstance(hp.rated_condition.source, AirCoilSide)
    assert hp.rated_condition.source.density == pytest.approx(air_density(35.0))
    assert hp.rated_condition.load.density == pytest.approx(air_density(27.0))
    row = hp.analyze_steady(Q_r_iu=hp.hp_capacity, T0=35.0, T_a_room=27.0, verbose=False)
    assert row["cmp_rpm [rpm]"] / 60.0 == pytest.approx(hp.rps_rated, rel=1e-3)
    assert row["m_dot_ref [kg/s]"] == pytest.approx(hp.m_dot_ref_rated, rel=1e-3)

    boiler = AirSourceHeatPumpBoiler()
    assert boiler.rated_condition.source.density == pytest.approx(air_density(7.0))
    row = boiler.analyze_steady(T_tank_w=55.0, T0=7.0, Q_ref_tank=boiler.hp_capacity)
    assert row["cmp_rpm [rpm]"] / 60.0 == pytest.approx(boiler.rps_rated, rel=1e-6)
    assert row["m_dot_ref [kg/s]"] == pytest.approx(boiler.m_dot_ref_rated, rel=1e-6)
