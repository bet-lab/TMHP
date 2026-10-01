"""Gate A of plan v3: rated-point COP error and residual structure of the catalogue parity.

Reads the per-unit results written by ``validation.parity.run`` and scores

* the **rated operating point** of every unit -- the catalogue row at the
  unit's rating condition and nameplate duty (air-to-water: outdoor 7 degC,
  leaving water 45 degC, duty = nominal; air-to-air: cooling at outdoor 35 degC
  and the indoor temperature nearest 27 degC, duty nearest nominal, and
  heating at outdoor 6-7 degC / indoor 20-21 degC) -- with the plan's
  target of |relative error| <= 10 %;
* the overall point-weighted COP MAPE per (model class, rating standard),
  target <= 10 %;
* residuals (model - catalogue, %) against refrigerant, nominal capacity,
  pressure ratio and relative speed n*.

Writes ``validation/results/gate_a/{rated_points.csv, residual_bins.csv,
gate_a_summary.json}``.  Nothing here changes a coefficient;
it reports where the assembled model stands after the compressor
correlations were frozen on compressor data alone.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "validation" / "results"
#: Gate A outputs live in a sub-directory so the per-unit result glob of the docs data generator stays clean.
GATE_A_DIR = RESULTS_DIR / "gate_a"

RATED_TOL_PCT = 10.0
MAPE_TARGET_PCT = 10.0


def load_points(results_dir: Path = RESULTS_DIR) -> pd.DataFrame:
    frames = []
    for f in sorted(results_dir.glob("*.csv")):
        if f.name in ("summary.csv", "rated_points.csv", "residual_bins.csv"):
            continue
        frames.append(pd.read_csv(f))
    d = pd.concat(frames, ignore_index=True)
    d["usable"] = d["usable"].astype(str) == "True"
    d = d[d.usable].copy()
    d["rel_err_pct"] = (d.cop_pred - d.cop_target) / d.cop_target * 100.0
    return d


def rated_rows(d: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _slug, g in d.groupby("slug"):
        cls = g.model_class.iat[0]
        nominal = g.nominal_kW.iat[0]
        if cls == "ASHPB":
            c = g[(g.t_source_C == 7.0) & (g.t_sink_C == 45.0)]
            c = c.assign(dq=(c.q_kW - nominal).abs()).sort_values("dq").head(1).assign(rating="A7/W45 heating")
            rows.append(c)
        else:
            cool = g[(g["mode"] == "cooling") & (g.t_source_C.between(34.0, 36.0))]
            if len(cool):
                sink = cool.t_sink_C.iloc[int((cool.t_sink_C - 27.0).abs().argmin())]
                c = cool[cool.t_sink_C == sink]
                c = c.assign(dq=(c.q_kW - nominal).abs()).sort_values("dq").head(1).assign(rating="T1 cooling 35/27")
                rows.append(c)
            heat = g[(g["mode"] == "heating") & (g.t_source_C.between(5.0, 8.5)) & (g.t_sink_C.between(19.5, 21.5))]
            if len(heat):
                c = heat.assign(dq=(heat.q_kW - heat.q_kW.max()).abs()).sort_values("dq").head(1)
                rows.append(c.assign(rating="H1 heating 7/20"))
    r = pd.concat(rows, ignore_index=True)
    r["within_10pct"] = r.rel_err_pct.abs() <= RATED_TOL_PCT
    cols = [
        "slug",
        "unit",
        "model_class",
        "refrigerant",
        "nominal_kW",
        "rating_standard",
        "status",
        "rating",
        "t_source_C",
        "t_sink_C",
        "q_kW",
        "cop_target",
        "cop_pred",
        "rel_err_pct",
        "within_10pct",
        "n_star",
        "pr_cmp",
        "coefficient_version",
    ]
    return r[cols]


def residual_bins(d: pd.DataFrame) -> pd.DataFrame:
    adopted = d[d.status == "adopted"].copy()
    out = []

    def add(axis, key):
        for (cls, lvl), g in adopted.groupby(["model_class", key], observed=True):
            out.append(
                {
                    "axis": axis,
                    "model_class": cls,
                    "bin": str(lvl),
                    "n": len(g),
                    "bias_pct": g.rel_err_pct.mean(),
                    "mape_pct": g.rel_err_pct.abs().mean(),
                    "sd_pct": g.rel_err_pct.std(),
                }
            )

    adopted["cap_bin"] = adopted.nominal_kW.astype(str) + " kW"
    adopted["pr_bin"] = pd.cut(adopted.pr_cmp, [0, 2, 2.5, 3, 4, 5, 6, 12]).astype(str)
    adopted["n_bin"] = pd.cut(adopted.n_star, [0, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5, 3.0]).astype(str)
    add("refrigerant", "refrigerant")
    add("nominal_capacity", "cap_bin")
    add("pressure_ratio", "pr_bin")
    add("n_star", "n_bin")
    return pd.DataFrame(out)


def headline(d: pd.DataFrame) -> pd.DataFrame:
    adopted = d[d.status == "adopted"]
    rows = []
    for (cls, std), g in adopted.groupby(["model_class", "rating_standard"]):
        rows.append(
            {
                "model_class": cls,
                "rating_standard": std,
                "units": g.slug.nunique(),
                "points": len(g),
                "cop_MAPE_pct": g.rel_err_pct.abs().mean(),
                "cop_bias_pct": g.rel_err_pct.mean(),
                "meets_target": bool(g.rel_err_pct.abs().mean() <= MAPE_TARGET_PCT),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", default=str(RESULTS_DIR))
    ap.add_argument("--out", default=str(GATE_A_DIR))
    a = ap.parse_args()
    d = load_points(Path(a.results))
    r = rated_rows(d)
    b = residual_bins(d)
    h = headline(d)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    r.to_csv(out / "rated_points.csv", index=False)
    b.to_csv(out / "residual_bins.csv", index=False)
    pd.set_option("display.width", 250)
    print(f"Gate A -- coefficients {d.coefficient_version.iat[0]}")
    print("\nHeadline (adopted units only):")
    print(h.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print("\nRated points:")
    cols = [
        "unit",
        "refrigerant",
        "status",
        "rating",
        "q_kW",
        "cop_target",
        "cop_pred",
        "rel_err_pct",
        "within_10pct",
        "n_star",
        "pr_cmp",
    ]
    print(r[cols].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    ad = r[r.status == "adopted"]
    print(
        f"\nadopted rated points within +/-{RATED_TOL_PCT:.0f} %: {int(ad.within_10pct.sum())}/{len(ad)}"
        f"  (worst {ad.rel_err_pct.abs().max():.1f} %: {ad.loc[ad.rel_err_pct.abs().idxmax(), 'unit']})"
    )
    print("\nResiduals by bin (adopted):")
    print(b.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    summary = {
        "coefficient_version": d.coefficient_version.iat[0],
        "rated_within_10pct": int(ad.within_10pct.sum()),
        "rated_total": int(len(ad)),
        "rated_worst_abs_pct": float(ad.rel_err_pct.abs().max()),
        "headline": h.to_dict(orient="records"),
    }
    (out / "gate_a_summary.json").write_text(json.dumps(summary, indent=1))
    print(f"\nwrote {out / 'rated_points.csv'}, residual_bins.csv, gate_a_summary.json")


if __name__ == "__main__":
    main()
