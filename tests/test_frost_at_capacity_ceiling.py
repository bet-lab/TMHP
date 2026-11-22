"""Frost on a unit held at its capacity ceiling follows the load it runs at (#207).

With ``DefrostModel.load_weighted`` (the default) the coil temperature is
estimated at the load ratio of the machine: a lightly loaded coil is warmer
and takes less water out of the air. A request above what the unit can
deliver does not load the coil more than the ceiling does -- the compressor is
at its speed ceiling or on an envelope limit either way. The frost state of a
row with ``capacity_clamped == "max"`` is therefore that of the frost-free
duty the row was solved at, whatever was asked, and
``AirSourceHeatPump.max_capacity`` reports the same state.

Before this the state was that of the request: asking a unit limited to
2.48 kW for 2.5, 2.8 and 3.2 kW returned 2,480, 2,464 and 2,370 W from one
and the same frost-free operating point.
"""

from __future__ import annotations

import dataclasses
import math
import os
import warnings

import numpy as np
import pytest

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp.defrost import DefrostModel

os.environ.setdefault("TQDM_DISABLE", "1")

MODEL = DefrostModel()  # the default: load-weighted, frost-point band 3 K


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _state_at(duty: float, rated: float, T_out: float, RH_out: float | None = None, model: DefrostModel = MODEL):
    """Frost state of a machine running the frost-free ``duty`` [W]."""
    return model.frost_state(T_out, RH_out, load_ratio=model.load_ratio(duty, rated, T_out))


def _same_state(a, b) -> bool:
    """Field for field, with NaN equal to NaN (a state without frost has no humidity-ratio difference)."""
    pairs = zip(dataclasses.astuple(a), dataclasses.astuple(b), strict=True)
    return all(x == y or (isinstance(x, float) and math.isnan(x) and math.isnan(y)) for x, y in pairs)


def _same_row(a: dict, b: dict, skip: tuple[str, ...], rel: float = 0.0) -> None:
    assert a.keys() == b.keys()
    for key, value in a.items():
        if key in skip:
            continue
        other = b[key]
        if isinstance(value, float) and isinstance(other, float):
            if math.isnan(value) or math.isnan(other):
                assert math.isnan(value) and math.isnan(other), key
            elif rel > 0.0:
                assert other == pytest.approx(value, rel=rel, abs=1e-12), key
            else:
                assert other == value, key
        else:
            assert other == value, key


# ---------------------------------------------------------------------------
# The frost model: a ceiling below the reference capacity is a part-loaded coil
# ---------------------------------------------------------------------------


def test_state_of_a_ceiling_below_the_reference_capacity_is_lighter_than_any_request_above_it():
    """10 kW unit at -7 degC: the correlation's unit delivers 7.44 kW there, this one 5 kW at most.

    ``frost_state_at_duty`` knows the request only: asked for more than 5 kW
    it reads a more and more loaded coil, up to the rated-load state. The
    coil of a unit that cannot run more than 5 kW is that of 5 kW.
    """
    rated, T_out, ceiling = 10000.0, -7.0, 5000.0
    assert DefrostModel.reference_capacity(rated, T_out) == pytest.approx(7441.4, rel=1e-4)
    at_ceiling = _state_at(ceiling, rated, T_out)
    assert at_ceiling.active and at_ceiling.load_ratio == pytest.approx(0.6719, rel=1e-3)
    rated_load = MODEL.frost_state(T_out)
    assert at_ceiling.capacity_multiplier > rated_load.capacity_multiplier + 0.02
    multipliers = [
        MODEL.frost_state_at_duty(T_out, None, request, rated).capacity_multiplier
        for request in (5000.0, 5500.0, 7000.0, 9000.0)
    ]
    assert multipliers == sorted(multipliers, reverse=True) and multipliers[-1] == rated_load.capacity_multiplier
    assert all(value < at_ceiling.capacity_multiplier for value in multipliers)
    # With the default model the mean a duty delivers rises with the duty: what the ceiling's
    # coil delivers is less than any request whose own state asks for more than the ceiling.
    most = at_ceiling.capacity_multiplier * ceiling - MODEL.defrost_load(at_ceiling, T_out, rated)
    for request in np.linspace(3000.0, 9000.0, 61):
        own = MODEL.frost_state_at_duty(T_out, None, float(request), rated)
        needs = MODEL.gross_duty(own, request, MODEL.defrost_load(own, T_out, rated))
        assert (needs > ceiling) == (request > most)


