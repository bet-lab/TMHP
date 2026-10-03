"""Cuevas & Lebrun (2009), Appl. Therm. Eng. 29:469-478, doi:10.1016/j.applthermaleng.2008.03.016.

Hermetic scroll, R134a, POE oil.  Theoretical swept volume flow 9.44 m3/h at the
nominal speed 2900 rpm (50 Hz supply, two-pole induction motor with slip), so
V_disp = 9.44 / 60 / 2900 = 54.25 cm3/rev (matches Ossorio 2023 Table 2).
Table 2: 18 tests at 50 Hz supplied from the network (no inverter in the power
measurement).  Table 3: 30 tests at 35/40/50/65/75 Hz supplied through the
inverter; the electrical power there is measured at the inverter supply, i.e.
it *includes* inverter losses (the paper's Fig. 3 inverter efficiency ~0.94-0.98).

Speed convention: the motor is an induction machine; the paper states 2900 rpm
at 50 Hz, so N_rps = f_Hz * 2900/50 / 60 = 0.9667 f.  Rated speed for n* is the
50 Hz nominal: 48.33 rev/s.

Columns per row: f [Hz], p_su [bar], p_ex [bar], t_su [degC], Rp, t_ex [degC],
m_dot [kg/s], W_el [kW] (+ Q_amb, eps_s, eps_v in Table 3, ignored here).
"""

from __future__ import annotations

import re
import subprocess

from validation.compressor_maps.schema import DATA_DIR, EVIDENCE_DIR, CompressorPoint, write_points

PDF = EVIDENCE_DIR / "papers" / "cuevas_lebrun_2009_ate.pdf"
SOURCE_ID = "cuevas_lebrun_2009"
V_DISP_CM3 = 9.44 / 60.0 / 2900.0 * 1e6  # 54.25
RPM_PER_HZ = 2900.0 / 50.0
N_RATED = 50.0 * RPM_PER_HZ / 60.0  # 48.33 rev/s

ROW = re.compile(r"^\s*(\d{2})\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(0\.\d{3})\s+(\d+\.\d{3})")


def parse() -> list[CompressorPoint]:
    txt = subprocess.run(["pdftotext", "-layout", str(PDF), "-"], capture_output=True, text=True, timeout=60).stdout
    pts: list[CompressorPoint] = []
    table = None
    for line in txt.splitlines():
        if line.strip().startswith("Table 2"):
            table = "Table 2"
        elif line.strip().startswith("Table 3"):
            table = "Table 3"
        elif line.strip().startswith("Table 4"):
            table = None
        m = ROW.match(line)
        if not m or table is None:
            continue
        f_hz, p_su, p_ex, t_su, rp, t_ex, mdot, w_kw = (float(x) for x in m.groups())
        if not (30 <= f_hz <= 80):
            continue
        pts.append(
            CompressorPoint(
                source_id=SOURCE_ID,
                manufacturer="(undisclosed, Cuevas & Lebrun 2009)",
                model="hermetic scroll 54.25 cm3 R134a",
                comp_type="scroll",
                refrigerant="R134a",
                V_disp_cm3=V_DISP_CM3,
                N_rated_rps=N_RATED,
                N_rated_basis="nominal_statement",
                N_rps=f_hz * RPM_PER_HZ / 60.0,
                Q_evap_W=None,
                P_el_W=w_kw * 1000.0,
                p_suc_Pa=p_su * 1e5,
                p_dis_Pa=p_ex * 1e5,
                sat_convention="pressure",
                sh_convention="measured",
                T_suc_C=t_su,
                m_dot_kg_s=mdot,
                T_dis_C=t_ex,
                P_includes_inverter=(table == "Table 3"),
                method="pdf_text",
                doc_path=str(PDF.relative_to(EVIDENCE_DIR.parent)),
                page="472",
                table_id=table,
                note=f"Rp printed {rp}",
            )
        )
    return pts


def main() -> None:
    pts = parse()
    assert len(pts) == 48, f"expected 18+30 rows, got {len(pts)}"
    # verbatim guard: first row of Table 2 and first row of Table 3 as printed
    a, b = pts[0], pts[18]
    assert (round(a.p_suc_Pa), round(a.p_dis_Pa), a.m_dot_kg_s, a.P_el_W) == (810000, 1560000, 0.096, 2180.0), a
    assert (round(b.N_rps, 3), round(b.p_dis_Pa), b.T_dis_C) == (round(35 * RPM_PER_HZ / 60, 3), 4030000, 123.6), b
    out = DATA_DIR / "points_cuevas2009.csv"
    write_points(pts, out)
    print(f"{len(pts)} points -> {out}")


if __name__ == "__main__":
    main()
