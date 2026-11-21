"""Wet indoor coil of ``AirSourceHeatPump`` in cooling (#205).

With ``RH_room`` given, the indoor coil condenses water where its surface --
taken at the evaporating temperature -- is below the room air's dew point
(apparatus dew point / bypass factor, Lewis number 1). The coil's total heat
is the dry coil's with the driving temperature difference enlarged by
``(h_fg / c_a) * max(0, w_in - w_sat(T_e))``, so:

- without ``RH_room``, and with a coil above the dew point, every number is
  the dry coil's;
- the refrigerant-side heat is sensible plus latent, the condensate is the
  air mass flow times its humidity drop, and the air leaves on the straight
  line to saturated air at the evaporating temperature;
- the sensible heat ratio falls with the room humidity from 1 at the dew-point
  onset, and at a fixed total duty the evaporating temperature rises;
- the capacity ceiling, a dynamic run, and the cycling and frost means take
  the input and keep the balances.

The room-side keys are checked with it: ``Q_room_sensible [W]`` is the net
sensible heat the room air gives up (cooling) or receives (heating) after the
indoor fan's heat, and ``cop_sys [-]`` counts sensible plus latent.
"""

from __future__ import annotations

import math
import warnings

import CoolProp.CoolProp as CP
import numpy as np
import pytest
from CoolProp.HumidAirProp import HAPropsSI

from tmhp import AirSourceHeatPump
from tmhp.air_properties import air_density
from tmhp.constants import c_a
from tmhp.defrost import humidity_ratio, saturation_humidity_ratio
from tmhp.heat_exchanger import (
    calc_HX_perf_for_target_heat,
    water_latent_heat,
    wet_coil_latent_difference,
)

#: The issue's example: default 4 kW unit, 35 °C outdoor, 27 °C room, 4 kW cooling.
POINT = dict(Q_r_iu=4000.0, T0=35.0, T_a_room=27.0)
#: Keys the wet coil adds to every row.
WET_KEYS = (
    "Q_iu_sensible [W]",
    "Q_iu_latent [W]",
    "SHR_iu [-]",
    "m_dot_iu_condensate [kg/s]",
    "w_iu_a_in [kg/kg]",
    "w_iu_a_out [kg/kg]",
    "RH_room [-]",
    "Q_room_sensible [W]",
)
#: Relative tolerance of a coil's air-side heat against the refrigerant side: the coil solver
#: closes a coil on a fan limit within 1e-5 of its duty.
COIL_TOL = 2e-5


