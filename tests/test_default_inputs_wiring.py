"""Constructor defaults taken from the catalogue decision module (issue #86).

A machine given only a capacity and a refrigerant gets the compressor
displacement :mod:`tmhp.default_inputs` derives from compressor catalogues, and
says so in ``default_input_estimates``. An input the caller supplies is used as
given and has no entry there.
"""

import pytest

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler, GroundSourceHeatPump
from tmhp.compressor_efficiency import RPS_FIT_MAX, RPS_FIT_MIN
from tmhp.default_inputs import DEFAULT_DISPLACEMENT_BASIS, estimate_displacement

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
