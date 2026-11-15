"""The call form of an efficiency model is decided once per model, and nothing else changes.

``_eval_eff`` used to bind each model's signature on every call to tell
``model(PR, rps)`` from ``model(PR)``; the steady solves of the three boilers
asked for it some twenty times per cycle evaluation. The answer is now kept per
model (``compressor_efficiency._TAKES_SPEED``, a weak-key dictionary shared with
``_opt_utils.efficiency_evaluator``). These tests pin what a cache could get
wrong:

* a model reassigned after construction (``model.eta_cmp_isen = f``),
  including to the other call form;
* a new model at the address of a collected one, and two models that compare
  equal but take different arguments (the dictionary finds keys by equality);
* models that cannot be weak keys (``__slots__`` instances, unhashable
  callables) and the less obvious callables (builtins, ``functools.partial``
  with a keyword-bound speed, bound methods);
* every result of a representative steady grid of the five families against
  the path that inspects the signature on every call.
"""

from __future__ import annotations

import functools
import gc
import inspect
import math
import operator
import struct
import sys
import warnings

import pytest

import tmhp
from tmhp import _opt_utils, compressor_efficiency
from tmhp.compressor_efficiency import _eval_eff


def _takes_speed_every_call(model):
    """The binding test of ``_eval_eff`` before the cache: inspected on every call."""
    signature = inspect.signature(model)
    try:
        signature.bind(1.0, 1.0)
    except TypeError:
        signature.bind(1.0)
        return False
    return True


@pytest.fixture
def inspect_every_call(monkeypatch):
    """Run the models through the uncached call-form test."""
    monkeypatch.setattr(compressor_efficiency, "_takes_speed", _takes_speed_every_call)
    monkeypatch.setattr(_opt_utils, "_takes_speed", _takes_speed_every_call)


@pytest.fixture
def counted_signatures(monkeypatch):
    calls: list[object] = []
    original = inspect.signature

    def counting(obj, *args, **kwargs):
        calls.append(obj)
        return original(obj, *args, **kwargs)

    monkeypatch.setattr(inspect, "signature", counting)
    return calls


