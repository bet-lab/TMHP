"""Volumetric efficiency: a form positive by construction, its shape from compressor data, its level from the
catalogue rating points that also fix the default displacement.

Why (DESIGN 12, defect 1): the archive's first eta_v form ``c0 - c_re (PR^(1/k) - 1) - c_Lv (PR - 1)/n`` crosses
zero at high PR and low relative speed (the leakage term is linear in 1/n), and rested on one Shao 2004 machine. The
form here is :class:`~validation.compressor_maps.semi_empirical.model.EtaVBounded`::

    eta_v = c0 * exp(-c_re (PR^(1/k) - 1)) / (1 + c_L (PR - 1)/n + c_s/n + c_f n^2)

**Shape** (rotary). Rows with a mass flow from the sources that measure a pressure-ratio or speed dependence, each
unit (machine x refrigerant x campaign) with its own level, so only the shape is shared:

* Shao 2004, the one machine whose displacement the archive confirms (Mitsubishi RHV207FEM, 72 rows, 30-120 rev/s);
* AREP #26 (Shanghai Hitachi 28 cc, measured mass flow at fixed 50 Hz, PR 2.3-6.3, R-410A and R-32). Its rated
  eta_v (0.83-0.85) is under the archive's 0.85 displacement bracket; a 50 Hz induction motor turns slower than the
  nominal 50 rev/s by its slip, which is a level effect, carried by the unit level;
* Ossorio 2019 (R290, measured, 30-120 rev/s at one condition). Its displacement was inferred from the printed
  efficiency: a level effect, carried by the unit level; it informs the speed shape only.

The two other Shao machines (displacement not confirmed) are left out of the shape: their speed records disagree with
every other source (Hitachi SGZ20DB2-Y 0.54 at 30 Hz against 0.81 at 120 Hz) and the cause is not known. They are
scored as a sensitivity. Speed coordinate: relative ``n = N/N_rated`` (the archive's rotary selection; the level is
read at the machine's own rated point). Selection: the archive's rule -- backward elimination from every candidate
term on the leave-one-machine-out shape RMSE, a term dropped unless it lowers that RMSE by more than ``DROP_TOL``.

**Level** (rotary). Every eligible catalogue rating point of
``validation/default_inputs/compressor_displacement/measurements.csv`` (the rows the default displacement regression
pools: capacity, displacement and test frequency; power is not needed) gives eta_v at its own condition and
``n = 1``. ``c0`` per point is that eta_v over the fitted shape there; the type's level is the median (IQR) over the
points of its basis -- 41 inverter twin rotaries (60 rev/s) and 98 fixed-speed single rotaries (50 Hz supply
frequency, the regression's speed convention). No eta_v bracket is applied to the rating points: the bracket would
select on the quantity being measured.

**Scroll.** Same form with the archive's two terms (clearance and leakage), absolute speed ``n = N/50``, refitted on
the archive's scroll eta_v rows; its level stays the median of the unit levels. The linear form is positive over the
default speed range (>= 15 rev/s) but crosses zero below it at high PR, and the models accept any ``rps_min > 0``.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from validation.compressor_maps.semi_empirical import cv
from validation.compressor_maps.semi_empirical.model import ETA_V_TERMS, Data, EtaV, EtaVBounded, State
from validation.compressor_maps.semi_empirical.thermo import _props, k_effective, suction_state

DROP_TOL = 1e-4
WORKERS = min(8, os.cpu_count() or 1)
UNCONFIRMED = "displacement not confirmed (eta_v fit only)"
#: rotary shape machines whose missing displacement confirmation is a level effect only (see module docstring)
LEVEL_ONLY_UNCONFIRMED = ("arep_rpt26_hitachi2013", "ossorio_2019_cnit")
SPEED = {"scroll": "abs", "rotary": "rel"}
SCROLL_TERMS = ("c_re", "c_L")
LOSO_V = ("shao_2004", "ossorio_2019_cnit", "arep_rpt26_hitachi2013")
#: displacement-regression basis of each rotary type (``regression.json`` -> ``normalized_fits``)
BASIS = {"rotary_twin_inverter": "rotary_twin_inverter_60rps", "rotary_single_fixed": "rotary_single_fixed_50Hz"}
# envelope grid of the bounds check: PR beyond the single-stage limit 11, every k the forms meet, speeds from the
# lowest the models reach (15 rev/s against a rated speed up to the 150 rev/s rated-speed search bound) and beyond
ENV_PR = np.r_[np.linspace(1.01, 2.0, 12), np.linspace(2.0, 15.0, 53)[1:]]
ENV_K = (0.9, 1.0, 1.05, 1.1, 1.15, 1.2, 1.3)
ENV_N = {
    "rel": np.r_[0.05, 0.1, np.linspace(0.15, 3.0, 20)],
    "abs": np.array([2.5, 5, 7.5, 10, 15, 20, 30, 45, 60, 90, 120, 150]) / 50.0,
}


def shape_rows(d: pd.DataFrame, family: str, with_unconfirmed_shao: bool = False) -> pd.DataFrame:
    """The eta_v shape rows of ``family`` (see the module docstring for the rotary rule)."""
    base = (d.family == family) & (d.role == "shape") & d.eta_v.notna()
    if family == "scroll":
        return d[base & d.use_v].copy()
    ok = d.reason_v == ""
    ok |= (d.reason_v == UNCONFIRMED) & d.source_id.isin(LEVEL_ONLY_UNCONFIRMED)
    if with_unconfirmed_shao:
        ok |= (d.reason_v == UNCONFIRMED) & (d.source_id == "shao_2004")
    return d[base & ok].copy()


# ------------------------------------------------------------------------------------------------ CV
def _loco_job(args):
    form, df = args
    st = form.fit(Data.of(df, form.speed, ycol="eta_v"))
    pred, thetas = cv.loco(form, df, ycol="eta_v", warm=st)
    return form.name, cv.loco_summary(df, pred, ycol="eta_v"), thetas, st


class Evaluator:
    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df
        self.cache: dict = {}

    def run(self, forms: list) -> None:
        todo = [f for f in forms if f.name not in self.cache]
        if not todo:
            return
        with ProcessPoolExecutor(max_workers=WORKERS) as ex:
            for name, summ, thetas, st in ex.map(_loco_job, [(f, self.df) for f in todo]):
                self.cache[name] = (summ, thetas, st)

    def shape(self, form) -> float:
        self.run([form])
        s = self.cache[form.name][0]
        return float(s.loc[s.group == "all", "rmse_shape"].iloc[0])


def backward(ev: Evaluator, family: str, speed: str) -> tuple[tuple[str, ...], list[dict]]:
    terms = ETA_V_TERMS
    cur = ev.shape(EtaVBounded(family, speed, terms))
    hist = [{"step": 0, "candidate": EtaVBounded(family, speed, terms).name, "loco_shape": cur, "decision": "start"}]
    step = 0
    while len(terms) > 1:
        step += 1
        trials = [EtaVBounded(family, speed, tuple(x for x in terms if x != t)) for t in terms]
        ev.run(trials)
        scores = [(t, ev.shape(f), f) for t, f in zip(terms, trials, strict=True)]
        best_t, best_r, best_f = min(scores, key=lambda x: (x[1], x[0]))
        accept = best_r <= cur + DROP_TOL
        for t, r, f in scores:
            hist.append(
                {
                    "step": step,
                    "candidate": f.name,
                    "loco_shape": r,
                    "decision": f"drop {t}: accepted" if (t == best_t and accept) else f"drop {t}: not taken",
                }
            )
        if not accept:
            break
        terms, cur = best_f.terms, best_r
    return terms, hist


def metrics(ev: Evaluator, form, loso_groups: tuple[str, ...] | None) -> dict:
    df = ev.df
    ev.run([form])
    summ, thetas, st = ev.cache[form.name]
    row = {
        "candidate": form.name,
        "loco_shape": float(summ.loc[summ.group == "all", "rmse_shape"].iloc[0]),
        "loco_total": float(summ.loc[summ.group == "all", "rmse_total"].iloc[0]),
        "parameters": "; ".join(f"{p}={v:.4g}" for p, v in zip(form.shape_names, st.theta, strict=True)),
    }
    if loso_groups:
        lo, _ = cv.loso(form, df, loso_groups, "source_id", ycol="eta_v", warm=st)
        row["loso_shape_mean"] = float(lo.rmse_shape.mean())
        for _, r in lo.iterrows():
            row[f"loso_shape_{r.held_out}"] = r.rmse_shape
    ed, _ = cv.edges(form, df, ycol="eta_v", warm=st)
    for _, r in ed[ed.group == "all"].iterrows():
        key = "top" if "top" in r.holdout else "bottom" if "bottom" in r.holdout else "low_speed"
        row[f"edge_bias_{key}"] = r.bias
    return {"row": row, "state": st, "loco": summ}


# ------------------------------------------------------------------------------------------------ rating points
def rating_points(path) -> pd.DataFrame:
    """eta_v at every eligible catalogue rating point (``n = 1``), with PR and k of its condition."""
    m = pd.read_csv(path)
    m = m[(m.kind == "compressor") & m.eligible.astype(bool)].copy()
    rows = []
    for r in m.itertuples():
        st = suction_state(r.refrigerant_normalized, r.T_evap_C, r.T_cond_C, r.T_suction_C)
        h_liq = _props("H", "P", st["p_dis"], "T", r.T_liquid_C + 273.15, st["fluid"])
        mdot = r.capacity_W / (st["h1"] - h_liq)
        x = st["dh_is"] * st["rho1"] / st["p_suc"]
        k = float(k_effective(np.array([st["PR"]]), np.array([x]))[0])
        rows.append(
            {
                "basis": r.basis,
                "manufacturer": r.manufacturer,
                "family_id": r.family_id,
                "model_id": r.model_id,
                "refrigerant": r.refrigerant_normalized,
                "protocol": r.protocol,
                "has_power": bool(pd.notna(r.power_W)),
                "N_rps": float(r.frequency_Hz),
                "PR": st["PR"],
                "k": k,
                "eta_v": mdot / (st["rho1"] * r.value * 1e-6 * r.frequency_Hz),
                "eta_vol_rps_per_f_regression": float(r.eta_vol_rps_per_f),
            }
        )
    return pd.DataFrame(rows)


def _data_n1(t: pd.DataFrame) -> Data:
    n = len(t)
    return Data(
        pr=t.PR.to_numpy(float),
        k=t.k.to_numpy(float),
        mep=np.ones(n),
        rho=np.ones(n),
        n=np.ones(n),
        N=t.N_rps.to_numpy(float),
        y=t.eta_v.to_numpy(float),
        w=np.ones(n),
        u=np.zeros(n, int),
        units=["p"],
    )


def rating_levels(form, st: State, pts: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """c0 of every rating point (eta_v over the shape at its condition, n = 1); median and IQR per type."""
    pts = pts.copy()
    if isinstance(form, EtaVBounded):
        pts["c0"] = pts.eta_v / form.shape(st.theta, _data_n1(pts))
    else:  # linear form: additive intercept
        pts["c0"] = pts.eta_v - form.X(_data_n1(pts)) @ st.theta
    out = {}
    for t, basis in BASIS.items():
        g = pts[pts.basis == basis]
        out[t] = {
            "basis": basis,
            "points": int(len(g)),
            "points_with_power": int(g.has_power.sum()),
            "manufacturers": sorted(g.manufacturer.unique().tolist()),
            "c0_median": float(g.c0.median()),
            "c0_iqr": [float(g.c0.quantile(0.25)), float(g.c0.quantile(0.75))],
            "eta_v_at_rating_median": float(g.eta_v.median()),
            "eta_v_at_rating_iqr": [float(g.eta_v.quantile(0.25)), float(g.eta_v.quantile(0.75))],
            "PR_median": float(g.PR.median()),
            "by_refrigerant": {
                r: {"points": int(len(x)), "c0_median": float(x.c0.median()), "eta_v_median": float(x.eta_v.median())}
                for r, x in g.groupby("refrigerant")
            },
        }
    return out, pts


def eval_at(form, st: State, c0: float, ref: str, te: float, tc: float, t_suc: float, n: float) -> float:
    s = suction_state(ref, te, tc, t_suc)
    x = s["dh_is"] * s["rho1"] / s["p_suc"]
    k = float(k_effective(np.array([s["PR"]]), np.array([x]))[0])
    d = _data_n1(pd.DataFrame({"PR": [s["PR"]], "k": [k], "N_rps": [60.0], "eta_v": [0.0]}))
    d.n = np.array([n])
    return float(form.predict(st, d, c0)[0])


def regression_consistency(form, st: State, levels: dict, regression: dict) -> dict:
    """Model eta_v at each regression basis's reference condition (n = 1) against the basis's pooled eta_vol."""
    out = {}
    for t, basis in BASIS.items():
        b = regression["normalized_fits"][basis]
        proto = regression["protocols"][b["protocols"][0]]
        vals = {}
        for ref in ("R32", "R410A", "R290"):
            vals[ref] = eval_at(
                form, st, levels[t]["c0_median"], ref, proto["T_evap_C"], proto["T_cond_C"], proto["T_suction_C"], 1.0
            )
        out[t] = {
            "basis": basis,
            "reference_protocol": b["protocols"][0],
            "regression_eta_vol_rps_per_f": float(b["selected_law"]["eta_vol_rps_per_f"]),
            "model_eta_v_at_reference": vals,
        }
    return out


# ------------------------------------------------------------------------------------------------ bounds, speed shape
def envelope(form, st: State, c0: float, speed: str) -> dict:
    """min / max of eta_v over the envelope grid and whether it falls with PR and with falling speed everywhere."""
    pr, n = np.meshgrid(ENV_PR, ENV_N[speed], indexing="ij")
    mins, maxs, mono_pr, mono_n = [], [], True, True
    for k in ENV_K:
        nn = pr.size
        d = Data(
            pr=pr.ravel(),
            k=np.full(nn, k),
            mep=np.ones(nn),
            rho=np.ones(nn),
            n=n.ravel(),
            N=n.ravel() * 50.0,
            y=np.zeros(nn),
            w=np.ones(nn),
            u=np.zeros(nn, int),
            units=["p"],
        )
        e = form.predict(st, d, c0).reshape(pr.shape)
        mins.append(float(e.min()))
        maxs.append(float(e.max()))
        mono_pr &= bool(np.all(np.diff(e, axis=0) < 0))
        low = n[0] <= 1.0  # below the rated / reference speed
        mono_n &= bool(np.all(np.diff(e[:, low], axis=1) > 0))
    return {
        "PR_range": [float(ENV_PR.min()), float(ENV_PR.max())],
        "n_range": [float(ENV_N[speed].min()), float(ENV_N[speed].max())],
        "k_values": list(ENV_K),
        "min": min(mins),
        "max": max(maxs),
        "decreasing_in_PR": mono_pr,
        "increasing_with_speed_up_to_n_1": mono_n,
    }


def speed_ratios(form, st: State, rows: pd.DataFrame) -> pd.DataFrame:
    """eta_v(N)/eta_v(60) of the data and the model, per multi-speed unit, at PR bins of its records."""
    out = []
    for unit, g in rows.groupby("unit"):
        if g.N_rps.round(0).nunique() < 3:
            continue
        lv = form.fit_levels(st, Data.of(g, form.speed, ycol="eta_v"))
        pred = form.predict(st, Data.of(g, form.speed, ycol="eta_v"), lv)
        t = g.assign(pred=pred, Nr=g.N_rps.round(0), PRb=g.PR.round(1))
        for prb, h in t.groupby("PRb"):
            m = h.groupby("Nr")[["eta_v", "pred"]].mean()
            if 60.0 not in m.index:
                continue
            for n in (30.0, 90.0, 120.0):
                if n in m.index:
                    out.append(
                        {
                            "unit": unit,
                            "source_id": g.source_id.iloc[0],
                            "PR": prb,
                            "N": n,
                            "data": m.loc[n, "eta_v"] / m.loc[60.0, "eta_v"],
                            "model": m.loc[n, "pred"] / m.loc[60.0, "pred"],
                        }
                    )
    r = pd.DataFrame(out)
    if r.empty:
        return r
    return r.groupby(["source_id", "N"])[["data", "model"]].mean().reset_index()


# ------------------------------------------------------------------------------------------------ driver
def fit_all(d: pd.DataFrame, rating_path, regression: dict) -> dict:
    """Everything the archive records about eta_v (rotary selection and levels, scroll refit, old-form comparison)."""
    out: dict = {"tables": {}}
    # rotary
    rot = shape_rows(d, "rotary")
    ev = Evaluator(rot)
    terms, hist = backward(ev, "rotary", SPEED["rotary"])
    sel = EtaVBounded("rotary", SPEED["rotary"], terms)
    old = EtaV("rotary", SPEED["rotary"], True)
    m_sel = metrics(ev, sel, LOSO_V)
    m_old = metrics(ev, old, LOSO_V)
    cands = [("selected (bounded)", sel, m_sel), ("archive linear form V1", old, m_old)]
    for t in ETA_V_TERMS:  # each single-term bounded form, for the identification statement
        f = EtaVBounded("rotary", SPEED["rotary"], (t,) if t == "c_re" else ("c_re", t))
        if f.name != sel.name:
            cands.append((f"bounded {'+'.join(f.terms)}", f, metrics(ev, f, LOSO_V)))
    # sensitivity: with the two Shao machines whose displacement is not confirmed
    rot_s = shape_rows(d, "rotary", with_unconfirmed_shao=True)
    ev_s = Evaluator(rot_s)
    terms_s, _ = backward(ev_s, "rotary", SPEED["rotary"])
    sel_s = EtaVBounded("rotary", SPEED["rotary"], terms_s)
    m_s = metrics(ev_s, sel_s, LOSO_V)
    st = m_sel["state"]
    pts = rating_points(rating_path)
    levels, pts = rating_levels(sel, st, pts)
    _, pts_old = rating_levels(old, m_old["state"], pts.drop(columns="c0"))
    hi = d[(d.source_id == "highly_catalogue_2024") & d.eta_v.notna()]
    hpred = sel.predict(st, Data.of(hi, sel.speed, ycol="eta_v"), levels["rotary_twin_inverter"]["c0_median"])
    out["rotary"] = {
        "form": "eta_v = c0 * exp(-c_re*(PR^(1/k) - 1)) / (1 + c_L*(PR - 1)/n + c_s/n + c_f*n^2)",
        "candidate": sel.name,
        "terms": list(terms),
        "speed": SPEED["rotary"],
        "parameters": dict(zip(sel.shape_names, st.theta.tolist(), strict=True)),
        "shape_rows": int(len(rot)),
        "shape_machines": sorted(rot.machine.unique().tolist()),
        "shape_units_levels": st.levels,
        "levels": levels,
        "regression_consistency": regression_consistency(sel, st, levels, regression),
        "highly_2024_check": {
            "rows": int(len(hi)),
            "data_eta_v_median": float(hi.eta_v.median()),
            "model_median_twin_level": float(np.median(hpred)),
        },
        "sensitivity_with_unconfirmed_shao": {
            "terms": list(terms_s),
            "parameters": dict(zip(sel_s.shape_names, m_s["state"].theta.tolist(), strict=True)),
            "loco_shape": m_s["row"]["loco_shape"],
        },
        "old_form": {
            "parameters": dict(zip(old.shape_names, m_old["state"].theta.tolist(), strict=True)),
            "c0_by_type_from_the_same_points": {
                t: float(pts_old[pts_old.basis == b].c0.median()) for t, b in BASIS.items()
            },
        },
    }
    out["tables"]["eta_v_backward"] = pd.DataFrame(hist).assign(family="rotary")
    sel_rows = [{"family": "rotary", "role": n, **m["row"]} for n, _, m in cands]
    sel_rows.append({"family": "rotary", "role": "sensitivity: with the unconfirmed Shao machines", **m_s["row"]})
    sr = []
    for name, f, m in cands[:2]:
        sr.append(speed_ratios(f, m["state"], rot).assign(candidate=name))
    out["tables"]["eta_v_speed_ratios"] = pd.concat(sr, ignore_index=True)
    out["tables"]["eta_v_rating_points"] = pts
    # scroll
    scr = shape_rows(d, "scroll")
    ev_c = Evaluator(scr)
    f_c = EtaVBounded("scroll", SPEED["scroll"], SCROLL_TERMS)
    o_c = EtaV("scroll", SPEED["scroll"], True)
    m_c, m_co = metrics(ev_c, f_c, None), metrics(ev_c, o_c, None)
    sel_rows += [
        {"family": "scroll", "role": "selected (bounded, archive terms)", **m_c["row"]},
        {"family": "scroll", "role": "archive linear form V1", **m_co["row"]},
    ]
    st_c = m_c["state"]
    out["scroll"] = {
        "form": out["rotary"]["form"],
        "candidate": f_c.name,
        "terms": list(SCROLL_TERMS),
        "speed": SPEED["scroll"],
        "parameters": dict(zip(f_c.shape_names, st_c.theta.tolist(), strict=True)),
        "c0": f_c.population_level(st_c),
        "c0_iqr": [float(np.quantile(list(st_c.levels.values()), q)) for q in (0.25, 0.75)],
        "rows": int(len(scr)),
        "machines": int(scr.machine.nunique()),
        "old_form": {
            "parameters": dict(zip(o_c.shape_names, m_co["state"].theta.tolist(), strict=True)),
            "c0": o_c.population_level(m_co["state"]),
        },
    }
    out["tables"]["eta_v_selection"] = pd.DataFrame(sel_rows)
    # bounds over the envelope, new and old
    env = []
    for t in ("scroll", "rotary_twin_inverter", "rotary_single_fixed"):
        fam = "scroll" if t == "scroll" else "rotary"
        if fam == "scroll":
            c0n, c0o = out["scroll"]["c0"], out["scroll"]["old_form"]["c0"]
            fn, sn, fo, so = f_c, st_c, o_c, m_co["state"]
        else:
            c0n = levels[t]["c0_median"]
            c0o = out["rotary"]["old_form"]["c0_by_type_from_the_same_points"][t]
            fn, sn, fo, so = sel, st, old, m_old["state"]
        en, eo = envelope(fn, sn, c0n, SPEED[fam]), envelope(fo, so, c0o, SPEED[fam])
        out.setdefault("envelope", {})[t] = {"new": en, "old_linear": eo}
        env.append({"type": t, "form": "new", **{k: v for k, v in en.items() if not isinstance(v, list)}})
        env.append({"type": t, "form": "old linear", **{k: v for k, v in eo.items() if not isinstance(v, list)}})
    out["tables"]["eta_v_envelope"] = pd.DataFrame(env)
    return out
