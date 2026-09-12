"""The reference the model's part-load trend is judged against.

The working assumption when this effort started was that a real inverter heat
pump peaks somewhere around a third of load and falls away below it, and that
TMHP was wrong to show COP climbing all the way down. That assumption is what
this module tests, against the largest body of independently measured heat-pump
performance that is public.

Source
------
Heat Pump Keymark is European third-party certification: an accredited
laboratory measures to the standard and the declared values are published.
``hplib`` (Forschungszentrum Juelich, MIT licence,
doi:10.5281/zenodo.5521597) republishes the Keymark records as one CSV per
certificate. This module reads those records directly rather than hplib's
fitted parameter database, because what is wanted here is the declared
measurement, not somebody's fit to it.

The variable codes are those of EN 14825 as hplib stores them:

======================  ===================================  ==============
code                    quantity                             test point
======================  ===================================  ==============
``EN14825_008/009``     heat output / COP                    A  (-7 degC)
``EN14825_010/011``     heat output / COP                    B  (+2 degC)
``EN14825_012/013``     heat output / COP                    C  (+7 degC)
``EN14825_014/015``     heat output / COP                    D  (+12 degC)
======================  ===================================  ==============

Average climate only (``climate`` 3-4). The low-temperature application
(``temperature`` 4 or 6) is underfloor heating, flow temperature falling
34/30/27/24 degC from A to D; the medium-temperature application
(``temperature`` 5 or 7) is radiators, 52/42/36/30 degC. "W35" and "W55" name
the application, not a fixed flow temperature -- reading them as fixed would
put three of the four points at the wrong boundary condition.

Run
---
``uv run python -m validation.extraction.keymark_declared``
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd

from ._pdftext import EVIDENCE, EvidenceMissing

REPO_ROOT = Path(__file__).resolve().parents[2]
CSV_DIR = EVIDENCE / "data" / "hplib" / "csv"
OUT_CSV = REPO_ROOT / "validation" / "data" / "keymark_en14825_declared.csv"
OUT_SUMMARY = REPO_ROOT / "validation" / "data" / "keymark_en14825_summary.csv"

SOURCE_URL = "https://github.com/FZJ-IEK3-VSA/hplib (input/csv), MIT licence"

#: Test point -> (heat output code, COP code, outdoor temperature degC,
#: theoretical part load of the design heating demand).
POINTS = {
    "A": ("EN14825_008", "EN14825_009", -7.0, 0.88),
    "B": ("EN14825_010", "EN14825_011", 2.0, 0.54),
    "C": ("EN14825_012", "EN14825_013", 7.0, 0.35),
    "D": ("EN14825_014", "EN14825_015", 12.0, 0.15),
}
#: ``temperature`` codes for the two applications, before hplib's remapping.
APPLICATION = {"low": {4, 6}, "medium": {5, 7}}
AVERAGE_CLIMATE = {3, 4}


# A handful of certificates carry a very long free-text field; the default
# limit is not a data-integrity guard here, only a memory one.
csv.field_size_limit(10_000_000)


def _read_certificate(path: Path) -> list[dict]:
    """One certificate file may hold several models; split on ``title`` rows."""
    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return []

    general: dict[str, str] = {}
    for row in rows:
        if row.get("varName") in ("Manufacturer", "Modelname", "Refrigerant", "Type", "Date"):
            general.setdefault(row["varName"], row.get("value", ""))

    blocks: list[list[dict]] = []
    current: list[dict] = []
    for row in rows:
        if row.get("varName") == "title" and current:
            blocks.append(current)
            current = []
        current.append(row)
    if current:
        blocks.append(current)

    out = []
    for block in blocks:
        title = next((r["value"] for r in block if r.get("varName") == "title"), "")
        for application, codes in APPLICATION.items():
            selected: dict[str, float] = {}
            for row in block:
                try:
                    climate = int(float(row.get("climate") or 0))
                    temperature = int(float(row.get("temperature") or 0))
                except ValueError:
                    continue
                if climate not in AVERAGE_CLIMATE or temperature not in codes:
                    continue
                name = row.get("varName", "")
                value = row.get("value", "")
                if not value:
                    continue
                try:
                    selected[name] = float(str(value).replace(",", "."))
                except ValueError:
                    continue
            record = {
                "manufacturer": general.get("Manufacturer", ""),
                "model": general.get("Modelname", ""),
                "title": title,
                "refrigerant": general.get("Refrigerant", ""),
                "type": general.get("Type", ""),
                "application": application,
            }
            complete = True
            for label, (p_code, cop_code, t_outdoor, plr) in POINTS.items():
                cop = selected.get(cop_code)
                p_th = selected.get(p_code)
                if cop is None or p_th is None or not (1.0 < cop < 15.0) or p_th <= 0.0:
                    complete = False
                    break
                record[f"cop_{label}"] = cop
                record[f"p_th_{label}_kW"] = p_th
                record[f"t_out_{label}_C"] = t_outdoor
                record[f"plr_{label}"] = plr
            if complete:
                out.append(record)
    return out


def build() -> pd.DataFrame:
    if not CSV_DIR.exists():
        raise EvidenceMissing(
            f"{CSV_DIR.relative_to(REPO_ROOT)} not found.\n"
            f"Obtain it from {SOURCE_URL} -- the repository archive's "
            "`input/csv` directory, copied under validation/evidence/data/hplib/."
        )
    records: list[dict] = []
    for path in sorted(CSV_DIR.glob("*.csv")):
        records.extend(_read_certificate(path))
    return pd.DataFrame(records).drop_duplicates(subset=["title", "application"])


def main() -> None:
    print("Heat Pump Keymark declared performance -- EN 14825 average climate")
    try:
        df = build()
    except EvidenceMissing as exc:
        print(f"  [skip] {exc}")
        return
    df.to_csv(OUT_CSV, index=False)

    print(f"  certificates read      : {len(df)} (model x application)")
    print(f"  distinct models        : {df.title.nunique()}")
    print(f"  manufacturers          : {df.manufacturer.nunique()}")
    print()

    rows = []
    for application, group in df.groupby("application"):
        cops = {label: group[f"cop_{label}"] for label in POINTS}
        rises = np.ones(len(group), dtype=bool)
        labels = list(POINTS)
        for lo, hi in zip(labels, labels[1:], strict=False):
            rises &= cops[hi].to_numpy() > cops[lo].to_numpy()
        d_over_c = float((cops["D"] > cops["C"]).mean() * 100.0)
        print(f"  {application}-temperature application ({len(group)} records)")
        print(f"    {'point':>7}{'T_out':>8}{'PLR':>7}{'COP p25':>10}{'median':>9}{'p75':>8}")
        for label in labels:
            _, _, t_out, plr = POINTS[label]
            series = cops[label]
            print(
                f"    {label:>7}{t_out:>8.0f}{plr:>7.2f}"
                f"{series.quantile(0.25):>10.2f}{series.median():>9.2f}{series.quantile(0.75):>8.2f}"
            )
            rows.append(
                {
                    "application": application,
                    "point": label,
                    "t_outdoor_C": t_out,
                    "plr": plr,
                    "n": len(series),
                    "cop_p10": series.quantile(0.10),
                    "cop_p25": series.quantile(0.25),
                    "cop_median": series.median(),
                    "cop_p75": series.quantile(0.75),
                    "cop_p90": series.quantile(0.90),
                }
            )
        print(f"    COP rises at every step, A->D : {rises.mean() * 100:.1f} % of models")
        print(f"    COP(D) > COP(C)               : {d_over_c:.1f} % of models")
        print()

    pd.DataFrame(rows).to_csv(OUT_SUMMARY, index=False)
    print("  The expected roll-over is not there. Declared COP improves toward")
    print("  the light test points, because EN 14825 lowers the flow temperature")
    print("  as it lowers the load -- so the lift falls with the duty. The")
    print("  part-load penalty real equipment pays comes from the minimum")
    print("  modulation limit and from cycling, not from the compressor")
    print("  efficiency correlations.")
    print()
    print(f"  wrote {OUT_CSV.relative_to(REPO_ROOT)}")
    print(f"  wrote {OUT_SUMMARY.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