def _same(a, b) -> bool:
    """Equal to the last bit; NaN equals NaN, the sign of zero counts."""
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return (math.isnan(a) and math.isnan(b)) or struct.pack("<d", a) == struct.pack("<d", b)
    if isinstance(a, dict):
        return list(a) == list(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


# ---------------------------------------------------------------------------
# Callables of every kind
# ---------------------------------------------------------------------------


class _SlotsModel:
    __slots__ = ("value",)  # no __weakref__: cannot be a weak key

    def __init__(self, value):
        self.value = value

    def __call__(self, pr):
        return self.value


class _UnhashableModel:
    def __init__(self, value):
        self.value = value

    def __eq__(self, other):  # sets __hash__ to None
        return isinstance(other, _UnhashableModel) and other.value == self.value

    def __call__(self, pr, rps):
        return self.value + 0.001 * rps


class _Holder:
    def eff(self, pr, rps):
        return 0.5 + 0.002 * rps


def _speed_by_keyword(pr, *, rps):
    return 0.4 + 0.003 * rps


_HOLDER = _Holder()
#: (callable, value at PR 3, N 40, cached as a weak key)
CALLABLES = {
    "builtin (PR, rps)": (operator.truediv, 3.0 / 40.0, True),
    "builtin (PR)": (math.log10, math.log10(3.0), True),
    "__slots__ instance": (_SlotsModel(0.61), 0.61, False),
    "unhashable instance": (_UnhashableModel(0.6), 0.6 + 0.001 * 40.0, False),
    "bound method": (_HOLDER.eff, 0.5 + 0.002 * 40.0, True),
    "partial, positional": (functools.partial(lambda k, pr, rps: k * rps, 0.01), 0.01 * 40.0, True),
    # rps bound by keyword: the model takes (PR) only and keeps its own speed.
    "partial, speed by keyword": (functools.partial(_speed_by_keyword, rps=50.0), 0.4 + 0.003 * 50.0, True),
}


@pytest.mark.parametrize("name", list(CALLABLES))
def test_every_kind_of_callable_is_called_in_its_form(name, counted_signatures):
    model, expected, weak_key = CALLABLES[name]
    for _ in range(3):
        assert _eval_eff(model, 3.0, 40.0) == expected
    # A weak key is inspected once; anything else on every call, as before.
    assert len(counted_signatures) == (1 if weak_key else 3)


@pytest.mark.parametrize("name", list(CALLABLES))
def test_every_kind_of_callable_gives_what_inspecting_every_call_gives(name, monkeypatch):
    model = CALLABLES[name][0]
    cached = [_eval_eff(model, pr, rps) for pr, rps in ((1.4, 20.0), (3.0, 40.0), (7.5, 95.0))]
    monkeypatch.setattr(compressor_efficiency, "_takes_speed", _takes_speed_every_call)
    assert cached == [_eval_eff(model, pr, rps) for pr, rps in ((1.4, 20.0), (3.0, 40.0), (7.5, 95.0))]


def test_scalars_and_bad_inputs_behave_as_before():
    assert _eval_eff(0.7, 3.0, 40.0) == 0.7
    assert _eval_eff(1.3, 3.0, 40.0) == 1.0
    assert _eval_eff("0.5", 3.0, 40.0) == 0.5
    with pytest.raises(TypeError):
        _eval_eff(None, 3.0, 40.0)
    with pytest.raises(TypeError, match="too many positional arguments"):
        _eval_eff(lambda: 0.7, 3.0, 40.0)
    with pytest.raises(TypeError, match="missing a required argument"):
        _eval_eff(lambda pr, rps, extra: 0.7, 3.0, 40.0)
    with pytest.raises(compressor_efficiency.InvalidCompressorEfficiency, match="finite and positive"):
        _eval_eff(lambda pr: math.nan, 3.0, 40.0)


def test_a_model_that_fails_the_binding_is_not_cached():
    def takes_nothing():
        return 0.7

    for _ in range(2):
        with pytest.raises(TypeError):
            _eval_eff(takes_nothing, 3.0, 40.0)
    assert takes_nothing not in compressor_efficiency._TAKES_SPEED


class _AllEqual:
    """Every instance compares equal and hashes alike, whatever its call form."""

    def __eq__(self, other):
        return isinstance(other, _AllEqual)

    def __hash__(self):
        return 7


class _AllEqualPR(_AllEqual):
    def __call__(self, pr):
        return 0.61


class _AllEqualPRSpeed(_AllEqual):
    def __call__(self, pr, rps):
        return 0.5 + 0.002 * rps


def test_models_that_compare_equal_keep_their_own_call_forms(counted_signatures):
    # The cache finds keys by equality; an answer must still belong to the object.
    pr_only, pr_speed = _AllEqualPR(), _AllEqualPRSpeed()
    assert pr_only == pr_speed and hash(pr_only) == hash(pr_speed)
    for _ in range(3):
        assert _eval_eff(pr_only, 3.0, 40.0) == 0.61
        assert _eval_eff(pr_speed, 3.0, 40.0) == 0.5 + 0.002 * 40.0
    for _ in range(2):
        assert compressor_efficiency._takes_speed(pr_only) is False
        assert compressor_efficiency._takes_speed(pr_speed) is True
    # The first object keeps its cached answer; the equal one is inspected every time.
    assert sum(obj is pr_only for obj in counted_signatures) == 1
    assert sum(obj is pr_speed for obj in counted_signatures) == 5


def test_equal_models_through_the_solver_evaluator():
    pr_only, pr_speed = _AllEqualPR(), _AllEqualPRSpeed()
    assert _opt_utils.efficiency_evaluator(pr_only)(3.0, 40.0) == 0.61
    assert _opt_utils.efficiency_evaluator(pr_speed)(3.0, 40.0) == 0.5 + 0.002 * 40.0


class _HashRaises:
    def __hash__(self):
        raise ValueError("no hash")

    def __call__(self, pr, rps):
        return 0.55


def test_a_model_whose_hash_raises_is_evaluated_as_before(counted_signatures):
    model = _HashRaises()
    for _ in range(2):
        assert _eval_eff(model, 3.0, 40.0) == 0.55
    assert counted_signatures == [model, model]


# ---------------------------------------------------------------------------
# A collected model and a new one at its address
# ---------------------------------------------------------------------------


def test_a_new_model_at_a_reused_address_is_inspected_afresh():
    gc.collect()
    entries_before = len(compressor_efficiency._TAKES_SPEED)
    form_at_address: dict[int, bool] = {}
    reused_with_other_form = 0
    for i in range(400):
        takes_speed = bool(i % 2)
        if takes_speed:

            def model(pr, rps):
                return 0.5 + 0.001 * rps

            expected = 0.5 + 0.001 * 40.0
        else:

            def model(pr):
                return 0.6

            expected = 0.6
        address = id(model)
        if form_at_address.get(address, takes_speed) != takes_speed:
            reused_with_other_form += 1
        form_at_address[address] = takes_speed
        assert _eval_eff(model, 3.0, 40.0) == expected
        del model
        gc.collect()
    assert len(compressor_efficiency._TAKES_SPEED) <= entries_before  # every collected model left the cache
    if sys.implementation.name == "cpython":
        # The address of a freed function is handed out again at once; the
        # loop must have met a model of the other form at a reused address.
        assert reused_with_other_form > 0


def test_the_cache_does_not_keep_a_model_alive():
    import weakref

    def model(pr, rps):
        return 0.7

    _eval_eff(model, 3.0, 40.0)
    assert model in compressor_efficiency._TAKES_SPEED
    alive = weakref.ref(model)
    del model
    gc.collect()
    assert alive() is None


# ---------------------------------------------------------------------------
# The five families
# ---------------------------------------------------------------------------


def _construct(cls):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return cls()


def _steady(model, kwargs):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = model.analyze_steady(**kwargs)
    return result, [(w.category, str(w.message)) for w in caught]


#: Representative points: ordinary, part load, off, and points the models cannot run.
FAMILIES = {
    "ASHP": (
        tmhp.AirSourceHeatPump,
        [
            dict(Q_r_iu=q, T0=t0, T_a_room=20.0 if q <= 0 else 26.0)
            for t0, q in [(-20.0, -14000.0), (-2.0, -5000.0), (7.0, -800.0), (7.0, 0.0), (35.0, 6000.0)]
        ],
    ),
    "GSHP": (
        tmhp.GroundSourceHeatPump,
        [
            dict(Q_r_iu=q, T0=t0, T_a_room=20.0 if q <= 0 else 26.0)
            for t0, q in [(-10.0, -14000.0), (0.0, -5000.0), (10.0, -800.0), (30.0, 6000.0)]
        ],
    ),
    "ASHPB": (
        tmhp.AirSourceHeatPumpBoiler,
        [
            dict(T_tank_w=tw, T0=t0, Q_ref_tank=q)
            for t0, tw, q in [(-25.0, 60.0, 6000.0), (0.0, 50.0, 2500.0), (7.0, 70.0, 12000.0), (20.0, 35.0, 500.0)]
        ],
    ),
    "GSHPB": (
        tmhp.GroundSourceHeatPumpBoiler,
        [
            dict(T_tank_w=tw, T_source=ts, Q_ref_tank=q)
            for ts, tw, q in [(-8.0, 60.0, 6000.0), (0.0, 50.0, 2500.0), (10.0, 70.0, 12000.0), (20.0, 35.0, 500.0)]
        ],
    ),
    "WSHPB": (
        tmhp.WaterSourceHeatPumpBoiler,
        [
            dict(T_tank_w=tw, T_source=ts, Q_ref_tank=q)
            for ts, tw, q in [(-2.0, 60.0, 6000.0), (4.0, 50.0, 2500.0), (10.0, 70.0, 12000.0), (20.0, 35.0, 500.0)]
        ],
    ),
}


def _pr_only(pr):
    return 0.72 - 0.01 * pr


def _pr_speed(pr, rps):
    return 0.95 - 0.012 * pr - 0.0004 * rps


@pytest.mark.parametrize("family", list(FAMILIES))
def test_a_steady_grid_is_the_same_as_inspecting_every_call(family, request):
    cls, grid = FAMILIES[family]
    cached = [_steady(_construct(cls), kwargs) for kwargs in grid]
    request.getfixturevalue("inspect_every_call")
    reference = [_steady(_construct(cls), kwargs) for kwargs in grid]
    assert any(result.get("hp_is_on") for result, _ in cached)
    assert _same(cached, reference)


@pytest.mark.parametrize("attribute", ["eta_cmp_isen", "eta_cmp_vol", "eta_cmp"])
@pytest.mark.parametrize("family", list(FAMILIES))
def test_reassigning_an_efficiency_after_construction_changes_its_call_form(family, attribute, monkeypatch):
    cls, grid = FAMILIES[family]
    point = grid[1]  # an ordinary heating point of each family
    calls: list[tuple] = []

    def pr_speed(pr, rps):
        calls.append(("pr_speed", rps))
        return _pr_speed(pr, rps)

    def pr_only(pr):
        calls.append(("pr_only",))
        return _pr_only(pr)

    # Default model, then (PR, rps), then (PR), then (PR, rps) again on the same object.
    model = _construct(cls)
    cached = [_steady(model, point)]
    for function in (pr_speed, pr_only, pr_speed):
        setattr(model, attribute, function)
        calls.clear()
        cached.append(_steady(model, point))
        assert {call[0] for call in calls} == {function.__name__}
    assert any(call[0] == "pr_speed" and call[1] is not None for call in calls)

    monkeypatch.setattr(compressor_efficiency, "_takes_speed", _takes_speed_every_call)
    monkeypatch.setattr(_opt_utils, "_takes_speed", _takes_speed_every_call)
    model = _construct(cls)
    reference = [_steady(model, point)]
    for function in (pr_speed, pr_only, pr_speed):
        setattr(model, attribute, function)
        reference.append(_steady(model, point))
    assert _same(cached, reference)
    # The reassignment takes effect: the user efficiency moves the result.
    assert not _same(cached[0], cached[2])
