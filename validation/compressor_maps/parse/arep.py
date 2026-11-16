"""ORNL / AHRI Low-GWP AREP compressor-calorimeter tables (appendix A) -> point rows.

Three public ORNL reports, one Copeland hermetic scroll per report:

* ``arep_rpt11``   AHRI Low-GWP AREP Test Report #11 (Shrestha et al., ORNL, 2013),
  ZP21K5E-PFV, 1.24 in3/rev, 60 Hz; R-410A, R32, DR-5, L-41A; Tables A1-A12; 15 degF
  subcooling.
* ``arep_rpt21``   AHRI Low-GWP AREP Test Report #21 (ORNL, 5 Aug 2013), a *different*,
  larger compressor (3.11 in3/rev, 60 Hz; R-404A and four alternatives, 0 degF
  subcooling); Tables A1-A15.
* ``ornl_tm_2013_414``  ORNL/TM-2013/414, same 1.24 in3/rev scroll as report #11;
  R-410A and R-32/R-134a (94.07/5.93 mass%); Tables A1-A6; 15 degF subcooling.

Table layout (``pdftotext -layout``): per table 10 suction dew-point columns
(headers like ``10 (76)`` = dew-point degF (psia)) and one 7-line block per discharge
dew-point temperature, line types C capacity [Btu/h], P power [W], A current [A at 230 V],
M mass flow [lb/h], E EER [Btu/Wh], % isentropic efficiency [%], T discharge temperature
[degF].  The discharge dew point [degF] and pressure [psia] are printed on their own lines
inside the block.  The staircase of blank cells shifts values left/right, so every number
is assigned to the suction column whose header is horizontally closest to it; the
assignment is cross-checked (equal column sets over the seven line types,
monotonic capacity and mass flow, pressure and EER recomputation against CoolProp).

Unit constants (exact or standard): degC = (degF - 32) * 5/9; 1 Btu_IT/h = 0.29307107 W;
1 lb/h = 0.45359237/3600 kg/s; 1 psia = 6894.757293168 Pa; 1 in3 = 16.387064 cm3.

Speed: the reports give only "60 Hz"; the printed displacement rate of 150.69 ft3/h for
1.24 in3/rev implies 150.69*1728/60/1.24 = 3500.0 rev/min (3.11 in3/rev -> 377.71 ft3/h
gives 3498 rev/min, rounding of 3.11), so N = 3500/60 rev/s for all rows.

The reports never define their "isentropic efficiency" or what the Yokogawa power
includes; the recomputation tests are in the verification log of the PR.  The printed value is kept as
``eta_isen_printed`` (a fraction), unconverted values as ``*_printed`` columns.

Run ``python -m validation.compressor_maps.parse.arep`` to write the CSV.
"""

from __future__ import annotations

import csv
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from validation.compressor_maps.schema import COLUMNS, DATA_DIR, EVIDENCE_DIR, CompressorPoint

ARDIR = EVIDENCE_DIR / "arep"
OUT = DATA_DIR / "points_arep.csv"

W_PER_BTUH = 0.29307107
KGS_PER_LBH = 0.45359237 / 3600.0
PA_PER_PSI = 6894.757293168
CM3_PER_IN3 = 16.387064
RPM = 3500.0
N_RPS = RPM / 60.0
MANUFACTURER = "Copeland"

LINE_TYPES = ("C", "P", "A", "M", "E", "%", "T")


@dataclass(frozen=True)
class Report:
    key: str
    pdf: str
    source_id: str
    model: str
    V_in3: float
    n_tables: int
    # printed table title refrigerant name -> (CoolProp string or report name, composition note)
    fluids: dict[str, tuple[str, str]]


