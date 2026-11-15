"""Constructor defaults taken from the catalogue decision module (issue #86).

A machine given only a capacity and a refrigerant gets the compressor
displacement and the maximum fan airflows :mod:`tmhp.default_inputs` derives
from manufacturer catalogues, and says so in ``default_input_estimates``. An
input the caller supplies is used as given and has no entry there.
"""

import pytest

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler, GroundSourceHeatPump
from tmhp.compressor_efficiency import RPS_FIT_MAX, RPS_FIT_MIN
from tmhp.default_inputs import DEFAULT_DISPLACEMENT_BASIS, estimate_displacement, estimate_max_airflow

CATALOGUE_DISPLACEMENT = [AirSourceHeatPump, GroundSourceHeatPump, AirSourceHeatPumpBoiler]


@pytest.mark.parametrize("cls", CATALOGUE_DISPLACEMENT)
@pytest.mark.parametrize("ref", ["R32", "R290", "R134a", "R410A"])
def test_default_displacement_is_the_catalogue_estimate(cls, ref):
    capacity = 8000.0
    model = cls(hp_capacity=capacity, ref=ref)
    estimate = estimate_displacement(capacity, ref)
    assert model.V_cmp_ref == estimate.value
    assert model.default_input_estimates["V_cmp_ref"] == estimate


@pytest.mark.parametrize("cls", CATALOGUE_DISPLACEMENT)
def test_default_displacement_follows_the_refrigerant(cls):
    """One capacity, three fluids: the displacement differs by their volumetric capacity."""
    by_ref = {ref: cls(hp_capacity=3500.0, ref=ref).V_cmp_ref * 1e6 for ref in ("R32", "R290", "R134a")}
    assert by_ref["R32"] == pytest.approx(10.55, abs=0.01)
    assert by_ref["R290"] == pytest.approx(19.68, abs=0.01)
    assert by_ref["R134a"] == pytest.approx(25.60, abs=0.01)


@pytest.mark.parametrize("cls", CATALOGUE_DISPLACEMENT)
def test_a_supplied_displacement_is_used_as_given(cls):
    model = cls(hp_capacity=8000.0, ref="R32", V_cmp_ref=42.0e-6)
    assert model.V_cmp_ref == 42.0e-6
    assert "V_cmp_ref" not in model.default_input_estimates


@pytest.mark.parametrize("cls", [AirSourceHeatPump, AirSourceHeatPumpBoiler])
def test_the_deprecated_displacement_name_is_a_supplied_input(cls):
    model = cls(hp_capacity=8000.0, ref="R32", V_disp_cmp=42.0e-6)
    assert model.V_cmp_ref == 42.0e-6
    assert "V_cmp_ref" not in model.default_input_estimates


def test_the_estimate_says_where_it_comes_from_and_whether_it_is_extrapolated():
    inside = AirSourceHeatPump(hp_capacity=4000.0).default_input_estimates["V_cmp_ref"]
    assert inside.key == DEFAULT_DISPLACEMENT_BASIS and inside.unit == "m3/rev"
    assert inside.version.startswith("displacement-v")
    assert inside.status == "shipped" and not inside.extrapolated
    assert set(inside.source_ids) == {"LG-ROTARY-2024", "SCI-ROTARY-2026"}
    assert "option A" in " ".join(inside.assumptions)
    low, high = inside.capacity_range_W
    # A capacity outside the catalogue rows still gets a value, flagged as extrapolated.
    outside = AirSourceHeatPump(hp_capacity=2.0 * high).default_input_estimates["V_cmp_ref"]
    assert outside.extrapolated and outside.value == pytest.approx(inside.value * 2.0 * high / 4000.0, rel=1e-9)
    assert AirSourceHeatPump(hp_capacity=0.5 * low).default_input_estimates["V_cmp_ref"].extrapolated


def test_default_input_estimates_is_read_only():
    model = AirSourceHeatPump()
    with pytest.raises(TypeError):
        model.default_input_estimates["V_cmp_ref"] = None  # type: ignore[index]


