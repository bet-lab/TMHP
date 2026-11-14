"""What one cycle evaluation of the space-conditioning models costs, and that the cheaper form is the same numbers.

A steady solve evaluates the refrigerant cycle a hundred times or more
(``_calc_state``), a 24 h run tens of thousands of times, so what one
evaluation repeats is what a run costs. Three repetitions were most of it:

* the call form of each efficiency model was inspected on every speed
  candidate (``inspect.signature``, some twenty times per evaluation);
* the cycle state was built twice -- once at unit isentropic efficiency for
  the speed solve, once at the solved efficiency -- with every flash repeated,
  and the isentropic flash a third time in between;
* each coil's airflow was bisected through SciPy with a Python callback per
  step (about forty per coil);
* the compressor-speed search evaluated its residual again at points it had
  just evaluated (both ends of a bracket, the peak it had located);
* a steady point's exergy post-processing filled some fifty columns of a
  one-row frame one assignment at a time (5 to 6 ms, a quarter of the point).

The tests count those operations rather than time them (a count does not
depend on the load of the machine), and pin each cheaper form to the one it
replaces to the last bit: the goldens and the committed validation results
are compared at 1e-6, and the searches around a cycle evaluation compare
candidates, so a cheaper evaluation must not move one.
"""

from __future__ import annotations

import inspect
import math
import warnings

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import bisect, brentq, minimize_scalar

import tmhp
from tmhp import _opt_utils, heat_exchanger, refrigerant
from tmhp._opt_utils import efficiency_evaluator
from tmhp.compressor_efficiency import (
    InvalidCompressorEfficiency,
    _eval_eff,
    make_eta_em,
    make_eta_isen,
    make_eta_vol,
)
from tmhp.compressor_speed import CAPACITY_CLAMPED_MAX, CAPACITY_CLAMPED_MIN, solve_compressor_speed
from tmhp.heat_exchanger import _bisect_fan_flow, calc_HX_perf_for_target_heat
from tmhp.refrigerant import calc_ref_state, calc_ref_state_without_discharge, with_discharge_state


@pytest.fixture(scope="module")
def ashp():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return tmhp.AirSourceHeatPump()


@pytest.fixture(scope="module")
def gshp():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return tmhp.GroundSourceHeatPump()


#: An ordinary candidate of each model: inside the envelope, no pressure-ratio
#: clamp, no discharge-temperature control, both coils inside their fan range.
ASHP_POINT = dict(dT_ref_evap=7.0, dT_ref_cond=14.0, Q_r_iu=-3000.0, T0=7.0, T_a_room=20.0)
GSHP_POINT = dict(dT_ref_evap=7.0, dT_ref_cond=18.0, Q_r_iu=-3000.0, T0=0.0, T_a_room=20.0)


def _ordinary(state):
    assert state is not None and state["hp_is_on"] and state["converged"]
    assert state["capacity_clamped"] is None and not state["discharge_control"]
    return state


@pytest.fixture
def counted_flashes(monkeypatch):
    """Input pairs of every property flash made through :mod:`tmhp.refrigerant`."""
    calls: list[tuple[str, str]] = []
    original = refrigerant._props

    def counting(outputs, name1, value1, name2, value2, ref):
        calls.append((name1, name2))
        return original(outputs, name1, value1, name2, value2, ref)

    monkeypatch.setattr(refrigerant, "_props", counting)
    return calls


@pytest.fixture
def counted_signatures(monkeypatch):
    """Objects whose signature is inspected."""
    calls: list[object] = []
    original = inspect.signature

    def counting(obj, *args, **kwargs):
        calls.append(obj)
        return original(obj, *args, **kwargs)

    monkeypatch.setattr(inspect, "signature", counting)
    return calls


# ---------------------------------------------------------------------------
# One cycle evaluation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", ["ashp", "gshp"])
def test_a_cycle_evaluation_makes_each_flash_once(family, request, counted_flashes):
    model = request.getfixturevalue(family)
    point = ASHP_POINT if family == "ashp" else GSHP_POINT
    counted_flashes.clear()  # the constructor's reference state is not a cycle evaluation
    _ordinary(model._calc_state(**point))
    # Saturation at both pressures, suction, isentropic discharge, saturated
    # vapour at the condensing pressure, liquid, expansion outlet, discharge.
    assert sorted(counted_flashes) == sorted(
        [("T", "Q"), ("T", "Q"), ("T", "P"), ("P", "S"), ("P", "Q"), ("T", "P"), ("P", "H"), ("P", "H")]
    )


