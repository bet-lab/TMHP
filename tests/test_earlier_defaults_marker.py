"""The ``earlier_defaults`` marker of ``tests/conftest.py``: what it gives a machine, and what it leaves alone.

A marked test gets the machine a bare constructor built before the
semi-empirical compressor models and the maps after them: the recorded quadratic efficiency map and
the earlier speed ceiling among its inputs. An unmarked test gets the
library's defaults, whatever ran before it in the same process: the two never
share a reference state, although the library's cache key cannot tell them
apart.
"""

from __future__ import annotations

import warnings

import pytest
from validation.coefficients.recorded_maps import bitzer_quadratic_2026_10_06 as quadratic

import tmhp.reference_state as reference_state
from tests import conftest
from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp.compressor_efficiency import MapEfficiency
from tmhp.compressor_envelope import default_rps_max

#: The library's own cache, bound when this module is imported (collection runs outside every marker).
LIBRARY_CACHE = reference_state._CACHE
EFFICIENCIES = ("eta_cmp_isen", "eta_cmp_vol", "eta_cmp")
#: Every input the marker would otherwise fill in, given explicitly: what is left to differ is the efficiency
#: map and the speed ceiling, and the reference-state cache key is the same inside the marker and outside it.
EXPLICIT = dict(
    ref="R32",
    hp_capacity=9000.0,
    V_cmp_ref=4.2e-5,
    UA_tank_hx=1800.0,
    UA_ou_rated=1260.0,
    m_dot_w_rated=0.0,
    dV_fan_a_rated=1.35,
)


def _build(cls=AirSourceHeatPumpBoiler, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return cls(**kwargs)


def test_an_unmarked_machine_has_the_library_defaults():
    assert reference_state._CACHE is LIBRARY_CACHE
    hp = _build(**EXPLICIT)
    for name in EFFICIENCIES:
        assert isinstance(getattr(hp, name), MapEfficiency)
    assert hp.rps_max == default_rps_max(hp.rps_rated) == 120.0


@pytest.mark.earlier_defaults
def test_a_marked_machine_has_the_recorded_map_and_the_earlier_speed_ceiling():
    assert reference_state._CACHE is conftest._EARLIER_REFERENCE_STATES is not LIBRARY_CACHE
    hp = _build(**EXPLICIT)
    for name, coefficients in (
        ("eta_cmp_isen", quadratic.COEFFICIENTS_ETA_IS),
        ("eta_cmp_vol", quadratic.COEFFICIENTS_ETA_V),
        ("eta_cmp", quadratic.COEFFICIENTS_ETA_EM),
    ):
        model = getattr(hp, name)
        assert isinstance(model, quadratic.FittedEfficiency) and model.coef == coefficients
        assert model.rps_rated is None  # the map reads the absolute speed the models always pass
    assert hp.rps_rated < 60.0
    assert hp.rps_max == pytest.approx(2.0 * hp.rps_rated) == conftest.earlier_rps_max(hp.rps_rated)
    # A machine rated fast keeps 1.5 times its rated speed; one in between is capped at 120 rev/s.
    assert conftest.earlier_rps_max(90.0) == 135.0 and conftest.earlier_rps_max(70.0) == 120.0


@pytest.mark.earlier_defaults
def test_a_marked_test_keeps_what_it_gives_itself():
    hp = _build(**EXPLICIT, eta_cmp_isen=0.7, rps_max=95.0)
    assert hp.eta_cmp_isen == 0.7 and hp.rps_max == 95.0
    # only the omitted efficiencies are the recorded ones
    assert isinstance(hp.eta_cmp_vol, quadratic.FittedEfficiency) and isinstance(hp.eta_cmp, quadratic.FittedEfficiency)


@pytest.mark.earlier_defaults
@pytest.mark.current_defaults
def test_the_opt_out_inside_a_marked_scope_gets_the_library_defaults():
    assert reference_state._CACHE is LIBRARY_CACHE
    assert isinstance(_build(**EXPLICIT).eta_cmp_isen, MapEfficiency)


@pytest.mark.parametrize("first", ["library", "earlier"])
def test_marked_and_unmarked_machines_never_share_a_reference_state(first):
    """The same constructor call inside the marker and outside it, in either order, solves its own rated state."""
    # A capacity no other test uses, so that neither cache holds the state before this test.
    kwargs = {**EXPLICIT, "hp_capacity": 9000.0 + (17.0 if first == "library" else 23.0)}

    def library():
        assert reference_state._CACHE is LIBRARY_CACHE
        return _build(**kwargs)

    def earlier():
        with conftest.earlier_default_inputs():
            return _build(**kwargs)

    order = (library, earlier) if first == "library" else (earlier, library)
    a, b = order[0](), order[1]()
    again_a, again_b = order[0](), order[1]()
    # Each context solved its own state and finds it again; the other context's build did not replace it.
    assert a.reference_state is again_a.reference_state and b.reference_state is again_b.reference_state
    assert a.reference_state is not b.reference_state
    assert a.rps_rated != b.rps_rated
    new, old = (a, b) if first == "library" else (b, a)
    assert isinstance(new.eta_cmp_isen, MapEfficiency)
    assert isinstance(old.eta_cmp_isen, quadratic.FittedEfficiency)
    # The library's key does not tell the two apart: the same key holds a different state in each cache.
    shared = [
        key
        for key in LIBRARY_CACHE.keys() & conftest._EARLIER_REFERENCE_STATES.keys()
        if LIBRARY_CACHE[key] is new.reference_state
    ]
    assert len(shared) == 1
    assert conftest._EARLIER_REFERENCE_STATES[shared[0]] is old.reference_state
    assert reference_state._CACHE is LIBRARY_CACHE  # restored after the block


def test_the_marker_reaches_the_space_conditioning_models_too():
    with conftest.earlier_default_inputs():
        room = _build(AirSourceHeatPump, ref="R32")
    assert isinstance(room.eta_cmp_isen, quadratic.FittedEfficiency)
    assert room.rps_max == conftest.earlier_rps_max(room.rps_rated)
    assert isinstance(_build(AirSourceHeatPump, ref="R32").eta_cmp_isen, MapEfficiency)
