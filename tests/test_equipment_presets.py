"""Equipment parameter preset tests."""

from __future__ import annotations

import pytest

from tmhp.equipment_presets import resolve_preset, validated_rule_set


def test_validated_rule_set_values() -> None:
    """The paper benchmark preset exposes every validated rule-set value."""
    kwargs = validated_rule_set(
        "R32",
        9000.0,
        V_cmp_disp_cc=42.0,
        dV_fan_a_rated=1.153,
    )

    assert set(kwargs) == {
        "V_cmp_ref",
        "UA_tank",
        "UA_ou_rated",
        "dV_fan_a_rated",
        "eta_cmp_vol",
        "eta_cmp_isen",
        "eta_cmp",
        "dT_superheat",
        "dT_subcool",
        "n_ou",
        "A_cross",
        "dP_fan_rated",
        "eta_fan_rated",
        "PR_cycle_max",
    }
    assert kwargs["V_cmp_ref"] == pytest.approx(4.2e-5)
    assert kwargs["UA_tank"] == pytest.approx(1800.0)
    assert kwargs["UA_ou_rated"] == pytest.approx(1260.0)
    assert kwargs["dV_fan_a_rated"] == pytest.approx(1.153)
    assert kwargs["eta_cmp_vol"](1.0) == pytest.approx(1.0)
    assert kwargs["eta_cmp_vol"](5.0) == pytest.approx(0.92)
    assert kwargs["eta_cmp_isen"](1.0) == pytest.approx(0.88)
    assert kwargs["eta_cmp_isen"](5.0) == pytest.approx(0.80)
    assert kwargs["eta_cmp"](4.0, 55.0) == pytest.approx(0.80)
    assert kwargs["eta_cmp"](4.0, 65.0) == pytest.approx(0.797)
    assert kwargs["dT_superheat"] == 5.0
    assert kwargs["dT_subcool"] == 5.0
    assert kwargs["n_ou"] == 0.65
    assert kwargs["A_cross"] == 1.15
    assert kwargs["dP_fan_rated"] == 60.0
    assert kwargs["eta_fan_rated"] == 0.6
    assert kwargs["PR_cycle_max"] == 20.0


def test_resolve_preset_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown equipment preset 'missing'.*validated_rule_set"):
        resolve_preset("missing")
