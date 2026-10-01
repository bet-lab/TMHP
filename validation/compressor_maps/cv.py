"""Leave-one-compressor-out (LOCO) cross-validation, speed-transfer error and
model selection for the compressor efficiency families.

Two questions are scored for every candidate family, in both estimators
(``pooled`` and ``fe``, see ``fit.py``):

``loco_wmape_pct``
    Refit without machine k, predict k with the population level.  The error
    of the shipped default on a compressor nobody has calibrated -- the
    catalogue-parity use case.
``st_wmape_pct`` (speed transfer)
    Refit without machine k; then take k's speed record nearest n* = 1 as
    known (its level is read from that record alone) and predict k's *other*
    speed records.  The error of the speed shape once the rated point is
    matched -- the part-load use case, where a heat pump's rated speed level
    is set by its catalogue and the correlation only has to carry the change
    with speed.  Only machines with >= 2 speed records enter.

Acceptance rules (strategy doc Sec. 13, judgement.md, plan v3 Sec. 6.4/7):
  R1  parsimony: an extra coefficient must buy >= 0.1 pp in the metric its
      term targets (speed terms: speed transfer; lift terms: LOCO) and may
      not cost more than 0.1 pp per coefficient in the other.
  R2  no stratum with >= 3 machines worse than the parent by > 2 pp absolute
      or 25 % relative (LOCO).
  R3  constraints: 0 < eta <= 1.02 on PR in [1.5, 8], n* in [0.15, 2.5];
      d(n* eta_vol)/dn* > 0 on the same grid (solver bracketing assumption);
      for the product, the heating duty n* eta_vol (1 + kappa / eta_isen)
      stays monotonic for kappa in {0.1, 0.3, 0.6} (the isentropic split is
      applied downstream, so this uses the product's speed factor).
  R4  identification: a term that changes the speed dependence relative to
      its parent must be supported by the within-machine speed trend of the
      machines that identify it (median within-machine/added ratio >= 2/3,
      >= 3 machines from >= 2 sources).  Added 2026-09-15 with the rotary
      maps, generalised 2026-09-24 to lift x speed terms: the added effect is
      evaluated row by row, the machine level is read from its record nearest
      n* = 1 under the parent form.
  Tie within 0.1 pp: the form with a physical precedent (E1 drive loss,
      Ossorio & Navarro-Peris 2023; L1 leakage, Cuevas & Lebrun 2009) wins
      over a bare exponential or a log-linear interaction.
Writes validation/data/compressor_maps/{loco_pooled.csv, loco_strata.csv,
model_selection.csv, selected.json}.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from validation.compressor_maps.fit import (
    FAMILY,
    VOL_FAMILIES,
    fit_fe,
    fit_pooled,
    load_ok,
    pack_theta,
    product3_fn,
    product_bounds,
    split_key,
)
from validation.compressor_maps.schema import DATA_DIR

# The "old" baseline is the pre-refit module frozen under validation/coefficients/v1-legacy/,
# not whatever tmhp.compressor_efficiency currently ships -- otherwise rerunning after the
# refit would compare the new defaults with themselves.
_LEGACY = DATA_DIR.parents[1] / "coefficients" / "v1-legacy" / "legacy_forms.py"
MODES = ("pooled", "fe")
KAPPAS = (0.1, 0.3, 0.6)  # dh_is / (h_suc - h_liq) over the heat-pump envelope


def _legacy_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("tmhp_legacy_v1_forms", _LEGACY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


GRID_PR = np.linspace(1.5, 8.0, 27)
GRID_N = np.linspace(0.15, 2.5, 48)


def _fit(mode, fn, t0, lo, hi, pr, n, y, w, keys, f_scale, warm=None):
    if mode == "pooled":
        return fit_pooled(fn, t0, lo, hi, pr, n, y, w, f_scale)
    th, _, lam_pop = fit_fe(fn, t0, lo, hi, pr, n, y, w, keys, f_scale, warm=warm)
    return th  # lambda_pop is pinned to 0 inside fit_fe


def anchor_record(m: pd.DataFrame) -> float:
    """The machine's speed record nearest n* = 1 (its 'rated' record)."""
    rec = m.groupby("N_rps").n_star.median()
    return float(rec.index[int((rec - 1.0).abs().argmin())])


