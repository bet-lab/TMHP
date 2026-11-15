"""Approach-temperature conductance defaults wired into the model families (issues #115-#118).

A default conductance is the effective ``UA`` of the model's own exchanger that reproduces the leaving-end
approach real machines of its stratum print (``tmhp.default_inputs.estimate_hx_UA``). The tests check that
round trip -- the coil equation alone, and the cycle through ``analyze_steady`` at the rating point -- that a
conductance the caller gives is used as given, that the estimates are recorded, and that the law gives a
value at any flow.
"""

from __future__ import annotations

import math
import warnings

import pytest

from tmhp import AirSourceHeatPump
from tmhp.air_properties import air_heat_capacity_rate
from tmhp.default_inputs import _artifact, hx_duty_per_nameplate

CAPACITIES = (3500.0, 8000.0, 15000.0)
#: Coil-equation round trip: the default reproduces the law's leaving approach to this [K].
ROUND_TRIP_TOL_K = 1e-6
#: Through the cycle at the rating point [K].
CYCLE_TOL_K = 0.5
#: Relative difference under which a solved duty or flow counts as the rating one.
SAME_POINT_REL = 0.01


def law_leaving_approach(stratum: str) -> float:
    """The stratum's leaving-end approach [K], as the shipped artifact states it."""
    law = _artifact("hx_approach_regression.json")["strata"][stratum]["law"]
    assert law["kind"] == "leaving_approach"
    return float(law["dT_out_K"])


def leaving_approach(UA: float, C: float, duty: float) -> float:
    """Leaving-end approach [K] of an isothermal-refrigerant exchanger, ``Q = C (1 - exp(-UA / C)) ITD``."""
    inlet_difference = duty / (C * (1.0 - math.exp(-UA / C)))
    return inlet_difference - duty / C