REPORTS = [
    Report(
        "rpt11",
        "AHRI_AREP_Rpt011_ORNL_Pub44928.pdf",
        "arep_rpt11_ornl2013",
        "Copeland ZP21K5E-PFV",
        1.24,
        12,
        {
            "R-410A": ("R410A", "R32/R125 50/50 mass%"),
            "R32": ("R32", ""),
            "DR-5": ("DR-5", "DuPont developmental blend, composition not in report"),
            "L-41A": ("L-41A", "Honeywell developmental blend, composition not in report"),
        },
    ),
    Report(
        "rpt21",
        "Pub42278.pdf",
        "arep_rpt21_ornl2013",
        "Copeland scroll 3.11 in3/rev (PFV, 7/8 in suction)",
        3.11,
        15,
        {
            "R-404A": ("R404A", ""),
            "ARM-31a": ("ARM-31a", "R32/R134a/R1234yf 28/21/51 mass%"),
            "D2Y-65": ("D2Y-65", "R32/R1234yf 35/65 mass%"),
            "L-40": ("L-40", "R32/R152a/R1234yf/R1234ze(E) 40/10/20/30 mass%"),
            "R-32 + R-134a mixture": ("R32/R134a", "R32 50.53 / R134a 49.47 mass% (report text)"),
        },
    ),
    Report(
        "tm414",
        "Pub46026.pdf",
        "ornl_tm_2013_414",
        "Copeland ZP21K5E-PFV",
        1.24,
        6,
        {
            "R-410A": ("R410A", "R32/R125 50/50 mass%"),
            "R-32/134a": ("R32/R134a", "R32/R134a 94.07/5.93 mass%"),
        },
    ),
]

# Printed values that are inconsistent with the same report elsewhere; kept as printed, flagged in the CSV.
PRINT_FLAGS = {
    (
        "rpt11",
        "A5",
        140.0,
    ): "printed discharge pressure 556 psia is the R-410A value; R32 140 F dew is 570 psia (printed in A4, CoolProp 570.5)",
    (
        "rpt11",
        "A6",
        140.0,
    ): "printed discharge pressure 556 psia is the R-410A value; R32 140 F dew is 570 psia (printed in A4, CoolProp 570.5)",
}

TITLE = re.compile(r"Table (A\d+)[.:]\s+(.+?)\s*$")
TITLE_BODY = re.compile(
    r"^(?P<fluid>.+?) at (?:(?P<sh>\d+)\s*°F\s*Superheat|(?:Suction Temperature\s*(?P<ts1>\d+)\s*°F|(?P<ts2>\d+)\s*°F\s*Suction Temperature)),\s*(?P<sc>\d+)\s*°F\s*Subcooling$"
)
HEADER = re.compile(r"(-?\d+)\s*\(([\d.]+)\)")
NUM = re.compile(r"-?\d+(?:\.\d+)?")
LINE = re.compile(r"^\s*(?:(\d{2,3})\s+)?([CPAME%T])\s{2,}(\S.*)$")
DISCH_T = re.compile(r"^\s*(\d{2,3})\s*$")
DISCH_P = re.compile(r"^\s*\(([\d.]+)\)\s*$")


@dataclass
class Cell:
    report: Report
    table: str
    title: str
    fluid_title: str
    sh_F: float | None
    tsuc_F: float | None
    sc_F: float
    pdf_page: int
    page_label: str
    Td_F: float
    pd_psia: float
    Ts_F: float
    ps_text: str
    vals: dict[str, float]
    raw: dict[str, str]
    col: int


def _text(pdf: Path) -> list[str]:
    out = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, timeout=120, check=True
    )
    return out.stdout.split("\f")


def _assign(rest_start: int, line: str, centers: list[float]) -> dict[int, float]:
    """Map each number token in ``line`` to the nearest header column by horizontal centre."""
    res: dict[int, float] = {}
    for m in NUM.finditer(line, rest_start):
        c = (m.start() + m.end()) / 2.0
        j = min(range(len(centers)), key=lambda k: abs(centers[k] - c))
        if j in res:
            raise ValueError(f"two numbers map to column {j}: {line!r}")
        res[j] = float(m.group())
    return res