def _quiet(function, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return function(*args, **kwargs)


@pytest.fixture(scope="module")
def hp():
    return _quiet(AirSourceHeatPump)


@pytest.fixture(scope="module")
def dry(hp):
    return hp.analyze_steady(**POINT, verbose=False)


@pytest.fixture(scope="module")
def wet(hp):
    return hp.analyze_steady(**POINT, RH_room=0.5, verbose=False)


def _same_numbers(a: dict, b: dict, *, skip=()) -> list[str]:
    """Keys of ``a`` whose value differs in ``b`` (NaN equals NaN), exactly."""
    out = []
    for key, value in a.items():
        if key in skip:
            continue
        other = b[key]
        both_nan = isinstance(value, float) and isinstance(other, float) and math.isnan(value) and math.isnan(other)
        if not both_nan and value != other:
            out.append(f"{key}: {value!r} != {other!r}")
    return out


# ---------------------------------------------------------------------------
# The coil itself (tmhp.heat_exchanger)
# ---------------------------------------------------------------------------


def test_latent_driving_difference_is_zero_above_the_dew_point_and_the_stated_term_below():
    w_in = humidity_ratio(27.0, 0.5)
    dew_point_K = float(HAPropsSI("D", "T", 300.15, "P", 101325.0, "R", 0.5))
    assert wet_coil_latent_difference(w_in, dew_point_K + 0.01) == 0.0
    assert wet_coil_latent_difference(w_in, 300.15) == 0.0
    T_e = 283.15
    h_fg = CP.PropsSI("H", "T", T_e, "Q", 1, "Water") - CP.PropsSI("H", "T", T_e, "Q", 0, "Water")
    assert water_latent_heat(T_e) == pytest.approx(h_fg, rel=1e-12)
    expected = h_fg / c_a * (w_in - saturation_humidity_ratio(10.0))
    assert wet_coil_latent_difference(w_in, T_e) == pytest.approx(expected, rel=1e-12)
    assert 8.0 < expected < 10.0  # 8.8 K added to the 17 K between room air and coil
    # Continuous at the onset, and growing as the coil gets colder.
    assert 0.0 < wet_coil_latent_difference(w_in, dew_point_K - 0.01) < 0.03
    assert wet_coil_latent_difference(w_in, 278.15) > expected
    # No saturated liquid below the triple point: the latent heat stays at its triple-point value.
    assert water_latent_heat(265.0) == water_latent_heat(273.16) == pytest.approx(2.5009e6, rel=1e-4)
    with pytest.raises(ValueError):
        wet_coil_latent_difference(-1e-3, T_e)


def test_coil_solver_carries_the_total_duty_and_splits_it():
    coil = dict(
        Q_ref_target=4000.0,
        T_a_in_C=27.0,
        T_ref_sat_K=283.15,
        A_cross=0.1,
        UA_rated=640.0,
        dV_fan_ref=0.232,
        exponent=0.65,
        rho_air="inlet",
    )
    w_in = humidity_ratio(27.0, 0.5)
    dry_coil = calc_HX_perf_for_target_heat(**coil)
    wet_coil = calc_HX_perf_for_target_heat(**coil, w_a_in=w_in)
    assert wet_coil["converged"] and dry_coil["converged"]
    # The same duty with a larger driving difference: less air.
    assert wet_coil["dV_fan"] < dry_coil["dV_fan"]
    C, eps = wet_coil["C_air"], wet_coil["epsilon"]
    assert eps == pytest.approx(1.0 - math.exp(-wet_coil["UA"] / C), rel=1e-12)
    assert wet_coil["Q_sensible"] == pytest.approx(C * eps * 17.0, rel=1e-9)
    assert wet_coil["Q_latent"] == pytest.approx(C * eps * wet_coil["dT_latent"], rel=1e-9)
    assert wet_coil["Q_air"] == pytest.approx(4000.0, rel=1e-9)
    assert wet_coil["Q_air"] == wet_coil["Q_sensible"] + wet_coil["Q_latent"]
    # Leaving state: a fraction eps of the way to saturated air at the coil temperature.
    assert wet_coil["T_a_mid_C"] == pytest.approx(27.0 - eps * 17.0, rel=1e-12)
    assert wet_coil["w_a_mid"] == pytest.approx(w_in - eps * (w_in - saturation_humidity_ratio(10.0)), rel=1e-12)
    m_dot_air = C / c_a
    assert wet_coil["m_dot_condensate"] == pytest.approx(m_dot_air * (w_in - wet_coil["w_a_mid"]), rel=1e-12)
    assert wet_coil["Q_latent"] == pytest.approx(wet_coil["m_dot_condensate"] * water_latent_heat(283.15), rel=1e-12)


def test_coil_solver_above_the_dew_point_is_the_dry_coil_exactly():
    coil = dict(
        Q_ref_target=1500.0,
        T_a_in_C=27.0,
        T_ref_sat_K=293.15,  # 20 °C: above the 15.7 °C dew point
        A_cross=0.1,
        UA_rated=640.0,
        dV_fan_ref=0.232,
        exponent=0.65,
        rho_air="inlet",
    )
    w_in = humidity_ratio(27.0, 0.5)
    dry_coil = calc_HX_perf_for_target_heat(**coil)
    wet_coil = calc_HX_perf_for_target_heat(**coil, w_a_in=w_in)
    assert not _same_numbers(dry_coil, wet_coil)
    assert wet_coil["Q_latent"] == 0.0 and wet_coil["m_dot_condensate"] == 0.0 and wet_coil["w_a_mid"] == w_in
    assert wet_coil["Q_sensible"] == wet_coil["Q_air"]
    # A coil that heats the air ignores the humidity.
    heating = dict(coil, T_ref_sat_K=318.15, T_a_in_C=20.0)
    assert not _same_numbers(
        calc_HX_perf_for_target_heat(**heating), calc_HX_perf_for_target_heat(**heating, w_a_in=w_in)
    )


# ---------------------------------------------------------------------------
# Dry limit: today's numbers
# ---------------------------------------------------------------------------


def test_without_room_humidity_the_issue_example_is_the_dry_coil_of_before(dry):
    # Values of main 5552d106 (before the wet coil), default 4 kW unit, 35/27 °C, 4 kW.
    assert dry["T_iu_a_mid [°C]"] == pytest.approx(12.391108632378348, rel=1e-9)
    assert dry["T_ref_evap_sat [°C]"] == pytest.approx(10.82934642707579, rel=1e-9)
    assert dry["E_tot [W]"] == pytest.approx(931.4959798560261, rel=1e-9)
    assert dry["cop_sys [-]"] == pytest.approx(4.269341845880731, rel=1e-9)
    assert dry["Q_a_iu_out [W]"] - dry["Q_a_iu_in [W]"] == pytest.approx(-3976.874766068, rel=1e-9)
    # ... and the new keys say so: all sensible, no water, no humidity known.
    assert dry["Q_iu_latent [W]"] == 0.0 and dry["m_dot_iu_condensate [kg/s]"] == 0.0 and dry["SHR_iu [-]"] == 1.0
    assert dry["Q_iu_sensible [W]"] == pytest.approx(dry["Q_ref_iu [W]"], rel=COIL_TOL)
    for key in ("RH_room [-]", "w_iu_a_in [kg/kg]", "w_iu_a_out [kg/kg]"):
        assert math.isnan(dry[key]), key


def test_a_coil_that_never_reaches_the_dew_point_gives_the_dry_numbers_exactly(hp, dry):
    # 5 % at 27 °C: dew point -14 °C, below every evaporating temperature the search admits (-3 °C).
    row = hp.analyze_steady(**POINT, RH_room=0.05, verbose=False)
    differences = _same_numbers(dry, row, skip=("RH_room [-]", "w_iu_a_in [kg/kg]", "w_iu_a_out [kg/kg]"))
    assert not differences, differences
    assert row["RH_room [-]"] == 0.05
    assert row["w_iu_a_in [kg/kg]"] == row["w_iu_a_out [kg/kg]"] == pytest.approx(humidity_ratio(27.0, 0.05))
    assert row["Q_iu_latent [W]"] == 0.0 and row["SHR_iu [-]"] == 1.0


def test_a_part_load_coil_above_the_dew_point_is_dry_at_ordinary_humidity(hp):
    # 1.2 kW: the coil runs near 22 °C, above the 12.3 °C dew point of 27 °C / 40 %.
    request = dict(Q_r_iu=1200.0, T0=35.0, T_a_room=27.0, verbose=False)
    reference = hp.analyze_steady(**request)
    row = hp.analyze_steady(**request, RH_room=0.4)
    dew_point = float(HAPropsSI("D", "T", 300.15, "P", 101325.0, "R", 0.4)) - 273.15
    assert row["T_ref_evap_sat [°C]"] > dew_point + 2.0
    for key in ("E_tot [W]", "E_cmp [W]", "T_ref_evap_sat [°C]", "T_iu_a_out [°C]", "dV_iu_a [m3/s]", "cop_sys [-]"):
        assert row[key] == pytest.approx(reference[key], rel=1e-9), key
    assert row["Q_iu_latent [W]"] == 0.0 and row["SHR_iu [-]"] == 1.0


# ---------------------------------------------------------------------------
# The wet point
# ---------------------------------------------------------------------------


def test_wet_coil_energy_balance(wet):
    assert wet["failure_reason"] == "none" and wet["capacity_clamped"] is None
    assert wet["Q_ref_iu [W]"] == pytest.approx(4000.0, rel=1e-9)
    assert wet["Q_iu_sensible [W]"] + wet["Q_iu_latent [W]"] == pytest.approx(wet["Q_ref_iu [W]"], rel=COIL_TOL)
    assert 0.0 < wet["Q_iu_latent [W]"] < wet["Q_iu_sensible [W]"]
    assert wet["SHR_iu [-]"] == pytest.approx(
        wet["Q_iu_sensible [W]"] / (wet["Q_iu_sensible [W]"] + wet["Q_iu_latent [W]"]), rel=1e-12
    )
    assert 0.6 < wet["SHR_iu [-]"] < 0.95


def test_wet_coil_water_balance_and_leaving_state(wet):
    T_in, T_e = wet["T_iu_a_in [°C]"], wet["T_ref_evap_sat [°C]"]
    w_in, w_out = wet["w_iu_a_in [kg/kg]"], wet["w_iu_a_out [kg/kg]"]
    assert wet["RH_room [-]"] == 0.5 and w_in == pytest.approx(humidity_ratio(27.0, 0.5), rel=1e-12)
    m_dot_air = air_density(T_in) * wet["dV_iu_a [m3/s]"]
    assert wet["m_dot_iu_condensate [kg/s]"] == pytest.approx(m_dot_air * (w_in - w_out), rel=1e-9)
    h_fg = CP.PropsSI("H", "T", T_e + 273.15, "Q", 1, "Water") - CP.PropsSI("H", "T", T_e + 273.15, "Q", 0, "Water")
    assert wet["Q_iu_latent [W]"] == pytest.approx(wet["m_dot_iu_condensate [kg/s]"] * h_fg, rel=1e-9)
    assert wet["Q_iu_sensible [W]"] == pytest.approx(c_a * m_dot_air * (T_in - wet["T_iu_a_mid [°C]"]), rel=1e-9)
    # The air leaves on the straight line to saturated air at the evaporating temperature
    # (one bypass factor for temperature and humidity), still above the coil.
    w_coil = saturation_humidity_ratio(T_e)
    assert w_coil < w_out < w_in
    by_temperature = (T_in - wet["T_iu_a_mid [°C]"]) / (T_in - T_e)
    by_humidity = (w_in - w_out) / (w_in - w_coil)
    assert by_humidity == pytest.approx(by_temperature, rel=1e-9)
    # The fan adds heat, not water.
    assert wet["T_iu_a_out [°C]"] > wet["T_iu_a_mid [°C]"]


def test_evaporating_temperature_rises_and_input_falls_at_the_same_total_duty(dry, wet):
    assert wet["T_ref_evap_sat [°C]"] > dry["T_ref_evap_sat [°C]"] + 1.0
    assert wet["T_iu_a_mid [°C]"] > dry["T_iu_a_mid [°C]"] + 1.0
    assert wet["E_cmp [W]"] < dry["E_cmp [W]"]
    assert wet["Q_iu_sensible [W]"] < dry["Q_iu_sensible [W]"]


def test_sensible_heat_ratio_falls_with_room_humidity(hp):
    humidities = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    rows = [hp.analyze_steady(**POINT, RH_room=rh, verbose=False) for rh in humidities]
    assert all(row["failure_reason"] == "none" for row in rows)
    shr = [row["SHR_iu [-]"] for row in rows]
    assert shr[0] == shr[1] == 1.0  # dew points 1.9 and 7.9 °C, below the 10.8 °C coil
    assert all(a >= b for a, b in zip(shr, shr[1:], strict=False)), shr
    assert all(a > b + 0.02 for a, b in zip(shr[2:], shr[3:], strict=False)), shr
    assert shr[-1] < 0.6
    evaporating = [row["T_ref_evap_sat [°C]"] for row in rows]
    assert all(b >= a - 1e-3 for a, b in zip(evaporating, evaporating[1:], strict=False)), evaporating
    assert evaporating[-1] > evaporating[0] + 3.0


def test_the_onset_of_condensation_is_continuous(hp, dry):
    # Room humidity at which the dry coil's evaporating temperature is the dew point.
    onset = float(HAPropsSI("R", "T", 300.15, "P", 101325.0, "D", dry["T_ref_evap_sat [°C]"] + 273.15))
    assert 0.3 < onset < 0.45
    below = hp.analyze_steady(**POINT, RH_room=onset * 0.99, verbose=False)
    above = hp.analyze_steady(**POINT, RH_room=onset * 1.0005, verbose=False)
    assert below["SHR_iu [-]"] == 1.0 and below["Q_iu_latent [W]"] == 0.0
    assert 0.999 < above["SHR_iu [-]"] < 1.0 and 0.0 < above["Q_iu_latent [W]"] < 4.0
    # No step in the operating point: within the tolerance the search stops at (0.1 W, 1e-3 K).
    assert above["E_tot [W]"] == pytest.approx(below["E_tot [W]"], abs=0.5)
    for key in ("T_ref_evap_sat [°C]", "T_iu_a_out [°C]"):
        assert above[key] == pytest.approx(below[key], abs=0.02), key
    # From there the input falls steadily as the coil gets wetter.
    wetter = hp.analyze_steady(**POINT, RH_room=onset * 1.05, verbose=False)
    assert wetter["E_tot [W]"] < above["E_tot [W]"] - 5.0 and 0.95 < wetter["SHR_iu [-]"] < 0.99


# ---------------------------------------------------------------------------
# Room-side keys and the system COP
# ---------------------------------------------------------------------------


def test_room_sensible_heat_and_system_cop_count_the_fan_heat_once(hp, dry, wet):
    for row in (dry, wet):
        air = row["Q_a_iu_out [W]"] - row["Q_a_iu_in [W]"]  # dry-air enthalpy flow: sensible, negative in cooling
        assert row["Q_room_sensible [W]"] == pytest.approx(row["Q_iu_sensible [W]"] - row["E_iu_fan [W]"], rel=1e-12)
        assert row["Q_room_sensible [W]"] == pytest.approx(-air, rel=1e-9)
        room_total = row["Q_room_sensible [W]"] + row["Q_iu_latent [W]"]
        assert row["cop_sys [-]"] == pytest.approx(room_total / row["E_tot [W]"], rel=1e-9)
        assert row["indoor_heat_direction_valid"] is True
    # The issue's figure: 3 977 W of the 4 000 W reach the room as sensible cooling with a dry coil.
    assert dry["Q_room_sensible [W]"] == pytest.approx(3976.87, abs=0.01)
    assert wet["Q_room_sensible [W]"] < 0.9 * dry["Q_room_sensible [W]"]
    heating = hp.analyze_steady(Q_r_iu=-3000.0, T0=7.0, T_a_room=20.0, RH_room=0.5, verbose=False)
    air = heating["Q_a_iu_out [W]"] - heating["Q_a_iu_in [W]"]
    assert heating["Q_room_sensible [W]"] == pytest.approx(heating["Q_iu_sensible [W]"] + heating["E_iu_fan [W]"])
    assert heating["Q_room_sensible [W]"] == pytest.approx(air, rel=1e-9)
    assert heating["Q_iu_sensible [W]"] == pytest.approx(heating["Q_ref_iu [W]"], rel=COIL_TOL)


def test_request_and_unmet_stay_on_the_refrigerant_side(wet):
    assert wet["Q_request [W]"] == 4000.0 and wet["Q_unmet [W]"] == 0.0
    assert wet["Q_ref_iu [W]"] == pytest.approx(wet["Q_request [W]"], rel=1e-9)


def test_heating_ignores_the_room_humidity(hp):
    request = dict(Q_r_iu=-3000.0, T0=7.0, T_a_room=20.0, verbose=False)
    reference = hp.analyze_steady(**request)
    row = hp.analyze_steady(**request, RH_room=0.9)
    assert not _same_numbers(reference, row), _same_numbers(reference, row)
    assert math.isnan(row["RH_room [-]"]) and math.isnan(row["w_iu_a_in [kg/kg]"])
    assert row["Q_iu_latent [W]"] == 0.0 and row["SHR_iu [-]"] == 1.0


def test_every_row_has_the_wet_coil_keys(hp):
    steady = dict(verbose=False, postprocess=False)
    rows = {
        "wet": hp.analyze_steady(**POINT, RH_room=0.6, **steady),
        "dry": hp.analyze_steady(**POINT, **steady),
        "cycling": hp.analyze_steady(Q_r_iu=150.0, T0=35.0, T_a_room=27.0, RH_room=0.6, **steady),
        "ceiling": hp.analyze_steady(Q_r_iu=20000.0, T0=35.0, T_a_room=27.0, RH_room=0.6, **steady),
        "heating": hp.analyze_steady(Q_r_iu=-2500.0, T0=7.0, T_a_room=20.0, RH_room=0.6, **steady),
        "frosting": hp.analyze_steady(Q_r_iu=-2500.0, T0=0.0, T_a_room=20.0, **steady),
        "off": hp.analyze_steady(Q_r_iu=0.0, T0=35.0, T_a_room=27.0, RH_room=0.6, **steady),
        "failed": _quiet(AirSourceHeatPump, ref="R134a").analyze_steady(
            Q_r_iu=-6000.0, T0=-45.0, T_a_room=30.0, **steady
        ),
    }
    reference = set(rows["wet"])
    for name, row in rows.items():
        assert set(row) == reference, (name, sorted(set(row) ^ reference))
        for key in WET_KEYS:
            assert isinstance(row[key], float), (name, key, row[key])
    off = rows["off"]
    assert off["Q_iu_sensible [W]"] == off["Q_iu_latent [W]"] == off["Q_room_sensible [W]"] == 0.0
    assert off["m_dot_iu_condensate [kg/s]"] == 0.0 and math.isnan(off["SHR_iu [-]"])
    assert rows["failed"]["Q_iu_sensible [W]"] == 0.0 and math.isnan(rows["failed"]["SHR_iu [-]"])


@pytest.mark.parametrize("value", [-0.1, 1.2, math.nan, True, "0.5"])
def test_room_humidity_must_be_a_fraction(hp, value):
    with pytest.raises(ValueError, match=r"Room relative humidity must be a fraction in \[0, 1\]"):
        hp.analyze_steady(**POINT, RH_room=value, verbose=False)
    with pytest.raises(ValueError, match=r"Room relative humidity must be a fraction in \[0, 1\]"):
        hp.max_capacity(T0=35.0, T_a_room=27.0, mode="cooling", RH_room=value)


# ---------------------------------------------------------------------------
# Capacity ceiling
# ---------------------------------------------------------------------------


def test_max_capacity_takes_the_room_humidity(hp):
    dry_ceiling = hp.max_capacity(T0=35.0, T_a_room=27.0, mode="cooling")
    wet_ceiling = hp.max_capacity(T0=35.0, T_a_room=27.0, mode="cooling", RH_room=0.5)
    assert dry_ceiling["SHR_iu [-]"] == 1.0 and 0.5 < wet_ceiling["SHR_iu [-]"] < 0.95
    # Flat out the compressor moves more refrigerant at the higher evaporating pressure of the wet coil.
    assert wet_ceiling["binding"] == dry_ceiling["binding"] == "compressor"
    assert wet_ceiling["Q_max [W]"] > 1.02 * dry_ceiling["Q_max [W]"]
    assert wet_ceiling["dT_ref_evap [K]"] < dry_ceiling["dT_ref_evap [K]"]
    # analyze_steady above the ceiling runs at it, with the balances kept.
    row = hp.analyze_steady(Q_r_iu=20000.0, T0=35.0, T_a_room=27.0, RH_room=0.5, verbose=False)
    assert row["capacity_clamped"] == "max" and row["capacity_binding"] == "compressor"
    assert row["Q_ref_iu [W]"] == pytest.approx(wet_ceiling["Q_max [W]"], rel=1e-6)
    assert row["Q_iu_sensible [W]"] + row["Q_iu_latent [W]"] == pytest.approx(row["Q_ref_iu [W]"], rel=COIL_TOL)
    assert row["SHR_iu [-]"] == pytest.approx(wet_ceiling["SHR_iu [-]"], rel=1e-6)
    assert row["Q_unmet [W]"] == pytest.approx(20000.0 - row["Q_ref_iu [W]"], rel=1e-9)
    # Heating has no use for it.
    heating = hp.max_capacity(T0=7.0, T_a_room=20.0, mode="heating")
    assert not _same_numbers(heating, hp.max_capacity(T0=7.0, T_a_room=20.0, mode="heating", RH_room=0.5))


def test_indoor_coil_ceiling_rises_with_a_wet_coil():
    # A 15 kW unit moves less air per kW: at 20/27 °C its ceiling is the indoor coil at its largest
    # airflow and the widest approach. A wet coil carries more there.
    big = _quiet(AirSourceHeatPump, hp_capacity=15000.0)
    dry_ceiling = big.max_capacity(T0=20.0, T_a_room=27.0, mode="cooling")
    wet_ceiling = big.max_capacity(T0=20.0, T_a_room=27.0, mode="cooling", RH_room=0.5)
    assert dry_ceiling["binding"] == "cycle"
    assert wet_ceiling["Q_max [W]"] > 1.05 * dry_ceiling["Q_max [W]"]
    row = big.analyze_steady(Q_r_iu=60000.0, T0=20.0, T_a_room=27.0, RH_room=0.5, verbose=False)
    assert row["capacity_clamped"] == "max"
    assert row["Q_ref_iu [W]"] == pytest.approx(wet_ceiling["Q_max [W]"], rel=2e-3)
    assert row["Q_iu_sensible [W]"] + row["Q_iu_latent [W]"] == pytest.approx(row["Q_ref_iu [W]"], rel=COIL_TOL)


# ---------------------------------------------------------------------------
# Dynamic run
# ---------------------------------------------------------------------------


def test_dynamic_run_takes_a_room_humidity_schedule(hp):
    loads = [4000.0, 4000.0, 1200.0, 0.0, -3000.0]
    outdoor = [35.0, 35.0, 30.0, 30.0, 7.0]
    room = [27.0, 27.0, 26.0, 26.0, 20.0]
    humidity = [0.5, 0.7, 0.9, 0.5, 0.5]
    frame = _quiet(hp.analyze_dynamic, 5 * 3600, 3600, loads, outdoor, room, RH_room_schedule=humidity)
    for key in WET_KEYS:
        assert key in frame.columns, key
    for n in range(5):
        row = hp.analyze_steady(
            Q_r_iu=loads[n], T0=outdoor[n], T_a_room=room[n], RH_room=humidity[n], verbose=False, postprocess=False
        )
        for key in (*WET_KEYS, "E_tot [W]", "T_ref_evap_sat [°C]", "cop_sys [-]"):
            got, want = frame[key].iloc[n], row[key]
            assert (math.isnan(got) and math.isnan(want)) or got == pytest.approx(want, rel=1e-9), (n, key)
    assert frame["SHR_iu [-]"].iloc[1] < frame["SHR_iu [-]"].iloc[0] < 1.0
    assert list(frame["RH_room [-]"].iloc[:3]) == [0.5, 0.7, 0.9] and frame["RH_room [-]"].iloc[3:].isna().all()
    # Without the schedule the frame is the dry one.
    reference = _quiet(hp.analyze_dynamic, 5 * 3600, 3600, loads, outdoor, room)
    assert (reference["Q_iu_latent [W]"] == 0.0).all() and reference["RH_room [-]"].isna().all()
    assert frame["E_tot [W]"].iloc[4] == reference["E_tot [W]"].iloc[4]  # heating step
    assert frame["E_tot [W]"].iloc[0] < reference["E_tot [W]"].iloc[0]


def test_dynamic_run_checks_the_room_humidity_schedule(hp):
    arguments = (2 * 3600, 3600, [4000.0, 4000.0], [35.0, 35.0], [27.0, 27.0])
    with pytest.raises(ValueError, match=r"RH_room_schedule length \(3\) != tN \(2\)"):
        hp.analyze_dynamic(*arguments, RH_room_schedule=[0.5, 0.5, 0.5])
    with pytest.raises(ValueError, match="Room relative humidity must be a fraction"):
        _quiet(hp.analyze_dynamic, *arguments, RH_room_schedule=[0.5, 50.0])


# ---------------------------------------------------------------------------
# Cycling and frost means keep the balances
# ---------------------------------------------------------------------------


def test_cycling_mean_of_a_wet_coil_keeps_the_balances(hp):
    # 95 % at 27 °C: dew point 26.1 °C, so even the minimum-speed coil is wet.
    request = dict(Q_r_iu=150.0, T0=35.0, T_a_room=27.0, RH_room=0.95, verbose=False)
    row = hp.analyze_steady(**request)
    assert row["capacity_clamped"] == "min" and 0.0 < row["runtime_fraction [-]"] < 1.0
    assert row["Q_ref_iu [W]"] == pytest.approx(150.0, rel=1e-9)
    assert row["Q_iu_latent [W]"] > 0.0 and row["SHR_iu [-]"] < 1.0
    assert row["Q_iu_sensible [W]"] + row["Q_iu_latent [W]"] == pytest.approx(row["Q_ref_iu [W]"], rel=COIL_TOL)
    T_e = row["T_ref_evap_sat [°C]"] + 273.15
    h_fg = CP.PropsSI("H", "T", T_e, "Q", 1, "Water") - CP.PropsSI("H", "T", T_e, "Q", 0, "Water")
    assert row["Q_iu_latent [W]"] == pytest.approx(row["m_dot_iu_condensate [kg/s]"] * h_fg, rel=1e-9)
    # Mean flows of the cycling period: the mean air mass flow times the running machine's humidity drop.
    m_dot_air = air_density(27.0) * row["dV_iu_a [m3/s]"]
    drop = row["w_iu_a_in [kg/kg]"] - row["w_iu_a_out [kg/kg]"]
    assert row["m_dot_iu_condensate [kg/s]"] == pytest.approx(m_dot_air * drop, rel=1e-9)
    assert row["Q_room_sensible [W]"] == pytest.approx(row["Q_iu_sensible [W]"] - row["E_iu_fan [W]"], rel=1e-12)
    room_total = row["Q_room_sensible [W]"] + row["Q_iu_latent [W]"]
    assert row["cop_sys [-]"] == pytest.approx(room_total / row["E_tot [W]"], rel=1e-9)
    # The mean of the running machine's rates: its total coil heat is Q_min, and the split is its own.
    ratio = row["capacity_ratio [-]"]
    assert row["Q_iu_sensible [W]"] + row["Q_iu_latent [W]"] == pytest.approx(ratio * row["Q_min [W]"], rel=COIL_TOL)
    # The machine that runs without the cycling mean (its fans settle a little differently: the
    # search weighs the cycling cost) has the same sensible share and, scaled, the same rates.
    running = _quiet(AirSourceHeatPump, Cd_cycling=None).analyze_steady(**request)
    assert running["capacity_clamped"] == "min" and running["Q_ref_iu [W]"] > 150.0
    assert running["Q_ref_iu [W]"] == pytest.approx(row["Q_min [W]"], rel=5e-3)
    assert row["SHR_iu [-]"] == pytest.approx(running["SHR_iu [-]"], rel=5e-3)
    for key in ("Q_iu_sensible [W]", "Q_iu_latent [W]", "m_dot_iu_condensate [kg/s]"):
        assert row[key] / 150.0 == pytest.approx(running[key] / running["Q_ref_iu [W]"], rel=5e-3), key


def test_frost_mean_keeps_the_room_side_balance(hp):
    row = hp.analyze_steady(Q_r_iu=-2500.0, T0=0.0, T_a_room=20.0, RH_room=0.5, verbose=False)
    assert row["defrost_fraction [-]"] > 0.0 and row["Q_ref_iu [W]"] == pytest.approx(2500.0, rel=1e-9)
    assert row["Q_iu_latent [W]"] == 0.0 and row["m_dot_iu_condensate [kg/s]"] == 0.0 and row["SHR_iu [-]"] == 1.0
    assert row["Q_iu_sensible [W]"] == pytest.approx(row["Q_ref_iu [W]"], rel=COIL_TOL)
    air = row["Q_a_iu_out [W]"] - row["Q_a_iu_in [W]"]
    assert row["Q_room_sensible [W]"] == pytest.approx(air, rel=1e-9)
    assert row["Q_room_sensible [W]"] == pytest.approx(row["Q_iu_sensible [W]"] + row["E_iu_fan [W]"], rel=1e-12)
    assert row["cop_sys [-]"] == pytest.approx(row["Q_room_sensible [W]"] / row["E_tot [W]"], rel=1e-9)


# ---------------------------------------------------------------------------
# Exergy columns: dry-air thermal exergy; no indoor-coil balance where water is removed
# ---------------------------------------------------------------------------


def test_exergy_columns_stay_those_of_a_dry_air_stream(hp, wet):
    frame_row = hp.postprocess_exergy(
        hp.analyze_steady(**POINT, RH_room=0.5, verbose=False, postprocess=False, return_dict=False)
    ).iloc[0]
    # Thermal exergy of the dry air stream and the stream sums of the coil: reported as on a dry row.
    for key in ("X_a_iu_in [W]", "X_a_iu_mid [W]", "X_a_iu_out [W]", "X_in_iu_hx [W]", "X_out_iu_hx [W]"):
        assert np.isfinite(wet[key]) and wet[key] == frame_row[key], key
    # The coil removes water: the refrigerant side carries the latent heat and the air side
    # does not, so the difference is not the coil's destruction and is not reported (it was
    # pinned finite and positive here before the review of #205; the dry rows keep theirs,
    # tests/test_ashp_wet_coil_limits.py).
    assert wet["Q_iu_latent [W]"] > 0.0
    for key in ("Xc_iu_hx [W]", "X_eff_iu_hx [-]", "X_eff_sys [-]"):
        assert math.isnan(wet[key]) and math.isnan(frame_row[key]), key


# ---------------------------------------------------------------------------
# Leaving air is never beyond saturation
# ---------------------------------------------------------------------------


def test_coil_line_that_crosses_the_saturation_curve_ends_on_it():
    # Saturated room air and a coil 17 K colder: the straight line between two points of the
    # (convex) saturation curve lies above it, so the leaving air is put on the curve at the same
    # enthalpy -- the same total heat, more of it latent.
    coil = dict(
        Q_ref_target=4000.0,
        T_a_in_C=27.0,
        T_ref_sat_K=283.15,
        A_cross=0.1,
        UA_rated=640.0,
        dV_fan_ref=0.232,
        exponent=0.65,
        rho_air="inlet",
    )
    w_in = humidity_ratio(27.0, 1.0)
    out = calc_HX_perf_for_target_heat(**coil, w_a_in=w_in)
    C, eps = out["C_air"], out["epsilon"]
    on_the_line = (27.0 - eps * 17.0, w_in - eps * (w_in - saturation_humidity_ratio(10.0)))
    assert on_the_line[1] > saturation_humidity_ratio(on_the_line[0])  # the line itself is in the fog
    assert out["w_a_mid"] == pytest.approx(saturation_humidity_ratio(out["T_a_mid_C"]), rel=1e-9)
    assert out["T_a_mid_C"] > on_the_line[0] and out["w_a_mid"] < on_the_line[1]
    # Total heat as before the correction, and the balances of the split.
    assert out["converged"] and out["Q_air"] == pytest.approx(4000.0, rel=1e-9)
    assert out["Q_air"] == pytest.approx(C * eps * (17.0 + out["dT_latent"]), rel=1e-9)
    assert out["Q_sensible"] == pytest.approx(C * (27.0 - out["T_a_mid_C"]), rel=1e-9)
    assert out["m_dot_condensate"] == pytest.approx(C / c_a * (w_in - out["w_a_mid"]), rel=1e-12)
    assert out["Q_latent"] == pytest.approx(out["m_dot_condensate"] * water_latent_heat(283.15), rel=1e-12)
    assert out["Q_sensible"] + out["Q_latent"] == pytest.approx(out["Q_air"], rel=1e-9)
    # An ordinary room is not affected: the leaving air is on the line, below saturation.
    w_dry = humidity_ratio(27.0, 0.5)
    ordinary = calc_HX_perf_for_target_heat(**coil, w_a_in=w_dry)
    assert ordinary["w_a_mid"] < saturation_humidity_ratio(ordinary["T_a_mid_C"])
    assert ordinary["T_a_mid_C"] == pytest.approx(27.0 - ordinary["epsilon"] * 17.0, rel=1e-12)


@pytest.mark.parametrize("load", [150.0, 1500.0, 4000.0, 20000.0])
@pytest.mark.parametrize("humidity", [0.5, 0.8, 0.9, 0.95, 1.0])
def test_supply_air_is_never_beyond_saturation(hp, load, humidity):
    row = hp.analyze_steady(Q_r_iu=load, T0=35.0, T_a_room=27.0, RH_room=humidity, verbose=False, postprocess=False)
    assert row["failure_reason"] == "none"
    limit = saturation_humidity_ratio(row["T_iu_a_mid [°C]"])
    assert row["w_iu_a_out [kg/kg]"] <= limit * (1.0 + 1e-9), (row["w_iu_a_out [kg/kg]"], limit)
    assert row["Q_iu_sensible [W]"] + row["Q_iu_latent [W]"] == pytest.approx(row["Q_ref_iu [W]"], rel=COIL_TOL)
    assert row["Q_iu_sensible [W]"] > 0.0 and 0.0 < row["SHR_iu [-]"] <= 1.0
