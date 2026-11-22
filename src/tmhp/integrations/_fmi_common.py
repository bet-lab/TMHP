"""Shared helpers for FMI adapter boundary hygiene."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any
from xml.etree.ElementTree import Element, SubElement

UnitSpec = tuple[str, Mapping[str, str]]

# FMI inputs have a start value. A value outside the physical fraction range
# lets an unconnected input follow the model default without pinning it here.
RH_OUT_USE_MODEL_DEFAULT = -1.0
# The EPW data dictionary accepts relative humidity up to 110 %, and EnergyPlus
# passes such readings on unclipped. A fraction from 1 to 1.1 is saturated air
# and is used as 1, as the EnergyPlus plugin does.
RH_OUT_MAX_ACCEPTED = 1.1

VARIABLE_DESCRIPTIONS: Mapping[str, str] = {
    "ref": "Working fluid (CoolProp name, e.g. R32, R290, R410A)",
    "hp_capacity": "Nominal heat pump heating capacity",
    "T_tank_w_init": "Initial tank water temperature",
    "T_sur": "Surrounding temperature for tank heat loss",
    "T0": "Outdoor air temperature",
    "RH_out": "Outdoor relative humidity as a fraction (0 to 1; up to 1.1 is used as 1); -1 uses the model default",
    "dhw_draw": "DHW draw-off volumetric flow rate",
    "T_sup_w": "Mains supply water temperature",
    "E_cmp": "Compressor electric power averaged over the communication step",
    "E_tot": "Total system electric power (compressor and fan) averaged over the communication step",
    "Q_ref_tank": "Condenser heat transfer rate to the tank averaged over the communication step",
    "cop_sys": "System coefficient of performance of the running cycle (Q_ref_tank / E_tot)",
    "T_tank_w": "Tank water temperature after the step",
    "hp_is_on": "Whether the compressor ran at any time during the communication step",
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


def valid_outdoor_humidity(value: Any) -> bool:
    """Accept a relative humidity fraction or the unconnected FMI start value."""
    if not is_finite(value):
        return False
    rh = float(value)
    return rh == RH_OUT_USE_MODEL_DEFAULT or 0.0 <= rh <= RH_OUT_MAX_ACCEPTED


def outdoor_humidity_input(value: Any) -> float | None:
    """The ``RH_out`` fraction for ``step()``, or ``None`` for the model default.

    Call only after :func:`valid_outdoor_humidity` accepted *value*.
    """
    rh = float(value)
    if rh == RH_OUT_USE_MODEL_DEFAULT:
        return None
    return min(rh, 1.0)


def invalid_step_arguments(current_time: Any, step_size: Any) -> str | None:
    """Describe an illegal ``doStep`` argument, or return ``None`` if both are legal.

    FMI requires a positive communication step size; a non-finite communication
    point is equally unusable. FMI 3.0.2 sec. 2.2.4 makes ``fmi3Error`` the
    answer to illegal arguments.
    """
    if not is_finite(current_time):
        return f"currentCommunicationPoint must be finite but was {current_time!r}"
    if not is_finite(step_size) or float(step_size) <= 0.0:
        return f"communicationStepSize must be finite and positive but was {step_size!r}"
    return None


def invalid_step_inputs(*, T0: Any, RH_out: Any, dhw_draw: Any, T_sup_w: Any, T_sur: Any) -> str | None:
    """Describe the input or parameter values a step cannot use, or return ``None``."""
    problems = [
        f"{name} must be finite but was {value!r}"
        for name, value in (("T0", T0), ("T_sup_w", T_sup_w), ("T_sur", T_sur))
        if not is_finite(value)
    ]
    if not is_finite(dhw_draw) or float(dhw_draw) < 0.0:
        problems.append(f"dhw_draw must be finite and non-negative but was {dhw_draw!r}")
    if not valid_outdoor_humidity(RH_out):
        problems.append(
            f"RH_out must be a fraction from 0 to {RH_OUT_MAX_ACCEPTED:g}, or the start value "
            f"{RH_OUT_USE_MODEL_DEFAULT:g}, but was {RH_out!r}"
        )
    return "; ".join(problems) or None


def rejected_step_message(current_time: Any, problem: str) -> str:
    """Log text for a refused ``doStep``: what was wrong and that nothing changed."""
    return f"doStep at t = {current_time!r} s rejected: {problem}. The FMU state and outputs are unchanged."


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
