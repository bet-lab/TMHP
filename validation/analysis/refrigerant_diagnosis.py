"""Why one refrigerant looks badly predicted, and why that reading is wrong.

The air-to-air parity panel shows R410A sitting well above the diagonal --
33 % mean error against 9 % for R32 -- which invites the conclusion that the
compressor correlations mishandle that fluid. They do not. The R410A in that
panel is two Fujitsu units and nothing else, and those two are also the only
units in the air-to-air set rated to AHRI 210/240 rather than EN 14511.
Refrigerant, manufacturer and rating standard are one block; the panel cannot
separate them.

This module separates them, three ways:

1. **Remove the confound.** The air-to-water set holds manufacturer and rating
   standard fixed and varies only the fluid. If the correlations were
   fluid-biased the ranking would survive; it inverts.
2. **Ask the model directly.** Build both machines at one matched operating
   point and compare what the model says about each, rather than what two
   different documents declare about them.
3. **Find the lever.** Sweep the one scalar that could carry a machine-level
   offset -- how the nameplate is read -- and watch the bias cross zero while
   an untouched control does not move.

The sweep in step 3 is the expensive part: it re-runs both Fujitsu catalogues
at every multiplier, plus a control. It is cached to CSV so the figure script
does not pay for it.

Run
---
``uv run python -m validation.analysis.refrigerant_diagnosis``
``uv run python -m validation.analysis.refrigerant_diagnosis --reuse``
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

import pandas as pd

from tmhp import AirSourceHeatPump

from ..parity.run import run_catalog
from ..parity.spec import load_all

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "validation" / "data"
OUT_SWEEP = DATA / "refrigerant_nameplate_sweep.csv"
OUT_MATCHED = DATA / "refrigerant_matched_point.csv"

#: Nameplate multipliers applied to the Fujitsu catalogues. Nothing else moves:
#: the requested duties, the temperatures, the declared COP and every
#: coefficient stay exactly as published.
MULTIPLIERS = (1.00, 0.85, 0.75, 0.70, 0.65, 0.60)

#: Catalogues rescaled, and the one held fixed as a control.
RESCALED = ("fujitsu_asuh09lpas", "fujitsu_asuh12lpas")
CONTROL = "daikin_rxm25a"

#: One operating point both documents describe, as close as the two rating
#: standards come to naming the same condition: outdoor 35 degC, indoor around
#: 27 degC, cooling near the nameplate. ``(slug, refrigerant, nameplate W,
#: indoor flow m3/s, room degC, duty W, declared COP)``.
MATCHED = (
    ("Fujitsu ASUH09LPAS", "R410A", 2640.0, 0.1944, 26.7, 2640.0, 3.67),
    ("Daikin RXM25A", "R32", 2500.0, 0.1500, 27.0, 2500.0, 5.21),
)


def matched_point() -> pd.DataFrame:
    """What the model says about each machine at one shared condition."""
    rows = []
    for name, refrigerant, nameplate_w, indoor_flow, t_room, duty_w, declared in MATCHED:
        model = AirSourceHeatPump(hp_capacity=nameplate_w, ref=refrigerant, dV_iu_fan_a_rated=indoor_flow)
        result = model.analyze_steady(Q_r_iu=duty_w, T0=35.0, T_a_room=t_room, return_dict=True, verbose=False)
        assert isinstance(result, dict)
        rows.append(
            {
                "unit": name,
                "refrigerant": refrigerant,
                "nameplate_kW": nameplate_w / 1000.0,
                "declared_cop": declared,
                "predicted_cop": float(result["cop_sys [-]"]),
                "t_evap_C": float(result["T_ref_evap_sat [°C]"]),
                "t_cond_C": float(result["T_ref_cond_sat_l [°C]"]),
                "e_cmp_W": float(result["E_cmp [W]"]),
                "V_cmp_ref_cm3": model.V_cmp_ref * 1e6,
                "UA_ou_rated_W_K": model.UA_ou_rated,
            }
        )
    return pd.DataFrame(rows)


def nameplate_sweep() -> pd.DataFrame:
    """Rescale only the Fujitsu nameplates; keep everything else published."""
    catalogs = {catalog.slug: catalog for catalog in load_all()}
    rows = []
    for multiplier in MULTIPLIERS:
        for slug in (*RESCALED, CONTROL):
            catalog = catalogs[slug]
            if slug in RESCALED:
                catalog = dataclasses.replace(
                    catalog, nominal_capacity_kW=catalogs[slug].nominal_capacity_kW * multiplier
                )
            frame = run_catalog(catalog)
            good = frame[frame.usable]
            bias = ((good.cop_pred - good.cop_target) / good.cop_target * 100.0).mean()
            rows.append(
                {
                    "multiplier": multiplier,
                    "slug": slug,
                    "rescaled": slug in RESCALED,
                    "points": int(len(good)),
                    "mape_pct": float(good.abs_pct_error.mean()),
                    "bias_pct": float(bias),
                }
            )
            print(f"    {multiplier:.2f}  {slug:22s} MAPE {rows[-1]['mape_pct']:6.2f}  bias {bias:+6.2f}", flush=True)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reuse", action="store_true", help="reuse the cached nameplate sweep")
    args = parser.parse_args()

    print("Refrigerant parity diagnosis -- air-to-air")
    print()

    matched = matched_point()
    matched.to_csv(OUT_MATCHED, index=False)
    print("  matched operating point (outdoor 35 degC, cooling near nameplate)")
    for _, row in matched.iterrows():
        print(
            f"    {row.unit:22s} {row.refrigerant:6s} declared {row.declared_cop:.2f}   model {row.predicted_cop:.3f}"
        )
    spread_declared = matched.declared_cop.max() / matched.declared_cop.min() - 1.0
    spread_model = matched.predicted_cop.max() / matched.predicted_cop.min() - 1.0
    print(f"    declared spread {spread_declared:.0%}, model spread {spread_model:.1%}")
    print()

    if args.reuse and OUT_SWEEP.exists():
        sweep = pd.read_csv(OUT_SWEEP)
        print("  (reusing the cached nameplate sweep)")
    else:
        print("  nameplate sweep -- this re-runs both catalogues at every multiplier")
        sweep = nameplate_sweep()
        sweep.to_csv(OUT_SWEEP, index=False)
    print()

    control = sweep[~sweep.rescaled]
    print(f"  control {CONTROL} MAPE across the sweep: {control.mape_pct.min():.2f}-{control.mape_pct.max():.2f} %")
    for slug in RESCALED:
        block = sweep[sweep.slug == slug].sort_values("multiplier", ascending=False)
        crossing = block.iloc[(block.bias_pct.abs()).argmin()]
        print(
            f"  {slug:22s} bias {block.bias_pct.iloc[0]:+.2f} -> {block.bias_pct.iloc[-1]:+.2f} %,"
            f" crosses zero near x{crossing.multiplier:.2f};"
            f" MAPE floors at {block.mape_pct.min():.2f} %"
        )
    print()
    print(f"  wrote {OUT_MATCHED.relative_to(REPO_ROOT)}")
    print(f"  wrote {OUT_SWEEP.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
