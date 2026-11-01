"""ASHP research defaults reproduce their archive and retain explicit caller overrides."""

import json
from pathlib import Path
from random import Random

import numpy as np
import pytest

from tmhp import AirSourceHeatPump
from tmhp.compressor_baseline import BASELINE_VERSION, OssorioR290Map, ossorio_r290_efficiencies

ARCHIVE = Path(__file__).parents[1] / "validation" / "compressor_fitting" / "baseline_surrogate.json"


@pytest.mark.parametrize("quantity", ["eta_v", "eta_ov", "f_loss"])
def test_callable_coefficients_reproduce_the_generated_surrogate_archive(quantity):
    """Independent evaluations recover all six archived coefficients in their physical units."""
    archive = json.loads(ARCHIVE.read_text())
    assert archive["version"] == BASELINE_VERSION
    assert archive["terms"] == ["1", "PR", "N", "PR^2", "N^2", "PR*N"]
    assert archive["speed_unit"] == "rev/s"
    coordinates = [(2, 30), (4, 30), (6, 30), (2, 70), (2, 110), (6, 110)]
    design = np.array([[1, pr, n, pr**2, n**2, pr * n] for pr, n in coordinates], dtype=float)
    values = np.array([OssorioR290Map(quantity)(pr, n) for pr, n in coordinates])
    recovered_coefficients = np.linalg.solve(design, values)
    assert recovered_coefficients == pytest.approx(archive["fits"][quantity]["coefficients"], abs=2e-14)


def test_transformed_efficiencies_preserve_compressor_energy_balance_across_the_domain():
    """Overall efficiency and loss reconstruct the same electrical and refrigerant powers."""
    maps = ossorio_r290_efficiencies()
    overall, loss = OssorioR290Map("eta_ov"), OssorioR290Map("f_loss")
    rng = Random(122666)
    for _ in range(100):
        pr, speed = rng.uniform(1.5, 7.49), rng.uniform(30, 110)
        eta_is = maps["eta_cmp_isen"](pr, speed)
        eta_em = maps["eta_cmp"](pr, speed)
        eta_ov, f_loss = overall(pr, speed), loss(pr, speed)
        assert eta_is * eta_em == pytest.approx(eta_ov)
        assert eta_em + f_loss == pytest.approx(1.0)
        mass_flow = rng.uniform(0.005, 0.1)
        isentropic_rise = rng.uniform(10000, 100000)
        electrical_power = mass_flow * isentropic_rise / eta_ov
        refrigerant_power = mass_flow * isentropic_rise / eta_is
        assert refrigerant_power == pytest.approx((1 - f_loss) * electrical_power)


def test_ashp_uses_the_research_baseline_without_user_map_arguments():
    """Omitting efficiency inputs selects the same maps as explicit Ossorio overrides."""
    implicit = AirSourceHeatPump(ref="R32")
    explicit = AirSourceHeatPump(ref="R32", **ossorio_r290_efficiencies())
    for field in ("eta_cmp_vol", "eta_cmp_isen", "eta_cmp"):
        assert isinstance(getattr(implicit, field), OssorioR290Map)
        for pressure_ratio, speed in ((2, 30), (3, 50), (6, 100)):
            assert getattr(implicit, field)(pressure_ratio, speed) == pytest.approx(
                getattr(explicit, field)(pressure_ratio, speed)
            )
    assert implicit.rps_rated == pytest.approx(explicit.rps_rated)
    assert implicit.m_dot_ref_rated == pytest.approx(explicit.m_dot_ref_rated)


@pytest.mark.parametrize("field", ["eta_cmp_vol", "eta_cmp_isen", "eta_cmp"])
@pytest.mark.parametrize("kind", ["scalar", "callable"])
def test_explicit_efficiency_overrides_replace_only_the_requested_default(field, kind):
    """A supplied scalar or callable remains authoritative while omitted maps use Ossorio."""

    def custom(pressure_ratio, speed):
        """Return a stable explicit user efficiency for override-preservation checks."""
        return 0.85

    override = 0.85 if kind == "scalar" else custom
    model = AirSourceHeatPump(ref="R32", **{field: override})
    assert getattr(model, field) is override
    for other_field in {"eta_cmp_vol", "eta_cmp_isen", "eta_cmp"} - {field}:
        assert isinstance(getattr(model, other_field), OssorioR290Map)


def test_deprecated_mechanical_override_remains_a_fallback_below_explicit_eta_cmp():
    """Legacy inputs remain usable and cannot override a modern explicit efficiency."""
    fallback = AirSourceHeatPump(ref="R32", eta_cmp_mech=0.87)
    explicit = AirSourceHeatPump(ref="R32", eta_cmp_mech=0.87, eta_cmp=0.91)
    assert fallback.eta_cmp == 0.87
    assert explicit.eta_cmp == 0.91


@pytest.mark.parametrize("quantity", ["", "eta_isentropic", "power", "eta_v "])
def test_unknown_quantity_is_rejected_before_map_evaluation(quantity):
    """Invalid map names cannot silently select a different physical quantity."""
    with pytest.raises(ValueError, match="Unknown Ossorio map quantity"):
        OssorioR290Map(quantity)
