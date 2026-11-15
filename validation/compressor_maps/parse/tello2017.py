"""Tello-Oquendo, Navarro-Peris, Gonzalvez-Macia, Corberan et al. (2017), "New characterization methodology
for vapor-injection scroll compressors", Int. J. Refrigeration (author version, UPV repository).

Table 2 (printed page 10, pdf page 11): 16 calorimeter characterisation points of a fixed-speed scroll
compressor with vapour injection (SCVI), ZH18KVE-TFD, R407C, swept volume "17.1 m3h-1".

Stated by the paper
* Tests per UNE-EN 13771-1; secondary refrigerant calorimeter as primary, Coriolis (CMF025M) as confirming
  method; compressor-inlet superheat 5 K and injection superheat 5 K for all points (Sec. 3).
* "The evaporating and condensing temperatures are dew point temperatures"; Pe, Pc are the printed
  pressures (kPa), P_int the intermediate (injection) pressure (kPa).  Properties from REFPROP.
* Eq. (5): eta_v = m_e / (rho_1 V_dot), 1 = compressor inlet.
  Eq. (6): "overall compressor efficiency ... ratio between the ideal isentropic power consumption and the
  real indicated work": eta_c = [m_e (h4s' - h1) + m_inj (h4s - h8)] / E, h4s' isentropic from the inlet
  state 1 to the discharge pressure, h4s isentropic from the injection state 8 to the discharge pressure.
* E is "compressor power input" (kW); the paper does not state a measurement point (fixed speed, no inverter).
* Supply frequency and speed are not stated in the paper; the thesis (tello2019_thesis.pdf, Table 5.1, printed
  page 174) lists this SCVI as 98 cm3/rev, 17.1 m3/h "@ 50 Hz".  98 cm3 and 17.1 m3/h together give
  48.47 rev/s (2908 rpm, a two-pole induction motor at 50 Hz with slip); the CSV uses V_disp = 98 cm3 and
  N = 17.1e6/3600/98 rev/s, so V_disp*N reproduces the paper's V_dot of Eq. (5) exactly.
  ``N_rated_basis`` is "nominal_statement".
* Subcooling is not stated for the SCVI tests (the thesis gives 5 K subcooling only for the non-injection
  scrolls).  ``dT_sc_K`` is left blank.

Injection quantities go to ``m_inj_kg_s`` / ``p_int_Pa``; ``injection_compressor`` is True on every row.
Printed values are kept in ``*_printed`` columns (E in kW, flows in g/s, pressures in kPa).

Run ``python -m validation.compressor_maps.parse.tello2017`` to write the CSV.
"""

from __future__ import annotations

import csv
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

from validation.compressor_maps.schema import COLUMNS, DATA_DIR, EVIDENCE_DIR, CompressorPoint

REL = "compressor_maps/tello2017/tello_newcharacterization.pdf"
PDF = EVIDENCE_DIR.parent / REL
OUT = DATA_DIR / "points_tello2017_scvi.csv"
SOURCE_ID = "tello_oquendo_2017_ijr"
V_DISP_CM3 = 98.0
N_RPS = 17.1e6 / 3600.0 / V_DISP_CM3  # 48.47 rev/s

EXTRA_COLUMNS = [
    "injection_compressor",
    "m_inj_kg_s",
    "p_int_Pa",
    "T_inj_superheat_K",
    "Te_C_printed",
    "Tc_C_printed",
    "E_kW_printed",
    "m_e_g_s_printed",
    "m_inj_g_s_printed",
    "P_int_kPa_printed",
    "Pe_kPa_printed",
    "Pc_kPa_printed",
    "eta_c_printed",
    "eta_v_printed",
]

NUM = r"(-?\d+(?:\.\d+)?)"
ROW = re.compile(r"^\s*" + r"\s+".join([NUM] * 10) + r"\s*$")


def parse() -> list[dict]:
    txt = subprocess.run(["pdftotext", "-layout", str(PDF), "-"], capture_output=True, text=True, timeout=60).stdout
    start = txt.index("Results of the SCVI characterization at several working conditions.")
    end = txt.index("Fig. 4 depicts", start)
    rows: list[dict] = []
    for line in txt[start:end].splitlines():
        m = ROW.match(line)
        if not m:
            continue
        raw = list(m.groups())
        te, tc, e_kw, me, minj, pint, pe, pc = (float(x) for x in raw[:8])
        pt = CompressorPoint(
            source_id=SOURCE_ID,
            manufacturer="(not stated; ZH18KVE-TFD)",
            model="ZH18KVE-TFD (scroll with vapour injection)",
            comp_type="scroll",
            refrigerant="R407C",
            V_disp_cm3=V_DISP_CM3,
            N_rated_rps=N_RPS,
            N_rated_basis="nominal_statement",
            N_rps=N_RPS,
            Q_evap_W=None,
            P_el_W=round(e_kw * 1000.0, 6),
            T_evap_C=te,
            T_cond_C=tc,
            p_suc_Pa=round(pe * 1e3, 4),
            p_dis_Pa=round(pc * 1e3, 4),
            sat_convention="dew_dew",
            sh_convention="custom",
            dT_sh_K=5.0,
            m_dot_kg_s=round(me / 1000.0, 8),
            P_includes_inverter=False,
            method="pdf_text",
            doc_path=REL,
            page="pdf 11 (printed 10)",
            table_id="Table 2",
            note="vapour-injection scroll, fixed speed; Te/Tc are dew temperatures; suction and injection "
            "superheat 5 K; V_disp and N from the thesis Table 5.1 (98 cm3, 17.1 m3/h @ 50 Hz)",
        )
        d = asdict(pt)
        d.pop("extra", None)
        d.update(
            injection_compressor=True,
            m_inj_kg_s=round(minj / 1000.0, 8),
            p_int_Pa=round(pint * 1e3, 4),
            T_inj_superheat_K=5.0,
            Te_C_printed=raw[0],
            Tc_C_printed=raw[1],
            E_kW_printed=raw[2],
            m_e_g_s_printed=raw[3],
            m_inj_g_s_printed=raw[4],
            P_int_kPa_printed=raw[5],
            Pe_kPa_printed=raw[6],
            Pc_kPa_printed=raw[7],
            eta_c_printed=raw[8],
            eta_v_printed=raw[9],
        )
        rows.append(d)
    return rows


def write(rows: list[dict], path: Path = OUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS + EXTRA_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    rows = parse()
    assert len(rows) == 16, len(rows)
    a, b = rows[0], rows[-1]
    assert (a["Te_C_printed"], a["E_kW_printed"], a["Pc_kPa_printed"], a["eta_v_printed"]) == (
        "-25",
        "3.817",
        "1540.66",
        "0.849",
    ), a
    assert (b["Tc_C_printed"], b["m_inj_g_s_printed"], b["P_int_kPa_printed"], b["eta_c_printed"]) == (
        "67",
        "37.98",
        "1409.89",
        "0.577",
    ), b
    write(rows)
    print(f"{len(rows)} points -> {OUT}")


if __name__ == "__main__":
    main()
