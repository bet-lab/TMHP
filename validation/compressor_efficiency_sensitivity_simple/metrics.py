"""Summary of the simplified sweep: ΔCOP against BASE at one common low-load point.

Only *modulating* rows count (``capacity_clamped`` is null): below the speed
floor the unit over-delivers and the COP there is not a part-load COP.
``plr_low`` is the lowest PLR at which every case of a duty still modulates, so
the six cases are compared at one operating point.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .config import CASE_ORDER, CASES, DRIVER_TEXT, DUTIES, EFF_TEXT, OUT_DIR

__all__ = ["load", "load_all", "summarise", "common_plr_low"]


def load(duty: str) -> tuple[pd.DataFrame, dict]:
    df = pd.read_csv(OUT_DIR / f"results_{duty}.csv")
    df["modulating"] = df.capacity_clamped.isna() & (df.failure_reason == "none")
    params = json.loads((OUT_DIR / f"parameters_{duty}.json").read_text(encoding="utf-8"))
    return df, params


def load_all() -> tuple[dict[str, pd.DataFrame], dict[str, dict]]:
    frames, params = {}, {}
    for d in DUTIES:
        frames[d], params[d] = load(d)
    return frames, params


def common_plr_low(df: pd.DataFrame) -> float:
    return float(max(g[g.modulating].plr_request.min() for _, g in df.groupby("case")))


def _cop_at(g: pd.DataFrame, plr: float) -> float:
    hit = g[np.isclose(g.plr_request, plr)]
    return float(hit.cop_sys.iloc[0]) if len(hit) else float("nan")


def summarise(df: pd.DataFrame) -> pd.DataFrame:
    """One row per case; ``d_cop_low*`` are against BASE at ``plr_low``."""
    base = df[df.case == "BASE"]
    plr_low = common_plr_low(df)
    cop_base_low = _cop_at(base, plr_low)
    rows = []
    for case in CASE_ORDER:
        g = df[df.case == case]
        mod = g[g.modulating].sort_values("plr_request")
        eff, driver = CASES.get(case, (None, None))
        i_max = mod.cop_sys.idxmax()
        plr_max = float(mod.loc[i_max, "plr_request"])
        cop_low = _cop_at(g, plr_low)
        rows.append(
            {
                "case": case,
                "varied": EFF_TEXT.get(eff, "—"),
                "driver": DRIVER_TEXT.get(driver, "—"),
                "plr_low": plr_low,
                "plr_floor": float(g[~g.modulating].plr_request.max()) if (~g.modulating).any() else float("nan"),
                "cop_100": _cop_at(g, 1.0),
                "cop_low": cop_low,
                "d_cop_low": cop_low - cop_base_low,
                "d_cop_low_pct": (cop_low - cop_base_low) / cop_base_low * 100.0,
                "d_cop_100_pct": (_cop_at(g, 1.0) - _cop_at(base, 1.0)) / _cop_at(base, 1.0) * 100.0,
                "plr_cop_max": plr_max,
                "internal_max": bool(mod.plr_request.min() < plr_max < mod.plr_request.max()),
                "eta_clipped_rows": int(g.eta_clipped.sum()),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    for duty in DUTIES:
        df, _ = load(duty)
        s = summarise(df)
        s.to_csv(OUT_DIR / f"summary_{duty}.csv", index=False)
        with pd.option_context("display.width", 200, "display.float_format", "{:.3f}".format):
            print(f"== {duty} (PLR_low = {s.plr_low.iloc[0]:.3f})")
            print(s.to_string(index=False))
    print(f"wrote {OUT_DIR.name}/summary_*.csv")


if __name__ == "__main__":
    main()