def test_ceiling_state_is_not_taken_where_it_would_deliver_more_than_was_asked():
    """Without the frost-point band the mean folds, and a row must not report more than the request.

    2 degC, 49 % humidity, EnergyPlus' fixed losses (``frost_point_band_K=None``),
    a unit held to 3.47 kW frost-free. The fixed losses come in over a small
    range of load just above that duty: its coil hardly frosts and delivers a
    mean of 3,460 W, while the rated-load coil would lose an eighth.

    - A request above 3,460 W gets the ceiling's row, whatever its size.
    - A request just below it is read by ``frost_state_at_duty`` as the
      rated-load coil (which asks for more than the ceiling), as on main.
      The ceiling's own state would deliver more than was asked, so it is
      not taken: the row keeps the request's state and stays short. That a
      duty just under the ceiling would meet such a request is a limit of
      the band-free form the frost module documents, not of the ceiling.
    """
    model = DefrostModel(frost_point_band_K=None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        hp = AirSourceHeatPump(PR_cycle_max=2.97, defrost=model)
    point = {"T0": 2.0, "T_a_room": 20.0, "RH_out": 0.49}
    ceiling = hp.max_capacity(mode="heating", **point)
    mean, frost_free = ceiling["Q_max [W]"], ceiling["Q_max_frost_free [W]"]
    running = _state_at(frost_free, hp.hp_capacity, 2.0, 0.49, model)
    at_ceiling = running.capacity_multiplier * frost_free - model.defrost_load(running, 2.0, hp.hp_capacity)
    assert mean == pytest.approx(at_ceiling, rel=1e-12)
    assert running.capacity_multiplier > 0.99 and model.frost_state(2.0, 0.49).capacity_multiplier < 0.88
    for request in (mean + 20.0, 5000.0, 9000.0):
        row = hp.analyze_steady(Q_r_iu=-request, return_dict=True, **point)
        assert row["capacity_clamped"] == "max"
        assert row["frost_capacity_factor [-]"] == pytest.approx(running.capacity_multiplier, rel=1e-12)
        assert row["Q_ref_iu [W]"] == pytest.approx(mean, rel=1e-12)
    for request in (mean - 20.0, mean - 60.0):
        own = model.frost_state_at_duty(2.0, 0.49, request, hp.hp_capacity)
        row = hp.analyze_steady(Q_r_iu=-request, return_dict=True, **point)
        assert row["capacity_clamped"] == "max" and row["Q_ref_iu_frost_free [W]"] == pytest.approx(
            frost_free, rel=1e-9
        )
        assert row["frost_capacity_factor [-]"] == own.capacity_multiplier
        assert row["Q_ref_iu [W]"] < request  # never more than was asked


# ---------------------------------------------------------------------------
# AirSourceHeatPump: analyze_steady and max_capacity
# ---------------------------------------------------------------------------

#: Outdoor state, room and humidity of the reproduction in #207.
POINT = {"T0": -5.0, "T_a_room": 20.0}
NOT_THE_REQUEST = ("Q_request [W]", "Q_unmet [W]")


@pytest.fixture(scope="module")
def limited() -> AirSourceHeatPump:
    """The default 4 kW unit with a pressure-ratio ceiling that holds it to 2.5 kW at -5 degC."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return AirSourceHeatPump(PR_cycle_max=3.2)


@pytest.mark.parametrize("RH_out", [0.55, 0.84], ids=["dry: no frost at the ceiling", "humid: frost at the ceiling"])
def test_air_to_air_rows_above_the_ceiling_do_not_depend_on_the_request(limited, RH_out):
    rows = {
        request: limited.analyze_steady(Q_r_iu=-request, RH_out=RH_out, return_dict=True, **POINT)
        for request in (2500.0, 2800.0, 3200.0, 4000.0, 8000.0)
    }
    first = rows[2500.0]
    assert first["capacity_clamped"] == "max" and first["capacity_binding"] == "pressure_ratio"
    q_frost_free = first["Q_ref_iu_frost_free [W]"]
    # The ceiling is below what the coil-temperature correlation's unit delivers here: a part-loaded coil.
    ratio = DefrostModel.load_ratio(q_frost_free, limited.hp_capacity, POINT["T0"])
    assert 0.75 < ratio < 0.85
    running = _state_at(q_frost_free, limited.hp_capacity, POINT["T0"], RH_out)
    rated_load = MODEL.frost_state(POINT["T0"], RH_out)
    assert rated_load.active and running.capacity_multiplier > rated_load.capacity_multiplier + 0.01
    assert running.active is (RH_out == 0.84)
    for request, row in rows.items():
        _same_row(first, row, skip=NOT_THE_REQUEST)
        assert row["Q_request [W]"] == -request
        assert row["Q_unmet [W]"] == pytest.approx(request - row["Q_ref_iu [W]"], rel=1e-12)
        assert row["defrost_fraction [-]"] == pytest.approx(running.defrost_fraction, rel=1e-12, abs=0.0)
        assert row["frost_capacity_factor [-]"] == pytest.approx(running.capacity_multiplier, rel=1e-12)
        assert row["frost_power_factor [-]"] == pytest.approx(running.power_multiplier, rel=1e-12)
        delivered = running.capacity_multiplier * q_frost_free - row["Q_defrost [W]"]
        assert row["Q_ref_iu [W]"] == pytest.approx(delivered, rel=1e-12)
        assert row["Q_max [W]"] == pytest.approx(row["Q_ref_iu [W]"], rel=1e-12)


@pytest.mark.parametrize("RH_out", [0.55, 0.84])
def test_max_capacity_is_the_row_analyze_steady_returns_above_it(limited, RH_out):
    ceiling = limited.max_capacity(mode="heating", RH_out=RH_out, **POINT)
    row = limited.analyze_steady(Q_r_iu=-8000.0, RH_out=RH_out, return_dict=True, **POINT)
    assert ceiling["binding"] == "pressure_ratio"
    assert ceiling["Q_max_frost_free [W]"] == pytest.approx(row["Q_ref_iu_frost_free [W]"], rel=1e-12)
    assert ceiling["Q_max [W]"] == pytest.approx(row["Q_ref_iu [W]"], rel=1e-12)
    assert ceiling["E_tot [W]"] == pytest.approx(row["E_tot [W]"], rel=1e-12)
    assert ceiling["cop_sys [-]"] == pytest.approx(row["cop_sys [-]"], rel=1e-12)
    assert ceiling["defrost_fraction [-]"] == pytest.approx(row["defrost_fraction [-]"], rel=1e-12, abs=0.0)
    # A request for exactly that is not held short by more than the search's own tolerance (#234).
    met = limited.analyze_steady(Q_r_iu=-0.999 * ceiling["Q_max [W]"], RH_out=RH_out, return_dict=True, **POINT)
    assert met["capacity_clamped"] is None and met["Q_unmet [W]"] == 0.0
    assert met["Q_ref_iu [W]"] == pytest.approx(0.999 * ceiling["Q_max [W]"], rel=1e-9)


#: Relative width of the band at the ceiling inside which the frost-free search of ``AirSourceHeatPump``
#: answers a request with either the request or the ceiling: a request up to 0.03 % above ``max_capacity``
#: is delivered in full (measured here; the search is not part of this change, see #234).
SEARCH_BAND = 5.0e-4


@pytest.mark.parametrize("RH_out", [0.55, 0.84])
def test_delivered_heat_does_not_fall_as_the_request_grows_past_the_ceiling(limited, RH_out):
    """One operating point, one delivered heat: no request is answered with less than a smaller one.

    Coarsely across the ceiling, and in steps of 0.01 % around it: what is
    left there is the band of the frost-free search (:data:`SEARCH_BAND`),
    not the 4 % the frost of the request used to take.
    """
    ceiling = limited.max_capacity(mode="heating", RH_out=RH_out, **POINT)["Q_max [W]"]
    for requests in (np.linspace(0.93, 1.4, 12) * ceiling, (1.0 + np.arange(-6, 13) * 1.0e-4) * ceiling):
        delivered = [
            limited.analyze_steady(Q_r_iu=-float(q), RH_out=RH_out, return_dict=True, **POINT)["Q_ref_iu [W]"]
            for q in requests
        ]
        assert all(b >= a * (1.0 - SEARCH_BAND) for a, b in zip(delivered, delivered[1:], strict=False))
        assert delivered[-1] == pytest.approx(ceiling, rel=1e-12)
        assert max(delivered) <= ceiling * (1.0 + SEARCH_BAND)


def test_ceiling_above_the_reference_capacity_keeps_the_rated_load_correction():
    """The default unit flat out at -7 degC delivers more than the correlation's unit: EnergyPlus unchanged."""
    hp = AirSourceHeatPump()
    T0 = -7.0
    row = hp.analyze_steady(Q_r_iu=-4.0 * hp.hp_capacity, T0=T0, T_a_room=20.0, return_dict=True)
    assert row["capacity_clamped"] == "max"
    assert row["Q_ref_iu_frost_free [W]"] >= DefrostModel.reference_capacity(hp.hp_capacity, T0)
    rated_load = MODEL.frost_state(T0)
    assert row["defrost_fraction [-]"] == rated_load.defrost_fraction
    assert row["frost_capacity_factor [-]"] == rated_load.capacity_multiplier
    ceiling = hp.max_capacity(T0=T0, T_a_room=20.0, mode="heating")
    assert ceiling["Q_max [W]"] == pytest.approx(row["Q_ref_iu [W]"], rel=1e-12)
    assert ceiling["defrost_fraction [-]"] == rated_load.defrost_fraction


def test_rated_load_form_is_untouched(limited):
    """``load_weighted=False`` has one state per outdoor condition: nothing to follow."""
    plain = DefrostModel(load_weighted=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        hp = AirSourceHeatPump(PR_cycle_max=3.2, defrost=plain)
    state = plain.frost_state(POINT["T0"], 0.84)
    for request in (2800.0, 8000.0):
        row = hp.analyze_steady(Q_r_iu=-request, RH_out=0.84, return_dict=True, **POINT)
        assert row["capacity_clamped"] == "max"
        assert row["frost_capacity_factor [-]"] == state.capacity_multiplier
    assert hp.max_capacity(mode="heating", RH_out=0.84, **POINT)["defrost_fraction [-]"] == state.defrost_fraction


# ---------------------------------------------------------------------------
# AirSourceHeatPumpBoiler: analyze_steady and the cycle of a dynamic step
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def boiler() -> AirSourceHeatPumpBoiler:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return AirSourceHeatPumpBoiler()


@pytest.fixture(scope="module")
def boiler_frost_free() -> AirSourceHeatPumpBoiler:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return AirSourceHeatPumpBoiler(defrost=False)


def test_boiler_with_a_fixed_ceiling_returns_one_row_for_every_request_above_it(boiler, boiler_frost_free):
    """The frost balance of a machine whose frost-free solve stops at 8 kW (-7 degC, 45 degC tank).

    The frost-free solve is replaced by one with a ceiling that does not
    move with the request, so that the rows can be compared key for key: the
    boiler's own envelope search lands within its tolerance of the ceiling,
    not on one number (next test).
    """
    T0, T_tank, ceiling = -7.0, 45.0, 8000.0
    assert ceiling < DefrostModel.reference_capacity(boiler.hp_capacity, T0)
    at_ceiling = boiler_frost_free.analyze_steady(T_tank_w=T_tank, T0=T0, Q_ref_tank=ceiling)
    assert at_ceiling["capacity_clamped"] is None

    def solve(duty: float) -> dict:
        if duty <= ceiling:
            return boiler_frost_free.analyze_steady(T_tank_w=T_tank, T0=T0, Q_ref_tank=duty)
        row = dict(at_ceiling)
        row.update({"capacity_clamped": "max", "capacity_binding": "compressor", "Q_max [W]": ceiling})
        row["Q_request [W]"] = duty
        return row

    # The state of the duty the ceiling row reports (the solve returns 8 kW to its last digits).
    held = float(at_ceiling["Q_ref_tank [W]"])
    assert held == pytest.approx(ceiling, rel=1e-9)
    running = _state_at(held, boiler.hp_capacity, T0)
    assert running.active and running.capacity_multiplier > MODEL.frost_state(T0).capacity_multiplier + 0.02
    delivered = running.capacity_multiplier * held - MODEL.defrost_load(running, T0, boiler.hp_capacity)
    rows = {}
    for request in (1.001 * delivered, 8000.0, 9000.0, 12000.0, 40000.0):
        frost = boiler._frost_state(request, T0, None)
        rows[request] = boiler._steady_over_frost(frost, solve, request, T0)
    first = next(iter(rows.values()))
    for request, row in rows.items():
        _same_row(first, row, skip=("Q_request [W]", "PLR_request [-]"))
        assert row["Q_request [W]"] == request
        assert row["frost_capacity_factor [-]"] == running.capacity_multiplier
        assert row["defrost_fraction [-]"] == running.defrost_fraction
        assert row["Q_ref_tank [W]"] == pytest.approx(delivered, rel=1e-12)
        assert row["Q_max [W]"] == pytest.approx(row["Q_ref_tank [W]"], rel=1e-12)
    # A request the ceiling machine does deliver is met, with the frost of its own load.
    met = boiler._steady_over_frost(boiler._frost_state(0.98 * delivered, T0, None), solve, 0.98 * delivered, T0)
    assert met["capacity_clamped"] is None
    assert met["Q_ref_tank [W]"] == pytest.approx(0.98 * delivered, rel=1e-9)
    assert met["frost_capacity_factor [-]"] > running.capacity_multiplier


@pytest.mark.parametrize("RH_out", [0.84, 0.70])
def test_boiler_row_on_the_pressure_ratio_ceiling_frosts_as_the_duty_it_runs(boiler, RH_out):
    """-20 degC, 65 degC tank: the pressure-ratio ceiling holds the unit at 6.3 kW, below the reference 7.6 kW."""
    T0, T_tank = -20.0, 65.0
    reference = DefrostModel.reference_capacity(boiler.hp_capacity, T0)
    rated_load = MODEL.frost_state(T0, RH_out)
    assert rated_load.active
    delivered = []
    for request in (7000.0, 9000.0, 15000.0, 40000.0):
        row = boiler.analyze_steady(T_tank_w=T_tank, T0=T0, Q_ref_tank=request, RH_out=RH_out)
        assert row["capacity_clamped"] == "max" and row["capacity_binding"] == "pressure_ratio"
        q_frost_free = row["Q_ref_tank_frost_free [W]"]
        # The figures docs/source/models/ashpb.rst quotes ("Frost and defrost", part load).
        assert reference == pytest.approx(7600.0, abs=50.0) and q_frost_free == pytest.approx(6300.0, abs=100.0)
        running = _state_at(q_frost_free, boiler.hp_capacity, T0, RH_out)
        assert row["frost_capacity_factor [-]"] == pytest.approx(running.capacity_multiplier, rel=1e-12)
        assert row["frost_power_factor [-]"] == pytest.approx(running.power_multiplier, rel=1e-12)
        assert row["defrost_fraction [-]"] == pytest.approx(running.defrost_fraction, rel=1e-12)
        assert row["frost_capacity_factor [-]"] > rated_load.capacity_multiplier + 0.005
        assert row["Q_ref_tank [W]"] == pytest.approx(
            running.capacity_multiplier * q_frost_free - row["Q_defrost [W]"], rel=1e-12
        )
        assert row["Q_max [W]"] == pytest.approx(row["Q_ref_tank [W]"], rel=1e-12) and row["Q_request [W]"] == request
        delivered.append(row["Q_ref_tank [W]"])
    # What is left between the rows is the tolerance of the envelope search on the frost-free
    # duty (it starts from the request), not the frost: 2.4 % here, against 12 % before.
    assert max(delivered) / min(delivered) < 1.03


def test_dynamic_step_at_the_ceiling_frosts_as_the_duty_it_runs(boiler):
    """The thermostat asks for the nameplate; at -22 degC the envelope holds the cycle below the reference capacity.

    A tank charged from 50 degC: the pressure-ratio ceiling leaves 9.8 kW
    frost-free in the first ten minutes (more than the 7.05 kW of the
    correlation's unit: the rated-load correction) and 6.1 kW in the next
    (less: a part-loaded coil).
    """
    n, T0 = 4, -22.0
    frame = boiler.analyze_dynamic(
        simulation_period_sec=n * 600,
        dt_s=600,
        T_tank_w_init_C=50.0,
        dhw_usage_schedule=np.zeros(n),
        T0_schedule=np.full(n, T0),
    )
    full = frame[frame["hp_is_on"] & (frame["runtime_fraction [-]"] == 1.0)]
    assert len(full) >= 2
    reference = DefrostModel.reference_capacity(boiler.hp_capacity, T0)
    rated_load = MODEL.frost_state(T0)
    above = below = 0
    for _, row in full.iterrows():
        assert row["capacity_clamped"] == "max" and row["Q_request [W]"] == boiler.hp_capacity
        q_frost_free = row["Q_ref_tank_frost_free [W]"]
        running = _state_at(q_frost_free, boiler.hp_capacity, T0)
        assert row["frost_capacity_factor [-]"] == pytest.approx(running.capacity_multiplier, rel=1e-12)
        assert row["defrost_fraction [-]"] == pytest.approx(running.defrost_fraction, rel=1e-12)
        assert row["Q_ref_tank [W]"] == pytest.approx(
            running.capacity_multiplier * q_frost_free - row["Q_defrost [W]"], rel=1e-9
        )
        if q_frost_free >= reference:
            assert row["frost_capacity_factor [-]"] == rated_load.capacity_multiplier
            above += 1
        elif q_frost_free < 0.95 * reference:
            assert row["frost_capacity_factor [-]"] > rated_load.capacity_multiplier + 0.01
            below += 1
    assert above >= 1 and below >= 1
