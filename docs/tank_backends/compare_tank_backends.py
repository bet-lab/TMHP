"""Compare the three tmhp tank backends + quantitative metrics.

- lumped         : single-node fully-mixed  (StratifiedTank n=1)
- stratified     : Cadau 2019 multi-node smooth (StratifiedTank, MPC-internal)
- hybrid         : Cruz-Loredo 2023 hybrid thermocline (HybridStratifiedTank, plant)

Two experiments:
  A. Charging a cold tank — thermocline sharpness (numerical diffusion).
  B. Drawing hot water — supply-temperature hold time (stratification flexibility).

Run::

    .venv/bin/python docs/tank_backends/compare_tank_backends.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from tmhp.hybrid_tank import HybridStratifiedTank
from tmhp.stratified_tank import StratifiedTank

HERE = Path(__file__).resolve().parent

VOL, H, DT = 0.2, 1.2, 60.0
T_COLD, T_HOT, T_MAKEUP = 15.0, 60.0, 12.0
K_EFF = 0.606  # water conduction only (no extra mixing) to isolate the front


def _height_frac(n):
    # node 0 = top; return fraction from bottom (0=bottom, 1=top) at node mids.
    return (n - np.arange(n) - 0.5) / n


def _band_width(T):
    span = T_HOT - T_COLD
    return int(np.sum((T_COLD + 0.25 * span < T) & (T_COLD + 0.75 * span > T)))


# --- A. Charge thermocline ---------------------------------------------------
def charge_profiles(n=20, nsteps=14, q=1.2e-4):
    lump = StratifiedTank(1, VOL, H, k_eff=K_EFF, ua=0.0)
    cad = StratifiedTank(n, VOL, H, k_eff=K_EFF, ua=0.0)
    hyb = HybridStratifiedTank(n, VOL, H, k_eff=K_EFF, ua=0.0)
    for t in (lump, cad, hyb):
        t.reset(T_COLD)
    for _ in range(nsteps):
        lump.step(DT, charge_flow=q, T_charge=T_HOT)
        cad.step(DT, charge_flow=q, T_charge=T_HOT)
        hyb.step(DT, charge_flow=q, T_charge=T_HOT)
    return {
        "lumped": (np.array([0.0, 1.0]), np.full(2, lump.T[0])),
        "stratified": (_height_frac(n), cad.T),
        "hybrid": (_height_frac(n), hyb.T),
        "width_cad": _band_width(cad.T),
        "width_hyb": _band_width(hyb.T),
    }


def diffusion_vs_n(q=1.2e-4):
    ns = [8, 12, 20, 30, 40, 60]
    w_cad, w_hyb = [], []
    for n in ns:
        cad = StratifiedTank(n, VOL, H, k_eff=K_EFF, ua=0.0)
        hyb = HybridStratifiedTank(n, VOL, H, k_eff=K_EFF, ua=0.0)
        cad.reset(T_COLD)
        hyb.reset(T_COLD)
        nsteps = int((H / 2) / (q / (VOL / H)) / DT)
        for _ in range(nsteps):
            cad.step(DT, charge_flow=q, T_charge=T_HOT)
            hyb.step(DT, charge_flow=q, T_charge=T_HOT)
        # band width as a fraction of tank height (node-count / N).
        w_cad.append(_band_width(cad.T) / n)
        w_hyb.append(_band_width(hyb.T) / n)
    return np.array(ns), np.array(w_cad), np.array(w_hyb)


# --- B. Draw supply temperature ---------------------------------------------
def draw_supply(n=20, nsteps=40, q=1.0e-4):
    lump = StratifiedTank(1, VOL, H, k_eff=K_EFF, ua=0.0)
    cad = StratifiedTank(n, VOL, H, k_eff=K_EFF, ua=0.0)
    hyb = HybridStratifiedTank(n, VOL, H, k_eff=K_EFF, ua=0.0)
    for t in (lump, cad, hyb):
        t.reset(T_HOT)  # start fully charged (uniform hot)
    sup = {"lumped": [], "stratified": [], "hybrid": []}
    for _ in range(nsteps):
        sup["lumped"].append(lump.step(DT, draw_flow=q, T_makeup=T_MAKEUP)["T_top"])
        sup["stratified"].append(cad.step(DT, draw_flow=q, T_makeup=T_MAKEUP)["T_top"])
        sup["hybrid"].append(hyb.step(DT, draw_flow=q, T_makeup=T_MAKEUP)["T_top"])
    t_min = np.arange(1, nsteps + 1) * DT / 60.0
    return t_min, {k: np.array(v) for k, v in sup.items()}


def _hold_time(t_min, sup, threshold=45.0):
    above = sup >= threshold
    return float(t_min[above][-1]) if above.any() else 0.0


# --- Plotting ----------------------------------------------------------------
def _plot(prof, ns, w_cad, w_hyb, t_min, sup):
    import dartwork_mpl as dm
    import matplotlib.pyplot as plt

    dm.style.use("scientific")
    gk = {"wspace": 0.34}

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0), gridspec_kw=gk)
    ax = axes[0]
    ax.plot(*prof["lumped"], color="0.5", lw=1.6, label="lumped")
    ax.plot(
        prof["stratified"][0], prof["stratified"][1], color="C0", lw=1.6, marker="o", ms=2.5, label="stratified (Cadau)"
    )
    ax.plot(
        prof["hybrid"][0],
        prof["hybrid"][1],
        color="C3",
        lw=1.6,
        marker="s",
        ms=2.5,
        ls="--",
        label="hybrid (Cruz-Loredo)",
    )
    ax.set_xlabel("height fraction (0 = bottom)", fontsize=dm.fs(-1))
    ax.set_ylabel("temperature [°C]", fontsize=dm.fs(-1))
    ax.set_title("charging thermocline (N=20, front ~mid)", fontsize=dm.fs(-1))
    ax.legend(fontsize=dm.fs(-2), frameon=False, loc="upper left")

    ax = axes[1]
    ax.plot(ns, w_cad * 100, color="C0", lw=1.6, marker="o", ms=3, label="stratified")
    ax.plot(ns, w_hyb * 100, color="C3", lw=1.6, marker="s", ms=3, ls="--", label="hybrid")
    ax.set_xlabel("number of nodes N", fontsize=dm.fs(-1))
    ax.set_ylabel("front width [% of height]", fontsize=dm.fs(-1))
    ax.set_title("numerical diffusion vs N", fontsize=dm.fs(-1))
    ax.legend(fontsize=dm.fs(-2), frameon=False)
    dm.simple_layout(fig, margin="2mm")
    dm.save_formats(fig, str(HERE / "fig1_tank_diffusion"))
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.plot(t_min, sup["lumped"], color="0.5", lw=1.6, label="lumped")
    ax.plot(t_min, sup["stratified"], color="C0", lw=1.6, label="stratified")
    ax.plot(t_min, sup["hybrid"], color="C3", lw=1.6, ls="--", label="hybrid")
    ax.axhline(45.0, color="0.7", lw=0.7, ls=":")
    ax.set_xlabel("draw time [min]", fontsize=dm.fs(-1))
    ax.set_ylabel("supply (top) temp [°C]", fontsize=dm.fs(-1))
    ax.set_title("hot-water hold during draw", fontsize=dm.fs(-1))
    ax.legend(fontsize=dm.fs(-2), frameon=False, loc="lower left")
    dm.simple_layout(fig, margin="2mm")
    dm.save_formats(fig, str(HERE / "fig2_tank_draw"))
    plt.close(fig)


def main():
    prof = charge_profiles()
    ns, w_cad, w_hyb = diffusion_vs_n()
    t_min, sup = draw_supply()

    print("=== A. Charge thermocline (N=20, front ~mid) ===")
    print(f"  front band width [nodes]: stratified={prof['width_cad']}  hybrid={prof['width_hyb']}")
    print("  front width vs N [% of height]:")
    for n, wc, wh in zip(ns, w_cad, w_hyb, strict=True):
        print(f"    N={n:>3}: stratified={wc * 100:5.1f}%  hybrid={wh * 100:5.1f}%")

    print("\n=== B. Draw supply hold (>45°C) ===")
    for k in ("lumped", "stratified", "hybrid"):
        print(f"  {k:>11}: {_hold_time(t_min, sup[k]):5.1f} min above 45°C")

    _plot(prof, ns, w_cad, w_hyb, t_min, sup)
    print(f"\nFigures written to {HERE}")


if __name__ == "__main__":
    main()
