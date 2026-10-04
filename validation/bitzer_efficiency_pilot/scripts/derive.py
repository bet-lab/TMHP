"""Derive (r_p, n*, eta_v, eta_is, eta_em) from BITZER raw points.

Definitions follow TMHP (``air_source_heat_pump.py``):
    h_2 = h_1 + (h_2s - h_1) / eta_is          -> eta_is = (h_2s - h_1) / (h_2 - h_1)
    E_cmp = m_dot (h_2 - h_1) / eta_em         -> eta_em = m_dot (h_2 - h_1) / P_el
    m_dot = eta_v rho_1 V_d N                  -> eta_v  = m_dot / (rho_1 V_d N)

State 1: dew-point pressure at SST, T_1 = SST + SH.  State 2s: isentropic to the
dew-point pressure at SDT.  State 2: (P_2, BITZER "Discharge gas temp. w/o cooling").
N is the shaft speed assumed proportional to frequency from BITZER's own
"Displacement (2900 rpm 50 Hz)" rating: N = f * rpm_50 / 50 / 60 [rev/s]. That is
an assumption (constant relative slip); n*_f = f / 50 Hz does not depend on it.

Also kept: eta_oi = m_dot (h_2s - h_1) / P_el (identified without T_dis) and the
energy-balance closure of BITZER's condenser capacity, Q_cond / (Q_0 + P_el).

usage: derive.py RAW.csv OUT.csv
"""

from __future__ import annotations

import csv
import sys
from functools import cache

import CoolProp.CoolProp as CP

FLUIDS = {
    "R454B": "HEOS::R32[0.829248]&R1234yf[0.170752]",
    "R452B": "HEOS::R32[0.7124]&R125[0.0616]&R1234yf[0.2260]",
}
F_REF_HZ = 50.0


@cache
def props(out, a, av, b, bv, fluid):
    return float(CP.PropsSI(out, a, av, b, bv, fluid))


def h_ps(p, s, fluid, t_lo, t_hi):
    if "&" not in fluid:
        return props("H", "P", p, "S", s, fluid)
    lo, hi = t_lo, t_hi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if props("S", "P", p, "T", mid, fluid) < s:
            lo = mid
        else:
            hi = mid
    return props("H", "P", p, "T", 0.5 * (lo + hi), fluid)


def f(x):
    return float(x) if x not in (None, "", "None") else None


def derive(r: dict) -> dict:
    fluid = FLUIDS.get(r["refrigerant"], r["refrigerant"])
    out = dict(r)
    status = int(float(r["BITZER_limit_status"] or 0))
    P = f(r["power_input_W"])
    if status != 0 or P is None:
        out.update(valid_bitzer="no")
        return out
    Te, Tc = f(r["T_evap_C"]) + 273.15, f(r["T_cond_C"]) + 273.15
    p1 = props("P", "T", Te, "Q", 1, fluid)
    p2 = props("P", "T", Tc, "Q", 1, fluid)
    T1 = Te + f(r["superheat_K"])
    rho1 = props("D", "P", p1, "T", T1, fluid)
    h1 = props("H", "P", p1, "T", T1, fluid)
    s1 = props("S", "P", p1, "T", T1, fluid)
    h2s = h_ps(p2, s1, fluid, Tc, Tc + 150.0)
    h3 = props("H", "P", p2, "Q", 0, fluid)  # SC = 0 K; bubble point at the condenser pressure
    Q0 = f(r["capacity_W"])
    m = f(r["mass_flow_kg_s"])
    m_basis = "reported"
    if m is None:  # screw module reports no mass flow: EN 12900 rating, SC = 0 K
        m = Q0 / (h1 - h3)
        m_basis = "Q0/(h1-h3)"
    Qc = f(r["condenser_capacity_W"])
    hz = f(r["frequency_Hz"]) or f(r["frequency_requested_Hz"])
    rpm50 = f(r["rated_rpm_if_available"])
    Vd = f(r["displacement_m3_rev"])
    N = hz * rpm50 / F_REF_HZ / 60.0 if rpm50 else None
    eta_v = m / (rho1 * Vd * N) if (Vd and N) else None
    Tdis = f(r["T_discharge_C"])
    eta_is = eta_em = h2 = None
    if Tdis is not None:
        h2 = props("H", "P", p2, "T", Tdis + 273.15, fluid)
        if h2 > h1:
            eta_is = (h2s - h1) / (h2 - h1)
            eta_em = m * (h2 - h1) / P
    eta_oi = m * (h2s - h1) / P
    flags = []
    if "Additional cooling" in str(r.get("BITZER_warning", "")):
        flags.append("additional_cooling_required")
    for name, v, lo, hi in (("eta_v", eta_v, 0, 1.0), ("eta_is", eta_is, 0, 1), ("eta_em", eta_em, 0, 1)):
        if v is not None and not (lo < v <= hi):
            flags.append(f"{name}_gt_1" if v > hi else f"{name}_le_0")
    out.update(
        valid_bitzer="yes",
        fluid=fluid,
        mass_flow_basis=m_basis,
        P_suc_Pa=p1,
        P_dis_Pa=p2,
        pressure_ratio=p2 / p1,
        T_suc_K=T1,
        rho_suc=rho1,
        h1=h1,
        h2s=h2s,
        h2=h2,
        h3=h3,
        N_rps_assumed=N,
        normalized_speed=hz / F_REF_HZ,
        normalized_speed_basis="frequency_ratio",
        reference_speed_basis="manufacturer_nominal_frequency_50Hz",
        eta_v=eta_v,
        eta_is=eta_is,
        eta_em=eta_em,
        eta_oi=eta_oi,
        q0_check=Q0 / (m * (h1 - h3)),
        qc_closure=Qc / (Q0 + P) if Qc else None,
        h2_energy=h1 + P / m,
        sanity_flags=";".join(flags),
    )
    return out


def main(src, dst):
    with open(src) as fh:
        rows = [derive(r) for r in csv.DictReader(fh)]
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(dst, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    ok = [r for r in rows if r.get("valid_bitzer") == "yes"]
    print(f"{len(rows)} rows, {len(ok)} valid")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
