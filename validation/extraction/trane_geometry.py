"""L5 -- outdoor-coil conductance of packaged heat pumps, from geometry alone.

Why this route exists
---------------------
Inverting component heat-exchanger catalogues (``en328_inversion`` /
``env327_inversion``) gives a band of conductance per unit duty, but those are
refrigeration components, not heat pumps. Transferring their band to heat-pump
outdoor coils needs an argument. This module removes the need for one: Trane
prints, for every Precedent packaged heat pump, the outdoor coil's face area,
row count, fin density and tube size *together with* the outdoor fan flow and
the rated cooling capacity. That is enough to compute the conductance directly
from a published air-side correlation, with no reference to any component
catalogue.

The two routes share neither inputs nor method, so agreement between them is
evidence and not circularity.

Method
------
Air side
    Wang, Chi & Chang (2000), "Heat transfer and friction characteristics of
    plain fin-and-tube heat exchangers, part II: Correlation",
    *Int. J. Heat Mass Transfer* 43(15), 2693-2700,
    doi:10.1016/S0017-9310(99)00333-6 -- the ``N >= 2`` Colburn-j correlation.
    This is the correlation the TMHP manuscript already cites for the air-side
    exponent, so the evidence base gains no new citation.
Fin efficiency
    Schmidt's equivalent-annulus approximation for a staggered bank.
Refrigerant side
    Declared film coefficient (see ``H_REF_DECLARED``). This is the largest
    single uncertainty and is reported as a sensitivity, not hidden.

Declared values
---------------
Trane prints tube outside diameter, rows and fin density but not tube pitch or
fin thickness. Standard round-tube-plate-fin practice for 5/16 in. tubes is
declared below and swept +-15 % in the sensitivity block, so the reader sees
what the declaration is worth.

Run
---
``uv run python -m validation.extraction.trane_geometry``
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
GEOMETRY_CSV = REPO_ROOT / "validation" / "data" / "trane_precedent_geometry.csv"
OUT_CSV = REPO_ROOT / "validation" / "data" / "hp_outdoor_coil_ua_geometry.csv"

# --- declared standard RTPF practice (not printed by the manufacturer) -------
#: Transverse tube pitch (perpendicular to air flow, within a row) [m].
PT_DECLARED = 25.4e-3
#: Longitudinal tube pitch (row-to-row, along air flow) [m].
PL_DECLARED = 22.0e-3
#: Fin thickness [m]. 0.0045 in aluminium, ordinary for this equipment class.
FIN_THICKNESS_DECLARED = 0.115e-3
#: Tube wall thickness [m].
TUBE_WALL_DECLARED = 0.33e-3
#: Fin thermal conductivity [W/(m K)] -- aluminium.
K_FIN = 204.0
#: Tube thermal conductivity [W/(m K)] -- copper.
K_TUBE = 385.0
#: Refrigerant-side film coefficient [W/(m^2 K)]. Condensing R-410A / R-22 in a
#: 5/16 in. tube at rating mass flux. Dominant declared quantity.
H_REF_DECLARED = 2500.0

# --- air properties at the AHRI cooling rating point, 95 F = 35 degC ---------
AIR_T_RATING_C = 35.0
AIR_RHO_35 = 1.1455
AIR_MU_35 = 1.8925e-5
AIR_K_35 = 0.02625
AIR_CP_35 = 1006.5
AIR_PR_35 = AIR_MU_35 * AIR_CP_35 / AIR_K_35

BTUH_TO_W = 0.29307107
CFM_TO_M3S = 0.000471947
SQFT_TO_M2 = 0.09290304
INCH_TO_M = 0.0254


@dataclass(frozen=True)
class CoilGeometry:
    """Derived areas of one finned-tube coil."""

    a_face: float
    depth: float
    dc: float
    fin_pitch: float
    a_min_free: float
    a_fin: float
    a_tube_out: float
    a_tube_in: float
    a_out_total: float
    d_hydraulic: float
    tube_length_total: float


def build_geometry(
    a_face_m2: float,
    rows: int,
    fpi: float,
    tube_od_m: float,
    *,
    pt: float = PT_DECLARED,
    pl: float = PL_DECLARED,
    fin_t: float = FIN_THICKNESS_DECLARED,
    tube_wall: float = TUBE_WALL_DECLARED,
) -> CoilGeometry:
    """Reduce printed coil dimensions to the areas the correlation needs."""
    fin_pitch = INCH_TO_M / fpi  # centre-to-centre fin spacing [m]
    dc = tube_od_m + 2.0 * fin_t  # collar diameter
    depth = rows * pl

    # Tubes per unit face area: one tube every `pt` of face height, per row.
    tube_length_total = rows * a_face_m2 / pt  # [m] of tube per m^2 face * m^2

    # Bare tube outside area, less the strip covered by fin collars.
    a_tube_out = math.pi * dc * tube_length_total * (1.0 - fin_t / fin_pitch)
    d_inner = tube_od_m - 2.0 * tube_wall
    a_tube_in = math.pi * d_inner * tube_length_total

    # Fin plates: two sides, minus the tube penetrations.
    n_fins = a_face_m2 / fin_pitch  # count * (unit width) -- area-consistent
    a_fin = 2.0 * n_fins * (depth * 1.0 - rows * (1.0 / pt) * math.pi * dc**2 / 4.0)
    # Note: the `1.0` factors carry the unit face width; every term below is a
    # ratio or is divided by a_face, so the bookkeeping stays consistent.

    a_out_total = a_fin + a_tube_out

    # Minimum free-flow area: transverse gap between collars, less fin blockage.
    a_min_free = a_face_m2 * ((pt - dc) / pt) * ((fin_pitch - fin_t) / fin_pitch)

    d_hydraulic = 4.0 * a_min_free * depth / a_out_total

    return CoilGeometry(
        a_face=a_face_m2,
        depth=depth,
        dc=dc,
        fin_pitch=fin_pitch,
        a_min_free=a_min_free,
        a_fin=a_fin,
        a_tube_out=a_tube_out,
        a_tube_in=a_tube_in,
        a_out_total=a_out_total,
        d_hydraulic=d_hydraulic,
        tube_length_total=tube_length_total,
    )


def wang_j_factor(
    re_dc: float, rows: int, geom: CoilGeometry, pt: float = PT_DECLARED, pl: float = PL_DECLARED
) -> float:
    """Colburn j factor, Wang/Chi/Chang (2000) plain-fin correlation, N >= 2."""
    fp, dc, dh = geom.fin_pitch, geom.dc, geom.d_hydraulic
    n = float(rows)
    ln_re = math.log(re_dc)

    p3 = -0.361 - 0.042 * n / ln_re + 0.158 * math.log(n * (fp / dc) ** 0.41)
    p4 = -1.224 - 0.076 * (pl / dh) ** 1.42 / ln_re
    p5 = -0.083 + 0.058 * n / ln_re
    p6 = -5.735 + 1.21 * math.log(re_dc / n)

    return 0.086 * re_dc**p3 * n**p4 * (fp / dc) ** p5 * (fp / dh) ** p6 * (fp / pt) ** -0.93


def schmidt_fin_efficiency(
    h_air: float,
    geom: CoilGeometry,
    fin_t: float = FIN_THICKNESS_DECLARED,
    pt: float = PT_DECLARED,
    pl: float = PL_DECLARED,
) -> float:
    """Schmidt equivalent-annulus fin efficiency for a staggered bank."""
    r = geom.dc / 2.0
    m = math.sqrt(2.0 * h_air / (K_FIN * fin_t))

    xm = pt / 2.0
    xl = math.sqrt(pl**2 + (pt / 2.0) ** 2) / 2.0
    req_over_r = 1.27 * (xm / r) * math.sqrt(xl / xm - 0.3)
    phi = (req_over_r - 1.0) * (1.0 + 0.35 * math.log(req_over_r))

    mrphi = m * r * phi
    return math.tanh(mrphi) / mrphi


def coil_ua(
    a_face_m2: float,
    rows: int,
    fpi: float,
    tube_od_m: float,
    volume_flow_m3_s: float,
    *,
    h_ref: float = H_REF_DECLARED,
    pt: float = PT_DECLARED,
    pl: float = PL_DECLARED,
    fin_t: float = FIN_THICKNESS_DECLARED,
) -> dict[str, float]:
    """Overall conductance of one outdoor coil at its rating air flow [W/K]."""
    geom = build_geometry(a_face_m2, rows, fpi, tube_od_m, pt=pt, pl=pl, fin_t=fin_t)

    u_max = volume_flow_m3_s / geom.a_min_free
    g_max = AIR_RHO_35 * u_max  # mass flux at minimum area [kg/(m^2 s)]
    re_dc = g_max * geom.dc / AIR_MU_35

    j = wang_j_factor(re_dc, rows, geom, pt=pt, pl=pl)
    h_air = j * g_max * AIR_CP_35 / AIR_PR_35 ** (2.0 / 3.0)

    eta_fin = schmidt_fin_efficiency(h_air, geom, fin_t=fin_t, pt=pt, pl=pl)
    eta_surface = 1.0 - (geom.a_fin / geom.a_out_total) * (1.0 - eta_fin)

    d_inner = tube_od_m - 2.0 * TUBE_WALL_DECLARED
    r_wall = math.log(tube_od_m / d_inner) / (2.0 * math.pi * K_TUBE * geom.tube_length_total)

    r_total = 1.0 / (eta_surface * h_air * geom.a_out_total) + r_wall + 1.0 / (h_ref * geom.a_tube_in)
    ua = 1.0 / r_total

    return {
        "re_dc": re_dc,
        "u_max_m_s": u_max,
        "j": j,
        "h_air": h_air,
        "eta_fin": eta_fin,
        "eta_surface": eta_surface,
        "a_out_total_m2": geom.a_out_total,
        "a_tube_in_m2": geom.a_tube_in,
        "d_hydraulic_mm": geom.d_hydraulic * 1000.0,
        "ua_w_k": ua,
        "air_frac": (1.0 / (eta_surface * h_air * geom.a_out_total)) / r_total,
    }


def load_units() -> pd.DataFrame:
    return pd.read_csv(GEOMETRY_CSV, comment="#")


def compute(df: pd.DataFrame, **kw) -> pd.DataFrame:
    rows_out = []
    for _, u in df.iterrows():
        q_cool_w = u.gross_cool_btuh * BTUH_TO_W
        eer_w_w = q_cool_w / (u.system_power_kw * 1000.0)
        flow = u.od_fan_cfm * CFM_TO_M3S
        res = coil_ua(
            u.od_face_area_sqft * SQFT_TO_M2,
            int(u.od_rows),
            float(u.od_fpi),
            u.od_tube_od_in * INCH_TO_M,
            flow,
            **kw,
        )
        # Heat rejected by the outdoor coil at the rating point.
        q_cond_w = q_cool_w * (1.0 + 1.0 / eer_w_w)
        lmtd = q_cond_w / res["ua_w_k"]
        c_air = AIR_RHO_35 * flow * AIR_CP_35
        ntu = res["ua_w_k"] / c_air
        eps = 1.0 - math.exp(-ntu)
        # Saturated condensing temperature implied by that LMTD.
        t_cond = AIR_T_RATING_C + lmtd * ntu / eps

        rows_out.append(
            {
                "model": u.model,
                "generation": u.generation,
                "refrigerant": u.refrigerant,
                "Q_cool_kW": q_cool_w / 1000.0,
                "EER_W_W": eer_w_w,
                "Q_cond_kW": q_cond_w / 1000.0,
                "Q_cond_over_Q_cool": q_cond_w / q_cool_w,
                "od_flow_m3_s": flow,
                "flow_m3h_per_kW_cond": flow * 3600.0 / (q_cond_w / 1000.0),
                "Re_Dc": res["re_dc"],
                "h_air_W_m2K": res["h_air"],
                "eta_surface": res["eta_surface"],
                "A_out_m2": res["a_out_total_m2"],
                "air_side_resistance_frac": res["air_frac"],
                "UA_W_K": res["ua_w_k"],
                "LMTD_K": lmtd,
                "T_cond_implied_C": t_cond,
                "UA_over_Q_cond": res["ua_w_k"] / q_cond_w,
                "UA_over_Q_cool": res["ua_w_k"] / q_cool_w,
                "capacity_divisor": q_cool_w / res["ua_w_k"],
            }
        )
    return pd.DataFrame(rows_out)


def _band(s: pd.Series) -> str:
    return f"{s.min():.3f}-{s.max():.3f} (median {s.median():.3f})"


def main() -> None:
    units = load_units()
    out = compute(units)
    out.to_csv(OUT_CSV, index=False)

    print("L5 -- outdoor-coil UA from published geometry (Trane Precedent, 12 units)")
    print("  air-side correlation : Wang, Chi & Chang (2000), N >= 2 plain fin")
    print(f"  rating point         : {AIR_T_RATING_C:.0f} degC air (AHRI cooling)")
    print(
        f"  declared: Pt {PT_DECLARED * 1e3:.1f} mm, Pl {PL_DECLARED * 1e3:.1f} mm, "
        f"fin {FIN_THICKNESS_DECLARED * 1e3:.3f} mm, h_ref {H_REF_DECLARED:.0f} W/(m2 K)"
    )
    print()
    cols = [
        "model",
        "Q_cool_kW",
        "EER_W_W",
        "Re_Dc",
        "h_air_W_m2K",
        "eta_surface",
        "UA_W_K",
        "LMTD_K",
        "T_cond_implied_C",
        "UA_over_Q_cool",
        "capacity_divisor",
    ]
    print(out[cols].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print()
    print(f"  UA/Q_cool        : {_band(out.UA_over_Q_cool)}")
    print(f"  UA/Q_cond        : {_band(out.UA_over_Q_cond)}")
    print(f"  equivalent LMTD  : {_band(out.LMTD_K)} K")
    print(f"  implied T_cond   : {_band(out.T_cond_implied_C)} degC")
    print(f"  coil-duty flow   : {_band(out.flow_m3h_per_kW_cond)} m3/h per kW")
    print(f"  air-side share of total resistance: {_band(out.air_side_resistance_frac)}")
    print()
    divisor = 1.0 / out.UA_over_Q_cool.median()
    print(f"  ==> capacity-based rule: UA_ou = hp_capacity / {divisor:.2f}")
    print(f"      wrote {OUT_CSV.relative_to(REPO_ROOT)}")

    print()
    print("  Sensitivity to the declared quantities (median UA/Q_cool):")
    base = out.UA_over_Q_cool.median()
    for label, kw in [
        ("h_ref 1500 (from 2500)", {"h_ref": 1500.0}),
        ("h_ref 5000", {"h_ref": 5000.0}),
        ("h_ref -> infinity", {"h_ref": 1.0e9}),
        ("Pt +15 %", {"pt": PT_DECLARED * 1.15}),
        ("Pt -15 %", {"pt": PT_DECLARED * 0.85}),
        ("Pl +15 %", {"pl": PL_DECLARED * 1.15}),
        ("Pl -15 %", {"pl": PL_DECLARED * 0.85}),
        ("fin thickness +15 %", {"fin_t": FIN_THICKNESS_DECLARED * 1.15}),
        ("fin thickness -15 %", {"fin_t": FIN_THICKNESS_DECLARED * 0.85}),
    ]:
        m = compute(units, **kw).UA_over_Q_cool.median()
        print(f"    {label:24s} {m:.4f}  ({(m / base - 1.0) * 100:+.1f} %)  -> Q/{1.0 / m:.2f}")


if __name__ == "__main__":
    main()
