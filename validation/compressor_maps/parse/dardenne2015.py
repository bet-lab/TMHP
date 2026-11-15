"""Dardenne, Molinaroli, Joppolo et al. (2015), Int. J. Refrigeration 54, "Semi-empirical modelling of a
variable speed scroll compressor with vapour injection" (author version, POLIMI repository).

Appendix A, Table A.1 (pdf page 11-12 of the 12-page author version): 63 calorimeter tests of a hermetic,
variable-speed scroll compressor **with vapour injection**, R410A / POE oil.

What the paper states (and what it does not)
--------------------------------------------
* Swept volume "equal to 5.3 m3 h-1 at 50 Hz" (Sec. 3).  Taken literally as V_disp = 5.3e6/3600/50 =
  29.44 cm3/rev with N = f; the paper's reference mass flow m_REF = 0.045 kg/s = swept volume x vapour
  density at 273.15 K (R410A dew, 798 kPa, ~30.6 kg/m3) reproduces 5.3 m3/h.  The identified swept volume is
  said to be 1.5 % above the geometrical one.  If the 50 Hz were the 48.33 Hz shaft speed (see below) V_disp
  would be 3.5 % larger -- the paper does not say; the literal reading is used.
* ``f`` is the "rotational frequency [Hz]" (nomenclature); the model reference is "the grid frequency ...
  f_ROT,REF = 50 Hz".  The five test levels 40 / 48.33 / 75 / 100 / 116.67 Hz equal 2400 / 2900 / 4500 /
  6000 / 7000 rpm, i.e. 48.33 and 116.67 are shaft speeds in rev/s (2900 rpm is the 50 Hz two-pole induction
  speed with slip, as in Cuevas & Lebrun 2009).  Pole pairs are not stated.  N_rps = f as printed.
* Test standard: calorimetric room "designed to comply with European Standard EN 13771 (2003)".  Ambient
  temperature 308.15 K, suction superheat 9.8-10.3 K, injection superheat 4.4-10.1 K (Table 2).
* Power W_COMP: "Power meter, +-1 %"; the paper does not say where it is measured (compressor terminals or
  inverter input).  ``P_includes_inverter`` is therefore left blank.
* Mass-flow unit: the header of Table A.1 prints "[kg s-1]" for m_SUC and m_INJ, but the values (16.7-97.3
  and 1.6-31.2) cannot be kg/s: the paper's own reference flow is m_REF = 0.045 kg/s and the swept volume is
  5.3 m3/h.  Read as g/s they give volumetric efficiencies 0.84-0.98 (R410A density from CoolProp, N = f).  The CSV holds kg/s
  (= printed / 1000); the printed number and the header unit are kept in ``m_*_printed`` /
  ``m_unit_printed_header``.  The page was rendered and read as an image: the header really prints kg s-1.
* Temperatures are printed in K (T_SUC 258.05-298.15 K); the CSV stores degC = K - 273.15, rounded to 0.01.

Table A.1 columns: Run, f [Hz], pSUC [kPa], TSUC [K], pINJ [kPa], TINJ [K], pDIS [kPa], m_SUC, m_INJ,
W_COMP [W], TDIS [K].  Injection quantities go to the ``p_inj_Pa``, ``T_inj_C``, ``m_inj_kg_s`` columns;
``injection_compressor`` is True on every row (the suction flow alone does not close the compressor energy
balance, and a single-stage isentropic efficiency is not defined without a convention).

Run ``python -m validation.compressor_maps.parse.dardenne2015`` to write the CSV.
"""

from __future__ import annotations

import csv
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

from validation.compressor_maps.schema import COLUMNS, DATA_DIR, EVIDENCE_DIR, CompressorPoint

REL = "compressor_maps/dardenne2015/dardenne2015_polimi.pdf"
PDF = EVIDENCE_DIR.parent / REL
OUT = DATA_DIR / "points_dardenne2015.csv"
SOURCE_ID = "dardenne_2015_ijr"
V_DISP_CM3 = 5.3e6 / 3600.0 / 50.0  # 5.3 m3/h at 50 Hz -> 29.444 cm3/rev
N_RATED = 50.0

