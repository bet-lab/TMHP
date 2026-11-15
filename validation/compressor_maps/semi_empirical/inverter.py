"""Drive (inverter) efficiency from the two measured sets, kept apart from the compressor.

Data
    ``validation/data/inverter_efficiency_ossorio2023.csv``  three inverters A/B/C, 185 points, 15-110 Hz
                                                             (drive output frequency; two-pole, 1 Hz = 1 rev/s)
    ``points_ossorio2019_rotary.csv``                        two inverters (APY, AKO) on one R290 rotary,
                                                             30-120 rev/s, printed E2/E1

Forms (loss fraction ``1 - eta``; ``n60 = 60 / N`` with N in rev/s; ``x = P_out / P_out,max`` of that inverter)
    I1  lam_i (1 + beta (n60 - 1))                         speed only: a constant-power share beta of the loss
    I2  lam_i (1 + gamma (1/x - 1))                        load only
    I3  lam_i (1 + beta (n60 - 1) + gamma (1/x - 1))       both
``lam_i`` is the loss of inverter i at 60 rev/s (I1) -- a fixed effect, because inverters differ in quality --
and the shape (beta, gamma) is shared. The default level is the median of the five ``lam_i``.

I3 needs ``P_out,max`` (the inverter's rating), which is not known for a compressor whose drive is not named; I1
needs only the speed. Leave-one-inverter-out with the level refitted (shape error) and with the median level
(total error), and the 2023 -> 2019 transfer, are reported for each form.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from validation.compressor_maps.schema import DATA_DIR, REPO_ROOT

INV_2023 = REPO_ROOT / "validation" / "data" / "inverter_efficiency_ossorio2023.csv"
INV_2019 = DATA_DIR / "points_ossorio2019_rotary.csv"
N_REF_INV = 60.0
FORMS = ("I1", "I2", "I3")


def load() -> pd.DataFrame:
    a = pd.read_csv(INV_2023)
    a = pd.DataFrame(
        {
            "set": "ossorio2023",
            "inverter": a.inverter.astype(str),
            "N": a.frequency_hz.astype(float),
            "P_out": a.P_out_W.astype(float),
            "eta": a.eta_drive.astype(float),
        }
    )
    b = pd.read_csv(INV_2019)
    eta = b.eta_invert_pct_printed.astype(float) / 100.0
    b = pd.DataFrame(
        {
            "set": "ossorio2019",
            "inverter": b.inverter.astype(str),
            "N": b.speed_rps_printed.astype(float),
            "P_out": b.E1_W_printed.astype(float) * eta,
            "eta": eta,
        }
    )
    d = pd.concat([a, b], ignore_index=True)
    d["x"] = d.P_out / d.groupby("inverter").P_out.transform("max")
    return d


def _shape(form: str, th: np.ndarray, n: np.ndarray, x: np.ndarray) -> np.ndarray:
    beta, gamma = (th[0], 0.0) if form == "I1" else (0.0, th[0]) if form == "I2" else (th[0], th[1])
    return 1.0 + beta * (N_REF_INV / n - 1.0) + gamma * (1.0 / x - 1.0)


def n_shape(form: str) -> int:
    return 2 if form == "I3" else 1


@dataclass
class InverterFit:
    form: str
    theta: np.ndarray
    lam: dict[str, float]

    def eta(self, n: np.ndarray, x: np.ndarray | None = None, lam: float | None = None) -> np.ndarray:
        lam_v = float(np.median(list(self.lam.values()))) if lam is None else lam
        xx = np.ones_like(n) if x is None else x
        return 1.0 - lam_v * _shape(self.form, self.theta, n, xx)


def fit(d: pd.DataFrame, form: str, theta_fixed: np.ndarray | None = None) -> InverterFit:
    inv = sorted(d.inverter.unique())
    idx = d.inverter.map({k: i for i, k in enumerate(inv)}).to_numpy()
    n, x, y = d.N.to_numpy(), d.x.to_numpy(), d.eta.to_numpy()
    ns = n_shape(form)

    def res(p):
        th = theta_fixed if theta_fixed is not None else p[:ns]
        lam = p[ns:] if theta_fixed is None else p
        return 1.0 - lam[idx] * _shape(form, th, n, x) - y

    lam0 = np.full(len(inv), 0.06)
    if theta_fixed is None:
        p0 = np.r_[np.full(ns, 0.3), lam0]
        lo, hi = np.r_[np.full(ns, -1.0), np.zeros(len(inv))], np.r_[np.full(ns, 5.0), np.full(len(inv), 0.5)]
    else:
        p0, lo, hi = lam0, np.zeros(len(inv)), np.full(len(inv), 0.5)
    r = least_squares(res, p0, bounds=(lo, hi), loss="huber", f_scale=0.05)
    th = theta_fixed if theta_fixed is not None else r.x[:ns]
    lam = r.x if theta_fixed is not None else r.x[ns:]
    return InverterFit(form, np.asarray(th, float), {k: float(v) for k, v in zip(inv, lam, strict=True)})


def cross_validate(d: pd.DataFrame) -> pd.DataFrame:
    """Leave-one-inverter-out and 2023 -> 2019 transfer, per form."""
    rows = []
    for form in FORMS:
        full = fit(d, form)
        resid = 1.0 - np.array([full.lam[i] for i in d.inverter]) * _shape(form, full.theta, d.N.values, d.x.values)
        rows.append(
            {
                "form": form,
                "test": "in_sample",
                "held_out": "all",
                "n": len(d),
                "rmse_shape": float(np.sqrt(np.mean((resid - d.eta.values) ** 2))),
                "bias_total": np.nan,
                "rmse_total": np.nan,
                "theta": ";".join(f"{v:.4f}" for v in full.theta),
            }
        )
        splits = [(k, d.inverter == k) for k in sorted(d.inverter.unique())]
        splits.append(("ossorio2019 (fit on 2023 only)", d.set == "ossorio2019"))
        for name, te in splits:
            tr = fit(d[~te], form)
            t = d[te]
            own = fit(t, form, theta_fixed=tr.theta)
            p_shape = 1.0 - np.array([own.lam[i] for i in t.inverter]) * _shape(form, tr.theta, t.N.values, t.x.values)
            p_tot = tr.eta(t.N.values, t.x.values)
            rows.append(
                {
                    "form": form,
                    "test": "leave_one_inverter_out" if not name.startswith("ossorio2019") else "set_transfer",
                    "held_out": name,
                    "n": len(t),
                    "rmse_shape": float(np.sqrt(np.mean((p_shape - t.eta.values) ** 2))),
                    "bias_total": float(np.mean(p_tot - t.eta.values)),
                    "rmse_total": float(np.sqrt(np.mean((p_tot - t.eta.values) ** 2))),
                    "theta": ";".join(f"{v:.4f}" for v in tr.theta),
                }
            )
    return pd.DataFrame(rows)


def default_model(d: pd.DataFrame) -> InverterFit:
    """I1 (speed only) on all five inverters: the form used to convert inverter-input sources and the default."""
    return fit(d, "I1")