@pytest.mark.parametrize(
    ("cls", "kwargs"),
    [
        (AirSourceHeatPump, {}),
        (GroundSourceHeatPump, {}),
        (AirSourceHeatPumpBoiler, {}),
        (AirSourceHeatPumpBoiler, {"ref": "R290"}),
        (AirSourceHeatPumpBoiler, {"ref": "R32"}),
    ],
)
@pytest.mark.parametrize("capacity", [3500.0, 8000.0, 15000.0])
def test_the_rated_speed_of_a_default_machine_is_inside_the_fitted_speed_range(cls, kwargs, capacity):
    """The catalogue displacement puts the rated speed where the efficiency fits have data.

    With the earlier refrigerant-blind constant an R134a boiler was rated at
    89.8 rev/s, 1.24 times the upper edge of the fitted box, and an R32 split
    at 31.2 rev/s, below its lower edge.
    """
    model = cls(hp_capacity=capacity, **kwargs)
    assert RPS_FIT_MIN < model.rps_rated < RPS_FIT_MAX


# ---------------------------------------------------------------------------
# Maximum fan airflow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capacity", [3500.0, 8000.0])
@pytest.mark.parametrize("mode", ["cooling", "heating"])
def test_ashp_default_airflows_are_the_catalogue_maxima_of_the_rated_duty(capacity, mode):
    """The nameplate names the duty of the rating condition, and each law is fitted against its own duty."""
    model = AirSourceHeatPump(hp_capacity=capacity, rated_condition={"mode": mode})
    outdoor = estimate_max_airflow(capacity, side="outdoor", product="single_split", mode=mode)
    indoor = estimate_max_airflow(capacity, side="indoor", product="wall_mounted", mode=mode)
    assert model.dV_ou_fan_a_rated == outdoor.value
    assert model.dV_iu_fan_a_rated == indoor.value
    assert model.default_input_estimates["dV_ou_fan_a_rated"] == outdoor
    assert model.default_input_estimates["dV_iu_fan_a_rated"] == indoor
    assert outdoor.key == f"airflow|{mode}|outdoor|single_split"


def test_ashp_default_rating_duty_is_cooling():
    model = AirSourceHeatPump(hp_capacity=3500.0)
    assert model.default_input_estimates["dV_ou_fan_a_rated"].key == "airflow|cooling|outdoor|single_split"
    assert model.dV_ou_fan_a_rated == pytest.approx(0.5570, abs=5e-5)
    assert model.dV_iu_fan_a_rated == pytest.approx(0.2157, abs=5e-5)


@pytest.mark.parametrize("mode", ["cooling", "heating"])
def test_gshp_default_indoor_airflow_is_the_water_to_air_catalogue_maximum(mode):
    model = GroundSourceHeatPump(hp_capacity=8000.0, rated_condition={"mode": mode})
    estimate = estimate_max_airflow(8000.0, side="indoor", product="water_to_air", mode=mode)
    assert model.dV_iu_fan_a_rated == estimate.value
    assert model.default_input_estimates["dV_iu_fan_a_rated"] == estimate


def test_the_rated_airflow_is_the_largest_the_fan_reaches():
    """Catalogue airflows are maxima: reference, rated and maximum coincide (issue #86 decision 1)."""
    ashp = AirSourceHeatPump(hp_capacity=5000.0)
    assert ashp.dV_ou_fan_a_max == ashp.dV_ou_fan_a_ref == ashp.dV_ou_fan_a_rated
    assert ashp.dV_iu_fan_a_max == ashp.dV_iu_fan_a_ref == ashp.dV_iu_fan_a_rated
    assert ashp.A_cross_ou == pytest.approx(ashp.dV_ou_fan_a_rated / 2.0)
    gshp = GroundSourceHeatPump(hp_capacity=5000.0)
    assert gshp.dV_iu_fan_a_max == gshp.dV_iu_fan_a_ref == gshp.dV_iu_fan_a_rated


