"""Low-speed behaviour: eta(f) / eta(50 Hz) at the same (SST, SDT), per compressor.

usage: lowspeed.py EFFICIENCY_POINTS.csv OUT_DIR FIG_DIR
Writes lowspeed_ratios.csv (median / IQR over operating points, per group x f) and
one figure per group with the median ratio of each efficiency against n*.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

EFFS = ["eta_v", "eta_is", "eta_em", "eta_oi"]
LABEL = {"eta_v": r"$\eta_v$", "eta_is": r"$\eta_{is}$", "eta_em": r"$\eta_{em}$", "eta_oi": r"$\eta_{is}\eta_{em}$"}


def main(src, out_dir, fig_dir):
    df = pd.read_csv(src)
    df = df[df.valid_bitzer == "yes"].copy()
    df["grp"] = df.compressor_type + "_" + df.refrigerant
    key = ["compressor_id", "refrigerant", "T_evap_C", "T_cond_C"]
    ref = df[df.normalized_speed.round(3) == 1.0].set_index(key)[EFFS]
    rows = []
    for e in EFFS:
        df[f"{e}_rel"] = df[e].values / ref[e].reindex(pd.MultiIndex.from_frame(df[key])).values
    for (grp, f), g in df.groupby(["grp", "frequency_Hz"]):
        r = dict(
            group=grp, frequency_Hz=f, normalized_speed=f / 50, n_points=len(g), n_compressors=g.compressor_id.nunique()
        )
        for e in EFFS:
            s = g[f"{e}_rel"].dropna()
            r[f"{e}_rel_median"] = s.median()
            r[f"{e}_rel_q25"] = s.quantile(0.25)
            r[f"{e}_rel_q75"] = s.quantile(0.75)
        rows.append(r)
    out = pd.DataFrame(rows)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out.to_csv(Path(out_dir) / "lowspeed_ratios.csv", index=False)
    # per-compressor slope sign at low speed: is eta(35)/eta(50) < 1 at every operating point?
    sign = (
        df[df.frequency_Hz == df.groupby(["compressor_id", "refrigerant"]).frequency_Hz.transform("min")]
        .groupby(["grp", "compressor_id"])[[f"{e}_rel" for e in EFFS]]
        .agg(["min", "median", "max"])
    )
    sign.to_csv(Path(out_dir) / "lowspeed_by_compressor.csv")
    for grp, g in out.groupby("group"):
        fig, ax = plt.subplots(figsize=(5, 3.6))
        for e, c in zip(EFFS, ["C0", "C1", "C2", "C3"], strict=True):
            ax.plot(g.normalized_speed, g[f"{e}_rel_median"], "-o", ms=3, color=c, label=LABEL[e])
            ax.fill_between(g.normalized_speed, g[f"{e}_rel_q25"], g[f"{e}_rel_q75"], color=c, alpha=0.15, lw=0)
        ax.axhline(1, color="k", lw=0.6)
        ax.set_xlabel(r"$n^* = f/50$ Hz [-]")
        ax.set_ylabel(r"$\eta(n^*)/\eta(n^*=1)$ at same SST/SDT")
        ax.set_title(f"speed dependence, {grp.replace('_', ' / ')} (median, IQR)", fontsize=10)
        ax.legend(frameon=False, fontsize=8)
        fig.tight_layout()
        fig.savefig(Path(fig_dir) / f"lowspeed_{grp}.png", dpi=200)
        fig.savefig(Path(fig_dir) / f"lowspeed_{grp}.svg")
        plt.close(fig)
    # group comparison: one panel per efficiency, one line per group
    fig, axs = plt.subplots(1, 3, figsize=(12, 3.6), sharex=True)
    for ax, e in zip(axs, ["eta_v", "eta_oi", "eta_em"], strict=True):
        for grp, g in out.groupby("group"):
            ax.plot(g.normalized_speed, g[f"{e}_rel_median"], "-o", ms=3, label=grp.replace("_", " / "))
        ax.axhline(1, color="k", lw=0.6)
        ax.set_xlabel(r"$n^* = f/50$ Hz [-]")
        ax.set_title(LABEL[e] + r"$(n^*)/$" + LABEL[e] + r"$(1)$, median", fontsize=10)
    axs[0].legend(frameon=False, fontsize=7)
    fig.tight_layout()
    fig.savefig(Path(fig_dir) / "group_comparison_speed.png", dpi=200)
    fig.savefig(Path(fig_dir) / "group_comparison_speed.svg")
    plt.close(fig)
    print(out.round(3).to_string())


if __name__ == "__main__":
    main(*sys.argv[1:])
