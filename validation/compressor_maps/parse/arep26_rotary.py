"""AHRI Low-GWP AREP Test Reports #26 and #40: Shanghai Hitachi hermetic rotary compressor (R-410A design).

The only public rotary-compressor calorimeter data for modern refrigerants in the AREP series.

* **Report #26** (R-32 in an R-410A rotary, 13 Oct 2013): nominal displacement 28.0 cc, constant-speed motor
  2.5 hp, single phase 220 V 50 Hz, POE oil; secondary-refrigerant calorimeter per ASHRAE 23-2010.  The compressor
  was modified with a liquid-injection port to control the R-32 discharge temperature; the same modified compressor
  ran R-32 and R-410A.
  - Table 4 (appendix A, pdf p. 7): the four standard rating points A-D x {R-410A, R-32}, *measured*.
  - Tables 5 / 6 (pdf pp. 7 / 9): the ten-coefficient polynomials (power, capacity, COP, flow) of R-410A / R-32,
    ``X = C1 + C2 S + C3 D + C4 S^2 + C5 S D + C6 D^2 + C7 S^3 + C8 D S^2 + C9 S D^2 + C10 D^3`` with S, D the
    suction / discharge *dew-point* temperatures in degC.  They are stored verbatim in
    ``arep26_coefficients.json``.  The report prints no individual map test results.
  - Table 3 (pdf pp. 3-4): the 26 reporting conditions; the report marks the 16 run with R-32 with ``*``
    (R-410A data of conditions 1-24 went into its polynomial; nos. 25 and 26 are R-32 only).  Polynomial rows are
    generated **only** at those tested conditions: R-410A at nos. 1-24, R-32 at the 16 starred nos.
    These rows are model values (``data_kind`` = ``polynomial evaluated at tested conditions``), not measurements,
    and carry the three-significant-digit rounding of the printed coefficients.
* **Report #40** (DR-5 in an R-410A rotary, 1 May 2014): same compressor, Table 3 = standard points A-D x
  {R-410A, DR-5}, no coefficients, no injection.  Its R-410A column is identical to the digit to report #26's
  R-410A column of Table 4 (same test data re-used, not an independent measurement), so only the four DR-5 rows
  are stored.

The reports print neither suction/discharge pressures, nor the rotational speed, nor the number of cylinders.
Speed: constant-speed 50 Hz motor; ``N_rps`` is set to 50.0 (synchronous speed of a 2-pole machine, an upper
bound) with ``N_rated_basis = unresolved`` and a flag.  ``comp_type = rotary_single`` is the schema's default
rotary label, not a statement of the report.

Run ``python -m validation.compressor_maps.parse.arep26_rotary`` to write the CSV and the JSON.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
from dataclasses import asdict
from pathlib import Path

from validation.compressor_maps.parse.arep import ARDIR
from validation.compressor_maps.schema import COLUMNS, DATA_DIR, CompressorPoint

OUT = DATA_DIR / "points_arep26_rotary.csv"
OUT_JSON = DATA_DIR / "arep26_coefficients.json"

NUM = r"-?\d+(?:\.\d+)?"
TERMS = ["1", "S", "D", "S^2", "S*D", "D^2", "S^3", "D*S^2", "S*D^2", "D^3"]
QUANTITIES = ("power", "capacity", "cop", "flow")  # column order of Tables 5 and 6
TABLE_LABELS = {
    "evap": r"evaporating temperature",
    "cond": r"condensing temperature",
    "tdis": r"discharge temperature",
    "sh": r"applicable superheating",
    "sc": r"applicable subcooling",
    "cap": r"compressor capacity",
    "m": r"refrigerant mass flow rate",
    "amp": r"amperes",
    "power": r"input power",
    "cop": r"COP",
    "ratio": r"COP\s*alt\s*/\s*COP\s*baseline",
}
POINTS = "ABCD"
# standard rating conditions, Table 2 of both reports: return-gas and expansion-valve inlet temperature [degC]
TABLE2 = {
    "A": dict(S=7.2, D=54.4, ev=46.1, ret=18.0),
    "B": dict(S=7.2, D=46.1, ev=37.8, ret=18.0),
    "C": dict(S=7.2, D=37.8, ev=29.5, ret=18.0),
    "D": dict(S=-1.1, D=43.3, ev=35.0, ret=10.0),
}
# Table 3 of report #26: (suction dew point groups) -> (discharge dew point, expansion valve inlet temperature).
# Group boundaries read from the page images (merged cells) and checked against the text layer in ``table3``.
T3_GROUPS = [
    (range(1, 6), 60.0, 51.7),
    (range(6, 11), 55.0, 46.7),
    (range(11, 15), 50.0, 41.7),
    (range(15, 19), 45.0, 36.7),
    (range(19, 23), 40.0, 31.7),
    (range(23, 25), 37.0, 28.7),
    (range(25, 26), 40.0, 31.7),
    (range(26, 27), 50.0, 41.7),
]
T3_SUPERHEAT_K = 11.1  # printed once for nos. 1-24; blank for 25 and 26


def _pages(pdf: Path) -> list[list[str]]:
    out = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, timeout=120, check=True
    ).stdout
    return [p.splitlines() for p in out.split("\f")]


# ------------------------------------------------------------------------------------ tables 5 / 6


def coefficients(pages: list[list[str]]) -> dict:
    res: dict = {}
    for label, fluid in (("Table5.", "R410A"), ("Table6.", "R32")):
        for pno, lines in enumerate(pages, start=1):
            idx = next((i for i, ln in enumerate(lines) if ln.strip().startswith(label)), None)
            if idx is None:
                continue
            title = lines[idx].strip()
            hdr = lines[idx + 1].split()
            if [h.lower() for h in hdr[1:]] != ["power", "capacity", "cop", "flow"]:
                raise ValueError(f"{label} header {hdr}")
            rows = []
            for ln in lines[idx + 2 : idx + 12]:
                m = re.match(rf"^\s*C(\d+)\s+((?:{NUM}E[+-]\d+\s*){{4}})$", ln)
                if not m or int(m.group(1)) != len(rows) + 1:
                    raise ValueError(f"{label} coefficient line {ln!r}")
                rows.append(m.group(2).split())
            res[fluid] = dict(
                table=label.rstrip("."),
                title=title,
                pdf_page=pno,
                verbatim={q: [r[k] for r in rows] for k, q in enumerate(QUANTITIES)},
                value={q: [float(r[k]) for r in rows] for k, q in enumerate(QUANTITIES)},
            )
    if set(res) != {"R410A", "R32"}:
        raise ValueError("coefficient tables not found")
    return res


def evaluate(c: list[float], s: float, d: float) -> float:
    return (
        c[0] + c[1] * s + c[2] * d + c[3] * s**2 + c[4] * s * d + c[5] * d**2
        + c[6] * s**3 + c[7] * d * s**2 + c[8] * s * d**2 + c[9] * d**3
    )  # fmt: skip


def write_json(coef: dict, path: Path = OUT_JSON) -> None:
    doc = dict(
        source="AHRI Low-GWP AREP Test Report #26, Compressor Calorimeter Test of Refrigerant R-32 in a R-410A Rotary "
        "Type Compressor (Shanghai Hitachi Electrical Appliances Co., 13 Oct 2013)",
        file="compressor_maps/arep/AREP_Rpt026.pdf",
        compressor="Shanghai Hitachi hermetic rotary, R-410A design, nominal displacement 28.0 cc, constant speed "
        "50 Hz, 2.5 hp, with a liquid-injection port added (table header: compressor model ASH280*)",
        note="Coefficients transcribed verbatim (three significant digits as printed). Defining sentences, pdf p. 6: "
        "'The equation is in the form of: X=C1+C2*(S) +C3*D+C4*(S2) +C5*(S*D) +C6*(D2) +C7*(S3) +C8*(D*S2) +C9*(S*D2) "
        "+C10*(D3). Where, C= Equation Coefficient, represents compressor performance; S= Suction dew point "
        "temperature, degC; D=Discharge dew point temperature, degC; X=Compressor performance (mass flow rate, "
        "capacity, power and COP)'. Tables 5 and 6 print no units: the units below (W, W, W/W, kg/h) are those of "
        "Table 4 of the same report and are confirmed by evaluating the polynomials at the four standard points "
        "(see the verification log). Table 5 (R410A) is fitted to the test results of conditions 1-24 of Table 3, "
        "Table 6 (R32) to the 16 starred conditions of Table 3 (report p. 4). Valid range per report: evaporating "
        "-8 to 13 degC, condensing 37 to 60 degC (Table 3).",
        term_order=TERMS,
        term_order_note="index k of the coefficient list multiplies TERMS[k]: C1..C10",
        variables={"S": "suction dew-point temperature, degC", "D": "discharge dew-point temperature, degC"},
        units={"power": "W", "capacity": "W", "cop": "W/W", "flow": "kg/h"},
        quantity_order_in_tables=list(QUANTITIES),
        fluids={
            f: dict(table=v["table"], title=v["title"], pdf_page=v["pdf_page"], coefficients_verbatim=v["verbatim"],
                    coefficients=v["value"])
            for f, v in coef.items()
        },
    )  # fmt: skip
    with path.open("w") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# ------------------------------------------------------------------------------------ table 3 (#26)


def table3(pages: list[list[str]]) -> list[dict]:
    """The 26 reporting conditions of report #26 with the ``*`` marks (R-32 tested) from the text layer."""
    found: dict[int, tuple[bool, float, int]] = {}
    text_by_page: dict[int, str] = {}
    for pno, lines in enumerate(pages, start=1):
        if not any("Table3." in ln for ln in lines):
            continue
        text_by_page[pno] = "\n".join(lines)
        for ln in lines:
            m = re.match(rf"^\s*(\*?)\s*(\d+)\.?\s+({NUM})\s*(?:\s{{2,}}.*)?$", ln)
            if m and 1 <= int(m.group(2)) <= 26:
                found[int(m.group(2))] = (bool(m.group(1)), float(m.group(3)), pno)
    if sorted(found) != list(range(1, 27)):
        raise ValueError(f"report 26 Table 3: conditions found {sorted(found)}")
    alltext = "\n".join(text_by_page.values())
    out = []
    for no in range(1, 27):
        star, s, pno = found[no]
        grp = next(g for g in T3_GROUPS if no in g[0])
        d, ev = grp[1], grp[2]
        for v in (d, ev):  # the merged-cell values must exist in the text layer
            if (
                re.search(
                    rf"(?<![\d.]){re.escape(str(v).rstrip('0').rstrip('.') if v == int(v) else str(v))}(?![\d.])",
                    alltext,
                )
                is None
            ):
                raise ValueError(f"report 26 Table 3: value {v} of no. {no} not in the text")
        out.append(dict(no=no, star=star, S=s, D=d, ev=ev, sh=T3_SUPERHEAT_K if no <= 24 else None, pdf_page=pno))
    if sum(r["star"] for r in out) != 16 or any(r["star"] for r in out if r["no"] in (1, 2, 3, 4, 5, 6, 7, 11, 14, 21)):
        raise ValueError("report 26 Table 3: star marks differ from the expected 16")
    return out


