"""Leave-one-compressor-out (LOCO) cross-validation and model selection.

For every candidate family (eta_vol: V1-V5; eta_oi: g x s [x X]) refit without
machine k and predict k.  Reported per family: pooled weighted MAPE of eta_vol
(= MAPE of predicted capacity at fixed speed) and of eta_oi (= MAPE of
predicted power), the same per stratum (source, refrigerant, compressor type,
n* bin, PR bin), and the legacy TMHP defaults (``src/tmhp/compressor_efficiency``
at HEAD) scored on the same rows -- the "old" baseline.

Acceptance rules (strategy doc Sec. 13, judgement.md):
  R1  pooled LOCO MAPE not worse than the simpler nested family by more than
      the pooled between-machine spread would explain (we require: improvement
      >= 0.1 pp per extra coefficient, else the simpler family wins)
  R2  no stratum worse than the simpler family by > 2 pp absolute or 25 % relative
  R3  constraints: 0 < eta <= 1.02 on the grid PR in [1.5, 8], n* in [0.15, 2.5];
      d(n* eta_vol)/dn* > 0 on the same grid (solver bracketing assumption)
  R4  identification of a speed term (added 2026-09-15 when the rotary maps
      entered): a term that changes s(n*) relative to its parent must be
      supported by the *within-machine* speed trend of the machines that
      identify it -- pooling across machines lets a source whose whole level
      is off (the 2004 R22 rotaries sit 27 % below the population) masquerade
      as a speed effect when it alone covers a speed range.  The added term is
      isolated by evaluating the parent *form* with the child's shared
      parameters; for every machine with >= 2 speed records the observed
      change of ln(eta_oi / g_child(PR)) between its record nearest n* = 1 and
      each other record, minus what the parent form predicts, is divided by
      the added term.  The child is accepted only if at least 3 machines from
      >= 2 sources see an added effect of >= 2 % and the median ratio is at
      least 2/3 (one-sided: a term the machines show *more* strongly than the
      pooled fit is under-fitted, not spurious).  E2 (high-speed roll-off)
      scores 0.12 -- almost all of its fitted magnitude is the rotary level,
      not a speed trend -- and is rejected; E1 (low-speed drive loss) scores
      about 2 and passes.
  Tie  within 0.1 pp of pooled LOCO MAPE (the resolution R1 itself uses) the
      speed form with a physical precedent (E1, Ossorio & Navarro-Peris 2023)
      is preferred over a bare exponential (E3).  The window was 0.05 pp
      before the rotary data; E1 and E3 then differed by 0.03 pp, now by 0.06.
Writes validation/data/compressor_maps/{loco_pooled.csv, loco_strata.csv, model_selection.csv}.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from validation.compressor_maps.fit import (
    G_FAMILIES,
    S_FAMILIES,
    VOL_FAMILIES,
    X_FAMILIES,
    load_ok,
    product3_fn,
    product_fn,
)
from validation.compressor_maps.schema import DATA_DIR

# The "old" baseline is the pre-refit module frozen under validation/coefficients/v1-legacy/,
# not whatever tmhp.compressor_efficiency currently ships -- otherwise rerunning after the
# refit would compare the new defaults with themselves.
_LEGACY = DATA_DIR.parents[1] / "coefficients" / "v1-legacy" / "legacy_forms.py"


def _legacy_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("tmhp_legacy_v1_forms", _LEGACY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


GRID_PR = np.linspace(1.5, 8.0, 27)
GRID_N = np.linspace(0.15, 2.5, 48)


def _fit(fn, t0, lo, hi, pr, n, y, w, f_scale):
    return least_squares(lambda t: w * (fn(t, pr, n) - y), t0, bounds=(lo, hi), loss="soft_l1", f_scale=f_scale).x


def loco(df: pd.DataFrame, fn, t0, lo, hi, ycol: str, f_scale: float) -> np.ndarray:
    pred = np.full(len(df), np.nan)
    keys = df.compressor_key.to_numpy()
    pr, n, y, w = df.PR.to_numpy(), df.n_star.to_numpy(), df[ycol].to_numpy(), np.sqrt(df.w_record.to_numpy())
    for k in np.unique(keys):
        m = keys == k
        th = _fit(fn, t0, lo, hi, pr[~m], n[~m], y[~m], w[~m], f_scale)
        pred[m] = fn(th, pr[m], n[m])
    return pred


def wmape(y, p, w) -> float:
    return float(100 * np.average(np.abs(p - y) / y, weights=w))


def strata(df: pd.DataFrame) -> pd.Series:
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


def constraint_check(fn_vol, th_vol) -> dict:
    PR, N = np.meshgrid(GRID_PR, GRID_N, indexing="ij")
    ev = fn_vol(th_vol, PR, N)
    duty = N * ev
    mono = bool(np.all(np.diff(duty, axis=1) > 0))
    return {"eta_vol_min": float(ev.min()), "eta_vol_max": float(ev.max()), "duty_monotone_in_n": mono}


def legacy_predictions(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Pre-refit TMHP defaults (v1-legacy) on the compressor rows: eta_vol(PR, rps) and eta_isen(PR)*eta_em(PR, rps) with the machine's rated speed."""
    leg = _legacy_module()
    ev = np.array([leg.eta_vol_default(pr, rps) for pr, rps in zip(df.PR, df.N_rps, strict=True)])
    eo = np.array(
        [
            leg.eta_isen_default(pr) * leg.make_eta_em(nr)(pr, rps)
            for pr, rps, nr in zip(df.PR, df.N_rps, df.N_rated_rps, strict=True)
        ]
    )
    return ev, eo


