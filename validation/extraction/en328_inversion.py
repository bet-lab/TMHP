"""L2/L3 (evaporator side) -- invert EN 328 unit-cooler catalogues.

EN 328 rates an entire product range at one declared condition: Standard
Condition 2, R-404A, saturated suction -8 degC, entering air 0 degC, hence
``DT1 = 8 K``. Because the condition is common to every model in the range,
conductance per unit duty becomes comparable across capacities *by
construction* -- which is what a capacity-normalised default rule needs and
what a single research paper reporting one machine's UA can never provide.

Four manufacturers are scraped so the resulting band is not one company's
product policy. Every row is reduced by the identity in :mod:`._inversion`;
nothing is fitted.

Reading guard
-------------
Each parser asserts one printed row verbatim before trusting the rest of its
table. Column order differs between manufacturers and even between series of
the same manufacturer (Guentner GHF prints air volume before surface area,
GACC prints surface area before air volume), so an unguarded scrape silently
reads the wrong column. The effectiveness check in
``ua_from_declared_rating`` is the second net: a column swap almost always
pushes effectiveness outside (0, 1).

Run
---
``uv run python -m validation.extraction.en328_inversion``
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from ._inversion import invert
from ._pdftext import EvidenceMissing, assembled_rows, layout_text, number, text_lines

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_CSV = REPO_ROOT / "validation" / "data" / "en328_evaporator_inversion.csv"

#: EN 328 Standard Condition 2 entering-air to saturation temperature difference.
DT1_SC2 = 8.0
#: Entering air temperature at SC2 [degC] -- fixes the inlet air density.
T_AIR_SC2 = 0.0
RHO_AIR_SC2 = 1.2922


def _record(
    source: str,
    series: str,
    model: str,
    duty_w: float,
    flow_m3_s: float,
    surface_m2: float | None,
    fin_mm: float | None,
    note: str = "",
) -> dict:
    inv = invert(duty_w, flow_m3_s, DT1_SC2, rho=RHO_AIR_SC2)
    return {
        "source": source,
        "series": series,
        "model": model,
        "fin_spacing_mm": fin_mm,
        "Q_kW": duty_w / 1000.0,
        "airflow_m3h": flow_m3_s * 3600.0,
        "surface_m2": surface_m2,
        "C_air_W_K": inv.c_air_w_k,
        "eps": inv.eps,
        "NTU": inv.ntu,
        "UA_W_K": inv.ua_w_k,
        "LMTD_K": inv.lmtd_k,
        "UA_over_Q": inv.ua_over_q,
        "airflow_m3h_per_kW": inv.flow_m3h_per_kw,
        "U_W_m2K": (inv.ua_w_k / surface_m2) if surface_m2 else None,
        "note": note,
    }


# --------------------------------------------------------------------------
# Alfa Laval TYR-A air-sock unit coolers (ERC00369EN)
# --------------------------------------------------------------------------
ALFA_TYRA_PDF = "catalogs/alfalaval_tyrva_airsock_unit_coolers.pdf"
_TYRA_ROW = re.compile(r"^(\d{3}-\d-\*\s*[LH])\s+(.+)$")


def parse_alfalaval_tyra() -> list[dict]:
    text = layout_text(ALFA_TYRA_PDF)
    # Assert one printed row before trusting the table (p.6, fin spacing 4 mm).
    assert re.search(r"214-4-\*\s*L\s+4[.,]8\s+2740", text), "Alfa Laval TYR-A anchor row not found"

    # Surface area lives in a separate technical-data table, keyed by model.
    surface: dict[str, float] = {}
    fin_of: dict[str, float] = {}
    fin = None
    for line in text.split("\n"):
        m = re.search(r"Fin spacing\s+(\d+)\s*mm", line)
        if m:
            fin = float(m.group(1))
        m = re.match(r"\s*(\d{3}-\d-\*\s*[LH])\s+(\d{2})\s+([\d.,]+)\s+(\d+)\s+(\d+)\s+(\d{4})\s", line)
        if m:  # technical-data row: model, dB, surface, int.vol, weight, length
            key = re.sub(r"\s+", "", m.group(1))
            surface[key] = number(m.group(3))
            if fin is not None:
                fin_of[key] = fin

    rows: list[dict] = []
    fin = None
    for line in text.split("\n"):
        m = re.search(r"Fin spacing\s+(\d+)\s*mm", line)
        if m:
            fin = float(m.group(1))
        m = _TYRA_ROW.match(line.strip())
        if not m:
            continue
        key = re.sub(r"\s+", "", m.group(1))
        tail = m.group(2).split()
        # Capacity / air-flow pairs, one pair per external-pressure column.
        pressures = [40.0, 60.0, 80.0]
        pairs = [(tail[i], tail[i + 1]) for i in range(0, len(tail) - 1, 2)]
        for idx, (q_tok, v_tok) in enumerate(pairs):
            try:
                q_kw = number(q_tok)
                flow = number(v_tok)
            except ValueError:
                continue
            if not (0.5 <= q_kw <= 60.0 and 500.0 <= flow <= 30000.0):
                continue
            rows.append(
                _record(
                    "Alfa Laval TYR-A (ERC00369EN)",
                    "TYR-A",
                    f"{key}@{pressures[idx]:.0f}Pa" if idx < len(pressures) else key,
                    q_kw * 1000.0,
                    flow / 3600.0,
                    surface.get(key),
                    fin_of.get(key, fin),
                    note=f"ext. static {pressures[idx]:.0f} Pa" if idx < len(pressures) else "",
                )
            )
    return rows


# --------------------------------------------------------------------------
# Guentner GHF.2 high-efficiency unit coolers
# --------------------------------------------------------------------------
GHF_PDF = "catalogs/guntner_ghf_unit_coolers.pdf"
GHF_PAGES = range(3, 11)
# model  Q_SC2  Q_SC3  airflow  surface  throw  dB ...
_GHF_ROW = re.compile(
    r"(?:^|\s)(\d{3}\.\d[A-Z]/\d+-[A-Z]{3}\d+\.[A-Z])\s+"
    r"([\d,]+)\s+([\d,]+)\s+(\d+)\s+([\d,]+)\s+(\d+)\s+(\d+)(?:\s|$)"
)


def parse_guntner_ghf() -> list[dict]:
    rows: list[dict] = []
    anchored = False
    for page in GHF_PAGES:
        for line in assembled_rows(GHF_PDF, page):
            if "020.2C/14-ANW50.E 0,82 0,66 725 3,8" in line:
                anchored = True
            m = _GHF_ROW.search(line)
            if not m:
                continue
            model, q_sc2, _q_sc3, flow, surf, _throw, _db = m.groups()
            fin = float(re.search(r"/(\d+)-", model).group(1)) / 10.0
            rows.append(
                _record(
                    "Guentner GHF.2",
                    "GHF.2",
                    model,
                    number(q_sc2) * 1000.0,
                    float(flow) / 3600.0,
                    number(surf),
                    fin,
                )
            )
    assert anchored, "Guentner GHF.2 anchor row not found"
    return rows


# --------------------------------------------------------------------------
# Guentner GACC Cubic Compact air coolers
# --------------------------------------------------------------------------
GACC_PDF = "catalogs/guntner_gacc_air_cooler_datasheet.pdf"
GACC_PAGES = range(3, 8)
# model  Q_SC2  Q_SC3  surface  airflow  throw ...   <- note: surface BEFORE flow
_GACC_ROW = re.compile(
    r"(?:^|\s)(\d{3}\.\d[A-Z]/\d+-[A-Z]{2}\.[A-Z])\s+"
    r"([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+(\d+)\s+(\d+)\s+([\d,]+)(?:\s|$)"
)


def parse_guntner_gacc() -> list[dict]:
    rows: list[dict] = []
    anchored = False
    for page in GACC_PAGES:
        for line in assembled_rows(GACC_PDF, page):
            if "031.1C/14-AW.E 1,8 1,4 6,7 1610" in line:
                anchored = True
            m = _GACC_ROW.search(line)
            if not m:
                continue
            model, q_sc2, _q_sc3, surf, flow, _throw, _pwr = m.groups()
            fin = float(re.search(r"/(\d+)-", model).group(1)) / 10.0
            hz = "60 Hz" if "-AX." in model else "50 Hz"
            rows.append(
                _record(
                    "Guentner GACC Cubic Compact",
                    "GACC",
                    model,
                    number(q_sc2) * 1000.0,
                    float(flow) / 3600.0,
                    number(surf),
                    fin,
                    note=hz,
                )
            )
    assert anchored, "Guentner GACC anchor row not found"
    return rows


# --------------------------------------------------------------------------
# GEA Searle cooler ranges (six series, each with its own table layout)
# --------------------------------------------------------------------------
GEA_PDF = "catalogs/gea_searle_coolers_condensing_units.pdf"

#: ``(series, page index, anchor row, row pattern, column picker)``.
#: The column order is different in every series -- JG prints two refrigerant
#: capacity columns before the fan count, KLe prints four -- so each series
#: carries its own pattern and an anchor row asserted verbatim against the
#: printed page before any of its rows are trusted.
_GEA_SPECS: list[tuple] = [
    (
        "JG",
        9,
        "JG1 330 300 1 0.1 3.5 50 38 0.25 0.81",
        re.compile(r"^(JG\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\b"),
        lambda g: (float(g[1]), float(g[4]), float(g[9])),  # duty [W], flow, surface
    ),
    (
        "TEC",
        13,
        "TEC1-7 0.57 0.15 4.5 20 51 1.5 0.39",
        re.compile(r"^(TEC[\d.]+-\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+(\d+)\s+([\d.]+)\b"),
        lambda g: (float(g[1]) * 1000.0, float(g[2]), float(g[6])),
    ),
    (
        "NS",
        17,
        "NS14 - 6 1.69 1 0.24 6.0 53 70",
        re.compile(
            r"^(NS\d+\s*-\s*\d+)\s+([\d.]+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\b"
        ),
        lambda g: (float(g[1]) * 1000.0, float(g[3]), float(g[10])),
    ),
    (
        "KEC",
        21,
        "KEC10-4 1.65 0.28 8.5 1.4 0.45",
        re.compile(r"^(KEC\d+-\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\b"),
        lambda g: (float(g[1]) * 1000.0, float(g[2]), float(g[3])),
    ),
    (
        "KMe",
        25,
        "KMe50-4 7.36 0.89 38.0 6.7 2.1",
        re.compile(r"^(KMe\d+-\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\b"),
        lambda g: (float(g[1]) * 1000.0, float(g[2]), float(g[3])),
    ),
    (
        # KLe prints four refrigerant columns (R404A R507A R134a R407C) before
        # the air volume; only the R-404A column is the EN 328 SC2 rating.
        "KLe",
        29,
        "KLe75-5 11.3 11.0 10.3 11.4 1.62 36.3",
        re.compile(r"^(KLe\d+-\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\b"),
        lambda g: (float(g[1]) * 1000.0, float(g[5]), float(g[6])),
    ),
]


def parse_gea_searle() -> list[dict]:
    rows: list[dict] = []
    for series, page, anchor, pattern, pick in _GEA_SPECS:
        lines = text_lines(GEA_PDF, page)
        assert any(line.strip().startswith(anchor) for line in lines), (
            f"GEA Searle {series}: anchor row {anchor!r} not found on page {page + 1}"
        )
        found = 0
        for line in lines:
            m = pattern.match(line.strip())
            if not m:
                continue
            duty_w, flow_m3_s, surface = pick(m.groups())
            model = re.sub(r"\s+", "", m.group(1))
            fin_match = re.search(r"-(\d+)$", model)
            rows.append(
                _record(
                    "GEA Searle cooler ranges",
                    series,
                    model,
                    duty_w,
                    flow_m3_s,
                    surface,
                    float(fin_match.group(1)) if fin_match else None,
                )
            )
            found += 1
        assert found >= 4, f"GEA Searle {series}: only {found} rows parsed on page {page + 1}"
    return rows


PARSERS = {
    "Alfa Laval TYR-A": parse_alfalaval_tyra,
    "Guentner GHF.2": parse_guntner_ghf,
    "Guentner GACC": parse_guntner_gacc,
    "GEA Searle": parse_gea_searle,
}


def build() -> pd.DataFrame:
    frames: list[dict] = []
    for name, fn in PARSERS.items():
        try:
            got = fn()
        except EvidenceMissing as exc:
            print(f"  [skip] {name}: {exc}")
            continue
        print(f"  {name:24s} {len(got):4d} rows")
        frames.extend(got)
    df = pd.DataFrame(frames)
    if df.empty:
        return df
    # Some ranges declare one model at several external static pressures. Each
    # is a genuine rating point, but weighting the band by how many pressure
    # columns a manufacturer chose to print would let one catalogue dominate.
    # `is_primary` marks one row per physical model -- the highest air flow,
    # i.e. the lowest external resistance -- and the band statistics use it.
    hardware = df.model.str.replace(r"@.*$", "", regex=True)
    df["hardware"] = df.source + "/" + hardware
    df["is_primary"] = df.groupby("hardware")["airflow_m3h"].transform("max").eq(df.airflow_m3h)
    df["is_primary"] &= ~df.duplicated(subset=["hardware", "is_primary"])
    return df


def main() -> None:
    print("L2/L3 -- EN 328 SC2 evaporator practice (R-404A, t0 -8 degC, DT1 8 K)")
    df = build()
    if df.empty:
        print("  no evidence available")
        return
    df.to_csv(OUT_CSV, index=False)
    prim = df[df.is_primary]
    print()
    print(f"  rating rows           : {len(df)}  (one per printed capacity/air-flow column)")
    print(f"  distinct models       : {len(prim)}  <- band statistics use these")
    print(f"  duty range            : {prim.Q_kW.min():.2f} - {prim.Q_kW.max():.2f} kW")
    print(
        f"  equivalent LMTD       : median {prim.LMTD_K.median():.2f} K "
        f"(p10 {prim.LMTD_K.quantile(0.1):.2f}, p90 {prim.LMTD_K.quantile(0.9):.2f})"
    )
    print(
        f"  UA/Q                  : median {prim.UA_over_Q.median():.3f} "
        f"(p10 {prim.UA_over_Q.quantile(0.1):.3f}, p90 {prim.UA_over_Q.quantile(0.9):.3f}) W/K per W"
    )
    print(f"  air flow per kW       : median {prim.airflow_m3h_per_kW.median():.0f} m3/h")
    print()
    print("  per source (distinct models):")
    g = prim.groupby("source").agg(
        n=("model", "size"),
        lmtd_median=("LMTD_K", "median"),
        ua_over_q=("UA_over_Q", "median"),
        flow_per_kw=("airflow_m3h_per_kW", "median"),
    )
    print(g.to_string(float_format=lambda v: f"{v:.3f}"))
    print()
    print(f"  wrote {OUT_CSV.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
