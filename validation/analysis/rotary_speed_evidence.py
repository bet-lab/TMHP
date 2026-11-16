r"""Device-level evidence for the default rotary speed shape (#183): ``shao_informed`` against ``ossorio_anchored``.

The rotary speed shape is the one coefficient choice of ``semi-empirical-2026-11-15`` the compressor data cannot
make: Shao 2004 and Ossorio 2019 disagree on the low-speed fall and no third rotary speed source exists (archive
README, "Rotary speed-shape variants"). The default was chosen by the EN 14825 trajectory shape (D/A of 30 cases
inside the certified p10-p90) and the choice was reopened as #183. This study puts the two shipped variants
side by side at the device level on everything the question touches, with one addition the model does not carry:
a fixed control and electronics power.

Why a fixed power
-----------------
The model's electrical input ``E_tot`` is the compressor plus the outdoor fan. A real unit also draws a control
board, sensors, communications and the drive's own standby; at the lightest EN 14825 point (D, about 15 rev/s) a
constant 15 W is about 3 % of the input. Heat Pump Keymark declares two such powers on every certificate
(``validation/data/auxiliary_power/``, extracted by :mod:`validation.auxiliary_power.pto_proxy`): the
off/standby modes (``P_offsb_a_W``, ``P_offsb_b_W``) and the thermostat-off mode (``P_TO_W``, compressor off,
controls live; on an air-to-water unit possibly a circulator too). The levels are read from those files, not
chosen here (:func:`fixed_power_levels`):

``none``
    0 W -- the model as shipped.
``certified_median``
    air-to-water: the median of the two off/standby declarations pooled; air-to-air: the median ``P_TO_W``
    (the off/standby declarations of the air-to-air certificates are about 1 W, the ecodesign standby cap, not a
    controls-live power).
``certified_upper``
    the upper quartile of ``P_TO_W`` of each population -- a sensitivity step above the median.

The fixed power is added afterwards: ``COP' = COP x E_tot / (E_tot + P)``, i.e. the heat the model's COP counts
over the input plus ``P``. At a running point (``Cd_cycling=None``, no on-off averaging) this is exact for a power
drawn whenever the unit runs; it is not a model of the unit's behaviour, and nothing in the library changes.

What is computed (both variants, every fixed-power level)
---------------------------------------------------------
(a) **EN 14825** -- the 30 cases of :mod:`validation.analysis.en14825_trend` (5 machines x 3 oversizings x 2
    applications, the same water side and model settings), with the delivered heat and the input at every point,
    scored against the certified population: every point inside its p10-p90 (the study's C2), and B/A, C/A, D/A
    inside the p10-p90 of the per-record ratio (D/A is the study's C3).
(b) **Catalogue parity** -- the adopted catalogues through :func:`validation.parity.run.run_catalog` with the
    rotary catalogues' ``compressor_type`` set to the variant (scroll catalogues are the same under both):
    COP MAPE and bias by model class and mode, and the A-7/W55 gate points of UQ09/12/16.
(c) **Part-load curves at fixed temperatures**, heating and cooling, from the speed floor to the speed ceiling:
    air-to-water 9 kW R32 at A7/W35, A7/W45 and A-7/W45 (leaving water held, fixed water flow of the EN 14511
    low-temperature rating as in the EN 14825 study) and air-to-air 3.5 kW R32 heating 7 / 20 degC and cooling
    35 / 27 degC, with :data:`CURVE_SETTINGS` (the running cycle, frost-free). The floor and the ceiling are run
    explicitly (a request far below and far above the range), the grid in between in steps of 0.02 of the
    nameplate.

Outputs (``validation/data/rotary_speed_evidence/``)
----------------------------------------------------
``fixed_power_levels.csv``, ``certified_bands.csv``, ``en14825_points.csv``, ``en14825_scores.csv``,
``en14825_summary.csv``, ``parity_points.csv``, ``parity_summary.csv``, ``parity_gate.csv``, ``curves.csv``,
``curve_summary.csv``; provenance record ``validation/data/provenance/rotary_speed_evidence.json``.

The certified population is the untracked per-model table ``validation/data/keymark_en14825_declared.csv``
(``python -m validation.extraction.keymark_declared``, from the hplib evidence in
``validation/registry/sources.yaml``); without it the study stops before running, as
``scripts/regen-validation.sh`` does for the EN 14825 study.

The study measures; it does not choose. The choice of the default is the owner's and the co-author's (#183).

Run
---
``uv run python -m validation.analysis.rotary_speed_evidence [--jobs N]``
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp.compressor_efficiency import COEFFICIENT_VERSION, ROTARY_SPEED_VARIANTS, CompressorSpec
from validation.analysis import en14825_trend as T

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "validation" / "data"
OUT_DIR = DATA / "rotary_speed_evidence"
PROVENANCE = DATA / "provenance" / "rotary_speed_evidence.json"
AUX_AIR_TO_WATER = DATA / "auxiliary_power" / "keymark_pto_air_to_water.csv"
AUX_AIR_TO_AIR = DATA / "auxiliary_power" / "keymark_pto_air_to_air.csv"

#: The two shipped rotary speed shapes (``compressor_efficiency.SEMI_EMPIRICAL_ROTARY_SPEED_VARIANTS``).
VARIANTS: tuple[str, ...] = tuple(ROTARY_SPEED_VARIANTS)
LEVELS = ("none", "certified_median", "certified_upper")
APPLICATIONS = ("low", "medium")
POINTS = ("A", "B", "C", "D")
RATIOS = {"B/A": "B", "C/A": "C", "D/A": "D"}

#: Model settings of the EN 14825 cases: those of the EN 14825 study (``en14825_trend.trajectory``).
EN14825_SETTINGS: dict = {"Cd_cycling": None}
#: Model settings of the fixed-temperature curves: the running cycle, no frost (``fixed_boundary_plr``).
CURVE_SETTINGS: dict = {"Cd_cycling": None, "defrost": False}

#: (capacity W, refrigerant, outdoor degC, leaving water degC)
AIR_TO_WATER_CURVES = (
    (9000.0, "R32", 7.0, 35.0),
    (9000.0, "R32", 7.0, 45.0),
    (9000.0, "R32", -7.0, 45.0),
)
#: (capacity W, refrigerant, duty, outdoor degC, room degC)
AIR_TO_AIR_CURVES = (
    (3500.0, "R32", "heating", 7.0, 20.0),
    (3500.0, "R32", "cooling", 35.0, 27.0),
)
#: Requested duty in units of the nameplate: the grid, and the two requests that land on the floor and the ceiling.
CURVE_FRACTIONS = tuple(round(0.04 + 0.02 * i, 2) for i in range(129))  # 0.04 .. 2.60
FLOOR_REQUEST = 0.01
CEILING_REQUEST = 5.0
#: The A-7/W55 gate points of the parity set (validation gate, UQ09/12/16).
GATE_SLUGS = ("panasonic_wh_uq09he8", "panasonic_wh_uq12he8", "panasonic_wh_uq16he8")
GATE_CONDITION = (-7.0, 55.0)


def spec(variant: str, kind: str = "rotary_twin_inverter") -> CompressorSpec:
    return CompressorSpec(kind, rotary_speed_variant=variant)


def with_fixed_power(cop, e_tot, p_fixed):
    """COP with a fixed power added to the input: the heat the COP counts over ``E_tot + P``."""
    return cop * e_tot / (e_tot + p_fixed)


# ---------------------------------------------------------------------------
# Fixed power levels, from the certified declarations
# ---------------------------------------------------------------------------
def fixed_power_levels() -> pd.DataFrame:
    """The three levels for each population, with the statistic and the records behind each value."""
    a2w = pd.read_csv(AUX_AIR_TO_WATER)
    a2a = pd.read_csv(AUX_AIR_TO_AIR)
    standby = pd.concat([a2w.P_offsb_a_W, a2w.P_offsb_b_W]).dropna()
    rows = []
    for model_class, frame in (("ASHPB", a2w), ("ASHP", a2a)):
        pto = frame.P_TO_W.dropna()
        if model_class == "ASHPB":
            median = ("median of P_offsb_a_W and P_offsb_b_W pooled", int(standby.size), float(standby.median()))
        else:
            median = ("median of P_TO_W", int(pto.size), float(pto.median()))
        for level, (statistic, n, value) in (
            ("none", ("model as shipped", 0, 0.0)),
            ("certified_median", median),
            ("certified_upper", ("upper quartile of P_TO_W", int(pto.size), float(pto.quantile(0.75)))),
        ):
            rows.append(
                {
                    "level": level,
                    "model_class": model_class,
                    "statistic": statistic,
                    "records": n,
                    "certificates": int(len(frame)),
                    "outdoor_units": int(frame.outdoor_unit.nunique()),
                    "P_fixed_W": value,
                }
            )
    return pd.DataFrame(rows)


def _power(levels: pd.DataFrame) -> dict[tuple[str, str], float]:
    return {(r.level, r.model_class): float(r.P_fixed_W) for r in levels.itertuples()}


# ---------------------------------------------------------------------------
# (a) EN 14825
# ---------------------------------------------------------------------------
def certified_bands() -> pd.DataFrame:
    """p10 / p50 / p90 of the declared COP at A-D and of the per-record ratios B/A, C/A, D/A."""
    df = pd.read_csv(T.DECLARED_CSV)
    rows = []
    for application in APPLICATIONS:
        q = df[df.application == application].dropna(subset=[f"cop_{p}" for p in POINTS])
        series = {p: q[f"cop_{p}"] for p in POINTS} | {r: q[f"cop_{p}"] / q.cop_A for r, p in RATIOS.items()}
        for quantity, s in series.items():
            rows.append(
                {
                    "application": application,
                    "quantity": quantity,
                    "n": int(s.size),
                    "p10": float(s.quantile(0.10)),
                    "p50": float(s.quantile(0.50)),
                    "p90": float(s.quantile(0.90)),
                }
            )
    return pd.DataFrame(rows)


def en14825_case(variant: str, capacity_w: float, refrigerant: str, application: str, oversizing: float) -> list[dict]:
    """One case of the EN 14825 study with the delivered heat and the input kept (as ``en14825_trend.trajectory``)."""
    model = AirSourceHeatPumpBoiler(
        hp_capacity=capacity_w, ref=refrigerant, compressor_type=spec(variant), **EN14825_SETTINGS
    )
    m_dot_w = T.en14825_water_flow(capacity_w, application)
    rows = []
    for label, t_outdoor, lwt, load_ratio in T.EN14825_POINTS[application]:
        r, t_in = T.solve_at_leaving_water(model, t_outdoor, lwt, capacity_w * oversizing * load_ratio, m_dot_w)
        ok = r.get("failure_reason", "none") == "none"
        rows.append(
            {
                "variant": variant,
                "application": application,
                "capacity_kW": capacity_w / 1000.0,
                "refrigerant": refrigerant,
                "oversizing": oversizing,
                "point": label,
                "plr": load_ratio,
                "t_outdoor_C": t_outdoor,
                "lwt_C": lwt,
                "t_water_in_C": t_in,
                "rps": float(r.get("cmp_rpm [rpm]") or float("nan")) / 60.0,
                "pr": float(r.get("pr_cmp [-]") or float("nan")),
                "clamped": r.get("capacity_clamped"),
                "q_W": float(r.get("Q_ref_tank [W]", float("nan"))) if ok else float("nan"),
                "E_tot_W": float(r.get("E_tot [W]", float("nan"))) if ok else float("nan"),
                "cop": float(r["cop_sys [-]"]) if ok else float("nan"),
                "failure_reason": r.get("failure_reason", "none"),
                "coefficient_version": COEFFICIENT_VERSION,
            }
        )
    return rows


def _en14825_tasks(variants=VARIANTS) -> list[tuple]:
    return [
        (v, cap, ref, app, ov)
        for v in variants
        for app in APPLICATIONS
        for cap, ref in T.CONFIGURATIONS
        for ov in T.OVERSIZING
    ]


def en14825_scores(points: pd.DataFrame, bands: pd.DataFrame, levels: pd.DataFrame) -> pd.DataFrame:
    """Per case, variant and fixed-power level: the four COPs, the three ratios and whether each is in its band."""
    band = bands.set_index(["application", "quantity"])
    power = _power(levels)
    keys = ["variant", "application", "capacity_kW", "refrigerant", "oversizing"]
    rows = []
    for level in LEVELS:
        p_fixed = power[(level, "ASHPB")]
        for key, g in points.groupby(keys, sort=False):
            g = g.set_index("point").loc[list(POINTS)]
            cop = with_fixed_power(g.cop, g.E_tot_W, p_fixed)
            row = dict(zip(keys, key, strict=True)) | {"level": level, "P_fixed_W": p_fixed}
            app = row["application"]
            inside = {}
            for p in POINTS:
                row[f"cop_{p}"] = float(cop[p])
                inside[p] = bool(band.at[(app, p), "p10"] <= cop[p] <= band.at[(app, p), "p90"])
                row[f"in_{p}"] = inside[p]
            for r, p in RATIOS.items():
                x = float(cop[p] / cop["A"])
                row[f"ratio_{p}A"] = x
                row[f"in_{p}A"] = bool(band.at[(app, r), "p10"] <= x <= band.at[(app, r), "p90"])
            row["points_inside"] = sum(inside.values())
            row["level_all_inside"] = all(inside.values())
            row["rising_A_to_D"] = bool(cop.is_monotonic_increasing and cop.notna().all())
            rows.append(row)
    return pd.DataFrame(rows)


def en14825_summary(scores: pd.DataFrame, bands: pd.DataFrame) -> pd.DataFrame:
    """Per variant, level and application (and both): median model value and cases inside, against the band."""
    band = bands.set_index(["application", "quantity"])
    quantities = {p: (f"cop_{p}", f"in_{p}") for p in POINTS} | {
        r: (f"ratio_{p}A", f"in_{p}A") for r, p in RATIOS.items()
    }
    rows = []
    for (variant, level), g in scores.groupby(["variant", "level"], sort=False):
        p_fixed = float(g.P_fixed_W.iat[0])
        for application in (*APPLICATIONS, "both"):
            s = g if application == "both" else g[g.application == application]
            base = {"variant": variant, "level": level, "P_fixed_W": p_fixed, "application": application}
            for quantity, (value, flag) in quantities.items():
                ref = band.loc[(application, quantity)] if application != "both" else None
                rows.append(
                    base
                    | {
                        "quantity": quantity,
                        "model_median": float(s[value].median()) if ref is not None else float("nan"),
                        "cases_inside": int(s[flag].sum()),
                        "cases": int(len(s)),
                        "pop_p10": float(ref.p10) if ref is not None else float("nan"),
                        "pop_p50": float(ref.p50) if ref is not None else float("nan"),
                        "pop_p90": float(ref.p90) if ref is not None else float("nan"),
                    }
                )
            for quantity, flag in (("all four points", "level_all_inside"), ("rising A to D", "rising_A_to_D")):
                rows.append(
                    base
                    | {
                        "quantity": quantity,
                        "model_median": float("nan"),
                        "cases_inside": int(s[flag].sum()),
                        "cases": int(len(s)),
                        "pop_p10": float("nan"),
                        "pop_p50": float("nan"),
                        "pop_p90": float("nan"),
                    }
                )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# (b) Catalogue parity
# ---------------------------------------------------------------------------
PARITY_COLUMNS = (
    "slug",
    "model_class",
    "refrigerant",
    "compressor_type",
    "point_id",
    "mode",
    "t_source_C",
    "t_sink_C",
    "q_kW",
    "cop_target",
    "cop_pred",
    "power_pred_kW",
    "rps",
    "capacity_clamped",
    "failure_reason",
    "usable",
)


def parity_catalog(variant: str, slug: str) -> pd.DataFrame:
    """One adopted catalogue with the rotary speed shape set to ``variant`` (scroll catalogues are not touched)."""
    from validation.parity.run import run_catalog
    from validation.parity.spec import load_all

    catalog = load_all(slug)[0]
    kind = catalog.model_compressor_type
    overrides = {"compressor_type": spec(variant, kind)} if kind and kind.startswith("rotary") else {}
    df = run_catalog(catalog, overrides)
    df = df[df.status == "adopted"][list(PARITY_COLUMNS)].copy()
    df.insert(0, "variant", variant)
    df["model_compressor_type"] = kind
    return df


def _parity_tasks(variants=VARIANTS) -> list[tuple]:
    from validation.parity.spec import load_all

    slugs = [c.slug for c in load_all()]
    return [(v, s) for v in variants for s in slugs]


def _with_level(points: pd.DataFrame, levels: pd.DataFrame, level: str) -> pd.DataFrame:
    power = _power(levels)
    u = points[points.usable].copy()
    u["P_fixed_W"] = u.model_class.map(lambda c: power[(level, c)])
    u["cop"] = with_fixed_power(u.cop_pred, u.power_pred_kW * 1e3, u.P_fixed_W)
    u["err_pct"] = (u.cop - u.cop_target) / u.cop_target * 100.0
    return u


def parity_summary(points: pd.DataFrame, levels: pd.DataFrame) -> pd.DataFrame:
    """COP MAPE and bias of the usable points by variant, level, model class and mode."""
    rows = []
    for level in LEVELS:
        u = _with_level(points, levels, level)
        for (variant, model_class, mode), g in u.groupby(["variant", "model_class", "mode"], sort=False):
            attempted = points[
                (points.variant == variant) & (points.model_class == model_class) & (points["mode"] == mode)
            ]
            rows.append(
                {
                    "variant": variant,
                    "level": level,
                    "model_class": model_class,
                    "mode": mode,
                    "P_fixed_W": float(g.P_fixed_W.iat[0]),
                    "points": int(len(g)),
                    "attempted": int(len(attempted)),
                    "MAPE_pct": float(g.err_pct.abs().mean()),
                    "bias_pct": float(g.err_pct.mean()),
                }
            )
    return pd.DataFrame(rows)


def parity_gate(points: pd.DataFrame, levels: pd.DataFrame) -> pd.DataFrame:
    """The A-7/W55 gate points of UQ09/12/16 under each variant and level (a failed point keeps its reason)."""
    t_source, t_sink = GATE_CONDITION
    gate = points[points.slug.isin(GATE_SLUGS) & (points.t_source_C == t_source) & (points.t_sink_C == t_sink)]
    rows = []
    for level in LEVELS:
        scored = _with_level(gate, levels, level).set_index(["variant", "slug", "point_id"])
        for r in gate.itertuples():
            key = (r.variant, r.slug, r.point_id)
            ok = key in scored.index
            rows.append(
                {
                    "variant": r.variant,
                    "level": level,
                    "slug": r.slug,
                    "point_id": r.point_id,
                    "cop_target": r.cop_target,
                    "cop": float(scored.at[key, "cop"]) if ok else float("nan"),
                    "err_pct": float(scored.at[key, "err_pct"]) if ok else float("nan"),
                    "failure_reason": r.failure_reason,
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# (c) Part-load curves at fixed temperatures
# ---------------------------------------------------------------------------
def _curve_requests() -> list[tuple[str, float]]:
    return [("floor", FLOOR_REQUEST), *(("grid", f) for f in CURVE_FRACTIONS), ("ceiling", CEILING_REQUEST)]


def _curve_row(base: dict, kind: str, fraction: float, capacity: float, result: dict, q_delivered: float) -> dict:
    ok = result.get("failure_reason", "none") == "none"
    return base | {
        "kind": kind,
        "plr_request": fraction,
        "q_request_W": capacity * fraction,
        "q_delivered_W": q_delivered if ok else float("nan"),
        "plr_delivered": q_delivered / capacity if ok else float("nan"),
        "E_tot_W": float(result.get("E_tot [W]", float("nan"))) if ok else float("nan"),
        "cop": float(result["cop_sys [-]"]) if ok else float("nan"),
        "rps": float(result.get("cmp_rpm [rpm]") or float("nan")) / 60.0,
        "pr": float(result.get("pr_cmp [-]") or float("nan")),
        "clamped": result.get("capacity_clamped"),
        "failure_reason": result.get("failure_reason", "none"),
        "coefficient_version": COEFFICIENT_VERSION,
    }


def curve_air_to_water(variant: str, case: tuple, fractions=None) -> list[dict]:
    capacity, ref, t_outdoor, lwt = case
    model = AirSourceHeatPumpBoiler(hp_capacity=capacity, ref=ref, compressor_type=spec(variant), **CURVE_SETTINGS)
    m_dot_w = T.en14825_water_flow(capacity, "low")
    base = {
        "variant": variant,
        "model_class": "ASHPB",
        "duty": "heating",
        "capacity_W": capacity,
        "refrigerant": ref,
        "t_outdoor_C": t_outdoor,
        "t_sink_C": lwt,
    }
    rows = []
    for kind, f in _curve_requests() if fractions is None else fractions:
        r, _ = T.solve_at_leaving_water(model, t_outdoor, lwt, capacity * f, m_dot_w)
        row = _curve_row(base, kind, f, capacity, r, float(r.get("Q_ref_tank [W]", float("nan"))))
        if kind == "grid" and row["clamped"] is not None:
            continue  # on the floor or the ceiling: the explicit end points carry it
        rows.append(row)
    return rows


def curve_air_to_air(variant: str, case: tuple, fractions=None) -> list[dict]:
    capacity, ref, duty, t_outdoor, t_room = case
    model = AirSourceHeatPump(hp_capacity=capacity, ref=ref, compressor_type=spec(variant), **CURVE_SETTINGS)
    sign = -1.0 if duty == "heating" else 1.0
    base = {
        "variant": variant,
        "model_class": "ASHP",
        "duty": duty,
        "capacity_W": capacity,
        "refrigerant": ref,
        "t_outdoor_C": t_outdoor,
        "t_sink_C": t_room,
    }
    rows = []
    for kind, f in _curve_requests() if fractions is None else fractions:
        r = model.analyze_steady(
            Q_r_iu=sign * capacity * f, T0=t_outdoor, T_a_room=t_room, return_dict=True, verbose=False
        )
        assert isinstance(r, dict)
        row = _curve_row(base, kind, f, capacity, r, abs(float(r.get("Q_ref_iu [W]", float("nan")))))
        if kind == "grid" and row["clamped"] is not None:
            continue
        rows.append(row)
    return rows


def _curve_tasks(variants=VARIANTS) -> list[tuple]:
    return [("ASHPB", v, c) for v in variants for c in AIR_TO_WATER_CURVES] + [
        ("ASHP", v, c) for v in variants for c in AIR_TO_AIR_CURVES
    ]


def _curve_by_class(model_class: str, variant: str, case: tuple) -> list[dict]:
    return (curve_air_to_water if model_class == "ASHPB" else curve_air_to_air)(variant, case)


def curve_summary(curves: pd.DataFrame, levels: pd.DataFrame) -> pd.DataFrame:
    """Per curve and level: floor and ceiling, the peak, and the COP there against the nameplate point."""
    power = _power(levels)
    keys = ["variant", "model_class", "duty", "capacity_W", "refrigerant", "t_outdoor_C", "t_sink_C"]
    rows = []
    for level in LEVELS:
        for key, g in curves.groupby(keys, sort=False):
            row = dict(zip(keys, key, strict=True))
            p_fixed = power[(level, row["model_class"])]
            g = g[g.failure_reason == "none"].sort_values("q_delivered_W")
            cop = with_fixed_power(g.cop, g.E_tot_W, p_fixed).to_numpy()
            floor = g.kind.to_numpy() == "floor"
            ceiling = g.kind.to_numpy() == "ceiling"
            name = (g.kind.to_numpy() == "grid") & np.isclose(g.plr_request.to_numpy(), 1.0)
            assert floor.sum() == ceiling.sum() == name.sum() == 1, key
            peak = int(np.argmax(cop))
            q = g.q_delivered_W.to_numpy()
            q_max = float(q[ceiling][0])
            cop_name = float(cop[name][0])
            rows.append(
                row
                | {
                    "level": level,
                    "P_fixed_W": p_fixed,
                    "q_floor_W": float(q[floor][0]),
                    "q_ceiling_W": q_max,
                    "plr_floor": float(q[floor][0]) / row["capacity_W"],
                    "plr_ceiling": q_max / row["capacity_W"],
                    "rps_floor": float(g.rps.to_numpy()[floor][0]),
                    "rps_ceiling": float(g.rps.to_numpy()[ceiling][0]),
                    "cop_floor": float(cop[floor][0]),
                    "cop_nameplate": cop_name,
                    "cop_peak": float(cop[peak]),
                    "cop_ceiling": float(cop[ceiling][0]),
                    "plr_peak": float(q[peak]) / row["capacity_W"],
                    "peak_of_ceiling": float(q[peak]) / q_max,
                    "rps_peak": float(g.rps.to_numpy()[peak]),
                    "peak_gain_over_nameplate_pct": (float(cop[peak]) / cop_name - 1.0) * 100.0,
                    "floor_vs_nameplate_pct": (float(cop[floor][0]) / cop_name - 1.0) * 100.0,
                    "ceiling_vs_nameplate_pct": (float(cop[ceiling][0]) / cop_name - 1.0) * 100.0,
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
_POOL: ProcessPoolExecutor | None = None


def _map(fn, tasks):
    """``map`` over task tuples, in order (so a ``--jobs N`` run writes the serial run's files)."""
    if _POOL is not None:
        return list(_POOL.map(fn, *zip(*tasks, strict=True)))
    return [fn(*t) for t in tasks]


def _flat(chunks) -> pd.DataFrame:
    return pd.DataFrame([row for chunk in chunks for row in chunk])


def write_provenance(levels: pd.DataFrame) -> None:
    from validation import study_provenance

    study_provenance.write(
        PROVENANCE,
        "validation/analysis/rotary_speed_evidence.py",
        sorted(OUT_DIR.glob("*.csv")),
        {
            "variants": VARIANTS,
            "en14825": {**EN14825_SETTINGS, "CONFIGURATIONS": T.CONFIGURATIONS, "OVERSIZING": T.OVERSIZING},
            "curves": {
                **CURVE_SETTINGS,
                "AIR_TO_WATER_CURVES": AIR_TO_WATER_CURVES,
                "AIR_TO_AIR_CURVES": AIR_TO_AIR_CURVES,
                "water_flow": "en14825_trend.en14825_water_flow(capacity, 'low')",
                "fractions": [CURVE_FRACTIONS[0], CURVE_FRACTIONS[-1], 0.02],
                "floor_request": FLOOR_REQUEST,
                "ceiling_request": CEILING_REQUEST,
            },
            "parity": "validation.parity.run.run_catalog on the adopted catalogues; rotary compressor_type set to the variant",
            "fixed_power_W": {f"{r.level}/{r.model_class}": r.P_fixed_W for r in levels.itertuples()},
        },
        "rotary speed shape at the device level (#183): EN 14825, catalogue parity and fixed-temperature curves, "
        "with and without a certified fixed power",
        command="python -m validation.analysis.rotary_speed_evidence --jobs 8",
    )


def main() -> None:
    global _POOL
    parser = argparse.ArgumentParser(description="Device-level evidence for the default rotary speed shape (#183)")
    parser.add_argument("--jobs", type=int, default=1, help="worker processes (default 1: serial)")
    args = parser.parse_args()
    if not T.DECLARED_CSV.exists():
        raise SystemExit(
            f"missing {T.DECLARED_CSV.relative_to(REPO_ROOT)} (gitignored): run "
            "`uv run python -m validation.extraction.keymark_declared` with hplib's input/csv under "
            "validation/evidence/data/hplib/csv/ (see validation/registry/sources.yaml)"
        )
    if args.jobs > 1:
        _POOL = ProcessPoolExecutor(max_workers=args.jobs)
    try:
        _main()
    finally:
        if _POOL is not None:
            _POOL.shutdown()


def _main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    levels = fixed_power_levels()
    bands = certified_bands()
    points = _flat(_map(en14825_case, _en14825_tasks()))
    scores = en14825_scores(points, bands, levels)
    summary = en14825_summary(scores, bands)
    parity = pd.concat(_map(parity_catalog, _parity_tasks()), ignore_index=True)
    psum = parity_summary(parity, levels)
    gate = parity_gate(parity, levels)
    curves = _flat(_map(_curve_by_class, _curve_tasks()))
    csum = curve_summary(curves, levels)

    for name, df in (
        ("fixed_power_levels", levels),
        ("certified_bands", bands),
        ("en14825_points", points),
        ("en14825_scores", scores),
        ("en14825_summary", summary),
        ("parity_points", parity),
        ("parity_summary", psum),
        ("parity_gate", gate),
        ("curves", curves),
        ("curve_summary", csum),
    ):
        df.to_csv(OUT_DIR / f"{name}.csv", index=False)
    write_provenance(levels)

    pd.set_option("display.width", 250)
    fmt = {"float_format": lambda v: f"{v:.2f}", "index": False}
    print(levels.to_string(**fmt))
    both = summary[(summary.application == "both") & summary.quantity.isin(["all four points", "D/A", "B/A", "C/A"])]
    print(both.pivot_table(index=["variant", "level"], columns="quantity", values="cases_inside", sort=False))
    print(psum.to_string(**fmt))
    print(gate.to_string(**fmt))
    cols = ["variant", "level", "duty", "t_outdoor_C", "t_sink_C", "plr_floor", "plr_ceiling", "plr_peak"]
    cols += ["peak_gain_over_nameplate_pct", "floor_vs_nameplate_pct", "ceiling_vs_nameplate_pct"]
    print(csum[cols].to_string(**fmt))
    print(f"wrote {OUT_DIR.relative_to(REPO_ROOT)}/ and {PROVENANCE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
