"""Shared test configuration.

``earlier_defaults``
    A marker for tests that pin a *scenario* -- a limit reached at a stated
    duty, a number recorded from an earlier commit, a point that lies just
    inside or outside the compressor envelope -- on the machine a bare
    constructor built before the catalogue default inputs (issue #86) and the
    R290 default of the air-source boiler. What such a test examines is the
    mechanism at that scenario, not the default: with another displacement or
    refrigerant the same request sits somewhere else in the envelope and the
    test would pass or fail for a reason it was not written for.

    Under the marker (on a test, or on a module through ``pytestmark``)
    every model is built with the inputs it had then, wherever the test
    leaves them out:

    - displacement ``hp_capacity * 42 cm3/rev / 9 kW``, whatever the
      refrigerant (:func:`tmhp.compressor_speed.default_displacement`);
    - largest airflow ``hp_capacity * 2.0e-4 m3/s`` outdoors and
      ``hp_capacity * 5.37e-5 m3/s`` (400 cfm/ton) indoors, for
      ``AirSourceHeatPump`` and ``GroundSourceHeatPump``, and
      ``hp_capacity * 1.5e-4 m3/s`` outdoors for ``AirSourceHeatPumpBoiler``;
    - refrigerant R134a for ``AirSourceHeatPumpBoiler``;
    - approach-temperature search box 1-20 K for ``AirSourceHeatPump``
      (30 K since the catalogue airflows: tests/test_default_inputs_wiring.py).

    These are explicit inputs given on the test's behalf -- the constructors
    still take them through ``default_input_estimates`` -- so the scenario
    survives the next change of a default as well. The default machines
    themselves are examined where they are named: ``test_default_inputs_wiring``,
    ``test_operating_range``, ``test_gshp_capacity_defaults``,
    ``test_ashp_indoor_airflow_default``, the documentation examples and the
    committed validation results.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import inspect
from collections.abc import Iterator
from typing import Any

import pytest

import tmhp.reference_state as reference_state
from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp.compressor_speed import default_displacement

MARKER = "earlier_defaults"
#: A test of a marked module that compares against committed results, which are made with the
#: present defaults (it must build no module-scoped model fixture of its own).
OPT_OUT = "current_defaults"

#: Largest airflow per watt of nameplate capacity the constructors assumed before issue #86 [m3/s per W].
EARLIER_AIRFLOW_PER_W = {
    ("outdoor", "single_split"): 2.0e-4,
    ("outdoor", "air_to_water"): 1.5e-4,
    ("indoor", "wall_mounted"): 5.37e-5,
    ("indoor", "floor_standing"): 5.37e-5,
    ("indoor", "water_to_air"): 5.37e-5,
}
#: Constructor arguments whose default was another one then.
EARLIER_ARGUMENTS: tuple[tuple[type, str, Any], ...] = (
    (AirSourceHeatPumpBoiler, "ref", "R134a"),  # R290 now
    (AirSourceHeatPump, "dT_approach_bounds", (1.0, 20.0)),  # (1, 30) now
)


def _with_earlier_argument(init, name: str, value: Any):
    """``init`` with ``name=value`` filled in where the caller gives no value for it."""
    signature = inspect.signature(init)

    @functools.wraps(init)
    def constructor(self, *args, **kwargs):
        if name not in signature.bind_partial(self, *args, **kwargs).arguments:
            kwargs[name] = value
        init(self, *args, **kwargs)

    return constructor


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{MARKER}: build models with the default inputs of before issue #86 and the R290 air-source boiler "
        "(tests/conftest.py)",
    )
    config.addinivalue_line("markers", f"{OPT_OUT}: inside an {MARKER} module, build models with the present defaults")


@contextlib.contextmanager
def earlier_default_inputs() -> Iterator[None]:
    """Models built inside get the earlier default inputs for whatever the caller leaves out."""
    estimate_displacement = reference_state.estimate_displacement
    estimate_max_airflow = reference_state.estimate_max_airflow

    def displacement(hp_capacity, refrigerant, *args, **kwargs):
        estimate = estimate_displacement(hp_capacity, refrigerant, *args, **kwargs)
        return dataclasses.replace(estimate, value=default_displacement(hp_capacity))

    def max_airflow(hp_capacity, *, side, product, mode="cooling"):
        estimate = estimate_max_airflow(hp_capacity, side=side, product=product, mode=mode)
        return dataclasses.replace(estimate, value=hp_capacity * EARLIER_AIRFLOW_PER_W[side, product])

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(reference_state, "estimate_displacement", displacement)
        patch.setattr(reference_state, "estimate_max_airflow", max_airflow)
        for cls, name, value in EARLIER_ARGUMENTS:
            patch.setattr(cls, "__init__", _with_earlier_argument(cls.__init__, name, value))
        yield


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item):
    """Run a marked test -- its fixtures' set-up included -- with the earlier default inputs.

    A hook and not an autouse fixture: the module-scoped model fixtures of a
    marked module are built during the set-up of its first test, before any
    function-scoped fixture, and must be built inside as well.
    """
    if item.get_closest_marker(MARKER) is None or item.get_closest_marker(OPT_OUT) is not None:
        return (yield)
    with earlier_default_inputs():
        return (yield)
