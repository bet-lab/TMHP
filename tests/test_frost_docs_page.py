"""The frost sections of the model pages quote the default machines: these tests keep the figures the model's (#208).

``docs/source/models/ashp.rst`` and ``ashpb.rst`` ("Frost and defrost") carry
tables and sentences with numbers computed from a bare ``AirSourceHeatPump()``
and ``AirSourceHeatPumpBoiler()``. The default machine has changed several
times (catalogue inputs, R290 for the boiler, the compressor map) and the
boiler's table was left behind once. Every figure of the two sections is read
off the page here and compared with what the model returns, as
``tests/test_fan_control_page.py`` does for the fan section.

A quoted figure passes if it is a rounding of the model's value: half a unit
of its last digit, plus a tenth for the last digits of a solve that differ
between platforms. Figures of the frost model alone (no cycle solve) are
checked the same way.
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path

import pytest
from scipy.optimize import brentq

import tmhp.defrost
from tests._docs_figures import flat as _flat
from tests._docs_figures import quotes as _quotes
from tests._docs_figures import sentence as _sentence
from tests._docs_figures import table as _table
from tests._docs_figures import value as _value
from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp.defrost import DefrostModel, frost_point, humidity_ratio, saturation_humidity_ratio

DOCS = Path(__file__).resolve().parents[1] / "docs" / "source"
DEFAULT = DefrostModel()
#: EnergyPlus as published: a switch at the outdoor limit, the fixed losses whatever the air deposits, no load term.
ENERGYPLUS = DefrostModel(transition_K=0.0, frost_point_band_K=None, load_weighted=False)


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _build(cls, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return cls(**kwargs)


@pytest.fixture(scope="module")
def ashp_page() -> str:
    return (DOCS / "models" / "ashp.rst").read_text()


@pytest.fixture(scope="module")
def ashpb_page() -> str:
    return (DOCS / "models" / "ashpb.rst").read_text()


def _cop_multiplier(frost) -> float:
    return frost.capacity_multiplier / (frost.power_multiplier + frost.defrost_fraction)


# ---------------------------------------------------------------------------
# ashp.rst
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ashp() -> AirSourceHeatPump:
    return _build(AirSourceHeatPump)


def test_ashp_table_of_the_adjustment_at_the_rating_humidity(ashp_page: str) -> None:
    rows = _table(ashp_page, "Frost adjustment at 84 % relative humidity")
    assert list(rows)[1:] == ["5", "3.5", "2", "−7", "−15"]
    for label, cells in list(rows.items())[1:]:
        T_out = _value(label)
        frost = DEFAULT.frost_state(T_out)
        values = (0.82 * T_out - 8.589, frost.defrost_fraction, frost.capacity_multiplier, frost.power_multiplier)
        for cell, value, name in zip(cells, values, ("coil", "defrost_fraction", "capacity", "power"), strict=True):
            _quotes(cell, value, f"ashp.rst, frost adjustment at {label} °C, {name}")


def test_ashp_table_of_the_system_cop_by_load(ashp_page: str, ashp: AirSourceHeatPump) -> None:
    rows = _table(ashp_page, "System COP under frost, by load (84 % relative humidity)")
    assert rows["Request"] == ["2 °C", "0 °C", "−7 °C", "−15 °C"]
    outdoor = (2.0, 0.0, -7.0, -15.0)
    rated_load_form = _build(AirSourceHeatPump, defrost=DefrostModel(load_weighted=False))
    cases = {
        "3 kW (:math:`r = 1` at each)": (ashp, 3000.0),
        "2 kW": (ashp, 2000.0),
        "2 kW, ``load_weighted=False``": (rated_load_form, 2000.0),
        "1 kW (cycling at the minimum speed at 2 and 0 °C)": (ashp, 1000.0),
    }
    assert list(rows)[1:] == list(cases)
    assert ashp.hp_capacity == 4000.0  # "The default 4 kW unit against a 20 °C room"
    for label, (model, request) in cases.items():
        for cell, T0 in zip(rows[label], outdoor, strict=True):
            row = model.analyze_steady(Q_r_iu=-request, T0=T0, T_a_room=20.0, return_dict=True)
            assert row["failure_reason"] == "none" and row["Q_unmet [W]"] == 0.0
            _quotes(cell, row["cop_sys [-]"], f"ashp.rst, COP by load, {label} at {T0} °C")
            ratio = DefrostModel.load_ratio(row["Q_ref_iu_frost_free [W]"], model.hp_capacity, T0)
            if label.startswith("3 kW"):
                assert ratio == 1.0
            if label.startswith("1 kW"):
                assert (row["capacity_clamped"] == "min") is (T0 >= 0.0)


def test_ashp_step_of_the_energyplus_switch(ashp_page: str, ashp: AirSourceHeatPump) -> None:
    low, high = _sentence(
        ashp_page, "the COP of the default unit steps from {} to {} between 5.0 and 5.5 °C at nameplate capacity"
    )
    switch = _build(AirSourceHeatPump, defrost=DefrostModel(transition_K=0.0))
    at = {
        T0: switch.analyze_steady(Q_r_iu=-switch.hp_capacity, T0=T0, T_a_room=20.0, return_dict=True)
        for T0 in (5.0, 5.5)
    }
    assert all(row["Q_unmet [W]"] == 0.0 for row in at.values())
    _quotes(low, at[5.0]["cop_sys [-]"], "ashp.rst, EnergyPlus switch at 5.0 °C")
    _quotes(high, at[5.5]["cop_sys [-]"], "ashp.rst, EnergyPlus switch at 5.5 °C")
    # The default has no such step: at 5.0 degC the correction has not begun.
    default = ashp.analyze_steady(Q_r_iu=-ashp.hp_capacity, T0=5.0, T_a_room=20.0, return_dict=True)
    assert default["defrost_fraction [-]"] == 0.0 and default["cop_sys [-]"] > at[5.0]["cop_sys [-]"] + 1.0


def test_ashp_dry_air_paragraph_and_table(ashp_page: str) -> None:
    point, coil, multiplier, share = _sentence(
        ashp_page,
        "its frost point, {} °C, is below the coil at {} °C — and EnergyPlus still multiplies the COP by {}, "
        "with a defrost time of {} % of the runtime",
    )
    T_out, RH = -5.0, 0.50
    assert "At −5 °C and 50 % relative humidity the air cannot deposit on the coil" in _flat(ashp_page)
    _quotes(point, frost_point(T_out, humidity_ratio(T_out, RH)), "ashp.rst, frost point at −5 °C / 50 %")
    _quotes(coil, 0.82 * T_out - 8.589, "ashp.rst, coil at −5 °C")
    source = ENERGYPLUS.frost_state(T_out, RH)
    _quotes(multiplier, _cop_multiplier(source), "ashp.rst, EnergyPlus COP multiplier in dry air")
    _quotes(share, 100.0 * source.defrost_fraction, "ashp.rst, EnergyPlus defrost time in dry air [%]")
    assert not DEFAULT.frost_state(T_out, RH).active

    rows = _table(ashp_page, "COP multiplier of the frost adjustment at the rated load, by outdoor humidity")
    assert rows["Outdoor"] == ["84 %", "70 %", "60 %", "50 %", "30 %", "EnergyPlus, 30 %"]
    assert list(rows)[1:] == ["2 °C", "−5 °C", "−10 °C", "−15 °C"]
    for label, cells in list(rows.items())[1:]:
        T = _value(label.removesuffix(" °C"))
        values = [_cop_multiplier(DEFAULT.frost_state(T, rh)) for rh in (0.84, 0.70, 0.60, 0.50, 0.30)]
        values.append(_cop_multiplier(ENERGYPLUS.frost_state(T, 0.30)))
        for cell, value, column in zip(cells, values, rows["Outdoor"], strict=True):
            _quotes(cell, value, f"ashp.rst, COP multiplier at {label}, {column}")


def test_ashp_where_dry_air_stops_frosting_the_rated_load_coil(ashp_page: str) -> None:
    at_60, at_84 = _sentence(ashp_page, "(below {} °C at 60 % relative humidity, below {} °C at 84 %)")
    for cell, rh in ((at_60, 0.60), (at_84, 0.84)):
        # The outdoor temperature at which the air holds what saturated air holds at the coil of the correlation.
        limit = brentq(lambda T, rh=rh: humidity_ratio(T, rh) - saturation_humidity_ratio(0.82 * T - 8.589), -60.0, 0.0)
        _quotes(cell, limit, f"ashp.rst, no frost on the rated-load coil below, {rh:.0%}")
        assert not DEFAULT.frost_state(limit - 0.1, rh).active and DEFAULT.frost_state(limit + 0.1, rh).active
    before, T_before, after, T_after = _sentence(
        ashp_page, "the COP multiplier would fall from {} at {} °C to {} at {} °C"
    )
    without_band = DefrostModel(frost_point_band_K=None)
    for cell, T_cell in ((before, T_before), (after, T_after)):
        frost = without_band.frost_state(_value(T_cell), 0.60, load_ratio=0.5)
        _quotes(cell, _cop_multiplier(frost), f"ashp.rst, half load at 60 % without the band, {T_cell} °C")
    assert _value(T_after) == _value(at_60)


# ---------------------------------------------------------------------------
# ashpb.rst
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def boilers() -> dict[str, AirSourceHeatPumpBoiler]:
    return {
        "default": _build(AirSourceHeatPumpBoiler),
        "frost_free": _build(AirSourceHeatPumpBoiler, defrost=False),
        "rated_load_form": _build(AirSourceHeatPumpBoiler, defrost=DefrostModel(load_weighted=False)),
        "without_band": _build(AirSourceHeatPumpBoiler, defrost=DefrostModel(frost_point_band_K=None)),
    }


def test_ashpb_dry_air_paragraph(ashpb_page: str, boilers) -> None:
    here_50, source_50, here_60, source_60, rating = _sentence(
        ashpb_page,
        "At −5 °C and 50 % relative humidity the default 15 kW unit delivering 10 kW to a 40 °C tank draws {} W, "
        "the frost-free figure, where the EnergyPlus multipliers (``frost_point_band_K=None``) give {} W; "
        "at 60 % it draws {} W against {} W, and at the rating humidity, 84 %, {} W either way.",
    )
    point = {"T_tank_w": 40.0, "T0": -5.0, "Q_ref_tank": 10000.0}
    assert boilers["default"].hp_capacity == 15000.0

    def power(name: str, RH_out: float | None = None) -> float:
        row = boilers[name].analyze_steady(**point, RH_out=RH_out)
        assert row["failure_reason"] == "none" and row["capacity_clamped"] is None
        return float(row["E_tot [W]"])

    frost_free = power("frost_free")
    assert power("default", 0.50) == pytest.approx(frost_free, rel=1e-9)  # "the frost-free figure"
    _quotes(here_50, frost_free, "ashpb.rst, dry air, 50 %")
    _quotes(source_50, power("without_band", 0.50), "ashpb.rst, dry air, 50 %, EnergyPlus multipliers")
    _quotes(here_60, power("default", 0.60), "ashpb.rst, dry air, 60 %")
    _quotes(source_60, power("without_band", 0.60), "ashpb.rst, dry air, 60 %, EnergyPlus multipliers")
    _quotes(rating, power("default", 0.84), "ashpb.rst, dry air, 84 %")
    assert power("without_band", 0.84) == pytest.approx(power("default", 0.84), rel=1e-9)  # "either way"


def test_ashpb_table_of_the_system_cop_by_outdoor_temperature(ashpb_page: str, boilers) -> None:
    rows = _table(ashpb_page, "The default 15 kW unit delivering 8 kW to a 45 °C tank, system COP")
    assert rows["Outdoor air"] == ["7 °C", "4 °C", "2 °C", "0 °C", "−7 °C", "−15 °C"]
    assert list(rows)[1:] == [
        "Frost-free",
        "With frost and defrost",
        "Load ratio ``r``",
        "``defrost_fraction [-]``",
        "``load_weighted=False``",
    ]
    for column, label in enumerate(rows["Outdoor air"]):
        T0 = _value(label.removesuffix(" °C"))
        point = {"T_tank_w": 45.0, "T0": T0, "Q_ref_tank": 8000.0}
        solved = {name: boilers[name].analyze_steady(**point) for name in ("default", "frost_free", "rated_load_form")}
        for name, row in solved.items():
            assert row["failure_reason"] == "none" and row["capacity_clamped"] is None, (name, T0)
            assert row["Q_ref_tank [W]"] == pytest.approx(8000.0, rel=1e-9)
        where = f"ashpb.rst, 8 kW to a 45 °C tank at {label}"
        _quotes(rows["Frost-free"][column], solved["frost_free"]["cop_sys [-]"], f"{where}, frost-free")
        _quotes(rows["With frost and defrost"][column], solved["default"]["cop_sys [-]"], f"{where}, with frost")
        _quotes(rows["``load_weighted=False``"][column], solved["rated_load_form"]["cop_sys [-]"], f"{where}, rated")
        fraction = solved["default"]["defrost_fraction [-]"]
        ratio = DefrostModel.load_ratio(solved["default"]["Q_ref_tank_frost_free [W]"], 15000.0, T0)
        if fraction == 0.0:
            # Above the outdoor limit nothing frosts: no load ratio to quote.
            assert rows["Load ratio ``r``"][column] == "—" and rows["``defrost_fraction [-]``"][column] == "0"
            assert T0 > 5.0
        else:
            _quotes(rows["Load ratio ``r``"][column], ratio, f"{where}, load ratio")
            _quotes(rows["``defrost_fraction [-]``"][column], fraction, f"{where}, defrost fraction")


def test_ashpb_light_load_and_the_ceiling_under_frost(ashpb_page: str, boilers) -> None:
    ratio, frosted, frost_free = _sentence(
        ashpb_page, "At 4 kW the same unit hardly frosts (``r`` {}: {} at 2 °C against {} frost-free)"
    )
    point = {"T_tank_w": 45.0, "T0": 2.0, "Q_ref_tank": 4000.0}
    row, reference = boilers["default"].analyze_steady(**point), boilers["frost_free"].analyze_steady(**point)
    _quotes(ratio, DefrostModel.load_ratio(row["Q_ref_tank_frost_free [W]"], 15000.0, 2.0), "ashpb.rst, 4 kW, r")
    _quotes(frosted, row["cop_sys [-]"], "ashpb.rst, 4 kW at 2 °C")
    _quotes(frost_free, reference["cop_sys [-]"], "ashpb.rst, 4 kW at 2 °C, frost-free")

    ceiling, mean, request = _sentence(
        ashpb_page,
        "at A2 against a 35 °C tank the default unit reaches its speed ceiling at a frost-free {} kW, "
        "a mean of {} kW -- what a {} kW request receives",
    )
    asked = 1000.0 * _value(request)
    held = boilers["default"].analyze_steady(T_tank_w=35.0, T0=2.0, Q_ref_tank=asked)
    assert held["capacity_clamped"] == "max" and held["capacity_binding"] == "compressor"
    assert held["cmp_rpm [rpm]"] == pytest.approx(60.0 * boilers["default"].rps_max, rel=1e-9)
    _quotes(ceiling, held["Q_ref_tank_frost_free [W]"] / 1000.0, "ashpb.rst, A2/W35 frost-free ceiling [kW]")
    _quotes(mean, held["Q_ref_tank [W]"] / 1000.0, "ashpb.rst, A2/W35 mean ceiling [kW]")
    assert held["Q_ref_tank [W]"] < asked < held["Q_ref_tank_frost_free [W]"]  # frost is what holds it short


# ---------------------------------------------------------------------------
# tmhp.defrost (the module docstring the API reference renders)
# ---------------------------------------------------------------------------


def test_defrost_module_docstring_quotes_the_frost_model() -> None:
    text = _flat(tmhp.defrost.__doc__ or "").replace("-", "−")  # the docstring writes an ASCII minus

    def read(template: str) -> list[str]:
        return _sentence(text, template.replace("-", "−"))

    (at_limit,) = read("at 5.0 degC and the default humidity the capacity multiplier is {}, at 5.1 degC it is 1")
    _quotes(at_limit, DefrostModel(transition_K=0.0).frost_state(5.0).capacity_multiplier, "defrost.py, switch at 5.0")
    here, source = read(
        "at 2 degC and 50 % relative humidity it is not: the COP multiplier is {} here and {} in EnergyPlus"
    )
    _quotes(here, _cop_multiplier(DEFAULT.frost_state(2.0, 0.50)), "defrost.py, 2 degC / 50 %")
    _quotes(source, _cop_multiplier(ENERGYPLUS.frost_state(2.0, 0.50)), "defrost.py, 2 degC / 50 %, EnergyPlus")
    share, multiplier = read(
        "at −5 degC and 50 % relative humidity the defrost time is {} % of the runtime and the COP is still multiplied by {}"
    )
    dry = ENERGYPLUS.frost_state(-5.0, 0.50)
    _quotes(share, 100.0 * dry.defrost_fraction, "defrost.py, dry air defrost time [%]")
    _quotes(multiplier, _cop_multiplier(dry), "defrost.py, dry air COP multiplier")
    warm, at_warm, cold, at_cold = read("({} K at {} degC, {} K at {} degC)")
    for difference, T_cell in ((warm, at_warm), (cold, at_cold)):
        T = _value(T_cell)
        _quotes(difference, 0.18 * T + 8.589, f"defrost.py, air-to-coil difference at {T_cell} degC")
    at_60, at_84 = read("(at 60 % relative humidity below {} degC, at 84 % below {} degC)")
    before, T_before, after, T_after = read("the COP multiplier falls from {} at {} degC to {} at {} degC")
    without_band = DefrostModel(frost_point_band_K=None)
    for cell, T_cell in ((before, T_before), (after, T_after)):
        frost = without_band.frost_state(_value(T_cell), 0.60, load_ratio=0.5)
        _quotes(cell, _cop_multiplier(frost), f"defrost.py, half load at 60 % without the band, {T_cell} degC")
    for cell, rh in ((at_60, 0.60), (at_84, 0.84)):
        limit = brentq(lambda T, rh=rh: humidity_ratio(T, rh) - saturation_humidity_ratio(0.82 * T - 8.589), -60.0, 0.0)
        _quotes(cell, limit, f"defrost.py, no frost on the rated-load coil below, {rh:.0%}")
    assert math.isclose(_value(T_after), _value(at_60))
