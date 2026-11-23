"""Fit the default compressor efficiency maps to the Ossorio R290 scroll measurements (#217, #218).

Reads   validation/coefficients/ossorio-r290-te-2026-11-21/ossorio_thesis_r290_134.csv
        (134 measured points, Ossorio PhD thesis Appendix 1, PDF pp. 244-252; the same tests as
        Compressor A of Ossorio & Navarro-Peris 2023, ATE 230:120725: 46 cm3 R290 scroll, 30-110 rev/s)
Writes  points.csv         efficiencies derived from each measured point
        coefficients.json  the three maps, their scores (in-sample and 10-fold CV) and data domain

Efficiencies from a measured point (CoolProp HEOS, Propane), power = compressor + inverter input::

    eta_v  = m_dot / (rho_1 V N)                  V = 46 cm3/rev
    eta_ov = m_dot (h_2s - h_1) / W_total
    f_loss = 1 - m_dot (h_2 - h_1) / W_total       h_2 from the measured discharge temperature

Maps (l = ln PR, N the speed in rev/s, T_e the evaporating saturation temperature in C, both as they are)::

    eta_v, eta_ov = c . [1, l, l^2, l^3, N, N^2, l N, T_e]
    f_loss        = c . [1, l, l^2, l^3, N, N^2, l N]

``f_loss`` has no T_e term: adding one does not lower its error (0.0137 -> 0.0136).

    uv run python -m validation.compressor_maps.ossorio_te.fit
"""

from __future__ import annotations

import json
from pathlib import Path

import CoolProp
import CoolProp.CoolProp as CP
import numpy as np
import pandas as pd
from scipy.spatial import ConvexHull

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSION = "ossorio-r290-te-2026-11-21"
ARCHIVE = REPO_ROOT / "validation" / "coefficients" / VERSION
DATA = ARCHIVE / "ossorio_thesis_r290_134.csv"
FLUID = "Propane"
DISPLACEMENT_M3 = 46e-6
FOLDS = 10
SEED = 0

#: quantity -> whether the map has the evaporating-temperature term
TE_TERM = {"eta_v": True, "eta_ov": True, "f_loss": False}
TERMS = ["1", "ln PR", "(ln PR)^2", "(ln PR)^3", "N", "N^2", "N ln PR"]


def derive(frame: pd.DataFrame) -> pd.DataFrame:
    """Efficiencies of each measured point."""
    rows = []
    for r in frame.itertuples(index=False):
        te_k, tc_k = r.Tevap_C + 273.15, r.Tcond_C + 273.15
        p_e = CP.PropsSI("P", "T", te_k, "Q", 1, FLUID)
        p_c = CP.PropsSI("P", "T", tc_k, "Q", 1, FLUID)
        t_suc = r.Tsuc_C + 273.15
        h1 = CP.PropsSI("H", "P", p_e, "T", t_suc, FLUID)
        s1 = CP.PropsSI("S", "P", p_e, "T", t_suc, FLUID)
        rho1 = CP.PropsSI("D", "P", p_e, "T", t_suc, FLUID)
        h2s = CP.PropsSI("H", "P", p_c, "S", s1, FLUID)
        h2 = CP.PropsSI("H", "P", p_c, "T", r.Tdis_C + 273.15, FLUID)
        m_dot = r.mdot_g_s / 1000.0
        w_total = float(r.compressor_plus_inverter_power_W)
        rows.append(
            dict(
                point_id=r.point_id,
                T_evap_C=r.Tevap_C,
                T_cond_C=r.Tcond_C,
                N_rps=float(r.speed_rps),
                PR=p_c / p_e,
                eta_v=m_dot / (rho1 * DISPLACEMENT_M3 * r.speed_rps),
                eta_ov=m_dot * (h2s - h1) / w_total,
                f_loss=1.0 - m_dot * (h2 - h1) / w_total,
                author_eta_v=r.author_eta_v,
                author_eta_comp=r.author_eta_comp,
            )
        )
    return pd.DataFrame(rows)