# ------------------------------------------------------------------------------------ table 4 / 3


def tabular(pages: list[list[str]], anchor: str) -> tuple[int, dict[str, list[float]]]:
    """Standard-point table: label rows with merged cells; returns ``(pdf_page, label -> numbers)``."""
    for pno, lines in enumerate(pages, start=1):
        if not any(anchor in ln for ln in lines) or not any("test conditions" in ln for ln in lines):
            continue
        res: dict[str, list[float]] = {}
        for key, lab in TABLE_LABELS.items():
            for ln in lines:
                m = re.match(rf"^\s*{lab}\s+(?:℃|W/W|W|kg/h|A)?\s*((?:{NUM}\s*)+)$", ln)
                if m:
                    res[key] = [float(x) for x in re.findall(NUM, m.group(1))]
                    break
        if set(res) != set(TABLE_LABELS):
            raise ValueError(f"{anchor}: missing rows {set(TABLE_LABELS) - set(res)}")
        return pno, res
    raise ValueError(f"{anchor}: table not found")


def expand(res: dict[str, list[float]]) -> dict[str, list[tuple[float, float]]]:
    """Per condition A-D the (baseline, alternative) values, undoing the merged cells."""
    out: dict[str, list[tuple[float, float]]] = {}
    for key, v in res.items():
        if key == "evap":
            if len(v) != 5:
                raise ValueError(f"evaporating temperature cells: {v}")
            out[key] = [(v[0], v[0]), (v[1], v[1]), (v[2], v[3]), (v[4], v[4])]
        elif key == "cond":
            if len(v) != 4:
                raise ValueError(f"condensing temperature cells: {v}")
            out[key] = [(x, x) for x in v]
        else:
            if len(v) != 8:
                raise ValueError(f"{key}: {len(v)} cells")
            out[key] = [(v[2 * i], v[2 * i + 1]) for i in range(4)]
    return out


