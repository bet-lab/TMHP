"""Split of the electrical-to-isentropic product into eta_isen and eta_em.

Power tables identify only ``eta_oi = eta_isen * eta_em``.  How much of the
speed and lift dependence sits in the drive and motor (``eta_em``, losses that
leave through the shell and the inverter) and how much in the compression
itself (``eta_isen``, losses that end up in the discharge gas) is decided here,
from the only rows measured under TMHP's definition -- the calorimeter tests
of Cuevas & Lebrun (2009) with discharge temperature, inverter-fed, five
speeds (n* 0.7-1.5) and PR 1.5-5.6 -- and checked against the drive-only
efficiency measured by Ossorio & Navarro-Peris (2023) on three inverters
(15-110 Hz, 185 points; ``validation/extraction/compressor_speed_losses``).

    eta_em(PR, n*) = ETA_EM_REF * s(n*) * m(PR)
    s(n*) = n*(1 + n0)/(n* + n0)                 fixed drive + motor losses (Ossorio form)
    m(PR) = (PR-1)/(PR-1+p0) * (PR_REF-1+p0)/(PR_REF-1)   motor load: torque grows with lift

``n0`` from the total electro-mechanical efficiency is compared with the
drive-only ``f0 / f_rated`` of the three inverters: the total must fall at
least as steeply as the drive alone.  ``m(PR)`` is kept only if it improves
the leave-one-speed-record-out error of the split rows; it is one machine's
evidence and is reported as such.

The product fit then carries ``s(n*)`` with this ``n0`` *fixed* (``fit.py``
S-families F0/F2/F3), so every further speed term the compressor set demands
(leakage L1/L2, flow-loss roll-off) is assigned to ``eta_isen``.

Writes validation/data/compressor_maps/split.json.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from validation.compressor_maps.schema import DATA_DIR, REPO_ROOT

FIT_READY = DATA_DIR / "points_fit_ready.csv"
OSSORIO = REPO_ROOT / "validation" / "data" / "compressor_speed_loss_fit.json"
OUT = DATA_DIR / "split.json"
PR_REF = 3.0


def s_drive(n: np.ndarray, n0: float) -> np.ndarray:
    return n * (1.0 + n0) / (n + n0)


def m_load(pr: np.ndarray, p0: float) -> np.ndarray:
    return (pr - 1.0) / (pr - 1.0 + p0) * (PR_REF - 1.0 + p0) / (PR_REF - 1.0)


def split_rows() -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(FIT_READY, low_memory=False)
    ok = df[(~df.exclude_fixed) & df.point_ok & (df.split_basis == "measured_Tdis")]
    inv = ok[ok.P_includes_inverter.astype(str) == "True"].copy()
    net = ok[ok.P_includes_inverter.astype(str) != "True"].copy()
    return inv, net


MODELS = {
    "M0": ("R s(n*)", lambda t, n, pr: t[0] * s_drive(n, t[1]), [0.94, 0.05], [0.8, 1e-4], [1.0, 1.0]),
    "M1": (
        "R s(n*) m(PR)",
        lambda t, n, pr: t[0] * s_drive(n, t[1]) * m_load(pr, t[2]),
        [0.94, 0.05, 0.1],
        [0.8, 1e-4, 1e-4],
        [1.0, 1.0, 3.0],
    ),
}


def fit_model(key: str, n, pr, y) -> np.ndarray:
    _, fn, t0, lo, hi = MODELS[key]
    return least_squares(lambda t: fn(t, n, pr) - y, t0, bounds=(lo, hi)).x


def loro(key: str, inv: pd.DataFrame) -> float:
    """Leave-one-speed-record-out MAPE of eta_em on the split rows."""
    n, pr, y, rec = inv.n_star.to_numpy(), inv.PR.to_numpy(), inv.eta_em.to_numpy(), inv.N_rps.to_numpy()
    fn = MODELS[key][1]
    errs = []
    for r in np.unique(rec):
        m = rec == r
        th = fit_model(key, n[~m], pr[~m], y[~m])
        errs.append(np.abs(fn(th, n[m], pr[m]) - y[m]) / y[m])
    return float(100 * np.concatenate(errs).mean())


def main() -> None:
    inv, net = split_rows()
    n, pr, y = inv.n_star.to_numpy(), inv.PR.to_numpy(), inv.eta_em.to_numpy()
    out: dict = {
        "source": "Cuevas & Lebrun 2009, doi:10.1016/j.applthermaleng.2008.03.016, Tables 2-3, inverter-fed rows",
        "n_rows": int(len(inv)),
        "speed_records": sorted(map(float, inv.n_star.round(3).unique())),
        "PR_range": [float(pr.min()), float(pr.max())],
        "PR_REF": PR_REF,
        "models": {},
    }
    for key, (label, fn, *_r) in MODELS.items():
        th = fit_model(key, n, pr, y)
        pred = fn(th, n, pr)
        out["models"][key] = {
            "label": label,
            "theta": dict(zip(["R", "n0", "p0"][: len(th)], map(float, th), strict=True)),
            "rmse": float(np.sqrt(np.mean((pred - y) ** 2))),
            "loro_mape_pct": loro(key, inv),
        }
    m0, m1 = out["models"]["M0"], out["models"]["M1"]
    # the PR term is kept only if it earns >= 0.1 pp in leave-one-record-out error (rule R1 at this level)
    use_m1 = (m0["loro_mape_pct"] - m1["loro_mape_pct"]) >= 0.1
    chosen = m1 if use_m1 else m0
    out["selected"] = "M1" if use_m1 else "M0"
    out["ETA_EM_REF"] = chosen["theta"]["R"]
    out["ETA_EM_N0"] = chosen["theta"]["n0"]
    out["ETA_EM_P0"] = chosen["theta"].get("p0", 0.0)
    out["ETA_EM_REF_note"] = f"eta_em at PR = {PR_REF:g}, n* = 1 from the {out['selected']} fit"
    # network-fed rows (no inverter): the level the anchor would have without drive losses
    out["network_fed_eta_em_median"] = float(net.eta_em.median()) if len(net) else float("nan")
    out["network_fed_rows"] = int(len(net))
    # drive-only floor from Ossorio & Navarro-Peris 2023
    if OSSORIO.exists():
        oss = json.loads(OSSORIO.read_text())
        rated = {"A": 70.0, "B": 60.0, "C": 50.0}  # nominal compressor frequencies, Table 2
        drives = {
            k: {"f0_hz": v["f0_hz"], "f_rated_hz": rated[k], "n0_drive": v["f0_hz"] / rated[k]}
            for k, v in oss["per_drive"].items()
        }
        out["ossorio_drive_only"] = {
            "source": oss["source"],
            "drives": drives,
            "n0_drive_median": float(np.median([d["n0_drive"] for d in drives.values()])),
            "n0_drive_max": float(max(d["n0_drive"] for d in drives.values())),
            "consistent": bool(out["ETA_EM_N0"] >= max(d["n0_drive"] for d in drives.values())),
            "note": "total (motor + drive) n0 must be at least the drive-only n0; motor losses add to it",
        }
    # what the chosen shape does at the speeds the heat-pump models reach
    table = []
    for ns in (0.25, 0.375, 0.5, 0.7, 1.0, 1.5, 2.0):
        table.append({"n_star": ns, "s": float(s_drive(np.array([ns]), out["ETA_EM_N0"])[0])})
    out["s_table"] = table
    out["m_table"] = [
        {"PR": p, "m": float(m_load(np.array([p]), out["ETA_EM_P0"])[0]) if use_m1 else 1.0}
        for p in (1.5, 2, 3, 4, 6, 8)
    ]
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k not in ("s_table", "m_table")}, indent=1))
    print("s(n*):", ", ".join(f"{r['n_star']:g}->{r['s']:.4f}" for r in table))
    print("m(PR):", ", ".join(f"{r['PR']:g}->{r['m']:.4f}" for r in out["m_table"]))
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
