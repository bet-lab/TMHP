"""Shared test configuration.

``earlier_defaults``
    A marker for tests that pin a *scenario* -- a limit reached at a stated
    duty, a number recorded from an earlier commit, a point that lies just
    inside or outside the compressor envelope -- on the machine a bare
    constructor built before the catalogue default inputs (issue #86), the
    R290 default of the air-source boiler and the semi-empirical compressor
    models. What such a test examines is the mechanism at that scenario, not
    the default: with another displacement, refrigerant or efficiency map the
    same request sits somewhere else in the envelope and the test would pass
    or fail for a reason it was not written for.

    Under the marker (on a test, or on a module through ``pytestmark``)
    every model is built with the inputs it had then, wherever the test
    leaves them out:

    - displacement ``hp_capacity * 42 cm3/rev / 9 kW``, whatever the
      refrigerant (:data:`EARLIER_DISPLACEMENT_PER_W`), for every family;
    - largest airflow ``hp_capacity * 2.0e-4 m3/s`` outdoors and
      ``hp_capacity * 5.37e-5 m3/s`` (400 cfm/ton) indoors, for
      ``AirSourceHeatPump`` and ``GroundSourceHeatPump``, and
      ``hp_capacity * 1.5e-4 m3/s`` outdoors for ``AirSourceHeatPumpBoiler``;
    - refrigerant R134a for ``AirSourceHeatPumpBoiler``;
    - approach-temperature search box 1-20 K for ``AirSourceHeatPump``
      (30 K since the catalogue airflows: tests/test_default_inputs_wiring.py);
    - the heat-exchanger conductance rules of before the approach-temperature
      defaults (issues #115-#118, :data:`EARLIER_UA_RULES`): ``AirSourceHeatPump``
      ``UA_ou_rated = hp_capacity / 5`` for both refrigerant roles and
      ``UA_iu_rated = 0.8 UA_ou_rated``; ``AirSourceHeatPumpBoiler``
      ``UA_tank_hx = hp_capacity / 5`` without circulating water
      (``m_dot_w_rated = 0``) and ``UA_ou_rated = 0.7 UA_tank_hx``;
      ``GroundSourceHeatPump`` ``UA_ground_rated = 0.18 hp_capacity`` for both
      roles and ``UA_iu_rated = 0.08 hp_capacity``; ``GroundSourceHeatPumpBoiler``
      and ``WaterSourceHeatPumpBoiler`` ``UA_tank_hx = hp_capacity / 5`` without
      circulating water and a source exchanger of ``0.18 hp_capacity``;
    - the compressor efficiency map of before the semi-empirical models, for
      whichever of ``eta_cmp_isen`` / ``eta_cmp_vol`` / ``eta_cmp`` the test
      leaves out: the quadratic map of ``BITZER-three-C3-absolute-N-2026-10-06``
      with its guard, a record kept outside the library
      (``validation/coefficients/recorded_maps``, :func:`earlier_efficiencies`).
      The isentropic-efficiency floor the library applied to that map
      (``eta_isen_below_min``) left the library with it and is not restored;
    - the compressor speed ceiling of before the fixed 120 rev/s, where the
      test gives no ``rps_max``: ``max(min(2 rps_rated, 120), 1.5 rps_rated)``
      (:func:`earlier_rps_max`).

    These are explicit inputs given on the test's behalf -- the constructors
    still take them through ``default_input_estimates`` -- so the scenario
    survives the next change of a default as well. (The efficiency map and the
    speed ceiling are given where the constructor resolves its defaults,
    because both depend on the rated speed the constructor solves.) The
    reference-state cache of the library does not know which default map
    solved an entry, so marked tests use a cache of their own
    (:data:`_EARLIER_REFERENCE_STATES`): a machine built under the marker and
    one built outside it never share a rated state, in either direction. The default machines
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
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from validation.coefficients.recorded_maps import recorded_efficiencies

import tmhp.reference_state as reference_state
from tmhp import (
    AirSourceHeatPump,
    AirSourceHeatPumpBoiler,
    GroundSourceHeatPump,
    GroundSourceHeatPumpBoiler,
    WaterSourceHeatPumpBoiler,
)

MARKER = "earlier_defaults"
#: A test of a marked module that compares against committed results, which are made with the
#: present defaults (it must build no module-scoped model fixture of its own).
OPT_OUT = "current_defaults"

#: Displacement per watt of nameplate capacity every constructor assumed before issue #86, whatever the
#: refrigerant [m3/rev per W]: 42 cm3/rev per 9 kW.
EARLIER_DISPLACEMENT_PER_W = 42.0e-6 / 9000.0
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


def _first(arguments: dict[str, Any], *names: str) -> Any:
    """The first of ``names`` the arguments give a value (not ``None``) for."""
    return next(arguments[name] for name in names if arguments.get(name) is not None)


@dataclasses.dataclass(frozen=True)
class EarlierRule:
    """An argument filled from the caller's other arguments (signature defaults applied).

    The rule applies where the caller gives no value for ``name`` nor for any of its older names
    ``aliases``; the rules of a class apply in order, each seeing the values the earlier ones filled.
    """

    name: str
    rule: Callable[[dict[str, Any]], Any]
    aliases: tuple[str, ...] = ()


#: The deprecated cycle-role conductances of GroundSourceHeatPump (their adapter needs the physical ones unset).
_GSHP_ROLE_NAMES = ("UA_cond", "UA_evap", "UA_cond_design", "UA_evap_design")
#: Conductance rules of before the approach-temperature defaults (issues #115-#118).
EARLIER_UA_RULES: tuple[tuple[type, tuple[EarlierRule, ...]], ...] = (
    (
        AirSourceHeatPump,
        (
            EarlierRule("UA_ou_rated", lambda a: a["hp_capacity"] / 5.0, ("UA_cond_rated", "UA_cond_design")),
            EarlierRule(
                "UA_iu_rated",
                lambda a: 0.8 * _first(a, "UA_ou_rated", "UA_cond_rated", "UA_cond_design"),
                ("UA_evap_rated", "UA_evap_design"),
            ),
        ),
    ),
    (
        AirSourceHeatPumpBoiler,
        (
            EarlierRule("UA_tank_hx", lambda a: a["hp_capacity"] / 5.0, ("UA_tank", "UA_cond_design")),
            EarlierRule(
                "UA_ou_rated",
                lambda a: 0.7 * _first(a, "UA_tank_hx", "UA_tank", "UA_cond_design"),
                ("UA_evap_design",),
            ),
            # No circulating water: the tank exchanger works against the tank temperature.
            EarlierRule("m_dot_w_rated", lambda a: 0.0),
        ),
    ),
    (
        GroundSourceHeatPump,
        (
            # 0.18 W/K per W for both roles (ground_hx_ua_per_capacity, given, keeps that rule itself).
            EarlierRule(
                "UA_ground_rated",
                lambda a: 0.18 * a["hp_capacity"],
                ("ground_hx_ua_per_capacity", *_GSHP_ROLE_NAMES),
            ),
            EarlierRule("UA_iu_rated", lambda a: 0.08 * a["hp_capacity"], _GSHP_ROLE_NAMES),
        ),
    ),
    *(
        (
            cls,
            (
                EarlierRule("UA_tank_hx", lambda a: a["hp_capacity"] / 5.0, ("UA_tank", "UA_cond_design")),
                EarlierRule("m_dot_w_rated", lambda a: 0.0),
                EarlierRule(source, lambda a: 0.18 * a["hp_capacity"], ("UA_evap_design",)),
            ),
        )
        for cls, source in ((GroundSourceHeatPumpBoiler, "UA_ground"), (WaterSourceHeatPumpBoiler, "UA_water"))
    ),
)


def _with_earlier_rules(init, rules: tuple[EarlierRule, ...]):
    """``init`` with each rule's argument filled in where the caller gives no value for it."""
    signature = inspect.signature(init)

    @functools.wraps(init)
    def constructor(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs)
        values = signature.bind(self, *args, **kwargs)
        values.apply_defaults()
        arguments = dict(values.arguments)
        for rule in rules:
            if arguments.get(rule.name) is None and all(arguments.get(alias) is None for alias in rule.aliases):
                arguments[rule.name] = bound.arguments[rule.name] = rule.rule(arguments)
        init(*bound.args, **bound.kwargs)

    return constructor


