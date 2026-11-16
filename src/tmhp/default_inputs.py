"""Catalogue-based default inputs: compressor displacement, maximum fan airflow (issue #86) and heat-exchanger
conductance (issues #115-#118).

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
- **Maximum fan airflow** [m³/s] per mode, side and product class, from the largest published fan step. The
  estimate's ``assumptions`` also state how far makers sit from the fitted law (each manufacturer held out and
  predicted from the others), because at equal capacity real airflows differ by maker; an airflow given for the
  actual unit takes precedence over the default.
- **Heat-exchanger conductance** [W/K] per exchanger kind and refrigerant role, from the leaving-end
  approach temperatures real machines print (:func:`estimate_hx_UA`), sized for the exchanger's own duty
  (:func:`hx_duty_per_nameplate`). An exchanger whose stratum issues no value keeps an earlier rule, recorded
  as provisional (:func:`kept_hx_UA`).

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
    "estimate_hx_UA",
    "estimate_max_airflow",
    "hx_duty_per_nameplate",
    "kept_hx_UA",
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
    #: ``"shipped"``, or ``"provisional"`` when the evidence meets the shipping gate but is weak. A recorded reason
    #: (``"Provisional: ..."``, or ``"Note: ..."`` for a shipped stratum with a caveat) is the last entry of
    #: ``assumptions``; for airflow, the line before it gives the spread between manufacturers.
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
    reason: tuple[str, ...] = ()
    if entry.get("status_reason"):
        reason = (f"{'Note' if status == 'shipped' else 'Provisional'}: {entry['status_reason']}",)
    maker_cv = entry.get("nested_manufacturer_cv", {})
    maker_bias = entry.get("manufacturer_holdout_bias_pct") or {}
    spread: tuple[str, ...] = ()
    if maker_cv.get("MAPE_pct") is not None and maker_bias:
        spread = (
            f"Spread between manufacturers (each held out, predicted from the others): MAPE "
            f"{maker_cv['MAPE_pct']:.1f} %, bias {maker_cv['bias_pct']:+.1f} %, per-manufacturer bias "
            f"{min(maker_bias.values()):+.0f} to {max(maker_bias.values()):+.0f} %. A given unit's airflow can differ "
            f"from this default by about +/-{maker_cv['MAPE_pct']:.0f} %; an airflow given for the unit takes precedence.",
        )
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
            *spread,
            *reason,
        ),
        status=status,
    )


def _hx_stratum(stratum: str) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = _artifact("hx_approach_regression.json")
    entry = artifact["strata"].get(stratum)
    if entry is None:
        raise ValueError(f"No heat-exchanger stratum {stratum!r}; available: {sorted(artifact['strata'])}")
    if not entry["meets_shipping_gate"] or entry["law"] is None:
        raise ValueError(
            f"Heat-exchanger stratum {stratum!r} rests on {entry['n_holdout_groups']} product lines from "
            f"{len(entry['manufacturers'])} manufacturer(s); the shipping gate needs "
            f"{artifact['shipping_gate']['holdout_groups']} and {artifact['shipping_gate']['manufacturers']}"
        )
    return artifact, entry


def hx_duty_per_nameplate(stratum: str, nameplate_mode: str) -> float:
    """Median duty of the exchangers of ``stratum`` per watt of their units' ``nameplate_mode`` nameplate.

    The outdoor coil of a split unit rejects more than its cooling nameplate (the nameplate plus the compressor
    work), and as an evaporator in heating it absorbs less than that nameplate; the ratios are those of the
    archive's rows and move with its releases. A default conductance is sized for that duty, not for the
    nameplate.
    """
    _, entry = _hx_stratum(stratum)
    ratios = entry["duty_per_nameplate_p10_p50_p90"]
    if nameplate_mode not in ratios:
        raise ValueError(
            f"Stratum {stratum!r} has no duty per {nameplate_mode!r} nameplate; available: {sorted(ratios)}"
        )
    return float(ratios[nameplate_mode][1])


def estimate_hx_UA(stratum: str, *, duty: float, heat_capacity_rate: float) -> DefaultInputEstimate:
    """Default conductance [W/K] of a refrigerant heat exchanger from the approach temperatures of real machines.

    The value is the *effective* conductance of the model's exchanger (isothermal refrigerant, single-phase
    fluid, effectiveness ``1 - exp(-UA / C)``) that reproduces, at the stated duty and fluid flow, the
    saturation temperature machines of the stratum print or were measured at. That pressure is read at a
    service or compressor port, so line and coil pressure drop and the desuperheating and subcooling zones are
    inside the value: it is not a geometric ``U A``.

    Parameters
    ----------
    stratum
        Exchanger kind and refrigerant role, for example ``"outdoor_coil|fin_tube|evaporating"`` or
        ``"water_condenser|bphe|condensing"``.
    duty
        Heat duty of the exchanger itself at the rating point [W] (see :func:`hx_duty_per_nameplate`).
    heat_capacity_rate
        Heat capacity rate of the fluid at the reference flow the conductance is stated at [W/K].

    The law of each stratum was selected by holding one product line out and predicting its saturation
    temperature (``validation/default_inputs/hx_approach``); ``assumptions`` states the law, the spread of the
    population and the hold-out error.
    """
    if not (0 < duty < float("inf") and 0 < heat_capacity_rate < float("inf")):
        raise ValueError("duty and heat_capacity_rate must be positive and finite")
    # heat_exchanger imports reference_state, which imports this module.
    from .heat_exchanger import calc_UA_from_inlet_difference, calc_UA_from_leaving_approach

    artifact, entry = _hx_stratum(stratum)
    law, band = entry["law"], entry["band_p10_p50_p90"]
    if law["kind"] == "leaving_approach":
        value = calc_UA_from_leaving_approach(duty, heat_capacity_rate, law["dT_out_K"])
        rule = f"leaving-end approach {law['dT_out_K']:.2f} K: UA = C ln(1 + Q / (C dT_out))"
    elif law["kind"] == "itd":
        value = calc_UA_from_inlet_difference(duty, heat_capacity_rate, law["ITD_K"])
        rule = f"inlet temperature difference {law['ITD_K']:.2f} K: UA = -C ln(1 - Q / (C ITD))"
    elif law["kind"] == "ua_per_duty":
        value = law["k_per_K"] * duty
        rule = f"UA = {law['k_per_K']:.4f} / K x duty"
    else:  # pragma: no cover - guarded by the archive export
        raise ValueError(f"Unknown law kind {law['kind']!r}")
    low, high = entry["duty_range_W"] or (0.0, float("inf"))
    cv, maker = entry["group_holdout"], entry["manufacturer_holdout"]
    spread = (
        f"Population (10th-50th-90th percentile, product lines weighted equally): leaving approach "
        f"{band['leave_K'][0]:.1f}-{band['leave_K'][1]:.1f}-{band['leave_K'][2]:.1f} K, inlet difference "
        f"{band['ITD_K'][0]:.1f}-{band['ITD_K'][1]:.1f}-{band['ITD_K'][2]:.1f} K. A given machine can sit anywhere "
        "in that band; a conductance given for the machine takes precedence."
    )
    holdout = f"Product-line hold-out error of the predicted saturation temperature: RMSE {cv['RMSE_K']:.1f} K"
    if maker.get("valid"):
        holdout += f"; manufacturer hold-out RMSE {maker['RMSE_K']:.1f} K"
    return DefaultInputEstimate(
        value=float(value),
        unit="W/K",
        quantity="heat-exchanger conductance",
        version=artifact["version"],
        key=stratum,
        capacity_range_W=(float(low), float(high)),
        extrapolated=not low <= duty <= high,
        source_ids=tuple(entry["source_ids"]),
        assumptions=(
            f"{rule}; n = {entry['n']} operating points of {entry['n_holdout_groups']} product lines from "
            f"{', '.join(entry['manufacturers'])}.",
            "Effective conductance: reproduces the saturation temperature at the printed (service or compressor "
            "port) pressure, so refrigerant pressure drop and the desuperheating and subcooling zones are inside it.",
            spread,
            holdout + ".",
        ),
    )


def kept_hx_UA(stratum: str, value: float, *, duty: float, rule: str, reason: str) -> DefaultInputEstimate:
    """A conductance default [W/K] kept at an earlier rule because ``stratum`` issues no value.

    For an exchanger whose stratum is archived but does not meet the shipping gate (or whose evidence
    disagrees with itself), the model keeps its earlier sizing rule instead of an estimate. The record
    says so: ``status`` is ``"provisional"``, ``rule`` (the rule the value follows) is the first entry of
    ``assumptions`` and ``"Provisional: " + reason`` the last. ``duty`` [W] is the exchanger's duty at the
    rating point, compared with the duty range the archive covers.
    """
    if not (0 < value < float("inf") and 0 < duty < float("inf")):
        raise ValueError("value and duty must be positive and finite")
    artifact = _artifact("hx_approach_regression.json")
    entry = artifact["strata"].get(stratum)
    if entry is None:
        raise ValueError(f"No heat-exchanger stratum {stratum!r}; available: {sorted(artifact['strata'])}")
    low, high = entry["duty_range_W"] or (0.0, float("inf"))
    return DefaultInputEstimate(
        value=float(value),
        unit="W/K",
        quantity="heat-exchanger conductance",
        version=artifact["version"],
        key=stratum,
        capacity_range_W=(float(low), float(high)),
        extrapolated=not low <= duty <= high,
        source_ids=tuple(entry["source_ids"]),
        assumptions=(rule, f"Provisional: {reason}"),
        status="provisional",
    )
