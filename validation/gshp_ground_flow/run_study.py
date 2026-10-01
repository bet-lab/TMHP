"""Reproduce the illustrative case and independent physical verification data.

Run with the project's locked environment, from any working directory:
    python validation/gshp_ground_flow/run_study.py --workers 4
"""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from tmhp import GroundSourceHeatPump  # noqa: E402
from tmhp.constants import c_w, k_w, mu_w, rho_w  # noqa: E402
from tmhp.ground_loop import calc_borefield_linear_load, ground_flow_state  # noqa: E402
from tmhp.heat_exchanger import calc_UA_two_stream_scaled  # noqa: E402

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text())


@lru_cache(maxsize=4)
def model(control="constant", depth=100):
    return GroundSourceHeatPump(**(CONFIG["model"] | {"H_b": depth, "ground_flow_control": control}))


def evaluate(task):
    kind, plr, ratio = task
    control = "optimal_power" if kind == "optimal" else "constant"
    hp = model(control)
    row = hp.analyze_steady(
        Q_r_iu=-CONFIG["model"]["hp_capacity"] * plr,
        ground_flow_ratio=ratio,
        **CONFIG["operating"],
    )
    return dict(kind=kind, plr=plr, prescribed_ratio=ratio, **row)


def component_data(out):
    total = CONFIG["model"]["hp_capacity"]
    pd.DataFrame(
        [
            dict(
                n_boreholes=n,
                total_heat_W=total,
                depth_m=100,
                linear_load_W_m=calc_borefield_linear_load(total, n, 100),
            )
            for n in (1, 2, 4, 8, 16)
        ]
    ).to_csv(out / "normalization.csv", index=False)
    components = []
    for depth in CONFIG["depths_m"]:
        hp = model(depth=depth)
        rated = ground_flow_state(hp._ground_settings, 1)
        for f in np.linspace(0.2, 1.2, 101):
            row = ground_flow_state(hp._ground_settings, float(f))
            components.append(
                dict(
                    depth_m=depth,
                    ratio=f,
                    pump_W=row["E_pmp"],
                    pump_ratio=row["E_pmp"] / rated["E_pmp"],
                    Rb_mK_W=row["R_b"],
                    mass_per_borehole_kg_s=row["m_dot_borehole"],
                )
            )
    pd.DataFrame(components).to_csv(out / "components.csv", index=False)
    ua = [
        dict(water_ratio=w, refrigerant_ratio=r, UA_ratio=calc_UA_two_stream_scaled(1, w, 1, r, 1))
        for r in np.linspace(0.2, 1.2, 51)
        for w in np.linspace(0.2, 1.2, 51)
    ]
    pd.DataFrame(ua).to_csv(out / "ua_map.csv", index=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true", help="Reuse points from the same config only")
    args = parser.parse_args()
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    config_hash = hashlib.sha256((HERE / "config.json").read_bytes()).hexdigest()
    previous_hash = out / "config_sha256.txt"
    if args.resume:
        if previous_hash.exists():
            assert previous_hash.read_text().strip() == config_hash, "Cannot resume a different configuration"
        elif (out / "verification.json").exists():
            assert json.loads((out / "verification.json").read_text())["config_sha256"] == config_hash
        else:
            raise ValueError("Resume requires configuration provenance")
    previous_hash.write_text(config_hash + "\n")
    component_data(out)
    grid = CONFIG["flow_grid"]
    tasks = [(kind, p, 1.0 if kind == "constant" else None) for p in CONFIG["plrs"] for kind in ("constant", "optimal")]
    tasks += [
        ("flow_scan", p, float(f)) for p in CONFIG["plrs"] for f in np.linspace(grid["min"], grid["max"], grid["count"])
    ]
    rows = []
    if args.resume and (out / "operating_points.csv").exists():
        rows = pd.read_csv(out / "operating_points.csv").to_dict("records")
        existing = {
            (r["kind"], round(r["plr"], 6), round(r["prescribed_ratio"], 6) if pd.notna(r["prescribed_ratio"]) else -1)
            for r in rows
        }
        tasks = [t for t in tasks if (t[0], round(t[1], 6), round(t[2], 6) if t[2] is not None else -1) not in existing]
    total_tasks = len(rows) + len(tasks)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(evaluate, task): task for task in tasks}
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(
                f"{len(rows)}/{total_tasks} {row['kind']} PLR={row['plr']:.1f} "
                f"f={row.get('ground_flow_ratio', float('nan')):.3f} "
                f"{row['failure_reason']}",
                flush=True,
            )
            pd.DataFrame(rows).sort_values(["kind", "plr", "prescribed_ratio"]).to_csv(
                out / "operating_points.csv", index=False
            )
    df = pd.DataFrame(rows)
    good = df[df.converged]
    check = {
        "hx_max_abs_residual_W": float((good["Q_HX_available [W]"] - good["Q_ref_required [W]"]).abs().max()),
        "temperature_max_abs_residual_K": float(good["ground_temperature_residual [K]"].abs().max()),
        "points": len(df),
        "feasible_points": len(good),
        "infeasible_reasons": df[~df.converged].failure_reason.value_counts().to_dict(),
    }
    baseline = df[df.kind == "constant"].set_index("plr")
    optimal = df[df.kind == "optimal"].set_index("plr")
    assert baseline.converged.all() and optimal.converged.all(), "Paper sweep must not hide infeasible points"
    comparison = pd.DataFrame(
        {
            "constant_power_W": baseline["E_cmp_plus_pmp [W]"],
            "optimal_power_W": optimal["E_cmp_plus_pmp [W]"],
            "constant_COP_system": baseline["cop_sys [-]"],
            "optimal_COP_system": optimal["cop_sys [-]"],
            "optimal_flow_ratio": optimal.ground_flow_ratio,
        }
    )
    comparison["cmp_pump_saving_percent"] = 100 * (1 - comparison.optimal_power_W / comparison.constant_power_W)
    comparison["system_COP_gain_percent"] = 100 * (comparison.optimal_COP_system / comparison.constant_COP_system - 1)
    comparison.to_csv(out / "comparison.csv")
    gaps = []
    for p in CONFIG["objective_plrs"]:
        scan = good[(good.kind == "flow_scan") & (good.plr == p)]
        gap = float(optimal.loc[p, "E_cmp_plus_pmp [W]"] - scan["E_cmp_plus_pmp [W]"].min())
        gaps.append(gap)
    check["optimizer_minus_independent_grid_W"] = gaps
    assert max(gaps) <= 0.5, check
    assert check["hx_max_abs_residual_W"] < 0.1 and check["temperature_max_abs_residual_K"] < 1e-5, check
    check["water_properties"] = dict(rho_kg_m3=rho_w, mu_Pa_s=mu_w, cp_J_kgK=c_w, k_W_mK=k_w)
    check["source_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    check["config_sha256"] = config_hash
    check["python"] = platform.python_version()
    check["packages"] = {
        p: importlib.metadata.version(p) for p in ("numpy", "scipy", "pandas", "CoolProp", "pygfunction")
    }
    (out / "verification.json").write_text(json.dumps(check, indent=2) + "\n")
    print(json.dumps(check, indent=2))


if __name__ == "__main__":
    main()