EXTRA_COLUMNS = [
    "report",
    "data_kind",
    "printed_flag",
    "pdf_page",
    "refrigerant_report_name",
    "composition_note",
    "condition",
    "table3_no",
    "r32_tested_star",
    "injection",
    "I_comp_A",
    "T_evap_C_printed",
    "T_cond_C_printed",
    "return_gas_C_Table2",
    "expansion_valve_inlet_C",
    "superheat_K_printed",
    "subcooling_K_printed",
    "T_dis_C_printed",
    "C_W_printed",
    "M_kgh_printed",
    "P_W_printed",
    "COP_printed",
    "COP_ratio_to_baseline_printed",
    "COP_polynomial",
]

COMMON = dict(
    manufacturer="Shanghai Hitachi",
    model="Shanghai Hitachi hermetic rotary 28.0 cc (R-410A design, ASH280*)",
    comp_type="rotary_single",
    V_disp_cm3=28.0,
    N_rated_rps=50.0,
    N_rated_basis="unresolved",
    N_rps=50.0,
    sat_convention="dew_dew",
    sh_convention="custom",
    Q_cond_W=None,
    P_includes_inverter=False,
    method="pdf_text",
)
SPEED_FLAG = "rotational speed and cylinder count not printed; N_rps = 50 (synchronous speed at 50 Hz, upper bound)"