def loco(df: pd.DataFrame, fn, t0, lo, hi, ycol: str, f_scale: float, mode: str) -> tuple[np.ndarray, np.ndarray]:
    """LOCO predictions with the population level, and speed-transfer predictions (NaN where not applicable)."""
    pred = np.full(len(df), np.nan)
    pred_st = np.full(len(df), np.nan)
    keys = df.compressor_key.to_numpy()
    pr, n, y, w = df.PR.to_numpy(), df.n_star.to_numpy(), df[ycol].to_numpy(), np.sqrt(df.w_record.to_numpy())
    nrps = df.N_rps.to_numpy()
    warm = None
    if mode == "fe":  # full-data solution as the starting point of every refit
        th_all, lam_all, _ = fit_fe(fn, t0, lo, hi, pr, n, y, w, keys, f_scale)
        warm = (th_all, lam_all)
    for k in np.unique(keys):
        m = keys == k
        th = _fit(mode, fn, t0, lo, hi, pr[~m], n[~m], y[~m], w[~m], keys[~m], f_scale, warm)
        p = fn(th, pr[m], n[m])
        pred[m] = p
        if len(np.unique(nrps[m])) >= 2:
            a = anchor_record(df[m])
            at_anchor = nrps[m] == a
            level = np.exp(np.median(np.log(y[m][at_anchor] / p[at_anchor])))
            ps = p * level
            ps[at_anchor] = np.nan  # the anchor record is used, not predicted
            pred_st[m] = ps
    return pred, pred_st


def wmape(y, p, w) -> float:
    ok = np.isfinite(p)
    return float(100 * np.average(np.abs(p[ok] - y[ok]) / y[ok], weights=w[ok])) if ok.any() else float("nan")


def strata(df: pd.DataFrame) -> pd.DataFrame:
    nb = pd.cut(df.n_star, [0, 0.5, 0.8, 1.2, 3.0], labels=["n*<0.5", "0.5-0.8", "0.8-1.2", ">1.2"]).astype(str)
    pb = pd.cut(df.PR, [0, 2.5, 4, 6, 9], labels=["PR<2.5", "2.5-4", "4-6", ">6"]).astype(str)
    return pd.concat(
        [
            "source=" + df.source_id,
            "ref=" + df.refrigerant,
            "type=" + df.comp_type,
            "nstar=" + nb,
            "PR=" + pb,
        ],
        axis=1,
    )


def constraint_check_vol(fn_vol, th_vol) -> dict:
    PR, N = np.meshgrid(GRID_PR, GRID_N, indexing="ij")
    ev = fn_vol(th_vol, PR, N)
    duty = N * ev
    mono = bool(np.all(np.diff(duty, axis=1) > 0))
    return {"eta_vol_min": float(ev.min()), "eta_vol_max": float(ev.max()), "duty_monotone_in_n": mono}


def constraint_check_oi(fn_oi, th_oi, fn_vol, th_vol) -> dict:
    """Product range, and heating-duty monotonicity with the product's speed factor carried by eta_isen."""
    PR, N = np.meshgrid(GRID_PR, GRID_N, indexing="ij")
    eo = fn_oi(th_oi, PR, N)
    ev = fn_vol(th_vol, PR, N)
    # eta_isen shares the product's (PR, n*) shape up to the drive factor; the worst case for the
    # duty bracket is all of the speed effect landing in eta_isen, which is what is checked here.
    ref = fn_oi(th_oi, PR, np.ones_like(N))
    eta_isen_rel = np.maximum(eo / ref, 0.3)
    mono = True
    for kappa in KAPPAS:
        duty = N * ev * (1.0 + kappa / eta_isen_rel)
        mono &= bool(np.all(np.diff(duty, axis=1) > 0))
    return {"eta_oi_min": float(eo.min()), "eta_oi_max": float(eo.max()), "heating_duty_monotone_in_n": bool(mono)}


