"""Run BASE and the six single-efficiency cases over the PLR grid, per duty.

Boundary conditions are the air-to-air fixed-boundary conditions (3.5 kW R32;
heating outdoor 7 °C / room 20 °C, cooling outdoor 35 °C / room 27 °C).
Requested PLR 1.000 → 0.100 in 0.025 steps.

Output (``validation/data/compressor_efficiency_sensitivity_simple/``):

* ``results_<duty>.csv`` -- one row per case × PLR with the columns listed in
  ``COLUMNS`` (plus ``failure_reason``, ``modulating`` and ``eta_clipped``);
* ``parameters_<duty>.json`` -- boundary conditions, the model inputs by their
  canonical attribute names, the BASE constants and the function constants.

Run::

    uv run python3 -m validation.compressor_efficiency_sensitivity_simple.sweep            # both duties
    uv run python3 -m validation.compressor_efficiency_sensitivity_simple.sweep --duty heating
"""

from __future__ import annotations

import argparse
import json
import subprocess

import pandas as pd

from tmhp import AirSourceHeatPump
from tmhp.compressor_efficiency import COEFFICIENT_VERSION
from tmhp.compressor_speed import RATED_POINT_AIR_TO_AIR

from .config import (
    A_N,
    A_P,
    B_V,
    BOUNDARY,
    CAPACITY_W,
    CASE_ORDER,
    CASES,
    ETA_BASE,
    ETA_CLIP,
    N_STAR_C,
    OUT_DIR,
    PLR_GRID,
    PR_C,
    REF,
    REPO_ROOT,
    SHAPE,
    UA_RATED,
)
from .functions import clipped, constant, make_pr_case, make_speed_case

#: model result key -> (CSV column, scale)
STATE_KEYS = {
    "cmp_rpm [rpm]": ("rps", 1 / 60.0),
    "n_star [-]": ("n_star", 1.0),
    "pr_cmp [-]": ("p_r", 1.0),
    "eta_cmp_vol [-]": ("eta_cmp_vol", 1.0),
    "eta_cmp_isen [-]": ("eta_cmp_isen", 1.0),
    "eta_cmp [-]": ("eta_cmp", 1.0),
    "E_cmp [W]": ("E_cmp", 1.0),
    "E_tot [W]": ("E_tot", 1.0),
    "cop_sys [-]": ("cop_sys", 1.0),
    "T_ref_evap_sat [°C]": ("T_evap_C", 1.0),
    "T_ref_cond_sat_v [°C]": ("T_cond_C", 1.0),
    "m_dot_ref [kg/s]": ("m_dot_ref", 1.0),
}
COLUMNS = (
    "case",
    "duty",
    "plr_request",
    "q_request_W",
    "q_delivered_W",
    "capacity_clamped",
    "rps",
    "n_star",
    "p_r",
    "eta_cmp_vol",
    "eta_cmp_isen",
    "eta_cmp",
    "E_cmp",
    "E_tot",
    "cop_sys",
    "T_evap_C",
    "T_cond_C",
    "m_dot_ref",
    "failure_reason",
    "modulating",
    "eta_clipped",
)


def _num(result: dict, key: str) -> float:
    v = result.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float("nan")


def build_model(**efficiencies) -> AirSourceHeatPump:
    """The shared machine; only the efficiency callables differ between cases."""
    return AirSourceHeatPump(
        hp_capacity=CAPACITY_W,
        ref=REF,
        UA_ou_rated=UA_RATED,
        UA_iu_rated=UA_RATED,
        **efficiencies,
    )


def case_kwargs(case: str, rps_rated: float) -> dict:
    kwargs = {k: constant(v) for k, v in ETA_BASE.items()}
    if case == "BASE":
        return kwargs
    eff, driver = CASES[case]
    shape = SHAPE[eff]
    kwargs[eff] = (
        make_speed_case(ETA_BASE[eff], rps_rated, shape) if driver == "n_star" else make_pr_case(ETA_BASE[eff], shape)
    )
    return kwargs