def _point(**kw: object) -> dict:
    pt = CompressorPoint(**{**COMMON, **kw})  # type: ignore[arg-type]
    d = asdict(pt)
    d.pop("extra", None)
    return d


def measured_rows(report: str, pdf: str, source_id: str, anchor: str, alt: tuple[str, str, str, str]) -> list[dict]:
    """Four standard points of the alternative (and, for #26, of the baseline) as measured rows."""
    pages = _pages(ARDIR / pdf)
    pno, res = tabular(pages, anchor)
    ex = expand(res)
    rows: list[dict] = []
    alt_cp, alt_name, alt_comp, alt_inj = alt
    refs = [("R410A", "R-410A", "R32/R125 50/50 mass%", 0)] if report == "rpt26" else []
    refs.append((alt_cp, alt_name, alt_comp, 1))
    for cp, name, comp, side in refs:
        for i, cond in enumerate(POINTS):
            t2 = TABLE2[cond]
            g = {k: v[i][side] for k, v in ex.items()}
            inj = "not stated"
            if report == "rpt26" and cp == "R32" and cond == "A":
                inj = "liquid injection 8 L/h used; injected flow not in capacity (ASHRAE 23-2010)"
            elif report == "rpt40":
                inj = "none (no injection in report #40)"
            flags = [SPEED_FLAG]
            if abs(g["evap"] - t2["S"]) > 0.05:
                flags.append(
                    f"printed evaporating temperature {g['evap']} differs from the nominal condition {t2['S']}"
                )
            d = _point(
                source_id=source_id,
                refrigerant=cp,
                Q_evap_W=g["cap"],
                P_el_W=g["power"],
                T_evap_C=g["evap"],
                T_cond_C=g["cond"],
                dT_sh_K=g["sh"],
                T_suc_C=None,
                dT_sc_K=g["sc"],
                m_dot_kg_s=g["m"] / 3600.0,
                T_dis_C=g["tdis"],
                doc_path=f"compressor_maps/arep/{pdf}",
                page=f"pdf {pno}",
                table_id=f"{report} {'Table 4' if report == 'rpt26' else 'Table 3'} {cond}",
                note="measured at a standard rating point (secondary-refrigerant calorimeter, ASHRAE 23-2010); "
                "no pressures printed; capacity = refrigerant mass flow x enthalpy change; power = compressor input; "
                "'applicable' superheat/subcooling as printed; no cylinder count or speed printed",
            )
            d.update(
                report=report,
                data_kind="measured",
                printed_flag="; ".join(flags),
                pdf_page=pno,
                refrigerant_report_name=name,
                composition_note=comp,
                condition=cond,
                table3_no="",
                r32_tested_star="",
                injection=inj,
                I_comp_A=g["amp"],
                T_evap_C_printed=g["evap"],
                T_cond_C_printed=g["cond"],
                return_gas_C_Table2=t2["ret"],
                expansion_valve_inlet_C=t2["ev"],
                superheat_K_printed=g["sh"],
                subcooling_K_printed=g["sc"],
                T_dis_C_printed=g["tdis"],
                C_W_printed=g["cap"],
                M_kgh_printed=g["m"],
                P_W_printed=g["power"],
                COP_printed=g["cop"],
                COP_ratio_to_baseline_printed=g["ratio"],
                COP_polynomial="",
            )
            rows.append(d)
    return rows


