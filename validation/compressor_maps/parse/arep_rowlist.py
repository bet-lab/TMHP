"""Emerson / Danfoss AHRI Low-GWP AREP compressor-calorimeter *row-list* reports -> point rows.

One line of the results table is one measured test point (unlike the ORNL matrix reports parsed by
``arep.py``).  Same output conventions as ``arep.py``: SI columns of ``CompressorPoint`` plus ``*_printed``
originals, ``report``, ``pdf_page``, one provenance note; a cell that the report leaves blank stays blank.

Reports handled (all fixed-speed scrolls, drop-in tests at dew-point conditions, AHRI 540 / ASHRAE 23):

====== ============================== ============================ ======================= ====
key    compressor                     displacement / speed         refrigerant (measured)  rows
====== ============================== ============================ ======================= ====
rpt24  Copeland ZP31K5E-PFV           1.8 in3/rev, 3500 rpm, 60 Hz DR-5                    22
rpt38  Copeland ZP31K5E-PFV           1.8 in3/rev                  L-41b                   30
rpt39  Copeland ZP31K5E-PFV           1.8 in3/rev                  R32 (pure)              23
rpt58  Copeland ZP31K5E-PFV           1.8 in3/rev (gas-cycle stand) DR-5A (R454B)           29
rpt65  Copeland ZP122KCE-TFD          6.853 in3/rev, 2900 rpm 50 Hz R447A                  23
rpt66  Danfoss SH161A4                not printed, 2900 rpm 50 Hz  HPR2A                   28
rpt34  Copeland ZF18K4E-TFD (R404A)   5.983 in3/rev, 3500 rpm      DR-7                    19
rpt36  Copeland ZF18K4E-TFD (R404A)   5.983 in3/rev                L-40                    (see log)
====== ============================== ============================ ======================= ====

Every one of these reports states that the **R-410A / R-404A baseline is "published nominal rating data"** and
prints only the ratio COP_alt/COP_base for it, never the baseline's own test lines, so no baseline rows exist
here (nothing is back-computed from the ratio).

Join of two printed tables: *Table 1* (test points: absolute suction/discharge pressure, entering gas
temperature, discharge temperature, measured rpm) and *Table 3* (results: capacity, mass flow, current, power,
EER, COP).  Rows are paired by position and every pair is checked (evaporating and condensing dew
temperature equal within printed rounding); a mismatch raises.  #66 prints Table A1 (SI) and A2 (IP) with
the same rows; A1 is the primary, mass flow is taken from A2 (lb/h, finer relative resolution than the integer
kg/h of A1) and both are kept as ``*_printed``.

Report-internal inconsistencies are kept as printed and flagged in ``printed_flag``: #65 prints its "lb/h" flow
column as kg/h x 0.4536 (conversion applied the wrong way; ``m_dot_kg_s`` uses the kg/s column), #39 row 7 prints a COP
/ EER 2.2 % off its own capacity and power, #58 Table 1 row 7 prints 12.8 psia for a 40 F dew point, and #58 prints
the entering gas temperature equal to the dew temperature although it states 20 F superheat.

Report #58 Table 1 wraps its cells over three text lines; the pieces are re-joined by column position
(see ``_t1_wrapped``) and checked against Table 3.

Report #66 prints no displacement of the SH161A4; its 28 rows go to ``arep66_rowlist_no_displacement.csv`` so that
``schema.read_points`` (which requires ``V_disp_cm3``) still reads ``points_arep_rowlist.csv``.

Run ``python -m validation.compressor_maps.parse.arep_rowlist`` to write the CSVs.
"""

from __future__ import annotations

import csv
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from validation.compressor_maps.parse.arep import ARDIR, CM3_PER_IN3, KGS_PER_LBH, PA_PER_PSI, W_PER_BTUH, f2c
from validation.compressor_maps.schema import COLUMNS, DATA_DIR, CompressorPoint

