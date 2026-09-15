"""Pooled fit of the three compressor efficiencies on standalone-compressor data.

Structure (decision D-B, judgement.md): eta_vol = f(PR, n*), eta_isen = f(PR),
eta_em = f(n*), n* = N / N_rated.  Catalogue heat-pump data never enter here.

What the data identify
----------------------
* eta_vol directly (mass flow, suction density, displacement, speed).
* eta_oi = eta_isen * eta_em directly (power).  Because eta_isen carries no
  speed term and eta_em no PR term, the product is *separable*:
  eta_oi(PR, n*) = g(PR) * s(n*) with s(1) = 1 identified up to one scale
  factor.  That factor -- how much of the electrical loss shows up as
  refrigerant enthalpy (eta_isen) versus leaves the shell or is lost in the
  drive (eta_em) -- is set by the only rows with a measured discharge
  temperature under the TMHP definition (Cuevas & Lebrun 2009, inverter-fed,
  n* ~ 1): ``ETA_EM_ANCHOR`` = median eta_em there.  Sensitivity +/-0.03 is
  reported.  Guth & Atakan's published split uses a different definition of
  the isentropic efficiency and is used for eta_vol and the product only.

Candidate families
------------------
eta_vol: V1  1 - a(PR-1)
         V2  1 - a(PR-1) - b*max(0, 1/n* - 1)            (one-sided, legacy shape in n*)
         V3  c - a(PR-1) - b/n*                          (V_disp-free intercept)
         V4  c - a(PR-1) - b*exp(-n*/nc)
         V5  c - a(PR-1) - b*max(0, 1/n* - 1)
g(PR):   I1  A - B*PR
         I2  A - B*PR - C/PR                             (under-compression peak)
         I3  A + B*PR + C*PR^2
s(n*):   E0  1                                           (no speed effect)
         E1  ((1+n0)/(n*+n0)) * n*                       (saturating, s(1)=1)
         E2  E1 * (1 - d*max(0, n*-1)^2)                 (high-speed roll-off)
         E3  (1-exp(-n*/nc)) / (1-exp(-1/nc))
Fits: scipy least_squares, loss='soft_l1', weights sqrt(w_record) so every
compressor x speed record counts once.  Selection by leave-one-compressor-out
CV (``cv.py``) against the acceptance rules of the strategy document.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from validation.compressor_maps.schema import DATA_DIR

FIT_READY = DATA_DIR / "points_fit_ready.csv"


@dataclass(frozen=True)
class Family:
    key: str
    label: str
    fn: Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]  # (theta, PR, n*) -> eta
    theta0: tuple[float, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]
    names: tuple[str, ...]


def _pos(x):
    return np.maximum(x, 0.0)


VOL_FAMILIES = [
    Family("V1", "1 - a(PR-1)", lambda t, pr, n: 1 - t[0] * (pr - 1), (0.02,), (0,), (0.2,), ("a",)),
    Family(
        "V2",
        "1 - a(PR-1) - b max(0,1/n*-1)",
        lambda t, pr, n: 1 - t[0] * (pr - 1) - t[1] * _pos(1 / n - 1),
        (0.02, 0.03),
        (0, 0),
        (0.2, 0.5),
        ("a", "b"),
    ),
    Family(
        "V3",
        "c - a(PR-1) - b/n*",
        lambda t, pr, n: t[2] - t[0] * (pr - 1) - t[1] / n,
        (0.02, 0.02, 1.0),
        (0, 0, 0.8),
        (0.2, 0.5, 1.2),
        ("a", "b", "c"),
    ),
    Family(
        "V4",
        "c - a(PR-1) - b exp(-n*/nc)",
        lambda t, pr, n: t[2] - t[0] * (pr - 1) - t[1] * np.exp(-n / t[3]),
        (0.02, 0.1, 1.0, 0.3),
        (0, 0, 0.8, 0.05),
        (0.2, 1.0, 1.2, 2.0),
        ("a", "b", "c", "nc"),
    ),
    Family(
        "V5",
        "c - a(PR-1) - b max(0,1/n*-1)",
        lambda t, pr, n: t[2] - t[0] * (pr - 1) - t[1] * _pos(1 / n - 1),
        (0.02, 0.03, 1.0),
        (0, 0, 0.8),
        (0.2, 0.5, 1.2),
        ("a", "b", "c"),
    ),
]
G_FAMILIES = [
    Family("I1", "A - B PR", lambda t, pr, n: t[0] - t[1] * pr, (0.8, 0.02), (0.3, -0.2), (1.2, 0.3), ("A", "B")),
    Family(
        "I2",
        "A - B PR - C/PR",
        lambda t, pr, n: t[0] - t[1] * pr - t[2] / pr,
        (0.9, 0.02, 0.2),
        (0.3, -0.2, 0),
        (1.5, 0.3, 1.5),
        ("A", "B", "C"),
    ),
    Family(
        "I3",
        "A + B PR + C PR^2",
        lambda t, pr, n: t[0] + t[1] * pr + t[2] * pr * pr,
        (0.7, 0.0, -0.005),
        (0.2, -0.5, -0.1),
        (1.2, 0.5, 0.1),
        ("A", "B", "C"),
    ),
]
S_FAMILIES = [
    Family("E0", "1", lambda t, pr, n: np.ones_like(n), (), (), (), ()),
    Family("E1", "n*(1+n0)/(n*+n0)", lambda t, pr, n: n * (1 + t[0]) / (n + t[0]), (0.05,), (1e-4,), (2.0,), ("n0",)),
    Family(
        "E2",
        "E1 (1 - d max(0,n*-1)^2)",
        lambda t, pr, n: n * (1 + t[0]) / (n + t[0]) * (1 - t[1] * _pos(n - 1) ** 2),
        (0.05, 0.05),
        (1e-4, 0),
        (2.0, 2.0),
        ("n0", "d"),
    ),
    Family(
        "E3",
        "(1-exp(-n*/nc))/(1-exp(-1/nc))",
        lambda t, pr, n: (1 - np.exp(-n / t[0])) / (1 - np.exp(-1 / t[0])),
        (0.3,),
        (0.02,),
        (3.0,),
        ("nc",),
    ),
]


# Extension candidate: the separability test shows the speed penalty grows with PR (leakage at
# low speed and high lift, Cuevas & Lebrun 2009 Sec. 3).  Kept as a *candidate* the CV must earn:
# eta_oi = g(PR) * s(n*) * exp(k (PR-3) ln n*) -- k>0 means low speed hurts more at high PR.
X_FAMILIES = [
    Family(
        "X1",
        "exp(k (PR-3) ln n*)",
        lambda t, pr, n: np.exp(t[0] * (pr - 3.0) * np.log(n)),
        (0.05,),
        (-0.5,),
        (0.5,),
        ("k",),
    ),
]


def product3_fn(gf: Family, sf: Family, xf: Family):
    ng, ns = len(gf.theta0), len(sf.theta0)

    def fn(t, pr, n):
        return gf.fn(t[:ng], pr, n) * sf.fn(t[ng : ng + ns], pr, n) * xf.fn(t[ng + ns :], pr, n)

    return fn


def fit_oi_x(df: pd.DataFrame, gf: Family, sf: Family, xf: Family) -> dict:
    pr, n, y, w = df.PR.to_numpy(), df.n_star.to_numpy(), df.eta_oi.to_numpy(), np.sqrt(df.w_record.to_numpy())
    fn = product3_fn(gf, sf, xf)
    t0 = tuple(gf.theta0) + tuple(sf.theta0) + tuple(xf.theta0)
    lo = tuple(gf.lower) + tuple(sf.lower) + tuple(xf.lower)
    hi = tuple(gf.upper) + tuple(sf.upper) + tuple(xf.upper)
    res = least_squares(lambda t: w * (fn(t, pr, n) - y), t0, bounds=(lo, hi), loss="soft_l1", f_scale=0.03)
    pred = fn(res.x, pr, n)
    ng, ns = len(gf.theta0), len(sf.theta0)
    return {
        "g": gf.key,
        "s": sf.key,
        "x": xf.key,
        "label": f"({gf.label}) x ({sf.label}) x {xf.label}",
        "theta_g": dict(zip(gf.names, map(float, res.x[:ng]), strict=True)),
        "theta_s": dict(zip(sf.names, map(float, res.x[ng : ng + ns]), strict=True)),
        "theta_x": dict(zip(xf.names, map(float, res.x[ng + ns :]), strict=True)),
        "n": len(df),
        "wrmse": float(np.sqrt(np.average((pred - y) ** 2, weights=w**2))),
        "wmape_pct": float(100 * np.average(np.abs(pred - y) / y, weights=w**2)),
    }


def machine_level_spread(df: pd.DataFrame, pred: np.ndarray, col: str) -> dict:
    """Between-machine spread of the mean residual: the part of the scatter a generic default cannot remove."""
    r = pd.DataFrame({"k": df.compressor_key.to_numpy(), "res": df[col].to_numpy() - pred, "w": df.w_record.to_numpy()})
    per = r.groupby("k").apply(lambda g: np.average(g.res, weights=g.w))
    within = r.groupby("k").apply(
        lambda g: np.sqrt(np.average((g.res - np.average(g.res, weights=g.w)) ** 2, weights=g.w))
    )
    return {
        "between_machine_sd": float(per.std()),
        "between_machine_p10": float(per.quantile(0.1)),
        "between_machine_p90": float(per.quantile(0.9)),
        "within_machine_sd_median": float(within.median()),
        "machines": int(len(per)),
    }


def load_ok() -> pd.DataFrame:
    df = pd.read_csv(FIT_READY, low_memory=False)
    return df[(~df.exclude_fixed) & df.point_ok].copy()


def fit_vol(df: pd.DataFrame, fam: Family) -> dict:
    d = df[~df.vdisp_suspect]
    pr, n, y, w = d.PR.to_numpy(), d.n_star.to_numpy(), d.eta_vol.to_numpy(), np.sqrt(d.w_record.to_numpy())
    res = least_squares(
        lambda t: w * (fam.fn(t, pr, n) - y), fam.theta0, bounds=(fam.lower, fam.upper), loss="soft_l1", f_scale=0.02
    )
    pred = fam.fn(res.x, pr, n)
    return {
        "family": fam.key,
        "label": fam.label,
        "theta": dict(zip(fam.names, map(float, res.x), strict=True)),
        "n": len(d),
        "wrmse": float(np.sqrt(np.average((pred - y) ** 2, weights=w**2))),
        "wmape_pct": float(100 * np.average(np.abs(pred - y) / y, weights=w**2)),
    }


def product_fn(gf: Family, sf: Family):
    ng = len(gf.theta0)

    def fn(t, pr, n):
        return gf.fn(t[:ng], pr, n) * sf.fn(t[ng:], pr, n)

    return fn


def fit_oi(df: pd.DataFrame, gf: Family, sf: Family) -> dict:
    pr, n, y, w = df.PR.to_numpy(), df.n_star.to_numpy(), df.eta_oi.to_numpy(), np.sqrt(df.w_record.to_numpy())
    fn = product_fn(gf, sf)
    t0 = tuple(gf.theta0) + tuple(sf.theta0)
    lo, hi = tuple(gf.lower) + tuple(sf.lower), tuple(gf.upper) + tuple(sf.upper)
    res = least_squares(lambda t: w * (fn(t, pr, n) - y), t0, bounds=(lo, hi), loss="soft_l1", f_scale=0.03)
    pred = fn(res.x, pr, n)
    ng = len(gf.theta0)
    return {
        "g": gf.key,
        "s": sf.key,
        "label": f"({gf.label}) x ({sf.label})",
        "theta_g": dict(zip(gf.names, map(float, res.x[:ng]), strict=True)),
        "theta_s": dict(zip(sf.names, map(float, res.x[ng:]), strict=True)),
        "n": len(df),
        "wrmse": float(np.sqrt(np.average((pred - y) ** 2, weights=w**2))),
        "wmape_pct": float(100 * np.average(np.abs(pred - y) / y, weights=w**2)),
    }


def eta_em_anchor(df_all: pd.DataFrame) -> dict:
    """Median measured eta_em at n* ~ 1 on inverter-fed rows with a measured discharge temperature."""
    d = df_all[(df_all.split_basis == "measured_Tdis") & (df_all.P_includes_inverter.astype(str) == "True")]
    near = d[(d.n_star - 1).abs() < 0.05]
    return {
        "anchor": float(near.eta_em.median()),
        "n": int(len(near)),
        "p10": float(near.eta_em.quantile(0.1)),
        "p90": float(near.eta_em.quantile(0.9)),
        "source": sorted(near.source_id.unique().tolist()),
    }


def separability_check(df: pd.DataFrame) -> dict:
    """Does the speed effect on eta_oi depend on PR?  Fit log eta_oi on machines with >= 2 speeds."""
    multi = df.groupby("compressor_key").N_rps.transform("nunique") >= 2
    d = df[multi]
    if len(d) < 50:
        return {"n": int(len(d)), "note": "too few multi-speed rows"}
    X = np.column_stack(
        [np.ones(len(d)), d.PR, d.PR**2, np.log(d.n_star), np.log(d.n_star) ** 2, (d.PR - 3) * np.log(d.n_star)]
    )
    y = np.log(d.eta_oi.to_numpy())
    w = np.sqrt(d.w_record.to_numpy())
    beta, *_ = np.linalg.lstsq(X * w[:, None], y * w, rcond=None)
    resid = y - X @ beta
    dof = len(d) - X.shape[1]
    cov = np.linalg.inv((X * w[:, None]).T @ (X * w[:, None])) * (resid**2 * w**2).sum() / dof
    se = np.sqrt(np.diag(cov))
    return {
        "n": int(len(d)),
        "interaction_coef_(PR-3)lnn": float(beta[-1]),
        "se": float(se[-1]),
        "t": float(beta[-1] / se[-1]),
        "note": "effect of a 1-unit PR change on d ln eta_oi / d ln n*",
    }


def main() -> None:
    all_rows = pd.read_csv(FIT_READY, low_memory=False)
    df = load_ok()
    out = {
        "n_rows": int(len(df)),
        "machines": int(df.compressor_key.nunique()),
        "speed_records": int(df.speed_record.nunique()),
    }
    out["eta_em_anchor"] = eta_em_anchor(all_rows)
    out["separability"] = separability_check(df)
    out["eta_vol"] = [fit_vol(df, f) for f in VOL_FAMILIES]
    out["eta_oi"] = [fit_oi(df, g, s) for g in G_FAMILIES for s in S_FAMILIES]
    out["eta_oi_x"] = [fit_oi_x(df, g, s, x) for g in G_FAMILIES[1:2] for s in S_FAMILIES[1:3] for x in X_FAMILIES]
    best_v = min(out["eta_vol"], key=lambda r: r["wrmse"])
    fam_v = next(f for f in VOL_FAMILIES if f.key == best_v["family"])
    dv = df[~df.vdisp_suspect]
    out["spread_eta_vol"] = machine_level_spread(
        dv, fam_v.fn(np.array(list(best_v["theta"].values())), dv.PR.to_numpy(), dv.n_star.to_numpy()), "eta_vol"
    )
    best_o = min(out["eta_oi"], key=lambda r: r["wrmse"])
    gf = next(f for f in G_FAMILIES if f.key == best_o["g"])
    sf = next(f for f in S_FAMILIES if f.key == best_o["s"])
    th = np.array(list(best_o["theta_g"].values()) + list(best_o["theta_s"].values()))
    out["spread_eta_oi"] = machine_level_spread(
        df, product_fn(gf, sf)(th, df.PR.to_numpy(), df.n_star.to_numpy()), "eta_oi"
    )
    (DATA_DIR / "fit_results.json").write_text(json.dumps(out, indent=1))
    print(
        json.dumps(
            {
                k: out[k]
                for k in (
                    "n_rows",
                    "machines",
                    "speed_records",
                    "eta_em_anchor",
                    "separability",
                    "spread_eta_vol",
                    "spread_eta_oi",
                )
            },
            indent=1,
        )
    )
    print("\neta_vol families (weighted RMSE / MAPE %):")
    for r in out["eta_vol"]:
        print(f"  {r['family']:3s} {r['label']:36s} rmse={r['wrmse']:.4f} mape={r['wmape_pct']:.2f}  {r['theta']}")
    print("\neta_oi families:")
    for r in sorted(out["eta_oi"], key=lambda r: r["wrmse"]):
        print(
            f"  {r['g']}x{r['s']} {r['label']:60s} rmse={r['wrmse']:.4f} mape={r['wmape_pct']:.2f}  g={r['theta_g']} s={r['theta_s']}"
        )
    print("\neta_oi with PR x speed interaction (extension candidates):")
    for r in out["eta_oi_x"]:
        print(
            f"  {r['g']}x{r['s']}x{r['x']} rmse={r['wrmse']:.4f} mape={r['wmape_pct']:.2f}  g={r['theta_g']} s={r['theta_s']} x={r['theta_x']}"
        )


if __name__ == "__main__":
    main()
