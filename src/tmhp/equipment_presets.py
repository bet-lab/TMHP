"""Named parameter presets for equipment models exposed by BES adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

EquipmentPreset = Callable[..., dict[str, Any]]


def validated_rule_set(
    ref: str,
    hp_capacity: float,
    *,
    V_cmp_disp_cc: float,
    dV_fan_a_rated: float,
) -> dict[str, Any]:
    """Return the ASHPB rule set used by the paper validation benchmark.

    This is the Panasonic Aquarea nine-unit validation rule set.  The
    efficiency coefficients are literature-based representative values shared
    by all nine units; capacity, compressor displacement, and rated fan flow
    carry the unit-specific scaling.

    ``ref`` is accepted as part of the common preset contract.  The working
    fluid itself remains a top-level model constructor argument.
    """
    del ref
    UA_tank = hp_capacity / 5.0
    return {
        "V_cmp_ref": V_cmp_disp_cc * 1.0e-6,
        "UA_tank": UA_tank,
        "UA_ou_rated": 0.7 * UA_tank,
        "dV_fan_a_rated": dV_fan_a_rated,
        "eta_cmp_vol": lambda pi: 1.0 - 0.020 * (pi - 1.0),
        "eta_cmp_isen": lambda r: 0.90 - 0.02 * r,
        "eta_cmp": lambda r, rps: 0.80 - 3.0e-5 * (rps - 55.0) ** 2,
        "dT_superheat": 5.0,
        "dT_subcool": 5.0,
        "n_ou": 0.65,
        "A_cross": 1.15,
        "dP_fan_rated": 60.0,
        "eta_fan_rated": 0.6,
        "PR_cycle_max": 20.0,
    }


PRESETS: dict[str, EquipmentPreset] = {"validated_rule_set": validated_rule_set}


def resolve_preset(name: str) -> EquipmentPreset:
    """Resolve a named equipment preset or raise a descriptive error."""
    try:
        return PRESETS[name]
    except KeyError as exc:
        available = ", ".join(sorted(PRESETS))
        raise ValueError(f"Unknown equipment preset {name!r}; available presets: {available}") from exc
