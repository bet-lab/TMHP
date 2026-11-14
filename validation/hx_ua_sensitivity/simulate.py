"""Heat-exchanger UA sensitivity sweep (UA sensitivity plan v3, Sec. 2-3).

Air-to-air only, on the two fixed-boundary cases of
``validation/fixed_boundary_plr/sweep.py``:

    heating  3.5 kW R32, outdoor  7 degC, room 20 degC
    cooling  3.5 kW R32, outdoor 35 degC, room 27 degC

Requested duty runs 1.00 -> 0.12 of nameplate in 0.04 steps, exactly as the
production harness does.  Rows at the compressor speed floor
(``capacity_clamped == "min"``) are kept in the CSV and flagged; the verdicts
use the continuously modulating rows only.

The sensitivity axis is the *rated* coil conductance handed to the
constructor.  Nothing else moves: the airflow law
``UA = UA_rated (V/V_rated)^0.65`` , the compressor coefficients and the
approach-temperature search bounds stay as shipped.

Passes
------
1. ``default``  model built without UA arguments -- the regression reference
                (``UA_ou_rated = hp_capacity/5``, ``UA_iu_rated = 0.8 UA_ou``).
2. ``joint``    both coils scaled by the same factor, JOINT_SCALES.
3. ``split``    one coil at a time, SPLIT_CASES, to separate outdoor from
                indoor when the joint scan shows a sensitivity.

The ``joint`` pass at scale 1.0 must reproduce pass 1 bit for bit; that gate
runs before anything else is written.

Every model is the running cycle (:data:`CYCLE_SETTINGS`): no on-off averaging
below the speed floor (``Cd_cycling=None``) and no frost (``defrost=False``),
so the sub-floor rows stay the minimum-capacity state the verdicts separate.
A scaled coil the library cannot rate (its reference state does not close,
``ReferenceStateError``) is kept as rows with that failure reason rather than
stopping the sweep.

Run::

    uv run python3 -m validation.hx_ua_sensitivity.simulate [--jobs 24]
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "results" / "hx_ua_sensitivity_once"
CSV_NAME = "hx_ua_sensitivity_once.csv"

FRACTIONS = tuple(round(1.0 - 0.04 * i, 2) for i in range(23))  # 1.00 .. 0.12

CASES = (
    # duty, capacity W, refrigerant, outdoor degC, room degC
    ("heating", 3500.0, "R32", 7.0, 20.0),
    ("cooling", 3500.0, "R32", 35.0, 27.0),
)

#: Both coils scaled together (Figures 1-4).
JOINT_SCALES = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)

#: One coil at a time (Figure 7), as ``(scale_ou, scale_iu)``.
SPLIT_CASES = ((0.5, 1.0), (2.0, 1.0), (1.0, 0.5), (1.0, 2.0))

#: Fan-flow lower bound hard-coded in ``calc_HX_perf_for_target_heat``.
FAN_FLOOR = 0.05

#: Constructor arguments of every model: the running cycle, no frost.
CYCLE_SETTINGS: dict = {"Cd_cycling": None, "defrost": False}

_MODELS: dict = {}


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------
def _model(cap: float, ref: str, s_ou: float | None, s_iu: float | None):
    """Model cached per (capacity, refrigerant, UA scaling).

    ``s_ou is None`` builds the model with no UA arguments at all, which is
    the regression reference: it must not merely agree with scale 1.0, it has
    to take the same code path the shipped library takes.
    """
    from tmhp import AirSourceHeatPump

    key = (cap, ref, s_ou, s_iu)
    if key not in _MODELS:
        if s_ou is None:
            _MODELS[key] = AirSourceHeatPump(hp_capacity=cap, ref=ref, **CYCLE_SETTINGS)
        else:
            probe = _default_ua(cap, ref)
            _MODELS[key] = AirSourceHeatPump(
                hp_capacity=cap,
                ref=ref,
                **CYCLE_SETTINGS,
                UA_ou_rated=probe[0] * s_ou,
                UA_iu_rated=probe[1] * float(s_iu),
            )
    return _MODELS[key]


def _default_ua(cap: float, ref: str) -> tuple[float, float]:
    """Shipped rated conductances, read off a default-constructed model."""
    m = _model(cap, ref, None, None)
    return float(m.UA_ou_rated), float(m.UA_iu_rated)


def _num(r: dict, key: str) -> float:
    v = r.get(key)
    return float(v) if isinstance(v, (int, float)) else float("nan")


def coil_state(m, r: dict, side: str) -> tuple[float, float, float]:
    """``(UA, NTU, epsilon)`` of one coil of a solved row, from the model's own law.

    The library does not report the coil conductance, so it is evaluated here
    at the solved airflow with exactly the call the model makes
    (``tmhp.heat_exchanger.calc_UA_from_dV_fan`` on the coil's rated UA,
    reference flow, cross-section and exponent; air density at the coil inlet).
    A study that patches that law (``refrigerant_ua_sensitivity.patch``) gets
    the patched value, provided its state is set to the solved row first.
    """
    import math

    from tmhp import heat_exchanger
    from tmhp.air_properties import air_density
    from tmhp.constants import c_a

    dV = _num(r, f"dV_{side}_a [m3/s]")
    t_in = _num(r, f"T_{side}_a_in [°C]")
    if not (math.isfinite(dV) and dV > 0.0 and math.isfinite(t_in)):
        return float("nan"), float("nan"), float("nan")
    if side == "ou":
        args = (m.dV_ou_fan_a_ref, m.A_cross_ou, m.UA_ou_rated, m.n_ou)
    else:
        args = (m.dV_iu_fan_a_ref, m.A_cross_iu, m.UA_iu_rated, m.n_iu)
    ua = float(heat_exchanger.calc_UA_from_dV_fan(dV, *args))
    ntu = ua / (c_a * float(air_density(t_in)) * dV)
    return ua, ntu, 1.0 - math.exp(-ntu)


def run_point(task: dict) -> dict:
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION
    from tmhp.reference_state import ReferenceStateError

    try:
        m = _model(task["capacity_W"], task["refrigerant"], task["scale_ou"], task["scale_iu"])
    except ReferenceStateError as exc:
        return {
            "variant": task["variant"],
            "pass": task["pass"],
            "scale_ou": task["scale_ou"] if task["scale_ou"] is not None else 1.0,
            "scale_iu": task["scale_iu"] if task["scale_iu"] is not None else 1.0,
            "model_class": "ASHP",
            "duty": task["duty"],
            "refrigerant": task["refrigerant"],
            "capacity_W": task["capacity_W"],
            "t_outdoor_C": task["t_outdoor_C"],
            "t_sink_C": task["t_sink_C"],
            "plr_request": task["plr_request"],
            "q_request_W": task["capacity_W"] * task["plr_request"],
            "failure_reason": f"model_not_built: {str(exc).split(':', 1)[0]}",
            "converged": False,
            "coefficient_version": COEFFICIENT_VERSION,
        }

    heating = task["duty"] == "heating"
    sign = -1.0 if heating else 1.0
    q_req = task["capacity_W"] * task["plr_request"]
    r = m.analyze_steady(
        Q_r_iu=sign * q_req,
        T0=task["t_outdoor_C"],
        T_a_room=task["t_sink_C"],
        return_dict=True,
        verbose=False,
    )
    assert isinstance(r, dict)

    delivered = abs(_num(r, "Q_ref_iu [W]"))
    fan_ou = _num(r, "dV_ou_a [m3/s]") / m.dV_ou_fan_a_rated
    fan_iu = _num(r, "dV_iu_a [m3/s]") / m.dV_iu_fan_a_rated
    ua_ou, ntu_ou, eps_ou = coil_state(m, r, "ou")
    ua_iu, ntu_iu, eps_iu = coil_state(m, r, "iu")
    t_evap, t_cond = _num(r, "T_ref_evap_sat [°C]"), _num(r, "T_ref_cond_sat_v [°C]")
    t_ou_in, t_iu_in = _num(r, "T_ou_a_in [°C]"), _num(r, "T_iu_a_in [°C]")

    # In heating the outdoor coil is the evaporator and the indoor coil the
    # condenser; in cooling the roles swap.  The approach is always written
    # so that a larger number means a larger temperature penalty.
    t_air_evap, t_air_cond = (t_ou_in, t_iu_in) if heating else (t_iu_in, t_ou_in)
    ua_evap, ua_cond = (ua_ou, ua_iu) if heating else (ua_iu, ua_ou)

    e_cmp, e_tot = _num(r, "E_cmp [W]"), _num(r, "E_tot [W]")
    e_fan = _num(r, "E_ou_fan [W]") + _num(r, "E_iu_fan [W]")
    return {
        "variant": task["variant"],
        "pass": task["pass"],
        "scale_ou": task["scale_ou"] if task["scale_ou"] is not None else 1.0,
        "scale_iu": task["scale_iu"] if task["scale_iu"] is not None else 1.0,
        "model_class": "ASHP",
        "duty": task["duty"],
        "refrigerant": task["refrigerant"],
        "capacity_W": task["capacity_W"],
        "t_outdoor_C": task["t_outdoor_C"],
        "t_sink_C": task["t_sink_C"],
        "plr_request": task["plr_request"],
        "q_request_W": q_req,
        "q_delivered_W": delivered,
        "plr_delivered": delivered / task["capacity_W"],
        "cr_actual": delivered / task["capacity_W"],
        "capacity_clamped": r.get("capacity_clamped"),
        "failure_reason": r.get("failure_reason", "none"),
        "converged": bool(r.get("converged", False)),
        "converged_rps": bool(r.get("converged_rps", False)),
        "ou_fan_flow_min_limit": bool(r.get("ou_fan_flow_min_limit", False)),
        "iu_fan_flow_min_limit": bool(r.get("iu_fan_flow_min_limit", False)),
        "rps": _num(r, "cmp_rpm [rpm]") / 60.0,
        "rps_min": m.rps_min,
        "rps_rated": m.rps_rated,
        "n_star": _num(r, "n_star [-]"),
        "pr": _num(r, "pr_cmp [-]"),
        "m_dot_ref": _num(r, "m_dot_ref [kg/s]"),
        "UA_ou_rated": float(m.UA_ou_rated),
        "UA_iu_rated": float(m.UA_iu_rated),
        "UA_ou": ua_ou,
        "UA_iu": ua_iu,
        "UA_evap": ua_evap,
        "UA_cond": ua_cond,
        "NTU_ou": ntu_ou,
        "NTU_iu": ntu_iu,
        "epsilon_ou": eps_ou,
        "epsilon_iu": eps_iu,
        "T_evap_C": t_evap,
        "T_cond_C": t_cond,
        "T_dis_C": _num(r, "T_ref_cmp_out [°C]"),
        "T_air_in_evap_C": t_air_evap,
        "T_air_in_cond_C": t_air_cond,
        "approach_evap_K": t_air_evap - t_evap,
        "approach_cond_K": t_cond - t_air_cond,
        "lift_ref_K": t_cond - t_evap,
        "lift_air_K": t_air_cond - t_air_evap,
        "fan_fraction_ou": fan_ou,
        "fan_fraction_iu": fan_iu,
        "fan_floor_ou": bool(fan_ou <= FAN_FLOOR * 1.01),
        "fan_floor_iu": bool(fan_iu <= FAN_FLOOR * 1.01),
        "eta_vol": _num(r, "eta_cmp_vol [-]"),
        "eta_isen": _num(r, "eta_cmp_isen [-]"),
        "eta_em": _num(r, "eta_cmp [-]"),
        "E_cmp": e_cmp,
        "E_ou_fan": _num(r, "E_ou_fan [W]"),
        "E_iu_fan": _num(r, "E_iu_fan [W]"),
        "E_fan": e_fan,
        "E_tot": e_tot,
        "fan_share": e_fan / e_tot if e_tot else float("nan"),
        "cop_sys": _num(r, "cop_sys [-]"),
        "cop_ref": _num(r, "cop_ref [-]"),
        "coefficient_version": COEFFICIENT_VERSION,
    }


# ---------------------------------------------------------------------------
# task construction
# ---------------------------------------------------------------------------
def variant_name(s_ou: float | None, s_iu: float | None) -> str:
    if s_ou is None:
        return "default"
    if s_ou == s_iu:
        return f"joint_{s_ou:g}"
    return f"ou_{s_ou:g}_iu_{s_iu:g}"


def _tasks(pass_name: str, s_ou: float | None, s_iu: float | None) -> list[dict]:
    out = []
    for duty, cap, ref, t_out, t_room in CASES:
        for f in FRACTIONS:
            out.append(
                {
                    "pass": pass_name,
                    "variant": variant_name(s_ou, s_iu),
                    "scale_ou": s_ou,
                    "scale_iu": s_iu,
                    "duty": duty,
                    "capacity_W": cap,
                    "refrigerant": ref,
                    "t_outdoor_C": t_out,
                    "t_sink_C": t_room,
                    "plr_request": f,
                }
            )
    return out


def _run(tasks: list[dict], jobs: int) -> pd.DataFrame:
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        rows = list(ex.map(run_point, tasks, chunksize=2))
    # A model that could not be built gives a short row; keep the full row's column order.
    columns = list(max(rows, key=len))
    return pd.DataFrame(rows, columns=columns)


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("SIM_JOBS", "24")))
    a = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- pass 1: library defaults, no UA arguments ----------------------
    ref_df = _run(_tasks("default", None, None), a.jobs)
    print(f"default: {len(ref_df)} rows, failures {(ref_df.failure_reason != 'none').sum()}")

    # --- pass 2: joint scaling ------------------------------------------
    joint_tasks: list[dict] = []
    for s in JOINT_SCALES:
        joint_tasks += _tasks("joint", s, s)
    joint_df = _run(joint_tasks, a.jobs)
    print(
        f"joint  : {len(JOINT_SCALES)} scales, {len(joint_df)} rows, failures {(joint_df.failure_reason != 'none').sum()}"
    )

    # --- regression gate: joint scale 1.0 == library defaults -----------
    keys = ["duty", "plr_request"]
    cmp_cols = ["cop_sys", "cop_ref", "UA_ou", "UA_iu", "T_evap_C", "T_cond_C", "pr", "E_tot", "n_star"]
    unity = joint_df[joint_df.variant == "joint_1"]
    merged = ref_df.set_index(keys)[cmp_cols].join(unity.set_index(keys)[cmp_cols], lsuffix="_ref", rsuffix="_unity")
    worst = {}
    for c in cmp_cols:
        d = (merged[f"{c}_unity"] - merged[f"{c}_ref"]).abs()
        scale = merged[f"{c}_ref"].abs().clip(lower=1e-12)
        worst[c] = float((d / scale).max())
    print("regression (joint scale 1.0 vs library defaults), max relative deviation:")
    for c, v in worst.items():
        print(f"    {c:<10} {v:.3e}")
    # ``not (v <= tol)``: a NaN deviation (a column the library stopped reporting) fails too.
    if any(not (v <= 1e-12) for v in worst.values()):
        raise SystemExit(f"REGRESSION FAILED: scale 1.0 does not reproduce the shipped model ({worst})")

    # --- pass 3: one coil at a time -------------------------------------
    split_tasks: list[dict] = []
    for s_ou, s_iu in SPLIT_CASES:
        split_tasks += _tasks("split", s_ou, s_iu)
    split_df = _run(split_tasks, a.jobs)
    print(
        f"split  : {len(SPLIT_CASES)} cases, {len(split_df)} rows, failures {(split_df.failure_reason != 'none').sum()}"
    )

    out = pd.concat([ref_df, joint_df, split_df], ignore_index=True)
    out.to_csv(OUT_DIR / CSV_NAME, index=False)

    meta = {
        "default_UA_ou_rated_W_K": float(ref_df.UA_ou_rated.iloc[0]),
        "default_UA_iu_rated_W_K": float(ref_df.UA_iu_rated.iloc[0]),
        "joint_scales": list(JOINT_SCALES),
        "split_cases": [list(c) for c in SPLIT_CASES],
        "fractions": list(FRACTIONS),
        "regression_max_rel_dev": worst,
        "n_rows": int(len(out)),
        "n_failures": int((out.failure_reason != "none").sum()),
        "n_speed_floor": int((out.capacity_clamped == "min").sum()),
        "n_fan_floor_ou": int(out.fan_floor_ou.astype("boolean").fillna(False).sum()),
        "n_fan_floor_iu": int(out.fan_floor_iu.astype("boolean").fillna(False).sum()),
        "coefficient_version": str(out.coefficient_version.iloc[0]),
    }
    (OUT_DIR / "run_manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    from validation import study_provenance

    study_provenance.write(
        OUT_DIR / "provenance.json",
        "validation/hx_ua_sensitivity/",
        sorted(p for p in OUT_DIR.iterdir() if p.suffix in (".csv", ".json") and p.name != "provenance.json"),
        {**CYCLE_SETTINGS, "cases": CASES, "joint_scales": JOINT_SCALES, "split_cases": SPLIT_CASES},
        "how the part-load curve of the running cycle moves with the rated coil conductance",
        command="python -m validation.hx_ua_sensitivity.simulate && python -m validation.hx_ua_sensitivity.figures",
    )
    print(json.dumps(meta, indent=2))
    print(f"wrote {OUT_DIR / CSV_NAME}")


if __name__ == "__main__":
    main()
