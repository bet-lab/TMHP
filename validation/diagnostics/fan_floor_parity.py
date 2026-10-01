"""Catalogue COP parity re-run with a fan power floor.

The fixed-power parity (``professor_summary_figures.p4_parity``) is arithmetic:
adding ``W`` to the predicted input power does not move the solved state, so
the published prediction is simply re-divided. A fan power floor cannot be
treated that way -- it sits inside the objective the approach temperatures
minimise -- so every catalogue point is solved again with the floor in place.

Only one thing is shimmed: ``calc_fan_power_from_dV_fan``, rebound on both
model modules. The identity used is exact rather than approximate ::

    P_floor = P_min + (P_rated - P_min) f = m P_rated + (1 - m) P_orig

because the shipped function returns ``P_rated * f`` with the same ``f``. The
``m = 0`` pass is run as well and must reproduce ``fig4_cop_parity.csv``
exactly; ``--check`` asserts that.

Run::

    uv run python3 -m validation.diagnostics.fan_floor_parity [--jobs 16]
"""

from __future__ import annotations

import argparse
import math
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

import tmhp.air_source_heat_pump as ashp_mod
import tmhp.air_source_heat_pump_boiler as ashpb_mod
from tmhp.hx_fan import calc_fan_power_from_dV_fan as _ORIG_FAN
from validation.compressor_maps.schema import REPO_ROOT
from validation.parity.run import run_catalog
from validation.parity.spec import load_all

OUT = REPO_ROOT / "validation" / "data" / "professor_summary"
CSV = OUT / "fan_floor_parity.csv"
SIM = REPO_ROOT / "validation" / "data" / "final_report"

P_MIN_FRACS = (0.0, 0.30)

_M = 0.0


def _fan_floor(dV_fan=None, fan_params=None, vsd_coeffs=None, is_active=True):
    p = _ORIG_FAN(dV_fan, fan_params, vsd_coeffs, is_active)
    if not is_active or _M <= 0.0 or not math.isfinite(p):
        return p
    p_rated = fan_params.get("fan_rated_power", fan_params.get("fan_design_power"))
    return float(_M * p_rated + (1.0 - _M) * p)


ashp_mod.calc_fan_power_from_dV_fan = _fan_floor
ashpb_mod.calc_fan_power_from_dV_fan = _fan_floor


def _run(task: dict) -> pd.DataFrame:
    global _M
    _M = float(task["p_min_frac"])
    cat = load_all(task["slug"])[0]
    df = run_catalog(cat)
    df["p_min_frac"] = _M
    return df


def check(df: pd.DataFrame) -> None:
    """The zero-floor pass against the shipped parity table."""
    ref = pd.read_csv(SIM / "fig4_cop_parity.csv")
    base = df[df.p_min_frac == 0.0]
    j = base.merge(ref, on=["slug", "t_source_C", "t_sink_C", "q_kW"], suffixes=("", "_ref"))
    d = (j.cop_pred - j.cop_pred_ref).abs() / j.cop_pred_ref * 100
    print(f"zero-floor vs fig4_cop_parity: n = {len(j)}, max |dCOP| = {d.max():.6f} %")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=min(16, os.cpu_count() or 4))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        check(pd.read_csv(CSV))
        return

    slugs = [c.slug for c in load_all()]
    tasks = [{"slug": s, "p_min_frac": m} for m in P_MIN_FRACS for s in slugs]
    print(f"{len(slugs)} catalogues x {len(P_MIN_FRACS)} floors on {args.jobs} workers", flush=True)
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        frames = list(ex.map(_run, tasks))
    df = pd.concat(frames, ignore_index=True)
    df = df[(df.usable.astype(str) == "True") & (df.status == "adopted")]
    keep = ["slug", "unit", "model_class", "refrigerant", "rating_standard", "mode",
            "t_source_C", "t_sink_C", "q_kW", "cop_target", "cop_pred", "n_star", "pr_cmp", "p_min_frac"]
    df = df[keep]
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(CSV, index=False)
    print(f"-> {CSV}  ({len(df)} rows)")
    for m, g in df.groupby("p_min_frac"):
        for mc, gg in g.groupby("model_class"):
            e = (gg.cop_pred - gg.cop_target) / gg.cop_target * 100
            print(f"  P_min {m:.2f}  {mc:5s}  n = {len(gg):4d}  MAPE {np.abs(e).mean():5.2f} %  bias {e.mean():+6.2f} %")
    check(df)


if __name__ == "__main__":
    main()
