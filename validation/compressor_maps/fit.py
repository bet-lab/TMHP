"""Pooled and within-machine fits of the three compressor efficiencies on
standalone-compressor data.

Structure (decision D-B, judgement.md; extended 2026-09-24 by plan v3):
eta_vol = f(PR, n*), eta_isen = f(PR, n*), eta_em = f(PR, n*), n* = N / N_rated.
Catalogue heat-pump data never enter here.

What the data identify
----------------------
* eta_vol directly (mass flow, suction density, displacement, speed).
* eta_oi = eta_isen * eta_em directly (power).  The product is modelled as
  ``g(PR) * s(n*) * x(PR, n*)``: a lift shape, a pure speed factor and a
  lift x speed interaction.  Which factor belongs to ``eta_isen`` and which
  to ``eta_em`` is *not* identified by power tables; it is set afterwards
  from the only rows with a measured discharge temperature under TMHP's
  definition (Cuevas & Lebrun 2009, inverter-fed) -- see ``split.py``.

Two estimators
--------------
``pooled``
    One least-squares fit over every row (robust soft-L1, weights so that
    each compressor x speed record counts once).  This is what v2026-09-15b
    used.  Its speed terms are diluted: 67 % of the rows sit at n* = 1 and
    the between-machine level spread (sd 0.07) is larger than any speed
    effect, so a term that is clearly visible *inside* each machine can look
    negligible in the pooled residual.
``fe`` (fixed effects)
    The same shape parameters, but every machine carries its own log-level
    ``lambda_k``, so the shape is identified from within-machine contrasts
    only -- the estimator behind rule R4 in ``cv.py``.  The generic default
    level is the record-weighted mean of the machine levels (each machine
    counts once), so the shipped curve still passes through the population.

Candidate families
------------------
eta_vol: V1  1 - a(PR-1)
         V2  1 - a(PR-1) - b*max(0, 1/n* - 1)            (one-sided, adopted in v2026-09-15b)
         V3  c - a(PR-1) - b/n*                          (V_disp-free intercept)
         V4  c - a(PR-1) - b*exp(-n*/nc)
         V5  c - a(PR-1) - b*max(0, 1/n* - 1)
         V6  1 - a(PR-1) - b*(PR-1)*max(0, 1/n* - 1)     (leakage scales with the pressure difference)
         V7  1 - a(PR-1) - b*max(0,1/n*-1) - c*(PR-1)*max(0,1/n*-1)
g(PR):   I1  A - B*PR
         I2  A - B*PR - C/PR                             (under-compression peak)
         I3  A + B*PR + C*PR^2
s(n*):   E0  1                                           (no speed effect)
         E1  ((1+n0)/(n*+n0)) * n*                       (saturating drive loss, s(1)=1)
         E2  E1 * (1 - d*max(0, n*-1)^2)                 (high-speed roll-off)
         E3  (1-exp(-n*/nc)) / (1-exp(-1/nc))
x(PR,n*):X0  1
         X1  exp(k (PR-3) ln n*)                         (log-linear interaction, v2026-09-15 extension)
         L1  1 - c*(PR-1)*max(0, 1/n*-1)                 (leakage: pressure difference over speed, one-sided)
         L2  1 - c*(PR-1)*(1/n*-1)                       (leakage, two-sided)
Fits: scipy least_squares, loss='soft_l1', weights sqrt(w_record).  Selection
by leave-one-compressor-out CV and the speed-transfer error (``cv.py``).
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
    Family(
        "V6",
        "1 - a(PR-1) - b (PR-1) max(0,1/n*-1)",
        lambda t, pr, n: 1 - t[0] * (pr - 1) - t[1] * (pr - 1) * _pos(1 / n - 1),
        (0.02, 0.01),
        (0, 0),
        (0.2, 0.3),
        ("a", "b"),
    ),
    Family(
        "V7",
        "1 - a(PR-1) - b max(0,1/n*-1) - c (PR-1) max(0,1/n*-1)",
        lambda t, pr, n: 1 - t[0] * (pr - 1) - t[1] * _pos(1 / n - 1) - t[2] * (pr - 1) * _pos(1 / n - 1),
        (0.02, 0.01, 0.01),
        (0, 0, 0),
        (0.2, 0.5, 0.3),
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

# Lift x speed interaction factors.  X1 is the v2026-09-15 extension candidate
# (log-linear).  L1/L2 are the leakage reading of Cuevas & Lebrun 2009 Sec. 3:
# leakage flow is set by the pressure difference and hardly by speed, the swept
# flow is proportional to speed, so the *fraction* re-compressed goes as
# (PR-1)/n*.  L1 is one-sided (no bonus above rated speed), L2 two-sided.
X_FAMILIES = [
    Family("X0", "1", lambda t, pr, n: np.ones_like(n), (), (), (), ()),
    Family(
        "X1",
        "exp(k (PR-3) ln n*)",
        lambda t, pr, n: np.exp(t[0] * (pr - 3.0) * np.log(n)),
        (0.05,),
        (-0.5,),
        (0.5,),
        ("k",),
    ),
    Family(
        "L1",
        "1 - c (PR-1) max(0,1/n*-1)",
        lambda t, pr, n: 1 - t[0] * (pr - 1) * _pos(1 / n - 1),
        (0.02,),
        (0.0,),
        (0.3,),
        ("c",),
    ),
    Family(
        "L2",
        "1 - c (PR-1)(1/n*-1)",
        lambda t, pr, n: 1 - t[0] * (pr - 1) * (1 / n - 1),
        (0.02,),
        (0.0,),
        (0.3,),
        ("c",),
    ),
]


def _split_n0() -> float:
    """Drive + motor n0 measured on the discharge-temperature split rows (``split.py``)."""
    path = DATA_DIR / "split.json"
    if not path.exists():
        raise SystemExit("run validation.compressor_maps.split first: split.json is missing")
    return float(json.loads(path.read_text())["ETA_EM_N0"])


N0_EM = _split_n0()


def _split_n0_drive() -> float:
    """Drive-only n0 (median of the three Ossorio & Navarro-Peris inverters) -- the floor of the drive term."""
    d = json.loads((DATA_DIR / "split.json").read_text()).get("ossorio_drive_only", {})
    return float(d.get("n0_drive_median", N0_EM))


N0_DRIVE = _split_n0_drive()


def _s_fixed(n):
    return n * (1 + N0_EM) / (n + N0_EM)


def _s_drive_floor(n):
    return n * (1 + N0_DRIVE) / (n + N0_DRIVE)


# Speed factors with the drive term *anchored* on the measured electro-mechanical
# efficiency (n0 = N0_EM, no free coefficient).  Every further speed term the
# product then needs is, by construction, assigned to eta_isen:
#   F0  anchored drive loss only
#   F2  + two-sided flow loss  1 - d (n*^2 - 1): pressure losses through the
#       ports grow with the square of speed, so below rated speed the
#       compression is *better*, above it worse
#   F3  + one-sided roll-off above rated speed (the E2 shape)
S_FAMILIES += [
    Family("F0", f"n*(1+n0)/(n*+n0), n0={N0_EM:.4f} fixed", lambda t, pr, n: _s_fixed(n), (), (), (), ()),
    Family(
        "F2",
        "F0 (1 - d (n*^2-1))",
        lambda t, pr, n: _s_fixed(n) * (1 - t[0] * (n * n - 1)),
        (0.02,),
        (0.0,),
        (0.3,),
        ("d",),
    ),
    Family(
        "F3",
        "F0 (1 - d max(0,n*-1)^2)",
        lambda t, pr, n: _s_fixed(n) * (1 - t[0] * _pos(n - 1) ** 2),
        (0.05,),
        (0.0,),
        (2.0,),
        ("d",),
    ),
    # sensitivity of the anchor: the drive-only floor (inverter alone, no motor) instead of the total
    Family("G0", f"n*(1+n0)/(n*+n0), n0={N0_DRIVE:.4f} fixed", lambda t, pr, n: _s_drive_floor(n), (), (), (), ()),
    Family(
        "G2",
        "G0 (1 - d (n*^2-1))",
        lambda t, pr, n: _s_drive_floor(n) * (1 - t[0] * (n * n - 1)),
        (0.02,),
        (0.0,),
        (0.3,),
        ("d",),
    ),
]
FAMILY = {f.key: f for f in VOL_FAMILIES + G_FAMILIES + S_FAMILIES + X_FAMILIES}

#: Product grid.  The pooled fit runs the whole grid; the fixed-effects fit
#: the physically nested subset that the selection walks (cv.py NEST).
OI_GRID = [(g, s, x) for g in G_FAMILIES for s in S_FAMILIES for x in X_FAMILIES]


def oi_key(g: Family, s: Family, x: Family) -> str:
    return f"{g.key}x{s.key}x{x.key}"


def split_key(key: str) -> tuple[str, str, str]:
    parts = key.split("x")
    if len(parts) == 2:  # v2026-09-15 naming ("I2xE1") = identity interaction
        parts.append("X0")
    return parts[0], parts[1], parts[2]


def product3_fn(gf: Family, sf: Family, xf: Family):
    ng, ns = len(gf.theta0), len(sf.theta0)

    def fn(t, pr, n):
        return gf.fn(t[:ng], pr, n) * sf.fn(t[ng : ng + ns], pr, n) * xf.fn(t[ng + ns :], pr, n)

    return fn


def product_fn(gf: Family, sf: Family):
    """Two-factor product (kept for callers written before the interaction axis)."""
    return product3_fn(gf, sf, FAMILY["X0"])


def product_bounds(gf: Family, sf: Family, xf: Family):
    t0 = tuple(gf.theta0) + tuple(sf.theta0) + tuple(xf.theta0)
    lo = tuple(gf.lower) + tuple(sf.lower) + tuple(xf.lower)
    hi = tuple(gf.upper) + tuple(sf.upper) + tuple(xf.upper)
    return t0, lo, hi


def unpack_theta(theta, gf: Family, sf: Family, xf: Family) -> dict:
    ng, ns = len(gf.theta0), len(sf.theta0)
    return {
        "theta_g": dict(zip(gf.names, map(float, theta[:ng]), strict=True)),
        "theta_s": dict(zip(sf.names, map(float, theta[ng : ng + ns]), strict=True)),
        "theta_x": dict(zip(xf.names, map(float, theta[ng + ns :]), strict=True)),
    }


def pack_theta(rec: dict, gf: Family, sf: Family, xf: Family) -> np.ndarray:
    return np.array(
        [rec["theta_g"][k] for k in gf.names]
        + [rec["theta_s"][k] for k in sf.names]
        + [rec.get("theta_x", {}).get(k, 0.0) for k in xf.names]
    )


# ---------------------------------------------------------------------------
# Estimators
# ---------------------------------------------------------------------------
def fit_pooled(fn, t0, lo, hi, pr, n, y, w, f_scale: float) -> np.ndarray:
    return least_squares(lambda t: w * (fn(t, pr, n) - y), t0, bounds=(lo, hi), loss="soft_l1", f_scale=f_scale).x


def fit_fe(
    fn, t0, lo, hi, pr, n, y, w, keys, f_scale: float, warm: tuple[np.ndarray, dict[str, float]] | None = None
) -> tuple[np.ndarray, dict[str, float], float]:
    """Fixed-effects fit in log space: ln y = ln fn(theta) + lambda_k.

    Returns the shape parameters, the per-machine log-levels and the
    population level (mean of lambda_k, one weight per machine).  The mean
    of the machine levels is pinned to zero inside the fit so that the level
    lives in ``theta`` (the g(PR) scale), not in the offsets.
    """
    uniq, idx = np.unique(keys, return_inverse=True)
    nk, npar = len(uniq), len(t0)
    ly = np.log(y)
    root_n = np.sqrt(len(y))

    def resid(p):
        th, lam = p[:npar], p[npar:]
        pred = fn(th, pr, n)
        r = w * (np.log(np.maximum(pred, 1e-6)) + lam[idx] - ly)
        return np.concatenate([r, [root_n * lam.mean()]])

    lam0 = np.zeros(nk)
    th0 = np.asarray(t0, float)
    if warm is not None:  # start from a previous solution (LOCO refits differ by one machine)
        th0 = np.clip(np.asarray(warm[0], float), lo, hi)
        lam0 = np.array([warm[1].get(k, 0.0) for k in uniq.tolist()])
        lam0 -= lam0.mean()
    p0 = np.concatenate([th0, lam0])
    plo = np.concatenate([np.asarray(lo, float), np.full(nk, -1.5)])
    phi = np.concatenate([np.asarray(hi, float), np.full(nk, 1.5)])
    res = least_squares(resid, p0, bounds=(plo, phi), loss="soft_l1", f_scale=f_scale)
    th, lam = res.x[:npar], res.x[npar:]
    return th, dict(zip(uniq.tolist(), map(float, lam), strict=True)), float(lam.mean())


def load_ok() -> pd.DataFrame:
    df = pd.read_csv(FIT_READY, low_memory=False)
    return df[(~df.exclude_fixed) & df.point_ok].copy()


def _metrics(pred, y, w) -> dict:
    return {
        "wrmse": float(np.sqrt(np.average((pred - y) ** 2, weights=w**2))),
        "wmape_pct": float(100 * np.average(np.abs(pred - y) / y, weights=w**2)),
    }


def fit_vol(df: pd.DataFrame, fam: Family, mode: str = "pooled") -> dict:
    d = df[~df.vdisp_suspect]
    pr, n, y, w = d.PR.to_numpy(), d.n_star.to_numpy(), d.eta_vol.to_numpy(), np.sqrt(d.w_record.to_numpy())
    if mode == "pooled":
        th = fit_pooled(fam.fn, fam.theta0, fam.lower, fam.upper, pr, n, y, w, 0.02)
        levels: dict[str, float] = {}
    else:
        th, levels, _ = fit_fe(fam.fn, fam.theta0, fam.lower, fam.upper, pr, n, y, w, d.compressor_key.to_numpy(), 0.02)
    pred = fam.fn(th, pr, n)
    return {
        "family": fam.key,
        "mode": mode,
        "label": fam.label,
        "theta": dict(zip(fam.names, map(float, th), strict=True)),
        "n": len(d),
        **_metrics(pred, y, w),
        "machine_levels": levels,
    }


def fit_oi(df: pd.DataFrame, gf: Family, sf: Family, xf: Family | None = None, mode: str = "pooled") -> dict:
    xf = xf or FAMILY["X0"]
    pr, n, y, w = df.PR.to_numpy(), df.n_star.to_numpy(), df.eta_oi.to_numpy(), np.sqrt(df.w_record.to_numpy())
    fn = product3_fn(gf, sf, xf)
    t0, lo, hi = product_bounds(gf, sf, xf)
    if mode == "pooled":
        th = fit_pooled(fn, t0, lo, hi, pr, n, y, w, 0.03)
        levels: dict[str, float] = {}
    else:
        th, levels, _ = fit_fe(fn, t0, lo, hi, pr, n, y, w, df.compressor_key.to_numpy(), 0.03)
    pred = fn(th, pr, n)
    return {
        "g": gf.key,
        "s": sf.key,
        "x": xf.key,
        "key": oi_key(gf, sf, xf),
        "mode": mode,
        "label": f"({gf.label}) x ({sf.label}) x ({xf.label})",
        **unpack_theta(th, gf, sf, xf),
        "n": len(df),
        **_metrics(pred, y, w),
        "machine_levels": levels,
    }


def fit_oi_x(df: pd.DataFrame, gf: Family, sf: Family, xf: Family) -> dict:
    return fit_oi(df, gf, sf, xf, "pooled")


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


def within_machine_contrasts(df: pd.DataFrame) -> pd.DataFrame:
    """Row-level within-machine speed contrasts: ln(eta / eta at the machine's rated-speed record) at matched (T_evap, T_cond).

    This is the evidence the speed terms rest on, laid out so a reader can
    see it without a model: one row per (machine, off-rated speed record,
    operating point), with the pressure ratio of the point.
    """
    rows = []
    for k, m in df.groupby("compressor_key"):
        if m.N_rps.nunique() < 2:
            continue
        ref_n = m.N_rps.iloc[int((m.n_star - 1).abs().argmin())]
        ref = m[m.N_rps == ref_n]
        for n, g in m.groupby("N_rps"):
            if n == ref_n:
                continue
            mg = g.merge(ref, on=["T_evap_C", "T_cond_C"], suffixes=("", "_r"))
            if len(mg) < 3:
                continue
            for _, r in mg.iterrows():
                rows.append(
                    {
                        "compressor_key": k,
                        "source_id": k.split("::")[0],
                        "n_star": r.n_star,
                        "n_star_ref": r.n_star_r,
                        "PR": r.PR,
                        "d_ln_eta_oi": np.log(r.eta_oi / r.eta_oi_r),
                        "d_ln_eta_vol": np.log(r.eta_vol / r.eta_vol_r) if not r.vdisp_suspect else np.nan,
                    }
                )
    return pd.DataFrame(rows)


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
    out["eta_vol_fe"] = [fit_vol(df, f, "fe") for f in VOL_FAMILIES]
    # v2026-09-15 compatibility: two-factor records keyed "g"/"s" (x = X0)
    out["eta_oi"] = [fit_oi(df, g, s) for g in G_FAMILIES for s in S_FAMILIES]
    out["eta_oi_x"] = [fit_oi(df, g, s, x) for g, s, x in OI_GRID if x.key != "X0"]
    out["eta_oi_fe"] = [fit_oi(df, g, s, x, "fe") for g, s, x in OI_GRID]
    out["N0_EM"] = N0_EM
    best_v = min(out["eta_vol"], key=lambda r: r["wrmse"])
    fam_v = FAMILY[best_v["family"]]
    dv = df[~df.vdisp_suspect]
    out["spread_eta_vol"] = machine_level_spread(
        dv, fam_v.fn(np.array(list(best_v["theta"].values())), dv.PR.to_numpy(), dv.n_star.to_numpy()), "eta_vol"
    )
    best_o = min(out["eta_oi"], key=lambda r: r["wrmse"])
    gf, sf = FAMILY[best_o["g"]], FAMILY[best_o["s"]]
    th = pack_theta(best_o, gf, sf, FAMILY["X0"])
    out["spread_eta_oi"] = machine_level_spread(
        df, product_fn(gf, sf)(th, df.PR.to_numpy(), df.n_star.to_numpy()), "eta_oi"
    )
    (DATA_DIR / "fit_results.json").write_text(json.dumps(out, indent=1))
    within_machine_contrasts(df).to_csv(DATA_DIR / "within_machine_contrasts.csv", index=False)
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
    for mode, key in (("pooled", "eta_vol"), ("fixed-effects", "eta_vol_fe")):
        print(f"\neta_vol families, {mode} (weighted RMSE / MAPE %):")
        for r in out[key]:
            print(f"  {r['family']:3s} {r['label']:52s} rmse={r['wrmse']:.4f} mape={r['wmape_pct']:.2f}  {r['theta']}")
    print("\neta_oi families, pooled:")
    for r in sorted(out["eta_oi"] + out["eta_oi_x"], key=lambda r: r["wrmse"]):
        print(
            f"  {r['key']:12s} rmse={r['wrmse']:.4f} mape={r['wmape_pct']:.2f}  g={r['theta_g']} s={r['theta_s']} x={r['theta_x']}"
        )
    print("\neta_oi families, fixed effects (I2 only):")
    for r in sorted(out["eta_oi_fe"], key=lambda r: r["wrmse"]):
        print(
            f"  {r['key']:12s} rmse={r['wrmse']:.4f} mape={r['wmape_pct']:.2f}  g={r['theta_g']} s={r['theta_s']} x={r['theta_x']}"
        )


if __name__ == "__main__":
    main()
