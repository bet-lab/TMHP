"""One-off sensitivity: does splitting the compressor set by refrigerant or by
size change the fitted efficiency curves, and does that reach the PLR-COP?

The shipped defaults (``v2026-09-24``) are one correlation fitted to all 76
standalone compressors.  This diagnostic refits **the same functional forms**
on subsets and asks three questions in order:

F1  do the subgroup efficiency curves differ from the pooled one?
F2  is the difference earned out of sample, or is it small-sample noise?
F3  does whatever difference survives reach the assembled heat pump?

Nothing here is a production change: no file under ``src/`` is written, the
library constants are overridden per worker process and restored, and the
fitting forms and estimator are exactly the ones the archive names --
``eta_vol`` V2, ``eta_oi`` I2xE0xL1, fixed-effects estimator.  Only the
coefficients are refitted.

What is *not* refitted
----------------------
``eta_em``.  The split of the fitted product ``eta_oi = eta_isen * eta_em``
into its two factors is not identified by power tables; it is set once from
the only rows with a measured discharge temperature (Cuevas & Lebrun 2009,
one machine).  A subgroup has no independent evidence to move it, so every
group carries the same ``eta_em``, and the whole of a group's difference in
``eta_oi`` lands in ``eta_isen`` by construction.

Groups
------
``A refrigerant``   R410A (39 machines), R32 (10), R407C (14) -- all Copeland
                    variable-speed scrolls, so this contrast is refrigerant at
                    a fixed source and measurement convention.  R290 (9) and
                    R22 (3) are reported as reference only: they are also a
                    change of source and of compressor type.
``B size``          displacement terciles of the 76 machines, cut so each
                    holds about a third of the machines: < 30.7, 30.7-63.3,
                    > 63.3 cm3/rev.
``C refrigerant x size``
                    R410A x tercile -- the only combination with enough
                    machines per cell (12 / 14 / 13).
``D compressor type``
                    scroll (65) against rotary (11).  Not in the plan, added
                    because refrigerant and size are both nearly collinear
                    with the source, and this is the one split where the two
                    populations are different machines rather than different
                    labels on the same machine.

Run::

    uv run python3 -m validation.diagnostics.compressor_group_fitting_once
    uv run python3 -m validation.diagnostics.compressor_group_fitting_once --only fig
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from validation.compressor_maps.fit import (
    FAMILY,
    fit_fe,
    fit_pooled,
    load_ok,
    product3_fn,
    product_bounds,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "validation" / "results" / "compressor_group_fitting_once"

#: The shipped forms.  ``emit_coefficients`` wrote these into the library from
#: the fixed-effects estimator; the sensitivity keeps both fixed.
VOL_FAM = FAMILY["V2"]
OI_FAMS = (FAMILY["I2"], FAMILY["E0"], FAMILY["L1"])
MODE = "fe"
F_SCALE = {"eta_vol": 0.02, "eta_oi": 0.03}
MIN_MACHINES = 3

#: Representative pressure ratios for the curve figure (F1).
PR_SHOW = (2.0, 3.0, 4.5)
#: Speed grid of the curve figure -- the range the heat-pump models reach.
N_GRID = np.linspace(0.2, 1.4, 121)

# --- part-load sweep (F3) --------------------------------------------------
FRACTIONS = tuple(round(1.0 - 0.04 * i, 2) for i in range(23))  # 1.00 .. 0.12
SIM_CASES = (
    # key, model class, capacity [W], refrigerant, duty, outdoor [degC], sink [degC]
    ("ashp_heating", "ASHP", 3500.0, "R32", "heating", 7.0, 20.0),
    ("ashp_cooling", "ASHP", 3500.0, "R32", "cooling", 35.0, 27.0),
    ("ashpb_heating", "ASHPB", 9000.0, "R32", "heating", 7.0, 42.5),
)
#: Coefficient sets carried into the heat-pump sweep.  The refrigerant of both
#: modelled machines is R32, so R32 is *its* refrigerant-specific set and R410A
#: is the contrast; the size sets bracket both machines' displacement.
SIM_GROUPS = ("pooled", "ref=R32", "ref=R410A", "size=Small", "size=Medium", "size=Large", "type=rotary")


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------
def machine_table(df: pd.DataFrame) -> pd.DataFrame:
    return df.groupby("compressor_key").agg(
        source_id=("source_id", "first"),
        refrigerant=("refrigerant", "first"),
        comp_type=("comp_type", "first"),
        V_disp_cm3=("V_disp_cm3", "first"),
        rows=("PR", "size"),
        speed_records=("speed_record", "nunique"),
    )


def size_cuts(m: pd.DataFrame) -> tuple[float, float]:
    """Displacement terciles by machine count (not by row count)."""
    q = np.nanpercentile(m.V_disp_cm3.to_numpy(), [100 / 3, 200 / 3])
    return float(q[0]), float(q[1])


def size_label(v: float, cuts: tuple[float, float]) -> str:
    return "Small" if v < cuts[0] else ("Medium" if v < cuts[1] else "Large")


def build_groups(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """Row masks for every group, plus the per-machine table with its labels."""
    m = machine_table(df)
    cuts = size_cuts(m)
    m["size"] = [size_label(v, cuts) for v in m.V_disp_cm3]
    size_of = m["size"].to_dict()
    df = df.assign(size_group=df.compressor_key.map(size_of))
    groups: dict[str, pd.Series] = {"pooled": pd.Series(True, index=df.index)}
    for ref in ("R410A", "R32", "R407C", "R290", "R22"):
        groups[f"ref={ref}"] = df.refrigerant == ref
    for s in ("Small", "Medium", "Large"):
        groups[f"size={s}"] = df.size_group == s
    for s in ("Small", "Medium", "Large"):
        groups[f"R410A x {s}"] = (df.refrigerant == "R410A") & (df.size_group == s)
    groups["type=scroll"] = df.comp_type == "scroll"
    groups["type=rotary"] = df.comp_type.str.startswith("rotary")
    keep = {k: v for k, v in groups.items() if df.loc[v, "compressor_key"].nunique() >= MIN_MACHINES}
    return df, keep


def case_of(key: str) -> str:
    if key == "pooled":
        return "baseline"
    if key.startswith("ref="):
        return "A refrigerant"
    if key.startswith("size="):
        return "B size"
    if key.startswith("type="):
        return "D compressor type"
    return "C refrigerant x size"


PRIMARY = {
    "pooled",
    "ref=R410A",
    "ref=R32",
    "ref=R407C",
    "size=Small",
    "size=Medium",
    "size=Large",
    "type=scroll",
    "type=rotary",
}


# ---------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------
def _vol_arrays(d: pd.DataFrame):
    dv = d[~d.vdisp_suspect]
    return (
        dv.PR.to_numpy(),
        dv.n_star.to_numpy(),
        dv.eta_vol.to_numpy(),
        np.sqrt(dv.w_record.to_numpy()),
        dv.compressor_key.to_numpy(),
        dv,
    )


def _oi_arrays(d: pd.DataFrame):
    return (
        d.PR.to_numpy(),
        d.n_star.to_numpy(),
        d.eta_oi.to_numpy(),
        np.sqrt(d.w_record.to_numpy()),
        d.compressor_key.to_numpy(),
        d,
    )


ARRAYS = {"eta_vol": _vol_arrays, "eta_oi": _oi_arrays}


def _enough(d: pd.DataFrame, kind: str) -> bool:
    """A group is fitted for ``kind`` only if it still holds enough machines.

    Leave-one-out needs at least two machines left over, and a fixed-effects
    level per machine needs more than one machine to mean anything.
    """
    return pd.Series(ARRAYS[kind](d)[4]).nunique() >= MIN_MACHINES


def model_fn(kind: str):
    if kind == "eta_vol":
        return VOL_FAM.fn, VOL_FAM.theta0, VOL_FAM.lower, VOL_FAM.upper, VOL_FAM.names
    fn = product3_fn(*OI_FAMS)
    t0, lo, hi = product_bounds(*OI_FAMS)
    names = ("A", "B", "C", "c")
    return fn, t0, lo, hi, names


def fit_kind(d: pd.DataFrame, kind: str, mode: str = MODE) -> dict:
    fn, t0, lo, hi, names = model_fn(kind)
    pr, n, y, w, keys, dd = ARRAYS[kind](d)
    if mode == "pooled":
        th = fit_pooled(fn, t0, lo, hi, pr, n, y, w, F_SCALE[kind])
        levels: dict[str, float] = {}
    else:
        th, levels, _ = fit_fe(fn, t0, lo, hi, pr, n, y, w, keys, F_SCALE[kind])
    pred = fn(th, pr, n)
    return {
        "theta": dict(zip(names, map(float, th), strict=True)),
        "n_rows": int(len(dd)),
        "n_machines": int(pd.Series(keys).nunique()),
        "n_multispeed": int(dd.groupby("compressor_key").speed_record.nunique().gt(1).sum()),
        "wrmse": float(np.sqrt(np.average((pred - y) ** 2, weights=w**2))),
        "wmape_pct": float(100 * np.average(np.abs(pred - y) / y, weights=w**2)),
        "between_machine_sd": float(np.std(list(levels.values()))) if levels else float("nan"),
    }


# ---------------------------------------------------------------------------
# leave-one-compressor-out
# ---------------------------------------------------------------------------
_CV: dict = {}


def _cv_init(payload: dict) -> None:
    _CV.update(payload)


def _cv_one(k: str) -> tuple[str, np.ndarray, np.ndarray]:
    """Refit without machine ``k`` and predict its rows, twice.

    ``pred``     population level -- what the shipped default does on a machine
                 nobody has calibrated.
    ``pred_st``  speed transfer -- the machine's record nearest ``n* = 1`` is
                 taken as known and its *other* speed records are predicted.
    """
    kind = _CV["kind"]
    fn, t0, lo, hi, _ = model_fn(kind)
    pr, n, y, w, keys, nrps = (_CV[s] for s in ("pr", "n", "y", "w", "keys", "nrps"))
    m = keys == k
    if MODE == "pooled":
        th = fit_pooled(fn, t0, lo, hi, pr[~m], n[~m], y[~m], w[~m], F_SCALE[kind])
    else:
        th, _, _ = fit_fe(
            fn, t0, lo, hi, pr[~m], n[~m], y[~m], w[~m], keys[~m], F_SCALE[kind], warm=_CV.get("warm")
        )
    p = fn(th, pr[m], n[m])
    ps = np.full(p.shape, np.nan)
    if len(np.unique(nrps[m])) >= 2:
        rec = pd.Series(n[m]).groupby(pd.Series(nrps[m])).median()
        anchor = rec.index[int((rec - 1.0).abs().argmin())]
        at = nrps[m] == anchor
        level = np.exp(np.median(np.log(y[m][at] / p[at])))
        ps = p * level
        ps[at] = np.nan
    return k, p, ps


def loco_preds(d: pd.DataFrame, kind: str, jobs: int) -> tuple[np.ndarray, np.ndarray]:
    """Row-aligned LOCO and speed-transfer predictions for ``d``."""
    pr, n, y, w, keys, dd = ARRAYS[kind](d)
    nrps = dd.N_rps.to_numpy()
    fn, t0, lo, hi, _ = model_fn(kind)
    warm = None
    if MODE == "fe":
        th_all, lam_all, _ = fit_fe(fn, t0, lo, hi, pr, n, y, w, keys, F_SCALE[kind])
        warm = (th_all, lam_all)
    payload = {"kind": kind, "pr": pr, "n": n, "y": y, "w": w, "keys": keys, "nrps": nrps, "warm": warm}
    uniq = np.unique(keys)
    pred = np.full(len(dd), np.nan)
    pred_st = np.full(len(dd), np.nan)
    with ProcessPoolExecutor(max_workers=min(jobs, len(uniq)), initializer=_cv_init, initargs=(payload,)) as ex:
        for k, p, ps in ex.map(_cv_one, uniq.tolist()):
            m = keys == k
            pred[m] = p
            pred_st[m] = ps
    return pd.Series(pred, index=dd.index), pd.Series(pred_st, index=dd.index)


def wmape_on(y: np.ndarray, p: np.ndarray, w: np.ndarray) -> float:
    """Record-weighted MAPE, weights as in ``cv.py`` (``w_record``, not its root)."""
    ok = np.isfinite(p)
    return float(100 * np.average(np.abs(p[ok] - y[ok]) / y[ok], weights=w[ok])) if ok.any() else float("nan")


# ---------------------------------------------------------------------------
# stage 1+2: coefficients and cross-validation
# ---------------------------------------------------------------------------
def run_fit_cv(jobs: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = load_ok()
    df, groups = build_groups(df)
    m = machine_table(df)
    cuts = size_cuts(m)
    print(f"{len(df)} rows, {df.compressor_key.nunique()} machines; size cuts {cuts[0]:.1f} / {cuts[1]:.1f} cm3")

    coef_rows = []
    for key, mask in groups.items():
        d = df[mask]
        rec = {"group": key, "case": case_of(key), "primary": key in PRIMARY}
        for kind in ("eta_vol", "eta_oi"):
            if not _enough(d, kind):
                # ``vdisp_suspect`` machines drop out of the volumetric fit, which
                # can leave a group below the floor for eta_vol but not for eta_oi.
                rec[f"{kind}_n_machines"] = int(pd.Series(ARRAYS[kind](d)[4]).nunique())
                continue
            f = fit_kind(d, kind)
            rec[f"{kind}_n_rows"] = f["n_rows"]
            rec[f"{kind}_n_machines"] = f["n_machines"]
            rec[f"{kind}_n_multispeed"] = f["n_multispeed"]
            rec[f"{kind}_wmape_pct"] = f["wmape_pct"]
            for nm, v in f["theta"].items():
                rec[f"{kind}:{nm}"] = v
        coef_rows.append(rec)
        print(f"  fitted {key:16s} vol={rec['eta_vol_n_machines']:3d}m oi={rec['eta_oi_n_machines']:3d}m")
    coef = pd.DataFrame(coef_rows)

    # baseline LOCO once on the whole set, then within each group
    cv_rows = []
    base_pred: dict[str, tuple[pd.Series, pd.Series]] = {}
    cache = OUT_DIR / "baseline_loco_preds.csv"
    cached = pd.read_csv(cache, index_col=0) if cache.exists() else None
    for kind in ("eta_vol", "eta_oi"):
        if cached is not None and f"{kind}_loco" in cached:
            # NaN is meaningful here (a row outside the volumetric fit, or a
            # machine with one speed record), so it is kept rather than dropped.
            base_pred[kind] = (cached[f"{kind}_loco"], cached[f"{kind}_st"])
            print(f"  baseline LOCO {kind}: reused {cache.name}")
            continue
        t = time.time()
        base_pred[kind] = loco_preds(df, kind, jobs)
        print(f"  baseline LOCO {kind}: {time.time() - t:.0f} s")
    if cached is None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({f"{k}_{s}": base_pred[k][i] for k in base_pred for i, s in enumerate(("loco", "st"))}).to_csv(
            cache
        )
    for key, mask in groups.items():
        d = df[mask]
        rec = {"group": key, "case": case_of(key), "primary": key in PRIMARY}
        for kind in ("eta_vol", "eta_oi"):
            _, _, y, _, keys, dd = ARRAYS[kind](d)
            w = dd.w_record.to_numpy()
            bp, bs = base_pred[kind][0].loc[dd.index], base_pred[kind][1].loc[dd.index]
            rec[f"{kind}_n_rows"] = int(len(dd))
            rec[f"{kind}_n_machines"] = int(pd.Series(keys).nunique())
            rec[f"{kind}_loco_pooled"] = wmape_on(y, bp.to_numpy(), w)
            rec[f"{kind}_st_pooled"] = wmape_on(y, bs.to_numpy(), w)
            if key == "pooled":
                gp, gs = bp, bs
            elif _enough(d, kind):
                gp, gs = loco_preds(d, kind, jobs)
            else:
                continue
            rec[f"{kind}_loco_group"] = wmape_on(y, gp.to_numpy(), w)
            rec[f"{kind}_st_group"] = wmape_on(y, gs.to_numpy(), w)
        cv_rows.append(rec)
        print(
            f"  cv {key:16s} vol {rec.get('eta_vol_loco_group', float('nan')):5.2f} vs "
            f"{rec['eta_vol_loco_pooled']:5.2f} | oi {rec.get('eta_oi_loco_group', float('nan')):5.2f} vs "
            f"{rec['eta_oi_loco_pooled']:5.2f}"
        )
    cv = pd.DataFrame(cv_rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    coef.to_csv(OUT_DIR / "group_coefficients.csv", index=False)
    cv.to_csv(OUT_DIR / "group_cv.csv", index=False)
    m.assign(size_group=[size_label(v, cuts) for v in m.V_disp_cm3]).to_csv(OUT_DIR / "machines.csv")
    (OUT_DIR / "meta.json").write_text(
        json.dumps(
            {
                "generated": datetime.now(UTC).isoformat(timespec="seconds"),
                "python": platform.python_version(),
                "estimator": MODE,
                "eta_vol_family": VOL_FAM.key,
                "eta_oi_family": "x".join(f.key for f in OI_FAMS),
                "size_cuts_cm3": list(cuts),
                "min_machines": MIN_MACHINES,
                "n_star_min": float(df.n_star.min()),
                "n_star_max": float(df.n_star.max()),
            },
            indent=1,
        )
    )
    return coef, cv


# ---------------------------------------------------------------------------
# stage 3: the part-load sweep with each coefficient set
# ---------------------------------------------------------------------------
def coefficient_sets() -> dict[str, dict[str, float]]:
    coef = pd.read_csv(OUT_DIR / "group_coefficients.csv").set_index("group")
    out = {}
    for key in SIM_GROUPS:
        r = coef.loc[key]
        out[key] = {
            "ETA_VOL_A": float(r["eta_vol:a"]),
            "ETA_VOL_B": float(r["eta_vol:b"]),
            "ETA_VOL_C": 0.0,
            "ETA_OI_A": float(r["eta_oi:A"]),
            "ETA_OI_B": float(r["eta_oi:B"]),
            "ETA_OI_C": float(r["eta_oi:C"]),
            "ETA_LEAK_C": float(r["eta_oi:c"]),
        }
    return out


_MODELS: dict = {}


def _apply(coeffs: dict[str, float]) -> None:
    import tmhp.compressor_efficiency as ce

    for k, v in coeffs.items():
        setattr(ce, k, v)


def sim_point(task: dict) -> dict:
    from tmhp import AirSourceHeatPump, AirSourceHeatPumpBoiler

    _apply(task["coeffs"])
    mc, cap, ref = task["model_class"], task["capacity_W"], task["refrigerant"]
    key = (mc, cap, ref)
    if key not in _MODELS:
        _MODELS[key] = (
            AirSourceHeatPump(hp_capacity=cap, ref=ref)
            if mc == "ASHP"
            else AirSourceHeatPumpBoiler(hp_capacity=cap, ref=ref)
        )
    m = _MODELS[key]
    f, t_out, t_sink = task["plr_request"], task["t_outdoor_C"], task["t_sink_C"]
    if mc == "ASHP":
        sign = -1.0 if task["duty"] == "heating" else 1.0
        r = m.analyze_steady(Q_r_iu=sign * cap * f, T0=t_out, T_a_room=t_sink, return_dict=True, verbose=False)
        delivered = abs(_num(r, "Q_ref_iu [W]"))
    else:
        r = m.analyze_steady(T_tank_w=t_sink, T0=t_out, Q_ref_tank=cap * f, return_dict=True)
        delivered = _num(r, "Q_ref_tank [W]")
    assert isinstance(r, dict)
    return {
        **{k: v for k, v in task.items() if k != "coeffs"},
        "V_disp_cm3": m.V_cmp_ref * 1e6,
        "q_delivered_W": delivered,
        "plr_delivered": delivered / cap,
        "capacity_clamped": r.get("capacity_clamped"),
        "failure_reason": r.get("failure_reason", "none"),
        "converged": bool(r.get("converged", False)),
        "n_star": _num(r, "n_star [-]"),
        "pr": _num(r, "pr_cmp [-]"),
        "eta_vol": _num(r, "eta_cmp_vol [-]"),
        "eta_isen": _num(r, "eta_cmp_isen [-]"),
        "eta_em": _num(r, "eta_cmp [-]"),
        "E_cmp": _num(r, "E_cmp [W]"),
        "E_tot": _num(r, "E_tot [W]"),
        "cop_sys": _num(r, "cop_sys [-]"),
    }


def _num(r: dict, key: str) -> float:
    v = r.get(key)
    return float(v) if isinstance(v, (int, float)) else float("nan")


def run_sim(jobs: int) -> pd.DataFrame:
    sets = coefficient_sets()
    tasks = []
    for gkey, coeffs in sets.items():
        for ckey, mc, cap, ref, duty, t_out, t_sink in SIM_CASES:
            for f in FRACTIONS:
                tasks.append(
                    {
                        "group": gkey,
                        "case": ckey,
                        "model_class": mc,
                        "capacity_W": cap,
                        "refrigerant": ref,
                        "duty": duty,
                        "t_outdoor_C": t_out,
                        "t_sink_C": t_sink,
                        "plr_request": f,
                        "coeffs": coeffs,
                    }
                )
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        rows = list(ex.map(sim_point, tasks, chunksize=2))
    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / "plr_group_sensitivity.csv", index=False)
    print(f"plr sweep: {len(df)} rows, failures {(df.failure_reason != 'none').sum()}")
    return df


def sweep_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Peak position and low-load behaviour of each curve, modulating rows only."""
    rows = []
    for (case, group), g in df.groupby(["case", "group"]):
        g = g[g.converged].sort_values("plr_request")
        mod = g[g.capacity_clamped.isna()]
        if not len(mod):
            continue
        i = int(mod.cop_sys.idxmax())
        rated = mod[mod.plr_request == mod.plr_request.max()].cop_sys.iloc[0]
        low = mod[mod.plr_request == mod.plr_request.min()]
        rows.append(
            {
                "case": case,
                "group": group,
                "plr_min_modulating": 100 * mod.plr_request.min(),
                "cop_rated": rated,
                "cop_peak": float(mod.cop_sys.max()),
                "plr_peak_pct": 100 * float(df.loc[i, "plr_request"]),
                "cop_low": float(low.cop_sys.iloc[0]),
                "cop_low_over_rated": float(low.cop_sys.iloc[0]) / rated,
            }
        )
    out = pd.DataFrame(rows)
    base = out[out.group == "pooled"].set_index("case")
    out["d_cop_rated_pct"] = [
        100 * (r.cop_rated / base.loc[r.case, "cop_rated"] - 1) for r in out.itertuples()
    ]
    out["d_cop_low_pct"] = [100 * (r.cop_low / base.loc[r.case, "cop_low"] - 1) for r in out.itertuples()]
    out.to_csv(OUT_DIR / "plr_group_metrics.csv", index=False)
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("SIM_JOBS", "16")))
    ap.add_argument("--only", nargs="*", default=None, choices=["fit", "sim", "fig"])
    a = ap.parse_args()
    want = set(a.only or ["fit", "sim", "fig"])
    if "fit" in want:
        run_fit_cv(a.jobs)
    if "sim" in want:
        df = run_sim(a.jobs)
        print(sweep_metrics(df).to_string(index=False))
    if "fig" in want:
        from validation.diagnostics.compressor_group_fitting_figures import make_figures

        make_figures(OUT_DIR)
    print(f"wrote {OUT_DIR}")


if __name__ == "__main__":
    main()