def test_supplied_airflows_are_used_as_given():
    ashp = AirSourceHeatPump(hp_capacity=3500.0, dV_ou_fan_a_rated=0.61, dV_iu_fan_a_rated=0.2067)
    assert (ashp.dV_ou_fan_a_rated, ashp.dV_iu_fan_a_rated) == (0.61, 0.2067)
    assert set(ashp.default_input_estimates) == {"V_cmp_ref"}
    only_indoor = AirSourceHeatPump(hp_capacity=3500.0, dV_iu_fan_a_ref=0.2067)
    assert only_indoor.dV_iu_fan_a_rated == 0.2067
    assert set(only_indoor.default_input_estimates) == {"V_cmp_ref", "dV_ou_fan_a_rated"}
    gshp = GroundSourceHeatPump(hp_capacity=8000.0, dV_iu_fan_a_rated=0.43, V_cmp_ref=2.4e-5)
    assert gshp.dV_iu_fan_a_rated == 0.43 and not gshp.default_input_estimates


def test_indoor_unit_type_selects_the_catalogue_class():
    floor = AirSourceHeatPump(hp_capacity=8000.0, indoor_unit_type="floor_standing")
    assert floor.default_input_estimates["dV_iu_fan_a_rated"].key == "airflow|cooling|indoor|floor_standing"
    assert floor.dV_iu_fan_a_rated != AirSourceHeatPump(hp_capacity=8000.0).dV_iu_fan_a_rated
    # A class without catalogue rows is refused, unless the unit's own airflow is given.
    with pytest.raises(ValueError, match="indoor_unit_type='cassette' has no catalogue airflow"):
        AirSourceHeatPump(hp_capacity=8000.0, indoor_unit_type="cassette")
    assert AirSourceHeatPump(indoor_unit_type="cassette", dV_iu_fan_a_rated=0.4).dV_iu_fan_a_rated == 0.4


def test_an_unknown_rating_mode_is_refused_before_any_default_is_taken():
    with pytest.raises(ValueError, match="ASHP has no 'defrost' rating condition"):
        AirSourceHeatPump(rated_condition={"mode": "defrost"})


def test_airflow_outside_the_catalogue_capacities_is_flagged():
    """The default water-to-air unit (4 kW) is smaller than the smallest catalogue row (4.7 kW)."""
    estimate = GroundSourceHeatPump().default_input_estimates["dV_iu_fan_a_rated"]
    assert estimate.extrapolated and estimate.capacity_range_W[0] > 4000.0
    assert not GroundSourceHeatPump(hp_capacity=8000.0).default_input_estimates["dV_iu_fan_a_rated"].extrapolated


def test_the_air_to_water_outdoor_airflow_is_not_wired():
    """The provisional air-to-water stratum is not a constructor default; the boiler keeps its constant."""
    boiler = AirSourceHeatPumpBoiler(hp_capacity=8000.0)
    assert boiler.dV_fan_a_rated == pytest.approx(8000.0 * 0.00015)
    assert set(boiler.default_input_estimates) == {"V_cmp_ref"}
    assert all(estimate.status == "shipped" for estimate in boiler.default_input_estimates.values())


@pytest.mark.parametrize("capacity", [2000.0, 4000.0, 8000.0])
def test_a_default_split_unit_delivers_its_nameplate_at_both_rating_points(capacity):
    """Up to 8 kW the catalogue airflows leave the rating points inside the approach-temperature bound."""
    model = AirSourceHeatPump(hp_capacity=capacity)
    for load, outdoor, room in ((capacity, 35.0, 27.0), (-capacity, 7.0, 20.0)):
        out = model.analyze_steady(Q_r_iu=load, T0=outdoor, T_a_room=room, verbose=False, postprocess=False)
        assert out["converged"] and out["capacity_clamped"] is None
        assert out["Q_ref_iu [W]"] == pytest.approx(abs(load), rel=1e-6)