EXTRA_COLUMNS = [
    "injection_compressor",
    "run",
    "p_inj_Pa",
    "T_inj_C",
    "m_inj_kg_s",
    "f_Hz_printed",
    "p_suc_kPa_printed",
    "T_suc_K_printed",
    "p_inj_kPa_printed",
    "T_inj_K_printed",
    "p_dis_kPa_printed",
    "m_suc_printed",
    "m_inj_printed",
    "m_unit_printed_header",
    "W_comp_W_printed",
    "T_dis_K_printed",
]

NUM = r"(\d+(?:\.\d+)?)"
ROW = re.compile(r"^\s*(\d{1,2})\s+" + r"\s+".join([NUM] * 10) + r"\s*$")


def parse() -> list[dict]:
    txt = subprocess.run(["pdftotext", "-layout", str(PDF), "-"], capture_output=True, text=True, timeout=60).stdout
    start = txt.index("Table A.1 Experimental results")
    end = txt.index("references", start)
    rows: list[dict] = []
    for line in txt[start:end].splitlines():
        m = ROW.match(line)
        if not m:
            continue
        run = int(m.group(1))
        raw = m.groups()[1:]
        f, psuc, tsuc, pinj, tinj, pdis, msuc, minj, w, tdis = (float(x) for x in raw)
        pt = CompressorPoint(
            source_id=SOURCE_ID,
            manufacturer="(not stated; Emerson test bench)",
            model="variable-speed scroll with vapour injection, 5.3 m3/h at 50 Hz",
            comp_type="scroll",
            refrigerant="R410A",
            V_disp_cm3=V_DISP_CM3,
            N_rated_rps=N_RATED,
            N_rated_basis="nominal_statement",
            N_rps=f,
            Q_evap_W=None,
            P_el_W=w,
            p_suc_Pa=psuc * 1e3,
            p_dis_Pa=pdis * 1e3,
            sat_convention="pressure",
            sh_convention="measured",
            T_suc_C=round(tsuc - 273.15, 2),
            m_dot_kg_s=round(msuc / 1000.0, 8),
            T_dis_C=round(tdis - 273.15, 2),
            P_includes_inverter=None,
            method="pdf_text",
            doc_path=REL,
            page="pdf 11-12 (Appendix, Table A.1)",
            table_id="Table A.1",
            note="vapour-injection scroll; m printed under kg/s header, read as g/s (see module docstring); "
            "power measurement point not stated; EN 13771 calorimeter",
        )
        d = asdict(pt)
        d.pop("extra", None)
        d.update(
            injection_compressor=True,
            run=run,
            p_inj_Pa=pinj * 1e3,
            T_inj_C=round(tinj - 273.15, 2),
            m_inj_kg_s=round(minj / 1000.0, 8),
            f_Hz_printed=raw[0],
            p_suc_kPa_printed=raw[1],
            T_suc_K_printed=raw[2],
            p_inj_kPa_printed=raw[3],
            T_inj_K_printed=raw[4],
            p_dis_kPa_printed=raw[5],
            m_suc_printed=raw[6],
            m_inj_printed=raw[7],
            m_unit_printed_header="kg s-1 (values are g/s)",
            W_comp_W_printed=raw[8],
            T_dis_K_printed=raw[9],
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
    assert [r["run"] for r in rows] == list(range(1, 64)), [r["run"] for r in rows]
    # verbatim guards: first and last printed rows
    a, b = rows[0], rows[-1]
    assert (a["f_Hz_printed"], a["p_suc_kPa_printed"], a["m_suc_printed"], a["W_comp_W_printed"]) == (
        "40.00",
        "460",
        "17.31",
        "1616.96",
    ), a
    assert (b["f_Hz_printed"], b["p_dis_kPa_printed"], b["m_inj_printed"], b["T_dis_K_printed"]) == (
        "116.67",
        "3830",
        "24.28",
        "384.40",
    ), b
    write(rows)
    print(f"{len(rows)} points -> {OUT}")


if __name__ == "__main__":
    main()
