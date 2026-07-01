"""Smoke tests — verify that core classes can be imported and instantiated."""


def test_import_ashpb():
    from tmhp.air_source_heat_pump_boiler import AirSourceHeatPumpBoiler

    assert AirSourceHeatPumpBoiler is not None


def test_import_gshpb():
    from tmhp.ground_source_heat_pump_boiler import GroundSourceHeatPumpBoiler

    assert GroundSourceHeatPumpBoiler is not None


def test_calc_util_constants():
    from tmhp.calc_util import C2K, K2C, W2kW, h2s

    assert C2K(0) == 273.15
    assert K2C(273.15) == 0.0
    # W2kW and h2s are multiplicative conversion constants, not callables.
    assert 1000 * W2kW == 1.0
    assert h2s == 3600
