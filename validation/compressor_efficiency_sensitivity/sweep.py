"""Run the eight cases (C0, CURRENT, N-V, N-I, N-E, PR-V, PR-I, PR-E) for one duty.

Boundary conditions are the air-to-air fixed-boundary conditions already used by
``validation.fixed_boundary_plr.sweep``: 3.5 kW R32, heating outdoor 7 °C /
room 20 °C, cooling outdoor 35 °C / room 27 °C.  Requested duty runs 1.000 →
0.100 in 0.025 steps.

Order of operations (the anchors come from the model, not from a guess):

1. ``CURRENT`` -- shipped correlations.  The three efficiencies it reports at
   PLR 0.65 (a modulating point, not at the speed floor) become the constant
   levels of ``C0`` and the peaks ``eta_peak`` of the synthetic curves.
2. ``C0`` -- all three constant.  Its modulating rows give the anchors:
   ``x_high`` at PLR 1.00, ``x_opt`` interpolated at PLR 0.60, ``x_low`` at
   the last modulating PLR before ``capacity_clamped == "min"``.
3. Six cases, each varying one efficiency along one driver with the same
   normalised penalty (``d_low = 0.10``, ``d_high = 0.05``).

Output (``validation/data/compressor_efficiency_sensitivity/``):
``results_<duty>.csv`` (one row per case × PLR) and ``parameters_<duty>.json``
(boundary conditions, model inputs by their canonical attribute names,
reference efficiencies, anchors).

Run::

    uv run python3 -m validation.compressor_efficiency_sensitivity.sweep --duty heating
    uv run python3 -m validation.compressor_efficiency_sensitivity.sweep --duty cooling
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from tmhp import AirSourceHeatPump
from tmhp.compressor_efficiency import COEFFICIENT_VERSION, coefficients
from tmhp.compressor_speed import RATED_POINT_AIR_TO_AIR

from .synthetic_efficiencies import CapShape, constant, make_pr_case, make_speed_case

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "data" / "compressor_efficiency_sensitivity"

CAPACITY_W = 3500.0
REF = "R32"
#: duty -> (outdoor air T0 [°C], room air T_a_room [°C]); same as fixed_boundary_plr.sweep.
BOUNDARY = {"heating": (7.0, 20.0), "cooling": (35.0, 27.0)}
PLR_GRID = tuple(float(v) for v in np.round(np.arange(1.0, 0.0999, -0.025), 3))  # 1.000 .. 0.100
PLR_REF = 0.65  # where the reference (peak) efficiencies are read from CURRENT
PLR_OPT = 0.60  # where the synthetic curves peak
D_LOW = 0.10
D_HIGH = 0.05

#: case -> (varied efficiency, driver).  The constructor keyword of each efficiency
#: is the canonical model attribute of the same name.
CASES: dict[str, tuple[str, str]] = {
    "N-V": ("eta_cmp_vol", "n_star"),
    "N-I": ("eta_cmp_isen", "n_star"),
    "N-E": ("eta_cmp", "n_star"),
    "PR-V": ("eta_cmp_vol", "pr"),
    "PR-I": ("eta_cmp_isen", "pr"),
    "PR-E": ("eta_cmp", "pr"),
}
CASE_ORDER = ("C0", "CURRENT", "N-V", "N-I", "N-E", "PR-V", "PR-I", "PR-E")
EFF_KEYS = ("eta_cmp_vol", "eta_cmp_isen", "eta_cmp")

#: result key -> (column, scale)
STATE_KEYS = {
    "cmp_rpm [rpm]": ("rps", 1 / 60.0),
    "n_star [-]": ("n_star", 1.0),
    "pr_cmp [-]": ("pr", 1.0),
    "m_dot_ref [kg/s]": ("m_dot_ref", 1.0),
    "T_ref_evap_sat [°C]": ("T_evap_C", 1.0),
    "T_ref_cond_sat_v [°C]": ("T_cond_C", 1.0),
    "eta_cmp_vol [-]": ("eta_cmp_vol", 1.0),
    "eta_cmp_isen [-]": ("eta_cmp_isen", 1.0),
    "eta_cmp [-]": ("eta_cmp", 1.0),
    "E_cmp [W]": ("E_cmp", 1.0),
    "E_ou_fan [W]": ("E_ou_fan", 1.0),
    "E_iu_fan [W]": ("E_iu_fan", 1.0),
    "E_tot [W]": ("E_tot", 1.0),
    "cop_sys [-]": ("cop_sys", 1.0),
    "dV_ou_a [m3/s]": ("dV_ou_a", 1.0),
}


def _num(result: dict, key: str) -> float:
    v = result.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else float("nan")


def build_model(**efficiencies) -> AirSourceHeatPump:
    """The shared machine; only the efficiency callables differ between cases."""
    return AirSourceHeatPump(hp_capacity=CAPACITY_W, ref=REF, **efficiencies)


def run_case(case: str, duty: str, model: AirSourceHeatPump) -> pd.DataFrame:
    t0, t_room = BOUNDARY[duty]
    sign = -1.0 if duty == "heating" else 1.0
    rows = []
    for plr in PLR_GRID:
        r = model.analyze_steady(
            Q_r_iu=sign * CAPACITY_W * plr, T0=t0, T_a_room=t_room, return_dict=True, verbose=False
        )
        assert isinstance(r, dict)
        delivered = abs(_num(r, "Q_ref_iu [W]"))
        row = {
            "case": case,
            "duty": duty,
            "plr_request": plr,
            "q_request_W": CAPACITY_W * plr,
            "q_delivered_W": delivered,
            "cr_actual": delivered / CAPACITY_W,
            "capacity_clamped": r.get("capacity_clamped"),
            "failure_reason": r.get("failure_reason", "none"),
        }
        for key, (col, scale) in STATE_KEYS.items():
            row[col] = _num(r, key) * scale
        row["E_fan"] = row["E_ou_fan"] + row["E_iu_fan"]
        row["fan_fraction"] = row["dV_ou_a"] / model.dV_ou_fan_a_rated
        rows.append(row)
    df = pd.DataFrame(rows)
    df["modulating"] = df.capacity_clamped.isna() & (df.failure_reason == "none")
    return df


def _at(df: pd.DataFrame, plr: float) -> pd.Series:
    hit = df[np.isclose(df.plr_request, plr)]
    if len(hit) != 1:
        raise RuntimeError(f"PLR {plr} not on the grid")
    return hit.iloc[0]


def anchors_from(control: pd.DataFrame, driver: str) -> CapShape:
    mod = control[control.modulating].sort_values("plr_request")
    if len(mod) < 3:
        raise RuntimeError("control run has too few modulating points to anchor a curve")
    x_high = float(_at(mod, 1.0)[driver])
    x_low = float(mod.iloc[0][driver])  # last modulating point before the floor
    x_opt = float(np.interp(PLR_OPT, mod.plr_request.to_numpy(), mod[driver].to_numpy()))
    lo, hi = sorted((x_low, x_high))
    return CapShape(x_low=lo, x_opt=x_opt, x_high=hi, d_low=D_LOW, d_high=D_HIGH)


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001 -- provenance only
        return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duty", choices=tuple(BOUNDARY), required=True)
    a = ap.parse_args()
    duty = a.duty
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. CURRENT and its reference efficiencies
    current_model = build_model()
    current = run_case("CURRENT", duty, current_model)
    ref_row = _at(current, PLR_REF)
    if not bool(ref_row.modulating):
        raise RuntimeError(f"CURRENT is not modulating at PLR {PLR_REF}; pick another reference point")
    eta_ref = {k: float(ref_row[k]) for k in EFF_KEYS}
    print(f"[{duty}] CURRENT done; eta_ref @ PLR {PLR_REF}: " + ", ".join(f"{k}={v:.4f}" for k, v in eta_ref.items()))

    # 2. C0 and the anchors
    c0 = run_case("C0", duty, build_model(**{k: constant(v) for k, v in eta_ref.items()}))
    shapes = {"n_star": anchors_from(c0, "n_star"), "pr": anchors_from(c0, "pr")}
    for d, s in shapes.items():
        print(f"[{duty}] C0 anchors {d}: low={s.x_low:.4f} opt={s.x_opt:.4f} high={s.x_high:.4f}")

    # 3. six single-efficiency cases
    frames = [c0, current]
    rps_rated = current_model.rps_rated
    for case, (eff, driver) in CASES.items():
        kwargs = {k: constant(v) for k, v in eta_ref.items()}
        if driver == "n_star":
            kwargs[eff] = make_speed_case(eta_ref[eff], shapes["n_star"], rps_rated)
        else:
            kwargs[eff] = make_pr_case(eta_ref[eff], shapes["pr"])
        df = run_case(case, duty, build_model(**kwargs))
        mod = df[df.modulating]
        print(
            f"[{duty}] {case:5s} modulating {len(mod):2d}/{len(df)} pts, "
            f"COP {mod.cop_sys.min():.3f}-{mod.cop_sys.max():.3f}, floor from PLR {df[~df.modulating].plr_request.max():.3f}"
        )
        frames.append(df)

    out = pd.concat(frames, ignore_index=True)
    out["coefficient_version"] = COEFFICIENT_VERSION
    out.to_csv(OUT_DIR / f"results_{duty}.csv", index=False)

    m = current_model
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
            "rated_point": RATED_POINT_AIR_TO_AIR._asdict(),
        },
        "sweep": {"plr_grid": list(PLR_GRID), "plr_ref": PLR_REF, "plr_opt": PLR_OPT, "d_low": D_LOW, "d_high": D_HIGH},
        "eta_ref": eta_ref,
        "anchors": {d: s.to_dict() for d, s in shapes.items()},
        "current_coefficients": coefficients(),
        "git_head": _git_head(),
    }
    (OUT_DIR / f"parameters_{duty}.json").write_text(json.dumps(params, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT_DIR.relative_to(REPO_ROOT)}/results_{duty}.csv and parameters_{duty}.json")


if __name__ == "__main__":
    main()
