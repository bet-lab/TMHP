"""Catalogue-based default inputs: compressor displacement and maximum fan airflow (issue #86).

When a user gives only the rated capacity ``hp_capacity`` (``Q_nom``) and the refrigerant, these functions
return the inputs a manufacturer's catalogue would have given, with their evidence:

- **Compressor displacement** ``V_cmp_ref`` [m³/rev]. Catalogue rows of every refrigerant and rating point are
  pooled per compressor basis by dividing out the fluid property ``rho_1 * dh`` (CoolProp). What remains is
  the effective speed ``eta_vol * rps``, one value per basis, so

  .. math::

      V_{\\mathrm{cmp,ref}} = \\frac{Q_{\\mathrm{nom}}}{\\rho_1\\,\\Delta h\\;\\eta_{\\mathrm{vol}}\\,\\mathrm{rps}}

  is :func:`tmhp.compressor_speed.refrigerant_aware_displacement` evaluated at the catalogue rating point of
  the basis with the catalogue ``eta_vol * rps`` (option A, 2026-11-14). The product's nameplate capacity
  is used as the compressor's catalogue capacity at that rating point.
- **Maximum fan airflow** [m³/s] per mode, side and product class, from the largest published fan step.

Values come from ``tmhp/data/*.json``, exported from the archives in ``validation/default_inputs`` (see the
README there for collection and filtering rules). A basis or stratum that does not meet the shipping gate
(3 product families, 2 manufacturers) is refused with its reason rather than silently replaced.

Nothing here changes a constructor default; the models call these functions only where they are wired in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from .compressor_speed import RatedPoint, refrigerant_aware_displacement

__all__ = [
    "DEFAULT_DISPLACEMENT_BASIS",
    "DefaultInputEstimate",
    "estimate_displacement",
    "estimate_max_airflow",
]

#: TMHP heat pumps are inverter driven; their default compressor is the 60 rps inverter twin rotary basis.
DEFAULT_DISPLACEMENT_BASIS = "rotary_twin_inverter_60rps"
_SIDES = ("indoor", "outdoor")
_MODES = ("cooling", "heating")


@dataclass(frozen=True)
class DefaultInputEstimate:
    """A default input with the evidence it rests on."""

    value: float
    unit: str
    quantity: str
    version: str
    key: str
    capacity_range_W: tuple[float, float]
    extrapolated: bool
    source_ids: tuple[str, ...]
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    #: ``"shipped"``, or ``"provisional"`` when the evidence meets the shipping gate but is weak; the reason is
    #: the last entry of ``assumptions``.
    status: str = "shipped"


@cache
def _artifact(name: str) -> dict[str, Any]:
    artifact: dict[str, Any] = json.loads((resources.files("tmhp") / "data" / name).read_text())
    return artifact


def _check_capacity(hp_capacity: float) -> float:
    value = float(hp_capacity)
    if not value > 0 or value != value or value == float("inf"):
        raise ValueError(f"hp_capacity must be positive and finite, got {hp_capacity!r}")
    return value


def estimate_displacement(
    hp_capacity: float, refrigerant: str, basis: str = DEFAULT_DISPLACEMENT_BASIS
) -> DefaultInputEstimate:
    """Default ``V_cmp_ref`` [m³/rev] = ``Q_nom / (rho_1 * dh * eta_vol * rps)`` from compressor catalogues.

    Parameters
    ----------
    hp_capacity
        Nameplate capacity ``Q_nom`` [W], used as the compressor's catalogue cooling capacity at the
        basis's rating point.
    refrigerant
        CoolProp fluid name; any fluid CoolProp evaluates is accepted, because the refrigerant enters only
        through ``rho_1 * dh``.
    basis
        Compressor basis (type and speed basis) whose catalogue ``eta_vol * rps`` is used.
    """
    capacity = _check_capacity(hp_capacity)
    artifact = _artifact("compressor_displacement_regression.json")
    entry = artifact["bases"].get(basis)
    if entry is None:
        raise ValueError(f"Unknown displacement basis {basis!r}; available: {sorted(artifact['bases'])}")
    if not entry["meets_shipping_gate"]:
        raise ValueError(
            f"Displacement basis {basis!r} rests on {entry['n_families']} families from "
            f"{len(entry['manufacturers'])} manufacturer(s); the shipping gate needs "
            f"{artifact['shipping_gate']['families']} and {artifact['shipping_gate']['manufacturers']}"
        )
    condition = entry["reference_condition"]
    rated_point = RatedPoint(
        T_evap_C=condition["T_evap_C"],
        T_cond_C=condition["T_cond_C"],
        rps=entry["frequency_Hz"],
        duty="cooling",
        basis=f"compressor catalogue rating point {entry['reference_protocol']} ({artifact['version']})",
    )
    value = refrigerant_aware_displacement(
        capacity,
        refrigerant,
        rated_point,
        dT_superheat=condition["superheat_K"],
        dT_subcool=condition["subcooling_K"],
        eta_vol=entry["eta_vol_rps_per_f"],
    )
    low, high = entry["capacity_range_W"]
    cv = entry["nested_refrigerant_cv"]
    return DefaultInputEstimate(
        value=value,
        unit="m3/rev",
        quantity="V_cmp_ref",
        version=artifact["version"],
        key=basis,
        capacity_range_W=(low, high),
        extrapolated=not low <= capacity <= high,
        source_ids=tuple(entry["source_ids"]),
        assumptions=(
            f"V_cmp_ref = Q_nom / (rho_1 * dh * eta_vol * rps); eta_vol * rps = {entry['eta_vol_rps']:.2f} rev/s "
            f"from {entry['n']} catalogue rows of {', '.join(entry['manufacturers'])}.",
            f"rho_1 * dh at {entry['reference_protocol']}: Te {condition['T_evap_C']:g} degC, Tc "
            f"{condition['T_cond_C']:g} degC, suction {condition['T_suction_C']:g} degC, liquid "
            f"{condition['T_liquid_C']:g} degC (option A).",
            "Nameplate capacity stands in for the compressor's catalogue capacity at that rating point.",
            f"Refrigerant hold-out error of this basis: MAPE {cv.get('MAPE_pct', float('nan')):.1f} %.",
        ),
    )


def estimate_max_airflow(hp_capacity: float, *, side: str, product: str, mode: str = "cooling") -> DefaultInputEstimate:
    """Default maximum fan airflow [m³/s] for a product class, side and mode from catalogue fits.

    Parameters
    ----------
    hp_capacity
        Rated capacity [W] in ``mode``.
    side
        ``"indoor"`` or ``"outdoor"``.
    product
        Indoor: ``"wall_mounted"``, ``"floor_standing"`` or ``"water_to_air"``. Outdoor: ``"single_split"``
        or ``"air_to_water"``.
    mode
        ``"cooling"`` or ``"heating"``: the duty the capacity and airflow were published for.
    """
    capacity = _check_capacity(hp_capacity)
    if side not in _SIDES or mode not in _MODES:
        raise ValueError(f"side must be one of {_SIDES} and mode one of {_MODES}")
    artifact = _artifact("fan_airflow_regression.json")
    key = f"airflow|{mode}|{side}|{product}"
    entry = artifact["strata"].get(key)
    if entry is None:
        raise ValueError(f"No catalogue airflow stratum {key!r}; available: {sorted(artifact['strata'])}")
    if not entry["meets_shipping_gate"]:
        raise ValueError(
            f"Airflow stratum {key!r} rests on {entry['n_families']} families from "
            f"{len(entry['manufacturers'])} manufacturer(s); the shipping gate needs "
            f"{artifact['shipping_gate']['families']} and {artifact['shipping_gate']['manufacturers']}"
        )
    law = entry["selected_law"]
    x = capacity / 1000.0
    if law["kind"] == "proportional":
        value = law["a"] * x
    elif law["kind"] == "linear":
        value = law["a"] * x + law["b"]
    elif law["kind"] == "power":
        value = law["a"] * x ** law["b"]
    else:  # pragma: no cover - guarded by the archive build
        raise ValueError(f"Unknown law kind {law['kind']!r}")
    if not value > 0:
        raise ValueError(f"Airflow law {key!r} gives a non-positive value at {capacity:g} W")
    low, high = entry["capacity_range_W"]
    cv = entry["nested_family_cv"]
    status = entry.get("status", "shipped")
    reason = (f"Provisional: {entry['status_reason']}",) if status != "shipped" and entry.get("status_reason") else ()
    return DefaultInputEstimate(
        value=float(value),
        unit="m3/s",
        quantity="max fan airflow",
        version=artifact["version"],
        key=key,
        capacity_range_W=(low, high),
        extrapolated=not low <= capacity <= high,
        source_ids=tuple(entry["source_ids"]),
        assumptions=(
            f"{law} with x = capacity in kW; n = {entry['n']} from {', '.join(entry['manufacturers'])}.",
            f"Family hold-out error: MAPE {cv.get('MAPE_pct', float('nan')):.1f} %.",
            *reason,
        ),
        status=status,
    )
