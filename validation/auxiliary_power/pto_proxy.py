"""Thermostat-off power as a measured proxy for non-vanishing parasitic power.

What this is *not*
------------------
This is **not** ``P_aux,active``. The auxiliary-power plan asks for

    P_aux,active = P_total - P_comp - P_fan - P_pump

measured on one unit at one rating condition, and forbids backing it out of a
model prediction. That dataset could not be built: catalogues give the
compressor as locked-rotor/running amps and the fan as motor *output*, never
compressor input in watts, and the laboratory studies that do sub-meter publish
either an aggregated indoor/outdoor split or a power-meter uncertainty (50 W)
larger than the quantity being fitted. Subtracting two ~3 kW numbers to recover
a ~15 W residual needs the two to agree to 0.5 %; nothing accessible does.

What this *is*
--------------
``P_TO`` -- thermostat-off mode -- is declared in watts on every Heat Pump
Keymark certificate and is measured by an accredited laboratory: the unit is
on, the controls, sensors and communications are live, and the compressor is
off. It is therefore a **lower bound proxy** for the parasitic power that does
not vanish at low load. It is used here only to answer one question -- *is the
real, measured parasitic level big enough to produce the low-load COP
behaviour that Appendix D showed a fixed 25-50 W would produce?* -- and is
never adopted as a TMHP coefficient.

Variable identification
-----------------------
hplib republishes Keymark records under EN 14825 numeric codes with no legend.
``EN14825_024 = P_TO`` was established against the manufacturer's own ErP
fiches rather than assumed: Bosch CS2000AWF 6/12/16 R-S declare P_TO = 24 W and
carry ``EN14825_024 = 24``; CS2000AWF 30 R-T declares P_TO = 84 W against
``EN14825_024 = 96`` (a later certificate revision, same structure -- one value
about five times the others). ``EN14825_026 = P_CK`` is zero on 70 % of
records, the crankcase-heater signature. ``EN14825_023`` and ``_025`` are
P_OFF and P_SB in an order this module does not need and does not claim.

``EN14511_2_005`` is the EN 14511 rated heating capacity: for Ariston KYRIS NET
R32 25/35 it reads 2.64/3.52 kW against 0.54/0.75 kW input and declared COP
4.90/4.70, and 2.64/0.54 = 4.89, 3.52/0.75 = 4.69.

Air-to-air is the population that matters here -- it has no circulation pump,
so its P_TO is controls and electronics rather than controls plus a pump -- and
it is small (19 certificates). Air-to-water is carried as a large-N shape
check only, and its P_TO is *not* comparable in level.

Run::

    uv run python3 -m validation.auxiliary_power.pto_proxy
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

csv.field_size_limit(10**7)

REPO_ROOT = Path(__file__).resolve().parents[2]
HPLIB = REPO_ROOT / "validation" / "evidence" / "data" / "hplib" / "csv"
DATA_DIR = REPO_ROOT / "validation" / "data" / "auxiliary_power"
OUT_DIR = REPO_ROOT / "validation" / "results" / "auxiliary_power_pto_proxy"
APPENDIX_D_CSV = (
    REPO_ROOT / "validation" / "results" / "low_load_fixed_power_sh_sc_once" / "low_load_fixed_power_sh_sc_once.csv"
)

SOURCE = "Heat Pump Keymark via hplib (FZJ-IEK3-VSA, MIT, doi:10.5281/zenodo.5521597)"
TMHP_Q_RATED_KW = 3.5

#: W_fixed values Appendix D swept, for the direct comparison.
APPENDIX_D_W = (0.0, 25.0, 50.0)


def _f(v):
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return None


def _outdoor_unit(model: str) -> str:
    """Certificates pair one outdoor unit with several indoor units.

    Three rows of the same outdoor unit are not three independent measurements
    of P_TO, so the cross-validation holds out the *outdoor unit*.
    """
    for sep in ("+", "/"):
        if sep in model:
            return model.split(sep)[0].strip()
    return model.strip()


def extract() -> tuple[pd.DataFrame, pd.DataFrame]:
    a2a, a2w = {}, {}
    for f in sorted(glob.glob(str(HPLIB / "*.csv"))):
        try:
            rs = [r for r in csv.DictReader(open(f, encoding="utf-8", errors="replace")) if r.get("varName")]
        except Exception:
            continue
        typ = next((r["value"] for r in rs if r["varName"] == "Type"), "").strip()
        man = next((r["value"] for r in rs if r["varName"] == "Manufacturer"), "").strip()
        ref = next((r["value"] for r in rs if r["varName"] == "Refrigerant"), "").strip()
        is_a2a = "Air/Air" in typ
        is_a2w = "Air/Water" in typ and not is_a2a
        if not (is_a2a or is_a2w):
            continue

        blocks: dict = {}
        title = None
        for r in rs:
            if r["varName"] == "title":
                title = r["value"]
            blocks.setdefault((title, r["temperature"], r["climate"]), {})[r["varName"]] = r["value"]

        # One certificate carries several blocks (application x climate). P_TO is
        # a property of the unit, capacity of the rating point; merge per model.
        merged: dict = {}
        for (t, _temp, _clim), b in blocks.items():
            if not t:
                continue
            m = merged.setdefault(t, {})
            for k, v in b.items():
                m.setdefault(k, v)

        for t, b in merged.items():
            pto = _f(b.get("EN14825_024"))
            if pto is None:
                continue
            cap = _f(b.get("EN14511_2_005")) or _f(b.get("EN14511_2_001")) or _f(b.get("EN14825_002"))
            if cap is None or cap <= 0:
                continue
            rec = {
                "manufacturer": man,
                "model": t,
                "outdoor_unit": _outdoor_unit(t),
                "system_type": typ,
                "refrigerant": ref,
                "Q_rated_kW": cap,
                "P_rated_input_kW": _f(b.get("EN14511_2_002")),
                "P_TO_W": pto,
                "P_offsb_a_W": _f(b.get("EN14825_023")),
                "P_offsb_b_W": _f(b.get("EN14825_025")),
                "P_CK_W": _f(b.get("EN14825_026")),
                "Pdesignh_kW": _f(b.get("EN14825_002")),
                # A multi-split outdoor unit serves 2-4 indoor units, so it
                # carries more indoor PCBs than a 1:1 split of the same total
                # capacity. Mixing the two makes "more indoor units" look like
                # "more capacity"; the project treats 1:1 splits as the ASHP
                # reference class, so they are separated here rather than pooled.
                "is_multi_split": t.count("+") >= 2,
                "source": SOURCE,
                "note": "P_TO = thermostat-off (compressor off, controls live). Proxy, NOT P_aux,active.",
            }
            (a2a if is_a2a else a2w)[(man, t)] = rec

    da = pd.DataFrame(sorted(a2a.values(), key=lambda r: r["Q_rated_kW"]))
    dw = pd.DataFrame(sorted(a2w.values(), key=lambda r: r["Q_rated_kW"]))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    da.to_csv(DATA_DIR / "keymark_pto_air_to_air.csv", index=False)
    dw.to_csv(DATA_DIR / "keymark_pto_air_to_water.csv", index=False)
    print(f"air-to-air  : {len(da):5d} models, {da.outdoor_unit.nunique()} outdoor units")
    print(f"air-to-water: {len(dw):5d} models, {dw.outdoor_unit.nunique()} outdoor units")
    return da, dw


# ---------------------------------------------------------------------------
# regression
# ---------------------------------------------------------------------------
def _ols(x: np.ndarray, y: np.ndarray, with_slope: bool):
    if with_slope:
        A = np.column_stack([np.ones_like(x), x])
    else:
        A = np.ones((len(x), 1))
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    a = float(coef[0])
    b = float(coef[1]) if with_slope else 0.0
    return a, b


def _predict(a: float, b: float, x) -> np.ndarray:
    return np.maximum(0.0, a + b * np.asarray(x, dtype=float))


def _metrics(y, yhat) -> dict:
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    e = yhat - y
    ss = float(((y - y.mean()) ** 2).sum())
    return {
        "N": int(len(y)),
        "MAE_W": float(np.abs(e).mean()),
        "RMSE_W": float(math.sqrt((e**2).mean())),
        "bias_W": float(e.mean()),
        "R2": float(1.0 - (e**2).sum() / ss) if ss > 0 else float("nan"),
    }


def select(res: dict) -> str:
    """D.X.10 criterion 5: keep the slope only if it earns its place.

    Judged on leave-one-unit-out MAE, not in-sample -- a two-parameter fit to
    fourteen points will always look better in sample.
    """
    return "model1_linear" if res["model1_linear"]["loo_unit"]["MAE_W"] < res["model0_constant"]["loo_unit"]["MAE_W"] else "model0_constant"


def fit(df: pd.DataFrame, label: str) -> dict:
    """Model 0 (constant) and Model 1 (a + b Q), with leave-one-unit-out CV."""
    x = df.Q_rated_kW.to_numpy(float)
    y = df.P_TO_W.to_numpy(float)
    out: dict = {"label": label, "N_rows": int(len(df)), "N_units": int(df.outdoor_unit.nunique())}

    for name, slope in (("model0_constant", False), ("model1_linear", True)):
        a, b = _ols(x, y, slope)
        m = _metrics(y, _predict(a, b, x))
        # Leave-one-outdoor-unit-out: a unit contributes no row to its own fit.
        preds = np.full(len(df), np.nan)
        for u in df.outdoor_unit.unique():
            hold = (df.outdoor_unit == u).to_numpy()
            if hold.all():
                continue
            aa, bb = _ols(x[~hold], y[~hold], slope)
            preds[hold] = _predict(aa, bb, x[hold])
        ok = ~np.isnan(preds)
        out[name] = {
            "a_W": a,
            "b_W_per_kW": b,
            "in_sample": m,
            "loo_unit": _metrics(y[ok], preds[ok]),
            "pred_at_3p5kW_W": float(_predict(a, b, [TMHP_Q_RATED_KW])[0]),
        }
        out[name + "_cv_pred"] = preds.tolist()
    return out


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def figures(da: pd.DataFrame, das: pd.DataFrame, dw: pd.DataFrame, res_s: dict, res_a: dict, res_w: dict,
            plr: pd.DataFrame, w_fit: float, chosen: str) -> None:
    import dartwork_mpl as dm
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scripts.visualization._dmpl_common import COLORS, GRIDLINE, HAIRLINE, apply_style, finalize, panel_letter, ticks

    apply_style()
    MS = 3.0
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def save(fig, name, **margins):
        for extra in (0, 2, 4, 6, 8, 12):
            try:
                kw = {k: f"{float(v.rstrip('%')) + extra}%" for k, v in margins.items()}
                finalize(fig, OUT_DIR / name, formats=("svg", "png"), margin=f"{2 + extra}%", **kw)
                break
            except RuntimeError as exc:
                if extra == 12:
                    raise exc
        plt.close(fig)
        print("wrote", name)

    def grid(ax):
        ax.grid(True, alpha=0.25, linewidth=GRIDLINE)

    # -- 01 P_TO vs capacity ---------------------------------------------
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("15cm", 0.42))
    for ax, d, res, title, xmax in (
        (axes[0], da, res_a, "Air-to-air (no circulation pump)", 9.0),
        (axes[1], dw, res_w, "Air-to-water (includes pump)", 30.0),
    ):
        multi = d.model.str.count(r"\+") >= 2
        ax.plot(d.Q_rated_kW[~multi], d.P_TO_W[~multi], "o", ms=MS, color=COLORS["ink"], mfc="none",
                mew=HAIRLINE + 0.2, label="single split" if d is da else "unit")
        if multi.any():
            ax.plot(d.Q_rated_kW[multi], d.P_TO_W[multi], "s", ms=MS, color=COLORS["accent"], mfc="none",
                    mew=HAIRLINE + 0.2, label="multi-split outdoor")
        a = res["model1_linear"]["a_W"]
        b = res["model1_linear"]["b_W_per_kW"]
        xs = np.linspace(0, xmax, 100)
        ax.plot(xs, _predict(a, b, xs), lw=dm.lw(0), color=COLORS["hot"],
                label=f"$a+bQ$ = {a:.1f} + {b:.2f} Q")
        ax.axhline(res["model0_constant"]["a_W"], ls=(0, (2, 1.5)), lw=HAIRLINE + 0.2, color=COLORS["muted"],
                   label=f"constant {res['model0_constant']['a_W']:.1f} W")
        if d is da:  # the 1:1-split fit, which is the one actually adopted
            aa = res_s[chosen]["a_W"]
            bb = res_s[chosen]["b_W_per_kW"]
            ax.plot(xs, _predict(aa, bb, xs), lw=dm.lw(0), color=COLORS["ess"], ls=(0, (4, 1.5)),
                    label=f"1:1 splits only: {aa:.1f}" + (f" + {bb:.2f} Q" if bb else " W (adopted)"))
        ax.set_xlim(0, xmax)
        ax.set_xlabel(r"Rated heating capacity $Q_{rated}$ [kW]")
        ax.set_ylabel(r"Declared $P_{TO}$ [W]")
        ax.set_title(f"{title} — N = {len(d)}", fontsize=dm.fs(-2.5), loc="left")
        ax.legend(loc="upper left", frameon=False, fontsize=dm.fs(-4.5))
        grid(ax)
    axes[0].set_ylim(0, 40)
    # A2W has a long upper tail (p99 = 169 W, max 700 W); the axis is clipped to
    # keep the bulk readable and the caption says so rather than the figure
    # implying the tail does not exist.
    axes[1].set_ylim(0, 120)
    panel_letter(axes[0], "a")
    panel_letter(axes[1], "b")
    save(fig, "01_pto_vs_capacity", mt="7%")

    # -- 02 parity (air-to-air, leave-one-unit-out) ----------------------
    fig, ax = plt.subplots(figsize=dm.figsize("8cm", 0.85))
    yhat = np.asarray(res_s[chosen + "_cv_pred"], float)
    y = das.P_TO_W.to_numpy(float)
    lim = (0, max(y.max(), np.nanmax(yhat)) * 1.15)
    ax.plot(lim, lim, lw=HAIRLINE + 0.2, color=COLORS["muted"], ls=":")
    for d_, lbl, col in ((20.0, "±20 W", COLORS["band20"]),):
        ax.fill_between(lim, [lim[0] - d_, lim[1] - d_], [lim[0] + d_, lim[1] + d_], color=col, alpha=0.35, lw=0,
                        label=lbl, zorder=0)
    ax.plot(y, yhat, "o", ms=MS, color=COLORS["ink"], mfc="none", mew=HAIRLINE + 0.2)
    m = res_s[chosen]["loo_unit"]
    ax.text(0.04, 0.96, f"leave-one-unit-out\nN = {m['N']}\nMAE = {m['MAE_W']:.1f} W\nRMSE = {m['RMSE_W']:.1f} W\n"
                        f"bias = {m['bias_W']:+.1f} W",
            transform=ax.transAxes, va="top", ha="left", fontsize=dm.fs(-4))
    ax.set_xlim(*lim)
    ax.set_ylim(*lim)
    ax.set_xlabel(r"Declared $P_{TO}$ [W]")
    ax.set_ylabel(r"Predicted $P_{TO}$ [W]")
    ax.legend(loc="lower right", frameon=False, fontsize=dm.fs(-4.5))
    grid(ax)
    save(fig, "02_pto_parity", mt="4%")

    # -- 03 residual vs capacity -----------------------------------------
    fig, ax = plt.subplots(figsize=dm.figsize("9cm", 0.62))
    ax.axhline(0, lw=HAIRLINE + 0.2, color=COLORS["muted"], ls=":")
    for name, col, mk, lbl in (("model1_linear", COLORS["ink"], "o", "Model 1  a + bQ"),
                               ("model0_constant", COLORS["accent"], "s", "Model 0  constant")):
        r = np.asarray(res_s[name + "_cv_pred"], float) - das.P_TO_W.to_numpy(float)
        ax.plot(das.Q_rated_kW, r, mk, ms=MS, color=col, mfc="none", mew=HAIRLINE + 0.2, label=lbl)
    ax.set_xlabel(r"Rated heating capacity $Q_{rated}$ [kW]")
    ax.set_ylabel(r"Predicted − declared $P_{TO}$ [W]")
    ax.set_title("Air-to-air 1:1 splits, leave-one-unit-out", fontsize=dm.fs(-2.5), loc="left")
    ax.legend(loc="best", frameon=False, fontsize=dm.fs(-4.5))
    grid(ax)
    save(fig, "03_pto_residual_vs_capacity", mt="7%")

    # -- 04 PLR-COP with the measured proxy against Appendix D ------------
    cond_title = {"heating_7": "Heating 7/20 °C", "cooling_35": "Cooling 35/27 °C"}
    fig, axes = plt.subplots(1, 2, figsize=dm.figsize("15cm", 0.44))
    series = [
        (0.0, COLORS["ink"], "solid", "current TMHP"),
        (w_fit, COLORS["ess"], (0, (5, 1.6)), rf"+ measured $P_{{TO}}$ proxy ({w_fit:.0f} W)"),
        (25.0, COLORS["accent"], (0, (2.4, 1.3)), r"+ 25 W (Appendix D)"),
        (50.0, COLORS["hot"], (0, (1, 1.3)), r"+ 50 W (Appendix D)"),
    ]
    for ax, cond in zip(axes, ("heating_7", "cooling_35"), strict=True):
        g = plr[plr.condition == cond]
        mod = g[g.capacity_clamped.isna()].sort_values("PLR_request")
        flo = g[g.capacity_clamped == "min"].sort_values("PLR_request")
        for w, col, ls, lbl in series:
            ax.plot(100 * mod.PLR_request, mod.Q_delivered_W / (mod.E_tot_baseline + w), ls=ls, lw=dm.lw(0),
                    color=col, marker="o" if w == 0 else "", ms=MS * 0.8, label=lbl)
            if len(flo):
                if w == 0:
                    ax.plot(100 * flo.PLR_request, flo.Q_delivered_W / (flo.E_tot_baseline + w), ls="none",
                            marker="o", ms=MS * 0.8, mfc="white", mec=col, mew=HAIRLINE)
                else:
                    ax.plot(100 * flo.PLR_request, flo.Q_delivered_W / (flo.E_tot_baseline + w), ls=(0, (1, 2)),
                            lw=dm.lw(0), color=col)
        ax.set_xlim(0, 100)
        ax.set_xticks(ticks(0, 100, 20))
        ax.set_xlabel("Requested part-load ratio [%]")
        ax.set_ylabel(r"System $\mathrm{COP}$ [-]")
        ax.set_title(cond_title[cond], fontsize=dm.fs(-2.5), loc="left")
        ax.legend(loc="upper right", frameon=False, fontsize=dm.fs(-4.5))
        grid(ax)
    panel_letter(axes[0], "a")
    panel_letter(axes[1], "b")
    save(fig, "04_plr_cop_with_pto_proxy", mt="7%")


def plr_table(plr: pd.DataFrame, w_values) -> pd.DataFrame:
    """Low-load rise for each added fixed power, from the Appendix D states.

    ``W_fixed`` is pure post-processing -- Appendix D verified the solved state
    does not move with it -- so an arbitrary W is evaluated by re-dividing,
    with no re-simulation and no new modelling assumption.
    """
    rows = []
    for cond, g in plr.groupby("condition"):
        mod = g[g.capacity_clamped.isna()].sort_values("PLR_request")
        for w in w_values:
            cop = (mod.Q_delivered_W / (mod.E_tot_baseline + w)).to_numpy()
            rows.append({
                "condition": cond,
                "W_added_W": w,
                "PLR_low": float(mod.PLR_request.iloc[0]),
                "COP_low": float(cop[0]),
                "COP_rated": float(cop[-1]),
                "rise_pct": float(100.0 * (cop[0] / cop[-1] - 1.0)),
                "COP_max": float(cop.max()),
                "PLR_at_COP_max": float(mod.PLR_request.to_numpy()[int(cop.argmax())]),
                "monotonic_rise": bool(cop.argmax() == 0),
            })
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", default=None, choices=["data", "fit", "fig"])
    a = ap.parse_args()
    want = set(a.only or ["data", "fit", "fig"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if "data" in want:
        da, dw = extract()
    else:
        da = pd.read_csv(DATA_DIR / "keymark_pto_air_to_air.csv")
        dw = pd.read_csv(DATA_DIR / "keymark_pto_air_to_water.csv")

    das = da[~da.is_multi_split].reset_index(drop=True)  # 1:1 splits -- the ASHP reference class
    res_s = fit(das, "air_to_air_single_split")
    res_a = fit(da, "air_to_air_all")
    res_w = fit(dw, "air_to_water")
    chosen = select(res_s)
    w_fit = res_s[chosen]["pred_at_3p5kW_W"]

    for r in (res_s, res_a, res_w):
        print(json.dumps({k: v for k, v in r.items() if not k.endswith("_cv_pred")}, indent=2))
    print(f"\nselected model for 1:1 splits: {chosen}")
    print(f"prediction at Q = {TMHP_Q_RATED_KW} kW : {w_fit:.1f} W")

    # cross-validated predictions, per row
    cvp = das[["manufacturer", "model", "outdoor_unit", "Q_rated_kW", "P_TO_W"]].copy()
    cvp["pred_model0_W"] = res_s["model0_constant_cv_pred"]
    cvp["pred_model1_W"] = res_s["model1_linear_cv_pred"]
    cvp["resid_model1_W"] = cvp.pred_model1_W - cvp.P_TO_W
    cvp.to_csv(OUT_DIR / "cv_predictions.csv", index=False)

    plr = pd.read_csv(APPENDIX_D_CSV)
    plr = plr[(plr.sh_sc_mode == "mode_A") & (plr.SH_set == 3.0) & (plr.SC_set == 3.0)
              & (plr.W_fixed == 0.0) & plr.converged
              & plr.condition.isin(["heating_7", "cooling_35"])]
    tab = plr_table(plr, sorted({0.0, round(w_fit, 1), *APPENDIX_D_W}))
    tab.to_csv(OUT_DIR / "plr_cop_with_pto_proxy.csv", index=False)
    print("\n" + tab.to_string(index=False))

    metrics = []
    for lbl, res in (("air_to_air_single_split", res_s), ("air_to_air_all", res_a), ("air_to_water", res_w)):
        for mdl in ("model0_constant", "model1_linear"):
            metrics.append({"population": lbl, "model": mdl, "a_W": res[mdl]["a_W"],
                            "b_W_per_kW": res[mdl]["b_W_per_kW"],
                            **{f"in_{k}": v for k, v in res[mdl]["in_sample"].items()},
                            **{f"loo_{k}": v for k, v in res[mdl]["loo_unit"].items()},
                            "pred_at_3p5kW_W": res[mdl]["pred_at_3p5kW_W"]})
    pd.DataFrame(metrics).to_csv(OUT_DIR / "metrics.csv", index=False)
    (OUT_DIR / "coefficients.json").write_text(json.dumps({
        "warning": "P_TO proxy for non-vanishing parasitic power. NOT P_aux,active. Not a TMHP coefficient.",
        "source": SOURCE,
        "variable_identification": "EN14825_024 = P_TO, verified against Bosch CS2000AWF ErP fiches",
        "capacity_variable": "EN14511_2_005 rated heating capacity [kW]",
        "selected_population": "air_to_air_single_split",
        "selected_model": chosen,
        "selection_rule": "leave-one-outdoor-unit-out MAE; slope kept only if it improves on the constant",
        "air_to_air_single_split": {k: v for k, v in res_s.items() if not k.endswith("_cv_pred")},
        "air_to_air_all": {k: v for k, v in res_a.items() if not k.endswith("_cv_pred")},
        "air_to_water": {k: v for k, v in res_w.items() if not k.endswith("_cv_pred")},
        "tmhp_prediction_at_3p5kW_W": w_fit,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    if "fig" in want:
        figures(da, das, dw, res_s, res_a, res_w, plr, w_fit, chosen)
    print("wrote", OUT_DIR)


if __name__ == "__main__":
    main()
