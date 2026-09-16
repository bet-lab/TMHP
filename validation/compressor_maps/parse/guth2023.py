"""Guth & Atakan (2023), Int. J. Refrig. 146:483-499, doi:10.1016/j.ijrefrig.2022.10.024.

Variable-speed scroll (Emerson/Copeland ZHV046, R-290, POE) tested on a 14 kW
bench, 1800-5400 rpm, 156 operating points; the paper publishes *fitted*
efficiency functions rather than the raw points:

    Eq. (20)  eta_v(pi)           = a pi^2 + b pi + c                     Table A.1
    Eq. (21)  eta_s(pi, n, Tc)    = sum_i pi^(2-i) [Tc^2 Tc 1] A_i [n^2 n 1]^T   Table A.2
    Eq. (22)  eta_comp(pi, n, Te) = sum_i pi^(2-i) [Te^2 Te 1] A_i [n^2 n 1]^T   Table A.3

with pi = p(Tc)/p(Te) (dew), n in rpm, T in degC.  eta_s is the isentropic
efficiency (isentropic work / work delivered to the refrigerant), eta_comp the
electro-mechanical efficiency (motor + mechanical; the frequency-converter loss
is stated to be small and is not separated).  Their product is the electrical-
to-isentropic efficiency eta_oi.  The functions are evaluated on the fitting
envelope of Table 1 (Te -5/0/5 degC, Tc 35-50 degC, n 1800-5400 rpm) and stored
directly in the *derived* format (they are efficiencies, not capacities), with
``method='published_model'`` and ``split_basis='published_split'``.

Displacement: 46.05 cm3/rev (Copeland 046 platform, OPI mechanical
displacement 2.81 in^3/rev).  Rated speed: 4500 rpm -- Copeland's "capacity
rating speed" for the ZHV/ZPV families in OPI (basis 'rating_row').
Coefficient matrices transcribed from the appendix tables of the archived PDF
(validation/evidence/pdfs/guth_atakan_2023_ijrefrig.pdf, pp. 495-496).
"""

from __future__ import annotations

import csv

import CoolProp.CoolProp as CP

from validation.compressor_maps.derive import DERIVED_COLUMNS
from validation.compressor_maps.schema import DATA_DIR

SOURCE_ID = "guth_atakan_2023"
V_DISP_CM3 = 46.05
N_RATED_RPM = 4500.0
FLUID = "R290"

A1_VOL = (0.00683154, -0.06876312, 1.00797235)  # Table A.1, scroll/R-290

# Table A.2, scroll/R-290 -- eta_s, rows pair with (n^2, n, 1), columns with (Tc^2, Tc, 1)
A_S = (
    (
        (9.88235991e-11, -8.83581762e-09, 1.95189394e-07),
        (-9.74390523e-07, 8.65545844e-05, -1.89450724e-03),
        (1.56129544e-03, -1.32067821e-01, 2.69683260e00),
    ),
    (
        (-4.06643637e-10, 3.62423302e-08, -7.95868728e-07),
        (4.37082719e-06, -3.87143895e-04, 8.42228834e-03),
        (-7.31600581e-03, 6.11368510e-01, -1.22011957e01),
    ),
    (
        (3.81902911e-10, -3.41674867e-08, 7.47429740e-07),
        (-4.73896035e-06, 4.22415809e-04, -9.21630940e-03),
        (8.55048075e-03, -7.20783031e-01, 1.51818016e01),
    ),
)
# Table A.3, scroll/R-290 -- eta_comp, columns pair with (Te^2, Te, 1)
A_COMP = (
    (
        (-3.66418494e-10, 1.72087897e-09, 1.04857472e-08),
        (2.81656414e-06, -1.09398298e-05, -8.80890652e-05),
        (-5.94673980e-03, 1.41172509e-02, 1.85009090e-01),
    ),
    (
        (2.22336271e-09, -1.14775474e-08, -5.65025114e-08),
        (-1.65831030e-05, 7.13478801e-05, 4.76082956e-04),
        (3.38019071e-02, -8.72990757e-02, -9.85248276e-01),
    ),
    (
        (-3.31242445e-09, 1.88339750e-08, 6.44687148e-08),
        (2.37737242e-05, -1.14880656e-04, -5.43870648e-04),
        (-4.59804512e-02, 1.36903699e-01, 1.92659163e00),
    ),
)


def triple_poly(A, pi: float, rpm: float, t_c: float) -> float:
    t_vec = (t_c * t_c, t_c, 1.0)
    n_vec = (rpm * rpm, rpm, 1.0)
    total = 0.0
    for i, mat in enumerate(A):
        inner = 0.0
        for row, n_comp in zip(mat, n_vec, strict=True):
            inner += sum(a * t for a, t in zip(row, t_vec, strict=True)) * n_comp
        total += pi ** (2 - i) * inner
    return total


