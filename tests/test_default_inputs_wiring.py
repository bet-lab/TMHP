"""Constructor defaults taken from the catalogue decision module (issue #86).

A machine given only a capacity and a refrigerant gets the compressor
displacement and the maximum fan airflows :mod:`tmhp.default_inputs` derives
from manufacturer catalogues, and says so in ``default_input_estimates``. An
input the caller supplies is used as given and has no entry there.
"""

import pytest

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler, GroundSourceHeatPump
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
    """The catalogue displacement puts the rated speed where the default efficiency models have data.

    The range is the speed span of the data hull of the machine's compressor
    type (``validity_domain``). With the earlier refrigerant-blind constant an
    R134a boiler was rated at 89.8 rev/s and an R32 split at 31.2 rev/s.
    """
    model = cls(hp_capacity=capacity, **kwargs)
    speeds = [rps for _, rps in model.eta_cmp_isen.validity_domain.vertices]
    assert min(speeds) < model.rps_rated < max(speeds)


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
    # The conductances are defaults (stated at the supplied airflows): tests/test_hx_ua_defaults.py.
    ua = {"UA_ou_rated", "UA_ou_evap_rated", "UA_iu_rated"}
    assert set(ashp.default_input_estimates) == {"V_cmp_ref", *ua}
    only_indoor = AirSourceHeatPump(hp_capacity=3500.0, dV_iu_fan_a_ref=0.2067)
    assert only_indoor.dV_iu_fan_a_rated == 0.2067
    assert set(only_indoor.default_input_estimates) == {"V_cmp_ref", "dV_ou_fan_a_rated", *ua}
    gshp = GroundSourceHeatPump(hp_capacity=8000.0, dV_iu_fan_a_rated=0.43, V_cmp_ref=2.4e-5)
    assert gshp.dV_iu_fan_a_rated == 0.43
    assert set(gshp.default_input_estimates) == {"UA_ground_rated", "UA_ground_evap_rated", "UA_iu_rated"}


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


@pytest.mark.parametrize("capacity", [3500.0, 8000.0, 15000.0])
def test_ashpb_default_outdoor_airflow_is_the_air_to_water_catalogue_maximum(capacity):
    """The largest fan step air-to-water outdoor units of that heating capacity publish (owner decision 2026-11-15).

    The constant before it, ``hp_capacity * 1.5e-4 m3/s``, was 1.0 to 1.5 times
    the catalogue airflow from 3.5 to 15 kW.
    """
    boiler = AirSourceHeatPumpBoiler(hp_capacity=capacity)
    law = estimate_max_airflow(capacity, side="outdoor", product="air_to_water", mode="heating")
    assert boiler.dV_fan_a_rated == law.value
    assert boiler.dV_fan_a_max == boiler.dV_fan_a_ref == boiler.dV_fan_a_rated
    estimate = boiler.default_input_estimates["dV_fan_a_rated"]
    assert estimate == law and estimate.key == "airflow|heating|outdoor|air_to_water"
    assert estimate.source_ids and estimate.assumptions  # where it comes from, and its scatter
    assert set(boiler.default_input_estimates) == {"V_cmp_ref", "dV_fan_a_rated", "UA_tank_hx", "UA_ou_rated"}


def test_a_published_outdoor_airflow_overrides_the_ashpb_default():
    boiler = AirSourceHeatPumpBoiler(hp_capacity=8000.0, dV_fan_a_rated=1.1)
    assert boiler.dV_fan_a_rated == 1.1
    # The conductances are defaults, the outdoor one stated at the published airflow.
    assert set(boiler.default_input_estimates) == {"V_cmp_ref", "UA_tank_hx", "UA_ou_rated"}
    # The earlier names of the argument are inputs too.
    assert AirSourceHeatPumpBoiler(dV_ou_fan_a_design=2.0).dV_fan_a_rated == 2.0
    assert AirSourceHeatPumpBoiler(dV_fan_a_ref=2.0).dV_fan_a_rated == 2.0


RATING_POINTS = {"cooling": (1.0, 35.0, 27.0), "heating": (-1.0, 7.0, 20.0)}  # ISO 5151 T1 / H1


@pytest.mark.parametrize("capacity", [1500.0, 4000.0, 9000.0, 15000.0, 30000.0])
@pytest.mark.parametrize("indoor_unit_type", ["wall_mounted", "floor_standing"])
@pytest.mark.parametrize("rated_mode", ["cooling", "heating"])
def test_a_default_split_unit_delivers_its_nameplate_at_both_rating_points(capacity, indoor_unit_type, rated_mode):
    """No default airflow cuts the rated capacity (issue #86, completion criterion 1).

    The catalogue indoor airflow is 150 to 160 m3/h per kW from 9 kW up, and a
    dry coil carrying the nameplate at that airflow needs about 20 K of
    approach. With the earlier search bound of 20 K a default 15 kW unit
    delivered 89 % of its nameplate at 35/27 degC and 91 % at 7/20 degC.
    """
    model = AirSourceHeatPump(
        hp_capacity=capacity, indoor_unit_type=indoor_unit_type, rated_condition={"mode": rated_mode}
    )
    for sign, outdoor, room in RATING_POINTS.values():
        out = model.analyze_steady(Q_r_iu=sign * capacity, T0=outdoor, T_a_room=room, verbose=False, postprocess=False)
        assert out["converged"] and out["failure_reason"] == "none" and out["capacity_clamped"] is None
        assert out["Q_ref_iu [W]"] == pytest.approx(capacity, rel=1e-6)


def test_the_earlier_search_bound_cut_the_rated_capacity_of_a_large_default_unit():
    """What the 30 K default guards against: at 20 K the bound itself is the limit at the rating point."""
    boxed = AirSourceHeatPump(hp_capacity=15000.0, dT_approach_bounds=(1.0, 20.0))
    out = boxed.analyze_steady(Q_r_iu=15000.0, T0=35.0, T_a_room=27.0, verbose=False, postprocess=False)
    assert out["capacity_clamped"] == "max" and out["capacity_binding"] == "cycle"
    assert out["Q_ref_iu [W]"] == pytest.approx(0.892 * 15000.0, rel=5e-3)


@pytest.mark.parametrize("capacity", [3500.0, 8000.0, 15000.0])
def test_a_default_ground_source_unit_delivers_its_nameplate_in_cooling(capacity):
    model = GroundSourceHeatPump(hp_capacity=capacity)
    out = model.analyze_steady(Q_r_iu=capacity, T0=27.0, T_a_room=27.0, T_bhe_wall=25.0)
    row = out if isinstance(out, dict) else out.iloc[0]
    assert row["failure_reason"] == "none" and row["capacity_clamped"] is None
    assert row["Q_ref_iu [W]"] == pytest.approx(capacity, rel=1e-6)