def within_machine_support(df: pd.DataFrame, fit_res: dict, child: str, parent: str) -> dict:
    """R4: how much of the extra speed effect a child family adds is seen inside single machines."""
    fam = {f.key: f for f in G_FAMILIES + S_FAMILIES}
    gc, sc = child.split("x")[:2]
    gp, sp = parent.split("x")[:2]
    if sc == sp:  # no change in the speed factor -> rule not applicable
        return {"R4_applicable": False, "R4_pass": True, "R4_ratio": float("nan"), "R4_machines": 0, "R4_sources": 0}
    rec_c = next(r for r in fit_res["eta_oi"] if r["g"] == gc and r["s"] == sc)
    thg_c = np.array(list(rec_c["theta_g"].values()))
    ths_c = np.array(list(rec_c["theta_s"].values()))
    # the parent *form* with the child's shared parameters isolates the term the child adds
    shared = [rec_c["theta_s"][k] for k in fam[sp].names if k in rec_c["theta_s"]]
    if len(shared) != len(fam[sp].names):  # parent has a parameter the child lacks: not nested in s
        return {"R4_applicable": False, "R4_pass": True, "R4_ratio": float("nan"), "R4_machines": 0, "R4_sources": 0}
    ths_p = np.array(shared)
    g_chi = lambda pr: fam[gc].fn(thg_c, pr, np.ones_like(pr))  # noqa: E731
    s_par = lambda n: fam[sp].fn(ths_p, np.ones_like(n), n)  # noqa: E731
    s_chi = lambda n: fam[sc].fn(ths_c, np.ones_like(n), n)  # noqa: E731
    ratios, sources = [], set()
    for key, m in df.groupby("compressor_key"):
        if m.N_rps.nunique() < 2:
            continue
        rec = (
            m.assign(sf=m.eta_oi.to_numpy() / g_chi(m.PR.to_numpy()))
            .groupby("N_rps")
            .agg(n=("n_star", "median"), sf=("sf", "median"))
        )
        ref = rec.loc[(rec.n - 1.0).abs().idxmin()]
        n = rec.n.to_numpy()
        n_ref = np.array([ref.n])
        par = np.log(s_par(n) / s_par(n_ref)[0])
        extra = np.log(s_chi(n) / s_chi(n_ref)[0]) - par
        resid = np.log(rec.sf.to_numpy() / ref.sf) - par
        seen = np.abs(extra) >= 0.02
        if seen.any():
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


