import math

import pytest

from tmhp.pump import calc_aux_loss_from_ashrae_grade, calc_parallel_borefield_pressure_drop

PARAMS = dict(volume_flow_ref=36 / 60000, n_boreholes=2, H_b=100, pipe_inner_diameter=0.026)


def test_reference_resistance_and_runtime_pressure():
    result = calc_aux_loss_from_ashrae_grade("A", **PARAMS)
    assert result["pressure_bhe_Pa"] == pytest.approx(34271.8800839)
    assert result["K_aux"] == pytest.approx(165.5736257)
    assert result["K_aux_branch"] == pytest.approx(662.2945029)
    for flow in (18, 36, 54):
        volume = flow / 60000
        actual = calc_parallel_borefield_pressure_drop(
            volume * 1000,
            2,
            100,
            0.026,
            1000,
            0.001,
            volume_flow_ref=PARAMS["volume_flow_ref"],
            dp_aux_ref=result["dp_aux_ref"],
        )
        bhe = calc_parallel_borefield_pressure_drop(volume * 1000, 2, 100, 0.026, 1000, 0.001)
        dynamic = 1000 * (volume / (math.pi * 0.026**2 / 4)) ** 2 / 2
        assert actual == pytest.approx(bhe + result["K_aux"] * dynamic)
        if flow == 36:
            assert actual == pytest.approx(140000)


@pytest.mark.parametrize(("grade", "pressure"), [("a", 140000), ("B", 210000), ("C", 280000), ("D", 420000)])
def test_single_source_budgets(grade, pressure):
    assert calc_aux_loss_from_ashrae_grade(grade, **PARAMS)["target_pressure_Pa"] == pressure


def test_diameter_conversion_and_user_reference():
    result = calc_aux_loss_from_ashrae_grade(
        "A", **(PARAMS | {"volume_flow_ref": 24 / 60000}), auxiliary_pipe_inner_diameter=0.04
    )
    assert result["volume_flow_ref"] == 24 / 60000
    assert result["K_aux_branch"] == pytest.approx(result["K_aux"] * 4 * (0.026 / 0.04) ** 4)
    assert result["dp_aux_ref"] != calc_aux_loss_from_ashrae_grade("A", **PARAMS)["dp_aux_ref"]


@pytest.mark.parametrize("grade", ["F", "E", "", None])
def test_no_undefined_pressure_budget(grade):
    with pytest.raises(ValueError, match="finite upper"):
        calc_aux_loss_from_ashrae_grade(grade, **PARAMS)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_reference_flow(value):
    with pytest.raises(ValueError, match="volume_flow_ref"):
        calc_aux_loss_from_ashrae_grade("A", **(PARAMS | {"volume_flow_ref": value}))


def test_negative_residual_is_not_clipped():
    with pytest.raises(ValueError, match="exceeds"):
        calc_aux_loss_from_ashrae_grade("A", **(PARAMS | {"H_b": 1000}))
