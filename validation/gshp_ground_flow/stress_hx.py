"""Separate undersized-HX feasibility map; does not alter the paper case."""

from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd
from run_study import CONFIG, HERE, GroundSourceHeatPump


@lru_cache(maxsize=1)
def stress_model():
    return GroundSourceHeatPump(**(CONFIG["model"] | {"UA_evap": 400}))


def evaluate(task):
    p, f = task
    row = stress_model().analyze_steady(
        Q_r_iu=-CONFIG["model"]["hp_capacity"] * p, ground_flow_ratio=f, **CONFIG["operating"]
    )
    return dict(plr=p, prescribed_ratio=f, UA_ground_rated_W_K=400, **row)


if __name__ == "__main__":
    tasks = [(p, float(f)) for p in CONFIG["plrs"] for f in np.linspace(0.2, 1.2, 11)]
    with ProcessPoolExecutor(max_workers=4) as pool:
        rows = []
        for row in pool.map(evaluate, tasks):
            rows.append(row)
            print(f"{len(rows)}/{len(tasks)} {row['failure_reason']}", flush=True)
    data = pd.DataFrame(rows)
    assert data.converged.any() and (~data.converged).any()
    assert data.loc[~data.converged, "cop_sys [-]"].isna().all()
    data.to_csv(HERE / "results/hx_stress.csv", index=False)
