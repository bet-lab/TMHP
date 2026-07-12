"""Shared helpers for FMI adapter boundary hygiene."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any
from xml.etree.ElementTree import Element, SubElement

UnitSpec = tuple[str, Mapping[str, str]]

VARIABLE_DESCRIPTIONS: Mapping[str, str] = {
    "ref": "Working fluid (CoolProp name, e.g. R32, R290, R410A)",
    "hp_capacity": "Nominal heat pump heating capacity",
    "T_tank_w_init": "Initial tank water temperature",
    "T_sur": "Surrounding temperature for tank heat loss",
    "T0": "Outdoor air temperature",
    "dhw_draw": "DHW draw-off volumetric flow rate",
    "T_sup_w": "Mains supply water temperature",
    "E_cmp": "Compressor electric power",
    "E_tot": "Total system electric power (compressor and fan)",
    "Q_ref_tank": "Condenser heat transfer rate to the tank",
    "cop_sys": "System coefficient of performance (Q_ref_tank / E_tot)",
    "T_tank_w": "Tank water temperature after the step",
    "hp_is_on": "Whether the heat pump is active for this step",
    "converged": "Whether the cycle solve accepted the step",
    "failure_reason": 'Step-level failure reason, or "none"',
    "preset": "Named ASHPB equipment parameter preset; empty uses model defaults",
    "V_cmp_disp_cc": "Compressor displacement used by the selected preset",
    "dV_fan_a_rated": "Rated outdoor-unit fan volumetric flow used by the selected preset",
}


def preset_kwargs(
    preset: str,
    *,
    ref: str,
    hp_capacity: float,
    V_cmp_disp_cc: float,
    dV_fan_a_rated: float,
) -> dict[str, Any]:
    """Build optional preset kwargs shared by both FMI adapter versions."""
    if not preset:
        return {}
    if not is_finite(V_cmp_disp_cc) or float(V_cmp_disp_cc) <= 0.0:
        raise ValueError("V_cmp_disp_cc must be finite and > 0 when preset is specified")
    if not is_finite(dV_fan_a_rated) or float(dV_fan_a_rated) <= 0.0:
        raise ValueError("dV_fan_a_rated must be finite and > 0 when preset is specified")

    from tmhp.equipment_presets import resolve_preset

    return resolve_preset(preset)(
        ref,
        hp_capacity,
        V_cmp_disp_cc=float(V_cmp_disp_cc),
        dV_fan_a_rated=float(dV_fan_a_rated),
    )


def finite(value: float | None) -> float:
    """Return a finite scalar for FMI numeric output variables."""
    if value is None:
        return 0.0
    out = float(value)
    return out if math.isfinite(out) else 0.0


def is_finite(value: Any) -> bool:
    """Return whether *value* can be represented as a finite FMI scalar."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(out)


def failure_reason(value: object) -> str:
    """Normalize diagnostic reason values at FMI string boundaries."""
    if value is None:
        return "none"
    return str(value)


def ensure_unit_definitions(
    root: Element,
    unit_specs: Iterable[UnitSpec],
    *,
    insertion_after: set[str],
) -> None:
    """Insert FMI ``UnitDefinitions`` once, after the model interface element."""
    if root.find("UnitDefinitions") is not None:
        return

    unit_definitions = Element("UnitDefinitions")
    for name, base_attrs in unit_specs:
        unit = SubElement(unit_definitions, "Unit", attrib={"name": name})
        SubElement(unit, "BaseUnit", attrib=dict(base_attrs))

    insertion_index = 0
    for index, child in enumerate(list(root)):
        if child.tag in insertion_after:
            insertion_index = index + 1
    root.insert(insertion_index, unit_definitions)


def apply_fmi2_real_units(root: Element, real_units: Mapping[str, str]) -> None:
    """Attach unit metadata to FMI 2.0 ``ScalarVariable/Real`` elements."""
    model_variables = root.find("ModelVariables")
    if model_variables is None:
        return

    for scalar in model_variables.findall("ScalarVariable"):
        unit = real_units.get(scalar.attrib.get("name", ""))
        real = scalar.find("Real")
        if unit is not None and real is not None:
            real.set("unit", unit)
