"""Build the heat-exchanger approach archive: observations -> measurements -> transfer laws -> release record.

One observation row is one refrigerant heat exchanger of a real machine at a declared operating point:
entering fluid temperature, fluid flow, heat duty and the refrigerant saturation temperature (from the printed
pressure where its basis is stated). From those the build computes, with no free parameter,

    ITD   = |T_fluid_in - T_sat|          dT    = Q / C            eps = dT / ITD
    leave = ITD - dT   (leaving-end approach)                      UA  = -C ln(1 - eps)

for the model's exchanger (isothermal refrigerant, single-phase fluid, eps = 1 - exp(-UA/C)). ``T_sat`` is the
saturation temperature at the pressure the document prints (service port, compressor port), so the conductance
is the *effective* one that reproduces the pressure the compressor sees: line and coil pressure drop and the
desuperheating and subcooling zones are inside it.

What transfers to a machine of another size is decided by the data (issues #115-#118, owner decision
2026-11-16): per stratum, each candidate law is fitted with one product line held out and scored by the error
of the predicted saturation temperature of the held-out units at their own flow and duty; the simplest law
within 10 % of the lowest RMSE is selected.

Hand-edited inputs: sources.json, observations/<source_id>.csv, errata.csv, holdout_groups.json.

  uv run python -m validation.default_inputs.hx_approach.build
  uv run python -m validation.default_inputs.hx_approach.build --release VERSION --note "what changed"
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
from collections import Counter, defaultdict
from functools import cache
from pathlib import Path

import CoolProp.CoolProp as CP
import numpy as np
from scipy.optimize import brentq

from validation.default_inputs import archive

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
OBSERVATION_COLUMNS = [
    "record_id", "source_pdf_page", "printed_page", "locator", "manufacturer", "family_id", "model",
    "product_class", "exchanger", "hx_type", "role", "mode", "refrigerant", "point_label", "fluid",
    "fluid_in_text", "fluid_in_unit", "fluid_in_wb_text", "fluid_out_text", "fluid_out_unit", "flow_text",
    "flow_unit", "flow_basis", "pressure_text", "pressure_unit", "pressure_location", "sat_temp_text",
    "sat_temp_unit", "sat_temp_kind", "duty_text", "duty_unit", "duty_kind", "input_power_text",
    "input_power_unit", "nameplate_capacity_text", "nameplate_capacity_unit", "nameplate_mode",
    "compressor_speed_text", "compressor_speed_unit", "value_kind", "surface_state", "note",
]  # fmt: skip
ERRATA_FIELDS = set(OBSERVATION_COLUMNS) - {"record_id", "note"}

PRODUCT_CLASSES = ("split_wall", "split_ducted", "split_other", "air_to_water", "water_to_air", "water_to_water",
                   "hpwh_integrated", "test_rig")  # fmt: skip
EXCHANGERS = ("outdoor_coil", "indoor_coil", "liquid_source_hx", "water_condenser", "tank_coil", "load_water_hx")
ROLES = ("condensing", "evaporating")
#: Kinds of value a default may rest on. Design-sheet and model output are recorded, never fitted.
FITTED_VALUE_KINDS = ("manufacturer_standard_data", "manufacturer_operating_table", "measured_lab")
#: Surface states under which the sensible effectiveness is that of the model's dry exchanger.
DRY_STATES = ("dry", "possibly_wet", "n/a")
CLASS_GROUP = {"split_wall": "split", "split_other": "split"}

POWER_TO_W = {"W": 1.0, "kW": 1000.0, "Btu/h": 0.29307107, "MBtu/h": 293.07107, "MBH": 293.07107, "ton": 3516.8525}
AIRFLOW_TO_M3_S = {"m3/s": 1.0, "m3/min": 1 / 60, "m3/h": 1 / 3600, "l/s": 1e-3, "L/s": 1e-3, "CFM": 0.0283168466 / 60}
LIQUID_TO_M3_S = {"m3/s": 1.0, "m3/h": 1 / 3600, "l/s": 1e-3, "L/s": 1e-3, "l/min": 1e-3 / 60, "L/min": 1e-3 / 60,
                  "gpm": 3.785411784e-3 / 60}  # fmt: skip
MASSFLOW_TO_KG_S = {"kg/s": 1.0, "kg/h": 1 / 3600}
ATM = 101325.0
#: Pressure units to Pa. The basis (gauge or absolute) is read from the printed unit; a unit that does not
#: state it is converted only when the basis can be inferred from the same table (see ``infer_pressure_basis``)
#: or is declared for the source in sources.json (``pressure_basis`` with ``pressure_basis_evidence``).
PRESSURE_FACTOR = {"psi": 6894.757293, "mpa": 1e6, "kpa": 1e3, "bar": 1e5, "kgf/cm2": 98066.5}
_GAUGE_MARKS = ("(g)", "(gauge)")
_ABSOLUTE_MARKS = ("(a)", "(abs)", "(absolute)")


def pressure_unit(unit: str) -> tuple[float, str | None]:
    """Printed pressure unit -> (factor to Pa, "gauge" | "absolute" | None when the basis is not stated)."""
    u = unit.replace(" ", "").lower()
    for marks, basis in ((_GAUGE_MARKS, "gauge"), (_ABSOLUTE_MARKS, "absolute")):
        for mark in marks:
            if u.endswith(mark):
                return PRESSURE_FACTOR[u[: -len(mark)]], basis
    if u in ("psig", "kgf/cm2g", "barg", "mpag", "kpag"):
        return PRESSURE_FACTOR[u[:-1]], "gauge"
    if u in ("psia", "bara"):
        return PRESSURE_FACTOR[u[:-1]], "absolute"
    return PRESSURE_FACTOR[u], None


C_AIR = 1006.0  # J/(kg K), dry air
R_AIR = 287.05
ANTIFREEZE = {"methanol": "MMA", "propylene glycol": "MPG", "ethylene glycol": "MEG", "ethanol": "MEA"}
REFRIGERANT_FLUIDS = {"R410A": "R410A", "R32": "R32", "R290": "R290", "R22": "R22", "R134a": "R134a",
                      "R407C": "R407C", "R454B": "R454B.mix", "R1234yf": "R1234yf", "R744": "CO2"}  # fmt: skip

#: Candidate transfer laws in order of preference when their hold-out errors are within the tolerance:
#: one parameter and always solvable first, the fixed inlet difference (not always solvable) after them,
#: the two-parameter law last.
LAWS = ("leaving_approach", "ua_per_duty", "ua_per_nameplate", "itd", "ua_linear")
SELECTION_TOLERANCE = 1.10
SHIPPING_GATE = {"holdout_groups": 3, "manufacturers": 2}
SELECTION = (
    "Per stratum, every candidate law is fitted with one hold-out group (product lines sharing hardware) left "
    "out, family-balanced, and scored by the RMSE of the predicted inlet temperature difference [K] of the "
    "held-out units at their own flow and duty. Selected: the first law in the order "
    f"{', '.join(LAWS)} whose RMSE is within {SELECTION_TOLERANCE:.2f} of the lowest."
)
_NUMBER = r"-?\d+(?:\.\d+)?"
_RANGE = re.compile(rf"^({_NUMBER})\s*(?:-|–|~|to)\s*({_NUMBER})$")
_RANGE_OR_NUMBER = re.compile(rf"^{_NUMBER}(\s*(?:-|–|~|to)\s*{_NUMBER})?$")
_CHANGE = re.compile(r"^(rise|drop)\s+(.+)$", re.IGNORECASE)


def refrigerant_fluid(label: str) -> str:
    """CoolProp fluid of a printed refrigerant label ('R-410A', 'Propane (R290)')."""
    m = re.search(r"R-?\s?(\d{2,4}[A-Za-z]{0,2})", label)
    if not m:
        raise ValueError(f"unknown refrigerant label {label!r}")
    return REFRIGERANT_FLUIDS[archive.normalize_refrigerant("R" + m.group(1))]


def number(text: str) -> tuple[float, float, float] | None:
    """Printed number or range -> (central, low, high); empty or not a number -> None."""
    text = text.strip()
    # '10,187' is a thousands separator; '26,7' and '8024,3' are decimal commas (AHRI report forms). A decimal
    # comma followed by exactly three digits cannot be told apart and is corrected through errata.csv.
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    if not text:
        return None
    m = _RANGE.match(text)
    if m:
        low, high = sorted((float(m.group(1)), float(m.group(2))))
        return (low + high) / 2, low, high
    try:
        value = float(text)
    except ValueError:
        return None  # a statement in words ('incoming brine', 'not measured'): kept in the observation only
    return value, value, value


def to_celsius(value: float, unit: str) -> float:
    unit = unit.replace("°", "").replace("deg", "").strip().upper()
    if unit == "C":
        return value
    if unit == "F":
        return (value - 32.0) / 1.8
    if unit == "K":
        return value - 273.15
    raise ValueError(f"unknown temperature unit {unit!r}")


def kelvin_per_unit(unit: str) -> float:
    return 1 / 1.8 if unit.replace("°", "").replace("deg", "").strip().upper() == "F" else 1.0


@cache
def _mixture_saturation_curve(fluid: str, q: float) -> tuple[float, ...]:
    """Coefficients of ``ln p = a + b/T + c ln T + d T`` for a CoolProp predefined mixture at quality ``q``.

    CoolProp's mixture flash fails above about 330 K for R454B, where the condensing pressures of cooling
    duty lie. The curve is fitted to its temperature-quality flashes between 240 and 322 K (residual below
    0.01 K there; fitted to 307 K it predicts 322 K within 0.04 K) and carries the last fifteen kelvin.
    """
    T = np.linspace(240.0, 322.0, 40)
    lnp = np.log([CP.PropsSI("P", "T", float(t), "Q", q, fluid) for t in T])
    coefficients, *_ = np.linalg.lstsq(np.column_stack([np.ones_like(T), 1 / T, np.log(T), T]), lnp, rcond=None)
    return tuple(float(c) for c in coefficients)


def _mixture_saturation_K(p_abs: float, fluid: str, q: float) -> float:
    a, b, c, d = _mixture_saturation_curve(fluid, q)
    return float(brentq(lambda t: a + b / t + c * math.log(t) + d * t - math.log(p_abs), 200.0, 400.0))


def saturation_C(p_abs: float, refrigerant: str) -> tuple[float, float]:
    """Mean of bubble and dew temperature [degC] at ``p_abs`` [Pa], and the glide [K]."""
    fluid = refrigerant_fluid(refrigerant)
    if fluid.endswith(".mix"):
        bubble, dew = (_mixture_saturation_K(p_abs, fluid, q) for q in (0.0, 1.0))
    else:
        bubble, dew = (CP.PropsSI("T", "P", p_abs, "Q", q, fluid) for q in (0.0, 1.0))
    return (bubble + dew) / 2 - 273.15, dew - bubble


def capacity_rate(row: dict, T_in: float) -> tuple[float | None, str]:
    """Fluid heat capacity rate C [W/K] at the entering state and how it was obtained.

    A liquid the document does not name is taken as water (the heat capacity per litre of the antifreeze
    mixtures ground loops use is within about 5 % of it); a flow printed per ton of nominal capacity is not
    converted, because the nominal tonnage would have to be decoded from the model number.
    """
    flow = number(row["flow_text"])
    if flow is None:
        return None, "no_flow"
    unit, fluid = row["flow_unit"], row["fluid"].strip().lower()
    if fluid == "air":
        return flow[0] * AIRFLOW_TO_M3_S[unit] * ATM / (R_AIR * (T_in + 273.15)) * C_AIR, "dry air at inlet, 1 atm"
    if unit not in LIQUID_TO_M3_S and unit not in MASSFLOW_TO_KG_S:
        return None, f"flow unit {unit!r} not converted"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*%\s*(.+)$", fluid)
    if m and m.group(2).strip() in ANTIFREEZE:
        name = f"INCOMP::{ANTIFREEZE[m.group(2).strip()]}[{float(m.group(1)) / 100:.4f}]"
        basis = f"{name} at inlet"
    else:
        name = "Water"
        basis = "water at inlet" if fluid.startswith("water") else f"liquid {row['fluid']!r} taken as water"
    T, p = max(T_in, 0.5) + 273.15, 2e5
    cp = CP.PropsSI("C", "T", T, "P", p, name)
    if unit in MASSFLOW_TO_KG_S:
        return flow[0] * MASSFLOW_TO_KG_S[unit] * cp, basis
    return flow[0] * LIQUID_TO_M3_S[unit] * CP.PropsSI("D", "T", T, "P", p, name) * cp, basis


def exchanger_duty(row: dict) -> tuple[float | None, str]:
    """Heat duty of this exchanger [W]: printed directly, or the unit capacity with the input power on the
    side that carries the compressor work."""
    duty, kind = number(row["duty_text"]), row["duty_kind"]
    if duty is None or kind in ("", "none"):
        return None, "no_duty"
    Q = duty[0] * POWER_TO_W[row["duty_unit"]]
    if kind == "coil_duty":
        return Q, "printed coil duty"
    if kind != "unit_capacity_at_point":
        raise ValueError(f"{row['record_id']}: unknown duty_kind {kind!r}")
    # The exchanger the capacity is stated on: the load side of the mode.
    load_side = {"cooling": "evaporating", "heating": "condensing", "dhw": "condensing"}[row["mode"]]
    if row["role"] == load_side:
        return Q, "unit capacity (load side)"
    power = number(row["input_power_text"])
    if power is None:
        return None, "no_input_power"
    P = power[0] * POWER_TO_W[row["input_power_unit"]]
    return (
        (Q + P, "unit capacity + total input")
        if row["role"] == "condensing"
        else (Q - P, "unit capacity - total input")
    )


def infer_pressure_basis(observations: list[dict]) -> dict[str, dict]:
    """Basis of the pressures of sources whose unit does not state it, from the table itself.

    Where a table prints a condensing (or evaporating) temperature next to the pressure, only one basis
    reproduces it: the basis is accepted when its median mismatch is below 1 K and the other's above 2 K.
    """
    by_source = defaultdict(list)
    for o in observations:
        p, t = number(o["pressure_text"].split("/")[0]), number(o["sat_temp_text"])
        if (
            p
            and t
            and o["sat_temp_kind"] in ("printed_condensing_temp", "printed_evaporating_temp", "computed_by_source")
        ):
            factor, basis = pressure_unit(o["pressure_unit"])
            if basis is None:
                printed = to_celsius(t[0], o["sat_temp_unit"])
                by_source[o["source_id"]].append(
                    {
                        b: abs(saturation_C(p[0] * factor + off, o["refrigerant"])[0] - printed)
                        for b, off in (("gauge", ATM), ("absolute", 0.0))
                    }
                )
    out = {}
    for source, pairs in by_source.items():
        med = {b: sorted(x[b] for x in pairs)[len(pairs) // 2] for b in ("gauge", "absolute")}
        best, other = sorted(med, key=med.get)
        if med[best] < 1.0 and med[other] > 2.0:
            out[source] = {"basis": best, "n": len(pairs), "median_mismatch_K": med,
                           "evidence": "the printed saturation temperatures of the same table"}  # fmt: skip
    return out


def measure(row: dict, validation_sources: set[str], pressure_basis: dict[str, dict] | None = None) -> dict:
    """SI values and the exchanger quantities of one observation row."""
    for field, allowed in (("product_class", PRODUCT_CLASSES), ("exchanger", EXCHANGERS), ("role", ROLES)):
        if row[field] not in allowed:
            raise ValueError(f"{row['record_id']}: {field} {row[field]!r} not in {allowed}")
    out = {k: row[k] for k in ("record_id", "source_id", "manufacturer", "family_id", "model", "product_class",
                               "exchanger", "hx_type", "role", "mode", "refrigerant", "point_label", "fluid",
                               "value_kind", "surface_state", "flow_basis", "source_pdf_page")}  # fmt: skip
    out["source_pdf_page"] = int(row["source_pdf_page"])
    out["errata_applied"] = row.get("errata_applied", [])
    reasons: list[str] = [row["exclusion_reason"]] if row.get("exclusion_reason") else []
    T_in = number(row["fluid_in_text"])
    out["T_in_C"] = to_celsius(T_in[0], row["fluid_in_unit"]) if T_in else None

    # Saturation temperature: the printed pressure where its basis is stated, else what the document prints.
    out["T_sat_C"], out["sat_basis"], out["T_sat_low_C"], out["T_sat_high_C"] = None, "none", None, None
    pressure = number(row["pressure_text"].split("/")[0])
    printed = number(row["sat_temp_text"])
    if printed:
        out["T_sat_printed_C"] = to_celsius(printed[0], row["sat_temp_unit"])
        out["sat_temp_kind"] = row["sat_temp_kind"]
    factor, basis = pressure_unit(row["pressure_unit"]) if pressure else (None, None)
    if pressure and basis is None and row["source_id"] in (pressure_basis or {}):
        basis = pressure_basis[row["source_id"]]["basis"]
        out["pressure_basis_from"] = pressure_basis[row["source_id"]]["evidence"]
    if pressure and basis:
        offset = ATM if basis == "gauge" else 0.0
        (out["T_sat_C"], out["glide_K"]) = saturation_C(pressure[0] * factor + offset, row["refrigerant"])
        out["T_sat_low_C"] = saturation_C(pressure[1] * factor + offset, row["refrigerant"])[0]
        out["T_sat_high_C"] = saturation_C(pressure[2] * factor + offset, row["refrigerant"])[0]
        out["sat_basis"] = "printed pressure"
    elif printed:
        out["T_sat_C"] = out["T_sat_printed_C"]
        out["T_sat_low_C"] = to_celsius(printed[1], row["sat_temp_unit"])
        out["T_sat_high_C"] = to_celsius(printed[2], row["sat_temp_unit"])
        out["sat_basis"] = row["sat_temp_kind"]
    if pressure and basis is None:
        out["pressure_basis_not_stated"] = f"{row['pressure_text']} {row['pressure_unit']}"

    nameplate = number(row["nameplate_capacity_text"])
    out["nameplate_W"] = nameplate[0] * POWER_TO_W[row["nameplate_capacity_unit"]] if nameplate else None
    out["nameplate_mode"] = row["nameplate_mode"]
    out["Q_hx_W"], out["duty_basis"] = exchanger_duty(row)
    leaving_only = number(row["fluid_out_text"]) if re.fullmatch(_NUMBER, row["fluid_out_text"].strip()) else None
    if out["T_in_C"] is None and leaving_only and out["Q_hx_W"] and row["fluid"].strip().lower() != "air":
        # A table that prints only the leaving liquid temperature with flow and duty: the entering temperature
        # follows from the energy balance (liquid properties at the leaving temperature, one iteration).
        T_out = to_celsius(leaving_only[0], row["fluid_out_unit"])
        C_out = capacity_rate(row, T_out)[0]
        if C_out:
            sign = -1 if row["role"] == "condensing" else 1
            out["T_in_C"] = T_out + sign * out["Q_hx_W"] / C_out
            out["T_in_basis"] = "leaving temperature, flow and duty"
    out["C_W_K"], out["C_basis"] = (None, "no_inlet") if out["T_in_C"] is None else capacity_rate(row, out["T_in_C"])

    # Fluid temperature change printed by the document (leaving temperature, or a rise/drop).
    out["Q_fluid_W"], out["dT_fluid_printed_K"] = None, None
    leaving = row["fluid_out_text"].strip()
    change = _CHANGE.match(leaving)
    if change and not _RANGE_OR_NUMBER.match(change.group(2).strip()):
        change = None
    if leaving and not change and not re.fullmatch(_NUMBER, leaving.replace(",", "")):
        # Two sensors, 'not measured', a statement in words: kept in the observation, not used.
        out["fluid_out_not_used"], leaving = leaving, ""
    if leaving and change:
        out["dT_fluid_printed_K"] = number(change.group(2))[0] * kelvin_per_unit(row["fluid_out_unit"])
    elif leaving and out["T_in_C"] is not None:
        out["dT_fluid_printed_K"] = abs(to_celsius(number(leaving)[0], row["fluid_out_unit"]) - out["T_in_C"])
    if out["dT_fluid_printed_K"] and out["C_W_K"]:
        out["Q_fluid_W"] = out["C_W_K"] * out["dT_fluid_printed_K"]
        if out["Q_hx_W"] is None and row["fluid"].strip().lower() != "air":
            # A liquid exchanger with printed flow and temperature change carries its own duty.
            out["Q_hx_W"], out["duty_basis"] = out["Q_fluid_W"], "liquid flow x printed temperature change"

    for key in ("ITD_K", "dT_K", "eps", "leave_K", "LMTD_K", "UA_W_K", "UA_per_Q_hx"):
        out[key] = None
    out["dT_basis"] = "none"
    if out["T_sat_C"] is not None and out["T_in_C"] is not None:
        out["ITD_K"] = abs(out["T_in_C"] - out["T_sat_C"])
        sign = 1 if row["role"] == "condensing" else -1
        if sign * (out["T_sat_C"] - out["T_in_C"]) <= 0:
            reasons.append("saturation_on_wrong_side_of_inlet")
    else:
        reasons.append("no_saturation_or_inlet")
    # The fluid temperature change the exchanger works against: the declared duty over the heat capacity rate
    # where both are printed (what the model is given), else the change the document prints.
    if out["Q_hx_W"] and out["C_W_K"]:
        out["dT_K"], out["dT_basis"] = out["Q_hx_W"] / out["C_W_K"], "duty / heat capacity rate"
    elif out["dT_fluid_printed_K"]:
        out["dT_K"], out["dT_basis"] = out["dT_fluid_printed_K"], "printed fluid temperature change"
    if out["ITD_K"] and out["dT_K"]:
        out["eps"] = out["dT_K"] / out["ITD_K"]
        if 0 < out["eps"] < 1:
            out["leave_K"] = out["ITD_K"] - out["dT_K"]
            out["LMTD_K"] = out["dT_K"] / math.log(out["ITD_K"] / out["leave_K"])
            out["UA_per_Q_hx"] = 1.0 / out["LMTD_K"]
            if out["C_W_K"]:
                out["UA_W_K"] = -out["C_W_K"] * math.log(1 - out["eps"])
        else:
            reasons.append("effectiveness_outside_0_1")
    elif not reasons:
        reasons.append("no_fluid_temperature_change")
    if out["Q_fluid_W"] and out["Q_hx_W"] and out["duty_basis"] != "liquid flow x printed temperature change":
        out["Q_fluid_over_Q_hx"] = out["Q_fluid_W"] / out["Q_hx_W"]

    if row["source_id"] in validation_sources:
        reasons.append("validation_unit")
    if row["value_kind"] not in FITTED_VALUE_KINDS:
        reasons.append(f"value_kind_{row['value_kind']}")
    if row["surface_state"] not in DRY_STATES:
        reasons.append(f"surface_{row['surface_state']}")
    if out["sat_basis"] == "pipe_surface_temp":
        reasons.append("saturation_from_pipe_surface")
    out["eligible"] = not reasons
    out["exclusion_reason"] = "; ".join(reasons)
    out["stratum"] = "|".join(
        (CLASS_GROUP.get(row["product_class"], row["product_class"]), row["exchanger"], row["hx_type"], row["role"])
    )
    return out


def weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    order = sorted(range(len(values)), key=values.__getitem__)
    total, cumulative = sum(weights), 0.0
    for i in order:
        cumulative += weights[i]
        if cumulative >= q * total - 1e-12:
            return values[i]
    return values[order[-1]]


def balanced_weights(rows: list[dict]) -> list[float]:
    """Every hold-out group counts once, however many units it lists."""
    count = Counter(r["holdout_group"] for r in rows)
    return [1.0 / count[r["holdout_group"]] for r in rows]


def _full(rows: list[dict]) -> bool:
    """Whether every row carries a conductance and a nameplate (the laws written on the nameplate need both)."""
    return all(r["UA_W_K"] and r["nameplate_W"] for r in rows)


def fit_law(rows: list[dict], kind: str) -> dict | None:
    w = balanced_weights(rows)

    def median(values: list[float]) -> float:
        return weighted_quantile(values, w, 0.5)

    if kind == "leaving_approach":
        return {"kind": kind, "dT_out_K": median([r["leave_K"] for r in rows])}
    if kind == "itd":
        return {"kind": kind, "ITD_K": median([r["ITD_K"] for r in rows])}
    if kind == "ua_per_duty":
        return {"kind": kind, "k_per_K": median([r["UA_per_Q_hx"] for r in rows])}
    if not _full(rows):
        return None
    if kind == "ua_per_nameplate":
        return {"kind": kind, "k_per_K": median([r["UA_W_K"] / r["nameplate_W"] for r in rows])}
    # Two parameters: at least four hold-out groups, and a positive conductance over the fitted range.
    if len({r["holdout_group"] for r in rows}) < 4:
        return None
    x = [r["nameplate_W"] / 1000 for r in rows]
    y = [r["UA_W_K"] for r in rows]
    sw, sx, sy = sum(w), sum(a * b for a, b in zip(w, x, strict=True)), sum(a * b for a, b in zip(w, y, strict=True))
    sxx = sum(a * b * b for a, b in zip(w, x, strict=True))
    sxy = sum(a * b * c for a, b, c in zip(w, x, y, strict=True))
    det = sw * sxx - sx * sx
    if abs(det) < 1e-12:
        return None
    a = (sw * sxy - sx * sy) / det
    b = (sy - a * sx) / sw
    if min(a * min(x) + b, a * max(x) + b) <= 0:
        return None
    return {"kind": kind, "a_W_K_per_kW": a, "b_W_K": b}


def predict_itd(law: dict, row: dict) -> float | None:
    """Inlet temperature difference [K] the law gives for ``row`` at its own fluid temperature change."""
    dT = row["dT_K"]
    if law["kind"] == "leaving_approach":
        return law["dT_out_K"] + dT
    if law["kind"] == "itd":
        return law["ITD_K"]
    if law["kind"] == "ua_per_duty":
        return dT / (1 - math.exp(-law["k_per_K"] * dT))  # NTU = UA / C = k Q / C = k dT
    if law["kind"] == "ua_per_nameplate":
        UA = law["k_per_K"] * row["nameplate_W"]
    else:
        UA = law["a_W_K_per_kW"] * row["nameplate_W"] / 1000 + law["b_W_K"]
        if UA <= 0:
            return None
    return dT / (1 - math.exp(-UA / row["C_W_K"]))


def holdout(rows: list[dict], kind: str, by: str) -> dict:
    errors, per_fold = [], {}
    for fold in sorted({r[by] for r in rows}):
        train = [r for r in rows if r[by] != fold]
        law = fit_law(train, kind) if train else None
        if law is None:
            return {"valid": False, "reason": f"law not defined without {fold}"}
        predictions = [(predict_itd(law, r), r["ITD_K"]) for r in rows if r[by] == fold]
        if any(a is None for a, _ in predictions):
            return {"valid": False, "reason": f"no positive conductance for {fold}"}
        fold_errors = [a - b for a, b in predictions]
        per_fold[fold] = {"n": len(fold_errors), "bias_K": sum(fold_errors) / len(fold_errors)}
        errors += fold_errors
    return {
        "valid": True,
        "n": len(errors),
        "RMSE_K": math.sqrt(sum(e * e for e in errors) / len(errors)),
        "bias_K": sum(errors) / len(errors),
        "MAE_K": sum(abs(e) for e in errors) / len(errors),
        "folds": per_fold,
    }


def fit_stratum(rows: list[dict]) -> dict:
    groups = sorted({r["holdout_group"] for r in rows})
    makers = sorted({r["manufacturer"] for r in rows})
    w = balanced_weights(rows)
    band = {
        name: [weighted_quantile(values, w, q) for q in (0.1, 0.5, 0.9)]
        for name, values in (
            ("ITD_K", [r["ITD_K"] for r in rows]),
            ("leave_K", [r["leave_K"] for r in rows]),
            ("LMTD_K", [r["LMTD_K"] for r in rows]),
            ("eps", [r["eps"] for r in rows]),
            ("UA_per_Q_hx", [r["UA_per_Q_hx"] for r in rows]),
        )
    }
    named = [r for r in rows if r["nameplate_W"] and r["UA_W_K"] and r["Q_hx_W"]]
    if named:
        wn = balanced_weights(named)
        band["Q_hx_per_nameplate"] = [
            weighted_quantile([r["Q_hx_W"] / r["nameplate_W"] for r in named], wn, q) for q in (0.1, 0.5, 0.9)
        ]
        band["UA_per_nameplate"] = [
            weighted_quantile([r["UA_W_K"] / r["nameplate_W"] for r in named], wn, q) for q in (0.1, 0.5, 0.9)
        ]
    candidates, selected = {}, None
    if len(groups) >= 2:
        for kind in LAWS:
            law = fit_law(rows, kind)
            cv = holdout(rows, kind, "holdout_group") if law else {"valid": False, "reason": "nameplate missing"}
            maker_cv = (
                holdout(rows, kind, "manufacturer")
                if law and len(makers) > 1
                else {"valid": False, "reason": "single manufacturer"}
            )
            candidates[kind] = {"law": law, "group_holdout": cv, "manufacturer_holdout": maker_cv}
        valid = {k: c["group_holdout"]["RMSE_K"] for k, c in candidates.items() if c["group_holdout"].get("valid")}
        if valid:
            best = min(valid.values())
            selected = next(k for k in LAWS if k in valid and valid[k] <= SELECTION_TOLERANCE * best + 1e-12)
    meets = len(groups) >= SHIPPING_GATE["holdout_groups"] and len(makers) >= SHIPPING_GATE["manufacturers"]
    return {
        "n": len(rows),
        "holdout_groups": groups,
        "families": sorted({r["family_id"] for r in rows}),
        "manufacturers": makers,
        "value_kinds": dict(Counter(r["value_kind"] for r in rows)),
        "band_p10_p50_p90": band,
        "selected_law": candidates[selected]["law"] if selected else None,
        "candidates": candidates,
        "nameplate_range_W": [min(r["nameplate_W"] for r in named), max(r["nameplate_W"] for r in named)]
        if named
        else None,
        "duty_range_W": [min(d), max(d)] if (d := [r["Q_hx_W"] for r in rows if r["Q_hx_W"]]) else None,
        "n_with_conductance": sum(bool(r["UA_W_K"]) for r in rows),
        "meets_shipping_gate": meets,
        "source_ids": sorted({r["source_id"] for r in rows}),
    }


def role_ratios(rows: list[dict]) -> dict:
    """Effective UA of the same exchanger of the same unit as evaporator over that as condenser.

    A unit listed at several operating points contributes the ratio of its median conductances.
    """
    units: dict[tuple, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["UA_W_K"] and r["value_kind"] in FITTED_VALUE_KINDS and r["surface_state"] in DRY_STATES and r["eligible"]:
            units[(r["source_id"], r["model"], r["product_class"], r["exchanger"], r["hx_type"])][r["role"]].append(r)
    out: dict[str, list[dict]] = defaultdict(list)
    for (_, model, product, exchanger, hx_type), by_role in units.items():
        if len(by_role) == 2:
            med = {role: sorted(x["UA_W_K"] for x in v)[len(v) // 2] for role, v in by_role.items()}
            first = by_role["condensing"][0]
            out["|".join((CLASS_GROUP.get(product, product), exchanger, hx_type))].append(
                {"model": model, "manufacturer": first["manufacturer"], "holdout_group": first["holdout_group"],
                 "ratio": med["evaporating"] / med["condensing"],
                 "n_rows": {role: len(v) for role, v in by_role.items()}}
            )  # fmt: skip
    report = {}
    for key, items in out.items():
        count = Counter(i["holdout_group"] for i in items)
        w = [1.0 / count[i["holdout_group"]] for i in items]
        values = [i["ratio"] for i in items]
        makers = sorted({i["manufacturer"] for i in items})
        report[key] = {
            "n_units": len(items),
            "manufacturers": makers,
            "holdout_groups": sorted(count),
            "UA_evaporating_over_condensing_p10_p50_p90": [weighted_quantile(values, w, q) for q in (0.1, 0.5, 0.9)],
            "by_manufacturer_median": {
                m: sorted(v := [i["ratio"] for i in items if i["manufacturer"] == m])[len(v) // 2] for m in makers
            },
            "meets_shipping_gate": len(count) >= SHIPPING_GATE["holdout_groups"]
            and len(makers) >= SHIPPING_GATE["manufacturers"],
            "units": items,
        }
    return report


def write_measurements(rows: list[dict]) -> str:
    """measurements.csv (one line per observation, values to six significant digits) and its SHA-256."""
    columns = sorted({k for r in rows for k in r})

    def cell(value):
        if isinstance(value, float):
            return f"{value:.6g}"
        if isinstance(value, list | dict):
            return json.dumps(value, ensure_ascii=False, sort_keys=True) if value else ""
        return "" if value is None else value

    with (HERE / "measurements.csv").open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows([cell(r.get(c)) for c in columns] for r in rows)
    return archive.sha(HERE / "measurements.csv")


def build(release: str | None = None, note: str = "") -> dict:
    sources = archive.load_json(HERE, "sources.json")
    registry = {s["source_id"]: s for s in sources}
    if len(registry) != len(sources):
        raise ValueError("source_id must be unique")
    validation_sources = {s["source_id"] for s in sources if s.get("validation_unit")}
    groups = archive.load_json(HERE, "holdout_groups.json")
    observations = archive.read_observations(HERE, registry, OBSERVATION_COLUMNS)
    archive.apply_errata(HERE, observations, ERRATA_FIELDS)
    rows = []
    declared = {s["source_id"]: {"basis": s["pressure_basis"], "evidence": s["pressure_basis_evidence"]}
                for s in sources if s.get("pressure_basis")}  # fmt: skip
    pressure_basis = {**infer_pressure_basis(observations), **declared}
    for o in observations:
        r = measure(o, validation_sources, pressure_basis)
        r["holdout_group"] = groups.get(r["family_id"], r["family_id"])
        r["source_sha256"] = registry[o["source_id"]]["sha256"]
        rows.append(r)
    data_sha256 = write_measurements(rows)
    eligible = defaultdict(list)
    for r in rows:
        if r["eligible"]:
            eligible[r["stratum"]].append(r)
    # Rows that give only the temperature difference (no flow or duty, or a pipe-surface temperature) do not
    # enter a fit; they are reported per stratum as corroboration of the inlet difference.
    corroboration = defaultdict(list)
    for r in rows:
        if (
            not r["eligible"]
            and r["ITD_K"]
            and "validation_unit" not in r["exclusion_reason"]
            and r["value_kind"] in FITTED_VALUE_KINDS
        ):
            corroboration[r["stratum"]].append(r)
    report = {
        "dataset": {"records": len(rows), "eligible": sum(r["eligible"] for r in rows), "sources": len(sources)},
        "data_sha256": data_sha256,
        "selection": SELECTION,
        "pressure_basis": pressure_basis,
        "shipping_gate": SHIPPING_GATE,
        "fits": {k: fit_stratum(v) for k, v in sorted(eligible.items())},
        "itd_only_rows": {
            k: {
                "n": len(v),
                "manufacturers": sorted({r["manufacturer"] for r in v}),
                "ITD_K_min_median_max": [min(x := sorted(r["ITD_K"] for r in v)), x[len(x) // 2], max(x)],
                "reasons": dict(Counter(r["exclusion_reason"] for r in v)),
            }
            for k, v in sorted(corroboration.items())
        },
        "role_ratios": role_ratios(rows),
        "excluded": dict(Counter(r["exclusion_reason"] for r in rows if not r["eligible"])),
    }
    (HERE / "regression.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    record_release(report, release, note)
    return report


def record_release(report: dict, release: str | None, note: str) -> None:
    path = HERE / "releases.json"
    releases = json.loads(path.read_text()) if path.exists() else []
    current = {
        k: {"n": f["n"], "selected_law": f["selected_law"], "meets_shipping_gate": f["meets_shipping_gate"]}
        for k, f in report["fits"].items()
    }
    previous = releases[-1]["strata"] if releases else {}
    changes = [
        f"{k}: {previous.get(k)} -> {current.get(k)}"
        for k in sorted(set(previous) | set(current))
        if previous.get(k) != current.get(k)
    ]
    if release:
        if any(r["version"] == release for r in releases):
            raise ValueError(f"release {release} already recorded")
        releases.append({"version": release, "date": dt.date.today().isoformat(), "data_sha256": report["data_sha256"],
                         **report["dataset"], "changes": changes or ["none"], "note": note, "strata": current})  # fmt: skip
        path.write_text(json.dumps(releases, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({**report["dataset"], "changes_since_latest_release": changes}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--release", help="record this build in releases.json under the given version")
    parser.add_argument("--note", default="", help="what changed, for the release record")
    args = parser.parse_args()
    result = build(args.release, args.note)
    for key, fit in result["fits"].items():
        law = fit["selected_law"]
        print(f"{key}: n={fit['n']} groups={len(fit['holdout_groups'])} makers={len(fit['manufacturers'])} "
              f"gate={'yes' if fit['meets_shipping_gate'] else 'no'} law={law}")  # fmt: skip
