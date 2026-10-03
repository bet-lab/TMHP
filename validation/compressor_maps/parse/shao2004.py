"""Shao, Shi, Li & Chen (2004), Int. J. Refrig. 27(8):805-815, doi:10.1016/j.ijrefrig.2004.02.008.

Map-based models of three rolling-piston *rotary* inverter compressors fitted to
the manufacturers' calorimeter performance curves:

    Mitsubishi RHV207FEM  20.7 cm3   base 60 Hz   Table 1: separate (a1..a6, b1..b6) at 30/60/90/120 Hz
    SANYO 80870080        13.2 cm3   base 60 Hz   Table 4: base coefficients + frequency corrections
    Hitachi SGZ20DB2-Y    12.5 cm3   base 79 Hz   Table 4

    M0p [kg/h] = a1 Tc^2 + a2 Tc + a3 Tc Te + a4 Te^2 + a5 Te + a6            Eq. (1)
    P0p [W]    = b1 Tc^2 + b2 Tc + b3 Tc Te + b4 Te^2 + b5 Te + b6            Eq. (2)
    kM = c1 (f-fp)^2 + c2 (f-fp) + c3,   kP = d1 (f-fp)^2 + d2 (f-fp) + d3    Eqs. (12), (18)

Map condition (Sec. 2): 11 K return-gas superheat, 8.3 K liquid subcooling,
35 degC ambient -- the ASHRAE rotary rating convention.  Frequency is the
inverter output for a two-pole motor, so 1 Hz = 1 rev/s: at 60 Hz the fitted
mass flow of the 20.7 cm3 machine implies a volumetric efficiency of ~0.85 with
R22 suction density, which is only consistent with rps = f.  Refrigerant: R22 --
Mitsubishi's RH-series designation and the era (curves published before the
R410A rotary generation); the volumetric check in ``main`` guards the reading
(R410A density would give eta_vol ~0.6).  Power is the compressor motor input
from the manufacturer curves; whether the inverter is included is not stated.

These are the only *rotary* multi-speed data in the evidence base, so they are
kept as their own source and stratum.  Fit accuracy vs the manufacturer data:
<3 % mass flow, <5 % power (paper, Sec. 4).
"""

from __future__ import annotations

from validation.compressor_maps.schema import DATA_DIR, EVIDENCE_DIR, CompressorPoint, write_points

PDF = EVIDENCE_DIR / "papers" / "shao_2004_ijr_rotary.pdf"
SOURCE_ID = "shao_2004"
FLUID = "R22"
SH_K, SC_K = 11.0, 8.3
TE_GRID = (-10.0, -5.0, 0.0, 5.0, 10.0, 15.0)
TC_GRID = (40.0, 50.0, 60.0)

# Table 1 -- Mitsubishi RHV207FEM, per frequency (a1..a6 mass flow kg/h; b1..b6 power W)
MITSU_TABLE1 = {
    30.0: (
        (-8.92689e-4, -9.85219e-2, -4.97751e-3, 1.93062e-2, 1.50703e0, 4.01342e1),
        (4.30230e-2, 7.23835e0, 2.19679e-1, -8.74631e-2, -6.73222e0, 2.62012e2),
    ),
    60.0: (
        (-4.79867e-4, -3.58098e-1, -9.47004e-3, 4.42825e-2, 3.32561e0, 9.34178e1),
        (6.09746e-2, 1.42262e1, 4.00577e-1, -1.70792e-1, -1.25144e1, 4.08910e2),
    ),
    90.0: (
        (-8.35170e-4, -5.64545e-1, -1.62082e-2, 6.81640e-2, 5.28889e0, 1.47663e2),
        (1.51100e-1, 1.59387e1, 6.04910e-1, -2.42673e-1, -1.87068e1, 7.89481e2),
    ),
    120.0: (
        (-2.67829e-3, -5.91017e-1, -2.76056e-2, 9.22765e-2, 7.28263e0, 1.91097e2),
        (2.28717e-1, 2.80081e1, 9.89106e-1, -4.07291e-1, -3.08771e1, 1.26339e3),
    ),
}
# Table 4 -- base-frequency coefficients + frequency corrections
TABLE4 = {
    "SANYO 80870080": dict(
        V=13.2,
        fp=60.0,
        a=(-1.9397e-3, -3.7124e-2, -7.8361e-4, 1.8225e-2, 1.6186e0, 5.1302e1),
        b=(3.7275e-3, 1.5457e1, 1.1382e-1, -1.4670e-1, -3.2375e0, 3.1643e1),
        c=(-1.75090e-5, 1.84918e-2, 9.98824e-1),
        d=(2.06383e-5, 1.71603e-2, 9.95598e-1),
    ),
    "Hitachi SGZ20DB2-Y": dict(
        V=12.5,
        fp=79.0,
        a=(-5.9681e-3, 3.3301e-1, -2.4303e-3, 3.3835e-2, 2.0600e0, 4.6007e1),
        b=(6.8257e-2, 9.0473e0, 2.6956e-1, -1.2363e-1, -9.7600e0, 4.1689e2),
        c=(-9.44859e-6, 1.45697e-2, 9.99998e-1),
        d=(5.22484e-5, 1.54208e-2, 1.00000e0),
    ),
}
FREQS = (30.0, 45.0, 60.0, 75.0, 90.0, 105.0, 120.0)