def _with_earlier_argument(init, name: str, value: Any):
    """``init`` with ``name=value`` filled in where the caller gives no value for it."""
    signature = inspect.signature(init)

    @functools.wraps(init)
    def constructor(self, *args, **kwargs):
        if name not in signature.bind_partial(self, *args, **kwargs).arguments:
            kwargs[name] = value
        init(self, *args, **kwargs)

    return constructor


#: The efficiency map a bare constructor used before the semi-empirical models (a recorded release).
EARLIER_EFFICIENCY_MAP = "BITZER-three-C3-absolute-N-2026-10-06"
#: Reference states solved under the marker. Kept apart from ``reference_state._CACHE``, whose key does not
#: include the default efficiency map; shared by the marked tests of a process, as the library's is by the others.
_EARLIER_REFERENCE_STATES: dict[tuple, Any] = {}


def earlier_efficiencies(spec: Any, ref: str, rps_rated: float | None) -> dict[str, Callable]:
    """The recorded quadratic map in place of :func:`tmhp.compressor_efficiency.default_efficiencies`.

    The map reads the absolute shaft speed and no refrigerant, compressor type or suction state; ``rps_rated``
    (``None`` while the rated speed is being solved) only stands in for a speed a caller omits, which the models
    never do.
    """
    del spec, ref
    return recorded_efficiencies(EARLIER_EFFICIENCY_MAP, rps_rated)  # type: ignore[arg-type]


