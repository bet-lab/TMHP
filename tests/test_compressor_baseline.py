"""Research maps reproduce their archived surrogate without replacing production defaults."""

import json
from pathlib import Path
from random import Random

import numpy as np
import pytest

from tmhp import AirSourceHeatPump
from tmhp.compressor_baseline import BASELINE_VERSION, OssorioR290Map, ossorio_r290_efficiencies
from tmhp.compressor_efficiency import make_eta_em, make_eta_isen, make_eta_vol

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


def test_research_baseline_requires_explicit_overrides_and_leaves_bitzer_defaults_unchanged():
    """Constructing an Ossorio model has no side effect on existing or later default models."""
    before = AirSourceHeatPump(ref="R32")
    research = AirSourceHeatPump(ref="R32", **ossorio_r290_efficiencies())
    after = AirSourceHeatPump(ref="R32")
    for field, factory in (
        ("eta_cmp_vol", make_eta_vol),
        ("eta_cmp_isen", make_eta_isen),
        ("eta_cmp", make_eta_em),
    ):
        expected = factory(50)(3, 50)
        assert getattr(before, field)(3, 50) == pytest.approx(expected)
        assert getattr(after, field)(3, 50) == pytest.approx(expected)
        assert isinstance(getattr(research, field), OssorioR290Map)
        assert getattr(research, field)(3, 50) != pytest.approx(expected)


@pytest.mark.parametrize("quantity", ["", "eta_isentropic", "power", "eta_v "])
def test_unknown_quantity_is_rejected_before_map_evaluation(quantity):
    """Invalid map names cannot silently select a different physical quantity."""
    with pytest.raises(ValueError, match="Unknown Ossorio map quantity"):
        OssorioR290Map(quantity)
