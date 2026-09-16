"""L2/L3 (condenser side) -- invert ENV 327 air-cooled condenser catalogues.

ENV 327 rates air-cooled condensers at R-404A, entering air 25 degC, condensing
40 degC, hence ``DT1 = 15 K``. It is a different standard, written by a
different committee for a different application than the EN 328 used on the
evaporator side, and it adopts an air flow per kilowatt roughly 2.5 times
smaller. If both populations nonetheless occupy the same band of conductance
per unit duty, that band is a property of how air coils are built rather than
an artefact of either rating rule.

Why LU-VE
---------
LU-VE prints its ranges transposed -- one row per property, one column per
model -- and the capacity row carries its own rating condition inline
(``Capacity kW (DT 15K)``). The rating condition therefore sits directly above
the data rather than in a distant footnote, which removes the column-drift
failure mode that makes the row-per-model condenser tables risky to scrape.

Run
---
``uv run python -m validation.extraction.env327_inversion``
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from ._inversion import invert
from ._pdftext import EvidenceMissing, evidence_path, text_lines

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_CSV = REPO_ROOT / "validation" / "data" / "env327_condenser_inversion.csv"

LUVE_PDF = "catalogs/luve_air_cooled_condensers.pdf"
ALFA_PDF = "catalogs/alfalaval_acq504_condenser_spec.pdf"

#: ENV 327 entering-air to condensing temperature difference.
DT1_ENV327 = 15.0
#: Entering air temperature at the ENV 327 point [degC].
T_AIR_ENV327 = 25.0
#: Air density at 25 degC, 101.325 kPa [kg/m^3].
RHO_AIR_ENV327 = 1.1839

_MODEL_ROW = re.compile(r"Modello\s+Type\s+(\S+)\s+(.*)$")
_CAPACITY_ROW = re.compile(r"Capacity\s+kW\s*\(.?T\s*15\s*K\)\s*(.*)$")
_FLOW_ROW = re.compile(r"Air\s+quantity\s+m3/h\s*(.*)$")
_SURFACE_ROW = re.compile(r"External\s+surface\s+m2\s*(.*)$")

_NUM = re.compile(r"^\d{1,3}(?:[.,]\d+)?$")


def _numbers(blob: str) -> list[float]:
    out = []
    for tok in blob.split():
        tok = tok.strip()
        if re.fullmatch(r"\d+(?:[.,]\d+)?", tok):
            out.append(float(tok.replace(",", ".")))
    return out


def parse_luve() -> list[dict]:
    """Walk every page, pairing a model row with the rows that follow it."""
    import pdfplumber

    rows: list[dict] = []
    anchored = False
    with pdfplumber.open(evidence_path(LUVE_PDF)) as pdf:
        n_pages = len(pdf.pages)
    for page in range(n_pages):
        lines = text_lines(LUVE_PDF, page)
        family = None
        models: list[str] = []
        caps: list[float] = []
        flows: list[float] = []
        surfaces: list[float] = []
        for line in lines:
            m = _MODEL_ROW.search(line)
            if m:
                if family and models and caps and flows:
                    rows.extend(_emit(family, models, caps, flows, surfaces))
                    if family == "LMC3N" and models[:2] == ["1510", "1511"]:
                        anchored = True
                family, tail = m.group(1), m.group(2)
                models = tail.split()
                caps, flows, surfaces = [], [], []
                continue
            m = _CAPACITY_ROW.search(line)
            if m and not caps:
                caps = _numbers(m.group(1))
                continue
            m = _FLOW_ROW.search(line)
            if m and not flows:
                flows = [v for v in _numbers(m.group(1)) if v >= 500.0]
                continue
            m = _SURFACE_ROW.search(line)
            if m and not surfaces:
                surfaces = _numbers(m.group(1))
        # Emit at end of page: the external-surface row is not printed for every
        # range, so a block must not depend on it being present.
        if family and models and caps and flows:
            rows.extend(_emit(family, models, caps, flows, surfaces))
            if family == "LMC3N" and models[:2] == ["1510", "1511"]:
                anchored = True
    assert anchored, "LU-VE anchor block not found: expected LMC3N 1510/1511 with 9,3/11 kW at 2700/2500 m3/h"
    return rows


def _emit(family: str, models: list[str], caps: list[float], flows: list[float], surfaces: list[float]) -> list[dict]:
    n = min(len(models), len(caps), len(flows))
    if n == 0:
        return []
    out = []
    for i in range(n):
        if not (1.0 <= caps[i] <= 3000.0 and 500.0 <= flows[i] <= 800000.0):
            continue  # not a capacity/air-flow pair -- a stray number in the row
        duty_w = caps[i] * 1000.0
        flow_m3_s = flows[i] / 3600.0
        surface = surfaces[i] if i < len(surfaces) else None
        try:
            inv = invert(duty_w, flow_m3_s, DT1_ENV327, rho=RHO_AIR_ENV327)
        except ValueError:
            continue  # effectiveness out of range -> column drift, drop the row
        out.append(
            {
                "source": "LU-VE air-cooled condensers",
                "series": family,
                "model": f"{family} {models[i]}",
                "Q_kW": caps[i],
                "airflow_m3h": flows[i],
                "surface_m2": surface,
                "C_air_W_K": inv.c_air_w_k,
                "eps": inv.eps,
                "NTU": inv.ntu,
                "UA_W_K": inv.ua_w_k,
                "LMTD_K": inv.lmtd_k,
                "UA_over_Q": inv.ua_over_q,
                "airflow_m3h_per_kW": inv.flow_m3h_per_kw,
                "U_W_m2K": (inv.ua_w_k / surface) if surface else None,
            }
        )
    return out


# --------------------------------------------------------------------------
# Alfa Laval AlfaGreen AC / ACD / ACV air-cooled condensers (ECR00011EN)
# --------------------------------------------------------------------------
# The document declares the rating condition verbatim: "standard ENV 327
# (R404A, Tair = 25 degC, Tcond = 40 degC)", hence DT1 = 15 K.
#
# Reading the columns took some care. Each range is printed with two motor
# connections -- star and delta, i.e. two fan speeds -- so capacity and air
# flow each appear twice, and some pages carry an extra placeholder column that
# others do not, which makes counting tokens unreliable. Two facts make the
# split unambiguous instead: every capacity in the range is below 1000 kW and
# every air flow is above 1000 m3/h, and the two speeds must satisfy the fan
# law that ties duty to flow. Both are asserted rather than assumed.
_ALFA_ROW = re.compile(r"^(AC[A-Z]*\d+[A-Z]?)\s+(.+)$")
#: Duty scales with air flow through the coil's own exponent. The two printed
#: speeds of one model must obey it; if the columns had been mis-split they
#: would not.
FLOW_EXPONENT = 0.65
FLOW_EXPONENT_TOLERANCE = 0.08


def _alfa_numbers(tail: str) -> tuple[list[float], list[float]]:
    duties, flows = [], []
    for token in tail.split():
        if not re.fullmatch(r"\d+(?:[,.]\d+)?", token):
            continue
        value = float(token.replace(",", "."))
        if value < 1000.0:
            duties.append(value)
        else:
            flows.append(value)
    return duties, flows


def parse_alfalaval() -> list[dict]:
    import pdfplumber

    from ._pdftext import evidence_path

    with pdfplumber.open(evidence_path(ALFA_PDF)) as pdf:
        n_pages = len(pdf.pages)

    rows: list[dict] = []
    checked = 0
    for page in range(n_pages):
        for raw in text_lines(ALFA_PDF, page):
            m = _ALFA_ROW.match(raw.strip())
            if not m:
                continue
            model, tail = m.group(1), m.group(2)
            duties, flows = _alfa_numbers(tail)
            if not duties or not flows:
                continue
            duty_kw, flow_m3h = duties[0], flows[0]
            if not (5.0 <= duty_kw <= 999.0 and 1000.0 <= flow_m3h <= 400000.0):
                continue

            # Fan-law cross-check on the models that print both speeds.
            if len(duties) >= 2 and len(flows) >= 2 and duties[1] > 0 and flows[1] > 0:
                predicted = (flows[1] / flow_m3h) ** FLOW_EXPONENT
                observed = duties[1] / duty_kw
                if abs(observed - predicted) > FLOW_EXPONENT_TOLERANCE:
                    continue  # columns do not hang together -- drop the row
                checked += 1

            try:
                inv = invert(duty_kw * 1000.0, flow_m3h / 3600.0, DT1_ENV327, rho=RHO_AIR_ENV327)
            except ValueError:
                continue
            rows.append(
                {
                    "source": "Alfa Laval AlfaGreen AC/ACD/ACV (ECR00011EN)",
                    "series": re.match(r"^(AC[A-Z]*)", model).group(1),
                    "model": model,
                    "Q_kW": duty_kw,
                    "airflow_m3h": flow_m3h,
                    "surface_m2": None,
                    "C_air_W_K": inv.c_air_w_k,
                    "eps": inv.eps,
                    "NTU": inv.ntu,
                    "UA_W_K": inv.ua_w_k,
                    "LMTD_K": inv.lmtd_k,
                    "UA_over_Q": inv.ua_over_q,
                    "airflow_m3h_per_kW": inv.flow_m3h_per_kw,
                    "U_W_m2K": None,
                }
            )
    assert checked >= 50, (
        f"Alfa Laval: only {checked} rows could be cross-checked against the fan law; "
        "the column split is not trustworthy"
    )
    return rows


def main() -> None:
    print("L2/L3 -- ENV 327 condenser practice (R-404A, air 25 degC, cond 40 degC, DT1 15 K)")
    rows: list[dict] = []
    for name, parser in (("LU-VE", parse_luve), ("Alfa Laval", parse_alfalaval)):
        try:
            got = parser()
        except EvidenceMissing as exc:
            print(f"  [skip] {name}: {exc}")
            continue
        print(f"  {name:<24} {len(got):4d} rows")
        rows.extend(got)
    if not rows:
        return
    df = pd.DataFrame(rows).drop_duplicates(subset=["model", "Q_kW", "airflow_m3h"])
    df.to_csv(OUT_CSV, index=False)
    print(f"  {len(df)} models, {df.source.nunique()} manufacturers, {df.series.nunique()} series")
    print()
    print("  per source:")
    print(
        df.groupby("source")
        .agg(
            n=("model", "size"),
            lmtd_median=("LMTD_K", "median"),
            ua_over_q=("UA_over_Q", "median"),
            flow_per_kw=("airflow_m3h_per_kW", "median"),
        )
        .to_string(float_format=lambda v: f"{v:.3f}")
    )
    print()
    print(f"  duty range       : {df.Q_kW.min():.1f} - {df.Q_kW.max():.1f} kW")
    print(
        f"  equivalent LMTD  : median {df.LMTD_K.median():.2f} K "
        f"(p10 {df.LMTD_K.quantile(0.1):.2f}, p90 {df.LMTD_K.quantile(0.9):.2f})"
    )
    print(
        f"  UA/Q             : median {df.UA_over_Q.median():.3f} "
        f"(p10 {df.UA_over_Q.quantile(0.1):.3f}, p90 {df.UA_over_Q.quantile(0.9):.3f}) W/K per W"
    )
    print(f"  air flow per kW  : median {df.airflow_m3h_per_kW.median():.0f} m3/h")
    print(f"  wrote {OUT_CSV.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