def basis(pressure_ratio, rps, t_evap_c=None) -> np.ndarray:
    """Design matrix of the maps (``t_evap_c=None``: no evaporating-temperature term)."""
    log_pr = np.log(np.asarray(pressure_ratio, dtype=float))
    n = np.asarray(rps, dtype=float)
    columns = [np.ones_like(log_pr), log_pr, log_pr**2, log_pr**3, n, n**2, log_pr * n]
    if t_evap_c is not None:
        columns.append(np.asarray(t_evap_c, dtype=float))
    return np.column_stack(columns)


def _design(points: pd.DataFrame, quantity: str) -> np.ndarray:
    return basis(points.PR, points.N_rps, points.T_evap_C if TE_TERM[quantity] else None)


def _rmse(residual) -> float:
    return float(np.sqrt(np.mean(np.square(residual))))


def fit(points: pd.DataFrame) -> dict:
    """Least-squares coefficients, in-sample and 10-fold CV RMSE of each map."""
    folds = np.random.default_rng(SEED).permutation(len(points)) % FOLDS
    out = {}
    for quantity in TE_TERM:
        y = points[quantity].to_numpy()
        a = _design(points, quantity)
        coefficients = np.linalg.lstsq(a, y, rcond=None)[0]
        cv = np.empty_like(y)
        for k in range(FOLDS):
            train = folds != k
            c_k = np.linalg.lstsq(a[train], y[train], rcond=None)[0]
            cv[~train] = a[~train] @ c_k
        out[quantity] = dict(
            terms=TERMS + (["T_e"] if TE_TERM[quantity] else []),
            coefficients=[float(v) for v in coefficients],
            rmse=_rmse(a @ coefficients - y),
            cv_rmse=_rmse(cv - y),
            max_abs_error=float(np.max(np.abs(a @ coefficients - y))),
        )
    return out


def hull(points: pd.DataFrame) -> list[list[float]]:
    """Convex (PR, T_e) hull of the data, counter-clockwise (every condition is tested at all five speeds)."""
    xy = points[["PR", "T_evap_C"]].to_numpy()
    return [[float(xy[i, 0]), float(xy[i, 1])] for i in ConvexHull(xy).vertices]


def main() -> None:
    points = derive(pd.read_csv(DATA))
    points.to_csv(ARCHIVE / "points.csv", index=False)
    maps = fit(points)
    payload = dict(
        version=VERSION,
        source=(
            "Ossorio R. (2023) PhD thesis, Universitat Politecnica de Valencia, Appendix 1 (PDF pp. 244-252); "
            "same tests as Compressor A in Ossorio & Navarro-Peris (2023) Appl. Therm. Eng. 230:120725"
        ),
        compressor="46 cm3/rev R290 scroll, superheat about 10 K, 30-110 rev/s",
        power_basis="compressor + inverter input (no separate inverter model, #217)",
        form=(
            "eta = c . [1, l, l^2, l^3, N, N^2, l N (, T_e)], l = ln PR, N the speed in rev/s, "
            "T_e the evaporating saturation temperature in C; eta_v and eta_ov have T_e, f_loss not"
        ),
        points=len(points),
        cv=dict(folds=FOLDS, seed=SEED),
        maps=maps,
        domain=dict(
            hull_PR_Te=hull(points),
            T_evap_C=[float(points.T_evap_C.min()), float(points.T_evap_C.max())],
            PR=[float(points.PR.min()), float(points.PR.max())],
            N_rps=[float(points.N_rps.min()), float(points.N_rps.max())],
        ),
        check=dict(
            eta_v_vs_author_max_abs=float((points.eta_v - points.author_eta_v).abs().max()),
            eta_ov_vs_author_max_abs=float((points.eta_ov - points.author_eta_comp).abs().max()),
        ),
        environment=dict(CoolProp=CoolProp.__version__, fluid=FLUID),
    )
    (ARCHIVE / "coefficients.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({q: {k: v for k, v in m.items() if k != "coefficients"} for q, m in maps.items()}, indent=1))


if __name__ == "__main__":
    main()
