"""Derive compressor efficiencies from published points (CoolProp).

For each ``CompressorPoint``:
    p_e, p_c    dew-point saturation pressures at T_evap/T_cond (or printed pressures)
    state 1     suction: T_1 = T_evap + dT_sh (or measured T_suc), rho_1, h_1, s_1
    state 2s    isentropic discharge at p_c
    state 3     liquid: T_cond - dT_sc (0 K when the source rates 0 subcooling)
    m_dot       printed, or Q_evap / (h_1 - h_3)
    eta_vol   = m_dot / (rho_1 * V_disp * N)
    eta_oi    = m_dot (h_2s - h_1) / P_el              (electrical-to-isentropic, "overall isentropic")
    eta_isen  = (h_2s - h_1) / (h_2 - h_1)  when T_dis is printed (split_basis='measured')
    eta_em    = eta_oi / eta_isen              (same rows)

Identifiability: Q/P tables give eta_vol and the *product* eta_isen*eta_em only.
Rows without T_dis carry eta_oi and leave eta_isen/eta_em empty; ``fit`` handles
the split with a prior.  Discharge-temperature based eta_isen is biased high by
shell heat loss (part of the work leaves as heat, not as enthalpy rise); the
Cuevas rows carry the measured ambient loss so the bias can be bounded.

Refrigerant strings that CoolProp does not know by name are mapped to HEOS
mixtures (R454B).  Mixture PS flashes are done by bisection on T at fixed p.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict
from functools import cache
from pathlib import Path

import CoolProp.CoolProp as CP

from validation.compressor_maps.schema import COLUMNS, DATA_DIR, CompressorPoint, read_points

FLUIDS = {
    "R454B": "HEOS::R32[0.829248]&R1234yf[0.170752]",
    "R452B": "HEOS::R32[0.7124]&R125[0.0616]&R1234yf[0.2260]",  # 67/7/26 wt% -> mole fractions
}
DERIVED_COLUMNS = COLUMNS + [
    "fluid",
    "p_suc_Pa_d",
    "p_dis_Pa_d",
    "PR",
    "T_suc_K",
    "rho_suc",
    "h_suc",
    "s_suc",
    "h_dis_is",
    "h_liq",
    "m_dot_d",
    "Q_evap_d",
    "dh_is",
    "eta_vol",
    "eta_oi",
    "eta_isen",
    "eta_em",
    "split_basis",
    "n_star",
    "T_dis_is_C",
]


def fluid_of(refrigerant: str) -> str:
    return FLUIDS.get(refrigerant, refrigerant)


@cache
def _props(out: str, a: str, av: float, b: str, bv: float, fluid: str) -> float:
    return float(CP.PropsSI(out, a, av, b, bv, fluid))


def _h_isentropic(p: float, s: float, fluid: str, t_lo: float, t_hi: float) -> float:
    """H at (p, s): direct for pure fluids, bisection on T for mixtures."""
    if not fluid.startswith("HEOS::") or "&" not in fluid:
        return _props("H", "P", p, "S", s, fluid)
    lo, hi = t_lo, t_hi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _props("S", "P", p, "T", mid, fluid) < s:
            lo = mid
        else:
            hi = mid
    return _props("H", "P", p, "T", 0.5 * (lo + hi), fluid)


def derive_one(pt: CompressorPoint) -> dict:
    fluid = fluid_of(pt.refrigerant)
    if pt.p_suc_Pa is not None and pt.p_dis_Pa is not None:
        p_e, p_c = pt.p_suc_Pa, pt.p_dis_Pa
        T_e = _props("T", "P", p_e, "Q", 1, fluid)
        T_c = _props("T", "P", p_c, "Q", 1, fluid)
    else:
        T_e, T_c = pt.T_evap_C + 273.15, pt.T_cond_C + 273.15
        q_sat = 1 if pt.sat_convention in ("dew_dew", "dew_mid") else 0
        p_e = _props("P", "T", T_e, "Q", q_sat, fluid)
        p_c = _props("P", "T", T_c, "Q", 1 if pt.sat_convention != "bubble_dew" else 0, fluid)
    if pt.T_suc_C is not None:
        T_1 = pt.T_suc_C + 273.15
    elif pt.sh_convention == "rg20":
        T_1 = 293.15
    else:
        T_1 = T_e + (pt.dT_sh_K if pt.dT_sh_K is not None else 0.0)
    T_1 = max(T_1, T_e + 0.3)
    rho_1 = _props("D", "P", p_e, "T", T_1, fluid)
    h_1 = _props("H", "P", p_e, "T", T_1, fluid)
    s_1 = _props("S", "P", p_e, "T", T_1, fluid)
    h_2s = _h_isentropic(p_c, s_1, fluid, T_c, T_c + 150.0)
    T_liq = T_c - (pt.dT_sc_K or 0.0)
    if pt.dT_sc_K and pt.dT_sc_K > 0:
        h_3 = _props("H", "P", p_c, "T", T_liq, fluid)
    else:
        h_3 = _props("H", "P", p_c, "Q", 0, fluid)
    m_dot = pt.m_dot_kg_s if pt.m_dot_kg_s is not None else pt.Q_evap_W / (h_1 - h_3)
    q_evap = pt.Q_evap_W if pt.Q_evap_W is not None else m_dot * (h_1 - h_3)
    dh_is = h_2s - h_1
    eta_vol = m_dot / (rho_1 * pt.V_disp_cm3 * 1e-6 * pt.N_rps)
    eta_oi = m_dot * dh_is / pt.P_el_W if pt.P_el_W else None
    eta_isen = eta_em = None
    basis = "product_only"
    if pt.T_dis_C is not None:
        h_2 = _props("H", "P", p_c, "T", pt.T_dis_C + 273.15, fluid)
        if h_2 > h_1:
            eta_isen = dh_is / (h_2 - h_1)
            eta_em = eta_oi / eta_isen if eta_oi else None
            basis = "measured_Tdis"
    elif pt.Q_cond_W is not None:
        h_2 = h_3 + pt.Q_cond_W / m_dot
        eta_isen = dh_is / (h_2 - h_1)
        eta_em = eta_oi / eta_isen if eta_oi else None
        basis = "measured_Qcond"
    try:
        T_2s = _props("T", "P", p_c, "H", h_2s, fluid) - 273.15
    except Exception:  # noqa: BLE001
        T_2s = math.nan
    d = asdict(pt)
    d.pop("extra", None)
    d.update(
        {
            "fluid": fluid,
            "p_suc_Pa_d": p_e,
            "p_dis_Pa_d": p_c,
            "PR": p_c / p_e,
            "T_suc_K": T_1,
            "rho_suc": rho_1,
            "h_suc": h_1,
            "s_suc": s_1,
            "h_dis_is": h_2s,
            "h_liq": h_3,
            "m_dot_d": m_dot,
            "Q_evap_d": q_evap,
            "dh_is": dh_is,
            "eta_vol": eta_vol,
            "eta_oi": eta_oi,
            "eta_isen": eta_isen,
            "eta_em": eta_em,
            "split_basis": basis,
            "n_star": pt.n_star,
            "T_dis_is_C": T_2s,
        }
    )
    return d


def derive_file(path: Path) -> list[dict]:
    rows = []
    for pt in read_points(path):
        try:
            rows.append(derive_one(pt))
        except Exception as e:  # noqa: BLE001
            print(f"  skip {pt.source_id} {pt.model} N={pt.N_rps:.1f} Te={pt.T_evap_C} Tc={pt.T_cond_C}: {str(e)[:80]}")
    return rows


def main() -> None:
    out = DATA_DIR / "points_derived.csv"
    rows: list[dict] = []
    for p in sorted(DATA_DIR.glob("points_*.csv")):
        if p.name in ("points_derived.csv", "points_fit_ready.csv"):
            continue
        r = derive_file(p)
        print(f"{p.name}: {len(r)} rows")
        rows.extend(r)
    for p in sorted(DATA_DIR.glob("derived_extra_*.csv")):  # sources published as efficiency functions
        with p.open() as fh:
            extra = list(csv.DictReader(fh))
        print(f"{p.name}: {len(extra)} rows (pre-derived)")
        rows.extend(extra)
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=DERIVED_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} derived rows -> {out}")


if __name__ == "__main__":
    main()