@pytest.mark.parametrize("family", ["ashp", "gshp"])
def test_a_cycle_evaluation_inspects_no_signature(family, request, counted_signatures):
    model = request.getfixturevalue(family)
    point = ASHP_POINT if family == "ashp" else GSHP_POINT
    for eta in (model.eta_cmp_vol, model.eta_cmp_isen, model.eta_cmp):
        efficiency_evaluator(eta)  # the call form of each model is decided here, once
    counted_signatures.clear()
    _ordinary(model._calc_state(**point))
    assert counted_signatures == []


def test_a_coil_inside_its_fan_range_is_not_solved_through_scipy(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("root_scalar called for a bracketed coil")

    monkeypatch.setattr(heat_exchanger, "root_scalar", refuse)
    coil = calc_HX_perf_for_target_heat(**COIL)
    assert coil["converged"] and not coil["min_limit"] and not coil["max_limit"]
    assert coil["capacity_margin_W"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Efficiency models: the call form decided once
# ---------------------------------------------------------------------------


class _Unhashable:
    """A callable that can be neither hashed nor cached by identity."""

    __hash__ = None  # type: ignore[assignment]

    def __call__(self, pr, rps):
        return 0.6 + 0.01 * pr - 0.001 * rps


class _Curve:
    def one(self, pr):
        return 0.9 - 0.02 * pr

    def two(self, pr, rps):
        return 0.9 - 0.02 * pr + 0.0005 * rps


_CURVE = _Curve()
MODELS = [
    0.7,
    1,
    1.3,  # above one: set to one
    lambda pr: 0.8 - 0.03 * pr,
    lambda pr, rps: 0.8 - 0.03 * pr + 0.001 * rps,
    lambda pr, rps=50.0: 0.5 + 0.0002 * rps * pr,
    lambda pr, rps: 1.7,  # above one: set to one
    _CURVE.one,
    _CURVE.two,
    _Unhashable(),
    make_eta_vol(60.0),
    make_eta_isen(60.0),
    make_eta_em(60.0),
    make_eta_isen(60.0).raw,
]
POINTS = [(1.2, 15.0), (2.0, 53.0), (3.7, 40.0), (6.5, 72.5), (9.0, 110.0)]


def _outcome(function, *args):
    """The value, or the type of the efficiency error raised instead."""
    try:
        return function(*args)
    except InvalidCompressorEfficiency as error:
        return type(error)


@pytest.mark.parametrize("model", MODELS)
def test_evaluator_returns_what_eval_eff_returns(model):
    evaluate = efficiency_evaluator(model)
    outcomes = [_outcome(evaluate, pr, rps) for pr, rps in POINTS]
    assert outcomes == [_outcome(_eval_eff, model, pr, rps) for pr, rps in POINTS]
    assert any(isinstance(value, float) for value in outcomes)


@pytest.mark.parametrize("bad", [0.0, -0.2, math.nan, math.inf])
def test_evaluator_rejects_what_eval_eff_rejects(bad):
    for model in (bad, lambda pr: bad, lambda pr, rps: bad):
        with pytest.raises(InvalidCompressorEfficiency):
            _eval_eff(model, 3.0, 40.0)
        with pytest.raises(InvalidCompressorEfficiency):
            efficiency_evaluator(model)(3.0, 40.0)


def test_a_model_that_takes_no_pressure_ratio_fails_as_before():
    def takes_nothing():
        return 0.7

    with pytest.raises(TypeError):
        _eval_eff(takes_nothing, 3.0, 40.0)
    with pytest.raises(TypeError):
        efficiency_evaluator(takes_nothing)


def test_the_call_form_of_a_model_is_inspected_once(counted_signatures):
    def model(pr, rps):
        return 0.7

    for _ in range(3):
        evaluate = efficiency_evaluator(model)
        assert evaluate(3.0, 40.0) == 0.7
    assert counted_signatures == [model]
    assert model in _opt_utils._TAKES_SPEED


def test_the_call_form_cache_does_not_keep_a_model_alive():
    import gc
    import weakref

    def model(pr):
        return 0.7

    efficiency_evaluator(model)
    alive = weakref.ref(model)
    del model
    gc.collect()
    assert alive() is None


# ---------------------------------------------------------------------------
# Cycle state: everything but the discharge state, then the discharge state
# ---------------------------------------------------------------------------

SUCTION = ((0.0, 1.0), (5.0, 1.0), (0.0, 0.92))


@pytest.mark.parametrize("ref", ["R32", "R290", "R410A"])
def test_two_part_cycle_state_is_the_one_call_state(ref):
    for T_evap, T_cond in ((253.15, 318.15), (283.15, 303.15)):
        for superheat, x in SUCTION:
            for subcool in (0.0, 3.0):
                kwargs = dict(dT_superheat=superheat, dT_subcool=subcool, x_suction=x)
                part, h_isen = calc_ref_state_without_discharge(T_evap, T_cond, ref, mode="cooling", **kwargs)
                before = dict(part)
                assert all(
                    math.isnan(part[key])
                    for key in (
                        "h_ref_cmp_out [J/kg]",
                        "T_ref_cmp_out_K",
                        "T_ref_cmp_out [°C]",
                        "s_ref_cmp_out [J/(kg·K)]",
                    )
                )
                for eta in (1.0, 0.72, 0.4):
                    whole = calc_ref_state(T_evap, T_cond, ref, eta, mode="cooling", **kwargs)
                    two_part = with_discharge_state(part, h_isen, eta, ref)
                    # Same values, and the same key order: it is the column order of a result frame.
                    assert list(two_part.items()) == list(whole.items())
                assert list(part) == list(before) and all(
                    part[k] == v or (v != v and part[k] != part[k]) for k, v in before.items()
                )


def test_a_discharge_state_coolprop_cannot_evaluate_stays_nan():
    part, h_isen = calc_ref_state_without_discharge(268.15, 338.15, "R32", dT_superheat=5.0)
    hot = with_discharge_state(part, h_isen, 0.02, "R32")  # far above the fluid's temperature range
    whole = calc_ref_state(268.15, 338.15, "R32", 0.02, dT_superheat=5.0)
    assert math.isnan(hot["T_ref_cmp_out_K"]) and math.isnan(hot["s_ref_cmp_out [J/(kg·K)]"])
    assert hot["h_ref_cmp_out [J/kg]"] == whole["h_ref_cmp_out [J/kg]"]


# ---------------------------------------------------------------------------
# Coil airflow: the bisection written out
# ---------------------------------------------------------------------------

COIL = dict(
    Q_ref_target=2400.0,
    T_a_in_C=2.0,
    T_ref_sat_K=269.15,
    A_cross=0.4,
    UA_rated=900.0,
    dV_fan_ref=0.8,
    dV_fan_min=0.12,
    dV_fan_max=0.8,
    exponent=0.71,
    rho_air="inlet",
)


@pytest.mark.parametrize("share", [0.02, 0.35, 0.7, 0.99])
@pytest.mark.parametrize("T_sat_K", [262.15, 269.15, 288.15])
@pytest.mark.parametrize("rho_air", [None, "inlet", 1.31])
def test_coil_airflow_is_scipys_bisection_to_the_last_bit(share, T_sat_K, rho_air):
    C_per_flow = heat_exchanger.c_a * heat_exchanger.resolve_air_density(rho_air, COIL["T_a_in_C"])
    dT = abs(COIL["T_a_in_C"] + 273.15 - T_sat_K)

    def capacity(dV):
        UA = heat_exchanger.calc_UA_from_dV_fan(
            dV, COIL["dV_fan_ref"], COIL["A_cross"], COIL["UA_rated"], COIL["exponent"]
        )
        C_air = C_per_flow * dV
        return C_air * (1 - heat_exchanger.np.exp(-UA / C_air)) * dT

    # A duty between what the coil carries at its smallest and its largest airflow.
    low, high = capacity(COIL["dV_fan_min"]), capacity(COIL["dV_fan_max"])
    duty = low + share * (high - low)

    def margin(dV):
        return capacity(dV) - duty

    reference = bisect(margin, COIL["dV_fan_min"], COIL["dV_fan_max"])
    solved = calc_HX_perf_for_target_heat(**dict(COIL, Q_ref_target=duty, T_ref_sat_K=T_sat_K, rho_air=rho_air))
    assert not solved["min_limit"] and not solved["max_limit"]
    assert solved["dV_fan"] == reference
    assert solved["converged"] is True
    root, converged = _bisect_fan_flow(
        COIL["dV_fan_min"], COIL["dV_fan_max"], margin(COIL["dV_fan_min"]), duty, COIL["UA_rated"],
        COIL["dV_fan_ref"], COIL["exponent"], C_per_flow, dT,
    )  # fmt: skip
    assert (root, converged) == (reference, True)


def test_a_coil_margin_that_is_not_a_number_is_reported_unconverged_as_before():
    """SciPy's ``root_scalar`` stops at the first point whose margin is NaN and reports it unconverged."""
    coil = calc_HX_perf_for_target_heat(**dict(COIL, T_ref_sat_K=math.nan))
    assert coil["converged"] is False and coil["dV_fan"] == COIL["dV_fan_min"]
    assert _bisect_fan_flow(0.12, 0.8, -1.0, 2400.0, 900.0, 0.8, 0.71, 1200.0, math.nan) == (0.46, False)


# ---------------------------------------------------------------------------
# Compressor speed: the residual evaluated once at each point
# ---------------------------------------------------------------------------


def _solve_compressor_speed_before(residual, rps_min, rps_max):
    """``solve_compressor_speed`` as it was: ``brentq`` on the bare residual, the peak evaluated again."""

    def value(rps):
        try:
            result = float(residual(rps))
            return result if math.isfinite(result) else -math.inf
        except ValueError:
            return -math.inf

    low = value(rps_min)
    if not math.isfinite(low):
        return rps_min, False, None
    if low > 0:
        return rps_min, True, CAPACITY_CLAMPED_MIN
    if low == 0:
        return rps_min, True, None
    high = value(rps_max)
    if math.isfinite(high) and high >= 0:
        try:
            return brentq(residual, rps_min, rps_max), True, None
        except ValueError:
            bound = rps_min if abs(low) < abs(high) else rps_max
            return bound, False, None
    peak = minimize_scalar(lambda rps: -value(rps), bounds=(rps_min, rps_max), method="bounded")
    n_peak, f_peak = float(peak.x), value(peak.x)
    if not math.isfinite(f_peak):
        return rps_min, False, None
    if f_peak < 0:
        if math.isfinite(high) and rps_max - n_peak < 1e-4 and high >= f_peak:
            return rps_max, True, CAPACITY_CLAMPED_MAX
        return n_peak, True, CAPACITY_CLAMPED_MAX
    return brentq(residual, rps_min, n_peak), True, None


def _past_100(x):
    if x > 100.0:
        raise ValueError("outside the fit")
    return 3.0 * x - 150.0


#: (residual, rps_min, rps_max): every branch of the speed search, from the
#: request met inside the band to the one a fit that turns down leaves.
SPEED_RESIDUALS = {
    "inside the band": (lambda x: 4.0 * x - 130.0, 20.0, 90.0),
    "numpy float": (lambda x: np.float64(0.9 * x * x - 700.0), 20.0, 90.0),
    "root at the lower end": (lambda x: x - 20.0, 20.0, 90.0),
    "root at the upper end": (lambda x: x - 90.0, 20.0, 90.0),
    "below the band": (lambda x: 5.0 + x, 20.0, 90.0),
    "above the band": (lambda x: 2.0 * x - 400.0, 20.0, 90.0),
    "above the band, turning down": (lambda x: -1.0 - 0.01 * (x - 60.0) ** 2, 20.0, 90.0),
    "turns down after the root": (lambda x: 90.0 - 0.05 * (x - 70.0) ** 2 - 60.0, 20.0, 110.0),
    "turns down from the start": (lambda x: 2.0 * x - 0.02 * x**3 - 30.0, 20.0, 110.0),
    "not a number above 90": (lambda x: 3.0 * x - 200.0 if x < 90.0 else math.nan, 20.0, 110.0),
    "not a number at the lower end": (lambda x: math.nan, 20.0, 90.0),
    "invalid past 100": (_past_100, 20.0, 120.0),
    "constant": (lambda x: -7.0, 20.0, 90.0),
}


@pytest.mark.parametrize("case", SPEED_RESIDUALS)
def test_speed_search_returns_what_it_returned_to_the_last_bit(case):
    residual, low, high = SPEED_RESIDUALS[case]
    assert solve_compressor_speed(residual, low, high) == _solve_compressor_speed_before(residual, low, high)
    solved = solve_compressor_speed(residual, low, high)
    before = _solve_compressor_speed_before(residual, low, high)
    assert float(solved[0]).hex() == float(before[0]).hex() and solved[1:] == before[1:]


def _counted(function, residual, low, high):
    calls = []

    def counting(rps):
        calls.append(rps)
        return residual(rps)

    return function(counting, low, high), calls


def test_speed_search_evaluates_a_bracket_end_only_once():
    residual, low, high = SPEED_RESIDUALS["inside the band"]
    now, calls = _counted(solve_compressor_speed, residual, low, high)
    before, calls_before = _counted(_solve_compressor_speed_before, residual, low, high)
    assert now == before and now[2] is None
    # brentq evaluated both ends of its bracket again; the interior points are the same, in the same order.
    assert calls_before.count(low) == 2 and calls_before.count(high) == 2
    assert [x for x in calls if x not in (low, high)] == [x for x in calls_before if x not in (low, high)]
    assert calls.count(low) == 1 and calls.count(high) == 1  # the evaluations that decide the branch
    assert len(calls) == len(calls_before) - 2


def test_speed_search_does_not_evaluate_the_peak_again():
    residual, low, high = SPEED_RESIDUALS["above the band"]
    now, calls = _counted(solve_compressor_speed, residual, low, high)
    before, calls_before = _counted(_solve_compressor_speed_before, residual, low, high)
    assert now == before == (high, True, CAPACITY_CLAMPED_MAX)
    assert len(calls) == len(calls_before) - 1


def test_speed_search_of_a_fit_that_turns_down_evaluates_the_bracket_ends_once():
    residual, low, high = SPEED_RESIDUALS["turns down after the root"]
    now, calls = _counted(solve_compressor_speed, residual, low, high)
    before, calls_before = _counted(_solve_compressor_speed_before, residual, low, high)
    assert now == before and now[1] and now[2] is None
    assert len(calls) == len(calls_before) - 3  # the peak, and both ends of the bracket on its rising branch


def test_speed_search_error_from_an_interior_point_still_reaches_the_caller():
    """``brentq`` on the rising branch of a turning-down fit: an invalid interior point is the caller's error."""

    def residual(x):
        if 60.0 < x < 61.0:
            raise ValueError("outside the fit")
        return 2.0 * x - 0.02 * x**3 - 30.0

    outcomes = []
    for solve in (solve_compressor_speed, _solve_compressor_speed_before):
        try:
            outcomes.append(solve(residual, 20.0, 110.0))
        except ValueError as error:
            outcomes.append(("ValueError", str(error)))
    assert outcomes[0] == outcomes[1]


def test_a_steady_point_solves_its_speed_with_fewer_residual_evaluations(monkeypatch, ashp):
    from tmhp import air_source_heat_pump

    counts = []
    original = air_source_heat_pump.solve_compressor_speed

    def counting(residual, rps_min, rps_max):
        n = [0]

        def counted(rps):
            n[0] += 1
            return residual(rps)

        out = original(counted, rps_min, rps_max)
        counts.append(n[0])
        return out

    monkeypatch.setattr(air_source_heat_pump, "solve_compressor_speed", counting)
    _ordinary(ashp._calc_state(**ASHP_POINT))
    # Both ends of the bracket and brentq's own steps; before, brentq evaluated the two ends again (10 here).
    assert len(counts) == 1 and counts[0] <= 9


# ---------------------------------------------------------------------------
# Exergy post-processing of a steady point
# ---------------------------------------------------------------------------

STEADY_ROWS = [
    dict(Q_r_iu=-4000.0, T0=7.0, T_a_room=20.0),  # heating
    dict(Q_r_iu=-200.0, T0=7.0, T_a_room=20.0),  # heating, on/off
    dict(Q_r_iu=4000.0, T0=35.0, T_a_room=27.0),  # cooling
    dict(Q_r_iu=-60000.0, T0=-15.0, T_a_room=20.0),  # above the ceiling
]


def _frame_row(model, result):
    return model.postprocess_exergy(pd.DataFrame([result])).iloc[0].to_dict()


def _same_row(a, b):
    assert list(a) == list(b)  # the keys, in order
    for key in a:
        assert type(a[key]) is type(b[key]), key
        assert repr(a[key]) == repr(b[key]), key  # the bits, NaN included


@pytest.fixture(scope="module")
def steady_rows(ashp):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return [ashp.analyze_steady(postprocess=False, return_dict=True, **point) for point in STEADY_ROWS]


@pytest.mark.parametrize("index", range(len(STEADY_ROWS)))
def test_exergy_of_a_row_is_the_frames_to_the_last_bit(ashp, steady_rows, index):
    row = steady_rows[index]
    assert row["hp_is_on"] and "h_ref_cmp_in [J/kg]" in row
    now = ashp._exergy_of_row(dict(row))
    _same_row(now, _frame_row(ashp, dict(row)))
    assert len([key for key in now if key not in row]) == 48  # the exergy columns, none missing, none extra


def test_a_steady_point_is_post_processed_without_a_frame(ashp, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("postprocess_exergy called for a usual row")

    monkeypatch.setattr(ashp, "postprocess_exergy", refuse)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = ashp.analyze_steady(**STEADY_ROWS[0])
    assert result["X_tot [W]"] == result["E_tot [W]"] and "X_eff_sys [-]" in result


def test_the_dataframe_result_of_a_steady_point_is_the_dict_result(ashp):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        frame = ashp.analyze_steady(return_dict=False, **STEADY_ROWS[2])
        as_dict = ashp.analyze_steady(**STEADY_ROWS[2])
    assert list(frame.columns) == list(as_dict)
    _same_row(frame.iloc[0].to_dict(), as_dict)


def _perturbations(row):
    """Rows a frame treats in its own way: a number missing, not a number, zero, negative, held as an int or ``None``."""
    yield "no outdoor outlet air", {k: v for k, v in row.items() if k != "T_ou_a_out [°C]"}
    yield "no indoor outlet air", {k: v for k, v in row.items() if k != "T_iu_a_out [°C]"}
    yield "no indoor airflow", {k: v for k, v in row.items() if k not in ("dV_iu_a [m3/s]", "T_iu_a_in [°C]")}
    yield "no mass flow", {k: v for k, v in row.items() if k != "m_dot_ref [kg/s]"}
    yield "no entropy at the discharge", {k: v for k, v in row.items() if k != "s_ref_cmp_out [J/(kg·K)]"}
    yield "no mode", {k: v for k, v in row.items() if k != "mode"}
    yield "no refrigerant state", {k: v for k, v in row.items() if not k.startswith(("h_ref", "s_ref"))}
    yield "no dead state", {k: v for k, v in row.items() if k != "T0 [°C]"}
    yield "mode off", {**row, "mode": "off"}
    yield "mode none", {**row, "mode": None}
    yield "airflow not a number", {**row, "dV_ou_a [m3/s]": math.nan}
    yield "airflow zero", {**row, "dV_iu_a [m3/s]": 0.0}
    yield "inlet air below absolute zero", {**row, "T_ou_a_in [°C]": -300.0}
    yield "dead state not a number", {**row, "T0 [°C]": math.nan}
    yield "dead state at absolute zero", {**row, "T0 [°C]": -273.15}
    yield "saturation at absolute zero", {**row, "T_ref_evap_sat [°C]": -273.15, "T_ref_cond_sat_v [°C]": -273.15}
    yield "compressor power zero", {**row, "E_cmp [W]": 0.0, "E_ou_fan [W]": 0.0, "E_iu_fan [W]": 0.0}
    yield "fan power not a number", {**row, "E_ou_fan [W]": math.nan, "E_iu_fan [W]": math.nan}
    yield "power as an int", {**row, "E_cmp [W]": 300}
    yield "power None", {**row, "E_ou_fan [W]": None}
    yield "numpy scalars", {k: (np.float64(v) if type(v) is float else v) for k, v in row.items()}
    yield "infinite enthalpy", {**row, "h_ref_exp_in [J/kg]": math.inf}


def _outcome_of(function, row):
    try:
        return "ok", function(dict(row))
    except Exception as error:  # the frame's own error, whatever it is, is the row's too
        return "raise", type(error)


@pytest.mark.parametrize("index", [0, 2])
def test_exergy_of_an_unusual_row_is_the_frames(ashp, steady_rows, index):
    for name, row in _perturbations(steady_rows[index]):
        frame = _outcome_of(lambda r: _frame_row(ashp, r), row)
        now = _outcome_of(ashp._exergy_of_row, row)
        assert frame[0] == now[0], name
        if frame[0] == "raise":
            assert frame[1] is now[1], name
        else:
            _same_row(now[1], frame[1])