def earlier_rps_max(rps_rated: float) -> float:
    """The default speed ceiling of before the fixed 120 rev/s [rev/s].

    Twice the rated speed, capped at 120 rev/s, and at least 1.5 times the rated speed.
    """
    return max(min(2.0 * float(rps_rated), 120.0), 1.5 * float(rps_rated))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{MARKER}: build models with the default inputs of before issue #86, the R290 air-source boiler and the "
        "semi-empirical compressor models (tests/conftest.py)",
    )
    config.addinivalue_line("markers", f"{OPT_OUT}: inside an {MARKER} module, build models with the present defaults")


@contextlib.contextmanager
def earlier_default_inputs() -> Iterator[None]:
    """Models built inside get the earlier default inputs for whatever the caller leaves out."""
    estimate_displacement = reference_state.estimate_displacement
    estimate_max_airflow = reference_state.estimate_max_airflow

    def displacement(hp_capacity, refrigerant, *args, **kwargs):
        estimate = estimate_displacement(hp_capacity, refrigerant, *args, **kwargs)
        return dataclasses.replace(estimate, value=hp_capacity * EARLIER_DISPLACEMENT_PER_W)

    def max_airflow(hp_capacity, *, side, product, mode="cooling"):
        estimate = estimate_max_airflow(hp_capacity, side=side, product=product, mode=mode)
        return dataclasses.replace(estimate, value=hp_capacity * EARLIER_AIRFLOW_PER_W[side, product])

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(reference_state, "estimate_displacement", displacement)
        patch.setattr(reference_state, "estimate_max_airflow", max_airflow)
        # Resolved inside the constructor (both depend on the rated speed it solves), so they are replaced
        # where it looks them up; the reference states they produce go to the marker's own cache.
        patch.setattr(reference_state, "default_efficiencies", earlier_efficiencies)
        patch.setattr(reference_state, "default_rps_max", earlier_rps_max)
        patch.setattr(reference_state, "_CACHE", _EARLIER_REFERENCE_STATES)
        for cls, name, value in EARLIER_ARGUMENTS:
            patch.setattr(cls, "__init__", _with_earlier_argument(cls.__init__, name, value))
        for cls, rules in EARLIER_UA_RULES:
            patch.setattr(cls, "__init__", _with_earlier_rules(cls.__init__, rules))
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