def rows() -> list[dict]:
    out = []
    grid = [
        (te, tc, rpm)
        for rpm in (1800.0, 3000.0, 4200.0, 5400.0)
        for te in (-5.0, 0.0, 5.0)
        for tc in (35.0, 40.0, 45.0, 50.0)
    ]
    for te, tc, rpm in grid:
        p_e = CP.PropsSI("P", "T", te + 273.15, "Q", 1, FLUID)
        p_c = CP.PropsSI("P", "T", tc + 273.15, "Q", 1, FLUID)
        pi = p_c / p_e
        eta_v = A1_VOL[0] * pi * pi + A1_VOL[1] * pi + A1_VOL[2]
        eta_s = triple_poly(A_S, pi, rpm, tc)
        eta_c = triple_poly(A_COMP, pi, rpm, te)
        T_1 = te + 273.15 + 6.0  # constant superheat 6 +/- 1 K in the fitting campaign
        rho_1 = CP.PropsSI("D", "P", p_e, "T", T_1, FLUID)
        h_1 = CP.PropsSI("H", "P", p_e, "T", T_1, FLUID)
        s_1 = CP.PropsSI("S", "P", p_e, "T", T_1, FLUID)
        h_2s = CP.PropsSI("H", "P", p_c, "S", s_1, FLUID)
        h_3 = CP.PropsSI("H", "P", p_c, "Q", 0, FLUID)
        m_dot = rho_1 * V_DISP_CM3 * 1e-6 * rpm / 60.0 * eta_v
        d = {c: "" for c in DERIVED_COLUMNS}
        d.update(
            {
                "source_id": SOURCE_ID,
                "manufacturer": "Copeland (tested by Guth & Atakan 2023)",
                "model": "ZHV046 R-290",
                "comp_type": "scroll",
                "refrigerant": FLUID,
                "V_disp_cm3": V_DISP_CM3,
                "N_rated_rps": N_RATED_RPM / 60.0,
                "N_rated_basis": "rating_row",
                "N_rps": rpm / 60.0,
                "Q_evap_W": m_dot * (h_1 - h_3),
                "P_el_W": m_dot * (h_2s - h_1) / (eta_s * eta_c),
                "T_evap_C": te,
                "T_cond_C": tc,
                "sat_convention": "dew_dew",
                "sh_convention": "custom",
                "dT_sh_K": 6.0,
                "dT_sc_K": 0.0,
                "m_dot_kg_s": m_dot,
                "P_includes_inverter": "",
                "speed_variant": f"{rpm:.0f} rpm",
                "method": "published_model",
                "doc_path": "pdfs/guth_atakan_2023_ijrefrig.pdf",
                "page": "495-496",
                "table_id": "Tables A.1-A.3",
                "note": "efficiency functions Eq.(20)-(22) evaluated on the Table 1 fitting envelope",
                "weight": 1.0 / len(grid),
                "fluid": FLUID,
                "p_suc_Pa_d": p_e,
                "p_dis_Pa_d": p_c,
                "PR": pi,
                "T_suc_K": T_1,
                "rho_suc": rho_1,
                "h_suc": h_1,
                "s_suc": s_1,
                "h_dis_is": h_2s,
                "h_liq": h_3,
                "m_dot_d": m_dot,
                "Q_evap_d": m_dot * (h_1 - h_3),
                "dh_is": h_2s - h_1,
                "eta_vol": eta_v,
                "eta_oi": eta_s * eta_c,
                "eta_isen": eta_s,
                "eta_em": eta_c,
                "split_basis": "published_split",
                "n_star": rpm / N_RATED_RPM,
                "T_dis_is_C": CP.PropsSI("T", "P", p_c, "H", h_2s, FLUID) - 273.15,
            }
        )
        out.append(d)
    return out


def main() -> None:
    r = rows()
    # guards: at 4200 rpm, Te 0, Tc 45 the paper's Fig. 5/6 show eta_comp ~0.8 and eta_s ~0.8; eta_v within 0.84-0.89
    ev = [x["eta_vol"] for x in r]
    assert min(ev) > 0.83 and max(ev) < 0.90, (min(ev), max(ev))
    es = [x["eta_isen"] for x in r]
    ec = [x["eta_em"] for x in r]
    print(f"eta_s range {min(es):.3f}-{max(es):.3f}, eta_comp range {min(ec):.3f}-{max(ec):.3f}")
    assert min(es) > 0.6 and max(es) < 0.95 and min(ec) > 0.6 and max(ec) < 0.95
    out = DATA_DIR / "derived_extra_guth2023.csv"
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=DERIVED_COLUMNS)
        w.writeheader()
        w.writerows(r)
    print(f"{len(r)} rows -> {out}")


if __name__ == "__main__":
    main()