def parse_report(rep: Report) -> list[Cell]:
    pages = _text(ARDIR / rep.pdf)
    cells: list[Cell] = []
    ntab = 0
    for pno, page in enumerate(pages, start=1):
        lines = page.splitlines()
        tl = [i for i, ln in enumerate(lines) if TITLE.search(ln)]
        if not tl:
            continue
        i0 = tl[0]
        m = TITLE.search(lines[i0])
        assert m is not None
        table, title = m.group(1), m.group(2)
        b = TITLE_BODY.match(title)
        if b is None:
            raise ValueError(f"{rep.key} {table}: unparsed title {title!r}")
        fluid_title = b["fluid"].strip()
        if fluid_title not in rep.fluids:
            raise ValueError(f"{rep.key} {table}: unknown refrigerant {fluid_title!r}")
        sh = float(b["sh"]) if b["sh"] else None
        ts = float(b["ts1"] or b["ts2"]) if not b["sh"] else None
        sc = float(b["sc"])
        # header line: first line after 'Suction Dew Point' containing >= 8 header matches
        hi = next(i for i in range(i0, len(lines)) if "Suction Dew Point" in lines[i])
        hline = lines[hi + 1]
        hm = list(HEADER.finditer(hline))
        if len(hm) != 10:
            raise ValueError(f"{rep.key} {table}: {len(hm)} header columns")
        centers = [(h.start() + h.end()) / 2.0 for h in hm]
        suc = [(float(h.group(1)), h.group(0).replace(" ", "")) for h in hm]
        i = hi + 2
        end = next(k for k in range(i, len(lines)) if "Capacity (Btu" in lines[k])
        label = next((ln.strip() for ln in lines[end:] if DISCH_T.match(ln)), "")
        block: dict[str, tuple[str, dict[int, float]]] = {}
        extra_t: list[float] = []
        extra_p: list[float] = []
        nblocks = 0
        while i < end:
            ln = lines[i]
            mt, mp, ml = DISCH_T.match(ln), DISCH_P.match(ln), LINE.match(ln)
            if ml:
                lt = ml.group(2)
                if ml.group(1):  # discharge dew point printed on the same text line as this row
                    extra_t.append(float(ml.group(1)))
                block[lt] = (ln, _assign(ml.start(3), ln, centers))
                if lt == "T":
                    if len(extra_t) != 1 or len(extra_p) != 1 or set(block) != set(LINE_TYPES):
                        raise ValueError(f"{rep.key} {table}: bad block ending line {i}")
                    td, pd = extra_t[0], extra_p[0]
                    cols = {k: set(v[1]) for k, v in block.items()}
                    if any(c != cols["C"] for c in cols.values()):
                        raise ValueError(f"{rep.key} {table} Td={td}: column sets differ {cols}")
                    for j in sorted(cols["C"]):
                        cells.append(
                            Cell(
                                rep, table, title, fluid_title, sh, ts, sc, pno, label, td, pd,
                                suc[j][0], hm[j].group(2),
                                {k: block[k][1][j] for k in LINE_TYPES},
                                {k: block[k][0] for k in LINE_TYPES}, j,
                            )
                        )  # fmt: skip
                    block, extra_t, extra_p = {}, [], []
                    nblocks += 1
            elif mt:
                extra_t.append(float(mt.group(1)))
            elif mp:
                extra_p.append(float(mp.group(1)))
            i += 1
        if block or extra_t or extra_p:
            raise ValueError(f"{rep.key} {table}: dangling lines")
        ntab += 1
    if ntab != rep.n_tables:
        raise ValueError(f"{rep.key}: found {ntab} tables, expected {rep.n_tables}")
    return cells


def f2c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