def polynomial_rows(coef: dict, conds: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for fluid, name, comp in (("R410A", "R-410A", "R32/R125 50/50 mass%"), ("R32", "R-32", "R32 100 mass%")):
        v = coef[fluid]["value"]
        for c in conds:
            if fluid == "R410A" and c["no"] > 24:
                continue  # nos. 25 and 26 are R-32 conditions only (report p. 4)
            if fluid == "R32" and not c["star"]:
                continue
            cap = evaluate(v["capacity"], c["S"], c["D"])
            pwr = evaluate(v["power"], c["S"], c["D"])
            flow = evaluate(v["flow"], c["S"], c["D"])
            cop = evaluate(v["cop"], c["S"], c["D"])
            sc = round(c["D"] - c["ev"], 1)
            flags = [SPEED_FLAG, "subcooling = discharge dew point minus the printed expansion-valve inlet temperature"]
            if c["sh"] is None:
                flags.append("no superheat printed for Table 3 nos. 25 and 26; dT_sh_K left blank")
            d = _point(
                source_id="arep_rpt26_hitachi2013",
                refrigerant=fluid,
                Q_evap_W=cap,
                P_el_W=pwr,
                T_evap_C=c["S"],
                T_cond_C=c["D"],
                dT_sh_K=c["sh"],
                T_suc_C=None,
                dT_sc_K=sc,
                m_dot_kg_s=flow / 3600.0,
                T_dis_C=None,
                doc_path="compressor_maps/arep/AREP_Rpt026.pdf",
                page=f"pdf {coef[fluid]['pdf_page']} (coefficients), pdf {c['pdf_page']} (condition)",
                table_id=f"rpt26 {coef[fluid]['table']} at Table 3 no. {c['no']}",
                note="polynomial evaluated at a tested condition: ten-coefficient fit of the report (3 significant "
                "digits as printed), not a measurement; units W, W, kg/h as in Table 4; injection use at map points "
                "not stated",
            )
            d.update(
                report="rpt26",
                data_kind="polynomial evaluated at tested conditions",
                printed_flag="; ".join(flags),
                pdf_page=c["pdf_page"],
                refrigerant_report_name=name,
                composition_note=comp,
                condition="",
                table3_no=c["no"],
                r32_tested_star="*" if c["star"] else "",
                injection="not stated (modified compressor, injection 'when necessary')",
                I_comp_A="",
                T_evap_C_printed=c["S"],
                T_cond_C_printed=c["D"],
                return_gas_C_Table2="",
                expansion_valve_inlet_C=c["ev"],
                superheat_K_printed="" if c["sh"] is None else c["sh"],
                subcooling_K_printed="",
                T_dis_C_printed="",
                C_W_printed="",
                M_kgh_printed="",
                P_W_printed="",
                COP_printed="",
                COP_ratio_to_baseline_printed="",
                COP_polynomial=cop,
            )
            rows.append(d)
    return rows


def parse_all() -> tuple[list[dict], dict, list[dict]]:
    pages26 = _pages(ARDIR / "AREP_Rpt026.pdf")
    coef = coefficients(pages26)
    conds = table3(pages26)
    rows = measured_rows(
        "rpt26", "AREP_Rpt026.pdf", "arep_rpt26_hitachi2013", "Table4.", ("R32", "R-32", "R32 100 mass%", "")
    )
    rows += polynomial_rows(coef, conds)
    rows += [
        r
        for r in measured_rows(
            "rpt40",
            "AREP_Rpt040.pdf",
            "arep_rpt40_hitachi2014",
            "Table3.",
            ("R32/R1234yf", "DR-5", "R32/R1234yf 72.5/27.5 mass%", ""),
        )
        if r["refrigerant_report_name"] == "DR-5"
    ]
    return rows, coef, conds


def write(rows: list[dict], path: Path = OUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS + EXTRA_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    rows, coef, _ = parse_all()
    write(rows)
    write_json(coef)
    print(f"{len(rows)} points -> {OUT}; coefficients -> {OUT_JSON}")


if __name__ == "__main__":
    main()
