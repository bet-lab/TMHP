"""Highly (Shanghai Hitachi) rotary compressor catalogue 2024 -- R290 inverter line.

Source: ``validation/evidence/compressor_maps/highly/highly_catalogue_2024.pdf`` (p. 2-3),
"HIGHLY R290 INVERTER COMPRESSOR - Cooling" table: displacement [cc/rev], cooling
capacity [W], COP (cooling), rated speed 3600 rpm, testing condition ASHRAE/T.
ASHRAE/T rating point (ASHRAE 23 / AHRI 540 "T" condition): evaporating 7.2 degC,
condensing 54.4 degC, return gas 35 degC (27.8 K superheat), liquid 46.1 degC
(8.3 K subcooling).  Power = capacity / COP.  One point per compressor at n* = 1
(rated speed), so these rows anchor the *level* of eta_vol and eta_oi for
single/twin rotaries; they carry no speed or PR information.
The heating rows of the same table are condenser-side capacities and are not used.
"""

from __future__ import annotations

import re
import subprocess

from validation.compressor_maps.schema import DATA_DIR, EVIDENCE_DIR, CompressorPoint, write_points

PDF = EVIDENCE_DIR / "highly" / "highly_catalogue_2024.pdf"
SOURCE_ID = "highly_catalogue_2024"
ROW = re.compile(
    r"^\s*(SD|TD|TH|TE)\s+(WHP\w+)\s+(Single|Twin) cylinder\s+([\d,]+)\s+(\d+)\s+(\d+)\s+([\d,]+)\s+(\d+)\s+ASHRAE/T"
)


def parse() -> list[CompressorPoint]:
    txt = subprocess.run(
        ["pdftotext", "-layout", "-f", "1", "-l", "3", str(PDF), "-"], capture_output=True, text=True, timeout=60
    ).stdout
    pts: list[CompressorPoint] = []
    in_cooling = False
    for line in txt.splitlines():
        if "INVERTER COMPRESSOR - Cooling" in line:
            in_cooling = True
        elif "INVERTER COMPRESSOR - Heating" in line:
            in_cooling = False
        m = ROW.match(line)
        if not m or not in_cooling:
            continue
        series, model, cyl, disp, q_w, _btuh, cop, rpm = m.groups()
        disp_f, q, cop_f, rpm_f = float(disp.replace(",", ".")), float(q_w), float(cop.replace(",", ".")), float(rpm)
        pts.append(
            CompressorPoint(
                source_id=SOURCE_ID,
                manufacturer="Highly",
                model=model,
                comp_type="rotary_single" if cyl == "Single" else "rotary_twin",
                refrigerant="R290",
                V_disp_cm3=disp_f,
                N_rated_rps=rpm_f / 60.0,
                N_rated_basis="nameplate",
                N_rps=rpm_f / 60.0,
                Q_evap_W=q,
                P_el_W=q / cop_f,
                T_evap_C=7.2,
                T_cond_C=54.4,
                sat_convention="dew_dew",
                sh_convention="ashrae23",
                dT_sh_K=35.0 - 7.2,
                dT_sc_K=54.4 - 46.1,
                P_includes_inverter=None,
                method="pdf_text",
                doc_path=str(PDF.relative_to(EVIDENCE_DIR.parent)),
                page="2-3",
                table_id="R290 inverter cooling",
                note=f"series {series}",
            )
        )
    return pts


def main() -> None:
    pts = parse()
    assert len(pts) == 8, len(pts)
    a = pts[0]
    assert (a.model, a.V_disp_cm3, a.Q_evap_W, round(a.P_el_W)) == (
        "WHP03300PRKQA6JT6",
        14.0,
        2680.0,
        round(2680 / 3.78),
    ), a
    out = DATA_DIR / "points_highly2024.csv"
    write_points(pts, out)
    print(f"{len(pts)} points -> {out}")


if __name__ == "__main__":
    main()
