"""Refrigerant-only PLR-COP sensitivity sweep (plan v2, Sec. 2-3, 5).

Three fixed-boundary cases, the same three the validation page uses::

    ASHP  heating   3.5 kW, outdoor  7 degC, room  20 degC
    ASHP  cooling   3.5 kW, outdoor 35 degC, room  27 degC
    ASHPB heating   9   kW, outdoor  7 degC, tank  42.5 degC

Requested duty runs 1.00 -> 0.12 of nameplate in 0.04 steps, exactly as the
production harness does.  Rows at the compressor speed floor
(``capacity_clamped == "min"``) are kept in the CSV and flagged; the verdicts
use the continuously modulating rows only.

The sensitivity axis is the refrigerant, nothing else.  The compressor
efficiency laws and all their coefficients, ``rps_min`` / ``rps_rated`` /
``rps_max``, the coil UA law, airflow and fan power laws, superheat,
subcooling, the boundary temperatures, the solver settings and the PLR
definition all stay as shipped.

Passes
------
1. ``fixed_disp``  the plan's primary comparison: every refrigerant runs in
                   the *same machine*, i.e. the displacement the library
                   derives for R32 on that case is handed to all of them via
                   ``V_cmp_ref``.  Refrigerant and compressor size are then
                   not confounded -- but the machine is no longer sized for
                   the fluid, so a low volumetric-capacity fluid has to spin
                   proportionally faster and the whole sweep slides along the
                   ``n*`` axis of the efficiency surface.
2. ``own_disp``    the control the plan's own closing question needs ("is the
                   difference ``r_p`` or ``n*``?"): each refrigerant gets the
                   displacement the library would derive for it, so every
                   fluid starts from roughly the same rated ``n*`` and only
                   the cycle state differs.  Contrast 1 vs 2 separates the
                   speed-shift from the thermophysics.

Pass 1 with ``R32`` must reproduce a default-constructed model bit for bit;
that regression gate runs before anything is written.

Run::

    uv run python3 -m validation.refrigerant_only_plr_cop.simulate [--jobs 16]
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "results" / "refrigerant_only_plr_cop"
CSV_NAME = "refrigerant_plr_sensitivity.csv"
METRICS_NAME = "refrigerant_plr_metrics.csv"

FRACTIONS = tuple(round(1.0 - 0.04 * i, 2) for i in range(23))  # 1.00 .. 0.12

#: ``(case, model class, capacity W, duty, outdoor degC, sink degC)``.
CASES = (
    ("ASHP_heating", "ASHP", 3500.0, "heating", 7.0, 20.0),
    ("ASHP_cooling", "ASHP", 3500.0, "cooling", 35.0, 27.0),
    ("ASHPB_heating", "ASHPB", 9000.0, "heating", 7.0, 42.5),
)
CASE_LABEL = {
    "ASHP_heating": "ASHP heating, 7 °C outdoor / 20 °C room, 3.5 kW",
    "ASHP_cooling": "ASHP cooling, 35 °C outdoor / 27 °C room, 3.5 kW",
    "ASHPB_heating": "ASHPB heating, 7 °C outdoor / 42.5 °C tank, 9 kW",
}
CASE_TAG = {"ASHP_heating": "H", "ASHP_cooling": "C", "ASHPB_heating": "B"}

#: Plan Sec. 2.  The first three are the headline comparison, the rest are the
#: "if they compute" extension; every one of them was probed at the rating
#: point before being listed here.
REFRIGERANTS = ("R32", "R410A", "R290", "R407C", "R134a", "R22")
PRIMARY_REFRIGERANTS = ("R32", "R410A", "R290")
REFERENCE_REFRIGERANT = "R32"

#: Compressor-map fitting domain, for the extrapolation hatch of Figure 5.
FIT_DOMAIN_N_STAR = (0.27, 2.0)
FIT_DOMAIN_PR = (1.5, 7.3)

#: Fan-flow lower bound hard-coded in the HX routines.
FAN_FLOOR = 0.05

_MODELS: dict = {}


# ---------------------------------------------------------------------------
# model construction
# ---------------------------------------------------------------------------
def _rated_point(model_class: str):
    from tmhp.compressor_speed import RATED_POINT_AIR_TO_AIR, RATED_POINT_AIR_TO_WATER

    return RATED_POINT_AIR_TO_AIR if model_class == "ASHP" else RATED_POINT_AIR_TO_WATER


def displacement(model_class: str, capacity: float, ref: str) -> float:
    """Displacement the library derives for ``ref`` on this equipment class."""
    from tmhp.compressor_speed import default_displacement

    return float(default_displacement(capacity, ref, _rated_point(model_class)))


def _model(model_class: str, cap: float, ref: str, v_cmp: float | None):
    """Model cached per ``(class, capacity, refrigerant, displacement)``.

    ``v_cmp is None`` constructs with no displacement argument at all, which
    is the regression reference: it has to take the same code path the
    shipped library takes, not merely agree numerically.
    """
    from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler

    key = (model_class, cap, ref, v_cmp)
    if key not in _MODELS:
        cls = AirSourceHeatPump if model_class == "ASHP" else AirSourceHeatPumpBoiler
        kw = {} if v_cmp is None else {"V_cmp_ref": v_cmp}
        _MODELS[key] = cls(hp_capacity=cap, ref=ref, **kw)
    return _MODELS[key]


def _num(r: dict, key: str) -> float:
    v = r.get(key)
    return float(v) if isinstance(v, (int, float)) else float("nan")


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------
def run_point(task: dict) -> dict:
    from tmhp.compressor_efficiency import COEFFICIENT_VERSION

    ashpb = task["model_class"] == "ASHPB"
    heating = task["duty"] == "heating"
    m = _model(task["model_class"], task["capacity_W"], task["refrigerant"], task["V_cmp_ref"])
    q_req = task["capacity_W"] * task["plr_request"]

    if ashpb:
        r = m.analyze_steady(T_tank_w=task["t_sink_C"], T0=task["t_outdoor_C"], Q_ref_tank=q_req, return_dict=True)
        delivered = abs(_num(r, "Q_ref_tank [W]"))
        e_fan = _num(r, "E_ou_fan [W]")
        e_iu_fan = float("nan")
        fan_ou = _num(r, "dV_ou_a [m3/s]") / m.dV_fan_a_rated
        fan_iu = float("nan")
        fan_floor_ou = bool(r.get("fan_flow_min_limit", False)) or fan_ou <= FAN_FLOOR * 1.01
        fan_floor_iu = False
    else:
        sign = -1.0 if heating else 1.0
        r = m.analyze_steady(
            Q_r_iu=sign * q_req,
            T0=task["t_outdoor_C"],
            T_a_room=task["t_sink_C"],
            return_dict=True,
            verbose=False,
        )
        delivered = abs(_num(r, "Q_ref_iu [W]"))
        e_iu_fan = _num(r, "E_iu_fan [W]")
        e_fan = _num(r, "E_ou_fan [W]") + e_iu_fan
        fan_ou = _num(r, "dV_ou_a [m3/s]") / m.dV_ou_fan_a_rated
        fan_iu = _num(r, "dV_iu_a [m3/s]") / m.dV_iu_fan_a_rated
        fan_floor_ou = bool(r.get("ou_fan_flow_min_limit", False)) or fan_ou <= FAN_FLOOR * 1.01
        fan_floor_iu = bool(r.get("iu_fan_flow_min_limit", False)) or fan_iu <= FAN_FLOOR * 1.01
    assert isinstance(r, dict)

    p_suc, p_dis = _num(r, "P_ref_cmp_in [Pa]"), _num(r, "P_ref_cmp_out [Pa]")
    e_cmp, e_tot = _num(r, "E_cmp [W]"), _num(r, "E_tot [W]")
    t_evap, t_cond = _num(r, "T_ref_evap_sat [°C]"), _num(r, "T_ref_cond_sat_v [°C]")
    return {
        "pass": task["pass"],
        "case": task["case"],
        "model_class": task["model_class"],
        "duty": task["duty"],
        "refrigerant": task["refrigerant"],
        "capacity_W": task["capacity_W"],
        "t_outdoor_C": task["t_outdoor_C"],
        "t_sink_C": task["t_sink_C"],
        "V_cmp_ref_m3": float(m.V_cmp_ref),
        "V_cmp_ref_cm3": float(m.V_cmp_ref) * 1e6,
        "PLR_requested": task["plr_request"],
        "Q_requested": q_req,
        "Q_delivered": delivered,
        "PLR_delivered": delivered / task["capacity_W"],
        "COP_comp": _num(r, "cop_ref [-]"),
        "COP_sys": _num(r, "cop_sys [-]"),
        "W_comp": e_cmp,
        "W_fan": e_fan,
        "W_ou_fan": _num(r, "E_ou_fan [W]"),
        "W_iu_fan": e_iu_fan,
        "W_tot": e_tot,
        "fan_fraction": e_fan / e_tot if e_tot else float("nan"),
        "N": _num(r, "cmp_rpm [rpm]") / 60.0,
        "n_star": _num(r, "n_star [-]"),
        "rps_min": float(m.rps_min),
        "rps_rated": float(m.rps_rated),
        "rps_max": float(m.rps_max),
        "p_suc": p_suc,
        "p_dis": p_dis,
        "r_p": _num(r, "pr_cmp [-]"),
        "T_evap_sat": t_evap,
        "T_cond_sat": t_cond,
        "T_dis": _num(r, "T_ref_cmp_out [°C]"),
        "lift_K": t_cond - t_evap,
        "m_ref": _num(r, "m_dot_ref [kg/s]"),
        "rho_suc": _num(r, "rho_ref_cmp_in [kg/m3]"),
        "eta_v": _num(r, "eta_cmp_vol [-]"),
        "eta_is": _num(r, "eta_cmp_isen [-]"),
        "eta_em": _num(r, "eta_cmp [-]"),
        "UA_ou": _num(r, "UA_ou [W/K]"),
        "UA_iu": _num(r, "UA_iu [W/K]"),
        "air_flow_fraction_ou": fan_ou,
        "air_flow_fraction_iu": fan_iu,
        "fan_floor_ou": bool(fan_floor_ou),
        "fan_floor_iu": bool(fan_floor_iu),
        "speed_bound": r.get("capacity_clamped"),
        "converged": bool(r.get("converged", False)),
        "converged_rps": bool(r.get("converged_rps", False)),
        "failure_reason": r.get("failure_reason", "none"),
        "coefficient_version": COEFFICIENT_VERSION,
    }


# ---------------------------------------------------------------------------
# task construction
# ---------------------------------------------------------------------------
def _tasks(pass_name: str, disp: dict[tuple[str, str], float | None]) -> list[dict]:
    out = []
    for case, klass, cap, duty, t_out, t_sink in CASES:
        for ref in REFRIGERANTS:
            if (case, ref) not in disp:
                continue
            for f in FRACTIONS:
                out.append(
                    {
                        "pass": pass_name,
                        "case": case,
                        "model_class": klass,
                        "capacity_W": cap,
                        "duty": duty,
                        "t_outdoor_C": t_out,
                        "t_sink_C": t_sink,
                        "refrigerant": ref,
                        "V_cmp_ref": disp[(case, ref)],
                        "plr_request": f,
                    }
                )
    return out


def _run(tasks: list[dict], jobs: int) -> pd.DataFrame:
    if not tasks:
        return pd.DataFrame()
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        return pd.DataFrame(list(ex.map(run_point, tasks, chunksize=2)))


# ---------------------------------------------------------------------------
# summary metrics (plan Sec. 5, second file)
# ---------------------------------------------------------------------------
def metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Per (pass, case, refrigerant) shape summary.

    "Lowest modulating PLR" is the smallest requested PLR whose row still
    modulates continuously -- ``speed_bound`` unset and the solver converged.
    Rows at the speed floor deliver more than was asked and carry no shape
    verdict, so they are excluded from every ratio here (they stay in the raw
    CSV and are drawn with open markers).
    """
    rows = []
    for (pass_name, case, ref), g in df.groupby(["pass", "case", "refrigerant"], sort=False):
        ok = g[(g.failure_reason == "none") & g.converged]
        mod = ok[ok.speed_bound.isna()].sort_values("PLR_requested")
        if mod.empty:
            continue
        rated = mod[mod.PLR_requested == mod.PLR_requested.max()].iloc[0]
        low = mod.iloc[0]
        floor = ok[ok.speed_bound == "min"]
        rows.append(
            {
                "pass": pass_name,
                "case": case,
                "refrigerant": ref,
                "V_cmp_ref_cm3": float(rated.V_cmp_ref_cm3),
                "PLR_rated": float(rated.PLR_requested),
                "PLR_low": float(low.PLR_requested),
                "COP_comp_rated": float(rated.COP_comp),
                "COP_sys_rated": float(rated.COP_sys),
                "COP_comp_low": float(low.COP_comp),
                "COP_sys_low": float(low.COP_sys),
                "R_low_comp": float(low.COP_comp / rated.COP_comp),
                "R_low_sys": float(low.COP_sys / rated.COP_sys),
                "PLR_peak_comp": float(mod.loc[mod.COP_comp.idxmax(), "PLR_requested"]),
                "PLR_peak_sys": float(mod.loc[mod.COP_sys.idxmax(), "PLR_requested"]),
                "COP_comp_peak": float(mod.COP_comp.max()),
                "COP_sys_peak": float(mod.COP_sys.max()),
                "fan_fraction_rated": float(rated.fan_fraction),
                "fan_fraction_low": float(low.fan_fraction),
                "n_star_rated": float(rated.n_star),
                "n_star_low": float(low.n_star),
                "r_p_rated": float(rated.r_p),
                "r_p_low": float(low.r_p),
                "eta_is_rated": float(rated.eta_is),
                "eta_is_low": float(low.eta_is),
                "eta_em_rated": float(rated.eta_em),
                "eta_em_low": float(low.eta_em),
                "eta_v_rated": float(rated.eta_v),
                "eta_v_low": float(low.eta_v),
                "n_modulating": int(len(mod)),
                "n_speed_floor": int(len(floor)),
                "n_failed": int(len(g) - len(ok)),
            }
        )
    return _add_common_plr(pd.DataFrame(rows), df)


