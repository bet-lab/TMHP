"""Ossorio-Santiago & Navarro-Peris (2019), "Efficiency characterization of a variable speed compressor",
XI National / II International Engineering Thermodynamics Congress (CNIT 2019), pp. 265-272
(UPV handle 10251/180707; author copy).

Table 1 (printed page 270, pdf page 8): 18 calorimeter tests of one R290 rotary compressor, model TPB306F
("for medium temperature using POE oil", "BLDC 1300W electric motor with 2 pole pairs that can be operated
from 30 to 120rps"), at one condition (Te -7 degC, Tc 50 degC, superheat ~10 K) over 30-120 rps, with two
inverters: APY F003i-2PHB (13 speeds) and AKO PF1 (5 speeds).

Stated by the paper (Sec. 2)
* Tests per EN 13771-1 on a calorimeter bench; primary mass-flow method is a Coriolis meter in the liquid
  line after the condenser (Micro Motion CMF025M, +-0.025 g/s), confirmed by a secondary calorimeter;
  discrepancy < 2 %.
* "10 and 4 degrees of superheat and subcooling respectively" -> dT_sc = 4 K (not printed per row).
* Power points: E1 "at the source line at 50Hz and 230V" (grid side, i.e. *before* the inverter);
  E2 "at the line connecting the inverter with the compressor"; E3 = m_coriolis (h2s - h1) (isentropic).
  Table 1 prints E1 only -> ``P_el_W`` is the grid-side power and ``P_includes_inverter`` is True.
* Efficiencies: eta_compr = m_coriolis (h2s - h1) / E2  (Eq. 2); "the efficiency of the inverter is defined
  as eta_inverter = E2/E1 and the overall efficiency eta_inverter+compr = m_coriolis (h2s - h1)/E1".
  eta_volum (printed as "ηvolum") is NOT defined in the text; the reading m/(rho_suc V_d N) is consistent
  with every row (see below).
* Cylinder count, displacement and rated speed are not stated.

Displacement: because the paper gives none, ``V_disp_cm3`` is inferred from the printed volumetric
efficiency, V = m / (rho_suc * eta_volum * N), with rho_suc from CoolProp (R290, dew pressure at the printed
Tevap, suction temperature Tevap + SH).  The 18 values agree within 1 % (verification log), so the
inference is stable, but it is *inferred*, not printed: ``V_disp_basis`` says so and ``N_rated_basis`` is
"unresolved" (the 60 rps rated speed is a placeholder, only n* = N/60 depends on it).  ``comp_type``
"rotary_single" is likewise a placeholder; ``cylinders_stated`` = "not stated".

Printed efficiencies (percent) are kept verbatim in ``eta_*_pct_printed``.  The prose after Table 1
says the maximum inverter efficiency is 94.8 % (AKO) and 95.8 % (APY); the table has 95.92 % for AKO and
94.87 % for APY, i.e. the prose swaps the two names (table values are transcribed).

Run ``python -m validation.compressor_maps.parse.ossorio2019`` to write the CSV.
"""

from __future__ import annotations

import csv
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

import numpy as np
from CoolProp.CoolProp import PropsSI

from validation.compressor_maps.schema import COLUMNS, DATA_DIR, EVIDENCE_DIR, CompressorPoint

REL = "compressor_maps/ossorio2019/ossorio2019_conf.pdf"
PDF = EVIDENCE_DIR.parent / REL
OUT = DATA_DIR / "points_ossorio2019_rotary.csv"
SOURCE_ID = "ossorio_2019_cnit"
N_RATED_PLACEHOLDER = 60.0
DT_SC_K = 4.0

EXTRA_COLUMNS = [
    "inverter",
    "V_disp_basis",
    "cylinders_stated",
    "T_evap_C_printed",
    "T_cond_C_printed",
    "SH_K_printed",
    "speed_rps_printed",
    "m_coriolis_g_s_printed",
    "E1_W_printed",
    "eta_volum_pct_printed",
    "eta_compr_pct_printed",
    "eta_invert_pct_printed",
    "eta_invert_compr_pct_printed",
]

