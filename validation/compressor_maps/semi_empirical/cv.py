"""Cross-validation protocol (DESIGN 4.2), identical for every candidate and for the shipped-form baseline.

* **LOCO** -- leave one machine out (all its units). Two predictions of the held-out machine: ``free`` (its level
  refitted with the shape fixed: the level-free *shape* error) and ``pop`` (the median level of the training units:
  the *total* error a user without a calibration point would see).
* **LOSO** -- leave one source group out (scroll: BITZER / Copeland / AREP / literature; rotary: per source), the
  same two predictions. ``bias_pop`` is the level bias of the held-out group.
* **Edge hold-outs** -- top 20 % and bottom 20 % of PR (row quantiles of the family's fit rows), and the lowest
  speed record of every unit that has two or more. The held-out rows are predicted with their unit's level fitted
  on the remaining rows (population level where a unit has none left; counted).
* **Step slopes** -- same unit, speed and evaporating temperature, adjacent condensing temperatures, both PR >= 5:
  d eta / d PR of the data against the model.
Metrics are weighted with the machine weights of the full fit table (each machine 1), so a machine with many rows
does not dominate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from validation.compressor_maps.semi_empirical.model import Data, State, machine_weights

STEP_PR_MIN = 5.0


def wstats(e: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    m = np.isfinite(e)
    if not m.any():
        return np.nan, np.nan
    e, w = e[m], w[m]
    return float(np.sum(w * e) / np.sum(w)), float(np.sqrt(np.sum(w * e**2) / np.sum(w)))


def _data(model, df: pd.DataFrame, ycol: str) -> Data:
    return Data.of(df, model.speed, ycol=ycol)


def holdout(model, df: pd.DataFrame, test: np.ndarray, ycol: str, warm: State | None) -> dict:
    """Fit on ~test, predict test with refitted levels (free) and with the population level (pop)."""
    tr, te = df[~test], df[test]
    st = model.fit(_data(model, tr, ycol), warm=warm)
    dte = _data(model, te, ycol)
    pop = model.population_level(st)
    free_levels = model.fit_levels(st, dte)
    return {
        "state": st,
        "pred_free": model.predict(st, dte, free_levels),
        "pred_pop": model.predict(st, dte, pop),
        "index": te.index,
    }


def loco(model, df: pd.DataFrame, ycol: str = "eta_oi", warm: State | None = None) -> tuple[pd.DataFrame, list]:
    out = pd.DataFrame(index=df.index, columns=["pred_free", "pred_pop"], dtype=float)
    thetas = []
    for m in sorted(df.machine.unique()):
        test = (df.machine == m).to_numpy()
        if test.all():
            continue
        h = holdout(model, df, test, ycol, warm)
        out.loc[h["index"], "pred_free"] = h["pred_free"]
        out.loc[h["index"], "pred_pop"] = h["pred_pop"]
        thetas.append(("loco", m, h["state"].theta))
    return out, thetas


def loso(model, df: pd.DataFrame, groups: tuple[str, ...], col: str, ycol: str = "eta_oi", warm: State | None = None):
    rows, thetas = [], []
    w = machine_weights(df)
    for g in groups:
        test = (df[col] == g).to_numpy()
        if not test.any() or test.all():
            continue
        h = holdout(model, df, test, ycol, warm)
        y = df.loc[test, ycol].to_numpy(float)
        bf, rf = wstats(h["pred_free"] - y, w[test])
        bp, rp = wstats(h["pred_pop"] - y, w[test])
        rows.append(
            {
                "held_out": g,
                "rows": int(test.sum()),
                "machines": int(df.loc[test, "machine"].nunique()),
                "rmse_shape": rf,
                "bias_shape": bf,
                "bias_level": bp,
                "rmse_total": rp,
            }
        )
        thetas.append(("loso", g, h["state"].theta))
    return pd.DataFrame(rows), thetas


def edge_masks(df: pd.DataFrame) -> dict[str, np.ndarray]:
    q20, q80 = df.PR.quantile([0.2, 0.8])
    low = np.zeros(len(df), bool)
    for _, g in df.groupby("unit"):
        if g.N_rps.round(3).nunique() >= 2:
            low[df.index.get_indexer(g.index[g.N_rps.round(3) == g.N_rps.round(3).min()])] = True
    return {
        f"PR top 20 % (PR > {q80:.2f})": (q80 < df.PR).to_numpy(),
        f"PR bottom 20 % (PR < {q20:.2f})": (q20 > df.PR).to_numpy(),
        "lowest speed record of each multi-speed unit": low,
    }


def edges(model, df: pd.DataFrame, ycol: str = "eta_oi", warm: State | None = None):
    rows, thetas = [], []
    w = machine_weights(df)
    for name, test in edge_masks(df).items():
        if not test.any():
            continue
        tr, te = df[~test], df[test]
        st = model.fit(_data(model, tr, ycol), warm=warm)
        dte = _data(model, te, ycol)
        pop = model.population_level(st)
        lv = {k: st.levels.get(k, pop) for k in dte.units}
        missing = sum(k not in st.levels for k in dte.units)
        e = model.predict(st, dte, lv) - te[ycol].to_numpy(float)
        for grp, sel in [
            ("all", np.ones(len(te), bool)),
            *[(g, (te.group == g).to_numpy()) for g in te.group.unique()],
        ]:
            if not sel.any():
                continue
            b, r = wstats(e[sel], w[test][sel])
            rows.append(
                {
                    "holdout": name,
                    "group": grp,
                    "rows": int(sel.sum()),
                    "units_without_level": missing if grp == "all" else np.nan,
                    "bias": b,
                    "rmse": r,
                }
            )
        thetas.append(("edge", name, st.theta))
    return pd.DataFrame(rows), thetas


def step_slopes(model, st: State, df: pd.DataFrame, ycol: str = "eta_oi") -> pd.DataFrame:
    d = _data(model, df, ycol)
    pop = model.population_level(st)
    pred = model.predict(st, d, {k: st.levels.get(k, pop) for k in d.units})
    t = df.assign(pred=pred)[["unit", "group", "source_id", "N_rps", "T_evap_C", "T_cond_C", "PR", ycol, "pred"]]
    t = t.dropna(subset=["T_evap_C", "T_cond_C"]).sort_values(["unit", "N_rps", "T_evap_C", "T_cond_C"])
    pairs = []
    for _, g in t.groupby(["unit", "N_rps", "T_evap_C"]):
        g = g.drop_duplicates("T_cond_C")
        for a, b in zip(g.iloc[:-1].itertuples(), g.iloc[1:].itertuples(), strict=True):
            if a.PR >= STEP_PR_MIN and b.PR > a.PR:
                dpr = b.PR - a.PR
                pairs.append(
                    {
                        "source_id": a.source_id,
                        "group": a.group,
                        "data": (getattr(b, ycol) - getattr(a, ycol)) / dpr,
                        "model": (b.pred - a.pred) / dpr,
                    }
                )
    p = pd.DataFrame(pairs)
    if p.empty:
        return p
    rows = []
    for key, g in [("all", p), *list(p.groupby("source_id"))]:
        rows.append(
            {
                "source_id": key,
                "pairs": len(g),
                "slope_data": g.data.mean(),
                "slope_model": g.model.mean(),
                "ratio_model_data": g.model.mean() / g.data.mean() if g.data.mean() != 0 else np.nan,
                "same_sign_pct": 100.0 * np.mean(np.sign(g.data) == np.sign(g.model)),
                "within_30pct_pct": 100.0 * np.mean(np.abs(g.model - g.data) <= 0.3 * np.abs(g.data)),
            }
        )
    return pd.DataFrame(rows)


def loco_summary(df: pd.DataFrame, pred: pd.DataFrame, ycol: str = "eta_oi") -> pd.DataFrame:
    w = machine_weights(df)
    y = df[ycol].to_numpy(float)
    rows = []
    for grp, sel in [("all", np.ones(len(df), bool)), *[(g, (df.group == g).to_numpy()) for g in df.group.unique()]]:
        bf, rf = wstats(pred.pred_free.to_numpy(float)[sel] - y[sel], w[sel])
        bp, rp = wstats(pred.pred_pop.to_numpy(float)[sel] - y[sel], w[sel])
        rows.append(
            {
                "group": grp,
                "machines": int(df.loc[sel, "machine"].nunique()),
                "rows": int(sel.sum()),
                "rmse_shape": rf,
                "bias_shape": bf,
                "rmse_total": rp,
                "bias_total": bp,
            }
        )
    return pd.DataFrame(rows)