def _add_common_plr(met: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Add the ratio read at a PLR every refrigerant still modulates at.

    ``R_low_*`` above is read at each refrigerant's *own* lowest modulating
    PLR, and those differ -- a fluid forced to spin faster reaches the speed
    floor later and so gets credited with a deeper part-load point.  Comparing
    those numbers across refrigerants compares different operating points.
    ``PLR_common`` is the shallowest of the per-refrigerant floors within one
    (pass, case), i.e. the deepest PLR they all still modulate at, and
    ``R_common_*`` is every refrigerant's ratio read there.
    """
    if met.empty:
        return met
    out = []
    for (pass_name, case), g in met.groupby(["pass", "case"], sort=False):
        plr_common = float(g.PLR_low.max())
        sub = df[(df["pass"] == pass_name) & (df.case == case) & (df.PLR_requested == plr_common)]
        sub = sub.set_index("refrigerant")
        g = g.copy()
        g["PLR_common"] = plr_common
        for col, src in (("COP_comp_common", "COP_comp"), ("COP_sys_common", "COP_sys"),
                         ("n_star_common", "n_star"), ("r_p_common", "r_p"),
                         ("fan_fraction_common", "fan_fraction")):
            g[col] = g.refrigerant.map(sub[src])
        g["R_common_comp"] = g.COP_comp_common / g.COP_comp_rated
        g["R_common_sys"] = g.COP_sys_common / g.COP_sys_rated
        out.append(g)
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("SIM_JOBS", "16")))
    ap.add_argument("--metrics-only", action="store_true", help="recompute the summary from an existing raw CSV")
    a = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if a.metrics_only:
        out = pd.read_csv(OUT_DIR / CSV_NAME)
        met = metrics(out)
        met.to_csv(OUT_DIR / METRICS_NAME, index=False)
        print(f"rewrote {OUT_DIR / METRICS_NAME} from {len(out)} rows")
        return

    # --- displacement tables -------------------------------------------
    fixed: dict[tuple[str, str], float | None] = {}
    own: dict[tuple[str, str], float | None] = {}
    own_skipped: dict[str, str] = {}
    ref_disp: dict[str, float] = {}
    for case, klass, cap, *_ in CASES:
        v_ref = displacement(klass, cap, REFERENCE_REFRIGERANT)
        ref_disp[case] = v_ref
        for ref in REFRIGERANTS:
            fixed[(case, ref)] = v_ref
            try:
                own[(case, ref)] = displacement(klass, cap, ref)
            except Exception as exc:  # CoolProp cannot rate every pseudo-pure blend
                own_skipped[f"{case}/{ref}"] = f"{type(exc).__name__}: {exc}"
    print("displacement [cm3/rev]")
    for case, *_ in CASES:
        shown = ", ".join(
            f"{r}={own[(case, r)] * 1e6:.2f}" if (case, r) in own else f"{r}=n/a" for r in REFRIGERANTS
        )
        print(f"  {case:<14} R32(fixed)={ref_disp[case] * 1e6:6.2f} | own: {shown}")
    if own_skipped:
        print("  own_disp skipped:", json.dumps(own_skipped, ensure_ascii=False))

    # --- regression gate: R32 with explicit displacement == library default
    gate_tasks = []
    for case, klass, cap, duty, t_out, t_sink in CASES:
        base = {
            "case": case,
            "model_class": klass,
            "capacity_W": cap,
            "duty": duty,
            "t_outdoor_C": t_out,
            "t_sink_C": t_sink,
            "refrigerant": REFERENCE_REFRIGERANT,
        }
        for f in (1.0, 0.6, 0.2):
            gate_tasks.append({**base, "pass": "gate_default", "V_cmp_ref": None, "plr_request": f})
            gate_tasks.append({**base, "pass": "gate_explicit", "V_cmp_ref": ref_disp[case], "plr_request": f})
    gate = _run(gate_tasks, a.jobs)
    cols = ["COP_comp", "COP_sys", "W_tot", "n_star", "r_p", "T_evap_sat", "T_cond_sat", "m_ref"]
    g0 = gate[gate["pass"] == "gate_default"].set_index(["case", "PLR_requested"])[cols]
    g1 = gate[gate["pass"] == "gate_explicit"].set_index(["case", "PLR_requested"])[cols]
    worst = {c: float(((g1[c] - g0[c]).abs() / g0[c].abs().clip(lower=1e-12)).max()) for c in cols}
    print("regression (explicit R32 displacement vs library default), max relative deviation:")
    for c, v in worst.items():
        print(f"    {c:<12} {v:.3e}")
    if max(worst.values()) > 1e-12:
        raise SystemExit(f"REGRESSION FAILED: explicit displacement changes the shipped R32 model ({worst})")

    # --- pass 1: plan's primary comparison ------------------------------
    df_fixed = _run(_tasks("fixed_disp", fixed), a.jobs)
    print(f"fixed_disp: {len(df_fixed)} rows, failures {(df_fixed.failure_reason != 'none').sum()}")

    # --- pass 2: control, each fluid in a machine sized for it -----------
    df_own = _run(_tasks("own_disp", own), a.jobs)
    print(f"own_disp  : {len(df_own)} rows, failures {(df_own.failure_reason != 'none').sum()}")

    out = pd.concat([df_fixed, df_own], ignore_index=True)
    out.to_csv(OUT_DIR / CSV_NAME, index=False)
    met = metrics(out)
    met.to_csv(OUT_DIR / METRICS_NAME, index=False)

    meta = {
        "cases": {c[0]: CASE_LABEL[c[0]] for c in CASES},
        "refrigerants": list(REFRIGERANTS),
        "reference_refrigerant": REFERENCE_REFRIGERANT,
        "fractions": list(FRACTIONS),
        "fixed_displacement_cm3": {c: ref_disp[c] * 1e6 for c in ref_disp},
        "own_displacement_cm3": {f"{c}/{r}": v * 1e6 for (c, r), v in own.items()},
        "own_displacement_skipped": own_skipped,
        "regression_max_rel_dev": worst,
        "n_rows": int(len(out)),
        "n_failures": int((out.failure_reason != "none").sum()),
        "n_speed_floor": int((out.speed_bound == "min").sum()),
        "n_fan_floor_ou": int(out.fan_floor_ou.sum()),
        "fit_domain_n_star": list(FIT_DOMAIN_N_STAR),
        "fit_domain_pr": list(FIT_DOMAIN_PR),
        "coefficient_version": str(out.coefficient_version.iloc[0]),
    }
    (OUT_DIR / "run_manifest.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("n_rows", "n_failures", "n_speed_floor", "coefficient_version")}, indent=2))
    print(f"wrote {OUT_DIR / CSV_NAME}")
    print(f"wrote {OUT_DIR / METRICS_NAME}")


if __name__ == "__main__":
    main()