OUT = DATA_DIR / "points_arep_rowlist.csv"
# rows of reports that print no displacement (#66): ``schema.read_points`` requires V_disp_cm3, so they live apart
OUT_NO_DISP = DATA_DIR / "arep66_rowlist_no_displacement.csv"

NUM = r"(-?\d+(?:\.\d+)?)"
NUMC = r"(-?[\d,]+(?:\.\d+)?)"  # allows thousands separator
PAIR = rf"{NUM}\s*\({NUM}\)"
VOLT = r"\d{3}-\d-\d{2}"


@dataclass(frozen=True)
class Spec:
    key: str
    pdf: str
    source_id: str
    manufacturer: str
    model: str
    V_in3: float | None
    rpm_nominal: float
    rated_basis: str
    layout: str  # "A" (Emerson Sidney), "058", "065", "066"
    n_rows: int
    fluid: str  # CoolProp-style fluid string
    fluid_name: str  # as printed in the report title
    composition: str
    power_meter: str
    refrigeration: bool = False
    flags: dict[int, str] = field(default_factory=dict)  # 1-based Table 3 row -> printed_flag


SPECS = [
    Spec(
        "rpt24", "AREP_Rpt024.pdf", "arep_rpt24_emerson2013", "Copeland", "Copeland ZP31K5E-PFV", 1.8, 3500.0,
        "nameplate", "A", 22, "DR-5", "DR-5", "R32/R1234yf 72.5/27.5 mass%", "DSP-1Y25A125 wattmeter",
    ),
    Spec(
        "rpt38", "AREP_Rpt038.pdf", "arep_rpt38_emerson2014", "Copeland", "Copeland ZP31K5E-PFV", 1.8, 3500.0,
        "nameplate", "A", 30, "L-41b", "L-41b", "R32/R1234ze(E) 73/27 mass%", "DSP-1Y25A125 wattmeter",
    ),
    Spec(
        "rpt39", "AREP_Rpt039.pdf", "arep_rpt39_emerson2014", "Copeland", "Copeland ZP31K5E-PFV", 1.8, 3500.0,
        "nameplate", "A", 23, "R32", "R-32", "R32 100 mass%", "DSP-1Y25A125 wattmeter",
    ),
    Spec(
        "rpt58", "AREP_Rpt058.pdf", "arep_rpt58_emerson2016", "Copeland", "Copeland ZP31K5E-PFV", 1.8, 3500.0,
        "nominal_statement", "058", 29, "R454B", "DR-5A (R454B)", "R32/R1234yf 68.9/31.1 mass%",
        "DSP-1Y25A125 wattmeter", False,
        {7: "printed Table 1 suction pressure 12.8 psia (0.88 bar) cannot belong to a 40 F dew point (the same "
            "report prints 121.8 psia / 8.40 bar for 40 F in other rows); kept as printed"},
    ),
    Spec(
        "rpt65", "AREP_Rpt065.pdf", "arep_rpt65_emerson2016", "Copeland", "Copeland ZP122KCE-TFD", 6.853, 2900.0,
        "nominal_statement", "065", 23, "R447A", "R-447A", "R32/R125/R1234ze(E) 68/3.5/28.5 mass%",
        "PM850MG power meter",
    ),
    Spec(
        "rpt66", "AREP_Rpt066.pdf", "arep_rpt66_danfoss2016", "Danfoss", "Danfoss SH161A4", None, 2900.0,
        "nameplate", "066", 28, "HPR2A", "HPR2A", "R32/R1234ze/R134a 76/18/6 mass%", "Norma4000 power analyser",
    ),
    Spec(
        "rpt34", "AREP_Rpt034.pdf", "arep_rpt34_emerson2014", "Copeland", "Copeland ZF18K4E-TFD", 5.983, 3500.0,
        "nameplate", "A", 19, "DR-7", "DR-7", "R32/R1234yf 36/64 mass%", "DSP-1Y25A125 wattmeter", True,
    ),
    Spec(
        "rpt36", "AREP_Rpt036.pdf", "arep_rpt36_emerson2014", "Copeland", "Copeland ZF18K4E-TFD", 5.983, 3500.0,
        "nameplate", "A", 0, "L-40", "L-40", "R32/R152a/R1234yf/R1234ze(E) 40/10/20/30 mass%",
        "DSP-1Y25A125 wattmeter", True,
    ),
]  # fmt: skip


