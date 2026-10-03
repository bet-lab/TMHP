"""Simulations behind the final validation report (v2026-09-24).

Four datasets, all with the shipped defaults and the frozen coefficients:

``plr_map_ashp.csv``    air-to-air 3.5 kW R32, heating (room 20 degC) at outdoor
                        -15 .. 15 degC and cooling (room 27 degC) at outdoor
                        20 .. 45 degC, requested duty 1.00 -> 0.12 of nameplate
``plr_map_ashpb.csv``   air-to-water 9 kW R32, tank 42.5 degC, outdoor -15 .. 15 degC
``base_cases.csv``      the six fixed-boundary cases of ``sweep.py`` with the fan
                        powers and coil states kept (minimum-speed diagnostic)
``displacement_sensitivity.csv``
                        the same three representative cases with the compressor
                        displacement scaled 0.6x .. 1.4x: lowest deliverable
                        part-load ratio, and full sweeps at 0.8 / 1.0 / 1.2x

Rows at the compressor speed floor are kept and flagged (``capacity_clamped``).

Run::

    uv run python3 -m validation.compressor_maps.final_simulate [--jobs 16]
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler
from tmhp.compressor_efficiency import COEFFICIENT_VERSION
from validation.compressor_maps.schema import REPO_ROOT

OUT_DIR = REPO_ROOT / "validation" / "data" / "final_report"
FRACTIONS = tuple(round(1.0 - 0.04 * i, 2) for i in range(23))  # 1.00 .. 0.12

ASHP_CAP, ASHP_REF = 3500.0, "R32"
ASHPB_CAP, ASHPB_REF = 9000.0, "R32"
ASHP_HEATING = {"room": 20.0, "outdoor": (-15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0)}
ASHP_COOLING = {"room": 27.0, "outdoor": (20.0, 25.0, 30.0, 35.0, 40.0, 45.0)}
ASHPB_MAP = {"tank": 42.5, "outdoor": (-15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0)}

BASE_CASES = (
    # model, capacity, refrigerant, duty, outdoor, sink
    ("ASHPB", 9000.0, "R32", "heating", 7.0, 42.5),
    ("ASHPB", 9000.0, "R410A", "heating", 7.0, 42.5),
    ("ASHPB", 9000.0, "R290", "heating", 7.0, 42.5),
    ("ASHPB", 9000.0, "R32", "heating", -7.0, 32.5),
    ("ASHP", 3500.0, "R32", "heating", 7.0, 20.0),
    ("ASHP", 3500.0, "R32", "cooling", 35.0, 27.0),
)
SENS_CASES = (
    ("ASHP", 3500.0, "R32", "heating", 7.0, 20.0),
    ("ASHP", 3500.0, "R32", "cooling", 35.0, 27.0),
    ("ASHPB", 9000.0, "R32", "heating", 7.0, 42.5),
)
SENS_MULT_FLOOR = tuple(round(0.6 + 0.1 * i, 1) for i in range(9))  # 0.6 .. 1.4
SENS_MULT_SWEEP = (0.8, 1.0, 1.2)

_MODELS: dict = {}


def _model(model_class: str, cap: float, ref: str, disp_mult: float):
    key = (model_class, cap, ref, disp_mult)
    if key not in _MODELS:
        if model_class == "ASHP":
            base = AirSourceHeatPump(hp_capacity=cap, ref=ref)
            m = AirSourceHeatPump(hp_capacity=cap, ref=ref, V_cmp_ref=base.V_cmp_ref * disp_mult)
        else:
            base = AirSourceHeatPumpBoiler(hp_capacity=cap, ref=ref)
            m = AirSourceHeatPumpBoiler(hp_capacity=cap, ref=ref, V_cmp_ref=base.V_cmp_ref * disp_mult)
        _MODELS[key] = m
    return _MODELS[key]


def _num(r: dict, key: str) -> float:
    v = r.get(key)
    return float(v) if isinstance(v, (int, float)) else float("nan")


def run_point(task: dict) -> dict:
    """One steady-state solution; ``task`` is picklable and self-describing."""
    model_class, cap, ref, duty = task["model_class"], task["capacity_W"], task["refrigerant"], task["duty"]
    t_out, t_sink, f, mult = task["t_outdoor_C"], task["t_sink_C"], task["plr_request"], task.get("disp_mult", 1.0)
    m = _model(model_class, cap, ref, mult)
    if model_class == "ASHP":
        sign = -1.0 if duty == "heating" else 1.0
        r = m.analyze_steady(Q_r_iu=sign * cap * f, T0=t_out, T_a_room=t_sink, return_dict=True, verbose=False)
        delivered = abs(_num(r, "Q_ref_iu [W]"))
        fan_rated = m.dV_ou_fan_a_rated
        e_ou_fan, e_iu_fan = _num(r, "E_ou_fan [W]"), _num(r, "E_iu_fan [W]")
        dv_iu = _num(r, "dV_iu_a [m3/s]") / m.dV_iu_fan_a_rated
    else:
        r = m.analyze_steady(T_tank_w=t_sink, T0=t_out, Q_ref_tank=cap * f, return_dict=True)
        delivered = _num(r, "Q_ref_tank [W]")
        fan_rated = m.dV_fan_a_rated
        e_ou_fan, e_iu_fan = _num(r, "E_ou_fan [W]"), 0.0
        dv_iu = float("nan")
    assert isinstance(r, dict)
    # Heat-exchanger closure diagnostics (speed-floor fix plan Sec. 7). The
    # tank-side keys are NaN for the air-to-air model, the indoor-coil keys for
    # the air-to-water one; `epsilon_tank_hx` is NaN in the fixed-UA fallback,
    # which has no water-side capacity rate to define an effectiveness against.
    hx = {
        "UA_ou": _num(r, "UA_ou [W/K]"),
        "NTU_ou": _num(r, "NTU_ou [-]"),
        "epsilon_ou": _num(r, "epsilon_ou [-]"),
        "UA_iu": _num(r, "UA_iu [W/K]"),
        "NTU_iu": _num(r, "NTU_iu [-]"),
        "epsilon_iu": _num(r, "epsilon_iu [-]"),
        "UA_tank_hx": _num(r, "UA_tank_hx [W/K]"),
        "C_w_tank": _num(r, "C_w_tank [W/K]"),
        "epsilon_tank_hx": _num(r, "epsilon_tank_hx [-]"),
        "dT_ref_tank": _num(r, "dT_ref_tank [K]"),
        "tank_hx_residual": _num(r, "tank_hx_residual [W]"),
    }
    return {
        **{k: v for k, v in task.items()},
        "V_disp_cm3": m.V_cmp_ref * 1e6,
        "rps_rated": m.rps_rated,
        "rps_min": m.rps_min,
        "q_request_W": cap * f,
        "q_delivered_W": delivered,
        "plr_delivered": delivered / cap,
        **hx,
        "capacity_clamped": r.get("capacity_clamped"),
        "failure_reason": r.get("failure_reason", "none"),
        "converged": bool(r.get("converged", False)),
        "rps": _num(r, "cmp_rpm [rpm]") / 60.0,
        "n_star": _num(r, "n_star [-]"),
        "pr": _num(r, "pr_cmp [-]"),
        "m_dot_ref": _num(r, "m_dot_ref [kg/s]"),
        "eta_vol": _num(r, "eta_cmp_vol [-]"),
        "eta_isen": _num(r, "eta_cmp_isen [-]"),
        "eta_em": _num(r, "eta_cmp [-]"),
        "E_cmp": _num(r, "E_cmp [W]"),
        "E_ou_fan": e_ou_fan,
        "E_iu_fan": e_iu_fan,
        "E_tot": _num(r, "E_tot [W]"),
        "cop_sys": _num(r, "cop_sys [-]"),
        "cop_cmp": delivered / _num(r, "E_cmp [W]") if _num(r, "E_cmp [W]") > 0 else float("nan"),
        "fan_fraction_ou": _num(r, "dV_ou_a [m3/s]") / fan_rated,
        "fan_fraction_iu": dv_iu,
        "T_evap_C": _num(r, "T_ref_evap_sat [°C]"),
        "T_cond_C": _num(r, "T_ref_cond_sat_v [°C]"),
        "T_dis_C": _num(r, "T_ref_cmp_out [°C]"),
        "coefficient_version": COEFFICIENT_VERSION,
    }


def _tasks_map() -> tuple[list[dict], list[dict]]:
    a2a, a2w = [], []
    for duty, spec in (("heating", ASHP_HEATING), ("cooling", ASHP_COOLING)):
        for t_out in spec["outdoor"]:
            for f in FRACTIONS:
                a2a.append(
                    {
                        "dataset": "plr_map",
                        "model_class": "ASHP",
                        "capacity_W": ASHP_CAP,
                        "refrigerant": ASHP_REF,
                        "duty": duty,
                        "t_outdoor_C": t_out,
                        "t_sink_C": spec["room"],
                        "plr_request": f,
                    }
                )
    for t_out in ASHPB_MAP["outdoor"]:
        for f in FRACTIONS:
            a2w.append(
                {
                    "dataset": "plr_map",
                    "model_class": "ASHPB",
                    "capacity_W": ASHPB_CAP,
                    "refrigerant": ASHPB_REF,
                    "duty": "heating",
                    "t_outdoor_C": t_out,
                    "t_sink_C": ASHPB_MAP["tank"],
                    "plr_request": f,
                }
            )
    return a2a, a2w


def _tasks_base() -> list[dict]:
    out = []
    for mc, cap, ref, duty, t_out, t_sink in BASE_CASES:
        for f in FRACTIONS:
            out.append(
                {
                    "dataset": "base",
                    "model_class": mc,
                    "capacity_W": cap,
                    "refrigerant": ref,
                    "duty": duty,
                    "t_outdoor_C": t_out,
                    "t_sink_C": t_sink,
                    "plr_request": f,
                }
            )
    return out


def _tasks_sens() -> list[dict]:
    out = []
    for mc, cap, ref, duty, t_out, t_sink in SENS_CASES:
        base = {
            "dataset": "sensitivity",
            "model_class": mc,
            "capacity_W": cap,
            "refrigerant": ref,
            "duty": duty,
            "t_outdoor_C": t_out,
            "t_sink_C": t_sink,
        }
        for mult in SENS_MULT_FLOOR:
            out.append({**base, "kind": "floor", "disp_mult": mult, "plr_request": 0.12})
        for mult in SENS_MULT_SWEEP:
            for f in FRACTIONS:
                out.append({**base, "kind": "sweep", "disp_mult": mult, "plr_request": f})
    return out


def _run(tasks: list[dict], jobs: int) -> pd.DataFrame:
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        rows = list(ex.map(run_point, tasks, chunksize=4))
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("SIM_JOBS", "16")))
    ap.add_argument("--only", nargs="*", default=None, choices=["map", "base", "sens"])
    a = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    want = set(a.only or ["map", "base", "sens"])
    if "map" in want:
        a2a, a2w = _tasks_map()
        df = _run(a2a, a.jobs)
        df.to_csv(OUT_DIR / "plr_map_ashp.csv", index=False)
        print(f"plr_map_ashp: {len(df)} rows, failures {(df.failure_reason != 'none').sum()}")
        df = _run(a2w, a.jobs)
        df.to_csv(OUT_DIR / "plr_map_ashpb.csv", index=False)
        print(f"plr_map_ashpb: {len(df)} rows, failures {(df.failure_reason != 'none').sum()}")
    if "base" in want:
        df = _run(_tasks_base(), a.jobs)
        df.to_csv(OUT_DIR / "base_cases.csv", index=False)
        print(f"base_cases: {len(df)} rows, failures {(df.failure_reason != 'none').sum()}")
    if "sens" in want:
        df = _run(_tasks_sens(), a.jobs)
        df.to_csv(OUT_DIR / "displacement_sensitivity.csv", index=False)
        print(f"displacement_sensitivity: {len(df)} rows, failures {(df.failure_reason != 'none').sum()}")
    print(f"wrote {OUT_DIR.relative_to(REPO_ROOT)}/")


if __name__ == "__main__":
    main()