def build(cls, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return cls(**kwargs)


def check_cycle_approach(
    label: str,
    *,
    law: float,
    model_dT: float,
    UA_rated: float,
    flow: float,
    flow_rated: float,
    exponent: float,
    C: float,
    duty: float,
    duty_rated: float,
) -> dict[str, float]:
    """Assert the leaving approach the cycle ran at against the law (rating duty and flow) or the coil equation.

    Where the solved duty and flow are the rating ones the cycle must reproduce the law within
    ``CYCLE_TOL_K``. Elsewhere it is held to the leaving approach the coil equation gives at the solved
    duty and flow with the default conductance, and the difference from the law is stated in the message.
    """
    UA = UA_rated * (flow / flow_rated) ** exponent
    recomputed = leaving_approach(UA, C, duty)
    same_point = abs(duty / duty_rated - 1.0) < SAME_POINT_REL and abs(flow / flow_rated - 1.0) < SAME_POINT_REL
    message = (
        f"{label}: cycle leaving approach {model_dT:.3f} K, law {law:.3f} K (difference {model_dT - law:+.3f} K), "
        f"coil equation at the solved point {recomputed:.3f} K; solved duty {duty:.0f} W vs rating "
        f"{duty_rated:.0f} W ({duty / duty_rated - 1.0:+.1%}), flow ratio {flow / flow_rated:.4f}"
    )
    target = law if same_point else recomputed
    assert abs(model_dT - target) <= CYCLE_TOL_K, message
    return {"model": model_dT, "law": law, "recomputed": recomputed, "duty_ratio": duty / duty_rated}


# ---------------------------------------------------------------------------
# AirSourceHeatPump
# ---------------------------------------------------------------------------
OU_COND = "outdoor_coil|fin_tube|condensing"
OU_EVAP = "outdoor_coil|fin_tube|evaporating"
IU_WALL = "indoor_coil|wall_mounted|fin_tube|condensing"


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_ashp_outdoor_defaults_reproduce_the_law_through_the_coil_equation(capacity: float) -> None:
    model = build(AirSourceHeatPump, hp_capacity=capacity)
    for stratum, UA, T_air in ((OU_COND, model.UA_ou_rated, 35.0), (OU_EVAP, model.UA_ou_evap_rated, 7.0)):
        C = air_heat_capacity_rate(model.dV_ou_fan_a_rated, T_air)
        duty = hx_duty_per_nameplate(stratum, "cooling") * capacity
        assert leaving_approach(UA, C, duty) == pytest.approx(law_leaving_approach(stratum), abs=ROUND_TRIP_TOL_K)


@pytest.mark.parametrize("capacity", CAPACITIES)
def test_ashp_rating_points_run_at_the_law_approach(capacity: float) -> None:
    """ISO 5151 T1 (35/27 cooling) and H1 (7/20 heating): the outdoor coil at the law's leaving approach."""
    model = build(AirSourceHeatPump, hp_capacity=capacity)
    for mode, Q, T0, T_room, stratum, UA in (
        ("cooling", capacity, 35.0, 27.0, OU_COND, model.UA_ou_rated),
        ("heating", -capacity, 7.0, 20.0, OU_EVAP, model.UA_ou_evap_rated),
    ):
        row = model.analyze_steady(Q_r_iu=Q, T0=T0, T_a_room=T_room, return_dict=True, verbose=False)
        assert row["failure_reason"] == "none"
        T_sat = row["T_ref_cond_sat_l [°C]"] if mode == "cooling" else row["T_ref_evap_sat [°C]"]
        check_cycle_approach(
            f"ASHP {capacity:.0f} W {mode} outdoor coil",
            law=law_leaving_approach(stratum),
            model_dT=abs(T_sat - row["T_ou_a_mid [°C]"]),
            UA_rated=UA,
            flow=row["dV_ou_a [m3/s]"],
            flow_rated=model.dV_ou_fan_a_ref,
            exponent=model.n_ou,
            C=air_heat_capacity_rate(row["dV_ou_a [m3/s]"], T0),
            duty=abs(row["Q_ref_ou [W]"]),
            duty_rated=hx_duty_per_nameplate(stratum, "cooling") * capacity,
        )


def test_ashp_given_conductances_are_used_as_given() -> None:
    one = build(AirSourceHeatPump, hp_capacity=8000.0, UA_ou_rated=1234.0)
    # A given coil value serves both refrigerant roles; it is not split.
    assert (one.UA_ou_rated, one.UA_ou_evap_rated) == (1234.0, 1234.0)
    assert not {"UA_ou_rated", "UA_ou_evap_rated"} & set(one.default_input_estimates)
    assert one.UA_iu_rated == pytest.approx(0.16 * 8000.0)  # no longer tied to the outdoor coil
    both = build(AirSourceHeatPump, hp_capacity=8000.0, UA_ou_rated=1234.0, UA_ou_evap_rated=987.0, UA_iu_rated=555.0)
    assert (both.UA_ou_rated, both.UA_ou_evap_rated, both.UA_iu_rated) == (1234.0, 987.0, 555.0)
    assert not {"UA_ou_rated", "UA_ou_evap_rated", "UA_iu_rated"} & set(both.default_input_estimates)
    evap_only = build(AirSourceHeatPump, hp_capacity=8000.0, UA_ou_evap_rated=987.0)
    assert evap_only.UA_ou_evap_rated == 987.0
    assert evap_only.default_input_estimates["UA_ou_rated"].key == OU_COND
    # The heating rating point and the heating cycle use the evaporating value.
    assert both._rating_side("source", "heating", 7.0).UA == 987.0
    assert both._rating_side("source", "cooling", 35.0).UA == 1234.0


def test_ashp_default_input_estimates_carry_the_conductances() -> None:
    model = build(AirSourceHeatPump, hp_capacity=8000.0)
    estimates = model.default_input_estimates
    assert estimates["UA_ou_rated"].key == OU_COND and estimates["UA_ou_rated"].value == model.UA_ou_rated
    assert estimates["UA_ou_evap_rated"].key == OU_EVAP
    assert estimates["UA_ou_evap_rated"].value == model.UA_ou_evap_rated
    assert {estimates[name].status for name in ("UA_ou_rated", "UA_ou_evap_rated")} == {"shipped"}
    indoor = estimates["UA_iu_rated"]
    assert (indoor.key, indoor.status, indoor.value) == (IU_WALL, "provisional", pytest.approx(0.16 * 8000.0))
    reason = indoor.assumptions[-1]
    assert reason.startswith("Provisional: kept at the previous rule")
    assert "one manufacturer" in reason and "8 product lines" in reason and "no estimate is issued" in reason


@pytest.mark.parametrize("factor", (0.85, 1.15))
def test_ashp_conductance_exists_at_any_airflow(factor: float) -> None:
    reference = build(AirSourceHeatPump, hp_capacity=8000.0)
    model = build(AirSourceHeatPump, hp_capacity=8000.0, dV_ou_fan_a_rated=factor * reference.dV_ou_fan_a_rated)
    for UA in (model.UA_ou_rated, model.UA_ou_evap_rated):
        assert math.isfinite(UA) and UA > 0
    # More air at the same leaving approach needs a larger conductance.
    assert (model.UA_ou_rated > reference.UA_ou_rated) == (factor > 1.0)
