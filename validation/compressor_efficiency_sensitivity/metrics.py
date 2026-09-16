"""Summary metrics of the sensitivity sweep (one row per duty × case).

All metrics are taken on the *modulating* region only (``capacity_clamped`` is
null): below the speed floor the model over-delivers and the COP there mixes the
floor, over-delivery and the absence of cycling with the efficiency effect.

``plr_low`` is the lowest PLR at which every case of a duty is still modulating,
so the low-load comparison is made at one common operating point.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .sweep import CASE_ORDER, CASES, OUT_DIR

__all__ = ["load", "summarise", "OUT_DIR"]

EFF_LABEL = {"eta_cmp_vol": "η_v", "eta_cmp_isen": "η_is", "eta_cmp": "η_em"}
DRIVER_LABEL = {"n_star": "n*", "pr": "PR"}


def load(duty: str) -> tuple[pd.DataFrame, dict]:
    df = pd.read_csv(OUT_DIR / f"results_{duty}.csv")
    df["modulating"] = df.capacity_clamped.isna() & (df.failure_reason == "none")
    params = json.loads((OUT_DIR / f"parameters_{duty}.json").read_text(encoding="utf-8"))
    return df, params


def common_plr_low(df: pd.DataFrame) -> float:
    """Lowest PLR at which every case is modulating."""
    return float(max(g[g.modulating].plr_request.min() for _, g in df.groupby("case")))


def _cop_at(g: pd.DataFrame, plr: float) -> float:
    hit = g[np.isclose(g.plr_request, plr)]
    return float(hit.cop_sys.iloc[0]) if len(hit) else float("nan")


def summarise(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """One row per case.  ``d_cop_*`` are against C0 at the same requested PLR."""
    c0 = df[df.case == "C0"].set_index("plr_request")
    plr_low = common_plr_low(df)
    eta_ref = params["eta_ref"]
    rows = []
    for case in CASE_ORDER:
        g = df[df.case == case].sort_values("plr_request", ascending=False)
        mod = g[g.modulating]
        eff, driver = CASES.get(case, (None, None))
        # ΔCOP against C0 over the common modulating range
        joined = mod.set_index("plr_request").join(c0[["cop_sys", "modulating"]], rsuffix="_c0")
        joined = joined[joined.modulating_c0]
        d_pct = (joined.cop_sys - joined.cop_sys_c0) / joined.cop_sys_c0 * 100.0
        # internal maximum: argmax strictly inside the case's own modulating range
        i_max = mod.cop_sys.idxmax()
        plr_max = float(mod.loc[i_max, "plr_request"])
        internal = bool(mod.plr_request.min() < plr_max < mod.plr_request.max())
        cop_low, cop_low_c0 = _cop_at(g, plr_low), _cop_at(c0.reset_index(), plr_low)
        row = {
            "case": case,
            "driver": DRIVER_LABEL.get(driver, "—"),
            "varied": EFF_LABEL.get(eff, "—"),
            "plr_low": plr_low,
            "plr_floor": float(g[~g.modulating].plr_request.max()) if (~g.modulating).any() else float("nan"),
            "cop_100": _cop_at(g, 1.0),
            "cop_50": _cop_at(g, 0.5),
            "cop_low": cop_low,
            "d_cop_low": cop_low - cop_low_c0,
            "d_cop_low_pct": (cop_low - cop_low_c0) / cop_low_c0 * 100.0,
            "d_cop_max_abs_pct": float(d_pct.abs().max()) if len(d_pct) else float("nan"),
            "plr_cop_max": plr_max,
            "cop_max": float(mod.cop_sys.max()),
            "internal_max": internal,
            "cop_range": float(mod.cop_sys.max() - mod.cop_sys.min()),
        }
        if eff is not None:
            eta_low = float(g[np.isclose(g.plr_request, plr_low)][eff].iloc[0])
            d_eta_rel = (eta_low - eta_ref[eff]) / eta_ref[eff]
            row["d_eta_low_pct"] = d_eta_rel * 100.0
            row["S_low"] = (row["d_cop_low_pct"] / 100.0) / d_eta_rel if abs(d_eta_rel) > 1e-9 else float("nan")
        else:
            row["d_eta_low_pct"] = float("nan")
            row["S_low"] = float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    for duty in ("heating", "cooling"):
        df, params = load(duty)
        s = summarise(df, params)
        s.to_csv(OUT_DIR / f"summary_{duty}.csv", index=False)
        with pd.option_context("display.width", 200, "display.float_format", "{:.3f}".format):
            print(f"== {duty} (PLR_low = {s.plr_low.iloc[0]:.3f})")
            print(s.to_string(index=False))
    print(f"wrote {Path(OUT_DIR).name}/summary_*.csv")


if __name__ == "__main__":
    main()