EXTRA_COLUMNS = [
    "report",
    "printed_flag",
    "pdf_page",
    "page_label",
    "printed_title",
    "refrigerant_report_name",
    "composition_note",
    "I_comp_A",
    "eta_isen_printed",
    "EER_Btu_per_Wh",
    "T_evap_F_printed",
    "T_cond_F_printed",
    "p_suc_psia_printed",
    "p_dis_psia_printed",
    "superheat_F_printed",
    "T_suc_F_printed",
    "subcooling_F_printed",
    "C_Btuh_printed",
    "P_W_printed",
    "A_A_printed",
    "M_lbh_printed",
    "EER_printed",
    "eta_isen_pct_printed",
    "T_dis_F_printed",
]


def to_row(c: Cell) -> dict:
    rep, v = c.report, c.vals
    cp, comp = rep.fluids[c.fluid_title]
    pt = CompressorPoint(
        source_id=rep.source_id,
        manufacturer=MANUFACTURER,
        model=rep.model,
        comp_type="scroll",
        refrigerant=cp,
        V_disp_cm3=rep.V_in3 * CM3_PER_IN3,
        N_rated_rps=N_RPS,
        N_rated_basis="nominal_statement",
        N_rps=N_RPS,
        Q_evap_W=v["C"] * W_PER_BTUH,
        P_el_W=v["P"],
        T_evap_C=f2c(c.Ts_F),
        T_cond_C=f2c(c.Td_F),
        p_suc_Pa=float(c.ps_text) * PA_PER_PSI,
        p_dis_Pa=c.pd_psia * PA_PER_PSI,
        sat_convention="dew_dew",
        sh_convention="custom" if c.sh_F is not None else "measured",
        dT_sh_K=None if c.sh_F is None else c.sh_F * 5.0 / 9.0,
        T_suc_C=None if c.tsuc_F is None else f2c(c.tsuc_F),
        dT_sc_K=c.sc_F * 5.0 / 9.0,
        m_dot_kg_s=v["M"] * KGS_PER_LBH,
        T_dis_C=f2c(v["T"]),
        Q_cond_W=None,
        P_includes_inverter=False,
        method="pdf_text",
        doc_path=f"compressor_maps/arep/{rep.pdf}",
        page=f"pdf {c.pdf_page} (printed {c.page_label})" if c.page_label else f"pdf {c.pdf_page}",
        table_id=f"{rep.key} {c.table}",
        note="Yokogawa PR300 compressor power, line-fed 60 Hz; eta_isen as printed (definition not given in report)",
    )
    d = asdict(pt)
    d.pop("extra", None)
    d.update(
        report=rep.key,
        printed_flag=PRINT_FLAGS.get((rep.key, c.table, c.Td_F), ""),
        pdf_page=c.pdf_page,
        page_label=c.page_label,
        printed_title=f"Table {c.table}: {c.title}",
        refrigerant_report_name=c.fluid_title,
        composition_note=comp,
        I_comp_A=v["A"],
        eta_isen_printed=v["%"] / 100.0,
        EER_Btu_per_Wh=v["E"],
        T_evap_F_printed=c.Ts_F,
        T_cond_F_printed=c.Td_F,
        p_suc_psia_printed=c.ps_text,
        p_dis_psia_printed=c.pd_psia,
        superheat_F_printed="" if c.sh_F is None else c.sh_F,
        T_suc_F_printed="" if c.tsuc_F is None else c.tsuc_F,
        subcooling_F_printed=c.sc_F,
        C_Btuh_printed=v["C"],
        P_W_printed=v["P"],
        A_A_printed=v["A"],
        M_lbh_printed=v["M"],
        EER_printed=v["E"],
        eta_isen_pct_printed=v["%"],
        T_dis_F_printed=v["T"],
    )
    return d


def parse_all() -> list[Cell]:
    out: list[Cell] = []
    for rep in REPORTS:
        out.extend(parse_report(rep))
    return out


def write(cells: list[Cell], path: Path = OUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS + EXTRA_COLUMNS)
        w.writeheader()
        for c in cells:
            w.writerow(to_row(c))


def main() -> None:
    cells = parse_all()
    write(cells)
    print(f"{len(cells)} points -> {OUT}")


if __name__ == "__main__":
    main()
