"""Refrigerant-flow-dependent UA sensitivity sweep (plan Sec. 7-9).

Air-to-air only, on the two fixed-boundary cases of
``validation/fixed_boundary_plr/sweep.py``:

    heating  3.5 kW R32, outdoor  7 degC, room 20 degC
    cooling  3.5 kW R32, outdoor 35 degC, room 27 degC

Requested duty runs 1.00 -> 0.12 of nameplate in 0.04 steps, exactly as the
production harness does.  Rows at the compressor speed floor
(``capacity_clamped == "min"``) are kept in the CSV and flagged; the verdicts
use the continuously modulating rows only.

Passes
------
1. ``unpatched``  the library as shipped -- the regression reference.
2. ``baseline``   patch installed with ``f_ref = 0``; must match pass 1.
                  Its PLR = 1.0 row defines ``m_dot_ref,rated`` per case.
3. ``variants``   the (f_ref, m) grid, with the refrigerant term active.

Run::

    uv run python3 -m validation.refrigerant_ua_sensitivity.simulate [--jobs 24]
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "results" / "refrigerant_ua_sensitivity_once"
CSV_NAME = "refrigerant_ua_sensitivity_once.csv"

FRACTIONS = tuple(round(1.0 - 0.04 * i, 2) for i in range(23))  # 1.00 .. 0.12

CASES = (
    # duty, capacity W, refrigerant, outdoor degC, room degC
    ("heating", 3500.0, "R32", 7.0, 20.0),
    ("cooling", 3500.0, "R32", 35.0, 27.0),
)

F_REF_MAIN = (0.10, 0.20, 0.30)  # Figures 2-6, at M_MAIN
M_MAIN = 0.6
F_REF_GRID = (0.05, 0.10, 0.20, 0.30)  # Figure 7 heatmap
M_GRID = (0.4, 0.6, 0.8)

#: Fan-flow lower bound hard-coded in ``calc_HX_perf_for_target_heat``.
FAN_FLOOR = 0.05

_MODELS: dict = {}
_PATCHED = False


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------
def _init(patched: bool) -> None:
    global _PATCHED
    _PATCHED = patched
    if patched:
        from validation.refrigerant_ua_sensitivity import patch

        patch.selftest()
        patch.install()


def _model(cap: float, ref: str):
    from tmhp import AirSourceHeatPump

    key = (cap, ref)
    if key not in _MODELS:
        _MODELS[key] = AirSourceHeatPump(hp_capacity=cap, ref=ref)
    return _MODELS[key]


def _num(r: dict, key: str) -> float:
    v = r.get(key)
    return float(v) if isinstance(v, (int, float)) else float("nan")


def run_point(task: dict) -> dict:
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION

    m = _model(task["capacity_W"], task["refrigerant"])
    ua_calls, x_ref_min = float("nan"), float("nan")
    if _PATCHED:
        from validation.refrigerant_ua_sensitivity import patch

        patch.set_law(task["f_ref"], task["m_exp"], task.get("m_dot_rated"))

    sign = -1.0 if task["duty"] == "heating" else 1.0
    q_req = task["capacity_W"] * task["plr_request"]
    r = m.analyze_steady(
        Q_r_iu=sign * q_req,
        T0=task["t_outdoor_C"],
        T_a_room=task["t_sink_C"],
        return_dict=True,
        verbose=False,
    )
    assert isinstance(r, dict)
    if _PATCHED:
        from validation.refrigerant_ua_sensitivity import patch

        ua_calls = float(patch.state["ua_calls"])
        x_ref_min = float(patch.state["x_ref_min"])

    delivered = abs(_num(r, "Q_ref_iu [W]"))
    m_dot = _num(r, "m_dot_ref [kg/s]")
    m_dot_rated = task.get("m_dot_rated")
    fan_ou = _num(r, "dV_ou_a [m3/s]") / m.dV_ou_fan_a_rated
    fan_iu = _num(r, "dV_iu_a [m3/s]") / m.dV_iu_fan_a_rated
    ua_ou, ua_iu = _num(r, "UA_ou [W/K]"), _num(r, "UA_iu [W/K]")
    return {
        "variant": task["variant"],
        "pass": task["pass"],
        "f_ref": task["f_ref"],
        "m_exp": task["m_exp"],
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
        "m_dot_ref": m_dot,
        "m_dot_ref_rated": float(m_dot_rated) if m_dot_rated else float("nan"),
        "x_ref": (m_dot / float(m_dot_rated)) if m_dot_rated else float("nan"),
        "x_ref_min_seen": x_ref_min,
        "ua_eval_count": ua_calls,
        "UA_ou": ua_ou,
        "UA_ou_rated": m.UA_ou_rated,
        "ua_ratio_ou": ua_ou / m.UA_ou_rated,
        "NTU_ou": _num(r, "NTU_ou [-]"),
        "epsilon_ou": _num(r, "epsilon_ou [-]"),
        "UA_iu": ua_iu,
        "UA_iu_rated": m.UA_iu_rated,
        "ua_ratio_iu": ua_iu / m.UA_iu_rated,
        "NTU_iu": _num(r, "NTU_iu [-]"),
        "epsilon_iu": _num(r, "epsilon_iu [-]"),
        "fan_fraction_ou": fan_ou,
        "fan_fraction_iu": fan_iu,
        "fan_floor_ou": bool(fan_ou <= FAN_FLOOR * 1.01),
        "fan_floor_iu": bool(fan_iu <= FAN_FLOOR * 1.01),
        "eta_vol": _num(r, "eta_cmp_vol [-]"),
        "eta_isen": _num(r, "eta_cmp_isen [-]"),
        "eta_em": _num(r, "eta_cmp [-]"),
        "E_cmp": _num(r, "E_cmp [W]"),
        "E_ou_fan": _num(r, "E_ou_fan [W]"),
        "E_iu_fan": _num(r, "E_iu_fan [W]"),
        "E_tot": _num(r, "E_tot [W]"),
        "cop_sys": _num(r, "cop_sys [-]"),
        "T_evap_C": _num(r, "T_ref_evap_sat [°C]"),
        "T_cond_C": _num(r, "T_ref_cond_sat_v [°C]"),
        "T_dis_C": _num(r, "T_ref_cmp_out [°C]"),
        "coefficient_version": COEFFICIENT_VERSION,
    }


# ---------------------------------------------------------------------------
# task construction
# ---------------------------------------------------------------------------
def _variant_name(f_ref: float, m_exp: float) -> str:
    return "baseline" if f_ref <= 0 else f"f{f_ref:.2f}_m{m_exp:.1f}"


def _tasks(pass_name: str, f_ref: float, m_exp: float, rated: dict[str, float] | None) -> list[dict]:
    out = []
    for duty, cap, ref, t_out, t_room in CASES:
        for f in FRACTIONS:
            out.append(
                {
                    "pass": pass_name,
                    "variant": _variant_name(f_ref, m_exp),
                    "f_ref": f_ref,
                    "m_exp": m_exp,
                    "duty": duty,
                    "capacity_W": cap,
                    "refrigerant": ref,
                    "t_outdoor_C": t_out,
                    "t_sink_C": t_room,
                    "plr_request": f,
                    "m_dot_rated": (rated or {}).get(duty),
                }
            )
    return out


def _run(tasks: list[dict], jobs: int, patched: bool) -> pd.DataFrame:
    with ProcessPoolExecutor(max_workers=jobs, initializer=_init, initargs=(patched,)) as ex:
        return pd.DataFrame(list(ex.map(run_point, tasks, chunksize=2)))


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("SIM_JOBS", "24")))
    a = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- pass 1: untouched library -------------------------------------
    ref_df = _run(_tasks("unpatched", 0.0, M_MAIN, None), a.jobs, patched=False)
    ref_df["variant"] = "unpatched"
    print(f"unpatched: {len(ref_df)} rows, failures {(ref_df.failure_reason != 'none').sum()}")

    # --- pass 2: patched, f_ref = 0 ------------------------------------
    base_df = _run(_tasks("baseline", 0.0, M_MAIN, None), a.jobs, patched=True)
    print(f"baseline : {len(base_df)} rows, failures {(base_df.failure_reason != 'none').sum()}")

    # --- regression check ----------------------------------------------
    keys = ["duty", "plr_request"]
    cmp_cols = ["cop_sys", "UA_ou", "UA_iu", "m_dot_ref", "T_evap_C", "T_cond_C", "pr", "E_tot"]
    merged = ref_df.set_index(keys)[cmp_cols].join(base_df.set_index(keys)[cmp_cols], lsuffix="_ref", rsuffix="_base")
    worst = {}
    for c in cmp_cols:
        d = (merged[f"{c}_base"] - merged[f"{c}_ref"]).abs()
        scale = merged[f"{c}_ref"].abs().clip(lower=1e-12)
        worst[c] = float((d / scale).max())
    print("regression (f_ref=0 vs unpatched), max relative deviation:")
    for c, v in worst.items():
        print(f"    {c:<12} {v:.3e}")
    if max(worst.values()) > 1e-12:
        raise SystemExit(f"REGRESSION FAILED: f_ref=0 does not reproduce the shipped model ({worst})")

    # --- m_dot_ref,rated from the PLR = 1.0 baseline rows ---------------
    rated = {}
    for duty in base_df.duty.unique():
        row = base_df[(base_df.duty == duty) & (base_df.plr_request == 1.0)].iloc[0]
        if row.failure_reason != "none":
            raise SystemExit(f"{duty}: PLR = 1.0 baseline point did not converge; cannot define m_dot_ref,rated")
        rated[duty] = float(row.m_dot_ref)
    print("m_dot_ref,rated [kg/s]: " + ", ".join(f"{k} {v:.6f}" for k, v in rated.items()))

    base_df["m_dot_ref_rated"] = base_df.duty.map(rated)
    base_df["x_ref"] = base_df.m_dot_ref / base_df.m_dot_ref_rated
    ref_df["m_dot_ref_rated"] = ref_df.duty.map(rated)
    ref_df["x_ref"] = ref_df.m_dot_ref / ref_df.m_dot_ref_rated

    # --- pass 3: the (f_ref, m) variants --------------------------------
    combos = {(f, M_MAIN) for f in F_REF_MAIN} | {(f, m) for f in F_REF_GRID for m in M_GRID}
    tasks: list[dict] = []
    for f, m in sorted(combos):
        tasks += _tasks("variant", f, m, rated)
    print(f"variants : {len(combos)} (f_ref, m) combinations, {len(tasks)} points")
    var_df = _run(tasks, a.jobs, patched=True)
    print(f"variants : {len(var_df)} rows, failures {(var_df.failure_reason != 'none').sum()}")

    out = pd.concat([ref_df, base_df, var_df], ignore_index=True)
    out.to_csv(OUT_DIR / CSV_NAME, index=False)

    meta = {
        "m_dot_ref_rated_kg_s": rated,
        "regression_max_rel_dev": worst,
        "n_rows": int(len(out)),
        "n_failures": int((out.failure_reason != "none").sum()),
        "n_speed_floor": int((out.capacity_clamped == "min").sum()),
        "n_fan_floor_ou": int(out.fan_floor_ou.sum()),
        "n_fan_floor_iu": int(out.fan_floor_iu.sum()),
        "fan_fraction_ou_min": float(out.fan_fraction_ou.min()),
        "combos": sorted([list(c) for c in combos]),
        "fractions": list(FRACTIONS),
    }
    (OUT_DIR / "run_manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))
    print(f"wrote {OUT_DIR / CSV_NAME}")


if __name__ == "__main__":
    main()
