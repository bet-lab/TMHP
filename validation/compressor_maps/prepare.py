"""Turn the derived point table into the fit-ready table.

Steps (all recorded as columns so the report can show what was dropped):
  compressor_key   one key per physical machine: source + model stem (Copeland
                   voltage/bill-of-material suffixes such as -2E9/-4X9 share one
                   coefficient set; exact duplicate rows are dropped)
  exclude_fixed    Copeland ZPS* are fixed-speed scrolls (induction motor, slip
                   unknown) -> not variable-speed evidence, excluded entirely
  vdisp_suspect    a machine whose rated-point (n*=1, PR 2-3.5) median eta_vol
                   falls outside [0.85, 1.05] has a wrong displacement in the
                   summary or a mis-scaled mass-flow polynomial -> excluded from
                   the eta_vol fit only (eta_oi does not use V_disp)
  point_ok         per-point sanity: 0.5 < eta_vol < 1.06, 0.30 < eta_oi < 0.85,
                   1.5 <= PR <= 8, 15 K <= lift <= 60 K (AHRI polynomials are
                   fits inside the tested map; outside they diverge)
  w_record         each compressor x speed record sums to weight 1
"""

from __future__ import annotations

import re

import pandas as pd

from validation.compressor_maps.schema import DATA_DIR

IN = DATA_DIR / "points_derived.csv"
OUT = DATA_DIR / "points_fit_ready.csv"


def compressor_key(row) -> str:
    m = str(row["model"])
    if row["source_id"] == "copeland_opi":
        m = re.split(r"-", m)[0]
    return f"{row['source_id']}::{m}"


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in (
        "eta_vol",
        "eta_oi",
        "eta_isen",
        "eta_em",
        "PR",
        "n_star",
        "N_rps",
        "N_rated_rps",
        "T_evap_C",
        "T_cond_C",
        "weight",
    ):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["compressor_key"] = df.apply(compressor_key, axis=1)
    df["exclude_fixed"] = df["model"].astype(str).str.startswith("ZPS")
    # exact duplicates from voltage-code twins
    before = len(df)
    df = df.drop_duplicates(
        subset=["compressor_key", "N_rps", "T_evap_C", "T_cond_C", "p_suc_Pa_d", "p_dis_Pa_d", "eta_vol", "eta_oi"]
    )
    print(f"dropped {before - len(df)} duplicate rows (voltage-code twins)")
    lift = df["T_cond_C"] - df["T_evap_C"]
    df["lift_K"] = lift
    df["point_ok"] = (
        df["eta_vol"].between(0.5, 1.06)
        & df["eta_oi"].between(0.30, 0.85)
        & df["PR"].between(1.5, 8.0)
        & (lift.isna() | lift.between(15.0, 60.0))
    )
    # rated-point volumetric sanity per machine
    # rated-point check on the speed record closest to n* = 1 (some machines publish no n* = 1 record)
    mid = df[df["PR"].between(2.0, 3.5)].copy()
    mid["dn"] = (mid["n_star"] - 1.0).abs()
    nearest = mid.sort_values("dn").groupby("compressor_key")["N_rps"].first()
    mid = mid[mid["N_rps"] == mid["compressor_key"].map(nearest)]
    rated = mid.groupby("compressor_key")["eta_vol"].median()
    df["eta_vol_rated_med"] = df["compressor_key"].map(rated)
    df["vdisp_suspect"] = ~df["eta_vol_rated_med"].between(0.85, 1.05) & df["eta_vol_rated_med"].notna()
    df.loc[df["eta_vol_rated_med"].isna(), "vdisp_suspect"] = False
    # weights: each (compressor, speed) record sums to 1
    grp = df.groupby(["compressor_key", "N_rps"])["eta_oi"].transform("size")
    df["w_record"] = 1.0 / grp
    df["speed_record"] = df["compressor_key"] + "@" + df["N_rps"].round(1).astype(str)
    return df


def main() -> None:
    df = pd.read_csv(IN, low_memory=False)
    out = prepare(df)
    out.to_csv(OUT, index=False)
    ok = out[~out.exclude_fixed & out.point_ok]
    print(f"{len(out)} rows -> {OUT}; usable for eta_oi: {len(ok)}, for eta_vol: {len(ok[~ok.vdisp_suspect])}")
    print("machines:", ok.compressor_key.nunique(), "| speed records:", ok.speed_record.nunique())
    print("vdisp_suspect machines:", sorted(out[out.vdisp_suspect].compressor_key.unique()))
    print(
        ok.groupby(["source_id", "refrigerant", "comp_type"])
        .agg(
            machines=("compressor_key", "nunique"),
            records=("speed_record", "nunique"),
            rows=("PR", "size"),
            nstar_min=("n_star", "min"),
            nstar_max=("n_star", "max"),
            PRmin=("PR", "min"),
            PRmax=("PR", "max"),
        )
        .round(2)
        .to_string()
    )


if __name__ == "__main__":
    main()
