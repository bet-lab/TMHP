"""Regress eta = f(r_p, x_s) per group with leave-one-compressor-out CV.

Candidates (x_s = n* = f/50 Hz, or f in Hz):
    C1  a0 + a1 rp + a2 x
    C2  C1 + a3 rp x
    C3  a0 + a1 rp + a2 x + a3 rp^2 + a4 x^2 + a5 rp x

Grouping levels (plan section 26):
    C  type x refrigerant   (own coefficients per group)
    B  type                 (refrigerants pooled within a type)
    A  global               (everything pooled)
Each level is scored on the same rows with LOCO: the held-out compressor is
predicted by the model of its own group fitted without it.

usage: fit.py EFFICIENCY_POINTS.csv OUT_DIR
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

EFFS = ["eta_v", "eta_is", "eta_em", "eta_oi"]
CANDS = {
    "C1": lambda rp, x: np.column_stack([np.ones_like(rp), rp, x]),
    "C2": lambda rp, x: np.column_stack([np.ones_like(rp), rp, x, rp * x]),
    "C3": lambda rp, x: np.column_stack([np.ones_like(rp), rp, x, rp**2, x**2, rp * x]),
}
SPEEDS = {"nstar": "normalized_speed", "freq": "frequency_Hz"}


def lstsq(X, y):
    return np.linalg.lstsq(X, y, rcond=None)[0]


def metrics(y, yhat):
    r = y - yhat
    ss = ((y - y.mean()) ** 2).sum()
    return dict(
        R2=1 - (r**2).sum() / ss if ss > 0 else np.nan,
        RMSE=float(np.sqrt((r**2).mean())),
        MAE=float(np.abs(r).mean()),
        MAPE=float(np.abs(r / y).mean() * 100),
    )


def loco_pred(df, eff, cand, xcol, groupcol):
    pred = pd.Series(np.nan, index=df.index)
    for comp in df.compressor_id.unique():
        test = df.compressor_id == comp
        grp = df.loc[test, groupcol].iloc[0]
        train = (~test) & (df[groupcol] == grp)
        if df.loc[train, "compressor_id"].nunique() < 2:
            continue  # need >= 2 machines left to call it out-of-sample
        X = CANDS[cand](df.loc[train, "pressure_ratio"].values, df.loc[train, xcol].values)
        a = lstsq(X, df.loc[train, eff].values)
        pred[test] = CANDS[cand](df.loc[test, "pressure_ratio"].values, df.loc[test, xcol].values) @ a
    return pred


def st_pred(df, eff, cand, xcol, groupcol):
    """Speed transfer (ST): LOCO shape, level re-anchored on the held-out machine's own n*=1 rows.

    Mirrors how a catalogue/rated point fixes a machine's level in TMHP while the
    correlation must carry the off-rated shape.  Scored on n* != 1 rows only.
    """
    pred = pd.Series(np.nan, index=df.index)
    for comp in df.compressor_id.unique():
        test = df.compressor_id == comp
        grp = df.loc[test, groupcol].iloc[0]
        train = (~test) & (df[groupcol] == grp)
        if df.loc[train, "compressor_id"].nunique() < 2:
            continue
        X = CANDS[cand](df.loc[train, "pressure_ratio"].values, df.loc[train, xcol].values)
        a = lstsq(X, df.loc[train, eff].values)
        p = CANDS[cand](df.loc[test, "pressure_ratio"].values, df.loc[test, xcol].values) @ a
        rated = (df.loc[test, "normalized_speed"].round(3) == 1.0).values
        if not rated.any():
            continue
        p = p * (df.loc[test, eff].values[rated] / p[rated]).mean()
        p[rated] = np.nan
        pred[test] = p
    return pred


def main(src, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(src)
    df = df[df.valid_bitzer == "yes"].copy()
    df["grp_C"] = df.compressor_type + "_" + df.refrigerant
    df["grp_B"] = df.compressor_type
    df["grp_A"] = "global"
    coef_rows, level_rows = [], []
    preds = df[["compressor_id", "grp_C", "pressure_ratio", "normalized_speed", "frequency_Hz"]].copy()
    for eff in EFFS:
        d = df.dropna(subset=[eff])
        if eff in ("eta_is", "eta_em"):
            # the "w/o cooling" discharge temperature is not the delivered state where BITZER
            # requires additional (oil/liquid) cooling: condenser closure breaks there
            d = d[~d.sanity_flags.fillna("").str.contains("additional_cooling_required")]
        for level, gcol in (("C", "grp_C"), ("B", "grp_B"), ("A", "grp_A")):
            for sp, xcol in SPEEDS.items():
                for cand in CANDS:
                    cv = loco_pred(d, eff, cand, xcol, gcol)
                    st = st_pred(d, eff, cand, xcol, gcol)
                    for grp, g in d.groupby(gcol):
                        X = CANDS[cand](g.pressure_ratio.values, g[xcol].values)
                        a = lstsq(X, g[eff].values)
                        fit = X @ a
                        m = metrics(g[eff].values, fit)
                        ok = cv.loc[g.index].notna()
                        mcv = metrics(g[eff].values[ok], cv.loc[g.index][ok].values) if ok.any() else {}
                        okst = st.loc[g.index].notna()
                        mst = metrics(g[eff].values[okst], st.loc[g.index][okst].values) if okst.any() else {}
                        typ, _, ref = grp.partition("_") if level == "C" else (grp, "", "all")
                        coef_rows.append(
                            dict(
                                efficiency=eff,
                                level=level,
                                group=grp,
                                compressor_type=typ if level != "A" else "all",
                                refrigerant=ref if level == "C" else "all",
                                speed_var=sp,
                                model=cand,
                                **{f"a{i}": (a[i] if i < len(a) else np.nan) for i in range(6)},
                                n_compressors=g.compressor_id.nunique(),
                                n_points=len(g),
                                R2=m["R2"],
                                RMSE=m["RMSE"],
                                MAE=m["MAE"],
                                MAPE=m["MAPE"],
                                CV_RMSE=mcv.get("RMSE", np.nan),
                                CV_MAE=mcv.get("MAE", np.nan),
                                CV_MAPE=mcv.get("MAPE", np.nan),
                                ST_RMSE=mst.get("RMSE", np.nan),
                                ST_MAPE=mst.get("MAPE", np.nan),
                            )
                        )
                        if level == "C" and sp == "nstar":
                            preds.loc[g.index, f"{eff}_{cand}_fit"] = fit
                            preds.loc[g.index, f"{eff}_{cand}_cv"] = cv.loc[g.index]
                    # level-wide score on the same rows (only rows predictable at every level are compared later)
                    level_rows.append(
                        dict(
                            efficiency=eff,
                            level=level,
                            speed_var=sp,
                            model=cand,
                            **{
                                f"cv_{k}": v
                                for k, v in metrics(d[eff].values[cv.notna()], cv[cv.notna()].values).items()
                            },
                            n_scored=int(cv.notna().sum()),
                            **{
                                f"st_{k}": v
                                for k, v in metrics(d[eff].values[st.notna()], st[st.notna()].values).items()
                            },
                        )
                    )
    coef = pd.DataFrame(coef_rows)
    coef.to_csv(out / "regression_coefficients.csv", index=False)
    coef[
        [
            "efficiency",
            "level",
            "group",
            "compressor_type",
            "refrigerant",
            "speed_var",
            "model",
            "n_compressors",
            "n_points",
            "R2",
            "RMSE",
            "MAE",
            "MAPE",
            "CV_RMSE",
            "CV_MAE",
            "CV_MAPE",
            "ST_RMSE",
            "ST_MAPE",
        ]
    ].to_csv(out / "regression_metrics.csv", index=False)
    pd.DataFrame(level_rows).to_csv(out / "grouping_comparison.csv", index=False)
    preds.to_csv(out / "predictions_groupC_nstar.csv", index=False)
    # within-machine adequacy of the (r_p, n*) form: each compressor fitted on its own
    pm = []
    for eff in EFFS:
        dd = df.dropna(subset=[eff])
        if eff in ("eta_is", "eta_em"):
            dd = dd[~dd.sanity_flags.fillna("").str.contains("additional_cooling_required")]
        for (comp, _ref), g in dd.groupby(["compressor_id", "refrigerant"]):
            for cand in CANDS:
                X = CANDS[cand](g.pressure_ratio.values, g.normalized_speed.values)
                a = lstsq(X, g[eff].values)
                pm.append(
                    dict(
                        efficiency=eff,
                        compressor_id=comp,
                        group=g.grp_C.iloc[0],
                        model=cand,
                        n_points=len(g),
                        **metrics(g[eff].values, X @ a),
                    )
                )
    pd.DataFrame(pm).to_csv(out / "per_machine_fit.csv", index=False)
    print(
        coef[(coef.level == "C") & (coef.speed_var == "nstar")][
            [
                "efficiency",
                "group",
                "model",
                "n_compressors",
                "n_points",
                "R2",
                "RMSE",
                "CV_RMSE",
                "CV_MAPE",
                "ST_RMSE",
                "ST_MAPE",
            ]
        ].to_string()
    )
    print(pd.DataFrame(level_rows).query("speed_var=='nstar'").to_string())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
