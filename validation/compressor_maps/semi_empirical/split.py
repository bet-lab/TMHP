"""Split of eta_oi into the package's eta_is (discharge enthalpy) and eta_em (electrical input).

The package computes ``h_2 = h_1 + w_s / eta_is`` and ``P = m_dot (h_2 - h_1) / eta_em``, so
``eta_em = m_dot (h_2 - h_1) / P`` is the share of the (terminal) electrical power that ends up as enthalpy rise of
the gas; ``1 - eta_em`` leaves the shell as heat. Only rows with a *measured* discharge temperature identify it
(BITZER's discharge temperature is an adiabatic energy balance, not a measurement, and is not used):

    scroll  AREP #11 / ORNL TM-2013/414 (ZP21K5E), AREP #21 (51 cm3, R-404A), AREP #39 (ZP31K5E, R32),
            Cuevas & Lebrun 2009 (inverter-fed rows converted to the terminal basis with the inverter model)
    rotary  AREP #26 measured standard points (R-410A and R-32; the R-32 runs may have used the liquid-injection
            port the report mentions, which lowers the discharge temperature -- reported separately)

Forms
    F   **loss share** (the default, bounded by construction): a fixed share ``f`` of the loss work
        ``P - m_dot w_s`` reaches the gas, the rest leaves the shell. Then
        ``eta_is = 1 / (1 + f (1/eta_oi - 1))`` and ``eta_em = eta_oi + f (1 - eta_oi)``, with
        ``eta_is * eta_em = eta_oi``, and ``0 < eta_is <= 1``, ``0 < eta_em <= 1`` for every ``eta_oi`` in (0, 1] and
        ``f`` in [0, 1]. ``f`` is fitted on the measured eta_is (the discharge temperature is what is measured).
    E0  ``eta_em = e0`` (the first archive's split; ``eta_is = eta_oi / e0`` exceeds one where ``eta_oi > e0``).
    E1  ``eta_em = e0 + e1 (PR - 3)``.

Leave-one-machine-out scores every form on the measured eta_is and eta_em; the between-unit spread of the unit
values of ``f`` is the identification statement.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from validation.compressor_maps.semi_empirical.cv import wstats
from validation.compressor_maps.semi_empirical.model import machine_weights


def _fit(t: pd.DataFrame, slope: bool, w: np.ndarray) -> np.ndarray:
    X = np.column_stack([np.ones(len(t)), t.PR - 3.0]) if slope else np.ones((len(t), 1))
    sw = np.sqrt(w)
    return np.linalg.lstsq(X * sw[:, None], t.eta_em_t.to_numpy(float) * sw, rcond=None)[0]


def _pred(c: np.ndarray, t: pd.DataFrame) -> np.ndarray:
    return c[0] + (c[1] * (t.PR.to_numpy(float) - 3.0) if len(c) > 1 else 0.0)


def eta_is_loss_share(eta_oi: np.ndarray, f: float) -> np.ndarray:
    return 1.0 / (1.0 + f * (1.0 / eta_oi - 1.0))


def eta_em_loss_share(eta_oi: np.ndarray, f: float) -> np.ndarray:
    return eta_oi + f * (1.0 - eta_oi)


def _fit_f(t: pd.DataFrame, w: np.ndarray) -> float:
    oi, y, sw = t.eta_oi.to_numpy(float), t.eta_isen_t.to_numpy(float), np.sqrt(w)
    r = least_squares(lambda f: sw * (eta_is_loss_share(oi, f[0]) - y), [0.6], bounds=([0.0], [1.0]))
    return float(r.x[0])


def split_rows(df: pd.DataFrame, family: str) -> pd.DataFrame:
    t = df[(df.family == family) & df.split_measured & df.use_oi & df.eta_em_t.notna()].copy()
    if family == "rotary":
        t = t[t.source_id == "arep_rpt26_hitachi2013"]
    return t


def _scores(prim: pd.DataFrame, pred_em: np.ndarray, pred_is: np.ndarray, loco_em, loco_is, w) -> dict:
    _, r_em = wstats(pred_em - prim.eta_em_t.to_numpy(float), w)
    _, r_is = wstats(pred_is - prim.eta_isen_t.to_numpy(float), w)
    b_em, lr_em = wstats(loco_em - prim.eta_em_t.to_numpy(float), w)
    b_is, lr_is = wstats(loco_is - prim.eta_isen_t.to_numpy(float), w)
    return {
        "rmse_in_sample": r_em,
        "rmse_in_sample_eta_is": r_is,
        "loco_bias": b_em,
        "loco_rmse": lr_em,
        "loco_bias_eta_is": b_is,
        "loco_rmse_eta_is": lr_is,
    }


def fit_split(df: pd.DataFrame, family: str) -> tuple[dict, pd.DataFrame]:
    t = split_rows(df, family)
    # rotary primary: the R-410A runs (no injection); R-32 is reported beside it
    prim = t[t.refrigerant == "R410A"] if family == "rotary" else t
    w = machine_weights(prim)
    oi = prim.eta_oi.to_numpy(float)
    rows = []
    out: dict = {"rows": int(len(prim)), "machines": int(prim.machine.nunique()), "units": int(prim.unit.nunique())}
    machines = prim.machine.unique()
    for name, slope in (("E0", False), ("E1", True)):
        c = _fit(prim, slope, w)
        loco_em = np.full(len(prim), np.nan)
        for m in machines:
            te = (prim.machine == m).to_numpy()
            if te.all():
                continue
            loco_em[te] = _pred(_fit(prim[~te], slope, machine_weights(prim[~te])), prim[te])
        pe = _pred(c, prim)
        s = _scores(prim, pe, oi / pe, loco_em, oi / loco_em, w)
        rows.append({"family": family, "form": name, "coefficients": ";".join(f"{v:.4f}" for v in c), **s})
        out[name] = {"coefficients": c.tolist(), **s}
    f = _fit_f(prim, w)
    loco_f = np.full(len(prim), np.nan)
    for m in machines:
        te = (prim.machine == m).to_numpy()
        if te.all():
            continue
        loco_f[te] = _fit_f(prim[~te], machine_weights(prim[~te]))
    s = _scores(
        prim,
        eta_em_loss_share(oi, f),
        eta_is_loss_share(oi, f),
        eta_em_loss_share(oi, loco_f),
        eta_is_loss_share(oi, loco_f),
        w,
    )
    rows.append({"family": family, "form": "F", "coefficients": f"{f:.4f}", **s})
    out["F"] = {"coefficients": [f], **s}
    # F is the default: the only form bounded by construction (E0/E1 give eta_is > 1 where eta_oi > eta_em)
    out["selected"] = "F"
    out["selection_rule"] = "loss-share form F: bounded by construction; E0/E1 scored beside it"
    units = t.groupby("unit").agg(
        source_id=("source_id", "first"),
        refrigerant=("refrigerant", "first"),
        rows=("PR", "size"),
        PR_min=("PR", "min"),
        PR_max=("PR", "max"),
        eta_em_mean=("eta_em_t", "mean"),
        eta_em_sd=("eta_em_t", "std"),
        eta_isen_mean=("eta_isen_t", "mean"),
        eta_oi_mean=("eta_oi", "mean"),
    )
    t = t.assign(f_row=(1.0 / t.eta_isen_t - 1.0) / (1.0 / t.eta_oi - 1.0))
    units["f_mean"] = t.groupby("unit").f_row.mean()
    units["f_fit"] = [_fit_f(t[t.unit == u], np.ones((t.unit == u).sum())) for u in units.index]
    out["unit_means"] = units.reset_index().to_dict(orient="records")
    sel_units = units.loc[units.index.isin(prim.unit.unique())]
    out["between_unit_range"] = [float(sel_units.eta_em_mean.min()), float(sel_units.eta_em_mean.max())]
    out["between_unit_range_f"] = [float(sel_units.f_fit.min()), float(sel_units.f_fit.max())]
    for r in rows:
        r["selected"] = r["form"] == "F"
    return out, pd.DataFrame(rows)
