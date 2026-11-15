"""Split of eta_oi into the package's eta_is (discharge enthalpy) and eta_em (electrical input).

The package computes ``h_2 = h_1 + w_s / eta_is`` and ``P = m_dot (h_2 - h_1) / eta_em``, so
``eta_em = m_dot (h_2 - h_1) / P`` is the share of the (terminal) electrical power that ends up as enthalpy rise of
the gas; ``1 - eta_em`` leaves the shell as heat. Only rows with a *measured* discharge temperature identify it
(BITZER's discharge temperature is an adiabatic energy balance, not a measurement, and is not used):

    scroll  AREP #11 / ORNL TM-2013/414 (ZP21K5E), AREP #21 (51 cm3, R-404A), AREP #39 (ZP31K5E, R32),
            Cuevas & Lebrun 2009 (inverter-fed rows converted to the terminal basis with the inverter model)
    rotary  AREP #26 measured standard points (R-410A and R-32; the R-32 runs may have used the liquid-injection
            port the report mentions, which lowers the discharge temperature -- reported separately)

Forms: E0 ``eta_em = e0``; E1 ``eta_em = e0 + e1 (PR - 3)``. Leave-one-machine-out decides whether E1's slope is
kept; the between-unit spread of the unit means is the identification statement.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from validation.compressor_maps.semi_empirical.cv import wstats
from validation.compressor_maps.semi_empirical.model import machine_weights


def _fit(t: pd.DataFrame, slope: bool, w: np.ndarray) -> np.ndarray:
    X = np.column_stack([np.ones(len(t)), t.PR - 3.0]) if slope else np.ones((len(t), 1))
    sw = np.sqrt(w)
    return np.linalg.lstsq(X * sw[:, None], t.eta_em_t.to_numpy(float) * sw, rcond=None)[0]


def _pred(c: np.ndarray, t: pd.DataFrame) -> np.ndarray:
    return c[0] + (c[1] * (t.PR.to_numpy(float) - 3.0) if len(c) > 1 else 0.0)


def split_rows(df: pd.DataFrame, family: str) -> pd.DataFrame:
    t = df[(df.family == family) & df.split_measured & df.use_oi & df.eta_em_t.notna()].copy()
    if family == "rotary":
        t = t[t.source_id == "arep_rpt26_hitachi2013"]
    return t


def fit_split(df: pd.DataFrame, family: str) -> tuple[dict, pd.DataFrame]:
    t = split_rows(df, family)
    # rotary primary: the R-410A runs (no injection); R-32 is reported beside it
    prim = t[t.refrigerant == "R410A"] if family == "rotary" else t
    w = machine_weights(prim)
    rows = []
    out: dict = {"rows": int(len(prim)), "machines": int(prim.machine.nunique()), "units": int(prim.unit.nunique())}
    for name, slope in (("E0", False), ("E1", True)):
        c = _fit(prim, slope, w)
        loco = np.full(len(prim), np.nan)
        for m in prim.machine.unique():
            te = (prim.machine == m).to_numpy()
            if te.all():
                continue
            loco[te] = _pred(_fit(prim[~te], slope, machine_weights(prim[~te])), prim[te])
        b, r = wstats(loco - prim.eta_em_t.to_numpy(float), w)
        _, r_in = wstats(_pred(c, prim) - prim.eta_em_t.to_numpy(float), w)
        rows.append(
            {
                "family": family,
                "form": name,
                "coefficients": ";".join(f"{v:.4f}" for v in c),
                "rmse_in_sample": r_in,
                "loco_bias": b,
                "loco_rmse": r,
            }
        )
        out[name] = {"coefficients": c.tolist(), "rmse_in_sample": r_in, "loco_rmse": r}
    sel = "E1" if rows[1]["loco_rmse"] < rows[0]["loco_rmse"] else "E0"
    out["selected"] = sel
    units = t.groupby("unit").agg(
        source_id=("source_id", "first"),
        refrigerant=("refrigerant", "first"),
        rows=("PR", "size"),
        PR_min=("PR", "min"),
        PR_max=("PR", "max"),
        eta_em_mean=("eta_em_t", "mean"),
        eta_em_sd=("eta_em_t", "std"),
        eta_isen_mean=("eta_isen_t", "mean"),
    )
    out["unit_means"] = units.reset_index().to_dict(orient="records")
    means = units.loc[units.index.isin(prim.unit.unique()), "eta_em_mean"]
    out["between_unit_range"] = [float(means.min()), float(means.max())]
    for r in rows:
        r["selected"] = r["form"] == sel
    return out, pd.DataFrame(rows)