@dataclass
class Row:
    spec: Spec
    n: int  # 1-based Table 3 row number
    pdf_page: int  # page of the Table 3 line
    pdf_page_t1: int
    t1: dict[str, float | None]
    t3: dict[str, float | None]
    raw_t1: str
    raw_t3: str
    raw_t3b: str = ""  # second table line (#66 Table A2)


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def _lines(pdf: Path) -> list[tuple[int, str]]:
    out = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, timeout=120, check=True
    ).stdout
    res: list[tuple[int, str]] = []
    for pno, page in enumerate(out.split("\f"), start=1):
        res.extend((pno, ln) for ln in page.splitlines())
    return res


# --------------------------------------------------------------------------------------- Table 1


T1_A = re.compile(rf"^\s*{NUM}\s+{NUM}\s+" + r"\s+".join([NUM] * 12) + rf"\s+({VOLT})(?:\s+([\d,]+))?\s*$")
T1_066 = re.compile(rf"^\s*{NUM}\s+{NUM}\s+" + r"\s+".join([NUM] * 10) + r"\s+\d+\s*/\s*\d\s*/\s*\d+\s+(\d+)\s*$")


def _t1_simple(lines: list[tuple[int, str]]) -> list[tuple[int, str, dict[str, float | None]]]:
    out = []
    for pno, ln in lines:
        m = T1_A.match(ln)
        if m:
            g = m.groups()
            v = [float(x) for x in g[:14]]
            out.append(
                (
                    pno, ln,
                    dict(
                        amb_F=v[0], p_suc_psia=v[2], Te_F=v[4], Tsuc_F=v[6], p_dis_psia=v[8], Tc_F=v[10], Tdis_F=v[12],
                        rpm=_f(g[15]) if g[15] else None,
                    ),
                )
            )  # fmt: skip
    return out


def _t1_066(lines: list[tuple[int, str]]) -> list[tuple[int, str, dict[str, float | None]]]:
    out = []
    for pno, ln in lines:
        m = T1_066.match(ln)
        if m:
            v = [float(x) for x in m.groups()[:12]]
            # amb F, amb C, psi, bar, Te F, Te C, Tsuc F, Tsuc C, pd psi, pd bar, Tc F, Tc C, rpm
            out.append(
                (
                    pno, ln,
                    dict(
                        amb_F=v[0], p_suc_psia=v[2], Te_F=v[4], Te_C=v[5], Tsuc_F=v[6], Tsuc_C=v[7],
                        p_dis_psia=v[8], Tc_F=v[10], Tc_C=v[11], rpm=float(m.group(13)),
                    ),
                )
            )  # fmt: skip
    return out