def run_case(case: str, duty: str, model: AirSourceHeatPump) -> pd.DataFrame:
    t0, t_room = BOUNDARY[duty]
    sign = -1.0 if duty == "heating" else 1.0  # air-to-air convention: cooling load positive
    eff, driver = CASES.get(case, (None, None))
    rows = []
    for plr in PLR_GRID:
        r = model.analyze_steady(
            Q_r_iu=sign * CAPACITY_W * plr, T0=t0, T_a_room=t_room, return_dict=True, verbose=False
        )
        assert isinstance(r, dict)
        row: dict = {
            "case": case,
            "duty": duty,
            "plr_request": plr,
            "q_request_W": CAPACITY_W * plr,
            "q_delivered_W": abs(_num(r, "Q_ref_iu [W]")),
            "capacity_clamped": r.get("capacity_clamped"),
            "failure_reason": r.get("failure_reason", "none"),
        }
        for key, (col, scale) in STATE_KEYS.items():
            row[col] = _num(r, key) * scale
        row["modulating"] = row["capacity_clamped"] is None and row["failure_reason"] == "none"
        # True when the reported efficiency is not the bare function: the harness clip engaged here.
        row["eta_clipped"] = bool(eff and clipped(ETA_BASE[eff], row[driver], driver, SHAPE[eff]))
        rows.append(row)
    return pd.DataFrame(rows)[list(COLUMNS)]


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001 -- provenance only
        return "unknown"


def run_duty(duty: str) -> pd.DataFrame:
    frames = []
    probe = build_model()
    rps_rated = probe.rps_rated
    for case in CASE_ORDER:
        df = run_case(case, duty, build_model(**case_kwargs(case, rps_rated)))
        mod = df[df.modulating]
        floor = df[~df.modulating].plr_request.max() if (~df.modulating).any() else float("nan")
        print(
            f"[{duty}] {case:4s} modulating {len(mod):2d}/{len(df)} pts, COP {mod.cop_sys.min():.3f}–{mod.cop_sys.max():.3f}, "
            f"floor from PLR {floor:.3f}, n* {mod.n_star.min():.3f}–{mod.n_star.max():.3f}, "
            f"P_r {mod.p_r.min():.3f}–{mod.p_r.max():.3f}, clipped rows {int(df.eta_clipped.sum())}"
        )
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["coefficient_version"] = COEFFICIENT_VERSION  # provenance of the library state, not used by any case
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_DIR / f"results_{duty}.csv", index=False)

    m = probe
    params = {
        "duty": duty,
        "model_class": "AirSourceHeatPump",
        "boundary": {"T0": BOUNDARY[duty][0], "T_a_room": BOUNDARY[duty][1]},
        "model": {
            "ref": m.ref,
            "hp_capacity": m.hp_capacity,
            "rps_rated": m.rps_rated,
            "rps_min": m.rps_min,
            "rps_max": m.rps_max,
            "V_cmp_ref": m.V_cmp_ref,
            "UA_ou_rated": m.UA_ou_rated,
            "UA_iu_rated": m.UA_iu_rated,
            "dV_ou_fan_a_rated": m.dV_ou_fan_a_rated,
            "dV_iu_fan_a_rated": m.dV_iu_fan_a_rated,
            "dT_approach_bounds": list(m.dT_approach_bounds),
            "PR_cycle_min": m.PR_cycle_min,
            "PR_cycle_max": m.PR_cycle_max,
            "rated_point": RATED_POINT_AIR_TO_AIR._asdict(),
        },
        "eta_base": ETA_BASE,
        "function": {
            "n_star_c": N_STAR_C,
            "a_n": A_N,
            "p_r_c": PR_C,
            "a_p": A_P,
            "b_v": B_V,
            "shape": SHAPE,
            "eta_clip": list(ETA_CLIP),
        },
        "sweep": {"plr_grid": list(PLR_GRID)},
        "clipped_rows": int(out.eta_clipped.sum()),
        "library_coefficient_version": COEFFICIENT_VERSION,
        "git_head": _git_head(),
    }
    (OUT_DIR / f"parameters_{duty}.json").write_text(json.dumps(params, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT_DIR.relative_to(REPO_ROOT)}/results_{duty}.csv and parameters_{duty}.json")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duty", choices=tuple(BOUNDARY), default=None, help="one duty; default both")
    a = ap.parse_args()
    for duty in (a.duty,) if a.duty else tuple(BOUNDARY):
        run_duty(duty)


if __name__ == "__main__":
    main()
