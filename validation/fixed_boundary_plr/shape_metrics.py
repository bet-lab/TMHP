"""Gate B of plan v3: shape metrics of the fixed-boundary PLR-COP curves.

For every case of ``ashp_sweep.csv`` / ``ashpb_sweep.csv`` (and any extra
sweep file given on the command line, e.g. the before/after comparison), over
the **continuously modulating** rows only (rows at the speed floor are
excluded -- there the delivered heat, not the request, sets the point):

    PLR_peak, COP_peak        location and value of the COP maximum
    COP_rated                 COP at the highest modulating PLR (1.00 unless clamped)
    COP_low, PLR_low          COP at the lowest modulating PLR
    dCOP_low                  (COP_peak - COP_low) / COP_peak
    slope_low                 dCOP/dPLR over the lowest three modulating points
    plr_min_speed             lowest PLR the compressor can still modulate to
    dn_star, dn_star_rel      change of n* from PLR 1 to PLR_low (absolute, relative)
    dpr, dpr_rel              change of pressure ratio over the same range
    shape                     'monotonic_rise' (COP still rising at the floor),
                              'interior_peak' (maximum strictly inside the range),
                              'plateau' (peak inside but dCOP_low < 2 %)

Writes ``validation/data/fixed_boundary_plr/shape_metrics.csv``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "validation" / "data" / "fixed_boundary_plr"
CASE_KEYS = ["model_class", "duty", "refrigerant", "capacity_W", "t_outdoor_C", "t_sink_C"]


def metrics_for(g: pd.DataFrame) -> dict:
    g = g.sort_values("plr_request")
    mod = g[(g.failure_reason == "none") & g.capacity_clamped.isna()]
    if len(mod) < 3:
        return {"shape": "insufficient", "n_modulating": len(mod)}
    i_peak = int(mod.cop_sys.idxmax())
    top = mod.iloc[-1]
    low = mod.iloc[0]
    peak = mod.loc[i_peak]
    low3 = mod.iloc[:3]
    slope = float(np.polyfit(low3.plr_request, low3.cop_sys, 1)[0])
    dcop_low = float((peak.cop_sys - low.cop_sys) / peak.cop_sys)
    if peak.plr_request <= low.plr_request + 1e-9:
        shape = "monotonic_rise"
    elif dcop_low < 0.02:
        shape = "plateau"
    else:
        shape = "interior_peak"
    floor_rows = g[(g.failure_reason == "none") & (g.capacity_clamped == "min")]
    return {
        "n_modulating": int(len(mod)),
        "PLR_rated": float(top.plr_request),
        "COP_rated": float(top.cop_sys),
        "PLR_peak": float(peak.plr_request),
        "COP_peak": float(peak.cop_sys),
        "PLR_low": float(low.plr_request),
        "COP_low": float(low.cop_sys),
        "dCOP_low": dcop_low,
        "COP_low_over_rated": float(low.cop_sys / top.cop_sys),
        "slope_low_dCOP_dPLR": slope,
        "plr_min_speed": float(low.plr_request),
        "plr_floor_delivered": float(floor_rows.cr_actual.iloc[0]) if len(floor_rows) else float("nan"),
        "n_star_rated": float(top.n_star),
        "n_star_low": float(low.n_star),
        "dn_star": float(low.n_star - top.n_star),
        "dn_star_rel": float((low.n_star - top.n_star) / top.n_star),
        "pr_rated": float(top.pr),
        "pr_low": float(low.pr),
        "dpr": float(low.pr - top.pr),
        "dpr_rel": float((low.pr - top.pr) / top.pr),
        "eta_vol_rated": float(top.eta_vol),
        "eta_vol_low": float(low.eta_vol),
        "eta_isen_rated": float(top.eta_isen),
        "eta_isen_low": float(low.eta_isen),
        "eta_em_rated": float(top.eta_em),
        "eta_em_low": float(low.eta_em),
        "eta_oi_low_over_rated": float((low.eta_isen * low.eta_em) / (top.eta_isen * top.eta_em)),
        "shape": shape,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", default=[str(DATA / "ashpb_sweep.csv"), str(DATA / "ashp_sweep.csv")])
    ap.add_argument("--out", default=str(DATA / "shape_metrics.csv"))
    a = ap.parse_args()
    rows = []
    for f in a.files:
        d = pd.read_csv(f)
        label_col = "variant" if "variant" in d.columns else None
        keys = CASE_KEYS + ([label_col] if label_col else [])
        for key, g in d.groupby(keys, dropna=False):
            rec = dict(zip(keys, key, strict=True))
            rec["file"] = Path(f).name
            rec["coefficient_version"] = g.coefficient_version.iat[0] if "coefficient_version" in g else ""
            rec.update(metrics_for(g))
            rows.append(rec)
    out = pd.DataFrame(rows)
    out.to_csv(a.out, index=False)
    pd.set_option("display.width", 300)
    cols = [
        c
        for c in [
            "model_class",
            "duty",
            "refrigerant",
            "t_outdoor_C",
            "t_sink_C",
            "variant",
            "coefficient_version",
            "shape",
            "PLR_peak",
            "COP_rated",
            "COP_peak",
            "COP_low",
            "dCOP_low",
            "COP_low_over_rated",
            "plr_min_speed",
            "n_star_low",
            "pr_rated",
            "pr_low",
            "eta_oi_low_over_rated",
        ]
        if c in out.columns
    ]
    print(out[cols].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
