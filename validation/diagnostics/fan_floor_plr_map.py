"""PLR-COP map by outdoor temperature, with and without a fan power floor.

The fixed-power "after" map (``professor_summary_figures.p1_before_after``) is
post-processing: adding ``W`` to the total does not move the solved state, so
the map is re-divided rather than re-run. **The fan power floor is not like
that.** ``P = P_min + (P_rated - P_min) f_VSD(x)`` sits inside the objective the
approach temperatures minimise, so raising ``P_min`` moves the airflow (up to
~26 % at ``P_min`` = 30 %), the coil ``UA``, the saturation temperatures and the
compressor power with it. The map therefore has to be solved again.

The point solver, the shims and the configs are imported from
``fan_model_sensitivity_once`` so this stays the same experiment, only over the
outdoor-temperature grid of ``final_simulate`` instead of its two conditions.
``baseline`` is re-run through the same shimmed path rather than read from
``plr_map_ashp.csv``, so the two panels of a figure differ by the floor alone;
``--check`` compares the two anyway.

Run::

    uv run python3 -m validation.diagnostics.fan_floor_plr_map [--jobs 16]
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from validation.compressor_maps.final_simulate import (
    ASHP_COOLING,
    ASHP_HEATING,
    FRACTIONS,
)
from validation.compressor_maps.schema import REPO_ROOT
from validation.diagnostics.fan_model_sensitivity_once import run_point

OUT = REPO_ROOT / "validation" / "data" / "professor_summary"
CSV = OUT / "fan_floor_plr_map.csv"
SIM = REPO_ROOT / "validation" / "data" / "final_report"

CONFIGS_RUN = ("baseline", "pmin_10", "pmin_20", "pmin_30")
DUTIES = (("heating", ASHP_HEATING), ("cooling", ASHP_COOLING))


def tasks() -> list[dict]:
    out = []
    for cfg in CONFIGS_RUN:
        for duty, bc in DUTIES:
            for t_out in bc["outdoor"]:
                for f in FRACTIONS:
                    out.append({
                        "config": cfg,
                        "family": "fan_floor_map",
                        "condition": f"{duty}_{t_out:g}",
                        "duty": duty,
                        "T_outdoor": float(t_out),
                        "T_room": float(bc["room"]),
                        "plr_request": float(f),
                    })
    return out


def check(df: pd.DataFrame) -> None:
    """Baseline against the shipped map: same boundary conditions, same answer."""
    ref = pd.read_csv(SIM / "plr_map_ashp.csv")
    ref = ref[ref.failure_reason == "none"]
    base = df[(df.config == "baseline") & (df.failure_reason == "none")]
    j = base.merge(
        ref, left_on=["duty", "T_outdoor", "PLR_request"],
        right_on=["duty", "t_outdoor_C", "plr_request"], suffixes=("", "_ref"),
    )
    d = (j.COP_sys - j.cop_sys).abs() / j.cop_sys * 100
    print(f"baseline vs plr_map_ashp: n = {len(j)}, max |dCOP| = {d.max():.3f} %, mean {d.mean():.3f} %")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=min(16, os.cpu_count() or 4))
    ap.add_argument("--check", action="store_true", help="only re-check an existing CSV")
    args = ap.parse_args()

    if args.check:
        check(pd.read_csv(CSV))
        return

    t = tasks()
    print(f"{len(t)} points on {args.jobs} workers", flush=True)
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        rows = list(ex.map(run_point, t, chunksize=4))
    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(CSV, index=False)
    print(f"-> {CSV}  ({len(df)} rows, {int((df.failure_reason != 'none').sum())} failed)")
    check(df)


if __name__ == "__main__":
    main()