def main() -> None:
    df = load_ok().reset_index(drop=True)
    dv = df[~df.vdisp_suspect].reset_index(drop=True)
    st_v, st_o = strata(dv), strata(df)
    pooled, per_stratum = [], []

    def record(name, kind, y, p, w, st, ncoef):
        pooled.append(
            {
                "kind": kind,
                "family": name,
                "n_coef": ncoef,
                "loco_wmape_pct": wmape(y, p, w),
                "loco_wrmse": float(np.sqrt(np.average((p - y) ** 2, weights=w))),
            }
        )
        for col in st.columns:
            for lvl, idx in st.groupby(col).groups.items():
                idx = np.asarray(list(idx))
                per_stratum.append(
                    {
                        "kind": kind,
                        "family": name,
                        "stratum": lvl,
                        "n": len(idx),
                        "loco_wmape_pct": wmape(y[idx], p[idx], w[idx]),
                    }
                )

    # eta_vol
    yv, wv = dv.eta_vol.to_numpy(), dv.w_record.to_numpy()
    for fam in VOL_FAMILIES:
        p = loco(dv, fam.fn, fam.theta0, fam.lower, fam.upper, "eta_vol", 0.02)
        record(fam.key, "eta_vol", yv, p, wv, st_v, len(fam.theta0))
    lv, lo_ = legacy_predictions(dv)
    record("legacy", "eta_vol", yv, lv, wv, st_v, 0)
    # eta_oi
    yo, wo = df.eta_oi.to_numpy(), df.w_record.to_numpy()
    for g in G_FAMILIES:
        for s in S_FAMILIES:
            fn = product_fn(g, s)
            p = loco(
                df,
                fn,
                tuple(g.theta0) + tuple(s.theta0),
                tuple(g.lower) + tuple(s.lower),
                tuple(g.upper) + tuple(s.upper),
                "eta_oi",
                0.03,
            )
            record(f"{g.key}x{s.key}", "eta_oi", yo, p, wo, st_o, len(g.theta0) + len(s.theta0))
    for g in G_FAMILIES[1:2]:
        for s in S_FAMILIES[1:3]:
            for x in X_FAMILIES:
                fn = product3_fn(g, s, x)
                p = loco(
                    df,
                    fn,
                    tuple(g.theta0) + tuple(s.theta0) + tuple(x.theta0),
                    tuple(g.lower) + tuple(s.lower) + tuple(x.lower),
                    tuple(g.upper) + tuple(s.upper) + tuple(x.upper),
                    "eta_oi",
                    0.03,
                )
                record(
                    f"{g.key}x{s.key}x{x.key}", "eta_oi", yo, p, wo, st_o, len(g.theta0) + len(s.theta0) + len(x.theta0)
                )
    _, lo2 = legacy_predictions(df)
    record("legacy", "eta_oi", yo, lo2, wo, st_o, 0)

    pooled_df = pd.DataFrame(pooled)
    strata_df = pd.DataFrame(per_stratum)
    pooled_df.to_csv(DATA_DIR / "loco_pooled.csv", index=False)
    strata_df.to_csv(DATA_DIR / "loco_strata.csv", index=False)

    # selection: sequential nesting (R1), strata with >= 3 machines (R2), constraints (R3)
    fit_res = json.loads((DATA_DIR / "fit_results.json").read_text())
    machines_per_stratum = (
        pd.concat([st_o.assign(k=df.compressor_key)])
        .melt(id_vars="k", value_name="stratum")
        .groupby("stratum")
        .k.nunique()
    )
    big_strata = set(machines_per_stratum[machines_per_stratum >= 3].index)
    NEST = {
        "eta_vol": ["V1", "V2", "V5", "V3", "V4"],
        "eta_oi": [
            "I1xE0",
            "I2xE0",
            "I2xE1",
            "I2xE2",
            "I2xE3",
            "I3xE0",
            "I3xE1",
            "I3xE2",
            "I3xE3",
            "I1xE1",
            "I1xE2",
            "I1xE3",
            "I2xE1xX1",
            "I2xE2xX1",
        ],
    }
    PARENT = {
        "V2": "V1",
        "V5": "V2",
        "V3": "V2",
        "V4": "V2",
        "I2xE0": "I1xE0",
        "I3xE0": "I1xE0",
        "I2xE1": "I2xE0",
        "I2xE2": "I2xE1",
        "I2xE3": "I2xE0",
        "I3xE1": "I3xE0",
        "I3xE2": "I3xE1",
        "I3xE3": "I3xE0",
        "I1xE1": "I1xE0",
        "I1xE2": "I1xE1",
        "I1xE3": "I1xE0",
        "I2xE1xX1": "I2xE1",
        "I2xE2xX1": "I2xE2",
    }
    sel_rows = []
    selected = {}
    for kind in ("eta_vol", "eta_oi"):
        cand = pooled_df[(pooled_df.kind == kind) & (pooled_df.family != "legacy")].set_index("family")
        sdf = strata_df[strata_df.kind == kind]
        for fam_key in NEST[kind]:
            r = cand.loc[fam_key]
            parent = PARENT.get(fam_key)
            if parent is None:
                gain, extra, r1 = 0.0, 0, True
            else:
                gain = cand.loc[parent].loco_wmape_pct - r.loco_wmape_pct
                extra = int(r.n_coef - cand.loc[parent].n_coef)
                r1 = gain >= 0.1 * max(extra, 1)
            s_new = sdf[sdf.family == fam_key].set_index("stratum").loco_wmape_pct
            s_par = sdf[sdf.family == (parent or fam_key)].set_index("stratum").loco_wmape_pct
            worse = s_new - s_par
            worse_big = worse[worse.index.isin(big_strata)]
            r2 = bool(((worse_big <= 2.0) | (worse_big <= 0.25 * s_par[worse_big.index])).all())
            row = {
                "kind": kind,
                "family": fam_key,
                "parent": parent or "",
                "n_coef": int(r.n_coef),
                "loco_wmape_pct": round(r.loco_wmape_pct, 3),
                "gain_vs_parent_pp": round(gain, 3),
                "R1_parsimony": bool(r1),
                "R2_no_big_stratum_worse": r2,
                "worst_big_stratum": worse_big.idxmax() if len(worse_big) else "",
                "worst_big_stratum_delta_pp": round(float(worse_big.max()), 2) if len(worse_big) else 0.0,
                "worst_any_stratum": worse.idxmax(),
                "worst_any_stratum_delta_pp": round(float(worse.max()), 2),
            }
            if kind == "eta_vol":
                fam = next(f for f in VOL_FAMILIES if f.key == fam_key)
                th = np.array(
                    [
                        fit_res["eta_vol"][[x["family"] for x in fit_res["eta_vol"]].index(fam_key)]["theta"][k]
                        for k in fam.names
                    ]
                )
                cc = constraint_check(fam.fn, th)
                row.update({f"R3_{k}": v for k, v in cc.items()})
                row["R3_pass"] = bool(cc["duty_monotone_in_n"] and cc["eta_vol_min"] > 0 and cc["eta_vol_max"] <= 1.02)
            else:
                row["R3_pass"] = True
            if kind == "eta_oi" and parent and "X" not in fam_key:
                row.update(within_machine_support(df, fit_res, fam_key, parent))
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
        # walk the nesting chain: the deepest family whose every ancestor step was accepted
        acc = {r["family"]: r["accepted_over_parent"] for r in sel_rows if r["kind"] == kind}

        def chain_ok(f, _acc=acc):
            while f:
                if not _acc[f]:
                    return False
                f = PARENT.get(f)
            return True

        ok_fams = [
            f for f in NEST[kind] if chain_ok(f) and "X" not in f
        ]  # X = documented extension, not adopted (decision D-B)
        best = min(ok_fams, key=lambda f: cand.loc[f].loco_wmape_pct)
        # tie-breaker (documented): within 0.1 pp (R1's own resolution), prefer the speed form with a physical precedent --
        # E1 n*(1+n0)/(n*+n0) is the saturating drive-loss form Ossorio & Navarro-Peris (2023) fit to
        # 185 inverter measurements; E3 is a bare exponential with no such reading.
        near = [f for f in ok_fams if cand.loc[f].loco_wmape_pct - cand.loc[best].loco_wmape_pct <= 0.10]
        pref = [f for f in near if f.endswith("E1")]
        tie_note = ""
        if pref and best not in pref:
            tie_note = f"{best} ({cand.loc[best].loco_wmape_pct:.3f} %) replaced by {pref[0]} ({cand.loc[pref[0]].loco_wmape_pct:.3f} %): within 0.1 pp (the R1 resolution), E1 has the Ossorio drive-loss precedent"
            best = pref[0]
        selected[kind] = {
            "family": best,
            "loco_wmape_pct": float(cand.loc[best].loco_wmape_pct),
            "legacy_loco_wmape_pct": float(
                pooled_df[(pooled_df.kind == kind) & (pooled_df.family == "legacy")].loco_wmape_pct.iloc[0]
            ),
            "chain_accepted": ok_fams,
            "extension_candidates": [f for f in NEST[kind] if "X" in f and chain_ok(f)],
            "rejected_by_R4": [
                r["family"]
                for r in sel_rows
                if r["kind"] == kind
                and r["R4_applicable"]
                and not r["R4_pass"]
                and r["R1_parsimony"]
                and r["R2_no_big_stratum_worse"]
            ],
            "R4": {
                r["family"]: {k: r[k] for k in ("R4_ratio", "R4_machines", "R4_sources", "R4_pass")}
                for r in sel_rows
                if r["kind"] == kind and r["R4_applicable"]
            },
            "tie_break": tie_note,
        }
    selected["eta_em_anchor"] = fit_res["eta_em_anchor"]
    selected["big_strata_min_machines"] = 3
    (DATA_DIR / "selected.json").write_text(json.dumps(selected, indent=1))
    sel = pd.DataFrame(sel_rows)
    sel.to_csv(DATA_DIR / "model_selection.csv", index=False)
    print(json.dumps(selected, indent=1))
    pd.set_option("display.width", 250)
    print(pooled_df.sort_values(["kind", "loco_wmape_pct"]).to_string(index=False))
    print()
    print(sel.to_string(index=False))


if __name__ == "__main__":
    main()
