"""Selection, final fits, levels, checks and the archive -- everything ``python -m ...semi_empirical`` runs.

Selection rule (fixed before the fit, DESIGN 4.2): for each family, level form and speed coordinate, start from every
candidate loss term and drop terms one at a time (backward elimination); at each step the term whose removal gives
the lowest leave-one-machine-out *shape* RMSE is dropped if that RMSE is not higher than the current one by more
than ``DROP_TOL`` (a term stays only if it lowers the cross-validated error). The speed coordinate is then the
one with the lower leave-one-source-out mean shape RMSE; within ``SPEED_TIE`` the absolute coordinate is kept
(it needs no rated speed, which several sources do not state). LOSO, edge hold-outs, step slopes and parameter
stability are reported for the finalists, the brief's starting form and the shipped-form baseline.

Level form (how the unit level enters, see ``model``): ``add`` (the brief's ``a_u * base + L``) or ``scaled``
(``base + a_u * L``: a machine's losses scale together). The rotary family runs the elimination in both; the scroll
family in ``add`` only (its scaled variant is scored for the selected terms). The level form with the lower LOCO
shape RMSE is kept, ``add`` within ``SPEED_TIE``. The scaled form was added after the first run showed that with
``add`` and the bound ``a_u >= 1`` the shared rotary losses (from Shao, AREP #26, Ossorio) exceed what the modern
twin-rotary rating points allow: most twin levels sat on the bound, i.e. the level was not identified.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.spatial import ConvexHull

from validation.compressor_maps.semi_empirical import cv
from validation.compressor_maps.semi_empirical.model import (
    N_REF,
    Data,
    SemiEmpirical,
    State,
)
from validation.compressor_maps.semi_empirical.sources import LOSO_GROUPS
from validation.compressor_maps.semi_empirical.thermo import state_at_pr, state_k_mep

FULL_TERMS = {
    "scroll": ("q0", "q1", "q2", "cL", "qf", "cu", "cH", "zv"),
    "rotary": ("q0", "q1", "q2", "cL", "qf", "cu", "cH"),
}
START_FORM = ("q0", "q2", "cL")  # the brief's starting point (prototype p01)
DROP_TOL = 1e-4
SPEED_TIE = 1e-3
WORKERS = min(8, os.cpu_count() or 1)

GRID_PR = (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0)
GRID_N = (20.0, 40.0, 60.0, 90.0)
GRID_REF = {"refrigerant": "R410A", "T_cond_C": 45.0, "superheat_K": 10.0}
# rated speed used when the selected speed coordinate is relative (twin: 60 Hz rating; fixed: 50 Hz supply)
N_RATED_DEFAULT = {"scroll": 60.0, "rotary_twin_inverter": 60.0, "rotary_single_fixed": 50.0}
RATING_REF = {"refrigerant": "R410A", "T_evap_C": 7.2, "T_cond_C": 54.4, "superheat_K": 11.1, "N": 60.0}


def shape_rows(d: pd.DataFrame, family: str) -> pd.DataFrame:
    return d[d.use_oi & (d.role == "shape") & (d.family == family)].copy()


def loso_col(family: str) -> str:
    return "group" if family == "scroll" else "source_id"


# ------------------------------------------------------------------------------------------------ LOCO evaluation
def _loco_job(args):
    form, df = args
    st = form.fit(Data.of(df, form.speed))
    pred, thetas = cv.loco(form, df, warm=st)
    return form.name, cv.loco_summary(df, pred), thetas, st


@dataclass
class Evaluator:
    df: dict[str, pd.DataFrame]
    cache: dict = field(default_factory=dict)

    def run(self, forms: list) -> None:
        todo = [f for f in forms if f.name not in self.cache]
        if not todo:
            return
        with ProcessPoolExecutor(max_workers=WORKERS) as ex:
            for name, summ, thetas, st in ex.map(_loco_job, [(f, self.df[f.family]) for f in todo]):
                self.cache[name] = (summ, thetas, st)

    def shape(self, form) -> float:
        self.run([form])
        s = self.cache[form.name][0]
        return float(s.loc[s.group == "all", "rmse_shape"].iloc[0])


def backward(ev: Evaluator, family: str, speed: str, level: str = "add") -> tuple[tuple[str, ...], list[dict]]:
    terms = FULL_TERMS[family]
    full = SemiEmpirical(family, terms, speed, level)
    cur = ev.shape(full)
    hist = [
        {
            "family": family,
            "speed": speed,
            "level": level,
            "step": 0,
            "candidate": full.name,
            "loco_shape": cur,
            "decision": "start",
        }
    ]
    step = 0
    while len(terms) > 1:
        step += 1
        droppable = [t for t in terms if not (level == "scaled" and t == "q2")]  # q2 normalises the scaled form
        if not droppable:
            break
        trials = [SemiEmpirical(family, tuple(x for x in terms if x != t), speed, level) for t in droppable]
        ev.run(trials)
        scores = [(t, ev.shape(f), f) for t, f in zip(droppable, trials, strict=True)]
        best_t, best_r, best_f = min(scores, key=lambda x: (x[1], x[0]))
        accept = best_r <= cur + DROP_TOL
        for t, r, f in scores:
            hist.append(
                {
                    "family": family,
                    "speed": speed,
                    "level": level,
                    "step": step,
                    "candidate": f.name,
                    "loco_shape": r,
                    "decision": (f"drop {t}: accepted" if (t == best_t and accept) else f"drop {t}: not taken"),
                }
            )
        if not accept:
            break
        terms, cur = best_f.terms, best_r
    return terms, hist


# ------------------------------------------------------------------------------------------------ finalist metrics
def finalist_metrics(ev: Evaluator, form, family: str) -> dict:
    df = ev.df[family]
    ev.run([form])
    summ, thetas, st = ev.cache[form.name]
    lo, lth = cv.loso(form, df, LOSO_GROUPS[family], loso_col(family), warm=st)
    ed, eth = cv.edges(form, df, warm=st)
    steps = cv.step_slopes(form, st, df)
    return {
        "form": form,
        "state": st,
        "loco": summ,
        "loso": lo,
        "edges": ed,
        "steps": steps,
        "thetas": thetas + lth + eth,
    }


def stability_table(name: str, form, res: dict) -> pd.DataFrame:
    rows = []
    full = res["state"].theta
    for i, p in enumerate(form.shape_names):
        for kind in ("loco", "loso", "edge"):
            vals = np.array([t[2][i] for t in res["thetas"] if t[0] == kind])
            if len(vals) == 0:
                continue
            rows.append(
                {
                    "candidate": name,
                    "parameter": p,
                    "folds": kind,
                    "n_folds": len(vals),
                    "full_fit": full[i],
                    "min": vals.min(),
                    "median": float(np.median(vals)),
                    "max": vals.max(),
                    "max_abs_dev_from_full": float(np.max(np.abs(vals - full[i]))),
                }
            )
    return pd.DataFrame(rows)


def selection_row(name: str, family: str, res: dict, role: str) -> dict:
    lo, ed, st = res["loso"], res["edges"], res["steps"]
    L = res["loco"].set_index("group")
    row = {
        "family": family,
        "candidate": name,
        "role": role,
        "n_shape_params": len(res["form"].shape_names),
        "loco_shape_all": L.loc["all", "rmse_shape"],
        "loco_total_all": L.loc["all", "rmse_total"],
        "loso_shape_mean": lo.rmse_shape.mean(),
        "loso_level_bias_max_abs": lo.bias_level.abs().max(),
    }
    for g in L.index:
        if g != "all":
            row[f"loco_shape_{g}"] = L.loc[g, "rmse_shape"]
    for _, r in ed[ed.group == "all"].iterrows():
        key = (
            "edge_bias_PR_top"
            if "top" in r.holdout
            else "edge_bias_PR_bottom"
            if "bottom" in r.holdout
            else "edge_bias_low_speed"
        )
        row[key] = r.bias
    if not st.empty:
        a = st[st.source_id == "all"].iloc[0]
        row["step_ratio_model_data"] = a.ratio_model_data
        row["step_same_sign_pct"] = a.same_sign_pct
    if "rv" in res["form"].shape_names:
        vals = [t[2][0] for t in res["thetas"]]
        row["rv_fold_range"] = f"{min(vals):.3f}-{max(vals):.3f}"
    row["parameters"] = "; ".join(
        f"{p}={v:.4g}" for p, v in zip(res["form"].shape_names, res["state"].theta, strict=True)
    )
    return row


def copeland_speed_test(df: pd.DataFrame, terms: tuple[str, ...], level: str = "add") -> pd.DataFrame:
    """Copeland machines whose rated speed is the catalogue's 'Capacity Rating Speed': abs vs rel coordinate."""
    c = df[(df.source_id == "copeland_opi") & (df.N_rated_basis == "rating_row")].copy()
    rows = []
    for sp in ("abs", "rel"):
        f = SemiEmpirical("scroll", terms, sp, level)
        st = f.fit(Data.of(c, sp))
        pred, _ = cv.loco(f, c, warm=st)
        s = cv.loco_summary(c, pred)
        rows.append(
            {
                "speed": sp,
                "machines": int(c.machine.nunique()),
                "rated_speeds_rps": ",".join(f"{v:g}" for v in sorted(c.N_rated_rps.round(1).unique())),
                "loco_shape": float(s.rmse_shape.iloc[0]),
                "loco_total": float(s.rmse_total.iloc[0]),
            }
        )
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------ levels
def scroll_level(form: SemiEmpirical, st: State, df: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    u = df.groupby("unit").agg(
        machine=("machine", "first"),
        source_id=("source_id", "first"),
        group=("group", "first"),
        refrigerant=("refrigerant", "first"),
        V=("V_disp_cm3", "first"),
        target=("target_level", "first"),
    )
    u["a"] = u.index.map(st.levels)
    m = u.groupby("machine").agg(
        a=("a", "median"), V=("V", "first"), target=("target", "first"), group=("group", "first")
    )
    t = m[m.target]
    q = t.a.quantile([0.25, 0.5, 0.75])
    # level-only LOCO over the target machines: median of the others vs a trend in ln V
    err_med, err_tr = [], []
    for name in t.index:
        rest = t.drop(index=name)
        a_med = rest.a.median()
        b = np.polyfit(np.log(rest.V), rest.a, 1)
        a_tr = float(np.polyval(b, np.log(t.loc[name, "V"])))
        rows = df[df.machine == name]
        d = Data.of(rows, form.speed)
        y = d.y
        err_med.append(np.sqrt(np.mean((form.predict(st, d, a_med) - y) ** 2)))
        err_tr.append(np.sqrt(np.mean((form.predict(st, d, a_tr) - y) ** 2)))
    trend = np.polyfit(np.log(t.V), t.a, 1)
    out = {
        "class": "residential-size scroll machines: displacement <= 80 cm3/rev, BITZER excluded",
        "machines": int(len(t)),
        "a_median": float(q.loc[0.5]),
        "a_iqr": [float(q.loc[0.25]), float(q.loc[0.75])],
        "by_group_median": t.groupby("group").a.median().to_dict(),
        "all_machines_median": float(m.a.median()),
        "bitzer_median": float(m[m.group == "BITZER"].a.median()),
        "displacement_trend": {
            "a_vs_lnV_slope": float(trend[0]),
            "level_loco_rmse_median": float(np.mean(err_med)),
            "level_loco_rmse_trend": float(np.mean(err_tr)),
            "adopted": bool(np.mean(err_tr) < np.mean(err_med)),
        },
    }
    return out, m.reset_index()


def rotary_levels(form: SemiEmpirical, st: State, d: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    rows = d[d.use_oi & (d.role == "level") & (d.family == "rotary")]
    lv = form.fit_levels(st, Data.of(rows, form.speed))
    u = rows.groupby("unit").agg(
        machine=("machine", "first"),
        source_id=("source_id", "first"),
        refrigerant=("refrigerant", "first"),
        V=("V_disp_cm3", "first"),
        level_class=("level_class", "first"),
        eta_oi=("eta_oi", "first"),
        PR=("PR", "first"),
        N=("N_rps", "first"),
    )
    u["a"] = u.index.map(lv)
    arep = {k: v for k, v in st.levels.items() if k.startswith("arep_rpt26")}
    out: dict = {}
    tw = u[u.level_class == "rotary_twin_inverter"]
    out["rotary_twin_inverter"] = {
        "class": "LG + Mitsubishi SCI inverter twin rotaries, 60 Hz rating points (power read as excluding the inverter)",
        "machines": int(len(tw)),
        "a_median": float(tw.a.median()),
        "a_iqr": [float(tw.a.quantile(0.25)), float(tw.a.quantile(0.75))],
        "by_refrigerant": {
            r: {
                "machines": int(len(g)),
                "a_median": float(g.a.median()),
                "a_iqr": [float(g.a.quantile(0.25)), float(g.a.quantile(0.75))],
                "eta_oi_median": float(g.eta_oi.median()),
                "eta_oi_iqr": [float(g.eta_oi.quantile(0.25)), float(g.eta_oi.quantile(0.75))],
            }
            for r, g in tw.groupby("refrigerant")
        },
    }
    fx = u[u.level_class == "rotary_single_fixed"]
    a_fx = np.r_[fx.a.to_numpy(), list(arep.values())]
    out["rotary_single_fixed"] = {
        "class": "LG + Panasonic fixed-speed single rotaries at 50 Hz and AREP #26 (R-410A, R-32 units)",
        "machines": int(len(fx)) + 1,
        "a_median": float(np.median(a_fx)),
        "a_iqr": [float(np.quantile(a_fx, 0.25)), float(np.quantile(a_fx, 0.75))],
        "arep26_unit_levels": arep,
        "by_refrigerant": {
            r: {"machines": int(len(g)), "a_median": float(g.a.median()), "eta_oi_median": float(g.eta_oi.median())}
            for r, g in fx.groupby("refrigerant")
        },
    }
    return out, u.reset_index()


# ------------------------------------------------------------------------------------------------ evaluation helpers
def eval_eta(
    form: SemiEmpirical, st: State, level: float, ref: str, pr, n_rps, t_cond: float, sh: float, n_rated: float = 60.0
) -> np.ndarray:
    pr = np.atleast_1d(np.asarray(pr, float))
    n_rps = np.broadcast_to(np.atleast_1d(np.asarray(n_rps, float)), pr.shape)
    k, mep, rho = [], [], []
    for p in pr:
        _, kk, mm, rr = state_at_pr(ref, float(p), t_cond, sh)
        k.append(kk)
        mep.append(mm)
        rho.append(rr)
    d = Data(
        pr=pr,
        k=np.array(k),
        mep=np.array(mep),
        rho=np.array(rho),
        n=n_rps / N_REF if form.speed == "abs" else n_rps / n_rated,
        N=n_rps,
        y=np.zeros_like(pr),
        w=np.ones_like(pr),
        u=np.zeros(len(pr), int),
        units=["default"],
    )
    return form.predict(st, d, level)


def eval_at_condition(form, st, level, ref, te, tc, sh, n_rps, n_rated: float = 60.0) -> float:
    pr, k, mep = state_k_mep(ref, te, tc, sh)
    from validation.compressor_maps.semi_empirical.thermo import suction_state  # noqa: PLC0415

    rho = suction_state(ref, te, tc, te + sh)["rho1"]
    d = Data(
        pr=np.array([pr]),
        k=np.array([k]),
        mep=np.array([mep]),
        rho=np.array([rho]),
        n=np.array([n_rps / N_REF if form.speed == "abs" else n_rps / n_rated]),
        N=np.array([n_rps]),
        y=np.zeros(1),
        w=np.ones(1),
        u=np.zeros(1, int),
        units=["default"],
    )
    return float(form.predict(st, d, level)[0])


def monotonic_check(form, st, level, pr_data_max: float, n_rated: float = 60.0) -> dict:
    out = {}
    pr = np.arange(1.2, 12.01, 0.05)
    for n in (15.0, 20.0, 40.0, 60.0, 90.0, 120.0):
        e = eval_eta(
            form, st, level, GRID_REF["refrigerant"], pr, n, GRID_REF["T_cond_C"], GRID_REF["superheat_K"], n_rated
        )
        beyond = pr >= pr_data_max
        out[f"N{n:g}"] = {
            "within_0_1": bool(np.all((e > 0) & (e <= 1.0))),
            "decreasing_beyond_data": bool(np.all(np.diff(e[beyond]) < 0)),
            "eta_at_PR12": float(e[-1]),
        }
    return out


def hull(df: pd.DataFrame) -> list[list[float]]:
    pts = df[["PR", "N_rps"]].drop_duplicates().to_numpy(float)
    h = ConvexHull(pts)
    return [[round(float(pts[i, 0]), 4), round(float(pts[i, 1]), 3)] for i in h.vertices]