ROW = re.compile(r"^\s*(APY|AKO)\s+" + r"\s+".join([r"(-?\d+(?:\.\d+)?)"] * 10) + r"\s*$")


def _read() -> list[tuple[str, list[str]]]:
    txt = subprocess.run(["pdftotext", "-layout", str(PDF), "-"], capture_output=True, text=True, timeout=60).stdout
    out = []
    for line in txt.splitlines():
        m = ROW.match(line)
        if m:
            out.append((m.group(1), list(m.groups()[1:])))
    return out


def inferred_displacement_cm3(raw: list[list[str]]) -> tuple[float, np.ndarray]:
    """V = m/(rho_suc eta_vol N) per row and its mean; the paper prints no displacement."""
    vs = []
    for r in raw:
        te, _tc, sh, n, m, _e, ev = (float(x) for x in r[:7])
        ps = PropsSI("P", "T", te + 273.15, "Q", 1, "R290")
        rho = PropsSI("D", "T", te + sh + 273.15, "P", ps, "R290")
        vs.append(m / 1000.0 / (rho * ev / 100.0 * n) * 1e6)
    v = np.array(vs)
    return float(v.mean()), v


def parse() -> list[dict]:
    parsed = _read()
    v_mean, _ = inferred_displacement_cm3([r for _, r in parsed])
    rows: list[dict] = []
    for inv, r in parsed:
        te, tc, sh, n, m, e1 = (float(x) for x in r[:6])
        pt = CompressorPoint(
            source_id=SOURCE_ID,
            manufacturer="(not stated)",
            model="TPB306F",
            comp_type="rotary_single",
            refrigerant="R290",
            V_disp_cm3=v_mean,
            N_rated_rps=N_RATED_PLACEHOLDER,
            N_rated_basis="unresolved",
            N_rps=n,
            Q_evap_W=None,
            P_el_W=e1,
            T_evap_C=te,
            T_cond_C=tc,
            sat_convention="dew_dew",
            sh_convention="custom",
            dT_sh_K=sh,
            dT_sc_K=DT_SC_K,
            m_dot_kg_s=round(m / 1000.0, 8),
            P_includes_inverter=True,
            speed_variant=inv,
            method="pdf_text",
            doc_path=REL,
            page="pdf 8 (printed 270)",
            table_id="Table 1",
            note="E1 = grid-side power before the inverter (Yokogawa WT1030); V_disp inferred from printed "
            "eta_volum (paper gives none); dT_sc = 4 K from the text, not per row; pure fluid so dew = bubble",
        )
        d = asdict(pt)
        d.pop("extra", None)
        d.update(
            inverter=inv,
            V_disp_basis="inferred from printed eta_volum, Coriolis flow and CoolProp R290 density (not printed)",
            cylinders_stated="not stated",
            T_evap_C_printed=r[0],
            T_cond_C_printed=r[1],
            SH_K_printed=r[2],
            speed_rps_printed=r[3],
            m_coriolis_g_s_printed=r[4],
            E1_W_printed=r[5],
            eta_volum_pct_printed=r[6],
            eta_compr_pct_printed=r[7],
            eta_invert_pct_printed=r[8],
            eta_invert_compr_pct_printed=r[9],
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
    assert len(rows) == 18 and sum(r["inverter"] == "APY" for r in rows) == 13, len(rows)
    a, b = rows[0], rows[-1]
    assert (a["speed_rps_printed"], a["E1_W_printed"], a["eta_invert_pct_printed"]) == ("30.0", "814", "89.09"), a
    assert (b["speed_rps_printed"], b["m_coriolis_g_s_printed"], b["eta_invert_compr_pct_printed"]) == (
        "117.6",
        "26.71",
        "56.87",
    ), b
    write(rows)
    print(f"{len(rows)} points -> {OUT}")


if __name__ == "__main__":
    main()