def legacy_predictions(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Pre-refit TMHP defaults (v1-legacy) on the compressor rows."""
    leg = _legacy_module()
    ev = np.array([leg.eta_vol_default(pr, rps) for pr, rps in zip(df.PR, df.N_rps, strict=True)])
    eo = np.array(
        [
            leg.eta_isen_default(pr) * leg.make_eta_em(nr)(pr, rps)
            for pr, rps, nr in zip(df.PR, df.N_rps, df.N_rated_rps, strict=True)
        ]
    )
    return ev, eo


def within_machine_support(df: pd.DataFrame, fits: dict, child: str, parent: str) -> dict:
    """R4: how much of the extra (PR, n*) effect a child family adds is seen inside single machines.

    The parent *form* is evaluated with the child's shared parameters, so the
    only difference between the two predictions is the added term.  For every
    machine with >= 2 speed records, the level is read from its record nearest
    n* = 1 under the parent form; on the other records the observed residual
    of the parent form is divided by the added effect, row by row, and the
    machine's median ratio is kept where the added effect is >= 2 %.
    """
    gc, sc, xc = split_key(child)
    gp, sp, xp = split_key(parent)
    if sc == sp and xc == xp:  # no change in the speed dependence -> rule not applicable
        return {"R4_applicable": False, "R4_pass": True, "R4_ratio": float("nan"), "R4_machines": 0, "R4_sources": 0}
    rec_c = fits[child]
    fam = FAMILY
    ths_c = np.array([rec_c["theta_s"][k] for k in fam[sc].names])
    thx_c = np.array([rec_c["theta_x"].get(k, 0.0) for k in fam[xc].names])
    thg_c = np.array([rec_c["theta_g"][k] for k in fam[gc].names])
    # parent form needs the child's values for every parameter it shares; a parent parameter the child
    # lacks (E1 -> E3 say) means the two are not nested in the speed factor
    if any(k not in rec_c["theta_s"] for k in fam[sp].names) or any(k not in rec_c["theta_x"] for k in fam[xp].names):
        return {"R4_applicable": False, "R4_pass": True, "R4_ratio": float("nan"), "R4_machines": 0, "R4_sources": 0}
    ths_p = np.array([rec_c["theta_s"][k] for k in fam[sp].names])
    thx_p = np.array([rec_c["theta_x"][k] for k in fam[xp].names])

    def g_c(pr):
        return fam[gc].fn(thg_c, pr, np.ones_like(pr))

    def sx_par(pr, n):
        return fam[sp].fn(ths_p, pr, n) * fam[xp].fn(thx_p, pr, n)

    def sx_chi(pr, n):
        return fam[sc].fn(ths_c, pr, n) * fam[xc].fn(thx_c, pr, n)

    ratios, sources = [], set()
    for key, m in df.groupby("compressor_key"):
        if m.N_rps.nunique() < 2:
            continue
        a = anchor_record(m)
        pr, n, y = m.PR.to_numpy(), m.n_star.to_numpy(), m.eta_oi.to_numpy()
        at = (m.N_rps == a).to_numpy()
        par = np.log(np.maximum(g_c(pr) * sx_par(pr, n), 1e-6))
        chi = np.log(np.maximum(g_c(pr) * sx_chi(pr, n), 1e-6))
        level = np.median(np.log(y[at]) - par[at])
        resid = np.log(y) - par - level
        extra = chi - par
        seen = (~at) & (np.abs(extra) >= 0.02)
        if seen.sum() >= 2:
            ratios.append(float(np.median(resid[seen] / extra[seen])))
            sources.add(key.split("::")[0])
    ratio = float(np.median(ratios)) if ratios else float("nan")
    ok = len(ratios) >= 3 and len(sources) >= 2 and ratio >= 2.0 / 3.0
    return {
        "R4_applicable": True,
        "R4_pass": bool(ok),
        "R4_ratio": round(ratio, 3) if ratios else float("nan"),
        "R4_machines": len(ratios),
        "R4_sources": len(sources),
    }


# nesting chains; the interaction axis (X) is now part of the walk.  E1 is E2 with d = 0;
# X0 is L1/L2/X1 with the coefficient at 0; E0 is E1 with n0 -> 0.
NEST = {
    "eta_vol": ["V1", "V2", "V5", "V3", "V4", "V6", "V7"],
    "eta_oi": [
        "I1xE0xX0",
        "I2xE0xX0",
        "I3xE0xX0",
        "I2xE1xX0",
        "I2xE2xX0",
        "I2xE3xX0",
        "I3xE1xX0",
        "I3xE2xX0",
        "I3xE3xX0",
        "I1xE1xX0",
        "I1xE2xX0",
        "I1xE3xX0",
        "I2xE0xL1",
        "I2xE0xL2",
        "I2xE1xL1",
        "I2xE1xL2",
        "I2xE2xL1",
        "I2xE2xL2",
        "I2xE1xX1",
        "I2xE2xX1",
        "I2xF0xX0",
        "I2xF0xL1",
        "I2xF0xL2",
        "I2xF2xL1",
        "I2xF3xL1",
        "I2xF2xL2",
        "I2xF3xL2",
        "I2xF2xX0",
        "I2xF3xX0",
        "I2xG0xX0",
        "I2xG0xL1",
        "I2xG2xX0",
        "I2xG2xL1",
    ],
}
PARENT = {
    "V2": "V1",
    "V5": "V2",
    "V3": "V2",
    "V4": "V2",
    "V6": "V1",
    "V7": "V6",
    "I2xE0xX0": "I1xE0xX0",
    "I3xE0xX0": "I1xE0xX0",
    "I2xE1xX0": "I2xE0xX0",
    "I2xE2xX0": "I2xE1xX0",
    "I2xE3xX0": "I2xE0xX0",
    "I3xE1xX0": "I3xE0xX0",
    "I3xE2xX0": "I3xE1xX0",
    "I3xE3xX0": "I3xE0xX0",
    "I1xE1xX0": "I1xE0xX0",
    "I1xE2xX0": "I1xE1xX0",
    "I1xE3xX0": "I1xE0xX0",
    "I2xE0xL1": "I2xE0xX0",
    "I2xE0xL2": "I2xE0xX0",
    "I2xE1xL1": "I2xE0xL1",
    "I2xE1xL2": "I2xE0xL2",
    "I2xE2xL1": "I2xE1xL1",
    "I2xE2xL2": "I2xE1xL2",
    "I2xE1xX1": "I2xE1xX0",
    "I2xE2xX1": "I2xE2xX0",
    # anchored drive term: no free coefficient, so F0 is judged against the no-speed root
    "I2xF0xX0": "I2xE0xX0",
    "I2xF0xL1": "I2xF0xX0",
    "I2xF0xL2": "I2xF0xX0",
    "I2xF2xL1": "I2xF0xL1",
    "I2xF3xL1": "I2xF0xL1",
    "I2xF2xL2": "I2xF0xL2",
    "I2xF3xL2": "I2xF0xL2",
    "I2xF2xX0": "I2xF0xX0",
    "I2xF3xX0": "I2xF0xX0",
    "I2xG0xX0": "I2xE0xX0",
    "I2xG0xL1": "I2xG0xX0",
    "I2xG2xX0": "I2xG0xX0",
    "I2xG2xL1": "I2xG0xL1",
}
#: which metric a family's added term targets (R1): speed terms -> speed transfer, lift terms -> LOCO
SPEED_TERM = {
    k: (split_key(k)[1] != split_key(PARENT[k])[1]) or (split_key(k)[2] != split_key(PARENT[k])[2])
    for k in PARENT
    if "x" in k
}
SPEED_TERM.update({"V2": True, "V5": False, "V3": True, "V4": True, "V6": True, "V7": True})
PRECEDENT = {"E1", "L1", "F0", "F2", "F3", "G0", "G2"}  # forms with a physical reading and an independent source


_DV: pd.DataFrame | None = None
_DF: pd.DataFrame | None = None


def _init_worker(dv: pd.DataFrame, df: pd.DataFrame) -> None:
    global _DV, _DF
    _DV, _DF = dv, df


def _run_task(task: tuple[str, str, str]) -> tuple[np.ndarray, np.ndarray, int]:
    """LOCO + speed-transfer predictions for one (mode, kind, family)."""
    mode, kind, key = task
    assert _DV is not None and _DF is not None
    if kind == "eta_vol":
        fam = FAMILY[key]
        p, ps = loco(_DV, fam.fn, fam.theta0, fam.lower, fam.upper, "eta_vol", 0.02, mode)
        return p, ps, len(fam.theta0)
    g, s, x = (FAMILY[k] for k in split_key(key))
    fn = product3_fn(g, s, x)
    t0, lo, hi = product_bounds(g, s, x)
    p, ps = loco(_DF, fn, t0, lo, hi, "eta_oi", 0.03, mode)
    return p, ps, len(t0)


def main() -> None:
    df = load_ok().reset_index(drop=True)
    dv = df[~df.vdisp_suspect].reset_index(drop=True)
    st_v, st_o = strata(dv), strata(df)
    fit_res = json.loads((DATA_DIR / "fit_results.json").read_text())
    pooled, per_stratum = [], []

    def record(name, kind, mode, y, p, p_st, w, st, ncoef):
        pooled.append(
            {
                "kind": kind,
                "mode": mode,
                "family": name,
                "n_coef": ncoef,
                "loco_wmape_pct": wmape(y, p, w),
                "loco_wrmse": float(np.sqrt(np.average((p - y) ** 2, weights=w))),
                "st_wmape_pct": wmape(y, p_st, w),
                "st_rows": int(np.isfinite(p_st).sum()),
            }
        )
        for col in st.columns:
            for lvl, idx in st.groupby(col).groups.items():
                idx = np.asarray(list(idx))
                per_stratum.append(
                    {
                        "kind": kind,
                        "mode": mode,
                        "family": name,
                        "stratum": lvl,
                        "n": len(idx),
                        "loco_wmape_pct": wmape(y[idx], p[idx], w[idx]),
                        "st_wmape_pct": wmape(y[idx], p_st[idx], w[idx]),
                    }
                )

    yv, wv = dv.eta_vol.to_numpy(), dv.w_record.to_numpy()
    yo, wo = df.eta_oi.to_numpy(), df.w_record.to_numpy()
    tasks = [(mode, "eta_vol", fam.key) for mode in MODES for fam in VOL_FAMILIES] + [
        (mode, "eta_oi", key) for mode in MODES for key in NEST["eta_oi"]
    ]
    jobs = int(os.environ.get("CV_JOBS", "1"))
    reuse = os.environ.get("CV_REUSE") == "1" and (DATA_DIR / "loco_pooled.csv").exists()
    if reuse:  # selection only: the LOCO tables of the previous run are read back
        results = None
    elif jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker, initargs=(dv, df)) as ex:
            results = list(ex.map(_run_task, tasks))
    else:
        _init_worker(dv, df)
        results = [_run_task(t) for t in tasks]
    if results is not None:
        for (mode, kind, key), (p, ps, ncoef) in zip(tasks, results, strict=True):
            if kind == "eta_vol":
                record(key, kind, mode, yv, p, ps, wv, st_v, ncoef)
            else:
                record(key, kind, mode, yo, p, ps, wo, st_o, ncoef)
        lv, _ = legacy_predictions(dv)
        _, lo2 = legacy_predictions(df)
        for mode in MODES:
            record("legacy", "eta_vol", mode, yv, lv, np.full(len(dv), np.nan), wv, st_v, 0)
            record("legacy", "eta_oi", mode, yo, lo2, np.full(len(df), np.nan), wo, st_o, 0)
        pooled_df = pd.DataFrame(pooled)
        strata_df = pd.DataFrame(per_stratum)
        pooled_df.to_csv(DATA_DIR / "loco_pooled.csv", index=False)
        strata_df.to_csv(DATA_DIR / "loco_strata.csv", index=False)
    else:
        pooled_df = pd.read_csv(DATA_DIR / "loco_pooled.csv")
        strata_df = pd.read_csv(DATA_DIR / "loco_strata.csv")

    # fitted parameters by (mode, key)
    fits: dict[str, dict[str, dict]] = {"pooled": {}, "fe": {}}
    for r in fit_res["eta_oi"] + fit_res["eta_oi_x"]:
        fits["pooled"][r["key"]] = r
    for r in fit_res["eta_oi_fe"]:
        fits["fe"][r["key"]] = r
    vol_fits = {
        "pooled": {r["family"]: r for r in fit_res["eta_vol"]},
        "fe": {r["family"]: r for r in fit_res["eta_vol_fe"]},
    }

    machines_per_stratum = (
        pd.concat([st_o.assign(k=df.compressor_key)])
        .melt(id_vars="k", value_name="stratum")
        .groupby("stratum")
        .k.nunique()
    )
    big_strata = set(machines_per_stratum[machines_per_stratum >= 3].index)
    sel_rows, selected = [], {}
    for mode in MODES:
        for kind in ("eta_vol", "eta_oi"):
            cand = pooled_df[
                (pooled_df.kind == kind) & (pooled_df["mode"] == mode) & (pooled_df.family != "legacy")
            ].set_index("family")
            sdf = strata_df[(strata_df.kind == kind) & (strata_df["mode"] == mode)]
            vol_best = vol_fits[mode]["V2"]  # the volumetric shape the duty check is evaluated with
            for fam_key in NEST[kind]:
                r = cand.loc[fam_key]
                parent = PARENT.get(fam_key)
                if parent is None:
                    gain_l, gain_s, extra, r1 = 0.0, 0.0, 0, True
                else:
                    gain_l = cand.loc[parent].loco_wmape_pct - r.loco_wmape_pct
                    gain_s = cand.loc[parent].st_wmape_pct - r.st_wmape_pct
                    extra = int(r.n_coef - cand.loc[parent].n_coef)
                    target, other = (gain_s, gain_l) if SPEED_TERM.get(fam_key, False) else (gain_l, gain_s)
                    if np.isnan(target):
                        target = other
                    if extra == 0:  # anchored term (no free coefficient): accepted unless it costs > 0.1 pp
                        r1 = bool(target >= -0.1 and (np.isnan(other) or other >= -0.1))
                    else:
                        r1 = bool(target >= 0.1 * max(extra, 1) and (np.isnan(other) or other >= -0.1 * max(extra, 1)))
                s_new = sdf[sdf.family == fam_key].set_index("stratum").loco_wmape_pct
                s_par = sdf[sdf.family == (parent or fam_key)].set_index("stratum").loco_wmape_pct
                worse = s_new - s_par
                worse_big = worse[worse.index.isin(big_strata)]
                r2 = bool(((worse_big <= 2.0) | (worse_big <= 0.25 * s_par[worse_big.index])).all())
                row = {
                    "kind": kind,
                    "mode": mode,
                    "family": fam_key,
                    "parent": parent or "",
                    "n_coef": int(r.n_coef),
                    "loco_wmape_pct": round(r.loco_wmape_pct, 3),
                    "st_wmape_pct": round(r.st_wmape_pct, 3) if np.isfinite(r.st_wmape_pct) else float("nan"),
                    "gain_loco_pp": round(gain_l, 3),
                    "gain_st_pp": round(gain_s, 3) if np.isfinite(gain_s) else float("nan"),
                    "speed_term": bool(SPEED_TERM.get(fam_key, False)),
                    "R1_parsimony": bool(r1),
                    "R2_no_big_stratum_worse": r2,
                    "worst_big_stratum": worse_big.idxmax() if len(worse_big) else "",
                    "worst_big_stratum_delta_pp": round(float(worse_big.max()), 2) if len(worse_big) else 0.0,
                }
                if kind == "eta_vol":
                    fam = FAMILY[fam_key]
                    th = np.array([vol_fits[mode][fam_key]["theta"][k] for k in fam.names])
                    cc = constraint_check_vol(fam.fn, th)
                    row.update({f"R3_{k}": v for k, v in cc.items()})
                    row["R3_pass"] = bool(
                        cc["duty_monotone_in_n"] and cc["eta_vol_min"] > 0 and cc["eta_vol_max"] <= 1.02
                    )
                    row.update(
                        {
                            "R4_applicable": False,
                            "R4_pass": True,
                            "R4_ratio": float("nan"),
                            "R4_machines": 0,
                            "R4_sources": 0,
                        }
                    )
                else:
                    g, s, x = (FAMILY[k] for k in split_key(fam_key))
                    th = pack_theta(fits[mode][fam_key], g, s, x)
                    thv = np.array([vol_best["theta"][k] for k in FAMILY["V2"].names])
                    cc = constraint_check_oi(product3_fn(g, s, x), th, FAMILY["V2"].fn, thv)
                    row.update({f"R3_{k}": v for k, v in cc.items()})
                    row["R3_pass"] = bool(
                        cc["heating_duty_monotone_in_n"] and cc["eta_oi_min"] > 0 and cc["eta_oi_max"] <= 1.02
                    )
                    if parent:
                        row.update(within_machine_support(df, fits[mode], fam_key, parent))
                    else:
                        row.update(
                            {
                                "R4_applicable": False,
                                "R4_pass": True,
                                "R4_ratio": float("nan"),
                                "R4_machines": 0,
                                "R4_sources": 0,
                            }
                        )
                row["accepted_over_parent"] = bool(r1 and r2 and row["R3_pass"] and row["R4_pass"])
                sel_rows.append(row)
            acc = {r["family"]: r["accepted_over_parent"] for r in sel_rows if r["kind"] == kind and r["mode"] == mode}

            def chain_ok(f, _acc=acc):
                while f:
                    if not _acc[f]:
                        return False
                    f = PARENT.get(f)
                return True

            ok_fams = [f for f in NEST[kind] if chain_ok(f)]
            # deepest accepted family with the smallest speed-transfer error where defined, else LOCO
            score = cand.st_wmape_pct.fillna(cand.loco_wmape_pct) if kind == "eta_oi" else cand.loco_wmape_pct
            best = min(ok_fams, key=lambda f: score.loc[f])
            near = [f for f in ok_fams if score.loc[f] - score.loc[best] <= 0.10]
            pref = [f for f in near if all(p in PRECEDENT or p in ("I2", "X0", "E0", "V2", "V6") for p in f.split("x"))]
            tie_note = ""
            if pref and best not in pref:
                simplest = min(pref, key=lambda f: (cand.loc[f].n_coef, score.loc[f]))
                tie_note = (
                    f"{best} ({score.loc[best]:.3f} %) replaced by {simplest} ({score.loc[simplest]:.3f} %): within 0.1 pp, "
                    "the form with a physical precedent is preferred"
                )
                best = simplest
            selected[f"{kind}:{mode}"] = {
                "family": best,
                "loco_wmape_pct": float(cand.loc[best].loco_wmape_pct),
                "st_wmape_pct": float(cand.loc[best].st_wmape_pct),
                "legacy_loco_wmape_pct": float(
                    pooled_df[
                        (pooled_df.kind == kind) & (pooled_df["mode"] == mode) & (pooled_df.family == "legacy")
                    ].loco_wmape_pct.iloc[0]
                ),
                "chain_accepted": ok_fams,
                "rejected_by_R4": [
                    r["family"]
                    for r in sel_rows
                    if r["kind"] == kind
                    and r["mode"] == mode
                    and r["R4_applicable"]
                    and not r["R4_pass"]
                    and r["R1_parsimony"]
                ],
                "R4": {
                    r["family"]: {k: r[k] for k in ("R4_ratio", "R4_machines", "R4_sources", "R4_pass")}
                    for r in sel_rows
                    if r["kind"] == kind and r["mode"] == mode and r["R4_applicable"]
                },
                "tie_break": tie_note,
            }
    selected["eta_em_anchor"] = fit_res["eta_em_anchor"]
    selected["big_strata_min_machines"] = 3
    (DATA_DIR / "selected.json").write_text(json.dumps(selected, indent=1))
    sel = pd.DataFrame(sel_rows)
    sel.to_csv(DATA_DIR / "model_selection.csv", index=False)
    print(json.dumps({k: v for k, v in selected.items() if k not in ("eta_em_anchor",)}, indent=1))
    pd.set_option("display.width", 250)
    print(pooled_df.sort_values(["kind", "mode", "st_wmape_pct"]).to_string(index=False))
    print()
    cols = [
        "kind",
        "mode",
        "family",
        "parent",
        "n_coef",
        "loco_wmape_pct",
        "st_wmape_pct",
        "gain_loco_pp",
        "gain_st_pp",
        "R1_parsimony",
        "R2_no_big_stratum_worse",
        "R3_pass",
        "R4_pass",
        "R4_ratio",
        "R4_machines",
        "R4_sources",
        "accepted_over_parent",
    ]
    print(sel[cols].to_string(index=False))


if __name__ == "__main__":
    main()
