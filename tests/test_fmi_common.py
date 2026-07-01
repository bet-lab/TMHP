"""Unit tests for FMI adapter boundary helpers."""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

import pytest

from tmhp.integrations._fmi_common import (
    apply_fmi2_real_units,
    ensure_unit_definitions,
    failure_reason,
    finite,
    is_finite,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 0.0),
        (float("nan"), 0.0),
        (float("inf"), 0.0),
        (float("-inf"), 0.0),
        (2.5, 2.5),
    ],
)
def test_fmi_numeric_boundary_outputs_are_finite(raw: float | None, expected: float) -> None:
    assert finite(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "none"),
        ("cycle_invalid", "cycle_invalid"),
        (42, "42"),
    ],
)
def test_fmi_failure_reason_is_normalized(raw: object, expected: str) -> None:
    assert failure_reason(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1.0, True),
        ("2.5", True),
        (float("nan"), False),
        (float("inf"), False),
        ("not-a-number", False),
        (None, False),
    ],
)
def test_fmi_importer_scalar_validation(raw: object, expected: bool) -> None:
    assert is_finite(raw) is expected


def test_ensure_unit_definitions_inserts_once_after_interface_element() -> None:
    root = Element("fmiModelDescription")
    SubElement(root, "CoSimulation")
    SubElement(root, "ModelVariables")

    ensure_unit_definitions(
        root,
        (("W", {"kg": "1", "m": "2", "s": "-3"}),),
        insertion_after={"CoSimulation"},
    )
    ensure_unit_definitions(
        root,
        (("degC", {"K": "1", "offset": "273.15"}),),
        insertion_after={"CoSimulation"},
    )

    assert [child.tag for child in root] == ["CoSimulation", "UnitDefinitions", "ModelVariables"]
    units = root.findall("./UnitDefinitions/Unit")
    assert len(units) == 1
    assert units[0].attrib["name"] == "W"
    assert units[0].find("BaseUnit").attrib == {"kg": "1", "m": "2", "s": "-3"}


def test_apply_fmi2_real_units_updates_known_real_variables_only() -> None:
    root = Element("fmiModelDescription")
    variables = SubElement(root, "ModelVariables")
    known = SubElement(variables, "ScalarVariable", attrib={"name": "T0"})
    known_real = SubElement(known, "Real")
    unknown = SubElement(variables, "ScalarVariable", attrib={"name": "not_unitized"})
    unknown_real = SubElement(unknown, "Real")
    boolean = SubElement(variables, "ScalarVariable", attrib={"name": "converged"})
    SubElement(boolean, "Boolean")

    apply_fmi2_real_units(root, {"T0": "degC", "converged": "1"})

    assert known_real.attrib["unit"] == "degC"
    assert "unit" not in unknown_real.attrib