def _t1_wrapped(pdf: Path) -> list[tuple[int, str, dict[str, float | None]]]:
    """Report #58: every Table 1 cell is wrapped over up to three text lines.

    ``pdftotext -layout`` loses the column geometry on the continuation page, so the word boxes
    (``pdftotext -bbox``) are used: each word is assigned to the header column whose centre is
    horizontally closest, the fragments of one column are concatenated top to bottom, and the row is
    anchored on the word ``35.0`` (ambient 35 degC).  The result is cross-checked against Table 3.
    """
    xml = subprocess.run(
        ["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True, timeout=120, check=True
    ).stdout
    word = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">([^<]*)</word>')
    out: list[tuple[int, str, dict[str, float | None]]] = []
    cent: list[float] = []
    for pno, page in enumerate(xml.split("<page ")[1:], start=1):
        ws = [(float(m[1]), float(m[2]), float(m[3]), m[4]) for m in word.finditer(page)]  # xmin, ymin, xmax, text
        # header unit row: the line holding the word RPM
        hy = next((y for x0, y, x1, t in ws if t == "RPM"), None)
        if hy is not None:
            hdr = sorted(
                (x0 + x1) / 2.0 for x0, y, x1, t in ws if abs(y - hy) < 1.0 and t in ("°F", "°C", "psia", "Bar", "RPM")
            )
            if len(hdr) != 15:
                raise ValueError(f"rpt58 Table 1 header: {len(hdr)} columns")
            cent = hdr
        anchors = sorted(y for x0, y, x1, t in ws if t == "35.0" and 95.0 < x0 < 105.0)
        for y0 in anchors:
            cols: dict[int, list[tuple[float, str]]] = {}
            for x0, y, x1, t in ws:
                if abs(y - y0) < 11.0 and not re.fullmatch(VOLT, t):
                    j = min(range(15), key=lambda q: abs(cent[q] - (x0 + x1) / 2.0))
                    cols.setdefault(j, []).append((y, t))
            if sorted(cols) != list(range(15)):
                raise ValueError(f"rpt58 Table 1 row at y={y0}: columns {sorted(cols)}")
            vals = ["".join(t for _, t in sorted(cols[j])) for j in range(15)]
            v = [float(x) for x in vals]
            out.append(
                (
                    pno, " ".join(vals),
                    dict(amb_F=v[0], p_suc_psia=v[2], Te_F=v[4], Tsuc_F=v[6], p_dis_psia=v[8], Tc_F=v[10], Tdis_F=v[12], rpm=v[14]),
                )
            )  # fmt: skip
    return out


# --------------------------------------------------------------------------------------- Table 3


T3_A = re.compile(r"^\s*" + r"\s+".join([PAIR] * 9) + rf"\s+{NUM}\s+{NUMC}\s+{NUM}\s+{NUM}\s+{NUM}\s*$")
T3_058 = re.compile(r"^\s*" + r"\s+".join([NUMC] * 19) + r"\s*$")
T3_065 = re.compile(r"^\s*" + r"\s+".join([NUMC] * 21) + r"\s*$")
T3_066 = re.compile(r"^\s*" + r"\s+".join([NUMC] * 15) + r"\s*$")


def _t3(spec: Spec, lines: list[tuple[int, str]]) -> list[tuple[int, str, dict[str, float | None], str]]:
    out = []
    if spec.layout == "A":
        for pno, ln in lines:
            m = T3_A.match(ln)
            if m:
                g = [_f(x) for x in m.groups()]
                out.append(
                    (
                        pno, ln,
                        dict(
                            Te_F=g[0], Te_C=g[1], glide_e_F=g[2], glide_e_K=g[3], Tc_F=g[4], Tc_C=g[5],
                            glide_c_F=g[6], glide_c_K=g[7], Tdis_F=g[8], Tdis_C=g[9], SH_F=g[10], SH_K=g[11],
                            SC_F=g[12], SC_K=g[13], C_Btuh=g[14], C_W=g[15], M_lbh=g[16], M_kgh=g[17],
                            A=g[18], P_W=g[19], EER=g[20], COP=g[21], ratio=g[22],
                        ),
                        "",
                    )
                )  # fmt: skip
    elif spec.layout == "058":
        for pno, ln in lines:
            m = T3_058.match(ln)
            if m:
                g = [_f(x) for x in m.groups()]
                out.append(
                    (
                        pno, ln,
                        dict(
                            Te_F=g[0], Te_C=g[1], Tc_F=g[2], Tc_C=g[3], Tdis_F=g[4], Tdis_C=g[5], SH_F=g[6],
                            SH_K=g[7], SC_F=g[8], SC_K=g[9], C_Btuh=g[10], C_W=g[11], M_lbh=g[12], M_kgh=g[13],
                            A=g[14], P_W=g[15], EER=g[16], COP=g[17], ratio=g[18],
                        ),
                        "",
                    )
                )  # fmt: skip
    elif spec.layout == "065":
        for pno, ln in lines:
            m = T3_065.match(ln)
            if m:
                g = [_f(x) for x in m.groups()]
                out.append(
                    (
                        pno, ln,
                        dict(
                            Te_F=g[0], Te_C=g[1], glide_e_F=g[2], glide_e_K=g[3], Tc_F=g[4], Tc_C=g[5],
                            glide_c_F=g[6], glide_c_K=g[7], SH_F=g[8], SH_K=g[9], SC_F=g[10], SC_K=g[11],
                            C_Btuh=g[12], C_W=g[13], M_lbh=g[14], M_kgs=g[15], A=g[16], P_W=g[17], EER=g[18],
                            COP=g[19], ratio=g[20],
                        ),
                        "",
                    )
                )  # fmt: skip
    elif spec.layout == "066":
        a1 = [i for i, (_, ln) in enumerate(lines) if ln.strip().startswith("Table A1.")]
        a2 = [i for i, (_, ln) in enumerate(lines) if ln.strip().startswith("Table A2.")]
        b1 = [i for i, (_, ln) in enumerate(lines) if ln.strip().startswith("Appendix B")]
        if not (a1 and a2 and b1):
            raise ValueError("rpt66: table anchors not found")
        si = [(p, ln) for p, ln in lines[a1[-1] : a2[-1]] if T3_066.match(ln)]
        ip = [(p, ln) for p, ln in lines[a2[-1] : b1[-1]] if T3_066.match(ln)]
        if len(si) != len(ip):
            raise ValueError(f"rpt66: A1 {len(si)} rows, A2 {len(ip)} rows")
        for (pno, l1), (_, l2) in zip(si, ip, strict=True):
            s = [_f(x) for x in T3_066.match(l1).groups()]  # type: ignore[union-attr]
            q = [_f(x) for x in T3_066.match(l2).groups()]  # type: ignore[union-attr]
            # A1: SST SDT Te_mean Tc_mean glide_e glide_c DGT SH SC cap_W M_kgh A P COP ratio
            # A2: SST SDT Te_mean Tc_mean glide_e glide_c DGT SH SC cap_Btuh M_lbh A P EER ratio
            if abs(s[0] - (q[0] - 32) * 5 / 9) > 0.06 or abs(s[1] - (q[1] - 32) * 5 / 9) > 0.06:
                raise ValueError(f"rpt66: A1/A2 rows differ: {l1!r} / {l2!r}")
            if (
                s[3] != s[3]
                or abs(s[9] - q[9] * W_PER_BTUH) > 0.01 * s[9]
                or abs(s[10] - q[10] * 0.45359237) > 0.01 * s[10]
            ):
                raise ValueError(f"rpt66: A1/A2 capacity or flow differ: {l1!r} / {l2!r}")
            out.append(
                (
                    pno, l1,
                    dict(
                        Te_C=s[0], Tc_C=s[1], Te_mean_C=s[2], Tc_mean_C=s[3], glide_e_K=s[4], glide_c_K=s[5],
                        Tdis_C=s[6], SH_K=s[7], SC_K=s[8], C_W=s[9], M_kgh=s[10], A=s[11], P_W=s[12], COP=s[13],
                        ratio=s[14], Te_F=q[0], Tc_F=q[1], Tdis_F=q[6], SH_F=q[7], SC_F=q[8], C_Btuh=q[9],
                        M_lbh=q[10], EER=q[13], glide_e_F=q[4], glide_c_F=q[5],
                    ),
                    l2,
                )
            )  # fmt: skip
    return out


# --------------------------------------------------------------------------------------- driver


def parse_report(spec: Spec, n_rows: int | None = None) -> list[Row]:
    lines = _lines(ARDIR / spec.pdf)
    if spec.layout == "058":
        t1 = _t1_wrapped(ARDIR / spec.pdf)
    else:
        t1 = {"A": _t1_simple, "065": _t1_simple, "066": _t1_066}[spec.layout](lines)
    t3 = _t3(spec, lines)
    expected = n_rows if n_rows is not None else spec.n_rows
    if expected and (len(t1) != expected or len(t3) != expected):
        raise ValueError(f"{spec.key}: Table 1 {len(t1)} rows, Table 3 {len(t3)} rows, expected {expected}")
    if len(t1) != len(t3):
        raise ValueError(f"{spec.key}: Table 1 {len(t1)} rows vs Table 3 {len(t3)} rows")
    rows: list[Row] = []
    for n, ((p1, l1, a), (p3, l3, b, l3b)) in enumerate(zip(t1, t3, strict=True), start=1):
        # the two tables must describe the same test point (their dew temperatures are printed in both)
        tol = (
            0.06 if spec.layout in ("065", "066") else 0.51
        )  # Table 3 prints dew temperatures rounded to 1 degF (or 0.1)
        for ka, kb in (("Te_F", "Te_F"), ("Tc_F", "Tc_F")):
            if abs(a[ka] - b[kb]) > tol:  # type: ignore[operator]
                raise ValueError(f"{spec.key} row {n}: Table 1 {ka}={a[ka]} vs Table 3 {kb}={b[kb]}")
        if "Tdis_F" in a and "Tdis_F" in b and abs(a["Tdis_F"] - b["Tdis_F"]) > 0.51:  # type: ignore[operator]
            raise ValueError(f"{spec.key} row {n}: discharge temperature differs between tables")
        rows.append(Row(spec, n, p3, p1, a, b, l1.strip(), l3.strip(), l3b.strip()))
    return rows


EXTRA_COLUMNS = [
    "report",
    "data_kind",
    "printed_flag",
    "pdf_page",
    "pdf_page_t1",
    "refrigerant_report_name",
    "composition_note",
    "row_no",
    "ambient_F_printed",
    "rpm_printed",
    "I_comp_A",
    "T_evap_F_printed",
    "T_cond_F_printed",
    "T_evap_C_printed",
    "T_cond_C_printed",
    "p_suc_psia_printed",
    "p_dis_psia_printed",
    "T_suc_F_printed",
    "T_suc_C_printed",
    "T_dis_F_printed",
    "T_dis_C_printed",
    "superheat_F_printed",
    "subcooling_F_printed",
    "glide_evap_K_printed",
    "glide_cond_K_printed",
    "C_Btuh_printed",
    "C_W_printed",
    "M_lbh_printed",
    "M_kgh_printed",
    "M_kgs_printed",
    "P_W_printed",
    "EER_printed",
    "COP_printed",
    "COP_ratio_to_baseline_printed",
]


def _note(spec: Spec) -> str:
    base = {
        "A": "measured (Table 3, row paired with Table 1 pressures/speed); power = compressor input, "
        f"{spec.power_meter}; capacity = measured mass flow x enthalpy change (chemical-maker properties); "
        "R-410A/R-404A baseline is published rating data, not tabulated here; superheat/subcooling 'applicable' "
        "values as printed; T_dis measured 6 in downstream of discharge port",
        "058": "measured on a gas-cycle stand (no evaporator/condenser, no glide printed); power = compressor "
        f"input, {spec.power_meter}; capacity = mass flow x enthalpy change; baseline = published rating data; "
        "T_dis measured 6 in downstream of discharge port",
        "065": "measured (Mikulov lab, 380-3-50, nominal 2900 rpm printed per row); power = compressor input, "
        f"{spec.power_meter}; capacity = mass flow x enthalpy change; R-410A baseline = published rating data, "
        "not tabulated; 'applicable superheat' printed as 20 R but Table 1 entering-gas temperature is about "
        "17-18 F above the dew point",
        "066": "measured (Danfoss Tianjin, EN 13771-1); Tables A1 (SI) + A2 (IP) with the same rows; power = "
        f"compressor input, {spec.power_meter}; subcooling defined at the bubble point of the condensing "
        "pressure; mass flow from A2 lb/h (A1 prints integer kg/h); baseline R410A = published data plus a few "
        "validation points, not tabulated",
    }[spec.layout]
    return base


def to_row(r: Row) -> dict:
    s, a, b = r.spec, r.t1, r.t3
    is066 = s.layout == "066"
    flags = [s.flags.get(r.n, "")]
    if s.V_in3 is None:
        flags.append("displacement not printed in report; V_disp_cm3 left blank")
    cop_calc = b["C_W"] / b["P_W"]  # type: ignore[operator]
    if abs(b["COP"] - cop_calc) / cop_calc > 0.01:  # type: ignore[operator]
        flags.append(
            f"printed COP {b['COP']} / EER {b['EER']} differ from printed capacity/power ({cop_calc:.3f} W/W) by "
            f"{abs(b['COP'] - cop_calc) / cop_calc * 100:.1f} %; all values kept as printed"  # type: ignore[operator]
        )
    m_kgs = b["M_lbh"] * KGS_PER_LBH  # type: ignore[operator]
    if s.layout == "065":
        # The printed 'lb/h' column equals kg/h x 0.4536 (the conversion was applied in the wrong direction):
        # the kg/s column (0.0342 kg/s = 123 kg/h, consistent with capacity / enthalpy change) is the usable flow.
        m_kgs = b["M_kgs"]
        flags.append(
            "printed lb/h flow equals kg/h x 0.4536 (wrong-direction conversion); m_dot_kg_s from the kg/s column"
        )
    rpm = a["rpm"]
    if rpm is None:
        flags.append("speed not printed for this row; nominal speed used in N_rps")
    n_rps = (rpm if rpm is not None else s.rpm_nominal) / 60.0
    sh_conv = "measured"
    if s.layout == "058":
        # Table 1 prints the entering gas temperature equal to the dew temperature (zero superheat) although
        # Table 3 and Table 1 footnote 2 say 20 F superheat: the printed value is not used for T_suc_C.
        sh_conv = "custom"
        flags.append(
            "Table 1 vapor temperature entering compressor equals the dew temperature although Table 3 states "
            "20 F superheat; T_suc_C left blank, dT_sh_K = printed applicable superheat"
        )
    Te_C = b["Te_C"] if is066 else f2c(b["Te_F"])  # type: ignore[arg-type]
    Tc_C = b["Tc_C"] if is066 else f2c(b["Tc_F"])  # type: ignore[arg-type]
    Tsuc_C = a["Tsuc_C"] if is066 else f2c(a["Tsuc_F"])  # type: ignore[arg-type]
    if s.layout == "058":
        Tsuc_C = None
    sh_K = b["SH_K"] if is066 else b["SH_F"] * 5.0 / 9.0  # type: ignore[operator]
    sc_K = b["SC_K"] if is066 else b["SC_F"] * 5.0 / 9.0  # type: ignore[operator]
    Tdis_F = b["Tdis_F"] if b.get("Tdis_F") is not None else a["Tdis_F"]  # #65 prints it only in Table 1
    Tdis_C = b["Tdis_C"] if is066 else f2c(Tdis_F)  # type: ignore[arg-type]
    pt = CompressorPoint(
        source_id=s.source_id,
        manufacturer=s.manufacturer,
        model=s.model,
        comp_type="scroll",
        refrigerant=s.fluid,
        V_disp_cm3=s.V_in3 * CM3_PER_IN3 if s.V_in3 else float("nan"),
        N_rated_rps=s.rpm_nominal / 60.0,
        N_rated_basis=s.rated_basis,
        N_rps=n_rps,
        Q_evap_W=b["C_Btuh"] * W_PER_BTUH,  # type: ignore[operator]
        P_el_W=b["P_W"],
        T_evap_C=Te_C,
        T_cond_C=Tc_C,
        p_suc_Pa=a["p_suc_psia"] * PA_PER_PSI,  # type: ignore[operator]
        p_dis_Pa=a["p_dis_psia"] * PA_PER_PSI,  # type: ignore[operator]
        sat_convention="dew_dew",
        sh_convention=sh_conv,
        dT_sh_K=sh_K,
        T_suc_C=Tsuc_C,
        dT_sc_K=sc_K,
        m_dot_kg_s=m_kgs,
        T_dis_C=Tdis_C,
        Q_cond_W=None,
        P_includes_inverter=False,
        method="pdf_text",
        doc_path=f"compressor_maps/arep/{s.pdf}",
        page=f"pdf {r.pdf_page}",
        table_id=f"{s.key} Table {'A1/A2' if is066 else '3'} row {r.n}",
        note=_note(s),
    )
    d = asdict(pt)
    d.pop("extra", None)
    if s.V_in3 is None:
        d["V_disp_cm3"] = ""
    d.update(
        report=s.key,
        data_kind="measured",
        printed_flag="; ".join(f for f in flags if f),
        pdf_page=r.pdf_page,
        pdf_page_t1=r.pdf_page_t1,
        refrigerant_report_name=s.fluid_name,
        composition_note=s.composition,
        row_no=r.n,
        ambient_F_printed=a["amb_F"],
        rpm_printed="" if rpm is None else rpm,
        I_comp_A=b["A"],
        T_evap_F_printed=b["Te_F"],
        T_cond_F_printed=b["Tc_F"],
        T_evap_C_printed=b.get("Te_C", ""),
        T_cond_C_printed=b.get("Tc_C", ""),
        p_suc_psia_printed=a["p_suc_psia"],
        p_dis_psia_printed=a["p_dis_psia"],
        T_suc_F_printed=a["Tsuc_F"],
        T_suc_C_printed=a.get("Tsuc_C", ""),
        T_dis_F_printed=Tdis_F,
        T_dis_C_printed=b.get("Tdis_C", ""),
        superheat_F_printed=b["SH_F"],
        subcooling_F_printed=b["SC_F"],
        glide_evap_K_printed=b.get("glide_e_K", ""),
        glide_cond_K_printed=b.get("glide_c_K", ""),
        C_Btuh_printed=b["C_Btuh"],
        C_W_printed=b["C_W"],
        M_lbh_printed=b["M_lbh"],
        M_kgh_printed=b.get("M_kgh", ""),
        M_kgs_printed=b.get("M_kgs", ""),
        P_W_printed=b["P_W"],
        EER_printed=b["EER"],
        COP_printed=b["COP"],
        COP_ratio_to_baseline_printed=b["ratio"],
    )
    return d


def parse_all() -> list[Row]:
    out: list[Row] = []
    for spec in SPECS:
        out.extend(parse_report(spec, spec.n_rows or None))
    return out


def _write_csv(rows: list[Row], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS + EXTRA_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(to_row(r))


def write(rows: list[Row], path: Path = OUT, path_no_disp: Path = OUT_NO_DISP) -> None:
    _write_csv([r for r in rows if r.spec.V_in3 is not None], path)
    _write_csv([r for r in rows if r.spec.V_in3 is None], path_no_disp)


def main() -> None:
    rows = parse_all()
    write(rows)
    n_nd = sum(r.spec.V_in3 is None for r in rows)
    print(f"{len(rows) - n_nd} points -> {OUT}; {n_nd} points (no displacement printed) -> {OUT_NO_DISP}")


if __name__ == "__main__":
    main()
