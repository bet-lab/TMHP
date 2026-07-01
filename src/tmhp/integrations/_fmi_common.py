"""Shared helpers for FMI adapter boundary hygiene."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any
from xml.etree.ElementTree import Element, SubElement

UnitSpec = tuple[str, Mapping[str, str]]


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