def _quad(k, tc, te):
    return k[0] * tc * tc + k[1] * tc + k[2] * tc * te + k[3] * te * te + k[4] * te + k[5]


def _corr(k, f, fp):
    return k[0] * (f - fp) ** 2 + k[1] * (f - fp) + k[2]


def _point(model, vdisp, fp, f, te, tc, m_kgh, p_w, basis_note):
    return CompressorPoint(
        source_id=SOURCE_ID,
        manufacturer=model.split()[0],
        model=model,
        comp_type="rotary_single",
        refrigerant=FLUID,
        V_disp_cm3=vdisp,
        N_rated_rps=fp,
        N_rated_basis="nominal_statement",
        N_rps=f,
        Q_evap_W=None,
        P_el_W=p_w,
        T_evap_C=te,
        T_cond_C=tc,
        sat_convention="dew_dew",
        sh_convention="custom",
        dT_sh_K=SH_K,
        dT_sc_K=SC_K,
        m_dot_kg_s=m_kgh / 3600.0,
        P_includes_inverter=None,
        speed_variant=f"{f:g} Hz",
        method="published_model",
        doc_path=str(PDF.relative_to(EVIDENCE_DIR.parent)),
        page="809-812",
        table_id=basis_note,
        note="R22 inferred (RH series, volumetric check); map 11 K SH / 8.3 K SC / 35 C ambient",
        weight=1.0,
    )


def parse() -> list[CompressorPoint]:
    pts: list[CompressorPoint] = []
    n_grid = len(TE_GRID) * len(TC_GRID)
    for f, (a, b) in MITSU_TABLE1.items():
        for te in TE_GRID:
            for tc in TC_GRID:
                m, p = _quad(a, tc, te), _quad(b, tc, te)
                if m > 0 and p > 0:
                    pts.append(_point("Mitsubishi RHV207FEM", 20.7, 60.0, f, te, tc, m, p, "Table 1"))
    for model, k in TABLE4.items():
        for f in FREQS:
            km, kp = _corr(k["c"], f, k["fp"]), _corr(k["d"], f, k["fp"])
            for te in TE_GRID:
                for tc in TC_GRID:
                    m, p = _quad(k["a"], tc, te) * km, _quad(k["b"], tc, te) * kp
                    if m > 0 and p > 0:
                        pts.append(_point(model, k["V"], k["fp"], f, te, tc, m, p, "Table 4"))
    # each machine x frequency record sums to weight 1
    for pt in pts:
        object.__setattr__(pt, "weight", 1.0 / n_grid)
    return pts


def main() -> None:
    import CoolProp.CoolProp as CP

    pts = parse()
    # volumetric sanity at the base frequency, ASHRAE-T point (7.2 / 54.4 degC) -- guards the refrigerant reading
    for model, (vd, fp) in (
        ("Mitsubishi RHV207FEM", (20.7, 60.0)),
        ("SANYO 80870080", (13.2, 60.0)),
        ("Hitachi SGZ20DB2-Y", (12.5, 79.0)),
    ):
        k = MITSU_TABLE1[60.0][0] if model.startswith("Mitsubishi") else TABLE4[model]["a"]
        m = _quad(k, 54.4, 7.2) / 3600.0
        p_e = CP.PropsSI("P", "T", 7.2 + 273.15, "Q", 1, FLUID)
        rho = CP.PropsSI("D", "P", p_e, "T", 7.2 + SH_K + 273.15, FLUID)
        ev = m / (rho * vd * 1e-6 * fp)
        print(f"{model:22s} eta_vol at base/ASHRAE-T = {ev:.3f}")
        assert 0.60 < ev < 1.05, (
            model,
            ev,
        )  # Hitachi sits at 0.70: excluded from the eta_vol fit by prepare (rated check), kept for eta_oi
    out = DATA_DIR / "points_shao2004.csv"
    write_points(pts, out)
    print(f"{len(pts)} points -> {out}")


if __name__ == "__main__":
    main()
